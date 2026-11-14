"""Reviewers remain in audit history while compiled drafts retain only their roles."""
import json

import pytest
import yaml

from kge import packstore
from kge.adapters import appstate, released
from kge.adapters.appstate import bigquery
from kge.estate.onboard import common, review
from kge.graph import history, release
from kge.pipeline import access_table, unit_history
from kge.pipeline.profile import Profile
from tests import bigquery_fake
from tests.unit.test_appstate import TableState
from tests.unit import test_faithful_rollback
from tests.unit import test_portal_metadata

client = test_faithful_rollback.client
metadata_prof = test_portal_metadata.prof

HASH = "ab" * 32
ADMIN = "operator@example.com"
ACCEPTER = "business@example.com"
CERTIFIER = "steward@example.com"
ROLES = [{"id": "z_reviewer", "processes": ["accepting", "certification"], "reads": "all"},
         {"id": "a_reviewer", "processes": ["accepting", "certification"], "reads": "all"},
         {"id": "reader", "processes": ["querying"], "reads": "all"}]


@pytest.fixture(params=["files", "bigquery"], ids=["files", "table-state"])
def prof(tmp_path, monkeypatch, request):
    monkeypatch.setenv("MODEL_PROVIDER", "none")
    monkeypatch.setenv("APP_STATE_BACKEND", request.param)
    monkeypatch.setenv("ACCESS_ADMINS", ADMIN)
    monkeypatch.delenv("STORE_URL", raising=False)
    if request.param == "bigquery":
        monkeypatch.setenv("BIGQUERY_PROJECT", bigquery_fake.PROJECT)
        monkeypatch.setenv("BIGQUERY_DATASET_PREFIX", "kge")
        fake = bigquery_fake.FakeBigQuery()
        monkeypatch.setattr(bigquery, "_ENSURED", set())
        monkeypatch.setattr(bigquery.BigQueryState, "_post", lambda self, body: fake.state_post(body))
    prof = Profile("acme", "acme", tmp_path / "clients", tmp_path / "artifacts", tmp_path / "store",
                   tmp_path / "landing", tmp_path / "warehouses", tmp_path / "identities.yaml", None,
                   tmp_path / "profiles.yaml")
    artifact = prof.artifacts / prof.client / HASH
    (artifact / "access").mkdir(parents=True)
    (artifact / "access/roles.json").write_text(json.dumps({"roles": ROLES}))
    (artifact.parent / "current").symlink_to(HASH)
    for who in (ACCEPTER, CERTIFIER):
        access_table.grant(prof, who, ["z_reviewer", "reader", "a_reviewer"], who, by=ADMIN, via="api")
    store = packstore.for_profile(prof)
    if request.param == "bigquery":
        state = TableState(store)
        monkeypatch.setattr(appstate, "for_profile", lambda profile, store: state)
    manifest = {"format": 2, "unit": "leasing", "version": 1, "rows": {"origin": "invented"},
                "inputs": {}, "items": {name: [] for name in common.SUB_GRAPHS}, "requests": []}
    appstate.for_profile(prof, store).commit("leasing", manifest, [])
    store.write("units/leasing/draft.yaml", yaml.safe_dump({"format": 1, "unit": "leasing", "items": [
        {"id": "source-leasing", "kind": "source", "pack": {"sources": []}}]}).encode())
    return prof


