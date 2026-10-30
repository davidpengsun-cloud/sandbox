"""The BigQuery client every BigQuery adapter shares: the account and its token, one query over the REST API, and the
dataset names Terraform gives a client (kge/infra). The warehouses (kge/adapters/warehouse/bigquery.py and, for the
estate and onboarding, bigquery_estate.py), the app state (kge/adapters/appstate/bigquery.py), the sync
(kge/estate/load_bigquery.py) and BigQuery's retrieval (kge/adapters/retrieval/bigquery.py) read through it (issue #70).
BigQueryLoader writes: a statement or script run to its end and every page of its rows read (run, rows: typed_rows gives
DuckDB's Python values, NUMERIC as Decimal), load jobs (load_rows, never streaming, so DML can follow; a load larger than
BIGQUERY_LOAD_INLINE_MAX staged in the client's scratch area, kge/adapters/objstore open_scratch, and loaded from gs://),
table listing and the labelled prune (issue #95)."""
from __future__ import annotations

import datetime as dt
import decimal
import json
import os
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from typing import Any

from kge.adapters.deadline import query_budget, request_cap, request_io, request_remaining
from kge.pipeline.settings import settings
from kge.ports import ScratchArea, StoreError

CLOUD_PLATFORM = "https://www.googleapis.com/auth/cloud-platform"
DEFAULT_DATASET_PREFIX = "kge"
NAME = re.compile(r"[A-Za-z][A-Za-z0-9_]*")
IDENT = re.compile(r"[a-z][a-z0-9_]{0,62}")

_bq_lock = threading.Lock()
_bq_cached_account: tuple[Any, Any, str | None] | None = None


def dataset_name(prefix: str, client: str, kind: str) -> str:
    """Format BigQuery dataset ID matching Terraform's replace(client, '-', '_') convention."""
    slug = str(client or "").strip().replace("-", "_")
    return f"{prefix}_{slug}_{kind}" if slug else f"{prefix}_{kind}"


def bq_account(project: str | None = None) -> tuple[Any, str]:
    """Resolve and cache Google Cloud credentials and default project."""
    global _bq_cached_account
    import google.auth
    with _bq_lock:
        if _bq_cached_account is None or _bq_cached_account[0] is not google.auth.default:
            credentials, default_proj = google.auth.default(scopes=[CLOUD_PLATFORM])
            _bq_cached_account = (google.auth.default, credentials, default_proj)
        _, credentials, default_proj = _bq_cached_account
    p = (project or settings().BIGQUERY_PROJECT or settings().VERTEX_PROJECT or default_proj or "").strip()
    if not p:
        raise ValueError("no Google Cloud project: set BIGQUERY_PROJECT or VERTEX_PROJECT, or run where credentials name one")
    return credentials, p


def bq_token(project: str | None = None) -> tuple[str, str]:
    """Return a valid OAuth bearer token and resolved project ID."""
    credentials, p = bq_account(project)
    with _bq_lock:
        if not credentials.valid:
            from google.auth.transport.requests import Request
            credentials.refresh(Request())
        return credentials.token, p


def _parse_bq_json_rows(data: dict) -> list[dict]:
    """Parse BigQuery REST API query result rows into list of dictionaries, properly unpacking arrays."""
    fields = [f["name"] for f in data.get("schema", {}).get("fields", [])]
    rows = []
    for r in data.get("rows", []):
        vals = []
        for col in r.get("f", []):
            v = col.get("v")
            if isinstance(v, list):
                v = [item.get("v") if isinstance(item, dict) else item for item in v]
            vals.append(v)
        rows.append(dict(zip(fields, vals)))
    return rows


@request_io
def bq_query(sql: str, project: str | None = None) -> list[dict]:
    """Execute a query against BigQuery REST API and return parsed rows as dictionaries."""
    token, p = bq_token(project)
    url = f"https://bigquery.googleapis.com/bigquery/v2/projects/{p}/queries"
    headers = {"authorization": f"Bearer {token}", "content-type": "application/json"}
    body = {"query": sql, "useLegacySql": False}
    query_budget(body, 60)
    req = urllib.request.Request(url, method="POST", headers=headers, data=json.dumps(body, ensure_ascii=False).encode())
    try:
        with urllib.request.urlopen(req, timeout=request_cap(60)) as r:
            data = json.loads(r.read())
    except urllib.error.HTTPError as exc:
        err_body = exc.read().decode(errors="replace")
        try:
            msg = json.loads(err_body).get("error", {}).get("message") or err_body
        except Exception:
            msg = err_body
        raise RuntimeError(f"BigQuery error (HTTP {exc.code}): {msg}") from None
    return _parse_bq_json_rows(data)


