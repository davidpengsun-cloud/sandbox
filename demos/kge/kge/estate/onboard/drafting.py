"""Drafting a unit's pack (draft): the drafted items from the unit's model, warehouse and the reference
standards, and, for an update, what changed from the live draft item by item."""
from __future__ import annotations

import copy
import datetime as dt
import hashlib
import json
import re

from kge.compiler import drafts, standards as preloaded
from kge.estate import ddl, draftflow
from kge import packstore
from kge.adapters import appstate
from kge.pipeline import privacy
from kge.estate.onboard.common import (
    NEEDS_ROWS,
    OnboardError,
    UPDATE,
    _accepted_items,
    require_process,
    _commit_manifest,
    _history_entry,
    _item,
    _land,
    _manifest,
    _pack,
    _parsed,
    _read_record,
    _record_bytes,
    _semantic_files,
    _state,
    _unit,
    _update_inputs,
    _update_line,
    _warehouse_entry,
    _estate,
    ensure_warehouse,
)
from kge.estate.onboard.figures import _client_golden, _figures


STATE_FIELDS = {"accepted_by", "accepted_on", "certified_by", "certified_on", "made_by", "model", "change", "state"}


def _remap(value, ids: dict[str, str]):
    """value with every string that is a renamed id replaced, wherever it appears (a context's golden items, the
    source's golden set, the question itself)."""
    if isinstance(value, str):
        return ids.get(value, value)
    if isinstance(value, list):
        return [_remap(item, ids) for item in value]
    if isinstance(value, dict):
        return {key: _remap(item, ids) for key, item in value.items()}
    return value


def _stable_ids(unit: str, items: list[dict], live: list[dict]) -> list[dict]:
    """Drafted questions numbered as the live draft numbers them. A drafted question's id is its position among the
    unit's terms (kge/compiler/drafts.py), so removing or adding a term would renumber the rest; here a question keeps the
    id its term's question had, and a new term's question takes the next number no item has used."""
    drafted = re.compile(rf"{re.escape(unit.upper())}-D(\d+)\Z")
    by_term = {tuple(x.get("terms") or []): x["id"] for x in live
               if x.get("kind") == "question" and x.get("from") != "client" and drafted.match(x["id"])}
    taken = {x["id"] for x in live} | {x["id"] for x in items if x.get("kind") != "question" or x.get("from") == "client"}
    numbers = [int(m.group(1)) for x in live if (m := drafted.match(x["id"]))]
    ids, following = {}, max(numbers, default=0)
    for item in items:
        if item.get("kind") != "question" or item.get("from") == "client" or not drafted.match(item["id"]):
            continue
        kept = by_term.get(tuple(item.get("terms") or []))
        if kept is None:
            following += 1
            while f"{unit.upper()}-D{following:02d}" in taken:
                following += 1
            kept = f"{unit.upper()}-D{following:02d}"
        ids[item["id"]] = kept
        taken.add(kept)
    return [_remap(item, ids) for item in items] if any(k != v for k, v in ids.items()) else items


def _context(model: dict, inputs: dict) -> dict:
    """What a source item stands for beyond its pack entries: the tables and columns, and the unit's documents."""
    return {"tables": model["tables"],
            "documents": {name.removeprefix("semantic/docs/"): digest for name, digest in sorted(inputs.items())
                          if name.startswith("semantic/docs/")}}


def _fingerprint(item: dict, context: dict | None) -> str:
    body = {key: value for key, value in item.items() if key not in STATE_FIELDS}
    if item.get("kind") == "source":
        body["_context"] = context
    return hashlib.sha256(json.dumps(body, sort_keys=True, default=str).encode()).hexdigest()[:16]


def _first(pack: dict, section: str) -> dict:
    return ((pack or {}).get(section) or [{}])[0]


