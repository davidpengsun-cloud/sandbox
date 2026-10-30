"""Adversarial periods and currencies stop every answering surface before any figure is read."""
import pytest

from kge.adapters.model import LLM
from kge.services.ask.engine.agent import Agent
from kge.services.ask.engine.combined import Combined
from tests.integration.test_answer_text import client as client, mcp_wire
from tests.integration.test_ask_scope import COHORT_TERMS, PARAPHRASE
from tests.integration.test_combined import Stub


@pytest.mark.parametrize("question", [
    "How do I open a window in the past?",
    "Is the test due today?",
    "Who is the manager of Manchester United?",
    "What rate does the bus run at?",
    "Who holds the new record for the long jump?",
])
def test_model_cohort_needs_its_own_terms_grounding(eng, monkeypatch, question):
    monkeypatch.setattr(eng.w, "row", lambda *a, **kw: pytest.fail("ungrounded cohort read figures"))
    monkeypatch.setattr(eng.w, "gate", lambda *a, **kw: pytest.fail("ungrounded cohort planned a warehouse query"))
    answer = Combined(eng, llm=Stub(terms=COHORT_TERMS)).ask(question, "analyst_retail")
    assert answer["behaviour"] == "abstain"
    assert answer["trace"][0]["terms"] == []
    assert not answer.get("figures") and not answer.get("sql") and not answer.get("citations")


@pytest.mark.parametrize("source", ["alias", "definition"])
def test_model_cohort_grounds_in_its_own_alias_but_not_definition(eng, monkeypatch, source):
    question = "How many lumens in Retail in June 2025?"
    if source == "alias":
        monkeypatch.setattr(eng, "ALIASES", {"lumen registry": COHORT_TERMS[0]})
    else:
        query = eng.g.q

        def definitions(cypher, params=None):
            if "t.definition AS excerpt" in cypher:
                return [{"excerpt": "Lumen registry"}]
            return query(cypher, params)

        monkeypatch.setattr(eng.g, "q", definitions)
    answer = Combined(eng, llm=Stub(terms=COHORT_TERMS)).ask(question, "analyst_retail")
    if source == "alias":
        assert answer["behaviour"] == "combined"
        assert [f["value"] for f in answer["figures"]] == [176, 3, 6400.62, 42.64]
    else:
        assert answer["behaviour"] == "abstain"
        assert not answer.get("figures")


@pytest.mark.parametrize("model", [False, True])
@pytest.mark.parametrize("surface", ["/ask", "/answer", "/agent", "/context", "ask", "get_context"])
@pytest.mark.parametrize("period,reason,model_allowed", [
    ("as at 31 June 2025", "That date does not exist:", False),
    ("as at 31 Feb 2025", "That date does not exist:", False),
    ("as at 99 Jan 2025", "That date does not exist:", False),
    ("for Junes 2025", "Please give a valid period:", True),
    ("for June 2025s", "Please give a valid period:", True),
    ("for June 20255", "Please give a valid period:", True),
    ("for June2025", "Please give a valid period:", True),
    ("for June 2025.5", "Please give a valid period:", False),
    ("for June 2025 in EUR", "No EUR conversion standard exists", False),
])
def test_bad_period_or_currency_abstains_on_every_surface(client, monkeypatch, model, surface, period, reason, model_allowed):
    import kge.app as appmod

    engine = appmod.engine()
    combined = Combined(engine, llm=Stub(terms=COHORT_TERMS) if model else None)
    agent = Agent(engine, llm=LLM("mock", "mock") if model else None)
    monkeypatch.setattr(appmod, "_combined", combined)
    monkeypatch.setattr(appmod, "_agent", agent)
    monkeypatch.setattr(engine.w, "row", lambda *a, **kw: pytest.fail("invalid request read figures"))
    monkeypatch.setattr(engine.w, "gate", lambda *a, **kw: pytest.fail("invalid request planned a warehouse query"))
    monkeypatch.setattr(agent, "plan", lambda *a, **kw: pytest.fail("invalid request called a model"))
    body = {"question": f"Total retail revenue {period}"}
    if surface.startswith("/"):
        response = client.post(surface, json=body, headers=client.as_("analyst_retail"))
        assert response.status_code == 200, response.text
        answer = response.json()
    else:
        result = mcp_wire(client, "/mcp" if surface == "get_context" else "/mcp/answer", "analyst_retail", surface, body)
        assert not result.get("isError"), result
        answer = result["structuredContent"]
    if surface in ("/context", "get_context"):
        assert reason in answer["block"]
        assert not answer["sources"] and not answer["certified"] and not answer.get("plan")
        answer = answer["decided"]
    model_period = model and surface in {"/ask", "ask"} and model_allowed
    assert answer["behaviour"] == ("abstain_with_reason" if model_period else "abstain"), answer
    assert answer["reason"].startswith(reason)
    assert not answer.get("figures") and not answer.get("sql") and not answer.get("citations")


