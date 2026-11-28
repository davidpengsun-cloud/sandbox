import datetime as dt

import duckdb
import pytest
from sqlglot import exp, parse_one

from kge.pipeline.sqlguard import check
from tests.bigquery_forms import for_bigquery, refusals
from tests.plan_fixtures import ROWS, model, monthly_time, name, time_dimension
from tests.unit.test_plan_compiler import compiled, connection

COUNT = {'kind': 'distinct_count', 'id': 'c', 'entity': name('entity'), 'context': name('ctx'), 'mode': 'current', 'filters': []}


def monthly_counts():
    plan = model(time=monthly_time(), dimensions=[time_dimension()], measures=[model().measures[0].model_dump(), COUNT])
    text = 'Metric by month 2025-04 through 2025-06 count Entities'
    return compiled(plan, text, {'by': 't', 'month': 't', '2025-04': 'time', 'through': 'time', '2025-06': 'time',
                                 'count': 'c', 'Entities': 'c'})


def test_null_safe_equality_reaches_bigquery_as_equality_or_both_null_with_the_same_truth_table():
    sql = for_bigquery('SELECT 1 FROM a JOIN b ON a.k IS NOT DISTINCT FROM b.k')
    on = parse_one(sql, read='bigquery').find(exp.Join).args['on']
    assert not on.find(exp.NullSafeEQ) and refusals(sql) == []
    form = on.sql('duckdb').replace('"', '')
    with duckdb.connect(':memory:') as con:
        for left, right in ((1, 1), (1, 2), (1, None), (None, 1), (None, None)):
            pair = (f'(SELECT {left if left is not None else "NULL::INT"} AS k) a, '
                    f'(SELECT {right if right is not None else "NULL::INT"} AS k) b')
            native = con.execute(f'SELECT a.k IS NOT DISTINCT FROM b.k, a.k IS DISTINCT FROM b.k FROM {pair}').fetchone()
            written = con.execute(f'SELECT {form}, NOT {form} FROM {pair}').fetchone()
            assert native == written, (left, right, form)


@pytest.mark.parametrize('authored', ['NOT (a.k IS NOT DISTINCT FROM b.k)', 'a.k IS DISTINCT FROM b.k'])
def test_a_null_safe_inequality_with_exactly_one_side_null_keeps_its_row_on_bigquery(authored):
    sql = for_bigquery('SELECT COUNT(*) FROM a, b WHERE ' + authored)
    assert refusals(sql) == []
    with duckdb.connect(':memory:') as con:
        con.execute('CREATE TABLE wealth_a AS SELECT 1 AS k')
        con.execute('CREATE TABLE wealth_b AS SELECT NULL::INT AS k')
        assert con.execute(parse_one(sql, read='bigquery').sql('duckdb')).fetchone() == (1,)


def test_distinct_count_totals_reach_bigquery_in_forms_it_runs_with_the_same_figures():
    c = monthly_counts()
    assert check(c.answer.sql, c.answer.allow) == []
    assert refusals(for_bigquery(c.answer.sql)) == []
    with connection() as con:
        rows = con.execute(c.answer.sql).fetchall()
    assert [row[-2] for row in rows] == [1, 1, 1]
    assert [row[2] for row in rows] == [1, 0, 1]


def test_a_count_over_no_rows_is_zero_as_count_gives():
    plan = model(measures=[model().measures[0].model_dump(), COUNT])
    c = compiled(plan, 'Metric and count Entities', {'count': 'c', 'Entities': 'c'})
    with connection([]) as con:
        assert con.execute(c.answer.sql).fetchall()[0][1] == 0
    with connection(ROWS + [(dt.date(2025, 6, 12), 3, 'A', None, 'y')]) as con:
        assert con.execute(c.answer.sql).fetchall()[0][1] == 1


@pytest.mark.parametrize('sql', ['SELECT 1 FROM a LEFT JOIN b ON a.k IS NOT DISTINCT FROM b.k',
                                 'SELECT 1 FROM a WHERE EXISTS (SELECT 1 FROM b WHERE b.k IS NOT DISTINCT FROM a.k)'])
def test_the_restriction_check_finds_what_bigquery_refuses(sql):
    assert refusals(for_bigquery(sql))


