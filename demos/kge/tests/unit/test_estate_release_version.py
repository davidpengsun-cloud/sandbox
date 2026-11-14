"""Fixture rows belong to the release's warehouse identity, checked against a BigQuery fake, never the live estate."""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from kge.adapters import released
from kge.adapters.warehouse import estate_of, open_warehouses
from kge.adapters.warehouse.bigquery import BigQueryWarehouses
from kge.graph import history, load, release
from kge.pipeline import unit_history, warehouse_versions
from kge.services.ask.engine import graphview
from kge.services.ask.engine.core import Engine
from tests import bigquery_fake
from tests.unit.test_released_generation import HASH, profile


@pytest.fixture
def estate_release(tmp_path, monkeypatch):
    monkeypatch.setenv("WAREHOUSE_BACKEND", "bigquery")
    monkeypatch.setenv("GRAPH_STORE", "ladybug")
    monkeypatch.setenv("RETRIEVAL_BACKEND", "graph")
    monkeypatch.setenv("BIGQUERY_PROJECT", bigquery_fake.PROJECT)
    fake = bigquery_fake.install(monkeypatch)
    prof = profile(tmp_path)
    pack = prof.clients / prof.client
    pack.mkdir(parents=True)
    (pack / "pack.yaml").write_text("manifest: {}\n")
    (pack / "pack.lock").write_text("{}\n")
    graph = prof.at(HASH).graph_path()
    graph.parent.mkdir(parents=True)
    graph.touch()
    artifact = prof.artifacts / prof.client / HASH
    artifact.mkdir(parents=True)
    (artifact / "manifest.json").write_text("{}")
    nodes = artifact / "data" / "nodes"
    nodes.mkdir(parents=True)
    (nodes / "Unit.csv").write_text("id\nretail\n")
    monkeypatch.setattr(load, "read_artifact", lambda folder, client: {"model_hash": HASH})
    monkeypatch.setattr(history, "review_proposals", lambda *args: None)
    monkeypatch.setattr(history, "record_release", lambda *args, **kwargs: None)
    monkeypatch.setattr(unit_history, "record", lambda *args, **kwargs: None)
    live, kept = {}, []

    def prune(prof, keep):
        kept.append(keep)
        estate = estate_of(prof)
        dropped = {}
        for unit in live:
            versions = estate.versions(unit) + ([0] if estate.exists(unit) else [])
            for version in versions:
                if version not in keep.get(unit, set()):
                    estate.drop(unit, version or None)
                    dropped.setdefault(unit, []).append(version)
        return dropped

    monkeypatch.setattr(warehouse_versions, "estate_live", lambda prof: dict(live))
    monkeypatch.setattr(warehouse_versions, "prune_estate", prune)
    monkeypatch.setattr(BigQueryWarehouses, "_query", lambda self, sql, dry_run=False: fake.run(None, sql, dry_run=dry_run))
    monkeypatch.setattr(graphview, "open_db", lambda *args: SimpleNamespace(close=lambda: None))
    monkeypatch.setattr(graphview, "connect", lambda db: SimpleNamespace(close=lambda: None))
    monkeypatch.setattr(graphview, "make_embedder", lambda: None)

    def engine_init(self, profile):
        self.profile = profile.pin()
        self.g = graphview.Graph(self.profile)
        self.w = open_warehouses(self.profile, ["retail"], [], self.g.warehouse_versions(), self.g.warehouse_inputs())

    monkeypatch.setattr(Engine, "__init__", engine_init)

    def stage(version, figure):
        estate = estate_of(prof)
        session = estate.stage("retail", ["CREATE TABLE sales (amount INTEGER)"], version)
        session.load("sales", ["amount"], [[str(figure)]])
        estate.publish("retail", version)
        session.close()
        live.clear()
        if version is not None:
            live["retail"] = version
        release.put(graph, release.LOADED, {"client": prof.client, "model_hash": HASH, "warehouses": dict(live)})
        release.put(graph, release.GATE, {"passed": True, "run_id": str(version), "model_hash": HASH})
        release.put(graph, release.SIGNOFF, {"principal": "steward", "run_id": str(version), "model_hash": HASH})
        entries = release.held(graph.parent, release.RELEASED)
        entries.pop(release.store_of(graph), None)
        release.write_marker(graph.parent, entries, release.RELEASED)

    return SimpleNamespace(prof=prof, graph=graph, fake=fake, live=live, kept=kept, stage=stage)


