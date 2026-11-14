"""Candidate and served permissions read live grants, never a compiled roster."""
import json

import pytest
import yaml

from kge.adapters import appstate
from kge.pipeline import access_table, roles
from kge.pipeline.profile import ProfileError
from tests.unit.test_access_table import prof as prof, ROLES, ADMIN


def test_candidate_and_served_templates_are_distinct_and_use_live_table(prof):
    access_table.grant(prof, "person@example.com", ["keeper"], "Person", by=ADMIN, via="api")
    candidate = prof.artifacts / prof.client / "b2" / "access"
    candidate.mkdir(parents=True)
    (candidate / "roles.json").write_text(json.dumps({"roles": [{"id": "keeper", "processes": ["querying"]}]}))
    assert roles.candidate_roles(prof, "b2") == {"keeper": {"id": "keeper", "processes": ["querying"]}}
    assert roles.served_roles(prof) == ROLES
    assert not roles.candidate_permissions(prof, "b2", "person@example.com")["sign_off"]
    assert roles.served_permissions(prof, "person@example.com")["sign_off"]
    access_table.revoke(prof, "person@example.com", by=ADMIN, via="api")
    assert not roles.served_permissions(prof, "person@example.com")["sign_off"]
    assert not roles.candidate_permissions(prof, "a1", "keeper")["sign_off"]


def test_roster_and_identities_do_not_authorize_or_block(prof):
    folder = prof.store / prof.client / "a1"
    folder.mkdir(parents=True)
    (folder / "ROSTER").write_text("broken")
    prof.identities.write_text("principals: [{principal: stranger, role: keeper}]")
    appstate.access_state(prof).access_put({"username": "person", "roles": ["keeper", "retired"]}, by=ADMIN, via="migration")
    assert not roles.candidate_permissions(prof, "a1", "stranger")["sign_off"]
    assert roles.candidate_permissions(prof, "a1", "person")["sign_off"]


@pytest.mark.parametrize("broken", ["not json", "[]", "{}", '{"roles": {}}', '{"roles": [{"id": "a"}, {"id": "a"}]}'])
def test_bad_artifact_is_refused(prof, broken):
    (prof.artifacts / prof.client / "a1/access/roles.json").write_text(broken)
    with pytest.raises(ProfileError, match="roles cannot be read"):
        roles.candidate_roles(prof, "a1")


def test_missing_artifact_is_refused_and_unreleased_profile_has_no_roles(prof):
    with pytest.raises(ProfileError, match="roles cannot be read"):
        roles.candidate_roles(prof, "b2")
    (prof.artifacts / prof.client / "current").unlink()
    assert access_table.served_roles(prof) == {}


def test_candidate_caller_never_seeds_or_resurrects_revoked_grants(prof):
    prof.identities.write_text("principals: [{principal: sam, role: keeper}]\n")
    assert not roles.candidate_permissions(prof, "a1", "sam")["sign_off"]
    access_table.seed(prof)
    assert roles.candidate_permissions(prof, "a1", "sam")["sign_off"]
    access_table.revoke(prof, "sam", by=ADMIN, via="job")
    assert not roles.candidate_permissions(prof, "a1", "sam")["sign_off"]
    assert appstate.access_state(prof).access_grants() == []


def test_permission_helpers_reuse_supplied_reader(prof, monkeypatch):
    access_table.grant(prof, "person@example.com", ["keeper"], "Person", by=ADMIN, via="api")
    state = appstate.access_state(prof)
    original = state.access_grants
    calls = []
    def rows():
        calls.append(1)
        return original()
    monkeypatch.setattr(state, "access_grants", rows)
    reader = access_table.Access(state)
    def unexpected(prof):
        raise AssertionError("shared read paths must not open another access state")
    monkeypatch.setattr(access_table, "access_state", unexpected)
    for _ in range(2):
        assert roles.candidate_permissions(prof, "a1", "person@example.com", reader=reader)["sign_off"]
        assert roles.served_permissions(prof, "person@example.com", reader=reader)["sign_off"]
    assert calls == [1]


def test_explicit_seed_can_validate_against_candidate_templates(prof):
    (prof.clients / prof.client / "pack.yaml").write_text("access: {roles: []}")
    prof.identities.write_text("asker: sam\nprincipals: [{principal: sam, role: keeper}, {principal: sam, role: reader}]")
    access_table.seed(prof, roles=roles.candidate_roles(prof, "a1"))
    assert roles.candidate_permissions(prof, "a1", "sam")["sign_off"]
    assert access_table.Access.for_profile(prof).roles_of("sam") == {"keeper", "reader"}


