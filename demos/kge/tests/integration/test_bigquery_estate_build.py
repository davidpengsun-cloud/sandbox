"""Meridian's generated estate on the warehouses' BigQuery path (issue #95, item 1.2): kge/estate/generate.py run with
WAREHOUSE_BACKEND=bigquery, against the stand-in for BigQuery (tests/bigquery_fake.py) and with DuckDB taken away,
writes its warehouses as tables of the client's dataset and nothing else, and its summary, golden, held-out and
golden_wealth sets are the demo's build's byte for byte; certification then lands from BigQuery the release the demo's
build lands from DuckDB. Integration lane: it is held to the demo's own build (make graph pipeline)."""
import sys

import pytest

from kge.compiler import pack as packs
from kge.estate import certify, generate
from tests import bigquery_fake
from tests.scratch import THE_BUILD

DEMO = THE_BUILD.parent
FILES = ("summary.json", "golden.json", "heldout.json", "golden_wealth_v1.json", "documents.json")


def _landed(release) -> dict:
    return {p.relative_to(release.path).as_posix(): p.read_bytes() for p in sorted(release.path.rglob("*")) if p.is_file()}


def test_meridian_s_estate_is_generated_and_certified_in_bigquery_as_the_demo_s_build(tmp_path, monkeypatch):
    if not (THE_BUILD / "summary.json").is_file():
        pytest.fail("no demo build: make graph pipeline first")
    mailboxes = certify.mailboxes(DEMO / "clients/meridian" / packs.PACK)
    local, _, _ = certify.land(THE_BUILD, "meridian", tmp_path / "local-landing", role_mailboxes=mailboxes)
    fake = bigquery_fake.install(monkeypatch)
    monkeypatch.setenv("WAREHOUSE_BACKEND", "bigquery")
    opened = bigquery_fake.block_duckdb(monkeypatch)
    out = tmp_path / "estate"
    monkeypatch.setattr(sys, "argv", ["generate", "--profile", "meridian", "--out", str(out)])
    assert generate.main() == 0
    for name in FILES:
        assert (out / name).read_bytes() == (THE_BUILD / name).read_bytes(), name
    cloud, _, _ = certify.land(out, "meridian", tmp_path / "cloud-landing", role_mailboxes=mailboxes)
    assert _landed(cloud) == _landed(local) and cloud.id == local.id
    assert opened == [] and not [p for p in tmp_path.rglob("*") if ".duckdb" in p.name]
    assert sorted(u for u, v, *_ in fake.rows("_kge_catalogue")) == ["corporate", "retail", "sme", "wealth", "xref"]
    updates = [s for s in fake.statements if s.startswith("UPDATE")]
    assert len(updates) == 1 and updates[0].startswith("UPDATE `retail_transaction` AS t SET"), \
        f"the transfer legs are written by one DML statement in BigQuery, after the rows are loaded, not {len(updates)}"
