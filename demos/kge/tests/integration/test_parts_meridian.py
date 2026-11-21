"""A measure split by a column's declared values is shown with its unsplit figure under the pack's whole label (the
value vocabulary's `whole`: Meridian's flow direction declares Net), whatever words coordinate the parts, and a named
filter reaches every output (issue #474, C5). Figures are checked against SQL written here on the sample."""
from decimal import Decimal

import pytest

from kge.estate.golden import warehouses
from kge.estate.golden20 import UNIT
from tests.scratch import BUILD

WHO = 'analyst_enterprise'
MARCH = "t.DW_TRAN_MNTH_END_DT = CAST('2024-03-31' AS DATE)"
SEGMENT = ("JOIN ACCT_DIM2_1 AS a ON t.ACCT_SRCE_SYS_ID = a.SRCE_SYS_ID AND t.DW_ACCT_ID = a.DW_ACCT_ID AND "
           "t.DW_TRAN_MNTH_END_DT BETWEEN a.DW_EFF_DT AND a.DW_EXPR_DT JOIN CLIENT_CD_ROLLUP_1 AS c ON a.CLNT_CD = c.CLNT_CD "
           "AND t.DW_TRAN_MNTH_END_DT BETWEEN c.DW_EFF_DT AND c.DW_EXPR_DT")


def figures(eng, question):
    ans = eng.answer(question, WHO)
    assert ans['behaviour'] in ('figure', 'combined'), ans.get('reason')
    columns = ans['result']['columns']
    cells = ans['result']['rows'][0]['cells']
    return {c['label']: Decimal(cell['value']['value']) for c, cell in zip(columns, cells)}


def expected(eng, where, join=''):
    sql = (f"SELECT SUM(IF(t.DW_TRAN_DIRCTN_CD = 'I', t.NNA_AM, 0)), SUM(IF(t.DW_TRAN_DIRCTN_CD = 'O', t.NNA_AM, 0)), "
           f"SUM(t.NNA_AM) FROM F_NNA_FIN AS t {join} WHERE {where}")
    estate = warehouses(BUILD, 'meridian')
    session = estate.open(UNIT)
    try:
        return [Decimal(str(v)).quantize(Decimal('0.01')) for v in session.execute(sql).fetchone()]
    finally:
        session.close()


@pytest.mark.parametrize('question', [
    'Certified NNA inflows and outflows for March 2024',
    'How much certified NNA flowed in and how much flowed out in March 2024?',
    'Certified NNA gross inflows, gross outflows and net for March 2024',
])
def test_parts_come_with_the_declared_whole(eng, question):
    got = figures(eng, question)
    assert 'Net' in got and len(got) == 3, got
    inflow, outflow = (v for k, v in got.items() if k != 'Net')
    assert [inflow, outflow, got['Net']] == expected(eng, MARCH)


def test_a_named_segment_reaches_every_part_and_the_whole(eng):
    got = figures(eng, 'For March 2024, how much certified NNA flowed in and how much flowed out for Affluent?')
    inflow, outflow = (v for k, v in got.items() if k != 'Net')
    assert [inflow, outflow, got['Net']] == expected(eng, f"{MARCH} AND c.SEGMT_LVL_ID5 = 'S4100'", SEGMENT)


def test_a_possessive_names_the_segment_of_every_measure_it_opens(eng):
    both = figures(eng, "Affluent's certified NNA and client-directed NNA for March 2024")
    alone = [figures(eng, f'Affluent {name} for March 2024') for name in ('certified NNA', 'client-directed NNA')]
    assert sorted(both.values()) == sorted(v for one in alone for v in one.values())
