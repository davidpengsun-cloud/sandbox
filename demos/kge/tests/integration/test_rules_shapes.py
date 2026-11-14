"""Issue 421 shape sets: every returned cell reconciles to independently authored SQL."""
import json
from collections import Counter
from decimal import Decimal
from datetime import date
from itertools import product
from functools import lru_cache

import pytest

from kge.pipeline.settings import ROOT
from kge.semantics.plan import Propose, QueryPlan
from kge.semantics.plancheck import CheckedPlan, check_plan
from kge.services.ask.engine import planmodel, planrules
from kge.services.ask.engine.core import PRESETS, parse
from kge.services.ask.engine.planner import Planner
from kge.services.ask.engine.wording import Hidden
from tests.integration.test_ask_gates import comparison_engine as comparison_engine
from tests.integration.test_context_scope import vocabulary
from tests.integration.test_plan_wording_round2 import Recording, sql


CASES = json.loads((ROOT / 'tests/fixtures/rules_shapes.json').read_text())
FLOW_FILTER_CASES = [{**case, 'id': case['id'] + '-' + filter, 'filter': filter,
    'question': case['question'] + suffix} for case in CASES if case['id'] in ('parts-10', 'parts-11', 'parts-12', 'parts-13')
    for filter, suffix in [('region', ' for the NORTHEAST region'), ('affluent', ' for segment level 5 AFFLUENT')]]
DIMENSIONS = {'BRCH_CD': 'branch', 'RGN_CD': 'region', 'SEGMT_LVL_ID6': 'segment',
              'month': 'month', 'calendar_quarter': 'quarter', 'year': 'year'}
REVIEW_PERIOD_CASES = [next(c for c in CASES if c['question'] == question) for question in (
    'percent change in NNA from Q1 2025 to Q2 2025',
    'percent change in NNA from Q1 to Q2 2025',
    'change in NNA from March to April 2025',
    'NNA for Q2 2025 vs the previous period',
    'year over year change in NNA for Q2 2025',
)]


def independent(eng, context, dims, window, filter=None, direction=None, scope=None):
    return dict(_independent(eng, context, tuple(dims), tuple(window), filter, direction, tuple(dims if scope is None else scope)))


