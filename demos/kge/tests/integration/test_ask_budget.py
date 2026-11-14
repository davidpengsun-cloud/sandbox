"""A real /ask and its MCP twin time out across multiple sleeping offline model calls."""

import json
import os
import time
from threading import Event
from types import SimpleNamespace

import pytest

from kge.adapters.model import LLM
from kge.adapters.model.errors import RequestDeadlineExceeded
from kge.adapters.model.providers import result
from tests.integration.test_answer_text import client as client, mcp_wire
from tests.integration.test_combined import Q, Stub


pytestmark = pytest.mark.xdist_group("ask_budget")


@pytest.fixture
def budget_scale():
    """Scale offline delays, request budgets and observation windows together under lane load."""
    return float(os.environ.get("TEST_ASK_BUDGET_SCALE", "1"))


@pytest.mark.parametrize("transport", ["http", "mcp"])
def test_multiple_model_calls_share_the_ask_budget(client, monkeypatch, transport, budget_scale):
    """A malformed plan and its schema retry share one budget, independently of later graph reads and wording."""
    import kge.app as appmod
    model = LLM("mock", "mock")
    monkeypatch.setattr(appmod.combined(), "llm", model)
    prompts = []
    finished = Event()
    replay = Stub(appmod.engine())

    def generate(self, messages, **kwargs):
        prompts.append(messages[0].content)
        attempt = len(prompts)
        time.sleep(budget_scale)
        try:
            text = {'kind': 'propose'} if attempt % 2 else replay.json(messages[0].content)
            return result(json.dumps(text), "mock", 1, 1)
        finally:
            if attempt == 4:
                finished.set()

    monkeypatch.setattr(type(model.chat), "_generate", generate)
    body = {"question": Q, "stages": {"renderer": "template"}}
    monkeypatch.setenv("MODEL_REQUEST_TIMEOUT", str(4 * budget_scale))
    response = client.post("/ask", json=body, headers=client.as_("analyst_retail.meridian@example.com"))
    assert response.status_code == 200, response.text
    assert response.json()["behaviour"] == "combined"
    assert len(prompts) == 2
    assert not json.loads(prompts[0].split('\n', 1)[1])['errors']
    assert json.loads(prompts[1].split('\n', 1)[1])['errors']

    monkeypatch.setenv("MODEL_REQUEST_TIMEOUT", str(1.6 * budget_scale))
    detail = str(RequestDeadlineExceeded())
    started = time.monotonic()
    try:
        if transport == "http":
            response = client.post("/ask", json=body, headers=client.as_("analyst_retail.meridian@example.com"))
            assert response.status_code == 503, response.text
            assert response.json() == {"detail": detail}
        else:
            response = mcp_wire(client, "/mcp/answer", "analyst_retail.meridian@example.com", "ask", body)
            assert response["isError"] is True
            assert response["content"][0]["text"].removeprefix("Error executing tool ask: ") == f"503: {detail}"
            assert "structuredContent" not in response
        assert 1.4 * budget_scale < time.monotonic() - started < 2.3 * budget_scale
        assert len(prompts) == 4
    finally:
        assert finished.wait(3 * budget_scale), prompts


def test_ask_refuses_before_starting_a_call_below_the_floor(client, monkeypatch):
    import kge.app as appmod
    monkeypatch.setenv("MODEL_REQUEST_TIMEOUT", ".025")
    model = LLM("mock", "mock")
    monkeypatch.setattr(appmod.combined(), "llm", model)
    monkeypatch.setattr(type(model.chat), "_generate", lambda *a, **kw: pytest.fail("model started below floor"))
    response = client.post("/ask", json={"question": Q}, headers=client.as_("analyst_retail.meridian@example.com"))
    assert response.status_code == 503
    assert response.json() == {"detail": str(RequestDeadlineExceeded())}


