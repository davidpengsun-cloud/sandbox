"""The reference OpenAPI tool spec matches the live API, and the operator guide names external-agent invokers."""
from __future__ import annotations

import copy
import importlib.util
import json
from types import SimpleNamespace

import anyio
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pathlib import Path

import yaml

from kge.app import app

DEMO = Path(__file__).resolve().parents[2]
PATHS = ("/context", "/ask", "/ask/runs/{run_id}", "/search", "/data/query")
GUIDES = (DEMO / "kit/INSTALL.md", DEMO / "kit/guides/operator.md")
INVOKER_NEEDLES = (
    "roles/run.invoker",
    "principal://agents.global.org-<ORG_ID>.system.id.goog/resources/aiplatform/projects/<PROJECT_NUMBER>/locations/global/agents/<AGENT_ID>",
    "principalSet://",
    "serviceAccount:service-<PROJECT_NUMBER>@gcp-sa-dialogflow.iam.gserviceaccount.com",
    "gcloud beta services identity create --service=dialogflow.googleapis.com",
    "allUsers",
    "allAuthenticatedUsers",
    "X-KGE-Authorization",
    "openapi/kge-tools.openapi.yaml",
)
GATE_NEEDLES = (
    "One statement",
    "SELECT or WITH ... SELECT",
    "nothing that writes",
    "inner joins only",
    "table.column = table.column",
    "SELECT *",
    "declared join",
    "Restricted personal data",
    "dry-runs the query",
    "may not borrow a warehouse table's name",
)
KEPT = ("answer_text", "figures", "citations", "certified_definitions", "figure", "behaviour", "reason",
        "history", "result", "clarification", "versions", "unit_label", "period")
DROPPED = ("sql", "sql_run", "run_trace", "trace", "plan", "plan_vocabulary", "named", "candidates",
           "guard", "vector_hits", "candidate_sources", "queue", "display", "join_path")


def load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


reference = load(DEMO / "kit/reference_openapi.py", "reference_openapi")
playbook = load(DEMO / "kit/scripts/playbook.py", "playbook")


def test_the_shipped_spec_is_the_live_schema():
    live = app.openapi()
    built = reference.reference_spec(live)
    shipped_text = (DEMO / "kit/openapi/kge-tools.openapi.yaml").read_text()
    shipped = yaml.safe_load(shipped_text)
    assert shipped == built
    assert shipped_text == reference.yaml_text(built)
    assert shipped["openapi"] == "3.0.3"
    assert list(shipped["paths"]) == list(PATHS)
    assert shipped["info"]["title"] == live["info"]["title"]
    assert shipped["info"]["version"] == live["info"]["version"]
    assert shipped["servers"] == [{"url": "https://<KGE_SERVICE_URL>"}]
    poll = shipped["paths"]["/ask/runs/{run_id}"]["get"]
    assert poll["responses"]["202"]["content"] == shipped["paths"]["/ask"]["post"]["responses"]["202"]["content"]
    assert {parameter["name"] for parameter in poll["parameters"]} == {"run_id", "after", "X-KGE-Authorization"}
    ask_fields = live["components"]["schemas"]["AskResponse"]["properties"]
    for field in KEPT:
        assert field in ask_fields
    for name in ("Ask", "ContextAsk"):
        compact = shipped["components"]["schemas"][name]["properties"]["compact"]
        assert compact["type"] == "boolean" and compact["default"] is False
        assert "SQL" in compact["description"]
        assert shipped["components"]["schemas"][name]["additionalProperties"] is False
    for path in PATHS:
        method = "get" if path == "/ask/runs/{run_id}" else "post"
        operation = shipped["paths"][path][method]
        live_operation = live["paths"][path][method]
        assert operation["description"] == live_operation["description"]
        header = [item for item in operation["parameters"] if item.get("name") == "X-KGE-Authorization"]
        assert header == [reference.KGE_AUTHORIZATION]
        for ref in reference.schema_names({"request": operation.get("requestBody"), "ok": operation["responses"]["200"]}):
            assert set(shipped["components"]["schemas"][ref]["properties"]) == set(
                live["components"]["schemas"][ref]["properties"])
        assert not reference.schema_names(operation) - set(shipped["components"]["schemas"])
    description = shipped["paths"]["/data/query"]["post"]["description"]
    for needle in GATE_NEEDLES:
        assert needle in description, needle
    lowered = description.lower()
    assert "no subquery" not in lowered and "no cte" not in lowered


