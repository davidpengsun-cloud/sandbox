"""Release ids keep the original bytes, inputs while access stays live even when the compiled model does not change."""
from __future__ import annotations

import hashlib
import json
import re
import shutil
from types import SimpleNamespace

import pytest
import yaml

from kge import packstore
from kge.adapters import appstate, released
from kge.adapters.objstore import open_store
from kge.graph import history, load, release
from kge.pipeline import unit_history
from kge.pipeline.objstore import release_paths, tarball
from tests.unit.test_release_record_unpack import profile

HASH = "12" * 32


@pytest.fixture
def client(tmp_path, monkeypatch):
    for name, value in {"GRAPH_STORE": "ladybug", "WAREHOUSE_BACKEND": "duckdb", "RETRIEVAL_BACKEND": "graph",
                        "APP_STATE_BACKEND": "files", "MODEL_PROVIDER": "none"}.items():
        monkeypatch.setenv(name, value)
    monkeypatch.delenv("STORE_URL", raising=False)
    prof = profile(tmp_path / "app")
    pack = prof.clients / prof.client
    pack.mkdir(parents=True)
    artifact = prof.artifacts / prof.client / HASH
    artifact.mkdir(parents=True)
    (artifact / "manifest.json").write_text("{}")
    monkeypatch.setattr(load, "read_artifact", lambda folder, client: {"model_hash": folder.name})
    monkeypatch.setattr(unit_history, "record", lambda *args, **kwargs: None)
    graph = prof.at(HASH).graph_path()
    graph.parent.mkdir(parents=True)
    prof.run_dir().mkdir(parents=True)
    prof.identities.parent.mkdir(parents=True, exist_ok=True)
    prof.estate().mkdir(parents=True, exist_ok=True)

    def stage(version):
        data = {"manifest": {"includes": ["glossary.yaml"], "inputs": {
            "estate": {"fixture": "source"}, "catalogue": "catalogue.yaml", "sets": ["golden"]}}}
        (pack / "pack.yaml").write_text(yaml.safe_dump(data))
        (pack / "pack.lock").write_text(f"lock {version}")
        (pack / "glossary.yaml").write_text(f"glossary {version}")
        (pack / "catalogue.yaml").write_text(f"catalogue {version}")
        source = pack / "source"
        source.mkdir(exist_ok=True)
        (source / "schema.yaml").write_text("warehouses: {}\n")
        (source / "golden.json").write_text(json.dumps([{"id": version}]))
        unit = pack / "units" / "branch"
        unit.mkdir(parents=True, exist_ok=True)
        (unit / "unit.yaml").write_text(f"version: {version}\n")
        graph.write_bytes(f"graph bytes {version}".encode())
        bound = [{"principal": f"person{version}", "role": "steward"}]
        roster = {"bindings": bound, "asker": f"person{version}", "principals": [{"id": f"person{version}"}]}
        prof.identities.write_text(yaml.safe_dump({"principals": bound, "asker": roster["asker"]}))
        (prof.run_dir() / "principals.json").write_text(json.dumps(roster["principals"]))
        fixture = hashlib.sha256()
        for path in sorted(source.iterdir()):
            fixture.update(path.name.encode() + b"\0" + path.read_bytes() + b"\0")
        names = ["pack.yaml", "glossary.yaml", "catalogue.yaml", "source/schema.yaml", "source/golden.json"]
        inputs = {"model_hash": HASH, "declared": True,
                  "files": {name: hashlib.sha256((pack / name).read_bytes()).hexdigest() for name in names},
                  "inputs_sha256": "recorded", "fixture_sha256": fixture.hexdigest(), "upload_sha256": None,
                  "warehouses": {}}
        (graph.parent / release.INPUTS).write_text(json.dumps(inputs))
        (prof.estate() / "summary.json").write_text(json.dumps({"version": version, "fixture_sha256": fixture.hexdigest()}))
        for name in ("documents.json", "golden.json", "heldout.json"):
            (prof.estate() / name).write_text(json.dumps([version]))
        release.put(graph, release.LOADED, {"client": prof.client, "model_hash": HASH, "warehouses": {}})
        release.put(graph, release.GATE, {"model_hash": HASH, "passed": True, "run_id": str(version)})
        release.put(graph, release.SIGNOFF, {"model_hash": HASH, "principal": roster["asker"], "run_id": str(version)})
        return inputs

    return SimpleNamespace(prof=prof, pack=pack, graph=graph, stage=stage, app=tmp_path / "app")


