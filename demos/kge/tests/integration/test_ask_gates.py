"""Question scopes bind adversarial plans before any governed execution."""
from copy import deepcopy
from decimal import Decimal

import pytest

from kge.semantics.coverage import ledger
from kge.semantics.plan import HistoryEntry, QueryPlan
from kge.semantics.plancheck import CheckedPlan, check_plan
from kge.semantics.planhistory import replacement
from kge.services.ask.engine.planmodel import read
from kge.services.ask.engine.planner import Planner
from kge.services.ask.engine.wording import Hidden
from tests.integration.test_context_scope import vocabulary
from tests.integration.test_plan_vocabulary_round3 import DIRECTED, FINANCE, single, window
from tests.integration.test_plan_wording_round2 import count, dimension, predicate, sql
from tests.integration.test_wealth_context_filters import measure
from tests.plan_fixtures import grounded, model, name


def interpreted(plan, text, vocab, history=(), turn='turn'):
    class Scripted:
        def json(self, prompt):
            return dict(kind='propose', base_turn=history[-1].turn_id if history else None,
                        plan=plan.model_dump(mode='json', by_alias=True))
    reply = read(Scripted(), text, vocab, history, turn)
    assert reply.kind == 'propose', reply
    inherited = ()
    if history:
        problem, inherited = replacement(reply, history, vocab, text, turn)
        assert problem is None, problem
    return reply, check_plan(reply.plan, vocab, text, turn, inherited)


def answered(eng, plan, text, vocab, history=(), turn='turn', expected_totals=None):
    reply, checked = interpreted(plan, text, vocab, history, turn)
    assert isinstance(checked, CheckedPlan), checked
    result = Planner(vocab, eng.w, Hidden([], set(), [])).answer(text, reply, turn=turn, history=history)
    assert result['kind'] == 'result' and result['guard']['passed'], result
    if expected_totals is not None:
        totals = [row for row in result['result']['rows'] if row.get('total')]
        assert len(totals) == 1
        values = {col['id']: Decimal(cell['value']['value']) for col, cell in zip(
            [c for c in result['result']['columns'] if c['kind'] != 'dimension'], totals[0]['cells'])}
        for identifier, value in expected_totals.items():
            assert values[identifier] == value
    return {tuple(d['value'] for d in row['dimensions']): {
        col['id']: Decimal(cell['value']['value']) for col, cell in zip(
            [c for c in result['result']['columns'] if c['kind'] != 'dimension'], row['cells'])}
        for row in result['result']['rows'] if not row.get('total')}


def monthly():
    return dict(kind='time', id='d', calendar=name('calendar'), grain='month')


@pytest.fixture(params=['local', 'native'])
def comparison_engine(request, monkeypatch, tmp_path):
    if request.param == 'local':
        yield request.getfixturevalue('eng'), None
        return
    from tests.integration.test_planner_request_jobs import pilot
    fake, twin, opened, engines = pilot(monkeypatch, tmp_path)
    try:
        yield opened(), fake
    finally:
        for engine in engines:
            engine.close()
        if twin.conn is not None:
            twin.conn.close()
            twin.db.close()
        fake.con.close()


@pytest.mark.parametrize('operator', ['versus', 'vs', 'compared with', 'against'])
@pytest.mark.parametrize('reverse', [False, True])
def test_context_comparison_uses_question_order_and_reconciles(eng, operator, reverse):
    labels = ['NNA', 'Certified NNA']
    contexts = ['nna', 'nna_certified']
    if reverse:
        labels.reverse()
        contexts.reverse()
    text = f'{labels[0]} {operator} {labels[1]} by month from April to June 2025 with difference'
    v = vocabulary(eng, text)
    assignments = {labels[0]: 'a', labels[1]: 'b', operator: 'c', 'by': 'd', 'month': 'd',
                   'April': 'time', 'June 2025': 'time', 'difference': 'delta'}
    plan = grounded(model(measures=[measure(v, contexts[0], 'a'), measure(v, contexts[1], 'b')],
        time=window(), dimensions=[monthly()], comparisons=[dict(kind='contexts', id='c', left='a', right='b', evidence=[])],
        derived=[dict(kind='difference', id='delta', numerator='c/base', denominator='c/other', evidence=[])]), text, v, assignments)
    data = plan.model_dump(mode='json', by_alias=True)
    data['comparisons'][0].update(left='b', right='a')
    assert interpreted(QueryPlan.model_validate(data), text, v)[1].kind == 'abstain'
    actual = answered(eng, plan, text, v)
    current = """FROM TRAN_FACT_1 f JOIN MONTH_DIM_1 m ON f.MNTH_END_DT=m.CLDR_EOM_DT
WHERE m.DW_BRKRG_MNTH_FRM_TODAY_CT<>0 AND f.NNA_IN='Y' AND f.RCV_IN=1 AND f.CURR_IN=1
AND NOT EXISTS (SELECT 1 FROM ACCT_DIM2_1 a WHERE a.DW_ACCT_ID=f.DW_ACCT_ID
AND a.SRCE_SYS_ID=f.ACCT_SRCE_SYS_ID AND a.TEST_ACCT_IN='Y')"""
    expected = {}
    for context in contexts:
        query = ('SELECT f.MNTH_END_DT, SUM(CASE WHEN f.DW_TRAN_DIRCTN_CD=\'O\' THEN -f.TRAN_AM ELSE f.TRAN_AM END) ' + current
                 if context == 'nna' else 'SELECT f.DW_TRAN_MNTH_END_DT, SUM(f.NNA_AM) ' + FINANCE)
        period = 'f.MNTH_END_DT' if context == 'nna' else 'f.DW_TRAN_MNTH_END_DT'
        expected[context] = {day.strftime('%Y-%m-01'): amount for day, amount in sql(eng, query
            + f" AND {period} BETWEEN DATE '2025-04-01' AND DATE '2025-06-30' GROUP BY 1")}
    assert actual
    for (month,), cells in actual.items():
        left, right = (expected[context][month] for context in contexts)
        assert cells['c/base'] == left and cells['c/other'] == right and cells['delta'] == left - right


