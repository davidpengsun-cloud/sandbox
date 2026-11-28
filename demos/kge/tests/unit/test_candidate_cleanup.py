"""Copilot on #97 (kge/improve/abtest.py): an improvement run builds a candidate's graph generation and chunks table in
the client's candidates dataset, and its work, which holds their pointers, is not uploaded, so once the container exits
nothing could prune them. The candidate's evaluation drops its own generation and chunks table when it ends, passed or
failed, through the graph store and retrieval ports (on Ladybug and the graph's own vectors, nothing, as before), and
the candidates dataset's default table expiration is the backstop for a run that crashes. On stand-ins: no BigQuery is
called."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from kge.adapters import graphstore, released, retrieval
from kge.adapters.graphstore import bigquery as bq
from kge.adapters.graphstore.gql import Schema
from kge.adapters.retrieval import bigquery as bqr
from kge.graph import release as rel
from kge.improve import CannotImprove, abtest
from kge.pipeline.profile import Profile

HASH = "cd" * 32
GEN = "5e5e5e5e"
TABLE = f"chunks_{HASH[:16]}_{GEN}"


def profile(tmp_path: Path) -> Profile:
    return Profile(id="meridian", client="meridian", clients=tmp_path / "clients", artifacts=tmp_path / "artifacts",
                   store=tmp_path / "stores", landing=tmp_path / "landing", warehouses=tmp_path / "warehouses",
                   identities=tmp_path / "identities.yaml", baseline=None, registry=None)


@pytest.fixture
def candidate(monkeypatch, tmp_path):
    """A candidate loaded into the candidates dataset, as build_candidate leaves it, and the statements BigQuery is sent."""
    monkeypatch.setenv("GRAPH_STORE", "bigquery")
    monkeypatch.setenv("RETRIEVAL_BACKEND", "bigquery")
    monkeypatch.setenv("BIGQUERY_PROJECT", "proj")
    monkeypatch.setenv("GRAPH_DATASET", "candidates")
    prof = profile(tmp_path)
    graph = prof.at(HASH).graph_path()
    made = bq.Graph(graph)
    made.ns.prefix = bq.prefix_of(HASH, GEN)
    made.write(Schema.build([], []))
    retrieval.name_chunks(prof.at(HASH), "kge_meridian_candidates", TABLE)
    rel.write_marker(graph.parent, {"bigquery": {"client": "meridian", "model_hash": HASH, "release": "r", "chunks": 1}},
                     rel.LOADED)
    monkeypatch.delenv("GRAPH_DATASET")
    sent: list[str] = []
    prefix = bq.prefix_of(HASH, GEN)

    def graph_query(self, sql, parameters):
        sent.append(sql)
        if "INFORMATION_SCHEMA" in sql:
            return {"rows": [{"f": [{"v": f"{prefix}{t}"}]} for t in ("Unit", "Term", "HAS_TERM")]}
        return {}

    def chunks_query(sql, project=None):
        sent.append(sql)
        return []
    monkeypatch.setattr(bq.Client, "query", graph_query)
    monkeypatch.setattr(bqr, "bq_query", chunks_query)
    return prof, graph, sent


def dropped_everything(sent: list[str], graph: Path) -> None:
    prefix = bq.prefix_of(HASH, GEN)
    drops = " ".join(s for s in sent if "DROP" in s)
    assert f"DROP PROPERTY GRAPH IF EXISTS `proj.kge_meridian_candidates.{prefix}graph`" in drops, drops
    for t in ("Unit", "Term", "HAS_TERM"):
        assert f"DROP TABLE IF EXISTS `proj.kge_meridian_candidates.{prefix}{t}`" in drops, t
    assert f"DROP TABLE IF EXISTS `proj.kge_meridian_candidates.{TABLE}`" in drops
    assert not graph.exists() and not (graph.parent / "chunks.bigquery").exists(), "no pointer names what was dropped"
    assert rel.loaded(graph) is None, "the candidate is no longer loaded"


def test_the_evaluation_drops_the_candidate_s_generation_and_chunks_when_it_passes(candidate, monkeypatch):
    prof, graph, sent = candidate

    def margin(args, prof_, force_rules=False, ok_codes=(0,)):
        rel.write_marker(graph.parent, {**rel.held(graph.parent, rel.GATE),
                                        "bigquery": {"passed": True, "run_id": "r1", "model_hash": HASH}}, rel.GATE)
        (prof.run_dir() / "runs" / "r1").mkdir(parents=True)
        (prof.run_dir() / "runs" / "r1" / "records.jsonl").write_text(json.dumps({"set": "golden", "id": "q1"}) + "\n")
        return type("P", (), {"stdout": "V01 passed\n", "stderr": ""})()
    monkeypatch.setattr(abtest, "_run_cmd", margin)
    out = abtest.test_candidate(prof, HASH)
    assert out["after_run_id"] == "r1" and out["gate_passed"] is True
    dropped_everything(sent, graph)


def test_the_evaluation_drops_the_candidate_s_generation_and_chunks_when_it_fails(candidate, monkeypatch):
    prof, graph, sent = candidate

    def margin(*args, **kwargs):
        raise CannotImprove("kge.eval.margin failed (exit 2)")
    monkeypatch.setattr(abtest, "_run_cmd", margin)
    with pytest.raises(CannotImprove):
        abtest.test_candidate(prof, HASH)
    dropped_everything(sent, graph)


def test_a_build_that_fails_after_its_load_drops_what_the_load_made(candidate, monkeypatch, tmp_path):
    prof, graph, sent = candidate
    monkeypatch.setattr(abtest, "write_candidate_pack", lambda *a, **k: ("1.0.1", [{"id": "e1"}], []))
    monkeypatch.setattr(abtest, "sync_proposal_pack_to_store", lambda prof_, pid: None)
    monkeypatch.setattr(abtest, "write_proposal_record", lambda prof_, pid, record: None)

    def steps(args, prof_, force_rules=False, ok_codes=(0,)):
        if args[0] == "kge.compiler.compile":
            return type("P", (), {"stdout": f"{prof.artifacts}/meridian/{HASH}\n", "stderr": ""})()
        if args[0] == "kge.pipeline.run":
            raise CannotImprove("kge.pipeline.run failed (exit 1)")
        return type("P", (), {"stdout": "", "stderr": ""})()
    monkeypatch.setattr(abtest, "_run_cmd", steps)
    evidence = {"base_pack_hash": "b", "served_hash": "s", "base_version": "1.0.0"}
    with pytest.raises(CannotImprove):
        abtest.build_candidate(prof, "open", [{"id": "e1"}], evidence)
    dropped_everything(sent, graph)


def test_a_candidate_never_drops_what_its_hash_s_release_record_names(candidate, monkeypatch):
    """A candidate whose pack compiles to the hash being served: the release record names the served generation and
    chunks, in the graph dataset, and the candidate's discard drops only its own."""
    prof, graph, sent = candidate
    served = {**bq.read_pointer(graph), "dataset": "kge_meridian_graph", "prefix": bq.prefix_of(HASH, "11111111")}
    released.write(graph.parent, HASH, served, {"dataset": "kge_meridian_graph", "table": f"chunks_{HASH[:16]}_11111111"})
    abtest.discard(prof, HASH)
    assert not [s for s in sent if "DROP" in s and "kge_meridian_graph" in s]
    dropped_everything(sent, graph)


