"""One model reading supplies terms and a period, on both public ask transports."""
import copy
import json
from decimal import Decimal

import pytest
from unittest.mock import Mock

from kge.adapters.model import NotJSON
from kge.ports import AUTHORED
from kge.services.ask.engine.combined import Combined
from kge.services.ask.engine.core import PRESETS
from kge.services.ask.engine import planner
from kge.services.ask.engine.question import parse
from tests.integration.test_answer_text import client as client, mcp_wire
from tests.integration.test_combined import Q, Stub
from tests.integration.test_ask_scope import PARAPHRASE
from tests.unit.test_question_periods import RANGE_POINT_FORMS, ROUND3_PERIODS, UNREAD_RANGE_FORMS


MONTH_WINDOWS = {
    'from April 2025 to June 2025': ('2025-04-01', '2025-07-01'),
    'between April 2025 and June 2025': ('2025-04-01', '2025-07-01'),
    'from April to June 2025': ('2025-04-01', '2025-07-01'),
    'between April and June 2025': ('2025-04-01', '2025-07-01'),
    'March 2025 to June 2025': ('2025-03-01', '2025-07-01'),
    'from 1 April 2025 to 30 June 2025': ('2025-04-01', '2025-07-01'),
    'between 1 May 2025 and 31 May 2025': ('2025-05-01', '2025-06-01'),
}
PERIOD_COMPARISONS = {f'June 2025 {operator} March 2025'
                      for operator in ('compared with', 'compared to', 'vs', 'versus')}


def assert_supported_period(answer, combined, question):
    phrase = question.removeprefix('Total retail revenue ').removeprefix('NNA ')
    if phrase not in MONTH_WINDOWS and phrase not in PERIOD_COMPARISONS:
        return False
    def figure(first, last):
        if question.startswith('NNA '):
            from tests.integration.test_rules_shapes import independent
            return independent(combined.e, 'nna', [], (first, last))[()]
        return combined.e.w.rows('retail', 'SELECT SUM(total_revenue_usd) FROM monthly_revenue '
            f"WHERE month_start >= DATE '{first}' AND month_start < DATE '{last}'", AUTHORED)[0][0]
    assert answer['guard']['passed']
    assert len(answer['sql_run']) == (2 if question.startswith('NNA ') else 1)
    if phrase in MONTH_WINDOWS:
        assert answer['behaviour'] == 'figure', answer
        expected = figure(*MONTH_WINDOWS[phrase])
        assert Decimal(str(answer['figure'])) == expected
        expected_cells = [expected]
    else:
        assert answer['behaviour'] == 'combined', answer
        current, prior = figure('2025-06-01', '2025-07-01'), figure('2025-03-01', '2025-04-01')
        expected_cells = [current, current, prior, current - prior, (current - prior) / abs(prior)]
    row, = answer['result']['rows']
    assert len(row['cells']) == len(expected_cells)
    for cell, expected in zip(row['cells'], expected_cells, strict=True):
        assert abs(Decimal(str(cell['value']['value'])) - expected) < Decimal('0.000000000000001')
    return True


