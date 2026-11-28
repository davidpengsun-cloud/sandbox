"""Installed review state survives the person-to-role format change."""
import copy
import json
from dataclasses import replace

import pytest
import yaml

from kge import packstore
from kge.adapters import appstate
from kge.adapters.appstate import bigquery
from tests import bigquery_fake
from kge.compiler import pack
from kge.estate.onboard.common import _read_record
from kge.graph import history
from kge.pipeline import profile, review_upgrade, unit_history


@pytest.fixture(params=["files", "bigquery"])
def installed(tmp_path, monkeypatch, request):
    monkeypatch.setenv("APP_STATE_BACKEND", request.param)
    monkeypatch.setenv("BIGQUERY_PROJECT", bigquery_fake.PROJECT)
    monkeypatch.delenv("STORE_URL", raising=False)
    if request.param == "bigquery":
        fake = bigquery_fake.FakeBigQuery()
        monkeypatch.setattr(bigquery, "_ENSURED", set())
        monkeypatch.setattr(bigquery.BigQueryState, "_post", lambda self, body: fake.state_post(body))
    prof = replace(profile.resolve("meridian"), clients=tmp_path / "clients", store=tmp_path / "store",
                   registry=tmp_path / "deploy/profiles.yaml", artifacts=tmp_path / "artifacts",
                   landing=tmp_path / "landing", warehouses=tmp_path / "warehouses")
    store = packstore.for_profile(prof)
    roles = [{"id": "business", "processes": ["querying", "accepting"], "reads": ["sales"]},
             {"id": "steward", "processes": ["certification"], "reads": ["sales"]}]
    store.write("pack.yaml", yaml.safe_dump({"manifest": {"client": prof.client}, "access": {"roles": roles}}).encode())
    seed = prof.registry.parent / prof.client / "identities.yaml"
    seed.parent.mkdir(parents=True)
    seed.write_text(yaml.safe_dump({"principals": [{"principal": "old_business", "role": "business"},
                                                  {"principal": "old_steward", "role": "steward"}]}))
    state = appstate.access_state(prof)
    for old, role in (("old_business", "business"), ("old_steward", "steward")):
        state.access_put({"username": f"{role}.client@example.com", "name": old, "roles": [role]}, by="migration", via="migration")
    monkeypatch.setattr(profile, "read", lambda path: ("meridian", {"meridian": prof}))
    return prof, store


def legacy_items():
    review = {"accepted_by": "old_business", "certified_by": "old_steward", "accepted_on": "2026-01-01", "certified_on": "2026-01-02"}
    return [{"id": "sales-revenue", "kind": "term", "pack": {"glossary": [{"id": "sales-revenue"}]}, **review},
            {"id": "sales-mapping", "kind": "mapping", "pack": {"ontology_mappings": [{"term": "sales-revenue", "concept": "Revenue"}]}, **review},
            {"id": "sales-policy", "kind": "policy", "pack": {"policies.restricted_attributes": [{"id": "sales-secret", "columns": ["sales.orders.secret"]}]}, **review},
            {"id": "sales-question", "kind": "question", "question": "Revenue?", "terms": ["sales-revenue"], "pack": {}, **review}]


@pytest.mark.parametrize("unknown", [False, True])
def test_compile_keeps_certified_mapping_policy_and_question(installed, unknown):
    prof, store = installed
    items = legacy_items()
    if unknown:
        for item in items:
            item["certified_by"] = "departed.client@example.com"
    store.write("units/sales/draft.yaml", yaml.safe_dump({"format": 1, "unit": "sales", "items": items}).encode())
    found = pack.read(prof.clients, prof.client, lock=False)
    assert not found.findings
    assert len(found.data.get("ontology_mappings", [])) == 1
    assert len(found.data.get("policies", {}).get("restricted_attributes", [])) == 1
    assert all(item["state"] == "certified" for item in found.drafts)
    assert "old_steward" not in pack.canonical(found.data)
    assert "departed.client@example.com" not in pack.canonical(found.data)
    report = json.loads(store.read(review_upgrade.REPORT))["entries"]
    assert sum(entry["unresolved"] for entry in report.values()) == (4 if unknown else 0)
    first = copy.deepcopy(report)
    assert pack.read(prof.clients, prof.client, lock=False).hash == found.hash
    assert json.loads(store.read(review_upgrade.REPORT))["entries"] == first


