"""The lock's rows and storage come from the warehouse a source is compiled against: an onboarded unit's live version
(its draft's), never <warehouses>/<unit>.duckdb, which an onboarded unit does not have."""
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


class Session:
    def describe(self, table):
        return {"rows": 3}

    def storage(self, table):
        return 12

    def close(self):
        pass


class Estate:
    def __init__(self):
        self.opened = []

    def open(self, unit, version=None, write=False):
        self.opened.append((unit, version))
        return Session()


def test_an_onboarded_unit_is_read_at_its_live_version_and_an_established_one_at_its_one_warehouse():
    estate = Estate()
    frozen = lock.freeze(Release(), ["retail", "leasing"], estate, {"leasing": 2})
    assert estate.opened == [("retail", None), ("leasing", 2)]
    assert frozen["sources"]["leasing"]["tables"][0]["rows"] == 3
    assert frozen["sources"]["leasing"]["tables"][0]["storage"] == 12
