"""Citation variants share one executable meaning; semantic mistakes never supply figures."""
from copy import deepcopy
from dataclasses import replace
from decimal import Decimal
import json

import pytest

from kge.semantics.coverage import ground_plan, ledger
from kge.semantics.plan import QueryPlan
from kge.semantics.plancheck import CheckedPlan, check_plan
from kge.services.ask.engine import planner as planner_module
from kge.services.ask.engine.combined import Combined
from kge.services.ask.engine.core import PRESETS
from kge.services.ask.engine.planner import Planner
from kge.services.ask.engine.wording import Hidden
from tests.integration.test_context_scope import vocabulary
from tests.integration.test_ask_gates import comparison_engine as comparison_engine
from tests.integration.test_planner_request_jobs import budgets
from tests.integration.test_wealth_context_filters import measure
from tests.plan_fixtures import grounded, model, name


QUESTION = 'certified NNA for March 2025'


def certified(vocab, context='nna_certified'):
    return grounded(model(measures=[measure(vocab, context)], time=dict(kind='single', period=dict(
        kind='month', value='2025-03', calendar=name('calendar')))), QUESTION, vocab,
        {'certified NNA': 'm', 'March 2025': 'time'})


def frozen(value='Y'):
    return dict(kind='compare', id='frozen', left=dict(kind='column', name=name('wealth.F_NNA_FIN.FRZN_IN'), path=[]),
                op='eq', value=dict(kind='text', value=value), evidence=[])


def citation_variant(plan, variant):
    data = plan.model_dump(mode='json', by_alias=True)
    span = data['obligations'][0]['spans'][0]
    first, second = dict(span, end=9), dict(span, start=10)
    if variant in ('split', 'split_names', 'redundant', 'redundant_whole', 'local_redundant'):
        data['obligations'][0]['spans'] = [first, second]
    if variant in ('partial_names', 'split_names'):
        data['measures'][0]['context']['evidence'] = [first]
        data['measures'][0]['measure']['evidence'] = [second]
    if variant in ('redundant', 'redundant_whole', 'local_redundant'):
        target = data['measures'][0] if variant == 'local_redundant' else data
        target['filters'] = [frozen()]
        data['obligations'].append(dict(id='extra', kind='filter', spans=[first], targets=['frozen'], state='applied'))
        if variant == 'redundant_whole':
            data['obligations'][0]['spans'] = [span]
    if variant == 'no_spans':
        data['obligations'] = []
    if variant == 'unrelated_spans':
        for obligation in data['obligations']:
            obligation['spans'] = [dict(span, start=1, end=2)]
        data['measures'][0]['context']['evidence'] = [dict(span, start=20, end=21)]
    return QueryPlan.model_validate(data)


@pytest.mark.parametrize('context', ['nna_certified', 'certified_nna'])
@pytest.mark.parametrize('variant', ['whole', 'split', 'partial_names', 'split_names', 'redundant',
                                    'redundant_whole', 'local_redundant', 'no_spans', 'unrelated_spans'])
def test_certified_citation_forms_ground_to_the_same_plan_and_two_statements(comparison_engine, monkeypatch, context, variant):
    eng, fake = comparison_engine
    vocab = vocabulary(eng, QUESTION)
    if context != 'nna_certified':
        old = 'nna_certified'
        vocab = replace(vocab, contexts={context if k == old else k: replace(v, id=context, grain=v.grain,
                                            ytd_calendar=v.ytd_calendar, bindings=v.bindings, version_dates=v.version_dates) if k == old else v
                                        for k, v in vocab.contexts.items()},
            entries={context if k == old else k: replace(v, id=context) if k == old else v for k, v in vocab.entries.items()},
            phrases={phrase: tuple(context if i == old else i for i in ids) for phrase, ids in vocab.phrases.items()})
    baseline = certified(vocab, context)
    plan = citation_variant(baseline, variant)
    proposal = model_reply(plan)
    calls = []
    execute = planner_module.execute_on_runtime
    def counted(*args, **kwargs):
        calls.append(args[1])
        return execute(*args, **kwargs)
    monkeypatch.setattr(planner_module, 'execute_on_runtime', counted)
    if fake:
        fake.requests.clear()
    result = Planner(vocab, eng.w, Hidden([], set(), [])).answer(QUESTION, proposal, grounding=True)
    assert result['kind'] == 'result', result
    assert Decimal(result['result']['rows'][0]['cells'][0]['value']['value']) == Decimal('5637481.28')
    assert len(calls) == 2
    assert result['plan'] == ground_plan(baseline, QUESTION, 'turn', vocab).model_dump(mode='json', by_alias=True)
    if fake:
        assert budgets(fake) == dict(graph=0, retrieval=0, warehouse=2, dry=2, catalogue=0)