class PeriodStub(Stub):
    """Replay a complete plan, with an optional adversarial time substitution."""
    def __init__(self, engine, period, base=None):
        super().__init__()
        self.engine, self.period, self.base = engine, period, base

    def json(self, prompt, max_tokens=800):
        if not prompt.startswith('query-plan-v1\n'):
            return super().json(prompt, max_tokens)
        from kge.services.ask.engine import planrules
        from kge.services.ask.engine.scope import ProductScope
        from kge.semantics.coverage import ledger, references, operations, referenced_targets
        data = json.loads(prompt.split('\n', 1)[1])
        question = data['question']
        base = self.base or question
        req = parse(base, self.engine.term_names, self.engine)
        if req.problem:
            base = Q if 'active customers' in base else 'NNA June 2025' if base.startswith('NNA') else 'Total corporate revenue for Q2 2025' if 'corporate revenue' in base else 'Total retail revenue for June 2025'
            req = parse(base, self.engine.term_names, self.engine)
        vocab = planrules.vocabulary(self.engine, 'analyst_enterprise', 'analysis', ProductScope(self.engine, data['vocabulary'].get('products')), PRESETS['graph'], req)
        reply = planrules.read(self.engine, req, vocab, PRESETS['graph'], data['turn_id'], bool(planrules.cohort_terms(self.engine, req)))
        if reply.kind != 'propose':
            self.proposal = reply.model_dump(mode='json', by_alias=True)
            return super().json(prompt, max_tokens)
        plan = reply.plan
        proposal = plan.model_dump(mode='json', by_alias=True)
        if self.period != 'rules':
            cal = plan.time.period.calendar.id if hasattr(plan.time, 'period') else plan.time.calendar.id
            period = self.period
            if period is None:
                proposal['time'] = {'kind': 'all_available', 'calendar': planrules.name(cal)}
            elif isinstance(period, dict) and period.get('kind') == 'range':
                proposal['time'] = {'kind': 'range', 'first': {'kind': 'day', 'value': period.get('start'), 'calendar': planrules.name(cal)},
                                    'last': {'kind': 'day', 'value': period.get('end'), 'calendar': planrules.name(cal)}}
            else:
                point = copy.deepcopy(period)
                if isinstance(point, dict):
                    point['kind'] = {'quarter': 'calendar_quarter'}.get(point.get('kind'), point.get('kind'))
                    point['calendar'] = planrules.name(cal)
                proposal['time'] = {'kind': 'single', 'period': point}
        targets = {n.id: n for n in plan.nodes()} | {'time': plan.time}
        obligations = []
        for i, piece in enumerate(ledger(question, data['turn_id'], vocab)):
            applied = []
            for identifier, node in targets.items():
                if piece.kind == 'name' and set(piece.candidates) & references(node, vocab):
                    applied.append(identifier)
                elif piece.kind in ('time', 'literal') and identifier == 'time':
                    applied.append(identifier)
                elif piece.kind == 'operator' and piece.operation in operations(node, vocab):
                    applied.append(identifier)
            if piece.kind == 'name' and set(piece.candidates) & referenced_targets(plan.measures, vocab) and not applied:
                applied = [m.id for m in plan.measures]
            obligations.append({'id': 'o' + str(i), 'spans': [piece.span.model_dump()], 'kind': 'name', 'targets': applied, 'state': 'applied' if applied else 'unresolved'})
        proposal['obligations'] = obligations
        self.proposal = {'kind': 'propose', 'base_turn': None, 'plan': proposal}
        return super().json(prompt, max_tokens)


def ask(client, transport, question, principal="analyst_retail.meridian@example.com"):
    body = {"question": question, "stages": {"renderer": "template"}}
    if transport == "http":
        response = client.post("/ask", json=body, headers=client.as_(principal))
        assert response.status_code == 200, response.text
        return response.json()
    response = mcp_wire(client, "/mcp/answer", principal, "ask", body)
    assert not response.get("isError"), response
    return response["structuredContent"]


@pytest.fixture
def configured(client, monkeypatch):
    import kge.app as appmod

    def configure(period, base=None):
        model = PeriodStub(appmod.engine(), period, base)
        combined = Combined(appmod.engine(), llm=model)
        monkeypatch.setattr(appmod, "_combined", combined)
        return combined, model

    return configure


@pytest.mark.parametrize("transport", ["http", "mcp"])
@pytest.mark.parametrize("phrase", UNREAD_RANGE_FORMS)
@pytest.mark.parametrize("term,principal", [("Total retail revenue", "analyst_retail.meridian@example.com"), ("NNA", "advisor_wealth.meridian@example.com")])
def test_unread_ranges_never_call_model_or_query(client, configured, monkeypatch, transport, phrase, term, principal):
    combined, model = configured({"kind": "month", "value": "2025-06"})
    gate = Mock(wraps=planner.execute)
    monkeypatch.setattr(planner, 'execute', gate)
    answer = ask(client, transport, f'{term} {phrase}', principal)
    assert_no_figure(answer)
    assert answer['trace'][0]['period'] is None
    assert len(model.prompts) == 1 and model.prompts[0].startswith('query-plan-v1\n')
    gate.assert_not_called()


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
    gate = Mock(wraps=planner.execute)
    monkeypatch.setattr(planner, 'execute', gate)
    answer = ask(client, transport, f'Total retail revenue {phrase}')
    assert_no_figure(answer)
    assert len(model.prompts) == 1
    gate.assert_not_called()


