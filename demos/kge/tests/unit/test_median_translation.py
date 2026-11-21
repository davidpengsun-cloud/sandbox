"""Exact midpoint percentiles preserve each aggregate's source rows and null semantics."""
from decimal import Decimal

import duckdb
import pytest
import sqlglot
from sqlglot import exp

from kge.adapters.warehouse.bigquery import rewrite_sql
from kge.pipeline.sqlguard import Allowlist, check


def execute(sql, values, *, kind='DECIMAL(18,4)'):
    columns = {'value', 'category', 'active'}
    allow = Allowlist('u', {'readings'}, {'readings': columns}, schema={'readings': columns})
    assert check(sql, allow) == []
    translated = rewrite_sql(sql, lambda _: '', unit='u')
    parsed = sqlglot.parse_one(translated, read='bigquery')
    assert not list(parsed.find_all(exp.Median))
    assert 'MEDIAN' not in translated.upper()
    assert 'APPROX' not in translated.upper()
    assert 'PERCENTILE_CONT(' in translated
    assert 'OVER (' in translated
    assert not list(parsed.find_all(exp.ArrayAgg))
    assert check(translated, allow, 'bigquery') == []
    with duckdb.connect(':memory:') as con:
        con.execute(f'CREATE TABLE readings(value {kind}, category VARCHAR, active BOOLEAN)')
        if values:
            con.executemany('INSERT INTO readings VALUES (?, ?, ?)', values)
        result = con.execute(parsed.sql('duckdb')).fetchall()
    return result, translated


@pytest.mark.parametrize('values,expected', [
    ([10, 2, 4], 4), ([10, 2, 8, 4], 6), ([7], 7), ([2, 2, 4, 10], 3),
    ([None, 10, 2, None, 4], 4), ([None, None], None), ([], None),
    ([-10, -2, -4, -8], -6), ([Decimal('1.25'), Decimal('2.75')], 2),
])
def test_ordered_middle_values_give_exact_hand_computed_medians(values, expected):
    rows = [(value, 'A', True) for value in values]
    result, _ = execute('SELECT median(value) FROM readings', rows)
    assert result == [(expected,)]


def test_a_grouped_median_keeps_null_only_groups_and_independent_group_sizes():
    values = [(10, 'A', True), (2, 'A', True), (4, 'A', True),
              (2, 'B', True), (8, 'B', True), (None, 'B', True), (None, 'C', True)]
    result, _ = execute('SELECT category, median(value) FROM readings GROUP BY category ORDER BY category', values)
    assert result == [('A', 4), ('B', 5), ('C', None)]


def test_filter_excludes_observations_from_the_percentile():
    values = [(2, 'A', True), (4, 'A', True), (100, 'A', False), (10, 'A', None), (None, 'A', True)]
    result, translated = execute('SELECT median(value) FILTER (WHERE active) FROM readings', values)
    assert result == [(3,)]
    assert 'PERCENTILE_CONT(CASE WHEN active THEN CAST(value AS FLOAT64) ELSE NULL END, 0.5)' in translated
    assert 'FILTER' not in translated


def test_filtered_groups_keep_an_empty_group_null():
    values = [(2, 'A', True), (4, 'A', True), (100, 'A', False), (10, 'B', False), (8, 'B', None)]
    result, _ = execute('SELECT category, median(value) FILTER (WHERE active) FROM readings GROUP BY category ORDER BY category', values)
    assert result == [('A', 3), ('B', None)]


def test_integer_and_decimal_inputs_can_have_a_fractional_even_median():
    for kind in ('BIGINT', 'DECIMAL(18,4)'):
        result, _ = execute('SELECT median(value) FROM readings', [(2, 'A', True), (3, 'A', True)], kind=kind)
        assert result == [(2.5,)]


def test_decimal_midpoints_keep_the_fraction_below_the_input_scale():
    values = [(Decimal('1.0001'), 'A', True), (Decimal('1.0002'), 'A', True)]
    result, _ = execute('SELECT median(value) FROM readings', values)
    assert result == [(pytest.approx(1.00015),)]


def test_interpolating_extreme_integers_does_not_overflow_before_division():
    values = [(2 ** 63 - 2, 'A', True), (2 ** 63 - 1, 'A', True)]
    result, _ = execute('SELECT median(value) FROM readings', values, kind='BIGINT')
    assert result == [(float(2 ** 63 - 1),)]


