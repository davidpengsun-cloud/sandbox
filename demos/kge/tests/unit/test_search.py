"""Search ranking, transport schemas, scope and failure contracts."""
from types import SimpleNamespace

import anyio
import pytest
from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.testclient import TestClient
from mcp.server.fastmcp.exceptions import ToolError

from kge.services.ask.mcp import tool
from kge.services.http import principal
from kge.services.search import mcp, routes, service
from kge.services.search.models import Search, SearchResponse


class Graph:
    passage_score = .5

    def readable_units(self, who):
        return {"retail"} if who == "analyst_retail" else set()

    def may(self, who, process):
        return who != "pipeline_tagger" and process == "querying"

    def domain(self):
        return {"aliases": {"sales": "Revenue"}, "restricted": [("retail", "monthly_revenue", "amount")]}

    def implementations(self, term):
        if term == "context-only":
            return []
        return [{"unit": unit, "tbl": "monthly_revenue", "col": "amount"} for unit in ("retail", "wealth")]

    def q(self, query, params=None):
        if "c:Chunk" in query:
            return [{"id": identifier, "text": "Opening boilerplate. " * 30 + "Revenue is the matching passage."}
                    for identifier in params["ids"]]
        if "DERIVED_FROM" in query:
            return [{"derived": "retail.report", "source": "retail.monthly_revenue"},
                    {"derived": "wealth.report", "source": "retail.monthly_revenue"}]
        if "MEASURE_OF" in query:
            if params["term"] == "context-only":
                return []
            return [{"id": "revenue", "unit": "retail", "column": "retail.monthly_revenue.amount"},
                    {"id": "private_metric", "unit": "wealth", "column": "wealth.secret.amount"}]
        if "HAS_COLUMN" in query:
            return [{"id": "retail.monthly_revenue.amount", "unit": "retail", "title": "amount",
                     "description": "Financial proceeds in currency", "product": "retail.monthly_revenue"}]
        if "t:Tbl" in query:
            return [{"id": f"retail.{name}", "unit": "retail", "title": name} for name in ("monthly_revenue", "report")]
        return [{"id": "revenue", "name": "Revenue"}, {"id": "context-only", "name": "context-only"}]

    def retrieve(self, query, who, purpose, k):
        assert (who, purpose, k) == ("analyst_retail", "analysis", 100)
        return {"exact": [{"doc": name, "title": name, "chunk": name + ":1", "score": .12 if query == "zzqx blorf" else self.passage_score, "excerpt": name}
                          for name in ("retail-doc", "wealth-secret", "unscoped")]}

    def certification(self, ids):
        return {name: {"unit": unit} for name, unit in
                (("retail-doc", "retail"), ("wealth-secret", "wealth"), ("unscoped", ""))}


class Semantics:
    def __init__(self):
        self.contexts = []

    def contexts_of(self, term):
        return self.contexts if term == "context-only" else []


@pytest.fixture
def engine():
    return SimpleNamespace(g=Graph(), semantics=Semantics())


def test_all_five_kinds_scope_reasons_and_stable_ranking(engine):
    response = service.search(engine, "analyst_retail", Search(query="revenue"))
    assert {hit.kind for hit in response.hits} == {"product", "column", "term", "metric", "document"}
    assert {hit.unit for hit in response.hits} == {"retail"}
    assert "wealth" not in response.model_dump_json() and "unscoped" not in response.model_dump_json()
    assert response == service.search(engine, "analyst_retail", Search(query="revenue"))
    assert [(hit.score, hit.kind, hit.unit, hit.id) for hit in response.hits] == sorted(
        [(hit.score, hit.kind, hit.unit, hit.id) for hit in response.hits], key=lambda row: (-row[0], *row[1:]))
    linked = next(hit for hit in response.hits if hit.id == "retail.report")
    assert linked.score == .6 and linked.why.via[-1] == "retail.monthly_revenue"
    assert service.search(engine, "nobody", Search(query="revenue")).hits == []
    assert service.search(engine, "analyst_retail", Search(query="revenue", kinds=[])).hits == []
    synonym = service.search(engine, "analyst_retail", Search(query="sales", kinds=["product"], limit=1)).hits[0]
    assert synonym.id == "retail.monthly_revenue" and synonym.why.kind == "synonym"


