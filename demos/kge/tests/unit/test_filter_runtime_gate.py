"""Checked filter policies keep their exact obligations after IMPLEMENTS renaming."""
from dataclasses import fields

import duckdb
import pytest
import sqlglot
from sqlglot import exp

import kge.adapters.warehouse.runtimes as runtime_module
from kge.pipeline.sqlguard import Allowlist, CheckedAllowlist, DataAllowlist, canonical, check, conjuncts, gate
from kge.ports import QueryRefused
from kge.semantics.compile import compile_plan
from kge.semantics.plancheck import CheckedPlan, check_plan
from tests.unit.test_filter_join_gate import statement
from tests.unit.test_plan_relational_matrix import membership_plan, membership_vocabulary
from tests.unit.test_runtime_allowlist import runtime


def translated(versioned=True):
    """Rename every physical name, retaining policy source aliases independently of the output SQL."""
    built = statement(versioned=versioned)
    return renamed(built)


def renamed(built):
    manager, _ = runtime()
    for table, columns in built.allow.schema.items():
        reference = table.split('.')[-1].lower()
        manager.rt_tables['live', reference] = {'table': 'warehouse.LIVE_' + reference.upper()}
        manager.rt_columns['live', reference] = {column.lower(): {'column': 'P_' + column.upper()} for column in columns}
    sql, allow, tables = manager._rename_sql_for_runtime(built.sql, 'u', 'live', built.allow)
    return built, manager, sql, allow, tables


def optional_join(tree):
    return next(join for join in tree.find_all(exp.Join)
                if isinstance(join.this, exp.Table) and join.this.name == 'LIVE_MEMBERS')


def test_checked_runtime_preserves_branch_and_composite_policy():
    built, _, sql, allow, tables = translated()
    assert isinstance(allow, CheckedAllowlist)
    assert check(sql, allow) == []
    assert tables == ['warehouse.LIVE_EVENTS', 'warehouse.LIVE_MEMBERS']
    branch, = allow.branches.values()
    assert branch['tables'] == {'events': 'warehouse.LIVE_EVENTS', 'members': 'warehouse.LIVE_MEMBERS'}
    assert branch['left_joins'] == {'members': frozenset({
        'events.P_ENTITY_ID = members.P_ENTITY_ID', 'events.P_SOURCE = members.P_SOURCE', 'members.P_IS_CURRENT'})}
    assert 'events.P_VALUE >= 0' in branch['predicates']
    assert allow.composite_joins == {'member_join': frozenset({
        frozenset({('warehouse.LIVE_EVENTS', 'P_ENTITY_ID'), ('warehouse.LIVE_MEMBERS', 'P_ENTITY_ID')}),
        frozenset({('warehouse.LIVE_EVENTS', 'P_SOURCE'), ('warehouse.LIVE_MEMBERS', 'P_SOURCE')})})}
    assert allow.alignment_keys == built.allow.alignment_keys and allow.alignments == built.allow.alignments
    assert allow.selections == built.allow.selections
    assert built.allow.branches['0']['tables'] == {'events': 'events', 'members': 'members'}


def test_checked_runtime_exclusion_keeps_missing_and_null_values():
    _, _, sql, allow, _ = translated()
    with duckdb.connect(':memory:') as con:
        con.execute('CREATE SCHEMA warehouse')
        con.execute('CREATE TABLE warehouse.LIVE_EVENTS(P_VALUE DECIMAL(18,2), P_ENTITY_ID INTEGER, P_SOURCE VARCHAR)')
        con.execute('CREATE TABLE warehouse.LIVE_MEMBERS(P_ENTITY_ID INTEGER, P_SOURCE VARCHAR, P_GROUPING VARCHAR, P_IS_CURRENT BOOLEAN)')
        con.execute("INSERT INTO warehouse.LIVE_EVENTS VALUES (10, 1, 'a'), (7, 2, 'b'), (3, 3, 'c'), (4, 4, 'd'), (5, 5, 'e')")
        con.execute("INSERT INTO warehouse.LIVE_MEMBERS VALUES (1, 'a', 'G', TRUE), (2, 'b', 'H', TRUE), (4, 'd', NULL, TRUE), (5, 'e', 'G', FALSE)")
        checked = gate(con, sql, allow)
        assert checked['passed'], checked
        assert con.execute(sql).fetchone()[0] == 19


@pytest.mark.parametrize('mutation', ['inner', 'missing_entity', 'missing_source', 'missing_version', 'false_version',
                                     'extra_on', 'or_true', 'key_expression', 'version_in_where'])
