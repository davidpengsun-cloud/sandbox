"""Checked outputs bind each name and position to exactly one certified expression."""
from dataclasses import replace

import pytest
import sqlglot
from sqlglot import exp
from sqlglot.optimizer.scope import traverse_scope

from kge.adapters.warehouse.bigquery import rewrite_sql
from kge.pipeline.sqlguard import check, physical_scope_name, projection_signature, resolved, warehouse_names
from kge.semantics.query import Rule
from tests.plan_fixtures import model, name, semantics, vocabulary
from tests.unit.test_plan_compiler import compiled, connection


def source(tree):
    return next(cte.this for cte in tree.find_all(exp.CTE) if cte.alias == 'b0')


def test_a_renamed_measure_cannot_redirect_the_answer_to_a_constant():
    result = compiled()
    tree = sqlglot.parse_one(result.answer.sql, read='duckdb')
    branch = source(tree)
    value = next(output for output in branch.expressions if output.alias == 'value')
    value.set('alias', exp.to_identifier('unused', quoted=True))
    branch.select(exp.alias_(exp.Literal.number(0), 'value', quoted=True), append=True, copy=False)
    changed = tree.sql('duckdb')
    with connection() as con:
        assert con.execute(result.answer.sql).fetchone()[0] == 15
        assert con.execute(changed).fetchone()[0] == 0
    assert any('different expressions' in problem for problem in check(changed, result.answer.allow))


@pytest.mark.parametrize('scope', ['base', 'spine', 'aligned', 'answer'])
@pytest.mark.parametrize('expression', ['0', 'MAX(events.value)', 'SUM(events.value) OVER ()'])
def test_no_scope_can_append_an_undeclared_output(scope, expression):
    result = compiled()
    tree = sqlglot.parse_one(result.answer.sql, read='duckdb')
    branch = (source(tree) if scope == 'base' else tree if scope == 'answer' else
              next(cte.this for cte in tree.find_all(exp.CTE) if cte.alias == scope))
    if scope != 'base':
        expression = expression.replace('events.value', '1')
    branch.select(exp.alias_(sqlglot.parse_one(expression, read='duckdb'), 'undeclared'), append=True, copy=False)
    assert check(tree.sql('duckdb'), result.answer.allow)


@pytest.mark.parametrize('mutation', ['alias', 'swap names', 'reorder', 'remove', 'replace'])
@pytest.mark.parametrize('scope', ['base', 'answer'])
def test_checked_columns_keep_their_names_positions_and_expressions(mutation, scope):
    result = compiled()
    tree = sqlglot.parse_one(result.answer.sql, read='duckdb')
    branch = source(tree) if scope == 'base' else tree
    first, second = branch.expressions
    if mutation == 'alias':
        first.set('alias', exp.to_identifier('unused'))
    elif mutation == 'swap names':
        a, b = first.args['alias'].copy(), second.args['alias'].copy()
        first.set('alias', b)
        second.set('alias', a)
    elif mutation == 'reorder':
        branch.set('expressions', [second, first])
    elif mutation == 'remove':
        branch.set('expressions', [first])
    else:
        first.set('this', exp.Literal.number(99))
    assert check(tree.sql('duckdb'), result.answer.allow)


def test_dimension_outputs_are_checked_even_without_a_measure_projection():
    plan = model(dimensions=[dict(kind='column', id='category', bindings=[
        dict(context=name('ctx'), column=name('u.events.category'), path=[], level=None)])])
    result = compiled(plan, 'Metric by category', {'by': 'category', 'category': 'category'})
    tree = sqlglot.parse_one(result.answer.sql, read='duckdb')
    expected = resolved(warehouse_names(tree.copy(), result.answer.allow), result.answer.allow)
    for scope in traverse_scope(expected):
        if isinstance(scope.expression, exp.Select):
            assert result.answer.allow.projections[physical_scope_name(scope)] == {
                position: projection_signature(output) for position, output in enumerate(scope.expression.expressions)}
    spine = next(cte.this for cte in tree.find_all(exp.CTE) if cte.alias == 'spine')
    spine.expressions[0].set('this', exp.Literal.string('wrong category'))
    assert check(tree.sql('duckdb'), result.answer.allow)


def test_lossless_dialect_encoding_keeps_output_names_and_refuses_new_outputs():
    result = compiled()
    native = rewrite_sql(result.answer.sql, lambda _: '')
    assert check(native, result.answer.allow, 'bigquery') == []
    tree = sqlglot.parse_one(native, read='bigquery')
    tree.select(exp.alias_(exp.Literal.number(0), 'extra'), append=True, copy=False)
    assert check(tree.sql('bigquery'), result.answer.allow, 'bigquery')


def test_dialect_output_name_encoding_never_conflates_physical_columns():
    sem = semantics()
    columns = {'amount/one': 'DECIMAL', 'amount__one': 'DECIMAL'}
    sem.tables['u.events'].update(columns)
    sem.types.update({'u.events.' + column: dtype for column, dtype in columns.items()})
    context = sem.contexts['ctx']
    sem.contexts['ctx'] = replace(context, grain='day', expression='sum(events."amount/one")',
        values=('u.events.amount/one',), filters=(*context.filters,
            Rule('other_positive', '', 'events.amount__one >= 0', 'u.events')))
    result = compiled(vocab=vocabulary(sem))
    assert {'amount/one', 'amount__one'} <= result.answer.allow.columns['events']
    assert check(result.answer.sql, result.answer.allow) == []
    with connection() as con:
        con.execute('ALTER TABLE events ADD COLUMN "amount/one" DECIMAL(18,2)')
        con.execute('ALTER TABLE events ADD COLUMN "amount__one" DECIMAL(18,2)')
        con.execute('UPDATE events SET "amount/one"=value, "amount__one"=value*10')
        assert con.execute(result.answer.sql).fetchone()[0] == 15
    changed = result.answer.sql.replace('SUM(events."amount/one")', 'SUM(events."amount__one")')
    assert changed != result.answer.sql
    assert any('different expressions' in problem for problem in check(changed, result.answer.allow))


@pytest.mark.parametrize('bound', ['LIMIT 0', 'OFFSET 1', 'LIMIT 1 OFFSET 1'])
@pytest.mark.parametrize('scope', ['base', 'answer'])
def test_a_checked_plan_keeps_its_rows(bound, scope):
    result = compiled()
    tree = sqlglot.parse_one(result.answer.sql, read='duckdb')
    branch = source(tree) if scope == 'base' else tree
    bounded = sqlglot.parse_one(f'SELECT 1 {bound}', read='duckdb')
    for kind in ('limit', 'offset'):
        if bounded.args.get(kind) is not None:
            branch.set(kind, bounded.args[kind])
    assert not check(result.answer.sql, result.answer.allow)
    assert any('row limit, offset or distinct' in problem for problem in check(tree.sql('duckdb'), result.answer.allow))


@pytest.mark.parametrize('scope', ['base', 'answer'])
def test_a_checked_plan_cannot_add_distinct(scope):
    result = compiled()
    tree = sqlglot.parse_one(result.answer.sql, read='duckdb')
    branch = source(tree) if scope == 'base' else tree
    branch.set('distinct', exp.Distinct())
    assert any('row limit, offset or distinct' in problem for problem in check(tree.sql('duckdb'), result.answer.allow))
