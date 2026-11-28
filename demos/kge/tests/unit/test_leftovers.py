"""The journal and retention floor, against real adapters with an offline BigQuery stand-in."""
import json
from dataclasses import FrozenInstanceError
from types import SimpleNamespace

import pytest

from kge.adapters import graphstore, released, retrieval
from kge.adapters.graphstore import bigquery as graphs
from kge.adapters.graphstore.gql import Schema
from kge.adapters.warehouse import estate_of
from kge.graph import leftovers, release
from kge.pipeline import profile, warehouse_versions
from tests import bigquery_fake
from tests.unit.test_backend_inventory import create
from tests.unit.test_released_generation import profile as make_profile

HASH = "ab" * 32
OTHER = "cd" * 32


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value))


@pytest.fixture
def prof(tmp_path, monkeypatch):
    monkeypatch.setenv("MODEL_PROVIDER", "none")
    monkeypatch.setenv("GRAPH_STORE", "ladybug")
    monkeypatch.setenv("WAREHOUSE_BACKEND", "duckdb")
    monkeypatch.setenv("RETRIEVAL_BACKEND", "graph")
    monkeypatch.setattr(release, "KEPT", "kept.json", raising=False)
    return make_profile(tmp_path)


@pytest.fixture
def cloud(prof, monkeypatch):
    monkeypatch.setenv("BIGQUERY_PROJECT", bigquery_fake.PROJECT)
    monkeypatch.setenv("BIGQUERY_DATASET_PREFIX", "pilot")
    monkeypatch.setenv("GRAPH_STORE", "bigquery")
    monkeypatch.setenv("WAREHOUSE_BACKEND", "bigquery")
    monkeypatch.setenv("RETRIEVAL_BACKEND", "bigquery")
    monkeypatch.setenv("GRAPH_DATASET", "candidates")
    monkeypatch.setattr(profile, "resolve", lambda: prof)
    fake = bigquery_fake.install(monkeypatch)
    monkeypatch.setattr(graphs.Client, "query", lambda self, sql, parameters, read=False: fake.run(self, sql))
    return SimpleNamespace(prof=prof, fake=fake, estate=estate_of(prof))


def objects(c, number, model_hash=HASH, pointers=False):
    """Create real inventory entries; optionally make a completed load name them."""
    path = c.prof.at(model_hash).graph_path()
    graph = graphs.Graph(path)
    graph.ns.prefix = graphs.prefix_of(model_hash, f"{number:08x}")
    dataset, prefix = graph.ns.dataset, graph.ns.prefix
    create(c.fake, dataset, prefix + "Node")
    create(c.fake, dataset, prefix + "graph", "property graph")
    table = f"chunks_{model_hash[:16]}_{number:08x}"
    create(c.fake, dataset, table)
    session = c.estate.stage("retail", ["CREATE TABLE sales (amount INTEGER)"], number)
    session.load("sales", ["amount"], [[number]])
    c.estate.publish("retail", number)
    session.close()
    if pointers:
        graph.write(Schema.build([], []))
        retrieval.name_chunks(c.prof.at(model_hash), dataset, table)
        release.put(path, release.LOADED, {"warehouses": {"retail": number}})
        release.put(path, release.GATE, {"passed": False, "run_id": str(number)})
    return leftovers.Held(frozenset([prefix]), frozenset([f"{dataset}.{table}"]),
                          frozenset([("retail", number)]))


def identity(value, model_hash=HASH):
    return {"hash": model_hash, "graph": next(iter(value.generations)), "chunks": next(iter(value.chunks)),
            "warehouses": dict(value.versions)}


def test_journal_round_trip_is_frozen_and_deterministic(prof):
    graph = prof.at(HASH).graph_path()
    graph.parent.mkdir(parents=True)
    graph.write_bytes(b"local graph")
    snapshot = leftovers.held(prof)
    record = json.loads(json.dumps(snapshot.to_json()))
    restored = leftovers.Held.from_json(record)
    assert restored == snapshot
    assert restored.to_json() == snapshot.to_json()
    assert list(record["folders"]) == [HASH]
    assert leftovers.JOURNAL == "onboard-journal.json"
    with pytest.raises(FrozenInstanceError):
        snapshot.chunks = frozenset(["changed"])
    value = leftovers.Held(frozenset(["z", "a"]), frozenset(["d.z", "d.a"]), frozenset([("u", 2), ("u", 1)]))
    assert value.to_json()["versions"] == [["u", 1], ["u", 2]]
    assert value.to_json()["generations"] == ["a", "z"]


def test_sweep_drops_killed_run_and_preserves_live_candidate(cloud):
    live = objects(cloud, 1, pointers=True)
    orphan = objects(cloud, 2)
    path = cloud.prof.at(HASH).graph_path()
    before = {p.name: p.read_bytes() for p in path.parent.iterdir()}
    assert leftovers.held(cloud.prof) == leftovers._union(live, orphan)
    assert leftovers.sweep(cloud.prof) == orphan
    assert leftovers.held(cloud.prof) == live
    assert {p.name: p.read_bytes() for p in path.parent.iterdir()} == before
    assert leftovers.sweep(cloud.prof) == leftovers.Held(frozenset(), frozenset(), frozenset())


