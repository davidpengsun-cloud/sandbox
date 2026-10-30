"""Record graph lifecycle events in unit application state.

This is the platform-facing seam: graph release code must not depend on the
estate workflow that originally created a unit.
"""
from __future__ import annotations

import datetime as dt

from kge import packstore
from kge.adapters import appstate


def record(prof, step: str, by: str, detail: dict, units: list[str] | None = None) -> None:
    """Record a step in each unit's history; a unit folder with no manifest yet, a create that stopped, is skipped. On
    release the unit version a hash carries is the one current when it was first released, so a rollback to an
    earlier hash makes that version live again, and an update landed since is not released."""
    store = packstore.for_profile(prof)
    state = appstate.for_profile(prof, store)
    with state.lock():
        known = state.units()
        for unit in known if units is None else units:
            if unit not in known:
                continue
            try:
                manifest = state.manifest(unit)
            except FileNotFoundError:
                continue
            if step == "release":
                released = dict(manifest.get("released") or {})
                version = released.setdefault(detail.get("model_hash"), manifest.get("version", 1))
                manifest["released"] = released
                manifest["release"] = {"pack_version": detail.get("pack_version"),
                                       "model_hash": detail.get("model_hash"), "version": version}
            entry = {"at": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                     "by": by, "step": step, "version": manifest.get("version", 1), "detail": detail}
            state.commit(unit, manifest, [entry])
