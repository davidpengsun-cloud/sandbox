"""What the page and make status show of onboarding (show, status_lines), and the record of a checks run
(record_onboard)."""
from __future__ import annotations

import json

from kge import packstore
from kge.pipeline.objstore import read_proposal_record, proposal_pending
from kge.adapters import appstate
from kge.estate.onboard.common import (
    ITEM_SUB_GRAPH,
    MANIFEST_FORMAT,
    NEEDS_ROWS,
    SUB_GRAPHS,
    _current_record,
    _item,
    _manifest,
    _read_record,
    _released,
    _state,
    _update_line,
)
from kge.estate.onboard.review import _open_proposal_id


def _what_changed(update: dict) -> list[str]:
    """The pending update's what-changed list, one line per item that needs review."""
    changes = (update or {}).get("changes") or {}
    return [f"{kind} {item}: {'; '.join(changes.get('details', {}).get(item, []))}"
            for kind in ("changed", "added", "removed") for item in changes.get(kind, [])]


def _status_line(unit: str, manifest: dict, record: dict | None, history: list[dict]) -> str:
    """One line of a unit's state; a release recorded before versions were (no version in it) was of version 1."""
    if manifest.get("update"):
        live = f"v{manifest.get('version', 1)} stays live" + (
            f" in {manifest['release']['model_hash'][:12]}" if _released(manifest) else "")
        return f"{_update_line(unit, manifest['update'], record)}; {live}"
    items = (record or {}).get("items", [])
    accepted = sum(bool(item.get("accepted_by")) for item in items)
    certified = sum(bool(item.get("certified_by")) for item in items)
    version = manifest.get("version", 1)
    parts = [f"{unit} v{version}: {accepted}/{len(items)} accepted, {certified}/{len(items)} certified"]
    release = manifest.get("release")
    if release and release.get("model_hash") and release.get("version", 1) == version:
        parts.append(f"released in {release['model_hash'][:12]}")
    else:
        if release and release.get("model_hash"):
            parts.append(f"v{release.get('version', 1)} live in {release['model_hash'][:12]}")
        history = [entry for entry in history if entry.get("version", 1) == version]
        onboarded = next((entry for entry in reversed(history) if entry.get("step") == "onboard"), None)
        signed = next((entry for entry in reversed(history) if entry.get("step") == "sign-off"), None)
        if onboarded:
            passed = (onboarded.get("detail") or {}).get("passed")
            parts.append("gate passed" if passed is True else "gate failed" if passed is False else "gate not checked")
            if passed is True:
                parts.append("awaiting release" if signed else "awaiting sign-off")
        elif items and certified == len(items):
            parts.append("awaiting checks")
        elif items and accepted == len(items):
            parts.append("awaiting certification")
        elif items:
            parts.append("awaiting acceptance")
        else:
            parts.append("awaiting draft")
    return ", ".join(parts)


def _documents(prof, unit: str, manifest: dict) -> list[dict]:
    """The unit's supplied documents, with the result of the certification path that actually ran."""
    names = sorted(name.removeprefix("semantic/docs/") for name in manifest.get("inputs", {})
                   if name.startswith("semantic/docs/"))
    release = prof.landing / prof.client / "current"
    try:
        paths = json.loads((release / "certification-paths.json").read_text())
    except (OSError, ValueError):
        paths = {}
    try:
        grades = {row["asset_id"]: row for row in json.loads((release / "certification.json").read_text())}
    except (OSError, ValueError, TypeError, KeyError):
        grades = {}
    try:
        index = {row["doc_id"]: row for row in json.loads((prof.estate() / "documents.json").read_text())}
    except (OSError, ValueError, TypeError, KeyError):
        index = {}
    documents = []
    for number, name in enumerate(names, 1):
        doc_id = f"DOC-{unit.upper()}-{number}"
        path = paths.get(doc_id)
        route = path.get("path") if isinstance(path, dict) else path
        grade = grades.get(doc_id, {})
        documents.append({"id": doc_id, "title": index.get(doc_id, {}).get("title") or name,
                          "path": "m365" if route == "upstream" else "graded" if route == "stand-in" else "not-used",
                          "tier": grade.get("tier")})
    return documents


