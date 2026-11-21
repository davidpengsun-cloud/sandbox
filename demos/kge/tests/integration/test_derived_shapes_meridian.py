"""Question shapes reconcile with separately authored sample-warehouse SQL."""
from collections import defaultdict
from decimal import Decimal

import pytest

from kge.ports import AUTHORED
from kge.semantics.plan import Propose, QueryPlan
from kge.semantics.plancheck import CheckedPlan, check_plan
from kge.semantics.query import Asked
from kge.services.ask.engine import planmodel
from kge.services.ask.engine.planner import Planner
from kge.services.ask.engine.planpublic import render
from kge.services.ask.engine.wording import Hidden
from tests.integration.test_context_scope import vocabulary
from tests.integration.test_plan_wording_round2 import Recording


def reference(eng, dimensions=(), filtered=False, reported=False, first='2025-01-01', last='2025-04-01', counted=False):
    """The signed flow definition, with dimension joins kept in the population."""
    expressions = {'region': 'b.RGN_CD', 'branch': 'b.BRCH_CD',
                   'month': "CAST(date_trunc('month', f.DW_TRAN_DT) AS DATE)"}
    columns = [expressions[d] for d in dimensions]
    aggregate = ('COUNT(DISTINCT f.DW_TRAN_ID)' if counted else
                 "COALESCE(SUM(f.TRAN_AM),0)")
    statement = 'SELECT ' + ', '.join([*columns, aggregate])
    statement += ' FROM TRAN_FACT_1 f JOIN MONTH_DIM_1 m ON f.MNTH_END_DT=m.CLDR_EOM_DT'
    if filtered or set(dimensions) & {'region', 'branch'}:
        statement += ''' JOIN ACCT_DIM2_1 a ON f.DW_ACCT_ID=a.DW_ACCT_ID
AND f.ACCT_SRCE_SYS_ID=a.SRCE_SYS_ID AND a.CURR_IN<>0
JOIN BRCH_DIM_1 b ON a.ACCT_DOMCL_BRCH_CD=b.BRCH_CD AND b.CURR_IN<>0'''
    statement += f""" WHERE m.DW_BRKRG_MNTH_FRM_TODAY_CT<>0
AND f.DW_TRAN_DT>=DATE '{first}' AND f.DW_TRAN_DT<DATE '{last}'
AND f.NNA_IN='Y' AND f.RCV_IN=1"""
    statement += (' AND m.FIN_MTRC_RLSE_DT BETWEEN f.DW_EFF_DT AND f.DW_EXPR_DT' if reported else ' AND f.CURR_IN=1')
    if filtered:
        statement += " AND b.RGN_CD IN ('CEO','NER')"
    if columns:
        statement += ' GROUP BY ' + ', '.join(str(i + 1) for i in range(len(columns)))
    context = eng.semantics.contexts['nna']
    allow = eng.semantics.allowlist(eng.semantics.plan(context, Asked(time_mode='as_reported' if reported else 'current')))
    guard = eng.w.gate('wealth', statement, allow, dialect=AUTHORED)
    assert guard['passed'], guard
    return {tuple(str(v) for v in row[:-1]): Decimal(str(row[-1]))
            for row in eng.w.rows('wealth', statement, AUTHORED)}


def answer(eng, text, modeled):
    public = eng.answer(text, 'analyst_enterprise')
    assert public['behaviour'] in ('figure', 'combined'), public.get('reason')
    assert public['guard']['passed']
    turn = public['history'][-1]
    plan = QueryPlan.model_validate(turn['plan'])
    vocab = vocabulary(eng, text)
    assert isinstance(check_plan(plan, vocab, text, turn['turn_id']), CheckedPlan)
    result = public['result']
    if modeled:
        raw = plan.model_dump(mode='json', by_alias=True)
        raw['obligations'] = []
        proposal = planmodel.read(Recording(Propose(kind='propose', base_turn=None,
            plan=QueryPlan.model_validate(raw))), text, vocab, grounding=True)
        checked = Planner(vocab, eng.w, Hidden([], set(), [])).answer(text, proposal, grounding=True)
        assert checked['kind'] == 'result' and checked['guard']['passed'], checked
        result = checked['result']
    return plan, result


def detail(result):
    outputs = [c['id'] for c in result['columns'] if c['kind'] != 'dimension']
    for row in result['rows']:
        if not row.get('total'):
            yield tuple(str(d.get('value')) for d in row['dimensions']), {
                identifier: Decimal(str(cell['value']['value'])) for identifier, cell in zip(outputs, row['cells'], strict=True)
                if cell['value']['kind'] != 'null'}, row


