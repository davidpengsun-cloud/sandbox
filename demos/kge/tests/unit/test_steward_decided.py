"""Decided suggestion history stays role-gated and readable on both graph-store adapters."""
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from kge.adapters import graphstore
from kge.adapters.graphstore import bigquery
from kge.adapters.graphstore.gql import Schema
from kge.graph.schema import meta_model
from kge.pipeline.overlay import Journal, record
from kge.services import http
from kge.services.ask.engine.graphview import Graph, Rights
from kge.services.steward import routes


PASSAGES = [("D1", "C1", "retail"), ("D2", "C2", "private"), ("D3", "C3", ""),
            ("D4", "C4", None), ("other-document", "C5", "retail")]


@pytest.fixture(params=["ladybug", "bigquery"])
def history(request, monkeypatch, tmp_path):
    monkeypatch.setenv("MODEL_PROVIDER", "none")
    monkeypatch.setenv("GRAPH_STORE", request.param)
    monkeypatch.setenv("BIGQUERY_PROJECT", "test-project")
    jobs = None
    store = graphstore.store()
    if request.param == "ladybug":
        db = store.open(tmp_path / "graph.ladybug")
        with store.connect(db) as conn:
            for ddl in ("CREATE NODE TABLE Document(id STRING, unit STRING, PRIMARY KEY(id))",
                        "CREATE NODE TABLE Chunk(id STRING, PRIMARY KEY(id))",
                        "CREATE REL TABLE HAS_CHUNK(FROM Document TO Chunk)"):
                conn.execute(ddl).close()
            for doc, chunk, unit in PASSAGES:
                conn.execute("CREATE (:Document {id: $d, unit: $u})", {"d": doc, "u": unit}).close()
                conn.execute("CREATE (:Chunk {id: $c})", {"c": chunk}).close()
                conn.execute("MATCH (d:Document), (c:Chunk) WHERE d.id = $d AND c.id = $c CREATE (d)-[:HAS_CHUNK]->(c)",
                             {"d": doc, "c": chunk}).close()
    else:
        jobs = []

        def post(client, url, body):
            jobs.append(body)
            return {"jobComplete": True, "schema": {"fields": [{"name": f"c{i}", "type": "STRING"} for i in range(3)]},
                    "rows": [{"f": [{"v": cell} for cell in row]} for row in PASSAGES]}
        monkeypatch.setattr(bigquery.Client, "_post", post)
        schema = Schema.from_meta(meta_model(), 128)
        db = bigquery.Graph(tmp_path / "graph.bigquery", pointer={
            "format": bigquery.POINTER_FORMAT, "dataset": "kge_meridian_graph", "prefix": "gabababababababab_abababab_",
            "schema": schema.to_json()})
    graph = object.__new__(Graph)
    graph.db = db
    graph.profile = SimpleNamespace(client="meridian", overlay_dir=lambda: tmp_path / "overlay")
    graph.identity = {"hash": "current"}
    graph._rights = Rights(graph.identity, {"steward": "Steward", "reader": "Reader"},
                           {"steward": frozenset({"retail"})}, {"steward": frozenset({"certification"})}, frozenset())
    graph._role_templates = {"steward": {}, "reader": {}}
    graph._roles_only = False
    graph.people = SimpleNamespace(roles_of=lambda who: {who})
    engine = SimpleNamespace(g=graph)
    fake = SimpleNamespace(engine=lambda: engine)
    monkeypatch.setattr(http, "served", fake)
    monkeypatch.setattr(routes, "served", fake)
    app = FastAPI()
    app.include_router(routes.router)

    def principal(request: Request):
        return request.headers.get("who", "steward")

    app.dependency_overrides[http.principal] = principal
    journal = Journal(graph.profile.overlay_dir(), "meridian")

    def append(chunk="C1", doc="D1", decision="accept", family="suggested_mention", release="hash-one"):
        journal.append(record("meridian", release, "past-steward", "2026-11-07T12:00:00Z", decision,
                              {"id": f"{chunk}/human"}, {"id": f"{chunk}/machine", "family": family, "value": "Revenue"},
                              {"id": chunk, "doc": doc, "text_sha256": "digest"}))

    with TestClient(app) as client:
        yield client, graph, append, jobs
    db.close()


def test_decided_route_refuses_the_same_roles_as_pending(history):
    client, graph, append, jobs = history
    graph.steward_queue = lambda who: [{"pending": True}]
    append()
    for query in ("", "?status=pending", "?status=decided"):
        assert client.get("/steward/queue" + query, headers={"who": "reader"}).status_code == 403
    assert not jobs
    assert client.get("/steward/queue").json() == [{"pending": True}]
    assert client.get("/steward/queue?status=pending").json() == [{"pending": True}]
    assert client.get("/steward/queue?status=unknown").status_code == 422


@pytest.mark.parametrize("count", [1, 25])
def test_decided_route_filters_unreadable_missing_and_other_families_in_one_job(history, count):
    client, graph, append, jobs = history
    for _ in range(count):
        append()
    append(decision="reject", release="hash-two")
    for doc, chunk, _unit in PASSAGES[1:]:
        append(chunk=chunk, doc="D5" if chunk == "C5" else doc)
    append(chunk="missing")
    append(family="privacy")
    append(decision="reject_requested")
    response = client.get("/steward/queue?status=decided")
    assert response.status_code == 200
    rows = response.json()
    assert len(rows) == count + 1
    assert rows[0] == {"release": "hash-two", "tag": "C1/machine", "doc": "D1", "chunk": "C1", "unit": "retail",
                       "term": "Revenue", "decision": "reject", "who": "past-steward", "at": "2026-11-07T12:00:00Z"}
    assert rows[1]["release"] == "hash-one" and rows[1]["decision"] == "accept"
    assert {r["chunk"] for r in rows} == {"C1"}
    if jobs is not None:
        assert len(jobs) == 1
        assert "GRAPH_TABLE" in jobs[0]["query"]
        assert "HAS_CHUNK" in jobs[0]["query"]
        assert jobs[0]["queryParameters"][0]["parameterType"]["type"] == "ARRAY"


def test_decided_history_rechecks_readable_units_each_request(history):
    client, graph, append, jobs = history
    append()
    assert len(client.get("/steward/queue?status=decided").json()) == 1
    graph._rights.reads["steward"] = frozenset()
    assert client.get("/steward/queue?status=decided").json() == []


@pytest.mark.parametrize("bad", ["unreadable journal", '{"client": "another-client"}'])
def test_empty_and_unreadable_journals_are_not_fabricated_history(history, bad):
    client, graph, append, jobs = history
    assert client.get("/steward/queue?status=decided").json() == []
    assert not jobs
    append(release=None)
    assert client.get("/steward/queue?status=decided").json()[0]["release"] is None
    (graph.profile.overlay_dir() / "decisions.jsonl").write_text(bad)
    response = client.get("/steward/queue?status=decided")
    assert response.status_code == 500
    assert response.json() == {"detail": "The steward decision history could not be read."}
