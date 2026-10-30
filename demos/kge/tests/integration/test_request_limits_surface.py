"""The adversarial question, request-clock, map and retrieval probes on both transports."""
import json
import time
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.routing import APIRoute

from kge.adapters.deadline import RequestDeadlineExceeded, request_remaining
from kge.services.ask.models import MAX_QUESTION_CHARS, QUESTION_TOO_LONG
from kge.services.http import BODY_TOO_LARGE, MAX_REQUEST_BYTES, RequestLimitsMiddleware
from tests.integration.test_answer_text import client as client, mcp_wire


@pytest.mark.parametrize("path", ["/ask", "/answer", "/agent", "/context"])
def test_megabyte_question_refused_fast_without_work(client, monkeypatch, path):
    from kge.services.ask import routes

    def unexpected(*args):
        raise AssertionError("oversized question reached service")

    monkeypatch.setattr(routes.service, "ask", unexpected)
    started = time.monotonic()
    response = client.post(path, json={"question": "z" * 1_000_000}, headers=client.as_("analyst_retail"))
    assert response.status_code == 422
    assert response.json() == {"detail": QUESTION_TOO_LONG}
    assert time.monotonic() - started < 1


@pytest.mark.parametrize("path,tool", [("/mcp/answer", "ask"), ("/mcp", "get_context")])
def test_mcp_question_limit_has_same_plain_detail(client, path, tool):
    response = mcp_wire(client, path, "analyst_retail", tool, {"question": "z" * 1_000_000})
    assert response["isError"]
    assert response["content"][0]["text"] == f"422: {QUESTION_TOO_LONG}"


def test_rejected_load_does_not_delay_a_colleagues_ask(client):
    def oversized(_):
        return client.post("/ask", json={"question": "z" * 1_000_000}, headers=client.as_("analyst_retail")).status_code
    started = time.monotonic()
    with ThreadPoolExecutor(5) as pool:
        refused = [pool.submit(oversized, i) for i in range(12)]
        response = client.post("/ask", json={"question": "Total retail revenue for June 2025"},
                               headers=client.as_("analyst_enterprise"))
        assert response.status_code == 200, response.text
        assert response.json()["behaviour"] == "figure"
        assert all(f.result() in {422, 429} for f in refused)
    assert time.monotonic() - started < 3


@pytest.mark.parametrize("path", ["/ask", "/data/query", "/search", "/session", "/mcp"])
def test_json_bodies_are_capped_before_route_work(client, path):
    response = client.post(path, content=b"x" * (MAX_REQUEST_BYTES + 1), headers={"content-type": "application/json"})
    assert response.status_code == 413
    assert response.json() == {"detail": BODY_TOO_LARGE}


@pytest.mark.parametrize("path", ["/answer", "/context", "/ask"])
def test_answering_routes_refuse_expired_work_with_plain_503(client, monkeypatch, path):
    import kge.app as appmod
    monkeypatch.setenv("MODEL_REQUEST_TIMEOUT", ".02")
    observed = []
    original = appmod.engine().g.may

    def slow(*args):
        observed.append(request_remaining())
        time.sleep(.04)
        return original(*args)

    monkeypatch.setattr(appmod.engine().g, "may", slow)
    response = client.post(path, json={"question": "Total retail revenue for June 2025"}, headers=client.as_("analyst_retail"))
    assert observed and all(value is not None for value in observed)
    assert response.status_code == 503, response.text
    assert response.json() == {"detail": str(RequestDeadlineExceeded())}


def test_every_registered_route_is_inside_the_same_budget(client, monkeypatch):
    """A new route cannot omit the scope: probe inside every route after all request middleware."""
    import kge.app as appmod
    from starlette.responses import JSONResponse
    assert appmod.app.user_middleware[0].cls is RequestLimitsMiddleware
    seen = []

    async def probe(scope, receive, send):
        seen.append((scope["path"], request_remaining()))
        await JSONResponse({"bounded": request_remaining() is not None})(scope, receive, send)

    for route in appmod.app.routes:
        if not hasattr(route, "methods"):
            continue
        monkeypatch.setattr(route, "app", probe)
        path = route.path.replace("{term:path}", "probe").replace("{run_id}", "probe")
        for method in route.methods:
            response = client.request(method, path)
            assert response.status_code == 200, (method, path, response.text)
    assert len(seen) >= len([r for r in appmod.app.routes if isinstance(r, APIRoute)])
    assert all(0 < remaining <= 270 for _, remaining in seen)
    assert request_remaining() is None


@pytest.mark.parametrize("k", [-5, 0, 51, 1_000_000])
def test_retrieve_bounds_on_http_and_mcp(client, k):
    body = {"query": "invented", "k": k}
    response = client.post("/retrieve", json=body, headers=client.as_("analyst_retail"))
    assert response.status_code == 422
    response = mcp_wire(client, "/mcp", "analyst_retail", "retrieve", body)
    assert response["isError"] and response["content"][0]["text"].startswith("422:")


