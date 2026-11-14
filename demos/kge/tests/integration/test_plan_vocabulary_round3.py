from decimal import Decimal

import pytest

from kge.semantics.coverage import ledger
from kge.semantics.plancheck import CheckedPlan, check_plan
from kge.services.ask.engine.planmodel import bundle
from tests.integration.test_plan_wording_round2 import cells, count, dimension, measure, predicate, run, sql, vocabulary
from tests.plan_fixtures import grounded, model, name


FINANCE = """FROM F_NNA_FIN f
JOIN ACCT_DIM2_1 a ON f.DW_ACCT_ID=a.DW_ACCT_ID AND f.ACCT_SRCE_SYS_ID=a.SRCE_SYS_ID
AND f.DW_TRAN_MNTH_END_DT BETWEEN a.DW_EFF_DT AND a.DW_EXPR_DT
JOIN CLIENT_CD_ROLLUP_1 r ON a.CLNT_CD=r.CLNT_CD AND f.DW_TRAN_MNTH_END_DT BETWEEN r.DW_EFF_DT AND r.DW_EXPR_DT
JOIN MONTH_DIM_1 m ON f.DW_TRAN_MNTH_END_DT=m.CLDR_EOM_DT
WHERE m.DW_BRKRG_MNTH_FRM_TODAY_CT<>0 AND f.FRZN_IN='Y'"""
DIRECTED = FINANCE.replace(" AND f.FRZN_IN='Y'", "") + " AND f.CLIENT_DIRECTED_NNA_IN='Y'"
SIGNED = "CASE WHEN f.DW_TRAN_DIRCTN_CD='O' THEN -f.TRAN_AM ELSE f.TRAN_AM END"

BRANCH_SALES = """FROM TRAN_FACT_EXCL_MFCLR_G f
JOIN MONTH_DIM_1 m ON f.MNTH_END_DT=m.CLDR_EOM_DT
JOIN ACCT_DIM2_1 a ON a.DW_ACCT_ID=f.DW_ACCT_ID AND a.SRCE_SYS_ID=f.ACCT_SRCE_SYS_ID
AND f.MNTH_END_DT BETWEEN a.DW_EFF_DT AND a.DW_EXPR_DT
JOIN CLIENT_CD_ROLLUP_1 r ON a.CLNT_CD=r.CLNT_CD AND f.MNTH_END_DT BETWEEN r.DW_EFF_DT AND r.DW_EXPR_DT
JOIN BRCH_DIM_1 b ON a.ACCT_DOMCL_BRCH_CD=b.BRCH_CD AND b.CURR_IN<>0
WHERE m.DW_BRKRG_MNTH_FRM_TODAY_CT<>0 AND m.FIN_MTRC_RLSE_DT BETWEEN f.DW_EFF_DT AND f.DW_EXPR_DT
AND f.NNA_IN='Y' AND f.RCV_IN=1 AND f.ACCT_SRCE_SYS_ID IN (100,200)
AND r.SEGMT_LVL_ID5='AFFLUENT' AND upper(b.BRCH_DIVSN_CD)='BR'
AND upper(coalesce(f.DW_TRAN_TYPE_CD,'')) NOT IN ('AQ','DT')"""


def single(value='2025-05'):
    return dict(kind='single', period=dict(kind='month', value=value, calendar=name('calendar')))


def window(kind='month', first='2025-04', last='2025-06'):
    return dict(kind='range', first=dict(kind=kind, value=first, calendar=name('calendar')),
                last=dict(kind=kind, value=last, calendar=name('calendar')))


def assignments(text, v, **updates):
    result = {p.text: 'time' for p in ledger(text, 'turn', v) if p.kind == 'time' or p.operation == 'range'}
    result.update(updates)
    return result