def model_reply(plan):
    from kge.semantics.plan import Propose
    return Propose(kind='propose', base_turn=None, plan=plan)


@pytest.mark.parametrize('selection', [
    dict(kind='range', first=dict(kind='month', value='2025-03', calendar=name('calendar')),
         last=dict(kind='month', value='2025-04', calendar=name('calendar'))),
    dict(kind='range', first=dict(kind='month', value='2025-02', calendar=name('calendar')),
         last=dict(kind='month', value='2025-03', calendar=name('calendar'))),
    dict(kind='single', period=dict(kind='month', value='2025-04', calendar=name('calendar'))),
    dict(kind='single', period=dict(kind='calendar_quarter', value='2025-Q1', calendar=name('calendar'))),
    dict(kind='single', period=dict(kind='day', value='2025-03-01', calendar=name('calendar'))),
], ids=['march-april', 'february-march', 'shifted-month', 'wider-quarter', 'narrower-day'])
def test_wrong_window_refuses_the_model_and_rules_answer_with_no_extra_jobs(comparison_engine, monkeypatch, selection):
    eng, fake = comparison_engine
    vocab = vocabulary(eng, QUESTION)
    data = certified(vocab).model_dump(mode='json', by_alias=True)
    data['time'] = selection
    checked = check_plan(ground_plan(QueryPlan.model_validate(data), QUESTION, 'turn', vocab), vocab, QUESTION)
    assert not isinstance(checked, CheckedPlan), checked
    if fake:
        rules = Combined(eng, llm=None)
        assert rules.ask(QUESTION, 'analyst_enterprise')['figure'] == 5637481.28
        fake.requests.clear()
        assert rules.ask(QUESTION, 'analyst_enterprise')['figure'] == 5637481.28
        expected_jobs = budgets(fake)
        fake.requests.clear()
    calls = []
    execute = planner_module.execute_on_runtime

    def counted(*args, **kwargs):
        calls.append(args[1])
        return execute(*args, **kwargs)

    monkeypatch.setattr(planner_module, 'execute_on_runtime', counted)
    combined = Combined(eng, llm=Recorded(dict(kind='propose', base_turn=None, plan=data)))
    result = combined.ask(QUESTION, 'analyst_enterprise', config=PRESETS['graph'].with_stages(sql_source='planner+model'))
    assert result['behaviour'] == 'figure', result
    assert Decimal(str(result['figure'])) == Decimal('5637481.28')
    refusal = next(step for step in result['trace'] if step['step'] == 'model_plan_refused')
    assert refusal['reason'] and refusal['fallback'] == 'rules'
    assert next(step for step in result['trace'] if step['step'] == 'plan')['interpretation'] == 'rules'
    assert len(calls) == 2
    if fake:
        assert budgets(fake) == expected_jobs


@pytest.mark.parametrize('value', ['Y', 'N'])
def test_wrong_context_cannot_be_repaired_by_a_frozen_predicate(eng, value):
    vocab = vocabulary(eng, QUESTION)
    data = certified(vocab).model_dump(mode='json', by_alias=True)
    data['measures'][0] = measure(vocab, 'nna')
    data['filters'] = [frozen(value)]
    plan = ground_plan(QueryPlan.model_validate(data), QUESTION, 'turn', vocab)
    assert len(plan.filters) == 1
    assert check_plan(plan, vocab, QUESTION).kind in ('abstain', 'clarify')


def test_a_required_filter_with_the_wrong_value_is_not_dropped(eng):
    vocab = vocabulary(eng, QUESTION)
    data = certified(vocab).model_dump(mode='json', by_alias=True)
    data['filters'] = [frozen('N')]
    plan = ground_plan(QueryPlan.model_validate(data), QUESTION, 'turn', vocab)
    assert len(plan.filters) == 1
    assert not isinstance(check_plan(plan, vocab, QUESTION), CheckedPlan)


