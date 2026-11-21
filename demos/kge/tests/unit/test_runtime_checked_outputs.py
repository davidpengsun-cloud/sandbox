"""Runtime mappings retain checked figures, ranking partitions and calendar windows, and each dialect reads a checked
output scope's ORDER BY as the plan was checked."""
import datetime as dt
from decimal import Decimal
from types import SimpleNamespace

import duckdb
import pytest
import sqlglot
from sqlglot import exp
from sqlglot.optimizer.scope import traverse_scope

from kge.adapters.warehouse.runtimes import RuntimeManager
from kge.pipeline.sqlguard import (CheckedAllowlist, check, checked_output_shape, gate, physical_scope_name, resolved,
                                   warehouse_names)
from kge.ports import AUTHORED
from kge.semantics.compile import compile_plan
from tests.plan_fixtures import vocabulary as plan_vocabulary
from tests.unit.test_calendar_spine import declared as spined
from tests.unit.test_filter_runtime_gate import renamed
from tests.unit.test_rank_windows import declared
from tests.unit.test_rules_derived_shapes import read_checked

CODE_NAME = dict(kind='code_name_pair', params=dict(code='u.events.category', name='u.events.source',
                                                    members=[dict(code='A', name='x')]))

ORDERED = ("WITH aligned AS (SELECT events.code AS d0, events.period AS d1, events.amount AS x0 FROM events) "
           "SELECT d0 AS d0, x0 AS m0 FROM aligned ORDER BY CAST(d0 ->> '$.code' AS TEXT) NULLS LAST, d1 NULLS LAST")


def code_named(monkeypatch):
    """The declared events with a day spine on their calendar and a code/name pair on category, as Meridian's
    sub-segment is: a breakdown by it orders by the code inside its output."""
    engine, _ = declared(monkeypatch, spined())
    engine.semantics.d.VOCABULARY.append(CODE_NAME)
    return engine, plan_vocabulary(engine.semantics, aliases=[('Metric', 'ctx'), *engine.semantics.d.GROUP_BY.items()])


def mapped(monkeypatch, shape, dialect):
    """Map every warehouse name, including columns used only by a date spine."""
    engine, vocabulary = code_named(monkeypatch) if shape == 'code-name by month' else declared(monkeypatch)
    text = {
        'sum': 'Metric for Q2 2025',
        'ranking': 'top 1 categories by Metric in each source for Q2 2025',
        'moving': '3-month moving average of Metric by month for Q2 2025',
        'unbounded moving': '3-month moving average of Metric by month',
        'code-name by month': 'Metric by category by month for Q2 2025',
    }[shape]
    built = compile_plan(read_checked(engine, vocabulary, text)).answer
    _, manager, _, _, _ = renamed(built)
    manager.runtimes['live'] = {'dialect': dialect}
    sql, allow, _ = manager._rename_sql_for_runtime(built.sql, 'u', 'live', built.allow)
    written = manager._render_for_dialect(sql, dialect)
    assert check(written, allow, dialect=dialect) == []
    assert allow.projections and set(allow.projections) == set(built.allow.projections)
    if shape != 'sum':
        assert allow.output_shapes and set(allow.output_shapes) == set(built.allow.output_shapes)
    if shape == 'unbounded moving':
        assert allow.date_series and set(allow.date_series) == set(built.allow.date_series)
    return sqlglot.parse_one(written, read=dialect), allow, written


