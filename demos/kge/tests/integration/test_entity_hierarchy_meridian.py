"""Hierarchy groupings and entity counts reconcile with independent gated SQL."""
from decimal import Decimal
import json

import pytest

from kge.ports import AUTHORED
from kge.eval import grounding
from kge.pipeline.settings import ROOT
from kge.semantics.query import Asked
from tests.integration.test_context_scope import vocabulary
from tests.integration.test_derived_shapes_meridian import answer, detail, reference


@pytest.mark.parametrize('modeled', [False, True], ids=['rules', 'model-plan'])
@pytest.mark.parametrize('dimension', ['region', 'branch'])
def test_hierarchy_kind_and_level_keep_the_reference_figures(eng, modeled, dimension):
    plan, result = answer(eng, f'NNA by {dimension} for Q1 2025', modeled)
    grouped, = plan.dimensions
    assert grouped.kind == 'hierarchy'
    assert grouped.bindings[0].level == 1
    measured, = plan.measures
    assert {key: cells[measured.id] for key, cells, _ in detail(result)} == reference(eng, (dimension,))


def counts(eng, entity, grouped=True, summed=False, first='2025-06-01', last='2025-07-01'):
    """Count complete keys in the independently stated governed transaction population."""
    counted = {
        'transaction': 'f.DW_TRAN_ID',
        'account': '(a.DW_ACCT_ID, a.SRCE_SYS_ID)',
        'household': 'a.LAST_RCV_HSHLD_DK',
    }[entity]
    aggregate = ("SUM(f.TRAN_AM)" if summed
                 else f'COUNT(DISTINCT {counted})')
    query = 'SELECT ' + ('a.LAST_RCV_HSHLD_DK, ' if grouped else '') + aggregate
    query += f""" FROM TRAN_FACT_1 f
JOIN MONTH_DIM_1 m ON f.MNTH_END_DT=m.CLDR_EOM_DT
JOIN ACCT_DIM2_1 a ON f.DW_ACCT_ID=a.DW_ACCT_ID
AND f.ACCT_SRCE_SYS_ID=a.SRCE_SYS_ID AND a.CURR_IN<>0
WHERE f.DW_TRAN_DT>=DATE '{first}' AND f.DW_TRAN_DT<DATE '{last}'
AND m.DW_BRKRG_MNTH_FRM_TODAY_CT<>0 AND f.CURR_IN<>0 AND f.NNA_IN='Y' AND f.RCV_IN=1"""
    if grouped:
        query += ' GROUP BY a.LAST_RCV_HSHLD_DK'
    context = eng.semantics.contexts['nna']
    allow = eng.semantics.allowlist(eng.semantics.plan(context, Asked()))
    guard = eng.w.gate('wealth', query, allow, dialect=AUTHORED)
    assert guard['passed'], guard
    return {tuple(str(value) for value in row[:-1]): Decimal(str(row[-1]))
            for row in eng.w.rows('wealth', query, AUTHORED)}


@pytest.mark.parametrize('modeled', [False, True], ids=['rules', 'model-plan'])
@pytest.mark.parametrize('phrase,entity,identifier', [
    ('with number of transactions by household', 'transaction', 'wealth_flow'),
    ('by household with number of transactions', 'transaction', 'wealth_flow'),
    ('with account counts by household', 'account', 'wealth_account'),
    ('by household with account counts', 'account', 'wealth_account'),
    ('with number of households by household', 'household', 'wealth_account_household'),
])
def test_counted_entity_is_independent_of_the_grouping(eng, modeled, phrase, entity, identifier):
    plan, result = answer(eng, f'NNA {phrase} for June 2025', modeled)
    measured, = [m for m in plan.measures if m.kind == 'distinct_count']
    assert measured.kind == 'distinct_count' and measured.entity.id == identifier
    assert {key: cells[measured.id] for key, cells, _ in detail(result)} == counts(eng, entity)
    amount, = [m for m in plan.measures if m.kind == 'measure']
    assert {key: cells[amount.id] for key, cells, _ in detail(result)} == counts(eng, entity, summed=True)