@pytest.mark.parametrize("stored", [False, True], ids=["local", "object-store"])
def test_same_hash_restores_bytes_inputs_and_ledger_but_preserves_access(client, monkeypatch, tmp_path, stored):
    c = client
    if stored:
        monkeypatch.setenv("STORE_URL", str(tmp_path / "objects"))
    original = None
    archives = {}
    for version in (1, 2):
        c.stage(version)
        if stored:
            store = packstore.for_profile(c.prof)
            for path in c.pack.rglob("*"):
                if path.is_file() and "versions" not in path.relative_to(c.pack).parts:
                    store.write(path.relative_to(c.pack).as_posix(), path.read_bytes())
        release.release(c.prof, HASH)
        entry = release.kept(c.prof)[0]
        assert re.fullmatch(r"\d{8}T\d{6}Z-[0-9a-f]{6}", entry["identity"]["release"])
        assert entry["tarball"] == f"releases/{c.prof.client}/{HASH}/{entry['identity']['release']}.tar.gz"
        if stored:
            data = tarball(c.app, release_paths(c.prof, HASH))
            open_store(str(tmp_path / "objects")).put(entry["tarball"], data)
            release.finish(c.prof)
        else:
            data = (c.prof.store / c.prof.client / "releases" / HASH / (entry["identity"]["release"] + ".tar.gz")).read_bytes()
        archives[entry["tarball"]] = data
        if version == 1:
            original = {"entry": entry, "runtime": history.capture_runtime(c.prof, HASH),
                        "pack": {p.relative_to(c.pack).as_posix(): p.read_bytes() for p in c.pack.rglob("*")
                                 if p.is_file() and "versions" not in p.relative_to(c.pack).parts}}
    second = c.prof.serve().identity()
    identities = c.prof.identities.read_bytes()
    state = appstate.access_state(c.prof)
    state.access_put({"username": "present@example.com", "roles": ["steward"]}, by="operator@example.com", via="job")
    grants, audit = state.access_grants(), state.access_audit()
    (c.pack / "source" / "extra.json").write_text("later")
    (c.prof.run_dir() / "passwords.json").write_text("private, untouched")
    release.release(c.prof, previous=True)
    assert c.prof.serve().identity() == original["entry"]["identity"]
    assert release.served(c.prof.store / c.prof.client) == [original["entry"]["identity"], second]
    assert [e["identity"] for e in release.kept(c.prof)] == release.served(c.prof.store / c.prof.client)
    history_rows = history.read_jsonl(history.versions_path(c.prof))
    assert [row["release"] for row in history_rows] == [original["entry"]["identity"]["release"],
                                                       second["release"], original["entry"]["identity"]["release"]]
    assert history_rows[-1]["note"].startswith("rollback to release ")
    for name, data in original["pack"].items():
        assert (c.pack / name).read_bytes() == data, name
    assert not (c.pack / "source" / "extra.json").exists()
    for name in ("graph/graph.ladybug", "estate/documents.json", "estate/golden.json", "estate/heldout.json",
                 "estate/summary.json", "run/principals.json"):
        assert history.capture_runtime(c.prof, HASH)[name] == original["runtime"][name]
    assert c.prof.identities.read_bytes() == identities
    assert state.access_grants() == grants and state.access_audit() == audit
    assert "roster" not in original["entry"]
    assert "roster" not in released.read(c.graph.parent)
    snapshot = history._release_snapshot(c.prof, original["entry"])
    assert "ROSTER" not in snapshot
    assert "roster" not in json.loads(snapshot[release.INPUTS])
    assert (c.prof.run_dir() / "passwords.json").read_text() == "private, untouched"
    report = json.loads((c.prof.run_dir() / "rollback.json").read_text())
    assert "people" not in report
    assert report["fixture"] == {"sha256": original["entry"]["inputs"]["fixture_sha256"], "kept": True}
    summary = json.loads((c.prof.estate() / "summary.json").read_text())
    assert summary["version"] == 1 and summary["fixture_sha256"] == report["fixture"]["sha256"]
    assert all(set(item) == {"file", "key"} for item in report["restored"])
    for key, data in archives.items():
        held = open_store(str(tmp_path / "objects")).get(key) if stored else (
            c.prof.store / c.prof.client / "releases" / HASH / key.rsplit("/", 1)[1]).read_bytes()
        assert held == data