@pytest.mark.parametrize("k", [1, 50])
def test_retrieve_inclusive_bounds_remain_usable(client, k):
    body = {"query": "invented", "k": k}
    response = client.post("/retrieve", json=body, headers=client.as_("analyst_retail"))
    assert response.status_code == 200, response.text
    response = mcp_wire(client, "/mcp", "analyst_retail", "retrieve", body)
    assert not response.get("isError")


def test_unreadable_map_contexts_are_withheld_on_both_transports(client):
    def maps(who):
        http = client.get("/map/Net New Assets", headers=client.as_(who)).json()
        mcp = mcp_wire(client, "/mcp", who, "map_term", {"term": "Net New Assets"})
        return [http, mcp["structuredContent"]]
    for response in maps("analyst_retail"):
        assert response["contexts"] == []
        assert "wealth.TRAN_FACT_1" not in json.dumps(response)
    for response in maps("advisor_wealth"):
        assert response["contexts"]
        assert all(c["unit"] == "wealth" for c in response["contexts"])


def test_question_schema_matches_mcp_limits(client):
    import kge.app as appmod
    assert appmod.app.openapi()["components"]["schemas"]["Ask"]["properties"]["question"]["maxLength"] == MAX_QUESTION_CHARS
    registered = appmod.answer_mcp._tool_manager.get_tool("ask")
    assert registered.parameters["properties"]["question"]["maxLength"] == MAX_QUESTION_CHARS


def test_session_validation_never_echoes_password(client):
    typed = "invented-secret-" * 30
    response = client.post("/session", json={"username": "invented", "password": typed})
    assert response.status_code == 422
    assert typed not in response.text
    assert all(set(error) == {"loc", "msg"} for error in response.json()["detail"])


@pytest.mark.parametrize("path,tool,body,who", [
    ("/ask", "ask", {"question": "\ud800"}, "analyst_retail"),
    ("/ask", "ask", {"question": "invented", "stages": {"resolution": "\ud800"}}, "analyst_retail"),
    ("/search", "search", {"query": "\ud800"}, "analyst_retail"),
    ("/onboard/source", "onboard_source", {"unit": "invented", "label": "Invented", "ddl": "\ud800"}, "knowledge_engineer"),
    ("/onboard/source", "onboard_source", {"unit": "invented", "label": "\ud800", "ddl": "invented"}, "knowledge_engineer"),
    ("/onboard/source", "onboard_source", {"unit": "invented", "label": "Invented", "ddl": "invented", "csv": {"invented": "\ud800"}}, "knowledge_engineer"),
])
def test_surrogate_is_plain_422_on_both_transports(client, path, tool, body, who):
    response = client.post(path, content=json.dumps(body), headers={**client.as_(who), "content-type": "application/json"})
    assert response.status_code == 422, response.text
    detail = response.json()["detail"]
    assert all(set(error) == {"loc", "msg"} for error in detail)
    mount = {"/ask": "/mcp/answer", "/search": "/mcp/search", "/onboard/source": "/mcp/onboard"}[path]
    wire = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": tool, "arguments": body}}
    response = client.post(mount, content=json.dumps(wire), headers={**client.as_(who), "content-type": "application/json",
                           "accept": "application/json, text/event-stream"}).json()["result"]
    assert response["isError"]
    assert response["content"][0]["text"] == f"422: {detail}"


@pytest.mark.parametrize("path,mount,tool,field,base", [
    ("/retrieve", "/mcp", "retrieve", "query", {}),
    ("/retrieve", "/mcp", "retrieve", "purpose", {"query": "invented"}),
    ("/ask", "/mcp/answer", "ask", "purpose", {"question": "invented"}),
    ("/context", "/mcp", "get_context", "purpose", {"question": "invented"}),
])
def test_query_and_purpose_limits_match_on_both_transports(client, path, mount, tool, field, base):
    body = {**base, field: "界" * 2001}
    response = client.post(path, json=body, headers=client.as_("analyst_retail"))
    assert response.status_code == 422
    detail = response.json()["detail"]
    result = mcp_wire(client, mount, "analyst_retail", tool, body)
    assert result["isError"]
    assert result["content"][0]["text"] == f"422: {detail}"


