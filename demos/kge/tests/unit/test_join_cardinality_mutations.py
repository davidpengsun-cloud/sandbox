"""Independent mutation probes cannot launder join authority through SQL wrappers."""
from dataclasses import replace

import pytest

from kge.pipeline.sqlguard import check
from kge.semantics.query import Join
from tests.plan_fixtures import semantics, vocabulary
from tests.unit.test_declared_join_gate import declared
from tests.unit.test_join_cardinality import allowance, joined, sample
from tests.unit.test_plan_compiler import compiled


@pytest.mark.parametrize('expression', ['SUM(1)', 'COUNT(1)'])
@pytest.mark.parametrize('kind', ['inner', 'left', 'right', 'full'])
def test_constant_aggregate_cannot_hide_fanout(expression, kind):
    problems = check('SELECT ' + expression + ' ' + joined(kind), allowance(kind))
    assert problems and any('one-to-many' in problem for problem in problems), problems


def test_reversed_inner_fanout_is_refused():
    sql = ('SELECT SUM(e.value) FROM members m INNER JOIN events e '
           'ON e.entity_id = m.entity_id AND e.source = m.source')
    problems = check(sql, allowance('inner'))
    assert problems and any('one-to-many' in problem for problem in problems), problems


def test_reversing_tables_cannot_drop_declared_type_or_window():
    _, allow = declared()
    assert check('SELECT t.amt FROM excl v INNER JOIN tran_fact t ON t.src = v.src', allow)


@pytest.mark.parametrize('form', ['cte', 'subquery'])
@pytest.mark.parametrize('condition', ['TRUE', 't.src = v.src'])
def test_derived_source_cannot_drop_exact_join_authority(form, condition):
    _, allow = declared()
    inner = 'SELECT src, amt, dw_tran_dt FROM tran_fact'
    source = 'source_rows t' if form == 'cte' else '(' + inner + ') t'
    sql = ('WITH source_rows AS (' + inner + ') ' if form == 'cte' else '') + \
          'SELECT t.amt FROM ' + source + ' LEFT JOIN excl v ON ' + condition
    assert check(sql, allow)


@pytest.mark.parametrize('expression', ['SUM(value)', 'COUNT(value)', 'COUNT(*)'])
def test_union_wrapper_cannot_launder_repeated_rows(expression):
    arm = 'SELECT e.value ' + joined('left')
    sql = 'WITH fanned AS (' + arm + ' UNION ALL ' + arm + ') SELECT ' + expression + ' FROM fanned'
    problems = check(sql, allowance('left'))
    assert problems and any('one-to-many' in problem for problem in problems), problems


def test_replacing_join_fields_preserves_pack_authority():
    options = {'type': 'left', 'cardinality': 'one-to-many',
               'validity_window': {'date': 'u.a.d', 'effective': 'u.b.lo', 'expiry': 'u.b.hi'}}
    join = Join('j', 'key', 'a.id = b.id', None, None, 'u.a', 'u.b', False, options)
    assert replace(join, required=True).options == options


@pytest.mark.parametrize('value', [
    '(SELECT SUM(e.value + 0 * m.entity_id) FROM members m WHERE e.entity_id = m.entity_id AND e.source = m.source)',
    'e.value * (SELECT COUNT(*) FROM members m WHERE e.entity_id = m.entity_id AND e.source = m.source)',
])
def test_scalar_correlated_subquery_cannot_hide_repeated_source_figures(value):
    sql = 'SELECT SUM(' + value + ') FROM events e'
    problems = check(sql, allowance('inner'))
    assert problems and any('one-to-many' in problem for problem in problems), problems


@pytest.mark.parametrize('condition', [
    'e.entity_id = m.entity_id',
    'TRUE WHERE e.entity_id = m.entity_id AND e.source = m.source',
])
def test_default_inner_still_requires_its_complete_declared_condition(condition):
    allow = allowance('inner')
    allow.required['fanout']['join_contract'].update(typed=False, cardinality='one-to-one')
    assert check('SELECT e.value FROM events e JOIN members m ON ' + condition, allow)


def test_unused_incomplete_runtime_join_does_not_block_unrelated_query():
    manager, allow = declared()
    del manager.rt_columns['live', 'excl']['exp']
    sql, physical, _ = manager._rename_sql_for_runtime('SELECT amt FROM tran_fact', 'u', 'live', allow)
    assert check(sql, physical) == []


