import datetime as dt

import duckdb
import pytest

from kge.pipeline.sqlguard import check
from kge.semantics.compile import compile_plan
from kge.semantics.plancheck import CheckedPlan, check_plan
from tests.plan_fixtures import ROWS, grounded, model, monthly_time, name, time_dimension, vocabulary


def compiled(plan=None, text='Metric', assignments=None, vocab=None):
    v = vocab or vocabulary()
    p = grounded(plan or model(), text, v, assignments)
    checked = check_plan(p, v, text)
    assert isinstance(checked, CheckedPlan), checked
    return compile_plan(checked)


def connection(rows=ROWS):
    con = duckdb.connect(':memory:')
    con.execute('CREATE TABLE events(occurred DATE, value DECIMAL(18,2), category VARCHAR, entity_id INT, source VARCHAR, private VARCHAR)')
    for row in rows:
        con.execute('INSERT INTO events VALUES (?, ?, ?, ?, ?, NULL)', row)
    return con


def test_explicit_fiscal_window_can_select_calendar_dated_observations():
    from tests.plan_fixtures import semantics
    sem = semantics()
    sem.d.CALENDARS['fiscal'] = {'year_start_month': 4, 'quarter_label': 'FY{yy}-Q{n}'}
    v = vocabulary(sem)
    plan = model(time={'kind': 'single', 'period': {'kind': 'fiscal_quarter', 'value': 'FY26-Q1',
                                                 'calendar': {'id': 'fiscal', 'evidence': []}}})
    c = compiled(plan, 'Metric FY26-Q1', {'FY26-Q1': 'time'}, v)
    assert check(c.answer.sql, c.answer.allow) == []
    with connection() as con:
        assert con.execute(c.answer.sql).fetchone()[0] == 15


def test_scalar_sql_is_gated_and_matches_independent_sum():
    c = compiled()
    assert check(c.answer.sql, c.answer.allow) == []
    with connection() as con:
        assert con.execute(c.answer.sql).fetchall()[0][0] == sum(r[1] for r in ROWS)


def test_unqualified_total_without_standing_predicates_passes_the_gate():
    from tests.plan_fixtures import semantics
    sem = semantics()
    object.__setattr__(sem.contexts['ctx'], 'filters', ())
    c = compiled(vocab=vocabulary(sem))
    assert check(c.answer.sql, c.answer.allow) == []
    with connection() as con:
        assert con.execute(c.answer.sql).fetchone()[0] == 15


@pytest.mark.parametrize('zero,expected', [(False, [10, None, None]), (True, [10, 10, 15])])
def test_running_total_unknown_and_observed_zero(zero, expected):
    plan = model(time=monthly_time(), dimensions=[time_dimension()],
                 derived=[{'kind': 'running_total', 'id': 'r', 'input': 'm', 'partition_by': [], 'order_by': 't', 'evidence': []}])
    text = 'Metric by month 2025-04 through 2025-06 running total'
    c = compiled(plan, text, {'by': 't', 'month': 't', '2025-04': 'time', 'through': 'time', '2025-06': 'time', 'running total': 'r'})
    assert check(c.answer.sql, c.answer.allow) == []
    rows = [*ROWS, (dt.date(2025, 5, 10), 0, 'A', 1, 'x')] if zero else ROWS
    with connection(rows) as con:
        actual = con.execute(c.answer.sql).fetchall()
    assert [r[2] for r in actual] == expected
    if not zero:
        assert actual[-1][-1] == 'missing_contributing_observation'


def test_gate_refuses_policy_removed_from_base_branch():
    c = compiled()
    assert check(c.answer.sql.replace('events.value >= 0', 'TRUE'), c.answer.allow)


def test_unchecked_model_never_compiles():
    with pytest.raises(TypeError):
        compile_plan(model())


