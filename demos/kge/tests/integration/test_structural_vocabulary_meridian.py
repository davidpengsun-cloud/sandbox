"""Plain structural variants reconcile against independently authored, gated SQL."""
from collections import defaultdict
from decimal import Decimal

import pytest

from kge.ports import AUTHORED
from kge.semantics.query import Asked
from tests.integration.test_derived_shapes_meridian import answer, detail, reference


@pytest.mark.parametrize('modeled', [False, True], ids=['rules', 'model-plan'])
@pytest.mark.parametrize('period,first,last', [
    ('first nine months of 2024', '2024-01-01', '2024-10-01'),
    ('last three quarters of 2024', '2024-04-01', '2025-01-01'),
    ('second month of 2025', '2025-02-01', '2025-03-01'),
    ('last month of 2024', '2024-12-01', '2025-01-01'),
    ('three months through June 2025', '2025-04-01', '2025-07-01'),
    ('two quarters ending Q2 2025', '2025-01-01', '2025-07-01'),
    ('one year through 2024', '2024-01-01', '2025-01-01'),
    ('ten days through 2025-06-10', '2025-06-01', '2025-06-11'),
    ('first two fiscal quarters of FY25', '2024-04-01', '2024-10-01'),
    ('last three fiscal quarters of FY25', '2024-07-01', '2025-04-01'),
    ('last fiscal quarter of FY25', '2025-01-01', '2025-04-01'),
    ('one fiscal year through FY25', '2024-04-01', '2025-04-01'),
])
def test_numbered_periods_have_the_requested_bounds_and_figures(eng, modeled, period, first, last):
    plan, result = answer(eng, 'NNA for the ' + period, modeled)
    _, cells, _ = next(detail(result))
    assert cells[plan.measures[0].id] == reference(eng, first=first, last=last)[()]


@pytest.mark.parametrize('modeled', [False, True], ids=['rules', 'model-plan'])
@pytest.mark.parametrize('word,prior', [
    ('prior month', ('2025-05-01', '2025-06-01')),
    ('last period', ('2025-05-01', '2025-06-01')),
    ('month over month', ('2025-05-01', '2025-06-01')),
    ('MoM', ('2025-05-01', '2025-06-01')),
    ('same month a year earlier', ('2024-06-01', '2024-07-01')),
    ('same period last year', ('2024-06-01', '2024-07-01')),
    ('a year earlier', ('2024-06-01', '2024-07-01')),
    ('YoY', ('2024-06-01', '2024-07-01')),
])
def test_relative_comparison_families_keep_both_figures(eng, modeled, word, prior):
    plan, result = answer(eng, f'dollar and percent change in NNA for June 2025 against the {word}', modeled)
    base = reference(eng, first='2025-06-01', last='2025-07-01')[()]
    other = reference(eng, first=prior[0], last=prior[1])[()]
    _, cells, _ = next(detail(result))
    comparison, = plan.comparisons
    measure, = plan.measures
    assert cells[measure.id] == base
    assert len(plan.derived) == 2
    for derived in plan.derived:
        assert derived.numerator == f'{comparison.id}/{measure.id}/base' and derived.numerator not in cells
        assert cells[derived.denominator] == other
        expected = base - other if derived.kind == 'difference' else (base - other) / abs(other)
        assert abs(cells[derived.id] - expected) < Decimal('0.000000000001')


@pytest.mark.parametrize('modeled', [False, True], ids=['rules', 'model-plan'])
@pytest.mark.parametrize('ranking,limit,descending', [
    ('top ten', 10, True), ('bottom three', 3, False), ('highest two', 2, True),
    ('lowest two', 2, False), ('best three', 3, True), ('worst three', 3, False),
    ('rank', None, True), ('ranked', None, True), ('ranking', None, True),
])
def test_rank_words_and_number_words_use_the_same_ranking(eng, modeled, ranking, limit, descending):
    plan, result = answer(eng, f'{ranking} branches by NNA during Q1 2025', modeled)
    expected = reference(eng, ('branch',))
    ordered = sorted(expected, key=lambda key: (-expected[key] if descending else expected[key], key))[:limit]
    assert plan.limit == limit
    rows = list(detail(result))
    assert [key for key, _, _ in rows] == ordered
    assert {key: cells[plan.measures[0].id] for key, cells, _ in rows} == {key: expected[key] for key in ordered}


@pytest.mark.parametrize('word', ['portion', 'proportion', 'percentage of total', '% of total'])
def test_share_words_reconcile_to_the_same_total(eng, word):
    plan, result = answer(eng, f'NNA by region as a {word} within Q1 2025', False)
    expected = reference(eng, ('region',))
    share, = plan.derived
    total = sum(expected.values())
    for key, cells, _ in detail(result):
        assert cells[plan.measures[0].id] == expected[key]
        assert abs(cells[share.id] - expected[key] / total) < Decimal('0.000000000001')