def figure(engine):
    with engine.w.cursor("retail") as cursor:
        return cursor.execute("SELECT SUM(amount) AS total FROM sales").fetchone()[0]


def test_old_figure_until_release_then_new_then_ledger_rollback(estate_release):
    c = estate_release
    c.stage(1, 100)
    release.release(c.prof, HASH)
    first = Engine.serving(c.prof)
    old = first.identity
    c.stage(2, 250)
    assert figure(first) == 100
    assert figure(Engine.serving(c.prof)) == 100
    assert figure(Engine.candidate(c.prof.at(HASH))) == 250
    c.live["retail"] = 99
    release.release(c.prof, HASH)
    second = Engine.serving(c.prof)
    assert figure(second) == 250
    assert second.identity == {**old, "warehouses": {"retail": 2}, "release": second.identity["release"]}
    assert second.identity["release"] != old["release"]
    assert c.prof.serve().identity() == second.identity
    assert released.read(c.graph.parent)["warehouses"] == {"retail": 2}
    release.release(c.prof, previous=True)
    assert figure(Engine.serving(c.prof)) == 100
    assert figure(second) == 250
    assert c.prof.serve().identity() == old
    assert release.served(c.prof.store / c.prof.client) == [old, second.identity]
    assert c.prof.model_hash() == HASH


def test_rollback_refuses_a_pruned_warehouse_without_moving_any_record(estate_release):
    c = estate_release
    c.stage(1, 100)
    release.release(c.prof, HASH)
    c.stage(2, 250)
    release.release(c.prof, HASH)
    estate_of(c.prof).drop("retail", 1)
    before = released.path(c.graph.parent).read_bytes()
    ledger = release.served(c.prof.store / c.prof.client)
    with pytest.raises(release.Unreleasable, match="retail.*v1"):
        release.release(c.prof, previous=True)
    assert released.path(c.graph.parent).read_bytes() == before
    assert release.served(c.prof.store / c.prof.client) == ledger
    assert figure(Engine.serving(c.prof)) == 250


def test_third_release_keeps_two_estate_versions(estate_release):
    c = estate_release
    for version in (1, 2, 3):
        c.stage(version, version * 100)
        release.release(c.prof, HASH)
    assert estate_of(c.prof).versions("retail") == [2, 3]
    assert "retail_v1_sales" not in c.fake.held()
    assert c.kept[-1] == {"retail": {2, 3}}
    assert [i["warehouses"] for i in release.served(c.prof.store / c.prof.client)] == [{"retail": 3}, {"retail": 2}]


def test_legacy_ledger_keeps_and_rolls_back_to_established_version_zero(estate_release):
    c = estate_release
    c.stage(None, 75)
    release.release(c.prof, HASH)
    root = c.prof.store / c.prof.client
    legacy = {"hash": HASH, "graph": None, "chunks": None}
    (root / release.SERVED).write_text(json.dumps([legacy]))
    c.stage(1, 125)
    release.release(c.prof, HASH)
    assert c.kept[-1] == {"retail": {0, 1}}
    assert [entry["identity"] for entry in release.kept(c.prof)] == release.served(root)
    with pytest.raises(release.Unreleasable, match="before release ids"):
        release.release(c.prof, previous=True)
    assert figure(Engine.serving(c.prof)) == 125


def test_a_missing_legacy_version_zero_is_named_and_refused(estate_release):
    c = estate_release
    c.stage(None, 75)
    release.release(c.prof, HASH)
    c.stage(1, 125)
    release.release(c.prof, HASH)
    estate_of(c.prof).drop("retail", None)
    with pytest.raises(release.Unreleasable, match="retail"):
        release.release(c.prof, previous=True)
    assert figure(Engine.serving(c.prof)) == 125


def test_a_missing_named_version_is_refused_even_when_version_zero_exists(estate_release):
    c = estate_release
    c.stage(None, 75)
    c.stage(1, 125)
    estate_of(c.prof).drop("retail", 1)
    with pytest.raises(release.Unreleasable, match="retail.*v1"):
        release.release(c.prof, HASH)
    assert c.prof.model_hash() is None


def test_changes_are_per_load_and_unmark_drops_them(estate_release):
    c = estate_release
    c.stage(1, 100)
    record = {"lock": {"written": False, "changes": []}, "estate": {"rebuilt": True}, "reloaded": True}
    assert release.changes(c.graph) is None
    release.mark_changes(c.graph, record)
    assert release.changes(c.graph) == record
    assert release.state(c.prof, HASH)["changes"] == record
    release.unmark(c.graph)
    assert release.changes(c.graph) is None
    assert release.state(c.prof, HASH)["changes"] is None


