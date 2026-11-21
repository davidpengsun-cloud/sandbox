"""Onboarding application state, the AppState port (kge/ports.py): files in the pack store, or BigQuery tables with
APP_STATE_BACKEND=bigquery. Callers take a backend from for_profile, or job_backend for the job runner, never from a
backend's module."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone

from kge.adapters.objstore import open_store, under
from kge.pipeline.settings import settings
from kge.ports import AppState, StateConflict, StateUnavailable

__all__ = ["AppState", "StateConflict", "StateUnavailable", "for_profile", "access_state", "job_backend", "selected"]


def _digest(rows: list[dict]) -> str:
    canonical = [{"username": row["username"], "name": row["name"], "roles": sorted(row["roles"])}
                 for row in sorted(rows, key=lambda row: row["username"])]
    return hashlib.sha256(json.dumps(canonical, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()


def _change(rows: list[dict], sequence: int, last_digest: str | None, username: str, row: dict | None, by: str, via: str):
    """Build both sides of one access commit without mutating a caller's rows."""
    if via not in ("api", "job", "migration"):
        raise ValueError("access via must be api, job or migration")
    if not username:
        raise ValueError("access username must not be empty")
    before = next((r for r in rows if r["username"] == username), None)
    if row is None and before is None:
        return rows, None
    at = datetime.now(timezone.utc).isoformat(timespec="microseconds")
    after = {"username": username, "name": row.get("name", ""), "roles": sorted(set(row["roles"])),
             "updated_at": at, "updated_by": by} if row is not None else None
    updated = [r for r in rows if r["username"] != username] + ([after] if after is not None else [])
    updated.sort(key=lambda r: r["username"])
    entry = {"sequence": sequence + 1, "at": at, "by": by, "via": via, "username": username,
             "before": before, "after": after, "digest": _digest(updated),
             "outside": _digest(rows) != (last_digest if last_digest is not None else _digest([]))}
    return updated, entry


def _access_url(prof, store_url: str | None) -> str:
    url = (store_url or "").strip()
    return under(url, "access", prof.client) if url else str((prof.store / prof.client / "access").resolve())


def _backend() -> str:
    return (settings().APP_STATE_BACKEND or "files").strip().lower() or "files"


def selected() -> str:
    """The backend APP_STATE_BACKEND names, files or bigquery; ValueError naming the setting for any other value."""
    backend = _backend()
    if backend not in ("files", "bigquery"):
        raise ValueError(f"APP_STATE_BACKEND must be files or bigquery, not {backend!r}")
    return backend


def for_profile(prof, store) -> AppState:
    from kge.adapters.appstate.bigquery import BigQueryState
    from kge.adapters.appstate.files import FilesState
    backend = selected()
    store_url = settings().STORE_URL
    key = (backend, prof.client, getattr(prof, "store", None), store_url) if backend == "files" else (backend, prof.client)
    held = getattr(store, "_appstate_backend", None)
    if held is not None and held[0] == key:
        return held[1]
    if backend == "files":
        state = FilesState(store, lambda: open_store(_access_url(prof, store_url)))
    else:
        state = BigQueryState(prof, store)
    store._appstate_backend = (key, state)
    return state


def access_state(prof) -> AppState:
    """The same access backend for a job or service with no pack store."""
    from kge.adapters.appstate.bigquery import BigQueryState
    from kge.adapters.appstate.files import FilesState
    return (BigQueryState(prof) if selected() == "bigquery"
            else FilesState(None, open_store(_access_url(prof, settings().STORE_URL))))


def identity_records(prof):
    """The existing BigQuery records reused by the session-state BigQuery adapter."""
    from kge.adapters.appstate.bigquery import BigQueryState
    return BigQueryState(prof)


def job_backend(prof) -> AppState | None:
    """The backend that keeps the onboarding job's record, for the job runner, which has no pack store: BigQuery's
    with APP_STATE_BACKEND=bigquery; None otherwise, where the record is a file in the object store."""
    from kge.adapters.appstate.bigquery import BigQueryState
    return BigQueryState(prof) if _backend() == "bigquery" else None
