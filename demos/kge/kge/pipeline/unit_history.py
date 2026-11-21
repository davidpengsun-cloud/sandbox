"""Record graph lifecycle events in unit application state.

This is the platform-facing seam: graph release code must not depend on the
estate workflow that originally created a unit.
"""
from __future__ import annotations

import contextlib
import datetime as dt

from kge import packstore
from kge.adapters import appstate


def record(prof, step: str, by: str, detail: dict, units: list[str] | None = None, *, locked: bool = False) -> None:
    """Record reviews even for base units without manifests; lifecycle steps only apply to onboarded units. On
    release the unit version a hash carries is the one current when it was first released, so a rollback to an
    earlier hash makes that version live again, and an update landed since is not released."""
    store = packstore.for_profile(prof)
    state = appstate.for_profile(prof, store)
    with contextlib.nullcontext() if locked else state.lock():
        known = state.units()
        for unit in known if units is None else units:
            try:
                manifest = state.manifest(unit)
            except FileNotFoundError:
                if step not in {"accept", "certify"}:
                    continue
                entry = {"at": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                         "by": by, "step": step, "version": 1, "detail": detail}
                state.append_history(unit, [entry])
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


def review_audit(prof, unit: str, version: int | None = None, scope: str | None = None, *, entries=None) -> dict:
    """Item review provenance from history, independent of today's grants.

    Unit versions bound the review so a newer pending update cannot speak for a
    released item. Scope separates proposal and metadata items from draft ids.
    """
    if entries is None:
        entries = appstate.for_profile(prof, packstore.for_profile(prof)).history(unit)
    audit = {}
    for entry in entries:
        step, detail = entry.get("step"), entry.get("detail") or {}
        if step not in {"accept", "certify"} or detail.get("scope") != scope:
            continue
        if version is not None and entry.get("version", 1) > version:
            continue
        for item in detail.get("items", []):
            if step == "accept":
                audit[item] = {}
            audit.setdefault(item, {})["accepted" if step == "accept" else "certified"] = {
                "by": entry["by"], "as": detail.get("as"), "on": detail.get("on") or entry["at"][:10]}
    return audit