@pytest.mark.parametrize('dialect', ['duckdb', 'bigquery'])
@pytest.mark.parametrize('mutation', ['aggregate', 'partition', 'frame', 'alias', 'extra aggregate', 'extra window'])
def test_mapped_runtime_refuses_changed_checked_outputs(monkeypatch, mutation, dialect):
    shape = 'ranking' if mutation == 'partition' else 'moving' if mutation == 'frame' else 'sum'
    tree, allow, _ = mapped(monkeypatch, shape, dialect)
    if mutation == 'aggregate':
        aggregate = next(tree.find_all(exp.Sum))
        aggregate.replace(exp.Max(this=aggregate.this.copy()))
    elif mutation == 'partition':
        rank = next(window for window in tree.find_all(exp.Window) if isinstance(window.this, exp.RowNumber))
        rank.set('partition_by', [])
    elif mutation == 'frame':
        for window in tree.find_all(exp.Window):
            if window.args.get('spec'):
                window.args['spec'].set('start', exp.Literal.number(999))
    else:
        aggregate = next(tree.find_all(exp.Sum))
        output = aggregate.find_ancestor(exp.Alias)
        select = output.find_ancestor(exp.Select)
        if mutation == 'alias':
            original = output.alias
            output.set('alias', exp.to_identifier('unused'))
            extra = exp.alias_(exp.Literal.number(0), original)
        elif mutation == 'extra aggregate':
            extra = exp.alias_(exp.Max(this=aggregate.this.copy()), 'extra')
        else:
            extra = exp.alias_(exp.Window(this=exp.Count(this=exp.Star())), 'extra')
        select.append('expressions', extra)
    assert check(tree.sql(dialect), allow, dialect=dialect)


@pytest.mark.parametrize('dialect', ['duckdb', 'bigquery'])
@pytest.mark.parametrize('shape', ['sum', 'ranking', 'moving', 'unbounded moving'])
def test_mapped_runtime_keeps_hand_computed_figures(monkeypatch, shape, dialect):
    _, allow, written = mapped(monkeypatch, shape, dialect)
    rows = [(dt.date(2025, month, 10), value, category, entity, source)
            for month, value, category, entity, source in (
                (4, 2, 'A', 1, 'x'), (5, 3, 'A', 1, 'x'), (6, 4, 'A', 1, 'x'),
                (4, 1, 'B', 2, 'x'), (4, 5, 'C', 3, 'y'))]
    executable = sqlglot.parse_one(written, read=dialect).sql('duckdb')
    with duckdb.connect(':memory:') as connection:
        connection.execute('CREATE SCHEMA warehouse')
        connection.execute('''CREATE TABLE warehouse.LIVE_EVENTS (
            P_OCCURRED DATE, P_VALUE DECIMAL(18,2), P_CATEGORY VARCHAR,
            P_ENTITY_ID INTEGER, P_SOURCE VARCHAR, P_PRIVATE VARCHAR)''')
        connection.executemany('INSERT INTO warehouse.LIVE_EVENTS VALUES (?, ?, ?, ?, ?, NULL)', rows)
        class NativeSession:
            def execute(self, sql):
                explain = sql.startswith('EXPLAIN ')
                translated = sqlglot.parse_one(sql.removeprefix('EXPLAIN '), read=dialect).sql('duckdb')
                return connection.execute(('EXPLAIN ' if explain else '') + translated)

        guarded = gate(NativeSession(), written, allow, dialect=dialect)
        assert guarded['passed'], guarded
        result = connection.execute(executable).fetchall()
    if shape == 'sum':
        assert [row[0] for row in result] == [Decimal(15)]
    elif shape == 'ranking':
        assert {(row[0], row[1], row[2]) for row in result} == {('A', 'x', Decimal(9)), ('C', 'y', Decimal(5))}
    else:
        assert [(row[0], row[1], float(row[2])) for row in result] == [
            (dt.date(2025, 4, 1), Decimal(8), 8),
            (dt.date(2025, 5, 1), Decimal(3), 5.5),
            (dt.date(2025, 6, 1), Decimal(4), 5),
        ]