@pytest.mark.parametrize("name", ["pack.yaml", "catalogue.yaml", "glossary.yaml", "source/golden.json", "source/added.json"])
def test_moved_input_refuses_before_any_release_write(client, name):
    c = client
    c.stage(1)
    release.release(c.prof, HASH)
    before = history.capture_runtime(c.prof, HASH)
    ledger = (c.prof.store / c.prof.client / release.KEPT).read_bytes()
    (c.pack / name).write_text("manifest: {}\n" if name == "pack.yaml" else "moved")
    with pytest.raises(release.Unreleasable, match="the stored inputs moved after .*: onboard again"):
        release.release(c.prof, HASH)
    assert history.capture_runtime(c.prof, HASH) == before
    assert (c.prof.store / c.prof.client / release.KEPT).read_bytes() == ledger


@pytest.mark.parametrize("name", ["catalogue.yaml", "glossary.yaml", "pack.yaml", "pack.lock"])
def test_load_without_inputs_keeps_legacy_release_checks(client, name):
    """A legacy load has no input baseline, even when its pack now declares inputs."""
    c = client
    c.stage(1)
    (c.graph.parent / release.INPUTS).unlink()
    manifest = c.prof.artifacts / c.prof.client / HASH / "manifest.json"
    manifest.write_text(json.dumps({"pack": {"hash": "earlier-pack"},
                                    "lock_hash": hashlib.sha256((c.pack / "pack.lock").read_bytes()).hexdigest()}))
    if name in {"catalogue.yaml", "glossary.yaml"}:
        (c.pack / name).unlink()
    else:
        (c.pack / name).write_text("manifest: {}\n" if name == "pack.yaml" else "later lock")
    assert release.moved_inputs(c.prof, HASH) == []
    release.release(c.prof, HASH)
    assert c.prof.model_hash() == HASH
    entry = release.kept(c.prof)[0]
    assert entry["inputs"] is None
    snapshot = history.snapshot_store(c.prof)
    assert json.loads(snapshot.read(entry["snapshot"] + release.INPUTS)) is None
    if name in {"catalogue.yaml", "glossary.yaml"}:
        assert not snapshot.exists(entry["snapshot"] + name)


@pytest.mark.parametrize("part", ["tarball", "pack.yaml", "source/golden.json"])
def test_pruned_release_refuses_before_restoring_anything(client, part):
    c = client
    for version in (1, 2):
        c.stage(version)
        release.release(c.prof, HASH)
    entry = release.kept(c.prof)[1]
    if part == "tarball":
        (c.prof.store / c.prof.client / "releases" / HASH / (entry["identity"]["release"] + ".tar.gz")).unlink()
    else:
        (c.prof.artifacts / c.prof.client / entry["snapshot"] / part).unlink()
    before = history.capture_runtime(c.prof, HASH)
    identity = c.prof.serve().identity()
    with pytest.raises(release.Unreleasable, match="the release tarball" if part == "tarball" else "the pack snapshot"):
        release.release(c.prof, previous=True)
    assert c.prof.serve().identity() == identity and history.capture_runtime(c.prof, HASH) == before
    assert yaml.safe_load(c.prof.identities.read_text())["asker"] == "person2"


