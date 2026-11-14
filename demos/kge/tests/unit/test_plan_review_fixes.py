import datetime as dt
from dataclasses import replace

import pytest
from sqlglot import exp

from kge.pipeline.sqlguard import check, conjuncts
from kge.semantics.compile import Builder, compile_plan
from kge.semantics.plan import Propose
from kge.semantics.plancheck import CheckedPlan, check_plan
from kge.semantics.query import Asked, Plan, Rule
from kge.services.ask.engine.planner import Planner
from kge.services.ask.engine.wording import Hidden
from tests.plan_fixtures import grounded, model, monthly_time, name, semantics, time_dimension, vocabulary
from tests.unit.test_plan_compiler import connection
from tests.unit.test_plan_relational_matrix import replace_context
from tests.unit.test_planner_jobs import jobs, setup


def dated_vocabulary(condition):
    sem = semantics(joined=True)
    sem.tables['u.members'].update({'since': 'DATE', 'until': 'DATE', 'active': 'BOOLEAN'})
    sem.scd2['u.members'] = {'valid_from': 'since', 'valid_to': 'until', 'is_current': 'active', 'valid_to_inclusive': False}
    ctx = sem.contexts['ctx']
    sem.contexts['ctx'] = replace_context(ctx, joins=tuple(replace(j, required=True, condition=j.condition + ' AND ' + condition) for j in ctx.joins))
    return vocabulary(sem)


@pytest.mark.parametrize('comparison_date', ['members.since', 'since'])
def test_self_dated_temporal_authority_abstains_naming_join(comparison_date):
    v = dated_vocabulary(f'members.since <= {comparison_date} AND (members.until IS NULL OR members.until > {comparison_date})')
    decision = check_plan(grounded(model(), 'Metric', v), v, 'Metric')
    assert not isinstance(decision, CheckedPlan)
    assert decision.kind == 'abstain'
    assert 'member_join' in str(decision.missing)


@pytest.mark.parametrize('comparison_date', ['members.since', 'since'])
def test_temporal_authority_resolves_unqualified_self_references(comparison_date):
    v = dated_vocabulary(f'members.since <= {comparison_date} AND (members.until IS NULL OR members.until > {comparison_date})')
    p = Plan(v.semantics, v.contexts['ctx'], Asked())
    p.fact = p.name(p.context.fact)
    with pytest.raises(ValueError, match='incomplete declared temporal'):
        p.add_join(p.context.joins[0])


@pytest.mark.parametrize('comparison_date', ['occurred', 'OCCURRED'])
def test_temporal_authority_accepts_resolved_date_from_another_occurrence(comparison_date):
    v = dated_vocabulary(f'members.since <= {comparison_date} AND (members.until IS NULL OR members.until > {comparison_date})')
    p = Plan(v.semantics, v.contexts['ctx'], Asked())
    p.fact = p.name(p.context.fact)
    p.add_join(p.context.joins[0])
    assert 'members.active' not in p.joins[0]


@pytest.mark.parametrize('day', ['2025-02-28', '2024-02-29'])
def test_prior_year_day_requires_bijective_keys(day):
    v = vocabulary()
    p = model(time={'kind': 'single', 'period': {'kind': 'day', 'value': day, 'calendar': name('cal')}},
              dimensions=[dict(time_dimension(), grain='day')],
              comparisons=[{'kind': 'previous_year', 'id': 'c', 'inputs': ['m'], 'evidence': []}])
    text = f'Metric by day {day} previous year'
    p = grounded(p, text, v, {'by': 't', 'day': 't', day: 'time', 'previous year': 'c'})
    decision = check_plan(p, v, text)
    assert not isinstance(decision, CheckedPlan)
    assert decision.kind == 'abstain'
    assert 'one-to-one' in str(decision.missing)


def test_fiscal_comparison_with_daily_output_requires_bijective_keys():
    sem = semantics()
    sem.d.CALENDARS['cal'] = {'year_start_month': 4, 'quarter_label': 'FY{yy}-Q{n}'}
    v = vocabulary(sem)
    p = model(time={'kind': 'single', 'period': {'kind': 'fiscal_quarter', 'value': 'FY25-Q4', 'calendar': name('cal')}},
              dimensions=[dict(time_dimension(), grain='day')],
              comparisons=[{'kind': 'previous_year', 'id': 'c', 'inputs': ['m'], 'evidence': []}])
    text = 'Metric by day FY25-Q4 previous year'
    decision = check_plan(grounded(p, text, v, {'by': 't', 'day': 't', 'FY25-Q4': 'time', 'previous year': 'c'}), v, text)
    assert decision.kind == 'abstain'
    assert 'one-to-one' in str(decision.missing)