@pytest.mark.parametrize('neutral', ['MAX(1) OVER ()', '(SELECT MAX(1))'])
def test_an_unrelated_aggregate_does_not_hide_a_repeated_projection(neutral):
    sql = 'SELECT SUM(value) FROM (SELECT e.value + 0 * ' + neutral + ' AS value ' + joined('left') + ') q'
    assert any('one-to-many' in problem for problem in check(sql, allowance('left')))


@pytest.mark.parametrize('kind', ['inner', 'left', 'right', 'full'])
@pytest.mark.parametrize('projection', ['1 AS value, MAX(1) AS neutral', 'MAX(e.value) AS value',
                                       'COUNT(DISTINCT e.entity_id) AS value'])
@pytest.mark.parametrize('expression', ['SUM(value)', 'COUNT(*)'])
def test_grouping_at_the_target_grain_cannot_clear_repeated_source_provenance(kind, projection, expression):
    sql = ('SELECT ' + expression + ' FROM (SELECT ' + projection + ' ' + joined(kind)
           + ' GROUP BY e.entity_id, e.source, e.occurred, m.grouping) q')
    assert any('one-to-many' in problem for problem in check(sql, allowance(kind)))


@pytest.mark.parametrize('kind', ['inner', 'left', 'right', 'full'])
def test_distinct_target_groups_cannot_clear_repeated_row_provenance(kind):
    sql = ('SELECT COUNT(*) FROM (SELECT DISTINCT e.entity_id, e.occurred, m.grouping '
           + joined(kind) + ') q')
    assert any('one-to-many' in problem for problem in check(sql, allowance(kind)))


def test_grouped_constant_mutation_exposes_its_changed_figure():
    sql = ('SELECT SUM(value) FROM (SELECT 1 AS value, MAX(1) AS neutral ' + joined('left')
           + ' GROUP BY e.entity_id, e.source, e.occurred, m.grouping) q')
    assert check(sql, allowance('left'))
    with sample() as con:
        assert con.execute(sql).fetchone()[0] == 5
        assert con.execute('SELECT COUNT(*) FROM events').fetchone()[0] == 3


@pytest.mark.parametrize('projection', ['(SELECT e.value)', '(SELECT e.value + 0)', '(SELECT SUM(e.value))',
                                       '1', '(SELECT MAX(1))'])
def test_scalar_and_constant_projections_retain_their_repeated_rows(projection):
    sql = 'SELECT SUM(value) FROM (SELECT ' + projection + ' AS value ' + joined('left') + ') q'
    assert any('one-to-many' in problem for problem in check(sql, allowance('left')))


@pytest.mark.parametrize('expression', ['COUNT_IF(e.value IS NOT NULL)', 'COUNTIF(TRUE)', 'FSUM(e.value)', 'AVG(e.value)'])
def test_other_duplicate_sensitive_aggregations_cannot_count_fanned_rows(expression):
    assert any('one-to-many' in problem for problem in check('SELECT ' + expression + ' ' + joined('left'), allowance('left')))


def test_a_threshold_on_the_preaggregated_measure_retains_its_own_grain():
    sql = ('WITH totals AS (SELECT entity_id, source, SUM(value) total FROM events '
           'GROUP BY entity_id, source HAVING SUM(value) > 0) SELECT DISTINCT e.entity_id, e.total '
           + joined('left', 'totals e'))
    assert check(sql, allowance('left')) == []


def test_a_scalar_dimension_does_not_count_the_source_again():
    sql = ('SELECT COUNT(DISTINCT e.entity_id), TO_JSON(e.category) ' + joined('left')
           + ' GROUP BY TO_JSON(e.category)')
    assert check(sql, allowance('left')) == []


def test_cardinality_belongs_to_the_matched_condition_not_just_the_tables():
    import sqlglot
    from kge.pipeline.sqlguard import canonical, conjuncts
    allow = allowance('left')
    condition = 'events.category = members.grouping AND events.source = members.source'
    expected = frozenset(canonical(c, {}) for c in conjuncts(sqlglot.parse_one(condition)))
    allow.required['one'] = {'join_contract': {'source': 'events', 'target': 'members', 'type': 'left',
                                             'cardinality': 'one-to-one', 'condition': expected}}
    allow.joins.add(frozenset({('events', 'category'), ('members', 'grouping')}))
    sql = 'SELECT SUM(e.value) FROM events e LEFT JOIN members m ON e.category = m.grouping AND e.source = m.source'
    assert check(sql, allow) == []
    with sample() as con:
        assert con.execute(sql).fetchone()[0] == 30


def test_conflicting_duplicate_authority_fails_closed_even_without_pack_validation():
    from copy import deepcopy
    allow = allowance('left')
    allow.required['conflict'] = deepcopy(allow.required['fanout'])
    allow.required['conflict']['join_contract']['cardinality'] = 'one-to-one'
    assert check('SELECT SUM(e.value) ' + joined('left'), allow)