@pytest.mark.parametrize("stored", [False, True], ids=["local", "object-store"])
def test_three_releases_prune_only_the_oldest_snapshot_and_archive(client, monkeypatch, tmp_path, stored):
    c = client
    if stored:
        monkeypatch.setenv("STORE_URL", str(tmp_path / "objects"))
    store = packstore.for_profile(c.prof) if stored else packstore.Store(c.prof.artifacts / c.prof.client)
    store.write(f"versions/{HASH}/obsolete.yaml", b"a legacy snapshot no release names")
    entries = []
    for version in (1, 2, 3):
        c.stage(version)
        if stored:
            for path in c.pack.rglob("*"):
                if path.is_file() and "versions" not in path.relative_to(c.pack).parts:
                    store.write(path.relative_to(c.pack).as_posix(), path.read_bytes())
        release.release(c.prof, HASH)
        entries.append(release.kept(c.prof)[0])
    if stored:
        assert store.exists(f"versions/{HASH}/obsolete.yaml")
        release.finish(c.prof)
    assert not store.exists(f"versions/{HASH}/obsolete.yaml")
    assert store.list(entries[0]["snapshot"]) == []
    assert all(store.exists(entry["snapshot"] + "snapshot.json") for entry in entries[1:])
    if not stored:
        archives = list((c.prof.store / c.prof.client / "releases" / HASH).glob("*.tar.gz"))
        assert {p.name for p in archives} == {entry["identity"]["release"] + ".tar.gz" for entry in entries[1:]}


def test_local_release_and_retire_leave_the_pack_folder_untouched(client):
    c = client
    sentinel = c.pack / "versions" / HASH / "people-owned.txt"
    sentinel.parent.mkdir(parents=True)
    sentinel.write_bytes(b"a file in the people's folder is not a local release snapshot")
    for version in (1, 2, 3):
        c.stage(version)
        before = {p.relative_to(c.pack): p.read_bytes() for p in c.pack.rglob("*") if p.is_file()}
        release.release(c.prof, HASH)
        assert {p.relative_to(c.pack): p.read_bytes() for p in c.pack.rglob("*") if p.is_file()} == before
        entry = release.kept(c.prof)[0]
        assert entry["snapshot"] == f"versions/{HASH}/{entry['identity']['release']}/"
        assert (c.prof.artifacts / c.prof.client / entry["snapshot"] / "snapshot.json").is_file()


def test_record_with_a_release_id_is_written_even_without_remote_pointers(tmp_path):
    audit = {"unit": {"item": {"accepted": {"by": "person@example.com", "as": "business", "on": "2026-10-31"}}}}
    released.write(tmp_path, HASH, None, None, release="20261031T000000Z-123abc", inputs={"files": {}}, audit=audit)
    assert released.read(tmp_path) == {"hash": HASH, "graph": None, "chunks": None, "warehouses": {},
                                       "release": "20261031T000000Z-123abc", "inputs": {"files": {}}, "audit": audit}


def test_signoff_uses_live_grants_and_candidate_roles(client):
    c = client
    c.stage(1)
    path = c.prof.artifacts / c.prof.client / HASH / "access/roles.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"roles": [{"id": "steward", "processes": ["certification"]}]}))
    c.prof.identities.write_text("principals: [{principal: obsolete, role: steward}]\n")
    state = appstate.access_state(c.prof)
    state.access_put({"username": "replacement@example.com", "roles": ["steward"]}, by="operator@example.com", via="job")
    assert release.sign_off(c.prof, HASH, "replacement@example.com")["principal"] == "replacement@example.com"
    for who in ("person1", "obsolete", "steward"):
        with pytest.raises(release.Unreleasable, match="is no principal"):
            release.sign_off(c.prof, HASH, who)
    path.write_text(json.dumps({"roles": [{"id": "steward", "processes": []}]}))
    with pytest.raises(release.Unreleasable, match="may not perform certification"):
        release.sign_off(c.prof, HASH, "replacement@example.com")
    path.write_text(json.dumps({"roles": [{"id": "steward", "processes": ["certification"]}]}))
    state.access_delete("replacement@example.com", by="operator@example.com", via="job")
    with pytest.raises(release.Unreleasable, match="is no principal"):
        release.sign_off(c.prof, HASH, "replacement@example.com")


def test_swap_failure_puts_back_inputs_people_runtime_and_ledger(client, monkeypatch):
    c = client
    for version in (1, 2):
        c.stage(version)
        release.release(c.prof, HASH)
    before = history.capture_runtime(c.prof, HASH)
    pack = {p.relative_to(c.pack): p.read_bytes() for p in c.pack.rglob("*") if p.is_file()}
    people = c.prof.identities.read_bytes()
    ledgers = {name: (c.prof.store / c.prof.client / name).read_bytes() for name in (release.SERVED, release.KEPT)}
    def fail(*args):
        raise OSError("injected swap failure")
    monkeypatch.setattr(release, "swap", fail)
    with pytest.raises(OSError, match="injected swap failure"):
        release.release(c.prof, previous=True)
    assert history.capture_runtime(c.prof, HASH) == before
    assert {p.relative_to(c.pack): p.read_bytes() for p in c.pack.rglob("*") if p.is_file()} == pack
    assert c.prof.identities.read_bytes() == people
    assert all((c.prof.store / c.prof.client / name).read_bytes() == data for name, data in ledgers.items())
    assert not (c.prof.run_dir() / "rollback.json").exists()