def test_physical_runtime_policy_refuses_changed_optional_join(mutation):
    _, _, sql, allow, _ = translated()
    tree = sqlglot.parse_one(sql, read='duckdb')
    join = optional_join(tree)
    on = join.args['on']
    if mutation == 'inner':
        join.set('side', None)
    elif mutation.startswith('missing_') or mutation == 'version_in_where':
        column = {'missing_entity': 'P_ENTITY_ID', 'missing_source': 'P_SOURCE', 'missing_version': 'P_IS_CURRENT',
                  'version_in_where': 'P_IS_CURRENT'}[mutation]
        kept, moved = [], []
        for condition in conjuncts(on):
            (moved if any(c.table == 'members' and c.name == column for c in condition.find_all(exp.Column)) else kept).append(condition)
        assert moved
        join.set('on', exp.and_(*kept))
        if mutation == 'version_in_where':
            select = join.find_ancestor(exp.Select)
            select.set('where', exp.Where(this=exp.and_(select.args['where'].this, *moved)))
    elif mutation == 'false_version':
        column = next(c for c in on.find_all(exp.Column) if c.name == 'P_IS_CURRENT')
        column.replace(exp.Not(this=column.copy()))
    elif mutation == 'extra_on':
        join.set('on', exp.and_(on, sqlglot.parse_one("members.P_GROUPING = 'H'", read='duckdb')))
    elif mutation == 'or_true':
        join.set('on', exp.or_(on, exp.true()))
    else:
        column = next(c for c in on.find_all(exp.Column) if c.table == 'members' and c.name == 'P_ENTITY_ID')
        column.replace(exp.Add(this=column.copy(), expression=exp.Literal.number(1)))
    assert check(tree.sql('duckdb'), allow)


@pytest.mark.parametrize('mutation', ['missing_filter', 'changed_filter', 'missing_alignment'])
def test_physical_runtime_retains_base_and_derived_obligations(mutation):
    _, _, sql, allow, _ = translated()
    tree = sqlglot.parse_one(sql, read='duckdb')
    if mutation == 'missing_alignment':
        join = next(join for join in tree.find_all(exp.Join) if join.this.name == 'b0')
        join.set('on', exp.true())
    else:
        select = optional_join(tree).find_ancestor(exp.Select)
        if mutation == 'missing_filter':
            select.set('where', exp.Where(this=exp.true()))
        else:
            literal = next(node for node in select.args['where'].find_all(exp.Literal) if node.this == 'G')
            literal.set('this', 'H')
    assert check(tree.sql('duckdb'), allow)


def test_checked_reference_is_validated_before_policy_translation():
    built, manager, _, _, _ = translated()
    sql = built.sql.replace('LEFT JOIN members', 'JOIN members')
    with pytest.raises(QueryRefused, match='checked reference SQL failed its gate'):
        manager._rename_sql_for_runtime(sql, 'u', 'live', built.allow)


def test_runtime_refuses_row_multiplying_derived_left_joins_before_and_after_mapping():
    built, manager, sql, allow, _ = translated()
    for statement_sql, policy in ((built.sql, built.allow), (sql, allow)):
        tree = sqlglot.parse_one(statement_sql, read='duckdb')
        aggregate = next(scope for scope in tree.find_all(exp.Select) if any(output.find(exp.Sum) for output in scope.selects))
        extra = sqlglot.parse_one('SELECT 1 FROM events LEFT JOIN (SELECT 1 AS n UNION ALL SELECT 2 AS n) d ON TRUE',
                                  read='duckdb').args['joins'][0]
        aggregate.append('joins', extra)
        wrong = tree.sql('duckdb')
        assert check(wrong, policy)
        if policy is built.allow:
            with pytest.raises(QueryRefused, match='checked reference SQL failed its gate'):
                manager._rename_sql_for_runtime(wrong, 'u', 'live', policy)


@pytest.mark.parametrize('allow_type', [Allowlist, DataAllowlist])
def test_plain_runtime_policies_do_not_gain_physical_left_permission(allow_type):
    built, manager, _, _, _ = translated()
    plain = allow_type(**{field.name: getattr(built.allow, field.name) for field in fields(allow_type)})
    sql, allow, _ = manager._rename_sql_for_runtime(built.sql, 'u', 'live', plain)
    assert not isinstance(allow, CheckedAllowlist)
    assert isinstance(allow, DataAllowlist) == (allow_type is DataAllowlist)
    assert any('inner joins only' in problem for problem in check(sql, allow))