def ordered_runtime(dialect, mapping):
    """The checked ORDERED statement, its output shapes recorded as the compiler records them (resolved as DuckDB reads
    it), and a named runtime of dialect that maps every name as itself or renames it, as an IMPLEMENTS mapping may."""
    schema = {'events': {'code', 'period', 'amount'}}
    allow = CheckedAllowlist(unit='u', tables=set(schema), columns=schema, schema=schema,
                             branches={'0': {'scope': 'aligned', 'tables': {'events': 'events'}, 'predicates': set()}})
    compiled = resolved(warehouse_names(sqlglot.parse_one(ORDERED, read=AUTHORED), allow), allow)
    allow.output_shapes = {physical_scope_name(scope): checked_output_shape(scope) for scope in traverse_scope(compiled)
                           if physical_scope_name(scope) == '/'}
    manager = RuntimeManager(SimpleNamespace(units=['u'], curated=[]))
    manager.ref_table_units = {'events': 'u'}
    manager.ref_columns = {('events', column): 'VARCHAR' for column in schema['events']}
    renaming = mapping == 'renamed'
    manager.rt_tables = {('live', 'events'): {'table': 'warehouse.LIVE_EVENTS' if renaming else 'ds.events'}}
    manager.rt_columns = {('live', 'events'): {column: {'column': 'P_' + column.upper() if renaming else column}
                                               for column in schema['events']}}
    manager.runtimes['live'] = {'dialect': dialect}
    sql, runtime_allow, _ = manager._rename_sql_for_runtime(ORDERED, 'u', 'live', allow)
    return manager._render_for_dialect(sql, dialect), runtime_allow


def final_shape(sql, allow, dialect):
    """The final scope's output shape as the gate reads the statement in dialect."""
    tree = resolved(warehouse_names(sqlglot.parse_one(sql, read=dialect), allow, dialect), allow, dialect)
    return next(checked_output_shape(scope) for scope in traverse_scope(tree) if physical_scope_name(scope) == '/')


@pytest.mark.parametrize('mapping', ['same names', 'renamed'])
@pytest.mark.parametrize('dialect', ['duckdb', 'bigquery'])
def test_an_order_by_naming_an_identity_output_reads_as_that_output_on_each_dialect(mapping, dialect):
    """BigQuery reads d0 inside an ORDER BY expression as the select alias d0, DuckDB as the source column aligned.d0;
    for the identity output aligned.d0 AS d0 they are the same value, so the shapes agree and the gate passes."""
    written, allow = ordered_runtime(dialect, mapping)
    assert final_shape(written, allow, dialect) == allow.output_shapes['/']
    assert check(written, allow, dialect=dialect) == []


@pytest.mark.parametrize('mapping', ['same names', 'renamed'])
@pytest.mark.parametrize('dialect', ['duckdb', 'bigquery'])
@pytest.mark.parametrize('mutation', ['ordered by another output', 'swapped alias', 'reversed keys'])
def test_an_order_by_changed_from_the_checked_plan_is_still_refused_on_each_dialect(mutation, mapping, dialect):
    written, allow = ordered_runtime(dialect, mapping)
    tree = sqlglot.parse_one(written, read=dialect)
    order = tree.args['order']
    if mutation == 'ordered by another output':
        code = next(column for column in order.find_all(exp.Column) if column.name == 'd0')
        code.replace(exp.column('m0'))
    elif mutation == 'swapped alias':
        code, value = tree.expressions
        code.set('alias', exp.to_identifier('m0'))
        value.set('alias', exp.to_identifier('d0'))
    else:
        order.set('expressions', list(reversed(order.expressions)))
    problems = check(tree.sql(dialect), allow, dialect=dialect)
    assert 'output scope differs from the checked plan' in problems, problems


@pytest.mark.parametrize('dialect', ['duckdb', 'bigquery'])
def test_a_code_name_breakdown_by_month_passes_the_gate_and_its_reversed_order_is_refused_on_each_dialect(monkeypatch, dialect):
    """Meridian's sub-segment by quarter (S07) on the declared events: the final scope orders by the code inside its
    code/name output, the runtime's statement passes as it was checked, and the same statement with its ORDER BY keys
    reversed is refused."""
    tree, allow, _ = mapped(monkeypatch, 'code-name by month', dialect)
    assert '/' in allow.output_shapes and 'calendar_ticks' in allow.output_shapes
    order = tree.args['order']
    assert order.find(exp.JSONExtractScalar)
    order.set('expressions', list(reversed(order.expressions)))
    assert 'output scope differs from the checked plan' in check(tree.sql(dialect), allow, dialect=dialect)