def test_comparison_overlapping_ranges_rekeys_previous_month():
    plan = model(time=monthly_time('2025-05', '2025-07'), dimensions=[time_dimension()],
                 comparisons=[{'kind': 'previous_period', 'id': 'c', 'inputs': ['m'], 'evidence': []}])
    text = 'Metric by month 2025-05 through 2025-07 previous period'
    c = compiled(plan, text, {'by': 't', 'month': 't', '2025-05': 'time', 'through': 'time', '2025-07': 'time', 'previous period': 'c'})
    assert check(c.answer.sql, c.answer.allow) == []
    rows = [(dt.date(2025, month, 10), value, 'A', 1, 'x') for month, value in [(4, 4), (5, 5), (6, 6), (7, 7)]]
    with connection(rows) as con:
        actual = con.execute(c.answer.sql).fetchall()
    assert [(r[1], r[3]) for r in actual] == [(5, None), (6, 5), (7, 6)]


def test_comparison_year_to_date_rekeys_previous_month():
    time = {'kind': 'ytd', 'through': {'kind': 'month', 'value': '2025-03', 'calendar': name('cal')}, 'calendar': name('cal')}
    plan = model(time=time, dimensions=[time_dimension()],
                 comparisons=[{'kind': 'previous_period', 'id': 'c', 'inputs': ['m'], 'evidence': []}])
    text = 'Metric by month year to date calendar Q1 2025 previous period'
    c = compiled(plan, text, {'by': 't', 'month': 't', 'year to date': 'time', 'calendar Q1 2025': 'time', 'previous period': 'c'})
    assert check(c.answer.sql, c.answer.allow) == []
    rows = [(dt.date(2024, 12, 10), 300, 'A', 1, 'x')]
    rows += [(dt.date(2025, month, 10), month, 'A', 1, 'x') for month in (1, 2, 3)]
    with connection(rows) as con:
        actual = con.execute(c.answer.sql).fetchall()
    assert [(r[1], r[3]) for r in actual] == [(1, None), (2, 1), (3, 2)]


@pytest.mark.parametrize('time,expected', [
    ({'kind': 'single', 'period': {'kind': 'month', 'value': '2025-03', 'calendar': name('cal')}}, (3, 2)),
    (monthly_time('2025-03', '2025-03'), (3, 2)),
    ({'kind': 'ytd', 'through': {'kind': 'month', 'value': '2025-01', 'calendar': name('cal')}, 'calendar': name('cal')}, (1, 300)),
])
def test_previous_period_single_period_windows_keep_both_figures(time, expected):
    plan = model(time=time, comparisons=[{'kind': 'previous_period', 'id': 'c', 'inputs': ['m'], 'evidence': []}])
    phrase = '2025-03' if time['kind'] == 'single' else '2025-03 through 2025-03' if time['kind'] == 'range' else 'year to date 2025-01'
    c = compiled(plan, f'Metric {phrase} previous period', {'2025-03': 'time', 'through': 'time', 'year to date': 'time',
                                                        '2025-01': 'time', 'previous period': 'c'})
    assert check(c.answer.sql, c.answer.allow) == []
    rows = [(dt.date(2024, month, 10), value, 'A', 1, 'x') for month, value in [(10, 100), (11, 200), (12, 300)]]
    rows += [(dt.date(2025, month, 10), month, 'A', 1, 'x') for month in (1, 2, 3)]
    with connection(rows) as con:
        row = con.execute(c.answer.sql).fetchone()
    assert row[1:3] == expected


def test_composite_count_accompanies_measure_and_ignores_incomplete_key():
    plan = model(measures=[model().measures[0].model_dump(), {'kind': 'distinct_count', 'id': 'c', 'entity': {'id': 'entity', 'evidence': []},
                                                               'context': {'id': 'ctx', 'evidence': []}, 'mode': 'current', 'filters': []}])
    text = 'Metric and count Entities'
    c = compiled(plan, text, {'count': 'c', 'Entities': 'c'})
    assert check(c.answer.sql, c.answer.allow) == []
    rows = [*ROWS, (dt.date(2025, 6, 11), 2, 'A', 1, 'y'), (dt.date(2025, 6, 12), 3, 'A', None, 'y')]
    with connection(rows) as con:
        actual = con.execute(c.answer.sql).fetchall()[0]
    assert actual[:2] == (20, 2)


