"""Onboarding application state in BigQuery tables, in the client's graph dataset: the AppState port (kge/ports.py) on
Google Cloud.

A state query may run QUERY_DEADLINE seconds, polls included, since an onboarding step waits on it under its
lock. The API builds a store, and so a state, per request, so the (project, dataset) pairs whose tables are made
are kept per process."""
from __future__ import annotations

import contextlib
import json
import re
import time
import urllib.parse
from typing import Any

from kge.adapters.bigquery import DEFAULT_DATASET_PREFIX, bq_account, dataset_name
from kge.pipeline.settings import settings
from kge.ports import StateConflict, StateUnavailable

ABORT_RETRIES = 3
QUERY_DEADLINE = 300
_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,1023}\Z")
_PROJECT = re.compile(r"[a-z][a-z0-9:.-]{4,61}[a-z0-9]\Z")


_ENSURED: set[tuple[str, str]] = set()


def _parameter(name: str, value: Any) -> dict:
    kind = "INT64" if isinstance(value, int) else "STRING"
    return {"name": name, "parameterType": {"type": kind},
            "parameterValue": {"value": str(value) if kind == "INT64" else value}}


def _rows(data: dict) -> list[dict]:
    fields = [field["name"] for field in data.get("schema", {}).get("fields", [])]
    return [dict(zip(fields, [cell.get("v") for cell in row.get("f", [])])) for row in data.get("rows", [])]