def test_legacy_snapshot_names_the_unkept_fixture_digest(client, capsys):
    c = client
    c.stage(1)
    prefix = f"versions/{HASH}/"
    store = packstore.Store(c.prof.artifacts / c.prof.client)
    for name in ("pack.yaml", "pack.lock", "glossary.yaml", "catalogue.yaml"):
        store.write(prefix + name, (c.pack / name).read_bytes())
    store.write(prefix + "estate.json", b'{"fixture_sha256": "older-fixture", "version": 0}')
    store.write(prefix + "20261031T000000Z-123abc/pack.yaml", b"a later snapshot of this hash")
    entry = {"identity": {"hash": HASH, "graph": None, "chunks": None, "warehouses": {}, "release": None},
             "snapshot": prefix, "inputs": None, "roster": None}
    report = history.restore_release(c.prof, entry)
    assert not (c.pack / "20261031T000000Z-123abc").exists()
    assert report["fixture"] == {"sha256": "older-fixture", "kept": False}
    assert "the fixture's files were not kept: upload the fixture whose digest is older-fixture" in capsys.readouterr().out


def test_wrong_archive_identity_is_refused_before_store_writes(client, monkeypatch):
    c = client
    for version in (1, 2):
        c.stage(version)
        release.release(c.prof, HASH)
    current, previous = release.kept(c.prof)
    folder = c.prof.store / c.prof.client / "releases" / HASH
    old_archive = folder / (previous["identity"]["release"] + ".tar.gz")
    old_archive.write_bytes((folder / (current["identity"]["release"] + ".tar.gz")).read_bytes())
    writes = []
    monkeypatch.setattr(packstore.Store, "write", lambda *args: writes.append(args))
    with pytest.raises(release.Unreleasable, match="the release tarball holds another release"):
        release.release(c.prof, previous=True)
    assert writes == []


def test_changed_lock_refuses_before_snapshot_or_pointer_writes(client):
    c = client
    c.stage(1)
    manifest = c.prof.artifacts / c.prof.client / HASH / "manifest.json"
    manifest.write_text(json.dumps({"lock_hash": hashlib.sha256((c.pack / "pack.lock").read_bytes()).hexdigest()}))
    (c.pack / "pack.lock").write_text("a different lock")
    with pytest.raises(release.Unreleasable, match="pack.lock: onboard again"):
        release.release(c.prof, HASH)
    assert c.prof.model_hash() is None
    assert not (c.pack / "versions").exists()


def test_remote_fixture_change_is_checked_before_snapshot_writes(client, monkeypatch, tmp_path):
    c = client
    c.stage(1)
    monkeypatch.setenv("STORE_URL", str(tmp_path / "objects"))
    store = open_store(str(tmp_path / "objects"))
    for path in c.pack.rglob("*"):
        if path.is_file():
            store.put(f"clients/{c.prof.client}/{path.relative_to(c.pack).as_posix()}", path.read_bytes())
    store.put(f"clients/{c.prof.client}/source/added.json", b"added after the job pulled its inputs")
    assert release.moved_inputs(c.prof, HASH) == []
    with pytest.raises(release.Unreleasable, match="source/: onboard again"):
        release.release(c.prof, HASH)
    assert c.prof.model_hash() is None
    assert not store.list(f"clients/{c.prof.client}/versions/")


