"""Arithmetic and share totals are checked against independent SQL operands."""
from copy import deepcopy
from contextlib import nullcontext
from types import SimpleNamespace
import json
from decimal import Decimal

import pytest

from kge.eval.grounding import evaluate, outcome, sql_result_matches
from tests.plan_fixtures import model


def comparison():
    return model(comparisons=[dict(kind='previous_period', id='c', inputs=['m'], evidence=[])],
        derived=[dict(kind='difference', id='delta', numerator='c/m/base', denominator='c/m/other', evidence=[])]
        ).model_dump(mode='json', by_alias=True)


def result():
    identifiers = ['m', 'c/m/base', 'c/m/other', 'delta']
    columns = [dict(id='t', kind='dimension'), *[dict(id=i, kind='derived' if i == 'delta' else 'measure', currency='USD')
                                               for i in identifiers]]
    rows = [dict(dimensions=[dict(kind='text', value=period)],
                 cells=[dict(value=dict(kind='decimal', value=str(v))) for v in values])
            for period, values in [('2025-04', [10, 10, 7, 3]), ('2025-05', [5, 5, 7, -2])]]
    rows.append(dict(dimensions=[dict(kind='null')], total=True,
                     cells=[dict(value=dict(kind='decimal', value=str(v))) for v in [15, 15, 14, 1]]))
    return dict(columns=columns, rows=rows)


TRUTH = [('2025-04', 10, 10, 7, 3), ('2025-05', 5, 5, 7, -2)]


def test_difference_totals_require_the_plan_and_both_independent_sql_operands():
    assert not sql_result_matches(result(), TRUTH)
    assert sql_result_matches(result(), TRUTH, plan=comparison())
    wrong = result()
    wrong['rows'][-1]['cells'][-1]['value']['value'] = '42'
    assert not sql_result_matches(wrong, TRUTH, plan=comparison())


@pytest.mark.parametrize('mistake', ['ratio', 'unknown_operand', 'unrelated_operand'])
def test_derived_totals_cannot_claim_unchecked_additivity(mistake):
    plan = comparison()
    node = plan['derived'][0]
    if mistake == 'ratio':
        node['kind'] = 'ratio'
    else:
        node['denominator'] = 'unknown' if mistake == 'unknown_operand' else 'm'
    assert not sql_result_matches(result(), TRUTH, plan=plan)


def test_matching_a_total_does_not_hide_a_wrong_detail_cell():
    wrong = deepcopy(result())
    wrong['rows'][0]['cells'][-1]['value']['value'] = '4'
    assert not sql_result_matches(wrong, TRUTH, plan=comparison())


@pytest.mark.parametrize('width', [1, 2, 5])
def test_multi_output_sql_truth_is_never_collapsed_to_its_last_numeric_cell(tmp_path, width):
    rows = [list(range(1, width + 1))]
    reads = []
    def reference(sql, **kwargs):
        reads.append(sql)
        return dict(figure=width, rows=rows)
    engine = SimpleNamespace(profile=SimpleNamespace(asking=lambda _: 'tester'), UNIT_ORDER=['u'],
        semantics=SimpleNamespace(contexts={'ctx': SimpleNamespace(id='ctx', measure='u.metric', aggregation='sum')}),
        g=SimpleNamespace(as_roles=nullcontext, readable_units=lambda _: {'u'}),
        w=SimpleNamespace(resolve_reference_sql=reference))
    path = tmp_path / 'shape.json'
    path.write_text(json.dumps([dict(id=str(i), question='Compare metrics', expect=dict(unit='u'),
                                    reference_sql='SELECT independent figures') for i in range(2)]))
    def compare(items):
        assert len(items) == 2
        for item in items:
            assert item['_context_metrics'] == {'ctx': dict(measure='u.metric', aggregation='sum')}
            if width == 1:
                assert item['expect']['figure'] == width and '_truth_rows' not in item
            else:
                assert item['_truth_rows'] == rows and 'figure' not in item['expect']
        return dict(rows=[])
    report = evaluate(engine, [path], compare_items=compare)
    assert report['shape']['captured_replies'] == 0
    assert len(reads) == 1


@pytest.mark.parametrize('kind', ['ratio', 'percent_change'])
@pytest.mark.parametrize('sign', [1, -1])
def test_ratio_and_percent_totals_use_sql_operand_totals_and_absolute_denominators(kind, sign):
    plan = comparison()
    plan['derived'][0]['kind'] = kind
    answer = result()
    column = answer['columns'][-1]
    column.update(currency=None, format='percent' if kind == 'percent_change' else 'ratio')
    truth = []
    for row, raw in zip(answer['rows'][:2], TRUTH):
        numerator, denominator = Decimal(raw[2]), Decimal(7 * sign)
        value = (numerator - denominator if kind == 'percent_change' else numerator) / abs(denominator)
        row['cells'][2]['value']['value'] = str(denominator)
        row['cells'][3]['value']['value'] = str(value)
        truth.append((*raw[:3], denominator, value))
    numerator, denominator = Decimal(15), Decimal(14 * sign)
    total = (numerator - denominator if kind == 'percent_change' else numerator) / abs(denominator)
    answer['rows'][-1]['cells'][2]['value']['value'] = str(denominator)
    answer['rows'][-1]['cells'][3]['value']['value'] = str(total)
    assert sql_result_matches(answer, truth, plan=plan)
    answer['rows'][-1]['cells'][3]['value']['value'] = str(sum((row[-1] for row in truth), Decimal(0)))
    assert not sql_result_matches(answer, truth, plan=plan)


