"""The new-client boundary and its reversible deployment entry."""
from pathlib import Path
import json
import shutil

import yaml

import pytest

from kge.estate import onboard

DEMO = Path(__file__).resolve().parents[2]


def scratch(monkeypatch, tmp_path):
    (tmp_path / "estate").mkdir()
    (tmp_path / "deploy").mkdir()
    shutil.copy(DEMO / "deploy/profiles.yaml", tmp_path / "deploy/profiles.yaml")
    shutil.copy(DEMO / "meta-model.yaml", tmp_path / "meta-model.yaml")
    monkeypatch.setattr(onboard.clients, "ROOT", tmp_path)
    monkeypatch.setattr(onboard.source, "ROOT", tmp_path)
    return tmp_path / "deploy/profiles.yaml"


def test_client_refuses_a_non_platform_principal(monkeypatch, tmp_path):
    scratch(monkeypatch, tmp_path)
    with pytest.raises(onboard.OnboardError, match="only the platform principal") as refused:
        onboard.client("newco", "New Co", ddl_file=DEMO / "examples/brackenridge/schema.sql", as_="business_team")
    assert refused.value.status == 403


def test_bad_profile_entry_rolls_registry_back_byte_for_byte(monkeypatch, tmp_path):
    registry = scratch(monkeypatch, tmp_path); before = registry.read_bytes()
    original = onboard.clients.read_profiles
    calls = 0
    def reject_second(path):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise onboard.clients.ProfileError("refused test entry")
        return original(path)
    monkeypatch.setattr(onboard.clients, "read_profiles", reject_second)
    with pytest.raises(onboard.clients.ProfileError, match="refused test entry"):
        onboard.client("newco", "New Co", ddl_file=DEMO / "examples/brackenridge/schema.sql", rows="invented", as_="platform")
    assert registry.read_bytes() == before and not (tmp_path / "clients/newco").exists()


def test_client_reset_round_trip_restores_registry_byte_for_byte(monkeypatch, tmp_path):
    registry = scratch(monkeypatch, tmp_path); before = registry.read_bytes()
    made = onboard.client("newco", "New Co", ddl_file=DEMO / "examples/brackenridge/schema.sql",
                          rows="invented", as_="platform")
    assert made["units"] == ["taproom", "cellar"] and registry.read_bytes() != before
    pack = yaml.safe_load((tmp_path / "clients/newco/pack.yaml").read_text())
    assert pack["manifest"]["meta_model_version"] == yaml.safe_load((DEMO / "meta-model.yaml").read_text())["version"]
    people = yaml.safe_load((tmp_path / "deploy/newco/identities.yaml").read_text())["principals"]
    assert {"principal": "knowledge_engineer", "role": "knowledge_engineer"} in people
    permissions = onboard.permissions(onboard.clients.resolve("newco", registry), "knowledge_engineer")
    assert permissions["draft"] and permissions["run"] and permissions["improve"]
    assert not any(permissions[step] for step in ("accept", "certify", "sign_off", "release"))
    onboard.reset_client("newco")
    assert registry.read_bytes() == before
    assert not (tmp_path / "clients/newco").exists() and not (tmp_path / "deploy/newco").exists()


def test_client_reset_round_trip_on_a_local_object_store(monkeypatch, tmp_path):
    registry = scratch(monkeypatch, tmp_path); before = registry.read_bytes()
    store = tmp_path / "objects"; (store / "deploy").mkdir(parents=True)
    (store / "deploy/profiles.yaml").write_bytes(before)
    monkeypatch.setenv("STORE_URL", str(store))
    onboard.client("newco", "New Co", ddl_file=DEMO / "examples/brackenridge/schema.sql",
                   rows="invented", as_="platform")
    assert (store / "clients/newco/pack.yaml").is_file()
    onboard.reset_client("newco")
    assert (store / "deploy/profiles.yaml").read_bytes() == before
    assert not (store / "clients/newco").exists() and not (store / "deploy/newco").exists()


@pytest.mark.parametrize("object_store", [False, True], ids=["local", "object-store"])
def test_reset_removes_only_its_own_entry_and_the_other_client_still_works(monkeypatch, tmp_path, object_store):
    registry = scratch(monkeypatch, tmp_path)
    store = tmp_path / "objects"
    if object_store:
        (store / "deploy").mkdir(parents=True)
        (store / "deploy/profiles.yaml").write_bytes(registry.read_bytes())
        monkeypatch.setenv("STORE_URL", str(store))
    kwargs = {"ddl_file": DEMO / "examples/brackenridge/schema.sql", "rows": "invented", "as_": "platform"}
    onboard.client("clienta", "Client A", **kwargs)
    onboard.client("clientb", "Client B", **kwargs)
    b_pack = (tmp_path / "clients/clientb/pack.yaml").read_bytes()
    b_ids = (tmp_path / "deploy/clientb/identities.yaml").read_bytes()
    onboard.reset_client("clienta")
    assert onboard.clients.resolve("clientb", registry).client == "clientb"
    assert (tmp_path / "clients/clientb/pack.yaml").read_bytes() == b_pack
    assert (tmp_path / "deploy/clientb/identities.yaml").read_bytes() == b_ids
    if object_store:
        assert (store / "clients/clientb/pack.yaml").read_bytes() == b_pack
        assert (store / "deploy/clientb/identities.yaml").read_bytes() == b_ids
    onboard.reset_client("clientb")