@pytest.mark.parametrize('wording', ['by business line', 'by sub-segment', 'by client segment level 6'])
def test_declared_children_grouping_reads_code_and_name(eng, wording):
    text = f'Break out certified NNA {wording} during May 2025 for segment level 5 PRIVATE'
    v = vocabulary(eng, text)
    ctx = v.contexts['nna_certified']
    path = ['finance_account', 'account_client_code']
    rows = run(eng, text, v, [measure(ctx)], assignments(text, v, **{wording: ['code', 'label'], 'segment level 5 PRIVATE': 'f'}),
               time=single(), filters=[predicate('wealth.CLIENT_CD_ROLLUP_1.SEGMT_LVL_ID5', 'f', 'PRIVATE', path)],
               dimensions=[dimension(ctx, 'wealth.CLIENT_CD_ROLLUP_1.SEGMT_LVL_ID6', path, 'code'),
                           dimension(ctx, 'wealth.CLIENT_CD_ROLLUP_1.SEGMT_LVL_NM6', path, 'label')])
    expected = sql(eng, 'SELECT r.SEGMT_LVL_ID6, r.SEGMT_LVL_NM6, sum(f.NNA_AM) ' + FINANCE
                   + " AND f.DW_TRAN_MNTH_END_DT=DATE '2025-05-31' AND r.SEGMT_LVL_ID5='PRIVATE' GROUP BY 1,2")
    assert {tuple(d['value'] for d in r['dimensions']): cells(r)[0] for r in rows} == {(code, label): amount for code, label, amount in expected}


@pytest.mark.parametrize('level', range(1, 8))
def test_client_segment_level_phrase_uses_the_declared_level(eng, level):
    phrase = f'by client segment level {level}'
    text = f'Give certified NNA during May 2025 {phrase}'
    v = vocabulary(eng, text)
    ctx = v.contexts['nna_certified']
    path = ['finance_account', 'account_client_code']
    dims = [dimension(ctx, f'wealth.CLIENT_CD_ROLLUP_1.SEGMT_LVL_ID{level}', path, 'code'),
            dimension(ctx, f'wealth.CLIENT_CD_ROLLUP_1.SEGMT_LVL_NM{level}', path, 'label')]
    rows = run(eng, text, v, [measure(ctx)], assignments(text, v, **{phrase: ['code', 'label']}), time=single(), dimensions=dims)
    expected = sql(eng, f'SELECT r.SEGMT_LVL_ID{level}, r.SEGMT_LVL_NM{level}, sum(f.NNA_AM) ' + FINANCE
                   + " AND f.DW_TRAN_MNTH_END_DT=DATE '2025-05-31' GROUP BY 1,2")
    assert {tuple(d['value'] for d in r['dimensions']): cells(r)[0] for r in rows} == {(code, label): amount for code, label, amount in expected}


@pytest.mark.parametrize('phrase,column', [('by account product code', 'ACCT_DETL_PROD_CD'),
                                         ('by jurisdiction country', 'JRSDCTN_CNTRY_CD'),
                                         ('jurisdiction countries', 'JRSDCTN_CNTRY_CD')])
def test_account_grouping_phrases_reconcile(eng, phrase, column):
    text = f'List certified NNA during May 2025 {phrase}'
    v = vocabulary(eng, text)
    ctx = v.contexts['nna_certified']
    rows = run(eng, text, v, [measure(ctx)], assignments(text, v, **{phrase: 'd'}), time=single(),
               dimensions=[dimension(ctx, 'wealth.ACCT_DIM2_1.' + column, ['finance_account'])])
    expected = sql(eng, f'SELECT a.{column}, sum(f.NNA_AM) ' + FINANCE + " AND f.DW_TRAN_MNTH_END_DT=DATE '2025-05-31' GROUP BY 1")
    assert {r['dimensions'][0]['value']: cells(r)[0] for r in rows} == dict(expected)


@pytest.mark.parametrize('phrase', ['certified May 2025 NNA', 'certified May 2025 client-directed NNA'])
def test_separated_certification_keeps_context_in_prompt_and_reconciles(eng, phrase):
    text = 'Give ' + phrase
    v = vocabulary(eng, text)
    directed = 'client-directed' in phrase
    ctx = v.contexts['nna_client_directed' if directed else 'nna_certified']
    assert 'nna_certified' in {c['id'] for c in bundle(v)['contexts']}
    filters = [predicate('wealth.F_NNA_FIN.FRZN_IN', 'frozen', 'Y')] if directed else []
    rows = run(eng, text, v, [measure(ctx)], assignments(text, v, **({'certified': 'frozen'} if directed else {})),
               time=single(), filters=filters)
    expected = sql(eng, 'SELECT sum(' + 'f.NNA_AM' + ') ' + (DIRECTED if directed else FINANCE)
                   + " AND f.DW_TRAN_MNTH_END_DT=DATE '2025-05-31'" + (" AND f.FRZN_IN='Y'" if directed else ''))[0][0]
    assert cells(rows[0]) == [expected]
    wrong = grounded(model(measures=[measure(v.contexts['nna'])], time=single()), text, v, assignments(text, v))
    assert not isinstance(check_plan(wrong, v, text), CheckedPlan)


