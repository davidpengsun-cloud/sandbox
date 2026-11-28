"""Offset figures on both transaction facts, independently of the compiler's date arithmetic.

The sample has no two complete calendar years. Annual cells therefore group one
released month by year and compare it with that month in the preceding year.
"""
from decimal import Decimal

import pytest

from kge.pipeline.sqlguard import schema_allowlist
from kge.ports import AUTHORED
from kge.services.data.engine.governance import declared_allowlist
from tests.integration.test_measure_capabilities import rules_and_replay


FLOW = "f.TRAN_AM"
FACTS = (
    ('nna', 'NNA', 'TRAN_FACT_1'),
    ('nna_branch_sales', 'branch sales NNA', 'TRAN_FACT_EXCL_MFCLR_G'),
)
PERIODS = (
    ('day', '2025-06-10', ('2025-06-10', '2025-06-11'),
     ('2025-06-09', '2025-06-10'), ('2024-06-10', '2024-06-11')),
    ('week', '2025-W24', ('2025-06-09', '2025-06-16'),
     ('2025-06-02', '2025-06-09'), ('2024-06-10', '2024-06-17')),
    ('month', 'June 2025', ('2025-06-01', '2025-07-01'),
     ('2025-05-01', '2025-06-01'), ('2024-06-01', '2024-07-01')),
    ('calendar_quarter', 'Q2 2025', ('2025-04-01', '2025-07-01'),
     ('2025-01-01', '2025-04-01'), ('2024-04-01', '2024-07-01')),
    ('year', 'September 2024', ('2024-09-01', '2024-10-01'),
     ('2023-09-01', '2023-10-01'), ('2023-09-01', '2023-10-01')),
)
OFFSETS = (
    ('previous_period', 'previous period'),
    ('previous_year', 'the same period a year earlier'),
)


def reference(eng, statement):
    """Use the golden reference's declared data-query gate, then the warehouse port."""
    allow = declared_allowlist(eng, 'wealth', schema_allowlist(eng.g, 'wealth', eng.RESTRICTED_COLUMNS))
    guard = eng.w.gate('wealth', statement, allow, dialect=AUTHORED)
    assert guard['passed'], guard
    return eng.w.rows('wealth', statement, AUTHORED)


def reference_amount(eng, table, window):
    """The fact's business definition, written without a generated plan or compiled SQL."""
    first, last = window
    period = f"f.DW_TRAN_DT >= DATE '{first}' AND f.DW_TRAN_DT < DATE '{last}'"
    if table == 'TRAN_FACT_1':
        statement = f"""SELECT coalesce(sum({FLOW}), 0)
FROM TRAN_FACT_1 f JOIN MONTH_DIM_1 m ON f.MNTH_END_DT=m.CLDR_EOM_DT
WHERE {period} AND m.DW_BRKRG_MNTH_FRM_TODAY_CT<>0
AND f.NNA_IN='Y' AND f.RCV_IN=1 AND f.CURR_IN=1"""
    else:
        statement = f"""SELECT coalesce(sum({FLOW}), 0)
FROM TRAN_FACT_EXCL_MFCLR_G f JOIN MONTH_DIM_1 m ON f.MNTH_END_DT=m.CLDR_EOM_DT
AND m.FIN_MTRC_RLSE_DT BETWEEN f.DW_EFF_DT AND f.DW_EXPR_DT
JOIN ACCT_DIM2_1 a ON f.DW_ACCT_ID=a.DW_ACCT_ID AND f.ACCT_SRCE_SYS_ID=a.SRCE_SYS_ID
AND f.MNTH_END_DT BETWEEN a.DW_EFF_DT AND a.DW_EXPR_DT
JOIN CLIENT_CD_ROLLUP_1 r ON a.CLNT_CD=r.CLNT_CD
AND f.MNTH_END_DT BETWEEN r.DW_EFF_DT AND r.DW_EXPR_DT
JOIN BRCH_DIM_1 b ON a.ACCT_DOMCL_BRCH_CD=b.BRCH_CD AND b.CURR_IN<>0
WHERE {period} AND m.DW_BRKRG_MNTH_FRM_TODAY_CT<>0 AND f.NNA_IN='Y' AND f.RCV_IN=1
AND f.ACCT_SRCE_SYS_ID IN (100,200) AND r.SEGMT_LVL_ID5='S4100'
AND upper(b.BRCH_DIVSN_CD)='BR' AND upper(coalesce(f.DW_TRAN_TYPE_CD,'')) NOT IN ('AQ','DT')"""
    amount, = reference(eng, statement)[0]
    return Decimal(str(amount))


@pytest.mark.parametrize('context,label,table', FACTS)
@pytest.mark.parametrize('grain,period,window,prior,annual', PERIODS)
@pytest.mark.parametrize('kind,wording', OFFSETS)
def test_generated_offset_grid_matches_independent_sql_on_both_paths(
        eng, context, label, table, grain, period, window, prior, annual, kind, wording):
    grouping = ' by year' if grain == 'year' else ''
    question = f'{label}{grouping} for {period} versus {wording}, with difference and percent change'
    plan, results = rules_and_replay(eng, question, grouped=True)
    measure, = plan.measures
    comparison, = plan.comparisons
    assert measure.context.id == context
    assert comparison.kind == kind and comparison.inputs == (measure.id,)
    if grain == 'year':
        dimension, = plan.dimensions
        assert dimension.kind == 'time' and dimension.grain == grain
    current = reference_amount(eng, table, window)
    other = reference_amount(eng, table, annual if kind == 'previous_year' else prior)
    expected = {measure.id: current, f'{comparison.id}/{measure.id}/other': other}
    assert {node.kind for node in plan.derived} == {'difference', 'percent_change'}
    for node in plan.derived:
        expected[node.id] = current - other if node.kind == 'difference' else (
            (current - other) / abs(other) if other else None)
    row, = results.values()
    assert set(results) == {('2024-01-01',)} if grain == 'year' else set(results) == {()}
    assert set(row) == set(expected)
    for output, amount in expected.items():
        assert row[output] == pytest.approx(amount) if amount is not None else row[output] is None


@pytest.mark.parametrize('grain', ('day', 'week', 'week_of_month'))
@pytest.mark.parametrize('kind,wording', OFFSETS)
def test_certified_nna_finer_calendar_cells_are_not_applicable(grain, kind, wording):
    """A month-grain fact cannot certify day, week or week-of-month comparisons."""
    pytest.skip('not applicable: month grain')