@pytest.mark.parametrize('fact', ['events', 'EVENTS'])
@pytest.mark.parametrize('release_alias', [None, 'ReleaseRows'])
def test_gate_matches_normalized_base_aliases_with_exact_policy(fact, release_alias):
    sem = semantics(release=True)
    sem.tables['u.' + fact] = sem.tables.pop('u.events')
    sem.d.PERIODS['u.' + fact] = sem.d.PERIODS.pop('u.events')
    sem.entities['entity']['table'] = 'u.' + fact
    ctx = sem.contexts['ctx']
    sem.contexts['ctx'] = replace_context(ctx, fact='u.' + fact, expression=ctx.expression.replace('events.', fact + '.'),
        values=tuple(value.replace('u.events.', 'u.' + fact + '.') for value in ctx.values),
        filters=tuple(replace(rule, table='u.' + fact, predicate=rule.predicate.replace('events.', fact + '.')) for rule in ctx.filters),
        joins=tuple(replace(join, source='u.' + fact, alias=release_alias, condition=join.condition.replace('events.', fact + '.')) for join in ctx.joins))
    v = vocabulary(sem)
    text = 'Metric 2025-04 through 2025-06'
    checked = check_plan(grounded(model(time=monthly_time()), text, v,
                                 {'2025-04': 'time', 'through': 'time', '2025-06': 'time'}), v, text)
    assert isinstance(checked, CheckedPlan), checked
    compiled = compile_plan(checked, [('0', dt.date(2025, month, 30 if month in (4, 6) else 31), True, dt.date(2025, month + 1, 1))
                                      for month in (4, 5, 6)])
    assert check(compiled.answer.sql, compiled.answer.allow) == []
    assert check(compiled.answer.sql.replace(f'{fact}.value >= 0', 'TRUE'), compiled.answer.allow)


@pytest.mark.parametrize('policy', ['filter', 'selector', 'version', 'window'])
def test_gate_catches_compiler_omitting_context_policy(monkeypatch, policy):
    original = Builder.cte

    def omitted(node):
        if policy == 'filter':
            return isinstance(node, exp.GTE) and isinstance(node.this, exp.Column) and node.this.name == 'value'
        if policy == 'selector':
            return node.find(exp.Exists) is not None
        if policy == 'version':
            return isinstance(node, exp.Column) and node.name == 'active'
        return isinstance(node, (exp.GTE, exp.LT)) and isinstance(node.this, exp.Column) and node.this.name == 'occurred'

    def omit_filter(self, name, query):
        if name == 'b0':
            kept = [node for node in conjuncts(query.args['where'].this) if not omitted(node)]
            query.set('where', exp.Where(this=exp.and_(*kept) if kept else exp.true()))
        original(self, name, query)

    monkeypatch.setattr(Builder, 'cte', omit_filter)
    sem = semantics()
    if policy == 'selector':
        sem.tables['u.exclusions'] = {'entity_id': 'INTEGER'}
        sem.contexts['ctx'] = replace_context(sem.contexts['ctx'], selectors=(Rule('exclude', '', 'events.entity_id = exclusions.entity_id', 'u.exclusions', 'exclude'),))
    if policy == 'version':
        sem.tables['u.events'].update({'active': 'BOOLEAN', 'since': 'DATE', 'until': 'DATE'})
        sem.scd2['u.events'] = {'valid_from': 'since', 'valid_to': 'until', 'is_current': 'active'}
    v = vocabulary(sem)
    p = model(time=monthly_time()) if policy == 'window' else model()
    text = 'Metric 2025-04 through 2025-06' if policy == 'window' else 'Metric'
    checked = check_plan(grounded(p, text, v, {'2025-04': 'time', 'through': 'time', '2025-06': 'time'}), v, text)
    compiled = compile_plan(checked)
    problems = check(compiled.answer.sql, compiled.answer.allow)
    assert problems
    if policy == 'filter':
        assert any('required predicate' in problem for problem in problems)


def test_partial_current_join_is_typed_abstention_before_jobs(monkeypatch, tmp_path):
    fake, warehouse = setup(monkeypatch, tmp_path, True)
    v = dated_vocabulary('members.active = 1')
    decision = check_plan(grounded(model(), 'Metric', v), v, 'Metric')
    assert decision.kind == 'abstain'
    assert 'member_join' in str(decision.missing)
    fake.requests.clear()
    result = Planner(v, warehouse, Hidden([], set(), [])).answer('Metric', Propose(kind='propose', base_turn=None, plan=grounded(model(), 'Metric', v)))
    assert result['kind'] == 'abstain'
    assert 'member_join' in str(result['missing'])
    assert jobs(fake) == (0, 0)


