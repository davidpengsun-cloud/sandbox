"""Job commands preserve operator bootstrap, administrator checks and migration history."""
import json
from types import SimpleNamespace

import pytest
import yaml

from kge.adapters import appstate
from kge.adapters.objstore import open_store
from kge.pipeline import access_table
from kge.services.access import cli
from tests.unit.test_access_table import ADMIN, prof as prof


@pytest.fixture
def command(prof, monkeypatch, capsys):
    monkeypatch.setattr(cli, "resolve", lambda profile_id: prof)
    for key in cli.OPTIONS:
        monkeypatch.delenv(key, raising=False)

    def call(*options, code=0):
        assert cli.main(list(options)) == code
        output = capsys.readouterr()
        return (json.loads(output.out) if output.out else None), output.err
    return call


def test_grant_list_audit_revoke_and_environment_inputs(prof, command, monkeypatch):
    monkeypatch.setenv("GRANT", "person@example.com:reader,keeper")
    monkeypatch.setenv("BY", ADMIN)
    monkeypatch.setenv("NAME", "Person")
    entry, _ = command()
    assert entry["by"] == ADMIN and entry["via"] == "job" and entry["after"]["name"] == "Person"
    monkeypatch.delenv("GRANT")
    listing, _ = command("LIST=yes")
    assert listing["people"][0]["roles"] == ["keeper", "reader"] and not listing["changed_outside"]
    audit, _ = command("AUDIT=yes")
    assert audit["entries"][0]["by"] == ADMIN
    deleted, _ = command("REVOKE=person@example.com")
    assert deleted["after"] is None and deleted["via"] == "job"
    assert not access_table.Access.for_profile(prof).roles_of("person@example.com")


@pytest.mark.parametrize("options", [("GRANT=p@example.com:reader",), ("REVOKE=p@example.com",),
    ("MIGRATE=yes",), ("ADMINS=op@example.com",), ("ADMINS=",)])
def test_every_write_requires_by(prof, command, options):
    _, error = command(*options, code=2)
    assert "BY=<email>" in error
    assert appstate.access_state(prof).access_audit() == []


@pytest.mark.parametrize("operation", ["GRANT=p@example.com:reader", "REVOKE=p@example.com"])
def test_non_admin_job_actor_cannot_change_access(prof, command, operation):
    _, error = command(operation, "BY=stranger@example.com", code=2)
    assert "not an admin" in error
    assert appstate.access_state(prof).access_audit() == []


@pytest.mark.parametrize("option", ["GRANT=p@example.com:access_admin", "GRANT=p@example.com:unknown",
    "GRANT=p@example.com:", "GRANT=bad", "REVOKE=", "LIST=no", "MIGRATE=no", "UNKNOWN=yes"])
def test_invalid_options_write_nothing(prof, command, option):
    command(option, f"BY={ADMIN}", code=2)
    assert appstate.access_state(prof).access_audit() == []


def test_ambiguous_or_missing_operations_refuse(prof, command):
    command(code=2)
    command("LIST=yes", "GRANT=p@example.com:reader", f"BY={ADMIN}", code=2)
    assert appstate.access_state(prof).access_audit() == []


def test_operator_sets_admins_without_being_one_and_can_clear_them(prof, command):
    result, _ = command("ADMINS=op@example.com,second@example.com", "BY=operator@example.com")
    assert all(row["via"] == "job" and row["by"] == "operator@example.com" for row in result["entries"])
    command("GRANT=p@example.com:reader", "BY=op@example.com")
    command("ADMINS=", "BY=operator@example.com")
    assert access_table.admins(prof) == {ADMIN, "bootstrap@example.com"}
    assert access_table.Access.for_profile(prof).roles_of("p@example.com") == {"reader"}


def seed(prof, monkeypatch, remote):
    document = {"default": prof.id, "profiles": {prof.id: {
        "client": prof.client, "clients": "../clients", "artifacts": "../build/artifacts",
        "store": "../build/stores", "landing": "../build/landing", "warehouses": "../build/acme/warehouses",
        "identities": "acme/identities.yaml", "baseline": "../clients/acme/baselines/held.json"}}}
    identities = {"principals": [{"principal": "ann", "role": "reader"},
                                {"principal": "sam", "role": "keeper"},
                                {"principal": "old_admin", "role": "admin"}], "asker": "ann"}
    if remote:
        url = str(prof.registry.parent / "remote")
        monkeypatch.setenv("STORE_URL", url)
        store = open_store(url)
        store.put(cli.REGISTRY, yaml.safe_dump(document).encode())
        store.put(f"deploy/{prof.client}/identities.yaml", yaml.safe_dump(identities).encode())
        store.put(f"clients/{prof.client}/pack.yaml", (prof.clients / prof.client / "pack.yaml").read_bytes())
        return store, cli.REGISTRY
    prof.registry.write_text(yaml.safe_dump(document))
    prof.identities.write_text(yaml.safe_dump(identities))
    return open_store(str(prof.registry.parent)), prof.registry.name