@pytest.mark.parametrize('whole', ['', ' of the firm', ' of the total', ' of all'])
def test_share_denominator_keeps_named_segment_unless_whole_population(eng, whole):
    text = f'SPS contribution{whole} to client-directed NNA for May 2025 for segment level 5 PRIVATE'
    v = vocabulary(eng, text)
    ctx = v.contexts['nna_client_directed']
    segment = predicate('wealth.CLIENT_CD_ROLLUP_1.SEGMT_LVL_ID5', 'f', 'PRIVATE', ['finance_account', 'account_client_code'])
    plan = grounded(model(measures=[measure(v, ctx.id)], time=single(), filters=[segment],
        dimensions=[dimension(ctx, 'wealth.ACCT_DIM2_1.ACCT_RELT_SPS_IN', ['finance_account'])],
        derived=[dict(kind='share', id='s', input='m', remove_dimensions=['d'], remove_filters=['f'] if whole else [], evidence=[])]),
        text, v, {'SPS': 'd', 'contribution': 's', 'client-directed NNA': 'm', 'May 2025': 'time', 'segment level 5 PRIVATE': 'f'})
    if not whole:
        data = plan.model_dump(mode='json', by_alias=True)
        data['derived'][0]['remove_filters'] = ['f']
        assert interpreted(QueryPlan.model_validate(data), text, v)[1].kind == 'abstain'
    actual = answered(eng, plan, text, v)
    expected = dict(sql(eng, 'SELECT a.ACCT_RELT_SPS_IN, SUM(f.NNA_AM) ' + DIRECTED
                        + " AND f.DW_TRAN_MNTH_END_DT=DATE '2025-05-31' AND r.SEGMT_LVL_ID5='PRIVATE' GROUP BY 1"))
    total = sql(eng, 'SELECT SUM(f.NNA_AM) ' + DIRECTED + " AND f.DW_TRAN_MNTH_END_DT=DATE '2025-05-31'"
                + ('' if whole else " AND r.SEGMT_LVL_ID5='PRIVATE'"))[0][0]
    assert actual and total
    for (flag,), cells in actual.items():
        assert cells['m'] == expected[flag]
        assert cells['s'] == pytest.approx(expected[flag] / total)


def segment_outputs(v):
    outputs = [measure(v, 'nna_certified', 'a'), measure(v, 'nna_client_directed', 'b')]
    outputs += [count(v.contexts['nna_certified'], 'wealth_account', 'n')]
    return outputs


@pytest.mark.parametrize('omitted', ['a', 'b', 'n'])
def test_first_question_segment_covers_every_measure_and_account_count(eng, omitted):
    text = 'Certified NNA and client-directed NNA with account counts by account product code for May 2025 for segment level 5 PRIVATE'
    v = vocabulary(eng, text)
    outputs = segment_outputs(v)
    contexts = ['nna_certified', 'nna_client_directed']
    dim = dimension(v.contexts[contexts[0]], 'wealth.ACCT_DIM2_1.ACCT_DETL_PROD_CD', ['finance_account'])
    dim['bindings'].append({**deepcopy(dim['bindings'][0]), 'context': name(contexts[1])})
    assignments = {'Certified NNA': 'a', 'client-directed NNA': 'b', 'account': 'n', 'counts': 'n',
                   'by account product code': 'd', 'May 2025': 'time', 'segment level 5 PRIVATE': ['fa', 'fb', 'fn']}
    for output in outputs:
        output['filters'] = [predicate('wealth.CLIENT_CD_ROLLUP_1.SEGMT_LVL_ID5', 'f' + output['id'], 'PRIVATE',
                                       ['finance_account', 'account_client_code'])]
    plan = grounded(model(measures=outputs, dimensions=[dim], time=single()), text, v, assignments)
    data = plan.model_dump(mode='json', by_alias=True)
    next(m for m in data['measures'] if m['id'] == omitted)['filters'] = []
    for obligation in data['obligations']:
        obligation['targets'] = [t for t in obligation['targets'] if t != 'f' + omitted]
    assert interpreted(QueryPlan.model_validate(data), text, v)[1].kind == 'abstain'
    actual = answered(eng, plan, text, v)
    expected = {}
    for context, source in zip(contexts, [FINANCE, DIRECTED]):
        expected[context] = {code: (amount, accounts) for code, amount, accounts in sql(eng,
            'SELECT a.ACCT_DETL_PROD_CD, SUM(f.NNA_AM), COUNT(DISTINCT (a.DW_ACCT_ID,a.SRCE_SYS_ID)) ' + source
            + " AND f.DW_TRAN_MNTH_END_DT=DATE '2025-05-31' AND r.SEGMT_LVL_ID5='PRIVATE' GROUP BY 1")}
    assert actual
    for (code,), cells in actual.items():
        assert cells['a'] == expected[contexts[0]][code][0]
        assert cells['b'] == expected[contexts[1]][code][0]
        assert cells['n'] == expected[contexts[0]][code][1]