def share_example():
    plan = model(dimensions=[dict(kind='time', id='t', calendar=dict(id='cal', evidence=[]), grain='month')],
        derived=[dict(kind='share', id='s', input='m', remove_dimensions=['t'], remove_filters=[], evidence=[])]
        ).model_dump(mode='json', by_alias=True)
    plan['measures'][0]['aggregation'] = 'sum'
    answer = dict(columns=[dict(id='t', kind='dimension'), dict(id='m', kind='measure', currency='USD'),
                           dict(id='s', kind='derived', format='percent')], rows=[])
    for period, amount, share in [('2025-04', 10, '0.25'), ('2025-05', 30, '0.75'), (None, 40, '1')]:
        answer['rows'].append(dict(dimensions=[dict(kind='null') if period is None else dict(kind='text', value=period)],
            cells=[dict(value=dict(kind='decimal', value=str(value))) for value in (amount, share)], total=period is None))
    return plan, answer, [('2025-04', 10, Decimal('0.25')), ('2025-05', 30, Decimal('0.75'))]


def test_share_total_requires_the_plan_and_independent_sql_input_cells():
    plan, answer, truth = share_example()
    assert not sql_result_matches(answer, truth)
    assert sql_result_matches(answer, truth, plan=plan)
    assert outcome(dict(_truth_rows=truth), dict(behaviour='combined', result=answer, history=[dict(plan=plan)])) == 'correct'


@pytest.mark.parametrize('row,column,value', [(0, 0, '11'), (0, 1, '0.3'), (2, 0, '41'), (2, 1, '0.9'), (2, 1, None)])
def test_share_totals_do_not_hide_wrong_detail_or_total_cells(row, column, value):
    plan, answer, truth = share_example()
    answer['rows'][row]['cells'][column]['value'] = dict(kind='null') if value is None else dict(kind='decimal', value=value)
    assert not sql_result_matches(answer, truth, plan=plan)


@pytest.mark.parametrize('mistake', ['unknown_input', 'derived_input', 'unknown_scope', 'unchanged_scope', 'removed_filter', 'ranked', 'output_filter'])
def test_share_total_refuses_an_unproven_denominator_population(mistake):
    plan, answer, truth = share_example()
    node = plan['derived'][0]
    if mistake == 'unknown_input':
        node['input'] = 'unknown'
    elif mistake == 'derived_input':
        node['input'] = 's'
    elif mistake == 'unknown_scope':
        node['remove_dimensions'] = ['unknown']
    elif mistake == 'unchanged_scope':
        node['remove_dimensions'] = []
    elif mistake == 'removed_filter':
        node['remove_filters'] = ['f']
    elif mistake == 'ranked':
        plan['limit'] = 1
    else:
        plan['filters'] = [dict(kind='junction', children=[dict(left=dict(kind='output', id='m'))])]
    assert not sql_result_matches(answer, truth, plan=plan)


@pytest.mark.parametrize('aggregation', [None, 'avg', 'min', 'max', 'median', 'count'])
def test_share_total_requires_the_plan_to_own_an_explicit_sum_input(aggregation):
    plan, answer, truth = share_example()
    if aggregation is None:
        del plan['measures'][0]['aggregation']
    else:
        plan['measures'][0]['aggregation'] = aggregation
    assert not sql_result_matches(answer, truth, plan=plan)


def test_share_total_can_bind_an_omitted_aggregation_to_the_trusted_declared_default():
    plan, answer, truth = share_example()
    del plan['measures'][0]['aggregation']
    declared = {'ctx': dict(measure='u.metric', aggregation='sum')}
    assert sql_result_matches(answer, truth, plan=plan, context_metrics=declared)
    assert outcome(dict(_truth_rows=truth, _context_metrics=declared),
                   dict(behaviour='combined', result=answer, history=[dict(plan=plan)])) == 'correct'


@pytest.mark.parametrize('declared', [{}, {'other': dict(measure='u.metric', aggregation='sum')},
    {'ctx': dict(measure='u.other', aggregation='sum')}, {'ctx': dict(measure='u.metric', aggregation='avg')}])
def test_share_default_requires_the_exact_trusted_context_measure_and_sum_aggregation(declared):
    plan, answer, truth = share_example()
    del plan['measures'][0]['aggregation']
    assert not sql_result_matches(answer, truth, plan=plan, context_metrics=declared)