def test_snapshot_restores_table_backed_unit_manifests(client, monkeypatch):
    c = client
    manifests = {"branch": {"unit": "branch", "version": 1, "_app_revision": 7}}
    restored = []
    def put(changed, histories, removed):
        restored.append((changed, histories, removed))
        for unit in removed:
            manifests.pop(unit, None)
        manifests.update(changed)
    state = SimpleNamespace(units=lambda: list(manifests), manifest=lambda unit: manifests[unit],
                            snapshot=lambda unit: (manifests[unit], []), restore=put)
    monkeypatch.setattr(history, "_table_state", lambda prof: state)
    for version in (1, 2):
        c.stage(version)
        manifests["branch"]["version"] = version
        release.release(c.prof, HASH)
    release.release(c.prof, previous=True)
    assert manifests["branch"]["version"] == 1
    changed, histories, removed = restored[-1]
    assert changed == {"branch": {"unit": "branch", "version": 1}}
    assert histories["branch"][0]["step"] == "rolled-back" and removed == set()


@pytest.mark.parametrize("candidate", ["unchanged", "unloaded", "reloaded", "unsigned", "retry"])
def test_explicit_hash_releases_new_candidate_but_restores_an_unchanged_release(client, monkeypatch, candidate):
    c = client
    c.stage(1)
    release.release(c.prof, HASH)
    original = release.kept(c.prof)[0]
    other = "34" * 32
    artifact = c.prof.artifacts / c.prof.client
    shutil.copytree(artifact / HASH, artifact / other)
    graph = c.prof.at(other).graph_path()
    shutil.copytree(c.graph.parent, graph.parent)
    release.unmark(graph)
    (graph.parent / release.INPUTS).unlink()
    release.put(graph, release.LOADED, {"client": c.prof.client, "model_hash": other, "warehouses": {}})
    release.release(c.prof, other, ungated=True)
    reload = candidate in {"reloaded", "unsigned", "retry"}
    if reload:
        release.unmark(c.graph)
        c.stage(3)
    if candidate == "unloaded":
        release.write_marker(c.graph.parent, {}, release.LOADED)
    if candidate == "unsigned":
        release.write_marker(c.graph.parent, {}, release.SIGNOFF)
        with pytest.raises(release.Unreleasable, match="sign-off"):
            release.release(c.prof, HASH)
        assert c.prof.model_hash() == other
        return
    if candidate == "retry":
        def fail(*args, **kwargs):
            raise OSError("injected record failure")
        with monkeypatch.context() as patch:
            patch.setattr(history, "record_release", fail)
            with pytest.raises(OSError, match="injected record failure"):
                release.release(c.prof, HASH)
    _, how = release.release(c.prof, HASH)
    entry = release.kept(c.prof)[0]
    if reload:
        assert entry["identity"]["release"] != original["identity"]["release"]
        assert "roster" not in entry
        assert "roster" not in released.read(c.graph.parent)
        assert c.graph.read_bytes() == b"graph bytes 3"
        _, runtime = history.review_restore(c.prof, entry)
        assert runtime["graph/graph.ladybug"] == b"graph bytes 3"
        assert "roster" not in json.loads(runtime["graph/" + released.RECORD])
    else:
        assert how == f"restored the previous release {HASH}"
        assert entry == original
        assert c.graph.read_bytes() == b"graph bytes 1"


@pytest.mark.parametrize("changed", [False, True])
def test_rollback_never_writes_identities(client, changed):
    c = client
    for version in (1, 2):
        c.stage(version)
        release.release(c.prof, HASH)
    people = {"client": c.prof.client, "principals": [{"principal": "person1", "role": "steward"}], "asker": "person1"}
    if not changed:
        c.prof.identities.write_text(json.dumps(people))
    before = c.prof.identities.read_bytes(), c.prof.identities.stat().st_mtime_ns
    release.release(c.prof, previous=True)
    report = json.loads((c.prof.run_dir() / "rollback.json").read_text())
    assert not any(row["key"] == f"deploy/{c.prof.client}/identities.yaml" for row in report["restored"])
    assert (c.prof.identities.read_bytes(), c.prof.identities.stat().st_mtime_ns) == before