@pytest.mark.parametrize("source", ["ledger", "kept", "record", "legacy"])
def test_each_release_source_protects_objects_without_live_pointers(cloud, source):
    value = objects(cloud, 1)
    orphan = objects(cloud, 2)
    root = cloud.prof.store / cloud.prof.client
    entry = identity(value)
    write(cloud.prof.estate() / "summary.json", {"fixture_units": ["retail"]})
    if source == "ledger":
        write(root / release.SERVED, [entry])
    elif source == "kept":
        write(root / release.KEPT, [{"identity": entry, "inputs": {}, "roster": {}}])
    else:
        graph = graphs.Graph(cloud.prof.at(HASH).graph_path())
        graph.ns.prefix = entry["graph"]
        graph.write(Schema.build([], []))
        pointer = graphstore.release_entry(graph.path)
        dataset, table = entry["chunks"].split(".")
        if source == "record":
            released.write(graph.path.parent, HASH, pointer, {"dataset": dataset, "table": table}, {"retail": 1})
        else:
            write(graph.path.parent / released.LEGACY["graph"], pointer)
            write(graph.path.parent / released.LEGACY["chunks"], {"dataset": dataset, "table": table})
        graph.path.unlink()
    protected = value if source != "legacy" else leftovers.Held(value.generations, value.chunks, frozenset())
    assert leftovers.released(cloud.prof) == protected
    assert leftovers.sweep(cloud.prof) == leftovers._union(value, orphan) - protected
    assert leftovers.held(cloud.prof) == protected


def test_named_keeps_live_estate_and_unassessed_units_not_historical_artifacts(prof, monkeypatch):
    root = prof.store / prof.client / HASH
    write(root / release.LOADED, {"one": {"warehouses": {"loaded": 3}},
                                "another": {"warehouses": {"other": 4}}})
    write(prof.artifacts / prof.client / OTHER / "drafts.json", {"warehouses": {"artifact": 5}})
    monkeypatch.setattr(warehouse_versions, "estate_live", lambda prof: {"fixture": 6})
    monkeypatch.setattr(leftovers, "estate_of", lambda prof: SimpleNamespace(
        established=lambda: ["established"], held=lambda: {"orphan": [0, 7]}))
    expected = frozenset([("fixture", 6), ("established", 0), ("orphan", 0), ("orphan", 7)])
    assert leftovers.named(prof).versions == expected
    assert leftovers.released(prof).versions == frozenset()


def test_failure_drops_new_objects_and_unmarks_only_their_pointers(cloud):
    old = objects(cloud, 1, pointers=True)
    before = leftovers.Held.from_json(json.loads(json.dumps(leftovers.held(cloud.prof).to_json())))
    old_folder = cloud.prof.at(HASH).graph_path().parent
    old_bytes = {p.name: p.read_bytes() for p in old_folder.iterdir()}
    with pytest.raises(RuntimeError, match="injected margin failure"):
        try:
            new = objects(cloud, 2, OTHER, pointers=True)
            gate = (cloud.prof.at(OTHER).graph_path().parent / release.GATE).read_bytes()
            raise RuntimeError("injected margin failure")
        finally:
            dropped = leftovers.drop_new(cloud.prof, before)
    assert dropped == new
    assert leftovers.held(cloud.prof) == old
    assert {p.name: p.read_bytes() for p in old_folder.iterdir()} == old_bytes
    path = cloud.prof.at(OTHER).graph_path()
    assert not path.exists()
    assert retrieval.release_entry(path.parent) is None
    assert release.loaded(path) is None
    assert (path.parent / release.GATE).read_bytes() == gate


def test_failure_after_same_hash_reload_keeps_old_backend_objects(cloud):
    old = objects(cloud, 1, pointers=True)
    before = leftovers.held(cloud.prof)
    new = objects(cloud, 2, pointers=True)
    assert leftovers.drop_new(cloud.prof, before) == new
    assert leftovers.held(cloud.prof) == old
    assert not cloud.prof.at(HASH).graph_path().exists()
    assert release.loaded(cloud.prof.at(HASH).graph_path()) is None


def test_failure_never_drops_objects_released_after_snapshot(cloud):
    before = leftovers.held(cloud.prof)
    kept = objects(cloud, 1, pointers=True)
    write(cloud.prof.store / cloud.prof.client / release.KEPT, [{"identity": identity(kept)}])
    assert leftovers.drop_new(cloud.prof, before) == leftovers.Held(frozenset(), frozenset(), frozenset())
    assert leftovers.held(cloud.prof) == kept
    assert release.loaded(cloud.prof.at(HASH).graph_path())


def test_released_warehouses_come_from_snapshot_not_recompiled_artifact(cloud):
    before = leftovers.held(cloud.prof)
    old = objects(cloud, 1)
    new = objects(cloud, 2)
    write(cloud.prof.store / cloud.prof.client / release.KEPT,
          [{"identity": {"hash": HASH}, "inputs": {"warehouses": {"retail": 1}}}])
    write(cloud.prof.artifacts / cloud.prof.client / HASH / "drafts.json", {"warehouses": {"retail": 2}})
    assert leftovers.released(cloud.prof).versions == old.versions
    assert leftovers.named(cloud.prof).versions == old.versions
    assert leftovers.drop_new(cloud.prof, before).versions == new.versions