@pytest.mark.parametrize('wrong_part', ['i', 'o', 'net'])
def test_adjacent_modifier_keeps_all_measure_parts_in_one_context(eng, wrong_part):
    text = 'Certified client-directed NNA inflows, outflows and net for May 2025'
    v = vocabulary(eng, text)
    assert [p.candidates for p in ledger(text, 'turn', v) if p.kind == 'name'] == [('nna_client_directed',)]
    outputs = [measure(v, 'nna_client_directed', identifier) for identifier in ('i', 'o', 'net')]
    for output, direction in zip(outputs, ['I', 'O']):
        output['filters'] = [predicate('wealth.F_NNA_FIN.DW_TRAN_DIRCTN_CD', 'f' + output['id'], direction, [])]
    frozen = predicate('wealth.F_NNA_FIN.FRZN_IN', 'frozen', 'Y')
    plan = grounded(model(measures=outputs, time=single(), filters=[frozen]), text, v,
        {'Certified': 'frozen', 'client-directed NNA': ['i', 'o', 'net'], 'inflows': 'fi', 'outflows': 'fo', 'net': 'net', 'May 2025': 'time'})
    data = plan.model_dump(mode='json', by_alias=True)
    next(m for m in data['measures'] if m['id'] == wrong_part)['context'] = name('nna_certified')
    assert interpreted(QueryPlan.model_validate(data), text, v)[1].kind == 'abstain'
    cells = next(iter(answered(eng, plan, text, v).values()))
    expected = dict(sql(eng, 'SELECT f.DW_TRAN_DIRCTN_CD, SUM(f.NNA_AM) ' + DIRECTED
                        + " AND f.FRZN_IN='Y' AND f.DW_TRAN_MNTH_END_DT=DATE '2025-05-31' GROUP BY 1"))
    assert cells['i'] == expected['I'] and cells['o'] == expected['O']
    assert cells['net'] == expected['I'] + expected['O']


def test_followup_preserves_resolved_segment_and_only_adds_account_flag(eng):
    text = 'Certified NNA and client-directed NNA by month from April to June 2025 for segment level 5 AFFLUENT'
    v = vocabulary(eng, text)
    plan = grounded(model(measures=segment_outputs(v)[:2], dimensions=[monthly()], time=window(),
        filters=[predicate('wealth.CLIENT_CD_ROLLUP_1.SEGMT_LVL_ID5', 'segment', 'AFFLUENT', ['finance_account', 'account_client_code'])]),
        text, v, {'Certified NNA': 'a', 'client-directed NNA': 'b', 'by': 'd', 'month': 'd', 'April': 'time',
                  'June 2025': 'time', 'segment level 5 AFFLUENT': 'segment'})
    answered(eng, plan, text, v)
    history = (HistoryEntry(turn_id='turn', question=text, plan=plan, clarification=None, binding=v.binding),)
    followup = 'Now just SPS accounts'
    data = plan.model_dump(mode='json', by_alias=True)
    data['filters'].append(predicate('wealth.ACCT_DIM2_1.ACCT_RELT_SPS_IN', 'flag', 'Y', ['finance_account']))
    piece, = ledger(followup, 'next', v)
    data['obligations'].append(dict(id='added', spans=[piece.span.model_dump()], kind='name', targets=['flag'], state='applied'))
    actual = answered(eng, QueryPlan.model_validate(data), followup, v, history, 'next')
    wrong = deepcopy(data)
    wrong['filters'][0]['value']['value'] = 'PRIVATE'
    class Contradicted:
        def json(self, prompt):
            return dict(kind='propose', base_turn='turn', plan=wrong)
    reply = read(Contradicted(), followup, v, history, 'next')
    assert replacement(reply, history, v, followup, 'next')[0].kind == 'abstain'
    assert actual
    for identifier, source in [('a', FINANCE), ('b', DIRECTED)]:
        expected = {day.strftime('%Y-%m-01'): value for day, value in sql(eng,
            'SELECT f.DW_TRAN_MNTH_END_DT, SUM(f.NNA_AM) ' + source
            + " AND f.DW_TRAN_MNTH_END_DT BETWEEN DATE '2025-04-01' AND DATE '2025-06-30'"
            + " AND r.SEGMT_LVL_ID5='AFFLUENT' AND a.ACCT_RELT_SPS_IN='Y' GROUP BY 1")}
        assert {month: cells[identifier] for (month,), cells in actual.items()} == expected


@pytest.mark.parametrize('text', [
    'Compare current vs. certified Q2 2025 NNA by month, with the dollar and percent difference.',
    'Show current versus certified NNA for each month of Q2 2025, with the dollar and percent difference.',
])
def test_mode_operand_comparison_answers_reference_wordings_and_rejects_swapped_sides(comparison_engine, text):
    from tests.integration.test_planner_request_jobs import budgets
    eng, fake = comparison_engine
    v = vocabulary(eng, text)
    plan = grounded(model(measures=[measure(v, 'nna', 'a'), measure(v, 'nna_certified', 'b')],
        time=dict(kind='single', period=dict(kind='calendar_quarter', value='2025-Q2', calendar=name('calendar'))),
        dimensions=[monthly()], comparisons=[dict(kind='contexts', id='c', left='a', right='b', evidence=[])],
        derived=[dict(kind=kind, id=identifier, numerator='c/base', denominator='c/other', evidence=[])
                 for kind, identifier in [('difference', 'delta'), ('percent_change', 'pct')]]), text, v,
        {piece.text: 'time' if piece.kind == 'time' else 'c' if piece.operation == 'comparison' else
         'a' if piece.operation == 'current' else 'd' if piece.operation in ('dimension', 'month') else
         'delta' if piece.operation == 'difference' else 'pct' if piece.operation == 'percent_change' else 'b'
         for piece in ledger(text, 'turn', v)})
    data = plan.model_dump(mode='json', by_alias=True)
    data['comparisons'][0].update(left='b', right='a')
    if fake is not None:
        fake.requests.clear()
    assert interpreted(QueryPlan.model_validate(data), text, v)[1].kind == 'abstain'
    if fake is not None:
        assert budgets(fake) == dict(graph=0, retrieval=0, warehouse=0, dry=0, catalogue=0)
    for context in v.contexts.values():
        if context.id == 'nna':
            continue
        wrong_context = plan.model_dump(mode='json', by_alias=True)
        wrong_context['measures'][0].update(context=name(context.id), measure=name(context.measure))
        reply, decision = interpreted(QueryPlan.model_validate(wrong_context), text, v)
        assert decision.kind == 'abstain', context.id
        assert Planner(v, eng.w, Hidden([], set(), [])).answer(text, reply)['kind'] == 'abstain', context.id
        if fake is not None:
            assert budgets(fake) == dict(graph=0, retrieval=0, warehouse=0, dry=0, catalogue=0)
    actual = answered(eng, plan, text, v, expected_totals={
        'c/base': Decimal('22007986.08'), 'c/other': Decimal('22007986.08'), 'delta': Decimal(0), 'pct': Decimal(0)})
    if fake is not None:
        assert budgets(fake) == dict(graph=0, retrieval=0, warehouse=2, dry=2, catalogue=0)
    expected = {('2025-04-01',): Decimal('7384872.28'), ('2025-05-01',): Decimal('7975551.02'),
                ('2025-06-01',): Decimal('6647562.78')}
    assert actual.keys() == expected.keys()
    for month, cells in actual.items():
        assert cells['c/base'] == cells['c/other'] == expected[month]
        assert cells['delta'] == cells['pct'] == 0