@pytest.mark.parametrize("transport", ["http", "mcp"])
@pytest.mark.parametrize("backend", ["graph", "warehouse"])
def test_query_exhaustion_has_the_same_plain_503(client, monkeypatch, transport, backend, budget_scale):
    """Only the inline BigQuery stand-in advances the clock, independent of runner speed and I/O scheduling."""
    import kge.app as appmod
    from kge.adapters import deadline
    from kge.adapters.deadline import request_remaining
    from kge.adapters.graphstore.bigquery import Client
    from tests.unit.test_bigquery import JsonResponse
    from tests.unit.test_data_limits import bq

    combined = appmod.combined()
    monkeypatch.setenv("MODEL_REQUEST_TIMEOUT", str(.4 * budget_scale))
    clock = [0.0]
    monkeypatch.setattr(deadline, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    monkeypatch.setattr(deadline, "Thread", lambda target, args, **kwargs: SimpleNamespace(start=lambda: target(*args)))
    warehouse, calls = bq(monkeypatch, [])
    graph = Client("fixture")
    monkeypatch.setattr(graph, "_token", lambda: "offline")

    def send(request, timeout):
        calls.append((request.full_url, json.loads(request.data) if request.data else None, timeout))
        if backend == "graph" and request.data:
            return JsonResponse({"jobComplete": False, "jobReference": {"jobId": "slow"}})
        clock[0] += .8 * budget_scale
        return JsonResponse({"jobComplete": True})

    monkeypatch.setattr("kge.adapters.http.open_http", send)
    if backend == "graph":
        original = combined.g.q

        def query(*args, **kwargs):
            if request_remaining() is None:
                return original(*args, **kwargs)
            return graph.query("SELECT 1", [])

        monkeypatch.setattr(combined.g, "q", query)
    else:
        monkeypatch.setattr("kge.services.ask.engine.planner.execute", lambda *a, **kw: warehouse.row("retail", "SELECT 1"))
    detail = str(RequestDeadlineExceeded())
    if transport == "http":
        response = client.post("/ask", json={"question": Q}, headers=client.as_("analyst_retail.meridian@example.com"))
        assert response.status_code == 503, response.text
        assert response.json() == {"detail": detail}
    else:
        response = mcp_wire(client, "/mcp/answer", "analyst_retail.meridian@example.com", "ask", {"question": Q})
        assert response["isError"] is True
        assert response["content"][0]["text"].removeprefix("Error executing tool ask: ") == f"503: {detail}"
        assert "structuredContent" not in response
    assert len(calls) == (2 if backend == "graph" else 1)
    assert sum(call[1] is not None for call in calls) == 1
    assert 0 < int(calls[0][1]["jobTimeoutMs"]) <= 400 * budget_scale
    assert all(0 < call[2] <= .4 * budget_scale for call in calls)


def test_figure_query_cannot_swallow_a_deadline_before_the_clock_expires(client, monkeypatch):
    monkeypatch.setenv("MODEL_REQUEST_TIMEOUT", "10")

    def expired(*args, **kwargs):
        raise RequestDeadlineExceeded()

    monkeypatch.setattr("kge.services.ask.engine.planner.execute", expired)
    response = client.post("/ask", json={"question": "Total retail revenue for June 2025"}, headers=client.as_("analyst_retail.meridian@example.com"))
    assert response.status_code == 503, response.text
    assert response.json() == {"detail": str(RequestDeadlineExceeded())}


def test_multiple_model_calls_share_the_agent_budget(client, monkeypatch, budget_scale):
    """The Agent's malformed proposal and its schema retry share one deadline; /agent has no MCP twin."""
    import kge.app as appmod
    from kge.services.ask.engine.agent import Agent

    model = LLM("mock", "mock")
    monkeypatch.setattr(appmod, "_agent", Agent(appmod.engine(), llm=model))
    replay = Stub(appmod.engine(), question="Total retail revenue for June 2025")
    prompts = []
    finished = Event()

    def sleeping_generate(self, messages, **kwargs):
        prompts.append(messages[0].content)
        time.sleep(budget_scale)
        try:
            reply = {'kind': 'propose'} if len(prompts) % 2 else replay.json(messages[0].content)
            return result(json.dumps(reply), 'mock', 1, 1)
        finally:
            if len(prompts) == 4:
                finished.set()

    monkeypatch.setattr(type(model.chat), "_generate", sleeping_generate)
    monkeypatch.setenv("MODEL_REQUEST_TIMEOUT", str(4 * budget_scale))
    body = {"question": "Total retail revenue for June 2025"}
    response = client.post("/agent", json=body, headers=client.as_("analyst_retail.meridian@example.com"))
    assert response.status_code == 200, response.text
    assert response.json()["behaviour"] == "figure"
    assert len(prompts) == 2

    monkeypatch.setenv("MODEL_REQUEST_TIMEOUT", str(1.6 * budget_scale))
    started = time.monotonic()
    try:
        response = client.post("/agent", json=body, headers=client.as_("analyst_retail.meridian@example.com"))
        assert response.status_code == 503, response.text
        assert response.json() == {"detail": str(RequestDeadlineExceeded())}
        assert 1.4 * budget_scale < time.monotonic() - started < 2.3 * budget_scale
        assert len(prompts) == 4
    finally:
        assert finished.wait(3 * budget_scale), prompts