def test_the_operator_guide_names_external_agent_invokers():
    for path in GUIDES:
        text = path.read_text()
        for needle in INVOKER_NEEDLES:
            assert needle in text, f"{path.name} lacks {needle}"
    operator = (DEMO / "kit/guides/operator.md").read_text()
    assert "Studio's Google identity token travels in `Authorization`." in operator
    assert "The KGE token travels in `X-KGE-Authorization`." in operator
    steps = playbook.steps(operator)
    assert steps[0]["name"].startswith("1.")
    assert all(step["commands"] and step["expect"] for step in steps)


@pytest.fixture
def responses(monkeypatch):
    from kge.services.ask import routes, service
    from kge.services.ask.engine.core import PRESETS

    figure = {"id": "f1", "label": "Revenue", "term": "Revenue", "kind": "sum", "column": "revenue.amount",
              "currency": "USD", "value": 10, "cite": 1}
    citation = {"n": 1, "doc": "D1", "title": "Revenue definition", "why": "certified definition"}
    definition = {"term": "Revenue", "definition": "Certified revenue.", "documents": ["D1"]}
    payloads = {
        "/ask": {"question": "Revenue?", "principal": "analyst", "purpose": "analysis", "arm": "graph",
                 "behaviour": "figure", "answer_text": "USD 10 [1]", "phrased_by": "template", "trace": [],
                 "versions": {"planner": "query-plan-v1", "interpretation": "rules"}, "run_trace": {"total_ms": 1},
                 "result": {"columns": [], "rows": []}, "history": [], "figure": 10, "sql": "SELECT 10",
                 "certified_definitions": [definition], "citations": [citation], "combined": False,
                 "figures": [figure], "unit_label": "Retail", "period": "June 2025",
                 "plan": {"sql": "SELECT 10"}, "candidates": [{"sql": "SELECT 10"}], "sql_run": ["SELECT 10"],
                 "candidate_sources": ["D1"], "queue": [{"id": "D1", "sql": "SELECT 10"}]},
        "/context": {"arm": "treatment", "block": "Scope: retail.\n\nTerms:\n  Revenue: Certified revenue.\n\nContexts:\n    sql SELECT 10; duplicate candidates",
                     "sources": [citation], "units": ["retail"], "certified": {}, "in_window": [1],
                     "defines": {"retail": {"revenue.amount": ["Revenue"]}}, "meta": {}, "group": None,
                     "named": ["D1"], "withheld": [], "needed": ["retail"], "plan": {"sql": "SELECT 10"},
                     "decided": {"behaviour": "figure", "figure": 10, "answer_text": "USD 10 [1]",
                                 "certified_definitions": [definition], "citations": [citation], "sql": "SELECT 10",
                                 "run_trace": {}, "candidates": [{"sql": "SELECT 10"}]},
                     "plan_vocabulary": None},
    }
    calls = []

    def answer(*args, **kwargs):
        calls.append(("/ask", args, kwargs))
        return copy.deepcopy(payloads["/ask"])

    def context(*args, **kwargs):
        calls.append(("/context", args, kwargs))
        return copy.deepcopy(payloads["/context"])

    served = SimpleNamespace(PROFILE=None, combined=lambda: SimpleNamespace(ask=answer, g=None, e=None),
                             agent=lambda: SimpleNamespace(context=context), as_json=lambda out: out)
    monkeypatch.setattr(routes, "served", served)
    monkeypatch.setattr(routes, "run_config", lambda *args: PRESETS["graph"])
    monkeypatch.setattr(service.runtrace, "run", lambda name, call, **kwargs: call())
    monkeypatch.setattr(service, "log_unmatched", lambda *args: None)
    monkeypatch.setattr(service, "worded", lambda out, engine: out)
    http = FastAPI()
    http.include_router(routes.router)
    for route in routes.router.routes:
        if getattr(route, "path", None) in payloads:
            http.dependency_overrides[route.dependant.dependencies[0].call] = lambda: "analyst"
    return TestClient(http), payloads, calls, served