def test_certified_modifier_scopes_the_amount_and_its_account_count(comparison_engine):
    from tests.integration.test_planner_request_jobs import budgets
    eng, fake = comparison_engine
    text = 'Certified client-directed NNA with account counts for AFFLUENT March 2025'
    v = vocabulary(eng, text)
    outputs = [measure(v, 'nna_client_directed'), count(v.contexts['nna_client_directed'], 'wealth_account', 'n')]
    for output in outputs:
        output['filters'] = [predicate('wealth.F_NNA_FIN.FRZN_IN', 'frozen-' + output['id'], 'Y')]
    plan = grounded(model(measures=outputs, time=single('2025-03'), filters=[
        predicate('wealth.CLIENT_CD_ROLLUP_1.SEGMT_LVL_ID5', 'segment', 'AFFLUENT', ['finance_account', 'account_client_code'])]),
        text, v, {'Certified': ['frozen-m', 'frozen-n'], 'client-directed NNA': 'm', 'account': 'n', 'counts': 'n',
                  'AFFLUENT': 'segment', 'March 2025': 'time'})
    wrong = plan.model_dump(mode='json', by_alias=True)
    wrong['measures'][1]['filters'] = []
    for obligation in wrong['obligations']:
        obligation['targets'] = [target for target in obligation['targets'] if target != 'frozen-n']
    if fake is not None:
        fake.requests.clear()
    reply, decision = interpreted(QueryPlan.model_validate(wrong), text, v)
    assert decision.kind == 'abstain'
    assert Planner(v, eng.w, Hidden([], set(), [])).answer(text, reply)['kind'] == 'abstain'
    if fake is not None:
        assert budgets(fake) == dict(graph=0, retrieval=0, warehouse=0, dry=0, catalogue=0)
    actual = answered(eng, plan, text, v)
    assert actual == {('2025-03',): dict(m=Decimal('3246872.40'), n=Decimal(62))}
    if fake is not None:
        assert budgets(fake) == dict(graph=0, retrieval=0, warehouse=2, dry=2, catalogue=0)
    source = DIRECTED + " AND f.DW_TRAN_MNTH_END_DT=DATE '2025-03-31' AND r.SEGMT_LVL_ID5='AFFLUENT'"
    expected, = sql(eng, 'SELECT SUM(f.NNA_AM), COUNT(DISTINCT (a.DW_ACCT_ID,a.SRCE_SYS_ID)) ' + source + " AND f.FRZN_IN='Y'")
    assert (Decimal(str(expected[0])), expected[1]) == (Decimal('3246872.40'), 62)
    unscoped, = sql(eng, "SELECT SUM(CASE WHEN f.FRZN_IN='Y' THEN f.NNA_AM END), "
                       + 'COUNT(DISTINCT (a.DW_ACCT_ID,a.SRCE_SYS_ID)) ' + source)
    assert (Decimal(str(unscoped[0])), unscoped[1]) == (Decimal('3246872.40'), 63)


@pytest.mark.parametrize('month,period', [('2025-03', 'March 2025'), ('2025-06', 'June 2025')])
@pytest.mark.parametrize('wording', ["How much did SPS contribute to AFFLUENT's certified {period} client-directed NNA?",
    'What was the SPS contribution to certified client-directed NNA for AFFLUENT in {period}?'])
