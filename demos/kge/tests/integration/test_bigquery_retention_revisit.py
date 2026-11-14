"""Retired packs lose their load approvals locally and in saved work before they are revisited."""
import json
import re
import shutil

from kge import cli
from kge.adapters import graphstore, retrieval
from kge.graph import release
from kge.pipeline.objstore import tarball, unpack
from tests.integration.test_bigquery_lifecycle import cloud as cloud


def test_three_pack_only_releases_revisit_reloads_and_saved_work_cannot_restore_approval(cloud, tmp_path):
    fake, _, prof, pack = cloud
    source = pack / "pack.yaml"
    original = source.read_text()
    identities = []
    stale = None
    for number in range(3):
        source.write_text(original.replace("Thornbury Mutual Casualty governance graph",
                                           f"Thornbury Mutual Casualty governance graph {number}"))
        cli.Commands({"REPEATS": "1"}).onboard()
        folders = [path for path in (prof.artifacts / prof.client).iterdir()
                   if path.is_dir() and re.fullmatch("[a-f0-9]{64}", path.name)]
        candidate = max(folders, key=lambda path: path.stat().st_mtime_ns).name
        cli.Commands({"HASH": candidate, "ROLE": "claims_steward"}).sign_off()
        cli.Commands({"HASH": candidate, "REPEATS": "1"}).release()
        identities.append(release.served(prof.store / prof.client)[0])
        if number == 0:
            first_folder = prof.store / prof.client / candidate
            stale = {path.name: path.read_bytes() for path in first_folder.iterdir() if path.is_file()}
    assert len({entry["hash"] for entry in identities}) == 3
    assert all(entry["warehouses"] == identities[0]["warehouses"] for entry in identities)
    first = identities[0]
    path = prof.at(first["hash"]).graph_path()
    assert not graphstore.loaded(path)
    assert release.loaded(path) is None
    assert release.marked(path, release.GATE) is None
    assert release.marked(path, release.SIGNOFF) is None
    assert not path.exists()
    assert not any(name.startswith(first["graph"]) for _, name in fake.objects)

    app = tmp_path / "saved-app"
    restored_root = app / "build" / "stores" / prof.client
    shutil.copytree(prof.store / prof.client, restored_root)
    archive = tarball(app, list((app / "build").rglob("*")))
    restored_folder = restored_root / first["hash"]
    for name, contents in stale.items():
        (restored_folder / name).write_bytes(contents)
    assert graphstore.loaded(graphstore.graph_path(restored_folder))
    unpack(archive, app, candidate_root=restored_root)
    restored = graphstore.graph_path(restored_folder)
    assert not graphstore.loaded(restored)
    assert not restored.exists()
    assert retrieval.release_entry(restored_folder) is None
    assert release.loaded(restored) is None
    assert release.marked(restored, release.GATE) is None
    assert release.marked(restored, release.SIGNOFF) is None

    source.write_text(original.replace("Thornbury Mutual Casualty governance graph",
                                       "Thornbury Mutual Casualty governance graph 0"))
    cli.Commands({"REPEATS": "1"}).onboard()
    assert graphstore.loaded(path)
    assert graphstore.generation(path) != first["graph"]
    assert release.marked(path, release.GATE)["passed"]
    assert json.loads((prof.estate() / "summary.json").read_text())["warehouse_versions"] == first["warehouses"]


def test_successful_stored_third_release_saves_retired_approvals_as_absent(cloud, tmp_path, monkeypatch):
    import yaml
    from kge.adapters.objstore import open_store
    from kge.pipeline import objstore
    from kge.pipeline.profile import resolve

    fake, _, initial, pack = cloud
    app = tmp_path / "stored-app"
    registry = app / "deploy" / "profiles.yaml"
    registry.parent.mkdir(parents=True)
    config = yaml.safe_load(initial.registry.read_text())
    config["profiles"][initial.client].update(
        artifacts=str(app / "build" / "artifacts"), store=str(app / "build" / "stores"),
        landing=str(app / "build" / "landing"), warehouses=str(app / "build" / "estate" / "warehouses"))
    registry.write_text(yaml.safe_dump(config))
    monkeypatch.setenv("PROFILES", str(registry))
    monkeypatch.chdir(app)
    prof = resolve()
    remote = tmp_path / "published-store"
    store = open_store(str(remote))
    source = pack / "pack.yaml"
    original = source.read_text()
    identities = []
    stale = None
    for number in range(3):
        monkeypatch.delenv("STORE_URL", raising=False)
        source.write_text(original.replace("Thornbury Mutual Casualty governance graph",
                                           f"Thornbury Mutual Casualty governance graph stored {number}"))
        cli.Commands({"REPEATS": "1"}).onboard()
        folders = [path for path in (prof.artifacts / prof.client).iterdir()
                   if path.is_dir() and re.fullmatch("[a-f0-9]{64}", path.name)]
        candidate = max(folders, key=lambda path: path.stat().st_mtime_ns).name
        cli.Commands({"HASH": candidate, "ROLE": "claims_steward"}).sign_off()
        cli.Commands({"HASH": candidate, "REPEATS": "1", "STORE_URL": str(remote)}).release()
        monkeypatch.setenv("STORE_URL", str(remote))
        cli.Commands({"COMMAND": "release"})._stored_after()
        identity = json.loads(store.get(objstore.identity_key(prof)))
        identities.append(identity)
        if number == 0:
            first_folder = prof.store / prof.client / identity["hash"]
            stale = {path.name: path.read_bytes() for path in first_folder.iterdir() if path.is_file()}
    first = identities[0]
    assert len({identity["hash"] for identity in identities}) == 3
    assert not any(name.startswith(first["graph"]) for _, name in fake.objects)
    first_folder = prof.store / prof.client / first["hash"]
    for name, data in stale.items():
        (first_folder / name).write_bytes(data)
    assert graphstore.loaded(graphstore.graph_path(first_folder))
    objstore.pull_work(store, app, prof)
    path = graphstore.graph_path(first_folder)
    assert not path.exists()
    assert retrieval.release_entry(first_folder) is None
    assert release.loaded(path) is None
    assert release.marked(path, release.GATE) is None
    assert release.marked(path, release.SIGNOFF) is None
    assert json.loads(store.get(objstore.identity_key(prof))) == identities[-1]
