"""Period readings reconcile with independent, gated SQL on both transaction facts."""
from decimal import Decimal

import pytest

from kge.ports import AUTHORED
from kge.semantics.planperiods import time_bounds
from kge.semantics.query import Asked
from tests.integration.test_derived_shapes_meridian import answer, detail, reference


CONTEXTS = [('NNA', 'nna'), ('Branch sales NNA', 'nna_branch_sales')]
RANGES = [
    ('January through September 2024', '2024-01-01', '2024-10-01'),
    ('Jan-Sep 2024', '2024-01-01', '2024-10-01'),
    ('Jan–Sep 2024', '2024-01-01', '2024-10-01'),
    ('January 2024 through September 2024', '2024-01-01', '2024-10-01'),
    ('from January to September 2024', '2024-01-01', '2024-10-01'),
    ('between January and September 2024', '2024-01-01', '2024-10-01'),
    ('first nine months of 2024', '2024-01-01', '2024-10-01'),
    ('Q1 through Q3 2024', '2024-01-01', '2024-10-01'),
    ('Q1-Q3 2024', '2024-01-01', '2024-10-01'),
    ('first three quarters of 2024', '2024-01-01', '2024-10-01'),
    ('year to date through September 2024', '2024-01-01', '2024-10-01'),
    ('YTD through September 2024', '2024-01-01', '2024-10-01'),
    ('YTD through 18 June 2024', '2024-01-01', '2024-06-19'),
    ('calendar year to date through September 2024', '2024-01-01', '2024-10-01'),
    ('YTD through 2024', '2024-01-01', '2025-01-01'),
    ('fiscal year to date through September 2024', '2024-04-01', '2024-10-01'),
    ('fiscal YTD through 2024', '2024-04-01', '2025-01-01'),
    ('FY25-Q1 through FY25-Q2', '2024-04-01', '2024-10-01'),
    ('first six fiscal months of FY25', '2024-04-01', '2024-10-01'),
    ('month to date through 2024-06-18', '2024-06-01', '2024-06-19'),
    ('MTD through 2024-06-18', '2024-06-01', '2024-06-19'),
    ('MTD through 18 June 2024', '2024-06-01', '2024-06-19'),
    ('MTD through 2024', '2024-12-01', '2025-01-01'),
    ('quarter to date through 2024-05-18', '2024-04-01', '2024-05-19'),
    ('QTD through 2024-05-18', '2024-04-01', '2024-05-19'),
    ('QTD through 18 June 2024', '2024-04-01', '2024-06-19'),
    ('QTD through 2024', '2024-10-01', '2025-01-01'),
    ('fiscal quarter to date through 2024-05-18', '2024-04-01', '2024-05-19'),
]


def branch_reference(eng, first, last, by_month=False, grain=None):
    """Apply the declared release and month-end population, independent of Ask's SQL."""
    dimension = ("d.WEEK_OF_MONTH_NUMBER" if grain == 'week of month' else
                 f"CAST(date_trunc('{grain}', f.DW_TRAN_DT) AS DATE)" if grain else
                 "CAST(date_trunc('month', f.DW_TRAN_DT) AS DATE)" if by_month else None)
    dimensions = [dimension] if dimension else []
    statement = 'SELECT ' + ', '.join([*dimensions,
        "COALESCE(SUM(f.TRAN_AM),0)"])
    statement += """ FROM TRAN_FACT_EXCL_MFCLR_G f
JOIN MONTH_DIM_1 m ON f.MNTH_END_DT=m.CLDR_EOM_DT
JOIN ACCT_DIM2_1 a ON f.DW_ACCT_ID=a.DW_ACCT_ID AND f.ACCT_SRCE_SYS_ID=a.SRCE_SYS_ID
AND f.MNTH_END_DT BETWEEN a.DW_EFF_DT AND a.DW_EXPR_DT
JOIN CLIENT_CD_ROLLUP_1 r ON a.CLNT_CD=r.CLNT_CD AND f.MNTH_END_DT BETWEEN r.DW_EFF_DT AND r.DW_EXPR_DT
JOIN BRCH_DIM_1 b ON a.ACCT_DOMCL_BRCH_CD=b.BRCH_CD AND b.CURR_IN=1"""
    if grain == 'week of month':
        statement += ' JOIN DATE_DIM_1 d ON f.DW_TRAN_DT=d.CALENDAR_DATE'
    statement += f"""
WHERE m.DW_BRKRG_MNTH_FRM_TODAY_CT<>0 AND f.DW_TRAN_DT>=DATE '{first}' AND f.DW_TRAN_DT<DATE '{last}'
AND m.FIN_MTRC_RLSE_DT BETWEEN f.DW_EFF_DT AND f.DW_EXPR_DT
AND f.RCV_IN=1 AND f.NNA_IN='Y' AND f.ACCT_SRCE_SYS_ID IN (100,200)
AND r.SEGMT_LVL_ID5='S4100' AND upper(b.BRCH_DIVSN_CD)='BR'
AND upper(coalesce(f.DW_TRAN_TYPE_CD,'')) NOT IN ('AQ','DT')"""
    if dimensions:
        statement += ' GROUP BY 1'
    context = eng.semantics.contexts['nna_branch_sales']
    allow = eng.semantics.allowlist(eng.semantics.plan(context, Asked()))
    guard = eng.w.gate('wealth', statement, allow, dialect=AUTHORED)
    assert guard['passed'], guard
    return {tuple(str(value) for value in row[:-1]): Decimal(str(row[-1]))
            for row in eng.w.rows('wealth', statement, AUTHORED)}


