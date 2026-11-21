"""Compact views preserve governed outcomes and the planner's continuation contract over HTTP and MCP. A receipt's
nonce and a pre-planner decline's answer id are fixed here, so two asks of one question seal the same receipt exactly
when they hold the same answer id, mode, answerer and plan."""
from types import SimpleNamespace

import pytest

from kge.services.ask import receipt, service
from kge.services.ask.service import response_view
from tests.integration.test_answer_text import client as client, mcp_wire
from tests.integration.test_combined import Q, Stub

WHO = "analyst_retail.meridian@example.com"
ENGINEER = "knowledge_engineer.meridian@example.com"
QUESTIONS = ["Total retail revenue for June 2025", "What is an active customer?", Q,
             "Combined Retail and SME revenue for June 2025", "Retail revenue for June 2025 sparkly",
             "What are the email addresses of retail customers?"]


@pytest.fixture(autouse=True)
def fixed_receipts(monkeypatch):
    monkeypatch.setattr(receipt, "os", SimpleNamespace(urandom=bytes))
    monkeypatch.setattr(service, "uuid", SimpleNamespace(uuid4=lambda: SimpleNamespace(hex="0" * 32)))


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
    asked = client.post("/ask", json=body, headers=client.as_(WHO))
    asked_compact = client.post("/ask", json={**body, "compact": True}, headers=client.as_(WHO))
    assert asked.status_code == asked_compact.status_code == 200, (asked.text, asked_compact.text)
    assert asked.json() == asked_compact.json()
    assert_no_diagnostics(asked.json())
    assert mcp_wire(client, "/mcp/answer", WHO, "ask", body)["structuredContent"] == asked.json()
    full = client.post("/ask", json=body, headers=client.as_(ENGINEER))
    compact = client.post("/ask", json={**body, "compact": True}, headers=client.as_(ENGINEER))
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
    over_mcp = mcp_wire(client, "/mcp/answer", ENGINEER, "ask", {**body, "compact": True})
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

    monkeypatch.setenv("ASK_ORDER", "model_first")
    model = Stub(appmod.engine(), question=question)
    monkeypatch.setattr(appmod.combined(), "llm", model)
    test_real_compact_ask_is_the_full_governed_answer_projected_once(client, question)
    response = client.post("/ask", json={"question": question, "compact": True}, headers=client.as_(WHO))
    assert response.status_code == 200, response.text
    assert response.json()["versions"]["interpretation"] == f"model:{model.name}"
    assert model.prompts


def test_rules_first_serves_a_full_match_certified_and_leaves_the_mock_model_untouched(client, monkeypatch):
    """The engineer's full reply shows the trace (#505, the role matrix); a compact reply to the same engineer keeps its
    answer id, and the analyst's reply, compact by role, has the same mode, answerer, fallback, model and order under an
    answer id of its own, since the turn id covers the binding's access fingerprint."""
    import kge.app as appmod

    question = "Total retail revenue for June 2025"
    model = Stub(appmod.engine(), question=question)
    monkeypatch.setattr(appmod.combined(), "llm", model)
    response = client.post("/ask", json={"question": question}, headers=client.as_(ENGINEER))
    assert response.status_code == 200, response.text
    out = response.json()
    assert model.prompts == []
    assert (out["answer_mode"], out["answered_by"], out["model"], out["ai_fallback"]) == ("certified", "rules", "none", "on")
    assert out["answer_id"] == out["history"][-1]["turn_id"]
    assert out["answer_text"].startswith("Certified: ")
    assert out["versions"]["interpretation"] == "rules"
    plan = next(step for step in out["trace"] if step["step"] == "plan")
    assert (plan["order"], plan["answer_mode"], plan["answered_by"]) == ("rules_first", "certified", "rules")
    fields = ("answer_mode", "answered_by", "answer_id", "ai_fallback", "model", "order")
    compact = client.post("/ask", json={"question": question, "compact": True}, headers=client.as_(ENGINEER)).json()
    assert_no_diagnostics(compact)
    assert {k: compact[k] for k in fields} == {k: out[k] for k in fields}
    business = client.post("/ask", json={"question": question}, headers=client.as_(WHO)).json()
    assert_no_diagnostics(business)
    assert {k: business[k] for k in fields if k != "answer_id"} == {k: out[k] for k in fields if k != "answer_id"}
    assert business["answer_id"] == business["history"][-1]["turn_id"]
    assert business["history"][-1]["binding"]["access_fingerprint"] != out["history"][-1]["binding"]["access_fingerprint"]
    assert business["answer_text"].startswith("Certified: ")
    assert model.prompts == []
    assert out["order"] == plan["order"]


def test_where_the_rules_do_not_fully_match_the_model_answers_ai_generated_with_its_versions(client, monkeypatch):
    import kge.app as appmod

    model = Stub(appmod.engine(), question="Total retail revenue for June 2025")
    monkeypatch.setattr(appmod.combined(), "llm", model)
    response = client.post("/ask", json={"question": "Retail revenue for June 2025 sparkly"}, headers=client.as_(WHO))
    assert response.status_code == 200, response.text
    out = response.json()
    assert len(model.prompts) == 1 and model.prompts[0].startswith("query-plan-on-v1\n")
    assert (out["answer_mode"], out["answered_by"], out["model"]) == ("ai_generated", "model", model.name)
    assert out["answer_text"].startswith("AI-Generated: ") and out["figure"] is not None
    interpretation = out["versions"]["interpretation"]
    assert interpretation.startswith(f"model:{model.name} prompt:query-plan-on-v1/") and " schema:" in interpretation


def test_with_the_fallback_off_no_model_is_asked_and_a_rules_decline_is_not_sure_with_no_label(client, monkeypatch):
    import kge.app as appmod

    monkeypatch.setenv("ASK_AI_FALLBACK", "off")
    model = Stub(appmod.engine(), question="Total retail revenue for June 2025")
    monkeypatch.setattr(appmod.combined(), "llm", model)
    out = client.post("/ask", json={"question": "Retail revenue for June 2025 sparkly"}, headers=client.as_(WHO)).json()
    assert model.prompts == []
    assert (out["answer_mode"], out["ai_fallback"], out["model"]) == ("not_sure", "off", "none")
    assert out["behaviour"] in ("abstain", "abstain_with_reason", "refuse", "clarify")
    assert not out["answer_text"].startswith(("Certified: ", "AI-Generated: "))
