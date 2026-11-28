"""Password merges follow the live access table and keep unchanged sign-ins working."""
import json
import stat

import pytest

from kge.pipeline.profile import Profile
from kge.services.identity import cli
from kge.services.identity.service import PASSWORDS, PLAIN, make_passwords, password_matches


def principal(name, process="querying"):
    return {"principal": name, "processes": [process]}


def hashes(folder):
    return json.loads((folder / PASSWORDS).read_text())


def plaintext(folder):
    return dict(line.split() for line in (folder / PLAIN).read_text().splitlines() if line and not line.startswith("#"))


def test_merge_keeps_unchanged_password_adds_one_and_removes_people_and_machines(tmp_path):
    make_passwords(tmp_path, [principal("ann@example.com"), principal("gone@example.com"), principal("bot@example.com")])
    previous = hashes(tmp_path)
    old = plaintext(tmp_path)
    result = make_passwords(tmp_path, [principal("ann@example.com"), principal("new@example.com", "certification"), principal("bot@example.com", "tagging")], previous)
    assert result == {"added": ["new@example.com"], "kept": ["ann@example.com"], "removed": ["bot@example.com", "gone@example.com"], "machines": ["bot@example.com"]}
    merged = hashes(tmp_path)
    assert set(merged) == {"ann@example.com", "new@example.com"}
    assert merged["ann@example.com"] == previous["ann@example.com"]
    assert password_matches(merged["ann@example.com"], old["ann@example.com"])
    assert not password_matches(merged.get("gone@example.com"), old["gone@example.com"])
    assert not password_matches(merged.get("bot@example.com"), old["bot@example.com"])
    new = plaintext(tmp_path)
    assert set(new) == {"new@example.com"}
    assert password_matches(merged["new@example.com"], new["new@example.com"])
    for name in (PASSWORDS, PLAIN):
        assert stat.S_IMODE((tmp_path / name).stat().st_mode) == 0o600


def test_unchanged_merge_has_no_new_plaintext_and_rotate_all_replaces_every_hash(tmp_path):
    principals = [principal("ann@example.com"), principal("sam@example.com", "certification")]
    make_passwords(tmp_path, principals)
    previous, old = hashes(tmp_path), plaintext(tmp_path)
    assert make_passwords(tmp_path, principals, previous)["kept"] == ["ann@example.com", "sam@example.com"]
    assert hashes(tmp_path) == previous
    assert plaintext(tmp_path) == {}
    result = make_passwords(tmp_path, principals, previous, rotate_all=True)
    assert result == {"added": ["ann@example.com", "sam@example.com"], "kept": [], "removed": [], "machines": []}
    rotated, new = hashes(tmp_path), plaintext(tmp_path)
    for name in previous:
        assert rotated[name] != previous[name]
        assert not password_matches(rotated[name], old[name])
        assert password_matches(rotated[name], new[name])


@pytest.fixture
def profile(tmp_path, monkeypatch):
    from kge.adapters.appstate import access_state
    monkeypatch.delenv("STORE_URL", raising=False)
    monkeypatch.delenv("ACCESS_ADMINS", raising=False)
    monkeypatch.setenv("APP_STATE_BACKEND", "files")
    prof = Profile(id="acme", client="acme", clients=tmp_path / "clients", artifacts=tmp_path / "artifacts",
                   store=tmp_path / "store", landing=tmp_path / "landing", warehouses=tmp_path / "warehouses",
                   identities=tmp_path / "identities.yaml", baseline=None, registry=tmp_path / "deploy/profiles.yaml")
    prof.run_dir().mkdir(parents=True)
    artifact = prof.artifacts / prof.client / "a1"
    (artifact / "access").mkdir(parents=True)
    (artifact / "access/roles.json").write_text(json.dumps({"roles": [{"id": "reader", "processes": ["querying"]},
                                                                   {"id": "bot", "processes": ["tagging"]}]}))
    (artifact.parent / "current").symlink_to(artifact.name)
    access_state(prof).access_put({"username": "ann@example.com", "name": "Ann", "roles": ["reader"]}, by="installer@example.com", via="job")
    monkeypatch.setattr("kge.pipeline.profile.resolve", lambda *_: prof)
    monkeypatch.setattr("sys.argv", ["identity", "--passwords"])
    return prof


def test_cli_refuses_missing_merge_base_and_changes_nothing(profile, capsys):
    run = profile.run_dir()
    (run / PLAIN).write_text("previous delivery file")
    assert cli.main() == 2
    assert "copy the secret's current version" in capsys.readouterr().err
    assert not (run / PASSWORDS).exists()
    assert (run / PLAIN).read_text() == "previous delivery file"