BATCH_SIZE = 500
UNIT_LABEL = "kge_unit"
VERSION_LABEL = "kge_version"
VERSION = re.compile(r"v[1-9][0-9]{0,8}")
DEFAULT_JOB_TIMEOUT = 300.0
DEFAULT_JOB_POLL = 1.0
DEFAULT_JOB_POLL_MAX = 10.0
DEFAULT_JOB_RETRIES = 3
DEFAULT_LOAD_INLINE_MAX = 5 * 1024 * 1024
BOUNDARY = "===============kge_bq_load_boundary=="


def inline_max() -> int:
    """BIGQUERY_LOAD_INLINE_MAX: the most bytes of newline-delimited JSON a load job is sent inline, in its request;
    a larger load is staged in the scratch area. Unset, 5 MiB, the size Google advises a multipart upload stay within (a
    planning default, not a measured one). Anything but a whole number above 0 is refused, naming the setting."""
    raw = (settings().BIGQUERY_LOAD_INLINE_MAX or "").strip()
    if not raw:
        return DEFAULT_LOAD_INLINE_MAX
    if not raw.isdigit() or int(raw) <= 0:
        raise ValueError(f"BIGQUERY_LOAD_INLINE_MAX={raw!r}: a whole number of bytes above 0")
    return int(raw)


def multipart(meta: dict, data: bytes) -> bytes:
    """A load job's insert with its data inline: the job's resource, then the data, as one multipart/related body."""
    head = (f"--{BOUNDARY}\r\n"
            "Content-Type: application/json; charset=UTF-8\r\n\r\n"
            f"{json.dumps(meta, ensure_ascii=False)}\r\n"
            f"--{BOUNDARY}\r\n"
            "Content-Type: application/octet-stream\r\n\r\n")
    return head.encode("utf-8") + data + f"\r\n--{BOUNDARY}--\r\n".encode("utf-8")
BIGQUERY_TABLE_MAX = 1024
BIGQUERY_COLUMN_MAX = 300


def _identifier(value: str, kind: str) -> str:
    if not isinstance(value, str) or not IDENT.fullmatch(value):
        raise ValueError(f"{kind} {value!r} must match {IDENT.pattern}")
    return value


def _bigquery_identifier(value: str, kind: str, maximum: int) -> str:
    """Keep the repository allowlist while applying the target's length limit. A table's or a column's name keeps the
    case the warehouse spells it in (SAGE's are upper case), as BigQuery does."""
    if not isinstance(value, str) or len(value) > maximum or not NAME.fullmatch(value):
        raise ValueError(f"{kind} {value!r} must match the identifier allowlist and be at most {maximum} characters")
    return value


def _positive_env(name: str, default: float) -> float:
    raw = os.getenv(name)
    try:
        value = default if raw is None else float(raw)
    except ValueError:
        raise ValueError(f"{name} must be a positive number") from None
    if value <= 0:
        raise ValueError(f"{name} must be a positive number")
    return value


def map_duckdb_type_to_bigquery(dtype: str) -> str:
    """Map DuckDB SQL data types to BigQuery standard SQL data types."""
    d = dtype.upper().split("(")[0].strip()
    if d in ("BIGINT", "INTEGER", "INT", "SMALLINT", "TINYINT", "INT8", "INT4", "INT2", "INT1", "HUGEINT"):
        return "INT64"
    if d in ("DOUBLE", "FLOAT", "REAL", "FLOAT8", "FLOAT4"):
        return "FLOAT64"
    if d in ("BOOLEAN", "BOOL"):
        return "BOOL"
    if d in ("DATE",):
        return "DATE"
    if d in ("TIMESTAMP", "TIMESTAMP WITH TIME ZONE", "TIMESTAMPTZ"):
        return "TIMESTAMP"
    if d in ("DECIMAL", "NUMERIC"):
        return "NUMERIC"
    return "STRING"


