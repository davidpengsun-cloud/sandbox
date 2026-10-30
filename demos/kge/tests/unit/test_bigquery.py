"""Tests for Native GCP Data & Retrieval Target (Issue #70).

Tests:
1. VertexEmbedder (Vertex AI gemini-embedding-001 API with ADC, output_dimensionality=128).
2. BigQueryWarehouses (BigQuery API queries, query routing, dry-run gate).
3. BigQuery Vector Search in Graph.retrieve (VECTOR_SEARCH).
4. Standalone load_bigquery.py utility.
5. Verification that DuckDB and Ladybug remain the default for offline local dev.
"""
from __future__ import annotations

import contextlib
import json
import re
import urllib.request
from pathlib import Path
import pytest

from kge.adapters.retrieval.bigquery import BigQueryRetrieval
from kge.adapters.warehouse.bigquery import BigQueryWarehouses
from kge.adapters.warehouse.duckdb import Warehouses
from kge.services.ask.engine.core import Engine, Graph
from kge.adapters.model import DIM, HashEmbedder, VertexEmbedder, make_embedder
from kge.pipeline.profile import resolve
from kge.estate import load_bigquery
from kge.adapters import appstate
from kge.adapters.appstate import bigquery as bigquery_state, files as files_state

DEMO = Path(__file__).resolve().parents[2]
TOKEN = "ya29.stand-in-token"
PROJECT = "pilot-project"
CLOUD_PLATFORM = "https://www.googleapis.com/auth/cloud-platform"


CHUNKS = "chunks_abcdefabcdefabcd_00000000"


@pytest.fixture(autouse=True)
def named_chunks(monkeypatch):
    """Each graph's chunks table, as its pointer would name it (kge/adapters/retrieval/bigquery.py chunks_table): the
    client's graph dataset's table CHUNKS, the project the settings name."""
    def table(profile, record=None):
        client = getattr(profile, "client", str(profile))
        return f"`{PROJECT}.kge_{client.replace('-', '_')}_graph.{CHUNKS}`"
    monkeypatch.setattr("kge.adapters.retrieval.bigquery.chunks_table", table)


def _bq_state(monkeypatch):
    """A BigQueryState on the stand-in project, its tables not yet created for this test."""
    monkeypatch.setenv("BIGQUERY_PROJECT", PROJECT)
    monkeypatch.setattr(bigquery_state, "_ENSURED", set())
    return bigquery_state.BigQueryState(resolve(), sleeper=lambda _: None)


def test_app_state_creates_tables_once_and_parameterises_values(monkeypatch):
    state = _bq_state(monkeypatch)
    sent = []

    def post(body):
        sent.append(body)
        if "SELECT revision" in body["query"]:
            return {"schema": {"fields": [{"name": "revision"}, {"name": "manifest"}]},
                    "rows": [{"f": [{"v": "2"}, {"v": '{"unit":"leasing","requests":[]}'}]}]}
        return {}

    monkeypatch.setattr(state, "_post", post)
    manifest = state.manifest("leasing")
    state.history("leasing")
    assert sum("CREATE TABLE IF NOT EXISTS" in call["query"] for call in sent) == 1
    assert manifest["_app_revision"] == 2
    reads = [call for call in sent if "WHERE unit=@unit" in call["query"]]
    assert all("leasing" not in call["query"] for call in reads)
    assert all(call["queryParameters"][0]["parameterValue"]["value"] == "leasing" for call in reads)


def test_app_state_step_is_one_transaction_for_manifest_history_and_requests(monkeypatch):
    state = _bq_state(monkeypatch)
    sent = []
    state._ready = True
    monkeypatch.setattr(state, "_post", lambda body: sent.append(body) or {})
    manifest = {"unit": "leasing", "_app_revision": 4,
                "golden": {"name": "golden_leasing", "version": "v1", "client": 2, "drafted": 1},
                "requests": [{"id": "r1", "ask": "value '); DROP TABLE unit_manifest; --"}]}
    state.commit("leasing", manifest, [{"step": "draft", "detail": {"label": "x'; DELETE"}}])
    body = sent[0]
    assert body["query"].count("BEGIN TRANSACTION") == body["query"].count("COMMIT TRANSACTION") == 1
    assert ".unit_manifest`" in body["query"] and ".unit_history`" in body["query"] and ".unit_requests`" in body["query"]
    assert "DROP TABLE" not in body["query"] and "DELETE'" not in body["query"]
    assert {p["name"] for p in body["queryParameters"]} == {
        "unit", "expected", "next_revision", "manifest", "history", "requests"}
    saved = next(p for p in body["queryParameters"] if p["name"] == "manifest")["parameterValue"]["value"]
    assert json.loads(saved)["golden"] == {"name": "golden_leasing", "version": "v1", "client": 2, "drafted": 1}


def test_app_state_retries_aborts_then_fails_in_one_line(monkeypatch):
    state = _bq_state(monkeypatch); state._ready = True
    calls = []

    def aborted(body):
        calls.append(body)
        error = RuntimeError("BigQuery state error: transaction aborted by concurrent update")
        error.reason = "aborted"
        raise error

    monkeypatch.setattr(state, "_post", aborted)
    with pytest.raises(RuntimeError, match="update aborted after 4 attempts") as failure:
        state.commit("leasing", {"unit": "leasing", "_app_revision": 1, "requests": []}, [])
    assert len(calls) == 4 and "\n" not in str(failure.value)


def test_app_state_refuses_a_stale_revision(monkeypatch):
    state = _bq_state(monkeypatch); state._ready = True
    monkeypatch.setattr(state, "_post", lambda body: (_ for _ in ()).throw(
        RuntimeError("BigQuery state error: stale revision")))
    with pytest.raises(appstate.StateConflict, match="changed by someone else; reload"):
        state.commit("leasing", {"unit": "leasing", "_app_revision": 3, "requests": []}, [])


def test_a_rollback_s_manifests_are_one_transaction_that_appends_history_and_drops_removed_units_whole(monkeypatch):
    state = _bq_state(monkeypatch); state._ready = True
    sent = []
    monkeypatch.setattr(state, "_post", lambda body: sent.append(body) or {})
    hostile = "x'); DROP TABLE unit_manifest; --"
    state.restore({"leasing": {"unit": "leasing", "version": 1, "_app_revision": 7,
                               "requests": [{"id": "r1", "ask": hostile}]}},
                  {"leasing": [{"step": "rolled-back", "detail": {"to": hostile}}],
                   "factoring": [{"step": "rolled-back", "detail": {"removed": True}}]},
                  {"factoring"})
    assert len(sent) == 1, "one statement: the whole rollback commits or none of it does"
    query = sent[0]["query"]
    assert query.count("BEGIN TRANSACTION") == query.count("COMMIT TRANSACTION") == 1
    assert "DROP TABLE" not in query and "leasing" not in query and "factoring" not in query, "values are parameters"
    params = {p["name"]: p["parameterValue"]["value"] for p in sent[0]["queryParameters"]}
    assert params["unit_0"] == "leasing" and params["drop_0"] == "factoring"
    assert "_app_revision" not in json.loads(params["manifest_0"]), "the revision is the table's, and only goes up"
    assert "revision=GREATEST(T.revision + 1" in query
    deletes = [line for line in query.splitlines() if line.startswith("DELETE FROM")]
    assert [line.split(".")[-1] for line in deletes if ".unit_history`" in line] == ["unit_history` WHERE unit=@drop_0;"], \
        "a restored unit's history is appended to, never deleted; a dropped unit's goes, as units/<unit>/ does"
    assert ".unit_requests` WHERE unit=@drop_0;" in query and ".unit_manifest` WHERE unit=@drop_0;" in query
    appends = [chunk for chunk in query.split(";\n") if chunk.startswith("INSERT `") and ".unit_history`" in chunk]
    assert len(appends) == 1 and "@drop_0" not in appends[0], "nothing is written to a dropped unit's history"
    assert not any(name.startswith("drop_history") for name in params)
    sent.clear()
    state.restore({}, {}, set())
    assert sent == [], "nothing to restore, nothing sent"


