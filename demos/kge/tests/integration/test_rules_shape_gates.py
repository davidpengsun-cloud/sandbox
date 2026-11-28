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
    'certified NNA for March through January 2025',
    'certified NNA for Q1 2025 by account name',
    'invented assets for Q1 2025 by month',
    'certified NNA for Q1 2026 by month',
])
def test_ambiguous_unreleased_and_unreadable_shapes_return_no_figures(eng, question):
    answer = eng.answer(question, 'analyst_enterprise')
    assert answer['behaviour'] in ('abstain', 'abstain_with_reason', 'clarify', 'refuse'), answer
    assert not any(answer.get(key) for key in ('figure', 'figures', 'result', 'sql', 'rows', 'parts'))


def test_month_range_without_a_year_takes_the_year_of_the_named_contexts_calendar(eng):
    import datetime
    from decimal import Decimal
    from kge.semantics.planperiods import time_bounds
    from tests.integration.test_context_scope import vocabulary
    from tests.integration.test_rules_shapes import independent
    question = 'certified NNA for January through June'
    answer = eng.answer(question, 'analyst_enterprise')
    assert answer['behaviour'] == 'figure', answer
    plan = QueryPlan.model_validate(answer['history'][-1]['plan'])
    assert time_bounds(plan.time, vocabulary(eng, question).calendars) == (datetime.date(2025, 1, 1), datetime.date(2025, 7, 1))
    assert Decimal(str(answer['figure'])) == independent(eng, 'nna_certified', [], ('2025-01-01', '2025-07-01'))[()]


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
def test_existing_eval_sql_checks_every_newly_answered_part_and_comparison(comparison_engine, identifier, modeled, monkeypatch):
    """The modeled case's recorded mistake is asked under ASK_ORDER=model_first, A1's documented order that keeps #415's
    (#464 requirement 2), so the model's plan is refused and the rules floor answers."""
    from kge.eval.grounding import sql_result_matches
    eng, _ = comparison_engine
    if modeled:
        monkeypatch.setenv('ASK_ORDER', 'model_first')
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


REFUSED_TO_DATE = {'June 2025 to date': 'June 2025', 'Q2 2025 to date': 'Q2 2025', '30 June 2025 to date': '30 June 2025',
                   'FY26-Q1 to date': 'FY26-Q1', 'June 2025 to date by month': 'June 2025',
                   'June 2025, to date': 'June 2025', 'Q2 2025 , to date': 'Q2 2025'}


@pytest.mark.parametrize('phrase', ['June 2025 YTD', 'June 2025 year to date', 'Q2 2025 YTD', 'Q2 2025 year to date',
                                    *REFUSED_TO_DATE])
def test_period_to_date_refuses_and_named_year_to_date_forms_still_answer(eng, phrase):
    """"<period> to date" for a stated period shorter than a year ("June 2025 to date", "Q2 2025 to date",
    "30 June 2025 to date", "June 2025, to date", "June 2025 to date by month") reads as the year to date through that
    period or as that period until now, so it refuses and says which readings it saw; the YTD and "year to date" forms name the year to
    date and answer it."""
    from tests.integration.test_rules_shapes import independent
    answer = Combined(eng, llm=None).ask('NNA ' + phrase, 'analyst_enterprise', config=PRESETS['graph'])
    if phrase in REFUSED_TO_DATE:
        stated = REFUSED_TO_DATE[phrase]
        assert answer['behaviour'] in ('abstain', 'abstain_with_reason') and answer.get('figure') is None, answer
        assert not answer.get('sql_run') and not answer.get('result'), answer
        assert (f"'{stated} to date' can mean the year to date through {stated} or the period from {stated} until now"
                in json.dumps(answer, default=str)), answer
    else:
        assert answer['behaviour'] == 'figure', answer
        assert answer['figure'] == pytest.approx(float(independent(eng, 'nna', [], ('2025-01-01', '2025-07-01'))[()]), abs=0.01)


@pytest.mark.parametrize('case', FLOW_FILTER_CASES, ids=lambda c: c['id'])
def test_follow_up_cannot_drop_an_inherited_named_filter(eng, case):
    """A follow-up inherits the earlier question's named filter and must keep owning it: a plan that drops it fails the
    word check as it would in the turn that named it, even where the measure's context already implies the filter."""
    from kge.semantics.plan import HistoryEntry
    from kge.semantics.planhistory import inherited
    v, reply = proposal(eng, case)
    history = (HistoryEntry(turn_id='turn', question=case['question'], plan=reply.plan, clarification=None, binding=v.binding),)
    followup = 'Show me that'
    kept = check_plan(reply.plan, v, followup, turn='next', inherited=inherited(reply.plan, history, v))
    assert isinstance(kept, CheckedPlan), kept
    data = reply.plan.model_dump(mode='json', by_alias=True)
    named = {'region': '.RGN_CD', 'affluent': '.SEGMT_LVL_ID5'}[case['filter']]
    dropped = {p['id'] for p in data['filters'] if p['left']['name']['id'].endswith(named)}
    assert dropped, data['filters']
    data['filters'] = [p for p in data['filters'] if p['id'] not in dropped]
    measures = [m['id'] for m in data['measures']]
    for retarget in ([], measures):
        for obligation in data['obligations']:
            named_here = bool(dropped & set(obligation['targets']))
            obligation['targets'] = [t for t in obligation['targets'] if t not in dropped]
            if named_here:
                obligation['targets'] = list(dict.fromkeys([*obligation['targets'], *retarget]))
        mistaken = QueryPlan.model_validate(data)
        decision = check_plan(mistaken, v, followup, turn='next', inherited=inherited(mistaken, history, v))
        assert not isinstance(decision, CheckedPlan), (retarget, decision)
        if retarget:
            from kge.semantics.coverage import INHERITED_UNAPPLIED
            assert INHERITED_UNAPPLIED not in decision.missing, decision
        data = reply.plan.model_dump(mode='json', by_alias=True)
        data['filters'] = [p for p in data['filters'] if p['id'] not in dropped]


@pytest.mark.parametrize('question', ['NNA by month in Q2 2025, running total to date', 'NNA to date by month for Q2 2025'])
def test_running_total_to_date_away_from_a_stated_period_still_answers(eng, question):
    """Only "to date" straight after a stated period is ambiguous; a running total "to date" whose period is stated
    elsewhere keeps answering, month by month."""
    answer = Combined(eng, llm=None).ask(question, 'analyst_enterprise', config=PRESETS['graph'])
    assert answer['behaviour'] not in ('abstain', 'abstain_with_reason'), answer
    assert 'can mean the year to date through' not in json.dumps(answer, default=str), answer
    assert 'Running total' in answer['answer_text'] and 'June 2025' in answer['answer_text'], answer
