"""Named BigQuery jobs whose lifetime belongs to the warehouse, not an HTTP request."""
import random
import time
import urllib.parse

from kge.adapters.bigquery import QueryFailed, _dataset_reference, _positive_env, query_failure, typed_rows
from kge.adapters.deadline import ExternalCallError
from kge.pipeline.questionrun import TIME_LIMIT, timeout, remaining


def retried(loader, call):
    for attempt in range(4):
        try:
            return call()
        except QueryFailed as exc:
            if exc.code == 409:
                raise
            if not exc.transient or attempt == 3:
                raise ExternalCallError("The warehouse is temporarily unavailable; retry.", transient=exc.transient) from None
            loader._sleep(0.05 * 2 ** attempt * random.uniform(1, 2))


def submit(loader, sql, dataset, job_id, max_bytes=None):
    token, project = loader.token()
    headers = {"authorization": f"Bearer {token}", "content-type": "application/json"}
    base = f"https://bigquery.googleapis.com/bigquery/v2/projects/{project}"
    dataset_ref = _dataset_reference(dataset, project)
    info = retried(loader, lambda: loader.get(
        f"https://bigquery.googleapis.com/bigquery/v2/projects/{dataset_ref['projectId']}/datasets/"
        + urllib.parse.quote(dataset_ref["datasetId"], safe=""), headers))
    location = info.get("location")
    if not location:
        raise ExternalCallError("The warehouse did not identify its query location.")
    reference = {"projectId": project, "jobId": job_id, "location": location}
    query = {"query": sql, "useLegacySql": False, "useQueryCache": True,
             "priority": "INTERACTIVE", "defaultDataset": dataset_ref}
    if max_bytes is not None:
        query["maximumBytesBilled"] = str(int(max_bytes))
    started = time.time()
    seconds = min(_positive_env("BIGQUERY_JOB_TIMEOUT", 600), timeout(), remaining())
    if seconds <= 0:
        raise ExternalCallError(TIME_LIMIT)
    body = {"jobReference": reference, "configuration": {"query": query, "jobTimeoutMs": str(max(1, int(seconds * 1000)))}}
    try:
        data = retried(loader, lambda: loader.post(base + "/jobs", headers, body))
    except QueryFailed as exc:
        if exc.code != 409:
            raise
        data = retried(loader, lambda: loader.get(
            base + "/jobs/" + urllib.parse.quote(job_id, safe="") + "?" + urllib.parse.urlencode({"location": location}), headers))
        held = data.get("configuration", {}).get("query", {})
        if (held.get("query") != sql or held.get("defaultDataset") != dataset_ref or held.get("useLegacySql", False)
                or str(held.get("maximumBytesBilled")) != str(query.get("maximumBytesBilled"))):
            raise ExternalCallError("The warehouse job could not be verified; ask the question again.")
    query_failure(data)
    created = int(data.get("statistics", {}).get("creationTime") or started * 1000) / 1000
    lifetime = int(data.get("configuration", {}).get("jobTimeoutMs") or body["configuration"]["jobTimeoutMs"]) / 1000
    return {"reference": reference, "started": created, "expires": min(started + seconds, created + lifetime)}


def metrics(data, started):
    stats = data.get("statistics", {})
    query = stats.get("query", {})
    start = int(stats.get("startTime") or stats.get("creationTime") or started * 1000)
    end = int(stats.get("endTime") or time.time() * 1000)
    return {"elapsed_ms": max(0, end - start),
            "bytes_processed": int(query.get("totalBytesProcessed") or data.get("totalBytesProcessed") or 0),
            "slot_ms": int(stats.get("totalSlotMs") or query.get("totalSlotMs") or 0)}


def record_completed(loader, data):
    from kge.pipeline.trace import current, warehouse_job
    if current() is None or not data.get("jobReference"):
        return
    token, project = loader.token()
    ref = data["jobReference"]
    url = f"https://bigquery.googleapis.com/bigquery/v2/projects/{project}/jobs/" + urllib.parse.quote(ref["jobId"], safe="")
    if ref.get("location"):
        url += "?" + urllib.parse.urlencode({"location": ref["location"]})
    status = retried(loader, lambda: loader.get(url, {"authorization": f"Bearer {token}"}))
    warehouse_job({"job_id": ref["jobId"], **metrics(status, time.time())})


def poll(loader, handle):
    token, project = loader.token()
    ref = handle["reference"]
    headers = {"authorization": f"Bearer {token}"}
    base = f"https://bigquery.googleapis.com/bigquery/v2/projects/{project}"
    suffix = urllib.parse.quote(ref["jobId"], safe="")
    params = {"location": ref["location"]}
    data = retried(loader, lambda: loader.get(base + "/jobs/" + suffix + "?" + urllib.parse.urlencode(params), headers))
    progress = metrics(data, handle["started"])
    end = data.get("statistics", {}).get("endTime")
    if ((end is not None and int(end) > handle["expires"] * 1000)
            or (end is None and time.time() >= handle["expires"])):
        if data.get("status", {}).get("state") != "DONE":
            cancel(loader, handle)
        return {"state": "failed", "progress": progress, "error": "The warehouse exceeded its time limit; ask a narrower question."}
    if data.get("status", {}).get("errorResult"):
        return {"state": "failed", "progress": progress, "error": "The warehouse could not finish this question within its limits."}
    if data.get("status", {}).get("state") != "DONE":
        if time.time() >= handle["expires"]:
            cancel(loader, handle)
            return {"state": "failed", "progress": progress, "error": "The warehouse exceeded its time limit; ask a narrower question."}
        return {"state": "running", "progress": progress}
    rows = []
    params.update(timeoutMs="0", **{"formatOptions.useInt64Timestamp": "true"})
    schema = None
    while True:
        page = retried(loader, lambda: loader.get(base + "/queries/" + suffix + "?" + urllib.parse.urlencode(params), headers))
        query_failure(page, polled=True)
        schema = page.get("schema") or schema
        rows.extend(typed_rows({**page, "schema": schema or {}}))
        if not page.get("pageToken"):
            break
        params["pageToken"] = page["pageToken"]
    return {"state": "done", "progress": progress, "rows": rows}


def cancel(loader, handle):
    token, project = loader.token()
    loader.cancel_job(handle["reference"], project, token)
