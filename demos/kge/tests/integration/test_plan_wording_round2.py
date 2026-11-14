import json
from decimal import Decimal

import pytest

from kge.ports import AUTHORED
from kge.semantics.coverage import ledger
from kge.semantics.plan import Propose, Clarify, Name
from kge.semantics.plancheck import CheckedPlan, check_plan
from kge.services.ask.engine import planmodel, planrules
from kge.services.ask.engine.combined import Combined
from kge.services.ask.engine.core import PRESETS, parse
from kge.services.ask.engine.planner import Planner
from kge.services.ask.engine.scope import ProductScope
from kge.services.ask.engine.wording import Hidden
from tests.plan_fixtures import grounded, model, name


class Recording:
    name = 'test:wording'

    def __init__(self, reply):
        self.reply = reply

    def json(self, prompt):
        turn = json.loads(prompt.split('\n', 1)[1])['turn_id']
        data = self.reply.model_dump(mode='json', by_alias=True)
        def replace_turn(value):
            if isinstance(value, dict):
                for key, member in value.items():
                    if key == 'turn' and member == 'next':
                        value[key] = turn
                    elif key == 'turn' and not data.get('base_turn'):
                        value[key] = turn
                    else:
                        replace_turn(member)
            elif isinstance(value, list):
                for member in value:
                    replace_turn(member)
        replace_turn(data)
        return data


def vocabulary(eng, text):
    req = parse(text, eng.term_names, eng)
    return planrules.vocabulary(eng, 'analyst_enterprise', 'analysis', ProductScope(eng, None), PRESETS['graph'], req)


def measure(ctx, identifier='m', filters=()):
    return dict(kind='measure', id=identifier, context=name(ctx.id), measure=name(ctx.measure), mode='current', filters=list(filters))


def count(ctx, entity, identifier):
    return dict(kind='distinct_count', id=identifier, context=name(ctx.id), entity=name(entity), mode='current', filters=[])


def predicate(column, identifier, value, path=(), op='eq', kind='text'):
    return dict(kind='compare', id=identifier, left=dict(kind='column', name=name(column), path=[name(p) for p in path]),
                op=op, value=dict(kind=kind, value=value), evidence=[])


def dimension(ctx, column, path=(), identifier='d'):
    return dict(kind='column', id=identifier, bindings=[dict(context=name(ctx.id), column=name(column), path=[name(p) for p in path], level=None)])


def april():
    return dict(kind='single', period=dict(kind='month', value='2025-04', calendar=name('fiscal_april')))


def run(eng, text, v, measures, assignments, **updates):
    updates.setdefault('time', april())
    p = grounded(model(measures=measures, **updates), text, v, assignments)
    reply = planmodel.read(Recording(Propose(kind='propose', base_turn=None, plan=p)), text, v)
    assert reply.kind == 'propose', reply
    checked = check_plan(reply.plan, v, text)
    assert isinstance(checked, CheckedPlan), checked
    result = Planner(v, eng.w, Hidden([], set(), [])).answer(text, reply)
    assert result['kind'] == 'result' and result['guard']['passed'], result
    return [r for r in result['result']['rows'] if not r.get('total')]


def cells(row):
    return [Decimal(str(c['value']['value'])) for c in row['cells']]


def sql(eng, statement):
    return eng.w.rows('wealth', statement, AUTHORED)