def test_modifier_across_period_keeps_frozen_share_and_rejects_unfrozen_plan(comparison_engine, month, period, wording):
    from tests.integration.test_planner_request_jobs import budgets
    eng, fake = comparison_engine
    text = wording.format(period=period)
    v = vocabulary(eng, text)
    ctx = v.contexts['nna_client_directed']
    assert [(p.text, p.candidates) for p in ledger(text, 'turn', v) if p.kind == 'name' and
            set(p.candidates) & v.contexts.keys()] == [('client-directed NNA', (ctx.id,))]
    frozen = predicate('wealth.F_NNA_FIN.FRZN_IN', 'frozen', 'Y')
    segment = predicate('wealth.CLIENT_CD_ROLLUP_1.SEGMT_LVL_ID5', 'segment', 'AFFLUENT', ['finance_account', 'account_client_code'])
    plan = grounded(model(measures=[measure(v, ctx.id)], time=single(month), filters=[segment, frozen],
        dimensions=[dimension(ctx, 'wealth.ACCT_DIM2_1.ACCT_RELT_SPS_IN', ['finance_account'])],
        derived=[dict(kind='share', id='s', input='m', remove_dimensions=['d'], remove_filters=[], evidence=[])]),
        text, v, {'How much': 'm', 'SPS': 'd', 'contribute': 's', 'contribution': 's', 'AFFLUENT': 'segment',
                  'certified': 'frozen', 'client-directed NNA': 'm', period: 'time'})
    wrong = plan.model_dump(mode='json', by_alias=True)
    wrong['filters'].pop()
    for obligation in wrong['obligations']:
        obligation['targets'] = ['m' if target == 'frozen' else target for target in obligation['targets']]
    if fake:
        fake.requests.clear()
    reply, decision = interpreted(QueryPlan.model_validate(wrong), text, v)
    assert decision.kind == 'abstain'
    assert Planner(v, eng.w, Hidden([], set(), [])).answer(text, reply)['kind'] == 'abstain'
    if fake:
        assert budgets(fake) == dict(graph=0, retrieval=0, warehouse=0, dry=0, catalogue=0)
    actual = answered(eng, plan, text, v)
    if fake:
        assert budgets(fake) == dict(graph=0, retrieval=0, warehouse=2, dry=2, catalogue=0)
    source = DIRECTED + f" AND f.DW_TRAN_MNTH_END_DT=last_day(DATE '{month}-01') AND r.SEGMT_LVL_ID5='AFFLUENT'"
    expected = {flag: Decimal(str(amount)) for flag, amount in sql(eng,
        'SELECT a.ACCT_RELT_SPS_IN, SUM(f.NNA_AM) ' + source + " AND f.FRZN_IN='Y' GROUP BY 1")}
    if month == '2025-03':
        assert expected == dict(N=Decimal('2268747.30'), Y=Decimal('978125.10'))
        assert sum(expected.values()) == Decimal('3246872.40')
    assert actual.keys() == {(flag,) for flag in expected}
    for (flag,), cells in actual.items():
        assert cells['m'] == expected[flag]
        assert cells['s'] == pytest.approx(expected[flag] / sum(expected.values()))


@pytest.mark.parametrize('placement', ['global', 'measure'])
def test_member_names_label_breakdowns_without_replacing_the_measure(comparison_engine, placement):
    from kge.semantics.plan import Propose
    from kge.services.ask.engine.combined import Combined
    from kge.services.ask.engine.core import PRESETS
    from kge.services.ask.engine.wording import template
    from tests.integration.test_plan_wording_round2 import Recording
    from tests.integration.test_planner_request_jobs import budgets
    eng, fake = comparison_engine
    text = 'Show me certified AFFLUENT NNA by sub-segment and quarter for Q1 through Q2 2025.'
    v = vocabulary(eng, text)
    ctx = v.contexts['nna_certified']
    path = ['finance_account', 'account_client_code']
    segment = predicate('wealth.CLIENT_CD_ROLLUP_1.SEGMT_LVL_ID5', 'segment', 'AFFLUENT', path)
    plan = grounded(model(measures=[measure(v, ctx.id, filters=[segment] if placement == 'measure' else [])],
        filters=[segment] if placement == 'global' else [], time=window('calendar_quarter', '2025-Q1', '2025-Q2'),
        dimensions=[dimension(ctx, 'wealth.CLIENT_CD_ROLLUP_1.SEGMT_LVL_ID6', path),
                    dict(kind='time', id='q', calendar=name('calendar'), grain='calendar_quarter')]),
        text, v, {'certified': 'm', 'NNA': 'm', 'AFFLUENT': 'segment', 'by sub-segment': 'd',
                  'and': 'q', 'quarter': 'q', 'Q1': 'time', 'through': 'time', 'Q2 2025': 'time'})
    if fake:
        fake.requests.clear()
    answer = Combined(eng, llm=Recording(Propose(kind='propose', base_turn=None, plan=plan))).ask(
        text, 'analyst_enterprise', config=PRESETS['graph'].with_stages(resolution='glossary+model'))
    assert answer['behaviour'] == 'figure', answer
    assert 'Certified NNA' in (answer.get('answer_text') or template(answer, eng))
    assert [c['label'] for c in answer['result']['columns'] if c['kind'] != 'dimension'] == ['Certified NNA']
    assert any(any(row.get('dimension_labels', ())) for row in answer['result']['rows'])
    if fake:
        counts = budgets(fake)
        assert (counts['warehouse'], counts['dry'], counts['catalogue']) == (2, 2, 0)


def test_prefix_comparison_keeps_reference_filter_and_period_order(eng):
    text = 'Compare AFFLUENT certified NNA in June 2025 with June 2024'
    v = vocabulary(eng, text)
    def period(year):
        return dict(kind='single', period=dict(kind='month', value=f'{year}-06', calendar=name('calendar')))
    plan = grounded(model(measures=[measure(v, 'nna_certified')], time=period(2025),
        filters=[predicate('wealth.CLIENT_CD_ROLLUP_1.SEGMT_LVL_ID5', 'segment', 'AFFLUENT', ['finance_account', 'account_client_code'])],
        comparisons=[dict(kind='explicit_period', id='c', inputs=['m'], other=period(2024), evidence=[])],
        derived=[dict(kind=kind, id=identifier, numerator='c/m/base', denominator='c/m/other', evidence=[])
                 for kind, identifier in [('difference', 'delta'), ('percent_change', 'pct')]]), text, v,
        {'Compare': ['c', 'delta', 'pct'], 'AFFLUENT': 'segment', 'certified NNA': 'm', 'June 2025': 'time', 'June 2024': 'c'})
    actual = answered(eng, plan, text, v)
    cells, = actual.values()
    for year, side in [(2025, 'base'), (2024, 'other')]:
        expected = sql(eng, 'SELECT SUM(f.NNA_AM) ' + FINANCE
                       + f" AND f.DW_TRAN_MNTH_END_DT=DATE '{year}-06-30' AND r.SEGMT_LVL_ID5='AFFLUENT'")[0][0]
        assert cells[f'c/m/{side}'] == expected
    data = plan.model_dump(mode='json', by_alias=True)
    data['time'], data['comparisons'][0]['other'] = period(2024), period(2025)
    assert interpreted(QueryPlan.model_validate(data), text, v)[1].kind == 'abstain'


