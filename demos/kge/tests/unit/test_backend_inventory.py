"""The cleanup ports, on the local adapters and a BigQuery stand-in: inventory names every held object except the
portal's, never creates a dataset, and drops only this client's selected objects, even with no local pointers."""
from functools import partial
from types import SimpleNamespace

import pytest

from kge.adapters import graphstore, retrieval
from kge.adapters.graphstore import bigquery as graph_bq
from kge.adapters.graphstore.ladybug import LadybugStore
from kge.adapters.warehouse.bigquery_estate import BigQueryEstate
from kge.adapters.warehouse.duckdb import DuckDBEstate
from kge.pipeline import profile
from kge.ports import EstateLimits, EstateWarehouses, GraphStore, HeldObject
from tests import bigquery_fake

CLIENT = "new-client"
PREFIX = "pilot"
WAREHOUSES = "pilot_new_client_warehouses"
GRAPH = "pilot_new_client_graph"
CANDIDATES = "pilot_new_client_candidates"
OTHER = "pilot_other_graph"
GEN1 = "g" + "a" * 16 + "_12345678_"
GEN2 = "g" + "b" * 16 + "_87654321_"
CHUNKS1 = "chunks_" + "a" * 16 + "_12345678"
CHUNKS2 = "chunks_" + "b" * 16 + "_87654321"


@pytest.fixture
def cloud(monkeypatch, tmp_path):
    monkeypatch.setenv("BIGQUERY_PROJECT", bigquery_fake.PROJECT)
    monkeypatch.setenv("BIGQUERY_DATASET_PREFIX", PREFIX)
    monkeypatch.setenv("GRAPH_STORE", "bigquery")
    monkeypatch.setenv("WAREHOUSE_BACKEND", "bigquery")
    monkeypatch.setenv("RETRIEVAL_BACKEND", "bigquery")
    monkeypatch.setenv("GRAPH_DATASET", "candidates")
    prof = SimpleNamespace(client=CLIENT, store=tmp_path / "stores")
    monkeypatch.setattr(profile, "resolve", lambda: prof)
    fake = bigquery_fake.install(monkeypatch)
    monkeypatch.setattr(graph_bq.Client, "query", lambda self, sql, parameters, read=False: fake.run(self, sql))
    return SimpleNamespace(fake=fake, prof=prof, estate=BigQueryEstate(CLIENT))


def create(fake, dataset, name, kind="table"):
    ref = f"`{bigquery_fake.PROJECT}.{dataset}.{name}`"
    if kind == "property graph":
        sql = f"CREATE PROPERTY GRAPH {ref} NODE TABLES (`{bigquery_fake.PROJECT}.{dataset}.node` KEY(id))"
    elif kind == "view":
        sql = f"CREATE VIEW {ref} AS SELECT 1 AS id"
    else:
        sql = f"CREATE TABLE {ref} (id INT64)"
    fake.run(None, sql)
    return HeldObject(dataset, name, kind)


def test_local_ports_are_complete_and_inventory_does_not_remove_local_files(tmp_path, monkeypatch):
    monkeypatch.setenv("GRAPH_STORE", "ladybug")
    monkeypatch.setenv("WAREHOUSE_BACKEND", "duckdb")
    monkeypatch.setenv("RETRIEVAL_BACKEND", "graph")
    path = tmp_path / "retail.duckdb"
    path.write_bytes(b"kept")
    estate = DuckDBEstate(tmp_path)
    assert isinstance(estate, EstateWarehouses)
    assert isinstance(LadybugStore(), GraphStore)
    assert isinstance(graph_bq.BigQueryStore(), GraphStore)
    assert estate.limits() == EstateLimits(None, None)
    assert estate.inventory() == estate.clear([HeldObject("x", "retail", "table")]) == []
    assert graphstore.generations() == graphstore.drop_generations([GEN1]) == []
    assert graphstore.inventory() == graphstore.clear([HeldObject("x", "graph", "table")]) == []
    assert retrieval.chunks_tables(None) == retrieval.drop_chunks(None, ["x.y"]) == []
    assert retrieval.inventory(None) == retrieval.clear(None, [HeldObject("x", "y", "table")]) == []
    assert path.read_bytes() == b"kept"


