"""Draft a pack runtime entry from a catalog export and the reference schema or lock.

    python3 -m kge.estate.author_runtime --catalog <export.json> --pack <client-folder>
                                         [--id <id>] [--label <label>] [--dialect <dialect>]
                                         [--location <location>] [--confirmed]

A catalog export lists a runtime's tables and columns, with optional lineage
(derived_from) back to reference tables and columns. author_runtime compares
the export against the pack's reference tables and columns (from pack.lock or
fixture/schema.yaml):
  - exact name matches need no listing (they inherit the reference name)
  - lineage matches and similar-name matches are proposed for author
    confirmation before writing
  - reference tables missing from the export are listed under absent
  - extra tables or columns in the runtime export are ignored and reported in
    the summary, never written into the pack
  - live project ids, dataset URIs, credentials and service URLs are replaced
    with placeholders unless explicitly overridden with a placeholder
"""
from __future__ import annotations

import argparse
import difflib
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from kge.compiler import pack as packs
from kge.compiler.pack import load_yaml
from kge.pipeline.profile import ProfileError, resolve


LIVE_RE = re.compile(r"(?:^[a-z][\w-]+:[a-z0-9_]+$|://|@[\w.-]+\.iam\.gserviceaccount\.com)", re.I)


@dataclass(frozen=True)
class Proposal:
    kind: str
    reference: str
    target: str
    target_type: str | None
    reason: str
    confidence: float


@dataclass
class DraftRuntime:
    entry: dict
    proposals: list[Proposal] = field(default_factory=list)
    absent: list[str] = field(default_factory=list)
    extra_tables: list[str] = field(default_factory=list)
    extra_columns: list[str] = field(default_factory=list)
    placeholders: list[str] = field(default_factory=list)


def _reference_tables(folder: Path) -> dict[str, dict[str, str]]:
    """Return { 'unit.table': { 'col': 'TYPE', ... } } from pack.lock or fixture/schema.yaml."""
    lock_path = folder / "pack.lock"
    if lock_path.is_file():
        lock = json.loads(lock_path.read_text())
        out: dict[str, dict[str, str]] = {}
        for unit, held in sorted((lock.get("sources") or {}).items()):
            for t in held.get("tables") or []:
                out[f"{unit}.{t['name']}"] = {c["name"]: c["type"] for c in t.get("columns") or []}
        return out
    schema_path = folder / "fixture" / "schema.yaml"
    if schema_path.is_file():
        schema = load_yaml(schema_path, "fixture/schema.yaml", []) or {}
        out = {}
        for unit, house in sorted((schema.get("warehouses") or {}).items()):
            for t in house.get("tables") or []:
                out[f"{unit}.{t['name']}"] = {c["name"]: c["type"] for c in t.get("columns") or []}
        return out
    read = packs.read(folder.parent, folder.name, lock=True)
    out = {}
    for unit, held in sorted(((read.lock or {}).get("sources") or {}).items()):
        for t in held.get("tables") or []:
            out[f"{unit}.{t['name']}"] = {c["name"]: c["type"] for c in t.get("columns") or []}
    return out