def test_drafts_record_roles_and_history_preserves_emails_after_revocation(prof):
    review.accept(prof, "leasing", as_=ACCEPTER, on="2026-10-30")
    review.certify(prof, "leasing", as_=CERTIFIER, on="2026-10-31")
    store = packstore.for_profile(prof)
    raw = store.read("units/leasing/draft.yaml").decode()
    item = yaml.safe_load(raw)["items"][0]
    assert item["accepted_as"] == item["certified_as"] == "a_reviewer"
    assert item["accepted_on"] == "2026-10-30" and item["certified_on"] == "2026-10-31"
    assert "accepted_by" not in item and "certified_by" not in item
    assert ACCEPTER not in raw and CERTIFIER not in raw
    expected = {"source-leasing": {
        "accepted": {"by": ACCEPTER, "as": "a_reviewer", "on": "2026-10-30"},
        "certified": {"by": CERTIFIER, "as": "a_reviewer", "on": "2026-10-31"}}}
    assert unit_history.review_audit(prof, "leasing", 1) == expected
    access_table.revoke(prof, ACCEPTER, by=ADMIN, via="api")
    access_table.revoke(prof, CERTIFIER, by=ADMIN, via="api")
    assert unit_history.review_audit(prof, "leasing", 1) == expected
    assert store.read("units/leasing/draft.yaml").decode() == raw
    with pytest.raises(common.OnboardError) as error:
        review.accept(prof, "leasing", as_=ACCEPTER)
    assert error.value.status == 403


def test_a_person_cannot_certify_their_acceptance_but_another_holder_of_the_same_role_can(prof):
    review.accept(prof, "leasing", as_=ACCEPTER, on="2026-10-30")
    store = packstore.for_profile(prof)
    before = store.read("units/leasing/draft.yaml")
    with pytest.raises(common.OnboardError, match="own acceptance") as error:
        review.certify(prof, "leasing", as_=ACCEPTER)
    assert error.value.status == 403
    assert store.read("units/leasing/draft.yaml") == before
    assert review.certify(prof, "leasing", as_=CERTIFIER)["changed"] == ["source-leasing"]


def test_review_mutations_and_status_show_audit_people_and_live_display_names(prof):
    from kge.estate.onboard import status
    from kge.services.onboard.service import page_items

    access_table.grant(prof, ACCEPTER, ["a_reviewer"], "Business reviewer", by=ADMIN, via="api")
    access_table.grant(prof, CERTIFIER, ["a_reviewer"], "Steward reviewer", by=ADMIN, via="api")
    accepted = page_items(review.accept(prof, "leasing", as_=ACCEPTER))["items"][0]
    assert (accepted["accepted_by"], accepted["accepted_name"], accepted["accepted_as"]) == (
        ACCEPTER, "Business reviewer", "a_reviewer")
    certified = page_items(review.certify(prof, "leasing", as_=CERTIFIER))["items"][0]
    assert (certified["accepted_by"], certified["accepted_name"], certified["certified_by"], certified["certified_name"]) == (
        ACCEPTER, "Business reviewer", CERTIFIER, "Steward reviewer")
    store = packstore.for_profile(prof)
    record = yaml.safe_load(store.read("units/leasing/draft.yaml"))
    assert all(not key.endswith(("_by", "_name")) for key in record["items"][0])
    access_table.grant(prof, ACCEPTER, ["a_reviewer"], "Updated reviewer", by=ADMIN, via="api")
    again = page_items(review.certify(prof, "leasing", as_=CERTIFIER))["items"][0]
    shown = status._reviewed_items(prof, record["items"], "leasing", 1)[0]
    assert again["accepted_name"] == shown["accepted_name"] == "Updated reviewer"
    access_table.revoke(prof, ACCEPTER, by=ADMIN, via="api")
    after = page_items(review.certify(prof, "leasing", as_=CERTIFIER))["items"][0]
    assert after["accepted_by"] == after["accepted_name"] == ACCEPTER


def test_acting_role_is_stable_when_grant_and_artifact_order_change(prof):
    assert common._acting_role(prof, ACCEPTER, "accept") == "a_reviewer"
    assert common._acting_role(prof, ACCEPTER, "certify") == "a_reviewer"
    access_table.grant(prof, ACCEPTER, ["a_reviewer", "z_reviewer", "reader"], "Business", by=ADMIN, via="api")
    path = prof.artifacts / prof.client / HASH / "access/roles.json"
    path.write_text(json.dumps({"roles": list(reversed(ROLES))}))
    assert common._acting_role(prof, ACCEPTER, "accept") == "a_reviewer"
    access_table.grant(prof, ACCEPTER, ["reader"], "Business", by=ADMIN, via="api")
    with pytest.raises(common.OnboardError):
        common._acting_role(prof, ACCEPTER, "certify")


