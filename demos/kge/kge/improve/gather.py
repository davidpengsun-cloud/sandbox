"""Gather step of the improvement job: reads A/B misses of the served release, unmatched page questions, steward queue, and onboarded content.

Drops every held-out record (heldout and heldout_*) before any inspection or diagnosis, and never opens the served graph.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any


from kge.pipeline.trace import read_jsonl
from kge.compiler import pack as packs
from kge.estate import ddl as ddl_mod
from kge.eval import sets as setfiles
from kge.graph.release import GATE, RELEASED, marked
from kge.improve import CannotImprove
from kge.pipeline.profile import Profile
from kge import packstore

RUN_ID = re.compile(r"^\d{8}T\d{6}Z-[0-9a-f]{6,12}$")


def _runs_root(prof: Profile) -> Path:
    return prof.run_dir() / "runs"


def _is_rules_run_for_hash(run_dir: Path, model_hash: str) -> bool:
    man_file = run_dir / "manifest.json"
    rec_file = run_dir / "records.jsonl"
    if not man_file.is_file() or not rec_file.is_file():
        return False
    try:
        man = json.loads(man_file.read_text())
    except (OSError, ValueError):
        return False
    artifact = (man.get("graph") or {}).get("artifact")
    if artifact != model_hash:
        return False
    arms = {a.get("arm"): a.get("mode") for a in (man.get("arms") or []) if isinstance(a, dict)}
    return arms.get("treatment") == "rules"


def find_before_run_dirs(prof: Profile, model_hash: str) -> list[Path]:
    """All rules A/B runs on the served model_hash, with the served release's gate/release run first and any newer runs after it."""
    runs_dir = _runs_root(prof)
    if not runs_dir.is_dir():
        return []
    graph = prof.at(model_hash).graph_path()
    preferred_ids: list[str] = []
    for marker_name in (RELEASED, GATE):
        m = marked(graph, marker_name)
        if m and isinstance(m.get("run_id"), str):
            preferred_ids.append(m["run_id"])

    found: list[Path] = []
    seen: set[str] = set()
    for rid in preferred_ids:
        cand = runs_dir / rid
        if rid not in seen and _is_rules_run_for_hash(cand, model_hash):
            found.append(cand)
            seen.add(rid)

    all_dirs = sorted(
        (d for d in runs_dir.iterdir() if d.is_dir() and (RUN_ID.match(d.name) or (d / "manifest.json").is_file())),
        key=lambda d: d.name,
        reverse=True,
    )
    for d in all_dirs:
        if d.name not in seen and _is_rules_run_for_hash(d, model_hash):
            found.append(d)
            seen.add(d.name)
    return found


def find_before_run(prof: Profile, model_hash: str | None = None) -> Path:
    """The primary before run for the served release; raises CannotImprove if none exists."""
    target_hash = model_hash or prof.model_hash()
    if not target_hash:
        raise CannotImprove(f"profile {prof.id} has no released graph (current): run make graph or make release first")
    runs = find_before_run_dirs(prof, target_hash)
    if not runs:
        raise CannotImprove(
            f"no A/B rules run on the served release {target_hash}: stop the API and run make margin HASH={target_hash} first"
        )
    return runs[0]


def _read_candidate_phrases_from_overlay(prof: Profile) -> set[str]:
    """Read candidate_term phrases from the runtime overlay without opening the graph."""
    out: set[str] = set()
    for overlay_file in (prof.overlay_dir() / "decisions.jsonl", prof.store / prof.client / "overlay.jsonl"):
        if overlay_file.is_file():
            for row in read_jsonl(overlay_file):
                for src in (row, row.get("machine"), row.get("tag")):
                    if isinstance(src, dict) and src.get("family") == "candidate_term" and isinstance(src.get("value"), str):
                        out.add(src["value"].strip().lower())
    return out


