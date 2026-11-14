"""Pure draft proposals and the pack reader's state rendering."""
import datetime as dt
from pathlib import Path

import yaml
import pytest

from kge.compiler import drafts, pack as packs
from kge.adapters.warehouse.duckdb import DuckDBEstate
from kge.estate import ddl

DEMO = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize('op,value,expected', [
    ('=', 'settled', ['settled-booking Turnover']),
    ('IN', ['settled', 'pending'], ['settled or pending booking Turnover', 'settled and pending booking Turnover']),
    ('!=', 'settled', []),
    ('NOT IN', ['settled', 'pending'], []),
    ('IS NULL', None, []),
    ('>', 10, []),
    ('=', 'not-a-simple-label', []),
])
def test_recipe_population_alias_names_only_its_declared_condition(op, value, expected):
    recipe = {'table': 'booking', 'column': 'status', 'op': op, 'value': value}
    assert drafts._qualified_aliases([recipe], 'Turnover') == expected


def test_union_population_aliases_keep_the_non_status_field_meaning():
    recipe = {'table': 'booking', 'column': 'sales_channel', 'op': 'IN', 'value': ['direct', 'broker']}
    assert drafts._qualified_aliases([recipe], 'Turnover') == [
        'sales channel direct or broker booking Turnover',
        'sales channel direct and broker booking Turnover',
    ]
    assert drafts._qualified_aliases([recipe, {**recipe, 'column': 'status'}], 'Turnover') == []


def test_leasing_proposes_source_term_and_question(tmp_path):
    base = yaml.safe_load((DEMO / "clients/meridian/pack.yaml").read_text())
    model = ddl.parse((DEMO / "examples/leasing/schema.sql").read_text(), "leasing")
    estate = DuckDBEstate(tmp_path)
    proposed = drafts.propose(base, ddl.preview(model, False, estate), "Asset Finance", estate.connection("leasing"),
                              {"lease-rentals": {"figure": "1.00"}})
    assert [(x["id"], x["kind"]) for x in proposed["items"]] == [
        ("source", "source"), ("lease-rentals", "term"), ("LEASING-D01", "question")]
    source = proposed["items"][0]["pack"]
    assert source["sources"][0]["entity_key"] == "lessee_id"
    assert list(source["sources"][0]["connection"].items()) == [("dialect", "duckdb"), ("ref", "warehouses/leasing.duckdb")]
    assert len(source["no_join"]) == 4
    assert source["period_bindings"] == [{"table": "leasing.rental", "column": "billed_on", "kind": "event_date", "calendar": "calendar"}]


def test_pack_reader_renders_uncertified_and_certified_markers(tmp_path):
    clients = tmp_path / "clients"; client = clients / "x"; client.mkdir(parents=True)
    base = yaml.safe_load((DEMO / "clients/meridian/pack.yaml").read_text())
    (client / "pack.yaml").write_text(yaml.safe_dump(base, sort_keys=False))
    unit = client / "units/u"; unit.mkdir(parents=True)
    item = {"id": "t", "kind": "term", "accepted_as": "business_team", "accepted_on": "2026-10-17",
            "certified_as": None, "certified_on": None,
            "pack": {"glossary": [{"id": "t", "name": "T", "domain": "U", "steward": "U Data Steward", "definition": "T"}],
                     "mappings": [{"column": "u.t.x", "term": "t"}]}}
    record = {"format": 1, "unit": "u", "drafted": {"on": "2026-10-17"}, "items": [item]}
    (unit / "draft.yaml").write_text(yaml.safe_dump(record))
    got = packs.read(clients, "x", lock=False)
    assert got.data["glossary"][-1]["status"] == "candidate" and got.data["mappings"][-1]["certified"] is False
    record["items"][0].update(certified_as="steward_glossary", certified_on="2026-10-18")
    (unit / "draft.yaml").write_text(yaml.safe_dump(record))
    got = packs.read(clients, "x", lock=False)
    assert got.data["glossary"][-1]["status"] == "certified"
    assert got.data["mappings"][-1]["certified_by"] == "steward_glossary"


def test_old_draft_people_preserve_review_without_findings(tmp_path):
    clients = tmp_path / "clients"; client = clients / "x"; client.mkdir(parents=True)
    (client / "pack.yaml").write_text((DEMO / "clients/meridian/pack.yaml").read_text())
    unit = client / "units/u"; unit.mkdir(parents=True)
    item = {"id": "t", "kind": "term", "accepted_by": "nobody_a", "accepted_on": "2026-10-17",
            "certified_by": "nobody_b", "certified_on": "2026-10-18", "pack": {}}
    (unit / "draft.yaml").write_text(yaml.safe_dump({"format": 1, "unit": "u", "drafted": {"on": "2026-10-17"}, "items": [item]}))
    got = packs.read(clients, "x", lock=False)
    assert got.findings == []
    assert got.drafts[0]["state"] == "certified"


def test_recipe_draft_sections_are_merged_only_as_validated_lists(tmp_path):
    clients = tmp_path / "clients"; client = clients / "x"; client.mkdir(parents=True)
    (client / "pack.yaml").write_text((DEMO / "clients/meridian/pack.yaml").read_text())
    unit = client / "units/u"; unit.mkdir(parents=True)
    item = {"id":"t","kind":"term","pack":{"filters":{"id":"smuggled"},"contexts":["not-an-object"]}}
    (unit / "draft.yaml").write_text(yaml.safe_dump({"format":1,"unit":"u","items":[item]}))
    got = packs.read(clients, "x", lock=False)
    assert any("draft section filters is a list" in str(f) for f in got.findings)
    assert any("contexts[0]" in str(f) and "is an object" in str(f) for f in got.findings)
    assert not any(x.get("id") == "smuggled" for x in got.data.get("filters", []))


def test_people_in_old_draft_fields_never_enter_the_hash_or_certification(tmp_path):
    clients = tmp_path / "clients"
    client = clients / "x"
    client.mkdir(parents=True)
    (client / "pack.yaml").write_bytes((DEMO / "clients/meridian/pack.yaml").read_bytes())
    unit = client / "units/u"
    unit.mkdir(parents=True)
    item = {"id": "t", "kind": "term", "accepted_as": "business_team", "accepted_on": "2026-10-17",
            "certified_as": "steward_glossary", "certified_on": "2026-10-18",
            "accepted_by": "former@example.com", "certified_by": "removed@example.com",
            "pack": {"glossary": [{"id": "t", "name": "T", "domain": "U", "steward": "Data Steward", "definition": "T"}],
                     "mappings": [{"column": "u.t.x", "term": "t", "certified_by": "removed@example.com"}]}}
    path = unit / "draft.yaml"
    draft = {"format": 1, "unit": "u", "items": [item]}
    path.write_text(yaml.safe_dump(draft))
    before = packs.read(clients, "x", lock=False)
    assert before.findings == []
    assert before.data["mappings"][-1]["certified_by"] == "steward_glossary"
    assert "@example.com" not in packs.canonical([before.data, before.drafts])
    item["accepted_on"] = dt.date(2026, 10, 17)
    item["certified_on"] = dt.date(2026, 10, 18)
    item["accepted_by"], item["certified_by"] = "another@example.com", "different@example.com"
    path.write_text(yaml.safe_dump(draft))
    assert packs.read(clients, "x", lock=False).hash == before.hash
