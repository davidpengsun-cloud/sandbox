"""The graph capability check executes DDL in the selected dataset before onboarding can start."""
import re

import pytest

from kge.adapters import bigquery_preflight, graph_creation_preflight
from kge.adapters.bigquery import BigQueryLoader


class Probe:
    def __init__(self, failure=None, cleanup_failure=False):
        self.failure = failure
        self.cleanup_failure = cleanup_failure
        self.calls = []
        self.table = None
        self.graph = None

    def run(self, sql, dataset):
        self.calls.append((sql, dataset))
        name = re.search(r"`([^`]+)`", sql)[1]
        if sql.startswith("CREATE TABLE"):
            self.table = name
        elif sql.startswith("CREATE PROPERTY GRAPH"):
            assert self.table in sql
            if self.failure:
                raise self.failure
            self.graph = name
        elif sql.startswith("DROP PROPERTY GRAPH"):
            if self.cleanup_failure:
                raise RuntimeError("HTTP 403: cleanup denied")
            self.graph = None
        else:
            assert sql.startswith("DROP TABLE") and self.graph is None
            self.table = None
        return {"jobComplete": True}


def test_graph_probe_creates_and_cleans_in_dependency_order():
    probe = Probe()
    graph_creation_preflight(probe, "pilot-project", "custom_aster_bank_graph")
    assert len(probe.calls) == 4
    assert all(dataset == "custom_aster_bank_graph" for _, dataset in probe.calls)
    assert all("pilot-project.custom_aster_bank_graph._kge_preflight_" in sql for sql, _ in probe.calls)
    assert probe.table is None and probe.graph is None
    assert "OR REPLACE" not in probe.calls[0][0]


@pytest.mark.parametrize("failure,action", [
    (RuntimeError("Graph is not supported with Standard edition"), "on-demand support graph creation"),
    (RuntimeError("Not found: Dataset in location EU"), "execution location"),
    (RuntimeError("HTTP 403: permission bigquery.propertyGraphs.create denied"), "grant the job account"),
    (RuntimeError("HTTP 503: backendError"), "temporary failure"),
    (TimeoutError("job did not finish"), "temporary failure"),
    (RuntimeError("unexpected graph definition"), "check the BigQuery error"),
])
def test_graph_refusal_preserves_reason_and_removes_probe(failure, action):
    probe = Probe(failure)
    with pytest.raises(RuntimeError) as caught:
        graph_creation_preflight(probe, "p", "d")
    assert str(failure) in str(caught.value) and action in str(caught.value)
    assert probe.table is None and probe.graph is None


def test_failed_cleanup_preserves_primary_failure_and_never_drops_a_referenced_table():
    probe = Probe(RuntimeError("Standard edition unsupported"), cleanup_failure=True)
    with pytest.raises(RuntimeError, match="Standard edition unsupported.*cleanup failed.*cleanup denied"):
        graph_creation_preflight(probe, "p", "d")
    assert len(probe.calls) == 3 and probe.table is not None


def test_cleanup_failure_after_success_is_not_a_pass():
    probe = Probe(cleanup_failure=True)
    with pytest.raises(RuntimeError, match="probe created, but cleanup failed"):
        graph_creation_preflight(probe, "p", "d")
    assert probe.graph is not None


def test_configured_graph_dataset_is_probed_and_local_graph_is_not(monkeypatch):
    monkeypatch.setattr("kge.pipeline.envfile.load_env", lambda *a, **k: None)
    monkeypatch.setenv("WAREHOUSE_BACKEND", "bigquery")
    monkeypatch.setenv("BIGQUERY_DATASET_PREFIX", "custom")
    monkeypatch.setenv("GRAPH_DATASET", "candidates")
    monkeypatch.setenv("GRAPH_STORE", "ladybug")
    monkeypatch.setattr(BigQueryLoader, "account", lambda self: (None, "pilot"))
    monkeypatch.setattr(BigQueryLoader, "tables", lambda *args: [])
    probe = Probe()
    monkeypatch.setattr(BigQueryLoader, "run", lambda self, *args, **kwargs: probe.run(*args, **kwargs))
    assert bigquery_preflight("aster-bank") == "pilot"
    assert probe.calls == []
    monkeypatch.setenv("GRAPH_STORE", "bigquery")
    assert bigquery_preflight("aster-bank") == "pilot"
    assert len(probe.calls) == 4
    assert all(dataset == "custom_aster_bank_candidates" for _, dataset in probe.calls)


def test_staging_plan_uses_selected_backends_and_never_calls_embedding(monkeypatch):
    from kge.adapters import preflight_loads
    from kge.adapters.model import embed
    monkeypatch.setattr("kge.pipeline.envfile.load_env", lambda *a, **k: None)
    checked = []
    monkeypatch.setattr(BigQueryLoader, "preflight_staging", lambda self, size: checked.append((self.client, size)),
                        raising=False)
    monkeypatch.setenv("EMBEDDING_PROVIDER", "vertex")
    monkeypatch.setenv("EMBEDDING_DIM", "768")
    def no_embedding(*args, **kwargs):
        pytest.fail("staging planning must not construct an embedder or request embeddings")
    monkeypatch.setattr(embed, "make_embedder", no_embedding)
    monkeypatch.setattr(embed.VertexEmbedder, "__init__", no_embedding)
    monkeypatch.setattr(embed.VertexEmbedder, "embed", no_embedding)
    monkeypatch.setenv("WAREHOUSE_BACKEND", "duckdb")
    monkeypatch.setenv("RETRIEVAL_BACKEND", "duckdb")
    preflight_loads("aster", [8_000_000], ["x"] * 300)
    assert checked == []
    monkeypatch.setenv("RETRIEVAL_BACKEND", "bigquery")
    preflight_loads("aster", [8_000_000], ["x"] * 300)
    assert checked[0][0] == "aster" and 5 * 1024 * 1024 < checked[0][1] < 8_000_000
    monkeypatch.setenv("WAREHOUSE_BACKEND", "bigquery")
    preflight_loads("aster", [8_000_000], ["x"] * 300)
    assert checked[-1] == ("aster", 8_000_000)
