"""Arithmetic totals are checked against independent SQL operands."""
from copy import deepcopy
from contextlib import nullcontext
from types import SimpleNamespace
import json
from decimal import Decimal

import pytest

from kge.eval.grounding import evaluate, sql_result_matches
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
        g=SimpleNamespace(as_roles=nullcontext, readable_units=lambda _: {'u'}),
        w=SimpleNamespace(resolve_reference_sql=reference))
    path = tmp_path / 'shape.json'
    path.write_text(json.dumps([dict(id=str(i), question='Compare metrics', expect=dict(unit='u'),
                                    reference_sql='SELECT independent figures') for i in range(2)]))
    def compare(items):
        assert len(items) == 2
        for item in items:
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