def _differences(old: dict, new: dict, was: dict, now: dict) -> list[str]:
    """What changed in one item, in words a business reviewer reads."""
    out = []
    if new["kind"] == "source":
        before, after = {t["name"]: t for t in was["tables"]}, {t["name"]: t for t in now["tables"]}
        out += [f"table {name} added" for name in sorted(set(after) - set(before))]
        out += [f"table {name} removed" for name in sorted(set(before) - set(after))]
        for name in sorted(set(before) & set(after)):
            a = {c["name"]: c for c in before[name]["columns"]}
            b = {c["name"]: c for c in after[name]["columns"]}
            said = [f"table {name}: column {c} added" for c in b if c not in a]
            said += [f"table {name}: column {c} removed" for c in a if c not in b]
            said += [f"table {name}: column {c} type {a[c]['type']} -> {b[c]['type']}" for c in a
                     if c in b and a[c].get("type") != b[c].get("type")]
            if not said and before[name] != after[name]:
                said = [f"table {name}: keys or comments changed"]
            out += said
        docs_before, docs_after = was["documents"], now["documents"]
        out += [f"document {d} added" for d in docs_after if d not in docs_before]
        out += [f"document {d} removed" for d in docs_before if d not in docs_after]
        out += [f"document {d} changed" for d in docs_after if d in docs_before and docs_before[d] != docs_after[d]]
        if (old.get("pack") or {}).get("tests.sets") != (new.get("pack") or {}).get("tests.sets"):
            out.append("its golden set's questions changed")
    elif new["kind"] == "term":
        a, b = old.get("pack") or {}, new.get("pack") or {}
        for field in ("name", "definition"):
            if _first(a, "glossary").get(field) != _first(b, "glossary").get(field):
                out.append(f"{field} changed")
        for field in ("column", "expression", "currency", "grain"):
            if _first(a, "measures").get(field) != _first(b, "measures").get(field):
                out.append(f"measure {field} {_first(a, 'measures').get(field)} -> {_first(b, 'measures').get(field)}")
        recipe = [[x.get("predicate") for x in (p.get("filters") or []) + (p.get("row_selectors") or [])] for p in (a, b)]
        if recipe[0] != recipe[1]:
            out.append(f"recipe {' AND '.join(recipe[0]) or 'none'} -> {' AND '.join(recipe[1]) or 'none'}")
        phrases = [{x.get("phrase") for x in p.get("terminology.aliases") or []} for p in (a, b)]
        out += [f"alias '{x}' added" for x in sorted(phrases[1] - phrases[0])]
        out += [f"alias '{x}' removed" for x in sorted(phrases[0] - phrases[1])]
    elif new["kind"] == "mapping":
        for field in ("concept", "relation"):
            if old.get(field) != new.get(field):
                out.append(f"{field} {old.get(field)} -> {new.get(field)}")
    elif new["kind"] == "policy":
        if old.get("family") != new.get("family"):
            out.append(f"personal data {old.get('family')} -> {new.get('family')}")
    else:
        figures = [(x.get("expect") or {}).get("figure") for x in (old, new)]
        if figures[0] != figures[1]:
            out.append(f"expected figure {figures[0]} -> {figures[1]}")
        if old.get("question") != new.get("question"):
            out.append(f"question '{old.get('question')}' -> '{new.get('question')}'")
        if old.get("sql") != new.get("sql"):
            out.append("reference SQL changed")
    return out or ["content changed"]


def _classify(live: list[dict], drafted: list[dict], was: dict, now: dict) -> tuple[list[dict], dict]:
    """The update's items against the live ones, by id and content fingerprint: an unchanged item is the live item
    itself, its acceptance and certification carried; an added or changed one is a new candidate; a live item the
    update no longer has is a proposed removal, accepted and certified like any other item, never dropped silently."""
    before = {x["id"]: x for x in live}
    items, changes = [], {"changed": [], "added": [], "removed": [], "unchanged": [], "details": {}}
    for item in drafted:
        old = before.get(item["id"])
        if old is None:
            items.append({**item, "change": {"kind": "added"}})
            changes["added"].append(item["id"])
            changes["details"][item["id"]] = [f"new {item['kind']}: {item.get('title') or item['id']}"]
        elif old.get("kind") == item.get("kind") and _fingerprint(old, was) == _fingerprint(item, now):
            items.append(old)
            changes["unchanged"].append(item["id"])
        else:
            said = _differences(old, item, was, now)
            items.append({**item, "change": {"kind": "changed", "was": _fingerprint(old, was), "details": said}})
            changes["changed"].append(item["id"])
            changes["details"][item["id"]] = said
    for old in live:
        if old["id"] not in {x["id"] for x in drafted}:
            items.append({**old, "accepted_by": None, "accepted_on": None, "certified_by": None, "certified_on": None,
                          "change": {"kind": "removed"}})
            changes["removed"].append(old["id"])
            changes["details"][old["id"]] = [f"removed {old['kind']}: {old.get('title') or old['id']}"]
    return items, changes