@pytest.mark.parametrize('word', ['SPS', 'SPS accounts'])
def test_account_flag_is_a_predicate(eng, word):
    text = f'Give client-directed NNA during May 2025 from {word}'
    v = vocabulary(eng, text)
    ctx = v.contexts['nna_client_directed']
    rows = run(eng, text, v, [measure(ctx)], assignments(text, v, **{word: 'f'}), time=single(),
               filters=[predicate('wealth.ACCT_DIM2_1.ACCT_RELT_SPS_IN', 'f', 'Y', ['finance_account'])])
    expected = sql(eng, 'SELECT sum(f.NNA_AM) ' + DIRECTED + " AND f.DW_TRAN_MNTH_END_DT=DATE '2025-05-31' AND a.ACCT_RELT_SPS_IN='Y'")[0][0]
    assert cells(rows[0]) == [expected]


@pytest.mark.parametrize('word', ['contribution', 'contribute'])
def test_attribute_contribution_is_a_split_and_share(eng, word):
    text = f'Give SPS {word} to client-directed NNA during May 2025'
    v = vocabulary(eng, text)
    ctx = v.contexts['nna_client_directed']
    rows = run(eng, text, v, [measure(ctx)], assignments(text, v, **{'SPS': 'd', word: 's'}), time=single(),
               dimensions=[dimension(ctx, 'wealth.ACCT_DIM2_1.ACCT_RELT_SPS_IN', ['finance_account'])],
               derived=[dict(kind='share', id='s', input='m', remove_dimensions=['d'], remove_filters=[], evidence=[])])
    expected = dict(sql(eng, 'SELECT a.ACCT_RELT_SPS_IN, sum(f.NNA_AM) ' + DIRECTED
                        + " AND f.DW_TRAN_MNTH_END_DT=DATE '2025-05-31' GROUP BY 1"))
    assert {r['dimensions'][0]['value']: cells(r)[0] for r in rows} == expected
    for row in rows:
        amount, share = cells(row)
        assert share == pytest.approx(amount / sum(expected.values()))


@pytest.mark.parametrize('phrase', ['day of the week', 'weekday'])
@pytest.mark.parametrize('rank', ['highest', 'most'])
def test_weekday_without_by_ranks_all_seven_days(eng, phrase, rank):
    text = f'On which {phrase} was certified NNA {rank} during May 2025?'
    v = vocabulary(eng, text)
    ctx = v.contexts['nna_certified']
    rows = run(eng, text, v, [measure(ctx)], assignments(text, v, **{phrase: 'd', rank: 'm'}), time=single(),
               dimensions=[dimension(ctx, 'wealth.DATE_DIM_1.DAY_OF_WEEK', ['finance_date'])],
               order=[dict(output='m', direction='desc', nulls='last', evidence=[])])
    expected = sql(eng, 'SELECT d.DAY_OF_WEEK, sum(f.NNA_AM) ' + FINANCE.replace('WHERE', 'JOIN DATE_DIM_1 d ON d.CALENDAR_DATE=f.DW_TRAN_DT WHERE')
                   + " AND f.DW_TRAN_MNTH_END_DT=DATE '2025-05-31' GROUP BY 1 ORDER BY 2 DESC")
    assert [(r['dimensions'][0]['value'], cells(r)[0]) for r in rows] == expected
    assert len(rows) == 7


