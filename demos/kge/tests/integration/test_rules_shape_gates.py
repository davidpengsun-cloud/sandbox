"""Adversarial grounding and full native request budgets for the new rules floor."""
from itertools import permutations
from time import perf_counter
import json

import pytest

from kge.eval.grounding import request_reset
from kge.semantics.coverage import ground_plan
from kge.semantics.plan import QueryPlan
from kge.semantics.plancheck import CheckedPlan, check_plan
from kge.semantics.plan import reply_adapter
from kge.pipeline.settings import ROOT
from kge.services.ask.engine.combined import Combined
from kge.services.ask.engine.core import PRESETS
from tests.bigquery_fake import budgets, pilot
from tests.integration.test_plan_wording_round2 import Recording
from tests.integration.test_plan_wording_round2 import sql
from tests.integration.test_ask_gates import comparison_engine as comparison_engine
from tests.integration.test_rules_shapes import CASES, FLOW_FILTER_CASES, assert_result, proposal


@pytest.mark.parametrize('case', [c for c in CASES if c['set'] == 'context'], ids=lambda c: c['id'])
@pytest.mark.parametrize('mistake', ['swap', 'wrong-context'])
def test_grounding_declines_swapped_or_wrong_contexts(eng, case, mistake):
    v, reply = proposal(eng, case)
    data = reply.plan.model_dump(mode='json', by_alias=True)
    if mistake == 'swap':
        c = data['comparisons'][0]
        c['left'], c['right'] = c['right'], c['left']
    else:
        ctx = v.contexts['nna_client_directed']
        m = data['measures'][0]
        m['context'] = dict(id=ctx.id, evidence=[])
        m['measure'] = dict(id=ctx.measure, evidence=[])
        for d in data['dimensions']:
            if d['kind'] == 'column':
                d['bindings'] = [b for b in d['bindings'] if b['context']['id'] != reply.plan.measures[0].context.id]
                d['bindings'].append({**d['bindings'][0], 'context': dict(id=ctx.id, evidence=[])})
    mistaken = ground_plan(QueryPlan.model_validate(data), case['question'], 'turn', v)
    assert not isinstance(check_plan(mistaken, v, case['question']), CheckedPlan)


@pytest.mark.parametrize('case', [c for c in CASES if c.get('ratio')], ids=lambda c: c['id'])
def test_ratio_and_share_never_swap_operands_or_drop_named_filters(eng, case):
    v, reply = proposal(eng, case)
    for error in ('swap', 'filter') if case.get('filter') else ('swap',):
        data = reply.plan.model_dump(mode='json', by_alias=True)
        if error == 'swap':
            d = data['derived'][0]
            d['numerator'], d['denominator'] = d['denominator'], d['numerator']
        else:
            data['filters'] = []
            for m in data['measures']:
                m['filters'] = []
        data['obligations'] = []
        mistaken = ground_plan(QueryPlan.model_validate(data), case['question'], 'turn', v)
        assert not isinstance(check_plan(mistaken, v, case['question']), CheckedPlan)


@pytest.mark.parametrize('case', [c for c in CASES if c['set'] == 'period' and 'previous' not in c['question']
                                and 'year over year' not in c['question']], ids=lambda c: c['id'])
def test_period_change_cannot_reverse_the_prior_window(eng, case):
    v, reply = proposal(eng, case)
    data = reply.plan.model_dump(mode='json', by_alias=True)
    data['time'], data['comparisons'][0]['other'] = data['comparisons'][0]['other'], data['time']
    mistaken = ground_plan(QueryPlan.model_validate(data), case['question'], 'turn', v)
    assert not isinstance(check_plan(mistaken, v, case['question']), CheckedPlan)


@pytest.mark.parametrize('case', FLOW_FILTER_CASES, ids=lambda c: c['id'])
def test_flow_parts_cannot_drop_named_filters(eng, case):
    v, reply = proposal(eng, case)
    data = reply.plan.model_dump(mode='json', by_alias=True)
    def direction(predicate):
        return predicate['left']['name']['id'].endswith('.DW_TRAN_DIRCTN_CD')
    data['filters'] = [p for p in data['filters'] if direction(p)]
    for measure in data['measures']:
        measure['filters'] = [p for p in measure['filters'] if direction(p)]
    data['obligations'] = []
    mistaken = ground_plan(QueryPlan.model_validate(data), case['question'], 'turn', v)
    assert not isinstance(check_plan(mistaken, v, case['question']), CheckedPlan)


BREAKDOWNS = [*permutations(('month', 'branch')), *permutations(('month', 'sub-segment')),
              *permutations(('branch', 'sub-segment')), *permutations(('month', 'branch', 'sub-segment'))]


@pytest.mark.parametrize('dimensions', BREAKDOWNS)
@pytest.mark.parametrize('joiner', [' by ', ' and ', ', '])
@pytest.mark.parametrize('modeled', [False, True], ids=['rules', 'model-replay'])
def test_public_every_pair_and_triple_in_both_orders(eng, dimensions, joiner, modeled):
    text = 'certified NNA for Q1 2025 by ' + joiner.join(dimensions)
    case = dict(id=text, set='time', question=text, contexts=['nna_certified'],
                dimensions=['segment' if d == 'sub-segment' else d for d in dimensions], window=['2025-01-01', '2025-04-01'])
    _, reply = proposal(eng, case)
    caller = Combined(eng, llm=Recording(reply) if modeled else None)
    cfg = PRESETS['graph'].with_stages(resolution='glossary+model') if modeled else PRESETS['graph']
    answer = caller.ask(text, 'analyst_enterprise', config=cfg)
    assert answer['behaviour'] == 'figure', answer
    if modeled:
        assert not any(step['step'] == 'model_plan_refused' for step in answer['trace']), answer
    assert_result(eng, case, reply, {'kind': 'result', 'guard': {'passed': True}, 'result': answer['result']})