def serialize_val(val: Any) -> Any:
    if val is None:
        return None
    if isinstance(val, (dt.date, dt.datetime)):
        return val.isoformat()
    if isinstance(val, decimal.Decimal):
        return str(val)
    if isinstance(val, (int, float, str, bool, list)):
        return val
    return str(val)


def _typed(value: Any, field: dict) -> Any:
    """One cell of a REST answer as the Python value DuckDB would give: NUMERIC as Decimal (never a float, so a figure
    keeps its digits), INT64 as int, DATE as date, TIMESTAMP (microseconds, useInt64Timestamp) as a naive UTC datetime;
    a REPEATED field a list of them."""
    if value is None:
        return None
    if field.get("mode") == "REPEATED":
        return [_typed(item.get("v") if isinstance(item, dict) else item, {**field, "mode": "NULLABLE"}) for item in value]
    kind = str(field.get("type", "STRING")).upper()
    if kind in ("INTEGER", "INT64"):
        return int(value)
    if kind in ("FLOAT", "FLOAT64"):
        return float(value)
    if kind in ("NUMERIC", "BIGNUMERIC", "DECIMAL", "BIGDECIMAL"):
        return decimal.Decimal(value)
    if kind in ("BOOLEAN", "BOOL"):
        return value.lower() == "true" if isinstance(value, str) else bool(value)
    if kind == "DATE":
        return dt.date.fromisoformat(value)
    if kind == "TIMESTAMP":
        text = str(value)
        if re.fullmatch(r"-?[0-9]+", text):
            return dt.datetime(1970, 1, 1) + dt.timedelta(microseconds=int(text))
        return dt.datetime.fromisoformat(text.replace("Z", "+00:00")).replace(tzinfo=None)
    if kind == "DATETIME":
        return dt.datetime.fromisoformat(value)
    return value


def typed_rows(data: dict) -> list[tuple]:
    """A query's REST answer (BigQueryLoader.run) as tuples of Python values, typed by its schema (_typed)."""
    fields = (data.get("schema") or {}).get("fields") or []
    return [tuple(_typed(cell.get("v"), field) for cell, field in zip(r.get("f", []), fields)) for r in data.get("rows") or []]


