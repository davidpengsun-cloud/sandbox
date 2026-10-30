"""A client's estate built from files: kge/estate/from_files.py (#39 item 11).

    .venv/bin/python -m pytest tests/unit/test_from_files.py -q

A client whose folder holds a fixture, <clients>/<client>/fixture/, has its
estate built from it, with no Python of the client's: a schema file naming its
warehouses, their tables, keys and column descriptions; a CSV of rows a table;
Markdown documents with front matter, on the certified and the uncertified
shelf; and its golden set. What it writes is what kge/estate/generate.py writes for
the demo's client: the warehouses, each catalogue as the schema says; the
documents with their title and metadata table at their head, and their index;
the golden set; and a summary. So certification lands it, the lock freezes it
and the graph loads it as it does the demo's. The same fixture gives the same
estate, and so the same landed release; a fixture it cannot read writes
nothing. make estate, make golden and the build that make demo starts with run
it for such a client, and kge/estate/generate.py for the demo's.

Builds a small fixture of its own in a temporary folder; reads the demo's
registry, never its build.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import duckdb
import pytest
import yaml

DEMO = Path(__file__).resolve().parents[2]
SCHEMA = {"warehouses": {"shop": {"label": "Corner Shop", "tables": [
    {"name": "item", "columns": [{"name": "item_id", "type": "INTEGER", "description": "The item's number"},
                                 {"name": "label", "type": "VARCHAR", "description": "What the shelf calls it"}],
     "primary_key": ["item_id"]},
    {"name": "sale", "columns": [{"name": "sale_id", "type": "INTEGER", "description": "The sale's number"},
                                 {"name": "item_id", "type": "INTEGER", "description": "The item sold"},
                                 {"name": "sold_on", "type": "DATE", "description": "The day of the sale"},
                                 {"name": "amount", "type": "DECIMAL(9,2)", "description": "What it sold for"}],
     "primary_key": ["sale_id"], "foreign_keys": [{"columns": ["item_id"], "ref_table": "item", "ref_columns": ["item_id"]}]}]}}}
DOC = """---
title: "Metric definition: Takings"
kind: metric_definition
unit: shop
owner: Shop Data Steward
version: "1.0"
published_at: "2025-01-02"
reviewed_at: "2025-06-01"
certification:
  ai_ready: "yes"
  tier: silver
  policy_id: shop-ai
  policy_version: "3.0"
  effective_from: "2025-01-02"
---
**Definition.** Takings are what the shop sold in a period.
"""
NOTE = """---
title: A note
kind: email
---
From: Till
To: Office