@pytest.mark.parametrize("failure", ["success", "error", "timeout"])
def test_http_and_mcp_share_person_slots_and_release_every_exit(client, monkeypatch, failure):
    from threading import Event, Lock
    import kge.app as appmod
    from kge.services import http
    from fastapi import HTTPException

    entered = Event()
    release = Event()
    lock = Lock()
    active = []
    original = appmod.engine().g.retrieve

    def held(*args, **kwargs):
        who = args[1]
        if who != "analyst_retail":
            return original(*args, **kwargs)
        with lock:
            active.append(True)
            if len(active) == http.MAX_IN_FLIGHT:
                entered.set()
        assert release.wait(5)
        if failure == "error":
            raise HTTPException(400, "Invented failure.")
        if failure == "timeout":
            raise RequestDeadlineExceeded()
        return {"exact": [], "index_returned": 0, "post_filter_lost": 0}

    monkeypatch.setattr(appmod.engine().g, "retrieve", held)
    body = {"query": "invented"}

    def invoke(i):
        if i % 2:
            return mcp_wire(client, "/mcp", "analyst_retail", "retrieve", body)
        return client.post("/retrieve", json=body, headers=client.as_("analyst_retail"))

    with ThreadPoolExecutor(http.MAX_IN_FLIGHT) as pool:
        futures = [pool.submit(invoke, i) for i in range(http.MAX_IN_FLIGHT)]
        try:
            assert entered.wait(5), active
            with ThreadPoolExecutor(8) as readers:
                reads = [readers.submit(client.get, path, headers=client.as_("analyst_retail"))
                         for path in ["/whoami", "/readable-units"] * 4]
                assert [f.result().status_code for f in reads] == [200] * 8
            for path in ["/", "/mode", "/principals", "/healthz"]:
                assert client.get(path, headers=client.as_("analyst_retail")).status_code == 200
            assert client.get("/onboard", headers=client.as_("analyst_retail")).status_code == 403
            assert client.delete("/session", headers=client.as_("analyst_retail")).status_code == 200
            identity = mcp_wire(client, "/mcp/identity", "analyst_retail", "readable_units", {})
            assert not identity.get("isError"), identity
            response = client.post("/retrieve", json=body, headers=client.as_("analyst_retail"))
            assert response.status_code == 429
            assert int(response.headers["retry-after"]) > 0
            assert response.json() == {"detail": http.TOO_MANY_REQUESTS}
            result = mcp_wire(client, "/mcp", "analyst_retail", "retrieve", body)
            assert result["content"][0]["text"] == f"429: {http.TOO_MANY_REQUESTS}"
            assert client.post("/retrieve", json=body, headers=client.as_("analyst_enterprise")).status_code == 200
        finally:
            release.set()
        results = [f.result() for f in futures]
    assert not http._IN_FLIGHT
    for i, result in enumerate(results):
        if i % 2:
            assert bool(result.get("isError")) == (failure != "success")
        else:
            assert result.status_code == {"success": 200, "error": 400, "timeout": 503}[failure]
    monkeypatch.setattr(appmod.engine().g, "retrieve", original)
    assert client.post("/retrieve", json=body, headers=client.as_("analyst_retail")).status_code == 200
    assert not http._IN_FLIGHT


def test_late_session_returns_its_cookie_and_the_session_is_usable(client, monkeypatch):
    from kge.services.identity import routes
    original = routes.signed_in

    def slow(*args):
        result = original(*args)
        time.sleep(.25)
        return result

    monkeypatch.setattr(routes, "signed_in", slow)
    with monkeypatch.context() as budget:
        budget.setenv("MODEL_REQUEST_TIMEOUT", ".2")
        token = client.as_("analyst_retail")["authorization"].removeprefix("Bearer ")
        response = client.post("/session", json={"token": token})
    try:
        assert response.status_code == 200, response.text
        assert "set-cookie" in response.headers
        assert client.get("/whoami").json()["id"] == "analyst_retail"
    finally:
        client.cookies.clear()


@pytest.mark.parametrize("depth", [400, 600, 1000, 100000])
@pytest.mark.parametrize("path", ["/ask", "/mcp/answer"])
@pytest.mark.parametrize("authenticated", [False, True])
def test_deep_json_is_plain_422(client, depth, path, authenticated):
    body = '{"question":' + '[' * depth + '0' + ']' * depth + '}'
    headers = client.as_("analyst_retail") if authenticated else {}
    response = client.post(path, content=body, headers={**headers, "content-type": "application/json"})
    assert response.status_code == 422, response.text
    assert "detail" in response.json()
    assert "Traceback" not in response.text


def test_label_validator_keeps_safe_reason_on_both_transports(client):
    body = {"unit": "invented", "label": "Invented\nlabel", "ddl": "invented"}
    response = client.post("/onboard/source", json=body, headers=client.as_("knowledge_engineer"))
    assert response.status_code == 422
    detail = response.json()["detail"]
    assert detail == [{"loc": ["body", "label"], "msg": "Value error, label must be one line without control characters"}]
    result = mcp_wire(client, "/mcp/onboard", "knowledge_engineer", "onboard_source", body)
    assert result["content"][0]["text"] == f"422: {detail}"
    assert body["label"] not in response.text


@pytest.mark.parametrize("path", ["/ask", "/answer"])
def test_invalid_arm_never_echoes_submitted_value(client, path):
    body = {"question": "invented", "arm": "invented-private-arm"}
    response = client.post(path, json=body, headers=client.as_("analyst_retail"))
    assert response.status_code == 400
    assert body["arm"] not in response.text
    assert "arm must be one of" in response.json()["error"]
    if path == "/ask":
        result = mcp_wire(client, "/mcp/answer", "analyst_retail", "ask", body)
        assert result["content"][0]["text"].endswith(f"400: {response.json()['error']}")
        assert body["arm"] not in result["content"][0]["text"]
