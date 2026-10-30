"""Onboarding application state, the AppState port (kge/ports.py): files in the pack store, or BigQuery tables with
APP_STATE_BACKEND=bigquery. Callers take a backend from for_profile, or job_backend for the job runner, never from a
backend's module."""
from __future__ import annotations

from kge.adapters.appstate.bigquery import BigQueryState
from kge.adapters.appstate.files import FilesState
from kge.pipeline.settings import settings
from kge.ports import AppState, StateConflict, StateUnavailable

__all__ = ["AppState", "StateConflict", "StateUnavailable", "for_profile", "job_backend", "selected"]


def _backend() -> str:
    return (settings().APP_STATE_BACKEND or "files").strip().lower() or "files"


def selected() -> str:
    """The backend APP_STATE_BACKEND names, files or bigquery; ValueError naming the setting for any other value."""
    backend = _backend()
    if backend not in ("files", "bigquery"):
        raise ValueError(f"APP_STATE_BACKEND must be files or bigquery, not {backend!r}")
    return backend


def for_profile(prof, store) -> AppState:
    backend = selected()
    held = getattr(store, "_appstate_backend", None)
    if held is not None and held[0] == backend:
        return held[1]
    if backend == "files":
        state = FilesState(store)
    else:
        state = BigQueryState(prof, store)
    store._appstate_backend = (backend, state)
    return state


def job_backend(prof) -> AppState | None:
    """The backend that keeps the onboarding job's record, for the job runner, which has no pack store: BigQuery's
    with APP_STATE_BACKEND=bigquery; None otherwise, where the record is a file in the object store."""
    return BigQueryState(prof) if _backend() == "bigquery" else None