@pytest.mark.parametrize("object_store", [False, True], ids=["local", "object-store"])
def test_reset_refuses_a_hand_edited_entry_without_changing_anything(monkeypatch, tmp_path, object_store):
    registry = scratch(monkeypatch, tmp_path)
    store = tmp_path / "objects"
    if object_store:
        (store / "deploy").mkdir(parents=True)
        (store / "deploy/profiles.yaml").write_bytes(registry.read_bytes())
        monkeypatch.setenv("STORE_URL", str(store))
    onboard.client("newco", "New Co", ddl_file=DEMO / "examples/brackenridge/schema.sql",
                   rows="invented", as_="platform")
    edited = store / "deploy/profiles.yaml" if object_store else registry
    edited.write_text(edited.read_text().replace("    client: newco\n", "    client: changed\n"))
    before = {p.relative_to(tmp_path): p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    with pytest.raises(onboard.OnboardError, match="registry entry is no longer present verbatim; nothing changed"):
        onboard.reset_client("newco")
    after = {p.relative_to(tmp_path): p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    assert after == before


@pytest.mark.parametrize("client", ["meridian", "thornbury", "fellmoor"])
def test_reset_refuses_clients_not_made_by_client(monkeypatch, tmp_path, client):
    scratch(monkeypatch, tmp_path)
    with pytest.raises(onboard.OnboardError, match="was not made by make client"):
        onboard.reset_client(client)


def test_a_made_client_build_writes_its_own_golden_set_and_summary(monkeypatch, tmp_path):
    """A client make client made has no estate generator: onboarding's build writes its golden.json (v1, a list) and
    its summary, in the client's own estate."""
    registry = scratch(monkeypatch, tmp_path)
    onboard.client("newco", "New Co", ddl_file=DEMO / "examples/brackenridge/schema.sql", rows="invented", as_="platform")
    prof = onboard.clients.resolve("newco", registry)
    built = onboard.build(prof)["units"]
    assert built
    golden = json.loads((prof.estate() / "golden.json").read_text())
    assert isinstance(golden, list)
    assert [item["kind"] for item in golden].count("within") == 3
    cross = next(item for item in golden if item["kind"] == "cross")
    abstain = next(item for item in golden if item["kind"] == "abstain")
    assert cross["expect"] == {"behaviour": "refuse", "joins_refused": ["cellar-taproom"], "no_figure": True}
    assert abstain["expect"] == {"behaviour": "abstain", "no_figure": True}
    assert json.loads((prof.estate() / "summary.json").read_text()) == {"client": "newco", "units": built}
    assert prof.estate() == tmp_path / "build/newco"
    onboard.reset_client("newco")
    assert not (tmp_path / "build/newco").exists()


def test_a_one_unit_client_is_made_and_its_golden_minimum_is_the_abstention_alone(monkeypatch, tmp_path):
    """A client with one schema is still a client: with no second unit there is no pair to refuse, so its platform
    minimum is the other-currency abstention, with no cross question."""
    registry = scratch(monkeypatch, tmp_path)
    one = tmp_path / "taproom.sql"
    one.write_text((DEMO / "examples/brackenridge/schema.sql").read_text().split("CREATE SCHEMA cellar;", 1)[0])
    onboard.client("onetap", "One Tap", ddl_file=one, rows="invented", as_="platform")
    prof = onboard.clients.resolve("onetap", registry)
    assert onboard.build(prof)["units"] == ["taproom"]
    golden = json.loads((prof.estate() / "golden.json").read_text())
    assert [item["kind"] for item in golden if item["kind"] != "within"] == ["abstain"]
    onboard.reset_client("onetap")
    assert registry.read_bytes() == (DEMO / "deploy/profiles.yaml").read_bytes()


def test_no_join_is_a_draft_until_a_steward_certifies_it(monkeypatch, tmp_path):
    registry = scratch(monkeypatch, tmp_path)
    onboard.client("newco", "New Co", ddl_file=DEMO / "examples/brackenridge/schema.sql",
                   rows="invented", as_="platform")
    prof = onboard.clients.resolve("newco", registry)
    assert onboard._pack(prof)["no_join"][0]["asserted_by"] == "make draft"
    onboard.accept(prof, "cellar", "all", "business_team", on="2026-10-18")
    onboard.certify(prof, "cellar", "all", "steward", on="2026-10-19")
    fact = onboard._pack(prof)["no_join"][0]
    assert fact["asserted_by"] == "steward" and fact["asserted_on"] == "2026-10-19"


def test_make_client_without_a_source_prints_the_usage_line():
    """make client with neither DDL nor DB stops at its usage line, not a traceback from an empty path."""
    import subprocess
    import sys
    done = subprocess.run(["make", "-s", "client", "CLIENT=nosource", "LABEL=No Source", f"PY={sys.executable}"], cwd=DEMO,
                          capture_output=True, text=True)
    assert done.returncode != 0
    assert done.stderr.startswith("make client CLIENT=<id> LABEL=<label> DDL=<file>|DB=<file>")
    assert "Traceback" not in done.stderr
    assert not (DEMO / "clients/nosource").exists()