def test_release_audit_uses_candidate_items_and_version_without_rechecking_people(prof):
    review.accept(prof, "leasing", as_=ACCEPTER, on="2026-10-30")
    review.certify(prof, "leasing", as_=CERTIFIER, on="2026-10-31")
    original = unit_history.review_audit(prof, "leasing", 1)
    store = packstore.for_profile(prof)
    state = appstate.for_profile(prof, store)
    manifest = state.manifest("leasing")
    state.commit("leasing", {**manifest, "version": 2}, [
        {"step": "accept", "by": "later@example.com", "at": "2026-11-01T00:00:00Z", "version": 2,
         "detail": {"items": ["source-leasing"], "as": "a_reviewer", "on": "2026-11-01"}},
        {"step": "certify", "by": "proposal@example.com", "at": "2026-10-31T00:00:00Z", "version": 1,
         "detail": {"items": ["source-leasing"], "as": "a_reviewer", "on": "2026-10-31", "scope": "proposal:p1"}},
        {"step": "accept", "by": ACCEPTER, "at": "2026-10-31T00:00:00Z", "version": 1,
         "detail": {"items": ["excluded-item"], "as": "a_reviewer", "on": "2026-10-31"}}])
    path = prof.artifacts / prof.client / HASH / "drafts.json"
    path.write_text(json.dumps({"format": 1, "warehouses": {"leasing": {"version": 1, "inputs": "cd" * 32}},
                               "items": [{"unit": "leasing", "id": "source-leasing", "state": "certified"}]}))
    access_table.revoke(prof, CERTIFIER, by=ADMIN, via="api")
    assert history.release_audit(prof, HASH) == {"leasing": original}
    assert unit_history.review_audit(prof, "leasing", 1, scope="proposal:p1")["source-leasing"]["certified"]["by"] == "proposal@example.com"
    release.review_drafts(path.parent, HASH, False)


def test_release_and_rollback_keep_the_original_audit_and_call_the_post_release_hook(client, monkeypatch):
    c = client
    observed = []
    audits = [{"branch": {"item": {"accepted": {"by": ACCEPTER, "as": "business", "on": "2026-10-30"}}}},
              {"branch": {"item": {"accepted": {"by": "later@example.com", "as": "business", "on": "2026-10-31"}}}}]
    current_audit = {"value": audits[0]}
    monkeypatch.setattr(history, "release_audit", lambda *args: current_audit["value"])
    monkeypatch.setattr(release, "post_release", lambda prof: observed.append((prof.serving, prof.identity())))
    c.stage(1)
    release.release(c.prof, c.graph.parent.name)
    original = release.kept(c.prof)[0]
    assert original["audit"] == audits[0]
    assert released.read(c.graph.parent)["audit"] == audits[0]
    c.stage(2)
    current_audit["value"] = audits[1]
    release.release(c.prof, c.graph.parent.name)
    assert release.kept(c.prof)[0]["audit"] == audits[1]
    release.release(c.prof, previous=True)
    assert released.read(c.graph.parent)["audit"] == audits[0]
    assert release.kept(c.prof)[0]["audit"] == audits[0]
    assert len(observed) == 3 and all(serving for serving, _ in observed)
    assert observed[0][1] == observed[2][1] == original["identity"]
    assert observed[1][1] != original["identity"]