def test_share_removes_only_explicit_dimension():
    from tests.plan_fixtures import name
    dimension = {'kind': 'column', 'id': 'd', 'bindings': [{'context': name('ctx'), 'column': name('u.events.category'), 'path': [], 'level': None}]}
    plan = model(dimensions=[dimension], derived=[{'kind': 'share', 'id': 's', 'input': 'm', 'remove_dimensions': ['d'], 'remove_filters': [], 'evidence': []}])
    text = 'Metric by category share'
    c = compiled(plan, text, {'by': 'd', 'category': 'd', 'share': 's'})
    assert check(c.answer.sql, c.answer.allow) == []
    rows = [*ROWS, (dt.date(2025, 6, 10), 5, 'B', 2, 'x')]
    with connection(rows) as con:
        actual = con.execute(c.answer.sql).fetchall()
    assert [(r[0], r[2]) for r in actual] == [('A', .75), ('B', .25)]


def test_ranking_threshold_after_aggregation():
    from tests.plan_fixtures import name
    dimension = {'kind': 'column', 'id': 'd', 'bindings': [{'context': name('ctx'), 'column': name('u.events.category'), 'path': [], 'level': None}]}
    plan = model(dimensions=[dimension], filters=[{'kind': 'compare', 'id': 'f', 'left': {'kind': 'output', 'id': 'm'},
                                                   'op': 'gt', 'value': {'kind': 'integer', 'value': 6}, 'evidence': []}],
                 order=[{'output': 'm', 'direction': 'desc', 'nulls': 'last', 'evidence': []}], limit=1)
    text = 'Metric by category above 6 top 1'
    c = compiled(plan, text, {'by': 'd', 'category': 'd', 'above': 'f', '6': 'f', 'top': 'm', '1': 'limit'})
    assert check(c.answer.sql, c.answer.allow) == []
    with connection([*ROWS, (dt.date(2025, 6, 10), 5, 'B', 2, 'x')]) as con:
        actual = con.execute(c.answer.sql).fetchall()
    assert len(actual) == 1 and actual[0][:2] == ('A', 15)


def test_separate_context_aggregates_never_join_raw_facts():
    from kge.semantics.query import Context
    from tests.plan_fixtures import semantics
    sem = semantics()
    c = sem.contexts['ctx']
    sem.contexts['ctx2'] = Context('ctx2', 'Context two', '', c.term, c.measure, c.expression, c.aggregation, c.grain,
                                 c.fact, c.values, c.filters, c.selectors, c.joins)
    v = vocabulary(sem)
    first = model().measures[0].model_dump(mode='json')
    second = dict(first, id='n', context={'id': 'ctx2', 'evidence': []})
    plan = model(measures=[first, second])
    result = compiled(plan, 'Metric', {'Metric': ['m', 'n']}, v)
    assert check(result.answer.sql, result.answer.allow) == []
    with connection() as con:
        assert con.execute(result.answer.sql).fetchall()[0][:2] == (15, 15)


def test_fiscal_predecessor_crosses_year_boundary_by_dates():
    from kge.semantics.plan import Period
    from kge.semantics.planperiods import bounds, periods
    from tests.plan_fixtures import name
    calendars = {'cal': {'year_start_month': 4, 'quarter_label': 'FY{yy}-Q{n}'}}
    assert bounds(Period(kind='fiscal_quarter', value='FY26-Q1', calendar=name('cal')), calendars) == (dt.date(2025, 4, 1), dt.date(2025, 7, 1))
    assert periods((dt.date(2025, 3, 1), dt.date(2025, 7, 1)), 'fiscal_quarter', calendars['cal']) == (dt.date(2025, 1, 1), dt.date(2025, 4, 1))


