"""One-to-many declarations prevent repeated sums and counts at every join type."""
import datetime as dt
from dataclasses import replace
from decimal import Decimal

import pytest
import sqlglot

from kge.pipeline.sqlguard import Allowlist, canonical, check, conjuncts
from kge.semantics.query import Join
from tests.plan_fixtures import semantics, vocabulary
from tests.unit.test_plan_compiler import compiled, connection
from tests.unit.test_runtime_allowlist import runtime


KINDS = ('inner', 'left', 'right', 'full')
CONDITION = 'events.entity_id = members.entity_id AND events.source = members.source'


def allowance(kind):
    schema = {'events': {'entity_id', 'source', 'value', 'occurred', 'category'},
              'members': {'entity_id', 'source', 'grouping'}}
    conditions = frozenset(canonical(c, {}) for c in conjuncts(sqlglot.parse_one(CONDITION)))
    pairs = {frozenset({('events', column), ('members', column)}) for column in ('entity_id', 'source')}
    return Allowlist('u', set(schema), schema, schema=schema, joins=pairs, declared=set(conditions),
                     required={'fanout': {'join_contract': {'source': 'events', 'target': 'members',
                               'type': kind, 'cardinality': 'one-to-many', 'condition': conditions}}})


def sample():
    con = connection([(dt.date(2025, 4, 10), 5, 'A', 1, 'x'),
                      (dt.date(2025, 4, 11), 5, 'A', 1, 'x'),
                      (dt.date(2025, 4, 10), 20, 'B', 2, 'x')])
    con.execute('CREATE TABLE members(entity_id INT, source VARCHAR, grouping VARCHAR)')
    con.execute("INSERT INTO members VALUES (1, 'x', 'first'), (1, 'x', 'second'), (3, 'x', 'right only')")
    return con


def joined(kind, source='events e'):
    return f'FROM {source} {kind.upper()} JOIN members m ON e.entity_id = m.entity_id AND e.source = m.source'


@pytest.mark.parametrize('kind', KINDS)
@pytest.mark.parametrize('expression', ['SUM(e.value)', 'SUM(DISTINCT e.value)', 'COUNT(e.value)', 'COUNT(*)'])
def test_gate_rejects_a_repeated_sum_or_count(kind, expression):
    problems = check(f'SELECT {expression} ' + joined(kind), allowance(kind))
    assert problems and any('one-to-many' in problem for problem in problems), problems


@pytest.mark.parametrize('kind,incorrect,correct', [('inner', 20, 10), ('left', 40, 30),
                                                  ('right', 20, 10), ('full', 40, 30)])
def test_small_fanout_exposes_the_wrong_figure_without_admission(kind, incorrect, correct):
    sql = 'SELECT SUM(e.value) ' + joined(kind)
    assert check(sql, allowance(kind))
    with sample() as con:
        assert con.execute(sql).fetchone()[0] == Decimal(incorrect)
        expected_sql = 'SELECT SUM(value) FROM events' + ('' if kind in ('left', 'full') else ' WHERE entity_id = 1')
        assert check(expected_sql, allowance(kind)) == []
        assert con.execute(expected_sql).fetchone()[0] == Decimal(correct)


@pytest.mark.parametrize('kind', KINDS)
@pytest.mark.parametrize('form', ['cte', 'subquery'])
@pytest.mark.parametrize('rename_keys', [False, True])
def test_preaggregation_preserves_figures_at_the_sources_own_grain(kind, form, rename_keys):
    inner = 'SELECT entity_id, source, SUM(value) AS total, COUNT(value) AS observations FROM events GROUP BY entity_id, source'
    if rename_keys:
        inner = inner.replace('SELECT entity_id, source,', 'SELECT entity_id AS parent_key, source AS source_key,')
    source = 'totals e' if form == 'cte' else f'({inner}) e'
    sql = ('WITH totals AS (' + inner + ') ' if form == 'cte' else '') + \
          'SELECT e.entity_id, e.total, e.observations, m.grouping ' + joined(kind, source)
    if rename_keys:
        sql = sql.replace('e.entity_id', 'e.parent_key').replace('e.source', 'e.source_key')
    assert check(sql, allowance(kind)) == []
    with sample() as con:
        got = set(con.execute(sql).fetchall())
    expected = {(1, Decimal(10), 2, 'first'), (1, Decimal(10), 2, 'second')}
    if kind in ('left', 'full'):
        expected.add((2, Decimal(20), 1, None))
    if kind in ('right', 'full'):
        expected.add((None, None, None, 'right only'))
    assert got == expected