@lru_cache(maxsize=2048)
def _independent(eng, context, dims, window, filter, direction, scope):
    finance = context in ('nna_certified', 'nna_client_directed')
    branch = context == 'nna_branch_sales'
    table = 'F_NNA_FIN' if finance else 'TRAN_FACT_EXCL_MFCLR_G' if branch else 'TRAN_FACT_1'
    release = 'f.DW_TRAN_MNTH_END_DT' if finance else 'f.MNTH_END_DT'
    period = release if finance else 'f.DW_TRAN_DT'
    amount = 'f.NNA_AM' if finance else "CASE WHEN f.DW_TRAN_DIRCTN_CD='O' THEN -f.TRAN_AM ELSE f.TRAN_AM END"
    account = f'{release} BETWEEN a.DW_EFF_DT AND a.DW_EXPR_DT' if finance or branch else 'a.CURR_IN<>0'
    segment = f'{release} BETWEEN r.DW_EFF_DT AND r.DW_EXPR_DT' if finance or branch else 'r.CURR_IN<>0'
    source = f'FROM {table} f JOIN MONTH_DIM_1 m ON {release}=m.CLDR_EOM_DT '
    if branch or set(scope) & {'branch', 'region', 'segment'} or filter:
        source += f' JOIN ACCT_DIM2_1 a ON f.DW_ACCT_ID=a.DW_ACCT_ID AND f.ACCT_SRCE_SYS_ID=a.SRCE_SYS_ID AND {account}'
    if branch or 'segment' in scope or filter == 'affluent':
        source += f' JOIN CLIENT_CD_ROLLUP_1 r ON a.CLNT_CD=r.CLNT_CD AND {segment}'
    if branch or set(scope) & {'branch', 'region'} or filter == 'region':
        source += ' JOIN BRCH_DIM_1 b ON a.ACCT_DOMCL_BRCH_CD=b.BRCH_CD AND b.CURR_IN<>0'
    source += f" WHERE m.DW_BRKRG_MNTH_FRM_TODAY_CT<>0 AND {period}>=DATE '{window[0]}' AND {period}<DATE '{window[1]}' "
    if context == 'nna_certified':
        source += " AND f.FRZN_IN='Y'"
    elif context == 'nna_client_directed':
        source += " AND f.CLIENT_DIRECTED_NNA_IN='Y'"
    else:
        source += " AND f.NNA_IN='Y' AND f.RCV_IN=1"
        if branch:
            source += ''' AND m.FIN_MTRC_RLSE_DT BETWEEN f.DW_EFF_DT AND f.DW_EXPR_DT
AND f.ACCT_SRCE_SYS_ID IN (100,200) AND r.SEGMT_LVL_ID5='AFFLUENT' AND upper(b.BRCH_DIVSN_CD)='BR'
AND upper(coalesce(f.DW_TRAN_TYPE_CD,'')) NOT IN ('AQ','DT')'''
        else:
            source += ''' AND f.CURR_IN=1 AND NOT EXISTS (SELECT 1 FROM ACCT_DIM2_1 x
WHERE x.DW_ACCT_ID=f.DW_ACCT_ID AND x.SRCE_SYS_ID=f.ACCT_SRCE_SYS_ID AND x.TEST_ACCT_IN='Y')'''
    if filter == 'region':
        source += " AND b.RGN_CD='NORTHEAST'"
    elif filter == 'affluent':
        source += " AND r.SEGMT_LVL_ID5='AFFLUENT'"
    if direction:
        source += f" AND f.DW_TRAN_DIRCTN_CD='{direction}'"
    expressions = {'month': f"strftime(date_trunc('month',{period}),'%Y-%m-%d')",
        'quarter': f"strftime(date_trunc('quarter',{period}),'%Y-%m-%d')",
        'year': f"strftime(date_trunc('year',{period}),'%Y-%m-%d')", 'branch': 'b.BRCH_CD', 'region': 'b.RGN_CD', 'segment': 'r.SEGMT_LVL_ID6'}
    columns = [expressions[d] for d in dims]
    query = 'SELECT ' + ', '.join([*columns, 'COALESCE(SUM(' + amount + '),0)']) + ' ' + source
    if dims:
        query += ' GROUP BY ' + ','.join(str(i + 1) for i in range(len(dims)))
    return {tuple(row[:-1]): row[-1] for row in sql(eng, query)}


def assert_public_current_certified_monthly(eng, answer):
    assert answer['behaviour'] == 'combined', answer
    case = {'id': 'public-current-certified-monthly', 'set': 'context',
            'contexts': ['nna', 'nna_certified'], 'dimensions': ['month'],
            'window': ['2023-07-01', '2025-07-01'], 'changes': False}
    reply = Propose(kind='propose', base_turn=None, plan=QueryPlan.model_validate(answer['history'][-1]['plan']))
    assert reply.plan.time.kind == 'all_available'
    assert_result(eng, case, reply, {'kind': 'result', 'guard': answer['guard'], 'result': answer['result']})


