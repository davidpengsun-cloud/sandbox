"""Transport limits reject work before parsing and preserve one request clock."""
import time
from types import SimpleNamespace

import anyio
import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from kge.adapters.deadline import ExternalCallError, RequestDeadlineExceeded, request_remaining
from kge.services.http import BODY_TOO_LARGE, MAX_REQUEST_BYTES, RequestLimitsMiddleware
from kge.services.ask import service


def test_chunked_body_is_bounded_before_the_route_runs():
    called = []
    sent = []
    chunks = iter([{"type": "http.request", "body": b"x" * MAX_REQUEST_BYTES, "more_body": True},
                   {"type": "http.request", "body": b"x", "more_body": False}])

    async def receive():
        return next(chunks)

    async def send(message):
        sent.append(message)

    async def app(scope, receive, send):
        called.append(True)

    anyio.run(RequestLimitsMiddleware(app), {"type": "http", "headers": []}, receive, send)
    assert not called
    assert sent[0]["status"] == 413
    assert BODY_TOO_LARGE.encode() in sent[1]["body"]


def test_declared_oversize_is_refused_without_reading():
    sent = []

    async def unused(*args):
        pytest.fail("oversized body reached a reader or route")

    async def send(message):
        sent.append(message)

    scope = {"type": "http", "headers": [(b"content-length", str(MAX_REQUEST_BYTES + 1).encode())]}
    anyio.run(RequestLimitsMiddleware(unused), scope, unused, send)
    assert sent[0]["status"] == 413


def test_slow_upload_uses_the_request_clock(monkeypatch):
    monkeypatch.setenv("MODEL_REQUEST_TIMEOUT", ".02")
    sent = []

    async def receive():
        await anyio.sleep(1)

    async def send(message):
        sent.append(message)

    async def unused(*args):
        pytest.fail("slow upload reached route")

    anyio.run(RequestLimitsMiddleware(unused), {"type": "http", "headers": []}, receive, send)
    assert sent[0]["status"] == 503
    assert request_remaining() is None


@pytest.mark.parametrize("status", [200, 202])
def test_late_success_is_refused_but_job_acknowledgement_is_preserved(monkeypatch, status):
    monkeypatch.setenv("MODEL_REQUEST_TIMEOUT", ".02")
    app = FastAPI()
    app.add_middleware(RequestLimitsMiddleware)
    app.add_exception_handler(ExternalCallError, lambda request, exc: JSONResponse({"detail": str(exc)}, status_code=503))
    budgets = []

    @app.get("/probe", status_code=status)
    def probe():
        budgets.append(request_remaining())
        time.sleep(.04)
        return {"job": "accepted"} if status == 202 else {"figure": 7}

    with TestClient(app) as client:
        response = client.get("/probe")
    assert budgets and 0 < budgets[0] <= .02
    assert response.status_code == (202 if status == 202 else 503)
    if status == 200:
        assert response.json() == {"detail": str(RequestDeadlineExceeded())}
    assert request_remaining() is None


def test_unmatched_text_is_bounded_after_privacy_and_heldout_checks(monkeypatch, tmp_path):
    question = "z" * 1500
    inspected = []
    recorded = []
    monkeypatch.setattr(service, "_is_heldout_question", lambda text, profile: inspected.append(text) or False)
    monkeypatch.setattr(service.runtrace, "append_jsonl", lambda path, row: recorded.append(row))
    service.log_unmatched(SimpleNamespace(run_dir=lambda: tmp_path), "/ask", "graph", True, "reader", question,
                          {"behaviour": "abstain", "reason": "No glossary term recognised"}, None)
    assert inspected == [question]
    assert recorded[0]["question"] == question[:500]


def test_eval_stops_between_answers_when_the_budget_expires(monkeypatch, tmp_path):
    from kge.adapters.deadline import request_budget
    from kge.services.quality import service as quality
    monkeypatch.setenv("MODEL_REQUEST_TIMEOUT", ".02")
    monkeypatch.setattr("kge.eval.sets.items", lambda path: [{"question": "invented", "id": "one", "kind": "within"}] * 10)
    monkeypatch.setattr("kge.eval.run_golden.uncertified_ids", lambda path: [])
    monkeypatch.setattr("kge.eval.run_golden.score", lambda *args: (True, ""))
    calls = []

    def answer(*args, **kwargs):
        calls.append(True)
        time.sleep(.04)
        return {}

    with pytest.raises(RequestDeadlineExceeded):
        request_budget(quality.evaluate)(SimpleNamespace(estate=lambda: tmp_path), SimpleNamespace(answer=answer), "reader")
    assert calls == [True]