def test_post_release_failure_reports_failure_without_undoing_successful_publication(client, monkeypatch):
    c = client
    c.stage(1)
    release.release(c.prof, c.graph.parent.name)
    first = c.prof.serve().identity()
    c.stage(2)

    def failed(prof):
        raise RuntimeError("post-release evaluation failed")

    monkeypatch.setattr(release, "post_release", failed)
    with pytest.raises(RuntimeError, match="post-release evaluation failed"):
        release.release(c.prof, c.graph.parent.name)
    second = c.prof.serve().identity()
    assert second != first and release.kept(c.prof)[0]["identity"] == second
    with pytest.raises(RuntimeError, match="post-release evaluation failed"):
        release.release(c.prof, previous=True)
    assert c.prof.serve().identity() == first


def test_certification_refuses_role_only_acceptance_whose_person_audit_is_missing(prof):
    store = packstore.for_profile(prof)
    record = yaml.safe_load(store.read("units/leasing/draft.yaml"))
    record["items"][0].update({"accepted_as": "a_reviewer", "accepted_on": "2026-10-30"})
    store.write("units/leasing/draft.yaml", yaml.safe_dump(record).encode())
    before = store.read("units/leasing/draft.yaml")
    with pytest.raises(common.OnboardError, match="audit|history|acceptance"):
        review.certify(prof, "leasing", as_=ACCEPTER)
    assert store.read("units/leasing/draft.yaml") == before


@pytest.mark.parametrize("signing_role", ["steward", "admin"])
def test_role_signoff_reads_only_candidate_roles_and_records_role_identity(client, monkeypatch, signing_role):
    c = client
    c.stage(1)
    artifact = c.prof.artifacts / c.prof.client / c.graph.parent.name
    (artifact / "access").mkdir(exist_ok=True)
    (artifact / "access/roles.json").write_text(json.dumps({"roles": [
        {"id": "steward", "processes": ["certification"]},
        {"id": "admin", "processes": ["certification"]},
        {"id": "reader", "processes": ["querying"]},
        {"id": "access_admin", "processes": ["certification"]}]}))

    def no_access(*args, **kwargs):
        raise AssertionError("role sign-off must not read the access table")

    monkeypatch.setattr(appstate, "access_state", no_access)
    recorded = []
    monkeypatch.setattr(unit_history, "record", lambda *args: recorded.append(args))
    result = release.sign_off(c.prof, c.graph.parent.name, role=signing_role)
    assert result["principal"] == f"role:{signing_role}"
    assert release.marked(c.graph, release.SIGNOFF)["principal"] == f"role:{signing_role}"
    assert recorded[0][1:3] == ("sign-off", f"role:{signing_role}")
    before = (c.graph.parent / release.SIGNOFF).read_bytes()
    for role in ("missing", "reader", "access_admin"):
        with pytest.raises(release.Unreleasable, match="may not perform"):
            release.sign_off(c.prof, c.graph.parent.name, role=role)
    with pytest.raises(release.Unreleasable, match="never both"):
        release.sign_off(c.prof, c.graph.parent.name, principal=ACCEPTER, role="steward")
    assert (c.graph.parent / release.SIGNOFF).read_bytes() == before


@pytest.mark.parametrize("principal", ["role:steward", "ROLE:steward", "access_admin", "steward\n", ""])
def test_a_principal_string_cannot_request_operator_role_signoff(client, principal):
    c = client
    c.stage(1)
    artifact = c.prof.artifacts / c.prof.client / c.graph.parent.name
    (artifact / "access").mkdir(exist_ok=True)
    (artifact / "access/roles.json").write_text(json.dumps({"roles": [{"id": "steward", "processes": ["certification"]}]}))
    before = (c.graph.parent / release.SIGNOFF).read_bytes()
    with pytest.raises(release.Unreleasable, match="may not sign"):
        release.sign_off(c.prof, c.graph.parent.name, principal=principal)
    assert (c.graph.parent / release.SIGNOFF).read_bytes() == before