@pytest.mark.parametrize("transport", ["http", "mcp"])
@pytest.mark.parametrize("question,principal,period", [
    ("Total retail revenue since March 2025", "analyst_retail.meridian@example.com", {"kind": "month", "value": "2025-03"}),
    ("Total retail revenue June 2025 compared with March 2025", "analyst_retail.meridian@example.com", {"kind": "month", "value": "2025-06"}),
    ("Total retail revenue from April 2025 to June 2025", "analyst_retail.meridian@example.com", {"kind": "quarter", "value": "2025-Q2"}),
    ("Total retail revenue between 2024 and 2025", "analyst_retail.meridian@example.com", {"kind": "year", "value": "2025"}),
    ("Total retail revenue YTD June 2025", "analyst_retail.meridian@example.com", {"kind": "month", "value": "2025-06"}),
    ("Total retail revenue June 2025 and July", "analyst_retail.meridian@example.com", {"kind": "month", "value": "2025-06"}),
    ("Total retail revenue as at 31 June 2025", "analyst_retail.meridian@example.com", {"kind": "day", "value": "2025-06-30"}),
    ("NNA since March 2025", "advisor_wealth.meridian@example.com", {"kind": "month", "value": "2025-03"}),
    ("NNA June 2025 vs March 2025", "advisor_wealth.meridian@example.com", {"kind": "month", "value": "2025-06"}),
    (Q + " and July", "analyst_retail.meridian@example.com", {"kind": "month", "value": "2025-06"}),
])
def test_final_rules_verdict_survives_model_point_on_every_path(client, configured, monkeypatch, transport,
                                                               question, principal, period):
    combined, model = configured(period)
    gate = Mock(wraps=planner.execute)
    monkeypatch.setattr(planner, 'execute', gate)
    answer = ask(client, transport, question, principal)
    supported = assert_supported_period(answer, combined, question)
    if not supported:
        assert_no_figure(answer)
    assert len(model.prompts) == (0 if '31 June' in question else 1)
    if supported:
        assert gate.call_count == (2 if question.startswith('NNA ') else 1)
    else:
        gate.assert_not_called()


@pytest.mark.parametrize("transport", ["http", "mcp"])
def test_final_rules_verdict_needs_no_model_call_even_on_cloud_path(client, configured, monkeypatch, transport):
    import kge.pipeline.settings as settings
    combined, model = configured(None)
    monkeypatch.setattr(settings, 'gcp_native', lambda: True)
    def broken(*args):
        raise NotJSON('no JSON')
    monkeypatch.setattr(model, 'json', broken)
    answer = ask(client, transport, 'NNA as at 31 June 2025', 'advisor_wealth.meridian@example.com')
    assert_no_figure(answer)
    assert answer['reason'].startswith('That date does not exist:') and not model.prompts


@pytest.mark.parametrize("transport", ["http", "mcp"])
@pytest.mark.parametrize("period", [None, {"kind": "month", "value": "2025-06"}])
@pytest.mark.parametrize("question", ["Total retail revenue", "Total retail revenue for the specified period"])
def test_unasked_model_period_keeps_all_time_answer(client, configured, transport, period, question):
    configured(period)
    answer = ask(client, transport, question)
    if question == 'Total retail revenue':
        assert answer['figure'] == 161362.4
        assert answer['notes'] == ['no period asked: every month the filters keep']
        assert answer['history'][-1]['plan']['time']['kind'] == 'all_available'
        if period is not None:
            assert_rules_floor(answer, 161362.4)
    else:
        assert_no_figure(answer)