@pytest.mark.parametrize("declared", ["INTEGER", "INT8", "text", " VARCHAR(24) ", "BOOLEAN", "TIMESTAMP", "DATE",
                                     "DOUBLE", "DECIMAL", "NUMERIC(38, 9)", "DECIMAL(38,38)", "CHAR(20)",
                                     "DOUBLE PRECISION", "INT64", "TIMESTAMP(6)"])
def test_supported_types_are_accepted_by_both_backends(tmp_path, declared):
    assert DuckDBEstate(tmp_path).refuses_type(declared) is None
    assert BigQueryEstate(CLIENT).refuses_type(declared) is None


@pytest.mark.parametrize("declared", ["", "NOT_A_TYPE", "INTEGER(2)", "DECIMAL(0,0)", "DECIMAL(39,1)",
                                     "DECIMAL(3,4)", "VARCHAR(0)", "VARCHAR(foo)", "INTEGER; DROP TABLE t", "INTEGER); DROP TABLE t; --"])
def test_unsupported_types_have_a_reason_without_creating_a_warehouse(tmp_path, declared):
    assert BigQueryEstate(CLIENT).refuses_type(declared)
    assert not list(tmp_path.iterdir())


def test_types_and_limits_come_from_the_backend(tmp_path):
    local = DuckDBEstate(tmp_path)
    assert local.refuses_type("UUID") is None
    assert BigQueryEstate(CLIENT).refuses_type("UUID")
    assert local.refuses_type("NOT_A_TYPE")
    assert local.refuses_type("DECIMAL(39,0)")
    assert local.limits() == EstateLimits(None, None)
    assert BigQueryEstate(CLIENT).limits() == EstateLimits(
        description_chars=1024, label_chars=63, table_bytes=1024, column_chars=300,
        columns=10000, primary_key_columns=16, foreign_keys=64, self_references=False)


def test_missing_datasets_are_empty_and_no_inventory_creates_them(cloud):
    assert isinstance(cloud.estate, EstateWarehouses)
    assert cloud.estate.inventory() == []
    assert graphstore.inventory() == graphstore.generations() == []
    assert retrieval.inventory(cloud.prof) == retrieval.chunks_tables(cloud.prof) == []
    assert cloud.estate.clear([HeldObject(WAREHOUSES, "gone", "table")]) == []
    assert graphstore.drop_generations([GEN1]) == []
    assert retrieval.drop_chunks(cloud.prof, [f"{GRAPH}.{CHUNKS1}"]) == []
    assert cloud.fake.datasets == set()
    assert all(sql.startswith("SELECT ") for sql in cloud.fake.statements)


def test_warehouse_inventory_names_tables_views_and_catalogue_but_never_portal(cloud):
    fake = cloud.fake
    expected = [create(fake, WAREHOUSES, "retail_sales"), create(fake, WAREHOUSES, "retail_v1_sales"),
                create(fake, WAREHOUSES, "_kge_catalogue"), create(fake, WAREHOUSES, "latest", "view")]
    portal = create(fake, WAREHOUSES, "_portal_activity")
    portal_view = create(fake, WAREHOUSES, "_portal_view", "view")
    other = create(fake, OTHER, "retail_sales")
    fake.run(None, f"INSERT INTO `{bigquery_fake.PROJECT}.{WAREHOUSES}.{portal.name}` VALUES (7)")
    fake.run(None, f"INSERT INTO `{bigquery_fake.PROJECT}.{OTHER}.{other.name}` VALUES (9)")
    assert cloud.estate.inventory() == sorted(expected)
    assert cloud.estate.clear(expected + [portal, portal_view, other]) == sorted(expected, key=lambda obj: (obj.kind == "table", obj.name))
    assert set(fake.objects) == {(obj.dataset, obj.name) for obj in (portal, portal_view, other)}
    assert fake.datasets == {WAREHOUSES, OTHER}
    assert cloud.estate.clear(expected) == []
    for obj, value in ((portal, "7"), (other, "9")):
        result = fake.run(None, f"SELECT id FROM `{bigquery_fake.PROJECT}.{obj.dataset}.{obj.name}`")
        assert result["rows"] == [{"f": [{"v": value}]}]