def expected(eng, context, first, last, by_month=False):
    if context == 'nna_branch_sales':
        return branch_reference(eng, first, last, by_month)
    return reference(eng, ('month',) if by_month else (), first=first, last=last)


@pytest.mark.parametrize('modeled', [False, True], ids=['rules', 'model-plan'])
@pytest.mark.parametrize('wording,context', CONTEXTS)
@pytest.mark.parametrize('period,first,last', RANGES)
def test_each_range_form_keeps_the_whole_window_and_reference_figure(eng, modeled, wording, context, period, first, last):
    plan, result = answer(eng, f'{wording} for {period}', modeled)
    window = time_bounds(plan.time, eng.CALENDARS)
    assert tuple(value.isoformat() for value in window) == (first, last)
    actual = {key: cells[plan.measures[0].id] for key, cells, _ in detail(result)}
    assert actual == expected(eng, context, first, last)


@pytest.mark.parametrize('modeled', [False, True], ids=['rules', 'model-plan'])
@pytest.mark.parametrize('wording,context', CONTEXTS)
@pytest.mark.parametrize('period', ['January through September 2024', 'Jan-Sep 2024', 'first three quarters of 2024',
                                   'year to date through September 2024'])
def test_a_month_breakdown_stays_inside_the_whole_range(eng, modeled, wording, context, period):
    plan, result = answer(eng, f'{wording} by month for {period}', modeled)
    actual = {key: cells[plan.measures[0].id] for key, cells, _ in detail(result)}
    assert actual == expected(eng, context, '2024-01-01', '2024-10-01', True)
    assert sorted(actual) == [(f'2024-{month:02d}-01',) for month in range(1, 10)]


def latest_released_month_end(eng):
    """The release calendar's latest released month on or before the calendar's current period, by its own SQL."""
    current = eng.CALENDARS['calendar']['current_period']
    statement = f"""SELECT max(CLDR_EOM_DT) FROM MONTH_DIM_1
WHERE DW_BRKRG_MNTH_FRM_TODAY_CT<>0 AND CLDR_EOM_DT<=last_day(DATE '{current}')"""
    allow = eng.semantics.allowlist(eng.semantics.plan(eng.semantics.contexts['nna'], Asked()))
    guard = eng.w.gate('wealth', statement, allow, dialect=AUTHORED)
    assert guard['passed'], guard
    return eng.w.rows('wealth', statement, AUTHORED)[0][0]