def _made_by(item: dict, hints: dict | None) -> dict:
    """Which drafting step, model or rules, made an item (kge/estate/draftflow.py), with the model's id for a model item.
    A source is the tables step's; a term, the recipes or definitions step's (a model in either shaped it); a drafted
    question is the model's only where it worded the subject, since code computes its period, SQL and figure; a
    client's golden question is checked by code, never the model's."""
    steps = (hints or {}).get("made_by", {})
    kind = item.get("kind")
    if kind == "source":
        made = [steps.get("tables", {})]
    elif kind == "term":
        made = [steps.get("recipes", {}), steps.get("definitions", {})]
    elif kind == "question" and item.get("worded_by") == "model":
        made = [steps.get("golden", {})]
    else:
        made = []
    model = next((m for m in made if m.get("made_by") == "model"), None)
    return {"made_by": "model", "model": model["model"]} if model else {"made_by": "rules"}


def _proposed(prof, unit: str, label: str, con, model: dict, parsed: dict | None,
              without_unit: str | None = None) -> tuple[list[dict], list[dict], dict | None]:
    """The unit's drafted items and gaps, from its loaded rows and (if any) its semantic files' proposal. without_unit:
    an update is drafted against the pack without the unit's own live draft, as its first draft was."""
    cat = ddl.catalogue(con, model)
    hints = parsed["hints"] if parsed else None
    pack = _pack(prof, without_unit)
    connection = _estate(prof).connection(unit)
    provisional = drafts.propose(pack, cat, label, connection, hints=hints)
    figures = _figures(con, provisional, unit)
    proposed = drafts.propose(pack, cat, label, connection, figures, hints=hints)
    supplied = parsed["golden"] if parsed else []
    client_items, client_held = _client_golden(con, pack, cat, unit, supplied,
                                               (hints or {}).get("measures", []), proposed["items"])
    proposed["items"].extend(client_items)
    proposed["not_drafted"].extend(client_held)
    items = [{**{k: v for k, v in item.items() if k != "worded_by"}, **_made_by(item, hints),
              "accepted_by": None, "accepted_on": None, "certified_by": None, "certified_on": None}
             for item in proposed["items"]]
    mapped, gaps, outcome = _standards_items(unit, items)
    governed = _governance_items(con, cat, unit, pack, items)
    items += [{**item, "accepted_by": None, "accepted_on": None, "certified_by": None, "certified_on": None}
              for item in mapped + governed]
    return items, proposed["not_drafted"] + gaps, hints, outcome


def _standards_items(unit: str, items: list[dict]) -> tuple[list[dict], list[dict], dict]:
    """The industry-ontology sub-graph's drafted items: each drafted term's candidate mappings to the platform's preloaded
    concepts (kge/estate/draftflow.py standards), one item a mapping; the gaps, a term with no adequate match; and the
    step's outcome, for the history."""
    preload = preloaded.load()
    subjects = []
    for item in items:
        if item.get("kind") != "term":
            continue
        g, m = _first(item["pack"], "glossary"), _first(item["pack"], "measures")
        subjects.append({"id": g["id"], "label": g["name"], "definition": g.get("definition"), "currency": m.get("currency"),
                         "aliases": [a["phrase"] for a in item["pack"].get("terminology.aliases", []) if a.get("term") == g["id"]]})
    result = draftflow.standards(subjects, unit, preload=preload)
    labels = {s["id"]: s["label"] for s in subjects}
    out = []
    for m in result["mappings"]:
        c = preload.concepts[m["concept"]]
        s = preload.standards[c["standard"]]
        out.append({"id": f"ontology-{m['term']}-{c['id']}", "kind": "mapping",
                    "title": f"{labels[m['term']]} {m['relation']} {c['label']} ({s['name']}, {s['version']})",
                    "from": m["why"], "term": m["term"], "concept": c["id"], "iri": c["iri"], "relation": m["relation"],
                    "standard": s["id"], "standard_version": s["version"], "made_by": m["made_by"],
                    **({"model": m["model"]} if m.get("model") else {}),
                    "pack": {"ontology_mappings": [{"term": m["term"], "iri": c["iri"], "relation": m["relation"],
                                                    "note": m["why"]}]}})
    return out, result["gaps"], result["outcome"]