def _norm(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", name.lower())


def _similarity(a: str, b: str) -> float:
    na, nb = _norm(a), _norm(b)
    if not na or not nb:
        return 0.0
    if na == nb:
        return 0.99
    if na in nb or nb in na:
        return max(0.75, difflib.SequenceMatcher(None, na, nb).ratio())
    return difflib.SequenceMatcher(None, na, nb).ratio()


def _sanitize_dialect(raw: str | None, placeholders: list[str], field_name: str) -> str:
    if not raw or not str(raw).strip():
        placeholders.append(f"{field_name}: wrote 'placeholder' (ask the person for the SQL dialect; do not guess)")
        return "placeholder"
    return str(raw).strip()


def _sanitize_location(raw: str | None, placeholders: list[str], field_name: str) -> str:
    if not raw or not str(raw).strip() or LIVE_RE.search(str(raw).strip()):
        placeholders.append(f"{field_name}: wrote 'placeholder' (ask the person for the value; do not copy a live project id, dataset URI or service account)")
        return "placeholder"
    return str(raw).strip()


def draft_from_catalog(
    catalog: dict,
    reference: dict[str, dict[str, str]],
    *,
    runtime_id: str | None = None,
    label: str | None = None,
    dialect: str | None = None,
    location: str | None = None,
    confirmed: bool = False,
) -> DraftRuntime:
    placeholders: list[str] = []
    rid = runtime_id or catalog.get("id") or "warehouse_runtime"
    rlabel = label or catalog.get("label") or rid
    rdialect = _sanitize_dialect(dialect or catalog.get("dialect"), placeholders, "connection.dialect")
    rloc = _sanitize_location(location if location is not None else catalog.get("location"), placeholders, "connection.location")

    export_tables = list(catalog.get("tables") or [])
    used_export: set[int] = set()
    proposals: list[Proposal] = []
    absent: list[str] = []
    extra_columns: list[str] = []
    tables_map: dict[str, object] = {}

    for ref_key, ref_cols in sorted(reference.items()):
        unit, ref_tname = ref_key.split(".", 1)
        matched_idx: int | None = None
        match_reason = ""
        match_conf = 0.0

        for idx, et in enumerate(export_tables):
            if idx in used_export:
                continue
            et_unit = et.get("unit") or et.get("source")
            if et_unit and et_unit != unit:
                continue
            if et.get("derived_from") in {ref_key, ref_tname}:
                matched_idx = idx
                match_reason = f"lineage derived_from {et.get('derived_from')}"
                match_conf = 1.0
                break

        if matched_idx is None:
            for idx, et in enumerate(export_tables):
                if idx in used_export:
                    continue
                et_unit = et.get("unit") or et.get("source")
                if et_unit and et_unit != unit:
                    continue
                if str(et.get("name") or "").rsplit(".", 1)[-1] == ref_tname:
                    matched_idx = idx
                    match_reason = "exact table name"
                    match_conf = 1.0
                    break

        if matched_idx is None:
            best_idx, best_score = None, 0.0
            for idx, et in enumerate(export_tables):
                if idx in used_export:
                    continue
                et_unit = et.get("unit") or et.get("source")
                if et_unit and et_unit != unit:
                    continue
                score = _similarity(ref_tname, str(et.get("name") or "").rsplit(".", 1)[-1])
                if score > best_score:
                    best_idx, best_score = idx, score
            if best_idx is not None and best_score >= 0.6:
                matched_idx = best_idx
                match_reason = f"similar table name ({best_score:.2f})"
                match_conf = round(best_score, 2)

        if matched_idx is None:
            absent.append(ref_key)
            continue

        used_export.add(matched_idx)
        et = export_tables[matched_idx]
        et_name = str(et["name"])
        if et_name != ref_tname:
            proposals.append(Proposal("table", ref_key, et_name, None, match_reason, match_conf))

        et_cols = list(et.get("columns") or [])
        used_cols: set[int] = set()
        col_map: dict[str, object] = {}

        for rc_name, rc_type in ref_cols.items():
            c_idx: int | None = None
            c_reason = ""
            c_conf = 0.0
            for j, ec in enumerate(et_cols):
                if j in used_cols:
                    continue
                if ec.get("derived_from") in {f"{ref_key}.{rc_name}", f"{ref_tname}.{rc_name}", rc_name}:
                    c_idx = j
                    c_reason = f"lineage derived_from {ec.get('derived_from')}"
                    c_conf = 1.0
                    break
            if c_idx is None:
                for j, ec in enumerate(et_cols):
                    if j in used_cols:
                        continue
                    if ec.get("name") == rc_name:
                        c_idx = j
                        c_reason = "exact column name"
                        c_conf = 1.0
                        break
            if c_idx is None:
                best_j, best_cscore = None, 0.0
                for j, ec in enumerate(et_cols):
                    if j in used_cols:
                        continue
                    score = _similarity(rc_name, str(ec.get("name") or ""))
                    if score > best_cscore:
                        best_j, best_cscore = j, score
                if best_j is not None and best_cscore >= 0.6:
                    c_idx = best_j
                    c_reason = f"similar column name ({best_cscore:.2f})"
                    c_conf = round(best_cscore, 2)

            if c_idx is None:
                continue
            used_cols.add(c_idx)
            ec = et_cols[c_idx]
            ec_name = str(ec["name"])
            ec_type = str(ec.get("type") or rc_type)
            if ec_name != rc_name or ec_type != rc_type:
                proposals.append(Proposal("column", f"{ref_key}.{rc_name}", ec_name, ec_type, c_reason, c_conf))
                if confirmed:
                    if ec_type != rc_type:
                        col_map[rc_name] = {"name": ec_name, "type": ec_type} if ec_name != rc_name else {"type": ec_type}
                    else:
                        col_map[rc_name] = ec_name

        for j, ec in enumerate(et_cols):
            if j not in used_cols and ec.get("name"):
                extra_columns.append(f"{et_name}.{ec['name']}")

        if confirmed:
            if col_map:
                t_spec: dict[str, object] = {}
                if et_name != ref_tname:
                    t_spec["name"] = et_name
                t_spec["columns"] = col_map
                tables_map[ref_key] = t_spec
            elif et_name != ref_tname:
                tables_map[ref_key] = et_name

    extra_tables = [str(et.get("name")) for idx, et in enumerate(export_tables) if idx not in used_export and et.get("name")]

    entry: dict[str, object] = {
        "id": rid,
        "label": rlabel,
        "connection": {"dialect": rdialect, "location": rloc},
    }
    if tables_map:
        entry["tables"] = tables_map
    if absent:
        entry["absent"] = sorted(absent)

    return DraftRuntime(
        entry=entry,
        proposals=proposals,
        absent=sorted(absent),
        extra_tables=extra_tables,
        extra_columns=extra_columns,
        placeholders=placeholders,
    )


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--catalog", type=Path, required=True, help="path to the runtime catalog export (JSON or YAML)")
    ap.add_argument("--pack", type=Path, help="the client pack folder, without using the profile registry")
    ap.add_argument("--profile", help="the profile whose pack to draft a runtime for (default: PROFILE, else the registry's default)")
    ap.add_argument("--id", dest="runtime_id", help="runtime id override")
    ap.add_argument("--label", help="runtime label override")
    ap.add_argument("--dialect", help="runtime SQL dialect override")
    ap.add_argument("--location", help="runtime location override (use placeholder for missing/live values)")
    ap.add_argument("--confirmed", action="store_true", help="emit confirmed table and column renames into the entry")
    ap.add_argument("--json", type=Path, help="write full proposal and entry report as JSON")
    args = ap.parse_args(argv)

    try:
        pack_folder = args.pack
        if pack_folder is None:
            prof = resolve(args.profile) if args.profile else resolve()
            pack_folder = prof.clients / prof.client
        raw_text = args.catalog.read_text()
        catalog = json.loads(raw_text) if args.catalog.suffix.lower() == ".json" else yaml.safe_load(raw_text)
        ref = _reference_tables(pack_folder)
        draft = draft_from_catalog(
            catalog,
            ref,
            runtime_id=args.runtime_id,
            label=args.label,
            dialect=args.dialect,
            location=args.location,
            confirmed=args.confirmed,
        )
    except (OSError, ProfileError, ValueError, yaml.YAMLError) as exc:
        print(f"author-runtime: refused: {exc}", file=sys.stderr)
        return 2

    report = {
        "entry": draft.entry,
        "confirmed": args.confirmed,
        "proposals": [
            {
                "kind": p.kind,
                "reference": p.reference,
                "target": p.target,
                "target_type": p.target_type,
                "reason": p.reason,
                "confidence": p.confidence,
            }
            for p in draft.proposals
        ],
        "absent": draft.absent,
        "ignored_extra_tables": draft.extra_tables,
        "ignored_extra_columns": draft.extra_columns,
        "placeholders": draft.placeholders,
    }
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(report, indent=2) + "\n")
    print(yaml.safe_dump({"runtimes": [draft.entry]}, sort_keys=False).rstrip())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