class BigQueryState:
    """Transactional state in the client's existing graph dataset."""

    tables = True

    def __init__(self, prof, store=None, sleeper=time.sleep):
        self.client = prof.client
        self.prefix = (settings().BIGQUERY_DATASET_PREFIX or "").strip() or DEFAULT_DATASET_PREFIX
        self.project = (settings().BIGQUERY_PROJECT or settings().VERTEX_PROJECT or "").strip() or None
        self.dataset = dataset_name(self.prefix, self.client, "graph")
        for value in (self.dataset, "unit_manifest", "unit_history", "unit_requests", "onboard_job"):
            if not _IDENTIFIER.fullmatch(value):
                raise ValueError(f"{value!r} is not a BigQuery identifier")
        self._account = None
        self._ready = False
        self._sleep = sleeper
        self._store = store

    @contextlib.contextmanager
    def lock(self):
        """BigQuery serialises the manifest transaction; a local pack store still needs its filesystem lock to keep
        derived files in that order."""
        with self._store.lock() if self._store is not None else contextlib.nullcontext():
            yield

    def _token(self) -> tuple[str, str]:
        import google.auth.exceptions
        try:
            if self._account is None:
                self._account = bq_account(self.project)
            credentials, project = self._account
            if not credentials.valid:
                from google.auth.transport.requests import Request
                credentials.refresh(Request())
        except (google.auth.exceptions.GoogleAuthError, ValueError) as exc:
            first = (str(exc).strip().splitlines() or [type(exc).__name__])[0].rstrip(". ")
            raise StateUnavailable(f"APP_STATE_BACKEND=bigquery cannot sign in to BigQuery: {first}") from None
        return credentials.token, project

    def _post(self, body: dict) -> dict:
        _, project = self._token()
        return self._call(f"https://bigquery.googleapis.com/bigquery/v2/projects/{project}/queries", body)

    def _call(self, url: str, body: dict | None = None) -> dict:
        """One BigQuery REST call, a POST with a body or a GET without. Every HTTP error is mapped the same way, the
        reason kept, so a failed transaction's 'stale revision' or 'aborted' is recognised whether the first reply
        or a later poll carries it."""
        import urllib.error
        import urllib.request
        token, _ = self._token()
        request = urllib.request.Request(
            url, method="POST" if body is not None else "GET",
            headers={"authorization": f"Bearer {token}", **({"content-type": "application/json"} if body is not None else {})},
            data=json.dumps(body, ensure_ascii=False).encode() if body is not None else None)
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                return json.loads(response.read())
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode(errors="replace")
            try:
                error = json.loads(raw).get("error", {})
                reason = ", ".join(item.get("reason", "") for item in error.get("errors", []))
                message = error.get("message") or raw
            except ValueError:
                reason, message = "", raw
            err = RuntimeError(f"BigQuery state error (HTTP {exc.code}): {message}")
            err.reason = reason
            err.status = exc.code
            raise err from None

    def _query(self, sql: str, **params) -> dict:
        data = self._post({"query": sql, "useLegacySql": False, "timeoutMs": 60000,
                           "parameterMode": "NAMED",
                           "queryParameters": [_parameter(name, value) for name, value in params.items()]})
        deadline = time.monotonic() + QUERY_DEADLINE
        while not data.get("jobComplete", True):
            ref = data.get("jobReference") or {}
            job_id = ref.get("jobId")
            if not job_id:
                raise RuntimeError("BigQuery state query is incomplete and has no job id")
            if time.monotonic() > deadline:
                raise RuntimeError(f"BigQuery state query {job_id} did not finish within {QUERY_DEADLINE} seconds")
            _, project = self._token()
            query = urllib.parse.urlencode({"timeoutMs": 60000, **({"location": ref["location"]} if ref.get("location") else {})})
            data = self._call(f"https://bigquery.googleapis.com/bigquery/v2/projects/{project}/queries/"
                              f"{urllib.parse.quote(job_id)}?{query}")
        errors = data.get("errors") or []
        if errors:
            err = RuntimeError("BigQuery state query failed: " + "; ".join(e.get("message", str(e)) for e in errors))
            err.reason = ", ".join(e.get("reason", "") for e in errors)
            raise err
        return data

    def _project(self) -> str:
        project = self.project or self._token()[1]
        if not _PROJECT.fullmatch(project):
            raise ValueError(f"{project!r} is not a Google Cloud project identifier")
        return project

    @staticmethod
    def _denied(exc: Exception) -> bool:
        """The CREATE itself refused for want of a permission (bigquery.tables.create): BigQuery's accessDenied reason,
        whether the first reply (HTTP 403) or the finished job carries it. Matched on the structured reason and status,
        never on free text, so another failure that mentions a permission is raised. A refusal of the query job itself
        (bigquery.jobs.create, also accessDenied) is not the CREATE's: no read could run either, so it is raised too.
        That BigQuery names the missing permission in the message is an assumption tested against stand-ins only."""
        reasons = {part.strip() for part in str(getattr(exc, "reason", "") or "").split(",")}
        status = getattr(exc, "status", None)
        return "accessDenied" in reasons and status in (None, 403) and "bigquery.jobs.create" not in str(exc)

    def _ensure(self) -> None:
        """Make the state tables once per process and dataset. An account that may read them but not create tables
        (no bigquery.tables.create) goes on to read the tables the job made, and a table still missing is reported by
        the read that needs it; its refused CREATE is not remembered, so the next call tries it again, as one whose
        grant was still propagating succeeds. Any other failure, the sign-in's included, is raised, and the creation
        tried again."""
        if self._ready:
            return
        key = (self._project(), self.dataset)
        if key in _ENSURED:
            self._ready = True
            return
        root = f"`{self._project()}.{self.dataset}"
        try:
            self._query(f"""
CREATE TABLE IF NOT EXISTS {root}.unit_manifest` (unit STRING NOT NULL, revision INT64 NOT NULL, manifest JSON NOT NULL);
CREATE TABLE IF NOT EXISTS {root}.unit_history` (unit STRING NOT NULL, sequence INT64 NOT NULL, entry JSON NOT NULL);
CREATE TABLE IF NOT EXISTS {root}.unit_requests` (unit STRING NOT NULL, request_id STRING NOT NULL, request JSON NOT NULL);
CREATE TABLE IF NOT EXISTS {root}.onboard_job` (job_id STRING NOT NULL, revision INT64 NOT NULL, state JSON NOT NULL);
""")
        except StateUnavailable:
            raise
        except RuntimeError as exc:
            if not self._denied(exc):
                raise
            return
        _ENSURED.add(key)
        self._ready = True

    def units(self) -> list[str]:
        self._ensure()
        return [row["unit"] for row in _rows(self._query(
            f"SELECT unit FROM `{self._project()}.{self.dataset}.unit_manifest` ORDER BY unit"))]

    def manifest(self, unit: str) -> dict:
        self._ensure()
        found = _rows(self._query(
            f"SELECT revision, TO_JSON_STRING(manifest) AS manifest FROM `{self._project()}.{self.dataset}.unit_manifest` WHERE unit=@unit",
            unit=unit))
        if not found:
            raise FileNotFoundError(unit)
        manifest = json.loads(found[0]["manifest"])
        manifest["_app_revision"] = int(found[0]["revision"])
        return manifest

    def history(self, unit: str) -> list[dict]:
        self._ensure()
        return [json.loads(row["entry"]) for row in _rows(self._query(
            f"SELECT TO_JSON_STRING(entry) AS entry FROM `{self._project()}.{self.dataset}.unit_history` WHERE unit=@unit ORDER BY sequence",
            unit=unit))]

    def snapshot(self, unit: str) -> tuple[dict, list[dict]]:
        """Read page state from one BigQuery statement, so it cannot straddle a commit. The history's subquery is matched
        on the unit's parameter, not correlated on the manifest's row: BigQuery refuses a correlated subquery over
        unit_history, whose entry is JSON, as one it cannot de-correlate (HTTP 400)."""
        self._ensure()
        found = _rows(self._query(f"""
SELECT revision, TO_JSON_STRING(manifest) AS manifest,
       TO_JSON_STRING(ARRAY(SELECT AS VALUE entry FROM `{self._project()}.{self.dataset}.unit_history` h
                            WHERE h.unit=@unit ORDER BY sequence)) AS history
FROM `{self._project()}.{self.dataset}.unit_manifest` m WHERE unit=@unit
""", unit=unit))
        if not found:
            raise FileNotFoundError(unit)
        manifest = json.loads(found[0]["manifest"])
        manifest["_app_revision"] = int(found[0]["revision"])
        return manifest, json.loads(found[0]["history"] or "[]")

    @staticmethod
    def _aborted(exc: Exception) -> bool:
        return "aborted" in f"{getattr(exc, 'reason', '')} {exc}".lower()

    def commit(self, unit: str, manifest: dict, additions: list[dict]) -> dict:
        """Commit as FilesState.commit does, leaving the caller's dict as it was: a request closed once is recorded
        once, so a second closing of it, in this commit or an earlier one, is left out."""
        self._ensure()
        manifest = dict(manifest)
        expected = int(manifest.pop("_app_revision", 0))
        next_revision = expected + 1
        sql = f"""
BEGIN TRANSACTION;
ASSERT (@expected=0 AND (SELECT COUNT(*) FROM `{self._project()}.{self.dataset}.unit_manifest` WHERE unit=@unit)=0)
    OR (@expected>0 AND (SELECT COUNT(*) FROM `{self._project()}.{self.dataset}.unit_manifest` WHERE unit=@unit AND revision=@expected)=1)
    AS 'stale revision';
MERGE `{self._project()}.{self.dataset}.unit_manifest` T USING (SELECT @unit unit) S ON T.unit=S.unit
WHEN MATCHED THEN UPDATE SET revision=@next_revision, manifest=PARSE_JSON(@manifest)
WHEN NOT MATCHED THEN INSERT(unit, revision, manifest) VALUES(@unit, @next_revision, PARSE_JSON(@manifest));
INSERT `{self._project()}.{self.dataset}.unit_history` (unit, sequence, entry)
SELECT @unit, @expected * 1000000 + offset, PARSE_JSON(raw_entry) FROM UNNEST(JSON_VALUE_ARRAY(@history)) raw_entry WITH OFFSET
WHERE NOT (JSON_VALUE(PARSE_JSON(raw_entry), '$.step') = 'request-closed' AND EXISTS (
    SELECT 1 FROM `{self._project()}.{self.dataset}.unit_history` h WHERE h.unit=@unit
    AND JSON_VALUE(h.entry, '$.step') = 'request-closed'
    AND IFNULL(JSON_VALUE(h.entry, '$.detail.id'), '') = IFNULL(JSON_VALUE(PARSE_JSON(raw_entry), '$.detail.id'), '')));
DELETE FROM `{self._project()}.{self.dataset}.unit_requests` WHERE unit=@unit;
INSERT `{self._project()}.{self.dataset}.unit_requests` (unit, request_id, request)
SELECT @unit, JSON_VALUE(raw_request, '$.id'), PARSE_JSON(raw_request) FROM UNNEST(JSON_VALUE_ARRAY(@requests)) raw_request;
COMMIT TRANSACTION;
"""
        closed, kept = set(), []
        for entry in additions:
            if entry.get("step") == "request-closed":
                key = (entry.get("detail") or {}).get("id")
                if key in closed:
                    continue
                closed.add(key)
            kept.append(entry)
        params = {"unit": unit, "expected": expected, "next_revision": next_revision,
                  "manifest": json.dumps(manifest, ensure_ascii=False),
                  "history": json.dumps([json.dumps(entry, ensure_ascii=False) for entry in kept]),
                  "requests": json.dumps([json.dumps(request, ensure_ascii=False)
                                            for request in manifest.get("requests", [])])}
        for attempt in range(ABORT_RETRIES + 1):
            try:
                self._query(sql, **params)
                manifest["_app_revision"] = next_revision
                return manifest
            except RuntimeError as exc:
                if "stale revision" in str(exc).lower():
                    raise StateConflict(f"unit {unit} changed by someone else; reload") from None
                if not self._aborted(exc) or attempt == ABORT_RETRIES:
                    if self._aborted(exc):
                        raise RuntimeError(f"unit {unit} update aborted after {ABORT_RETRIES + 1} attempts") from None
                    raise
                self._sleep(0.05 * 2 ** attempt)
        raise AssertionError("unreachable")

    def restore(self, manifests: dict[str, dict], additions: dict[str, list[dict]], drop=()) -> None:
        """Put units' manifests back as a rollback restores them (kge/graph/history.py restore_pack), in one transaction:
        each manifest in manifests replaces its unit's row (or makes one) with its requests, and each unit in drop loses
        its manifest, request and history rows, as a rollback to before it removes its files, units/<unit>/ with its
        history.jsonl, on the files backend: onboarded again, it starts a history of its own, at sequence 0, with none
        of the removed unit's entries to collide with or close its requests. The revision only goes up, so a reader
        holding the replaced one is refused (StateConflict), and a restored unit's history is never erased: additions'
        entries are appended to each unit's, past every entry it holds (a dropped unit's are not written). A manifest
        that carries the _app_revision it was captured at comes back above that revision, though the rows were dropped
        in between (a failed rollback's undo, kge/graph/history.py _restore_captured_store)."""
        self._ensure()
        root = f"`{self._project()}.{self.dataset}"
        man, hist, req = f"{root}.unit_manifest`", f"{root}.unit_history`", f"{root}.unit_requests`"
        statements, params = [], {}

        def append(unit_param: str, entries_param: str) -> str:
            return f"""INSERT {hist} (unit, sequence, entry)
SELECT @{unit_param}, GREATEST(COALESCE((SELECT revision FROM {man} WHERE unit=@{unit_param}), 0) * 1000000,
                     COALESCE((SELECT MAX(sequence) + 1 FROM {hist} WHERE unit=@{unit_param}), 0)) + offset, PARSE_JSON(raw_entry)
FROM UNNEST(JSON_VALUE_ARRAY(@{entries_param})) raw_entry WITH OFFSET;"""

        def entries(unit: str) -> str:
            return json.dumps([json.dumps(entry, ensure_ascii=False) for entry in additions.get(unit, [])])

        for i, (unit, manifest) in enumerate(sorted(manifests.items())):
            floor = int(manifest.get("_app_revision") or 0) + 1 if "_app_revision" in manifest else 0
            manifest = {k: v for k, v in manifest.items() if k != "_app_revision"}
            params.update({f"unit_{i}": unit, f"history_{i}": entries(unit), f"floor_{i}": floor,
                           f"manifest_{i}": json.dumps(manifest, ensure_ascii=False),
                           f"requests_{i}": json.dumps([json.dumps(r, ensure_ascii=False)
                                                        for r in manifest.get("requests", [])])})
            statements += [append(f"unit_{i}", f"history_{i}"), f"""MERGE {man} T
USING (SELECT @unit_{i} unit, GREATEST(DIV((SELECT COALESCE(MAX(sequence), -1) FROM {hist} WHERE unit=@unit_{i}), 1000000) + 1,
                                    @floor_{i}) least_revision) S
ON T.unit=S.unit
WHEN MATCHED THEN UPDATE SET revision=GREATEST(T.revision + 1, S.least_revision), manifest=PARSE_JSON(@manifest_{i})
WHEN NOT MATCHED THEN INSERT(unit, revision, manifest) VALUES(S.unit, S.least_revision, PARSE_JSON(@manifest_{i}));""",
                           f"DELETE FROM {req} WHERE unit=@unit_{i};",
                           f"""INSERT {req} (unit, request_id, request)
SELECT @unit_{i}, JSON_VALUE(raw_request, '$.id'), PARSE_JSON(raw_request) FROM UNNEST(JSON_VALUE_ARRAY(@requests_{i})) raw_request;"""]
        for j, unit in enumerate(sorted(set(drop) - set(manifests))):
            params[f"drop_{j}"] = unit
            statements += [f"DELETE FROM {hist} WHERE unit=@drop_{j};",
                           f"DELETE FROM {req} WHERE unit=@drop_{j};",
                           f"DELETE FROM {man} WHERE unit=@drop_{j};"]
        if not statements:
            return
        sql = "BEGIN TRANSACTION;\n" + "\n".join(statements) + "\nCOMMIT TRANSACTION;\n"
        for attempt in range(ABORT_RETRIES + 1):
            try:
                self._query(sql, **params)
                return
            except RuntimeError as exc:
                if not self._aborted(exc) or attempt == ABORT_RETRIES:
                    if self._aborted(exc):
                        raise RuntimeError(f"the units' rollback aborted after {ABORT_RETRIES + 1} attempts") from None
                    raise
                self._sleep(0.05 * 2 ** attempt)

    def delete(self, unit: str) -> None:
        self._ensure()
        self._query(f"""
BEGIN TRANSACTION;
DELETE FROM `{self._project()}.{self.dataset}.unit_requests` WHERE unit=@unit;
DELETE FROM `{self._project()}.{self.dataset}.unit_history` WHERE unit=@unit;
DELETE FROM `{self._project()}.{self.dataset}.unit_manifest` WHERE unit=@unit;
COMMIT TRANSACTION;
""", unit=unit)

    def job(self) -> dict | None:
        self._ensure()
        found = _rows(self._query(
            f"SELECT TO_JSON_STRING(state) AS state FROM `{self._project()}.{self.dataset}.onboard_job` "
            "WHERE job_id=@job_id", job_id="current"))
        return json.loads(found[0]["state"]) if found else None

    def save_job(self, job: dict) -> None:
        """Replace the client-wide job record in one transaction."""
        self._ensure()
        self._query(f"""
BEGIN TRANSACTION;
MERGE `{self._project()}.{self.dataset}.onboard_job` T USING (SELECT @job_id job_id) S ON T.job_id=S.job_id
WHEN MATCHED THEN UPDATE SET revision=T.revision+1, state=PARSE_JSON(@state)
WHEN NOT MATCHED THEN INSERT(job_id, revision, state) VALUES(@job_id, 1, PARSE_JSON(@state));
COMMIT TRANSACTION;
""", job_id="current", state=json.dumps(job, ensure_ascii=False))

    def clear_job(self) -> None:
        """Remove the client-wide job record (the last onboarded unit's reset)."""
        self._ensure()
        self._query(f"DELETE FROM `{self._project()}.{self.dataset}.onboard_job` WHERE job_id=@job_id", job_id="current")