def test_limit_applies_after_kind_filter_and_query_has_word_boundaries(engine):
    result = service.search(engine, "analyst_retail", Search(query="sales", kinds=["document"], limit=1))
    assert len(result.hits) == 1 and result.hits[0].why.kind == "passage"
    assert service.search(engine, "analyst_retail", Search(query="ale", kinds=["product"])).hits == []
    assert service.search(engine, "analyst_retail", Search(query="...", kinds=["product"])).hits == []


@pytest.fixture
def transports(engine, monkeypatch):
    from kge.services import http
    state = SimpleNamespace(engine=lambda: engine, SIGN_IN="sign in",
                            ids=lambda: SimpleNamespace(resolve=lambda token, cookie: token.removeprefix("Bearer ") if token else None))
    monkeypatch.setattr(routes, "served", state)
    monkeypatch.setattr(mcp, "served", state)
    monkeypatch.setattr(http, "served", state)
    from kge.adapters.model import ExternalCallError
    from fastapi.responses import JSONResponse
    app = FastAPI()
    app.add_exception_handler(RequestValidationError, http.request_validation_failed)
    app.add_exception_handler(ExternalCallError, lambda request, exc: JSONResponse({"detail": str(exc)}, status_code=503))
    app.include_router(routes.router)
    app.dependency_overrides[principal] = lambda: "analyst_retail"
    server = mcp.mcp_server(tool)
    with TestClient(app) as client:
        yield client, server


def call(server, body, who="analyst_retail"):
    ctx = SimpleNamespace(request_context=SimpleNamespace(request=SimpleNamespace(
        headers={"authorization": f"Bearer {who}"} if who else {})))
    return anyio.run(server._tool_manager.call_tool, "search", body, ctx)


def test_http_mcp_parity_and_shared_schemas(transports):
    client, server = transports
    body = {"query": "revenue"}
    assert client.post("/search", json=body).json() == call(server, body).model_dump(exclude_unset=True)
    registered = anyio.run(server.list_tools)[0]
    schema = Search.model_json_schema()
    assert registered.inputSchema["properties"] == schema["properties"]
    assert registered.inputSchema["required"] == schema["required"]
    assert registered.inputSchema["additionalProperties"] is False
    assert registered.outputSchema == SearchResponse.model_json_schema()
    operation = client.get("/openapi.json").json()["paths"]["/search"]["post"]
    assert operation["tags"] == ["external: knowledge"]
    assert "external" not in operation
    assert registered.meta == {"route": "POST /search", "tags": ["external: knowledge"]}


@pytest.mark.parametrize("body", [{"query": " "}, {"query": "x" * 2001}, {"query": "x", "limit": 0},
                                 {"query": "x", "limit": 101}, {"query": "x", "kinds": ["other"]},
                                 {"query": "x", "principal": "analyst_enterprise"}])
def test_http_mcp_validation_errors_match(transports, body):
    client, server = transports
    response = client.post("/search", json=body)
    assert response.status_code == 422
    assert all(set(error) == {"loc", "msg"} for error in response.json()["detail"])
    with pytest.raises(ToolError) as error:
        call(server, body)
    assert str(error.value) == f"422: {response.json()['detail']}"


def test_mcp_auth_and_roles(transports):
    _, server = transports
    for who, status in ((None, 401), ("pipeline_tagger", 403)):
        with pytest.raises(ToolError, match=f"{status}: "):
            call(server, {"query": "revenue"}, who)


def test_retrieval_failure_is_the_same_503(transports, engine, monkeypatch):
    from kge.adapters.model import ExternalCallError
    client, server = transports

    def fail(*args, **kwargs):
        raise ExternalCallError("retrieval unavailable")

    monkeypatch.setattr(engine.g, "retrieve", fail)
    response = client.post("/search", json={"query": "revenue"})
    assert response.status_code == 503
    with pytest.raises(ToolError, match="503: retrieval unavailable"):
        call(server, {"query": "revenue"})