def test_person_signoff_requires_a_table_content_role_and_records_normalized_username(prof, monkeypatch):
    path = prof.artifacts / prof.client / HASH / "access/roles.json"
    path.write_text(json.dumps({"roles": [*ROLES, {"id": "admin", "processes": ["certification"]},
                                           {"id": "access_admin", "processes": ["certification"]}]}))
    access_table.set_admins(prof, [CERTIFIER], by=ADMIN)
    access_table.grant(prof, CERTIFIER, ["reader"], "Access administrator", by=ADMIN, via="api")
    graph = prof.at(HASH).graph_path()
    graph.parent.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(release, "checked", lambda *args: {"run_id": "gate"})
    with pytest.raises(release.Unreleasable, match="may not sign"):
        release.sign_off(prof, HASH, CERTIFIER)
    access_table.grant(prof, CERTIFIER, ["admin"], "Content administrator", by=ADMIN, via="api")
    signed = release.sign_off(prof, HASH, f"  {CERTIFIER.upper()}  ")
    assert signed["principal"] == CERTIFIER


def test_reserved_admin_never_becomes_the_acting_review_role(prof):
    path = prof.artifacts / prof.client / HASH / "access/roles.json"
    path.write_text(json.dumps({"roles": [*ROLES, {"id": "access_admin", "processes": ["accepting", "certification"]}]}))
    access_table.grant(prof, ACCEPTER, ["z_reviewer"], "Business", by=ADMIN, via="api")
    access_table.set_admins(prof, [ACCEPTER], by=ADMIN)
    assert access_table.Access.for_profile(prof).roles_of(ACCEPTER) == {"access_admin", "z_reviewer"}
    assert common._acting_role(prof, ACCEPTER, "accept") == "z_reviewer"
    assert common._acting_role(prof, ACCEPTER, "certify") == "z_reviewer"
    review.accept(prof, "leasing", as_=ACCEPTER)
    record = yaml.safe_load(packstore.for_profile(prof).read("units/leasing/draft.yaml"))
    assert record["items"][0]["accepted_as"] == "z_reviewer"


def test_accept_again_repairs_missing_person_audit_and_enforces_separate_certifier(prof):
    store = packstore.for_profile(prof)
    record = yaml.safe_load(store.read("units/leasing/draft.yaml"))
    record["items"][0].update({"accepted_as": "a_reviewer", "accepted_on": "2026-10-29"})
    store.write("units/leasing/draft.yaml", yaml.safe_dump(record).encode())
    assert review.accept(prof, "leasing", as_=ACCEPTER, on="2026-10-31")["changed"] == ["source-leasing"]
    assert unit_history.review_audit(prof, "leasing", 1)["source-leasing"]["accepted"] == {
        "by": ACCEPTER, "as": "a_reviewer", "on": "2026-10-31"}
    with pytest.raises(common.OnboardError, match="own acceptance"):
        review.certify(prof, "leasing", as_=ACCEPTER)
    assert review.certify(prof, "leasing", as_=CERTIFIER)["changed"] == ["source-leasing"]


def test_release_audit_includes_only_the_matching_candidates_scoped_proposal_reviews(prof):
    store = packstore.for_profile(prof)
    for pid, model_hash, who in (("p1", HASH, ACCEPTER), ("p2", "cd" * 32, "unrelated@example.com")):
        store.write(f"proposals/{pid}/proposal.yaml", yaml.safe_dump({
            "candidate_hash": model_hash, "items": [{"unit": "leasing", "id": "source-leasing",
            "accepted_as": "a_reviewer", "certified_as": "a_reviewer"}]}).encode())
        unit_history.record(prof, "accept", who, {"items": ["source-leasing"], "as": "a_reviewer",
                            "on": "2026-10-30", "scope": f"proposal:{pid}"}, ["leasing"])
        unit_history.record(prof, "certify", CERTIFIER, {"items": ["source-leasing"], "as": "a_reviewer",
                            "on": "2026-10-31", "scope": f"proposal:{pid}"}, ["leasing"])
    assert history.release_audit(prof, HASH) == {"leasing": {"proposal:p1:source-leasing": {
        "accepted": {"by": ACCEPTER, "as": "a_reviewer", "on": "2026-10-30"},
        "certified": {"by": CERTIFIER, "as": "a_reviewer", "on": "2026-10-31"}}}}