def test_mark_loaded_snapshots_live_warehouses(estate_release, monkeypatch):
    from kge.adapters import graphstore
    c = estate_release
    c.stage(3, 300)
    rows = [[HASH]], [["release-1", c.prof.client]], [[1]]
    results = iter(rows)
    monkeypatch.setattr(graphstore, "open_db", lambda *args: SimpleNamespace(close=lambda: None))
    monkeypatch.setattr(graphstore, "connect", lambda db: SimpleNamespace(
        close=lambda: None, execute=lambda query: SimpleNamespace(get_all=lambda: next(results))))
    assert release.mark(c.prof.at(HASH))["warehouses"] == {"retail": 3}
    c.live["retail"] = 4
    assert release.loaded(c.graph)["warehouses"] == {"retail": 3}


@pytest.mark.parametrize("published", [True, False])
def test_whole_identity_comparison_follows_a_warehouse_only_release(estate_release, monkeypatch, published):
    from kge.services.onboard import jobs
    c = estate_release
    c.stage(1, 100)
    release.release(c.prof, HASH)
    old = Engine.serving(c.prof)
    c.stage(2, 200)
    release.release(c.prof, HASH)
    switches = []
    app = SimpleNamespace(engine=lambda: old, switch=lambda: switches.append(True))
    monkeypatch.setattr(jobs, "_cloud", lambda: True)
    monkeypatch.setattr(jobs, "the_store", lambda: SimpleNamespace(get=lambda key: HASH.encode()))
    monkeypatch.setattr(jobs, "FETCHED", {})
    monkeypatch.setattr(jobs, "released_identity", lambda *args: c.prof.serve().identity() if published else None)
    monkeypatch.setattr(jobs, "fetch", lambda *args: None)
    jobs.follow_release(app, c.prof, {"id": "release-2", "step": "release", "state": "done"})
    assert switches == [True]


def test_fixture_versions_override_named_versions_and_inputs(estate_release):
    c = estate_release
    artifact = c.prof.artifacts / c.prof.client / HASH
    (artifact / "drafts.json").write_text(json.dumps({"format": 1, "items": [], "warehouses": {
        "retail": {"version": 9, "inputs": "a" * 64}, "leasing": {"version": 4, "inputs": "b" * 64}}}))
    c.stage(2, 200)
    g = Engine.candidate(c.prof.at(HASH)).g
    assert g.warehouse_versions() == {"retail": 2, "leasing": 4}
    assert g.warehouse_inputs() == {"leasing": "b" * 64}


def test_explicit_released_version_zero_overrides_named_version_on_the_fake(estate_release):
    c = estate_release
    artifact = c.prof.artifacts / c.prof.client / HASH
    (artifact / "drafts.json").write_text(json.dumps({"format": 1, "items": [], "warehouses": {
        "retail": {"version": 9, "inputs": "a" * 64}}}))
    c.stage(None, 75)
    c.stage(9, 900)
    release.put(c.graph, release.LOADED, {"client": c.prof.client, "model_hash": HASH, "warehouses": {"retail": 0}})
    release.release(c.prof, HASH)
    engine = Engine.serving(c.prof)
    assert engine.g.warehouse_versions() == {"retail": 0}
    assert engine.g.warehouse_inputs() == {}
    assert engine.w.prefix("retail") == "retail"
    assert figure(engine) == 75
    assert "retail_sales" in c.fake.held() and "retail_v0_sales" not in c.fake.held()


@pytest.mark.parametrize("published", [True, False])
@pytest.mark.parametrize("legacy_served", [True, False])
def test_legacy_identity_does_not_switch_again_on_each_poll(monkeypatch, published, legacy_served):
    from kge.services.onboard import jobs
    legacy = {"hash": HASH, "graph": "generation", "chunks": "dataset.chunks"}
    upgraded = {**legacy, "warehouses": {}}
    opened, named = (legacy, upgraded) if legacy_served else (upgraded, legacy)
    fetched, switches = [], []
    prof = SimpleNamespace(client="meridian", model_hash=lambda: HASH)
    prof.serve = lambda: SimpleNamespace(at=lambda h: SimpleNamespace(identity=lambda: named))
    app = SimpleNamespace(engine=lambda: SimpleNamespace(identity=opened, profile=prof),
                          switch=lambda: switches.append(True))
    monkeypatch.setattr(jobs, "_cloud", lambda: True)
    monkeypatch.setattr(jobs, "the_store", lambda: SimpleNamespace(get=lambda key: HASH.encode()))
    monkeypatch.setattr(jobs, "released_identity", lambda *args: named if published else None)
    monkeypatch.setattr(jobs, "fetch", lambda *args: fetched.append(True))
    monkeypatch.setattr(jobs, "FETCHED", {})
    job = {"id": "legacy-release", "step": "release", "state": "done"}
    for _ in range(3):
        jobs.follow_release(app, prof, job)
    assert switches == []
    assert len(fetched) == (0 if published else 1)


