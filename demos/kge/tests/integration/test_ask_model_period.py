"""One model reading supplies terms and a period, on both public ask transports."""
import pytest
from unittest.mock import Mock

from kge.adapters.model import NotJSON
from kge.services.ask.engine.combined import Combined
from kge.services.ask.engine.core import PRESETS
from kge.services.ask.engine.question import parse
from tests.integration.test_answer_text import client as client, mcp_wire
from tests.integration.test_combined import Q, Stub
from tests.integration.test_ask_scope import COHORT_TERMS, PARAPHRASE
from tests.unit.test_question_periods import RANGE_POINT_FORMS, ROUND3_PERIODS, UNREAD_RANGE_FORMS


class PeriodStub(Stub):
    def __init__(self, period, **kwargs):
        super().__init__(**kwargs)
        self.period = period

    def json(self, prompt, max_tokens=800):
        reply = super().json(prompt, max_tokens)
        return {**reply, "period": self.period} if prompt.startswith("Glossary terms") else reply


def ask(client, transport, question, principal="analyst_retail"):
    if transport == "http":
        response = client.post("/ask", json={"question": question}, headers=client.as_(principal))
        assert response.status_code == 200, response.text
        return response.json()
    response = mcp_wire(client, "/mcp/answer", principal, "ask", {"question": question})
    assert not response.get("isError"), response
    return response["structuredContent"]


@pytest.fixture
def configured(client, monkeypatch):
    import kge.app as appmod

    def configure(period):
        model = PeriodStub(period)
        combined = Combined(appmod.engine(), llm=model)
        monkeypatch.setattr(appmod, "_combined", combined)
        return combined, model

    return configure


@pytest.mark.parametrize("transport", ["http", "mcp"])
@pytest.mark.parametrize("phrase", UNREAD_RANGE_FORMS)
@pytest.mark.parametrize("term,principal", [("Total retail revenue", "analyst_retail"), ("NNA", "advisor_wealth")])
def test_unread_ranges_never_call_model_or_query(client, configured, monkeypatch, transport, phrase, term, principal):
    combined, model = configured({"kind": "month", "value": "2025-06"})
    gate, row = Mock(wraps=combined.e.w.gate), Mock(wraps=combined.e.w.row)
    monkeypatch.setattr(combined.e.w, "gate", gate)
    monkeypatch.setattr(combined.e.w, "row", row)
    answer = ask(client, transport, f"{term} {phrase}", principal)
    assert answer["behaviour"].startswith("abstain"), answer
    assert answer["reason"]
    assert not answer.get("figure") and not answer.get("figures") and not answer.get("sql")
    assert answer["trace"][0]["period"] is None
    assert answer["trace"][0]["period_by"] == "rules"
    assert not model.prompts
    gate.assert_not_called()
    row.assert_not_called()


@pytest.mark.parametrize("transport", ["http", "mcp"])
@pytest.mark.parametrize("phrase,period,reason", [
    ("2025", {"kind": "month", "value": "2025-06"}, "grain"),
    ("active 2025", {"kind": "month", "value": "2025-06"}, "grain"),
    ("H1 2025", {"kind": "quarter", "value": "2025-Q2"}, "grain"),
    ("first half of 2025", {"kind": "quarter", "value": "2025-Q2"}, "grain"),
    ("the first half of 2025", {"kind": "quarter", "value": "2025-Q2"}, "grain"),
    ("Juni 2025", {"kind": "month", "value": "2024-06"}, "year"),
    ("Juni ２０２５", {"kind": "month", "value": "2024-06"}, "year"),
])
def test_model_period_must_agree_with_question(client, configured, monkeypatch, transport, phrase, period, reason):
    combined, model = configured(period)
    gate, row = Mock(wraps=combined.e.w.gate), Mock(wraps=combined.e.w.row)
    monkeypatch.setattr(combined.e.w, "gate", gate)
    monkeypatch.setattr(combined.e.w, "row", row)
    answer = ask(client, transport, f"Total retail revenue {phrase}")
    assert answer["behaviour"].startswith("abstain"), answer
    assert reason in answer["reason"]
    assert not answer.get("figure") and not answer.get("figures") and not answer.get("sql")
    assert len(model.prompts) == 1
    gate.assert_not_called()
    row.assert_not_called()


