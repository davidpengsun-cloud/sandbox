"""Check every onboarding input before building, collecting refusals without writing.

CHECKS is ordered and extensible: later estate limits belong here, through the
estate port, before any warehouse or candidate is created.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
from collections.abc import Callable
from dataclasses import asdict, dataclass, replace
from pathlib import Path

import yaml

from kge.adapters import graphstore, preflight_loads
from kge.adapters.warehouse import estate_of
from kge.compiler import catalogue, inputs
from kge.compiler.checks import condition, expression, join_condition, locate
from kge.compiler.inputs import Declared
from kge.compiler.pack import Finding, Pack, load_yaml, read
from kge.estate.from_files import FRONT, META, NAME, PHYSICAL, document_files, document_metadata, fixture_files, planned_loads, read_documents, warehouse_inputs, warehouse_statements
from kge.eval import report as comparison
from kge.eval import sets
from kge.pipeline.access_table import Access
from kge.pipeline.profile import Profile, ProfileError, resolve
from kge.ports import EstateWarehouses

BASELINE_FIX = ("fix: release records a new baseline on the released graph (or run baseline); "
                "or clear it with setup-client BASELINE=")


@dataclass
class Report:
    declared: bool
    findings: list[Finding]
    ignored: list[str]
    suggestion: str | None
    files: dict[str, str]
    inputs_sha256: str
    fixture_sha256: str | None


@dataclass
class Context:
    prof: Profile
    pack: Pack
    declared: Declared | None
    fixture: Path | None
    estate: EstateWarehouses
    baseline: dict | None


def _name(ctx: Context, path: Path) -> str:
    for root in (ctx.pack.folder, ctx.prof.estate()):
        if path.is_relative_to(root):
            return path.relative_to(root).as_posix()
    return str(path)


def _missing_schema(ctx: Context) -> bool:
    return ctx.fixture is not None and not (ctx.fixture / "schema.yaml").is_file()


def _entries(value) -> list[dict]:
    return [v for v in value if isinstance(v, dict)] if isinstance(value, list) else []


def _tests(ctx: Context) -> dict[str, str]:
    tests = ctx.pack.data.get("tests") or {}
    out = {}
    for entry in _entries(tests.get("sets") if isinstance(tests, dict) else None):
        name, version = entry.get("name"), entry.get("version")
        if not isinstance(name, str) or not isinstance(version, str):
            continue
        stem = name if name in {"golden", "heldout"} else f"{name}_{version}"
        if ctx.declared and stem not in ctx.declared.sets:
            matches = [s for s in ctx.declared.sets if re.sub(r"_v\d+$", "", s) == name]
            if len(matches) == 1:
                stem = matches[0]
        out[stem] = version
    return out


def _set_paths(ctx: Context) -> list[Path]:
    root = ctx.fixture or ctx.prof.estate()
    if ctx.declared is None:
        manifest = ctx.pack.data.get("manifest")
        if isinstance(manifest, dict) and inputs.INPUTS in manifest:
            return []
        names = [n for n in ("golden", "heldout") if not _tests(ctx) or n in _tests(ctx)]
    else:
        names = list(ctx.declared.sets)
    paths = [root / f"{n}.json" for n in dict.fromkeys(names)]
    if ctx.declared is None:
        paths += [p for p in sorted(ctx.prof.estate().glob("*_v*.json")) if sets.FURTHER.fullmatch(p.stem)]
    for unit in sorted((ctx.pack.folder / "units").glob("*")):
        if unit.is_dir() and not unit.name.startswith("."):
            paths += [p for kind in ("golden", "team", "heldout")
                      if (p := ctx.prof.estate() / f"{kind}_{unit.name}_v1.json").is_file() and p not in paths]
    return paths


def declarations(ctx: Context) -> list[Finding]:
    out = inputs.declared_findings(ctx.pack.folder)
    declared = ctx.declared
    if declared is None:
        return out
    for name in inputs.declared_files(ctx.pack.folder, declared):
        if not (ctx.pack.folder / name).is_file():
            key = "includes" if name in declared.includes else "inputs.catalogue" if name == declared.catalogue else "inputs.estate"
            out.append(Finding("I01", f"pack.yaml:manifest.{key}", f"{name} is declared and not in the store"))
    if _missing_schema(ctx):
        out.append(Finding("I02", "pack.yaml:manifest.inputs.estate.fixture",
                           f"{ctx.prof.client}'s pack declares a fixture and none was uploaded: upload it, or run teardown to start over"))
    allowed = set(sets.declared_files(ctx.pack.folder))
    for stem in _tests(ctx):
        if stem not in allowed:
            out.append(Finding("I03", "pack.yaml:tests.sets", f"{stem} is not declared in manifest.inputs.sets"))
    for path in _set_paths(ctx):
        if not path.is_file() and not (_missing_schema(ctx) and path.is_relative_to(ctx.fixture)):
            out.append(Finding("I01", f"{_name(ctx, path)}:set", "is declared and not in the store"))
    return out


def _schema(ctx: Context, out: list[Finding]) -> dict:
    if ctx.fixture is None or not (ctx.fixture / "schema.yaml").is_file():
        return {}
    path = ctx.fixture / "schema.yaml"
    found = []
    value = load_yaml(path, _name(ctx, path), found)
    out.extend(Finding("F01", f.path, f.message) for f in found)
    houses = value.get("warehouses") if isinstance(value, dict) else None
    if not isinstance(houses, dict) or not houses:
        if not found:
            out.append(Finding("F01", f"{_name(ctx, path)}:warehouses", "names no schema warehouses"))
        return {}
    return houses


def fixture_schema(ctx: Context) -> list[Finding]:
    out = []
    houses = _schema(ctx, out)
    if ctx.fixture is None or _missing_schema(ctx):
        return out
    schema = f"{_name(ctx, ctx.fixture / 'schema.yaml')}:warehouses"
    limits = ctx.estate.limits()
    limit = limits.description_chars
    for unit, house in houses.items():
        at = f"{schema}.{unit}"
        if not isinstance(unit, str) or not NAME.fullmatch(unit) or not isinstance(house, dict) or not house.get("label"):
            out.append(Finding("F01", at, "a warehouse has a name, label and tables"))
        if not isinstance(house, dict):
            continue
        if isinstance(unit, str) and limits.label_chars is not None and len(unit) > limits.label_chars:
            out.append(Finding("F01", at, f"unit label has {len(unit)} characters; the estate limit is {limits.label_chars}"))
        tables = house.get("tables")
        if not isinstance(tables, list):
            out.append(Finding("F01", at + ".tables", "is a list of tables"))
            continue
        seen = set()
        for i, table in enumerate(tables):
            where = f"{at}.tables[{i}]"
            if not isinstance(table, dict):
                out.append(Finding("F03", where, "a table has a name, columns and primary_key"))
                continue
            name = table.get("name")
            valid_name = isinstance(name, str) and PHYSICAL.fullmatch(name)
            if not valid_name or name in seen:
                out.append(Finding("F03", where + ".name", "is a distinct table name"))
            if valid_name and limits.table_bytes is not None and len(f"{unit}_v{10**9 - 1}_{name}".encode()) > limits.table_bytes:
                out.append(Finding("F03", where + ".name", f"versioned table name exceeds the estate limit of {limits.table_bytes} bytes"))
            cols = _entries(table.get("columns"))
            if not cols or not isinstance(table.get("columns"), list) or len(cols) != len(table["columns"]):
                out.append(Finding("F03", where + ".columns", "is a non-empty list of columns"))
            names = [c.get("name") for c in cols]
            if limits.columns is not None and len(cols) > limits.columns:
                out.append(Finding("F03", where + ".columns", f"has {len(cols)} columns; the estate limit is {limits.columns}"))
            for j, col in enumerate(cols):
                loc = f"{where}.columns[{j}]"
                if not isinstance(col.get("name"), str) or not PHYSICAL.fullmatch(col["name"]):
                    out.append(Finding("F03", loc + ".name", "is a column name"))
                elif names.count(col["name"]) > 1:
                    out.append(Finding("F03", loc + ".name", "repeats a column name"))
                if isinstance(col.get("name"), str) and limits.column_chars is not None and len(col["name"]) > limits.column_chars:
                    out.append(Finding("F03", loc + ".name", f"exceeds the estate limit of {limits.column_chars} characters"))
                reason = ctx.estate.refuses_type(col.get("type")) if isinstance(col.get("type"), str) else "a column names its type"
                if reason:
                    out.append(Finding("F04", loc + ".type", reason))
                desc = col.get("description")
                if not isinstance(desc, str) or not desc.strip():
                    out.append(Finding("F05", loc + ".description", "a column has a description"))
                elif limit is not None and len(desc) > limit:
                    out.append(Finding("F05", loc + ".description", f"has {len(desc)} characters; the estate limit is {limit}"))
            pk = table.get("primary_key")
            if not isinstance(pk, list) or not pk or any(c not in names for c in pk):
                out.append(Finding("F03", where + ".primary_key", "names the table's key columns"))
            if isinstance(pk, list) and limits.primary_key_columns is not None and len(pk) > limits.primary_key_columns:
                out.append(Finding("F03", where + ".primary_key", f"exceeds the estate limit of {limits.primary_key_columns} columns"))
            keys = table.get("foreign_keys") or []
            if not isinstance(keys, list):
                out.append(Finding("F06", where + ".foreign_keys", "is a list of parent references"))
                keys = []
            if limits.foreign_keys is not None and len(keys) > limits.foreign_keys:
                out.append(Finding("F06", where + ".foreign_keys", f"exceeds the estate limit of {limits.foreign_keys} keys"))
            key_columns = set(c for c in (pk or []) if isinstance(c, str)) if isinstance(pk, list) else set()
            for key in keys:
                if isinstance(key, dict) and isinstance(key.get("columns"), list):
                    key_columns.update(c for c in key["columns"] if isinstance(c, str))
            for col in cols:
                if isinstance(col.get("name"), str) and col["name"] in key_columns and isinstance(col.get("type"), str):
                    reason = ctx.estate.refuses_key_type(col["type"])
                    if reason:
                        out.append(Finding("F04", where + ".columns", reason))
            for j, key in enumerate(keys):
                loc = f"{where}.foreign_keys[{j}]"
                if not isinstance(key, dict) or not isinstance(key.get("ref_table"), str):
                    out.append(Finding("F06", loc, "names a parent table and its columns"))
                    continue
                parent = next((t for t in _entries(tables) if t.get("name") == key["ref_table"]), None)
                if key["ref_table"] == name and not limits.self_references:
                    out.append(Finding("F06", loc, "the estate does not support self-referencing foreign keys"))
                if key["ref_table"] not in seen:
                    out.append(Finding("F06", loc, f"references {key['ref_table']}, which is unknown or listed later; parents come first"))
                parent_cols = [c.get("name") for c in _entries((parent or {}).get("columns"))]
                local, remote = key.get("columns"), key.get("ref_columns")
                if (not isinstance(local, list) or not isinstance(remote, list) or not local or len(local) != len(remote)
                        or any(c not in names for c in local) or any(c not in parent_cols for c in remote)):
                    out.append(Finding("F06", loc, "pairs existing columns with the parent's columns"))
            if valid_name:
                seen.add(name)
                if isinstance(unit, str) and NAME.fullmatch(unit):
                    out.extend(_csv(ctx, ctx.fixture / "data" / unit / f"{name}.csv", names))
    if not out:
        for unit, house in houses.items():
            refused = ctx.estate.refuses_schema(unit, warehouse_statements(house["tables"]))
            if refused:
                out.append(Finding("F03", f"fixture/schema.yaml:warehouses.{unit}", refused))
    return out


def _csv(ctx: Context, path: Path, names: list) -> list[Finding]:
    out = []
    try:
        with path.open(newline="") as stream:
            rows = csv.reader(stream, strict=True)
            if next(rows, None) != names:
                out.append(Finding("F07", f"{_name(ctx, path)}:row 1", f"header must be {', '.join(map(str, names))}"))
            for row in rows:
                if len(row) != len(names):
                    out.append(Finding("F07", f"{_name(ctx, path)}:row {rows.line_num}", f"has {len(row)} fields; expected {len(names)}"))
    except (OSError, UnicodeDecodeError, csv.Error) as exc:
        out.append(Finding("F07", f"{_name(ctx, path)}:row 1", f"cannot read CSV ({type(exc).__name__})"))
    return out


def _lock(ctx: Context) -> dict:
    houses = _schema(ctx, [])
    sources = {u: {"tables": [{**t, "columns": [c for c in _entries(t.get('columns')) if isinstance(c.get('name'), str)]}
                             for t in _entries(h.get("tables")) if isinstance(t.get("name"), str)]}
               for u, h in houses.items() if isinstance(h, dict)}
    if ctx.fixture is None:
        found = []
        path = ctx.pack.folder / "pack.lock"
        value = load_yaml(path, "pack.lock", found) if path.is_file() else None
        return value if isinstance(value, dict) and isinstance(value.get("sources"), dict) else {"sources": {}}
    return {"sources": sources}


def references(ctx: Context) -> list[Finding]:
    if ctx.fixture is None or _missing_schema(ctx):
        return []
    lock, out = _lock(ctx), []
    for section in ("sources", "curated_sources"):
        for i, source in enumerate(_entries(ctx.pack.data.get(section))):
            if source.get("harvest") is True and (not isinstance(source.get("id"), str) or
                    (source["id"] not in lock["sources"] and source["id"] not in ctx.pack.warehouses)):
                out.append(Finding("F01", ctx.pack.at(f"{section}[{i}]"), f"harvested source {source.get('id')} has no schema warehouse"))

    def ref(value, at, depth, table=None):
        if isinstance(value, str) and table and "." not in value:
            value = f"{table}.{value}"
        if isinstance(value, str) and value.split(".")[0] in ctx.pack.warehouses:
            return
        if locate(lock, value, depth) is None:
            out.append(Finding("F03", ctx.pack.at(at), f"{value} is not a {'table' if depth == 2 else 'column'} in the fixture"))

    def walk(value, at="", table=None):
        if isinstance(value, list):
            for i, item in enumerate(value):
                walk(item, f"{at}[{i}]", table)
        elif isinstance(value, dict):
            table = value.get("table", table)
            for key, item in value.items():
                loc = f"{at}.{key}" if at else key
                if key in {"table", "fact"} or (at.startswith("joins[") and key in {"from", "to", "through"}):
                    ref(item, loc, 2)
                elif key in {"column", "group_by", "valid_from", "valid_to", "is_current", "release_date", "freeze_date", "released"} or (key == "period" and at.startswith("policies.release_driver")):
                    ref(item, loc, 3, table)
                elif key in {"columns", "key", "derived_from"} and isinstance(item, list):
                    for i, entry in enumerate(item):
                        ref(entry, f"{loc}[{i}]", 2 if key == "derived_from" else 3, table)
                else:
                    walk(item, loc, table)
    for section, value in ctx.pack.data.items():
        if section not in {"manifest", "schema", "sources", "curated_sources", "access", "tests", "runtimes", "default_runtimes"}:
            walk(value, section)
    if "runtimes" in ctx.pack.data or "default_runtimes" in ctx.pack.data:
        from kge.compiler import checks as compiler_checks
        shapes = []
        for name in ("runtimes", "default_runtimes"):
            if name in ctx.pack.data:
                found_shape: list[tuple[str, str]] = []
                compiler_checks.shape(ctx.pack.data[name], compiler_checks.SECTIONS[name], name, found_shape)
                shapes += [Finding("S01", ctx.pack.at(w), m) for w, m in found_shape]
        out.extend(shapes)
        if not shapes:
            pack_for_rt = replace(ctx.pack, lock=lock)
            out.extend(compiler_checks.runtimes(pack_for_rt))
    for i, measure in enumerate(_entries(ctx.pack.data.get("measures"))):
        if locate(lock, measure.get("column")) is not None and isinstance(measure.get("expression"), str):
            problems, _, _ = expression(measure["expression"], measure["column"], lock)
            out.extend(Finding("F03", ctx.pack.at(f"measures[{i}].expression"), p) for p in problems)
    for section in ("joins", "filters", "row_selectors"):
        for i, entry in enumerate(_entries(ctx.pack.data.get(section))):
            tables = {entry[k] for k in ("table", "from", "to", "through") if isinstance(entry.get(k), str)}
            if not tables or any(locate(lock, t, 2) is None for t in tables):
                continue
            names = {t.split(".")[-1]: t for t in tables}
            if isinstance(entry.get("alias"), str) and isinstance(entry.get("to"), str):
                names[entry["alias"]] = entry["to"]
            field = "condition" if section == "joins" else "predicate"
            if isinstance(entry.get(field), str):
                if section == "joins" and all(isinstance(entry.get(k), str) for k in ("from", "to")):
                    checked, _ = join_condition(entry, lock)
                else:
                    within = entry["table"].split(".")[0] if section == "row_selectors" else None
                    checked = condition(entry[field], lock, names, tables, within)
                out.extend(Finding("F03", ctx.pack.at(f"{section}[{i}].{field}"), p) for p in checked.problems)
    return out


def enrichment(ctx: Context) -> list[Finding]:
    pack = replace(ctx.pack, lock=_lock(ctx))
    try:
        _, found = catalogue.read(pack)
    except (KeyError, TypeError, AttributeError, ValueError):
        name = ctx.declared.catalogue if ctx.declared else catalogue.FILE
        return [Finding("F02", f"{name}:catalogue", "catalogue entries must name declared units, tables and fields")]
    return [Finding("F02", f.path, f.message) for f in found if f.rule == "C01"
            and not (_missing_schema(ctx) and f.message.startswith("names unknown table "))]


def documents(ctx: Context) -> list[Finding]:
    if ctx.fixture is None:
        return []
    houses, out = _schema(ctx, []), []
    for shelf, path in document_files(ctx.fixture):
        loc = f"{_name(ctx, path)}:front matter"
        try:
            meta, _ = document_metadata(path)
            if not isinstance(meta, dict) or set(meta) - set(META) or not meta.get("title") or not meta.get("kind"):
                out.append(Finding("F08", loc, "front matter between --- lines names a title, kind and valid metadata"))
                continue
            if not _missing_schema(ctx) and meta.get("unit") is not None and (not isinstance(meta["unit"], str) or meta["unit"] not in houses):
                out.append(Finding("F08", f"{_name(ctx, path)}:unit", f"{meta['unit']} is not a schema warehouse"))
        except (OSError, ValueError, yaml.YAMLError):
            out.append(Finding("F08", loc, "cannot read document front matter"))
    return out


def _baseline_set(ctx: Context, path: Path) -> dict | None:
    recorded = (ctx.baseline or {}).get("sets") or {}
    return next((s for key, s in recorded.items() if isinstance(s, dict) and
                 (s.get("file") == path.name or (not s.get("file") and key == path.stem))), None)


def question_sets(ctx: Context) -> list[Finding]:
    out = []
    for path in _set_paths(ctx):
        if not path.is_file():
            units_write_it = (not ctx.fixture and path.stem == "golden"
                              and any(p.is_dir() and not p.name.startswith(".") for p in (ctx.pack.folder / "units").glob("*")))
            if ctx.declared is None and not (ctx.fixture and path.stem == "heldout") and not units_write_it:
                out.append(Finding("F09", f"{_name(ctx, path)}:set", "question set is missing"))
            continue
        why = sets.undeclared_change(path, _baseline_set(ctx, path))
        if why:
            out.append(Finding("F10", f"{_name(ctx, path)}:changes_from", why))
        try:
            data = sets.read(path)
            version = _tests(ctx).get(path.stem)
            version = version or ("v2" if path.stem == "golden" else "v1" if path.stem == "heldout" else None)
            suffix = re.search(r"_v(\d+)$", path.stem)
            version = version or (f"v{suffix[1]}" if suffix else None)
            if version and data["version"] != version:
                out.append(Finding("F09", f"{_name(ctx, path)}:version", f"declares {version}, but the file is {data['version']}; versions after v1 use the object form"))
            for i, item in enumerate(data["items"]):
                if not isinstance(item.get("question"), str) or not item["question"].strip():
                    out.append(Finding("F09", f"{_name(ctx, path)}:items[{i}].question", "is a question"))
                if not isinstance(item.get("kind"), str) or not item["kind"]:
                    out.append(Finding("F09", f"{_name(ctx, path)}:items[{i}].kind", "names the question's kind"))
                if not isinstance(item.get("expect"), dict):
                    out.append(Finding("F09", f"{_name(ctx, path)}:items[{i}].expect", "is an object of expected answers"))
        except (OSError, ValueError) as exc:
            out.append(Finding("F09", f"{_name(ctx, path)}:set", str(exc)))
    return out


def access(ctx: Context) -> list[Finding]:
    definitions = ctx.pack.data.get("access")
    roles = definitions.get("roles") if isinstance(definitions, dict) else None
    valid = [r for r in _entries(roles) if isinstance(r.get("id"), str) and isinstance(r.get("processes"), list)]
    out = []
    if not isinstance(roles, list) or len(valid) != len(roles):
        out.append(Finding("S01", ctx.pack.at("access.roles"), "roles name an id and a list of processes"))
    known = {r["id"] for r in valid}
    for row in Access.for_profile(ctx.prof).grants():
        for role in row["roles"]:
            if role != "access_admin" and role not in known:
                out.append(Finding("A01", f"access:{row['username']}.roles",
                                   f"{row['username']} holds {role}, which the pack does not define"))
    return out


def baseline(ctx: Context) -> list[Finding]:
    if ctx.baseline is None:
        return []
    out = []
    who = ctx.baseline.get("principal")
    definitions = ctx.pack.data.get("access")
    roles = _entries(definitions.get("roles")) if isinstance(definitions, dict) else []
    if not isinstance(who, str) or who not in [r.get("id") for r in roles]:
        out.append(Finding("B01", f"{ctx.prof.baseline}:principal", f"the role it asked as no longer exists: {who}; {BASELINE_FIX}"))
    comparable = False
    for path in _set_paths(ctx):
        try:
            data = sets.read(path)
            old = _baseline_set(ctx, path)
            now = {**data, "items": len(data["items"]), "size": len(data["items"]),
                   "sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "item_sha256": sets.item_hashes(data["items"])}
            changed = comparison.changed_items(old, now)
            if (data["items"] and comparison.same_set(old, now)) or (changed is not None and set(now["item_sha256"]) - set(changed)):
                comparable = True
        except (OSError, ValueError):
            continue
    graph = ctx.baseline.get("graph")
    held = graph.get("store") if isinstance(graph, dict) else None
    comparable_base = {**ctx.baseline, "graph": {"store": held if isinstance(held, dict) else {}}}
    apart = comparison.store_apart(comparable_base, graphstore.store_info().get("engine"))
    if apart or (not comparable and not _missing_schema(ctx)):
        why = apart or "no declared set at a comparable version"
        out.append(Finding("B02", f"{ctx.prof.baseline}:sets", f"the baseline has nothing comparable: {why}; {BASELINE_FIX}"))
    return out


CHECKS: list[Callable[[Context], list[Finding]]] = [declarations, fixture_schema, references, enrichment, documents, question_sets, access, baseline]


def _digest(blobs: dict[str, bytes]) -> str:
    digest = hashlib.sha256()
    for name, data in sorted(blobs.items()):
        digest.update(name.encode() + b"\0" + data + b"\0")
    return digest.hexdigest()


def _document_budget(doc: dict, text: str, terms: list[str]) -> list[str]:
    from kge.pipeline.chunking import document_chunks
    return [json.dumps({**doc, "text": chunk, "excerpt": chunk[:200], "terms": terms}, ensure_ascii=False, default=str)
            for chunk in document_chunks(doc.get("title"), text)]


def _additional_document_loads(ctx: Context, terms: list[str]) -> list[str]:
    """Budget the kept estate and both live and pending unit inputs, without building or drafting them.

    Keeping both versions is conservative: a pending update may not land, and a fixture rebuild may replace estate
    documents. This avoids relying on mutable certification decisions when deciding whether staging is needed.
    Schema previews are in memory and use the same catalogue/type rendering as unit build's data dictionary.
    Remote application state need not mirror unit.yaml; its missing label is budgeted at source.validate_unit_label's
    80-character limit using four-byte letters, including their longer YAML escapes, without creating state tables.
    """
    from kge.estate import ddl
    chunks = []
    index = ctx.prof.estate() / "documents.json"
    for doc in json.loads(index.read_text()) if index.is_file() else []:
        chunks.extend(_document_budget(doc, (ctx.prof.estate() / doc["path"]).read_text(), terms))
    for folder in sorted((ctx.pack.folder / "units").glob("*")):
        if not folder.is_dir() or folder.name.startswith("."):
            continue
        manifest = folder / "unit.yaml"
        held = (yaml.safe_load(manifest.read_text()) or {}) if manifest.is_file() else {}
        for source_folder in (folder, folder / "update"):
            label = ((held.get("update") or {}).get("label") if source_folder.name == "update" else None) or held.get("label") or "\U00010000" * 80
            schema = source_folder / "schema.sql"
            if schema.is_file():
                model = ddl.parse(schema.read_text(), folder.name)
                with ctx.estate.preview(ddl.statements(model)) as preview:
                    catalogue = ddl.catalogue(preview, model)
                title = f"{label} data dictionary (drafted)"
                doc = {"doc_id": f"DD-{folder.name.upper()}", "shelf": "uncertified", "title": title,
                       "kind": "data_dictionary", "unit": folder.name, "owner": f"{label} Data Steward", "version": "draft"}
                lines = ["---", yaml.safe_dump({k: doc[k] for k in ("title", "kind", "unit", "owner", "version")},
                                              sort_keys=False).rstrip(), "---", ""]
                for table in catalogue["tables"]:
                    lines += [f"## {table['name']}", "", table.get("comment") or "No table comment.", "",
                              "| Column | Type | Key | Comment |", "|---|---|---|---|"]
                    lines += [f"| {col['name']} | {col['type']} | {col.get('key') or ''} | {col.get('comment') or ''} |"
                              for col in table["columns"]]
                    lines.append("")
                chunks.extend(_document_budget(doc, "\n".join(lines), terms))
            for path in sorted(fixture_files(ctx.pack.folder, f"{source_folder.relative_to(ctx.pack.folder).as_posix()}/semantic/docs/**/*")):
                text = path.read_text()
                match = FRONT.match(text)
                if not match:
                    raise ValueError(f"{path}: document needs YAML front matter")
                meta = yaml.safe_load(match.group(1))
                doc = {**meta, "doc_id": f"DOC-{folder.name.upper()}-{path.name}", "unit": folder.name,
                       "shelf": "uncertified"}
                rendered = "---\n" + yaml.safe_dump({**meta, "unit": folder.name}, sort_keys=False) + "---\n\n" + text[match.end():].strip() + "\n"
                chunks.extend(_document_budget(doc, rendered, terms))
    return chunks


def _unit_warehouse_loads(ctx: Context) -> list[int]:
    """Bound live repair and pending-update CSV loads using the same conservative row envelope as fixture planning."""
    loads = []
    for folder in sorted((ctx.pack.folder / "units").glob("*")):
        for source_folder in (folder, folder / "update"):
            for path in sorted(fixture_files(ctx.pack.folder, f"{source_folder.relative_to(ctx.pack.folder).as_posix()}/data/*.csv")):
                with path.open(newline="") as stream:
                    size, count = 0, 0
                    for row in csv.DictReader(stream):
                        size += len(json.dumps(row, ensure_ascii=False).encode()) + 1 + 64 * len(row)
                        count += 1
                        if count == 20000:
                            loads.append(size)
                            size, count = 0, 0
                    if count:
                        loads.append(size)
    return loads


def staging(ctx: Context) -> list[Finding]:
    """Plan all retrieval input bytes, including fixtureless units, before any warehouse or candidate writes."""
    if _missing_schema(ctx):
        return []
    try:
        terms = [entry.get("name", "") for entry in _entries(ctx.pack.data.get("glossary"))]
        for path in sorted(fixture_files(ctx.pack.folder, "units/*/**/draft.yaml")):
            draft = yaml.safe_load(path.read_text()) or {}
            for item in _entries(draft.get("items")):
                terms.extend(entry["name"] for entry in _entries((item.get("pack") or {}).get("glossary"))
                             if isinstance(entry.get("name"), str))
        terms = list(dict.fromkeys(terms))
        loads, chunks = [], []
        if ctx.fixture is not None:
            houses = _schema(ctx, [])
            summary = ctx.prof.estate() / "summary.json"
            previous = json.loads(summary.read_text()) if summary.is_file() else {}
            digests = warehouse_inputs(ctx.fixture, houses)
            changed = [unit for unit in houses if previous.get("warehouse_inputs", {}).get(unit) != digests[unit]]
            labels = {u: house["label"] for u, house in houses.items()}
            loads, chunks = planned_loads(ctx.fixture, houses, read_documents(ctx.fixture, labels), changed, terms)
        loads.extend(_unit_warehouse_loads(ctx))
        chunks.extend(_additional_document_loads(ctx, terms))
        preflight_loads(ctx.prof.client, loads, chunks)
    except (OSError, ValueError, RuntimeError) as exc:
        return [Finding("F11", "onboard:loads", str(exc))]
    return []


def check(prof) -> Report:
    """Read and validate inputs before building; a large load also probes and removes one scratch object."""
    folder = prof.clients / prof.client
    pack = read(prof.clients, prof.client, lock=False)
    declared = inputs.read_declared(folder)
    manifest = pack.data.get("manifest")
    declaring = isinstance(manifest, dict) and inputs.INPUTS in manifest
    fixture = folder / declared.fixture if declared and declared.fixture else None
    if not declaring and (folder / "fixture/schema.yaml").is_file():
        fixture = folder / "fixture"
    findings = list(pack.findings)
    base = None
    if prof.baseline:
        try:
            base = json.loads(Path(prof.baseline).read_text())
            if not isinstance(base, dict) or not isinstance(base.get("sets"), dict):
                raise ValueError("baseline must hold a sets mapping")
        except (OSError, ValueError) as exc:
            findings.append(Finding("B02", f"{prof.baseline}:baseline", f"baseline cannot be read ({type(exc).__name__}); {BASELINE_FIX}"))
            base = None
    ctx = Context(prof, pack, declared, fixture, estate_of(prof), base)
    for run in CHECKS:
        findings.extend(run(ctx))
    if not findings:
        findings.extend(staging(ctx))
    names = inputs.declared_files(folder, declared) if declared else ["pack.yaml"]
    if not declaring:
        included = manifest.get("includes") if isinstance(manifest, dict) else None
        names += [n for n in included if isinstance(n, str) and inputs.INCLUDE.fullmatch(n)
                  and folder.resolve() in (folder / n).resolve().parents] if isinstance(included, list) else []
        if (folder / "catalogue.yaml").is_file():
            names.append("catalogue.yaml")
    if fixture is not None:
        names += [p.relative_to(folder).as_posix() for p in fixture_files(fixture)]
    blobs = {}
    paths = {n: folder / n for n in names if (folder / n).is_file()}
    for name, path in sorted(paths.items()):
        try:
            blobs[name] = path.read_bytes()
        except OSError as exc:
            findings.append(Finding("I01", f"{name}:file", f"cannot read input ({type(exc).__name__})"))
    fixture_digest = None
    if fixture is not None and fixture.is_dir():
        prefix = fixture.relative_to(folder).as_posix() + "/"
        fixture_digest = _digest({n[len(prefix):]: b for n, b in blobs.items() if n.startswith(prefix)})
    return Report(declaring, findings, inputs.ignored(folder, prof.estate(), declared) if declared else [],
                  inputs.suggest(folder, prof.estate()) if not declaring else None,
                  {n: hashlib.sha256(b).hexdigest() for n, b in sorted(blobs.items())}, _digest(blobs), fixture_digest)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--json", type=Path)
    ap.add_argument("--profile", help="the profile whose inputs to check (default: PROFILE, else the registry's default)")
    args = ap.parse_args(argv)
    try:
        result = check(resolve(args.profile) if args.profile else resolve())
    except ProfileError as exc:
        print(f"onboard: refused: {exc}")
        return 2
    for path in result.ignored:
        print(f"onboard: ignored: {path}")
    if result.suggestion:
        print("onboard: pack.yaml declares no inputs; add this to its manifest:")
        print("\n".join("  " + line for line in result.suggestion.splitlines()))
    for finding in result.findings:
        print(f"onboard: refused: {finding}")
    if result.findings:
        print(f"onboard: refused, {len(result.findings)} finding(s): nothing was written")
        return 2
    if args.json:
        args.json.write_text(json.dumps(asdict(result), indent=2) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