@pytest.mark.parametrize("space", ["\u00a0", "\u2003", "\u202f", "\t", "  "])
@pytest.mark.parametrize("surface", ["/ask", "/answer", "/agent"])
def test_unicode_whitespace_keeps_the_month_figure(client, space, surface):
    response = client.post(surface, json={"question": f"Total retail revenue for June{space}2025"},
                           headers=client.as_("analyst_retail"))
    assert response.status_code == 200, response.text
    assert response.json()["figure"] == 13287.67


def test_grounded_paraphrase_and_native_currency_keep_their_figures(eng):
    answer = Combined(eng, llm=Stub(terms=COHORT_TERMS)).ask(PARAPHRASE, "analyst_retail")
    assert [f["value"] for f in answer["figures"]] == [176, 3, 6400.62, 42.64]
    answer = eng.answer("Total retail revenue for June 2025 in USD", "analyst_retail")
    assert answer["figure"] == 13287.67


@pytest.mark.parametrize("term", ["Assets Under Management", "Net New Assets", "Client-Directed NNA"])
def test_context_measures_also_reject_another_measures_currency(eng, monkeypatch, term):
    monkeypatch.setattr(eng.w, "row", lambda *a, **kw: pytest.fail("wrong currency queried a warehouse"))
    answer = Combined(eng, llm=None).ask(f"{term} in EUR for June 2025", "advisor_wealth")
    assert answer["behaviour"] == "abstain", answer
    assert answer["reason"].startswith("No EUR conversion standard exists")


def test_an_unrelated_retrieved_passage_cannot_ground_model_terms(eng, monkeypatch):
    monkeypatch.setattr(eng.g, "retrieve", lambda *a, **kw: {"exact": [{"excerpt": "The vellum is nearby."}]})
    monkeypatch.setattr(eng.w, "row", lambda *a, **kw: pytest.fail("unrelated passage admitted a cohort"))
    answer = Combined(eng, llm=Stub(terms=COHORT_TERMS)).ask("Who moved the vellum?", "analyst_retail")
    assert answer["behaviour"] == "abstain"
    assert answer["trace"][0]["terms"] == []


@pytest.mark.parametrize("question,extra", [
    ("Which transaction did the referee initiate in the calendar?", []),
    ("Do logins alone count towards the gym membership?", []),
    ("How do I open a window in the past?", ["Open Account"]),
    ("Is the test due today?", ["Test Account"]),
    ("Who holds the new record for the long jump?", ["New-to-Bank Customer"]),
])
def test_only_retained_cohort_names_and_aliases_ground_model_terms(eng, monkeypatch, question, extra):
    monkeypatch.setattr(eng.w, "row", lambda *a, **kw: pytest.fail("ungrounded cohort read figures"))
    monkeypatch.setattr(eng.w, "gate", lambda *a, **kw: pytest.fail("ungrounded cohort planned a query"))
    answer = Combined(eng, llm=Stub(terms=COHORT_TERMS + extra)).ask(question, "analyst_retail")
    assert answer["behaviour"] == "abstain", answer
    assert answer["trace"][0]["terms"] == []
    assert not answer.get("figures") and not answer.get("sql") and not answer.get("citations")


@pytest.mark.parametrize("period", [
    "June, 2025", "June of 2025", "Jun-2025", "06/2025", "2025-06", "June '25",
    "June\u200b 2025", "Ju\u200bne 2025", "June 20\u200b25", "June ２０２５", "June ٢٠٢٥", "June २०२५",
])
@pytest.mark.parametrize("surface", ["/ask", "/answer", "/agent"])
def test_common_month_forms_return_only_the_month_figure(client, period, surface):
    response = client.post(surface, json={"question": f"Total retail revenue for {period}"}, headers=client.as_("analyst_retail"))
    assert response.status_code == 200, response.text
    answer = response.json()
    assert answer["figure"] == 13287.67, answer
    assert "2025-06-01" in str(answer["trace"])
    assert "Conversion Error" not in response.text