class Recorded:
    name = 'recorded-grounding'

    def __init__(self, reply):
        self.reply = reply

    def json(self, prompt):
        turn = json.loads(prompt.split('\n', 1)[1])['turn_id']
        def current(value):
            if isinstance(value, dict):
                return {key: turn if key == 'turn' else current(child) for key, child in value.items()}
            if isinstance(value, list):
                return [current(child) for child in value]
            return value
        return current(deepcopy(self.reply))


@pytest.mark.parametrize('kind', ['wrong_context', 'abstain', 'clarify'])
def test_public_model_refusal_uses_the_rules_floor_with_reason_and_no_extra_statements(comparison_engine, monkeypatch, kind):
    eng, fake = comparison_engine
    vocab = vocabulary(eng, QUESTION)
    data = certified(vocab).model_dump(mode='json', by_alias=True)
    data['measures'][0] = measure(vocab, 'nna')
    reply = dict(kind='propose', base_turn=None, plan=data)
    if kind == 'abstain':
        reply = dict(kind='abstain', spans=[], missing=['No model plan.'])
    if kind == 'clarify':
        reply = dict(kind='clarify', spans=[], candidates=[name('nna_certified')])
    combined = Combined(eng, llm=Recorded(reply))
    if fake:
        baseline = Combined(eng, llm=None)
        baseline.ask(QUESTION, 'analyst_enterprise')
        fake.requests.clear()
        assert baseline.ask(QUESTION, 'analyst_enterprise')['figure'] == 5637481.28
        before_jobs = budgets(fake)
        fake.requests.clear()
    calls = []
    execute = planner_module.execute_on_runtime
    def counted(*args, **kwargs):
        calls.append(args[1])
        return execute(*args, **kwargs)
    monkeypatch.setattr(planner_module, 'execute_on_runtime', counted)
    cfg = PRESETS['graph'].with_stages(sql_source='planner+model')
    result = combined.ask(QUESTION, 'analyst_enterprise', config=cfg)
    assert result['behaviour'] == 'figure', result
    assert Decimal(str(result['figure'])) == Decimal('5637481.28')
    refusal = next(step for step in result['trace'] if step['step'] == 'model_plan_refused')
    assert refusal['reason'] and refusal['fallback'] == 'rules'
    assert next(step for step in result['trace'] if step['step'] == 'plan')['interpretation'] == 'rules'
    assert len(calls) == 2
    if fake:
        assert budgets(fake) == before_jobs
    combined.plan_grounding = False
    before = len(calls)
    legacy = combined.ask(QUESTION, 'analyst_enterprise', config=cfg)
    assert legacy['behaviour'] in ('abstain', 'clarify')
    assert len(calls) == before


def test_grounding_keeps_the_ledger_phrase_instead_of_model_subspans(eng):
    vocab = vocabulary(eng, QUESTION)
    plan = ground_plan(citation_variant(certified(vocab), 'split_names'), QUESTION, 'turn', vocab)
    phrase = ledger(QUESTION, 'turn', vocab)[0]
    assert plan.measures[0].context.evidence == (phrase.span,)
    assert plan.measures[0].measure.evidence == (phrase.span,)
    assert plan.obligations[0].spans == (phrase.span,)


@pytest.mark.parametrize('variant', ['whole', 'split', 'partial_names', 'split_names', 'redundant',
                                    'redundant_whole', 'local_redundant', 'no_spans', 'unrelated_spans'])
def test_public_certified_citation_forms_answer_the_same_figure(eng, variant):
    vocab = vocabulary(eng, QUESTION)
    reply = model_reply(citation_variant(certified(vocab), variant)).model_dump(mode='json', by_alias=True)
    result = Combined(eng, llm=Recorded(reply)).ask(QUESTION, 'analyst_enterprise',
        config=PRESETS['graph'].with_stages(sql_source='planner+model'))
    assert result['behaviour'] == 'figure', result
    assert Decimal(str(result['figure'])) == Decimal('5637481.28')
    assert not any(step['step'] == 'model_plan_refused' for step in result['trace'])


