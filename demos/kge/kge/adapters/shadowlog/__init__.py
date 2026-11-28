"""The shadow log, the ShadowLog port (kge/ports.py): JSON lines, one file a release, under the run state's shadow/
folder (folder.py), or, with STORE_URL set, one object an observation under shadow/<client>/<release>/ in that object
store (objstore.py). Callers take a log from shadow_log, never from a backend's module."""
from __future__ import annotations

from pathlib import Path

from kge.adapters.objstore import open_store
from kge.pipeline.settings import settings
from kge.ports import ShadowLog, ShadowObservation, ShadowSeals

__all__ = ["ShadowLog", "ShadowObservation", "ShadowSeals", "shadow_log", "shadow_seals"]


def shadow_log(state_dir: Path, client: str) -> ShadowLog:
    """The log this deployment keeps: the object store STORE_URL names, else <state_dir>/shadow/."""
    url = (settings().STORE_URL or "").strip()
    if url:
        from kge.adapters.shadowlog.objstore import StoreShadowLog
        return StoreShadowLog(open_store(url, private=True), client)
    from kge.adapters.shadowlog.folder import FolderShadowLog
    return FolderShadowLog(Path(state_dir) / "shadow")


def shadow_seals(state_dir: Path, client: str) -> ShadowSeals:
    """The job's durable event records, in the same deployment store as the shadow log."""
    from kge.adapters.shadowlog.seals import StoreShadowSeals
    url = (settings().STORE_URL or "").strip()
    if url:
        return StoreShadowSeals(open_store(url, private=True), client)
    root = (Path(state_dir) / "shadow").resolve()
    return StoreShadowSeals(open_store(str(root), private=True), None)
