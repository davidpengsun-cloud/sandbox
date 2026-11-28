"""kge/estate/golden20.py's difference: the first way an answer's SQL departs from a golden item's reference SQL, in
the order filter, join, period, grouping, measure, on synthetic changes to S10's reference SQL."""
import pytest
import sqlglot

from kge.estate.golden20 import difference, months, parts
from kge.estate.golden_sage import golden_20

S10 = next(item.sql for item, *_ in golden_20() if item.id == 'S10')


@pytest.mark.parametrize('change,what', [
    (("('CEO', 'NER')", "('CEO')"), 'filter'),
    (("CAST('2024-01-31' AS DATE)", "CAST('2024-02-29' AS DATE)"), 'period'),
    (("GROUP BY branch_branch_id, branch_branch", "GROUP BY branch_branch_id"), 'grouping'),
    (("SUM(IF(t.DW_TRAN_DIRCTN_CD = 'I', t.TRAN_AM, 0))",
      "SUM(IF(t.DW_TRAN_DIRCTN_CD = 'I', t.TRAN_SHR_QY, 0))"), 'measure'),
])
def test_the_first_difference_is_named_by_its_kind(change, what):
    old, new = change
    assert old in S10
    assert difference(S10.replace(old, new, 1), S10).startswith(what + ':')


def test_a_join_to_another_table_is_a_join_difference():
    asked = S10.replace("INNER JOIN BRCH_DIM_1 AS j4", "INNER JOIN RGN_DIM_1 AS j5 ON j5.RGN_CD = 'X' INNER JOIN BRCH_DIM_1 AS j4", 1)
    assert difference(asked, S10) == 'join: the answer has RGN_DIM_1'


def test_the_same_query_and_a_decline():
    assert difference(S10, S10) == 'no difference found in filters, joins, period, grouping or measures'
    assert difference(None, S10) == 'no SQL generated'


def test_a_half_open_month_is_the_month_a_month_end_names():
    ranged = sqlglot.parse_one("SELECT 1 FROM t WHERE d >= CAST('2024-01-01' AS DATE) AND d < CAST('2024-02-01' AS DATE)")
    assert months(ranged) == months(sqlglot.parse_one("SELECT 1 FROM t WHERE d = CAST('2024-01-31' AS DATE)")) == {'2024-01'}


def test_the_reference_sql_reads_its_filters_tables_and_measures():
    got = parts(S10)
    assert "RGN_CD IN ('CEO', 'NER')" in got['filter'] and got['period'] == {'2024-01'}
    assert got['join'] == {'TRAN_FACT_EXCL_MFCLR_G', 'MONTH_DIM_1', 'ACCT_DIM2_1', 'CLIENT_CD_ROLLUP_1', 'BRCH_DIM_1'}
    assert got['grouping'] == {'BRCH_CD', 'BRCH_NM'} and got['measure'] == {'DW_TRAN_DIRCTN_CD', 'TRAN_AM'}