@pytest.mark.parametrize('kind,expected', [('inner', 1), ('left', 2), ('right', 1), ('full', 2)])
def test_distinct_count_is_not_repeated_by_fanout(kind, expected):
    sql = 'SELECT COUNT(DISTINCT e.entity_id) ' + joined(kind)
    assert check(sql, allowance(kind)) == []
    with sample() as con:
        assert con.execute(sql).fetchone()[0] == expected


@pytest.mark.parametrize('kind', KINDS)
def test_summing_the_preaggregated_value_again_is_still_refused(kind):
    sql = ('WITH totals AS (SELECT entity_id, source, SUM(value) AS total FROM events GROUP BY entity_id, source) '
           'SELECT SUM(e.total) ' + joined(kind, 'totals e'))
    problems = check(sql, allowance(kind))
    assert problems and any('one-to-many' in problem for problem in problems), problems


@pytest.mark.parametrize('kind', KINDS)
@pytest.mark.parametrize('expression', ['SUM(value)', 'COUNT(value)', 'COUNT(*)'])
def test_an_outer_aggregate_cannot_hide_the_repeated_source_rows(kind, expression):
    sql = ('WITH fanned AS (SELECT e.value ' + joined(kind) + ') '
           f'SELECT {expression} FROM fanned')
    problems = check(sql, allowance(kind))
    assert problems and any('one-to-many' in problem for problem in problems), problems


@pytest.mark.parametrize('kind', KINDS)
@pytest.mark.parametrize('aggregation', ['sum', 'count'])
def test_compiler_refuses_aggregation_after_a_required_fanout(kind, aggregation):
    sem = semantics(joined=True)
    ctx = sem.contexts['ctx']
    original = ctx.joins[0]
    join = Join(original.id, original.kind, original.condition, None, None, original.source, original.target, True,
                {'type': kind, 'cardinality': 'one-to-many'})
    sem.contexts['ctx'] = replace(ctx, grain='day', expression=f'{aggregation}(events.value)', aggregation=aggregation,
                                  filters=(), joins=(join,))
    sem.declared = {join.id: join}
    with pytest.raises(ValueError, match='one-to-many'):
        compiled(vocab=vocabulary(sem))


@pytest.mark.parametrize('kind', KINDS)
def test_runtime_preserves_fanout_authority(kind):
    manager, allow = runtime()
    condition = canonical(sqlglot.parse_one('tran_fact.src = excl.src'), {})
    allow.required = {'fanout': {'join_contract': {'source': 'tran_fact', 'target': 'excl', 'type': kind,
                      'cardinality': 'one-to-many', 'condition': frozenset({condition})}}}
    sql = f'SELECT SUM(t.amt) FROM tran_fact t {kind.upper()} JOIN excl e ON t.src = e.src'
    assert check(sql, allow)
    from kge.ports import QueryRefused
    with pytest.raises(QueryRefused, match='one-to-many'):
        manager._rename_sql_for_runtime(sql, 'u', 'live', allow)
    rewritten, physical, _ = manager._rename_sql_for_runtime(sql.replace('SUM(t.amt)', 't.amt'), 'u', 'live', allow)
    assert physical.required['fanout']['join_contract']['cardinality'] == 'one-to-many'
    problems = check(rewritten.replace('t.AMT', 'SUM(t.AMT)', 1), physical)
    assert problems and any('one-to-many' in problem for problem in problems), problems