@pytest.mark.parametrize('phrase,entity', [('with account counts', 'account'), ('with number of households', 'household')])
def test_scalar_count_keeps_its_declared_complete_key(eng, phrase, entity):
    plan, result = answer(eng, f'NNA {phrase} for June 2025', False)
    measured, = [m for m in plan.measures if m.kind == 'distinct_count']
    assert measured.kind == 'distinct_count'
    _, cells, _ = next(detail(result))
    assert cells[measured.id] == counts(eng, entity, False)[()]


@pytest.mark.parametrize('modeled', [False, True], ids=['rules', 'model-plan'])
@pytest.mark.parametrize('text', ['NNA with account counts', 'account counts and NNA'])
def test_sum_and_count_keep_their_own_aggregations_in_either_order(eng, modeled, text):
    plan, result = answer(eng, text + ' for June 2025', modeled)
    amount, = [m for m in plan.measures if m.kind == 'measure']
    counted, = [m for m in plan.measures if m.kind == 'distinct_count']
    assert not plan.dimensions
    row, = list(detail(result))
    _, cells, _ = row
    assert cells[amount.id] == counts(eng, 'account', False, summed=True)[()]
    assert cells[counted.id] == counts(eng, 'account', False)[()]


@pytest.mark.parametrize('modeled', [False, True], ids=['rules', 'model-plan'])
@pytest.mark.parametrize('text', ['How many accounts had NNA in FY26-Q1?',
                                'count of accounts for NNA for FY26-Q1'])
def test_counted_population_keeps_the_existing_golden_account_figure(eng, modeled, text):
    plan, result = answer(eng, text, modeled)
    counted, = plan.measures
    assert counted.kind == 'distinct_count' and counted.entity.id == 'wealth_account'
    _, cells, _ = next(detail(result))
    expected = counts(eng, 'account', False, first='2025-04-01', last='2025-07-01')[()]
    assert cells[counted.id] == expected == 255


@pytest.mark.parametrize('identifier', ['m062', 'm063', 'm064', 'm065'])
@pytest.mark.parametrize('arm', ['C', 'B'])
def test_mixed_output_regressions_match_the_independent_reference_sql(eng, identifier, arm):
    items = json.loads((ROOT / 'tests/fixtures/grounding_eval/mistakes.json').read_text())
    item = next(i for i in items if i['id'].startswith(identifier))
    public = grounding.ask_with(eng, arm, item, 'analyst_enterprise')
    assert public['guard']['passed'], public
    truth = eng.w.resolve_reference_sql(item['reference_sql'], unit='wealth',
                                       readable_units=eng.g.readable_units('analyst_enterprise'))
    assert grounding.sql_result_matches(public['result'], truth['rows']), public['result']


@pytest.mark.parametrize('modeled', [False, True], ids=['rules', 'model-plan'])
def test_count_of_households_uses_the_declared_household_key(eng, modeled):
    text = 'count of households'
    plan, result = answer(eng, text, modeled)
    measured, = plan.measures
    assert measured.kind == 'distinct_count' and measured.entity.id == 'wealth_household'
    vocab = vocabulary(eng, text)
    context = vocab.contexts[measured.context.id]
    allow = vocab.semantics.allowlist(vocab.semantics.plan(context, Asked()))
    statement = 'SELECT COUNT(DISTINCT HSHLD_DK) FROM HSHLD_DIM_1'
    guard = eng.w.gate('wealth', statement, allow, dialect=AUTHORED)
    assert guard['passed'], guard
    expected = Decimal(str(eng.w.rows('wealth', statement, AUTHORED)[0][0]))
    _, cells, _ = next(detail(result))
    assert cells[measured.id] == expected