def test_row_expressions_and_existing_ctes_survive_translation():
    sql = 'WITH selected AS (SELECT value, active FROM readings) SELECT median(CASE WHEN active THEN value * 2 ELSE NULL END) FROM selected'
    result, translated = execute(sql, [(2, 'A', True), (4, 'A', True), (100, 'A', False)])
    assert result == [(6,)]
    assert 'FROM selected' in translated


def test_median_uses_the_routed_table_alias_in_every_copy_of_its_operand():
    sql = 'SELECT median(readings.value) FILTER (WHERE readings.active) FROM readings'
    translated = rewrite_sql(sql, lambda _: 'unit', unit='u')
    assert 'FROM unit_readings AS readings' in translated
    parsed = sqlglot.parse_one(translated, read='bigquery')
    assert not list(parsed.find_all(exp.Median))
    window, = parsed.find_all(exp.Window)
    assert {column.table for column in window.find_all(exp.Column)} == {'readings'}


def test_mixed_aggregates_keep_each_group_and_apply_its_source_filter_before_the_percentile():
    values = [(2, 'A', True), (4, 'A', True), (100, 'A', False), (10, 'B', True), (None, 'B', True)]
    sql = ('SELECT category, median(value) AS midpoint, sum(value) AS total, count(*) AS observations '
           'FROM readings WHERE active GROUP BY category ORDER BY category')
    result, _ = execute(sql, values)
    assert result == [('A', 3, 6, 2), ('B', 10, 10, 2)]


@pytest.mark.parametrize('group', ['category', 'label', '1'])
def test_group_names_and_positions_define_the_analytic_partition(group):
    sql = ('SELECT category AS label, median(value) AS midpoint FROM readings '
           f'GROUP BY {group} HAVING midpoint > 3 ORDER BY midpoint')
    result, _ = execute(sql, [(2, 'A', True), (4, 'A', True), (6, 'B', True), (10, 'B', True)])
    assert result == [('B', 8)]


def test_multiple_percentiles_and_aggregate_arithmetic_keep_independent_operands():
    sql = ('SELECT median(value) FILTER (WHERE active) AS included, median(value) AS all_rows, '
           'median(value) + sum(value) AS adjusted FROM readings')
    result, _ = execute(sql, [(2, 'A', True), (4, 'A', True), (100, 'A', False)])
    assert result == [(3, 4, 110)]


def test_nested_aggregate_scopes_keep_their_output_names():
    sql = ('WITH midpoints AS (SELECT category, median(value) AS value FROM readings GROUP BY category) '
           'SELECT median(value) AS result FROM midpoints')
    result, _ = execute(sql, [(2, 'A', True), (4, 'A', True), (10, 'B', True)])
    assert result == [(6.5,)]


def test_a_declared_join_and_its_filter_remain_inside_the_source_rows():
    sql = ('SELECT labels.label AS category, median(r.value) AS midpoint, sum(r.value) AS total '
           'FROM readings AS r JOIN labels ON r.category = labels.category '
           'WHERE labels.active GROUP BY labels.label ORDER BY category')
    columns = {'readings': {'value', 'category'}, 'labels': {'category', 'label', 'active'}}
    allow = Allowlist('u', set(columns), columns, schema=columns,
                      joins={frozenset({('readings', 'category'), ('labels', 'category')})})
    assert check(sql, allow) == []
    translated = rewrite_sql(sql, lambda _: '')
    assert check(translated, allow, 'bigquery') == []
    with duckdb.connect(':memory:') as connection:
        connection.execute('CREATE TABLE readings(value INTEGER, category VARCHAR)')
        connection.execute("INSERT INTO readings VALUES (2, 'A'), (4, 'A'), (10, 'B'), (12, 'B'), (100, 'C')")
        connection.execute('CREATE TABLE labels(category VARCHAR, label VARCHAR, active BOOLEAN)')
        connection.execute("INSERT INTO labels VALUES ('A', 'First', TRUE), ('B', 'Second', TRUE), ('C', 'Other', FALSE)")
        rows = connection.execute(sqlglot.parse_one(translated, read='bigquery').sql('duckdb')).fetchall()
    assert rows == [('First', 3, 6), ('Second', 11, 22)]