ACCOUNT = """JOIN ACCT_DIM2_1 a ON f.DW_ACCT_ID = a.DW_ACCT_ID AND f.ACCT_SRCE_SYS_ID = a.SRCE_SYS_ID
AND a.DW_EFF_DT <= f.MNTH_END_DT AND (a.DW_EXPR_DT IS NULL OR a.DW_EXPR_DT >= f.MNTH_END_DT)"""
BRANCH = """FROM TRAN_FACT_EXCL_MFCLR_G f JOIN MONTH_DIM_1 m ON f.MNTH_END_DT = m.CLDR_EOM_DT
""" + ACCOUNT + """
JOIN CLIENT_CD_ROLLUP_1 r ON r.CLNT_CD=a.CLNT_CD AND f.MNTH_END_DT BETWEEN r.DW_EFF_DT AND r.DW_EXPR_DT
JOIN BRCH_DIM_1 b ON b.BRCH_CD=a.ACCT_DOMCL_BRCH_CD AND b.CURR_IN<>0
WHERE f.MNTH_END_DT = DATE '2025-04-30'
AND m.DW_BRKRG_MNTH_FRM_TODAY_CT<>0 AND m.FIN_MTRC_RLSE_DT BETWEEN f.DW_EFF_DT AND f.DW_EXPR_DT
AND f.RCV_IN=1 AND f.NNA_IN='Y' AND f.ACCT_SRCE_SYS_ID IN (100,200)
AND r.SEGMT_LVL_ID5='AFFLUENT' AND upper(b.BRCH_DIVSN_CD)='BR'
AND upper(coalesce(f.DW_TRAN_TYPE_CD,'')) NOT IN ('AQ','DT')"""
FINANCE = """FROM F_NNA_FIN f JOIN MONTH_DIM_1 m ON f.DW_TRAN_MNTH_END_DT = m.CLDR_EOM_DT
WHERE m.DW_BRKRG_MNTH_FRM_TODAY_CT<>0 AND f.FRZN_IN='Y' AND f.DW_TRAN_MNTH_END_DT = DATE '2025-04-30'"""


@pytest.mark.parametrize('context,phrase,table,base,amount', [
    ('nna_branch_sales', 'Branch sales NNA', 'TRAN_FACT_EXCL_MFCLR_G', BRANCH,
     "CASE WHEN f.DW_TRAN_DIRCTN_CD = 'O' THEN -f.TRAN_AM ELSE f.TRAN_AM END"),
    ('nna_certified', 'Certified NNA', 'F_NNA_FIN', FINANCE, 'f.NNA_AM'),
])
@pytest.mark.parametrize('word,direction', [('inflows', 'I'), ('outflows', 'O')])
def test_direction_words_resolve_on_the_named_fact(eng, context, phrase, table, base, amount, word, direction):
    text = f'{phrase} with {word} during April 2025'
    v = vocabulary(eng, text)
    ctx = v.contexts[context]
    col = 'wealth.' + table + '.DW_TRAN_DIRCTN_CD'
    part = predicate(col, 'f', direction)
    rows = run(eng, text, v, [measure(ctx, filters=[part])], {word: 'f', 'April 2025': 'time'})
    assert cells(rows[0]) == [sql(eng, 'SELECT sum(' + amount + ') ' + base + f" AND f.DW_TRAN_DIRCTN_CD = '{direction}'")[0][0]]
    assert len(next(p for p in ledger(text, 'turn', v) if p.text == word).candidates) == 2


def test_branch_household_breakdown_and_distinct_count(eng):
    text = 'Branch sales NNA by household with number of households during April 2025'
    v = vocabulary(eng, text)
    ctx = v.contexts['nna_branch_sales']
    rows = run(eng, text, v, [measure(ctx), count(ctx, 'wealth_account_household', 'n')],
               {'by household': 'd', 'number of': 'n', 'households': 'n', 'April 2025': 'time'},
               dimensions=[dimension(ctx, 'wealth.ACCT_DIM2_1.LAST_RCV_HSHLD_DK', ['branch_sales_account'])])
    expected = sql(eng, "SELECT a.LAST_RCV_HSHLD_DK, sum(CASE WHEN f.DW_TRAN_DIRCTN_CD = 'O' THEN -f.TRAN_AM ELSE f.TRAN_AM END), count(DISTINCT a.LAST_RCV_HSHLD_DK) "
                   + BRANCH
                   + ' GROUP BY a.LAST_RCV_HSHLD_DK')
    actual = {r['dimensions'][0]['value']: cells(r) for r in rows}
    assert actual == {key: [amount, n] for key, amount, n in expected}


