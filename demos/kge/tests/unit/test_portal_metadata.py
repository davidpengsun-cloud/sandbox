"""Metadata input needs separate onboarding decisions before the next release."""
import copy
import json
import shutil
from dataclasses import replace

import pytest

from tests.access_fixture import seed_access

from kge import packstore
from kge.compiler import catalogue, emit, pack as packs
from kge.compiler.compile import VERSION
from kge.estate.onboard import metadata
from kge.estate.onboard.common import OnboardError
from kge.pipeline.profile import resolve
from kge.pipeline.settings import ROOT
from kge.services.catalogue import get


@pytest.fixture
def prof(tmp_path, monkeypatch):
    monkeypatch.delenv("STORE_URL", raising=False)
    monkeypatch.setenv("MODEL_PROVIDER", "none")
    shutil.copytree(ROOT / "clients/meridian", tmp_path / "clients/meridian")
    return seed_access(replace(resolve("meridian"), clients=tmp_path / "clients", artifacts=tmp_path / "artifacts", store=tmp_path / "store"))


def input_for(prof):
    return {"format": 1, "client": prof.client, "products": [{"id": "retail.account", "unit": "retail", "table": "account",
            "description": "Portal description accepted by the business team", "name": "Deposit Accounts", "product_owner": "Deposit Operations", "fields": [{"name": "account_id", "business_name": "Account reference", "description": "Portal field description"}]}]}


def artifact(prof):
    pack = packs.read(prof.clients, prof.client, lock=True)
    value, found = catalogue.read(pack)
    assert found == []
    return emit.build(pack, packs.meta_model(ROOT / "meta-model.yaml"), VERSION, catalogue.derive(value, pack))


def test_land_accept_certify_changes_next_artifact_but_never_current(prof):
    store = packstore.for_profile(prof)
    before = artifact(prof)
    baseline = store.read("catalogue.yaml")
    old_path = prof.artifacts / prof.client / before.manifest["model_hash"]
    emit.write(before, prof.artifacts, prof.client)
    current = old_path.parent / "current"
    current.unlink(missing_ok=True)
    current.symlink_to(old_path.name)
    job = metadata.land(prof, input_for(prof), "knowledge_engineer.meridian@example.com")
    assert job["state"] == "landed"
    assert metadata.land(prof, input_for(prof), "knowledge_engineer.meridian@example.com")["job"] == job["job"]
    assert store.read("catalogue.yaml") == baseline
    with pytest.raises(OnboardError, match="acceptance"):
        metadata.transition(prof, job["job"], "steward.meridian@example.com", True)
    metadata.transition(prof, job["job"], "business_team.meridian@example.com", False)
    assert store.read("catalogue.yaml") == baseline
    metadata.transition(prof, job["job"], "steward.meridian@example.com", True)
    after = artifact(prof)
    assert after.manifest["model_hash"] != before.manifest["model_hash"]
    emit.write(after, prof.artifacts, prof.client)
    assert current.resolve() == old_path
    old = get(prof, ["retail"])
    assert next(p for p in old.products if p.id == "retail.account").name != "Deposit Accounts"
    new = get(prof.at(after.manifest["model_hash"]), ["retail"])
    product = next(p for p in new.products if p.id == "retail.account")
    assert product.name == "Deposit Accounts"
    assert product.product_owner == "Deposit Operations"
    assert product.description == input_for(prof)["products"][0]["description"]
    assert next(f for f in product.fields if f.name == "account_id").description == "Portal field description"
    record = json.loads(store.read(f"portal-metadata/{job['job']}.json"))
    assert (record["landed_by"], record["accepted_as"], record["certified_as"]) == ("knowledge_engineer.meridian@example.com", "business_team", "steward")


def test_metadata_roles_staleness_and_governed_fields(prof):
    value = input_for(prof)
    with pytest.raises(OnboardError):
        metadata.land(prof, value, "analyst_retail.meridian@example.com")
    for update in [{"records": 10}, {"quality_score": 100}, {"fields": [{"name": "account_id", "terms": ["forged"]}]}, {"table": "missing"}]:
        bad = copy.deepcopy(value)
        bad["products"][0].update(update)
        with pytest.raises(OnboardError):
            metadata.land(prof, bad, "knowledge_engineer.meridian@example.com")
    job = metadata.land(prof, value, "knowledge_engineer.meridian@example.com")
    with pytest.raises(OnboardError):
        metadata.transition(prof, job["job"], "analyst_retail.meridian@example.com")
    metadata.transition(prof, job["job"], "business_team.meridian@example.com")
    with pytest.raises(OnboardError):
        metadata.transition(prof, job["job"], "business_team.meridian@example.com", True)
    store = packstore.for_profile(prof)
    store.write("catalogue.yaml", store.read("catalogue.yaml") + b"\n")
    with pytest.raises(OnboardError, match="changed since"):
        metadata.transition(prof, job["job"], "steward.meridian@example.com", True)