@pytest.mark.parametrize("transport", ["http", "mcp"])
@pytest.mark.parametrize("question,principal", [("What is Revenue?", "analyst_retail.meridian@example.com"), ("Define NNA", "advisor_wealth.meridian@example.com")])
def test_model_period_is_not_attached_to_definition(client, configured, monkeypatch, transport, question, principal):
    _, model = configured({'kind': 'month', 'value': '2024-01'})
    answer = ask(client, transport, question, principal)
    assert answer['behaviour'] in ('cite_certified', 'glossary_definition')
    assert answer['trace'][0]['period'] is None and not model.prompts


def test_cohort_validation_probes_each_table_once(client, configured, monkeypatch):
    combined, model = configured('rules')
    from kge.services.ask.engine import planner
    executed = []
    execute = planner.execute
    def counted(warehouses, statement):
        executed.append(statement)
        return execute(warehouses, statement)
    monkeypatch.setattr(planner, 'execute', counted)
    answer = ask(client, 'http', Q)
    assert answer['behaviour'] == 'combined'
    assert [f['value'] for f in answer['figures']] == [176, 3, 6400.62, 42.64]
    assert len(executed) == 1 and len(model.prompts) == 1


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
    answer = ask(client, transport, f'Total retail revenue {phrase}')
    if not assert_supported_period(answer, combined, f'Total retail revenue {phrase}'):
        assert_no_figure(answer)
    assert 1 <= len(model.prompts) <= 2


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
    gate = Mock(wraps=planner.execute)
    monkeypatch.setattr(planner, 'execute', gate)
    answer = ask(client, transport, 'Total retail revenue Juni 2025')
    assert_no_figure(answer)
    assert len(model.prompts) == (1 if period is None else 2)
    gate.assert_not_called()


@pytest.mark.parametrize("transport", ["http", "mcp"])
def test_calendar_grain_checked_before_any_query(client, configured, monkeypatch, transport):
    combined, model = configured({'kind': 'month', 'value': '2025-06'})
    gate = Mock(wraps=planner.execute)
    monkeypatch.setattr(planner, 'execute', gate)
    answer = ask(client, transport, 'Total corporate revenue for June 2025', 'analyst_enterprise.meridian@example.com')
    assert_no_figure(answer)
    assert 'period' in answer['reason'] or 'grain' in answer['reason']
    assert len(model.prompts) == 1
    gate.assert_not_called()


@pytest.mark.parametrize("transport", ["http", "mcp"])
def test_future_interval_never_becomes_a_zero_or_all_time_figure(client, configured, transport):
    _, model = configured({'kind': 'month', 'value': '2090-06'})
    answer = ask(client, transport, 'Total retail revenue Juni 2090')
    assert_no_figure(answer)
    assert len(model.prompts) == 1


@pytest.mark.parametrize("transport", ["http", "mcp"])
@pytest.mark.parametrize("period", [{"kind": "month", "value": "2025-03"}, {"kind": "month", "value": "2090-06"},
                                    {"kind": "day", "value": "2025-02-31"}, None])
def test_rules_period_is_never_overridden(client, configured, transport, period):
    _, model = configured(period)
    answer = ask(client, transport, 'Total retail revenue June 2025')
    if period is None or period.get('kind') == 'month':
        assert_rules_floor(answer, 13287.67)
        assert answer['history'][-1]['plan']['time']['period']['value'] == '2025-06'
    else:
        assert_no_figure(answer)
    assert 1 <= len(model.prompts) <= 2


@pytest.mark.parametrize("transport", ["http", "mcp"])
def test_model_period_reaches_cohort_plan(client, configured, transport):
    _, model = configured('rules')
    answer = ask(client, transport, Q)
    assert answer['behaviour'] == 'combined'
    assert [f['value'] for f in answer['figures']] == [176, 3, 6400.62, 42.64]
    assert answer['sql_source'] == 'query-plan-v1'
    assert len(model.prompts) == 1 and model.prompts[0].startswith('query-plan-v1\n')