@pytest.mark.parametrize("currency,code", [
    ("in eur", "EUR"), ("in Euros", "EUR"), ("in euro", "EUR"), (", GBP", "GBP"),
    ("in gbp", "GBP"), ("in Japanese yen", "JPY"), ("in jpy", "JPY"), ("in Swiss francs", "CHF"),
])
def test_currency_codes_and_iso_names_require_a_held_currency(eng, monkeypatch, currency, code):
    monkeypatch.setattr(eng.w, "run", lambda *a, **kw: pytest.fail("wrong currency read figures"))
    monkeypatch.setattr(eng.w, "gate", lambda *a, **kw: pytest.fail("wrong currency planned a query"))
    answer = Combined(eng, llm=None).ask(f"Total retail revenue for June 2025 {currency}", "analyst_retail")
    assert answer["behaviour"] == "abstain", answer
    assert answer["reason"].startswith(f"No {code} conversion standard exists")


@pytest.mark.parametrize("currency", ["in usd", "in US dollars", "in United States dollars"])
def test_native_currency_names_keep_the_figure(eng, currency):
    answer = eng.answer(f"Total retail revenue for June 2025 {currency}", "analyst_retail")
    assert answer["figure"] == 13287.67, answer


@pytest.mark.parametrize("surface", ["/ask", "/answer", "/agent"])
def test_warehouse_dry_run_diagnostics_never_reach_an_answer(client, monkeypatch, surface):
    import kge.app as appmod

    private = "Conversion Error: private-table could not convert date"
    monkeypatch.setattr(appmod.engine().w, "gate", lambda *a, **kw: {
        "passed": False, "failed_at": "dry run", "problems": [private], "checks": [],
    })
    response = client.post(surface, json={"question": "Total retail revenue for June ２０２５"},
                           headers=client.as_("analyst_retail"))
    assert response.status_code == 200, response.text
    assert private not in response.text
    assert "The requested data could not be read" in response.text


@pytest.mark.parametrize("path", ["rules", "combined", "context", "agent"])
def test_warehouse_execution_failures_have_a_plain_message(eng, monkeypatch, path):
    from kge.services.ask.engine.errors import WAREHOUSE_PROBLEM

    def fail(*args, **kwargs):
        raise ValueError("Conversion Error: private-table could not convert date")

    if path == "context":
        monkeypatch.setattr(eng, "session", fail)
        guard, rows = eng.run_plan(None, "unused")
        assert guard["problems"] == [WAREHOUSE_PROBLEM] and rows is None
        return
    if path == "agent":
        agent = Agent(eng, llm=LLM("mock", "mock"))
        monkeypatch.setattr(agent, "row", fail)
        answer = agent.ask("Total retail revenue for June ２０２５", "analyst_retail")
    elif path == "rules":
        monkeypatch.setattr(eng.w, "run", fail)
        answer = eng.answer("Total retail revenue for June ２０２５", "analyst_retail")
    else:
        monkeypatch.setattr(eng.w, "row", fail)
        answer = Combined(eng, llm=Stub(terms=COHORT_TERMS)).ask(PARAPHRASE, "analyst_retail")
    assert answer["behaviour"] == "abstain", answer
    assert WAREHOUSE_PROBLEM in answer["reason"]
    assert "Conversion Error" not in str(answer) and "private-table" not in str(answer)


@pytest.mark.parametrize("period", ["June", "June, unknown", "June of 25", "20255", "June 2025 and July"])
@pytest.mark.parametrize("model", [False, True])
def test_unread_period_abstains_before_a_warehouse_read(eng, monkeypatch, period, model):
    combined = Combined(eng, llm=Stub(terms=COHORT_TERMS) if model else None)
    if not model:
        monkeypatch.setattr(combined, "understand", lambda *a, **kw: pytest.fail("offline period reached the model"))
    monkeypatch.setattr(eng.w, "gate", lambda *a, **kw: pytest.fail("unread period reached the warehouse"))
    answer = combined.ask(f"Total retail revenue for {period}", "analyst_retail")
    model_allowed = period != "June 2025 and July"
    assert answer["behaviour"] == ("abstain_with_reason" if model and model_allowed else "abstain")
    if model:
        assert len(combined.llm.prompts) == int(model_allowed)
    assert answer["reason"].startswith("Please give a valid period:")
    assert not answer.get("figure") and not answer.get("figures")