def test_legacy_ledger_identity_is_equal_to_explicit_empty_warehouses(tmp_path):
    legacy = {"hash": HASH, "graph": None, "chunks": None}
    (tmp_path / release.SERVED).write_text(json.dumps([legacy]))
    current = {**legacy, "warehouses": {}, "release": None}
    assert release.served(tmp_path, current) == [current]
    assert release.served(tmp_path, legacy) == [current]
    assert json.loads((tmp_path / release.SERVED).read_text()) == [current]


def test_retire_prunes_unreferenced_fixture_versions_and_preserves_unassessed_units(tmp_path, monkeypatch):
    from kge.adapters import graphstore, retrieval
    from kge.pipeline.profile import Profile
    monkeypatch.setenv("WAREHOUSE_BACKEND", "bigquery")
    monkeypatch.setenv("BIGQUERY_PROJECT", bigquery_fake.PROJECT)
    bigquery_fake.install(monkeypatch)
    prof = profile(tmp_path)
    fixture = prof.clients / prof.client / "fixture"
    fixture.mkdir(parents=True)
    (fixture / "schema.yaml").write_text("warehouses:\n  retail: {}\n")
    prof.estate().mkdir(parents=True, exist_ok=True)
    (prof.estate() / "summary.json").write_text(json.dumps({
        "version": 4, "units": ["retail"], "fixture_units": ["retail", "removed", "legacy"]}))
    estate = estate_of(prof)
    for unit in ("retail", "removed", "legacy", "independent"):
        for version in (0, 1, 2, 3, 4):
            session = estate.stage(unit, ["CREATE TABLE sales (amount INTEGER)"], version)
            session.load("sales", ["amount"], [[version]])
            estate.publish(unit, version)
            session.close()
    previous = {"hash": HASH, "graph": None, "chunks": None, "warehouses": {"retail": 2, "removed": 1}}
    current = {**previous, "warehouses": {"retail": 3, "removed": 2}}
    release.served(prof.store / prof.client, previous)
    monkeypatch.setattr(Profile, "identity", lambda self: current)
    monkeypatch.setattr(graphstore, "prune", lambda *args: [])
    monkeypatch.setattr(retrieval, "prune", lambda *args: [])
    assert release.retire(prof, HASH) == []
    assert estate.versions("retail") == [2, 3, 4]
    assert estate.versions("removed") == [1, 2]
    assert not estate.exists("legacy") and estate.versions("legacy") == []
    assert estate.exists("independent") and estate.versions("independent") == [1, 2, 3, 4]


@pytest.fixture
def legacy_estate_without_summary(tmp_path, monkeypatch):
    monkeypatch.setenv("WAREHOUSE_BACKEND", "bigquery")
    monkeypatch.setenv("BIGQUERY_PROJECT", bigquery_fake.PROJECT)
    fake = bigquery_fake.install(monkeypatch)
    prof = profile(tmp_path)
    fixture = prof.clients / prof.client / "fixture"
    fixture.mkdir(parents=True)
    (fixture / "schema.yaml").write_text("warehouses:\n  retail: {}\n")
    estate = estate_of(prof)
    for version in (0, 1, 2):
        session = estate.stage("retail", ["CREATE TABLE sales (amount INTEGER)"], version)
        session.load("sales", ["amount"], [[version + 10]])
        estate.publish("retail", version)
        session.close()
    assert not (prof.estate() / "summary.json").exists()
    return SimpleNamespace(prof=prof, estate=estate, fake=fake)