def test_export_defaults_are_not_materialized_and_diff_is_minimal(prof):
    import yaml

    store = packstore.for_profile(prof)
    baseline = store.read("catalogue.yaml")
    pack = packs.read(prof.clients, prof.client, lock=True)
    held = yaml.safe_load(baseline)
    exported = catalogue.derive(held, pack)
    value = {"format": 1, "client": prof.client, "products": []}
    for p in exported["products"]:
        item = {key: p[key] for key in (metadata.BUSINESS | {"id", "unit", "table"}) if key in p}
        item["badges"] = [badge for badge in p.get("badges", []) if badge not in {"CERTIFIED", "GOVERNED", "RESTRICTED", "PII"}]
        item.setdefault("name", p["table"].replace("_", " ").title())
        columns = {c["name"]: c for c in pack.lock["sources"][p["unit"]]["tables"] if c["name"] == p["table"]}[p["table"]]["columns"]
        descriptions = {c["name"]: c.get("description") for c in columns}
        item["fields"] = [{"name": f["name"], "business_name": f["business_name"], "description": f.get("description") or descriptions[f["name"]] or f["business_name"]} for f in p["fields"]]
        value["products"].append(item)
    noop = metadata.land(prof, value, "knowledge_engineer.meridian@example.com")
    assert noop["changes"] == []
    metadata.transition(prof, noop["job"], "business_team.meridian@example.com")
    metadata.transition(prof, noop["job"], "steward_glossary.meridian@example.com", True)
    assert store.read("catalogue.yaml") == baseline
    item = next(p for p in value["products"] if p["id"] == "retail.account")
    item["description"] = "New description: reviewed"
    field = next(f for f in item["fields"] if f["name"] == "account_id")
    field["business_name"] = "Account reference"
    job = metadata.land(prof, value, "knowledge_engineer.meridian@example.com")
    assert len(job["changes"]) == 2
    assert all(change["before"] != change["after"] for change in job["changes"])
    metadata.transition(prof, job["job"], "business_team.meridian@example.com")
    metadata.transition(prof, job["job"], "steward_glossary.meridian@example.com", True)
    updated = store.read("catalogue.yaml")
    expected = baseline.decode()
    start = expected.index("  description:")
    end = expected.index("  product_owner:", start)
    expected = expected[:start] + '  description: "New description: reviewed"\n' + expected[end:]
    expected = expected.replace("business_name: Account identifier", 'business_name: "Account reference"', 1)
    assert updated.decode() == expected
    record = store.read(f"portal-metadata/{job['job']}.json")
    for who in ("steward_glossary.meridian@example.com", "admin.meridian@example.com"):
        with pytest.raises(OnboardError, match="already certified by steward_glossary") as error:
            metadata.transition(prof, job["job"], who, True)
        assert error.value.status == 409
        assert store.read(f"portal-metadata/{job['job']}.json") == record
    assert store.read("catalogue.yaml") == updated


def test_yaml_patch_preserves_comments_quotes_unicode_and_other_keys():
    import yaml

    baseline = 'format: 1\nunits: [retail]\nproducts:\n- id: retail.account # keep\n  description: "old" # unchanged comment\n  fields:\n  - name: account_id\n    business_name: Account\n'.encode()
    candidate = yaml.safe_load(baseline)
    candidate["products"][0]["description"] = "Reviewed: café\nnext line"
    candidate["products"][0]["fields"][0]["description"] = "Identifier"
    actual = metadata._patch_yaml(baseline, candidate)
    expected = baseline.decode().replace('"old"', '"Reviewed: café\\nnext line"').replace('  - name: account_id\n', '  - name: account_id\n    description: "Identifier"\n')
    assert actual.decode() == expected


def test_acceptance_requires_business_role_even_for_participants(prof):
    job = metadata.land(prof, input_for(prof), "knowledge_engineer.meridian@example.com")
    for who in ("admin.meridian@example.com", "steward.meridian@example.com", "steward_glossary.meridian@example.com", "knowledge_engineer.meridian@example.com"):
        with pytest.raises(OnboardError, match="business-team") as error:
            metadata.transition(prof, job["job"], who)
        assert error.value.status == 403