@pytest.mark.parametrize('placement', ['global', 'each'])
@pytest.mark.parametrize('certified_directed', [False, True])
def test_monthly_affluent_sps_followup_keeps_history_bindings(comparison_engine, placement, certified_directed):
    from tests.integration.test_planner_request_jobs import budgets
    eng, fake = comparison_engine
    from kge.services.ask.engine import planrules
    from kge.services.ask.engine.core import PRESETS, parse
    from kge.services.ask.engine.scope import ProductScope
    adjective = 'certified ' if certified_directed else ''
    text = f'Monthly certified NNA and {adjective}client-directed NNA for AFFLUENT January through June 2025'
    v = vocabulary(eng, text)
    outputs = segment_outputs(v)[:2]
    filters = [predicate('wealth.CLIENT_CD_ROLLUP_1.SEGMT_LVL_ID5', 'f', 'AFFLUENT', ['finance_account', 'account_client_code'])]
    assignments = {'Monthly': 'd', 'certified NNA': 'a', 'client-directed NNA': 'b', 'AFFLUENT': 'f',
                   'January': 'time', 'through': 'time', 'June 2025': 'time'}
    if certified_directed:
        outputs[1]['filters'].append(predicate('wealth.F_NNA_FIN.FRZN_IN', 'frozen', 'Y'))
        assignments['certified'] = 'frozen'
    plan = grounded(model(measures=outputs, dimensions=[monthly()], time=window(first='2025-01'), filters=filters),
                    text, v, assignments)
    answered(eng, plan, text, v)
    history = (HistoryEntry(turn_id='turn', question=text, plan=plan, clarification=None, binding=v.binding),)
    followup = 'Now just SPS accounts.'
    req = parse(followup, eng.term_names, eng)
    current = planrules.vocabulary(eng, 'analyst_enterprise', 'analysis', ProductScope(eng, None), PRESETS['graph'], req, history)
    assert current.history_vocabulary is not None
    data = plan.model_dump(mode='json', by_alias=True)
    identifiers = []
    for container in ([data] if placement == 'global' else data['measures']):
        identifier = 'sps' + str(len(identifiers))
        container['filters'].append(predicate('wealth.ACCT_DIM2_1.ACCT_RELT_SPS_IN', identifier, 'Y', ['finance_account']))
        identifiers.append(identifier)
    piece, = ledger(followup, 'next', current)
    data['obligations'].append(dict(id='sps-added', spans=[piece.span.model_dump()], kind='name', targets=identifiers, state='applied'))
    if fake:
        fake.requests.clear()
    actual = answered(eng, QueryPlan.model_validate(data), followup, current, history, 'next',
                      expected_totals={'a': Decimal('10343131.43'), 'b': Decimal('8404686.47')})
    if fake:
        assert budgets(fake) == dict(graph=0, retrieval=0, warehouse=2, dry=2, catalogue=0)
    if certified_directed:
        from kge.services.ask.engine.planner import measure_label
        updated = QueryPlan.model_validate(data)
        assert measure_label(updated.measures[1], updated, current, followup, 'next') == 'Certified (frozen) client-directed NNA'
    expected = [('214182.04', '125987.84'), ('792086.97', '1132041.23'), ('1846525.49', '978125.10'),
                ('2909416.28', '2514513.28'), ('2706227.26', '1895496.87'), ('1874693.39', '1758522.15')]
    assert actual == {(f'2025-{month:02}-01',): dict(a=Decimal(a), b=Decimal(b))
                      for month, (a, b) in enumerate(expected, 1)}
    march = sql(eng, 'SELECT SUM(f.NNA_AM) ' + DIRECTED + " AND f.DW_TRAN_MNTH_END_DT=DATE '2025-03-31'"
                + " AND r.SEGMT_LVL_ID5='AFFLUENT' AND a.ACCT_RELT_SPS_IN='Y' AND f.FRZN_IN='Y'")[0][0]
    assert Decimal(str(march)) == Decimal('978125.10')
    wrong = deepcopy(data)
    if placement == 'global':
        flag = wrong['filters'].pop()
        wrong['measures'][0]['filters'].append(flag)
    else:
        wrong['measures'][1]['filters'].pop()
        for obligation in wrong['obligations']:
            obligation['targets'] = [t for t in obligation['targets'] if t != 'sps1']
    bad_reply, checked = interpreted(QueryPlan.model_validate(wrong), followup, current, history, 'next')
    assert checked.kind == 'abstain'
    assert Planner(current, eng.w, Hidden([], set(), [])).answer(followup, bad_reply, 'next', history)['kind'] == 'abstain'
    wrong = deepcopy(data)
    wrong['filters'] = [f for f in wrong['filters'] if f['id'] != 'f']
    wrong['obligations'] = [o for o in wrong['obligations'] if 'f' not in o['targets']]
    class Dropped:
        def json(self, prompt):
            return dict(kind='propose', base_turn='turn', plan=wrong)
    bad_reply = read(Dropped(), followup, current, history, 'next')
    assert Planner(current, eng.w, Hidden([], set(), [])).answer(followup, bad_reply, 'next', history)['kind'] == 'abstain'