def test_cli_uses_table_without_roster_and_prints_no_password(profile, monkeypatch, capsys):
    monkeypatch.setattr("sys.argv", ["identity", "--passwords", "--rotate-all"])
    assert cli.main() == 0
    assert set(hashes(profile.run_dir())) == {"ann@example.com"}
    output = capsys.readouterr()
    assert all(value not in output.out + output.err for value in [*hashes(profile.run_dir()).values(), *plaintext(profile.run_dir()).values()])


def test_cli_merges_live_table_ignoring_release_roster(profile):
    from kge.adapters.appstate import access_state
    run = profile.run_dir()
    make_passwords(run, [principal("ann@example.com"), principal("departed@example.com")])
    earlier = hashes(run)
    state = access_state(profile)
    state.access_put({"username": "new@example.com", "name": "New", "roles": ["reader"]}, by="installer@example.com", via="job")
    release = profile.store / profile.client / "a1"
    release.mkdir()
    (release / "release.json").write_text(json.dumps({"roster": {"principals": [principal("departed@example.com")]}}))
    assert cli.main() == 0
    assert set(hashes(run)) == {"ann@example.com", "new@example.com"}
    assert hashes(run)["ann@example.com"] == earlier["ann@example.com"]
    assert set(plaintext(run)) == {"new@example.com"}
    state.access_delete("ann@example.com", by="installer@example.com", via="job")
    assert cli.main() == 0
    assert set(hashes(run)) == {"new@example.com"}
    assert plaintext(run) == {}


def test_passwords_include_stored_and_bootstrap_admins_but_not_machines(profile, monkeypatch):
    from kge.adapters.appstate import access_state
    state = access_state(profile)
    for name, role in [("operator@example.com", "access_admin"), ("machine@example.com", "bot")]:
        state.access_put({"username": name, "name": name, "roles": [role]}, by="installer@example.com", via="job")
    monkeypatch.setenv("ACCESS_ADMINS", "bootstrap@example.com")
    monkeypatch.setattr("sys.argv", ["identity", "--passwords", "--rotate-all"])
    assert cli.main() == 0
    assert set(hashes(profile.run_dir())) == {"ann@example.com", "operator@example.com", "bootstrap@example.com"}


def test_empty_table_removes_every_hash(profile):
    from kge.adapters.appstate import access_state
    access_state(profile).access_delete("ann@example.com", by="installer@example.com", via="job")
    make_passwords(profile.run_dir(), [principal("ann@example.com")])
    assert cli.main() == 0
    assert hashes(profile.run_dir()) == {} and plaintext(profile.run_dir()) == {}


@pytest.mark.parametrize("invalid", ["[]", "null", "{", '{"ann@example.com": null}'])
def test_cli_invalid_hash_file_refuses_without_replacing_either_file(profile, invalid):
    run = profile.run_dir()
    (run / PASSWORDS).write_text(invalid)
    (run / PLAIN).write_text("unchanged")
    assert cli.main() == 2
    assert (run / PASSWORDS).read_text() == invalid
    assert (run / PLAIN).read_text() == "unchanged"


def test_store_files_are_merge_base_and_receive_both_outputs(profile, tmp_path, monkeypatch):
    from kge.adapters.objstore import open_store
    from kge.pipeline import objstore
    root = tmp_path / "objects"
    store = open_store(str(root))
    monkeypatch.setenv("STORE_URL", str(root))
    from kge.adapters.appstate import access_state
    access_state(profile).access_put({"username": "ann@example.com", "name": "Ann", "roles": ["reader"]}, by="installer@example.com", via="job")
    make_passwords(profile.run_dir(), [principal("ann@example.com")])
    previous = hashes(profile.run_dir())
    store.put("secrets/acme/passwords.json", (profile.run_dir() / PASSWORDS).read_bytes())
    monkeypatch.setattr(objstore, "pull_config", lambda *_: profile)
    monkeypatch.setattr(objstore, "pull_work", lambda *_: None)
    monkeypatch.setattr(objstore, "resolve", lambda **_: profile)
    objstore.before("passwords", tmp_path)
    assert cli.main() == 0
    objstore.after("passwords", tmp_path)
    assert json.loads(store.get("secrets/acme/passwords.json")) == previous
    assert store.get("secrets/acme/passwords.txt") == (profile.run_dir() / PLAIN).read_bytes()
    assert plaintext(profile.run_dir()) == {}