@pytest.mark.parametrize("stored", [False, True], ids=["local", "object-store"])
def test_kept_pack_recognises_release_id_snapshots(client, monkeypatch, tmp_path, stored):
    c = client
    c.stage(1)
    if stored:
        monkeypatch.setenv("STORE_URL", str(tmp_path / "objects"))
        store = packstore.for_profile(c.prof)
        for path in c.pack.rglob("*"):
            if path.is_file():
                store.write(path.relative_to(c.pack).as_posix(), path.read_bytes())
    release.release(c.prof, HASH)
    store = packstore.for_profile(c.prof) if stored else packstore.Store(c.prof.artifacts / c.prof.client)
    assert not store.exists(f"versions/{HASH}/pack.yaml")
    assert history.has_kept_pack(c.prof, HASH)
    assert history.list_versions(c.prof)["versions"][-1]["kept_pack"] is True
    assert not history.has_kept_pack(c.prof, "34" * 32)
    store.delete(release.kept(c.prof)[0]["snapshot"] + "snapshot.json")
    assert not history.has_kept_pack(c.prof, HASH)
    assert history.list_versions(c.prof)["versions"][-1]["kept_pack"] is False


@pytest.mark.parametrize("stored", [False, True], ids=["local", "object-store"])
def test_failed_release_removes_only_its_snapshot(client, monkeypatch, tmp_path, stored):
    c = client
    c.stage(1)
    if stored:
        monkeypatch.setenv("STORE_URL", str(tmp_path / "objects"))
        store = packstore.for_profile(c.prof)
        for path in c.pack.rglob("*"):
            if path.is_file():
                store.write(path.relative_to(c.pack).as_posix(), path.read_bytes())
    release.release(c.prof, HASH)
    store = packstore.for_profile(c.prof) if stored else packstore.Store(c.prof.artifacts / c.prof.client)
    before = {name: store.read(name) for name in store.list("versions")}
    folders = set((store.root / "versions" / HASH).iterdir())
    def fail(*args):
        raise OSError("injected swap failure")
    monkeypatch.setattr(release, "swap", fail)
    with pytest.raises(OSError, match="injected swap failure"):
        release.release(c.prof, HASH)
    assert {name: store.read(name) for name in store.list("versions")} == before
    assert set((store.root / "versions" / HASH).iterdir()) == folders


@pytest.mark.parametrize("loaded", [False, True])
def test_retire_keeps_established_units_only_for_a_completed_candidate(client, monkeypatch, loaded):
    c = client
    c.stage(1)
    release.release(c.prof, HASH)
    candidate = c.prof.artifacts / c.prof.client / ("34" * 32)
    candidate.mkdir()
    (candidate / "drafts.json").write_text(json.dumps({"warehouses": {"pending": {"version": 1}}}))
    if loaded:
        graph = c.prof.at(candidate.name).graph_path()
        graph.parent.mkdir(parents=True)
        graph.touch()
        release.put(graph, release.LOADED, {"warehouses": {"pending": 1}})
    removed = []
    estate = SimpleNamespace(established=lambda: ["pending", "obsolete"], versions=lambda unit: [],
                             remove=removed.append)
    monkeypatch.setattr(release, "estate_of", lambda prof: estate)
    release.retire(c.prof, HASH)
    assert set(removed) == ({"obsolete"} if loaded else {"obsolete", "pending"})


@pytest.mark.parametrize("kind", ["Unit", "CuratedSource"])
@pytest.mark.parametrize("previous", [False, True])
def test_retire_keeps_every_warehouse_source_of_both_releases(client, monkeypatch, kind, previous):
    """A curated warehouse is still read by its release even though it is not a business unit."""
    c = client
    c.stage(1)
    release.release(c.prof, HASH)
    model_hash = "34" * 32 if previous else HASH
    nodes = c.prof.artifacts / c.prof.client / model_hash / "data/nodes"
    nodes.mkdir(parents=True, exist_ok=True)
    (nodes / f"{kind}.csv").write_text("id\nkept-source\n")
    if previous:
        release.served(c.prof.store / c.prof.client, {"hash": model_hash, "graph": None, "chunks": None})
    removed = []
    estate = SimpleNamespace(established=lambda: ["kept-source", "obsolete"], versions=lambda unit: [],
                             remove=removed.append)
    monkeypatch.setattr(release, "estate_of", lambda prof: estate)
    release.retire(c.prof, HASH)
    assert removed == ["obsolete"]


