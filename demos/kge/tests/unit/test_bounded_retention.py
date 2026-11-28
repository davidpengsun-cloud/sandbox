"""Only actual release identities and the current candidate retain remote generations."""
import json

import pytest

from kge.adapters import graphstore, released, retrieval
from kge.graph import leftovers, release
from tests.unit import test_leftovers as examples

prof = examples.prof
cloud = examples.cloud
HASH, OTHER = examples.HASH, examples.OTHER
THIRD = "ef" * 32


def record(c, model_hash):
    path = c.prof.at(model_hash).graph_path()
    released.write(path.parent, model_hash, graphstore.release_entry(path),
                   retrieval.release_entry(path.parent), release.loaded(path)["warehouses"])


def test_historical_release_file_does_not_extend_two_release_retention(cloud):
    first = examples.objects(cloud, 1, HASH, pointers=True)
    second = examples.objects(cloud, 2, OTHER, pointers=True)
    obsolete = examples.objects(cloud, 3, THIRD, pointers=True)
    for model_hash in (HASH, OTHER, THIRD):
        record(cloud, model_hash)
    examples.write(cloud.prof.store / cloud.prof.client / release.SERVED,
                   [examples.identity(second, OTHER), examples.identity(first, HASH)])
    assert leftovers.sweep(cloud.prof) == obsolete
    assert leftovers.held(cloud.prof) == leftovers._union(first, second)
    assert not leftovers.sweep(cloud.prof).generations


def test_only_latest_completed_unreleased_candidate_is_live(cloud):
    old = examples.objects(cloud, 1, HASH, pointers=True)
    current = examples.objects(cloud, 2, OTHER, pointers=True)
    abandoned = examples.objects(cloud, 3, THIRD)
    assert leftovers.sweep(cloud.prof) == leftovers._union(old, abandoned)
    assert leftovers.held(cloud.prof) == current


@pytest.mark.parametrize("kept", [False, True])
def test_same_hash_replacement_keeps_old_generation_only_when_released(cloud, kept):
    old = examples.objects(cloud, 1, HASH, pointers=True)
    if kept:
        examples.write(cloud.prof.store / cloud.prof.client / release.SERVED, [examples.identity(old)])
    current = examples.objects(cloud, 2, HASH, pointers=True)
    dropped = leftovers.sweep(cloud.prof)
    assert dropped == (leftovers.Held(frozenset(), frozenset(), frozenset()) if kept else old)
    assert leftovers.held(cloud.prof) == (leftovers._union(old, current) if kept else current)


def test_current_pointer_is_protected_beside_newer_candidate(cloud, monkeypatch):
    current = examples.objects(cloud, 1, HASH, pointers=True)
    candidate = examples.objects(cloud, 2, OTHER, pointers=True)
    orphan = examples.objects(cloud, 3, THIRD)
    monkeypatch.setattr(type(cloud.prof), "model_hash", lambda self: HASH)
    assert leftovers.sweep(cloud.prof) == orphan
    assert leftovers.held(cloud.prof) == leftovers._union(current, candidate)


def test_malformed_ledger_refuses_before_any_object_is_dropped(cloud):
    before = examples.objects(cloud, 1, HASH, pointers=True)
    examples.write(cloud.prof.store / cloud.prof.client / release.SERVED, {"not": "a ledger"})
    with pytest.raises(ValueError, match="list of release records"):
        leftovers.sweep(cloud.prof)
    assert leftovers.held(cloud.prof) == before


def test_legacy_fallback_is_bounded_and_ends_when_ledger_exists(cloud):
    old = examples.objects(cloud, 1, HASH, pointers=True)
    record(cloud, HASH)
    middle = examples.objects(cloud, 2, OTHER, pointers=True)
    record(cloud, OTHER)
    newest = examples.objects(cloud, 3, THIRD, pointers=True)
    record(cloud, THIRD)
    assert leftovers.released(cloud.prof) == leftovers._union(middle, newest)
    examples.write(cloud.prof.store / cloud.prof.client / release.SERVED, [examples.identity(middle, OTHER)])
    assert leftovers.sweep(cloud.prof) == leftovers._union(old, newest)
    assert leftovers.held(cloud.prof) == middle
    path = cloud.prof.store / cloud.prof.client / THIRD / released.RECORD
    assert json.loads(path.read_text())["hash"] == THIRD


def test_expired_implicit_v0_is_assessed_but_unrelated_established_unit_is_preserved(cloud):
    expired = examples.objects(cloud, 0)
    nodes = cloud.prof.artifacts / cloud.prof.client / HASH / "data" / "nodes"
    nodes.mkdir(parents=True)
    (nodes / "Unit.csv").write_text("id\nretail\n")
    session = cloud.estate.stage("independent", ["CREATE TABLE sales (amount INTEGER)"], 0)
    session.load("sales", ["amount"], [[99]])
    cloud.estate.publish("independent", 0)
    session.close()
    assert leftovers.sweep(cloud.prof) == expired
    assert not cloud.estate.exists("retail")
    assert cloud.estate.exists("independent")
    assert leftovers.held(cloud.prof).versions == frozenset([("independent", 0)])