def test_unit_reader_keeps_golden_reviews_and_records_original_email_once(installed):
    prof, store = installed
    store.write("units/sales/draft.yaml", yaml.safe_dump({"format": 1, "unit": "sales", "items": legacy_items()}).encode())
    record = _read_record(store, "sales")
    assert record["items"][-1]["certified_as"] == "steward"
    audits = unit_history.review_audit(prof, "sales")
    assert audits["sales-question"]["certified"]["by"] == "steward.client@example.com"
    state = appstate.for_profile(prof, store)
    before = state.history("sales")
    _read_record(store, "sales")
    assert state.history("sales") == before
    assert sum(row["step"] == "review-upgrade" for row in before) == 8


def test_proposal_release_keeps_legacy_certification(installed):
    prof, store = installed
    items = [{**item, "unit": "sales"} for item in legacy_items()]
    store.write("proposals/p1/proposal.yaml", yaml.safe_dump({"format": 1, "id": "p1", "candidate_hash": "abc", "items": items}).encode())
    history.review_proposals(prof, "abc", False)
    audits = unit_history.review_audit(prof, "sales", scope="proposal:p1")
    assert audits["sales-question"]["accepted"]["by"] == "business.client@example.com"


def test_metadata_reader_keeps_certification_and_audits_email(installed, monkeypatch):
    from kge.estate.onboard import metadata
    from kge.estate.onboard.common import OnboardError

    prof, store = installed
    job = "a" * 24
    value = {"job": job, "state": "certified", "accepted_by": "old_business", "certified_by": "old_steward",
             "accepted_at": "2026-01-01T00:00:00Z", "certified_at": "2026-01-02T00:00:00Z",
             "input": {"products": [{"id": "sales.orders", "unit": "sales"}, {"id": "sales.customers", "unit": "sales"}]}}
    store.write(f"portal-metadata/{job}.json", json.dumps(value).encode())
    monkeypatch.setattr(metadata, "_allowed", lambda *args: None)
    with pytest.raises(OnboardError, match="already certified"):
        metadata.transition(prof, job, "steward.client@example.com", True)
    record = json.loads(store.read(f"portal-metadata/{job}.json"))
    assert record["certified_as"] == "steward"
    assert "certified_by" not in record
    audit = unit_history.review_audit(prof, "sales", scope=f"metadata:{job}")
    assert audit["sales.orders"]["certified"] == {"by": "steward.client@example.com", "as": "steward", "on": "2026-01-02"}
    assert audit["sales.customers"] == audit["sales.orders"]


def test_upgrade_uses_migration_history_after_people_are_removed(installed):
    prof, store = installed
    (prof.registry.parent / prof.client / "identities.yaml").unlink()
    state = appstate.access_state(prof)
    for who in ("business.client@example.com", "steward.client@example.com"):
        state.access_delete(who, by="operator.client@example.com", via="job")
    store.write("units/sales/draft.yaml", yaml.safe_dump({"format": 1, "unit": "sales", "items": legacy_items()}).encode())
    record = _read_record(store, "sales")
    assert record["items"][-1]["certified_as"] == "steward"
    assert unit_history.review_audit(prof, "sales")["sales-question"]["certified"]["by"] == "steward.client@example.com"
    assert not state.access_grants()


def test_interrupted_upgrade_reuses_history_without_duplicate_reviews(installed, monkeypatch):
    prof, store = installed
    name = "units/sales/draft.yaml"
    store.write(name, yaml.safe_dump({"format": 1, "unit": "sales", "items": legacy_items()}).encode())
    write = store.write
    failed = []

    def interrupt(key, data):
        if key == name and not failed:
            failed.append(key)
            raise OSError("interrupted draft write")
        return write(key, data)

    monkeypatch.setattr(store, "write", interrupt)
    with pytest.raises(OSError, match="interrupted"):
        _read_record(store, "sales")
    history_before = appstate.for_profile(prof, store).history("sales")
    assert _read_record(store, "sales")["items"][-1]["certified_as"] == "steward"
    assert appstate.for_profile(prof, store).history("sales") == history_before


@pytest.mark.parametrize("actor", ["Old Person", "old\nname", "role:former"])
def test_unmappable_historical_name_preserves_review(installed, actor):
    prof, store = installed
    items = legacy_items()
    items[0]["certified_by"] = actor
    store.write("units/sales/draft.yaml", yaml.safe_dump({"format": 1, "unit": "sales", "items": items}).encode())
    found = pack.read(prof.clients, prof.client, lock=False)
    assert not found.findings
    assert found.drafts[0]["state"] == "certified"
    entries = json.loads(store.read(review_upgrade.REPORT))["entries"].values()
    assert any(entry["by"] == actor and entry["unresolved"] for entry in entries)


