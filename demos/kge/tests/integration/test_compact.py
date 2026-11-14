"""Compact views preserve governed outcomes and the planner's continuation contract over HTTP and MCP."""
import pytest

from kge.services.ask.service import response_view
from tests.integration.test_answer_text import client as client, mcp_wire
from tests.integration.test_combined import Q, Stub

WHO = "analyst_retail.meridian@example.com"
QUESTIONS = ["Total retail revenue for June 2025", "What is an active customer?", Q,
             "Combined Retail and SME revenue for June 2025", "Retail revenue for June 2025 sparkly",
             "What are the email addresses of retail customers?"]


def assert_no_diagnostics(value, history=False):
    if isinstance(value, dict):
        assert not {"sql", "sql_run", "run_trace", "trace", "plan_vocabulary", "candidates"} & value.keys()
        if not history:
            assert "plan" not in value
        for key, child in value.items():
            assert_no_diagnostics(child, history or key == "history")
    elif isinstance(value, list):
        for child in value:
            assert_no_diagnostics(child, history)


@pytest.mark.parametrize("question", QUESTIONS)
def test_real_compact_ask_is_the_full_governed_answer_projected_once(client, question):
    body = {"question": question}
    full = client.post("/ask", json=body, headers=client.as_(WHO))
    compact = client.post("/ask", json={**body, "compact": True}, headers=client.as_(WHO))
    assert full.status_code == compact.status_code == 200, (full.text, compact.text)
    out = compact.json()
    assert out == response_view(full.json(), True)
    assert_no_diagnostics(out)
    assert out["answer_text"] == full.json()["answer_text"]
    assert out["behaviour"] == full.json()["behaviour"]
    assert out["versions"] == full.json()["versions"]
    assert "run_trace" in full.json()
    if full.json().get("figure") is not None:
        assert "sql" in full.json() and out["figure"] == full.json()["figure"]
    over_mcp = mcp_wire(client, "/mcp/answer", WHO, "ask", {**body, "compact": True})
    assert over_mcp["structuredContent"] == out


@pytest.mark.parametrize("arm", ["control", "treatment"])
@pytest.mark.parametrize("plan_vocabulary", [False, True])
@pytest.mark.parametrize("question", ["What is an active customer?", "NNA for June 2025", "Retail revenue sparkly"])
def test_real_compact_context_preserves_structured_evidence_and_decisions(client, arm, plan_vocabulary, question):
    body = {"question": question, "arm": arm, "plan_vocabulary": plan_vocabulary}
    full = client.post("/context", json=body, headers=client.as_(WHO))
    compact = client.post("/context", json={**body, "compact": True}, headers=client.as_(WHO))
    assert full.status_code == compact.status_code == 200, (full.text, compact.text)
    out = compact.json()
    assert out == response_view(full.json(), True)
    assert_no_diagnostics(out)
    assert "block" in full.json()
    assert "\n    from " not in out["block"] and "\n    where " not in out["block"]
    assert "Not glossary terms:" not in out["block"] and "Sources, by number" not in out["block"]
    if arm == "treatment" and not plan_vocabulary and question == "What is an active customer?":
        assert "Terms:" in out["block"] and "Active Customer" in out["block"]
    if plan_vocabulary:
        import json
        original = full.json()["plan_vocabulary"]["contexts"]
        expected = [{k: v for k, v in context.items() if k in ("id", "label", "definition")} for context in original]
        assert json.loads(out["block"]) == {"contexts": expected}
    for field in ("sources", "certified", "defines", "meta", "units", "needed", "withheld", "in_window"):
        assert out[field] == full.json()[field]
    over_mcp = mcp_wire(client, "/mcp", WHO, "get_context", {**body, "compact": True})
    assert over_mcp["structuredContent"] == out


def test_compact_history_round_trips_for_two_followups(client):
    def ask(question, **kwargs):
        response = client.post("/ask", json={"question": question, "compact": True, **kwargs}, headers=client.as_(WHO))
        assert response.status_code == 200, response.text
        return response.json()

    first = ask("Retail revenue for June 2025")
    second = ask("And for April 2025?", history=first["history"])
    third = ask("And for May 2025?", history=second["history"])
    for index, (out, month) in enumerate(((first, "June"), (second, "April"), (third, "May")), 1):
        assert out["figure"] == ask(f"Retail revenue for {month} 2025")["figure"]
        assert len(out["history"]) == index
        assert out["history"][0] == first["history"][0]
        assert_no_diagnostics(out)
    assert second["history"][-1]["turn_id"] != first["history"][-1]["turn_id"]
    assert third["history"][-1]["plan"] and third["history"][-1]["binding"]


@pytest.mark.parametrize("path,mount,name", [("/ask", "/mcp/answer", "ask"), ("/context", "/mcp", "get_context")])
def test_compact_still_refuses_extra_fields_and_unauthenticated_callers(client, path, mount, name):
    body = {"question": "Revenue?", "compact": True, "unknown": True}
    response = client.post(path, json=body, headers=client.as_(WHO))
    assert response.status_code == 422
    assert any(item["loc"] == ["body", "unknown"] for item in response.json()["detail"])
    over_mcp = mcp_wire(client, mount, WHO, name, body)
    assert over_mcp["isError"] and "structuredContent" not in over_mcp
    response = client.post(path, json={"question": "Revenue?", "compact": True})
    assert response.status_code == 401


@pytest.mark.parametrize("question", ["Total retail revenue for June 2025", Q])
def test_compact_offline_model_answers_keep_runtime_and_certified_content(client, monkeypatch, question):
    import kge.app as appmod

    model = Stub(appmod.engine(), question=question)
    monkeypatch.setattr(appmod.combined(), "llm", model)
    test_real_compact_ask_is_the_full_governed_answer_projected_once(client, question)
    response = client.post("/ask", json={"question": question, "compact": True}, headers=client.as_(WHO))
    assert response.status_code == 200, response.text
    assert response.json()["versions"]["interpretation"] == f"model:{model.name}"
    assert model.prompts
