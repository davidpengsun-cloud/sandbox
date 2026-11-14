"""Calendar mistakes fall to rules; every executable shape resolves Map before querying."""
from copy import deepcopy

import pytest

from kge.pipeline import trace
from kge.semantics.plan import Propose, QueryPlan
from kge.semantics.plancheck import CheckedPlan
from kge.services.ask import service
from kge.services.ask.engine.combined import Combined
from kge.services.ask.engine import planrules
from kge.services.ask.engine.core import PRESETS
from kge.services.ask.engine.planner import Planner
from kge.services.ask.engine.scope import ProductScope
from kge.services.ask.engine.wording import Hidden
from tests.integration.test_ask_gates import comparison_engine as comparison_engine
from tests.integration.test_ask_stream_live import streamed
from tests.integration.test_context_scope import vocabulary
from tests.integration.test_grounding_plans import Recorded
from tests.integration.test_planner_request_jobs import budgets
from tests.integration.test_wealth_context_filters import dimension, measure, predicate
from tests.plan_fixtures import model, name


WHO = 'analyst_enterprise'
QUESTION = 'NNA for FY25-Q2 by division'


def test_wrong_calendar_returns_the_rules_quarter_and_records_refusal_without_extra_jobs(comparison_engine):
    engine, fake = comparison_engine
    baseline = service.answer(engine, engine.profile, QUESTION, WHO, 'analysis', 'graph', PRESETS['graph'])
    assert baseline['behaviour'] == 'figure', baseline
    if fake is not None:
        fake.requests.clear()
        service.answer(engine, engine.profile, QUESTION, WHO, 'analysis', 'graph', PRESETS['graph'])
        expected_jobs = budgets(fake)
        fake.requests.clear()
    plan = deepcopy(baseline['history'][-1]['plan'])
    plan['time']['period']['calendar']['id'] = 'calendar'
    combined = Combined(engine, llm=Recorded(dict(kind='propose', base_turn=None, plan=plan)))
    events, answer = streamed(engine, QUESTION, combined=combined)
    assert answer['behaviour'] == 'figure', answer
    assert answer['rows'] == baseline['rows']
    assert answer['result'] == baseline['result']
    assert answer['sql'] == baseline['sql']
    assert "2024-07-01" in answer['sql'] and "2024-10-01" in answer['sql']
    assert "2025-04-01" not in answer['sql']
    refusal = next(s for s in answer['trace'] if s['step'] == 'model_plan_refused')
    assert 'FY25-Q2' in refusal['reason'] and 'calendar' in refusal['reason'].lower() and refusal['fallback'] == 'rules'
    assert next(s for s in answer['trace'] if s['step'] == 'plan')['interpretation'] == 'rules'
    assert len([e for e in events if e['event'] == 'plan']) == 1
    if fake is not None:
        assert budgets(fake) == expected_jobs == dict(graph=0, retrieval=0, warehouse=2, dry=2, catalogue=0)