@pytest.mark.parametrize("arm", ["schema", "catalog", "graph"])
def test_unicode_period_warehouse_failures_are_plain_on_every_arm(eng, monkeypatch, arm):
    monkeypatch.setattr(eng.w, "gate", lambda *a, **kw: {
        "passed": False, "failed_at": "dry run", "problems": ["Conversion Error: private-table"], "checks": [],
    })
    answer = eng.answer("Total retail revenue for June ２０２５", "analyst_retail", arm_name=arm)
    assert answer["behaviour"] == "abstain"
    assert "The requested data could not be read" in answer["reason"]
    assert "Conversion Error" not in str(answer) and "private-table" not in str(answer)


@pytest.mark.parametrize("surface", ["/ask", "/answer", "/agent", "ask"])
@pytest.mark.parametrize("day", ["30/06/2025", "30/6/2025", "2025-06-30", "June 30, 2025", "30th June 2025"])
def test_round3_day_dates_keep_the_as_at_stock_figure(client, surface, day):
    body = {"question": f"Number of open retail complaints as at {day}"}
    if surface == "ask":
        result = mcp_wire(client, "/mcp/answer", "analyst_retail", "ask", body)
        assert not result.get("isError"), result
        answer = result["structuredContent"]
    else:
        response = client.post(surface, json=body, headers=client.as_("analyst_retail"))
        assert response.status_code == 200, response.text
        answer = response.json()
    assert answer["figure"] == 14, answer
    assert "as_of 2025-06-30" in str(answer["trace"])


@pytest.mark.parametrize("period", ["Q2 of 2025", "2025 Q2", "Q2, 2025", "second quarter of 2025", "2Q25"])
def test_round3_quarter_forms_keep_the_quarter_figure(eng, period):
    answer = eng.answer(f"Total retail revenue for {period}", "analyst_retail")
    assert answer["figure"] == 39983.72, answer


@pytest.mark.parametrize("text", ["in all?", ", all.", "in top 5 products", ", try retail only", "in php", "in cup"])
def test_round3_ordinary_currency_words_keep_the_figure(eng, text):
    answer = eng.answer(f"Total retail revenue for June 2025 {text}", "analyst_retail")
    assert answer["figure"] == 13287.67, answer


@pytest.mark.parametrize("period,reason", [
    ("H1 2025", "Please give a valid period"), ("Juni 2025", "Please give a valid period"),
    ("Ju\u200dne 2025", "Please give a valid period"), ("June 2025 in €", "No EUR conversion standard"),
    ("June 2025 (€)", "No EUR conversion standard"), ("June 2025 to EUR", "No EUR conversion standard"),
    ("June 2025 in pounds", "No GBP conversion standard"), ("June 2025 in francs", "No CHF conversion standard"),
])
@pytest.mark.parametrize("surface", ["/ask", "/answer", "/agent", "ask"])
def test_round3_unreadable_or_unheld_requests_stop_before_execution(client, monkeypatch, surface, period, reason):
    import kge.app as appmod

    engine = appmod.engine()
    monkeypatch.setattr(engine.w, "gate", lambda *a, **kw: pytest.fail("refused request planned SQL"))
    monkeypatch.setattr(engine.w, "row", lambda *a, **kw: pytest.fail("refused request read figures"))
    body = {"question": f"Total retail revenue for {period}"}
    if surface == "ask":
        result = mcp_wire(client, "/mcp/answer", "analyst_retail", "ask", body)
        assert not result.get("isError"), result
        answer = result["structuredContent"]
    else:
        response = client.post(surface, json=body, headers=client.as_("analyst_retail"))
        assert response.status_code == 200, response.text
        answer = response.json()
    assert answer["behaviour"] == "abstain" and answer["reason"].startswith(reason), answer
    assert not answer.get("figure") and not answer.get("figures") and not answer.get("sql")


@pytest.mark.parametrize("question", [
    "How many retail customers have a balance over 5000?",
    "How many retail customers have a balance over 1000?",
    "How many retail customers have a balance over 10000?",
])
def test_round3_numeric_thresholds_restore_the_previous_answer(eng, question):
    answer = eng.answer(question, "analyst_enterprise")
    assert answer["figure"] == 200, answer
    assert answer["trace"][0]["period"] is None