@pytest.mark.parametrize("part", ["graph", "chunks"])
def test_failure_clears_only_the_pointer_that_names_a_dropped_object(cloud, part):
    objects(cloud, 1, pointers=True)
    before = leftovers.held(cloud.prof)
    objects(cloud, 2, pointers=True)
    path = cloud.prof.at(HASH).graph_path()
    current = leftovers.held(cloud.prof)
    if part == "graph":
        before = leftovers.Held(before.generations, current.chunks, current.versions)
    else:
        before = leftovers.Held(current.generations, before.chunks, current.versions)
    leftovers.drop_new(cloud.prof, before)
    assert path.exists() == (part != "graph")
    assert (retrieval.release_entry(path.parent) is not None) == (part != "chunks")
    assert release.loaded(path) is None
    assert release.gate(path)["passed"] is False


def test_local_failure_removes_new_graph_and_sidecars_but_keeps_gate(prof):
    before = leftovers.Held.from_json(leftovers.held(prof).to_json())
    graph = prof.at(HASH).graph_path()
    graph.parent.mkdir(parents=True)
    graph.write_bytes(b"new graph")
    sidecar = graph.with_name(graph.name + ".wal")
    sidecar.write_bytes(b"new wal")
    release.put(graph, release.LOADED, {"model_hash": HASH})
    release.put(graph, release.GATE, {"passed": False})
    release.write_marker(graph.parent, {**graphstore.held(graph.parent), "other": {"kept": True}})
    gate = (graph.parent / release.GATE).read_bytes()
    leftovers.drop_new(prof, before)
    assert not graph.exists() and not sidecar.exists()
    assert graphstore.held(graph.parent) == {"other": {"kept": True}}
    assert (graph.parent / release.GATE).read_bytes() == gate


@pytest.mark.parametrize("changed", [False, True])
def test_local_existing_candidate_keeps_file_and_unchanged_candidate_keeps_approval(prof, changed):
    graph = prof.at(HASH).graph_path()
    graph.parent.mkdir(parents=True)
    graph.write_bytes(b"old graph")
    release.put(graph, release.LOADED, {"model_hash": HASH})
    before = leftovers.Held.from_json(leftovers.held(prof).to_json())
    if changed:
        graph.write_bytes(b"rewritten graph")
    release.put(graph, release.GATE, {"passed": False})
    leftovers.drop_new(prof, before)
    assert graph.exists()
    assert bool(release.loaded(graph)) == (not changed)
    assert release.gate(graph) == {"passed": False}


def test_local_release_is_kept_even_if_it_appeared_after_the_snapshot(prof):
    before = leftovers.held(prof)
    graph = prof.at(HASH).graph_path()
    graph.parent.mkdir(parents=True)
    graph.write_bytes(b"released graph")
    release.put(graph, release.LOADED, {"model_hash": HASH})
    write(graph.parent.parent / release.SERVED, [{"hash": HASH, "graph": None, "chunks": None}])
    leftovers.drop_new(prof, before)
    assert graph.read_bytes() == b"released graph"
    assert release.loaded(graph)


def test_failed_local_same_hash_reload_withdraws_approval_but_keeps_released_file(prof):
    graph = prof.at(HASH).graph_path()
    graph.parent.mkdir(parents=True)
    graph.write_bytes(b"released graph")
    write(graph.parent.parent / release.SERVED, [{"hash": HASH, "graph": None, "chunks": None}])
    before = leftovers.held(prof)
    graph.write_bytes(b"rewritten graph")
    release.put(graph, release.LOADED, {"model_hash": HASH})
    release.put(graph, release.GATE, {"passed": False})
    leftovers.drop_new(prof, before)
    assert graph.exists()
    assert release.loaded(graph) is None
    assert release.gate(graph) == {"passed": False}


@pytest.mark.parametrize("file", ["served.json", "kept.json", HASH + "/release.json"])
def test_unreadable_retention_record_refuses_cleanup_before_any_drop(cloud, file):
    objects(cloud, 1)
    before = leftovers.held(cloud.prof)
    path = cloud.prof.store / cloud.prof.client / file
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{")
    with pytest.raises((ValueError, released.Unreadable)):
        leftovers.sweep(cloud.prof)
    assert leftovers.held(cloud.prof) == before


def test_lines_name_each_object_in_stable_order():
    value = leftovers.Held(frozenset(["g2", "g1"]), frozenset(["d.t"]), frozenset([("retail", 2)]))
    assert leftovers.lines(value, "swept") == ["onboard: swept generation g1", "onboard: swept generation g2",
                                              "onboard: swept chunks d.t", "onboard: swept warehouse retail v2"]
    assert leftovers.lines(leftovers.Held(frozenset(), frozenset(), frozenset()), "dropped") == [
        "onboard: dropped nothing"]