def _sample(con, unit: str, table: str, column: str, limit: int = 50) -> list[str]:
    """Up to limit distinct values of a column, as text, for the privacy detectors: read only, from the unit's warehouse
    (the session's sample)."""
    return con.sample(table, column, limit)


def _governance_items(con, cat: dict, unit: str, pack: dict, items: list[dict]) -> list[dict]:
    """The governance sub-graph's drafted items: each column of the unit whose name, comment or sampled values the
    privacy detectors find personal data in (kge/pipeline/privacy.py column_findings), as a candidate restricted-data
    policy, refused by the SQL gate once certified, mapped to the preloaded data-privacy concept for its family. Never
    a key, nor a column a drafted measure, filter or row selector reads: a restricted column may not be joined or
    filtered on (S23, S24). Each cites the client's own data-classification policy, where its pack has one."""
    preload = preloaded.load()
    rules = preload.defaults["personal_data"]
    used = " ".join(str(x.get("predicate", "")) + " " + str(x.get("column", "")) + " " + str(x.get("expression", ""))
                    for item in items for section in ("measures", "filters", "row_selectors")
                    for x in (item.get("pack") or {}).get(section, []))
    held = (pack.get("policies") or {}).get("restricted_attributes") or []
    out = []
    for table in cat["tables"]:
        keys = set(table.get("primary_key", [])) | {c for f in table.get("foreign_keys", []) for c in f["columns"]}
        for column in table["columns"]:
            name = column["name"]
            if name in keys or column.get("key") or re.search(rf"\b{re.escape(table['name'])}\.{re.escape(name)}\b", used):
                continue
            text = str(column.get("type") or "").upper().startswith(("VARCHAR", "TEXT", "STRING", "CHAR"))
            found = privacy.column_findings(name, column.get("comment"), column.get("type"),
                                            _sample(con, unit, table["name"], name) if text else ())
            if not found:
                continue
            family = found[0]
            rule = rules.get(family.split(":")[0], rules["default"])
            concept = preload.concepts[rule["concept"]]
            col = f"{unit}.{table['name']}.{name}"
            pid = f"{unit}-{table['name']}-{name}-personal-data".lower()
            cite = held[0]["cite"] if held else pid
            out.append({"id": f"policy-{table['name']}-{name}", "kind": "policy", "column": col, "family": family,
                        "title": f"Restrict {col}: personal data ({family}), {rule['relation']} {concept['label']} "
                                 f"({preload.standards[concept['standard']]['name']})",
                        "from": "the column's name, comment and values, by the privacy detectors", "made_by": "rules",
                        "pack": {"policies.restricted_attributes": [{"id": pid, "action": "refuse", "cite": cite, "columns": [col]}],
                                 "ontology_mappings": [{"policy": pid, "iri": concept["iri"], "relation": rule["relation"],
                                                        "note": f"The column holds {family.replace('_', ' ')}: drafted by the "
                                                                "privacy detectors."}]}})
    return out


def _golden_counts(golden: dict, items: list[dict]) -> dict:
    return {**golden, "client": sum(x.get("kind") == "question" and x.get("from") == "client" for x in items),
            "drafted": sum(x.get("kind") == "question" and x.get("from") != "client" for x in items)}


