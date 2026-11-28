"""Partitioned ranking and moving averages reconcile with independent SQL."""
from decimal import Decimal

import pytest

from kge.ports import AUTHORED
from kge.semantics.query import Asked
from tests.integration.test_derived_shapes_meridian import answer, detail


def reference(eng, columns, grouped, final, filtered=False, all_available=False):
    """The reference writes the signed flow and its population independently."""
    statement = f"""WITH grouped AS (
SELECT {columns}, SUM(f.TRAN_AM) AS net
FROM TRAN_FACT_1 f
JOIN MONTH_DIM_1 m ON f.MNTH_END_DT=m.CLDR_EOM_DT"""
    if 'b.' in columns or filtered:
        statement += """
JOIN ACCT_DIM2_1 a ON f.DW_ACCT_ID=a.DW_ACCT_ID AND f.ACCT_SRCE_SYS_ID=a.SRCE_SYS_ID AND a.CURR_IN<>0
JOIN BRCH_DIM_1 b ON a.ACCT_DOMCL_BRCH_CD=b.BRCH_CD AND b.CURR_IN<>0"""
    statement += f"""
WHERE m.DW_BRKRG_MNTH_FRM_TODAY_CT<>0
{"AND f.DW_TRAN_DT>=DATE '2025-01-01' AND f.DW_TRAN_DT<DATE '2025-07-01'" if not all_available else ''}
AND f.CURR_IN=1 AND f.NNA_IN='Y' AND f.RCV_IN=1
{"AND b.RGN_CD IN ('CEO','NER')" if filtered else ''}
GROUP BY {grouped}) {final}"""
    context = eng.semantics.contexts['nna']
    allow = eng.semantics.allowlist(eng.semantics.plan(context, Asked()))
    guard = eng.w.gate('wealth', statement, allow, dialect=AUTHORED)
    assert guard['passed'], guard
    return eng.w.rows('wealth', statement, AUTHORED)


@pytest.mark.parametrize('modeled', [False, True], ids=['rules', 'model-plan'])
@pytest.mark.parametrize('ranking', ['top 3', 'bottom 3', 'top 3 and bottom 3'])
@pytest.mark.parametrize('filtered', [False, True])
def test_ranked_branch_limit_is_per_region(eng, modeled, ranking, filtered):
    question = ranking + ' branches by NNA in each region for January through June 2025'
    if filtered:
        question += ' for Northeast and Pacific regions'
    plan, result = answer(eng, question, modeled)
    branch, region = plan.dimensions
    order, = plan.order
    assert order.partition_by == (region.id,)
    directions = [('top', 'DESC'), ('bottom', 'ASC')] if order.direction == 'both' else [
        ('top', 'DESC') if order.direction == 'desc' else ('bottom', 'ASC')]
    expected = {}
    for end, direction in directions:
        rows = reference(eng, 'b.BRCH_CD AS branch, b.RGN_CD AS region', '1,2', f""",
ranked AS (SELECT branch, region, net, ROW_NUMBER() OVER (PARTITION BY region ORDER BY net {direction}, branch) AS place FROM grouped)
SELECT branch, region, net, place FROM ranked WHERE place<=3""", filtered)
        expected.update({(str(r), end, rank): (str(b), Decimal(str(net))) for b, r, net, rank in rows})
    observed = {(key[1], row['end'], row['rank']): (key[0], cells[order.output]) for key, cells, row in detail(result)}
    assert observed == expected


@pytest.mark.parametrize('modeled', [False, True], ids=['rules', 'model-plan'])
@pytest.mark.parametrize('length', [2, 3])
@pytest.mark.parametrize('partitioned', [False, True])
@pytest.mark.parametrize('all_available', [False, True])
def test_moving_average_reconciles_by_time_and_partition(eng, modeled, length, partitioned, all_available):
    question = f'{length}-month moving average of NNA by month' + (' and region' if partitioned else '')
    if not all_available:
        question += ' for January through June 2025'
    plan, result = answer(eng, question, modeled)
    moving, = plan.derived
    month = next(d for d in plan.dimensions if d.kind == 'time')
    grouping = '1,2' if partitioned else '1'
    columns = "CAST(date_trunc('month',f.DW_TRAN_DT) AS DATE) AS month" + (', b.RGN_CD AS region' if partitioned else '')
    final = ('SELECT month, region, net, AVG(net) OVER (PARTITION BY region ORDER BY month' if partitioned else
             'SELECT month, net, AVG(net) OVER (ORDER BY month') + f' ROWS BETWEEN {length-1} PRECEDING AND CURRENT ROW) AS moving FROM grouped'
    rows = reference(eng, columns, grouping, final, all_available=all_available)
    expected = {tuple(str(v) for v in row[:-2]): (Decimal(str(row[-2])), float(row[-1])) for row in rows}
    observed = {}
    for key, cells, _ in detail(result):
        calendar_index = next(i for i, d in enumerate(plan.dimensions) if d.id == month.id)
        ordered = (key[calendar_index], *(v for i, v in enumerate(key) if i != calendar_index))
        observed[ordered] = (cells[plan.measures[0].id], float(cells[moving.id]))
    assert observed.keys() == expected.keys()
    for key in expected:
        assert observed[key][0] == expected[key][0]
        assert observed[key][1] == pytest.approx(expected[key][1])
    assert any(c['id'] == moving.id and 'moving average' in c['label'] for c in result['columns'])