@pytest.mark.parametrize('context,phrase,base', [('nna_certified', 'Certified NNA', FINANCE),
                                              ('nna_branch_sales', 'Branch sales NNA', BRANCH)])
def test_source_system_words_bind_to_the_shared_dimension(eng, context, phrase, base):
    text = f'{phrase} by source system and by source system name during April 2025'
    v = vocabulary(eng, text)
    ctx = v.contexts[context]
    path = 'finance_source_system' if context == 'nna_certified' else 'branch_sales_source_system'
    rows = run(eng, text, v, [measure(ctx)], {'by source system': 'd', 'by source system name': 'label', 'April 2025': 'time'},
               dimensions=[dimension(ctx, 'wealth.SRCE_SYS_1.SRCE_SYS_ID', [path]),
                           dimension(ctx, 'wealth.SRCE_SYS_1.SRCE_SYS_NM', [path], 'label')])
    amount = 'f.NNA_AM' if context == 'nna_certified' else "CASE WHEN f.DW_TRAN_DIRCTN_CD = 'O' THEN -f.TRAN_AM ELSE f.TRAN_AM END"
    expected = sql(eng, 'SELECT s.SRCE_SYS_ID, s.SRCE_SYS_NM, sum(' + amount + ') ' + base.replace('WHERE',
        'JOIN SRCE_SYS_1 s ON f.ACCT_SRCE_SYS_ID = s.SRCE_SYS_ID AND s.CURR_IN <> 0 AND s.RCV_IN = 1 WHERE')
        + ' GROUP BY s.SRCE_SYS_ID, s.SRCE_SYS_NM')
    assert {tuple(d['value'] for d in r['dimensions']): cells(r)[0] for r in rows} == {(key, label): amount for key, label, amount in expected}


def test_branch_transaction_wording_and_amount_threshold(eng):
    text = 'Branch sales NNA with number of transactions with transaction amount at least 20000 during April 2025'
    v = vocabulary(eng, text)
    ctx = v.contexts['nna_branch_sales']
    rows = run(eng, text, v, [measure(ctx), count(ctx, 'wealth_flow_excl_mf_clearing', 'n')],
               {'number of': 'n', 'transactions': 'n', 'transaction amount': 'f', 'at least': 'f', '20000': 'f', 'April 2025': 'time'},
               filters=[predicate('wealth.TRAN_FACT_EXCL_MFCLR_G.TRAN_AM', 'f', '20000', op='ge', kind='decimal')])
    expected = sql(eng, "SELECT sum(CASE WHEN f.DW_TRAN_DIRCTN_CD = 'O' THEN -f.TRAN_AM ELSE f.TRAN_AM END), count(DISTINCT f.DW_TRAN_ID) "
                   + BRANCH + ' AND f.TRAN_AM >= 20000')[0]
    assert cells(rows[0]) == list(expected)


def test_finance_transactions_and_accounts_by_month(eng):
    text = 'Certified NNA by month with number of transactions and number of accounts during April 2025'
    v = vocabulary(eng, text)
    ctx = v.contexts['nna_certified']
    rows = run(eng, text, v, [measure(ctx), count(ctx, 'wealth_nna_reference', 'n'), count(ctx, 'wealth_account', 'a')],
               {'by': 'd', 'month': 'd', 'number of': ['n', 'a'], 'transactions': 'n', 'accounts': 'a', 'April 2025': 'time'},
               dimensions=[dict(kind='time', id='d', calendar=name('fiscal_april'), grain='month')])
    expected = sql(eng, 'SELECT sum(f.NNA_AM), count(DISTINCT f.DW_TRAN_ID), count(DISTINCT (a.DW_ACCT_ID, a.SRCE_SYS_ID)) '
        + FINANCE.replace('WHERE', ACCOUNT.replace('f.MNTH_END_DT', 'f.DW_TRAN_MNTH_END_DT') + ' WHERE'))[0]
    assert cells(rows[0]) == list(expected)