@pytest.mark.parametrize('modeled', [False, True], ids=['rules', 'model-plan'])
@pytest.mark.parametrize('shape', ['share of NNA by region', 'NNA by region share of total',
                                 'NNA by region as a share of total', 'NNA by region % of total'])
@pytest.mark.parametrize('filtered', [False, True])
def test_share_figures_and_percentages_reconcile_to_the_filtered_total(eng, modeled, shape, filtered):
    text = shape + ' for Q1 2025' + (' for Northeast and Pacific regions' if filtered else '')
    plan, result = answer(eng, text, modeled)
    expected = reference(eng, ('region',), filtered)
    measure, = plan.measures
    share, = plan.derived
    total = sum(expected.values())
    rows = list(detail(result))
    assert {key: cells[measure.id] for key, cells, _ in rows} == expected
    assert {key: float(cells[share.id]) for key, cells, _ in rows} == pytest.approx(
        {key: float(value / total) for key, value in expected.items()})
    assert sum(cells[share.id] for _, cells, _ in rows) == pytest.approx(Decimal(1))
    assert any(c.get('format') == 'percent' and c['id'] == share.id for c in result['columns'])
    assert '%' in render(result)


@pytest.mark.parametrize('filtered', [False, True])
def test_model_share_keeps_a_count_in_the_same_context_separate(eng, filtered):
    """A supplied count shares NNA's row population without requesting its share."""
    text = 'share of NNA by region for Q1 2025' + (' for Northeast and Pacific regions' if filtered else '')
    plan, _ = answer(eng, text, False)
    text += ' with number of transactions'
    raw = plan.model_dump(mode='json', by_alias=True)
    measure, = raw['measures']
    raw['measures'].append(dict(kind='distinct_count', id='n', entity=dict(id='wealth_flow', evidence=[]),
                               context=measure['context'], mode=measure['mode'], filters=[]))
    raw['obligations'] = []
    vocab = vocabulary(eng, text)
    proposal = planmodel.read(Recording(Propose(kind='propose', base_turn=None,
        plan=QueryPlan.model_validate(raw))), text, vocab, grounding=True)
    response = Planner(vocab, eng.w, Hidden([], set(), [])).answer(text, proposal, grounding=True)
    assert response['kind'] == 'result' and response['guard']['passed'], response
    expected = reference(eng, ('region',), filtered)
    counts = reference(eng, ('region',), filtered, counted=True)
    share, = plan.derived
    total = sum(expected.values())
    rows = list(detail(response['result']))
    assert {key: cells[measure['id']] for key, cells, _ in rows} == expected
    assert {key: cells['n'] for key, cells, _ in rows} == counts
    assert {key: float(cells[share.id]) for key, cells, _ in rows} == pytest.approx(
        {key: float(value / total) for key, value in expected.items()})


@pytest.mark.parametrize('modeled', [False, True], ids=['rules', 'model-plan'])
@pytest.mark.parametrize('opening', ['running total of', 'cumulative'])
@pytest.mark.parametrize('partitioned', [False, True])
def test_running_total_is_ordered_and_restarts_per_breakdown(eng, modeled, opening, partitioned):
    text = opening + ' NNA by month' + (' and region' if partitioned else '') + ' for Q1 2025'
    plan, result = answer(eng, text, modeled)
    dimensions = ['month' if d.kind == 'time' else 'region' for d in plan.dimensions]
    expected = reference(eng, dimensions)
    measure, = plan.measures
    running, = plan.derived
    time_index = dimensions.index('month')
    accumulated = defaultdict(Decimal)
    rows = sorted(detail(result), key=lambda item: item[0][time_index])
    for key, cells, _ in rows:
        partition = tuple(v for i, v in enumerate(key) if i != time_index)
        accumulated[partition] += expected[key]
        assert cells[measure.id] == expected[key]
        assert cells[running.id] == accumulated[partition]
    totals = reference(eng, ('region',) if partitioned else ())
    assert dict(accumulated) == totals