def test_explicit_separate_panels_do_not_align_contexts():
    from kge.semantics.query import Context
    from tests.plan_fixtures import semantics
    sem = semantics()
    c = sem.contexts['ctx']
    sem.contexts['ctx2'] = Context('ctx2', 'Context two', '', c.term, c.measure, c.expression, c.aggregation, c.grain,
                                 c.fact, c.values, c.filters, c.selectors, c.joins)
    v = vocabulary(sem)
    first = model().measures[0].model_dump(mode='json')
    plan = model(layout='separate', measures=[first, dict(first, id='n', context={'id': 'ctx2', 'evidence': []})])
    c = compiled(plan, 'Metric separate', {'Metric': ['m', 'n'], 'separate': ['m', 'n']}, v)
    assert check(c.answer.sql, c.answer.allow) == []
    with connection() as con:
        actual = con.execute(c.answer.sql).fetchall()
    assert {(r[0], r[1], r[2]) for r in actual} == {('ctx', 15, None), ('ctx2', None, 15)}


@pytest.mark.parametrize('scope', ['readable', 'restricted', 'hidden_name'])
def test_breakdown_names_share_the_query_and_respect_column_scope(scope):
    from kge.services.ask.engine.planner import result_of
    from tests.plan_fixtures import semantics
    sem = semantics()
    sem.d.GROUP_BY = {'by category': 'u.events.category'}
    sem.d.VOCABULARY = [dict(kind='code_name_pair', params=dict(code='u.events.category', name='u.events.source',
        members=[dict(code='A', name='x')]))]
    if scope == 'restricted':
        sem.d.RESTRICTED_COLUMNS.append(('u', 'events', 'source'))
    v = vocabulary(sem, aliases=[('by category', 'u.events.category')])
    if scope == 'hidden_name':
        from kge.semantics.planvocabulary import build_vocabulary
        v = build_vocabulary(sem, v.binding, ['u'], ['u'], list(v.measures.values()),
                             aliases=[('Metric', 'ctx'), ('by category', 'u.events.category')], denied_phrases=['x'])
    p = model(dimensions=[dict(kind='column', id='d', bindings=[dict(context=name('ctx'), column=name('u.events.category'), path=[], level=None)])])
    c = compiled(p, 'Metric by category', {'by category': 'd'}, v)
    assert check(c.answer.sql, c.answer.allow) == []
    with connection() as con:
        result = result_of(c, con.execute(c.answer.sql).fetchall(), {})
    row = next(r for r in result.rows if not r.total)
    assert row.dimensions[0].value == 'A'
    assert row.dimension_labels == (('x',) if scope == 'readable' else ())
    if scope != 'readable':
        import sqlglot
        from sqlglot import exp
        assert not any(c.name == 'source' for c in sqlglot.parse_one(c.answer.sql, read='duckdb').find_all(exp.Column))


def test_numeric_breakdown_codes_keep_numeric_order_with_names():
    from kge.services.ask.engine.planner import result_of
    from tests.plan_fixtures import semantics
    sem = semantics()
    sem.d.GROUP_BY = {'by entity': 'u.events.entity_id'}
    sem.d.VOCABULARY = [dict(kind='code_name_pair', params=dict(code='u.events.entity_id', name='u.events.category',
        members=[dict(code='2', name='A'), dict(code='10', name='A')]))]
    v = vocabulary(sem, aliases=[('by entity', 'u.events.entity_id')])
    p = model(dimensions=[dict(kind='column', id='d', bindings=[dict(context=name('ctx'), column=name('u.events.entity_id'), path=[], level=None)])])
    c = compiled(p, 'Metric by entity', {'by entity': 'd'}, v)
    rows = [(day, value, category, entity, source) for (day, value, category, _, source), entity in zip(ROWS, [10, 2])]
    with connection(rows) as con:
        result = result_of(c, con.execute(c.answer.sql).fetchall(), {})
    assert [r.dimensions[0].value for r in result.rows if not r.total] == [2, 10]