@pytest.mark.parametrize('word,direction', [('most', 'desc'), ('least', 'asc')])
def test_which_weekday_ranking_keeps_every_day(eng, word, direction):
    text = f'Which weekday had the {word} certified NNA year to date through April 2025?'
    v = vocabulary(eng, text)
    ctx = v.contexts['nna_certified']
    rows = run(eng, text, v, [measure(ctx)], {'weekday': 'd', word: 'm', 'year to date': 'time', 'through': 'time', 'April 2025': 'time'},
               time=dict(kind='ytd', calendar=name('calendar'), through=dict(kind='month', value='2025-04', calendar=name('calendar'))),
               dimensions=[dimension(ctx, 'wealth.DATE_DIM_1.DAY_OF_WEEK', ['finance_date'])],
               order=[dict(output='m', direction=direction, nulls='last', evidence=[])])
    expected = sql(eng, 'SELECT d.DAY_OF_WEEK, sum(f.NNA_AM) ' + FINANCE.replace("= DATE '2025-04-30'", "BETWEEN DATE '2025-01-01' AND DATE '2025-04-30'").replace('WHERE',
        'JOIN DATE_DIM_1 d ON f.DW_TRAN_DT = d.CALENDAR_DATE WHERE') + f' GROUP BY d.DAY_OF_WEEK ORDER BY 2 {direction}')
    assert [(r['dimensions'][0]['value'], cells(r)[0]) for r in rows] == expected
    assert len(rows) == 7


@pytest.mark.parametrize('explicit_amount', [False, True])
def test_positive_branch_parts_ratio_net_and_households(eng, explicit_amount):
    text = ('Branch sales NNA' + (' and branch sales flow amount' if explicit_amount else '')
            + ' with inflows and outflows and ratio of inflows to outflows '
            'with number of households during April 2025')
    v = vocabulary(eng, text)
    net, positive = v.contexts['nna_branch_sales'], v.contexts['branch_sales_flow_amount']
    col = 'wealth.TRAN_FACT_EXCL_MFCLR_G.DW_TRAN_DIRCTN_CD'
    measures = [measure(net), measure(positive, 'i', [predicate(col, 'fi', 'I')]),
                measure(positive, 'o', [predicate(col, 'fo', 'O')]), count(net, 'wealth_account_household', 'n')]
    assignments = {'Branch sales NNA': ['m', 'n'], 'branch sales flow amount': ['i', 'o'], 'inflows': ['fi', 'i'], 'outflows': ['fo', 'o'],
                   'ratio': 'r', 'number of': 'n', 'households': 'n', 'April 2025': 'time'}
    derived = [dict(kind='ratio', id='r', numerator='i', denominator='o', evidence=[])]
    if not explicit_amount:
        p = grounded(model(measures=measures, time=april(), derived=derived), text, v, assignments)
        decision = check_plan(p, v, text)
        assert decision.kind == 'abstain' and 'every measure part must use its named context' in decision.missing
        return
    rows = run(eng, text, v, measures, assignments, derived=derived)
    incoming, outgoing, net_amount, households = sql(eng, "SELECT sum(CASE WHEN f.DW_TRAN_DIRCTN_CD = 'I' THEN f.TRAN_AM ELSE 0 END), "
        "sum(CASE WHEN f.DW_TRAN_DIRCTN_CD = 'O' THEN f.TRAN_AM ELSE 0 END), "
        "sum(CASE WHEN f.DW_TRAN_DIRCTN_CD = 'O' THEN -f.TRAN_AM ELSE f.TRAN_AM END), count(DISTINCT a.LAST_RCV_HSHLD_DK) "
        + BRANCH)[0]
    actual = cells(rows[0])
    assert actual[:4] == [net_amount, incoming, outgoing, households]
    assert actual[4] == pytest.approx(incoming / outgoing)
    assert actual[4] > 0