@pytest.mark.parametrize("transport", ["http", "mcp"])
@pytest.mark.parametrize("question,principal,period", [
    ("Total retail revenue since March 2025", "analyst_retail", {"kind": "month", "value": "2025-03"}),
    ("Total retail revenue June 2025 compared with March 2025", "analyst_retail", {"kind": "month", "value": "2025-06"}),
    ("Total retail revenue from April 2025 to June 2025", "analyst_retail", {"kind": "quarter", "value": "2025-Q2"}),
    ("Total retail revenue between 2024 and 2025", "analyst_retail", {"kind": "year", "value": "2025"}),
    ("Total retail revenue YTD June 2025", "analyst_retail", {"kind": "month", "value": "2025-06"}),
    ("Total retail revenue June 2025 and July", "analyst_retail", {"kind": "month", "value": "2025-06"}),
    ("Total retail revenue as at 31 June 2025", "analyst_retail", {"kind": "day", "value": "2025-06-30"}),
    ("NNA since March 2025", "advisor_wealth", {"kind": "month", "value": "2025-03"}),
    ("NNA June 2025 vs March 2025", "advisor_wealth", {"kind": "month", "value": "2025-06"}),
    (Q + " and July", "analyst_retail", {"kind": "month", "value": "2025-06"}),
])
def test_final_rules_verdict_survives_model_point_on_every_path(client, configured, monkeypatch, transport,
                                                               question, principal, period):
    combined, model = configured(period)
    baseline = combined.e.answer(question, principal)
    from unittest.mock import Mock

    gate, row = Mock(wraps=combined.e.w.gate), Mock(wraps=combined.e.w.row)
    monkeypatch.setattr(combined.e.w, "gate", gate)
    monkeypatch.setattr(combined.e.w, "row", row)
    answer = ask(client, transport, question, principal)
    assert answer["behaviour"].startswith("abstain")
    assert answer["reason"] == baseline["reason"]
    assert not answer.get("figure") and not answer.get("figures") and not answer.get("sql")
    assert answer["trace"][0]["period"] is None and answer["trace"][0]["period_by"] == "rules"
    assert not model.prompts
    gate.assert_not_called()
    row.assert_not_called()


@pytest.mark.parametrize("transport", ["http", "mcp"])
def test_final_rules_verdict_needs_no_model_call_even_on_cloud_path(client, configured, monkeypatch, transport):
    import kge.pipeline.settings as settings

    combined, model = configured(None)
    monkeypatch.setattr(settings, "gcp_native", lambda: True)

    def broken(*args):
        raise NotJSON("model reply held no JSON object")

    monkeypatch.setattr(model, "json", broken)
    question = "NNA since March 2025"
    baseline = combined.e.answer(question, "advisor_wealth")
    answer = ask(client, transport, question, "advisor_wealth")
    assert answer["behaviour"] == baseline["behaviour"]
    assert answer["reason"] == baseline["reason"]


@pytest.mark.parametrize("transport", ["http", "mcp"])
@pytest.mark.parametrize("period", [None, {"kind": "month", "value": "2025-06"}])
@pytest.mark.parametrize("question", ["Total retail revenue", "Total retail revenue for the specified period"])
def test_unasked_model_period_keeps_all_time_answer(client, configured, transport, period, question):
    configured(period)
    answer = ask(client, transport, question)
    assert answer["figure"] == 161362.4
    assert answer["notes"][0] == "no period asked: every month the filters keep"
    assert answer["trace"][0].get("period_by", "none") == "none"
    assert answer["trace"][0]["period"] is None


@pytest.mark.parametrize("transport", ["http", "mcp"])
@pytest.mark.parametrize("question,principal", [("What is Revenue?", "analyst_retail"), ("Define NNA", "advisor_wealth")])
def test_model_period_is_not_attached_to_definition(client, configured, monkeypatch, transport, question, principal):
    combined, _ = configured({"kind": "month", "value": "2024-01"})
    original = combined.e.answer
    requests = []

    def capture(*args, **kwargs):
        requests.append(kwargs["_request"])
        return original(*args, **kwargs)

    monkeypatch.setattr(combined.e, "answer", capture)
    answer = ask(client, transport, question, principal)
    assert requests and requests[0].definitional and requests[0].period is None
    assert answer["trace"][0]["period"] is None