@pytest.mark.parametrize("kind", ["Unit", "CuratedSource"])
def test_release_and_restore_require_every_warehouse_source(client, monkeypatch, kind):
    """Req 21 checks curated warehouses as well as unit warehouses before a release or rollback writes state."""
    c = client
    c.stage(1)
    nodes = c.prof.artifacts / c.prof.client / HASH / "data/nodes"
    nodes.mkdir(parents=True)
    (nodes / f"{kind}.csv").write_text("id\nrequired-source\n")
    with pytest.raises(release.Unreleasable, match=r"no .*required-source\.duckdb"):
        release.release(c.prof, HASH)
    assert c.prof.model_hash() is None and release.kept(c.prof) == []
    c.prof.warehouses.mkdir(parents=True, exist_ok=True)
    (c.prof.warehouses / "required-source.duckdb").write_bytes(b"warehouse bytes")
    release.release(c.prof, HASH)
    saved = release.kept(c.prof)[0]
    runtime = history.release_archive(c.prof, saved)
    before = history.capture_runtime(c.prof, HASH)
    monkeypatch.setattr(history, "release_archive", lambda *_: {name: data for name, data in runtime.items()
                                                              if name != "warehouses/required-source.duckdb"})
    with pytest.raises(release.Unreleasable, match="warehouse required-source v0 is not held in the release tarball"):
        history.review_restore(c.prof, saved)
    assert history.capture_runtime(c.prof, HASH) == before
    assert c.prof.serve().identity() == saved["identity"]


def test_registry_under_the_estate_stays_deployment_configuration(client):
    """A profile registry beside the estate is never removed or replaced by a release's public runtime."""
    from dataclasses import replace
    c = client
    c.prof = replace(c.prof, registry=c.prof.estate() / "profiles.yaml")
    c.stage(1)
    c.prof.registry.write_bytes(b"original deployment")
    release.release(c.prof, HASH)
    c.stage(2)
    c.prof.registry.write_bytes(b"current deployment")
    release.release(c.prof, HASH)
    assert "estate/profiles.yaml" not in history.capture_runtime(c.prof, HASH)
    release.release(c.prof, previous=True)
    assert c.prof.registry.read_bytes() == b"current deployment"
    history.restore_runtime(c.prof, HASH, {"estate/profiles.yaml": b"archived deployment"})
    assert c.prof.registry.read_bytes() == b"current deployment"


def test_old_rosters_are_ignored_and_new_local_archives_omit_them(client):
    c = client
    inputs = c.stage(1)
    roster = {"bindings": [{"principal": "old@example.com", "role": "steward"}]}
    (c.graph.parent / "ROSTER").write_text(json.dumps(roster))
    (c.graph.parent / release.INPUTS).write_text(json.dumps({**inputs, "roster": roster}))
    release.release(c.prof, HASH)
    entry = release.kept(c.prof)[0]
    runtime = history.release_archive(c.prof, entry)
    assert "graph/ROSTER" not in runtime
    assert "roster" not in json.loads(runtime["graph/INPUTS"])
    record = released.path(c.graph.parent)
    record.write_text(json.dumps({**json.loads(record.read_text()), "roster": roster,
                                 "inputs": {**inputs, "roster": roster}}))
    assert "roster" not in released.read(c.graph.parent)
    assert "roster" not in released.read(c.graph.parent)["inputs"]
    snapshots = history.snapshot_store(c.prof)
    manifest = json.loads(snapshots.read(entry["snapshot"] + "snapshot.json"))
    manifest["ROSTER"] = "old-pruned-roster"
    snapshots.write(entry["snapshot"] + "snapshot.json", json.dumps(manifest).encode())
    assert "ROSTER" not in history._release_snapshot(c.prof, entry)


def test_deployment_identity_file_inside_runtime_is_never_captured_or_restored(client):
    from dataclasses import replace
    c = client
    c.prof = replace(c.prof, identities=c.prof.estate() / "identities.yaml")
    c.stage(1)
    c.prof.identities.write_bytes(b"current identity seed")
    before = c.prof.identities.read_bytes(), c.prof.identities.stat().st_mtime_ns
    assert "estate/identities.yaml" not in history.capture_runtime(c.prof, HASH)
    history.restore_runtime(c.prof, HASH, {"estate/identities.yaml": b"old roster"})
    assert (c.prof.identities.read_bytes(), c.prof.identities.stat().st_mtime_ns) == before