def test_month_range_with_account_and_transaction_counts(eng):
    text = 'Give certified NNA broken down by month from April to June 2025 with account and transaction counts'
    v = vocabulary(eng, text)
    ctx = v.contexts['nna_certified']
    rows = run(eng, text, v, [measure(ctx), count(ctx, 'wealth_account', 'a'), count(ctx, 'wealth_nna_reference', 'n')],
               assignments(text, v, **{'by': 'd', 'month': 'd', 'account': 'a', 'transaction': 'n', 'counts': ['a', 'n']}),
               time=window(), dimensions=[dict(kind='time', id='d', calendar=name('calendar'), grain='month')])
    expected = sql(eng, 'SELECT f.DW_TRAN_MNTH_END_DT, sum(f.NNA_AM), count(DISTINCT (f.ACCT_SRCE_SYS_ID,f.DW_ACCT_ID)), '
                   'count(DISTINCT (f.ACCT_SRCE_SYS_ID,f.DW_ACCT_ID,f.DW_TRAN_ID)) ' + FINANCE
                   + " AND f.DW_TRAN_MNTH_END_DT BETWEEN DATE '2025-04-30' AND DATE '2025-06-30' GROUP BY 1 ORDER BY 1")
    assert [cells(r) for r in rows] == [[a, b, c] for _, a, b, c in expected]


@pytest.mark.parametrize('wording,extra', [('Compare certified NNA in May 2025 with May 2024', {}),
                                         ('Give certified NNA for May 2025 versus May 2024 with dollar and percent difference',
                                          {'versus': 'c', 'dollar': 'df', 'percent difference': 'pc'})])
def test_two_period_comparison_accepts_dollar_and_percent_outputs(eng, wording, extra):
    text = wording
    v = vocabulary(eng, text)
    ctx = v.contexts['nna_certified']
    comparison = dict(kind='explicit_period', id='c', inputs=['m'], other=single('2024-05'), evidence=[])
    derived = [dict(kind=kind, id=identifier, numerator='c/m/base', denominator='c/m/other', evidence=[])
               for kind, identifier in [('difference', 'df'), ('percent_change', 'pc')]]
    rows = run(eng, text, v, [measure(ctx)], assignments(text, v, **{'Compare': ['c', 'df', 'pc'], 'May 2024': 'c', **extra}),
               time=single(), comparisons=[comparison], derived=derived)
    expected = dict(sql(eng, 'SELECT f.DW_TRAN_MNTH_END_DT, sum(f.NNA_AM) ' + FINANCE
                        + " AND f.DW_TRAN_MNTH_END_DT IN (DATE '2025-05-31',DATE '2024-05-31') GROUP BY 1"))
    current, prior = (value for _, value in sorted(expected.items(), reverse=True))
    assert cells(rows[0])[:3] == [current, current, prior]
    assert cells(rows[0])[-2:] == [current - prior, pytest.approx((current - prior) / abs(prior))]
    inverted = derived.copy()
    inverted[1] = {**inverted[1], 'numerator': 'c/m/other', 'denominator': 'c/m/base'}
    p = grounded(model(measures=[measure(ctx)], time=single(), comparisons=[comparison], derived=inverted), text, v,
                 assignments(text, v, **{'Compare': ['c', 'df', 'pc'], 'May 2024': 'c', **extra}))
    assert not isinstance(check_plan(p, v, text), CheckedPlan)
    if text.startswith('Compare'):
        missing_changes = grounded(model(measures=[measure(ctx)], time=single(), comparisons=[comparison]), text, v,
                                   assignments(text, v, **{'Compare': 'c', 'May 2024': 'c'}))
        decision = check_plan(missing_changes, v, text)
        assert decision.kind == 'abstain' and 'comparison requires dollar difference and percent change' in decision.missing