def gather(prof: Profile, unit: str | None = None) -> dict[str, Any]:
    """Read the misses, the evidence, and what stewards already hold for prof's served release, counting the open,
    uncertified draft items in units/<unit>/draft.yaml as held.


    Drops every held-out record first (heldout and heldout_*). Never opens the served graph.
    A selected unit limits misses and proposal evidence; unattributed questions are omitted.
    Client-wide pass counts remain available to the candidate regression gate.
    """
    served_hash = prof.model_hash()
    if not served_hash:
        raise CannotImprove(f"profile {prof.id} has no released graph (current): run make graph or make release first")

    runs = find_before_run_dirs(prof, served_hash)
    if not runs:
        raise CannotImprove(
            f"no A/B rules run on the served release {served_hash}: stop the API and run make margin HASH={served_hash} first"
        )
    before_dir = runs[0]
    before_manifest = json.loads((before_dir / "manifest.json").read_text())

    raw_records = read_jsonl(before_dir / "records.jsonl")
    learnable_records = [r for r in raw_records if not setfiles.is_heldout(str(r.get("set") or ""))]

    estate = prof.estate()
    expectations: dict[tuple[str, str], dict] = {}
    learnable_sets = sorted({str(r["set"]) for r in learnable_records if r.get("set")})
    for set_name in learnable_sets:
        set_path = estate / f"{set_name}.json"
        if set_path.is_file():
            for item in setfiles.items(set_path):
                expectations[(set_name, item["id"])] = item

    by_item: dict[tuple[str, str], list[dict]] = {}
    for r in learnable_records:
        if r.get("arm") == "treatment":
            by_item.setdefault((r["set"], r["id"]), []).append(r)

    ab_misses: list[dict] = []
    learnable_passes: dict[tuple[str, str], bool] = {}
    for (set_name, item_id), recs in sorted(by_item.items()):
        passed_all = all(bool(x.get("ok")) for x in recs)
        learnable_passes[(set_name, item_id)] = passed_all
        if not passed_all:
            sample = next((x for x in recs if not x.get("ok")), recs[0])
            exp_item = expectations.get((set_name, item_id), {})
            ans = sample.get("answer") or sample
            expected_unit = (exp_item.get("expect") or {}).get("unit")
            if unit is not None and (ans.get("unit") or expected_unit) != unit:
                continue
            ab_misses.append({
                "source": "ab",
                "set": set_name,
                "id": item_id,
                "kind": sample.get("kind") or exp_item.get("kind") or "within",
                "question": sample.get("question") or exp_item.get("question") or "",
                "why": sample.get("why") or "",
                "behaviour": ans.get("behaviour") or ((sample.get("measures") or {}).get("behaviour") or {}).get("got"),
                "reason": ans.get("reason") or "",
                "unit": ans.get("unit"),
                "column": ans.get("column"),
                "figure": ans.get("figure"),
                "sql": ans.get("sql"),
                "expect": exp_item.get("expect") or {},
                "measures": sample.get("measures") or {},
            })

    unmatched_path = prof.run_dir() / "unmatched.jsonl"
    unmatched_rows = read_jsonl(unmatched_path) if unmatched_path.is_file() else []
    page_counts: dict[str, dict] = {}
    for row in unmatched_rows:
        if unit is not None and row.get("unit") != unit:
            continue
        q = str(row.get("question") or "").strip()
        if not q:
            continue
        entry = page_counts.setdefault(q, {
            "source": "page",
            "question": q,
            "reason": str(row.get("reason") or "No glossary term recognised in the question."),
            "count": 0,
            "principals": [],
        })
        entry["count"] += 1
        who = row.get("principal")
        if isinstance(who, str) and who not in entry["principals"]:
            entry["principals"].append(who)
    page_misses = list(page_counts.values())

    p = packs.read(prof.clients, prof.client, lock=True, identities=prof.identities)
    pack_data = p.data
    lock_data = p.lock if isinstance(p.lock, dict) else {}
    base_pack_hash = p.hash

    certified_terms: dict[str, dict] = {}
    for g in pack_data.get("glossary") or []:
        if isinstance(g, dict) and g.get("status") == "certified" and isinstance(g.get("id"), str):
            certified_terms[g["id"]] = g

    existing_aliases: dict[str, str] = {}
    candidate_phrases: set[str] = _read_candidate_phrases_from_overlay(prof)
    for a in (pack_data.get("terminology") or {}).get("aliases") or []:
        if not isinstance(a, dict) or not isinstance(a.get("phrase"), str):
            continue
        ph = a["phrase"].strip().lower()
        if isinstance(a.get("term"), str):
            existing_aliases[ph] = a["term"]
        elif a.get("candidate"):
            candidate_phrases.add(ph)

    for d in p.drafts:
        if d.get("state") != "certified" and isinstance(d.get("id"), str):
            candidate_phrases.add(d["id"].replace("-", " ").strip().lower())

    certified_mappings: dict[str, list[str]] = {}
    term_columns: dict[str, list[str]] = {}
    for m in pack_data.get("mappings") or []:
        if isinstance(m, dict) and m.get("certified") is True and isinstance(m.get("column"), str) and isinstance(m.get("term"), str):
            certified_mappings.setdefault(m["column"], []).append(m["term"])
            term_columns.setdefault(m["term"], []).append(m["column"])

    columns: dict[str, dict] = {}
    declared_joins: dict[str, list[dict]] = {}
    for unit_id, src in sorted((lock_data.get("sources") or {}).items()):
        if unit is not None and unit_id != unit:
            continue
        for tbl in (src.get("tables") or []):
            tname = tbl["name"]
            pks = set(tbl.get("primary_key") or [])
            fks = tbl.get("foreign_keys") or []
            fk_cols = {c for fk in fks for c in (fk.get("columns") or [])}
            for fk in fks:
                declared_joins.setdefault(unit_id, []).append({
                    "from_table": tname,
                    "columns": fk.get("columns") or [],
                    "to_table": fk.get("ref_table"),
                    "ref_columns": fk.get("ref_columns") or [],
                })
            for col in (tbl.get("columns") or []):
                cname = col["name"]
                full = f"{unit_id}.{tname}.{cname}"
                columns[full] = {
                    "column": full,
                    "unit": unit_id,
                    "table": tname,
                    "name": cname,
                    "type": col.get("type") or "",
                    "description": col.get("description") or "",
                    "is_key": cname in pks or cname in fk_cols,
                    "certified_terms": list(certified_mappings.get(full) or []),
                }

    store = packstore.for_profile(prof)
    unit_names = sorted({n.split("/")[1] for n in store.list("units") if n.startswith("units/") and n.count("/") >= 2})
    for unit_id in unit_names:
        if unit is not None and unit_id != unit:
            continue
        ddl_rel = f"units/{unit_id}/schema.sql"
        if not store.exists(ddl_rel):
            continue
        try:
            parsed = ddl_mod.parse(store.read(ddl_rel).decode(), unit_id)
        except Exception:
            continue
        for tbl in parsed.get("tables") or []:
            tname = tbl["name"]
            pks = set(tbl.get("primary_key") or [])
            fk_cols = {c for fk in (tbl.get("foreign_keys") or []) for c in (fk.get("columns") or [])}
            for col in (tbl.get("columns") or []):
                cname = col["name"]
                full = f"{unit_id}.{tname}.{cname}"
                existing = columns.get(full)
                desc = col.get("comment") or (existing.get("description") if existing else "") or ""
                columns[full] = {
                    "column": full,
                    "unit": unit_id,
                    "table": tname,
                    "name": cname,
                    "type": col.get("type") or (existing.get("type") if existing else ""),
                    "description": desc,
                    "is_key": (cname in pks or cname in fk_cols) if existing is None else existing["is_key"],
                    "certified_terms": list(certified_mappings.get(full) or []),
                }

    if unit is not None:
        certified_mappings = {col: terms for col, terms in certified_mappings.items() if col in columns}
        term_columns = {term: [col for col in cols if col in columns] for term, cols in term_columns.items()}
        term_columns = {term: cols for term, cols in term_columns.items() if cols}
        certified_terms = {term: info for term, info in certified_terms.items() if term in term_columns}

    evidence = {
        "client": prof.client,
        "served_hash": served_hash,
        "base_version": str((pack_data.get("manifest") or {}).get("version") or "1.0.0"),
        "base_pack_hash": base_pack_hash,
        "before_run_id": before_dir.name,
        "before_run_dir": str(before_dir),
        "before_sets": [s for s in (before_manifest.get("sets") or {}) if not setfiles.is_heldout(s)],
        "learnable_passes": {f"{s}:{i}": ok for (s, i), ok in learnable_passes.items()},
        "certified_terms": certified_terms,
        "existing_aliases": existing_aliases,
        "candidate_phrases": sorted(candidate_phrases),
        "certified_mappings": certified_mappings,
        "term_columns": term_columns,
        "columns": columns,
        "declared_joins": declared_joins,
    }
    return {
        "ab_misses": ab_misses,
        "page_misses": page_misses,
        "evidence": evidence,
    }