def test_cohort_validation_probes_each_table_once(client, configured, monkeypatch):
    combined, _ = configured({"kind": "month", "value": "2025-06"})
    calls = {"gate": [], "row": []}

    def counted(name, original):
        def call(unit, sql, *args, **kwargs):
            if sql.startswith(("SELECT count(*)", "SELECT min(")):
                calls[name].append((unit, sql))
            return original(unit, sql, *args, **kwargs)
        return call

    for name in calls:
        monkeypatch.setattr(combined.e.w, name, counted(name, getattr(combined.e.w, name)))
    answer = ask(client, "http", "How many active customers in Retail in Juni 2025, and what were their average revenue and total fee income?")
    assert answer["behaviour"] == "combined"
    revenue = [f for f in answer["figures"] if f["column"].startswith("monthly_revenue.")]
    assert {f["kind"] for f in revenue} == {"avg", "sum"}
    assert len(calls["gate"]) == len(calls["row"]) == 4
    assert len(set(calls["gate"])) == len(set(calls["row"])) == 4


@pytest.mark.parametrize("transport", ["http", "mcp"])
@pytest.mark.parametrize("phrase,period,expected", [
    ("Juni 2025", {"kind": "month", "value": "2025-06"}, 13287.67),
    ("Ju\u200dne 2025", {"kind": "month", "value": "2025-06"}, 13287.67),
    ("Ju\u00adne 2025", {"kind": "month", "value": "2025-06"}, 13287.67),
    ("active 2025", {"kind": "year", "value": "2025"}, None),
    ("YTD", {"kind": "range", "start": "2025-01-01", "end": "2025-06-30"}, None),
    ("H1 2025", {"kind": "range", "start": "2025-01-01", "end": "2025-06-30"}, None),
    ("the first half of 2025", {"kind": "range", "start": "2025-01-01", "end": "2025-06-30"}, None),
    ("since March", {"kind": "range", "start": "2025-03-01"}, None),
    ("from April to June 2025", {"kind": "range", "start": "2025-04-01", "end": "2025-06-30"}, None),
    ("between April and June 2025", {"kind": "range", "start": "2025-04-01", "end": "2025-06-30"}, None),
    ("June compared with July", {"kind": "range"}, None),
])
def test_unplaced_attack_periods(client, configured, transport, phrase, period, expected):
    combined, model = configured(period)
    question = f"Total retail revenue {phrase}"
    req = parse(question, combined.e.term_names, combined.e)
    assert req.period is None
    answer = ask(client, transport, question)
    if not req.model_period_allowed:
        baseline = combined.e.answer(question, "analyst_retail")
        assert answer["behaviour"] == baseline["behaviour"]
        assert answer["reason"] == baseline["reason"]
        assert answer["trace"][0]["period_by"] == "rules"
        assert not model.prompts
        return
    assert len(model.prompts) == 1
    assert '"period"' in model.prompts[0]
    assert answer["trace"][0]["period_by"] == "model"
    if period["kind"] == "range":
        assert answer["behaviour"] == "abstain_with_reason", answer
        assert "cannot execute a period range" in answer["reason"]
        assert not answer.get("sql") and not answer.get("figure")
    elif expected is not None:
        assert answer["figure"] == expected, answer
    else:
        assert answer["behaviour"] == "abstain_with_reason"
        assert "does not hold the whole interval" in answer["reason"]


@pytest.mark.parametrize("transport", ["http", "mcp"])
@pytest.mark.parametrize("period,reason", [
    (None, "did not place a period"),
    ({"kind": "day", "value": "2025-02-31"}, "not a real date"),
    ({"kind": "month", "value": "2025-13"}, "not a real date"),
    ({"kind": "quarter", "value": "2025-Q5"}, "invalid date, grain or format"),
    ({"kind": "week", "value": "2025-W01"}, "invalid date, grain or format"),
    ({"kind": "month", "value": "2025-06' OR 1=1 --"}, "invalid date, grain or format"),
    ([], "must be an object"),
    ({"kind": "month", "value": 202506}, "needs a date value"),
])
def test_invalid_period_never_reaches_query_or_sql_model(client, configured, monkeypatch, transport, period, reason):
    combined, model = configured(period)
    monkeypatch.setattr(combined.e.w, "gate", lambda *a, **kw: pytest.fail("invalid period planned SQL"))
    monkeypatch.setattr(combined.e.w, "row", lambda *a, **kw: pytest.fail("invalid period queried rows"))
    answer = ask(client, transport, "Total retail revenue Juni 2025")
    assert answer["behaviour"] == "abstain_with_reason", answer
    assert reason in answer["reason"]
    assert "no figure is given rather than every month's" in answer["reason"]
    assert len(model.prompts) == 1


