"""A real /ask and its MCP twin time out across multiple sleeping offline model calls."""
import json
import time
from threading import Event

import pytest

from kge.adapters.model import LLM
from kge.adapters.model.errors import RequestDeadlineExceeded
from kge.adapters.model.providers import result
from tests.integration.test_answer_text import client as client, mcp_wire
from tests.integration.test_combined import Q


@pytest.mark.parametrize("transport", ["http", "mcp"])
def test_multiple_model_calls_share_the_ask_budget(client, monkeypatch, transport):
    """The same two sleeping calls answer within a roomy budget, which also warms the path, and refuse within a tight one."""
    import kge.app as appmod
    model = LLM("mock", "mock")
    monkeypatch.setattr(appmod.combined(), "llm", model)
    prompts = []
    finished = Event()

    def generate(self, messages, **kwargs):
        prompts.append(messages[0].content)
        time.sleep(1)
        if len(prompts) == 4:
            finished.set()
        text = {"terms": []} if messages[0].content.startswith("Glossary terms") else {"answer": ""}
        return result(json.dumps(text), "mock", 1, 1)

    monkeypatch.setattr(type(model.chat), "_generate", generate)
    monkeypatch.setenv("MODEL_REQUEST_TIMEOUT", "4")
    response = client.post("/ask", json={"question": Q}, headers=client.as_("analyst_retail"))
    assert response.status_code == 200, response.text
    assert response.json()["behaviour"] == "combined"
    assert len(prompts) == 2

    monkeypatch.setenv("MODEL_REQUEST_TIMEOUT", "1.6")
    detail = str(RequestDeadlineExceeded())
    started = time.monotonic()
    try:
        if transport == "http":
            response = client.post("/ask", json={"question": Q}, headers=client.as_("analyst_retail"))
            assert response.status_code == 503, response.text
            assert response.json() == {"detail": detail}
        else:
            response = mcp_wire(client, "/mcp/answer", "analyst_retail", "ask", {"question": Q})
            assert response["isError"] is True
            assert response["content"][0]["text"].removeprefix("Error executing tool ask: ") == f"503: {detail}"
            assert "structuredContent" not in response
        assert 1.4 < time.monotonic() - started < 2.3
        assert len(prompts) == 4
    finally:
        assert finished.wait(3), prompts


def test_ask_refuses_before_starting_a_call_below_the_floor(client, monkeypatch):
    import kge.app as appmod
    monkeypatch.setenv("MODEL_REQUEST_TIMEOUT", ".025")
    model = LLM("mock", "mock")
    monkeypatch.setattr(appmod.combined(), "llm", model)
    monkeypatch.setattr(type(model.chat), "_generate", lambda *a, **kw: pytest.fail("model started below floor"))
    response = client.post("/ask", json={"question": Q}, headers=client.as_("analyst_retail"))
    assert response.status_code == 503
    assert response.json() == {"detail": str(RequestDeadlineExceeded())}


@pytest.mark.parametrize("transport", ["http", "mcp"])
@pytest.mark.parametrize("backend", ["graph", "warehouse"])
def test_query_exhaustion_has_the_same_plain_503(client, monkeypatch, transport, backend):
    """A real answering service reaches the BigQuery adapter; its REST stand-in is slower than the request."""
    import kge.app as appmod
    from kge.adapters.deadline import request_remaining
    from kge.adapters.graphstore.bigquery import Client
    from tests.unit.test_bigquery import JsonResponse
    from tests.unit.test_data_limits import bq

    combined = appmod.combined()
    monkeypatch.setenv("MODEL_REQUEST_TIMEOUT", ".4")
    warehouse, calls = bq(monkeypatch, [])
    graph = Client("fixture")
    monkeypatch.setattr(graph, "_token", lambda: "offline")
    finished = Event()

    def send(request, timeout):
        calls.append((request.full_url, json.loads(request.data) if request.data else None, timeout))
        if backend == "graph" and request.data:
            return JsonResponse({"jobComplete": False, "jobReference": {"jobId": "slow"}})
        time.sleep(.8)
        finished.set()
        return JsonResponse({"jobComplete": True})

    monkeypatch.setattr("urllib.request.urlopen", send)
    if backend == "graph":
        original = combined.g.q

        def query(*args, **kwargs):
            if request_remaining() is None:
                return original(*args, **kwargs)
            return graph.query("SELECT 1", [])

        monkeypatch.setattr(combined.g, "q", query)
    else:
        monkeypatch.setattr(combined.e.w, "row", lambda *a, **kw: warehouse.row("retail", "SELECT 1"))
    started = time.monotonic()
    detail = str(RequestDeadlineExceeded())
    try:
        if transport == "http":
            response = client.post("/ask", json={"question": Q}, headers=client.as_("analyst_retail"))
            assert response.status_code == 503, response.text
            assert response.json() == {"detail": detail}
        else:
            response = mcp_wire(client, "/mcp/answer", "analyst_retail", "ask", {"question": Q})
            assert response["isError"] is True
            assert response["content"][0]["text"].removeprefix("Error executing tool ask: ") == f"503: {detail}"
            assert "structuredContent" not in response
        assert .3 < time.monotonic() - started < .7
        assert 0 < int(calls[0][1]["jobTimeoutMs"]) <= 400
        assert all(0 < call[2] <= .4 for call in calls)
    finally:
        assert finished.wait(2), calls


def test_figure_query_cannot_swallow_a_deadline_before_the_clock_expires(client, monkeypatch):
    import kge.app as appmod
    monkeypatch.setenv("MODEL_REQUEST_TIMEOUT", "10")

    def expired(*args, **kwargs):
        raise RequestDeadlineExceeded()

    monkeypatch.setattr(appmod.combined().e.w, "run", expired)
    response = client.post("/ask", json={"question": "Total retail revenue for June 2025"}, headers=client.as_("analyst_retail"))
    assert response.status_code == 503, response.text
    assert response.json() == {"detail": str(RequestDeadlineExceeded())}


def test_multiple_model_calls_share_the_agent_budget(client, monkeypatch):
    """The agent's planning and phrasing calls share one deadline; /agent has no MCP twin."""
    import kge.app as appmod
    from kge.services.ask.engine.agent import Agent

    model = LLM("mock", "mock")
    monkeypatch.setattr(appmod, "_agent", Agent(appmod.engine(), llm=model))
    generate = type(model.chat)._generate
    prompts = []
    finished = Event()

    def sleeping_generate(self, messages, **kwargs):
        prompts.append(messages[0].content)
        time.sleep(1)
        try:
            return generate(self, messages, **kwargs)
        finally:
            if len(prompts) == 4:
                finished.set()

    monkeypatch.setattr(type(model.chat), "_generate", sleeping_generate)
    monkeypatch.setenv("MODEL_REQUEST_TIMEOUT", "4")
    body = {"question": "Total retail revenue for June 2025"}
    response = client.post("/agent", json=body, headers=client.as_("analyst_retail"))
    assert response.status_code == 200, response.text
    assert response.json()["behaviour"] == "figure"
    assert len(prompts) == 2

    monkeypatch.setenv("MODEL_REQUEST_TIMEOUT", "1.6")
    started = time.monotonic()
    try:
        response = client.post("/agent", json=body, headers=client.as_("analyst_retail"))
        assert response.status_code == 503, response.text
        assert response.json() == {"detail": str(RequestDeadlineExceeded())}
        assert 1.4 < time.monotonic() - started < 2.3
        assert len(prompts) == 4
    finally:
        assert finished.wait(3), prompts
