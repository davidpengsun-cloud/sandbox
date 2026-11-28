"""Declared recurring calendar values reconcile Ask paths with independently authored sample SQL."""
from decimal import Decimal

import pytest

from kge.pipeline.sqlguard import schema_allowlist
from kge.ports import AUTHORED
from kge.services.data.engine.governance import declared_allowlist
from tests.integration.test_measure_capabilities import rules_and_replay


FLOW = "f.TRAN_AM"
CURRENT = """FROM TRAN_FACT_1 f JOIN MONTH_DIM_1 m ON f.MNTH_END_DT=m.CLDR_EOM_DT
WHERE m.DW_BRKRG_MNTH_FRM_TODAY_CT<>0 AND f.NNA_IN='Y' AND f.RCV_IN=1 AND f.CURR_IN=1"""
BRANCH = """FROM TRAN_FACT_EXCL_MFCLR_G f JOIN MONTH_DIM_1 m ON f.MNTH_END_DT=m.CLDR_EOM_DT
JOIN ACCT_DIM2_1 a ON f.DW_ACCT_ID=a.DW_ACCT_ID AND f.ACCT_SRCE_SYS_ID=a.SRCE_SYS_ID
AND f.MNTH_END_DT BETWEEN a.DW_EFF_DT AND a.DW_EXPR_DT
JOIN CLIENT_CD_ROLLUP_1 r ON a.CLNT_CD=r.CLNT_CD AND f.MNTH_END_DT BETWEEN r.DW_EFF_DT AND r.DW_EXPR_DT
JOIN BRCH_DIM_1 b ON a.ACCT_DOMCL_BRCH_CD=b.BRCH_CD AND b.CURR_IN=1
WHERE m.DW_BRKRG_MNTH_FRM_TODAY_CT<>0 AND m.FIN_MTRC_RLSE_DT BETWEEN f.DW_EFF_DT AND f.DW_EXPR_DT
AND f.NNA_IN='Y' AND f.RCV_IN=1 AND f.ACCT_SRCE_SYS_ID IN (100,200)
AND r.SEGMT_LVL_ID5='S4100' AND upper(b.BRCH_DIVSN_CD)='BR'
AND upper(coalesce(f.DW_TRAN_TYPE_CD,'')) NOT IN ('AQ','DT')"""
CERTIFIED = """FROM F_NNA_FIN f JOIN MONTH_DIM_1 m ON f.DW_TRAN_MNTH_END_DT=m.CLDR_EOM_DT
WHERE m.DW_BRKRG_MNTH_FRM_TODAY_CT<>0 AND f.FRZN_IN='Y'"""
CONTEXTS = [('NNA', FLOW, CURRENT, 'DW_TRAN_DT'),
            ('branch sales NNA', FLOW, BRANCH, 'DW_TRAN_DT'),
            ('certified NNA', 'f.NNA_AM', CERTIFIED, 'DW_TRAN_MNTH_END_DT')]


def independent(eng, statement):
    """The governed reference path checks the untouched independent statement before execution."""
    allow = declared_allowlist(eng, 'wealth', schema_allowlist(eng.g, 'wealth', eng.RESTRICTED_COLUMNS))
    guard = eng.w.gate('wealth', statement, allow, dialect=AUTHORED)
    assert guard['passed'], guard
    return eng.w.rows('wealth', statement, AUTHORED)


def amounts(values):
    return {key: next(iter(row.values())) for key, row in values.items()}


@pytest.mark.parametrize('label,expression,source,period', CONTEXTS)
def test_a_declared_month_of_year_matches_both_ask_paths_and_reference_sql(eng, label, expression, source, period):
    question = f'{label} for every June, 2024 to 2024'
    plan, values = rules_and_replay(eng, question, grouped=True)
    assert [dimension.grain for dimension in plan.dimensions] == ['year']
    assert any(predicate.kind == 'compare' and predicate.left.kind == 'column'
               and predicate.left.name.id == 'wealth.MONTH_DIM_1.MNTH_OF_YEAR_CT'
               and predicate.value.value == 6 for predicate in plan.nodes())
    rows = independent(eng, f"""SELECT cast(date_trunc('year',f.{period}) AS DATE) AS year_start, sum({expression}) AS amount {source}
AND f.{period}>=DATE '2024-01-01' AND f.{period}<DATE '2025-01-01' AND m.MNTH_OF_YEAR_CT=6 GROUP BY 1""")
    expected = {(day.isoformat(),): Decimal(str(value)) for day, value in rows}
    assert amounts(values) == expected


@pytest.mark.parametrize('label,expression,source,period', CONTEXTS)
def test_week_of_month_matches_both_ask_paths_and_independent_sql(eng, label, expression, source, period):
    if label == 'certified NNA':
        pytest.skip('not applicable: month grain')
    question = f'{label} for week 2 of each month for January through March 2024'
    plan, values = rules_and_replay(eng, question, grouped=True)
    assert [dimension.grain for dimension in plan.dimensions] == ['month']
    joined = source.replace('WHERE m.', 'JOIN DATE_DIM_1 d ON f.DW_TRAN_DT=d.CALENDAR_DATE\nWHERE m.', 1)
    rows = independent(eng, f"""SELECT cast(date_trunc('month',f.{period}) AS DATE) AS month_start, sum({expression}) AS amount {joined}
AND f.{period}>=DATE '2024-01-01' AND f.{period}<DATE '2024-04-01' AND d.WEEK_OF_MONTH_NUMBER=2 GROUP BY 1""")
    observed = {day.isoformat(): Decimal(str(value)) for day, value in rows}
    expected = {(f'2024-{month:02d}-01',): observed.get(f'2024-{month:02d}-01', Decimal(0)) for month in range(1, 4)}
    assert amounts(values) == expected


def test_a_month_grain_measure_refuses_a_recurring_week_filter(eng):
    answer = eng.answer('certified NNA for week 2 of each month for January through March 2024', 'analyst_enterprise')
    assert answer['behaviour'] not in ('figure', 'combined')
    assert answer['reason'] == 'This figure is recorded only by a longer period than the breakdown asks for.'


def test_an_explicit_recurring_range_does_not_silently_omit_unreleased_years(eng):
    answer = eng.answer('NNA for every June, 2023 to 2025', 'analyst_enterprise')
    assert answer['behaviour'] not in ('figure', 'combined')
    assert any(word in answer['reason'] for word in ('not released', 'not booked', 'release calendar'))