@pytest.mark.parametrize("remote", [False, True])
def test_migration_maps_emails_keeps_ids_sets_asker_and_refuses_repeat(prof, command, monkeypatch, remote):
    store, key = seed(prof, monkeypatch, remote)
    result, error = command("MIGRATE=yes", "EMAILS=ann=ann@example.com", f"BY={ADMIN}")
    assert result["migrated"] and result["rows"] == 3 and result["asker"] == "reader"
    assert not error
    assert result["written"] and result["missing_emails"] == ["old_admin", "sam"]
    held = {row["username"]: row for row in access_table.Access.for_profile(prof).grants()}
    assert held["ann@example.com"]["name"] == "ann" and held["sam"]["roles"] == ["keeper"]
    entries = appstate.access_state(prof).access_audit()
    assert all(row["by"] == ADMIN and row["via"] == "migration" for row in entries)
    entry = yaml.safe_load(store.get(key))["profiles"][prof.id]
    assert entry["asker"] == "reader" and entry["baseline"].endswith("held.json")
    result, _ = command("MIGRATE=yes", f"BY={ADMIN}")
    assert not result["migrated"] and len(appstate.access_state(prof).access_audit()) == 3


@pytest.mark.parametrize("emails", ["ann=bad", "missing=m@example.com", "ann=a@example.com ann=b@example.com"])
def test_invalid_migration_writes_nothing(prof, command, monkeypatch, emails):
    store, key = seed(prof, monkeypatch, False)
    before = store.get(key)
    command("MIGRATE=yes", f"EMAILS={emails}", f"BY={ADMIN}", code=2)
    assert appstate.access_state(prof).access_audit() == [] and store.get(key) == before


def test_operator_migrates_before_bootstrapping_stored_admins(prof, command, monkeypatch):
    monkeypatch.delenv("ACCESS_ADMINS")
    seed(prof, monkeypatch, False)
    result, _ = command("MIGRATE=yes", "BY=operator@example.com")
    assert result["migrated"] and result["rows"] == 3
    command("ADMINS=operator@example.com", "BY=operator@example.com")
    command("GRANT=p@example.com:reader", "BY=operator@example.com")
    assert access_table.Access.for_profile(prof).roles_of("ann") == {"reader"}


def test_admins_environment_contract(prof, command, monkeypatch):
    original = cli.settings
    monkeypatch.setattr(cli, "settings", lambda: SimpleNamespace(**{**vars(original()), "ADMINS": "op@example.com"}))
    monkeypatch.setenv("BY", "operator@example.com")
    result, _ = command()
    assert result["entries"][0]["after"]["roles"] == ["access_admin"]


def test_failed_registry_write_names_recovery_after_committed_migration(prof, command, monkeypatch):
    store, key = seed(prof, monkeypatch, False)
    before = store.get(key)

    def fail(*args):
        raise cli.StoreError("registry changed")

    monkeypatch.setattr(store, "put_version", fail)
    monkeypatch.setattr(cli, "open_store", lambda url: store)
    _, error = command("MIGRATE=yes", f"BY={ADMIN}", code=2)
    assert "access rows migrated" in error and "setup-client CLIENT=acme ASKER=reader" in error
    assert len(access_table.Access.for_profile(prof).grants()) == 3 and store.get(key) == before


@pytest.mark.parametrize("operation", ["GRANT=p@example.com:reader", "REVOKE=p@example.com", "MIGRATE=yes",
                                       "ADMINS=op@example.com"])
@pytest.mark.parametrize("by", ["admin@example.com\n", "\tadmin@example.com", "admin\u200b@example.com"])
def test_actor_controls_are_refused_before_every_write(prof, command, operation, by):
    _, error = command(operation, f"BY={by}", code=2)
    assert "BY=<email>" in error
    state = appstate.access_state(prof)
    assert state.access_grants() == [] and state.access_audit() == []


def test_actor_uses_shared_unicode_normalization(prof, command):
    result, error = command("GRANT=p@example.com:reader", "BY= ＡＤＭＩＮ@EXAMPLE.COM ")
    assert not error and result["by"] == ADMIN



def test_list_and_audit_work_before_first_release(prof, command):
    (prof.artifacts / prof.client / "current").unlink()
    command("ADMINS=operator@example.com", "BY=operator@example.com")
    listing, _ = command("LIST=yes")
    audit, _ = command("AUDIT=yes")
    assert {role["id"] for role in listing["roles"]} == {"reader", "keeper", "admin"}
    assert "operator@example.com" in listing["admins"]
    assert audit["entries"][0]["via"] == "job"