@pytest.mark.parametrize("who", ["admin.meridian@example.com", "steward.meridian@example.com", "steward_glossary.meridian@example.com", "business_team.meridian@example.com", "analyst_retail.meridian@example.com"])
def test_landing_requires_drafting_permission(prof, who):
    with pytest.raises(OnboardError, match="must land portal metadata") as error:
        metadata.land(prof, input_for(prof), who)
    assert error.value.status == 403


def test_knowledge_engineer_cannot_certify_accepted_metadata(prof):
    job = metadata.land(prof, input_for(prof), "knowledge_engineer.meridian@example.com")
    metadata.transition(prof, job["job"], "business_team.meridian@example.com")
    with pytest.raises(OnboardError, match="must certify portal metadata") as error:
        metadata.transition(prof, job["job"], "knowledge_engineer.meridian@example.com", True)
    assert error.value.status == 403
    assert metadata.transition(prof, job["job"], "steward.meridian@example.com", True)["state"] == "certified"


def test_metadata_uses_process_permissions_without_requiring_building(prof):
    roles = [{"id": role, "reads": "all", "processes": [process]} for role, process in
             (("drafter", "drafting"), ("acceptor", "accepting"), ("certifier", "certification"), ("builder", "building"))]
    seed_access(prof, roles)
    with pytest.raises(OnboardError, match="must land portal metadata"):
        metadata.land(prof, input_for(prof), "builder.meridian@example.com")
    job = metadata.land(prof, input_for(prof), "drafter.meridian@example.com")
    assert metadata.transition(prof, job["job"], "acceptor.meridian@example.com")["state"] == "accepted"
    assert metadata.transition(prof, job["job"], "certifier.meridian@example.com", True)["state"] == "certified"


def test_self_certification_guard_even_if_role_changes(prof, monkeypatch):
    job = metadata.land(prof, input_for(prof), "knowledge_engineer.meridian@example.com")
    metadata.transition(prof, job["job"], "business_team.meridian@example.com")
    roles = copy.deepcopy(metadata._roles(prof))
    roles["business_team.meridian@example.com"]["processes"].extend(["accepting", "certification"])
    monkeypatch.setattr(metadata, "_roles", lambda prof: roles)
    with pytest.raises(OnboardError, match="own acceptance"):
        metadata.transition(prof, job["job"], "business_team.meridian@example.com", True)


def test_landing_requires_unit_access(prof, monkeypatch):
    roles = metadata._roles(prof)
    roles["knowledge_engineer.meridian@example.com"]["reads"] = ["wealth"]
    monkeypatch.setattr(metadata, "_roles", lambda prof: roles)
    with pytest.raises(OnboardError, match="cannot read this unit"):
        metadata.land(prof, input_for(prof), "knowledge_engineer.meridian@example.com")


@pytest.mark.parametrize("terms", ["Revenue", ["Revenue"]])
def test_field_terms_are_rejected_by_ownership_guard(prof, terms):
    value = input_for(prof)
    value["products"][0]["fields"][0]["terms"] = terms
    with pytest.raises(OnboardError, match="Only descriptive metadata of landed columns"):
        metadata.land(prof, value, "knowledge_engineer.meridian@example.com")


@pytest.mark.parametrize("certify,action", [(False, "accept"), (True, "certify")])
def test_nonparticipant_error_names_requested_action(prof, certify, action):
    with pytest.raises(OnboardError, match=f"must {action} portal metadata"):
        metadata.transition(prof, "0" * 24, "analyst_retail.meridian@example.com", certify)


def test_yaml_appends_only_new_product_and_field_nodes():
    import yaml

    baseline = b'format: 1\nunits:\n- retail\nproducts:\n- id: retail.account\n  unit: retail\n  table: account\n  fields:\n  - name: account_id\n    business_name: Account\n'
    candidate = yaml.safe_load(baseline)
    candidate["products"][0]["fields"].append({"name": "customer_id", "business_name": "Customer"})
    candidate["products"].append({"id": "retail.customer", "unit": "retail", "table": "customer", "description": "Customers"})
    updated = metadata._patch_yaml(baseline, candidate)
    assert updated.startswith(baseline)
    assert len(metadata._changes(yaml.safe_load(baseline), candidate)) == 2
    assert yaml.safe_load(updated) == candidate