@pytest.mark.parametrize('layout', ['aligned', 'separate'])
@pytest.mark.parametrize('operation', ['sum', 'snapshot', 'share', 'top'])
def test_paired_group_positions_follow_select_items_without_changing_other_groups(layout, operation):
    from dataclasses import replace
    import json
    import sqlglot
    from sqlglot import exp
    from kge.adapters.warehouse.bigquery import rewrite_sql
    from tests.plan_fixtures import semantics
    sem = semantics()
    sem.d.VOCABULARY = [dict(kind='code_name_pair', params=dict(code='u.events.category', name='u.events.source',
        members=[dict(code='A', name='x'), dict(code='B', name='y')]))]
    v = vocabulary(sem)
    if operation == 'snapshot':
        v = replace(v, measures={'u.metric': replace(v.measures['u.metric'], additive=False)})
    dimension = dict(kind='column', id='d', bindings=[dict(context=name('ctx'), column=name('u.events.category'), path=[], level=None)])
    options = dict(layout=layout, dimensions=[time_dimension(), dimension], time=monthly_time())
    text = 'Metric by month by category 2025-04 through 2025-06'
    assignments = {'by': ['t', 'd'], 'month': 't', 'category': 'd', '2025-04': 'time', 'through': 'time', '2025-06': 'time'}
    if layout == 'separate':
        text += ' separate'
        assignments['separate'] = 'm'
    if operation == 'share':
        options['derived'] = [dict(kind='share', id='s', input='m', remove_dimensions=['t'], remove_filters=[], evidence=[])]
        text += ' share'
        assignments['share'] = 's'
    if operation == 'top':
        options.update(order=[dict(output='m', direction='desc', nulls='last', evidence=[])], limit=1)
        text += ' top 1'
        assignments.update(top='m', **{'1': 'limit'})
    c = compiled(model(**options), text, assignments, v)
    for dialect, sql in [('duckdb', c.answer.sql), ('bigquery', rewrite_sql(c.answer.sql, lambda unit: ''))]:
        assert check(sql, c.answer.allow, dialect) == []
        tree = sqlglot.parse_one(sql, read=dialect)
        groups = [select for select in tree.find_all(exp.Select) if select.args.get('group')]
        assert groups
        for select in groups:
            pair_position = next(i for i, output in enumerate(select.expressions, 1) if output.alias == 'd1')
            assert exp.Literal.number(pair_position) in select.args['group'].expressions
            assert all(not any(group.find_all(exp.Struct)) for group in select.args['group'].expressions)
            for output in select.expressions:
                if output.alias in ('d0', 'observed_at'):
                    assert output.this in select.args['group'].expressions
        if operation == 'share':
            assert {len(select.args['group'].expressions) for select in groups} == {1, 2}
    rows = [*ROWS, (dt.date(2025, 6, 10), 20, 'B', 2, 'y')]
    with connection(rows) as con:
        actual = con.execute(c.answer.sql).fetchall()
    offset = int(layout == 'separate')
    values = {(r[offset], json.loads(r[offset + 1])['code']): r[offset + 2] for r in actual}
    assert values == ({(dt.date(2025, 6, 1), 'B'): 20} if operation == 'top' else {
        (dt.date(2025, month, 1), code): value for month, code, value in
        [(4, 'A', 10), (5, 'A', None), (6, 'A', 5), (4, 'B', None), (5, 'B', None), (6, 'B', 20)]})
    assert {json.loads(r[offset + 1])['name'] for r in actual} == ({'y'} if operation == 'top' else {'x', 'y'})