Waste: what the shop throws away.
"""
GOLDEN = [{"id": "S01", "kind": "within", "question": "Takings for June 2025", "expect": {"figure": "3.5"}}]


def fixture(root: Path) -> Path:
    f = root / "fixture"
    (f / "data" / "shop").mkdir(parents=True)
    (f / "schema.yaml").write_text(yaml.safe_dump(SCHEMA, sort_keys=False))
    (f / "data" / "shop" / "item.csv").write_text("item_id,label\n1,Tea\n2,\n")
    (f / "data" / "shop" / "sale.csv").write_text("sale_id,item_id,sold_on,amount\n10,1,2025-06-03,1.50\n11,1,2025-06-04,2.00\n")
    for shelf, name, text in (("certified", "MD-TAKINGS", DOC), ("uncertified", "EM-01", NOTE)):
        (f / "docs" / shelf).mkdir(parents=True, exist_ok=True)
        (f / "docs" / shelf / f"{name}.md").write_text(text)
    (f / "golden.json").write_text(json.dumps(GOLDEN))
    return f


def build(fx: Path, out: Path, *more) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-m", "kge.estate.from_files", "--fixture", str(fx), "--out", str(out), *more],
                          capture_output=True, text=True, cwd=DEMO)


def test_it_builds_the_warehouses_the_schema_names_with_their_rows(tmp_path):
    from kge.estate import certify
    out = tmp_path / "estate"
    r = build(fixture(tmp_path), out)
    assert r.returncode == 0, r.stderr
    from kge.adapters.warehouse.duckdb import DuckDBEstate
    cat = certify.catalogue_of(DuckDBEstate(out / "warehouses"), "shop")
    assert [t["name"] for t in cat["tables"]] == ["item", "sale"]
    sale = cat["tables"][1]
    assert [(c["name"], c["type"], c["comment"]) for c in sale["columns"]] == [
        ("sale_id", "INTEGER", "The sale's number"), ("item_id", "INTEGER", "The item sold"), ("sold_on", "DATE", "The day of the sale"),
        ("amount", "DECIMAL(9,2)", "What it sold for")]
    assert sale["primary_key"] == ["sale_id"] and sale["foreign_keys"] == [{"columns": ["item_id"], "ref_table": "item", "ref_columns": ["item_id"]}]
    con = duckdb.connect(str(out / "warehouses" / "shop.duckdb"), read_only=True)
    try:
        assert con.execute("SELECT item_id, label FROM item ORDER BY 1").fetchall() == [(1, "Tea"), (2, None)], "an empty field is NULL"
        assert float(con.execute("SELECT sum(amount) FROM sale").fetchone()[0]) == 3.5
    finally:
        con.close()


def test_it_writes_the_documents_as_the_generator_does_and_their_index(tmp_path):
    out = tmp_path / "estate"
    assert build(fixture(tmp_path), out).returncode == 0
    index = json.loads((out / "documents.json").read_text())
    assert [(d["doc_id"], d["shelf"], d["kind"], d["unit"], d["owner"], d["version"], d["published_at"], d["reviewed_at"], d["path"])
            for d in index] == [("MD-TAKINGS", "certified", "metric_definition", "shop", "Shop Data Steward", "1.0", "2025-01-02",
                                 "2025-06-01", "docs/certified/MD-TAKINGS.md"),
                                ("EM-01", "uncertified", "email", None, None, None, None, None, "docs/uncertified/EM-01.md")]
    assert index[0]["certification"] == {"ai_ready": "yes", "tier": "silver", "policy_id": "shop-ai",
                                          "policy_version": "3.0", "effective_from": "2025-01-02"}
    assert index[1]["certification"] is None
    text = (out / "docs" / "certified" / "MD-TAKINGS.md").read_text()
    assert text.startswith("# Metric definition: Takings\n\n| | |\n|---|---|\n| Shelf | certified |\n| Document | MD-TAKINGS |\n")
    assert "| Business unit | Corner Shop |" in text and "---" not in text.split("|---|---|", 1)[1], "no front matter lands"
    from kge.pipeline.chunking import document_chunks
    assert document_chunks("Metric definition: Takings", text)[0].endswith("Takings are what the shop sold in a period.")
    assert json.loads((out / "golden.json").read_text()) == GOLDEN
    summary = json.loads((out / "summary.json").read_text())
    assert summary["generator"] == "kge/estate/from_files.py" and summary["units"] == ["shop"] and summary["golden_questions"] == 1
    assert summary["rows"] == {"shop": {"item": 2, "sale": 2}}


def test_the_same_fixture_lands_the_same_release(tmp_path):
    fx, clients = fixture(tmp_path), tmp_path / "clients"

    def certify(out: Path) -> subprocess.CompletedProcess:
        return subprocess.run([sys.executable, "-m", "kge.estate.certify", "--build", str(out), "--client", "shop", "--clients",
                               str(clients)], capture_output=True, text=True, cwd=DEMO)

    assert build(fx, tmp_path / "estate1").returncode == 0
    r = certify(tmp_path / "estate1")
    assert r.returncode == 2 and "pack" in r.stderr, "certification reads the client's mailboxes from its pack, which it has not"
    (clients / "shop").mkdir(parents=True)
    (clients / "shop" / "pack.yaml").write_text("manifest: {client: shop}\nterminology: {functional_mailboxes: [till]}\n")
    assert build(fx, tmp_path / "estate2").returncode == 0
    ids = []
    for out in (tmp_path / "estate1", tmp_path / "estate2"):
        r = certify(out)
        assert r.returncode == 0, r.stderr
        ids.append(json.loads(r.stdout)["release"])
        release = __import__("kge.landing.zone", fromlist=["Zone"]).Zone(out / "landing", "shop").open()
        assert release.manifest["certification_paths"] == {"MD-TAKINGS": "upstream", "EM-01": "stand-in"}
        grades = {row["asset_id"]: row for row in release.certification()}
        assert (grades["MD-TAKINGS"]["tier"], grades["MD-TAKINGS"]["version"]) == ("silver", "3.0")
        assert grades["EM-01"]["evidence"].endswith("(uncertified shelf)")
    assert ids[0] == ids[1]


@pytest.mark.parametrize("break_it, said", [
    (lambda f: (f / "data" / "shop" / "sale.csv").write_text("sale_id,item,sold_on,amount\n"), "the header must name sale's columns"),
    (lambda f: (f / "schema.yaml").write_text("warehouses: {shop: {label: Shop, tables: [{name: item, columns: [{name: x, type: INTEGER}], "
                                             "primary_key: [x]}]}}\n"), "a column is a name, a type and a description"),
    (lambda f: (f / "docs" / "certified" / "MD-TAKINGS.md").write_text("no front matter\n"), "opens with its front matter"),
    (lambda f: (f / "golden.json").unlink(), "holds no golden.json"),
    (lambda f: (f / "data" / "shop" / "sale.csv").write_text("sale_id,item_id,sold_on,amount\n10,1,not a date,1.50\n"), "date"),
], ids=["csv-header", "column-without-description", "document-without-front-matter", "no-golden-set", "a-row-that-does-not-load"])
def test_a_fixture_it_cannot_read_writes_nothing(tmp_path, break_it, said):
    fx = fixture(tmp_path)
    out = tmp_path / "estate"
    assert build(fx, out).returncode == 0
    before = {p.relative_to(out).as_posix(): p.read_bytes() for p in sorted(out.rglob("*")) if p.is_file()}
    break_it(fx)
    r = build(fx, out)
    assert r.returncode == 2 and said in r.stderr and "Nothing was written" in r.stderr, r.stderr
    assert {p.relative_to(out).as_posix(): p.read_bytes() for p in sorted(out.rglob("*")) if p.is_file()} == before


def test_golden_only_writes_the_golden_set_alone(tmp_path):
    out = tmp_path / "estate"
    r = build(fixture(tmp_path), out, "--golden-only")
    assert r.returncode == 0 and json.loads(r.stdout) == {"golden_questions": 1}
    assert sorted(p.name for p in out.iterdir()) == ["golden.json"]


def test_a_held_out_set_the_fixture_holds_is_copied_with_the_golden_set(tmp_path):
    """A fixture's held-out set, heldout.json, paraphrases its golden questions and is copied into the estate as the
    golden set is, by the full build and by --golden-only, so step 7's V03 has a set to report; a fixture without one
    writes none."""
    from kge.eval import sets
    fx = fixture(tmp_path)
    held = [{"id": "SH01", "kind": "within", "question": "What did the shop take in June 2025?", "paraphrase_of": "S01",
             "expect": {"figure": "3.5"}}]
    (fx / "heldout.json").write_text(json.dumps(held))
    out = tmp_path / "estate"
    r = build(fx, out)
    assert r.returncode == 0, r.stderr
    assert (out / "heldout.json").read_bytes() == (fx / "heldout.json").read_bytes() and sets.read(out / "heldout.json")["version"] == "v1"
    assert json.loads((out / "summary.json").read_text())["heldout_questions"] == 1
    r = build(fx, tmp_path / "golden-only", "--golden-only")
    assert r.returncode == 0 and json.loads(r.stdout) == {"golden_questions": 1, "heldout_questions": 1}, r.stdout + r.stderr
    assert sorted(p.name for p in (tmp_path / "golden-only").iterdir()) == ["golden.json", "heldout.json"]
    (fx / "heldout.json").write_text('{"items": "not a list"}')
    before = {p.relative_to(out).as_posix(): p.read_bytes() for p in sorted(out.rglob("*")) if p.is_file()}
    r = build(fx, out)
    assert r.returncode == 2 and "heldout.json" in r.stderr and "Nothing was written" in r.stderr, r.stderr
    assert {p.relative_to(out).as_posix(): p.read_bytes() for p in sorted(out.rglob("*")) if p.is_file()} == before
    (fx / "heldout.json").unlink()
    r = build(fx, tmp_path / "none")
    assert r.returncode == 0 and not (tmp_path / "none" / "heldout.json").exists()
    assert "heldout_questions" not in json.loads((tmp_path / "none" / "summary.json").read_text())


def test_it_names_no_client():
    from tests.unit.test_client_nouns import NOUN
    assert NOUN.findall((DEMO / "kge" / "estate" / "from_files.py").read_text()) == []


def make(*args, env: dict | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(["make", "-n", *args], capture_output=True, text=True, cwd=DEMO, env={**os.environ, **(env or {})})


def test_make_builds_a_fixture_client_s_estate_from_its_files_and_the_demo_s_by_generating_it(tmp_path, monkeypatch):
    """make estate, make golden and the estate make demo starts with: kge/estate/from_files.py for a client whose folder holds
    a fixture, kge/estate/generate.py for one whose does not (the demo's own)."""
    fx = fixture(tmp_path / "clients" / "shop")
    assert fx == tmp_path / "clients" / "shop" / "fixture"
    (tmp_path / "deploy").mkdir()
    (tmp_path / "deploy" / "identities.yaml").write_text("client: shop\nprincipals: []\n")
    registry = tmp_path / "profiles.yaml"
    registry.write_text(yaml.safe_dump({"default": "shop", "profiles": {"shop": {
        "client": "shop", "clients": "clients", "artifacts": "build/artifacts", "store": "build/stores", "landing": "build/landing",
        "warehouses": "build/shop/warehouses", "identities": "deploy/identities.yaml"}}}))
    r = make("golden", f"PY={sys.executable}", env={"PROFILES": str(registry), "PROFILE": ""})
    assert r.returncode == 0, r.stderr
    assert "kge.estate.from_files --golden-only" in r.stdout and "kge.estate.generate" not in r.stdout, r.stdout
    from tests.unit.test_cli import plan
    for command in ("estate", "certify"):
        code, ran = plan(monkeypatch, command, PROFILES=str(registry))
        assert code == 0 and ["kge.estate.from_files"] in ran and ["kge.estate.generate"] not in ran, (command, ran)
    code, ran = plan(monkeypatch, "estate", PROFILES="")
    assert ran[0] == ["kge.estate.generate"], ran