@pytest.mark.parametrize("transport", ["http", "mcp"])
def test_calendar_grain_checked_before_any_query(client, configured, monkeypatch, transport):
    combined, model = configured({"kind": "month", "value": "2025-06"})
    monkeypatch.setattr(combined.e.w, "gate", lambda *a, **kw: pytest.fail("unsupported grain planned SQL"))
    answer = ask(client, transport, "Total corporate revenue Juni 2025", "analyst_enterprise")
    assert answer["behaviour"] == "abstain_with_reason", answer
    assert "quarterly grain: a single month is not available" in answer["reason"]
    assert len(model.prompts) == 1


@pytest.mark.parametrize("transport", ["http", "mcp"])
def test_future_interval_never_becomes_a_zero_or_all_time_figure(client, configured, transport):
    _, model = configured({"kind": "month", "value": "2090-06"})
    answer = ask(client, transport, "Total retail revenue Juni 2090")
    assert answer["behaviour"] == "abstain_with_reason", answer
    assert "holds no data" in answer["reason"]
    assert not answer.get("figure") and not answer.get("figures")
    assert len(model.prompts) == 1


@pytest.mark.parametrize("transport", ["http", "mcp"])
@pytest.mark.parametrize("period", [{"kind": "month", "value": "2025-03"}, {"kind": "month", "value": "2090-06"},
                                    {"kind": "day", "value": "2025-02-31"}, None])
def test_rules_period_is_never_overridden(client, configured, transport, period):
    configured(period)
    answer = ask(client, transport, "Total retail revenue June 2025")
    assert answer["figure"] == 13287.67
    assert answer["trace"][0]["period_by"] == "rules"
    assert answer["trace"][0]["period"] == "month 2025-06-01"


@pytest.mark.parametrize("transport", ["http", "mcp"])
def test_model_period_reaches_cohort_plan(client, configured, transport):
    _, model = configured({"kind": "month", "value": "2025-06"})
    answer = ask(client, transport, Q.replace("June", "Juni"))
    assert answer["behaviour"] == "combined", answer
    assert [f["value"] for f in answer["figures"]] == [176, 3, 6400.62, 42.64]
    assert answer["trace"][0]["period_by"] == "model"
    assert len([p for p in model.prompts if p.startswith("Glossary terms")]) == 1
    assert not any(p.startswith("Write one") for p in model.prompts)


@pytest.mark.parametrize("transport", ["http", "mcp"])
@pytest.mark.parametrize("year", [2025, 2090])
def test_context_keeps_release_checks_with_a_model_period(client, configured, transport, year):
    combined, _ = configured({"kind": "month", "value": f"{year}-06"})
    answer = ask(client, transport, f"NNA Juni {year}", "advisor_wealth")
    assert answer["trace"][0]["period_by"] == "model"
    if year == 2025:
        baseline = combined.e.answer("NNA June 2025", "advisor_wealth")
        assert answer["figure"] == baseline["figure"]
    else:
        assert answer["behaviour"] == "abstain_with_reason"
        assert "not booked" in answer["reason"]