@pytest.mark.parametrize('question', [
    'top 5 branches by certified NNA and client-directed NNA for Q1 2025',
    'which branch had the most NNA for Q1 2025 by region',
    'certified NNA versus for Q1 2025',
    'certified vs current NNA for Q1 2025 previous period',
    'certified vs current NNA for Q2 2025 vs Q1 2025',
    'ratio of NNA for Q1 2025',
    'bottom 3 regions',
    'gross inflows',
    'certified NNA for January through June',
    'certified NNA for March through January 2025',
    'certified NNA for Q1 2025 by account name',
    'invented assets for Q1 2025 by month',
    'certified NNA for Q1 2026 by month',
])
def test_ambiguous_unreleased_and_unreadable_shapes_return_no_figures(eng, question):
    answer = eng.answer(question, 'analyst_enterprise')
    assert answer['behaviour'] in ('abstain', 'abstain_with_reason', 'clarify', 'refuse'), answer
    assert not any(answer.get(key) for key in ('figure', 'figures', 'result', 'sql', 'rows', 'parts'))


def test_shape_native_requests_match_accepted_model_job_budgets(monkeypatch, tmp_path):
    fake, twin, opened, engines = pilot(monkeypatch, tmp_path)
    ids = {'time-07', 'time-33', 'time-37', 'context-01', 'context-03', 'context-18',
           'ranking-01', 'period-01', 'period-15', 'parts-01', 'parts-02', 'parts-10'}
    try:
        eng = opened()
        reset = request_reset(eng)
        records = []
        for case in [c for c in CASES if c['id'] in ids] + [c for c in FLOW_FILTER_CASES if c['id'] in ('parts-10-region', 'parts-11-region')]:
            v, reply = proposal(eng, case)
            measurements = []
            elapsed = []
            results = []
            for modeled in (True, False):
                reset()
                caller = Combined(eng, llm=Recording(reply) if modeled else None)
                cfg = PRESETS['graph'].with_stages(resolution='glossary+model') if modeled else PRESETS['graph']
                for _ in range(2):
                    fake.requests.clear()
                    start = perf_counter()
                    answer = caller.ask(case['question'], 'analyst_enterprise', config=cfg)
                    elapsed.append(perf_counter() - start)
                    expected = 'figure' if len(reply.plan.measures) == 1 and not reply.plan.comparisons and not reply.plan.derived else 'combined'
                    assert answer['behaviour'] == expected and answer.get('result'), (case['id'], answer)
                    if modeled:
                        assert not any(step['step'] == 'model_plan_refused' for step in answer['trace']), (case['id'], answer)
                    results.append(answer['result'])
                    counts = budgets(fake)
                    assert {k: val for k, val in counts.items() if k != 'graph'} == dict(warehouse=2, dry=2, catalogue=0, retrieval=0)
                    assert counts['graph'] <= 2, (case['id'], counts)
                    measurements.append(counts)
            assert all(measurements[i + 2]['graph'] <= measurements[i]['graph'] for i in (0, 1)), (case['id'], measurements)
            assert all(result == results[0] for result in results), case['id']
            records.append(dict(case=case['id'], requests=measurements, elapsed_seconds=elapsed))
        print(records)
    finally:
        for eng in engines:
            eng.close()
        if twin.conn is not None:
            twin.conn.close()
            twin.db.close()
        fake.con.close()


@pytest.mark.parametrize('identifier', ['m059_405_modifier_context_i', 'm060_405_modifier_context_o',
                                      'm061_405_modifier_context_net', 'm066_410_comparison_sides', 'm067_410_period_sides'])
@pytest.mark.parametrize('modeled', [False, True])
def test_existing_eval_sql_checks_every_newly_answered_part_and_comparison(comparison_engine, identifier, modeled):
    from kge.eval.grounding import sql_result_matches
    eng, _ = comparison_engine
    items = json.loads((ROOT / 'tests/fixtures/grounding_eval/mistakes.json').read_text())
    item = next(item for item in items if item['id'] == identifier)
    caller = Combined(eng, llm=Recording(reply_adapter.validate_python(item['reply'])) if modeled else None)
    cfg = PRESETS['graph'].with_stages(resolution='glossary+model') if modeled else PRESETS['graph']
    answer = caller.ask(item['question'], item['principal'], config=cfg)
    assert answer['behaviour'] == 'combined' and answer.get('result'), answer
    plan = answer['history'][-1]['plan']
    assert sql_result_matches(answer['result'], sql(eng, item['reference_sql']), plan=plan), answer['result']
    with eng.g.as_roles():
        truth = eng.w.resolve_reference_sql(item['reference_sql'], unit='wealth', readable_units=eng.g.readable_units(item['principal']))
    assert 'rows' in truth, truth
    assert sql_result_matches(answer['result'], truth['rows'], plan=plan), (answer['result'], truth)
    if modeled:
        assert any(step['step'] == 'model_plan_refused' and step.get('fallback') == 'rules' for step in answer['trace'])