def test_graph_inventory_covers_both_datasets_without_pointers_and_drops_graphs_first(cloud):
    fake = cloud.fake
    expected = []
    for dataset, generation in ((GRAPH, GEN1), (CANDIDATES, GEN2)):
        expected += [create(fake, dataset, generation + "node"), create(fake, dataset, "latest", "view"),
                     create(fake, dataset, generation + "graph", "property graph"), create(fake, dataset, "appstate")]
        create(fake, dataset, "_portal_metadata")
        create(fake, dataset, "_portal_graph", "property graph")
    other = create(fake, OTHER, GEN1 + "node")
    assert graphstore.inventory() == sorted(expected)
    assert graphstore.generations() == [GEN1, GEN2]
    offset = len(fake.statements)
    dropped = graphstore.clear(list(reversed(expected)) + [other, HeldObject(GRAPH, "_portal_metadata", "table")])
    assert set(dropped) == set(expected)
    drops = [sql for sql in fake.statements[offset:] if sql.startswith("DROP")]
    assert all(sql.startswith("DROP PROPERTY GRAPH") for sql in drops[:2])
    assert all(sql.startswith("DROP VIEW") for sql in drops[2:4])
    assert all(name.startswith("_portal_") or dataset == OTHER for dataset, name in fake.objects)
    assert fake.datasets == {GRAPH, CANDIDATES, OTHER}
    assert graphstore.clear(expected) == []


def test_drop_generations_uses_held_objects_in_both_datasets_and_skips_absent_prefixes(cloud, monkeypatch):
    fake = cloud.fake
    for dataset in (GRAPH, CANDIDATES):
        create(fake, dataset, GEN1 + "node")
        create(fake, dataset, GEN1 + "graph", "property graph")
    create(fake, GRAPH, GEN1 + "view", "view")
    create(fake, CANDIDATES, GEN2 + "graph", "property graph")
    create(fake, GRAPH, CHUNKS1)
    create(fake, GRAPH, "_portal_activity")
    create(fake, OTHER, GEN1 + "node")
    called = []
    original = graph_bq.drop

    def drop(client, project_id, dataset, prefixes, tables, views=()):
        called.append((dataset, prefixes))
        return original(client, project_id, dataset, prefixes, tables, views)

    monkeypatch.setattr(graph_bq, "drop", drop)
    assert graphstore.generations() == [GEN1, GEN2]
    assert graphstore.drop_generations(iter([GEN1, GEN1, "absent", ""])) == [GEN1]
    assert called == [(CANDIDATES, [GEN1]), (GRAPH, [GEN1])]
    assert graphstore.generations() == [GEN2]
    assert (OTHER, GEN1 + "node") in fake.objects
    assert (GRAPH, CHUNKS1) in fake.objects
    assert (GRAPH, "_portal_activity") in fake.objects
    assert graphstore.drop_generations([GEN1]) == []
    assert graphstore.drop_generations([GEN2]) == [GEN2]
    assert graphstore.generations() == []


def test_chunks_inventory_and_drop_are_scoped_by_client_dataset_and_exact_table_name(cloud):
    fake = cloud.fake
    first = create(fake, GRAPH, CHUNKS1)
    same_name = create(fake, CANDIDATES, CHUNKS1)
    second = create(fake, CANDIDATES, CHUNKS2)
    unrelated = [create(fake, GRAPH, "chunks_old"), create(fake, GRAPH, CHUNKS2, "view"),
                 create(fake, GRAPH, "_portal_chunks"), create(fake, OTHER, CHUNKS1),
                 create(fake, GRAPH, GEN1 + "node")]
    assert retrieval.inventory(cloud.prof) == sorted([first, second, same_name])
    assert retrieval.chunks_tables(cloud.prof) == [f"{obj.dataset}.{obj.name}" for obj in sorted([first, second, same_name])]
    assert retrieval.drop_chunks(cloud.prof, [f"{GRAPH}.{CHUNKS1}", CHUNKS2, f"{OTHER}.{CHUNKS1}"]) == [f"{GRAPH}.{CHUNKS1}"]
    assert retrieval.clear(cloud.prof, [first, second, same_name] + unrelated) == sorted([second, same_name])
    assert retrieval.inventory(cloud.prof) == []
    assert set(fake.objects) == {(obj.dataset, obj.name) for obj in unrelated}
    assert retrieval.drop_chunks(cloud.prof, [f"{GRAPH}.{CHUNKS1}"]) == []


def test_cleanup_can_deduplicate_the_graph_and_retrieval_inventory(cloud):
    create(cloud.fake, GRAPH, CHUNKS1)
    objects = sorted(set(graphstore.inventory() + retrieval.inventory(cloud.prof)))
    assert objects == [HeldObject(GRAPH, CHUNKS1, "table")]
    assert graphstore.clear(objects) == objects
    assert retrieval.clear(cloud.prof, objects) == []


