"""Pure draft proposals and the pack reader's state rendering."""
from pathlib import Path

import yaml

from kge.compiler import drafts, pack as packs
from kge.adapters.warehouse.duckdb import DuckDBEstate
from kge.estate import ddl

DEMO = Path(__file__).resolve().parents[2]


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
    item = {"id": "t", "kind": "term", "accepted_by": "business_team", "accepted_on": "2026-10-17",
            "certified_by": None, "certified_on": None,
            "pack": {"glossary": [{"id": "t", "name": "T", "domain": "U", "steward": "U Data Steward", "definition": "T"}],
                     "mappings": [{"column": "u.t.x", "term": "t"}]}}
    record = {"format": 1, "unit": "u", "drafted": {"on": "2026-10-17"}, "items": [item]}
    (unit / "draft.yaml").write_text(yaml.safe_dump(record))
    identities = DEMO / "deploy/meridian/identities.yaml"
    got = packs.read(clients, "x", lock=False, identities=identities)
    assert got.data["glossary"][-1]["status"] == "candidate" and got.data["mappings"][-1]["certified"] is False
    record["items"][0].update(certified_by="steward_glossary", certified_on="2026-10-18")
    (unit / "draft.yaml").write_text(yaml.safe_dump(record))
    got = packs.read(clients, "x", lock=False, identities=identities)
    assert got.data["glossary"][-1]["status"] == "certified"
    assert got.data["mappings"][-1]["certified_by"] == "steward_glossary"


def test_hand_edited_draft_cannot_forge_acceptance_or_certification(tmp_path):
    clients = tmp_path / "clients"; client = clients / "x"; client.mkdir(parents=True)
    (client / "pack.yaml").write_text((DEMO / "clients/meridian/pack.yaml").read_text())
    unit = client / "units/u"; unit.mkdir(parents=True)
    item = {"id": "t", "kind": "term", "accepted_by": "nobody_a", "accepted_on": "2026-10-17",
            "certified_by": "nobody_b", "certified_on": "2026-10-18", "pack": {}}
    (unit / "draft.yaml").write_text(yaml.safe_dump({"format": 1, "unit": "u", "drafted": {"on": "2026-10-17"}, "items": [item]}))
    got = packs.read(clients, "x", lock=False, identities=DEMO / "deploy/meridian/identities.yaml")
    assert any("accepted_by 'nobody_a' is not a business-team principal" in str(f) for f in got.findings)
    assert any("certified_by 'nobody_b' is not a steward principal" in str(f) for f in got.findings)
    assert got.drafts[0]["state"] == "drafted"


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