@pytest.mark.parametrize('configured', [False, True])
@pytest.mark.parametrize('text', ['What were total client assets for segment level 5 PRIVATE at the end of April 2025?',
                                 'Compare certified NNA to budget during April 2025'])
def test_undeclared_metric_offers_only_nearby_measures(eng, text, configured):
    v = vocabulary(eng, text)
    p = grounded(model(measures=[measure(v.contexts['nna_certified'])], time=april()), text, v)
    answer = (Combined(eng, llm=Recording(Propose(kind='propose', base_turn=None, plan=p))).ask(
        text, 'analyst_enterprise', config=PRESETS['graph'].with_stages(resolution='glossary+model'))
        if configured else eng.answer(text, 'analyst_enterprise'))
    assert not answer.get('result') and answer.get('figure') is None and not answer.get('sql')
    assert 'Declared measures here are' in answer['reason'] and 'Which would you like?' in answer['reason']
    assert 'Active Customer' not in answer['reason'] and 'Credit Exposure' not in answer['reason']
    assert 'Segment has no certified implementation' not in answer['reason']


def test_retail_clarification_has_two_grounded_meanings(eng):
    text = 'Retail NNA during April 2025'
    v = vocabulary(eng, text)
    piece = next(p for p in ledger(text, 'turn', v) if p.text == 'Retail NNA')
    reply = Clarify(kind='clarify', spans=(piece.span,), candidates=tuple(Name(id=i, evidence=(piece.span,)) for i in ('retail', 'nna_branch_sales')))
    answer = Combined(eng, llm=Recording(reply)).ask(text, 'analyst_enterprise',
        config=PRESETS['graph'].with_stages(resolution='glossary+model'))
    assert answer['behaviour'] == 'clarify' and answer['reason'] == 'Which meaning do you intend?'
    assert not answer.get('result') and not answer.get('figure') and not answer.get('sql')
    assert {c['id'] for c in answer['clarification']['choices']} == {'retail', 'nna_branch_sales'}
    assert {c['label'] for c in answer['clarification']['choices']} == {'retail', 'Branch Sales NNA'}


def test_client_name_policy_fires_before_model_and_offers_keys(eng):
    class Uncalled:
        name = 'test:uncalled'
        def json(self, prompt):
            pytest.fail('restricted question reached the model')
    answer = Combined(eng, llm=Uncalled()).ask('Show client names for households by branch sales NNA during April 2025',
                                           'analyst_enterprise')
    assert answer['behaviour'] == 'refuse' and 'POL-DATA-CLASS' in answer['reason']
    assert 'household keys' in answer['reason'] and not answer.get('result') and not answer.get('figure')