def test_yaml_badges_change_preserves_next_key_and_comments():
    import yaml

    baseline = b'format: 1\nunits: [retail]\nproducts:\n- id: retail.account\n  badges:\n  - GOLD\n  description: Original # retained\n'
    candidate = yaml.safe_load(baseline)
    candidate["products"][0]["badges"] = ["REVIEWED", "CERTIFIED"]
    updated = metadata._patch_yaml(baseline, candidate)
    assert updated == baseline.replace(b'- GOLD', b'- "REVIEWED"\n  - "CERTIFIED"')


@pytest.mark.parametrize("badges", [["GOLD"], ["PII", "GOLD"], []])
def test_shortened_block_list_is_valid_and_only_changes_that_list(badges):
    import yaml
    baseline = b'products:\n- id: retail.account\n  badges:\n  - GOLD\n  - CERTIFIED\n  - RESTRICTED\n  - PII\n  description: Keep this # untouched\n'
    candidate = yaml.safe_load(baseline)
    candidate["products"][0]["badges"] = badges
    result = metadata._patch_yaml(baseline, candidate)
    assert yaml.safe_load(result) == candidate
    assert result.startswith(b'products:\n- id: retail.account\n  badges:\n')
    assert result.endswith(b'  description: Keep this # untouched\n')


def test_shortened_badges_land_accept_and_certify(prof):
    import yaml
    store = packstore.for_profile(prof)
    before = store.read("catalogue.yaml")
    held = yaml.safe_load(before)
    product = next(p for p in held["products"] if p["id"] == "retail.account")
    product["badges"] = ["GOLD", "FEATURED", "POPULAR"]
    baseline = yaml.safe_dump(held, sort_keys=False).encode()
    store.write("catalogue.yaml", baseline)
    value = input_for(prof)
    value["products"][0] = {"id": "retail.account", "unit": "retail", "table": "account", "fields": [], "badges": ["GOLD"]}
    job = metadata.land(prof, value, "knowledge_engineer.meridian@example.com")
    metadata.transition(prof, job["job"], "business_team.meridian@example.com")
    metadata.transition(prof, job["job"], "steward.meridian@example.com", True)
    assert store.read("catalogue.yaml") == baseline.replace(b'  - GOLD\n  - FEATURED\n  - POPULAR', b'  - "GOLD"')


def test_bad_yaml_is_a_plain_422(prof):
    store = packstore.for_profile(prof)
    store.write("catalogue.yaml", b'products: [broken\n')
    with pytest.raises(OnboardError) as error:
        metadata.land(prof, input_for(prof), "knowledge_engineer.meridian@example.com")
    assert error.value.status == 422
    assert "scanner" not in str(error.value).lower()
    with pytest.raises(OnboardError, match="YAML is invalid"):
        metadata._patch_yaml(b'bad: [', {})


def test_cli_yaml_errors_are_plain_422(monkeypatch, capsys):
    import sys
    import yaml
    monkeypatch.setattr(sys, "argv", ["metadata", "--as", "knowledge_engineer.meridian@example.com", "land", "--input", "unused.json"])
    def broken(*args):
        raise yaml.scanner.ScannerError(None, None, "private scanner details", None)
    monkeypatch.setattr(metadata, "resolve", broken)
    assert metadata.main() == 2
    assert json.loads(capsys.readouterr().out) == {"error": "Metadata YAML is invalid", "status": 422}


def test_metadata_input_cannot_introduce_governed_badges(prof):
    import yaml
    store = packstore.for_profile(prof)
    held = yaml.safe_load(store.read("catalogue.yaml"))
    next(p for p in held["products"] if p["id"] == "retail.account")["badges"] = []
    store.write("catalogue.yaml", yaml.safe_dump(held, sort_keys=False).encode())
    for badge in ("CERTIFIED", "GOVERNED", "RESTRICTED", "PII", " certified "):
        value = input_for(prof)
        value["products"][0]["badges"] = [badge]
        with pytest.raises(OnboardError, match="portal-owned badges") as error:
            metadata.land(prof, value, "knowledge_engineer.meridian@example.com")
        assert error.value.status == 422


