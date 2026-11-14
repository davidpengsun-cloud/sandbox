"""Build a client's estate from files: its warehouses, its documents and its question sets, as a fixture holds them.

    python3 -m kge.estate.from_files [--profile <id>] [--fixture <dir>] [--out <dir>] [--golden-only]

A client whose folder holds a fixture, <clients>/<client>/fixture/, has its
estate built from it, including the demo's own generated fixture.
The sample generator remains in kge/estate/generate.py. The estate is the profile's (kge/pipeline/profile.py:
--profile, else PROFILE, else the registry's default): the folder its
warehouses are in (Profile.estate); --fixture and --out name others. The
fixture holds data, never code:

  schema.yaml            each warehouse, a source's: its label and its tables, each with its columns (name, type and
                         description, in order), its primary key and its foreign keys; optional document_order
                         lists every document path once to preserve an existing landed release
  data/<warehouse>/<table>.csv
                         the table's rows, a header naming its columns; an empty field is NULL
  docs/<shelf>/<id>.md   a document on the certified or the uncertified shelf: front matter (title, kind, unit, owner,
                         version, published_at, reviewed_at, superseded_by, optional certification and planted test tags) between ---
                         lines, then its text
  docs/<shelf>/<file>    other documents, with front matter in <file>.yaml beside each one
  golden.json            the golden set: its items (id, kind, question, expect), as kge/eval/sets.py reads a set
  heldout.json           the held-out set, if it holds one: paraphrases of golden questions (each with its
                         paraphrase_of), read as the golden set is
  <stem>.json            further question sets selected by manifest.inputs.sets

It writes, into the estate, what kge/estate/generate.py writes for the demo's
client, so certification, the lock and the graph read the one as the other:

  warehouses <warehouse>          each table with its keys and its columns' descriptions, then its rows, through
                                  the estate's warehouse port (the factory's open_estate): a DuckDB file in
                                  warehouses/, or with WAREHOUSE_BACKEND=bigquery the client's BigQuery dataset
  docs/<shelf>/<id>.md, documents.json
                                  each document with its title and metadata table at its head, as the generator
                                  writes one, and the index of them
  <set>.json                      each selected question set, as the fixture holds it
  summary.json                    what was built, and from which fixture

With --golden-only it writes the question sets alone. The same fixture gives the
same catalogues, documents and sets. Conditional builds skip an unchanged fixture;
an unconditional build on a warehouse that builds apart allocates a new version,
which certification includes in the landing's identity. A fixture it cannot read is an
exit 2, naming what is wrong, and nothing is written.

Names no client: the fixture is the client's.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import shutil
import sys
from pathlib import Path

import yaml

from kge.adapters import docreader, preflight_loads
from kge.adapters.warehouse import open_estate
from kge.compiler.inputs import declared_findings, read_declared
from kge.pipeline.chunking import document_chunks
from kge.ports import DocumentReader, EstateWarehouses, ObjectStore, WarehouseRefused

FIXTURE = "fixture"
SHELVES = ("certified", "uncertified")
META = ("title", "kind", "unit", "owner", "version", "published_at", "reviewed_at", "superseded_by", "certification", "planted")
NAME = re.compile(r"[a-z][a-z0-9_]*")
PHYSICAL = re.compile(r"[A-Za-z][A-Za-z0-9_]*")
FRONT = re.compile(r"\A---\n(.*?)\n---\n", re.S)


class Unreadable(ValueError):
    """A fixture this cannot build an estate from, and why."""


def fixture_files(fixture: Path, pattern: str = "**/*"):
    """Files that count as fixture inputs, excluding dot-names at every depth."""
    return (path for path in fixture.glob(pattern) if path.is_file()
            and not any(name.startswith(".") for name in path.relative_to(fixture).parts))


def read_schema(fixture: Path) -> dict:
    """schema.yaml, checked: each warehouse and table a name, each column a name, a type and a description."""
    path = fixture / "schema.yaml"
    if not path.is_file():
        raise Unreadable(f"{fixture} holds no schema.yaml: a fixture names its warehouses and their tables")
    try:
        held = yaml.safe_load(path.read_text())
    except yaml.YAMLError as exc:
        raise Unreadable(f"{path} is not YAML: {exc}") from None
    houses = held.get("warehouses") if isinstance(held, dict) else None
    if not isinstance(houses, dict) or not houses:
        raise Unreadable(f"{path} names no warehouses")
    for w, house in houses.items():
        if not isinstance(w, str) or not NAME.fullmatch(w) or not isinstance(house, dict) or not house.get("label"):
            raise Unreadable(f"{path}: warehouse {w!r} is a name with a label and its tables")
        for t in house.get("tables") or []:
            if not PHYSICAL.fullmatch(str(t.get("name"))) or not t.get("columns") or not t.get("primary_key"):
                raise Unreadable(f"{path}: {w}: a table is a name, its columns and its primary key, not {t!r}")
            for c in t["columns"]:
                if not (isinstance(c, dict) and PHYSICAL.fullmatch(str(c.get("name"))) and c.get("type") and c.get("description")):
                    raise Unreadable(f"{path}: {w}.{t['name']}: a column is a name, a type and a description, not {c!r}")
    return houses


def quoted(text: str) -> str:
    return "'" + text.replace("'", "''") + "'"


def warehouse_statements(tables: list[dict]) -> list[str]:
    """One warehouse's DDL: each table, its keys and its columns' descriptions."""
    out = []
    for t in tables:
        cols = ", ".join(f"{c['name']} {c['type']}" for c in t["columns"])
        fks = "".join(f", FOREIGN KEY ({', '.join(k['columns'])}) REFERENCES {k['ref_table']}({', '.join(k['ref_columns'])})"
                      for k in t.get("foreign_keys") or [])
        out.append(f"CREATE TABLE {t['name']} ({cols}, PRIMARY KEY ({', '.join(t['primary_key'])}){fks})")
        for c in t["columns"]:
            out.append(f"COMMENT ON COLUMN {t['name']}.{c['name']} IS {quoted(c['description'])}")
    return out


def build_warehouse(estate: EstateWarehouses, unit: str, tables: list[dict], data: Path,
                    version: int | None = None) -> dict[str, int]:
    """One warehouse staged (published by the caller once every one is built): each table, its keys and descriptions,
    then its rows from <data>/<table>.csv. Rows by table."""
    counts = {}
    con = estate.stage(unit, warehouse_statements(tables), version)
    try:
        for t in tables:
            counts[t["name"]] = load_rows(con, t, data / f"{t['name']}.csv")
    finally:
        con.close()
    return counts


def load_rows(con, table: dict, csv_path: Path) -> int:
    """The table's rows, its CSV's header its columns in order; an empty field is NULL. Unreadable if they differ."""
    names = [c["name"] for c in table["columns"]]
    if not csv_path.is_file():
        raise Unreadable(f"no rows for {table['name']}: {csv_path} is not a file")
    with csv_path.open(newline="") as f:
        rows = list(csv.reader(f))
    if not rows or rows[0] != names:
        raise Unreadable(f"{csv_path}: the header must name {table['name']}'s columns in order, {', '.join(names)}")
    body = [[v if v != "" else None for v in r] for r in rows[1:]]
    if any(len(r) != len(names) for r in body):
        raise Unreadable(f"{csv_path}: every row has {len(names)} fields")
    if body:
        con.load(table["name"], names, body)
    return len(body)


def document_files(fixture: Path):
    """All shelf files except their adjacent <filename>.yaml front matter."""
    for shelf in SHELVES:
        paths = sorted(fixture_files(fixture, f"docs/{shelf}/*"))
        sidecars = {p.with_name(p.name + ".yaml") for p in paths}
        yield from ((shelf, p) for p in paths if p not in sidecars)


def document_metadata(path: Path) -> tuple[dict, str]:
    """Markdown keeps inline front matter; other files have adjacent <filename>.yaml metadata."""
    if path.suffix.lower() == ".md":
        text = path.read_text()
        match = FRONT.match(text)
        if not match:
            raise Unreadable(f"{path}: a document opens with its front matter, between --- lines")
        raw, body = match.group(1), text[match.end():].strip()
    else:
        sidecar = path.with_name(path.name + ".yaml")
        if not sidecar.is_file():
            raise Unreadable(f"{path}: front matter belongs in {sidecar.name}")
        raw, body = sidecar.read_text(), ""
    try:
        meta = yaml.safe_load(raw)
    except yaml.YAMLError:
        raise Unreadable(f"{path}: cannot read document front matter") from None
    if not isinstance(meta, dict):
        raise Unreadable(f"{path}: front matter is a mapping with a title and a kind")
    return meta, body


def read_documents(fixture: Path, labels: dict[str, str], reader: DocumentReader | None = None,
                   kept: ObjectStore | None = None) -> list[dict]:
    """Checked documents; a reader converts content only during the build, never in up-front validation."""
    out, paths, seen = [], {}, set()
    for shelf, path in document_files(fixture):
        meta, body = document_metadata(path)
        unknown = sorted(set(meta) - set(META))
        if unknown or not meta.get("title") or not meta.get("kind"):
            raise Unreadable(f"{path}: front matter of {', '.join(META)}, a title and a kind among them"
                             + (f"; not {', '.join(unknown)}" if unknown else ""))
        if meta.get("unit") and meta["unit"] not in labels:
            raise Unreadable(f"{path}: unit {meta['unit']!r} is no warehouse of the schema's")
        if "planted" in meta and (not isinstance(meta["planted"], list)
                                  or any(not isinstance(tag, str) for tag in meta["planted"])):
            raise Unreadable(f"{path}: planted must be a list of strings")
        if path.stem in seen:
            raise Unreadable(f"{path}: duplicate document id {path.stem}")
        seen.add(path.stem)
        extra = {}
        if path.suffix.lower() != ".md" and reader is not None:
            format = path.suffix.lower().lstrip(".") or "extensionless"
            extra = {"original": path.relative_to(fixture).as_posix()}
            if format in reader.formats:
                reading = docreader.read_once(reader, kept, path.read_bytes(), format, path.relative_to(fixture).as_posix())
                body = reading.markdown
                extra["reading"] = reader.identity()
            else:
                extra["content_unread"] = f"content not read: {format} isn't supported by {reader.name}"
        doc = {"doc_id": path.stem, "shelf": shelf,
               **{k: (meta[k] if k in {"certification", "planted"} else str(meta[k])) if meta.get(k) is not None else None for k in META},
               "body": body, **extra}
        out.append(doc)
        paths[path.relative_to(fixture).as_posix()] = doc
    order = yaml.safe_load((fixture / "schema.yaml").read_text()).get("document_order")
    if order is not None:
        if (not isinstance(order, list) or any(not isinstance(p, str) for p in order)
                or len(order) != len(paths) or set(order) != set(paths)):
            raise Unreadable(f"{fixture}/schema.yaml: document_order must name every document path exactly once")
        out = [paths[p] for p in order]
    return out


def document_text(d: dict, labels: dict[str, str]) -> str:
    """A document as the generator writes one (kge/estate/documents.py): its title, its metadata table, then its text."""
    meta = [("Shelf", d["shelf"]), ("Document", d["doc_id"]), ("Kind", d["kind"])]
    meta += [(name, value) for name, value in (("Business unit", labels.get(d["unit"]) if d["unit"] else None), ("Owner", d["owner"]),
                                               ("Version", d["version"]), ("Published", d["published_at"]),
                                               ("Superseded by", d["superseded_by"])) if value]
    head = [f"# {d['title']}", "", "| | |", "|---|---|", *(f"| {k} | {v} |" for k, v in meta), ""]
    return "\n".join(head) + d["body"] + "\n"


def golden_set(fixture: Path) -> int:
    """The fixture's golden set, checked: how many questions it holds."""
    from kge.eval import sets
    path = fixture / "golden.json"
    if not path.is_file():
        raise Unreadable(f"{fixture} holds no golden.json: a fixture's golden set")
    try:
        return len(sets.items(path))
    except ValueError as exc:
        raise Unreadable(str(exc)) from None


