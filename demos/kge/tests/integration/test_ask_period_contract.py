"""Context periods retain their refusal contract across the answering surfaces."""
import pytest

from tests.integration.test_answer_text import client as client, mcp_wire


@pytest.mark.parametrize("surface", ["/ask", "/answer", "/agent", "/context", "ask", "get_context"])
@pytest.mark.parametrize("period", ["FY26 Q1", "Q1 FY26", "the first quarter of FY26", "June", "March", "Jun-25"])
def test_context_unreadable_periods_keep_the_existing_refusal(client, monkeypatch, surface, period):
    import kge.app as appmod

    engine = appmod.engine()
    monkeypatch.setattr(engine.w, "gate", lambda *a, **kw: pytest.fail("unreadable period planned SQL"))
    monkeypatch.setattr(engine, "run_plan", lambda *a, **kw: pytest.fail("unreadable period executed a plan"))
    body = {"question": f"NNA for {period}"}
    if surface.startswith("/"):
        response = client.post(surface, json=body, headers=client.as_("advisor_wealth.meridian@example.com"))
        assert response.status_code == 200, response.text
        answer = response.json()
    else:
        result = mcp_wire(client, "/mcp" if surface == "get_context" else "/mcp/answer", "advisor_wealth.meridian@example.com", surface, body)
        assert not result.get("isError"), result
        answer = result["structuredContent"]
    if surface in {"/context", "get_context"}:
        assert not answer["sources"] and not answer.get("plan")
        assert answer["block"] == answer["decided"]["reason"]
        answer = answer["decided"]
    assert answer["behaviour"] == "abstain_with_reason", answer
    assert all(form in answer["reason"] for form in ("June 2025", "31 Mar 2025", "Q2 2025", "FY26-Q1", "in 2025"))
    assert not answer.get("figure") and not answer.get("figures") and not answer.get("sql")


@pytest.mark.parametrize("period", ["June", "31 June 2025"])
def test_model_agent_preserves_public_period_decisions_without_warehouse_execution(eng, monkeypatch, period):
    from kge.services.ask.engine.agent import Agent
    from kge.services.ask.engine.combined import Combined
    from tests.integration.test_ask_model_period import PeriodStub
    model = PeriodStub(eng, 'rules')
    agent = Agent(eng, llm=model)
    public_model = PeriodStub(eng, 'rules')
    monkeypatch.setattr(eng.w, 'cursor', lambda *a, **kw: pytest.fail('unreadable period read a figure'))
    question = f'NNA for {period}'
    answer = agent.ask(question, 'advisor_wealth')
    public = Combined(eng, llm=public_model).ask(question, 'advisor_wealth')
    assert answer['behaviour'] == public['behaviour']
    assert answer['reason'] == public['reason']
    assert not answer.get('result') and not answer.get('sql')
    assert len(model.prompts) == len(public_model.prompts)
    if period == '31 June 2025':
        assert model.prompts == []