def spine(expected, tags, window):
    temporal = [i for i, tag in enumerate(tags) if tag in ('month', 'quarter', 'year')]
    if not temporal:
        return
    partitions = {tuple(value for i, value in enumerate(key) if i not in temporal) for rows in expected.values() for key in rows}
    periods = []
    first, last = map(date.fromisoformat, window)
    for i in temporal:
        grain = tags[i]
        step = 1 if grain == 'month' else 3 if grain == 'quarter' else 12
        current = first.replace(month=((first.month - 1) // step) * step + 1, day=1)
        values = []
        while current < last:
            values.append(current.isoformat())
            month = current.month - 1 + step
            current = date(current.year + month // 12, month % 12 + 1, 1)
        periods.append(values)
    for partition, timing in product(partitions, product(*periods)):
        selected, moment = iter(partition), iter(timing)
        key = tuple(next(moment) if i in temporal else next(selected) for i in range(len(tags)))
        for rows in expected.values():
            rows.setdefault(key, Decimal(0))


def proposal(eng, case):
    text = case['question']
    v = vocabulary(eng, text)
    req = parse(text, eng.term_names, eng)
    reply = planrules.read(eng, req, v, PRESETS['graph'])
    assert reply.kind == 'propose', (case['id'], reply)
    checked = check_plan(reply.plan, v, text)
    assert isinstance(checked, CheckedPlan), (case['id'], checked)
    return v, reply


def dimensional_tags(plan):
    return [DIMENSIONS[d.grain if d.kind == 'time' else d.bindings[0].column.id.rsplit('.', 1)[1]] for d in plan.dimensions]


def assert_result(eng, case, reply, result):
    assert result['kind'] == 'result' and result['guard']['passed'], (case['id'], result)
    plan = reply.plan
    assert set(m.context.id for m in plan.measures) == set(case['contexts'])
    tags = dimensional_tags(plan)
    assert set(tags) == set(case['dimensions']) and len(tags) == len(case['dimensions'])
    if case['set'] == 'context':
        comparison, = plan.comparisons
        measures = {m.id: m.context.id for m in plan.measures}
        assert [measures[comparison.left], measures[comparison.right]] == case['contexts']
        assert {d.kind for d in plan.derived} == ({'difference', 'percent_change'} if case['changes'] else set())
    if case.get('ratio'):
        derived, = plan.derived
        measures = {m.id: m.context.id for m in plan.measures}
        assert derived.kind == 'ratio'
        assert [measures[derived.numerator], measures[derived.denominator]] == case['contexts']
    expected = {ctx: independent(eng, ctx, tags, case['window'], case.get('filter')) for ctx in case['contexts']}
    spine(expected, tags, case['window'])
    prior = independent(eng, case['contexts'][0], tags, case['prior']) if 'prior' in case else {}
    part_values = {direction: independent(eng, case['contexts'][0], tags, case['window'], case.get('filter'), direction=direction)
                   for direction in case.get('parts', ())}
    columns = [c['id'] for c in result['result']['columns'] if c['kind'] != 'dimension']
    details = [row for row in result['result']['rows'] if not row.get('total')]
    assert details
    if case['set'] == 'ranking':
        assert plan.limit == case['limit'] and plan.order[0].direction == case['direction']
        ranked = sorted(expected[case['contexts'][0]].items(), key=lambda pair: pair[1], reverse=case['direction'] == 'desc')[:case['limit']]
        assert {tuple(d['value'] for d in row['dimensions']) for row in details} == {key for key, _ in ranked}
    else:
        assert {tuple(d['value'] for d in row['dimensions']) for row in details} == set().union(*(set(rows) for rows in expected.values()))
    for row in result['result']['rows']:
        key = tuple(d.get('value') for d in row['dimensions'])
        subtotal_tags = [tag for tag, value in zip(tags, key) if value is not None] if row.get('total') else []
        subtotal_key = tuple(value for value in key if value is not None)
        values = {m.id: (independent(eng, m.context.id, subtotal_tags, case['window'], case.get('filter'), scope=tags).get(subtotal_key, Decimal(0))
                        if row.get('total') else expected[m.context.id].get(key)) for m in plan.measures}
        if part_values:
            assert len(plan.measures) == len(case['parts'])
            for m, direction in zip(plan.measures, case['parts'], strict=True):
                values[m.id] = (independent(eng, m.context.id, [], case['window'], case.get('filter'), direction=direction)[()]
                                if row.get('total') else part_values[direction].get(key))
        for c in plan.comparisons:
            if c.kind == 'contexts':
                values.update({c.id + '/base': values[c.left], c.id + '/other': values[c.right]})
            else:
                for m in plan.measures:
                    values[c.id + '/' + m.id + '/base'] = values[m.id]
                    values[c.id + '/' + m.id + '/other'] = (independent(eng, m.context.id, [], case['prior'])[()]
                        if row.get('total') else prior.get(key))
        for d in plan.derived:
            a, b = values[d.numerator], values[d.denominator]
            values[d.id] = None if a is None or b is None or d.kind != 'difference' and b == 0 else (
                a - b if d.kind == 'difference' else a / abs(b) if d.kind == 'ratio' else (a - b) / abs(b))
        for identifier, cell in zip(columns, row['cells'], strict=True):
            expected_cell = values[identifier]
            if expected_cell is None:
                assert cell['value']['kind'] == 'null'
            else:
                actual = Decimal(str(cell['value']['value']))
                if identifier in {d.id for d in plan.derived if d.kind != 'difference'}:
                    assert abs(actual - expected_cell) < Decimal('0.000000000001'), (case['id'], key, identifier, actual, expected_cell)
                else:
                    assert actual == expected_cell, (case['id'], key, identifier, actual, expected_cell)
    if tags:
        totals = [row for row in result['result']['rows'] if row.get('total')]
        assert len([row for row in totals if all(d['kind'] == 'null' for d in row['dimensions'])]) == 1
        time_tags = [tag for tag in tags if tag in ('month', 'quarter', 'year')]
        assert len(totals) == (1 + len(independent(eng, case['contexts'][0], time_tags, case['window'])) if time_tags and len(time_tags) < len(tags) else 1)


@pytest.mark.parametrize('case', [*CASES, *FLOW_FILTER_CASES], ids=lambda c: c['id'])
@pytest.mark.parametrize('modeled', [False, True], ids=['rules', 'model-replay'])
def test_shape_sets_reconcile_every_cell_to_independent_sql(eng, case, modeled):
    v, reply = proposal(eng, case)
    if modeled:
        raw = reply.plan.model_dump(mode='json', by_alias=True)
        raw['obligations'] = []
        reply = planmodel.read(Recording(Propose(kind='propose', base_turn=None, plan=QueryPlan.model_validate(raw))), case['question'], v, grounding=True)
    answer = Planner(v, eng.w, Hidden([], set(), [])).answer(case['question'], reply, grounding=modeled)
    assert_result(eng, case, reply, answer)


def test_shape_fixture_sizes():
    assert Counter(c['set'] for c in CASES) == dict(time=40, context=30, ranking=15, period=35, parts=15)
    assert len({c['id'] for c in CASES}) == 135


@pytest.mark.parametrize('case', REVIEW_PERIOD_CASES, ids=lambda c: c['id'])
def test_review_period_wordings_answer_with_fixed_pilot_job_budget(eng, comparison_engine, case):
    from tests.bigquery_fake import budgets
    active, fake = comparison_engine
    vocabulary(active, case['question'])
    if fake:
        fake.requests.clear()
    answer = active.answer(case['question'], 'analyst_enterprise')
    assert answer['behaviour'] == 'combined', answer
    reply = Propose(kind='propose', base_turn=None, plan=QueryPlan.model_validate(answer['history'][-1]['plan']))
    assert_result(eng, case, reply, {'kind': 'result', 'guard': answer['guard'], 'result': answer['result']})
    if fake:
        assert budgets(fake) == dict(graph=0, retrieval=0, warehouse=2, dry=2, catalogue=0)


@pytest.mark.parametrize('case', REVIEW_PERIOD_CASES, ids=lambda c: c['id'])
def test_review_period_wordings_decline_swapped_or_wrong_prior(eng, comparison_engine, case):
    from tests.bigquery_fake import budgets
    active, fake = comparison_engine
    v, reply = proposal(active, case)
    raw = reply.plan.model_dump(mode='json', by_alias=True)
    comparison, = raw['comparisons']
    if comparison['kind'] == 'explicit_period':
        raw['time'], comparison['other'] = comparison['other'], raw['time']
    else:
        comparison['kind'] = 'previous_year' if comparison['kind'] == 'previous_period' else 'previous_period'
    raw['obligations'] = []
    reply = planmodel.read(Recording(Propose(kind='propose', base_turn=None, plan=QueryPlan.model_validate(raw))), case['question'], v, grounding=True)
    if fake:
        fake.requests.clear()
    answer = Planner(v, active.w, Hidden([], set(), [])).answer(case['question'], reply, grounding=True)
    assert answer['kind'] == 'abstain', answer
    assert not answer.get('result')
    if fake:
        assert budgets(fake) == dict(graph=0, retrieval=0, warehouse=0, dry=0, catalogue=0)


def test_gross_inflows_clarifies_declared_measures_and_keeps_direction(eng, comparison_engine):
    from kge.semantics.plan import HistoryEntry
    from tests.bigquery_fake import budgets
    active, fake = comparison_engine
    question = 'gross inflows for Q1 2025'
    v = vocabulary(active, question)
    if fake:
        fake.requests.clear()
    answer = active.answer(question, 'analyst_enterprise')
    assert answer['behaviour'] == 'clarify', answer
    choices = answer['clarification']['choices']
    assert {c['id'] for c in choices} == {'nna', 'nna_certified', 'nna_client_directed', 'nna_branch_sales',
                                         'flow_amount', 'branch_sales_flow_amount'}
    labels = [c['label'] for c in choices]
    assert len(set(labels)) == len(labels)
    assert all(c['label'] == v.contexts[c['id']].label for c in choices)
    assert all('DW_TRAN_DIRCTN_CD' not in label for label in labels)
    if fake:
        assert budgets(fake) == dict(graph=0, retrieval=0, warehouse=0, dry=0, catalogue=0)
    choice = next(c for c in choices if c['id'] == 'nna')
    followup = question.replace('gross', choice['label'])
    history = [HistoryEntry.model_validate(h) for h in answer['history']]
    if fake:
        fake.requests.clear()
    resolved = active.answer(followup, 'analyst_enterprise', history=history)
    assert resolved['behaviour'] == 'figure', (resolved.get('reason'), resolved.get('trace'))
    expected = independent(eng, 'nna', [], ['2025-01-01', '2025-04-01'], direction='I')[()]
    assert Decimal(str(resolved['figure'])) == expected
    if fake:
        assert budgets(fake) == dict(graph=0, retrieval=0, warehouse=2, dry=2, catalogue=0)


def test_gross_inflows_does_not_offer_unreadable_measures(comparison_engine):
    from tests.bigquery_fake import budgets
    active, fake = comparison_engine
    for graph_jobs in (2, 0):
        if fake:
            fake.requests.clear()
        answer = active.answer('gross inflows for Q1 2025', 'analyst_retail')
        assert answer['behaviour'] in ('abstain', 'refuse'), answer
        assert not answer.get('result') and not answer.get('clarification')
        if fake:
            assert budgets(fake) == dict(graph=graph_jobs, retrieval=0, warehouse=0, dry=0, catalogue=0)


@pytest.mark.parametrize('question', [
    'certified NNA for Q1 2025 by month',
    'certified NNA for January through March 2025',
])
def test_public_acceptance_figures(eng, question):
    answer = eng.answer(question, 'analyst_enterprise')
    assert answer['behaviour'] == 'figure', answer
    if 'by month' in question:
        rows = answer['result']['rows']
        assert [Decimal(row['cells'][0]['value']['value']) for row in rows if not row.get('total')] == [
            Decimal('5831305.25'), Decimal('6598492.05'), Decimal('5637481.28')]
        assert Decimal(next(row for row in rows if row.get('total'))['cells'][0]['value']['value']) == Decimal('18067278.58')
    else:
        assert answer['figure'] == 18067278.58


@pytest.mark.parametrize('backend', ['local', 'pilot'])
@pytest.mark.parametrize('operands', ['Current NNA vs certified NNA', 'certified NNA vs current NNA'])
def test_comparison_filters_stay_with_their_context_after_reordering(eng, monkeypatch, tmp_path, backend, operands):
    from tests.bigquery_fake import pilot, budgets
    fake = twin = None
    engines = []
    active = eng
    question = operands + ' for Q1 2025 for the NORTHEAST region'
    expected = {context: independent(eng, context, [], ['2025-01-01', '2025-04-01'], 'region')[()]
                for context in ('nna', 'nna_certified')}
    if backend == 'pilot':
        fake, twin, opened, engines = pilot(monkeypatch, tmp_path)
        active = opened()
    try:
        vocabulary(active, question)
        if fake:
            fake.requests.clear()
        answer = active.answer(question, 'analyst_enterprise')
        assert answer['behaviour'] == 'combined', answer
        measures = answer['history'][-1]['plan']['measures']
        row, = [row for row in answer['result']['rows'] if not row.get('total')]
        columns = [column['id'] for column in answer['result']['columns'] if column['kind'] != 'dimension']
        cells = {identifier: Decimal(str(cell['value']['value'])) for identifier, cell in zip(columns, row['cells'], strict=True)}
        assert {measure['context']['id']: cells[measure['id']] for measure in measures} == expected
        if fake:
            counts = budgets(fake)
            assert counts == dict(graph=0, retrieval=0, warehouse=2, dry=2, catalogue=0)
    finally:
        for engine in engines:
            engine.close()
        if twin and twin.conn is not None:
            twin.conn.close()
            twin.db.close()
        if fake:
            fake.con.close()