def heldout_set(fixture: Path) -> int | None:
    """The fixture's held-out set, checked as its golden set is: how many questions it holds; None if it holds none."""
    from kge.eval import sets
    path = fixture / "heldout.json"
    if not path.is_file():
        return None
    try:
        return len(sets.items(path))
    except ValueError as exc:
        raise Unreadable(str(exc)) from None


def write_golden(fixture: Path, out: Path, sets: tuple[str, ...] = ("golden", "heldout")) -> dict[str, int]:
    """Copy the selected sets, checked before any is written; counts by <set>_questions."""
    counts = question_sets(fixture, sets)
    out.mkdir(parents=True, exist_ok=True)
    for name in sets:
        if f"{name}_questions" in counts:
            shutil.copyfile(fixture / f"{name}.json", out / f"{name}.json")
    return counts


def pack_folder(fixture: Path) -> Path:
    """The enclosing pack, including fixtures declared in a nested folder."""
    return next((p for p in fixture.parents if (p / "pack.yaml").is_file()), fixture.parent)


def question_sets(fixture: Path, names: tuple[str, ...]) -> dict[str, int]:
    """Check every selected set before writing; an older pack may omit heldout."""
    from kge.eval import sets
    folder = pack_folder(fixture)
    findings = declared_findings(folder)
    if findings:
        raise Unreadable("; ".join(str(f) for f in findings))
    legacy = read_declared(folder) is None
    counts = {}
    for name in names:
        if not isinstance(name, str) or not NAME.fullmatch(name):
            raise Unreadable(f"{name!r} is not a question set file stem")
        path = fixture / f"{name}.json"
        if not path.is_file():
            if legacy and name == "heldout":
                continue
            raise Unreadable(f"{fixture} holds no {name}.json: a fixture's selected question set")
        try:
            counts[f"{name}_questions"] = len(sets.items(path))
        except ValueError as exc:
            raise Unreadable(str(exc)) from None
    return counts