@pytest.mark.parametrize('configured', [False, True])
def test_future_model_abstention_and_release_driver_offer_the_latest_month(comparison_engine, configured):
    from tests.integration.test_planner_request_jobs import budgets
    eng, fake = comparison_engine
    from kge.semantics.plan import Abstain
    from kge.services.ask.engine.combined import Combined
    from kge.services.ask.engine.core import PRESETS
    from tests.integration.test_plan_wording_round2 import Recording
    question = 'What was certified NNA for December 2026?'
    if fake:
        fake.requests.clear()
    if configured:
        reply = Abstain(kind='abstain', spans=(), missing=('December 2026 has not started. No figure is given.',))
        result = Combined(eng, llm=Recording(reply)).ask(question, 'analyst_enterprise',
            config=PRESETS['graph'].with_stages(resolution='glossary+model'))
    else:
        result = eng.answer(question, 'analyst_enterprise')
    assert result['behaviour'] in ('abstain', 'abstain_with_reason')
    assert not result.get('figure') and not result.get('figures') and not result.get('result') and not result.get('sql')
    assert 'The latest released month is June 2025. Would you like that month instead?' in result['reason']
    assert result['reason'].endswith('Would you like that month instead?')
    if fake:
        counts = budgets(fake)
        assert (counts['warehouse'], counts['dry'], counts['catalogue']) == (0 if configured else 1, 0 if configured else 1, 0)


@pytest.mark.parametrize('certified', [False, True])
def test_client_directed_modifier_enforces_frozen_rows_without_changing_plain_measure(eng, certified):
    adjective = 'certified ' if certified else ''
    text = f'AFFLUENT {adjective}client-directed NNA by client segment level 6, January through June 2025'
    v = vocabulary(eng, text)
    ctx = v.contexts['nna_client_directed']
    filters = [predicate('wealth.CLIENT_CD_ROLLUP_1.SEGMT_LVL_ID5', 'segment', 'AFFLUENT', ['finance_account', 'account_client_code'])]
    if certified:
        filters.append(predicate('wealth.F_NNA_FIN.FRZN_IN', 'frozen', 'Y'))
    assignments = {'AFFLUENT': 'segment', 'certified': 'frozen', 'client-directed NNA': 'm',
                   'by client segment level 6': 'd', 'January': 'time', 'through': 'time', 'June 2025': 'time'}
    plan = grounded(model(measures=[measure(v, ctx.id)], filters=filters, time=window(first='2025-01'),
        dimensions=[dimension(ctx, 'wealth.CLIENT_CD_ROLLUP_1.SEGMT_LVL_ID6', ['finance_account', 'account_client_code'])]),
        text, v, assignments)
    total = Decimal('20800046.15' if certified else '20861792.94')
    actual = answered(eng, plan, text, v, expected_totals={'m': total})
    assert actual[('EMERGING',)]['m'] == Decimal('9884073.81' if certified else '9945820.60')
    if certified:
        wrong = plan.model_dump(mode='json', by_alias=True)
        wrong['filters'].pop()
        wrong['obligations'] = [o for o in wrong['obligations'] if 'frozen' not in o['targets']]
        assert interpreted(QueryPlan.model_validate(wrong), text, v)[1].kind == 'abstain'
    march_text = f'{adjective}client-directed NNA for AFFLUENT March 2025'
    mv = vocabulary(eng, march_text)
    march = grounded(model(measures=[measure(mv, ctx.id)], filters=filters, time=single('2025-03')),
                    march_text, mv, {'certified': 'frozen', 'client-directed NNA': 'm', 'AFFLUENT': 'segment', 'March 2025': 'time'})
    cells = next(iter(answered(eng, march, march_text, mv).values()))
    assert cells['m'] == Decimal('3246872.40' if certified else '3308619.19')


@pytest.mark.parametrize('column,phrase', [('SRCE_SYS_ID', 'source system'), ('SRCE_SYS_NM', 'source system name'),
                                          ('SRCE_SYS_NM', 'source system')])
