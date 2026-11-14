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
from datetime import datetime, timezone
from typing import Any

from kge.adapters import http
from kge.adapters.deadline import query_budget, request_cap, request_io, request_remaining
from kge.adapters.bigquery import DEFAULT_DATASET_PREFIX, bq_account, dataset_name
from kge.adapters.appstate import _change, _digest
from kge.pipeline.settings import settings
from kge.ports import StateConflict, StateUnavailable

ABORT_RETRIES = 3
QUERY_DEADLINE = 300
_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,1023}\Z")
_PROJECT = re.compile(r"[a-z][a-z0-9:.-]{4,61}[a-z0-9]\Z")


_ENSURED: set[tuple[str, str]] = set()
_IDENTITY_ENSURED: set[tuple[str, str]] = set()


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
        self._access_read = None

    def _ensure_identity(self):
        key = (self._project(), self.dataset)
        if key in _IDENTITY_ENSURED:
            return
        root = f"`{self._project()}.{self.dataset}"
        self._query(f"""
CREATE TABLE IF NOT EXISTS {root}.access_identity_tokens` (principal STRING, token STRING, revision INT64)
AS SELECT '' AS principal, '' AS token, 0 AS revision;
CREATE TABLE IF NOT EXISTS {root}.access_signed_out` (sid STRING, expires INT64);
""")
        _IDENTITY_ENSURED.add(key)

    def _prepare_identity(self):
        self._ensure_identity()

    def _identity_snapshot(self) -> dict:
        root = f"`{self._project()}.{self.dataset}"
        found = _rows(self._query(f"""
SELECT TO_JSON_STRING(ARRAY(SELECT AS STRUCT principal, token FROM {root}.access_identity_tokens`
                           WHERE principal!='')) AS tokens,
       TO_JSON_STRING(ARRAY(SELECT AS STRUCT sid, expires FROM {root}.access_signed_out`
                           WHERE expires>UNIX_SECONDS(CURRENT_TIMESTAMP()))) AS signed_out
"""))
        if len(found) != 1:
            raise StateUnavailable("shared identity state cannot be read")
        return {"tokens": {row["principal"]: row["token"] for row in json.loads(found[0]["tokens"])},
                "signed_out": {row["sid"]: int(row["expires"]) for row in json.loads(found[0]["signed_out"])}}

    def _identity_token(self, principal: str, candidate: str) -> str:
        self._ensure_identity()
        table = f"`{self._project()}.{self.dataset}.access_identity_tokens`"
        for attempt in range(ABORT_RETRIES + 1):
            try:
                found = _rows(self._query(f"""
BEGIN TRANSACTION;
ASSERT (SELECT COUNT(*) FROM {table} WHERE principal='')=1 AS 'identity revision row missing';
UPDATE {table} SET revision=revision+1 WHERE principal='';
INSERT {table} (principal, token, revision)
SELECT @principal, @token, 0 WHERE NOT EXISTS (SELECT 1 FROM {table} WHERE principal=@principal);
COMMIT TRANSACTION;
SELECT token FROM {table} WHERE principal=@principal;
""", principal=principal, token=candidate))
                if len(found) != 1:
                    raise StateUnavailable("shared personal token cannot be read")
                return found[0]["token"]
            except RuntimeError as exc:
                if not self._aborted(exc) or attempt == ABORT_RETRIES:
                    raise
                self._sleep(0.05 * 2 ** attempt)
        raise AssertionError("unreachable")

    def _identity_sign_out(self, sid: str, expires: int) -> None:
        self._ensure_identity()
        table = f"`{self._project()}.{self.dataset}.access_signed_out`"
        self._query(f"""
INSERT {table} (sid, expires) VALUES(@sid, @expires);
DELETE FROM {table} WHERE expires<=UNIX_SECONDS(CURRENT_TIMESTAMP());
""", sid=sid, expires=expires)

    @contextlib.contextmanager
    def lock(self):
        """BigQuery serialises the manifest transaction; a local pack store still needs its filesystem lock to keep
        derived files in that order."""
        with self._store.lock() if self._store is not None else contextlib.nullcontext():
            yield

    @request_io
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

    @request_io
    def _call(self, url: str, body: dict | None = None) -> dict:
        """One BigQuery REST call, a POST with a body or a GET without. Every HTTP error is mapped the same way, the
        reason kept, so a failed transaction's 'stale revision' or 'aborted' is recognised whether the first reply
        or a later poll carries it."""
        import urllib.error
        import urllib.request
        token, _ = self._token()
        if body is not None:
            query_budget(body, QUERY_DEADLINE)
        request = urllib.request.Request(
            url, method="POST" if body is not None else "GET",
            headers={"authorization": f"Bearer {token}", **({"content-type": "application/json"} if body is not None else {})},
            data=json.dumps(body, ensure_ascii=False).encode() if body is not None else None)
        try:
            with http.open_http(request, timeout=request_cap(60)) as response:
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

    @request_io
    def _query(self, sql: str, **params) -> dict:
        body = {"query": sql, "useLegacySql": False, "timeoutMs": 60000,
                "parameterMode": "NAMED",
                "queryParameters": [_parameter(name, value) for name, value in params.items()]}
        for attempt in range(ABORT_RETRIES + 1):
            try:
                query_budget(body, QUERY_DEADLINE)
                data = self._post(body)
                break
            except RuntimeError as exc:
                if not self._rate_limited(exc) or attempt == ABORT_RETRIES:
                    raise
                self._sleep(request_cap(0.05 * 2 ** attempt))
        deadline = time.monotonic() + QUERY_DEADLINE
        while not data.get("jobComplete", True):
            request_remaining()
            ref = data.get("jobReference") or {}
            job_id = ref.get("jobId")
            if not job_id:
                raise RuntimeError("BigQuery state query is incomplete and has no job id")
            if time.monotonic() > deadline:
                raise RuntimeError(f"BigQuery state query {job_id} did not finish within {QUERY_DEADLINE} seconds")
            _, project = self._token()
            query = urllib.parse.urlencode({"timeoutMs": max(1, int(request_cap(60) * 1000)), **({"location": ref["location"]} if ref.get("location") else {})})
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
CREATE TABLE IF NOT EXISTS {root}.unit_history_lock` (revision INT64 NOT NULL) AS SELECT 0 AS revision;
CREATE TABLE IF NOT EXISTS {root}.unit_history` (unit STRING NOT NULL, sequence INT64 NOT NULL, entry JSON NOT NULL);
CREATE TABLE IF NOT EXISTS {root}.unit_requests` (unit STRING NOT NULL, request_id STRING NOT NULL, request JSON NOT NULL);
CREATE TABLE IF NOT EXISTS {root}.onboard_job` (job_id STRING NOT NULL, revision INT64 NOT NULL, state JSON NOT NULL);
CREATE TABLE IF NOT EXISTS {root}.access_grants`
    (username STRING NOT NULL, revision INT64, name STRING, roles JSON, updated_at TIMESTAMP, updated_by STRING)
AS SELECT '' AS username, 0 AS revision, CAST(NULL AS STRING) AS name, CAST(NULL AS JSON) AS roles,
          CAST(NULL AS TIMESTAMP) AS updated_at, CAST(NULL AS STRING) AS updated_by;
CREATE TABLE IF NOT EXISTS {root}.access_audit`
    (sequence INT64, `at` TIMESTAMP, `by` STRING, via STRING, username STRING, before JSON, after JSON, digest STRING, outside BOOL);
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

    def append_history(self, unit: str, entries: list[dict]) -> None:
        """Append without onboarding a base unit. The singleton lock row serializes even two first appends, and
        shares the transaction boundary with manifest commits; INSERT-only transactions would not conflict."""
        if not entries:
            return
        self._ensure()
        root = f"`{self._project()}.{self.dataset}"
        sql = f"""
BEGIN TRANSACTION;
UPDATE {root}.unit_history_lock` SET revision=revision+1 WHERE TRUE;
INSERT {root}.unit_history` (unit, sequence, entry)
SELECT @unit, (SELECT IFNULL(MAX(sequence), -1) + 1 FROM {root}.unit_history` WHERE unit=@unit) + offset,
       PARSE_JSON(raw_entry) FROM UNNEST(JSON_VALUE_ARRAY(@history)) raw_entry WITH OFFSET;
COMMIT TRANSACTION;
"""
        for attempt in range(ABORT_RETRIES + 1):
            try:
                self._query(sql, unit=unit, history=json.dumps([json.dumps(entry, ensure_ascii=False) for entry in entries]))
                return
            except RuntimeError as exc:
                if not self._aborted(exc) or attempt == ABORT_RETRIES:
                    raise
                self._sleep(0.05 * 2 ** attempt)

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

    @staticmethod
    def _rate_limited(exc: Exception) -> bool:
        low = f"{getattr(exc, 'reason', '')} {exc}".lower()
        return getattr(exc, "status", None) == 429 or "ratelimitexceeded" in low or "exceeded rate limits" in low

    def capture_units(self, units) -> object:
        """Read each unit's full native records in one statement, retaining revisions, sequences and absent rows."""
        names = tuple(sorted(set(units)))
        if not names:
            return {}
        self._ensure()
        root = f"`{self._project()}.{self.dataset}"
        captured = {}
        for unit in names:
            columns = ",\n".join(
                f"TO_JSON_STRING(ARRAY(SELECT AS STRUCT {fields} FROM {root}.{table}` WHERE unit=@unit)) AS {table}"
                for table, fields in (("unit_manifest", "unit, revision, manifest"),
                                      ("unit_history", "unit, sequence, entry"),
                                      ("unit_requests", "unit, request_id, request")))
            row = _rows(self._query(f"SELECT {columns}", unit=unit))[0]
            captured[unit] = {table: json.loads(value) for table, value in row.items()}
        return captured

    def restore_units(self, captured) -> None:
        """Replace the captured units in one transaction, preserving native state rather than appending a rollback."""
        if not captured:
            return
        self._ensure()
        root = f"`{self._project()}.{self.dataset}"
        columns = {"unit_manifest": ("revision", "manifest"), "unit_history": ("sequence", "entry"),
                   "unit_requests": ("request_id", "request")}
        statements, params = [], {}
        for i, (unit, tables) in enumerate(sorted(captured.items())):
            unit_param = f"unit_{i}"
            params[unit_param] = unit
            for table, (number, content) in columns.items():
                target = f"{root}.{table}`"
                statements.append(f"DELETE FROM {target} WHERE unit=@{unit_param};")
                for j, row in enumerate(tables[table]):
                    key = f"{table}_{i}_{j}"
                    params[f"{key}_number"] = row[number] if number == "request_id" else int(row[number])
                    params[f"{key}_content"] = json.dumps(row[content], ensure_ascii=False)
                    statements.append(f"INSERT {target} (unit, {number}, {content}) VALUES "
                                      f"(@{unit_param}, @{key}_number, PARSE_JSON(@{key}_content));")
        sql = (f"BEGIN TRANSACTION;\nUPDATE {root}.unit_history_lock` SET revision=revision+1 WHERE TRUE;\n"
               + "\n".join(statements)) + "\nCOMMIT TRANSACTION;\n"
        for attempt in range(ABORT_RETRIES + 1):
            try:
                self._query(sql, **params)
                return
            except RuntimeError as exc:
                if not self._aborted(exc) or attempt == ABORT_RETRIES:
                    raise
                self._sleep(0.05 * 2 ** attempt)

    def commit(self, unit: str, manifest: dict, additions: list[dict]) -> dict:
        """Commit as FilesState.commit does, leaving the caller's dict as it was: a request closed once is recorded
        once, so a second closing of it, in this commit or an earlier one, is left out."""
        self._ensure()
        manifest = dict(manifest)
        expected = int(manifest.pop("_app_revision", 0))
        next_revision = expected + 1
        sql = f"""
BEGIN TRANSACTION;
UPDATE `{self._project()}.{self.dataset}.unit_history_lock` SET revision=revision+1 WHERE TRUE;
ASSERT (@expected=0 AND (SELECT COUNT(*) FROM `{self._project()}.{self.dataset}.unit_manifest` WHERE unit=@unit)=0)
    OR (@expected>0 AND (SELECT COUNT(*) FROM `{self._project()}.{self.dataset}.unit_manifest` WHERE unit=@unit AND revision=@expected)=1)
    AS 'stale revision';
MERGE `{self._project()}.{self.dataset}.unit_manifest` T USING (SELECT @unit unit) S ON T.unit=S.unit
WHEN MATCHED THEN UPDATE SET revision=@next_revision, manifest=PARSE_JSON(@manifest)
WHEN NOT MATCHED THEN INSERT(unit, revision, manifest) VALUES(@unit, @next_revision, PARSE_JSON(@manifest));
INSERT `{self._project()}.{self.dataset}.unit_history` (unit, sequence, entry)
SELECT @unit, (SELECT IFNULL(MAX(sequence), -1) + 1 FROM `{self._project()}.{self.dataset}.unit_history` WHERE unit=@unit) + offset, PARSE_JSON(raw_entry) FROM UNNEST(JSON_VALUE_ARRAY(@history)) raw_entry WITH OFFSET
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
        sql = (f"BEGIN TRANSACTION;\nUPDATE {root}.unit_history_lock` SET revision=revision+1 WHERE TRUE;\n"
               + "\n".join(statements)) + "\nCOMMIT TRANSACTION;\n"
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

    @staticmethod
    def _access_time(value: str) -> str:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc).isoformat(timespec="microseconds")

    def _access_snapshot(self):
        """The empty username is the revision row, created with the table. Every writer updates it, so concurrent
        transactions conflict even when adding different first users; an INSERT alone would not serialize them."""
        table = f"`{self._project()}.{self.dataset}.access_grants`"
        result = self._access_rows(f"""