@pytest.mark.parametrize('authored,kind', [('a IS NOT DISTINCT FROM b', exp.NullSafeEQ), ('a IS DISTINCT FROM b', exp.NullSafeNEQ),
                                           ('a = b OR (a IS NULL AND c IS NULL)', exp.Or), ('NOT COALESCE(a = b, FALSE)', exp.Not),
                                           ('a = b OR (a IS NULL AND b IS NULL)', exp.Or),
                                           ('COALESCE(a = b, FALSE) OR (a IS NULL AND c IS NULL)', exp.Or)])
def test_the_gate_reads_bigquery_null_safe_spellings_as_the_comparison_they_are(authored, kind):
    from kge.adapters.warehouse.bigquery import rewrite_sql
    from kge.pipeline.sqlguard import null_safe_pair
    native = rewrite_sql('SELECT ' + authored + ' FROM t', lambda _: '')
    assert isinstance(null_safe_pair(parse_one(native, read='bigquery').expressions[0]), kind), native


def test_a_distinct_count_total_reads_its_answer_scope_with_the_columns_own_collation_and_null_groups():
    dimension = {'kind': 'column', 'id': 'd', 'bindings': [{'context': name('ctx'), 'column': name('u.events.category'), 'path': [], 'level': None}]}
    above = {'kind': 'compare', 'id': 'f', 'left': {'kind': 'output', 'id': 'm'}, 'op': 'gt', 'value': {'kind': 'integer', 'value': 6}, 'evidence': []}
    plan = model(dimensions=[dimension], filters=[above], measures=[model().measures[0].model_dump(), COUNT])
    c = compiled(plan, 'Metric by category above 6 count Entities',
                 {'by': 'd', 'category': 'd', 'above': 'f', '6': 'f', 'count': 'c', 'Entities': 'c'})
    assert check(c.answer.sql, c.answer.allow) == []
    assert refusals(for_bigquery(c.answer.sql)) == []
    with duckdb.connect(':memory:') as con:
        con.execute('CREATE TABLE events(occurred DATE, value DECIMAL(18,2), category VARCHAR COLLATE NOCASE, entity_id INT, '
                    'source VARCHAR, private VARCHAR)')
        con.execute("INSERT INTO events VALUES ('2025-04-10', 10, 'A', 1, 'x', NULL), ('2025-05-10', 5, 'a', 2, 'x', NULL), "
                    "('2025-05-10', 5, 'B', 3, 'x', NULL), ('2025-05-10', 7, NULL, 4, 'x', NULL)")
        cursor = con.execute(c.answer.sql)
        names = [d[0] for d in cursor.description]
        rows = cursor.fetchall()
    assert sorted((r[names.index('c')], r[names.index('/total1')]) for r in rows) == [(1, 3), (2, 3)], (names, rows)


def test_a_distinct_count_total_in_a_separate_panel_its_dimension_does_not_reach_counts_that_panel():
    from dataclasses import replace
    from kge.semantics.compile import compile_plan
    from kge.semantics.plancheck import CheckedPlan, check_plan
    from tests.plan_fixtures import grounded, metric_vocabulary, semantics
    sem = semantics()
    sem.contexts['ctx2'] = replace(sem.contexts['ctx'], id='ctx2', label='Second', term='Second', grain='day')
    v = metric_vocabulary(sem, aliases=[('First', 'ctx'), ('Second', 'ctx2')])
    dimension = {'kind': 'column', 'id': 'd', 'bindings': [{'context': name('ctx'), 'column': name('u.events.category'), 'path': [], 'level': None}]}
    count = dict(COUNT, context=name('ctx2'))
    text = 'First by category and Second count Entities separate'
    plan = grounded(model(layout='separate', dimensions=[dimension], measures=[model().measures[0].model_dump(), count]), text, v,
                    {'First': 'm', 'by': 'd', 'category': 'd', 'Second': 'c', 'count': 'c', 'Entities': 'c', 'separate': ['m', 'c']})
    checked = check_plan(plan, v, text)
    assert isinstance(checked, CheckedPlan), checked
    c = compile_plan(checked)
    assert check(c.answer.sql, c.answer.allow) == []
    assert refusals(for_bigquery(c.answer.sql)) == []
    with connection() as con:
        cursor = con.execute(c.answer.sql)
        names = [d[0] for d in cursor.description]
        rows = cursor.fetchall()
    assert [(r[names.index('c')], r[names.index('/total1')]) for r in rows if r[names.index('_panel')] == 'ctx2'] == [(1, 1)], (names, rows)