def test_runtime_keeps_checked_derived_selection_conditions():
    manager, _ = runtime()
    schema = {'tran_fact': {'amt'}}
    allow = CheckedAllowlist(unit='u', tables=set(schema), columns=schema, schema=schema, declared={'1 = 1'},
                             branches={name: {'scope': name, 'tables': {'t': 'tran_fact'}, 'predicates': set(), 'left_joins': {}}
                                       for name in ('c', 'd')}, alignment_keys={'amt'},
                             selections={'/': {canonical(sqlglot.parse_one('c.amt > 0', read='duckdb'), {})}})
    sql = '''WITH c AS (SELECT t.amt FROM tran_fact t), d AS (SELECT t.amt FROM tran_fact t)
             SELECT c.amt FROM c LEFT JOIN d ON c.amt = d.amt WHERE c.amt > 0'''
    assert check(sql, allow) == []
    rewritten, physical, _ = manager._rename_sql_for_runtime(sql, 'u', 'live', allow)
    assert check(rewritten, physical) == []
    assert physical.selections == allow.selections
    assert check(rewritten.replace('c.amt > 0', 'TRUE'), physical)


def test_runtime_translates_checked_physical_selection_conditions():
    manager, _ = runtime()
    schema = {'tran_fact': {'amt'}}
    allow = CheckedAllowlist(unit='u', tables=set(schema), columns=schema, schema=schema, declared={'1 = 1'},
                             branches={'0': {'scope': 'c', 'tables': {'t': 'tran_fact'}, 'predicates': set(), 'left_joins': {}}},
                             selections={'c': {'t.amt > 0'}})
    sql = 'WITH c AS (SELECT t.amt FROM tran_fact t WHERE t.amt > 0) SELECT c.amt FROM c'
    assert check(sql, allow) == []
    rewritten, physical, _ = manager._rename_sql_for_runtime(sql, 'u', 'live', allow)
    assert physical.selections == {'c': {'t.AMT > 0'}}
    assert check(rewritten, physical) == []
    assert check(rewritten.replace('t.AMT > 0', 'TRUE'), physical)


def test_runtime_keeps_nested_correlated_membership_policy():
    vocab = membership_vocabulary()
    plan, text = membership_plan(vocab)
    checked = check_plan(plan, vocab, text)
    assert isinstance(checked, CheckedPlan), checked
    built, _, sql, allow, _ = renamed(compile_plan(checked).answer)
    assert len(allow.branches) >= 3
    assert check(sql, allow) == []
    assert any('P_ENTITY_ID' in predicate and 'member_0' in predicate
               for branch in allow.branches.values() for predicate in branch['predicates'])
    tree = sqlglot.parse_one(sql, read='duckdb')
    condition = next(node for node in tree.find_all(exp.EQ)
                     if isinstance(node.this, exp.Column) and node.this.name == 'P_CERTIFIED')
    condition.replace(exp.true())
    assert check(tree.sql('duckdb'), allow)
    assert built.allow.branches != allow.branches


def test_runtime_data_policy_preserves_complete_composite_keys():
    manager, original = runtime()
    pair = frozenset({('tran_fact', 'dw_tran_dt'), ('excl', 'eff')})
    original.joins.add(pair)
    original.required.clear()
    allow = DataAllowlist(**vars(original), composite_joins={'keys': frozenset(original.joins)})
    sql = 'SELECT t.amt FROM tran_fact t JOIN excl e ON t.src = e.src AND t.dw_tran_dt = e.eff'
    rewritten, physical, _ = manager._rename_sql_for_runtime(sql, 'u', 'live', allow)
    assert isinstance(physical, DataAllowlist)
    assert check(rewritten, physical, inner_only=True) == []
    assert check(rewritten.replace(' AND t.DW_TRAN_DT = e.EXCL_EFF_DT', ''), physical, inner_only=True)


@pytest.mark.parametrize('extra_predicates', [1, 32])
def test_runtime_batches_policy_translation_once_per_source_scope(monkeypatch, extra_predicates):
    built = statement(versioned=True, thresholds=range(1, extra_predicates + 1))
    _, manager, _, _, _ = renamed(built)
    sql = built.sql
    assert check(sql, built.allow) == []
    counts = {'rename': 0, 'resolve': 0}
    original_rename, original_resolve = manager._rename_runtime_tree, runtime_module.resolved

    def count_rename(*args, **kwargs):
        counts['rename'] += 1
        return original_rename(*args, **kwargs)

    def count_resolve(*args, **kwargs):
        counts['resolve'] += 1
        return original_resolve(*args, **kwargs)

    monkeypatch.setattr(manager, '_rename_runtime_tree', count_rename)
    monkeypatch.setattr(runtime_module, 'resolved', count_resolve)
    rewritten, physical, _ = manager._rename_sql_for_runtime(sql, 'u', 'live', built.allow)
    assert check(rewritten, physical) == []
    assert counts == {'rename': 3, 'resolve': 2}