@pytest.mark.parametrize("error", ["BigQuery error (HTTP 403): Access Denied", "BigQuery error (HTTP 500): backendError",
                                   "Not found: Table missing_metadata_view"])
def test_inventory_does_not_hide_permission_service_or_metadata_errors(cloud, monkeypatch, error):
    def fail(*args, **kwargs):
        raise RuntimeError(error)

    monkeypatch.setattr(cloud.estate.loader, "run", fail)
    monkeypatch.setattr(graph_bq.Client, "query", fail)
    monkeypatch.setattr(bigquery_fake.BigQueryLoader, "run", fail)
    with pytest.raises(RuntimeError, match=error.split(":")[-1].strip()):
        cloud.estate.inventory()
    with pytest.raises(RuntimeError, match=error.split(":")[-1].strip()):
        graphstore.inventory()
    with pytest.raises(RuntimeError, match=error.split(":")[-1].strip()):
        retrieval.inventory(cloud.prof)


@pytest.mark.parametrize("backend", ["estate", "graph", "retrieval"])
def test_clear_only_removes_selected_objects_and_a_failure_is_not_success(cloud, monkeypatch, backend):
    if backend == "estate":
        dataset, names, clear = WAREHOUSES, ("a", "b"), cloud.estate.clear
    elif backend == "graph":
        dataset, names, clear = GRAPH, ("a", "b"), graphstore.clear
    else:
        dataset, names = GRAPH, (CHUNKS1, CHUNKS2)
        clear = partial(retrieval.clear, cloud.prof)
    first, kept = [create(cloud.fake, dataset, name) for name in names]
    original = cloud.fake.run

    def fail(loader, sql, *args, **kwargs):
        if sql.startswith("DROP"):
            raise RuntimeError("BigQuery error (HTTP 403): Access Denied")
        return original(loader, sql, *args, **kwargs)

    monkeypatch.setattr(cloud.fake, "run", fail)
    with pytest.raises(RuntimeError, match="Access Denied"):
        clear([first])
    assert (dataset, first.name) in cloud.fake.objects
    monkeypatch.setattr(cloud.fake, "run", original)
    assert clear([HeldObject(dataset, kept.name, "view")]) == []
    assert clear([first, first]) == [first]
    assert (dataset, first.name) not in cloud.fake.objects
    assert (dataset, kept.name) in cloud.fake.objects


@pytest.mark.parametrize("backend", ["estate", "graph", "retrieval"])
def test_clear_skips_an_object_that_disappeared_after_listing(cloud, monkeypatch, backend):
    if backend == "estate":
        dataset, name, clear = WAREHOUSES, "gone", cloud.estate.clear
    elif backend == "graph":
        dataset, name, clear = GRAPH, "gone", graphstore.clear
    else:
        dataset, name = GRAPH, CHUNKS1
        clear = partial(retrieval.clear, cloud.prof)
    obj = create(cloud.fake, dataset, name)
    original = cloud.fake.run

    def vanished(loader, sql, *args, **kwargs):
        result = original(loader, sql, *args, **kwargs)
        if sql.startswith("DROP"):
            raise RuntimeError(f"BigQuery error (HTTP 404): Not found: Table {dataset}.{name}")
        return result

    monkeypatch.setattr(cloud.fake, "run", vanished)
    assert clear([obj]) == []
    assert (dataset, name) not in cloud.fake.objects


def test_stand_in_lists_and_drops_all_three_kinds_and_keeps_datasets(cloud):
    fake = cloud.fake
    objects = [create(fake, GRAPH, "table_a"), create(fake, GRAPH, "view_a", "view"),
               create(fake, GRAPH, "graph_a", "property graph")]
    assert len(graphstore.inventory()) == 3
    for obj in reversed(objects):
        sql = f"DROP {obj.kind.upper()} `{bigquery_fake.PROJECT}.{obj.dataset}.{obj.name}`"
        fake.run(None, sql)
        with pytest.raises(RuntimeError, match="Not found"):
            fake.run(None, sql)
        fake.run(None, sql.replace(" `", " IF EXISTS `", 1))
    assert fake.objects == {}
    assert fake.datasets == {GRAPH}
    assert graphstore.inventory() == []