def assert_compact(out):
    if isinstance(out, dict):
        assert not set(DROPPED) & out.keys(), out
        for key, value in out.items():
            if key != "history":
                assert_compact(value)
    elif isinstance(out, list):
        for value in out:
            assert_compact(value)


@pytest.mark.parametrize("path", ["/ask", "/context"])
def test_compact_is_one_projection_of_the_same_computation(responses, path):
    client, payloads, calls, _ = responses
    response = client.post(path, json={"question": "Revenue?", "compact": True}, params={"display": "1"})
    assert response.status_code == 200, response.text
    out = response.json()
    assert_compact(out)
    original = payloads[path] if path == "/ask" else payloads[path]["decided"]
    kept = out if path == "/ask" else out["decided"]
    for field in ("answer_text", "figure", "certified_definitions", "citations"):
        assert kept[field] == original[field]
    if path == "/ask":
        for field in ("figures", "history", "result", "versions", "unit_label", "period", "behaviour"):
            assert kept[field] == original[field]
    else:
        assert out["block"] == "Terms:\n  Revenue: Certified revenue."
        for field in ("sources", "defines", "certified", "units", "in_window", "needed"):
            assert out[field] == payloads[path][field]
    assert len(calls) == 1 and calls[0][0] == path
    assert "compact" not in calls[0][2], "compact must not alter the engine request"


@pytest.mark.parametrize("path", ["/ask", "/context"])
@pytest.mark.parametrize("compact", [None, False])
def test_default_response_bytes_are_unchanged(responses, path, compact):
    client, payloads, _, _ = responses
    body = {"question": "Revenue?"}
    if compact is not None:
        body["compact"] = compact
    response = client.post(path, json=body)
    assert response.status_code == 200, response.text
    assert response.content == json.dumps(payloads[path], ensure_ascii=False, separators=(",", ":")).encode()
    assert response.json() == payloads[path]
    if path == "/ask":
        assert "sql" in response.json() and "run_trace" in response.json()


@pytest.mark.parametrize("path", ["/ask", "/context"])
def test_compact_does_not_admit_unknown_request_fields(responses, path):
    client, _, calls, _ = responses
    response = client.post(path, json={"question": "Revenue?", "compact": True, "unknown": True})
    assert response.status_code == 422
    assert response.json()["detail"][0]["type"] == "extra_forbidden"
    assert calls == []


@pytest.mark.parametrize("name,path", [("ask", "/ask"), ("get_context", "/context")])
def test_mcp_compact_uses_the_same_view_and_request_contract(responses, monkeypatch, name, path):
    from kge.services.ask import mcp, routes
    from kge.services.ask.models import Ask, ContextAsk

    client, _, calls, served = responses
    monkeypatch.setattr(mcp, "served", served)
    monkeypatch.setattr(mcp, "run_config", routes.run_config)

    async def invoke(ctx, call, check):
        return call("analyst")

    monkeypatch.setattr(mcp, "tool", invoke)
    server = mcp.answer_mcp_server() if name == "ask" else mcp.mcp_server()
    registered = server._tool_manager._tools[name]
    listed = next(item for item in anyio.run(server.list_tools) if item.name == name)
    assert set(listed.inputSchema["properties"]) == set((Ask if name == "ask" else ContextAsk).model_fields)
    assert listed.inputSchema["properties"]["compact"]["default"] is False
    result = anyio.run(lambda: registered.fn(question="Revenue?", ctx=None, compact=True))
    expected = client.post(path, json={"question": "Revenue?", "compact": True}).json()
    assert result == expected
    assert_compact(result)
    assert len(calls) == 2