@pytest.mark.parametrize('reverse', [False, True])
@pytest.mark.parametrize('inner_only', [False, True])
def test_a_derived_inner_join_cannot_introduce_undeclared_multiplicity(reverse, inner_only):
    target = '(SELECT 1 AS n UNION ALL SELECT 1 AS n) g'
    sources = target + ' JOIN events e' if reverse else 'events e JOIN ' + target
    sql = 'SELECT SUM(e.value) FROM ' + sources + ' ON e.entity_id = g.n'
    assert check(sql, allowance('inner'), inner_only=inner_only)


def test_a_distinct_retained_preaggregation_grain_can_be_summed_after_the_join():
    sql = ('WITH totals AS (SELECT entity_id, source, SUM(value) total FROM events GROUP BY entity_id, source), '
           'kept AS (SELECT DISTINCT e.entity_id, e.source, e.total ' + joined('left', 'totals e')
           + ') SELECT SUM(total) FROM kept')
    assert check(sql, allowance('left')) == []
    with sample() as con:
        assert con.execute(sql).fetchone()[0] == 30


def test_an_unbound_derived_source_cannot_multiply_a_correlated_declared_join():
    allow = allowance('left')
    allow.required['fanout']['join_contract']['cardinality'] = 'one-to-one'
    sql = ('SELECT SUM((SELECT SUM(e.value) FROM (SELECT 1 AS n UNION ALL SELECT 1 AS n) g '
           'LEFT JOIN members m ON e.entity_id = m.entity_id AND e.source = m.source)) FROM events e')
    assert check(sql, allow)


def test_a_self_join_repeats_the_declared_source_role_only():
    import sqlglot
    from kge.pipeline.sqlguard import Allowlist, canonical
    schema = {'events': {'entity_id', 'parent_id', 'value'}}
    contract = {'source': 'events', 'target': 'events', 'type': 'left', 'cardinality': 'one-to-many',
                'condition': frozenset({canonical(sqlglot.parse_one('events.entity_id = events.parent_id'), {})}),
                'role_condition': frozenset({canonical(sqlglot.parse_one('join_source.entity_id = join_target.parent_id'), {})})}
    allow = Allowlist('u', set(schema), schema, schema=schema,
                      joins={frozenset({('events', 'entity_id'), ('events', 'parent_id')})},
                      required={'children': {'join_contract': contract}})
    source = ' FROM events p LEFT JOIN events c ON p.entity_id = c.parent_id'
    assert check('SELECT SUM(c.value)' + source, allow) == []
    assert check('SELECT SUM(p.value)' + source, allow)


def test_typed_self_join_preserves_source_and_target_roles():
    sem = semantics()
    sem.tables['u.events']['parent_id'] = 'INTEGER'
    sem.types['u.events.parent_id'] = 'INTEGER'
    join = Join('parent', 'key', 'events.parent_id = parent_event.entity_id', 'parent_event', None,
                'u.events', 'u.events', True, {'type': 'left', 'cardinality': 'one-to-one'})
    sem.contexts['ctx'] = replace(sem.contexts['ctx'], grain='day', filters=(), joins=(join,))
    sem.declared = {join.id: join}
    result = compiled(vocab=vocabulary(sem))
    assert 'LEFT JOIN' in result.answer.sql
    assert check(result.answer.sql, result.answer.allow) == []
    changed = result.answer.sql.replace('events.parent_id = parent_event.entity_id',
                                        'events.entity_id = parent_event.parent_id')
    assert changed != result.answer.sql
    assert check(changed, result.answer.allow)


@pytest.mark.parametrize('kind', ['inner', 'left', 'right', 'full'])
def test_typed_join_composes_its_declared_current_version_policy(kind):
    sem = semantics(joined=True)
    sem.tables['u.members'].update(current='BOOLEAN', effective='DATE', expiry='DATE')
    sem.types.update({'u.members.' + column: dtype for column, dtype in sem.tables['u.members'].items()})
    sem.scd2 = {'u.members': {'is_current': 'current', 'valid_from': 'effective', 'valid_to': 'expiry'}}
    join = replace(sem.contexts['ctx'].joins[0], required=True, options={'type': kind, 'cardinality': 'one-to-one'})
    sem.contexts['ctx'] = replace(sem.contexts['ctx'], grain='day', filters=(), joins=(join,))
    sem.declared = {join.id: join}
    result = compiled(vocab=vocabulary(sem))
    assert check(result.answer.sql, result.answer.allow) == []