@pytest.mark.parametrize("transport", ["http", "mcp"])
@pytest.mark.parametrize("year", [2025, 2090])
def test_context_keeps_release_checks_with_a_model_period(client, configured, transport, year):
    combined, model = configured({'kind': 'month', 'value': f'{year}-06'})
    answer = ask(client, transport, f'NNA June {year}', 'advisor_wealth.meridian@example.com')
    if year == 2025:
        baseline = combined.e.answer('NNA June 2025', 'advisor_wealth')
        assert answer['figure'] == baseline['figure']
    else:
        assert_no_figure(answer)
        assert 'not released yet' in answer['reason']
        assert 'latest released month is June 2025' in answer['reason']
    assert len(model.prompts) == 1


@pytest.mark.parametrize("transport", ["http", "mcp"])
def test_unreadable_rules_period_still_calls_model_and_preserves_no_fallback(client, configured, monkeypatch, transport):
    import kge.pipeline.settings as settings
    combined, model = configured(None)
    monkeypatch.setattr(settings, 'gcp_native', lambda: True)
    def broken(*args):
        raise NotJSON('model reply held no JSON object')
    monkeypatch.setattr(model, 'json', broken)
    if transport == 'http':
        response = client.post('/ask', json={'question': 'NNA Juni 2025'}, headers=client.as_('advisor_wealth.meridian@example.com'))
        assert response.status_code == 503
        assert 'interpreting the query plan' in response.json()['detail']
    else:
        response = mcp_wire(client, '/mcp/answer', 'advisor_wealth.meridian@example.com', 'ask', {'question': 'NNA Juni 2025'})
        assert response['isError'] and '503:' in response['content'][0]['text']


@pytest.mark.parametrize("text,expected", ROUND3_PERIODS)
@pytest.mark.parametrize("transport", ["http", "mcp"])
def test_every_recorded_round3_period_on_the_model_path(client, configured, transport, text, expected):
    combined, model = configured('rules')
    question = f'Total retail revenue for {text}'
    answer = ask(client, transport, question)
    baseline = combined.e.answer(question, 'analyst_retail')
    if expected == 'unreadable':
        assert_no_figure(answer)
    else:
        assert answer['behaviour'] == baseline['behaviour']
        assert answer.get('figure') == baseline.get('figure')
    assert all(p.startswith('query-plan-v1\n') for p in model.prompts)


@pytest.mark.parametrize("transport", ["http", "mcp"])
@pytest.mark.parametrize("kind,value,figure", [("day", "2025-06-30", 13287.67), ("quarter", "2025-Q2", 39983.72)])
def test_model_day_and_quarter_use_existing_calendar_mapping(client, configured, transport, kind, value, figure):
    configured({'kind': kind, 'value': value})
    phrase = '30 June 2025' if kind == 'day' else 'Q2 2025'
    answer = ask(client, transport, f'Total retail revenue for {phrase}')
    if kind == 'day':
        assert_rules_floor(answer, figure)
        assert answer['history'][-1]['plan']['time']['period']['kind'] == 'month'
        assert answer['history'][-1]['plan']['time']['period']['value'] == '2025-06'
    else:
        assert answer['figure'] == figure and answer['sql_source'] == 'query-plan-v1'


@pytest.mark.parametrize("transport", ["http", "mcp"])
@pytest.mark.parametrize("phrase", RANGE_POINT_FORMS)
def test_range_keeps_the_sql_checked_rules_verdict(client, configured, monkeypatch, transport, phrase):
    combined, model = configured({'kind': 'range', 'start': '2025-03-01', 'end': '2025-06-30'})
    question = f'Total retail revenue {phrase}'
    gate = Mock(wraps=planner.execute)
    monkeypatch.setattr(planner, 'execute', gate)
    answer = ask(client, transport, question)
    supported = assert_supported_period(answer, combined, question)
    if not supported:
        assert_no_figure(answer)
    assert len(model.prompts) == 1
    if supported:
        assert gate.call_count == 1
    else:
        gate.assert_not_called()


