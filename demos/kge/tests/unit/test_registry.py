"""The registry holds asker roles and baselines without changing access or seed identities."""
import copy
import os
from types import SimpleNamespace

import pytest
import yaml

from kge.adapters.objstore import open_store
from kge.pipeline import registry as people
from kge.pipeline.objstore import Refused
from kge.pipeline import profile

PACK = {"access": {"roles": [{"id": "reader", "processes": ["querying"]},
                              {"id": "keeper", "processes": ["certification"]},
                              {"id": "machine", "processes": ["tagging"]}]}}


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("STORE_URL", str(tmp_path / "store"))
    monkeypatch.setenv("CLIENT", "acme")
    monkeypatch.setenv("PEOPLE", "ann:reader sam:keeper")
    for name in ("ASKER", "BASELINE", "PROFILE"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(people, "settings", lambda: SimpleNamespace(**{
        name: os.environ.get(name) for name in ("CLIENT", "PEOPLE", "ASKER", "BASELINE", "PROFILE")}))
    store = open_store(str(tmp_path / "store"))
    store.put("clients/acme/pack.yaml", yaml.safe_dump(PACK).encode())
    return store


def snapshot(store):
    return {key: store.get(key) for key in store.list("")}


def test_setup_defaults_to_first_querying_role_and_never_writes_identities(store):
    people.setup_client()
    entry = yaml.safe_load(store.get(people.REGISTRY))["profiles"]["acme"]
    assert entry["asker"] == "reader"
    assert "identities" not in entry
    assert store.list("") == ["clients/acme/pack.yaml", people.REGISTRY]


@pytest.mark.parametrize("asker", ["missing", "keeper", "bad role", "admin"])
def test_setup_refuses_non_querying_asker_without_writes(store, monkeypatch, asker):
    before = snapshot(store)
    monkeypatch.setenv("ASKER", asker)
    with pytest.raises(Refused, match="ASKER"):
        people.setup_client()
    assert snapshot(store) == before


def test_setup_keeps_omitted_asker_and_baseline_and_sets_or_clears_explicit_values(store, monkeypatch):
    monkeypatch.setenv("ASKER", "reader")
    monkeypatch.setenv("BASELINE", "baselines/first.json")
    people.setup_client()
    before = snapshot(store)
    monkeypatch.delenv("ASKER")
    monkeypatch.delenv("BASELINE")
    people.setup_client()
    assert snapshot(store) == before
    monkeypatch.setenv("ASKER", "")
    monkeypatch.setenv("BASELINE", "")
    people.setup_client()
    entry = yaml.safe_load(store.get(people.REGISTRY))["profiles"]["acme"]
    assert entry["asker"] is None and entry["baseline"] is None
    monkeypatch.setenv("ASKER", "reader")
    monkeypatch.setenv("BASELINE", "baselines/second.json")
    people.setup_client()
    entry = yaml.safe_load(store.get(people.REGISTRY))["profiles"]["acme"]
    assert entry["asker"] == "reader"
    assert entry["baseline"] == "../clients/acme/baselines/second.json"


def test_preserved_asker_is_checked_against_pack(store, monkeypatch):
    people.setup_client()
    store.put("clients/acme/pack.yaml", yaml.safe_dump({"access": {"roles": []}}).encode())
    before = snapshot(store)
    with pytest.raises(Refused, match="ASKER"):
        people.setup_client()
    assert snapshot(store) == before


def test_people_setting_is_not_consumed_and_seed_is_untouched(store, monkeypatch):
    store.put("deploy/acme/identities.yaml", b"existing seed")
    monkeypatch.setenv("PEOPLE", "invalid")
    people.setup_client()
    assert store.get("deploy/acme/identities.yaml") == b"existing seed"


def test_no_pack_reports_that_roles_were_not_checked(store, capsys, monkeypatch):
    store.delete("clients/acme/pack.yaml")
    monkeypatch.setenv("ASKER", "reader")
    people.setup_client()
    assert "no pack in the store yet: roles not checked" in capsys.readouterr().out
    assert yaml.safe_load(store.get(people.REGISTRY))["profiles"]["acme"]["asker"] == "reader"


def test_set_baseline_changes_only_selected_profile(store, monkeypatch):
    monkeypatch.setenv("ASKER", "reader")
    people.setup_client()
    monkeypatch.setenv("CLIENT", "other")
    people.setup_client()
    before = snapshot(store)
    registry = yaml.safe_load(before[people.REGISTRY])
    expected = copy.deepcopy(registry)
    expected["profiles"]["acme"]["baseline"] = "../clients/acme/baselines/new.json"
    monkeypatch.setenv("PROFILE", "acme")
    people.set_baseline("baselines/new.json")
    after = snapshot(store)
    assert yaml.safe_load(after.pop(people.REGISTRY)) == expected
    before.pop(people.REGISTRY)
    assert after == before
    people.set_baseline("")
    assert yaml.safe_load(store.get(people.REGISTRY))["profiles"]["acme"]["baseline"] is None


@pytest.mark.parametrize("value", ["../other/baseline.json", "/tmp/baseline.json"])
def test_baseline_refuses_paths_outside_client(store, monkeypatch, value):
    before = snapshot(store)
    monkeypatch.setenv("BASELINE", value)
    with pytest.raises(Refused, match="BASELINE"):
        people.setup_client()
    assert snapshot(store) == before


def test_command_refusal_returns_two(store, monkeypatch, capsys):
    monkeypatch.setenv("ASKER", "missing")
    monkeypatch.setattr("sys.argv", ["registry", "setup-client"])
    before = snapshot(store)
    assert people.main() == 2
    assert "ASKER" in capsys.readouterr().err
    assert snapshot(store) == before


def test_written_registry_is_accepted_by_profile_reader(store, tmp_path):
    people.setup_client()
    raw = store.get(people.REGISTRY)
    path = tmp_path / "written" / "profiles.yaml"
    path.parent.mkdir()
    path.write_bytes(raw)
    default, held = profile.read(path)
    assert default == "acme" and held[default].client == "acme"


@pytest.mark.parametrize("explicit", [True, False])
def test_querying_pack_admin_is_an_ordinary_asker(store, monkeypatch, explicit):
    store.put("clients/acme/pack.yaml", b"access: {roles: [{id: admin, processes: [querying]}]}")
    if explicit:
        monkeypatch.setenv("ASKER", "admin")
    people.setup_client()
    assert yaml.safe_load(store.get(people.REGISTRY))["profiles"]["acme"]["asker"] == "admin"


def test_setup_refuses_reserved_pack_role_before_writes(store):
    store.put("clients/acme/pack.yaml", b"access: {roles: [{id: access_admin, processes: [querying]}]}")
    before = snapshot(store)
    with pytest.raises(Refused, match="access_admin is reserved"):
        people.setup_client()
    assert snapshot(store) == before
