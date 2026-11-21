"""Session state, independent of onboarding and access grants, selected by SESSION_STATE_BACKEND."""
from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path

from kge.adapters.deadline import ExternalCallError
from kge.adapters.objstore import open_store, under
from kge.pipeline.settings import settings
from kge.ports import SessionState

__all__ = ["SessionState", "for_profile", "identity_refresher", "local_identity"]


def local_identity(folder) -> SessionState:
    """Uncached credentials for local agents sharing their run folder."""
    from kge.adapters.sessionstate.store import StoreSessionState
    return StoreSessionState(open_store(str(Path(folder).resolve()), private=True), cache_seconds=0, legacy=True)


def for_profile(prof) -> SessionState:
    """Prime the selected adapter; idle requests share a refresh of its snapshot."""
    backend = (settings().SESSION_STATE_BACKEND or "store").strip().lower() or "store"
    if backend == "store":
        from kge.adapters.sessionstate.store import StoreSessionState
        url = (settings().STORE_URL or "").strip()
        state = (StoreSessionState(open_store(under(url, "access", prof.client, "identity"), private=True))
                 if url else local_identity(prof.run_dir()))
    elif backend == "bigquery":
        from kge.adapters.sessionstate.bigquery import BigQuerySessionState
        state = BigQuerySessionState(prof)
    else:
        raise ValueError(f"SESSION_STATE_BACKEND must be store or bigquery, not {backend!r}")
    try:
        state.refresh_identity()
    except ExternalCallError:
        pass
    return state


@contextmanager
def identity_refresher(state: SessionState):
    """Keep the instance's snapshot current for the serving lifespan."""
    state.start_identity_refresh()
    try:
        yield
    finally:
        state.stop_identity_refresh()
