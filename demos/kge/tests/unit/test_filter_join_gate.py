"""Independent mutations of the SQL gate's checked optional role joins."""
from dataclasses import fields, replace

import pytest
import sqlglot
from sqlglot import exp

from kge.pipeline.sqlguard import Allowlist, DataAllowlist, check, conjuncts
from kge.semantics.compile import compile_plan
from kge.semantics.coverage import ground_plan
from kge.semantics.plancheck import CheckedPlan, check_plan
from kge.semantics.query import Rule
from kge.semantics.vocabulary import compiled
from tests.plan_fixtures import model, name, semantics, metric_vocabulary as vocabulary
from tests.unit.test_in_filters import ROWS
from tests.unit.test_plan_compiler import connection


def statement(excluding=True, versioned=False, join_options=None, thresholds=()):
    """A composite optional role with an independently declared current-version condition."""
    sem = semantics(joined=True)
    if thresholds:
        context = sem.contexts['ctx']
        sem.contexts['ctx'] = replace(context, grain=context.grain, filters=(*context.filters, *(
            Rule('threshold-' + str(value), '', f'events.value >= {value}', 'u.events') for value in thresholds)))
    if join_options is not None:
        sem.contexts['ctx'] = replace(sem.contexts['ctx'], grain='day', joins=tuple(
            replace(join, options=join_options) for join in sem.contexts['ctx'].joins))
    if versioned:
        sem.tables['u.members'].update(is_current='BOOLEAN', valid_from='DATE', valid_to='DATE')
        sem.types.update({'u.members.' + column: dtype for column, dtype in sem.tables['u.members'].items()})
        sem.scd2 = {'u.members': dict(is_current='is_current', valid_from='valid_from', valid_to='valid_to')}
    sem.d.VOCABULARY = [dict(kind='predicate', params=dict(phrases=[value + ' group'], column='u.members.grouping', op='eq', value=value))
                        for value in ('G', 'H')]
    words = compiled(sem.d.VOCABULARY)
    sem.d.FILTERS, sem.d.FILTER_COLUMNS = words.filters, words.columns
    vocab = vocabulary(sem, aliases=[('Metric', 'ctx')])
    operand = dict(kind='column', name=name('u.members.grouping'), path=[name('member_join')])
    pred = dict(kind='compare', id='f', left=operand, op='eq', value=dict(kind='text', value='G'), evidence=[])
    if excluding:
        member = dict(kind='in', id='f-in', left=operand, values=[dict(kind='text', value='G')], evidence=[])
        pred = dict(kind='any', id='f', children=[dict(kind='not', id='f-not', child=member, evidence=[]),
                    dict(kind='null', id='f-null', left=operand, is_null=True, evidence=[])], evidence=[])
    text = 'Metric excluding G group' if excluding else 'Metric for G group'
    plan = ground_plan(model(filters=[pred]), text, 'turn', vocab)
    checked = check_plan(plan, vocab, text)
    assert isinstance(checked, CheckedPlan), checked
    return compile_plan(checked).answer


def changed(statement, mutate):
    tree = sqlglot.parse_one(statement.sql, read='duckdb')
    join = next(join for join in tree.find_all(exp.Join) if isinstance(join.this, exp.Table) and join.this.name == 'members')
    mutate(join)
    return tree.sql('duckdb')


@pytest.mark.parametrize('versioned', [False, True])
def test_exact_checked_optional_join_is_accepted_and_keeps_missing_members(versioned):
    built = statement(versioned=versioned)
    assert check(built.sql, built.allow) == []
    rows = [*ROWS, (ROWS[0][0], 3, 'D', 4, 'z')]
    with connection(rows) as con:
        con.execute('CREATE TABLE members(entity_id INTEGER, source VARCHAR, grouping VARCHAR, is_current BOOLEAN, valid_from DATE, valid_to DATE)')
        con.execute("INSERT INTO members VALUES (1, 'x', 'G', TRUE, DATE '2020-01-01', NULL), (2, 'x', 'G', TRUE, DATE '2020-01-01', NULL), (3, 'y', 'H', TRUE, DATE '2020-01-01', NULL)")
        assert con.execute(built.sql).fetchone()[0] == 8


def test_checked_optional_join_cannot_be_replaced_by_inner():
    built = statement()
    wrong = changed(built, lambda join: join.set('side', None))
    assert check(wrong, built.allow)


@pytest.mark.parametrize('kind', ['inner', 'right'])
def test_a_declared_type_cannot_silently_drop_a_missing_excluded_role(kind):
    with pytest.raises(ValueError, match='null-keeping exclusion'):
        statement(join_options={'type': kind, 'cardinality': 'one-to-one'})