@pytest.mark.parametrize('field', ['unit', 'context', 'measure', 'calendar', 'period', 'filter_value', 'grouping'])
def test_every_proposed_binding_is_checked_against_lexical_evidence_before_warehouse_jobs(comparison_engine, field):
    engine, fake = comparison_engine
    question = 'certified NNA for March 2025'
    if field == 'grouping':
        question += ' by division'
    if field == 'filter_value':
        question += ' for segment level 5 AFFLUENT'
    baseline = service.answer(engine, engine.profile, question, WHO, 'analysis', 'graph', PRESETS['graph'])
    assert baseline['behaviour'] == 'figure', baseline
    vocab = planrules.vocabulary(engine, WHO, 'analysis', ProductScope(engine, None), PRESETS['graph'])
    data = deepcopy(baseline['history'][-1]['plan'])
    if field == 'unit':
        context = next(c.id for c in vocab.contexts.values() if c.unit == 'retail')
        data['measures'][0] = measure(vocab, context, data['measures'][0]['id'])
    elif field == 'context':
        data['measures'][0]['context']['id'] = 'nna'
    elif field == 'measure':
        data['measures'][0]['measure']['id'] = next(c.measure for c in vocab.contexts.values() if c.unit == 'retail')
    elif field == 'calendar':
        data['time']['period']['calendar']['id'] = 'fiscal_april'
    elif field == 'period':
        data['time']['period']['value'] = '2025-06'
    elif field == 'filter_value':
        data['filters'][0]['value']['value'] = 'PRIVATE'
    else:
        data['dimensions'] = [dict(dimension(vocab, 'wealth.ACCT_DIM2_1.ACCT_RELT_SPS_IN', context='nna_certified'),
                                   id=data['dimensions'][0]['id'])]
    if fake is not None:
        fake.requests.clear()
    checked = Planner(vocab, engine.w, Hidden([], set(), [])).prepare(question,
        Propose(kind='propose', base_turn=None, plan=QueryPlan.model_validate(data)), grounding=True)
    assert not isinstance(checked, CheckedPlan), (field, checked)
    if fake is not None:
        assert budgets(fake) == dict(graph=0, retrieval=0, warehouse=0, dry=0, catalogue=0)


@pytest.mark.parametrize('shape,question', [
    ('single', 'What was Retail fee income for June 2025?'),
    ('grouped', QUESTION),
    ('count', 'Count certified NNA for March 2025'),
    ('comparison', 'NNA versus certified NNA for March 2025'),
    ('derived', 'SPS contribution to client-directed NNA for May 2025 for segment level 5 PRIVATE'),
    ('multiple', 'Total revenue for June 2025'),
])
def test_every_plan_shape_streams_the_final_maps_term_before_the_first_statement(comparison_engine, shape, question):
    engine, fake = comparison_engine
    combined = None
    if shape in ('comparison', 'derived'):
        vocab = vocabulary(engine, question)
        if shape == 'comparison':
            plan = model(measures=[measure(vocab, 'nna', 'a'), measure(vocab, 'nna_certified', 'b')],
                time=dict(kind='single', period=dict(kind='month', value='2025-03', calendar=name('calendar'))),
                comparisons=[dict(kind='contexts', id='c', left='a', right='b', evidence=[])])
        else:
            plan = model(measures=[measure(vocab, 'nna_client_directed')],
                time=dict(kind='single', period=dict(kind='month', value='2025-05', calendar=name('calendar'))),
                filters=[predicate(vocab, 'wealth.CLIENT_CD_ROLLUP_1.SEGMT_LVL_ID5', 'PRIVATE', context='nna_client_directed')],
                dimensions=[dimension(vocab, 'wealth.ACCT_DIM2_1.ACCT_RELT_SPS_IN', context='nna_client_directed')],
                derived=[dict(kind='share', id='s', input='m', remove_dimensions=['d'], remove_filters=[], evidence=[])])
        combined = Combined(engine, llm=Recorded(dict(kind='propose', base_turn=None, plan=plan.model_dump(mode='json', by_alias=True))))
    def answered():
        if combined is not None:
            return service.ask(combined, engine.profile, question, WHO, 'analysis', 'graph',
                               PRESETS['graph'].with_stages(sql_source='planner+model'))
        return service.answer(engine, engine.profile, question, WHO, 'analysis', 'graph', PRESETS['graph'])
    baseline = answered()
    assert baseline['behaviour'] in ('figure', 'combined', 'sum_with_verdict', 'parts_with_reason'), (shape, baseline)
    if fake is not None:
        fake.requests.clear()
        answered()
        expected_jobs = budgets(fake)
        fake.requests.clear()
    events = []
    with trace.graph_stream(events.append):
        answer = answered()
    context, = [e['context'] for e in events if e['event'] == 'context']
    expected = answer.get('context') or {'term': answer['trace'][0]['term']}
    assert context == expected and context['term']
    assert next(i for i, e in enumerate(events) if e['event'] == 'context') < next(
        i for i, e in enumerate(events) if e['event'] == 'progress')
    if fake is not None:
        assert budgets(fake) == expected_jobs
