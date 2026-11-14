"""Meridian's generator on BigQuery preserves the default fixture's estate content.

The REST stand-in blocks direct DuckDB access on the BigQuery path. Every row,
question set, raw document and landed file is compared with the fixture build.
Generator and fixture summaries have different provenance; a fresh DuckDB
regeneration and its byte-for-byte export are covered by test_sample_fixture.
"""
from collections import Counter
import json
import sys

import pytest

from kge.adapters.warehouse import open_estate
from kge.compiler import pack as packs
from kge.estate import certify, generate
from tests import bigquery_fake
from tests.scratch import BUILD, DEMO

SETS = ("golden", "heldout", "golden_wealth_v1", "sage_v1", "golden_figures_v1", "golden_documents_v1", "heldout_documents_v1")


def _landed(release) -> dict:
    return {p.relative_to(release.path).as_posix(): p.read_bytes() for p in sorted(release.path.rglob("*")) if p.is_file()}


def _rows(estate) -> dict:
    """All tables by column order and row content, preserving duplicate counts."""
    held = {}
    for unit in estate.established():
        con = estate.open(unit)
        try:
            for table in con.tables():
                columns = tuple(c["name"] for c in con.describe(table)["columns"])
                held[f"{unit}.{table}"] = (columns, Counter(con.execute(f"SELECT {', '.join(columns)} FROM {table}").fetchall()))
        finally:
            con.close()
    return held


def test_meridian_s_estate_is_generated_and_certified_in_bigquery_as_the_demo_s_build(tmp_path, monkeypatch):
    if not (BUILD / "summary.json").is_file():
        pytest.fail("no demo build: make graph pipeline first")
    mailboxes = certify.mailboxes(DEMO / "clients/meridian" / packs.PACK)
    local, _, _ = certify.land(BUILD, "meridian", tmp_path / "local-landing", role_mailboxes=mailboxes)
    expected_rows = _rows(open_estate(BUILD / "warehouses", "meridian"))
    assert sum(rows.total() for _, rows in expected_rows.values()) == 70552
    fake = bigquery_fake.install(monkeypatch)
    monkeypatch.setenv("WAREHOUSE_BACKEND", "bigquery")
    opened = bigquery_fake.block_duckdb(monkeypatch)
    out = tmp_path / "estate"
    monkeypatch.setattr(sys, "argv", ["generate", "--profile", "meridian", "--out", str(out)])
    assert generate.main() == 0
    assert _rows(open_estate(out / "warehouses", "meridian")) == expected_rows
    generated_summary = json.loads((out / "summary.json").read_text())
    fixture_summary = json.loads((BUILD / "summary.json").read_text())
    assert generated_summary["seed"] == 7
    for key in ("rows", "documents", "golden_questions", "heldout_questions"):
        assert generated_summary[key] == fixture_summary[key], key
    for name, key in (("golden_wealth_v1", "golden_wealth_questions"), ("sage_v1", "sage_questions")):
        assert generated_summary[key] == fixture_summary[f"{name}_questions"]
    for name in SETS:
        assert (out / f"{name}.json").read_bytes() == (BUILD / f"{name}.json").read_bytes(), name
    docs = [json.loads((folder / "documents.json").read_text()) for folder in (out, BUILD)]
    assert [{k: v for k, v in doc.items() if v is not None} for doc in docs[0]] == [
        {k: v for k, v in doc.items() if v is not None} for doc in docs[1]]
    for doc in docs[0]:
        assert (out / doc["path"]).read_bytes() == (BUILD / doc["path"]).read_bytes(), doc["doc_id"]
    cloud, _, _ = certify.land(out, "meridian", tmp_path / "cloud-landing", role_mailboxes=mailboxes)
    assert _landed(cloud) == _landed(local) and cloud.id == local.id
    assert opened == [] and not [p for p in tmp_path.rglob("*") if ".duckdb" in p.name]
    assert sorted(u for u, v, *_ in fake.rows("_kge_catalogue")) == ["corporate", "retail", "sme", "wealth", "xref"]
    updates = [s for s in fake.statements if s.startswith("UPDATE")]
    assert len(updates) == 1 and updates[0].startswith("UPDATE `retail_transaction` AS t SET"), \
        f"the transfer legs are written by one DML statement in BigQuery, after the rows are loaded, not {len(updates)}"