def _draft_update(prof, store, unit: str, meta: dict, as_: str, on: str | None) -> dict:
    """Draft a pending update from its staged inputs, in a throwaway warehouse, and set each item against the live
    draft: unchanged items keep their acceptance and certification; the rest wait for review."""
    update = meta["update"]
    if update.get("drafted"):
        record = _read_record(store, unit, update=True)
        _commit_manifest(prof, store, unit, meta, record, as_)
        return {"unit": unit, "drafted": False, "said": f"update v{update['version']} draft exists already; nothing changed",
                "items": [_item(x) for x in record["items"]], "not_drafted": record.get("not_drafted", []),
                "changes": update.get("changes")}
    before = copy.deepcopy(meta)
    live = _read_record(store, unit)
    text, files, semantic_files = _update_inputs(store, unit, update)
    model = ddl.parse(text, unit)
    parsed = _parsed(store, unit, semantic_files, update=True) if semantic_files is not None else None
    with ddl.scratch(model, _estate(prof), {"csv": ddl.read_csvs(model, files)} if files is not None else "invented") as con:
        items, not_drafted, _, outcome = _proposed(prof, unit, meta["label"], con, model, parsed, without_unit=unit)
    items = _stable_ids(unit, items, live["items"])
    was = _context(ddl.parse(store.read(_state(store, unit, "schema.sql")).decode(), unit), meta.get("inputs", {}))
    items, changes = _classify(live["items"], items, was, _context(model, update["inputs"]))
    today = on or dt.date.today().isoformat()
    record = {"format": 1, "unit": unit, "drafted": {"by": as_, "on": today, "from": "schema.sql",
              "ddl_sha256": update["ddl_sha256"]}, "update": {"version": update["version"], "from": update["from"]},
              "not_drafted": not_drafted, "items": items}
    meta = {**meta, "update": {**update, "drafted": True, "changes": changes,
                               "golden": _golden_counts(meta.get("golden") or {}, [x for x in items
                                                        if (x.get("change") or {}).get("kind") != "removed"])}}
    summary = {key: changes[key] for key in ("changed", "added", "removed")}
    history = [_history_entry(as_, "update-draft", update["version"], {**summary, "unchanged": len(changes["unchanged"]),
                                                                        "not_drafted": not_drafted}),
               _history_entry(as_, f"draft-{outcome['node']}", update["version"], outcome)]
    committed = _commit_manifest(prof, store, unit, meta, record, as_, history)
    _land(prof, store, unit, committed, before, {_state(store, unit, f"{UPDATE}/draft.yaml"): _record_bytes(record)}, as_)
    return {"unit": unit, "drafted": True, "said": _update_line(unit, meta["update"], record), "items": [_item(x) for x in items],
            "not_drafted": not_drafted, "changes": changes}


def draft(prof, unit: str, as_: str, on: str | None = None) -> dict:
    _unit(unit)
    require_process(prof, as_, "drafting")
    store = packstore.for_profile(prof)
    with appstate.for_profile(prof, store).lock():
        try:
            pending = _manifest(prof, store, unit)
        except FileNotFoundError:
            pending = None
        if pending is not None and pending.get("update"):
            return _draft_update(prof, store, unit, pending, as_, on)
        if store.exists(_state(store, unit, "draft.yaml")):
            record = _read_record(store, unit)
            meta = _manifest(prof, store, unit)
            said = NEEDS_ROWS.format(unit=unit) if meta["rows"]["origin"] == "none" else "draft exists already; nothing changed"
            _accepted_items(meta, record)
            _commit_manifest(prof, store, unit, meta, record, as_)
            return {"unit": unit, "drafted": False, "said": said,
                    "items": [_item(x) for x in record["items"]], "not_drafted": record.get("not_drafted", [])}
        try:
            unit_record = _manifest(prof, store, unit)
            before = copy.deepcopy(unit_record)
            text = store.read(_state(store, unit, "schema.sql")).decode()
        except FileNotFoundError:
            raise OnboardError(409, f"unit {unit} has no source: create it first") from None
        model = ddl.parse(text, unit)
        version = ensure_warehouse(prof, unit)
        con = _estate(prof).open(unit, version)
        try:
            parsed = (_parsed(store, unit, _semantic_files(store, unit, unit_record.get("inputs")))
                      if unit_record.get("semantic") else None)
            items, not_drafted, _, outcome = _proposed(prof, unit, unit_record["label"], con, model, parsed)
        finally:
            con.close()
        today = on or dt.datetime.now(dt.timezone.utc).date().isoformat()
        record = {"format": 1, "unit": unit, "drafted": {"by": as_, "on": today, "from": "schema.sql",
                  "ddl_sha256": unit_record["ddl_sha256"]}, "warehouse": _warehouse_entry(unit_record),
                  "not_drafted": not_drafted, "items": items}
        unit_record["golden"] = _golden_counts(unit_record["golden"], items)
        history = [_history_entry(as_, "draft", unit_record["version"],
                                  {"items": [item["id"] for item in items],
                                   "not_drafted": not_drafted}),
                   _history_entry(as_, f"draft-{outcome['node']}", unit_record["version"], outcome)]
        committed = _commit_manifest(prof, store, unit, unit_record, record, as_, history)
        _land(prof, store, unit, committed, before, {_state(store, unit, "draft.yaml"): _record_bytes(record)}, as_)
    said = NEEDS_ROWS.format(unit=unit) if unit_record["rows"]["origin"] == "none" else "drafted"
    return {"unit": unit, "drafted": True, "said": said, "items": [_item(x) for x in items],
            "not_drafted": not_drafted}