@pytest.mark.parametrize('phrase', [
    'April 2025 to June 2025', 'April 2025 through June 2025',
    'from April to June 2025', 'from April 2025 to June 2025',
    'between April and June 2025', 'between April 2025 and June 2025',
])
@pytest.mark.parametrize('selection', [
    dict(kind='range', first=dict(kind='month', value='2025-04', calendar=name('calendar')),
         last=dict(kind='month', value='2025-06', calendar=name('calendar'))),
    dict(kind='range', first=dict(kind='day', value='2025-04-01', calendar=name('calendar')),
         last=dict(kind='day', value='2025-06-30', calendar=name('calendar'))),
    dict(kind='single', period=dict(kind='calendar_quarter', value='2025-Q2', calendar=name('calendar'))),
    dict(kind='single', period=dict(kind='month', value='2025-06', calendar=name('calendar'))),
], ids=['month-range', 'day-range', 'quarter', 'endpoint'])
def test_public_month_ranges_answer_exact_sql_with_constant_jobs(comparison_engine, monkeypatch, phrase, selection):
    from tests.integration.test_rules_shapes import independent

    eng, fake = comparison_engine
    question = 'certified NNA for ' + phrase
    vocab = vocabulary(eng, question)
    plan = model(measures=[measure(vocab, 'nna_certified')], time=selection)
    expected = Decimal(str(independent(eng, 'nna_certified', [], ('2025-04-01', '2025-07-01'))[()]))
    execute = planner_module.execute_on_runtime
    calls = []
    def counted(*args, **kwargs):
        calls.append(args[1])
        return execute(*args, **kwargs)
    monkeypatch.setattr(planner_module, 'execute_on_runtime', counted)
    if fake:
        fake.requests.clear()
    baseline = Combined(eng, llm=None).ask(question, 'analyst_enterprise')
    assert baseline['behaviour'] == 'figure', baseline
    assert Decimal(str(baseline['figure'])) == expected
    assert len(calls) == 2
    if fake:
        assert budgets(fake) == dict(graph=0, retrieval=0, warehouse=2, dry=2, catalogue=0)
    calls.clear()
    if fake:
        fake.requests.clear()
    answer = Combined(eng, llm=Recorded(model_reply(plan).model_dump(mode='json', by_alias=True))).ask(
        question, 'analyst_enterprise', config=PRESETS['graph'].with_stages(sql_source='planner+model'))
    assert answer['behaviour'] == 'figure', answer
    assert Decimal(str(answer['figure'])) == expected
    assert len(calls) == 2
    refused = [step for step in answer['trace'] if step['step'] == 'model_plan_refused']
    if selection['kind'] == 'single' and selection['period']['kind'] == 'month':
        assert refused
    if refused:
        assert refused[0]['fallback'] == 'rules'
    if fake:
        assert budgets(fake) == dict(graph=0, retrieval=0, warehouse=2, dry=2, catalogue=0)


def certified_quarter_by_month(vocab, question, variant):
    plan = grounded(model(measures=[measure(vocab, 'nna_certified')], time=dict(kind='single', period=dict(
        kind='calendar_quarter', value='2025-Q1', calendar=name('calendar'))),
        dimensions=[dict(kind='time', id='d', calendar=name('calendar'), grain='month')]), question, vocab,
        {'certified NNA': 'm', 'Q1 2025': 'time', 'by': 'd', 'month': 'd'})
    data = plan.model_dump(mode='json', by_alias=True)
    grouping = [o for o in data['obligations'] if o['targets'] == ['d']]
    first, last = (o['spans'][0] for o in grouping)
    spans = [dict(first, end=last['end'])] if variant == 'whole' else [first, last] if variant == 'split' else [last]
    data['obligations'] = [o for o in data['obligations'] if o not in grouping]
    data['obligations'].append(dict(grouping[0], spans=spans))
    data['dimensions'][0]['calendar']['evidence'] = spans
    if variant == 'no_spans':
        data['obligations'] = []
        data['dimensions'][0]['calendar']['evidence'] = []
    return QueryPlan.model_validate(data)