class Tables:
    """The app state's four tables in memory, for BigQueryState's _post: each statement the backend sends, run as
    BigQuery would run it (the commit's revision check and request-closed filter, a rollback's appends, merges and
    deletes, and the reads), so a test sees what the tables hold after a sequence of commits and rollbacks. The
    manifest maps a unit to its (revision, manifest), and the history holds (unit, sequence, entry)."""

    def __init__(self):
        self.manifest, self.history, self.requests = {}, [], []

    @staticmethod
    def _result(names, rows):
        return {"schema": {"fields": [{"name": n} for n in names]}, "rows": [{"f": [{"v": v} for v in r]} for r in rows]}

    def post(self, body):
        query = body["query"]
        p = {x["name"]: x["parameterValue"]["value"] for x in body.get("queryParameters", [])}
        if "CREATE TABLE IF NOT EXISTS" in query:
            return {}
        if query.lstrip().startswith("SELECT unit FROM"):
            return self._result(["unit"], [[u] for u in sorted(self.manifest)])
        if query.lstrip().startswith("SELECT TO_JSON_STRING(entry)"):
            return self._result(["entry"], [[json.dumps(e)] for u, _, e in sorted(self.history, key=lambda h: h[1])
                                            if u == p["unit"]])
        if query.lstrip().startswith("SELECT revision"):
            held = self.manifest.get(p["unit"])
            entries = [e for u, _, e in sorted(self.history, key=lambda h: h[1]) if u == p["unit"]]
            snapshot = "ARRAY(SELECT AS VALUE entry" in query
            return self._result(["revision", "manifest", *(["history"] if snapshot else [])],
                                [[str(held[0]), json.dumps(held[1]), *([json.dumps(entries)] if snapshot else [])]] if held else [])
        statements = [s.strip() for s in query.split(";\n") if s.strip()]
        for st in statements:
            unit = lambda: p[re.search(r"@((?:unit|drop)(?:_\d+)?)\b", st).group(1)]  # noqa: E731
            seqs = lambda u: [q for v, q, _ in self.history if v == u]  # noqa: E731
            if st.startswith(("BEGIN TRANSACTION", "COMMIT TRANSACTION")):
                continue
            if st.startswith("ASSERT"):
                expected, held = int(p["expected"]), self.manifest.get(p["unit"])
                if not ((expected == 0 and held is None) or (expected > 0 and held and held[0] == expected)):
                    raise RuntimeError("BigQuery state query failed: stale revision")
            elif st.startswith("MERGE") and "least_revision" in st:
                u = unit()
                least = max((max(seqs(u)) // 1000000 if seqs(u) else 0) + 1,
                            int(p[floor.group(1)]) if (floor := re.search(r"@(floor_\d+)", st)) else 0)
                held = self.manifest.get(u)
                name = re.search(r"PARSE_JSON\(@(manifest_\d+)\)", st).group(1)
                self.manifest[u] = (max(held[0] + 1, least) if held else least, json.loads(p[name]))
            elif st.startswith("MERGE"):
                self.manifest[p["unit"]] = (int(p["next_revision"]), json.loads(p["manifest"]))
            elif st.startswith("INSERT") and ".unit_history`" in st and "@expected" in st:
                u = p["unit"]
                for offset, raw in enumerate(json.loads(p["history"])):
                    entry = json.loads(raw)
                    if entry.get("step") == "request-closed" and any(
                            v == u and e.get("step") == "request-closed" and (e.get("detail") or {}).get("id")
                            == (entry.get("detail") or {}).get("id") for v, _, e in self.history):
                        continue
                    self.history.append((u, int(p["expected"]) * 1000000 + offset, entry))
            elif st.startswith("INSERT") and ".unit_history`" in st:
                u = unit()
                name = re.search(r"JSON_VALUE_ARRAY\(@(\w+)\)", st).group(1)
                held = self.manifest.get(u)
                start = max((held[0] if held else 0) * 1000000, max(seqs(u)) + 1 if seqs(u) else 0)
                self.history += [(u, start + i, json.loads(e)) for i, e in enumerate(json.loads(p[name]))]
            elif st.startswith("INSERT") and ".unit_requests`" in st:
                u = unit()
                name = re.search(r"JSON_VALUE_ARRAY\(@(\w+)\)", st).group(1)
                self.requests += [(u, json.loads(r).get("id"), json.loads(r)) for r in json.loads(p[name])]
            elif st.startswith("DELETE FROM"):
                u = p[re.search(r"WHERE unit=@(\w+)", st).group(1)]
                if ".unit_manifest`" in st:
                    self.manifest.pop(u, None)
                elif ".unit_history`" in st:
                    self.history = [h for h in self.history if h[0] != u]
                elif ".unit_requests`" in st:
                    self.requests = [r for r in self.requests if r[0] != u]
            else:
                raise AssertionError(f"a statement the fake does not run: {st[:80]}")
        return {}


def test_a_unit_onboarded_again_after_a_rollback_to_before_it_starts_a_fresh_history(monkeypatch):
    """A rollback to before a unit drops its manifest, requests and history in its one transaction, as the files backend
    removes units/<unit>/ with its history.jsonl: onboarded again, its first commit (sequence 0) collides with no old
    entry, and its request-closed entries, whose ids are made from the same inputs as before, are kept, not taken for
    closings the old history holds."""
    state = _bq_state(monkeypatch); state._ready = True
    tables = Tables()
    monkeypatch.setattr(state, "_post", tables.post)
    closed = {"step": "request-closed", "detail": {"id": "leasing-rows", "ask": "rows"}}
    first = [{"step": "source", "version": 1}, {"step": "draft", "version": 1}, closed]

    def onboard_leasing():
        m = state.commit("leasing", {"unit": "leasing", "version": 1, "requests": [{"id": "leasing-rows"}]}, first[:2])
        m = state.commit("leasing", {**m, "requests": []}, [closed])
        return state.commit("leasing", {**m, "version": 2}, [{"step": "update", "version": 2}])

    onboard_leasing()
    assert [e["step"] for e in state.history("leasing")] == ["source", "draft", "request-closed", "update"]
    state.restore({}, {}, {"leasing"})
    assert state.units() == [] and state.history("leasing") == [] and tables.requests == []
    onboard_leasing()
    assert [e["step"] for e in state.history("leasing")] == ["source", "draft", "request-closed", "update"], \
        "a fresh history, the request's closing kept"
    assert [q for u, q, _ in sorted(tables.history, key=lambda h: h[1])] == [0, 1, 1000000, 2000000]
    assert state.manifest("leasing")["_app_revision"] == 3


def test_a_failed_rollback_s_undo_brings_a_dropped_unit_back_above_its_revision(monkeypatch):
    """A unit at revision 5; a rollback to before it drops its rows; the rebuild fails and the undo puts it back
    (kge/graph/history.py _capture_store and _restore_captured_store): above revision 5, with its history, so a page read at
    revision 1 is refused (StateConflict) rather than let in over newer content."""
    from types import SimpleNamespace
    from kge.graph import history
    state = _bq_state(monkeypatch); state._ready = True
    tables = Tables()
    monkeypatch.setattr(state, "_post", tables.post)
    monkeypatch.setattr(history, "_table_state", lambda _prof: state)
    monkeypatch.setattr(history.packstore, "for_profile", lambda _prof: SimpleNamespace(
        list=lambda _p: [], exists=lambda _n: False, read=None, write=None, delete=None))
    m = stale = state.commit("leasing", {"unit": "leasing", "version": 1, "requests": []}, [{"step": "source"}])
    for version in range(2, 6):
        m = state.commit("leasing", {**m, "version": version}, [{"step": "update", "version": version}])
    assert m["_app_revision"] == 5
    kept = state.history("leasing")
    captured = history._capture_store(None)
    history._put_manifests(None, {}, "rolled-back", {"to": "0" * 64})
    assert state.units() == [] and state.history("leasing") == []
    history._restore_captured_store(None, captured)
    back = state.manifest("leasing")
    assert back["_app_revision"] > 5 and back["version"] == 5, back
    assert state.history("leasing")[:-1] == kept and state.history("leasing")[-1]["step"] == "rollback-undone"
    with pytest.raises(appstate.StateConflict):
        state.commit("leasing", {**stale, "version": 1}, [{"step": "stale-edit"}])
    assert state.manifest("leasing")["version"] == 5


def test_a_request_closed_once_is_recorded_once_as_the_files_backend_records_it(monkeypatch, tmp_path):
    """FilesState.commit skips a request-closed entry for a request whose closing is in the history already; the BigQuery
    commit does the same, in the batch and against the table, in its one statement."""
    from kge import packstore
    from kge.pipeline.profile import Profile
    closed = {"step": "request-closed", "detail": {"id": "r1", "ask": "rows"}}
    prof = Profile("meridian", "meridian", tmp_path / "clients", *[tmp_path / n for n in ("a", "s", "l", "w")], None, None,
                   tmp_path / "p.yaml")
    files = files_state.FilesState(packstore.for_profile(prof))
    files.commit("leasing", {"unit": "leasing"}, [closed, closed])
    files.commit("leasing", {**files.manifest("leasing")}, [closed, {"step": "draft"}])
    assert [e["step"] for e in files.history("leasing")] == ["request-closed", "draft"]

    state = _bq_state(monkeypatch); state._ready = True
    sent = []
    monkeypatch.setattr(state, "_post", lambda body: sent.append(body) or {})
    state.commit("leasing", {"unit": "leasing", "_app_revision": 1, "requests": []}, [closed, closed, {"step": "draft"}])
    params = {p["name"]: p["parameterValue"]["value"] for p in sent[0]["queryParameters"]}
    assert [json.loads(e)["step"] for e in json.loads(params["history"])] == ["request-closed", "draft"]
    query = sent[0]["query"]
    assert "JSON_VALUE(h.entry, '$.step') = 'request-closed'" in query and "'$.detail.id'" in query, \
        "a closing already in the table is left out"


def _polled(monkeypatch, state, after):
    """The first reply says the job is still running; each poll (a GET through _call, not urlopen) gets after(n)."""
    monkeypatch.setattr(state, "_token", lambda: (TOKEN, PROJECT))
    polls = []

    def call(url, body=None):
        if body is not None:
            return {"jobComplete": False, "jobReference": {"jobId": "job-1", "location": "EU"}}
        polls.append(url)
        return after(len(polls))
    monkeypatch.setattr(state, "_call", call)
    return polls


def _http_failure(reason: str):
    error = RuntimeError(f"BigQuery state error (HTTP 400): {reason}")
    error.reason = reason
    return error


def test_a_stale_revision_found_while_polling_is_a_conflict_not_a_failure(monkeypatch):
    state = _bq_state(monkeypatch); state._ready = True
    polls = _polled(monkeypatch, state, lambda n: (_ for _ in ()).throw(_http_failure("stale revision")))
    with pytest.raises(appstate.StateConflict):
        state.commit("leasing", {"unit": "leasing", "_app_revision": 3, "requests": []}, [])
    assert polls and "/queries/job-1?" in polls[0] and "location=EU" in polls[0]


def test_an_abort_found_while_polling_is_retried(monkeypatch):
    state = _bq_state(monkeypatch); state._ready = True
    _polled(monkeypatch, state, lambda n: (_ for _ in ()).throw(_http_failure("aborted")) if n == 1 else {"jobComplete": True})
    saved = state.commit("leasing", {"unit": "leasing", "_app_revision": 3, "requests": []}, [])
    assert saved["_app_revision"] == 4


def test_a_job_that_never_finishes_stops_at_the_deadline(monkeypatch):
    state = _bq_state(monkeypatch); state._ready = True
    clock = iter(range(0, 10_000, 100))
    monkeypatch.setattr(bigquery_state.time, "monotonic", lambda: next(clock))
    _polled(monkeypatch, state, lambda n: {"jobComplete": False, "jobReference": {"jobId": "job-1"}})
    with pytest.raises(RuntimeError, match=f"did not finish within {bigquery_state.QUERY_DEADLINE} seconds"):
        state.units()


class JsonResponse:
    def __init__(self, value):
        self.value = value

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def read(self):
        return json.dumps(self.value).encode()


class MockAccount:
    def __init__(self):
        self.scopes = None
        self.token = TOKEN
        self.valid = True

    def refresh(self, request):
        pass


@pytest.fixture()
def mock_account(monkeypatch):
    acct = MockAccount()

    def default(scopes=None, **kwargs):
        acct.scopes = scopes
        return acct, PROJECT

    monkeypatch.setattr("google.auth.default", default)
    return acct


def test_default_embedder_remains_hash(monkeypatch):
    """Local dev default must remain HashEmbedder without GCP credentials."""
    monkeypatch.delenv("EMBEDDING_PROVIDER", raising=False)
    emb = make_embedder()
    assert isinstance(emb, HashEmbedder)
    assert emb.name == "hash-v1"
    vec = emb.embed("sample query")
    assert len(vec) == DIM


def test_vertex_embedder_calls_predict_api_with_output_dimensionality(monkeypatch, mock_account):
    """VertexEmbedder calls Vertex AI gemini-embedding-001 (its default: text-embedding-004 is retired) with 128 output
    dimensions and an ADC token."""
    monkeypatch.setenv("EMBEDDING_PROVIDER", "vertex")
    monkeypatch.setenv("VERTEX_REGION", "us-central1")
    monkeypatch.setenv("VERTEX_PROJECT", PROJECT)

    emb = make_embedder()
    assert isinstance(emb, VertexEmbedder)
    assert emb.name == "gemini-embedding-001"

    sent: list[tuple] = []
    fake_vector = [0.1] * DIM

    def mock_post(url, headers, body):
        sent.append((url, headers, body))
        return {
            "predictions": [
                {
                    "embeddings": {
                        "values": fake_vector,
                    }
                }
            ]
        }

    monkeypatch.setattr(emb, "_post", mock_post)

    vec = emb.embed("test search text")
    assert len(vec) == DIM
    assert len(sent) == 1
    url, headers, body = sent[0]

    assert url == f"https://us-central1-aiplatform.googleapis.com/v1/projects/{PROJECT}/locations/us-central1/publishers/google/models/gemini-embedding-001:predict"
    assert headers["authorization"] == f"Bearer {TOKEN}"
    assert headers["content-type"] == "application/json"
    assert body["instances"] == [{"content": "test search text"}]
    assert body["parameters"]["outputDimensionality"] == 128
    assert mock_account.scopes == [CLOUD_PLATFORM]


def test_default_warehouses_remains_duckdb(monkeypatch):
    """Local dev default warehouse backend must remain DuckDB."""
    monkeypatch.delenv("WAREHOUSE_BACKEND", raising=False)
    prof = resolve()
    engine = Engine(prof)
    try:
        assert isinstance(engine.w, Warehouses)
    finally:
        engine.close()


def test_bigquery_warehouses_routing_and_execution(monkeypatch, mock_account):
    """BigQueryWarehouses routes queries for unit retail to {project}.kge_meridian_warehouses."""
    prof = resolve()
    bq_w = BigQueryWarehouses(prof, ["retail", "wealth"], curated=["xref"], project=PROJECT)

    single_sql = "SELECT round(sum(monthly_revenue.retail_revenue_gbp), 2) FROM monthly_revenue"
    rewritten = bq_w.rewrite_sql(single_sql, unit="retail")
    assert "retail_monthly_revenue" in rewritten

    attached_sql = "SELECT * FROM retail.monthly_revenue r JOIN wealth.wealth_revenue w ON r.id = w.id"
    rewritten_att = bq_w.rewrite_sql(attached_sql)
    assert "retail_monthly_revenue" in rewritten_att
    assert "wealth_wealth_revenue" in rewritten_att

    sent_queries: list[dict] = []

    def mock_post(url, headers, body):
        sent_queries.append(body)
        return {
            "schema": {"fields": [{"name": "total", "type": "FLOAT64"}]},
            "rows": [{"f": [{"v": "12345.67"}]}],
        }

    monkeypatch.setattr(bq_w, "_post", mock_post)

    val = bq_w.run("retail", single_sql)
    assert val == 12345.67
    assert len(sent_queries) == 1
    assert "retail_monthly_revenue" in sent_queries[0]["query"]
    assert sent_queries[0]["defaultDataset"]["datasetId"] == f"kge_{prof.client}_warehouses"
    assert sent_queries[0]["defaultDataset"]["projectId"] == PROJECT


def test_bigquery_warehouses_gate_dry_run(monkeypatch, mock_account):
    """gate() runs dryRun: true query to validate syntax & schema at zero cost."""
    prof = resolve()
    bq_w = BigQueryWarehouses(prof, ["retail"], project=PROJECT)

    sent: list[dict] = []

    def mock_post(url, headers, body):
        sent.append(body)
        if "SYNTAX_ERROR" in body["query"]:
            raise RuntimeError("BigQuery error (HTTP 400): Syntax error: Unexpected keyword")
        return {"schema": {"fields": []}}

    monkeypatch.setattr(bq_w, "_post", mock_post)

    from kge.pipeline.sqlguard import schema_allowlist
    g = Graph(prof)
    try:
        allow = schema_allowlist(g, "retail")
    finally:
        g.close()

    valid_sql = "SELECT round(sum(monthly_revenue.total_revenue_usd), 2) FROM monthly_revenue"
    guard_ok = bq_w.gate("retail", valid_sql, allow)
    assert guard_ok["passed"] is True
    assert sent[-1].get("dryRun") is True

    bad_sql = "SELECT SYNTAX_ERROR FROM monthly_revenue"
    guard_bad = bq_w.gate("retail", bad_sql, allow)
    assert guard_bad["passed"] is False


def test_engine_activates_bigquery_warehouses_when_configured(monkeypatch, mock_account):
    """Engine uses BigQueryWarehouses when WAREHOUSE_BACKEND=bigquery."""
    monkeypatch.setenv("WAREHOUSE_BACKEND", "bigquery")
    monkeypatch.setenv("BIGQUERY_PROJECT", PROJECT)
    prof = resolve()
    engine = Engine(prof)
    try:
        assert isinstance(engine.w, BigQueryWarehouses)
        assert engine.w.project == PROJECT
        assert engine.w.dataset_id == f"kge_{prof.client}_warehouses"
    finally:
        engine.close()


def test_graph_retrieve_executes_bigquery_vector_search(monkeypatch, mock_account):
    """When RETRIEVAL_BACKEND=bigquery, Graph.retrieve executes VECTOR_SEARCH."""
    monkeypatch.setenv("RETRIEVAL_BACKEND", "bigquery")
    monkeypatch.setenv("BIGQUERY_PROJECT", PROJECT)
    prof = resolve()
    g = Graph(prof)

    sent_queries: list[str] = []

    def mock_bq_query(sql):
        sent_queries.append(sql)
        if "count(*)" in sql:
            return [{"n": "0"}]
        return [
            {
                "doc": "DOC-001",
                "title": "Retail Governance",
                "shelf": "certified",
                "tier": "gold",
                "ai_ready": True,
                "chunk": "CHUNK-001",
                "readiness": "citable",
                "score": 0.88,
                "terms": ["revenue"],
                "excerpt": "Retail revenue rules...",
                "eligible": True,
            }
        ]

    monkeypatch.setattr(BigQueryRetrieval, "query", lambda self, sql: mock_bq_query(sql))

    try:
        res = g.retrieve("retail revenue policy", principal="analyst_enterprise", purpose="analysis", k=3)
        assert res["asked_for"] == 3
        assert len(res["exact"]) == 1
        assert res["exact"][0]["chunk"] == "CHUNK-001"
        assert res["exact"][0]["score"] == 0.88
        assert res["exact"][0]["shelf"] == "certified"

        assert any("VECTOR_SEARCH" in q for q in sent_queries)
        assert any(f"`{PROJECT}.kge_{prof.client}_graph.{CHUNKS}`" in q for q in sent_queries)
    finally:
        g.close()


def test_load_bigquery_dry_run():
    """load_bigquery.py runs offline with --dry-run without GCP credentials."""
    ret = load_bigquery.main(["--dry-run", "--project", PROJECT])
    assert ret == 0


def test_load_bigquery_takes_the_dataset_prefix_terraform_sets(monkeypatch, capsys):
    """With no --prefix, the loader writes to the datasets Terraform made: BIGQUERY_DATASET_PREFIX, not a fixed kge."""
    monkeypatch.setenv("BIGQUERY_DATASET_PREFIX", "pilotx")
    assert load_bigquery.main(["--dry-run", "--project", PROJECT]) == 0
    assert "`pilotx_" in capsys.readouterr().out


def test_load_bigquery_table_mapping_and_types():
    """map_duckdb_type_to_bigquery correctly maps DuckDB types to BigQuery."""
    assert load_bigquery.map_duckdb_type_to_bigquery("BIGINT") == "INT64"
    assert load_bigquery.map_duckdb_type_to_bigquery("INTEGER") == "INT64"
    assert load_bigquery.map_duckdb_type_to_bigquery("DOUBLE") == "FLOAT64"
    assert load_bigquery.map_duckdb_type_to_bigquery("VARCHAR") == "STRING"
    assert load_bigquery.map_duckdb_type_to_bigquery("BOOLEAN") == "BOOL"
    assert load_bigquery.map_duckdb_type_to_bigquery("DATE") == "DATE"
    assert load_bigquery.map_duckdb_type_to_bigquery("DECIMAL(18, 2)") == "NUMERIC"


def test_load_job_waits_through_pending_and_running(monkeypatch):
    now = [0.0]
    loader = load_bigquery.BigQueryLoader(project=PROJECT, sleeper=lambda delay: now.__setitem__(0, now[0] + delay),
                                           clock=lambda: now[0])
    loader._account = (MockAccount(), PROJECT)
    monkeypatch.setattr(urllib.request, "urlopen", lambda request, timeout: JsonResponse({
        "jobReference": {"jobId": "load-1", "location": "us-central1"}, "status": {"state": "PENDING"}}))
    statuses = iter([{"status": {"state": "PENDING"}}, {"status": {"state": "RUNNING"}},
                     {"status": {"state": "DONE"}}])
    urls = []
    monkeypatch.setattr(loader, "get", lambda url, headers: (urls.append(url), next(statuses))[1])

    assert loader.load_rows("warehouse", "retail_account", [{"name": "id", "type": "INT64"}], [{"id": 1}]) == 1
    assert urls[-1].endswith("/jobs/load-1?location=us-central1")
    assert now[0] == 3.0


def test_load_job_done_with_error_raises(monkeypatch):
    loader = load_bigquery.BigQueryLoader(project=PROJECT, sleeper=lambda _: None, clock=lambda: 0.0)
    loader._account = (MockAccount(), PROJECT)
    monkeypatch.setattr(urllib.request, "urlopen", lambda request, timeout: JsonResponse({
        "jobReference": {"jobId": "load-2"}, "status": {"state": "RUNNING"}}))
    monkeypatch.setattr(loader, "get", lambda url, headers: {
        "status": {"state": "DONE", "errorResult": {"message": "bad row"}}})
    with pytest.raises(RuntimeError, match="BigQuery load job failed: bad row"):
        loader.load_rows("warehouse", "retail_account", [{"name": "id", "type": "INT64"}], [{"id": 1}])


def test_load_job_done_with_nonfatal_errors_succeeds(monkeypatch):
    loader = load_bigquery.BigQueryLoader(project=PROJECT)
    loader._account = (MockAccount(), PROJECT)
    monkeypatch.setattr(urllib.request, "urlopen", lambda request, timeout: JsonResponse({
        "jobReference": {"jobId": "load-warning"}, "status": {"state": "RUNNING"}}))
    monkeypatch.setattr(loader, "get", lambda url, headers: {
        "status": {"state": "DONE", "errors": [{"message": "non-fatal warning"}]}})

    assert loader.load_rows("warehouse", "retail_account", [{"name": "id", "type": "INT64"}], [{"id": 1}]) == 1


def test_load_job_without_location_omits_location_query(monkeypatch):
    loader = load_bigquery.BigQueryLoader(project=PROJECT)
    loader._account = (MockAccount(), PROJECT)
    monkeypatch.setattr(urllib.request, "urlopen", lambda request, timeout: JsonResponse({
        "jobReference": {"jobId": "load-no-location"}, "status": {"state": "RUNNING"}}))
    urls = []
    monkeypatch.setattr(loader, "get", lambda url, headers: (urls.append(url), {"status": {"state": "DONE"}})[1])

    loader.load_rows("warehouse", "retail_account", [{"name": "id", "type": "INT64"}], [{"id": 1}])
    assert urls == [f"https://bigquery.googleapis.com/bigquery/v2/projects/{PROJECT}/jobs/load-no-location"]


def test_load_job_retries_transient_poll_error(monkeypatch):
    now = [0.0]
    loader = load_bigquery.BigQueryLoader(project=PROJECT, sleeper=lambda delay: now.__setitem__(0, now[0] + delay),
                                           clock=lambda: now[0])
    loader._account = (MockAccount(), PROJECT)
    monkeypatch.setattr(urllib.request, "urlopen", lambda request, timeout: JsonResponse({
        "jobReference": {"jobId": "load-retry"}, "status": {"state": "RUNNING"}}))
    outcomes = iter([RuntimeError("BigQuery error (HTTP 503): unavailable"), {"status": {"state": "DONE"}}])

    def get(url, headers):
        outcome = next(outcomes)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    monkeypatch.setattr(loader, "get", get)
    assert loader.load_rows("warehouse", "retail_account", [{"name": "id", "type": "INT64"}], [{"id": 1}]) == 1
    assert now[0] == 1.0


def test_load_job_stops_after_transient_poll_retry_budget(monkeypatch):
    now = [0.0]
    loader = load_bigquery.BigQueryLoader(project=PROJECT, sleeper=lambda delay: now.__setitem__(0, now[0] + delay),
                                           clock=lambda: now[0])
    loader._account = (MockAccount(), PROJECT)
    monkeypatch.setattr(urllib.request, "urlopen", lambda request, timeout: JsonResponse({
        "jobReference": {"jobId": "load-retries-exhausted"}, "status": {"state": "RUNNING"}}))
    calls = []

    def unavailable(url, headers):
        calls.append(url)
        raise RuntimeError("BigQuery error (HTTP 503): unavailable")

    monkeypatch.setattr(loader, "get", unavailable)
    with pytest.raises(RuntimeError, match=(
            r"^BigQuery load job load-retries-exhausted polling failed after 4 consecutive attempts \(limit 300 seconds\): "
            r"BigQuery error \(HTTP 503\): unavailable$")):
        loader.load_rows("warehouse", "retail_account", [{"name": "id", "type": "INT64"}], [{"id": 1}])
    assert len(calls) == 4


def test_load_job_retry_budget_counts_consecutive_errors_only(monkeypatch):
    """A good poll between errors resets the budget: a long wait survives scattered transient errors."""
    now = [0.0]
    loader = load_bigquery.BigQueryLoader(project=PROJECT, sleeper=lambda delay: now.__setitem__(0, now[0] + delay),
                                           clock=lambda: now[0])
    loader._account = (MockAccount(), PROJECT)
    monkeypatch.setattr(urllib.request, "urlopen", lambda request, timeout: JsonResponse({
        "jobReference": {"jobId": "load-blips"}, "status": {"state": "RUNNING"}}))
    blip, running = RuntimeError("BigQuery error (HTTP 503): unavailable"), {"status": {"state": "RUNNING"}}
    outcomes = iter([blip, running] * 4 + [{"status": {"state": "DONE"}}])

    def get(url, headers):
        outcome = next(outcomes)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    monkeypatch.setattr(loader, "get", get)
    assert loader.load_rows("warehouse", "retail_account", [{"name": "id", "type": "INT64"}], [{"id": 1}]) == 1


def test_load_job_does_not_retry_nontransient_poll_error(monkeypatch):
    loader = load_bigquery.BigQueryLoader(project=PROJECT)
    loader._account = (MockAccount(), PROJECT)
    monkeypatch.setattr(urllib.request, "urlopen", lambda request, timeout: JsonResponse({
        "jobReference": {"jobId": "load-not-found"}, "status": {"state": "RUNNING"}}))
    calls = []

    def missing(url, headers):
        calls.append(url)
        raise RuntimeError("BigQuery error (HTTP 404): not found")

    monkeypatch.setattr(loader, "get", missing)
    with pytest.raises(RuntimeError, match=r"^BigQuery error \(HTTP 404\): not found$"):
        loader.load_rows("warehouse", "retail_account", [{"name": "id", "type": "INT64"}], [{"id": 1}])
    assert len(calls) == 1


def test_load_job_timeout_is_bounded(monkeypatch):
    now = [0.0]
    loader = load_bigquery.BigQueryLoader(project=PROJECT, sleeper=lambda delay: now.__setitem__(0, now[0] + delay),
                                           clock=lambda: now[0])
    loader._account = (MockAccount(), PROJECT)
    monkeypatch.setenv("BIGQUERY_JOB_TIMEOUT", "2")
    monkeypatch.setattr(urllib.request, "urlopen", lambda request, timeout: JsonResponse({
        "jobReference": {"jobId": "load-3"}, "status": {"state": "RUNNING"}}))
    monkeypatch.setattr(loader, "get", lambda url, headers: {"status": {"state": "RUNNING"}})
    with pytest.raises(TimeoutError, match=r"^BigQuery load job load-3 did not finish within 2 seconds$"):
        loader.load_rows("warehouse", "retail_account", [{"name": "id", "type": "INT64"}], [{"id": 1}])


def test_prune_drops_only_labelled_stale_units_across_pages(monkeypatch):
    loader = load_bigquery.BigQueryLoader(project=PROJECT)
    loader._account = (MockAccount(), PROJECT)
    pages = iter([
        {"tables": [{"tableReference": {"tableId": "leasing_account"}, "labels": {"kge_unit": "leasing"}},
                     {"tableReference": {"tableId": "manual_table"}}], "nextPageToken": "page-2"},
        {"tables": [{"tableReference": {"tableId": "retail_account"}, "labels": {"kge_unit": "retail"}}]},
    ])
    urls, sql = [], []
    monkeypatch.setattr(loader, "get", lambda url, headers: (urls.append(url), next(pages))[1])
    monkeypatch.setattr(loader, "query", lambda statement: sql.append(statement) or {})

    assert loader.prune_units("kge_meridian_warehouses", {"retail"}) == ["leasing_account"]
    assert sql == [f"DROP TABLE `{PROJECT}.kge_meridian_warehouses.leasing_account`"]
    assert "pageToken=page-2" in urls[1]


def test_prune_refuses_an_empty_current_warehouse_set():
    loader = load_bigquery.BigQueryLoader(project=PROJECT)
    with pytest.raises(ValueError, match="refusing to prune BigQuery warehouses with no current units"):
        loader.prune_units("kge_meridian_warehouses", set())


@pytest.mark.parametrize("invalid", [
    {"tableReference": {"tableId": "bad;table"}, "labels": {"kge_unit": "old"}},
    {"tableReference": {"tableId": "old_b"}, "labels": {"kge_unit": "Bad"}},
])
def test_prune_validates_every_labelled_table_before_dropping(monkeypatch, invalid):
    loader = load_bigquery.BigQueryLoader(project=PROJECT)
    loader._account = (MockAccount(), PROJECT)
    monkeypatch.setattr(loader, "tables", lambda dataset: [
        {"tableReference": {"tableId": "old_a"}, "labels": {"kge_unit": "old"}},
        invalid,
    ])
    sql = []
    monkeypatch.setattr(loader, "query", lambda statement: sql.append(statement) or {})

    with pytest.raises(ValueError, match="must match"):
        loader.prune_units("kge_meridian_warehouses", {"retail"})
    assert sql == []


@pytest.mark.parametrize("table,field", [("account;drop", "id"), ("account", "id;drop")])
def test_create_table_refuses_hostile_identifiers(table, field):
    loader = load_bigquery.BigQueryLoader(project=PROJECT, dry_run=True)
    with pytest.raises(ValueError, match="must match"):
        loader.create_table("warehouse", table, [{"name": field, "type": "INT64"}], unit="retail")


def test_create_table_accepts_combined_unit_table_name_longer_than_63_characters():
    loader = load_bigquery.BigQueryLoader(project=PROJECT, dry_run=True)
    sql = []
    loader.query = lambda statement: sql.append(statement) or {}
    table = f"{'u' * 30}_{'t' * 40}"

    loader.create_table("warehouse", table, [{"name": "id", "type": "INT64"}], unit="retail")
    assert table in sql[0]


def test_bigquery_warehouses_cte_rewrite_preserves_cte_alias():
    """rewrite_sql skips CTE aliases so CTE names in FROM/JOIN are not rewritten."""
    prof = resolve()
    bq_w = BigQueryWarehouses(prof, ["retail"], project=PROJECT)
    sql = "WITH m AS (SELECT * FROM tx) SELECT * FROM m"
    rewritten = bq_w.rewrite_sql(sql, unit="retail")
    assert "retail_tx" in rewritten
    assert "retail_m" not in rewritten
    assert "FROM m" in rewritten


def test_bigquery_warehouses_composite_distinct_coalesces_null():
    """rewrite_sql COALESCEs tuple elements in COUNT(DISTINCT) with \\N so NULL elements do not drop rows."""
    prof = resolve()
    bq_w = BigQueryWarehouses(prof, ["retail"], project=PROJECT)
    sql = "SELECT COUNT(DISTINCT (a, b)) FROM tx"
    rewritten = bq_w.rewrite_sql(sql, unit="retail")
    assert "COALESCE(CAST(a AS STRING), '\\\\N')" in rewritten
    assert "COALESCE(CAST(b AS STRING), '\\\\N')" in rewritten
    assert "||" in rewritten


def test_bigquery_warehouses_attached_and_run_interface(monkeypatch, mock_account):
    """attached() validates units and run() raises when no rows returned matching DuckDB behavior."""
    prof = resolve()
    bq_w = BigQueryWarehouses(prof, ["retail", "wealth"], project=PROJECT)

    with pytest.raises(ValueError, match="two or more"):
        with bq_w.attached(["retail"]):
            pass

    with pytest.raises(ValueError, match="two or more"):
        with bq_w.attached(["retail", "unknown"]):
            pass

    def mock_empty_post(url, headers, body):
        return {"schema": {"fields": [{"name": "n", "type": "INT64"}]}, "rows": []}

    monkeypatch.setattr(bq_w, "_post", mock_empty_post)
    with pytest.raises(TypeError):
        bq_w.run("retail", "SELECT 1")

    sent_writes = []
    def mock_write_post(url, headers, body):
        sent_writes.append(body)
        return {"schema": {"fields": []}, "rows": []}

    monkeypatch.setattr(bq_w, "_post", mock_write_post)
    bq_w.write("retail", "INSERT INTO tx VALUES (1)")
    assert len(sent_writes) == 1


def test_bigquery_vector_search_injection_safety_and_readiness_filter(monkeypatch, mock_account):
    """VECTOR_SEARCH safely escapes dynamic inputs and enforces readiness <> 'embed-prohibited'."""
    monkeypatch.setenv("RETRIEVAL_BACKEND", "bigquery")
    monkeypatch.setenv("BIGQUERY_PROJECT", PROJECT)
    prof = resolve()
    g = Graph(prof)

    sent_queries = []
    def mock_query(sql):
        sent_queries.append(sql)
        if "count(*)" in sql:
            return [{"n": "0"}]
        return [{"doc": "D1", "shelf": "certified", "tier": "gold", "ai_ready": True,
                 "chunk": "C1", "readiness": "citable", "score": 0.9, "terms": [], "eligible": True}]

    monkeypatch.setattr(BigQueryRetrieval, "query", lambda self, sql: mock_query(sql))
    try:
        injection_shelf = "certified' OR TRUE --"
        g.retrieve("test question", principal="analyst_enterprise", purpose="analysis", k=2, shelf=injection_shelf)
        assert len(sent_queries) >= 2
        exact_q = sent_queries[0]
        assert '"certified\' OR TRUE --"' in exact_q or "'certified\\' OR TRUE --'" in exact_q or json.dumps(injection_shelf) in exact_q
        assert "OR TRUE --" not in exact_q.replace(json.dumps(injection_shelf), "")
        assert "readiness <> 'embed-prohibited'" in exact_q
    finally:
        g.close()


def test_bigquery_vector_search_adc_project_fallback_and_live_readiness_filter(monkeypatch, mock_account):
    """When BIGQUERY_PROJECT and VERTEX_PROJECT are unset, bigquery_vector_search resolves project from ADC
    and filters out chunks that are embed-prohibited in the live in-process graph. The table is never named
    `None.kge_...`, and a chunk the graph holds as embed-prohibited is dropped even when BigQuery returns it."""
    monkeypatch.setenv("RETRIEVAL_BACKEND", "bigquery")
    monkeypatch.delenv("BIGQUERY_PROJECT", raising=False)
    monkeypatch.delenv("VERTEX_PROJECT", raising=False)
    prof = resolve()
    g = Graph(prof)

    prohibited_rows = g.q("MATCH (c:Chunk) WHERE c.readiness = 'embed-prohibited' RETURN c.id AS chunk LIMIT 1")
    citable_rows = g.q("MATCH (c:Chunk) WHERE c.readiness = 'citable' RETURN c.id AS chunk LIMIT 1")
    assert prohibited_rows and citable_rows
    prohibited_id = prohibited_rows[0]["chunk"]
    citable_id = citable_rows[0]["chunk"]

    sent_queries = []
    def mock_query(sql):
        sent_queries.append(sql)
        if "count(*)" in sql:
            return [{"n": "0"}]
        return [
            {"doc": "D1", "title": "T1", "shelf": "certified", "tier": "gold", "ai_ready": True,
             "chunk": prohibited_id, "readiness": "citable", "score": 0.95, "terms": [], "eligible": True},
            {"doc": "D2", "title": "T2", "shelf": "certified", "tier": "gold", "ai_ready": True,
             "chunk": citable_id, "readiness": "citable", "score": 0.85, "terms": [], "eligible": True},
        ]

    monkeypatch.setattr(BigQueryRetrieval, "query", lambda self, sql: mock_query(sql))
    try:
        res = g.retrieve("test question", principal="analyst_enterprise", purpose="analysis", k=5)
        assert any(f"`{PROJECT}.kge_{prof.client}_graph.{CHUNKS}`" in q for q in sent_queries)
        assert not any("`None." in q for q in sent_queries)
        exact_ids = [r["chunk"] for r in res["exact"]]
        post_ids = [r["chunk"] for r in res["post_filtered"]]
        assert prohibited_id not in exact_ids
        assert prohibited_id not in post_ids
        assert citable_id in exact_ids
    finally:
        g.close()


def test_sync_bigquery_chunk_readiness_updates_table_and_raises_overlay_error(monkeypatch, mock_account):
    """sync_bigquery_chunk_readiness updates readiness, purposes, and embedding on `{project}.{dataset}.chunks`
    and raises OverlayError if the BigQuery update fails."""
    from kge.adapters.bigquery import dataset_name
    from kge.adapters.retrieval.bigquery import sync_bigquery_chunk_readiness
    from kge.pipeline.overlay import OverlayError

    monkeypatch.delenv("BIGQUERY_PROJECT", raising=False)
    monkeypatch.delenv("VERTEX_PROJECT", raising=False)
    assert dataset_name("kge", "acme-corp", "graph") == "kge_acme_corp_graph"

    executed: list[tuple[str, str | None]] = []

    def mock_bq_query(sql: str, project: str | None = None):
        executed.append((sql, project))
        return []

    monkeypatch.setattr("kge.adapters.retrieval.bigquery.bq_query", mock_bq_query)

    sync_bigquery_chunk_readiness(
        "acme-corp",
        "CHUNK-9",
        "embed-prohibited",
        embedded_now=False,
        purposes=[],
    )
    assert len(executed) == 1
    sql_prohibited, proj_used = executed[-1]
    assert proj_used == PROJECT
    assert f"UPDATE `{PROJECT}.kge_acme_corp_graph.{CHUNKS}`" in sql_prohibited
    assert 'readiness = "embed-prohibited"' in sql_prohibited
    assert "purposes = []" in sql_prohibited
    assert "embedding = []" in sql_prohibited

    sync_bigquery_chunk_readiness(
        "meridian",
        "CHUNK-10",
        "citable",
        embedded_now=True,
        purposes=["analysis", "assistant"],
        embedding=[0.25, -0.5],
        embedding_model="text-embedding-004",
    )
    sql_embedded, _ = executed[-1]
    assert f"UPDATE `{PROJECT}.kge_meridian_graph.{CHUNKS}`" in sql_embedded
    assert 'readiness = "citable"' in sql_embedded
    assert "embedding = [0.25, -0.5]" in sql_embedded
    assert 'embedding_model = "text-embedding-004"' in sql_embedded

    def failing_bq_query(sql: str, project: str | None = None):
        raise RuntimeError("BigQuery error (HTTP 403): Access Denied")

    monkeypatch.setattr("kge.adapters.retrieval.bigquery.bq_query", failing_bq_query)
    with pytest.raises(OverlayError, match="BigQuery chunks table could not be updated"):
        sync_bigquery_chunk_readiness("meridian", "CHUNK-10", "embed-prohibited")


def test_bigquery_warehouses_read_the_version_the_served_artifact_names(monkeypatch, mock_account):
    """An onboarded unit's tables are the named version's (issue #57: <unit>_v<N>_<table>, the version the served
    artifact names); an established unit's are as before. Attached, the version changes only the table read, never the
    alias its columns bind to."""
    prof = resolve()
    bq_w = BigQueryWarehouses(prof, ["retail", "bancassurance"], project=PROJECT, versions={"bancassurance": 2})
    single = bq_w.rewrite_sql("SELECT sum(premium.premium_amount) FROM premium", unit="bancassurance")
    assert "bancassurance_v2_premium AS premium" in single and "bancassurance_premium" not in single
    assert "retail_monthly_revenue" in bq_w.rewrite_sql("SELECT 1 FROM monthly_revenue", unit="retail")
    attached = bq_w.rewrite_sql("SELECT r.id, bancassurance.premium.premium_amount FROM retail.monthly_revenue r "
                                "JOIN bancassurance.premium ON r.id = bancassurance.premium.policy_id")
    assert "bancassurance_v2_premium AS bancassurance_premium" in attached
    assert "bancassurance_premium.premium_amount" in attached and "retail_monthly_revenue AS r" in attached
    rolled_back = BigQueryWarehouses(prof, ["bancassurance"], project=PROJECT, versions={"bancassurance": 1})
    assert "bancassurance_v1_premium" in rolled_back.rewrite_sql("SELECT 1 FROM premium", unit="bancassurance")

    sent = []
    monkeypatch.setattr(bq_w, "_post", lambda url, headers, body: sent.append(body) or {
        "schema": {"fields": [{"name": "t"}, {"name": "c"}, {"name": "d"}]},
        "rows": [{"f": [{"v": "bancassurance_v2_premium"}, {"v": "premium_amount"}, {"v": "NUMERIC"}]}]})
    assert bq_w.columns("bancassurance") == [("premium", "premium_amount", "DECIMAL(38,9)")]
    assert "STARTS_WITH(table_name, 'bancassurance_v2_')" in sent[-1]["query"], "only the named version's columns"
    assert "CONTAINS_SUBSTR" not in sent[-1]["query"] and "ORDER BY table_name, ordinal_position" in sent[-1]["query"]
    assert bq_w.columns("bancassurance") == [("premium", "premium_amount", "DECIMAL(38,9)")] and len(sent) == 1
    assert bq_w.calendar_tables("bancassurance") == {"premium"}
    assert "STARTS_WITH(table_name, 'bancassurance_v2_')" in sent[-1]["query"]
    assert bq_w.calendar_tables("bancassurance") == {"premium"} and len(sent) == 2


def test_bigquery_columns_name_types_as_duckdb_does_and_the_catalogue_keeps_bigquery_s(monkeypatch, mock_account):
    """A review of #97: INFORMATION_SCHEMA names a column's type BigQuery's way (BOOL, INT64, FLOAT64, NUMERIC, STRING),
    and the engine reads every warehouse's columns DuckDB's way, so a yes/no column is a flag on both. columns() maps
    them; catalogue_columns keeps BigQuery's own, for a prompt that asks for BigQuery's SQL."""
    from kge.services.ask.engine.combined import role
    bq_w = BigQueryWarehouses(resolve(), ["retail"], project=PROJECT)
    native = [("BOOL", "BOOLEAN"), ("INT64", "BIGINT"), ("FLOAT64", "DOUBLE"), ("NUMERIC", "DECIMAL(38,9)"),
              ("NUMERIC(10, 2)", "DECIMAL(10,2)"), ("STRING", "VARCHAR"), ("STRING(50)", "VARCHAR"), ("DATE", "DATE"),
              ("TIMESTAMP", "TIMESTAMP"), ("BIGNUMERIC", "BIGNUMERIC"), ("ARRAY<STRING>", "ARRAY<STRING>")]
    monkeypatch.setattr(bq_w, "_post", lambda url, headers, body: {
        "schema": {"fields": [{"name": "t"}, {"name": "c"}, {"name": "d"}]},
        "rows": [{"f": [{"v": "retail_t"}, {"v": f"c{i}"}, {"v": raw}]} for i, (raw, _) in enumerate(native)]})
    assert bq_w.columns("retail") == [("t", f"c{i}", named) for i, (_, named) in enumerate(native)]
    assert bq_w.catalogue_columns("retail") == [("t", f"c{i}", raw) for i, (raw, _) in enumerate(native)]
    assert role({"col": "is_active", "dtype": bq_w.columns("retail")[0][2]}) == "flag"


def test_bigquery_warehouses_change_their_metadata_cache_only_under_their_lock(monkeypatch, mock_account):
    """A review of #97: columns() and calendar_tables() fill a cache a write clears, from any thread the API serves on;
    each change to it is made holding the warehouses' lock."""
    bq_w = BigQueryWarehouses(resolve(), ["retail"], project=PROJECT)

    class Guarded(dict):
        def __setitem__(self, key, value):
            assert bq_w._lock.locked(), "cache written without the lock"
            super().__setitem__(key, value)

        def pop(self, *args):
            assert bq_w._lock.locked(), "cache cleared without the lock"
            return super().pop(*args)

    bq_w._columns, bq_w._calendar_tables = Guarded(), Guarded()
    monkeypatch.setattr(bq_w, "_post", lambda url, headers, body: {
        "schema": {"fields": [{"name": "t"}, {"name": "c"}, {"name": "d"}]},
        "rows": [{"f": [{"v": "retail_t"}, {"v": "c"}, {"v": "BOOL"}]}]})
    assert bq_w.columns("retail") == [("t", "c", "BOOLEAN")] and bq_w.calendar_tables("retail") == {"t"}

    class Cursor:
        def execute(self, sql):
            return None

    @contextlib.contextmanager
    def cursor(unit):
        yield Cursor()
    monkeypatch.setattr(bq_w, "cursor", cursor)
    bq_w.write("retail", "DELETE FROM t WHERE TRUE")
    assert "retail" not in bq_w._columns and "retail" not in bq_w._calendar_tables


def test_engine_passes_the_artifacts_warehouse_versions_to_bigquery(monkeypatch, mock_account):
    monkeypatch.setenv("WAREHOUSE_BACKEND", "bigquery")
    monkeypatch.setenv("BIGQUERY_PROJECT", PROJECT)
    monkeypatch.setattr(Graph, "warehouse_versions", lambda self: {"bancassurance": 3})
    engine = Engine(resolve())
    try:
        assert engine.w.versions == {"bancassurance": 3} and engine.w.prefix("bancassurance") == "bancassurance_v3"
        assert engine.w.prefix("retail") == "retail"
    finally:
        engine.close()


def test_sync_loads_each_kept_version_and_prune_drops_only_versions_no_longer_kept(monkeypatch, tmp_path):
    """The sync loads each onboarded unit's versions on disk (retention keeps those a release names) as tables of their
    own, labelled with the unit and the version; prune drops a labelled version no longer synced, an established unit's
    tables by today's rule, and never an unlabelled table."""
    import duckdb
    from dataclasses import replace
    from kge.pipeline import warehouse_versions
    prof = replace(resolve(), warehouses=tmp_path / "warehouses")
    for version, rows in ((1, 1), (3, 2)):
        path = warehouse_versions.path(prof.warehouses, "leasing", version)
        path.parent.mkdir(parents=True)
        with duckdb.connect(str(path)) as con:
            con.execute("CREATE TABLE rental (id INTEGER)")
            con.execute(f"INSERT INTO rental SELECT * FROM range({rows})")
    with duckdb.connect(str(prof.warehouses / "retail.duckdb")) as con:
        con.execute("CREATE TABLE account (id INTEGER)")
    loader = load_bigquery.BigQueryLoader(project=PROJECT, dry_run=True)
    statements, pruned = [], []
    loader.query = lambda sql: statements.append(sql) or {}
    loader.prune_units = lambda dataset, units, versions: pruned.append((units, versions))
    report = load_bigquery.sync_warehouses(loader, prof, "meridian")
    assert report == {"retail": {"tables": 1, "rows": 0}, "leasing_v1": {"tables": 1, "rows": 1},
                      "leasing_v3": {"tables": 1, "rows": 2}}
    assert any("leasing_v3_rental" in s and '("kge_unit", "leasing"), ("kge_version", "v3")' in s for s in statements)
    assert any("retail_account" in s and '("kge_unit", "retail")])' in s for s in statements)
    assert pruned == [({"retail"}, {"leasing": {"v1", "v3"}})]

    loader = load_bigquery.BigQueryLoader(project=PROJECT)
    loader._account = (MockAccount(), PROJECT)
    monkeypatch.setattr(loader, "tables", lambda dataset: [
        {"tableReference": {"tableId": "leasing_v1_rental"}, "labels": {"kge_unit": "leasing", "kge_version": "v1"}},
        {"tableReference": {"tableId": "leasing_v2_rental"}, "labels": {"kge_unit": "leasing", "kge_version": "v2"}},
        {"tableReference": {"tableId": "leasing_rental"}, "labels": {"kge_unit": "leasing"}},
        {"tableReference": {"tableId": "retail_account"}, "labels": {"kge_unit": "retail"}},
        {"tableReference": {"tableId": "manual_table"}},
    ])
    sql = []
    monkeypatch.setattr(loader, "query", lambda statement: sql.append(statement) or {})
    assert loader.prune_units("kge_meridian_warehouses", {"retail"}, {"leasing": {"v1", "v3"}}) == \
        ["leasing_v2_rental", "leasing_rental"]


def test_prune_refuses_a_malformed_version_label(monkeypatch):
    loader = load_bigquery.BigQueryLoader(project=PROJECT)
    loader._account = (MockAccount(), PROJECT)
    monkeypatch.setattr(loader, "tables", lambda dataset: [
        {"tableReference": {"tableId": "leasing_vx_rental"}, "labels": {"kge_unit": "leasing", "kge_version": "vx"}}])
    sql = []
    monkeypatch.setattr(loader, "query", lambda statement: sql.append(statement) or {})
    with pytest.raises(ValueError, match="version label"):
        loader.prune_units("kge_meridian_warehouses", {"retail"}, {"leasing": {"v1"}})
    assert sql == []


def test_both_backends_offer_the_warehouse_seam(monkeypatch, mock_account):
    from kge.ports import WarehouseSet
    monkeypatch.delenv("WAREHOUSE_BACKEND", raising=False)
    engine = Engine(resolve())
    try:
        assert isinstance(engine.w, WarehouseSet) and engine.w.units == list(engine.w.con)
        assert isinstance(BigQueryWarehouses(resolve(), ["retail"], project=PROJECT), WarehouseSet)
    finally:
        engine.close()


def test_duckdb_catalogue_methods_read_the_warehouse_s_own_catalogue(monkeypatch):
    """The agent's graph-off schema reads these: columns in table then position order, primary and foreign keys as declared."""
    monkeypatch.delenv("WAREHOUSE_BACKEND", raising=False)
    engine = Engine(resolve())
    try:
        w, unit = engine.w, engine.UNIT_ORDER[0]
        columns = w.catalogue_columns(unit)
        assert columns and sorted(columns) == sorted(w.columns(unit))
        assert [t for t, _, _ in columns] == sorted(t for t, _, _ in columns)
        tables = {t for t, _, _ in columns}
        assert all(t in tables and cols for t, cols in w.primary_keys(unit))
        assert all(t in tables and rt in tables and len(cols) == len(rcols) for t, cols, rt, rcols in w.foreign_keys(unit))
    finally:
        engine.close()


def test_bigquery_catalogue_methods_read_information_schema(monkeypatch, mock_account):
    """Columns, primary keys and foreign keys from the dataset's INFORMATION_SCHEMA, the unit's (named version's) tables
    alone, under their unadorned names; a foreign key's referenced columns are the referenced table's key, in order."""
    bq_w = BigQueryWarehouses(resolve(), ["bancassurance"], project=PROJECT, versions={"bancassurance": 2})
    sent: list[str] = []

    def answer(fields, rows):
        return {"schema": {"fields": [{"name": f} for f in fields]}, "rows": [{"f": [{"v": v} for v in r]} for r in rows]}

    def post(url, headers, body):
        query = body["query"]
        sent.append(query)
        if "INFORMATION_SCHEMA.COLUMNS" in query:
            return answer("tcd", [("bancassurance_v2_policy", "policy_id", "STRING"), ("bancassurance_v2_premium", "policy_id", "STRING"),
                                  ("bancassurance_v2_premium", "premium_amount", "NUMERIC")])
        if "'FOREIGN KEY'" in query:
            return answer("tnckr", [("bancassurance_v2_premium", "fk_premium_policy", "policy_id", "1", "bancassurance_v2_policy")])
        return answer("tc", [("bancassurance_v2_policy", "policy_id")])
    monkeypatch.setattr(bq_w, "_post", post)

    assert bq_w.catalogue_columns("bancassurance") == [("policy", "policy_id", "STRING"), ("premium", "policy_id", "STRING"),
                                                       ("premium", "premium_amount", "NUMERIC")]
    assert f"`{PROJECT}.kge_{resolve().client}_warehouses.INFORMATION_SCHEMA.COLUMNS`" in sent[-1]
    assert "STARTS_WITH(table_name, 'bancassurance_v2_')" in sent[-1] and "ORDER BY table_name, ordinal_position" in sent[-1]
    assert bq_w.primary_keys("bancassurance") == [("policy", ["policy_id"])]
    assert "'PRIMARY KEY'" in sent[-1] and "KEY_COLUMN_USAGE" in sent[-1]
    assert bq_w.foreign_keys("bancassurance") == [("premium", ["policy_id"], "policy", ["policy_id"])]
    assert any("CONSTRAINT_COLUMN_USAGE" in q for q in sent)
    bq_w.close()


def test_a_steward_decision_reaches_bigquery_s_chunks_table_through_the_retrieval_adapter(monkeypatch, mock_account):
    """BigQueryRetrieval.chunk_decided reads the chunk's purposes and embedding from the graph, as Graph.steward_decide did,
    and writes them to the chunks table; the graph's own retrieval has nothing to write."""
    from kge.adapters.retrieval.graph import GraphRetrieval

    class Graph_:
        profile = "meridian"
        chunks = f"`{PROJECT}.kge_meridian_graph.{CHUNKS}`"

        def q(self, cypher, params=None):
            assert params == {"id": "CHUNK-7"}
            return [{"purposes": ["analysis"], "embedding": [0.5], "embedding_model": "hash-v1"}]

    monkeypatch.setenv("BIGQUERY_PROJECT", PROJECT)
    sent: list[str] = []
    monkeypatch.setattr("kge.adapters.retrieval.bigquery.bq_query", lambda sql, project=None: sent.append(sql) or [])
    BigQueryRetrieval(Graph_()).chunk_decided("CHUNK-7", "citable", True)
    assert len(sent) == 1 and f"UPDATE `{PROJECT}.kge_meridian_graph.{CHUNKS}`" in sent[0]
    assert 'purposes = ["analysis"]' in sent[0] and "embedding = [0.5]" in sent[0] and 'embedding_model = "hash-v1"' in sent[0]
    assert GraphRetrieval(Graph_()).chunk_decided("CHUNK-7", "citable", True) is None
