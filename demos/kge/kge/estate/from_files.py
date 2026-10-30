"""Build a client's estate from files: its warehouses, its documents and its question sets, as a fixture holds them.

    python3 -m kge.estate.from_files [--profile <id>] [--fixture <dir>] [--out <dir>] [--golden-only]

A client whose folder holds a fixture, <clients>/<client>/fixture/, has its
estate built from it, where the demo's own client has its estate generated
(kge/estate/generate.py). The estate is the profile's (kge/pipeline/profile.py:
--profile, else PROFILE, else the registry's default): the folder its
warehouses are in (Profile.estate); --fixture and --out name others. The
fixture holds data, never code:

  schema.yaml            each warehouse, a source's: its label and its tables, each with its columns (name, type and
                         description, in order), its primary key and its foreign keys
  data/<warehouse>/<table>.csv
                         the table's rows, a header naming its columns; an empty field is NULL
  docs/<shelf>/<id>.md   a document on the certified or the uncertified shelf: front matter (title, kind, unit, owner,
                         version, published_at, reviewed_at, superseded_by, and optional certification) between ---
                         lines, then its text
  golden.json            the golden set: its items (id, kind, question, expect), as kge/eval/sets.py reads a set
  heldout.json           the held-out set, if it holds one: paraphrases of golden questions (each with its
                         paraphrase_of), read as the golden set is

It writes, into the estate, what kge/estate/generate.py writes for the demo's
client, so certification, the lock and the graph read the one as the other:

  warehouses <warehouse>          each table with its keys and its columns' descriptions, then its rows, through
                                  the estate's warehouse port (the factory's open_estate): a DuckDB file in
                                  warehouses/, or with WAREHOUSE_BACKEND=bigquery the client's BigQuery dataset
  docs/<shelf>/<id>.md, documents.json
                                  each document with its title and metadata table at its head, as the generator
                                  writes one, and the index of them
  golden.json, heldout.json       the golden set, and the held-out set where the fixture holds one, as it holds them
  summary.json                    what was built, and from which fixture

With --golden-only it writes the question sets alone. The same fixture gives the
same estate: the warehouses' catalogues, the documents and the sets, byte for
byte, so certification lands the same release. A fixture it cannot read is an
exit 2, naming what is wrong, and nothing is written.

Names no client: the fixture is the client's.
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import shutil
import sys
from pathlib import Path

import yaml

from kge.adapters.warehouse import open_estate
from kge.ports import EstateWarehouses, WarehouseRefused

FIXTURE = "fixture"
SHELVES = ("certified", "uncertified")
META = ("title", "kind", "unit", "owner", "version", "published_at", "reviewed_at", "superseded_by", "certification")
NAME = re.compile(r"[a-z][a-z0-9_]*")
PHYSICAL = re.compile(r"[A-Za-z][A-Za-z0-9_]*")
FRONT = re.compile(r"\A---\n(.*?)\n---\n", re.S)


class Unreadable(ValueError):
    """A fixture this cannot build an estate from, and why."""


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


def build_warehouse(estate: EstateWarehouses, unit: str, tables: list[dict], data: Path) -> dict[str, int]:
    """One warehouse staged (published by the caller once every one is built): each table, its keys and descriptions,
    then its rows from <data>/<table>.csv. Rows by table."""
    counts = {}
    con = estate.stage(unit, warehouse_statements(tables))
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


def read_documents(fixture: Path, labels: dict[str, str]) -> list[dict]:
    """Each document on each shelf, its front matter checked: the unit, if any, a warehouse the schema names."""
    out = []
    for shelf in SHELVES:
        for path in sorted((fixture / "docs" / shelf).glob("*.md")):
            m = FRONT.match(path.read_text())
            if not m:
                raise Unreadable(f"{path}: a document opens with its front matter, between --- lines")
            meta = yaml.safe_load(m.group(1)) or {}
            unknown = sorted(set(meta) - set(META))
            if unknown or not meta.get("title") or not meta.get("kind"):
                raise Unreadable(f"{path}: front matter of {', '.join(META)}, a title and a kind among them"
                                 + (f"; not {', '.join(unknown)}" if unknown else ""))
            if meta.get("unit") and meta["unit"] not in labels:
                raise Unreadable(f"{path}: unit {meta['unit']!r} is no warehouse of the schema's")
            out.append({"doc_id": path.stem, "shelf": shelf,
                        **{k: (meta[k] if k == "certification" else str(meta[k])) if meta.get(k) is not None else None for k in META},
                        "body": path.read_text()[m.end():].strip()})
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


def write_golden(fixture: Path, out: Path) -> dict[str, int]:
    """The fixture's question sets into the estate, as it holds them: the golden set, and the held-out set where it holds
    one; how many questions each, by golden_questions and heldout_questions. Both are read before either is written."""
    counts = {"golden_questions": golden_set(fixture)}
    held = heldout_set(fixture)
    out.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(fixture / "golden.json", out / "golden.json")
    if held is not None:
        shutil.copyfile(fixture / "heldout.json", out / "heldout.json")
        counts["heldout_questions"] = held
    return counts


def build(fixture: Path, out: Path, client: str = "", estate: EstateWarehouses | None = None) -> dict:
    """The estate from the fixture into out; what was built. The fixture is read whole, and each warehouse built beside
    its place, before anything of the estate is written: a fixture it cannot read leaves the estate as it was."""
    houses = read_schema(fixture)
    labels = {w: h["label"] for w, h in houses.items()}
    docs = read_documents(fixture, labels)
    golden_set(fixture)
    heldout_set(fixture)
    out.mkdir(parents=True, exist_ok=True)
    estate = estate or open_estate(out / "warehouses", client)
    built, rows = [], {}
    try:
        for w, h in houses.items():
            built.append(w)
            rows[w] = build_warehouse(estate, w, h.get("tables") or [], fixture / "data" / w)
    except BaseException:
        for w in built:
            estate.discard(w)
        raise
    for w in built:
        estate.publish(w)
    if (out / "docs").exists():
        shutil.rmtree(out / "docs")
    index = []
    for d in docs:
        folder = out / "docs" / d["shelf"]
        folder.mkdir(parents=True, exist_ok=True)
        (folder / f"{d['doc_id']}.md").write_text(document_text(d, labels))
        index.append({"doc_id": d["doc_id"], "shelf": d["shelf"], **{k: d[k] for k in META if k != "reviewed_at"},
                      "reviewed_at": d["reviewed_at"], "planted": [], "path": f"docs/{d['shelf']}/{d['doc_id']}.md"})
    (out / "documents.json").write_text(json.dumps(index, indent=2) + "\n")
    questions = write_golden(fixture, out)
    tables = [t for h in houses.values() for t in h.get("tables") or []]
    summary = {"seed": None, "generator": "kge/estate/from_files.py", "units": list(houses), "tables": len(tables),
               "columns": sum(len(t["columns"]) for t in tables),
               "documents": {s: sum(d["shelf"] == s for d in docs) for s in SHELVES}, **questions, "rows": rows}
    (out / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
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
    fixture = Path(args.fixture) if args.fixture else prof.clients / prof.client / FIXTURE
    out = Path(args.out) if args.out else prof.estate()
    try:
        if args.golden_only:
            print(json.dumps(write_golden(fixture, out)))
            return 0
        summary = build(fixture, out, prof.client if prof is not None else "")
    except (Unreadable, WarehouseRefused) as exc:
        print(f"from_files: {exc}. Nothing was written.", file=sys.stderr)
        return 2
    print(json.dumps({k: v for k, v in summary.items() if k != "rows"}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
