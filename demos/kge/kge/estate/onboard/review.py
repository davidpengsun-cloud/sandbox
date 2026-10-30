"""The business team's acceptance and the steward's certification of drafted items (accept, certify), a unit's
or an open improvement proposal's."""
from __future__ import annotations

import copy
import datetime as dt
import os
import re

import yaml

from kge.compiler import pack as packs
from kge import packstore
from kge.adapters import appstate
from kge.pipeline.objstore import PENDING_PROPOSAL, read_proposal_record, proposal_pending
from kge.estate.onboard.common import (
    HEADER,
    NEEDS_ROWS,
    OnboardError,
    UPDATE,
    _accepted_items,
    _business,
    _commit_manifest,
    _history_entry,
    _item,
    _land,
    _manifest,
    _read_record,
    _record_bytes,
    _state,
    _steward,
    _unit,
)


def _open_proposal_id(store) -> str | None:
    pids = sorted({p.split("/")[1] for p in store.list("proposals") if p.endswith("/proposal.yaml") and p.count("/") == 2})
    return pids[-1] if pids else None


def require_no_pending_proposal(prof) -> None:
    """Keep every proposal awaiting human review, including one outside the selected unit."""
    store = packstore.for_profile(prof)
    for name in store.list("proposals"):
        if name.endswith("/proposal.yaml") and name.count("/") == 2:
            try:
                record = read_proposal_record(store, name)
            except ValueError as exc:
                raise OnboardError(409, str(exc)) from None
            if proposal_pending(record, prof.model_hash()):
                raise OnboardError(409, PENDING_PROPOSAL)


def _read_proposal(store, pid: str) -> dict:
    name = f"proposals/{pid}/proposal.yaml"
    if not store.exists(name):
        raise OnboardError(409, f"proposal {pid} does not exist: run improve first")
    try:
        return read_proposal_record(store, name)
    except ValueError as exc:
        raise OnboardError(409, str(exc)) from None


def _write_proposal(prof, store, pid: str, record: dict) -> None:
    data = (HEADER + yaml.safe_dump(record, sort_keys=False, width=4096)).encode()
    store.write(f"proposals/{pid}/proposal.yaml", data)


def _current_pack_hash(prof, store) -> str:
    if os.getenv("STORE_URL", "").strip():
        for name in ("pack.yaml", "pack.lock"):
            if store.exists(name):
                store.read(name)
        for name in store.list("units"):
            if name.endswith("/draft.yaml"):
                store.read(name)
    return packs.read(prof.clients, prof.client, lock=True, identities=prof.identities).hash


def _transition_proposal(prof, pid: str | None, item: str | list[str], who: str, on: str | None, certify_: bool) -> dict:
    (_steward if certify_ else _business)(prof, who)
    store, changed, unchanged = packstore.for_profile(prof), [], []
    with store.lock():
        pid = pid or _open_proposal_id(store)
        if not pid:
            raise OnboardError(409, "no open proposal: run improve first")
        if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,39}", pid):
            raise OnboardError(400, f"proposal id {pid!r} must match ^[a-z0-9][a-z0-9_-]{{0,39}}$")
        record = _read_proposal(store, pid)
        if record.get("heldout_fell"):
            raise OnboardError(409, f"proposal {pid} regressed on a held-out set: held-out score fell")
        if record.get("kept") is not True or not record.get("after_run_id"):
            raise OnboardError(409, f"proposal {pid} has not passed its candidate A/B check: run improve first")
        items = record.get("items") or []
        wanted = {x["id"] for x in items} if item == "all" else set(item if isinstance(item, list) else [item])
        if wanted - {x["id"] for x in items}:
            raise OnboardError(400, "unknown proposal item(s): " + ", ".join(sorted(wanted - {x["id"] for x in items})))
        if certify_:
            cur_hash = _current_pack_hash(prof, store)
            already_all_certified = all(x.get("certified_by") for x in items)
            if not already_all_certified and cur_hash != record.get("base_hash"):
                raise OnboardError(409, "the pack changed since this proposal: release or discard those edits, then run the job again")
        for entry in items:
            if entry["id"] not in wanted:
                continue
            field = "certified_by" if certify_ else "accepted_by"
            if entry.get(field):
                unchanged.append(entry["id"])
                continue
            if certify_ and not entry.get("accepted_by"):
                raise OnboardError(409, f"{entry['id']} has not been accepted")
            if certify_ and entry.get("accepted_by") == who:
                raise OnboardError(403, f"{who} accepted {entry['id']} and may not certify their own acceptance")
            entry[field], entry["certified_on" if certify_ else "accepted_on"] = (
                who,
                on or dt.datetime.now(dt.timezone.utc).date().isoformat(),
            )
            entry["state"] = "certified" if entry.get("certified_by") else "accepted" if entry.get("accepted_by") else "drafted"
            changed.append(entry["id"])
        all_certified = bool(items) and all(x.get("certified_by") for x in items)
        all_accepted = bool(items) and all(x.get("accepted_by") for x in items)
        record["state"] = "certified" if all_certified else "accepted" if all_accepted else "drafted"
        if changed:
            if certify_ and all_certified:
                prop_pack = f"proposals/{pid}/{prof.client}/pack.yaml"
                if not store.exists(prop_pack):
                    local_prop = prof.clients / prof.client / prop_pack
                    if local_prop.is_file():
                        store.write("pack.yaml", local_prop.read_bytes())
                    else:
                        raise OnboardError(409, f"proposal {pid} has no candidate pack.yaml")
                else:
                    store.write("pack.yaml", store.read(prop_pack))
            _write_proposal(prof, store, pid, record)
    return {"proposal": pid, "changed": changed, "unchanged": unchanged,
            "said": ("certified" if certify_ else "accepted") if changed else "nothing changed",
            "items": [_item(x) for x in items]}