SELECT (SELECT revision FROM {table} WHERE username='') AS revision,
       (SELECT digest FROM `{self._project()}.{self.dataset}.access_audit`
        ORDER BY sequence DESC LIMIT 1) AS last_digest,
       TO_JSON_STRING(ARRAY(SELECT AS STRUCT username, name, roles, updated_at, updated_by
                            FROM {table} WHERE username!='' ORDER BY username)) AS grants
""")
        if not result:
            result = self._access_rows(f"""
SELECT (SELECT revision FROM {table} WHERE username='') AS revision,
       TO_JSON_STRING(ARRAY(SELECT AS STRUCT username, name, roles, updated_at, updated_by
                            FROM {table} WHERE username!='' ORDER BY username)) AS grants
""")
            audit = self._access_rows(f"SELECT digest FROM `{self._project()}.{self.dataset}.access_audit` "
                                      "ORDER BY sequence DESC LIMIT 1")
            result = [{**(result[0] if result else {"revision": None if audit else 0, "grants": "[]"}),
                       "last_digest": audit[0]["digest"] if audit else None}]
        result = result[0]
        rows = json.loads(result["grants"])
        for row in rows:
            row["updated_at"] = self._access_time(row["updated_at"]) if row["updated_at"] else None
        return rows, int(result["revision"]) if result["revision"] is not None else None, result["last_digest"]

    def _access_rows(self, sql: str, **params) -> list[dict]:
        """Access reads never provision tables. Only a named, missing access table is an empty read;
        permission, query-job and transport failures remain failures."""
        try:
            return _rows(self._query(sql, **params))
        except RuntimeError as exc:
            reasons = {part.strip() for part in str(getattr(exc, "reason", "") or "").split(",")}
            location = re.escape(self._project()) + r"[:.]" + re.escape(self.dataset)
            missing = re.search(r"\b(?:Table " + location + r"\.(?:access_grants|access_audit)|Dataset "
                                + location + r")(?=\s|$)", str(exc))
            if reasons == {"notFound"} and getattr(exc, "status", None) in (None, 404) and missing:
                return []
            raise

    def access_grants(self) -> list[dict]:
        self._access_read = self._access_snapshot()
        return json.loads(json.dumps(self._access_read[0]))

    def access_digest(self) -> str:
        return _digest(self.access_grants())

    def access_put(self, row: dict, *, by: str, via: str) -> dict:
        return self._access_write(row["username"], row, by, via)

    def access_migrate(self, rows: list[dict], *, by: str) -> list[dict]:
        """Insert every seed row and audit entry in a single revision-guarded transaction."""
        held, revision, digest = self._access_read if self._access_read is not None else self._access_snapshot()
        self._access_read = None
        if held or revision != 0 or digest is not None:
            raise StateConflict("access already initialized; reload")
        grants, entries = [], []
        root = f"`{self._project()}.{self.dataset}"
        table, audit = f"{root}.access_grants`", f"{root}.access_audit`"
        params, statements = {"expected": revision}, []
        for i, row in enumerate(rows):
            grants, entry = _change(grants, len(entries), entries[-1]["digest"] if entries else None,
                                    row["username"], row, by, "migration")
            entries.append(entry)
            params.update({f"username_{i}": row["username"], f"sequence_{i}": entry["sequence"],
                           f"name_{i}": entry["after"]["name"], f"roles_{i}": json.dumps(entry["after"]["roles"]),
                           f"at_{i}": entry["at"], f"by_{i}": by, f"digest_{i}": entry["digest"],
                           f"after_{i}": json.dumps(entry["after"], ensure_ascii=False)})
            statements.append(f"""INSERT {table} (username, revision, name, roles, updated_at, updated_by)
