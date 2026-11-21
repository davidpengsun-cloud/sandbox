"""The reference runtime's SQL dialect is the serving warehouses' own, read at run time (issue #565): the graph's
runtime subgraph leaves it unset, whatever the compiled Runtime node holds, so the runtime layer labels it with the
warehouses' sql_dialect (BigQuery's on BigQuery) rather than a fixed duckdb. A declared runtime keeps the dialect its
connection declares."""
from types import SimpleNamespace

from kge.adapters.warehouse.runtimes import REFERENCE, RuntimeManager
from tests.unit.test_graph_path import vocabulary_graph

RUNTIMES = [
    {"id": REFERENCE, "label": "Reference", "kind": "internal", "dialect": "duckdb", "location": "internal"},
    {"id": "declared", "label": "Declared", "kind": "external", "dialect": "bigquery", "location": "somewhere"},
]


def subgraph(tmp_path) -> dict:
    def q(cypher, params=None):
        return [dict(r) for r in RUNTIMES] if cypher.startswith("MATCH (rt:Runtime) RETURN") else []
    graph = vocabulary_graph(q)
    graph._catalogue = None
    graph.profile = SimpleNamespace(artifacts=tmp_path, client="client", model_hash=lambda: "hash", warehouses=tmp_path)
    return graph.runtime_subgraph()


def test_the_subgraph_leaves_the_reference_runtimes_dialect_unset_and_keeps_a_declared_ones(tmp_path):
    runtimes = subgraph(tmp_path)["runtimes"]
    assert runtimes[REFERENCE]["dialect"] is None
    assert runtimes["declared"]["dialect"] == "bigquery"


def test_the_reference_runtime_takes_the_serving_warehouses_dialect(tmp_path):
    reference = {REFERENCE: subgraph(tmp_path)["runtimes"][REFERENCE]}
    for dialect in ("bigquery", "duckdb"):
        manager = RuntimeManager(SimpleNamespace(sql_dialect=dialect, units=["wealth"], folder=None))
        manager.configure({"runtimes": reference})
        assert manager.runtimes[REFERENCE]["dialect"] == dialect
        assert manager.health()[REFERENCE]["dialect"] == dialect