def test_source_system_breakdown_keeps_requested_column_and_declared_names(comparison_engine, column, phrase):
    from kge.services.ask.engine.planpublic import render
    from tests.integration.test_planner_request_jobs import budgets
    eng, fake = comparison_engine
    text = f'Show me YTD CORE certified NNA through June 2025 broken down by {phrase}.'
    v = vocabulary(eng, text)
    ctx = v.contexts['nna_certified']
    plan = grounded(model(measures=[measure(v, ctx.id)],
        dimensions=[dimension(ctx, 'wealth.SRCE_SYS_1.' + column, ['finance_source_system'])],
        time=dict(kind='ytd', calendar=name('calendar'), through=dict(kind='month', value='2025-06', calendar=name('calendar'))),
        filters=[predicate('wealth.CLIENT_CD_ROLLUP_1.SEGMT_LVL_ID6', 'core', 'CORE', ['finance_account', 'account_client_code'])]),
        text, v, {'YTD': 'time', 'CORE': 'core', 'certified NNA': 'm', 'through': 'time', 'June 2025': 'time', 'by ' + phrase: 'd'})
    reply, checked = interpreted(plan, text, v)
    assert isinstance(checked, CheckedPlan), checked
    if fake:
        fake.requests.clear()
    result = Planner(v, eng.w, Hidden([], set(), [])).answer(text, reply)
    assert result['kind'] == 'result', result
    data = result['result']
    rows = [r for r in data['rows'] if not r.get('total')]
    effective = reply.plan.dimensions[0].bindings[0].column.id.rsplit('.', 1)[1]
    labels = {100: 'LEGACY BROKERAGE', 200: 'BANK DEPOSITS'}
    expected = {100: Decimal('11832940.70'), 200: Decimal('2632601.66')}
    if effective == 'SRCE_SYS_ID':
        assert {r['dimensions'][0]['value']: r['dimension_labels'][0] for r in rows} == labels
        assert {r['dimensions'][0]['value']: Decimal(r['cells'][0]['value']['value']) for r in rows} == expected
    else:
        assert {r['dimensions'][0]['value']: Decimal(r['cells'][0]['value']['value']) for r in rows} == {labels[k]: v for k, v in expected.items()}
    total, = [r for r in data['rows'] if r.get('total')]
    assert Decimal(total['cells'][0]['value']['value']) == Decimal('14465542.36')
    assert all(label in render(data) for label in labels.values())
    if fake:
        assert budgets(fake) == dict(graph=0, retrieval=0, warehouse=2, dry=2, catalogue=0)
        if effective == 'SRCE_SYS_ID':
            import sqlglot
            from sqlglot import exp
            sent = [request['body'].get('configuration', {}).get('query', request['body']).get('query', '')
                    for request in fake.requests if request.get('body') is not None]
            breakdowns = [query for query in sent if 'TO_JSON_STRING(STRUCT(' in query]
            assert breakdowns
            for query in breakdowns:
                tree = sqlglot.parse_one(query, read='bigquery')
                groups = list(tree.find_all(exp.Group))
                assert groups
                for group in groups:
                    assert group.expressions == [exp.Literal.number(2)]
                    output = group.parent.expressions[1]
                    assert output.alias == 'd0'
                    assert {c.name for c in output.find_all(exp.Column)} == {'SRCE_SYS_ID', 'SRCE_SYS_NM'}
            assert not any('JSON_OBJECT' in query for query in sent)


@pytest.mark.parametrize('certified', [False, True])
@pytest.mark.parametrize('configured', [False, True])
def test_client_directed_public_answer_declares_the_frozen_basis(comparison_engine, certified, configured):
    from kge.semantics.plan import Propose
    from kge.services.ask.engine.combined import Combined
    from kge.services.ask.engine.core import PRESETS
    from tests.integration.test_plan_wording_round2 import Recording
    from tests.integration.test_planner_request_jobs import budgets
    eng, fake = comparison_engine
    text = ('certified ' if certified else '') + 'client-directed NNA for AFFLUENT March 2025'
    v = vocabulary(eng, text)
    filters = [predicate('wealth.CLIENT_CD_ROLLUP_1.SEGMT_LVL_ID5', 'segment', 'AFFLUENT', ['finance_account', 'account_client_code'])]
    if certified:
        filters.append(predicate('wealth.F_NNA_FIN.FRZN_IN', 'frozen', 'Y'))
    plan = grounded(model(measures=[measure(v, 'nna_client_directed')], filters=filters, time=single('2025-03')),
        text, v, {'certified': 'frozen', 'client-directed NNA': 'm', 'AFFLUENT': 'segment', 'March 2025': 'time'})
    if fake:
        fake.requests.clear()
    answer = (Combined(eng, llm=Recording(Propose(kind='propose', base_turn=None, plan=plan))).ask(
        text, 'analyst_enterprise', config=PRESETS['graph'].with_stages(resolution='glossary+model'))
        if configured else eng.answer(text, 'analyst_enterprise'))
    assert answer['behaviour'] == 'figure', answer
    assert answer['figure'] == (3246872.40 if certified else 3308619.19)
    from kge.services.ask.engine.wording import template
    text = answer.get('answer_text') or template(answer, eng)
    if certified:
        assert 'certified (frozen) client-directed nna' in text.casefold()
        assert answer['context']['term'] == 'Certified (frozen) client-directed NNA'
    else:
        assert 'frozen' not in text.casefold()
    if fake:
        counts = budgets(fake)
        assert (counts['warehouse'], counts['dry'], counts['catalogue']) == (2, 2, 0)


def test_source_system_public_table_shows_names_beside_codes(comparison_engine):
    from kge.services.ask.engine.present import present
    from kge.services.ask.engine.wording import template
    from tests.integration.test_planner_request_jobs import budgets
    eng, fake = comparison_engine
    text = 'Show me YTD CORE certified NNA through June 2025 broken down by source system.'
    if fake:
        fake.requests.clear()
    answer = eng.answer(text, 'analyst_enterprise')
    display = present(answer, eng)
    assert 'error' not in display, display
    assert [r[0] for r in display['table']['rows']] == ['100 · LEGACY BROKERAGE', '200 · BANK DEPOSITS']
    assert [r[1] for r in display['table']['rows']] == [11832940.70, 2632601.66]
    assert display['table']['total']['value'] == 14465542.36
    assert display['chart'] is not None
    assert all(name in template(answer, eng) for name in ['LEGACY BROKERAGE', 'BANK DEPOSITS'])
    if fake:
        counts = budgets(fake)
        assert (counts['warehouse'], counts['dry'], counts['catalogue'], counts['retrieval']) == (2, 2, 0, 0)
        assert counts['graph'] <= 2


def test_certified_dated_client_directed_rules_abstention_is_user_facing(comparison_engine):
    from kge.services.ask.engine.wording import template
    from tests.integration.test_planner_request_jobs import budgets
    eng, fake = comparison_engine
    if fake:
        fake.requests.clear()
    answer = eng.answer('certified June 2025 client-directed NNA', 'analyst_enterprise')
    assert answer['behaviour'] in ('abstain', 'abstain_with_reason')
    assert 'unrequested node' not in template(answer, eng)
    assert 'No figure is given' in answer['reason']
    if fake:
        counts = budgets(fake)
        assert (counts['warehouse'], counts['dry'], counts['catalogue']) == (0, 0, 0)