@pytest.mark.parametrize("badge", ["CERTIFIED", "GOVERNED", "RESTRICTED", "PII", "CERTIFIED\u200b", "C\u200bERTIFIED", "СERTIFIED", "CERTIFIED\u00a0", "ＣＥＲＴＩＦＩＥＤ", "GΟVERNED", "REЅTRICTED", "РІI", "certıfied", "CERTIFIED\ufe0f"])
def test_metadata_refuses_lookalike_governed_badges(prof, badge):
    value = input_for(prof)
    value["products"][0]["badges"] = [badge]
    with pytest.raises(OnboardError, match="portal-owned badges") as error:
        metadata.land(prof, value, "knowledge_engineer.meridian@example.com")
    assert error.value.status == 422


@pytest.mark.parametrize("badge", [
    *[badge + suffix for badge in ("CERTIFIED", "GOVERNED", "RESTRICTED", "PII")
      for suffix in ("", "\u034f", "\u3164", "\u1160", "\u115f", "\u2800", "\u0301", "\u0323")],
    "\u202eDEIFITREC", "CERTlFlED", "CERT1F1ED", "G0VERNED", "Pll", "P1I",
    "CERTIΦIED", "CERTIFIEԀ", "ᏟᎬRᎢIFIED", "REՍTRICTED", "ᴄERTIFIED", "ↄERTIFIED",
    "CERTIFIE", "CERTIFIEX", "XCERTIFIED", "C_E-R T I F I E D", "P2I", "", "   ",
    "GOLD!", "GOLD\n", "GOLD\t", "GÖLD", "A" * 33, "certıfied",
])
def test_badge_allow_list_refuses_report_bypasses(prof, badge):
    value = input_for(prof)
    value["products"][0]["badges"] = [badge]
    with pytest.raises(OnboardError, match="portal-owned badges"):
        metadata.land(prof, value, "knowledge_engineer.meridian@example.com")


@pytest.mark.parametrize("badge,expected", [
    *[(badge, badge) for badge in ("GOLD", "SILVER", "BRONZE", "TOP RATED", "Q3-2025", "A" * 32)],
    ("  top   rated  ", "TOP RATED"), ("ＧＯＬＤ", "GOLD"),
])
def test_badge_allow_list_normalises_stored_values(prof, badge, expected):
    value = input_for(prof)
    value["products"][0]["badges"] = [badge]
    job = metadata.land(prof, value, "knowledge_engineer.meridian@example.com")
    product = next(p for p in job["candidate"]["products"] if p["id"] == value["products"][0]["id"])
    assert expected in product["badges"]


def test_base_metadata_reviews_keep_people_in_audit_without_an_onboarding_manifest(prof):
    from kge.adapters import appstate
    from kge.pipeline import unit_history
    store = packstore.for_profile(prof)
    job = metadata.land(prof, input_for(prof), "knowledge_engineer.meridian@example.com")
    metadata.transition(prof, job["job"], "business_team.meridian@example.com")
    metadata.transition(prof, job["job"], "steward.meridian@example.com", True)
    state = appstate.for_profile(prof, store)
    assert state.units() == []
    assert not store.exists("units/retail/unit.yaml")
    audit = unit_history.review_audit(prof, "retail", scope=f"metadata:{job['job']}")["retail.account"]
    assert audit["accepted"]["by"] == "business_team.meridian@example.com"
    assert audit["certified"]["by"] == "steward.meridian@example.com"
    assert b"@example.com" not in store.read("catalogue.yaml")
    appstate.access_state(prof).access_delete("business_team.meridian@example.com", by="operator@example.com", via="job")
    appstate.access_state(prof).access_delete("steward.meridian@example.com", by="operator@example.com", via="job")
    assert unit_history.review_audit(prof, "retail", scope=f"metadata:{job['job']}")["retail.account"] == audit


def test_failed_metadata_record_write_restores_catalogue_and_review_history(prof, monkeypatch):
    from kge.adapters import appstate
    from kge.ports import StoreError
    store = packstore.for_profile(prof)
    job = metadata.land(prof, input_for(prof), "knowledge_engineer.meridian@example.com")
    metadata.transition(prof, job["job"], "business_team.meridian@example.com")
    before = store.read("catalogue.yaml")
    history = appstate.for_profile(prof, store).history("retail")
    write = packstore.Store.write

    def refused(self, name, data):
        if name == f"portal-metadata/{job['job']}.json":
            raise StoreError("record storage unavailable")
        return write(self, name, data)

    monkeypatch.setattr(packstore.Store, "write", refused)
    with pytest.raises(StoreError, match="record storage unavailable"):
        metadata.transition(prof, job["job"], "steward.meridian@example.com", True)
    assert store.read("catalogue.yaml") == before
    assert appstate.for_profile(prof, store).history("retail") == history