def test_current_and_certified_contexts_accept_both_difference_phrases(eng):
    text = 'Compare current NNA with certified NNA during Q2 2025 by month with dollar and percent difference'
    v = vocabulary(eng, text)
    current, certified = v.contexts['nna'], v.contexts['nna_certified']
    derived = [dict(kind=kind, id=identifier, numerator='c/base', denominator='c/other', evidence=[])
               for kind, identifier in [('difference', 'df'), ('percent_change', 'pc')]]
    rows = run(eng, text, v, [measure(current), measure(certified, 'cert')],
               assignments(text, v, **{'Compare': 'c', 'current': 'm', 'NNA': 'm', 'certified NNA': 'cert', 'by': 'd',
                                      'month': 'd', 'dollar': 'df', 'percent difference': 'pc'}),
               time=dict(kind='single', period=dict(kind='calendar_quarter', value='2025-Q2', calendar=name('calendar'))),
               dimensions=[dict(kind='time', id='d', calendar=name('calendar'), grain='month')],
               comparisons=[dict(kind='contexts', id='c', left='m', right='cert', evidence=[])], derived=derived)
    frozen = dict(sql(eng, 'SELECT f.DW_TRAN_MNTH_END_DT, sum(f.NNA_AM) ' + FINANCE
                      + " AND f.DW_TRAN_MNTH_END_DT BETWEEN DATE '2025-04-30' AND DATE '2025-06-30' GROUP BY 1"))
    base = """FROM TRAN_FACT_1 f JOIN MONTH_DIM_1 m ON f.MNTH_END_DT=m.CLDR_EOM_DT
WHERE m.DW_BRKRG_MNTH_FRM_TODAY_CT<>0 AND f.CURR_IN<>0 AND f.RCV_IN=1 AND f.NNA_IN='Y'
AND NOT EXISTS (SELECT 1 FROM ACCT_DIM2_1 a WHERE a.DW_ACCT_ID=f.DW_ACCT_ID
AND a.SRCE_SYS_ID=f.ACCT_SRCE_SYS_ID AND a.CURR_IN<>0 AND a.TEST_ACCT_IN='Y')"""
    actual = dict(sql(eng, 'SELECT f.MNTH_END_DT, sum(' + SIGNED + ') ' + base
                      + " AND f.MNTH_END_DT BETWEEN DATE '2025-04-30' AND DATE '2025-06-30' GROUP BY 1"))
    for row, month in zip(rows, sorted(frozen)):
        assert cells(row)[:4] == [actual[month], frozen[month], actual[month], frozen[month]]
        assert cells(row)[-2:] == [actual[month] - frozen[month], pytest.approx((actual[month] - frozen[month]) / abs(frozen[month]))]


def test_month_over_month_and_cumulative_are_declared_operations(eng):
    text = 'Give certified NNA by month from April to June 2025 with month-over-month % change and cumulative NNA'
    v = vocabulary(eng, text)
    ctx = v.contexts['nna_certified']
    comparison = dict(kind='previous_period', id='c', inputs=['m'], evidence=[])
    derived = [dict(kind=kind, id=identifier, numerator='c/m/base', denominator='c/m/other', evidence=[])
               for kind, identifier in [('difference', 'df'), ('percent_change', 'pc')]]
    derived.append(dict(kind='running_total', id='rt', input='m', partition_by=[], order_by='d', evidence=[]))
    rows = run(eng, text, v, [measure(ctx)], assignments(text, v, **{'by': 'd', 'month': 'd', 'month-over-month': 'c',
               '%': 'pc', 'change': 'df', 'cumulative': 'rt', 'NNA': 'rt'}), time=window(),
               dimensions=[dict(kind='time', id='d', calendar=name('calendar'), grain='month')], comparisons=[comparison], derived=derived)
    expected = sql(eng, 'SELECT f.DW_TRAN_MNTH_END_DT, sum(f.NNA_AM) ' + FINANCE
                   + " AND f.DW_TRAN_MNTH_END_DT BETWEEN DATE '2025-04-30' AND DATE '2025-06-30' GROUP BY 1 ORDER BY 1")
    amounts = [amount for _, amount in expected]
    for i, row in enumerate(rows):
        actual = [None if c['value'].get('value') is None else Decimal(str(c['value']['value'])) for c in row['cells']]
        assert actual[0] == amounts[i] and actual[-1] == sum(amounts[:i + 1])
        if i:
            assert actual[-3:-1] == [amounts[i] - amounts[i - 1], pytest.approx((amounts[i] - amounts[i - 1]) / abs(amounts[i - 1]))]
    wrong = grounded(model(measures=[measure(ctx)], time=window(),
                     dimensions=[dict(kind='time', id='d', calendar=name('calendar'), grain='month')],
                     comparisons=[{**comparison, 'kind': 'previous_year'}], derived=derived), text, v,
                     assignments(text, v, **{'by': 'd', 'month': 'd', 'month-over-month': 'c', '%': 'pc', 'change': 'df', 'cumulative': 'rt', 'NNA': 'rt'}))
    assert not isinstance(check_plan(wrong, v, text), CheckedPlan)