@pytest.mark.parametrize("model_name", ["Ask", "AgentAsk"])
@pytest.mark.parametrize("character", ["z", "界"])
def test_question_boundary_counts_characters(model_name, character):
    from pydantic import ValidationError
    from kge.services.ask import models
    model = getattr(models, model_name)
    question = character * models.MAX_QUESTION_CHARS
    assert model(question=question).question == question
    with pytest.raises(ValidationError):
        model(question=question + character)


def test_unmatched_privacy_checks_the_suffix_that_would_be_truncated(monkeypatch, tmp_path):
    monkeypatch.setattr(service.runtrace, "append_jsonl", lambda *args: pytest.fail("private question was logged"))
    service.log_unmatched(SimpleNamespace(run_dir=lambda: tmp_path), "/ask", "graph", True, "reader",
                          "z" * 600 + " contact person@example.com", {"behaviour": "abstain", "reason": "No glossary term recognised"}, None)


def test_budget_expiring_at_scope_entry_is_a_plain_503(monkeypatch):
    monkeypatch.setenv("MODEL_REQUEST_TIMEOUT", "1e-9")
    app = FastAPI()
    app.add_middleware(RequestLimitsMiddleware)
    with TestClient(app) as client:
        response = client.get("/unreached")
    assert response.status_code == 503
    assert response.json() == {"detail": str(RequestDeadlineExceeded())}
    assert request_remaining() is None


def test_mcp_deadline_inside_a_tool_keeps_the_503_status(monkeypatch):
    from mcp.server.fastmcp.exceptions import ToolError
    from kge.services import http
    monkeypatch.setattr(http, "mcp_principal", lambda request: "reader")
    server = http.mcp_server("deadline-probe")

    @server.tool()
    async def probe() -> dict:
        raise RequestDeadlineExceeded()

    with pytest.raises(ToolError, match="^503: " + str(RequestDeadlineExceeded()).replace(".", r"\.")):
        anyio.run(server._tool_manager.call_tool, "probe", {})


@pytest.mark.parametrize("path", ["/onboard/source", "/onboard/draft", "/onboard/accept", "/onboard/certify",
                                  "/steward/decide", "/session", "/future-write"])
def test_completed_write_keeps_its_result_after_deadline(monkeypatch, tmp_path, path):
    monkeypatch.setenv("MODEL_REQUEST_TIMEOUT", ".02")
    app = FastAPI()
    app.add_middleware(RequestLimitsMiddleware)
    marker = tmp_path / "written"

    @app.post(path, status_code=201)
    def write():
        marker.write_text("committed")
        time.sleep(.04)
        return {"saved": True}

    with TestClient(app) as client:
        response = client.post(path)
    assert marker.read_text() == "committed"
    assert response.status_code == 201, response.text
    assert response.json() == {"saved": True}


@pytest.mark.parametrize("path", ["/answer", "/ask", "/agent", "/search", "/data/query"])
def test_late_read_is_withheld_with_route_neutral_detail(monkeypatch, path):
    monkeypatch.setenv("MODEL_REQUEST_TIMEOUT", ".02")
    app = FastAPI()
    app.add_middleware(RequestLimitsMiddleware)

    @app.post(path)
    def read():
        time.sleep(.04)
        return {"figure": 7}

    with TestClient(app) as client:
        response = client.post(path)
    assert response.status_code == 503
    assert response.json() == {"detail": str(RequestDeadlineExceeded())}
    assert "question" not in response.json()["detail"]