class BigQueryLoader:
    def __init__(self, project: str | None = None, dataset_prefix: str | None = None, dry_run: bool = False,
                 sleeper=time.sleep, clock=time.monotonic, client: str | None = None):
        """client: whose scratch area (kge/adapters/objstore open_scratch) a large load is staged in; with none, every
        load is sent inline."""
        self.dry_run = dry_run
        self.client = client
        self.dataset_prefix = (dataset_prefix or os.getenv("BIGQUERY_DATASET_PREFIX", "")).strip() or DEFAULT_DATASET_PREFIX
        self._project = (project or os.getenv("BIGQUERY_PROJECT", "") or os.getenv("VERTEX_PROJECT", "")).strip() or None
        self._account: tuple | None = None
        self._lock = threading.Lock()
        self._sleep = sleeper
        self._clock = clock

    def account(self) -> tuple[Any, str]:
        with self._lock:
            if self._account is None:
                if self.dry_run:
                    self._account = (None, self._project or "pilot-project")
                else:
                    import google.auth
                    credentials, project = google.auth.default(scopes=[CLOUD_PLATFORM])
                    self._account = (credentials, self._project or project)
        credentials, project = self._account
        if not project:
            raise ValueError("no Google Cloud project: set BIGQUERY_PROJECT or VERTEX_PROJECT, or run where credentials name one")
        return credentials, project

    @request_io
    def token(self) -> tuple[str, str]:
        credentials, project = self.account()
        if self.dry_run:
            return "mock-token", project
        with self._lock:
            if not credentials.valid:
                from google.auth.transport.requests import Request
                credentials.refresh(Request())
            return credentials.token, project

    @request_io
    def post(self, url: str, headers: dict, body: dict) -> dict:
        if self.dry_run:
            return {}
        req = urllib.request.Request(url, method="POST", headers=headers, data=json.dumps(body, ensure_ascii=False).encode())
        try:
            with urllib.request.urlopen(req, timeout=request_cap(120)) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as exc:
            err_body = exc.read().decode(errors="replace")
            try:
                err_json = json.loads(err_body)
                msg = err_json.get("error", {}).get("message") or err_body
            except Exception:
                msg = err_body
            raise RuntimeError(f"BigQuery error (HTTP {exc.code}): {msg}") from None

    @request_io
    def get(self, url: str, headers: dict) -> dict:
        if self.dry_run:
            return {}
        req = urllib.request.Request(url, method="GET", headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=request_cap(120)) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as exc:
            err_body = exc.read().decode(errors="replace")
            try:
                msg = json.loads(err_body).get("error", {}).get("message") or err_body
            except Exception:
                msg = err_body
            raise RuntimeError(f"BigQuery error (HTTP {exc.code}): {msg}") from None

    def query(self, sql: str) -> dict:
        token, project = self.token()
        url = f"https://bigquery.googleapis.com/bigquery/v2/projects/{project}/queries"
        headers = {"authorization": f"Bearer {token}", "content-type": "application/json"}
        body = {"query": sql, "useLegacySql": False}
        query_budget(body, 120)
        data = self.post(url, headers, body)
        request_remaining()
        return data

    def run(self, sql: str, dataset_id: str | None = None, dry_run: bool = False,
            job_timeout_ms: int | None = None) -> dict:
        """One statement or script run to its end: the query's schema and every row, across pages. jobs.query answers
        within its own wait and may say the job is not complete yet, so the job's results are fetched until it is, and
        then page by page; a statement that follows never starts before this one ended. dataset_id is the default
        dataset unqualified names resolve in; job_timeout_ms cancels the job at that limit (BigQuery's jobTimeoutMs);
        dry_run plans it and returns no rows. Timestamps come back as microseconds (useInt64Timestamp)."""
        if self.dry_run:
            return {"schema": {"fields": []}, "rows": []}
        token, project = self.token()
        base = f"https://bigquery.googleapis.com/bigquery/v2/projects/{project}/queries"
        headers = {"authorization": f"Bearer {token}", "content-type": "application/json"}
        body: dict[str, Any] = {"query": sql, "useLegacySql": False, "timeoutMs": 60000,
                                "formatOptions": {"useInt64Timestamp": True}}
        if dataset_id:
            body["defaultDataset"] = {"projectId": project, "datasetId": dataset_id}
        if dry_run:
            body["dryRun"] = True
        if job_timeout_ms:
            body["jobTimeoutMs"] = str(int(job_timeout_ms))
        if request_remaining() is not None:
            query_budget(body, _positive_env("BIGQUERY_JOB_TIMEOUT", DEFAULT_JOB_TIMEOUT))
        data = self.post(base, headers, body)
        request_remaining()
        if dry_run:
            return {"schema": data.get("schema") or {"fields": []}, "rows": []}
        reference = data.get("jobReference") or {}
        timeout = _positive_env("BIGQUERY_JOB_TIMEOUT", DEFAULT_JOB_TIMEOUT)
        deadline = self._clock() + request_cap(timeout)
        rows = list(data.get("rows") or [])
        page = data.get("pageToken")
        while not data.get("jobComplete", True) or page:
            request_remaining()
            if self._clock() >= deadline and not data.get("jobComplete", True):
                raise TimeoutError(f"BigQuery job {reference.get('jobId')} did not finish within {timeout:g} seconds")
            params = {"timeoutMs": str(int(request_cap(10) * 1000)), "formatOptions.useInt64Timestamp": "true"}
            if reference.get("location"):
                params["location"] = reference["location"]
            if page:
                params["pageToken"] = page
            job = urllib.parse.quote(str(reference.get("jobId") or ""), safe="")
            if not job:
                raise RuntimeError("BigQuery answered a query that is not complete with no job reference")
            data = self.get(f"{base}/{job}?" + urllib.parse.urlencode(params), {"authorization": f"Bearer {token}"})
            if not data.get("jobComplete", True):
                page = None
                continue
            rows += data.get("rows") or []
            page = data.get("pageToken")
        request_remaining()
        return {**data, "rows": rows}

    def rows(self, sql: str, dataset_id: str | None = None, job_timeout_ms: int | None = None) -> list[tuple]:
        """run's rows as tuples of Python values (typed_rows)."""
        return typed_rows(self.run(sql, dataset_id, job_timeout_ms=job_timeout_ms))

    def ensure_dataset(self, dataset_id: str) -> None:
        """Create the dataset unless it is there; any answer but 404 means it exists, or access is granted at dataset
        level, so nothing is created."""
        if self.dry_run:
            return
        token, project = self.token()
        get_url = f"https://bigquery.googleapis.com/bigquery/v2/projects/{project}/datasets/{dataset_id}"
        headers = {"authorization": f"Bearer {token}", "content-type": "application/json"}
        req = urllib.request.Request(get_url, method="GET", headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=30):
                return
        except urllib.error.HTTPError as exc:
            if exc.code != 404:
                return
        except Exception:
            pass

        url = f"https://bigquery.googleapis.com/bigquery/v2/projects/{project}/datasets"
        body = {
            "datasetReference": {
                "projectId": project,
                "datasetId": dataset_id,
            }
        }
        try:
            self.post(url, headers, body)
        except RuntimeError as exc:
            if "Already Exists" not in str(exc) and "409" not in str(exc) and "403" not in str(exc):
                raise

    def create_table(self, dataset_id: str, table_id: str, schema_fields: list[dict], unit: str | None = None,
                     version: str | None = None) -> None:
        token, project = self.token()
        _bigquery_identifier(table_id, "table", BIGQUERY_TABLE_MAX)
        if unit is not None:
            _identifier(unit, "unit")
        if version is not None and (unit is None or not VERSION.fullmatch(version)):
            raise ValueError(f"version {version!r} must match {VERSION.pattern}, on a unit's table")
        ddl_fields = []
        for f in schema_fields:
            _bigquery_identifier(f["name"], "column", BIGQUERY_COLUMN_MAX)
            mode = f.get("mode", "NULLABLE")
            t = f["type"]
            if mode == "REPEATED":
                ddl_fields.append(f"`{f['name']}` ARRAY<{t}>")
            else:
                ddl_fields.append(f"`{f['name']}` {t}")
        labels = [(UNIT_LABEL, unit)] + ([(VERSION_LABEL, version)] if version is not None else [])
        options = (" OPTIONS(labels=[" + ", ".join(f'("{k}", "{v}")' for k, v in labels) + "])") if unit is not None else ""
        sql = f"CREATE OR REPLACE TABLE `{project}.{dataset_id}.{table_id}` ({', '.join(ddl_fields)}){options}"
        self.query(sql)

    def insert_rows(self, dataset_id: str, table_id: str, rows: list[dict]) -> int:
        if not rows:
            return 0
        token, project = self.token()
        url = f"https://bigquery.googleapis.com/bigquery/v2/projects/{project}/datasets/{dataset_id}/tables/{table_id}/insertAll"
        headers = {"authorization": f"Bearer {token}", "content-type": "application/json"}
        total = 0
        for i in range(0, len(rows), BATCH_SIZE):
            batch = rows[i:i + BATCH_SIZE]
            body = {"rows": [{"json": r} for r in batch]}
            self.post(url, headers, body)
            total += len(batch)
        return total

    def load_rows(self, dataset_id: str, table_id: str, schema_fields: list[dict], rows: list[dict],
                  write_disposition: str = "WRITE_TRUNCATE") -> int:
        """Load rows via a BigQuery Load Job (WRITE_TRUNCATE, NEWLINE_DELIMITED_JSON) so rows
        land directly in columnar storage and never block DML UPDATEs in the streaming buffer. Rows whose
        newline-delimited JSON is larger than BIGQUERY_LOAD_INLINE_MAX bytes are staged as one file in the client's scratch
        area and loaded from its gs:// URI, then the file is deleted (the tmp bucket deletes any left after 7 days); smaller
        loads, and any where the scratch area is a local folder no load job can read, are sent inline in the request.
        Either way the job loads the same bytes. Transient poll failures are counted consecutively, so a good poll resets
        the count and a long wait survives scattered blips; a finished job failed only if it has an errorResult, since
        status.errors can list non-fatal errors on a job that succeeded."""
        if not rows:
            return 0
        if self.dry_run:
            return len(rows)
        token, project = self.token()
        load = {
            "destinationTable": {
                "projectId": project,
                "datasetId": dataset_id,
                "tableId": table_id,
            },
            "schema": {"fields": schema_fields},
            "sourceFormat": "NEWLINE_DELIMITED_JSON",
            "writeDisposition": write_disposition,
        }
        ndjson = ("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n").encode("utf-8")
        staged = self.stage(dataset_id, table_id, ndjson)
        try:
            if staged:
                scratch, key, uri = staged
                job = self.submit(f"https://bigquery.googleapis.com/bigquery/v2/projects/{project}/jobs",
                                  "application/json",
                                  json.dumps({"configuration": {"load": {**load, "sourceUris": [uri]}}},
                                             ensure_ascii=False).encode("utf-8"), token)
            else:
                job = self.submit(f"https://bigquery.googleapis.com/upload/bigquery/v2/projects/{project}/jobs"
                                  "?uploadType=multipart", f"multipart/related; boundary={BOUNDARY}",
                                  multipart({"configuration": {"load": load}}, ndjson), token)
            self.wait(job, project, token)
        finally:
            if staged:
                self.unstage(*staged[:2])
        return len(rows)

    def stage(self, dataset_id: str, table_id: str, ndjson: bytes) -> tuple[ScratchArea, str, str] | None:
        """(scratch area, key, gs:// URI) of ndjson put in the client's scratch area, where it is larger than
        inline_max() and the area is one a load job reads; None, nothing written, where the load goes inline."""
        if len(ndjson) <= inline_max() or not self.client:
            return None
        from kge.adapters.objstore import open_scratch
        scratch = open_scratch(self.client)
        key = f"bigquery/{dataset_id}/{table_id}/{uuid.uuid4().hex}.json"
        uri = scratch.source_uri(key)
        if not uri:
            return None
        scratch.put(key, ndjson)
        return scratch, key, uri

    @staticmethod
    def unstage(scratch: ScratchArea, key: str) -> None:
        """The staged file deleted; one that cannot be is left to the tmp bucket's 7-day lifecycle rule."""
        try:
            scratch.delete(key)
        except StoreError:
            pass

    @staticmethod
    def submit(url: str, content_type: str, payload: bytes, token: str) -> dict:
        """The load job inserted: its resource, as BigQuery answers the insert."""
        headers = {"authorization": f"Bearer {token}", "content-type": content_type}
        req = urllib.request.Request(url, method="POST", headers=headers, data=payload)
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as exc:
            err_body = exc.read().decode(errors="replace")
            try:
                msg = json.loads(err_body).get("error", {}).get("message") or err_body
            except Exception:
                msg = err_body
            raise RuntimeError(f"BigQuery load job error (HTTP {exc.code}): {msg}") from None

    def wait(self, job: dict, project: str, token: str) -> None:
        """The inserted load job polled until it is done; RuntimeError where it failed, TimeoutError past
        BIGQUERY_JOB_TIMEOUT."""
        err = job.get("status", {}).get("errorResult")
        if err:
            raise RuntimeError(f"BigQuery load job failed: {err.get('message', err)}")
        reference = job.get("jobReference") or {}
        job_id = reference.get("jobId")
        if not job_id:
            raise RuntimeError("BigQuery load job returned no job reference")
        location = reference.get("location")
        timeout = _positive_env("BIGQUERY_JOB_TIMEOUT", DEFAULT_JOB_TIMEOUT)
        poll = _positive_env("BIGQUERY_JOB_POLL", DEFAULT_JOB_POLL)
        poll_max = _positive_env("BIGQUERY_JOB_POLL_MAX", DEFAULT_JOB_POLL_MAX)
        deadline = self._clock() + timeout
        job_project = reference.get("projectId") or project
        job_url = f"https://bigquery.googleapis.com/bigquery/v2/projects/{job_project}/jobs/{urllib.parse.quote(job_id, safe='')}"
        if location:
            job_url += "?" + urllib.parse.urlencode({"location": location})
        transient_failures = 0
        while True:
            try:
                status = self.get(job_url, {"authorization": f"Bearer {token}"}).get("status", {})
            except (urllib.error.URLError, ConnectionError, TimeoutError, RuntimeError) as exc:
                match = re.search(r"HTTP (\d{3})", str(exc)) if isinstance(exc, RuntimeError) else None
                if isinstance(exc, RuntimeError) and not (match and (match.group(1) == "429" or match.group(1).startswith("5"))):
                    raise
                transient_failures += 1
                if transient_failures > DEFAULT_JOB_RETRIES or self._clock() >= deadline:
                    raise RuntimeError(
                        f"BigQuery load job {job_id} polling failed after {transient_failures} consecutive attempts "
                        f"(limit {timeout:g} seconds): {exc}"
                    ) from None
                self._sleep(min(poll, max(0.0, deadline - self._clock())))
                poll = min(poll * 2, poll_max)
                continue
            transient_failures = 0
            if status.get("state") == "DONE":
                failure = status.get("errorResult")
                if failure:
                    message = failure.get("message", failure) if isinstance(failure, dict) else failure
                    raise RuntimeError(f"BigQuery load job failed: {message}")
                break
            if self._clock() >= deadline:
                raise TimeoutError(f"BigQuery load job {job_id} did not finish within {timeout:g} seconds")
            self._sleep(min(poll, max(0.0, deadline - self._clock())))
            poll = min(poll * 2, poll_max)

    def tables(self, dataset_id: str) -> list[dict]:
        """List every table; pruning must not miss a stale unit on a later page."""
        token, project = self.token()
        headers = {"authorization": f"Bearer {token}"}
        base = f"https://bigquery.googleapis.com/bigquery/v2/projects/{project}/datasets/{dataset_id}/tables"
        found, page = [], None
        while True:
            url = base + ("?" + urllib.parse.urlencode({"pageToken": page}) if page else "")
            data = self.get(url, headers)
            found.extend(data.get("tables", []))
            page = data.get("nextPageToken")
            if not page:
                return found

    def prune_units(self, dataset_id: str, current_units: set[str],
                    current_versions: dict[str, set[str]] | None = None) -> list[str]:
        """Drop each table labelled with a unit no longer synced. current_units: the units synced as one warehouse each
        (<unit>_<table>). current_versions: the onboarded units' versions synced, by unit ({unit: {"v1", "v2"}}): a table
        labelled with a unit and a version is kept only while that version is (the versions any kept release names,
        kge/pipeline/warehouse_versions.py); an unlabelled table is never dropped."""
        current_versions = current_versions or {}
        if not current_units and not any(current_versions.values()):
            raise ValueError("refusing to prune BigQuery warehouses with no current units")
        _, project = self.account()
        stale = []
        for table in self.tables(dataset_id):
            labels = table.get("labels") or {}
            unit, version = labels.get(UNIT_LABEL), labels.get(VERSION_LABEL)
            table_id = (table.get("tableReference") or {}).get("tableId")
            if unit:
                _identifier(unit, "unit label")
                _bigquery_identifier(table_id, "table", BIGQUERY_TABLE_MAX)
                if version is not None and not (isinstance(version, str) and VERSION.fullmatch(version)):
                    raise ValueError(f"version label {version!r} must match {VERSION.pattern}")
                if version is None and unit not in current_units:
                    stale.append(table_id)
                elif version is not None and version not in current_versions.get(unit, set()):
                    stale.append(table_id)
        for table_id in stale:
            self.query(f"DROP TABLE `{project}.{dataset_id}.{table_id}`")
        return stale


GRAPH_DATASETS = ("graph", "candidates")


def graph_kind() -> str:
    """Which of a client's datasets this process writes graphs and chunks to (GRAPH_DATASET): graph, the default, whose
    tables a release may serve, or candidates, where the improvement job builds and checks its candidates and which
    nothing serves. Any other value is refused, naming the setting."""
    named = (settings().GRAPH_DATASET or "").strip().lower() or "graph"
    if named not in GRAPH_DATASETS:
        raise ValueError(f"GRAPH_DATASET={settings().GRAPH_DATASET!r}: graph (the default) or candidates")
    return named


def served(ledger, now: str | None) -> list[str]:
    """The served ledger at ledger, a JSON list: now first, then the one before it, written back when now is given; what
    a release just named and what the release before it named, which a process not restarted may still read."""
    from pathlib import Path
    try:
        held = [x for x in json.loads(Path(ledger).read_text()) if isinstance(x, str)]
    except (OSError, ValueError, TypeError):
        held = []
    if now:
        held = [now] + [x for x in held if x != now][:1]
        Path(ledger).write_text(json.dumps(held) + "\n")
    return held