def test_retire_without_summary_preserves_legacy_and_unassessed_versions(legacy_estate_without_summary, monkeypatch):
    from kge.adapters import graphstore, retrieval
    from kge.pipeline.profile import Profile
    c = legacy_estate_without_summary
    legacy = {"hash": HASH, "graph": None, "chunks": None}
    monkeypatch.setattr(Profile, "identity", lambda self: legacy)
    monkeypatch.setattr(graphstore, "prune", lambda *args: [])
    monkeypatch.setattr(retrieval, "prune", lambda *args: [])
    assert release.retire(c.prof, HASH) == []
    assert c.estate.exists("retail") and c.estate.versions("retail") == [1, 2]
    assert c.fake.rows("retail_sales") == [(10,)]
    assert c.fake.rows("retail_v1_sales") == [(11,)]
    assert c.fake.rows("retail_v2_sales") == [(12,)]


def test_prune_omitted_unit_stays_whole_but_explicit_empty_set_drops_all(legacy_estate_without_summary):
    c = legacy_estate_without_summary
    assert warehouse_versions.prune_estate(c.prof, {}) == {}
    assert c.estate.exists("retail") and c.estate.versions("retail") == [1, 2]
    assert warehouse_versions.prune_estate(c.prof, {"retail": set()}) == {"retail": [0, 1, 2]}
    assert not c.estate.exists("retail") and c.estate.versions("retail") == []
    assert not any(name.startswith("retail_") for name in c.fake.held())


def test_previous_needs_a_second_ledger_entry(estate_release):
    with pytest.raises(release.Unreleasable, match="no previous release"):
        release.release(estate_release.prof, previous=True)


@pytest.fixture
def remote_ledger(estate_release, monkeypatch):
    from kge.adapters import graphstore
    from kge.adapters.graphstore import bigquery as graphs
    from kge.adapters.graphstore.gql import Schema
    c = estate_release
    c.stage(1, 100)
    c.stage(2, 200)
    monkeypatch.setenv("GRAPH_STORE", "bigquery")
    monkeypatch.setattr(graphstore, "prune", lambda *args: [])
    graph = c.prof.at(HASH).graph_path()
    schema = Schema.build([("GraphModel", "id", {"id": "STRING"})], [])
    pointers, identities, property_graphs = [], [], set()
    for version in (1, 2):
        generation = f"{version:08x}"
        g = graphs.Graph(graph)
        g.ns.prefix = graphs.prefix_of(HASH, generation)
        g.write(schema)
        pointer = graphstore.release_entry(graph)
        chunks = {"dataset": g.ns.dataset, "table": f"chunks_{HASH[:16]}_{generation}"}
        c.fake.run(None, f"CREATE TABLE `{g.ns.dataset}.{g.ns.prefix}GraphModel` (id STRING)")
        c.fake.run(None, f"INSERT INTO `{g.ns.dataset}.{g.ns.prefix}GraphModel` VALUES ('{HASH}')")
        c.fake.run(None, f"CREATE TABLE `{g.ns.dataset}.{chunks['table']}` (text STRING)")
        property_graphs.add(f"{g.ns.dataset}.{g.ns.prefix}graph")
        release_id = f"20261031T000000Z-{version:06x}"
        released.write(graph.parent, HASH, pointer, chunks, {"retail": version}, release_id)
        identity = c.prof.serve().at(HASH).identity()
        snapshot = history.snapshot_release(c.prof, HASH, release_id, None)
        history.archive_runtime(c.prof, HASH, release_id)
        release.keep_release(c.prof, {"identity": identity, "snapshot": snapshot, "inputs": None, "roster": None})
        release.served(c.prof.store / c.prof.client, identity)
        pointers.append(released.read(graph.parent))
        identities.append(identity)
    release.swap(c.prof, HASH)

    def query(client, sql, parameters, read=False):
        project = client.project
        name = sql.split("GRAPH_TABLE(", 1)[1].split("`", 2)[1].removeprefix(project + ".")
        if name not in property_graphs:
            raise RuntimeError(f"Not found: {name}")
        table = name.rsplit(".", 1)[1].removesuffix("graph") + "GraphModel"
        return c.fake.run(None, f"SELECT id AS c0 FROM `{table}`")

    monkeypatch.setattr(graphs.Client, "query", query)
    return SimpleNamespace(c=c, graph=graph, records=pointers, identities=identities, property_graphs=property_graphs)


