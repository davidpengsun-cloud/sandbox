"""Question trace identity is server-bound, and telemetry reads are engineer-only and audited."""
from contextlib import asynccontextmanager, nullcontext
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from opentelemetry import trace
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from kge.pipeline import trace as recording
from kge.pipeline.telemetry import FASTAPI_TELEMETRY_OFF
from kge.services import http
from kge.services.ask.mcp import MCPEndpoint
from kge.services.telemetry import routes, service
from tests.tracing_setup import tracing_to


@pytest.fixture
def context_app(monkeypatch):
    import kge.app as main
    monkeypatch.setattr(main, "PROFILE", SimpleNamespace(client="meridian"))
    monkeypatch.setattr(http, "settings", lambda: SimpleNamespace(TRUSTED_CALLER_AUDIENCE="", PORTAL_SYNC_TOKEN="trace-secret"))
    app = FastAPI(telemetry=FASTAPI_TELEMETRY_OFF)

    @app.post("/ask")
    def ask():
        return {"context": recording.telemetry_current(), "trace_id": f"{trace.get_current_span().get_span_context().trace_id:032x}"}

    @app.post("/eval")
    def evaluate():
        return {"context": recording.telemetry_current(), "trace_id": f"{trace.get_current_span().get_span_context().trace_id:032x}"}

    app.add_middleware(main.TracingMiddleware)
    return app


def test_forged_parent_and_agent_cannot_join_or_replace_question_scope(context_app):
    exporter = InMemorySpanExporter()
    parent = "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01"
    session = str(uuid4())
    headers = {"traceparent": parent, "x-kge-trace-agent": "cerebro", "x-kge-trace-session": session,
               "x-kge-trace-proof": "wrong"}
    with tracing_to(exporter), TestClient(context_app) as client:
        first = client.post("/ask", headers=headers).json()
        second = client.post("/ask", headers=headers).json()
        job = client.post("/eval", headers=headers).json()
    assert len({first["trace_id"], second["trace_id"], parent.split("-")[1]}) == 3
    assert first["context"]["kge.agent"] == second["context"]["kge.agent"] == "kge_http"
    assert first["context"]["gen_ai.conversation.id"] != second["context"]["gen_ai.conversation.id"]
    assert job["context"] == {}
    rows = exporter.get_finished_spans()
    job_span = next(row for row in rows if row.name == "POST /eval")
    assert "kge.agent" not in job_span.attributes


def test_authenticated_portal_proof_links_safe_context(context_app):
    exporter = InMemorySpanExporter()
    parent = "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01"
    session = str(uuid4())
    headers = {"traceparent": parent, "x-kge-trace-agent": "cerebro", "x-kge-trace-session": session,
               "x-kge-trace-proof": "trace-secret"}
    with tracing_to(exporter), TestClient(context_app) as client:
        got = client.post("/ask", headers=headers).json()
    assert got["trace_id"] == parent.split("-")[1]
    assert got["context"] == {"kge.client": "meridian", "kge.agent": "cerebro",
                              "gen_ai.conversation.id": session, "kge.telemetry.origin": "kge"}
    assert exporter.get_finished_spans()[0].attributes["kge.client"] == "meridian"


def test_engineer_reads_are_role_audited_and_other_roles_refused(monkeypatch):
    caller = {"who": "engineer"}
    audit = []
    app = FastAPI(telemetry=FASTAPI_TELEMETRY_OFF)
    app.include_router(routes.router)
    app.dependency_overrides[http.principal] = lambda: caller["who"]
    monkeypatch.setattr(http, "cerebro_held", lambda who, state=None: [who])
    monkeypatch.setattr(http, "may", lambda who, process: who)
    monkeypatch.setattr(http, "_role_audit", lambda state, who, held, row, endpoint, outcome: audit.append((who, row, endpoint, outcome)))
    monkeypatch.setattr(service, "reader", lambda profile: SimpleNamespace(agents=lambda start, end: []))
    with TestClient(app) as client:
        assert client.get("/telemetry/agents").status_code == 200
        for role in ("business", "steward"):
            caller["who"] = role
            assert client.get("/telemetry/agents").status_code == 403
    assert [(a[0], a[-1]) for a in audit] == [("engineer", "allowed"), ("business", "refused"), ("steward", "refused")]
    assert all(row == "telemetry" and endpoint == "GET /telemetry/agents" for _, row, endpoint, _ in audit)


