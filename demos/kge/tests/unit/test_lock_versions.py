"""The lock freezes landed schema, never warehouse figures, including for versioned units; a fixture rebuilt on BigQuery
is live at its new version, whose tables sit beside the old."""
from __future__ import annotations

from kge.compiler import lock


class Release:
    client = "toy"
    manifest = {"as_of": "2025-06-30"}

    def has(self, name):
        return True

    def structured(self, source):
        return {"tables": [{"name": "rental", "primary_key": ["rental_id"], "foreign_keys": [],
                            "columns": [{"name": "rental_id", "type": "INTEGER", "comment": None, "ordinal": 1}]}]}


class Estate:
    def __init__(self):
        self.opened = []

    def open(self, unit, version=None, write=False):
        self.opened.append((unit, version))
        raise AssertionError("lock generation must not open a warehouse")


def test_neither_an_onboarded_nor_an_established_units_warehouse_is_read_for_the_lock():
    estate = Estate()
    frozen = lock.freeze(Release(), ["retail", "leasing"], estate, {"leasing": 2})
    assert estate.opened == []
    assert all(not {"rows", "storage"} & table.keys() for source in frozen["sources"].values() for table in source["tables"])


def test_a_rebuilt_fixture_is_live_at_its_new_version_in_bigquery(tmp_path, monkeypatch):
    from kge.estate import from_files
    from kge.landing.zone import Zone
    from kge.pipeline.warehouse_versions import live_versions
    from tests import bigquery_fake
    from tests.unit.test_from_files import fixture

    monkeypatch.setenv("WAREHOUSE_BACKEND", "bigquery")
    fake = bigquery_fake.install(monkeypatch)
    pack = tmp_path / "clients" / "shop"
    fx = fixture(pack)
    (pack / "pack.yaml").write_text("manifest: {client: shop}\nterminology: {}\n")
    out = tmp_path / "estate"
    from_files.build(fx, out, "shop", if_changed=True)
    csv = fx / "data" / "shop" / "sale.csv"
    csv.write_text(csv.read_text() + "12,1,2025-06-05,4.00\n")
    from_files.build(fx, out, "shop", if_changed=True)
    versions = live_versions(pack, build=out)
    assert versions == {"shop": 2}
    frozen = lock.freeze(Zone(out / "landing", "shop").open(), ["shop"], None, versions)
    assert len(fake.rows("shop_v1_sale")) == 2 and len(fake.rows("shop_v2_sale")) == 3
    assert [t["name"] for t in frozen["sources"]["shop"]["tables"]] == ["item", "sale"]