def test_ledger_rollback_repoints_graph_chunks_and_warehouses_together(remote_ledger):
    r = remote_ledger
    release.release(r.c.prof, previous=True)
    assert released.read(r.graph.parent) == r.records[0]
    assert r.c.prof.serve().identity() == r.identities[0]
    assert release.served(r.c.prof.store / r.c.prof.client) == r.identities
    assert r.c.prof.model_hash() == HASH
    assert figure(Engine.serving(r.c.prof)) == 100


@pytest.mark.parametrize("part", ["table", "graph", "chunks"])
def test_ledger_rollback_names_missing_graph_or_chunks_and_changes_nothing(remote_ledger, part):
    r = remote_ledger
    old = r.records[0]
    if part == "graph":
        r.property_graphs.remove(f"{old['graph']['dataset']}.{old['graph']['graph']}")
    else:
        table = old["graph"]["prefix"] + "GraphModel" if part == "table" else old["chunks"]["table"]
        r.c.fake.run(None, f"DROP TABLE `{table}`")
    before = released.path(r.graph.parent).read_bytes()
    with pytest.raises(release.Unreleasable, match="chunks" if part == "chunks" else "graph generation"):
        release.release(r.c.prof, previous=True)
    assert released.path(r.graph.parent).read_bytes() == before
    assert r.c.prof.serve().identity() == r.identities[1]
    assert release.served(r.c.prof.store / r.c.prof.client) == list(reversed(r.identities))


def test_cli_previous_selects_the_ledger_and_rejects_a_hash(estate_release, monkeypatch):
    c = estate_release
    for version in (1, 2):
        c.stage(version, version * 100)
        release.release(c.prof, HASH)
    monkeypatch.setattr(release, "resolve", lambda name: c.prof)
    assert release.main(["--previous"]) == 0
    assert figure(Engine.serving(c.prof)) == 100
    with pytest.raises(SystemExit) as raised:
        release.main(["--previous", "--hash", HASH])
    assert raised.value.code == 2


def test_rollback_does_not_approve_an_unreleased_reload(estate_release):
    c = estate_release
    for version in (1, 2):
        c.stage(version, version * 100)
        release.release(c.prof, HASH)
    c.stage(3, 300)
    release.write_marker(c.graph.parent, {}, release.SIGNOFF)
    release.release(c.prof, previous=True)
    assert figure(Engine.serving(c.prof)) == 100
    assert release.marked(c.graph, release.RELEASED) is None
    assert c.kept[-1] == {"retail": {1, 2, 3}}
    with pytest.raises(release.Unreleasable, match="not signed off"):
        release.release(c.prof, HASH)


def test_ledger_rollback_to_another_hash_restores_its_pack_even_after_a_reload(estate_release, monkeypatch):
    c = estate_release
    c.stage(1, 100)
    release.release(c.prof, HASH)
    other = "cd" * 32
    artifact = c.prof.artifacts / c.prof.client / other
    artifact.mkdir()
    (artifact / "manifest.json").write_text("{}")
    monkeypatch.setattr(load, "read_artifact", lambda folder, client: {"model_hash": folder.name})
    c.stage(2, 200)
    graph = c.prof.at(other).graph_path()
    graph.parent.mkdir()
    graph.touch()
    release.put(graph, release.LOADED, {"client": c.prof.client, "model_hash": other, "warehouses": {"retail": 2}})
    release.release(c.prof, other, ungated=True)
    release.unmark(c.graph)
    restored = []
    monkeypatch.setattr(history, "check_rollback_allowed", lambda *args: None)
    monkeypatch.setattr(history, "_capture_store", lambda *args: {})
    monkeypatch.setattr(history, "removed_units_for_restore", lambda *args: set())
    monkeypatch.setattr(history, "added_units_for_restore", lambda *args: set())
    real_restore = history.restore_release
    def restore(prof, entry, **kwargs):
        restored.append(entry["identity"]["hash"])
        return real_restore(prof, entry, **kwargs)
    monkeypatch.setattr(history, "restore_release", restore)
    release.release(c.prof, previous=True)
    assert restored == [HASH]
    assert c.prof.model_hash() == HASH
    assert c.prof.serve().identity()["warehouses"] == {"retail": 1}
    assert figure(Engine.serving(c.prof)) == 100


def test_third_release_can_retire_the_established_version_zero(estate_release):
    c = estate_release
    for version in (None, 1, 2):
        c.stage(version, (version or 0) * 100)
        release.release(c.prof, HASH)
    assert c.kept[-1] == {"retail": {1, 2}}
    assert "retail_sales" not in c.fake.held()
    assert estate_of(c.prof).versions("retail") == [1, 2]