def test_suffix_region_and_each_branch_keep_net_and_parts(eng):
    text = 'Sum branch sales NNA inflows and outflows and net for the NORTHEAST region during May 2025 for each branch'
    v = vocabulary(eng, text)
    ctx = v.contexts['nna_branch_sales']
    col = 'wealth.TRAN_FACT_EXCL_MFCLR_G.DW_TRAN_DIRCTN_CD'
    office = ['branch_sales_account', 'account_current_office']
    rows = run(eng, text, v, [measure(ctx), measure(ctx, 'i', [predicate(col, 'fi', 'I')]), measure(ctx, 'o', [predicate(col, 'fo', 'O')])],
               assignments(text, v, **{'branch sales NNA': ['m', 'i', 'o'], 'inflows': 'fi', 'outflows': 'fo', 'NORTHEAST region': 'f', 'each branch': 'd'}),
               time=single(), dimensions=[dimension(ctx, 'wealth.BRCH_DIM_1.BRCH_CD', office)],
               filters=[predicate('wealth.BRCH_DIM_1.RGN_CD', 'f', 'NORTHEAST', office)])
    expected = sql(eng, 'SELECT b.BRCH_CD, sum(' + SIGNED + "), sum(CASE WHEN f.DW_TRAN_DIRCTN_CD='I' THEN f.TRAN_AM ELSE 0 END), "
                   "sum(CASE WHEN f.DW_TRAN_DIRCTN_CD='O' THEN -f.TRAN_AM ELSE 0 END) " + BRANCH_SALES
                   + " AND f.MNTH_END_DT=DATE '2025-05-31' AND b.RGN_CD='NORTHEAST' GROUP BY 1")
    assert {r['dimensions'][0]['value']: cells(r) for r in rows} == {code: [net, incoming, outgoing] for code, net, incoming, outgoing in expected}


@pytest.mark.parametrize('amount', ['$20K', '$20,000'])
def test_suffix_branch_amount_and_pronoun_reconcile_count_and_sum(eng, amount):
    text = f'How many transactions of {amount} or more did the BOS branch have during May 2025, and what was their branch sales NNA?'
    v = vocabulary(eng, text)
    ctx = v.contexts['nna_branch_sales']
    rows = run(eng, text, v, [measure(ctx), count(ctx, 'wealth_flow_excl_mf_clearing', 'n')],
               assignments(text, v, **{'how many': 'n', 'How many': 'n', 'transactions': 'n', amount: 'f', 'or more': 'f', 'BOS branch': 'b'}),
               time=single(), filters=[predicate('wealth.TRAN_FACT_EXCL_MFCLR_G.TRAN_AM', 'f', 20000, op='ge', kind='integer'),
                                       predicate('wealth.ACCT_DIM2_1.ACCT_DOMCL_BRCH_CD', 'b', 'BOS', ['branch_sales_account'])])
    expected = sql(eng, 'SELECT sum(' + SIGNED + '), count(DISTINCT (f.ACCT_SRCE_SYS_ID,f.DW_ACCT_ID,f.DW_TRAN_ID)) '
                   + BRANCH_SALES + " AND f.MNTH_END_DT=DATE '2025-05-31' AND a.ACCT_DOMCL_BRCH_CD='BOS' AND f.TRAN_AM>=20000")[0]
    assert cells(rows[0]) == list(expected)