@pytest.mark.parametrize("transport", ["http", "mcp"])
def test_unreadable_rules_period_still_calls_model_and_preserves_no_fallback(client, configured, monkeypatch, transport):
    import kge.pipeline.settings as settings

    combined, model = configured(None)
    monkeypatch.setattr(settings, "gcp_native", lambda: True)

    def broken(*args):
        raise NotJSON("model reply held no JSON object")

    monkeypatch.setattr(model, "json", broken)
    monkeypatch.setattr(combined.e, "answer", lambda *a, **kw: pytest.fail("invalid JSON fell back"))
    if transport == "http":
        response = client.post("/ask", json={"question": "NNA Juni 2025"}, headers=client.as_("advisor_wealth"))
        assert response.status_code == 503
        assert "reading the question" in response.json()["detail"]
    else:
        response = mcp_wire(client, "/mcp/answer", "advisor_wealth", "ask", {"question": "NNA Juni 2025"})
        assert response["isError"]
        assert "503:" in response["content"][0]["text"]


@pytest.mark.parametrize("text,expected", ROUND3_PERIODS)
@pytest.mark.parametrize("transport", ["http", "mcp"])
def test_every_recorded_round3_period_on_the_model_path(client, configured, transport, text, expected):
    """Recognised rules periods remain identical; unresolved adversarial forms explicitly abstain."""
    question = f"Total retail revenue for {text}"
    combined, model = configured(None)
    req = parse(question, combined.e.term_names, combined.e)
    answer = ask(client, transport, question)
    if expected == "unreadable":
        if req.model_period_allowed:
            assert answer["behaviour"] == "abstain_with_reason", answer
            assert "did not place a period" in answer["reason"]
        else:
            baseline = combined.e.answer(question, "analyst_retail")
            assert answer["behaviour"] == baseline["behaviour"]
            assert answer["reason"] == baseline["reason"]
        assert not answer.get("sql")
    else:
        baseline = combined.e.answer(question, "analyst_retail")
        assert answer["behaviour"] == baseline["behaviour"]
        assert answer.get("figure") == baseline.get("figure")
        assert answer["trace"][0]["period_by"] == "rules"
    assert len(model.prompts) == (0 if req.problem and not req.model_period_allowed else 1)


@pytest.mark.parametrize("transport", ["http", "mcp"])
@pytest.mark.parametrize("kind,value,figure", [("day", "2025-06-30", 13287.67), ("quarter", "2025-Q2", 39983.72)])
def test_model_day_and_quarter_use_existing_calendar_mapping(client, configured, transport, kind, value, figure):
    configured({"kind": kind, "value": value})
    answer = ask(client, transport, "Total retail revenue for Juni 2025")
    assert answer["figure"] == figure, answer
    assert answer["trace"][0]["period_by"] == "model"


@pytest.mark.parametrize("transport", ["http", "mcp"])
@pytest.mark.parametrize("phrase", RANGE_POINT_FORMS)
def test_range_keeps_the_rules_verdict_even_when_model_agrees(client, configured, monkeypatch, transport, phrase):
    """The rules' range verdict is final whether the model proposes a point or a range."""
    combined, model = configured({"kind": "range", "start": "2025-03-01", "end": "2025-06-30"})
    question = f"Total retail revenue {phrase}"
    req = parse(question, combined.e.term_names, combined.e)
    assert req.period is None and req.problem
    monkeypatch.setattr(combined.e.w, "gate", lambda *a, **kw: pytest.fail("range planned endpoint SQL"))
    monkeypatch.setattr(combined.e.w, "row", lambda *a, **kw: pytest.fail("range read endpoint rows"))
    answer = ask(client, transport, question)
    assert answer["behaviour"] == "abstain_with_reason", answer
    assert answer["trace"][0]["period_by"] == "rules"
    assert answer["trace"][0]["period"] is None
    assert answer["reason"] == combined.e.semantics.unplaced([question])
    assert not answer.get("figure") and not answer.get("figures") and not answer.get("sql")
    assert not model.prompts


@pytest.mark.parametrize("transport", ["http", "mcp"])
@pytest.mark.parametrize("phrase", RANGE_POINT_FORMS)
def test_offline_range_uses_the_existing_unplaced_period_abstention(client, monkeypatch, transport, phrase):
    import kge.app as appmod

    combined = Combined(appmod.engine(), llm=None)
    monkeypatch.setattr(appmod, "_combined", combined)
    monkeypatch.setattr(combined.e.w, "gate", lambda *a, **kw: pytest.fail("offline range planned endpoint SQL"))
    monkeypatch.setattr(combined.e.w, "row", lambda *a, **kw: pytest.fail("offline range read endpoint rows"))
    question = f"Total retail revenue {phrase}"
    answer = ask(client, transport, question)
    assert answer["behaviour"] == "abstain_with_reason", answer
    assert answer["reason"] == combined.e.semantics.unplaced([question])
    assert answer["trace"][0]["period"] is None
    assert answer["trace"][0]["period_by"] == "rules"
    assert not answer.get("figure") and not answer.get("figures") and not answer.get("sql")