def test_now_just_followup_keeps_the_month_and_narrows_the_account_rows(eng):
    from kge.semantics.plan import HistoryEntry
    from kge.semantics.planhistory import replacement
    text = 'Branch sales NNA during April 2025'
    v = vocabulary(eng, text)
    ctx = v.contexts['nna_branch_sales']
    base = grounded(model(measures=[measure(ctx)], time=april()), text, v, {'April 2025': 'time'})
    first = Planner(v, eng.w, Hidden([], set(), [])).answer(text, Propose(kind='propose', base_turn=None, plan=base))
    assert first['kind'] == 'result' and first['guard']['passed']
    history = (HistoryEntry(turn_id='turn', question=text, plan=base, clarification=None, binding=v.binding),)
    followup = 'Now just with account relationship'
    updated = base.model_dump(mode='json', by_alias=True)
    updated['filters'] = [predicate('wealth.ACCT_DIM2_1.ACCT_RELT_SPS_IN', 'flag', 'Y', ['branch_sales_account'])]
    updated['obligations'] = []
    from kge.semantics.plan import QueryPlan
    p = grounded(QueryPlan.model_validate(updated), followup, v, {'with account relationship': 'flag'})
    updated = p.model_dump(mode='json', by_alias=True)
    for obligation in updated['obligations']:
        obligation['id'] = 'followup-' + obligation['id']
        for span in obligation['spans']:
            span['turn'] = 'next'
    updated['obligations'].extend(base.model_dump(mode='json', by_alias=True)['obligations'])
    p = QueryPlan.model_validate(updated)
    reply = planmodel.read(Recording(Propose(kind='propose', base_turn='turn', plan=p)), followup, v, history, turn='next')
    assert reply.kind == 'propose', reply
    problem, inherited = replacement(reply, history, v, followup, 'next')
    assert problem is None, problem
    assert isinstance(check_plan(reply.plan, v, followup, turn='next', inherited=inherited), CheckedPlan)
    result = Planner(v, eng.w, Hidden([], set(), [])).answer(followup, reply, turn='next', history=history)
    assert result['kind'] == 'result' and result['guard']['passed'], result
    expected = sql(eng, "SELECT sum(CASE WHEN f.DW_TRAN_DIRCTN_CD = 'O' THEN -f.TRAN_AM ELSE f.TRAN_AM END) "
                   + BRANCH + " AND a.ACCT_RELT_SPS_IN = 'Y'")[0][0]
    assert cells(result['result']['rows'][0]) == [expected]


@pytest.mark.parametrize('context,phrase,table,base,amount', [
    ('nna_branch_sales', 'Branch sales NNA', 'TRAN_FACT_EXCL_MFCLR_G', BRANCH,
     "CASE WHEN f.DW_TRAN_DIRCTN_CD = 'O' THEN -f.TRAN_AM ELSE f.TRAN_AM END"),
    ('nna_certified', 'Certified NNA', 'F_NNA_FIN', FINANCE, 'f.NNA_AM'),
])
def test_native_direction_request_uses_only_release_and_answer_jobs(monkeypatch, tmp_path, context, phrase, table, base, amount):
    from tests.integration.test_planner_request_jobs import budgets, pilot
    fake, twin, opened, engines = pilot(monkeypatch, tmp_path)
    try:
        eng = opened()
        text = f'{phrase} with inflows during April 2025'
        v = vocabulary(eng, text)
        ctx = v.contexts[context]
        expected = sql(eng, 'SELECT sum(' + amount + ') ' + base + " AND f.DW_TRAN_DIRCTN_CD = 'I'")[0][0]
        fake.requests.clear()
        rows = run(eng, text, v, [measure(ctx, filters=[predicate('wealth.' + table + '.DW_TRAN_DIRCTN_CD', 'f', 'I')])],
                   {'inflows': 'f', 'April 2025': 'time'})
        assert cells(rows[0]) == [Decimal(str(expected))]
        assert budgets(fake) == dict(graph=0, retrieval=0, warehouse=2, dry=2, catalogue=0)
    finally:
        for eng in engines:
            eng.close()
        if twin.conn is not None:
            twin.conn.close()
            twin.db.close()
        fake.con.close()