@pytest.mark.parametrize("remote", [False, True])
def test_first_release_roles_allow_grants_and_review_then_follow_served(prof, monkeypatch, tmp_path, remote):
    from kge.adapters.objstore import open_store
    from kge.estate.onboard import common
    import yaml
    (prof.artifacts / prof.client / "current").unlink()
    templates = {**ROLES, "business": {"id": "business", "processes": ["accepting"]}}
    payload = yaml.safe_dump({"access": {"roles": list(templates.values())}}).encode()
    if remote:
        monkeypatch.setenv("STORE_URL", str(tmp_path / "objects"))
        open_store(str(tmp_path / "objects")).put(f"clients/{prof.client}/pack.yaml", payload)
        (prof.clients / prof.client / "pack.yaml").write_text("invalid local copy")
    else:
        (prof.clients / prof.client / "pack.yaml").write_bytes(payload)
    who = "business.acme@example.com"
    access_table.grant(prof, who, ["business", "keeper"], "Business", by=ADMIN, via="job")
    assert common.permissions(prof, who)["accept"]
    assert common.permissions(prof, who)["certify"]
    assert common._acting_role(prof, who, "accept") == "business"
    assert not common.permissions(prof, "business")["accept"]
    for role in ("unknown", "access_admin"):
        with pytest.raises(ValueError, match="unknown role|reserved"):
            access_table.grant(prof, who, [role], "Business", by=ADMIN, via="api")
    (prof.artifacts / prof.client / "current").symlink_to("a1")
    assert not common.permissions(prof, who)["accept"]
    assert common.permissions(prof, who)["certify"]
    with pytest.raises(ValueError, match="unknown role"):
        access_table.grant(prof, who, ["business"], "Business", by=ADMIN, via="api")


@pytest.fixture(params=[False, True], ids=["local-pack", "stored-pack"])
def pack_files(prof, monkeypatch, tmp_path, request):
    from kge.adapters.objstore import open_store
    (prof.artifacts / prof.client / "current").unlink()
    if request.param:
        monkeypatch.setenv("STORE_URL", str(tmp_path / "objects"))
        store = open_store(str(tmp_path / "objects"))
        (prof.clients / prof.client / "pack.yaml").write_text("invalid local copy")
        return lambda name, value: store.put(f"clients/{prof.client}/{name}", yaml.safe_dump(value).encode())
    def write(name, value):
        path = prof.clients / prof.client / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(yaml.safe_dump(value))
    return write


def test_unreleased_roles_resolve_declared_access_include(prof, pack_files):
    from kge.estate.onboard import common
    pack_files("pack.yaml", {"manifest": {"includes": ["governance/access.yaml"]}})
    pack_files("governance/access.yaml", {"access": {"roles": list(ROLES.values())}})
    assert roles.authorization_roles(prof) == ROLES
    who = "steward.acme@example.com"
    access_table.grant(prof, who, ["keeper"], "Steward", by=ADMIN, via="job")
    assert common.permissions(prof, who)["certify"]
    assert not common.permissions(prof, "keeper")["certify"]


@pytest.mark.parametrize("main_access", [False, True])
def test_duplicate_access_sections_are_refused_before_release(prof, pack_files, main_access):
    access = {"access": {"roles": list(ROLES.values())}}
    pack = {"manifest": {"includes": ["access.yaml"]}}
    if main_access:
        pack.update(access)
    else:
        pack["manifest"]["includes"].append("other.yaml")
        pack_files("other.yaml", access)
    pack_files("pack.yaml", pack)
    pack_files("access.yaml", access)
    with pytest.raises(ProfileError, match="section is kept in one file"):
        roles.authorization_roles(prof)


@pytest.mark.parametrize("name", ["../access.yaml", "/access.yaml", "nested/../../access.yaml", "access.json", 1])
def test_unsafe_or_invalid_include_is_refused_before_release(prof, pack_files, name):
    pack_files("pack.yaml", {"manifest": {"includes": [name]}, "access": {"roles": list(ROLES.values())}})
    with pytest.raises(ProfileError, match="not an include"):
        roles.authorization_roles(prof)


def test_local_access_include_cannot_follow_a_link_outside_pack(prof, tmp_path):
    (prof.artifacts / prof.client / "current").unlink()
    outside = tmp_path / "outside.yaml"
    outside.write_text(yaml.safe_dump({"access": {"roles": list(ROLES.values())}}))
    folder = prof.clients / prof.client
    (folder / "pack.yaml").write_text("manifest: {includes: [access.yaml]}")
    (folder / "access.yaml").symlink_to(outside)
    with pytest.raises(ProfileError, match="outside the client's folder"):
        roles.authorization_roles(prof)