@pytest.mark.parametrize('grouping', ['by sub-segment and quarter', 'by sub-segment for each quarter'])
def test_coordinated_quarters_and_children_share_the_trailing_year(eng, grouping):
    text = f'Give certified NNA {grouping} from Q1 to Q2 2025 for segment level 5 PRIVATE'
    v = vocabulary(eng, text)
    ctx = v.contexts['nna_certified']
    path = ['finance_account', 'account_client_code']
    rows = run(eng, text, v, [measure(ctx)], assignments(text, v, **{'by sub-segment': ['code', 'label'], 'and': 'd', 'each': 'd',
               'quarter': 'd', 'segment level 5 PRIVATE': 'f'}), time=window('calendar_quarter', '2025-Q1', '2025-Q2'),
               filters=[predicate('wealth.CLIENT_CD_ROLLUP_1.SEGMT_LVL_ID5', 'f', 'PRIVATE', path)],
               dimensions=[dimension(ctx, 'wealth.CLIENT_CD_ROLLUP_1.SEGMT_LVL_ID6', path, 'code'),
                           dimension(ctx, 'wealth.CLIENT_CD_ROLLUP_1.SEGMT_LVL_NM6', path, 'label'),
                           dict(kind='time', id='d', calendar=name('calendar'), grain='calendar_quarter')])
    expected = sql(eng, "SELECT r.SEGMT_LVL_ID6, r.SEGMT_LVL_NM6, cast(cast(date_trunc('quarter',f.DW_TRAN_MNTH_END_DT) AS DATE) AS VARCHAR), sum(f.NNA_AM) "
                   + FINANCE + " AND f.DW_TRAN_MNTH_END_DT BETWEEN DATE '2025-01-31' AND DATE '2025-06-30' AND r.SEGMT_LVL_ID5='PRIVATE' GROUP BY 1,2,3")
    assert {tuple(d['value'] for d in r['dimensions']): cells(r)[0] for r in rows} == {(code, label, quarter): value for code, label, quarter, value in expected}


def test_native_shared_grammar_keeps_the_two_job_request_budget(monkeypatch, tmp_path):
    from tests.integration.test_planner_request_jobs import budgets, pilot
    fake, twin, opened, engines = pilot(monkeypatch, tmp_path)
    try:
        eng = opened()
        text = 'Give certified May 2025 NNA by account product code'
        v = vocabulary(eng, text)
        ctx = v.contexts['nna_certified']
        fake.requests.clear()
        rows = run(eng, text, v, [measure(ctx)], assignments(text, v, **{'by account product code': 'd'}), time=single(),
                   dimensions=[dimension(ctx, 'wealth.ACCT_DIM2_1.ACCT_DETL_PROD_CD', ['finance_account'])])
        assert len(rows) == 8
        assert budgets(fake) == dict(graph=0, retrieval=0, warehouse=2, dry=2, catalogue=0)
    finally:
        for eng in engines:
            eng.close()
        if twin.conn is not None:
            twin.conn.close()
            twin.db.close()
        fake.con.close()


def test_ranked_household_plural_names_the_declared_grouping(eng):
    text = 'Which 2 households had the highest and lowest branch sales NNA during May 2025 in the BOS branch?'
    v = vocabulary(eng, text)
    ctx = v.contexts['nna_branch_sales']
    rows = run(eng, text, v, [measure(ctx)], assignments(text, v, **{'2': 'limit', 'households': 'd', 'highest': 'm', 'lowest': 'm', 'BOS branch': 'f'}),
               time=single(), dimensions=[dimension(ctx, 'wealth.ACCT_DIM2_1.LAST_RCV_HSHLD_DK', ['branch_sales_account'])],
               filters=[predicate('wealth.ACCT_DIM2_1.ACCT_DOMCL_BRCH_CD', 'f', 'BOS', ['branch_sales_account'])],
               order=[dict(output='m', direction='both', nulls='last', evidence=[])], limit=2)
    expected = sql(eng, 'SELECT a.LAST_RCV_HSHLD_DK, sum(' + SIGNED + ') amount ' + BRANCH_SALES
                   + " AND f.MNTH_END_DT=DATE '2025-05-31' AND a.ACCT_DOMCL_BRCH_CD='BOS' GROUP BY 1 ORDER BY 2 DESC,1")
    assert len(rows) == 4
    expected_keys = {key for key, _ in expected[:2] + expected[-2:]}
    assert {r['dimensions'][0]['value'] for r in rows} == expected_keys
    assert {r['dimensions'][0]['value']: cells(r)[0] for r in rows} == {key: value for key, value in expected if key in expected_keys}