def test_explicit_aggregation_overrides_the_trusted_context_default():
    plan, answer, truth = share_example()
    declared = {'ctx': dict(measure='u.metric', aggregation='sum')}
    plan['measures'][0]['aggregation'] = 'avg'
    assert not sql_result_matches(answer, truth, plan=plan, context_metrics=declared)
    declared['ctx']['aggregation'] = 'avg'
    plan['measures'][0]['aggregation'] = 'sum'
    assert sql_result_matches(answer, truth, plan=plan, context_metrics=declared)


def test_share_total_refuses_an_orphan_result_operand():
    plan, answer, truth = share_example()
    plan['measures'][0]['id'] = 'other'
    assert not sql_result_matches(answer, truth, plan=plan)


def test_share_of_distinct_counts_cannot_sum_overlapping_retained_populations():
    """Repeated entities can make every detail share correct while rollups are wrong."""
    plan, answer, _ = share_example()
    plan['measures'] = [dict(kind='distinct_count', id='m', entity=dict(id='entity', evidence=[]),
                             context=dict(id='ctx', evidence=[]), mode='current', filters=[])]
    plan['dimensions'].append(dict(kind='column', id='category'))
    answer['columns'].insert(1, dict(id='category', kind='dimension'))
    answer['columns'][2]['kind'] = 'count'
    answer['rows'] = []
    truth = []
    for period in ('2025-04', '2025-05'):
        for category in ('A', 'B'):
            truth.append((period, category, 1, Decimal('0.5')))
            answer['rows'].append(dict(dimensions=[dict(kind='text', value=value) for value in (period, category)],
                cells=[dict(value=dict(kind='decimal', value=str(value))) for value in (1, '0.5')]))
        answer['rows'].append(dict(dimensions=[dict(kind='text', value=period), dict(kind='null')], total=True,
            cells=[dict(value=dict(kind='decimal', value=str(value))) for value in (2, '0.5')]))
    answer['rows'].append(dict(dimensions=[dict(kind='null'), dict(kind='null')], total=True,
        cells=[dict(value=dict(kind='decimal', value=str(value))) for value in (4, 1)]))
    assert not sql_result_matches(answer, truth, plan=plan)


def test_share_subtotals_preserve_retained_dimensions_and_recompute_the_grand_total():
    plan, answer, _ = share_example()
    plan['dimensions'].insert(0, dict(kind='time', id='year', calendar=dict(id='cal', evidence=[]), grain='year'))
    answer['columns'].insert(0, dict(id='year', kind='dimension'))
    answer['rows'] = []
    truth = []
    for year, amounts in [('2024', [10, 30]), ('2025', [20, 20])]:
        for period, amount in zip(['04', '05'], amounts):
            share = Decimal(amount) / sum(amounts)
            truth.append((year, period, amount, share))
            answer['rows'].append(dict(dimensions=[dict(kind='text', value=value) for value in (year, period)],
                cells=[dict(value=dict(kind='decimal', value=str(value))) for value in (amount, share)]))
        answer['rows'].append(dict(dimensions=[dict(kind='text', value=year), dict(kind='null')], total=True,
            cells=[dict(value=dict(kind='decimal', value=str(value))) for value in (sum(amounts), 1)]))
    answer['rows'].append(dict(dimensions=[dict(kind='null'), dict(kind='null')], total=True,
        cells=[dict(value=dict(kind='decimal', value=str(value))) for value in (80, 1)]))
    assert sql_result_matches(answer, truth, plan=plan)
    answer['rows'][-1]['cells'][-1]['value']['value'] = '2'
    assert not sql_result_matches(answer, truth, plan=plan)


@pytest.mark.parametrize('amounts', [[-10, -30], [10, -30], [10, -10]])
def test_share_totals_keep_signed_denominators_and_refuse_zero_denominators(amounts):
    plan, answer, _ = share_example()
    denominator = sum(amounts)
    truth = []
    for row, amount in zip(answer['rows'][:2], amounts):
        share = Decimal(amount) / denominator if denominator else None
        row['cells'][0]['value']['value'] = str(amount)
        row['cells'][1]['value'] = dict(kind='null') if share is None else dict(kind='decimal', value=str(share))
        truth.append((row['dimensions'][0]['value'], amount, share))
    answer['rows'][-1]['cells'][0]['value']['value'] = str(denominator)
    assert sql_result_matches(answer, truth, plan=plan) is bool(denominator)


def test_share_details_keep_a_real_null_in_a_retained_dimension_separate():
    plan, answer, truth = share_example()
    plan['dimensions'].insert(0, dict(kind='time', id='year', calendar=dict(id='cal', evidence=[]), grain='year'))
    answer['columns'].insert(0, dict(id='year', kind='dimension'))
    for row in answer['rows']:
        row['dimensions'].insert(0, dict(kind='null'))
    truth = [(None, *row) for row in truth]
    answer['rows'].insert(0, dict(dimensions=[dict(kind='text', value='2024'), dict(kind='text', value='04')],
        cells=[dict(value=dict(kind='decimal', value=str(value))) for value in (20, 1)]))
    truth.append(('2024', '04', 20, 1))
    answer['rows'][-1]['cells'][0]['value']['value'] = '60'
    assert sql_result_matches(answer, truth, plan=plan)