def test_projection_keeps_refusals_runtime_labels_and_only_one_copy_of_parts():
    from kge.services.ask.service import response_view

    out = {"status": "refused", "behaviour": "blocked_access", "reason": "Read is not permitted.",
           "conversation_id": "conversation", "turn_id": "turn", "runtime": {"model": "mock"},
           "labels": {"f1": "Revenue"}, "clarification": {"choices": [{"id": "c1", "label": "Retail"}]},
           "context": {"id": "nna", "label": "Net new assets", "filters": ["internal"], "sql": "SELECT 1"},
           "parts": [{"value": 10, "sql": "SELECT 10"}], "plan": [{"value": 10, "sql": "SELECT 10"}]}
    compact = response_view(out, True)
    assert set(compact) == set(out) - {"parts", "plan"}
    assert compact["context"] == {"id": "nna", "label": "Net new assets"}
    for field in set(compact) - {"context"}:
        assert compact[field] == out[field]
    assert response_view(out) is out
    assert out["plan"] and out["context"]["filters"], "projection must not mutate computation or logs"
    assert response_view({"parts": {"retail": 10, "sme": 20}}, True) == {"parts": {"retail": 10, "sme": 20}}


@pytest.mark.parametrize("path", ["/ask", "/context"])
def test_compact_adds_zero_bigquery_jobs_per_request(responses, monkeypatch, path):
    from tests.unit.test_data_limits import bq

    client, _, _, served = responses
    replies = [{"jobComplete": True, "schema": {"fields": [{"name": "value", "type": "INTEGER"}]}, "rows": [{"f": [{"v": "10"}]}]}]
    warehouse, jobs = bq(monkeypatch, [])
    if path == "/ask":
        target = served.combined()
        served.combined = lambda: target
        original = target.ask
    else:
        target = served.agent()
        served.agent = lambda: target
        original = target.context

    def computed(*args, **kwargs):
        assert warehouse.row("retail", "SELECT 10") == (10,)
        return original(*args, **kwargs)

    setattr(target, "ask" if path == "/ask" else "context", computed)
    for compact in (False, True):
        warehouse, jobs = bq(monkeypatch, copy.deepcopy(replies))
        response = client.post(path, json={"question": "Revenue?", "compact": compact})
        assert response.status_code == 200, response.text
        assert len(jobs) == 1 and jobs[0][0] == "POST"


def test_compact_does_not_treat_group_values_or_metadata_labels_as_diagnostics():
    from kge.services.ask.service import response_view

    out = {"rows": {"sql": 10, "trace": 20, "plan": 30}, "labels": {"sql": "SQL team"},
           "defines": {"retail": {"plan": ["Plan"]}}, "versions": {"plan": "v1"},
           "figures": [{"id": "sql", "label": "SQL team", "value": 10, "sql": "SELECT 10"}]}
    compact = response_view(out, True)
    for field in ("rows", "labels", "defines", "versions"):
        assert compact[field] == out[field]
    assert compact["figures"] == [{"id": "sql", "label": "SQL team", "value": 10}]


def test_compact_context_keeps_definitions_from_the_planner_contract_without_candidates():
    from kge.services.ask.service import response_view

    vocabulary = {"names": [{"id": "nna", "label": "Net new assets"}],
                  "contexts": [{"id": "nna", "label": "Net new assets", "definition": "Certified flows.",
                                "fact": "wealth.flows", "paths": ["internal"]}]}
    out = {"block": json.dumps(vocabulary), "plan_vocabulary": vocabulary}
    compact = response_view(out, True)
    assert compact == {"block": '{"contexts":[{"id":"nna","label":"Net new assets","definition":"Certified flows."}]}'}


def test_compact_context_keeps_multiline_definitions_without_the_next_query_section():
    from kge.services.ask.service import response_view

    terms = "Terms:\n  Revenue: First paragraph.\n\nContexts affect this second paragraph of the certified definition."
    out = {"block": "Scope: retail.\n\n" + terms + "\n\nContexts (query parts):\n    from retail.revenue"}
    assert response_view(out, True)["block"] == terms