def test_retire_uses_legacy_snapshot_versions_when_identity_omits_them(cloud, monkeypatch):
    old = examples.objects(cloud, 1, HASH)
    examples.objects(cloud, 2, OTHER)
    legacy = examples.identity(old)
    legacy.pop("warehouses")
    examples.write(cloud.prof.store / cloud.prof.client / release.KEPT,
                   [{"identity": legacy, "inputs": {"warehouses": {"retail": 1}}}])
    monkeypatch.setattr(type(cloud.prof), "identity", lambda self: legacy)
    monkeypatch.setattr(graphstore, "prune", lambda *args: [])
    monkeypatch.setattr(retrieval, "prune", lambda *args: [])
    release.retire(cloud.prof, HASH)
    assert cloud.estate.versions("retail") == [1]


def test_prepare_retention_invalidates_approvals_before_any_remote_drop(cloud):
    old = examples.objects(cloud, 1, HASH, pointers=True)
    previous = examples.objects(cloud, 2, OTHER, pointers=True)
    current = examples.objects(cloud, 3, THIRD, pointers=True)
    for model_hash in (HASH, OTHER, THIRD):
        record(cloud, model_hash)
    examples.write(cloud.prof.store / cloud.prof.client / release.SERVED,
                   [examples.identity(current, THIRD), examples.identity(previous, OTHER)])
    before = leftovers.held(cloud.prof)
    release.prepare_retention(cloud.prof)
    path = cloud.prof.at(HASH).graph_path()
    assert not path.exists()
    assert release.loaded(path) is None
    assert release.marked(path, release.GATE) is None
    assert release.marked(path, release.SIGNOFF) is None
    assert retrieval.release_entry(path.parent) is None
    assert leftovers.held(cloud.prof) == before
    assert graphstore.loaded(cloud.prof.at(OTHER).graph_path())
    assert graphstore.loaded(cloud.prof.at(THIRD).graph_path())
    assert leftovers.sweep(cloud.prof) == old


def test_published_floor_outlives_ahead_of_publication_local_ledger(cloud, monkeypatch):
    from kge.pipeline import objstore
    advertised = examples.objects(cloud, 1, HASH, pointers=True)
    unadvertised = examples.objects(cloud, 2, OTHER, pointers=True)
    newest = examples.objects(cloud, 3, THIRD, pointers=True)
    for model_hash in (HASH, OTHER, THIRD):
        record(cloud, model_hash)
    examples.write(cloud.prof.store / cloud.prof.client / release.SERVED,
                   [examples.identity(newest, THIRD), examples.identity(unadvertised, OTHER)])
    monkeypatch.setattr(objstore, "published_records", lambda _: [examples.identity(advertised, HASH)])
    monkeypatch.setattr(type(cloud.prof), "model_hash", lambda self: self.pinned or THIRD)
    release.prepare_retention(cloud.prof)
    assert not graphstore.loaded(cloud.prof.at(HASH).graph_path())
    assert graphstore.loaded(cloud.prof.at(OTHER).graph_path())
    assert graphstore.loaded(cloud.prof.at(THIRD).graph_path())
    assert leftovers.sweep(cloud.prof) == unadvertised
    assert leftovers.held(cloud.prof) == leftovers._union(advertised, newest)


def test_advertised_snapshots_and_archives_survive_ahead_local_kept_records(cloud, monkeypatch):
    from kge.pipeline import objstore
    advertised = examples.objects(cloud, 1, HASH, pointers=True)
    previous = examples.objects(cloud, 2, OTHER, pointers=True)
    newest = examples.objects(cloud, 3, THIRD, pointers=True)
    for model_hash in (HASH, OTHER, THIRD):
        record(cloud, model_hash)
    records = [{**examples.identity(value, model_hash), "release": f"r{number}",
                "snapshot": f"versions/{model_hash}/r{number}/"}
               for number, (value, model_hash) in enumerate(((advertised, HASH), (previous, OTHER), (newest, THIRD)), 1)]
    root = cloud.prof.store / cloud.prof.client
    examples.write(root / release.SERVED, records[:0:-1])
    examples.write(root / release.KEPT, [{"identity": entry, "snapshot": entry["snapshot"]} for entry in records[:0:-1]])
    paths = []
    for entry in records:
        snapshot = cloud.prof.artifacts / cloud.prof.client / entry["snapshot"] / "pack.yaml"
        archive = root / "releases" / entry["hash"] / f"{entry['release']}.tar.gz"
        for path in (snapshot, archive):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"retained bytes")
        paths.append((snapshot, archive))
    monkeypatch.setattr(objstore, "published_records", lambda _: records[:2])
    monkeypatch.setattr(type(cloud.prof), "model_hash", lambda self: self.pinned or THIRD)
    monkeypatch.setattr(graphstore, "prune", lambda *args: [])
    monkeypatch.setattr(retrieval, "prune", lambda *args: [])
    release.retire(cloud.prof, THIRD)
    assert all(path.read_bytes() == b"retained bytes" for pair in paths[:2] for path in pair)
    assert all(path.read_bytes() == b"retained bytes" for path in paths[2])