@pytest.mark.parametrize('additive,released', [(True, True), (False, True), (True, False)])
@pytest.mark.parametrize('dimensions', [[], [time_dimension()]])
@pytest.mark.parametrize('layout', ['aligned', 'separate'])
def test_empty_release_period_zero_only_for_released_additive(additive, released, dimensions, layout):
    v = vocabulary(semantics(release=True))
    v = replace(v, measures={'u.metric': replace(v.measures['u.metric'], additive=additive)})
    p = model(time=monthly_time('2025-05', '2025-05'), dimensions=dimensions, layout=layout)
    text = 'Metric' + (' separate' if layout == 'separate' else '') + (' by month' if dimensions else '') + ' 2025-05 through 2025-05'
    checked = check_plan(grounded(p, text, v, {'separate': 'm', 'by': 't', 'month': 't', '2025-05': 'time', 'through': 'time'}), v, text)
    release_rows = [('0', dt.date(2025, 5, 31), released, dt.date(2025, 6, 1))]
    if not released:
        with pytest.raises(ValueError, match='not yet released'):
            compile_plan(checked, release_rows)
        return
    c = compile_plan(checked, release_rows)
    assert check(c.answer.sql, c.answer.allow) == []
    with connection([]) as con:
        con.execute('CREATE TABLE calendar_rows(period DATE, "freeze" DATE, release_date DATE, released BOOL)')
        con.execute("INSERT INTO calendar_rows VALUES ('2025-05-31', '2025-05-31', '2025-06-01', TRUE)")
        row = con.execute(c.answer.sql).fetchone()
    keys = (*(['ctx'] if layout == 'separate' else []), *([dt.date(2025, 5, 1)] if dimensions else []))
    assert row == ((*keys, 0, None) if additive else (*keys, None, 'missing_observation'))


@pytest.mark.parametrize('operation', ['running_total', 'previous_period'])
def test_released_empty_month_contributes_zero_without_extra_jobs(monkeypatch, tmp_path, operation):
    fake, warehouse = setup(monkeypatch, tmp_path, True)
    v = vocabulary(semantics(release=True))
    options = {'time': monthly_time(), 'dimensions': [time_dimension()]}
    if operation == 'running_total':
        options['derived'] = [{'kind': 'running_total', 'id': 'c', 'input': 'm', 'partition_by': [], 'order_by': 't', 'evidence': []}]
    else:
        options['comparisons'] = [{'kind': operation, 'id': 'c', 'inputs': ['m'], 'evidence': []}]
    text = 'Metric by month 2025-04 through 2025-06 ' + operation.replace('_', ' ')
    p = grounded(model(**options), text, v, {'by': 't', 'month': 't', '2025-04': 'time', 'through': 'time', '2025-06': 'time', operation.replace('_', ' '): 'c'})
    fake.requests.clear()
    result = Planner(v, warehouse, Hidden([], set(), [])).answer(text, Propose(kind='propose', base_turn=None, plan=p))
    assert result['kind'] == 'result', result
    rows = [r for r in result['result']['rows'] if not r.get('total')]
    total = result['result']['rows'][-1]
    assert total['total'] and float(total['cells'][0]['value']['value']) == 15
    assert total['cells'][-1]['value'] == {'kind': 'null'}
    if operation == 'previous_period':
        assert rows[0]['cells'][-1]['null_reasons'] == ['outside_requested_range']
        assert float(rows[-1]['cells'][-1]['value']['value']) == 0
    assert [float(row['cells'][-1]['value']['value']) if row['cells'][-1]['value']['kind'] != 'null' else None for row in rows] == ([10, 10, 15] if operation == 'running_total' else [None, 10, 0])
    assert all(not cell['null_reasons'] for row in (rows if operation == 'running_total' else rows[1:]) for cell in row['cells'])
    assert jobs(fake) == (2, 2)


@pytest.mark.parametrize('dimensions', [[], [time_dimension()]])
def test_released_separate_contexts_preserve_inapplicable_nulls(dimensions):
    sem = semantics(release=True)
    sem.contexts['ctx2'] = replace_context(sem.contexts['ctx'], id='ctx2', label='Context two')
    v = vocabulary(sem)
    first = model().measures[0].model_dump(mode='json')
    p = model(time=monthly_time('2025-04', '2025-04'), dimensions=dimensions, layout='separate',
              measures=[first, dict(first, id='n', context=name('ctx2'))])
    text = 'Metric separate' + (' by month' if dimensions else '') + ' 2025-04 through 2025-04'
    checked = check_plan(grounded(p, text, v, {'Metric': ['m', 'n'], 'separate': ['m', 'n'],
                                             'by': 't', 'month': 't', '2025-04': 'time', 'through': 'time'}), v, text)
    assert isinstance(checked, CheckedPlan), checked
    c = compile_plan(checked, [('0', dt.date(2025, 4, 30), True, dt.date(2025, 5, 1))])
    assert check(c.answer.sql, c.answer.allow) == []
    with connection() as con:
        con.execute('CREATE TABLE calendar_rows(period DATE, "freeze" DATE, release_date DATE, released BOOL)')
        con.execute("INSERT INTO calendar_rows VALUES ('2025-04-30', '2025-04-30', '2025-05-01', TRUE)")
        rows = con.execute(c.answer.sql).fetchall()
    keys = (dt.date(2025, 4, 1),) if dimensions else ()
    assert set(rows) == {('ctx', *keys, 10, None, None, 'missing_observation'),
                         ('ctx2', *keys, None, 10, 'missing_observation', None)}