def test_on_ladybug_and_the_graph_s_own_vectors_nothing_is_dropped(monkeypatch, tmp_path):
    for name in ("GRAPH_STORE", "RETRIEVAL_BACKEND", "WAREHOUSE_BACKEND"):
        monkeypatch.delenv(name, raising=False)
    prof = profile(tmp_path)
    graph = prof.at(HASH).graph_path()
    graph.parent.mkdir(parents=True)
    graph.write_text("stub")
    rel.write_marker(graph.parent, {"ladybug": {"client": "meridian", "model_hash": HASH, "release": "r", "chunks": 1}},
                     rel.LOADED)
    assert abtest.discard(prof, HASH) == []
    assert graph.read_text() == "stub" and rel.loaded(graph) is not None, "the local candidate stays as it was"
    assert graphstore.discard(graph) == [] and retrieval.discard_chunks(graph.parent) == []


def test_a_pointer_that_still_names_the_released_generation_and_chunks_is_never_dropped(candidate):
    """Grok m2 on #97: after a build that fails before its load repoints, the hash's folder still names the generation
    and the chunks table its release record names; the candidate's discard, BigQueryStore.discard and discard_chunks
    alike, drops neither."""
    prof, graph, sent = candidate
    released.write(graph.parent, HASH, bq.read_pointer(graph), {"dataset": "kge_meridian_candidates", "table": TABLE})
    assert bq.BigQueryStore().discard(graph) == []
    assert bqr.discard_chunks(graph.parent) == []
    abtest.discard(prof, HASH)
    assert not [s for s in sent if "DROP" in s]
    assert bq.read_pointer(graph) is not None