@pytest.mark.parametrize("transport", ["http", "mcp"])
def test_period_validation_cannot_read_a_denied_unit(client, configured, monkeypatch, transport):
    combined, _ = configured({"kind": "month", "value": "2025-06"})
    monkeypatch.setattr(combined.e.w, "gate", lambda *a, **kw: pytest.fail("period validation bypassed read access"))
    answer = ask(client, transport, "Total corporate revenue Juni 2025")
    assert answer["behaviour"] == "refuse"


@pytest.mark.parametrize("transport", ["http", "mcp"])
def test_repaired_period_keeps_the_currency_guard(client, configured, monkeypatch, transport):
    combined, _ = configured({"kind": "month", "value": "2025-06"})
    monkeypatch.setattr(combined.e.w, "gate", lambda *a, **kw: pytest.fail("repair dropped the currency refusal"))
    answer = ask(client, transport, "Total retail revenue for Juni 2025 in EUR")
    assert answer["behaviour"] == "abstain"
    assert answer["reason"].startswith("No EUR conversion standard")


@pytest.mark.parametrize("transport", ["http", "mcp"])
def test_missing_period_binding_never_allows_generated_sql(client, configured, monkeypatch, transport):
    combined, model = configured({"kind": "month", "value": "2025-06"})
    monkeypatch.setattr(combined.e, "PERIODS", {})
    monkeypatch.setattr(combined.e.w, "gate", lambda *a, **kw: pytest.fail("ignored period reached SQL"))
    answer = ask(client, transport, Q.replace("June", "Juni"))
    assert answer["behaviour"] == "abstain_with_reason"
    assert "no period column" in answer["reason"]
    assert len(model.prompts) == 1


@pytest.mark.parametrize("transport", ["http", "mcp"])
def test_missing_middle_month_is_not_a_complete_quarter(client, configured, monkeypatch, transport):
    combined, model = configured({"kind": "quarter", "value": "2025-Q2"})
    row = combined.e.w.row

    def missing_month(unit, sql, dialect):
        assert sql.startswith("SELECT count(*), count(DISTINCT"), "a figure ran before checking the full interval"
        count, _ = row(unit, sql, dialect)
        return count, 2

    monkeypatch.setattr(combined.e.w, "row", missing_month)
    answer = ask(client, transport, "Total retail revenue for Juni 2025")
    assert answer["behaviour"] == "abstain_with_reason"
    assert "expected 3 periods, found 2" in answer["reason"]
    assert len(model.prompts) == 1


@pytest.mark.parametrize("transport", ["http", "mcp"])
def test_model_only_terms_keep_the_invalid_period_reason(client, configured, monkeypatch, transport):
    combined, model = configured({"kind": "day", "value": "2025-02-31"})
    model.terms = COHORT_TERMS
    question = PARAPHRASE.replace("June 2025", "Juni 2025")
    req = parse(question, combined.e.term_names, combined.e)
    assert req.term is None and req.period is None and req.model_period_allowed
    monkeypatch.setattr(combined.e.w, "gate", lambda *a, **kw: pytest.fail("model-only terms bypassed period validation"))
    answer = ask(client, transport, question)
    assert answer["behaviour"] == "abstain_with_reason"
    assert "not a real date" in answer["reason"]


def test_resolution_stage_controls_period_reading(eng):
    model = PeriodStub({"kind": "month", "value": "2025-06"})
    combined = Combined(eng, llm=model)
    answer = combined.ask("Total retail revenue Juni 2025", "analyst_retail", config=PRESETS["graph"])
    baseline = eng.answer("Total retail revenue Juni 2025", "analyst_retail")
    assert answer["behaviour"] == baseline["behaviour"]
    assert answer.get("figure") == baseline.get("figure")
    assert not model.prompts
    assert answer["trace"][0]["period_by"] == "rules"