@pytest.mark.parametrize('period,label,start', [
    ('year to date', 'year to date', lambda end: end.replace(month=1, day=1)),
    ('month to date', 'month to date', lambda end: end.replace(day=1)),
    ('quarter to date', 'quarter to date', lambda end: end.replace(month=end.month - (end.month - 1) % 3, day=1)),
    ('fiscal year to date', 'fiscal year to date', lambda end: end.replace(year=end.year - (end.month < 4), month=4, day=1)),
])
def test_current_to_date_ends_at_the_latest_released_month(eng, period, label, start):
    """The pack's current period sits in an open month: the window ends at the latest released month and says so."""
    import datetime
    end = latest_released_month_end(eng)
    assert end < datetime.date.fromisoformat(eng.CALENDARS['calendar']['current_period'])
    got = eng.answer('NNA ' + period, 'analyst_enterprise')
    assert got['behaviour'] in ('figure', 'combined'), got.get('reason')
    assert got['period_label'].startswith(label + ', ') and got['period_label'].endswith(f'{end:%b %Y}'), got['period_label']
    first, last = start(end), end + datetime.timedelta(days=1)
    assert got['months'][-1] == end.isoformat() and got['months'][0][:7] == first.isoformat()[:7], got['months']
    assert Decimal(str(got['figure'])) == expected(eng, 'nna', first.isoformat(), last.isoformat())[()]


def test_a_stated_cutoff_in_the_open_month_still_refuses(eng):
    got = eng.answer('NNA year to date through July 2025', 'analyst_enterprise')
    assert got['behaviour'] in ('abstain', 'abstain_with_reason'), got
    assert 'July 2025' in got['reason'] and 'released' in got['reason'], got['reason']


def grid_reference(eng, context, grain):
    """Use the warehouse's declared day spine, including buckets with no observations."""
    if context == 'nna_branch_sales':
        observed = branch_reference(eng, '2025-06-01', '2025-07-01', grain=grain)
    else:
        dimension = ('d.WEEK_OF_MONTH_NUMBER' if grain == 'week of month' else
                     f"CAST(date_trunc('{grain}', f.DW_TRAN_DT) AS DATE)")
        statement = f"""SELECT {dimension},
COALESCE(SUM(f.TRAN_AM),0)
FROM TRAN_FACT_1 f JOIN MONTH_DIM_1 m ON f.MNTH_END_DT=m.CLDR_EOM_DT"""
        if grain == 'week of month':
            statement += ' JOIN DATE_DIM_1 d ON f.DW_TRAN_DT=d.CALENDAR_DATE'
        statement += """ WHERE m.DW_BRKRG_MNTH_FRM_TODAY_CT<>0
AND f.DW_TRAN_DT>=DATE '2025-06-01' AND f.DW_TRAN_DT<DATE '2025-07-01'
AND f.NNA_IN='Y' AND f.RCV_IN=1 AND f.CURR_IN=1 GROUP BY 1"""
        declaration = eng.semantics.contexts[context]
        allow = eng.semantics.allowlist(eng.semantics.plan(declaration, Asked()))
        guard = eng.w.gate('wealth', statement, allow, dialect=AUTHORED)
        assert guard['passed'], guard
        observed = {(str(key),): Decimal(str(value)) for key, value in eng.w.rows('wealth', statement, AUTHORED)}
    dimension = ('WEEK_OF_MONTH_NUMBER' if grain == 'week of month' else
                 f"CAST(date_trunc('{grain}', CALENDAR_DATE) AS DATE)")
    spine = f"""SELECT DISTINCT {dimension} FROM DATE_DIM_1
WHERE CALENDAR_DATE>=DATE '2025-06-01' AND CALENDAR_DATE<DATE '2025-07-01'"""
    declaration = eng.semantics.contexts[context]
    allow = eng.semantics.allowlist(eng.semantics.plan(declaration, Asked()))
    guard = eng.w.gate('wealth', spine, allow, dialect=AUTHORED)
    assert guard['passed'], guard
    return {(str(key),): observed.get((str(key),), Decimal(0)) for key, in eng.w.rows('wealth', spine, AUTHORED)}


@pytest.mark.parametrize('modeled', [False, True], ids=['rules', 'model-plan'])
@pytest.mark.parametrize('wording,context', CONTEXTS)
@pytest.mark.parametrize('grain', ['day', 'week', 'week of month'])
def test_submonth_grid_uses_the_transaction_facts_and_complete_day_spine(eng, modeled, wording, context, grain):
    plan, result = answer(eng, f'{wording} by {grain} for June 2025', modeled)
    actual = {key: cells[plan.measures[0].id] for key, cells, _ in detail(result)}
    assert actual == grid_reference(eng, context, grain)


@pytest.mark.parametrize('grain', ['day', 'week', 'week of month'])
def test_certified_submonth_grid_is_not_a_meaningful_time_breakdown(grain):
    pytest.skip('not applicable: month grain')