@pytest.mark.parametrize('kind', ['left', 'full'])
def test_a_declared_preserving_type_keeps_the_exclusion_figure(kind):
    built = statement(join_options={'type': kind, 'cardinality': 'one-to-one'})
    assert check(built.sql, built.allow) == []
    with connection([*ROWS, (ROWS[0][0], 3, 'D', 4, 'z')]) as con:
        con.execute('CREATE TABLE members(entity_id INTEGER, source VARCHAR, grouping VARCHAR)')
        con.execute("INSERT INTO members VALUES (1, 'x', 'G'), (2, 'x', 'G'), (3, 'y', 'H')")
        assert con.execute(built.sql).fetchone()[0] == 8


def test_checked_positive_join_cannot_be_replaced_by_left():
    built = statement(excluding=False)
    assert check(built.sql, built.allow) == []
    wrong = changed(built, lambda join: join.set('side', 'LEFT'))
    assert check(wrong, built.allow)


@pytest.mark.parametrize('excluding', [False, True])
def test_checked_aggregate_refuses_a_row_multiplying_derived_left_join(excluding):
    built = statement(excluding=excluding)
    tree = sqlglot.parse_one(built.sql, read='duckdb')
    aggregate = next(join.find_ancestor(exp.Select) for join in tree.find_all(exp.Join)
                     if isinstance(join.this, exp.Table) and join.this.name == 'members')
    extra = sqlglot.parse_one('SELECT 1 FROM events LEFT JOIN (SELECT 1 AS n UNION ALL SELECT 2 AS n) d ON TRUE',
                              read='duckdb').args['joins'][0]
    aggregate.append('joins', extra)
    assert check(built.sql, built.allow) == []
    assert any('inner joins only' in problem for problem in check(tree.sql('duckdb'), built.allow))


@pytest.mark.parametrize('condition', ['entity_id', 'source', 'is_current'])
def test_checked_left_join_requires_every_key_and_version_condition_in_on(condition):
    built = statement(versioned=True)
    def remove(join):
        kept = [node for node in conjuncts(join.args['on']) if not any(column.table == 'members' and column.name == condition
                                                                   for column in node.find_all(exp.Column))]
        join.set('on', exp.and_(*kept))
    assert check(changed(built, remove), built.allow)


@pytest.mark.parametrize('mutation', ['extra', 'key_expression', 'false_current', 'or_true'])
def test_checked_left_join_refuses_changed_on_semantics(mutation):
    built = statement(versioned=True)
    def mutate(join):
        on = join.args['on']
        if mutation == 'extra':
            join.set('on', exp.and_(on, sqlglot.parse_one("members.grouping='H'", read='duckdb')))
        elif mutation == 'or_true':
            join.set('on', exp.or_(on, exp.true()))
        elif mutation == 'key_expression':
            column = next(column for column in on.find_all(exp.Column) if column.table == 'members' and column.name == 'entity_id')
            column.replace(exp.Add(this=column.copy(), expression=exp.Literal.number(1)))
        else:
            column = next(column for column in on.find_all(exp.Column) if column.table == 'members' and column.name == 'is_current')
            column.replace(exp.Not(this=column.copy()))
    assert check(changed(built, mutate), built.allow)


@pytest.mark.parametrize('allow_type', [Allowlist, DataAllowlist])
def test_context_allowlist_does_not_gain_checked_left_permission(allow_type):
    built = statement()
    plain = allow_type(**{field.name: getattr(built.allow, field.name) for field in fields(allow_type)})
    assert plain.declares
    problems = check(built.sql, plain)
    assert any('inner joins only' in problem for problem in problems)


def test_person_authored_data_read_still_rejects_checked_left_join():
    built = statement()
    assert check(built.sql, built.allow, inner_only=True)


def test_version_condition_moved_from_on_to_where_is_rejected():
    built = statement(versioned=True)
    tree = sqlglot.parse_one(built.sql, read='duckdb')
    join = next(join for join in tree.find_all(exp.Join) if isinstance(join.this, exp.Table) and join.this.name == 'members')
    kept, moved = [], []
    for condition in conjuncts(join.args['on']):
        (moved if any(column.table == 'members' and column.name == 'is_current' for column in condition.find_all(exp.Column)) else kept).append(condition)
    assert moved
    join.set('on', exp.and_(*kept))
    select = join.find_ancestor(exp.Select)
    select.set('where', exp.Where(this=exp.and_(select.args['where'].this, *moved)))
    assert check(tree.sql('duckdb'), built.allow)