def _transition(prof, unit: str, item: str | list[str], who: str, on: str | None, certify_: bool) -> dict:
    """Accept, or certify, items of a unit's draft. While an update is pending, review is of its draft; the live draft
    stays as released until the update lands."""
    _unit(unit)
    (_steward if certify_ else _business)(prof, who)
    store, changed, unchanged = packstore.for_profile(prof), [], []
    with appstate.for_profile(prof, store).lock():
        meta = _manifest(prof, store, unit)
        before = copy.deepcopy(meta)
        update = meta.get("update")
        if update and not update.get("drafted"):
            raise OnboardError(409, f"unit {unit} has an update (v{update['version']}) not drafted yet: run draft first")
        if meta["rows"]["origin"] == "none" and not update:
            raise OnboardError(409, NEEDS_ROWS.format(unit=unit))
        record = _read_record(store, unit, update=bool(update))
        draft_name = _state(store, unit, f"{UPDATE}/draft.yaml" if update else "draft.yaml")
        wanted = {x["id"] for x in record["items"]} if item == "all" else set(item if isinstance(item, list) else [item])
        if wanted - {x["id"] for x in record["items"]}:
            raise OnboardError(400, "unknown draft item(s): " + ", ".join(sorted(wanted - {x["id"] for x in record["items"]})))
        for entry in record["items"]:
            if entry["id"] not in wanted:
                continue
            field = "certified_by" if certify_ else "accepted_by"
            if entry.get(field):
                unchanged.append(entry["id"])
                continue
            if certify_ and not entry.get("accepted_by"):
                raise OnboardError(409, f"{entry['id']} has not been accepted")
            if certify_ and entry.get("accepted_by") == who:
                raise OnboardError(403, f"{who} accepted {entry['id']} and may not certify their own acceptance")
            entry[field], entry["certified_on" if certify_ else "accepted_on"] = (
                who,
                on or dt.datetime.now(dt.timezone.utc).date().isoformat(),
            )
            changed.append(entry["id"])
        if not update:
            _accepted_items(meta, record)
        if changed:
            graph = "certify" if certify_ else "accept"
            history = [_history_entry(who, graph, update["version"] if update else meta["version"], {"items": changed})]
            committed = _commit_manifest(prof, store, unit, meta, record, who, history)
            _land(prof, store, unit, committed, before, {draft_name: _record_bytes(record)}, who)
        else:
            _commit_manifest(prof, store, unit, meta, record, who)
    return {"unit": unit, "changed": changed, "unchanged": unchanged,
            "said": ("certified" if certify_ else "accepted") if changed else "nothing changed",
            "items": [_item(x) for x in record["items"]]}


def accept(prof, unit: str | None = None, item="all", as_: str = "", on: str | None = None, proposal: str | None = None) -> dict:
    if proposal is not None or (isinstance(unit, str) and unit.startswith("proposal:")) or unit in (None, ""):
        pid = proposal or (unit.removeprefix("proposal:") if isinstance(unit, str) and unit.startswith("proposal:") else None)
        return _transition_proposal(prof, pid, item, as_, on, False)
    return _transition(prof, unit, item, as_, on, False)


def certify(prof, unit: str | None = None, item="all", as_: str = "", on: str | None = None, proposal: str | None = None) -> dict:
    if proposal is not None or (isinstance(unit, str) and unit.startswith("proposal:")) or unit in (None, ""):
        pid = proposal or (unit.removeprefix("proposal:") if isinstance(unit, str) and unit.startswith("proposal:") else None)
        return _transition_proposal(prof, pid, item, as_, on, True)
    return _transition(prof, unit, item, as_, on, True)