def test_top_and_bottom_households_use_the_declared_household_grouping(eng):
    text = 'Top 3 and bottom 3 branch sales NNA by household during April 2025'
    v = vocabulary(eng, text)
    ctx = v.contexts['nna_branch_sales']
    rows = run(eng, text, v, [measure(ctx)], {'Top': 'm', 'bottom': 'm', '3': 'limit', 'by household': 'd', 'April 2025': 'time'},
               dimensions=[dimension(ctx, 'wealth.ACCT_DIM2_1.LAST_RCV_HSHLD_DK', ['branch_sales_account'])],
               order=[dict(output='m', direction='both', nulls='last', evidence=[])], limit=3)
    grouped = ("SELECT a.LAST_RCV_HSHLD_DK, sum(CASE WHEN f.DW_TRAN_DIRCTN_CD = 'O' THEN -f.TRAN_AM ELSE f.TRAN_AM END) amount "
               + BRANCH + ' GROUP BY a.LAST_RCV_HSHLD_DK')
    expected = []
    for end, direction in [('top', 'DESC'), ('bottom', 'ASC')]:
        expected.extend((end, rank, key, amount) for key, amount, rank in sql(eng,
            'WITH amounts AS (' + grouped + f'), ranked AS (SELECT *, row_number() OVER (ORDER BY amount {direction}, LAST_RCV_HSHLD_DK) r FROM amounts) '
            'SELECT * FROM ranked WHERE r <= 3'))
    assert {(r['end'], r['rank'], r['dimensions'][0]['value'], cells(r)[0]) for r in rows} == set(expected)


@pytest.mark.parametrize('context,phrase,path,base,amount', [
    ('nna_branch_sales', 'Branch sales NNA', 'branch_sales_date', BRANCH,
     "CASE WHEN f.DW_TRAN_DIRCTN_CD = 'O' THEN -f.TRAN_AM ELSE f.TRAN_AM END"),
    ('nna_certified', 'Certified NNA', 'finance_date', FINANCE, 'f.NNA_AM'),
    ('nna_client_directed', 'Client-directed NNA', 'finance_date',
     FINANCE.replace("f.FRZN_IN='Y'", "f.CLIENT_DIRECTED_NNA_IN='Y'"), 'f.NNA_AM'),
    ('nna', 'NNA', 'flow_date',
     """FROM TRAN_FACT_1 f JOIN MONTH_DIM_1 m ON f.MNTH_END_DT=m.CLDR_EOM_DT
WHERE f.MNTH_END_DT=DATE '2025-04-30' AND f.CURR_IN<>0 AND f.RCV_IN=1 AND f.NNA_IN='Y'
AND m.DW_BRKRG_MNTH_FRM_TODAY_CT<>0
AND NOT EXISTS (SELECT 1 FROM ACCT_DIM2_1 t WHERE t.DW_ACCT_ID=f.DW_ACCT_ID AND t.SRCE_SYS_ID=f.ACCT_SRCE_SYS_ID AND t.TEST_ACCT_IN='Y')""",
     "CASE WHEN f.DW_TRAN_DIRCTN_CD = 'O' THEN -f.TRAN_AM ELSE f.TRAN_AM END"),
])
@pytest.mark.parametrize('month', ['April', 'May', 'June'])
def test_nna_weekday_figures_use_transaction_dates(eng, context, phrase, path, base, amount, month):
    import calendar
    import datetime as dt
    month_number = ['April', 'May', 'June'].index(month) + 4
    end = dt.date(2025, month_number, calendar.monthrange(2025, month_number)[1])
    text = f'{phrase} by day of week during {month} 2025'
    v = vocabulary(eng, text)
    ctx = v.contexts[context]
    rows = run(eng, text, v, [measure(ctx)], {'by day of week': 'd', f'{month} 2025': 'time'},
               time=dict(kind='single', period=dict(kind='month', value=f'2025-{month_number:02}', calendar=name('calendar'))),
               dimensions=[dimension(ctx, 'wealth.DATE_DIM_1.DAY_OF_WEEK', [path])])
    plain = base.replace('2025-04-30', str(end)).replace('WHERE',
        'JOIN DATE_DIM_1 d ON f.DW_TRAN_DT=d.CALENDAR_DATE WHERE', 1)
    expected = sql(eng, 'SELECT d.DAY_OF_WEEK, sum(' + amount + ') ' + plain + ' GROUP BY d.DAY_OF_WEEK')
    assert {r['dimensions'][0]['value']: cells(r)[0] for r in rows} == dict(expected)
    if context == 'nna_branch_sales' and month == 'May':
        assert len([value for _, value in expected if value]) > 1