def test_compiler_keeps_certification_after_both_reviewers_are_revoked(prof):
    """P2 integration contract: real pack reading must consume the role-valued review fields."""
    from kge.compiler import pack as packs

    store = packstore.for_profile(prof)
    store.write("pack.yaml", yaml.safe_dump({"manifest": {"client": prof.client, "id": "review-audit", "version": "1.0.0"},
                                           "access": {"roles": ROLES}}).encode())
    review.accept(prof, "leasing", as_=ACCEPTER, on="2026-10-30")
    review.certify(prof, "leasing", as_=CERTIFIER, on="2026-10-31")
    access_table.revoke(prof, ACCEPTER, by=ADMIN, via="api")
    access_table.revoke(prof, CERTIFIER, by=ADMIN, via="api")
    compiled = packs.read(prof.clients, prof.client, lock=False)
    assert not compiled.findings
    assert compiled.drafts[0]["state"] == "certified"
    assert ACCEPTER not in json.dumps(compiled.drafts) and CERTIFIER not in json.dumps(compiled.drafts)
    audit = unit_history.review_audit(prof, "leasing", 1)["source-leasing"]
    assert audit["accepted"]["by"] == ACCEPTER and audit["certified"]["by"] == CERTIFIER


@pytest.mark.parametrize("missing", ["item-unit"])
@pytest.mark.parametrize("action", ["accept", "certify"])
def test_proposal_review_without_unit_history_refuses_before_any_write(prof, missing, action):
    from kge.compiler import pack as packs

    store = packstore.for_profile(prof)
    store.write("pack.yaml", yaml.safe_dump({"manifest": {"client": prof.client, "id": "review-audit", "version": "1.0.0"},
                                           "access": {"roles": ROLES}}).encode())
    entry = {"id": "new-alias", "kind": "alias"}
    if missing == "unit-manifest":
        entry["unit"] = "baseunit"
    if action == "certify":
        entry.update({"accepted_as": "a_reviewer", "accepted_by": ACCEPTER, "accepted_on": "2026-10-30"})
    store.write("proposals/p1/proposal.yaml", yaml.safe_dump({
        "kept": True, "after_run_id": "checked-run", "base_hash": packs.read(prof.clients, prof.client, lock=False).hash,
        "items": [entry]}).encode())
    store.write(f"proposals/p1/{prof.client}/pack.yaml", b"a different candidate pack")
    state = appstate.for_profile(prof, store)
    before_store = {name: store.read(name) for name in store.list("")}
    before_units = state.units()
    before_history = {unit: state.history(unit) for unit in [*before_units, "baseunit"]}
    before_access = appstate.access_state(prof).access_audit()
    with pytest.raises(common.OnboardError, match="review history is unavailable") as error:
        getattr(review, action)(prof, proposal="p1", as_=CERTIFIER if action == "certify" else ACCEPTER)
    assert error.value.status == 409
    assert {name: store.read(name) for name in store.list("")} == before_store
    assert state.units() == before_units
    assert {unit: state.history(unit) for unit in [*before_units, "baseunit"]} == before_history
    assert appstate.access_state(prof).access_audit() == before_access