@pytest.mark.parametrize("path", ["/mcp", "/mcp/search", "/mcp/graph", "/mcp/data", "/mcp/catalogue"])
def test_only_authenticated_question_mcp_tool_gets_context(monkeypatch, path):
    import kge.app as main
    monkeypatch.setattr(main, "PROFILE", SimpleNamespace(client="meridian"))
    monkeypatch.setattr(http, "mcp_principal", lambda request: "person")
    import kge.services.ask.mcp as ask_mcp
    monkeypatch.setattr(ask_mcp, "mcp_principal", lambda request: "person")
    monkeypatch.setattr(http, "quota_context", lambda who: nullcontext())
    monkeypatch.setattr(http, "mcp_usage_context", lambda request, who, name: nullcontext())
    monkeypatch.setattr(http, "person_slot", lambda who: nullcontext())
    flushes = []
    async def flushed():
        flushes.append(True)
    monkeypatch.setattr(main, "exported", flushed)
    server = http.mcp_server("test", {"ask": lambda who: who, "onboard_draft": lambda who: who})
    seen = []

    @server.tool(name="ask", structured_output=True, meta={"route": "POST /ask"})
    async def ask() -> dict[str, str]:
        seen.append(recording.telemetry_current())
        return {"ok": "yes"}

    @server.tool(name="onboard_draft", structured_output=True, meta={"route": "POST /onboard/draft"})
    async def onboard_draft() -> dict[str, str]:
        seen.append(recording.telemetry_current())
        return {"ok": "yes"}

    @asynccontextmanager
    async def lifespan(app):
        async with server.session_manager.run():
            yield

    app = FastAPI(lifespan=lifespan, telemetry=FASTAPI_TELEMETRY_OFF)
    app.add_route(path, MCPEndpoint(server), methods=["GET", "POST", "DELETE"])
    app.add_middleware(main.TracingMiddleware)
    headers = {"Accept": "application/json, text/event-stream"}
    def call(client, method, params=None):
        return client.post(path, headers=headers, json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}})
    exporter = InMemorySpanExporter()
    with tracing_to(exporter), TestClient(app) as client:
        assert call(client, "tools/list").status_code == 200
        assert call(client, "tools/call", {"name": "onboard_draft", "arguments": {}}).status_code == 200
        assert call(client, "tools/call", {"name": "ask", "arguments": {}}).status_code == 200
    assert seen[0] == {}
    assert seen[1]["kge.agent"] == "kge_mcp"
    roots = [row for row in exporter.get_finished_spans() if row.name in ("POST", f"POST {path}")]
    assert len(roots) == 3, [(row.name, dict(row.attributes or {})) for row in exporter.get_finished_spans()]
    assert sum("kge.agent" in row.attributes for row in roots) == 1
    assert len(flushes) == 1


def test_streamed_model_gateway_flushes_before_final_response(monkeypatch):
    import kge.app as main
    from fastapi.responses import StreamingResponse
    monkeypatch.setattr(main, "PROFILE", SimpleNamespace(client="meridian"))
    flushes = []
    async def flushed():
        flushes.append(True)
    monkeypatch.setattr(main, "exported", flushed)
    app = FastAPI(telemetry=FASTAPI_TELEMETRY_OFF)
    @app.post("/model/chat")
    async def model_chat():
        async def stream():
            yield b"answer"
        return StreamingResponse(stream())
    app.add_middleware(main.TracingMiddleware)
    with tracing_to(InMemorySpanExporter()), TestClient(app) as client:
        assert client.post("/model/chat").content == b"answer"
    assert len(flushes) == 1