VALUES(@username_{i}, @sequence_{i}, @name_{i}, PARSE_JSON(@roles_{i}), TIMESTAMP(@at_{i}), @by_{i});
INSERT {audit} (sequence, `at`, `by`, via, username, before, after, digest, outside)
VALUES(@sequence_{i}, TIMESTAMP(@at_{i}), @by_{i}, 'migration', @username_{i}, PARSE_JSON('null'),
       PARSE_JSON(@after_{i}), @digest_{i}, FALSE);""")
        params["sequence"] = len(entries)
        sql = f"""BEGIN TRANSACTION;
ASSERT (SELECT COUNT(*) FROM {table} WHERE username='' AND revision=@expected)=1 AS 'stale access revision';
ASSERT (SELECT COUNT(*) FROM {table} WHERE username!='')=0 AS 'stale access revision';
ASSERT (SELECT COUNT(*) FROM {audit})=0 AS 'stale access revision';
UPDATE {table} SET revision=@sequence WHERE username='';
""" + "\n".join(statements) + "\nCOMMIT TRANSACTION;"
        self._ensure()
        try:
            self._query(sql, **params)
        except RuntimeError as exc:
            if "stale access revision" in str(exc).lower() or self._aborted(exc):
                raise StateConflict("access changed by someone else; reload") from None
            raise
        return entries

    def access_delete(self, username: str, *, by: str, via: str) -> dict | None:
        return self._access_write(username, None, by, via)

    def access_audit(self, limit: int = 200) -> list[dict]:
        rows = self._access_rows(f"""