def _progress(record: dict | None, documents: list[dict]) -> dict:
    """Each of the six sub-graphs' progress, for the unit card: how many drafted items it holds, how many the business
    team has accepted and the steward certified. The documents sub-graph's items are the unit's supplied documents:
    accepted with the source, and certified where a certification path ran for them (M365's tags or our grading)."""
    items = (record or {}).get("items", [])
    out = {}
    for graph in SUB_GRAPHS:
        if graph == "documents":
            accepted = any(x.get("kind") == "source" and x.get("accepted_by") for x in items)
            out[graph] = {"items": len(documents), "accepted": len(documents) if accepted else 0,
                          "certified": sum(d["path"] != "not-used" for d in documents) if accepted else 0}
            continue
        held = [x for x in items if ITEM_SUB_GRAPH.get(x.get("kind")) == graph]
        out[graph] = {"items": len(held), "accepted": sum(bool(x.get("accepted_by")) for x in held),
                      "certified": sum(bool(x.get("certified_by")) for x in held)}
    return out


def record_stage(prof, step: str, by: str, detail: dict, units: list[str] | None = None) -> None:
    """Record a client-level candidate action against each unit it contains."""
    from kge.pipeline import unit_history
    unit_history.record(prof, step, by, detail, units)


def status_lines(prof) -> dict:
    lines = []
    shown = show(prof)
    for unit in shown["units"]:
        lines.append(unit["status"])
        lines.extend(f"  {line}" for line in _what_changed(unit.get("update")))
        lines.extend(f"  request {request['id']}: {request['ask']} ({request['source']})"
                     for request in unit.get("requests", []))
    lines.extend(f"proposal {error['id']}: {error['status']}" for error in shown.get("proposal_errors", []))
    return {"lines": lines, "said": "\n".join(lines)}


def record_onboard(prof, model_hash: str, passed: str, as_: str = "onboarding") -> dict:
    verdict = passed.lower() == "true"
    units = None
    try:
        artifact = json.loads((prof.artifacts / prof.client / model_hash / "drafts.json").read_text())
        units = sorted({item["unit"] for item in artifact.get("items", [])
                        if isinstance(item, dict) and item.get("unit") and item.get("state") in {"accepted", "certified"}})
    except (OSError, ValueError):
        pass
    record_stage(prof, "onboard", as_, {"model_hash": model_hash, "passed": verdict}, units)
    return {"said": ""}


def show(prof) -> dict:
    store, units = packstore.for_profile(prof), []
    names = appstate.for_profile(prof, store).units()
    for unit in names:
        meta, history = appstate.for_profile(prof, store).snapshot(unit)
        if meta.get("format") != MANIFEST_FORMAT:
            meta = _manifest(prof, store, unit)
        record = _current_record(store, unit, meta, _read_record(store, unit)
                                 if store.exists(_state(store, unit, "draft.yaml")) else None)
        status_line = _status_line(unit, meta, record, history)
        visible_meta = {key: value for key, value in meta.items() if key != "_app_revision"}
        documents = _documents(prof, unit, meta)
        units.append({**visible_meta, "items_by_sub_graph": meta.get("items", {}), "progress": _progress(record, documents),
                      "items": [_item(x) for x in record["items"]] if record else [],
                      "not_drafted": record.get("not_drafted", []) if record else [],
                      "history": history, "status": status_line, "requests": meta.get("requests", []),
                      "documents": documents,
                      "live": bool((meta.get("release") or {}).get("model_hash") == prof.model_hash()),
                      **({"needs": NEEDS_ROWS.format(unit=unit)} if meta["rows"]["origin"] == "none" else {})})
    pid = _open_proposal_id(store)
    proposal = None
    notes, requests = [], []
    proposal_errors = []
    raw = None
    for name in sorted(store.list("proposals")):
        if not name.endswith("/proposal.yaml") or name.count("/") != 2:
            continue
        try:
            record = read_proposal_record(store, name)
        except ValueError as exc:
            error = {"id": name.split("/")[1], "state": "unreadable", "items": [], "status": str(exc)}
            proposal_errors.append(error)
            if error["id"] == pid:
                proposal = error
        else:
            if proposal_pending(record, prof.model_hash()):
                raw = record
    if raw is not None:
        proposal = {**raw, "items": [_item(x) for x in raw.get("items", [])]}
        notes = raw.get("notes") or []
        requests = raw.get("requests") or []
    if proposal is None and hasattr(prof, "run_dir"):
        last = prof.run_dir() / "improve" / "last_run.json"
        if last.is_file():
            try:
                summary = json.loads(last.read_text())
                notes = summary.get("notes") or []
                requests = summary.get("requests") or []
            except (OSError, ValueError):
                pass
    return {"units": units, "proposal": proposal, "notes": notes, "requests": requests,
            "proposal_errors": proposal_errors}