@pytest.mark.parametrize('modeled', [False, True], ids=['rules', 'model-plan'])
@pytest.mark.parametrize('ranking', ['top 2 and bottom 2', 'top and bottom 2'])
def test_both_ranked_ends_have_two_labelled_rows(eng, modeled, ranking):
    plan, result = answer(eng, ranking + ' branches by NNA for Q1 2025', modeled)
    assert plan.limit == 2 and plan.order[0].direction == 'both'
    expected = reference(eng, ('branch',))
    ends = defaultdict(list)
    for key, cells, row in detail(result):
        assert cells[plan.measures[0].id] == expected[key]
        ends[row['end']].append((row['rank'], key))
    assert set(ends) == {'top', 'bottom'}
    for end, descending in [('top', True), ('bottom', False)]:
        ranked = sorted(expected, key=lambda key: (-expected[key] if descending else expected[key], key))[:2]
        assert ends[end] == list(enumerate(ranked, 1))
    rendered = render(result)
    assert 'top 1:' in rendered and 'bottom 1:' in rendered


@pytest.mark.parametrize('modeled', [False, True], ids=['rules', 'model-plan'])
@pytest.mark.parametrize('modes', ['as reported vs current', 'as originally reported vs restated',
                                 'current vs as reported'])
def test_time_modes_give_both_figures_and_their_difference(eng, modeled, modes):
    plan, result = answer(eng, 'NNA for March 2025 ' + modes, modeled)
    comparison, = plan.comparisons
    difference, = plan.derived
    expected = {mode: reference(eng, reported=mode == 'as_reported', first='2025-03-01', last='2025-04-01')[()]
                for mode in ('current', 'as_reported')}
    _, cells, _ = next(detail(result))
    measure, = plan.measures
    assert difference.numerator == f'{comparison.id}/{measure.id}/base'
    assert difference.numerator not in cells
    assert cells[measure.id] == expected[comparison.base]
    assert cells[difference.denominator] == expected[comparison.other]
    assert cells[difference.id] == expected[comparison.base] - expected[comparison.other]
    rendered = render(result)
    header = rendered.splitlines()[0]
    assert '· as reported' in header and '· current' in header and 'Difference' in header


def test_region_figures_are_preserved_and_fiscal_year_is_reachable(eng):
    plan, result = answer(eng, 'NNA by region for June 2025', False)
    dimension, = plan.dimensions
    assert dimension.kind == 'hierarchy' and dimension.bindings[0].level == 1
    assert {key: cells[plan.measures[0].id] for key, cells, _ in detail(result)} == reference(
        eng, ('region',), first='2025-06-01', last='2025-07-01')
    plan, result = answer(eng, 'NNA for FY25', False)
    assert plan.time.period.kind == 'fiscal_year'
    _, cells, _ = next(detail(result))
    assert cells[plan.measures[0].id] == reference(eng, first='2024-04-01', last='2025-04-01')[()]


def test_explicit_separate_layout_reaches_ask(eng):
    """Aligned to the golden SQL: certified reads F_NNA_FIN with no frozen filter."""
    plan, result = answer(eng, 'NNA and certified NNA separate for June 2025', False)
    assert plan.layout == 'separate'
    assert {row['panel'] for row in result['rows']} == {m.context.id for m in plan.measures}
    statement = """SELECT COALESCE(SUM(f.NNA_AM),0) FROM F_NNA_FIN f
JOIN MONTH_DIM_1 m ON f.DW_TRAN_MNTH_END_DT=m.CLDR_EOM_DT
WHERE m.DW_BRKRG_MNTH_FRM_TODAY_CT<>0
AND f.DW_TRAN_MNTH_END_DT>=DATE '2025-06-01' AND f.DW_TRAN_MNTH_END_DT<DATE '2025-07-01'"""
    context = eng.semantics.contexts['nna_certified']
    allow = eng.semantics.allowlist(eng.semantics.plan(context, Asked()))
    assert eng.w.gate('wealth', statement, allow, dialect=AUTHORED)['passed']
    expected = {'nna': reference(eng, first='2025-06-01', last='2025-07-01')[()],
                'nna_certified': Decimal(str(eng.w.rows('wealth', statement, AUTHORED)[0][0]))}
    for _, cells, row in detail(result):
        measure = next(m for m in plan.measures if m.context.id == row['panel'])
        assert cells == {measure.id: expected[measure.context.id]}


def test_a_single_selected_region_preserves_its_scalar_answer(eng):
    scalar = eng.answer('NNA for June 2025 for Northeast region', 'analyst_enterprise')
    grouped = eng.answer('NNA by region for June 2025 for Northeast region', 'analyst_enterprise')
    assert scalar['guard']['passed'] and grouped['guard']['passed']
    assert grouped['history'][-1]['plan']['dimensions'][0]['kind'] == 'hierarchy'
    assert scalar['figure'] is not None and grouped['figure'] == scalar['figure']
    assert 'rows' not in grouped and 'group_by' not in grouped