@pytest.mark.parametrize("transport", ["http", "mcp"])
@pytest.mark.parametrize("phrase", RANGE_POINT_FORMS)
def test_offline_range_answers_supported_windows_and_declines_attacks(client, monkeypatch, transport, phrase):
    import kge.app as appmod
    combined = Combined(appmod.engine(), llm=None)
    monkeypatch.setattr(appmod, '_combined', combined)
    gate = Mock(wraps=planner.execute)
    monkeypatch.setattr(planner, 'execute', gate)
    question = f'Total retail revenue {phrase}'
    answer = ask(client, transport, question)
    if assert_supported_period(answer, combined, question):
        assert gate.call_count == 1
    else:
        assert_no_figure(answer)
        assert answer['trace'][0]['period'] is None
        gate.assert_not_called()


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
    combined, model = configured('rules')
    ask(client, transport, Q)
    model.prompts.clear()
    monkeypatch.setattr(combined.e, 'PERIODS', {})
    monkeypatch.setattr(combined.e.g, '_scoped_vocabularies', {})
    gate = Mock(wraps=planner.execute)
    monkeypatch.setattr(planner, 'execute', gate)
    answer = ask(client, transport, Q)
    assert_no_figure(answer)
    assert 'period binding' in answer['reason'] and len(model.prompts) == 1
    gate.assert_not_called()


@pytest.mark.parametrize("transport", ["http", "mcp"])
def test_missing_middle_month_is_not_a_complete_quarter(client, configured, monkeypatch, transport):
    combined, model = configured('rules')
    from kge.services.ask.engine import planner
    executed = []
    execute = planner.execute
    def missing_month(warehouses, statement):
        executed.append(statement)
        assert len(executed) == 1, 'a figure ran before checking the whole quarter'
        guard, rows = execute(warehouses, statement)
        assert {str(r[1])[:7] for r in rows} >= {'2025-04', '2025-05', '2025-06'}
        return guard, [r for r in rows if str(r[1])[:7] != '2025-05']
    monkeypatch.setattr(planner, 'execute', missing_month)
    answer = ask(client, transport, 'NNA for Q2 2025', 'advisor_wealth.meridian@example.com')
    assert_no_figure(answer)
    assert 'not released yet' in answer['reason'] and len(executed) == len(model.prompts) == 1
    assert 'latest released month is June 2025' in answer['reason']


@pytest.mark.parametrize("transport", ["http", "mcp"])
def test_model_only_terms_keep_the_invalid_period_reason(client, configured, monkeypatch, transport):
    combined, model = configured({'kind': 'day', 'value': '2025-02-31'}, base=Q)
    monkeypatch.setattr(combined.e, 'ALIASES', {**combined.e.ALIASES, **dict(zip(
        ('engaged retail shoppers', 'lodged a grievance', 'mean balances', 'fees'),
        ('Active Customer', 'Complaint', 'Average Balance', 'Fee Income')))})
    gate = Mock(wraps=planner.execute)
    monkeypatch.setattr(planner, 'execute', gate)
    answer = ask(client, transport, PARAPHRASE.replace('June 2025', 'Juni 2025'))
    assert_no_figure(answer)
    assert 'valid query plan' in answer['reason'] and len(model.prompts) == 2
    gate.assert_not_called()


def test_resolution_stage_controls_period_reading(eng):
    model = PeriodStub(eng, {'kind': 'month', 'value': '2025-06'})
    combined = Combined(eng, llm=model)
    question = 'Total retail revenue Juni 2025'
    answer = combined.ask(question, 'analyst_retail', config=PRESETS['graph'])
    baseline = eng.answer(question, 'analyst_retail')
    assert_no_figure(answer)
    assert answer['reason'] == baseline['reason'] and not model.prompts


def assert_rules_floor(answer, figure):
    assert answer['behaviour'] == 'figure' and answer['figure'] == figure
    assert answer['guard']['passed'] and len(answer['sql_run']) == 1
    refusal, = [step for step in answer['trace'] if step['step'] == 'model_plan_refused']
    assert refusal['fallback'] == 'rules' and refusal['reason']
    assert next(step for step in answer['trace'] if step['step'] == 'plan')['interpretation'] == 'rules'


def assert_no_figure(answer):
    assert answer['behaviour'] in ('abstain', 'abstain_with_reason', 'clarify'), answer
    assert answer.get('figure') is None and not answer.get('figures') and not answer.get('sql') and not answer.get('result')
    assert answer['reason']