def leftover_files(out: Path, written: set[str]) -> list[str]:
    """Estate files absent from written, as relative paths, in order.

    Warehouse history and the job's artifacts, stores, landing and certification
    records can share this folder; they are not fixture estate inputs.
    Callers include live units' implicit sets in written to keep them.
    """
    out = Path(out)
    kept = {"warehouses", "artifacts", "stores", "landing", "certification"}
    files = (p for root in out.glob("*") if root.name not in kept
             for p in (root.rglob("*") if root.is_dir() else [root]) if p.is_file())
    return sorted(p.relative_to(out).as_posix() for p in files if p.relative_to(out).as_posix() not in written)


def fixture_sha256(pack_dir: Path) -> str:
    """Fixture files in relative POSIX path order, skipping names beginning with '.' in any path component.
    Each path is delimited from its bytes and the next path by NUL."""
    return _fixture_sha256(Path(pack_dir) / FIXTURE)


def _fixture_sha256(fixture: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(fixture_files(fixture), key=lambda p: p.relative_to(fixture).as_posix()):
        digest.update(path.relative_to(fixture).as_posix().encode() + b"\0" + path.read_bytes() + b"\0")
    return digest.hexdigest()


def warehouse_inputs(fixture: Path, houses: dict) -> dict[str, str]:
    """Hash each warehouse's schema and named CSV bytes independently of document and question-set inputs."""
    found = {}
    for unit, house in houses.items():
        digest = hashlib.sha256(json.dumps(house, sort_keys=True, separators=(",", ":"),
                                           ensure_ascii=False).encode())
        for table in sorted(house.get("tables") or [], key=lambda value: value["name"]):
            name = f"data/{unit}/{table['name']}.csv"
            digest.update(b"\0" + name.encode() + b"\0" + (fixture / name).read_bytes())
        found[unit] = digest.hexdigest()
    return found


def planned_loads(fixture: Path, houses: dict, docs: list[dict], changed: list[str], terms: list[str] = ()) -> tuple[list[int], list[str]]:
    """Conservative load byte budgets; document metadata and every possible term are included for each chunk."""
    loads = []
    for unit in changed:
        for table in houses[unit]["tables"]:
            with (fixture / "data" / unit / f"{table['name']}.csv").open(newline="") as stream:
                total, count = 0, 0
                for row in csv.DictReader(stream):
                    total += len(json.dumps(row, ensure_ascii=False).encode()) + 1 + 64 * len(row)
                    count += 1
                    if count == 20000:
                        loads.append(total)
                        total, count = 0, 0
                if count:
                    loads.append(total)
    labels = {unit: house["label"] for unit, house in houses.items()}
    chunks = []
    for doc in docs:
        for text in document_chunks(doc["title"], document_text(doc, labels)):
            chunks.append(json.dumps({"doc": doc["doc_id"], "title": doc["title"], "shelf": doc["shelf"],
                                      "unit": doc["unit"], "text": text, "excerpt": text[:200], "terms": terms},
                                     ensure_ascii=False))
    return loads, chunks


def build(fixture: Path, out: Path, client: str = "", estate: EstateWarehouses | None = None,
          if_changed: bool = False, landing: Path | None = None, defer_landing: bool = False,
          sets: tuple[str, ...] = ("golden", "heldout")) -> dict:
    """Build the fixture and return its full estate summary for an unconditional seed. With if_changed, return the
    rebuild reason, digest and warehouse version, skipping a recorded digest or building, certifying and landing
    changed inputs. The caller records the returned digest only when its whole run commits;
    a conditional build never writes it into the summary. With defer_landing, the caller
    certifies the combined estate too. Invalid data is refused before publishing.
    Publishing in place first invalidates the old digest so a failed build can be rerun."""
    from kge.estate import certify
    from kge.pipeline import warehouse_versions

    fixture, out = Path(fixture), Path(out)
    houses = read_schema(fixture)
    pack = pack_folder(fixture)
    questions = question_sets(fixture, sets)
    digest = _fixture_sha256(fixture)
    reader = docreader.reader(client)
    has_converted = any(path.suffix.lower() != ".md" for _, path in document_files(fixture))
    reader_id = reader.identity() if has_converted else None
    summary_path = out / "summary.json"
    previous = json.loads(summary_path.read_text()) if summary_path.is_file() else {}
    why = "no-estate" if not previous else "unchanged" if previous.get("fixture_sha256") == digest else "fixture-changed"
    if why == "unchanged" and {k: v for k, v in previous.items() if k.endswith("_questions")} != questions:
        why = "fixture-changed"
    if has_converted and previous.get("document_reader") != reader_id:
        why = "reader-changed" if previous else "no-estate"
    if if_changed and why == "unchanged":
        if "warehouse_inputs" not in previous:
            previous["warehouse_inputs"] = warehouse_inputs(fixture, houses)
            previous["warehouse_versions"] = {u: int(previous["version"]) for u in previous.get("units", [])
                                               if previous.get("version") is not None}
            summary_path.write_text(json.dumps(previous, indent=2) + "\n")
        return {"rebuilt": False, "why": why, "version": previous.get("version"), "fixture_sha256": digest,
                **({"unread_documents": previous["unread_documents"]} if previous.get("unread_documents") else {})}
    labels = {w: h["label"] for w, h in houses.items()}
    docs = read_documents(fixture, labels, reader, docreader.readings(out, client))
    unit_inputs = warehouse_inputs(fixture, houses)
    estate = estate or open_estate(out / "warehouses", client)
    for unit, house in houses.items():
        refusal = estate.refuses_schema(unit, warehouse_statements(house["tables"]))
        if refusal:
            raise Unreadable(f"{unit}: {refusal}")
    fixture_units = set(houses) | set(previous.get("fixture_units", previous.get("units", [])))
    previous_versions = previous.get("warehouse_versions")
    if not isinstance(previous_versions, dict):
        previous_versions = {u: previous["version"] for u in previous.get("units", [])
                             if previous.get("version") is not None}
    recorded_inputs = previous.get("warehouse_inputs") or {}
    available = {u: estate.versions(u) for u in fixture_units} if estate.builds_apart else {}
    changed = [u for u in houses if not if_changed or recorded_inputs.get(u) != unit_inputs[u]
               or (previous_versions.get(u) not in available[u] if estate.builds_apart else not estate.exists(u))]
    pack_data = yaml.safe_load((pack / "pack.yaml").read_text()) if (pack / "pack.yaml").is_file() else {}
    terms = [value.get("name", "") for value in (pack_data or {}).get("glossary", []) if isinstance(value, dict)]
    preflight_loads(client, *planned_loads(fixture, houses, docs, changed, terms))
    out.mkdir(parents=True, exist_ok=True)
    next_version = max([0, *(v for values in available.values() for v in values)]) + 1 if estate.builds_apart else None
    unit_versions = {u: next_version if u in changed else int(previous_versions[u]) for u in houses} if estate.builds_apart else {}
    version = next(iter(set(unit_versions.values()))) if len(set(unit_versions.values())) == 1 else None
    mailboxes = certify.mailboxes(pack / "pack.yaml") if if_changed else frozenset()
    built, rows = [], {u: previous.get("rows", {}).get(u, {}) for u in houses if u not in changed}
    published = []
    try:
        for w in changed:
            h = houses[w]
            built.append(w)
            rows[w] = build_warehouse(estate, w, h.get("tables") or [], fixture / "data" / w, unit_versions.get(w))
        if built and not estate.builds_apart and summary_path.is_file():
            previous.pop("fixture_sha256", None)
            previous["warehouse_inputs"] = {u: value for u, value in recorded_inputs.items() if u not in built}
            summary_path.write_text(json.dumps(previous, indent=2) + "\n")
        for w in built:
            estate.publish(w, unit_versions.get(w), unit_inputs[w])
            published.append(w)
    except BaseException as exc:
        for w in built:
            try:
                estate.discard(w, unit_versions.get(w))
                if estate.builds_apart and w in published:
                    estate.drop(w, unit_versions[w])
            except Exception as cleanup:
                exc.add_note(f"cleanup of {w} failed: {cleanup}")
        raise
    written = {"documents.json", "summary.json"}
    index = []
    for d in docs:
        folder = out / "docs" / d["shelf"]
        folder.mkdir(parents=True, exist_ok=True)
        (folder / f"{d['doc_id']}.md").write_text(document_text(d, labels))
        written.add(f"docs/{d['shelf']}/{d['doc_id']}.md")
        if d.get("original"):
            original = "originals/" + d["original"]
            destination = out / original
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes((fixture / d["original"]).read_bytes())
            written.add(original)
        index.append({**{k: d[k] for k in ("content_unread", "reading", "original") if k in d}, "doc_id": d["doc_id"], "shelf": d["shelf"], **{k: d[k] for k in META if k not in {"reviewed_at", "planted"}},
                      "reviewed_at": d["reviewed_at"], "planted": d["planted"] or [], "path": f"docs/{d['shelf']}/{d['doc_id']}.md"})
    (out / "documents.json").write_text(json.dumps(index, indent=2) + "\n")
    for name in sets:
        if f"{name}_questions" in questions:
            shutil.copyfile(fixture / f"{name}.json", out / f"{name}.json")
            written.add(f"{name}.json")
    tables = [t for h in houses.values() for t in h.get("tables") or []]
    summary = {"seed": None, "generator": "kge/estate/from_files.py", "units": list(houses), "tables": len(tables),
               "columns": sum(len(t["columns"]) for t in tables),
               "documents": {s: sum(d["shelf"] == s for d in docs) for s in SHELVES}, **questions, "rows": rows,
               "fixture_sha256": digest, "version": version,
               "warehouse_versions": unit_versions, "warehouse_inputs": unit_inputs,
               "fixture_units": sorted(fixture_units)}
    if has_converted:
        summary["document_reader"] = reader_id
    unread = [{"path": d["original"], "evidence": d["content_unread"]} for d in docs if d.get("content_unread")]
    if unread:
        summary["unread_documents"] = unread
    if if_changed and not defer_landing:
        versions = warehouse_versions.live_versions(pack, build=out)
        versions = {u: v for u, v in versions.items() if u not in fixture_units}
        versions.update(unit_versions)
        certify.land(out, client, landing, role_mailboxes=mailboxes, onboarded=versions, estate=estate,
                     excluded=fixture_units - set(houses))
    if if_changed or defer_landing:
        summary.pop("fixture_sha256")
    summary_path.write_text(json.dumps(summary, indent=2) + "\n")
    units = pack / "units"
    if units.is_dir():
        written.update(f"{kind}_{unit.name}_v1.json" for unit in units.iterdir() if unit.is_dir()
                       for kind in ("golden", "team", "heldout"))
    for name in leftover_files(out, written):
        (out / name).unlink()
    for path in sorted((out / "docs").rglob("*"), reverse=True):
        if path.is_dir() and not any(path.iterdir()):
            path.rmdir()
    if (out / "docs").is_dir() and not any((out / "docs").iterdir()):
        (out / "docs").rmdir()
    if if_changed:
        return {"rebuilt": True, "why": why, "version": version, "fixture_sha256": digest,
                **({"unread_documents": unread} if unread else {})}
    return summary


def main(argv: list[str] | None = None) -> int:
    from kge.pipeline.profile import ProfileError, resolve
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--profile", help="whose estate to build (default: PROFILE, else the registry's default)")
    ap.add_argument("--fixture", help="the fixture to build from, instead of the profile's client's (<clients>/<client>/fixture/)")
    ap.add_argument("--out", help="the folder to build the estate in, instead of the profile's")
    ap.add_argument("--golden-only", action="store_true", help="write the question sets alone: the golden set, and the held-out set if any")
    args = ap.parse_args(argv)
    try:
        prof = None if (args.fixture and args.out) else resolve(args.profile)
    except ProfileError as exc:
        print(f"from_files: {exc}", file=sys.stderr)
        return 2
    pack = prof.clients / prof.client if prof is not None else pack_folder(Path(args.fixture))
    declared = read_declared(pack)
    fixture = Path(args.fixture) if args.fixture else pack / (declared.fixture if declared and declared.fixture else FIXTURE)
    out = Path(args.out) if args.out else prof.estate()
    sets = declared.sets if declared is not None else ("golden", "heldout")
    try:
        if args.golden_only:
            print(json.dumps(write_golden(fixture, out, sets)))
            return 0
        summary = build(fixture, out, prof.client if prof is not None else "", sets=sets)
    except (Unreadable, WarehouseRefused) as exc:
        print(f"from_files: {exc}. Nothing was written.", file=sys.stderr)
        return 2
    print(json.dumps({k: v for k, v in summary.items() if k not in {"rows", "fixture_sha256", "version", "fixture_units"}}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