def test_trace_proof_never_authenticates_a_person_or_mcp_caller(monkeypatch):
    from fastapi import HTTPException, Request
    ids = SimpleNamespace(resolve=lambda auth, cookie: None)
    monkeypatch.setattr(http, "served", SimpleNamespace(ids=lambda: ids, COOKIE="session", SIGN_IN="sign in", UNRELEASED_MESSAGE=None))
    monkeypatch.setattr(http, "settings", lambda: SimpleNamespace(TRUSTED_CALLER_AUDIENCE="", PORTAL_SYNC_TOKEN="trace-secret"))
    request = Request({"type": "http", "method": "POST", "path": "/mcp", "headers": [(b"x-kge-trace-proof", b"trace-secret")]})
    http.trusted_trace_context(request)
    with pytest.raises(HTTPException) as refused:
        http.mcp_principal(request)
    assert refused.value.status_code == 401
    with pytest.raises(HTTPException) as refused:
        http.principal(request)
    assert refused.value.status_code == 401


def test_ingest_rejects_foreign_client_but_accepts_matching_and_unbound(monkeypatch):
    app = FastAPI(telemetry=FASTAPI_TELEMETRY_OFF)
    app.include_router(routes.router)
    saved = []
    monkeypatch.setattr(routes, "trusted_portal", lambda request, message: None)
    monkeypatch.setattr(routes.served, "PROFILE", SimpleNamespace(client="meridian"))
    monkeypatch.setattr(routes, "sink", lambda profile: SimpleNamespace(append=lambda rows: saved.extend(rows) or len(rows)))
    attrs = {"kge.agent": "cerebro", "gen_ai.conversation.id": str(uuid4()),
             "http.request.method": "POST", "http.route": "/api/cerebro/stream", "http.response.status_code": 200}
    sample = {"id": "a" * 16, "trace_id": "b" * 32, "parent_id": None,
              "name": "POST /api/cerebro/stream", "started_at": "2026-11-26T12:00:00Z", "ended_at": "2026-11-26T12:00:01Z"}
    with TestClient(app) as client:
        for tenant in ("foreign", "meridian", None):
            current = dict(attrs)
            if tenant is not None:
                current["kge.client"] = tenant
            response = client.post("/telemetry/spans", json={"spans": [{**sample, "attributes": current}]})
            assert response.status_code == (422 if tenant == "foreign" else 200)
    assert len(saved) == 2


def test_ingest_preserves_aborted_spans_without_status_and_marks_all_aborts_failed(monkeypatch):
    app = FastAPI(telemetry=FASTAPI_TELEMETRY_OFF)
    app.include_router(routes.router)
    saved = []
    monkeypatch.setattr(routes, "trusted_portal", lambda request, message: None)
    monkeypatch.setattr(routes.served, "PROFILE", SimpleNamespace(client="meridian"))
    monkeypatch.setattr(routes, "sink", lambda profile: SimpleNamespace(append=lambda rows: saved.extend(rows) or len(rows)))
    attrs = {"kge.agent": "cerebro", "gen_ai.conversation.id": str(uuid4()), "error.type": "aborted",
             "http.request.method": "POST", "http.route": "/api/cerebro/stream"}
    sample = {"id": "a" * 16, "trace_id": "b" * 32, "parent_id": None,
              "name": "POST /api/cerebro/stream", "started_at": "2026-11-26T12:00:00Z", "ended_at": "2026-11-26T12:00:01Z"}
    with TestClient(app) as client:
        for status in (None, 200):
            current = dict(attrs)
            if status is not None:
                current["http.response.status_code"] = status
            assert client.post("/telemetry/spans", json={"spans": [{**sample, "attributes": current}]}).status_code == 200
        del attrs["error.type"]
        assert client.post("/telemetry/spans", json={"spans": [{**sample, "attributes": attrs}]}).status_code == 422
    assert [row["attributes"]["kge.outcome"] for row in saved] == ["failed", "failed"]