def test_ties_do_not_depend_on_graph_or_passage_order(engine, monkeypatch):
    request = Search(query="revenue", limit=100)
    expected = service.search(engine, "analyst_retail", request)
    original_query = engine.g.q
    original_retrieve = engine.g.retrieve
    monkeypatch.setattr(engine.g, "q", lambda *args, **kwargs: list(reversed(original_query(*args, **kwargs))))
    monkeypatch.setattr(engine.g, "retrieve", lambda *args, **kwargs: {
        "exact": list(reversed(original_retrieve(*args, **kwargs)["exact"]))})
    assert service.search(engine, "analyst_retail", request) == expected


def test_a_metric_with_a_hidden_source_is_not_discoverable(engine, monkeypatch):
    original_query = engine.g.q

    def query(text, params=None):
        if "MEASURE_OF" in text:
            return [{"id": "cross_unit_metric", "unit": unit, "column": f"{unit}.secret.amount"}
                    for unit in ("retail", "wealth")]
        return original_query(text, params)

    monkeypatch.setattr(engine.g, "q", query)
    assert service.search(engine, "analyst_retail", Search(query="revenue", kinds=["metric"])).hits == []


def test_no_retrieval_without_read_access_or_document_kind(engine, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("retrieval was unnecessary")

    monkeypatch.setattr(engine.g, "retrieve", forbidden)
    assert service.search(engine, "nobody", Search(query="revenue")).hits == []
    assert service.search(engine, "analyst_retail", Search(query="revenue", kinds=["product"])).hits


def test_http_and_mcp_use_the_same_role_failure(transports):
    client, server = transports
    client.app.dependency_overrides[principal] = lambda: "pipeline_tagger"
    response = client.post("/search", json={"query": "revenue"})
    assert response.status_code == 403
    with pytest.raises(ToolError) as error:
        call(server, {"query": "revenue"}, "pipeline_tagger")
    assert str(error.value).removeprefix("Error executing tool search: ") == f"403: {response.json()['detail']}"


@pytest.mark.parametrize("dependency", ["join_source", "join_target", "filter", "selector"])
def test_context_only_hits_require_every_dependency_unit(engine, dependency):
    context = SimpleNamespace(id="context-only", unit="retail", measure="context-measure",
                              fact="retail.report", joins=[], filters=[], selectors=[])
    if dependency.startswith("join"):
        context.joins = [SimpleNamespace(source="wealth.hidden" if dependency == "join_source" else "retail.report",
                                        target="wealth.hidden" if dependency == "join_target" else "retail.report")]
    else:
        setattr(context, "filters" if dependency == "filter" else "selectors", [SimpleNamespace(table="wealth.hidden")])
    engine.semantics.contexts = [context]
    denied = service.search(engine, "analyst_retail", Search(query="context-only", kinds=["product", "metric", "term"]))
    assert denied.hits == []
    context.joins = []
    context.filters = []
    context.selectors = []
    allowed = service.search(engine, "analyst_retail", Search(query="context-only", kinds=["product", "metric", "term"]))
    assert {hit.id for hit in allowed.hits} == {"retail.report", "retail.monthly_revenue", "context-measure", "context-only"}
    assert next(hit for hit in allowed.hits if hit.kind == "product").why.via == ["context-only"]


@pytest.mark.parametrize("query,weight,reason", [("revenue", 1, "term"), ("sales", .95, "synonym"),
                                                 ("monthly revenue total", .9, "term")])
def test_term_weights(engine, query, weight, reason):
    hit = service.search(engine, "analyst_retail", Search(query=query, kinds=["term"])).hits[0]
    assert (hit.score, hit.why.kind, hit.why.term) == (weight, reason, "Revenue")


@pytest.mark.parametrize("query,reason,text", [("amount", "name", "amount"),
                                              ("financial proceeds", "description", "Financial proceeds in currency")])
def test_metadata_reasons_are_honest_and_restricted(engine, query, reason, text):
    hit = service.search(engine, "analyst_retail", Search(query=query, kinds=["column"])).hits[0]
    assert hit.score == .8
    assert (hit.why.kind, hit.why.text, hit.why.term) == (reason, text, None)
    assert hit.restricted is True
    mapped = service.search(engine, "analyst_retail", Search(query="sales", kinds=["column"])).hits[0]
    assert mapped.restricted is True
    product = service.search(engine, "analyst_retail", Search(query="report", kinds=["product"])).hits[0]
    assert product.restricted is False


def test_same_kind_and_unit_ties_sort_by_id(engine, monkeypatch):
    original = engine.g.q
    def query(text, params=None):
        rows = original(text, params)
        if "t:Tbl" in text and "HAS_COLUMN" not in text and "DERIVED_FROM" not in text:
            return [{**row, "title": "Shared title"} for row in reversed(rows)]
        return rows
    monkeypatch.setattr(engine.g, "q", query)
    hits = service.search(engine, "analyst_retail", Search(query="shared", kinds=["product"])).hits
    assert [(hit.id, hit.score) for hit in hits] == [("retail.monthly_revenue", .8), ("retail.report", .8)]


@pytest.mark.parametrize("similarity,expected", [(0, None), (-.1, None), (.199, None), (.2, .14), (.5, .35), (1, .7), (1.2, .7)])
def test_passage_floor_cap_and_matching_text(engine, monkeypatch, similarity, expected):
    monkeypatch.delenv("SEARCH_PASSAGE_MIN_SIMILARITY", raising=False)
    engine.g.passage_score = similarity
    hits = service.search(engine, "analyst_retail", Search(query="revenue", kinds=["document"])).hits
    if expected is None:
        assert hits == []
    else:
        assert len(hits) == 1
        hit = hits[0]
        assert hit.score == expected
        assert hit.why.kind == "passage"
        assert hit.why.chunk == "retail-doc:1"
        assert hit.why.similarity == similarity
        assert "Revenue is the matching passage" in hit.why.text
        assert hit.why.text != hit.title
        assert hit.why.text.startswith("…")


def test_configurable_passage_floor_and_nonsense(engine, monkeypatch):
    assert service.search(engine, "analyst_retail", Search(query="zzqx blorf", kinds=["document"])).hits == []
    monkeypatch.setenv("SEARCH_PASSAGE_MIN_SIMILARITY", "0.6")
    assert service.search(engine, "analyst_retail", Search(query="revenue", kinds=["document"])).hits == []
    monkeypatch.setenv("SEARCH_PASSAGE_MIN_SIMILARITY", "0")
    engine.g.passage_score = 0
    assert service.search(engine, "analyst_retail", Search(query="revenue", kinds=["document"])).hits == []
    engine.g.passage_score = .01
    assert service.search(engine, "analyst_retail", Search(query="revenue", kinds=["document"])).hits


@pytest.mark.parametrize("value", ["oops", "", "nan", "inf", "-0.1", "1.1"])
def test_invalid_passage_floor_is_refused(engine, monkeypatch, value):
    monkeypatch.setenv("SEARCH_PASSAGE_MIN_SIMILARITY", value)
    with pytest.raises(ValueError, match="SEARCH_PASSAGE_MIN_SIMILARITY"):
        service.search(engine, "analyst_retail", Search(query="revenue", kinds=["document"]))


def test_name_matching_does_not_include_parent_table_or_unit(engine):
    assert service.search(engine, "analyst_retail", Search(query="monthly", kinds=["column"])).hits == []
    assert service.search(engine, "analyst_retail", Search(query="retail", kinds=["column", "product"])).hits == []


def test_name_reason_wins_when_description_matches_too(engine, monkeypatch):
    original = engine.g.q
    def query(text, params=None):
        rows = original(text, params)
        if "HAS_COLUMN" in text:
            return [{**row, "description": "Amount in currency"} for row in rows]
        return rows
    monkeypatch.setattr(engine.g, "q", query)
    hit = service.search(engine, "analyst_retail", Search(query="amount", kinds=["column"])).hits[0]
    assert (hit.why.kind, hit.why.text) == ("name", "amount")
