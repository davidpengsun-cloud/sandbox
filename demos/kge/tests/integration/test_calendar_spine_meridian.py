"""Week and complete-period answers reconcile to independent date-calendar SQL."""
from decimal import Decimal

import pytest

from kge.pipeline.sqlguard import schema_allowlist
from kge.ports import AUTHORED
from kge.semantics.query import Asked
from tests.integration.test_derived_shapes_meridian import answer, detail


def gated_rows(eng, statement, allow):
    guard = eng.w.gate('wealth', statement, allow, dialect=AUTHORED)
    assert guard['passed'], guard
    return eng.w.rows('wealth', statement, AUTHORED)


def reference(eng, grain, context='nna', first='2025-06-01', last='2025-07-01'):
    """Aggregate the declared population separately, then align to distinct released calendar dates."""
    aggregate = f"CAST(date_trunc('{grain}', f.DW_TRAN_DT) AS DATE)"
    population = ('TRAN_FACT_1' if context == 'nna' else 'TRAN_FACT_EXCL_MFCLR_G')
    query = f"""SELECT {aggregate}, SUM(f.TRAN_AM)
FROM {population} f JOIN MONTH_DIM_1 m ON f.MNTH_END_DT=m.CLDR_EOM_DT"""
    if context == 'nna_branch_sales':
        query += """ JOIN ACCT_DIM2_1 a ON f.DW_ACCT_ID=a.DW_ACCT_ID AND f.ACCT_SRCE_SYS_ID=a.SRCE_SYS_ID
AND f.MNTH_END_DT BETWEEN a.DW_EFF_DT AND a.DW_EXPR_DT
JOIN CLIENT_CD_ROLLUP_1 r ON a.CLNT_CD=r.CLNT_CD AND f.MNTH_END_DT BETWEEN r.DW_EFF_DT AND r.DW_EXPR_DT
JOIN BRCH_DIM_1 b ON a.ACCT_DOMCL_BRCH_CD=b.BRCH_CD AND b.CURR_IN=1"""
    query += f""" WHERE f.DW_TRAN_DT>=DATE '{first}' AND f.DW_TRAN_DT<DATE '{last}'
AND m.DW_BRKRG_MNTH_FRM_TODAY_CT<>0 AND f.NNA_IN='Y' AND f.RCV_IN=1"""
    if context == 'nna':
        query += ' AND f.CURR_IN=1'
    else:
        query += """ AND m.FIN_MTRC_RLSE_DT BETWEEN f.DW_EFF_DT AND f.DW_EXPR_DT
AND f.ACCT_SRCE_SYS_ID IN (100,200) AND r.SEGMT_LVL_ID5='S4100'
AND upper(b.BRCH_DIVSN_CD)='BR' AND upper(coalesce(f.DW_TRAN_TYPE_CD,'')) NOT IN ('AQ','DT')"""
    query += ' GROUP BY 1'
    plan = eng.semantics.plan(eng.semantics.contexts[context], Asked())
    observations = {str(row[0]): Decimal(str(row[1])) for row in gated_rows(eng, query, eng.semantics.allowlist(plan))}
    ticks = f"""SELECT DISTINCT CAST(date_trunc('{grain}', CALENDAR_DATE) AS DATE)
FROM DATE_DIM_1 WHERE CALENDAR_DATE>=DATE '{first}' AND CALENDAR_DATE<DATE '{last}' ORDER BY 1"""
    dates = gated_rows(eng, ticks, schema_allowlist(eng.g, 'wealth', eng.RESTRICTED_COLUMNS))
    return {(str(row[0]),): observations.get(str(row[0]), Decimal(0)) for row in dates}


@pytest.mark.parametrize('modeled', [False, True], ids=['rules', 'model-plan'])
@pytest.mark.parametrize('grain,word', [('day', 'day'), ('week', 'week'), ('month', 'month'),
                                       ('quarter', 'quarter'), ('year', 'year')])
def test_every_period_matches_the_declared_daily_calendar(eng, modeled, grain, word):
    plan, result = answer(eng, f'NNA by {word} for June 2025', modeled)
    measure, = plan.measures
    actual = {key: cells[measure.id] for key, cells, _ in detail(result)}
    expected = reference(eng, grain)
    assert actual == expected
    assert len(actual) == {'day': 30, 'week': 6, 'month': 1, 'quarter': 1, 'year': 1}[grain]


@pytest.mark.parametrize('modeled', [False, True], ids=['rules', 'model-plan'])
@pytest.mark.parametrize('context,term', [('nna', 'NNA'), ('nna_branch_sales', 'branch sales NNA')])
@pytest.mark.parametrize('word', ['by week', 'weekly'])
def test_transaction_views_week_cells_pass(eng, modeled, context, term, word):
    plan, result = answer(eng, f'{term} {word} for January 2025', modeled)
    measure, = plan.measures
    assert {key: cells[measure.id] for key, cells, _ in detail(result)} == reference(
        eng, 'week', context, '2025-01-01', '2025-02-01')
    assert plan.dimensions[0].grain == 'week'


@pytest.mark.parametrize('modeled', [False, True], ids=['rules', 'model-plan'])
def test_week_over_week_uses_the_preceding_seven_days(eng, modeled):
    plan, result = answer(eng, 'NNA by week for January 2025 week over week', modeled)
    measure, = plan.measures
    comparison, = plan.comparisons
    current = reference(eng, 'week', first='2025-01-01', last='2025-02-01')
    previous = reference(eng, 'week', first='2024-12-25', last='2025-01-25')
    from datetime import date, timedelta
    shifted = {(str(date.fromisoformat(key[0]) + timedelta(days=7)),): value for key, value in previous.items()}
    rows = list(detail(result))
    assert {key: cells[measure.id] for key, cells, _ in rows} == current
    assert {key: cells[f'{comparison.id}/{measure.id}/other'] for key, cells, _ in rows} == shifted


def test_calendar_week_number_uses_iso_year_derived_from_the_date(eng):
    query = """SELECT CALENDAR_DATE, WEEK_NUMBER, YEAR_VAL, EXTRACT(ISOYEAR FROM CALENDAR_DATE)
FROM DATE_DIM_1 WHERE CALENDAR_DATE BETWEEN DATE '2024-12-30' AND DATE '2025-01-05' ORDER BY 1"""
    rows = gated_rows(eng, query, schema_allowlist(eng.g, 'wealth', eng.RESTRICTED_COLUMNS))
    assert len(rows) == 7
    assert all(row[1] == 1 and row[3] == 2025 for row in rows)
    assert rows[0][2] == 2024


@pytest.mark.skip(reason='not applicable: month grain')
@pytest.mark.parametrize('grain', ['day', 'week'])
def test_certified_nna_submonth_grid_cells(grain):
    """Frozen monthly observations have no daily or weekly figures."""