@pytest.mark.parametrize('word', ['running totals', 'cumulative total', 'cumulative sum', 'to date'])
def test_running_words_and_grain_plurals_keep_the_accumulated_figure(eng, word):
    text = 'NNA to date by months over Q1 2025' if word == 'to date' else f'{word} of NNA by months over Q1 2025'
    plan, result = answer(eng, text, False)
    expected = reference(eng, ('month',))
    running, = plan.derived
    cumulative = Decimal(0)
    for key, cells, _ in sorted(detail(result)):
        cumulative += expected[key]
        assert cells[running.id] == cumulative


def excluded_region(eng):
    """Read the remaining and null regions directly from the signed flow definition."""
    statement = """SELECT COALESCE(SUM(f.TRAN_AM),0)
FROM TRAN_FACT_1 f JOIN MONTH_DIM_1 m ON f.MNTH_END_DT=m.CLDR_EOM_DT
WHERE m.DW_BRKRG_MNTH_FRM_TODAY_CT<>0 AND f.DW_TRAN_DT>=DATE '2025-01-01' AND f.DW_TRAN_DT<DATE '2025-04-01'
AND f.NNA_IN='Y' AND f.RCV_IN=1 AND f.CURR_IN=1
AND NOT EXISTS (SELECT 1 FROM ACCT_DIM2_1 a JOIN BRCH_DIM_1 b
ON a.ACCT_DOMCL_BRCH_CD=b.BRCH_CD AND b.CURR_IN<>0
WHERE f.DW_ACCT_ID=a.DW_ACCT_ID AND f.ACCT_SRCE_SYS_ID=a.SRCE_SYS_ID
AND a.CURR_IN<>0 AND b.RGN_CD='NER')"""
    context = eng.semantics.contexts['nna']
    allow = eng.semantics.allowlist(eng.semantics.plan(context, Asked()))
    guard = eng.w.gate('wealth', statement, allow, dialect=AUTHORED)
    assert guard['passed'], guard
    return Decimal(str(eng.w.rows('wealth', statement, AUTHORED)[0][0]))


@pytest.mark.parametrize('word', ['except', 'other than', 'outside', 'apart from', 'without', 'not in'])
def test_exclusion_words_preserve_the_remaining_and_null_population(eng, word):
    plan, result = answer(eng, f'NNA for Q1 2025 {word} Pacific region', False)
    _, cells, _ = next(detail(result))
    assert cells[plan.measures[0].id] == excluded_region(eng)


@pytest.mark.parametrize('modeled', [False, True], ids=['rules', 'model-plan'])
@pytest.mark.parametrize('quantifier,operation', [('any of', 'OR'), ('all of', 'AND')])
@pytest.mark.parametrize('connector', ['and', ','])
def test_quantified_filter_lists_reconcile_to_the_requested_union_or_intersection(eng, modeled, quantifier, operation, connector):
    text = f'NNA for January 2025 {quantifier} Northeast region {connector} SPS accounts'
    plan, result = answer(eng, text, modeled)
    statement = f"""SELECT COALESCE(SUM(f.TRAN_AM),0)
FROM TRAN_FACT_1 f JOIN MONTH_DIM_1 m ON f.MNTH_END_DT=m.CLDR_EOM_DT
JOIN ACCT_DIM2_1 a ON f.DW_ACCT_ID=a.DW_ACCT_ID AND f.ACCT_SRCE_SYS_ID=a.SRCE_SYS_ID AND a.CURR_IN<>0
JOIN BRCH_DIM_1 b ON a.ACCT_DOMCL_BRCH_CD=b.BRCH_CD AND b.CURR_IN<>0
WHERE m.DW_BRKRG_MNTH_FRM_TODAY_CT<>0 AND f.DW_TRAN_DT>=DATE '2025-01-01' AND f.DW_TRAN_DT<DATE '2025-02-01'
AND f.NNA_IN='Y' AND f.RCV_IN=1 AND f.CURR_IN=1
AND (b.RGN_CD='CEO' {operation} a.ACCT_RELT_SPS_IN='Y')"""
    context = eng.semantics.contexts['nna']
    requested = Asked(filters=[('RGN_CD', '=', 'CEO'), ('ACCT_RELT_SPS_IN', '=', 'Y')],
                      filtered=['wealth.BRCH_DIM_1.RGN_CD', 'wealth.ACCT_DIM2_1.ACCT_RELT_SPS_IN'])
    allow = eng.semantics.allowlist(eng.semantics.plan(context, requested))
    guard = eng.w.gate('wealth', statement, allow, dialect=AUTHORED)
    assert guard['passed'], guard
    expected = Decimal(str(eng.w.rows('wealth', statement, AUTHORED)[0][0]))
    _, cells, _ = next(detail(result))
    assert cells[plan.measures[0].id] == expected


def test_time_grain_plurals_and_connectors_keep_the_running_partition(eng):
    plan, result = answer(eng, 'cumulative NNA by months and region across Q1 2025', False)
    dimensions = ['month' if d.kind == 'time' else 'region' for d in plan.dimensions]
    expected = reference(eng, dimensions)
    time_index = dimensions.index('month')
    accumulated = defaultdict(Decimal)
    for key, cells, _ in sorted(detail(result), key=lambda item: item[0][time_index]):
        partition = tuple(v for i, v in enumerate(key) if i != time_index)
        accumulated[partition] += expected[key]
        assert cells[plan.derived[0].id] == accumulated[partition]