def test_public_certified_quarter_by_month_rules_reconcile_every_sql_cell(comparison_engine, monkeypatch):
    from tests.integration.test_rules_shapes import independent

    eng, fake = comparison_engine
    vocabulary(eng, 'certified NNA for Q1 2025 by month')
    expected = {key[0]: Decimal(str(value)) for key, value in independent(
        eng, 'nna_certified', ['month'], ('2025-01-01', '2025-04-01')).items()}
    total = Decimal(str(independent(eng, 'nna_certified', [], ('2025-01-01', '2025-04-01'))[()]))
    assert expected == {'2025-01-01': Decimal('5831305.25'), '2025-02-01': Decimal('6598492.05'),
                        '2025-03-01': Decimal('5637481.28')}
    assert total == Decimal('18067278.58')
    execute = planner_module.execute_on_runtime
    calls = []
    def counted(*args, **kwargs):
        calls.append(args[1])
        return execute(*args, **kwargs)
    monkeypatch.setattr(planner_module, 'execute_on_runtime', counted)
    if fake:
        fake.requests.clear()
    answer = Combined(eng, llm=None).ask('certified NNA for Q1 2025 by month', 'analyst_enterprise')
    assert answer['behaviour'] == 'figure', answer
    assert answer['figure'] is None
    assert {key: Decimal(str(value)) for key, value in answer['rows'].items()} == expected
    rows = answer['result']['rows']
    assert {row['dimensions'][0]['value']: Decimal(row['cells'][0]['value']['value'])
            for row in rows if not row.get('total')} == expected
    assert [Decimal(row['cells'][0]['value']['value']) for row in rows if row.get('total')] == [total]
    assert len(calls) == 2
    if fake:
        assert budgets(fake) == dict(graph=0, retrieval=0, warehouse=2, dry=2, catalogue=0)


@pytest.mark.parametrize('variant', ['whole', 'split', 'month_only', 'no_spans'])
def test_public_certified_quarter_by_month_citation_forms_reconcile(comparison_engine, variant):
    eng, fake = comparison_engine
    question = 'certified NNA for Q1 2025 by month'
    vocab = vocabulary(eng, question)
    cfg = PRESETS['graph'].with_stages(sql_source='planner+model')

    def ask(form):
        plan = certified_quarter_by_month(vocab, question, form)
        return Combined(eng, llm=Recorded(model_reply(plan).model_dump(mode='json', by_alias=True))).ask(
            question, 'analyst_enterprise', config=cfg)

    if fake:
        ask('whole')
        fake.requests.clear()
        assert ask('whole')['behaviour'] == 'figure'
        expected_jobs = budgets(fake)
        fake.requests.clear()
    answer = ask(variant)
    assert answer['behaviour'] == 'figure', answer
    rows = answer['result']['rows']
    assert {row['dimensions'][0]['value']: Decimal(row['cells'][0]['value']['value'])
            for row in rows if not row.get('total')} == {
        '2025-01-01': Decimal('5831305.25'),
        '2025-02-01': Decimal('6598492.05'),
        '2025-03-01': Decimal('5637481.28'),
    }
    assert [Decimal(row['cells'][0]['value']['value']) for row in rows if row.get('total')] == [Decimal('18067278.58')]
    assert not any(step['step'] == 'model_plan_refused' for step in answer['trace'])
    if fake:
        assert budgets(fake) == expected_jobs
        assert {key: value for key, value in expected_jobs.items() if key != 'graph'} == dict(
            warehouse=2, dry=2, retrieval=0, catalogue=0)


def test_unreleased_rules_floor_retains_model_refusal_and_zero_jobs(eng, monkeypatch):
    from kge.semantics.plan import Abstain
    question = 'What was certified NNA for December 2026?'
    model = Recorded(Abstain(kind='abstain', spans=(), missing=('December 2026 has not started. No figure is given.',)).model_dump())
    monkeypatch.setattr(planner_module, 'execute_on_runtime', lambda *args, **kwargs: pytest.fail('unexpected warehouse job'))
    result = Combined(eng, llm=model).ask(question, 'analyst_enterprise',
        config=PRESETS['graph'].with_stages(sql_source='planner+model'))
    assert result['behaviour'] == 'abstain'
    assert result['reason'].startswith('December 2026 has not started. No figure is given.')
    refusal = next(step for step in result['trace'] if step['step'] == 'model_plan_refused')
    assert refusal['fallback'] is None
    assert not result.get('figure') and not result.get('result')