@pytest.mark.parametrize("route", ["POST /onboard/source", "POST /session", "POST /future-write", "POST /answer", "GET /onboard"])
def test_mcp_completed_writes_keep_results_but_reads_expire(monkeypatch, route):
    from mcp.server.fastmcp.exceptions import ToolError
    from kge.services import http
    monkeypatch.setenv("MODEL_REQUEST_TIMEOUT", ".02")
    monkeypatch.setattr(http, "mcp_principal", lambda request: "reader")
    server = http.mcp_server("late-probe")
    written = []

    @server.tool(meta={"route": route})
    async def probe() -> dict:
        written.append(True)
        await anyio.sleep(.04)
        return {"saved": True}

    if http.read_route(*route.split(" ", 1)):
        with pytest.raises(ToolError, match="^503: The request"):
            anyio.run(server._tool_manager.call_tool, "probe", {})
    else:
        assert anyio.run(server._tool_manager.call_tool, "probe", {}) == {"saved": True}
    assert written == [True]
    assert not http._IN_FLIGHT


def test_tool_limits_are_scoped_to_server_and_tool_identity():
    from kge.services import http
    from kge.services.ask.models import Ask
    http.tool_limits("intended", ask=Ask)
    for server_name, expected in [("intended", 2000), ("unrelated", None)]:
        server = http.mcp_server(server_name)

        @server.tool(name="ask")
        def unrelated(question: str, k: int = 100) -> dict:
            return {}

        fields = server._tool_manager.get_tool("ask").parameters["properties"]
        assert fields["question"].get("maxLength") == expected
        assert "maximum" not in fields["k"]


@pytest.mark.parametrize("failure", ["error", "cancel"])
def test_mcp_slots_release_on_unexpected_error_and_cancellation(monkeypatch, failure):
    from mcp.server.fastmcp.exceptions import ToolError
    from kge.services import http
    monkeypatch.setattr(http, "mcp_principal", lambda request: "reader")
    server = http.mcp_server("exit-probe")

    async def exercise():
        entered = anyio.Event()

        @server.tool(meta={"route": "POST /retrieve"})
        async def probe() -> dict:
            entered.set()
            if failure == "error":
                raise RuntimeError("Invented failure.")
            await anyio.sleep_forever()

        if failure == "error":
            with pytest.raises(ToolError, match="Invented failure"):
                await server._tool_manager.call_tool("probe", {})
        else:
            async with anyio.create_task_group() as group:
                group.start_soon(server._tool_manager.call_tool, "probe", {})
                await entered.wait()
                assert http._IN_FLIGHT == {"reader": 1}
                group.cancel_scope.cancel()
        assert not http._IN_FLIGHT

    anyio.run(exercise)


@pytest.mark.parametrize("model_name,field,base", [("Retrieve", "query", {}), ("Retrieve", "purpose", {"query": "invented"}),
                                                   ("Ask", "purpose", {"question": "invented"}),
                                                   ("AgentAsk", "purpose", {"question": "invented"})])
def test_query_and_purpose_allow_exactly_2000_characters(model_name, field, base):
    from pydantic import ValidationError
    from kge.services.ask import models
    model = getattr(models, model_name)
    text = "界" * 2000
    assert getattr(model(**{**base, field: text}), field) == text
    with pytest.raises(ValidationError):
        model(**{**base, field: text + "界"})


def test_signed_in_public_route_never_takes_the_http_allowance(monkeypatch):
    from kge.services import http
    state = SimpleNamespace(ids=lambda: SimpleNamespace(resolve=lambda auth, cookie: "reader"), COOKIE="session")
    monkeypatch.setattr(http, "served", state)
    app = FastAPI()
    app.add_middleware(RequestLimitsMiddleware)

    @app.get("/public")
    def public():
        return {"ready": True}

    with TestClient(app) as client:
        assert client.get("/public", headers={"Authorization": "Bearer invented"}).status_code == 200
        from contextlib import ExitStack
        with ExitStack() as stack:
            for _ in range(http.MAX_IN_FLIGHT):
                stack.enter_context(http.person_slot("reader"))
            response = client.get("/public", headers={"Authorization": "Bearer invented"})
            assert response.status_code == 200
            assert http._IN_FLIGHT == {"reader": http.MAX_IN_FLIGHT}
    assert not http._IN_FLIGHT