def test_mixed_review_fields_record_people_before_removing_names(installed):
    prof, store = installed
    items = legacy_items()
    for item in items:
        item.update(accepted_as="business", certified_as="steward")
    store.write("units/sales/draft.yaml", yaml.safe_dump({"format": 1, "unit": "sales", "items": items}).encode())
    record = _read_record(store, "sales")
    assert all("accepted_by" not in item and "certified_by" not in item for item in record["items"])
    assert unit_history.review_audit(prof, "sales")["sales-question"]["certified"]["by"] == "steward.client@example.com"


def test_compile_upgrades_authoritative_remote_record(installed, monkeypatch, tmp_path):
    from kge.adapters.objstore import open_store

    prof, store = installed
    store.write("units/sales/draft.yaml", yaml.safe_dump({"format": 1, "unit": "sales", "items": legacy_items()}).encode())
    url = str(tmp_path / "object-store")
    remote = open_store(url)
    for name in store.list():
        remote.put(f"clients/{prof.client}/{name}", store.read(name))
    remote.put(f"deploy/{prof.client}/identities.yaml", (prof.registry.parent / prof.client / "identities.yaml").read_bytes())
    monkeypatch.setenv("STORE_URL", url)
    state = appstate.access_state(prof)
    for old, role in (("old_business", "business"), ("old_steward", "steward")):
        state.access_put({"username": f"{role}.client@example.com", "name": old, "roles": [role]}, by="migration", via="migration")
    found = pack.read(prof.clients, prof.client, lock=False)
    assert not found.findings
    record = yaml.safe_load(remote.get(f"clients/{prof.client}/units/sales/draft.yaml"))
    assert record["items"][-1]["certified_as"] == "steward"
    assert "certified_by" not in record["items"][-1]
    assert unit_history.review_audit(prof, "sales")["sales-question"]["certified"]["by"] == "steward.client@example.com"


def test_release_audit_upgrades_metadata_without_a_new_review(installed):
    prof, store = installed
    job = "b" * 24
    product = {"id": "sales.orders", "unit": "sales", "name": "Orders", "fields": []}
    value = {"job": job, "state": "certified", "accepted_by": "old_business", "certified_by": "old_steward",
             "accepted_at": "2026-01-01T00:00:00Z", "certified_at": "2026-01-02T00:00:00Z",
             "input": {"products": [product]}, "reviewed": [product]}
    store.write(f"portal-metadata/{job}.json", json.dumps(value).encode())
    artifact = prof.artifacts / prof.client / "abc"
    artifact.mkdir(parents=True)
    (artifact / "catalogue.yaml").write_text(yaml.safe_dump({"products": [product]}))
    audited = history.release_audit(prof, "abc")
    assert audited["sales"][f"metadata:{job}:sales.orders"]["certified"]["by"] == "steward.client@example.com"


@pytest.mark.parametrize("unknown", [False, True])
def test_build_keeps_legacy_certified_golden_and_first_status_shows_actor(installed, unknown):
    from kge.estate.onboard import status, units

    prof, store = installed
    question = {**legacy_items()[-1], "expect": {"value": 5}}
    if unknown:
        question["certified_by"] = "departed.client@example.com"
    store.write("units/sales/draft.yaml", yaml.safe_dump({"format": 1, "unit": "sales", "items": [question]}).encode())
    store.write("units/sales/schema.sql", b"CREATE TABLE orders (id BIGINT PRIMARY KEY, amount DECIMAL(18,2));")
    appstate.for_profile(prof, store).commit("sales", {"format": 2, "unit": "sales", "label": "Sales", "version": 1,
                                                     "rows": {"origin": "invented"}, "items": {}}, [])
    shown = status.show(prof)["units"][0]["items"][0]
    assert shown["state"] == "certified"
    assert shown["certified_by"] == ("departed.client@example.com" if unknown else "steward.client@example.com")
    assert units.build(prof)["units"] == ["sales"]
    golden = json.loads((prof.estate() / "golden_sales_v1.json").read_text())
    assert golden["items"] == [{"id": question["id"], "kind": "within", "question": question["question"], "expect": question["expect"]}]