SELECT sequence, CAST(`at` AS STRING) AS `at`, `by`, via, username,
       TO_JSON_STRING(before) AS before, TO_JSON_STRING(after) AS after, digest, outside
FROM `{self._project()}.{self.dataset}.access_audit` ORDER BY sequence DESC LIMIT @limit
""", limit=max(0, limit))
        return [{**row, "sequence": int(row["sequence"]), "at": self._access_time(row["at"]),
                 "outside": str(row["outside"]).lower() == "true",
                 "before": json.loads(row["before"]), "after": json.loads(row["after"])} for row in rows]

    def _access_write(self, username: str, row: dict | None, by: str, via: str) -> dict | None:
        rows, revision, last_digest = self._access_read if self._access_read is not None else self._access_snapshot()
        self._access_read = None
        if revision is None:
            raise StateUnavailable("access revision row is missing; restore access state before changing grants")
        _, entry = _change(rows, revision, last_digest, username, row, by, via)
        if entry is None:
            if self._access_snapshot()[1] != revision:
                raise StateConflict("access changed by someone else; reload")
            return None
        self._ensure()
        root = f"`{self._project()}.{self.dataset}"
        grants, audit = f"{root}.access_grants`", f"{root}.access_audit`"
        params = {"expected": revision, "sequence": entry["sequence"], "username": username,
                  "outside": str(entry["outside"]).lower(),
                  "at": entry["at"], "by": by, "via": via, "digest": entry["digest"],
                  "before": json.dumps(entry["before"], ensure_ascii=False),
                  "after": json.dumps(entry["after"], ensure_ascii=False)}
        if row is None:
            mutation = f"DELETE FROM {grants} WHERE username=@username;"
        else:
            params.update(name=entry["after"]["name"], roles=json.dumps(entry["after"]["roles"]))
            mutation = f"""MERGE {grants} T USING (SELECT @username username) S ON T.username=S.username
WHEN MATCHED THEN UPDATE SET revision=@sequence, name=@name, roles=PARSE_JSON(@roles),
    updated_at=TIMESTAMP(@at), updated_by=@by
WHEN NOT MATCHED THEN INSERT(username, revision, name, roles, updated_at, updated_by)
    VALUES(@username, @sequence, @name, PARSE_JSON(@roles), TIMESTAMP(@at), @by);"""
        sql = f"""
BEGIN TRANSACTION;
ASSERT (SELECT COUNT(*) FROM {grants} WHERE username='' AND revision=@expected)=1 AS 'stale access revision';
UPDATE {grants} SET revision=@sequence WHERE username='';
{mutation}
INSERT {audit} (sequence, `at`, `by`, via, username, before, after, digest, outside)
VALUES(@sequence, TIMESTAMP(@at), @by, @via, @username, PARSE_JSON(@before), PARSE_JSON(@after), @digest, CAST(@outside AS BOOL));
COMMIT TRANSACTION;
"""
        try:
            self._query(sql, **params)
        except RuntimeError as exc:
            if "stale access revision" in str(exc).lower() or self._aborted(exc):
                raise StateConflict("access changed by someone else; reload") from None
            raise
        return entry