def test_an_in_process_hop_takes_no_slot_of_the_person_it_serves(monkeypatch):
    from contextlib import ExitStack

    import httpx

    from kge.services import http
    state = SimpleNamespace(ids=lambda: SimpleNamespace(resolve=lambda auth, cookie: "reader"), COOKIE="session")
    monkeypatch.setattr(http, "served", state)
    app = FastAPI()
    app.add_middleware(RequestLimitsMiddleware)

    @app.post("/context")
    def context(who: str = http.Depends(http.principal)):
        return {"who": who, "in_flight": dict(http._IN_FLIGHT)}

    async def hop():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://context") as client:
            return await client.post("/context", json={}, headers={"authorization": "Bearer invented"})

    with ExitStack() as stack:
        for _ in range(http.MAX_IN_FLIGHT):
            stack.enter_context(http.person_slot("reader"))
        assert anyio.run(hop).status_code == 429
        with http.in_process_hop():
            response = anyio.run(hop)
        assert response.status_code == 200
        assert response.json() == {"who": "reader", "in_flight": {"reader": http.MAX_IN_FLIGHT}}
    assert not http._IN_FLIGHT


def test_bodyless_uploads_hold_no_slots_and_release_on_disconnect(monkeypatch):
    from kge.services import http
    state = SimpleNamespace(ids=lambda: SimpleNamespace(resolve=lambda auth, cookie: auth), COOKIE="session")
    monkeypatch.setattr(http, "served", state)

    async def exercise():
        full = anyio.Event()
        release = anyio.Event()
        waiting = []
        rejected = []

        async def receive():
            waiting.append(True)
            if len(waiting) == http.MAX_IN_FLIGHT:
                full.set()
            await release.wait()
            return {"type": "http.disconnect"}

        async def send(message):
            rejected.append(message)

        async def unused(*args):
            pytest.fail("a disconnected upload reached route work")

        middleware = http.RequestLimitsMiddleware(unused)

        def scope():
            return {"type": "http", "method": "POST", "path": "/onboard/source",
                    "headers": [(b"authorization", b"Bearer invented")]}

        async with anyio.create_task_group() as group:
            for _ in range(http.MAX_IN_FLIGHT):
                group.start_soon(middleware, scope(), receive, send)
            try:
                with anyio.fail_after(3):
                    await full.wait()
                assert not http._IN_FLIGHT
                assert not rejected
            finally:
                release.set()
        assert not http._IN_FLIGHT

    anyio.run(exercise)


@pytest.mark.parametrize("route", ["GET /", "GET /whoami", "GET /readable-units", "GET /onboard", "GET /catalogue",
                                  "GET /map/{term}", "POST /session", "DELETE /session"])
def test_mcp_cheap_tools_do_not_take_slots(monkeypatch, route):
    from contextlib import ExitStack
    from kge.services import http
    monkeypatch.setattr(http, "mcp_principal", lambda request: "reader")
    server = http.mcp_server("cheap-probe")

    @server.tool(meta={"route": route})
    async def probe() -> dict:
        return {"ready": True}

    with ExitStack() as stack:
        for _ in range(http.MAX_IN_FLIGHT):
            stack.enter_context(http.person_slot("reader"))
        assert anyio.run(server._tool_manager.call_tool, "probe", {}) == {"ready": True}
    assert not http._IN_FLIGHT


@pytest.mark.parametrize("path", ["/ask", "/agent", "/answer", "/context", "/retrieve", "/search", "/data/query",
                                  "/data/preview", "/data/export", "/sql/check", "/eval", "/onboard/source",
                                  "/onboard/rows", "/onboard/run", "/steward/decide"])
def test_work_routes_take_slots_only_after_complete_body(monkeypatch, path):
    from kge.services import http
    state = SimpleNamespace(ids=lambda: SimpleNamespace(resolve=lambda auth, cookie: "reader"), COOKIE="session")
    monkeypatch.setattr(http, "served", state)
    sent = []
    chunks = iter([{"type": "http.request", "body": b'{"invented":', "more_body": True},
                   {"type": "http.request", "body": b'1}', "more_body": False}])

    async def receive():
        assert not http._IN_FLIGHT
        return next(chunks)

    async def send(message):
        sent.append(message)

    async def app(scope, receive, send):
        assert http._IN_FLIGHT == {"reader": 1}
        await JSONResponse({"ok": True})(scope, receive, send)

    scope = {"type": "http", "method": "POST", "path": path, "headers": [(b"authorization", b"Bearer invented")]}
    anyio.run(http.RequestLimitsMiddleware(app), scope, receive, send)
    assert sent[0]["status"] == 200
    assert not http._IN_FLIGHT