def test_base_proposal_reviews_append_audit_without_onboarding_the_base_unit(prof):
    from kge.compiler import pack as packs
    store = packstore.for_profile(prof)
    store.write("pack.yaml", yaml.safe_dump({"manifest": {"client": prof.client, "id": "review-audit", "version": "1.0.0"},
                                           "access": {"roles": ROLES}}).encode())
    store.write("proposals/p1/proposal.yaml", yaml.safe_dump({
        "kept": True, "after_run_id": "checked-run", "base_hash": packs.read(prof.clients, prof.client, lock=False).hash,
        "items": [{"id": "alias:test", "kind": "alias", "unit": "baseunit"}]}).encode())
    store.write(f"proposals/p1/{prof.client}/pack.yaml", store.read("pack.yaml"))
    state = appstate.for_profile(prof, store)
    accepted = review.accept(prof, proposal="p1", as_=ACCEPTER)
    certified = review.certify(prof, proposal="p1", as_=CERTIFIER)
    assert accepted["changed"] == certified["changed"] == ["alias:test"]
    assert accepted["items"][0]["accepted_by"] == ACCEPTER
    assert certified["items"][0]["accepted_by"] == ACCEPTER
    assert certified["items"][0]["certified_by"] == CERTIFIER
    assert "baseunit" not in state.units()
    with pytest.raises(FileNotFoundError):
        state.manifest("baseunit")
    audit = unit_history.review_audit(prof, "baseunit", scope="proposal:p1")["alias:test"]
    assert audit["accepted"]["by"] == ACCEPTER and audit["certified"]["by"] == CERTIFIER
    record = store.read("proposals/p1/proposal.yaml").decode()
    assert ACCEPTER not in record and CERTIFIER not in record


def test_release_audit_selects_certified_metadata_present_in_the_candidate(prof):
    store = packstore.for_profile(prof)
    item = {"id": "baseunit.accounts", "unit": "baseunit", "table": "accounts", "description": "Reviewed",
            "fields": [{"name": "account_id", "business_name": "Account identifier"}]}
    path = prof.artifacts / prof.client / HASH / "catalogue.yaml"
    path.write_text(yaml.safe_dump({"products": [{**item, "counts": {"views": 0}}]}))
    expected = {}
    for job, state, description, field in [("kept", "certified", "Reviewed", "Account identifier"),
                                          ("pending", "accepted", "Reviewed", "Account identifier"),
                                          ("superseded", "certified", "Old description", "Account identifier"),
                                          ("old-field", "certified", "Reviewed", "Old identifier")]:
        reviewed = {**item, "description": description,
                    "fields": [{"name": "account_id", "business_name": field}]}
        store.write(f"portal-metadata/{job}.json", json.dumps({"job": job, "state": state, "reviewed": [reviewed]}).encode())
        for step, who in [("accept", ACCEPTER), ("certify", CERTIFIER)]:
            unit_history.record(prof, step, who, {"scope": f"metadata:{job}", "items": [item["id"]],
                                                "as": "a_reviewer", "on": "2026-10-31"}, ["baseunit"])
        if job == "kept":
            expected = unit_history.review_audit(prof, "baseunit", scope="metadata:kept")[item["id"]]
    access_table.revoke(prof, CERTIFIER, by=ADMIN, via="api")
    assert history.release_audit(prof, HASH) == {"baseunit": {"metadata:kept:baseunit.accounts": expected}}
    assert "baseunit" not in appstate.for_profile(prof, store).units()


def test_metadata_certification_records_the_values_the_compiled_candidate_carries(metadata_prof):
    from kge.compiler import emit
    from kge.estate.onboard import metadata

    prof = metadata_prof
    value = test_portal_metadata.input_for(prof)
    value["products"][0]["badges"] = [" reviewed "]
    job = metadata.land(prof, value, "knowledge_engineer.meridian@example.com")
    metadata.transition(prof, job["job"], "business_team.meridian@example.com")
    certified = metadata.transition(prof, job["job"], "steward.meridian@example.com", True)
    assert certified["reviewed"][0]["badges"] == ["REVIEWED", "CERTIFIED", "RESTRICTED", "PII"]
    artifact = test_portal_metadata.artifact(prof)
    emit.write(artifact, prof.artifacts, prof.client)
    expected = unit_history.review_audit(prof, "retail", scope=f"metadata:{job['job']}")["retail.account"]
    assert history.release_audit(prof, artifact.manifest["model_hash"])["retail"] == {
        f"metadata:{job['job']}:retail.account": expected}
