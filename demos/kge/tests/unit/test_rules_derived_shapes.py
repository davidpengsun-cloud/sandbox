"""Rules and supplied plans reach derived shapes on small, independent figures."""
import datetime as dt
import re
from collections import Counter
from dataclasses import replace
from types import SimpleNamespace

import duckdb
import pytest

from kge.pipeline.sqlguard import check
from kge.semantics.compile import compile_plan
from kge.semantics.coverage import ground_plan
from kge.semantics.plan import Propose, QueryPlan
from kge.semantics.plancheck import CheckedPlan, check_plan
from kge.semantics.planvocabulary import MeasureSpec, build_vocabulary
from kge.semantics.query import Context
from kge.semantics.vocabulary import compiled as lexical_vocabulary
from kge.services.ask.engine import planmodel, planrules
from kge.services.ask.engine.core import PRESETS
from kge.services.ask.engine.question import parse
from tests.plan_fixtures import semantics, metric_vocabulary as vocabulary
from tests.unit.test_plan_compiler import connection


ROWS = [(dt.date(2025, month, 10), value, category, entity, source)
        for month, value, category, entity, source in (
            (4, 10, 'A', 1, 'x'), (5, 20, 'A', 1, 'x'), (6, 30, 'A', 1, 'x'),
            (4, 40, 'B', 2, 'x'), (5, 50, 'B', 2, 'x'), (6, 60, 'B', 2, 'x'),
            (4, 70, 'C', 3, 'y'), (5, 80, 'C', 3, 'y'), (6, 90, 'C', 3, 'y'))]


class SuppliedPlan:
    def __init__(self, plan):
        self.plan = plan

    def json(self, prompt):
        return Propose(kind='propose', base_turn=None, plan=self.plan).model_dump(mode='json', by_alias=True)


def declared(monkeypatch, sem=None, aliases=()):
    sem = sem or semantics()
    sem.d.GROUP_BY = {'by category': 'u.events.category', 'by source': 'u.events.source',
                      'by customer': 'u.events.entity_id', **getattr(sem.d, 'GROUP_BY', {})}
    sem.d.ALIASES, sem.d.UNIT_WORDS = {'metric': 'Metric'}, {}
    sem.d.INTENTS = {'count': re.compile(r'\b(?:count|number of)\b', re.I)}
    sem.measure_declarations, sem.output_metadata, sem.measure_specs = {}, {}, {}
    sem.d.VOCABULARY = [dict(kind='predicate', params=dict(column='u.events.source', phrases=['x source'], op='eq', value='x')),
                        dict(kind='value', params=dict(column='u.events.source', values=['x', 'y']))]
    words = lexical_vocabulary(sem.d.VOCABULARY)
    sem.d.FILTERS, sem.d.FILTER_COLUMNS = words.filters, words.columns
    eng = SimpleNamespace(semantics=sem, term_names=list(dict.fromkeys(c.term for c in sem.contexts.values())),
                          FISCAL_START=4, **vars(sem.d))
    monkeypatch.setattr(planrules, 'snapshot', lambda *a, **kw: sem)
    v = vocabulary(sem, aliases=[('Metric', 'ctx'), *sem.d.GROUP_BY.items(), *aliases])
    return eng, v


def read_checked(eng, v, text):
    reply = planrules.read(eng, parse(text, eng.term_names, eng), v, PRESETS['graph'])
    assert reply.kind == 'propose', reply
    checked = check_plan(reply.plan, v, text)
    assert isinstance(checked, CheckedPlan), checked
    replay = planmodel.read(SuppliedPlan(ground_plan(reply.plan, text, 'turn', v)), text, v)
    assert replay.kind == 'propose', replay
    assert isinstance(check_plan(replay.plan, v, text), CheckedPlan)
    return checked


def execute(checked, con, releases=None):
    compiled = compile_plan(checked, releases)
    assert compiled.answer is not None
    assert check(compiled.answer.sql, compiled.answer.allow) == []
    cursor = con.execute(compiled.answer.sql)
    names = [column[0] for column in cursor.description]
    return [dict(zip(names, row)) for row in cursor.fetchall()]


@pytest.mark.parametrize('phrase', [
    'share of Metric by category', 'Metric by category share of total',
    'Metric by category as a share of total', 'Metric by category % of total',
])
@pytest.mark.parametrize('filtered', [False, True])
def test_share_uses_the_filtered_breakdown_total(monkeypatch, phrase, filtered):
    eng, v = declared(monkeypatch)
    text = phrase + ' for Q2 2025' + (' for x source' if filtered else '')
    checked = read_checked(eng, v, text)
    derived, = checked.plan.derived
    dimension, = checked.plan.dimensions
    measure, = checked.plan.measures
    assert derived.kind == 'share' and derived.input == measure.id
    assert derived.remove_dimensions == (dimension.id,) and derived.remove_filters == ()
    with connection(ROWS) as con:
        rows = execute(checked, con)
    totals = {'A': 60, 'B': 150} if filtered else {'A': 60, 'B': 150, 'C': 240}
    assert {r[dimension.id]: r[measure.id] for r in rows} == totals
    assert {r[dimension.id]: r[derived.id] for r in rows} == pytest.approx(
        {category: figure / sum(totals.values()) for category, figure in totals.items()})
    assert sum(r[derived.id] for r in rows) == pytest.approx(1)


@pytest.mark.parametrize('phrase', ['running total of Metric', 'cumulative Metric'])
@pytest.mark.parametrize('partitioned', [False, True])
def test_running_total_orders_time_and_restarts_for_the_other_breakdown(monkeypatch, phrase, partitioned):
    eng, v = declared(monkeypatch)
    text = phrase + ' by month' + (' and category' if partitioned else '') + ' for Q2 2025'
    checked = read_checked(eng, v, text)
    derived, = checked.plan.derived
    time, = (d for d in checked.plan.dimensions if d.kind == 'time')
    groups = [d for d in checked.plan.dimensions if d.kind != 'time']
    measure, = checked.plan.measures
    assert derived.kind == 'running_total' and derived.input == measure.id and derived.order_by == time.id
    assert set(derived.partition_by) == {d.id for d in groups}
    with connection(ROWS) as con:
        rows = execute(checked, con)
    if partitioned:
        assert {(r[groups[0].id], r[time.id].month): r[derived.id] for r in rows} == {
            ('A', 4): 10, ('A', 5): 30, ('A', 6): 60,
            ('B', 4): 40, ('B', 5): 90, ('B', 6): 150,
            ('C', 4): 70, ('C', 5): 150, ('C', 6): 240}
    else:
        assert {r[time.id].month: r[derived.id] for r in rows} == {4: 120, 5: 270, 6: 450}


@pytest.mark.parametrize('phrase', ['top 2 and bottom 2 categories', 'top and bottom 2 categories'])
def test_top_and_bottom_return_the_limit_for_each_labelled_end(monkeypatch, phrase):
    eng, v = declared(monkeypatch)
    checked = read_checked(eng, v, phrase + ' by Metric for Q2 2025')
    order, = checked.plan.order
    dimension, = checked.plan.dimensions
    assert order.direction == 'both' and checked.plan.limit == 2
    with connection(ROWS) as con:
        rows = execute(checked, con)
    assert Counter(r['/end'] for r in rows) == {'top': 2, 'bottom': 1}
    assert {(r['/end'], r['/rank']): (r[dimension.id], r[order.output]) for r in rows} == {
        ('top', 1): ('C', 240), ('top', 2): ('B', 150), ('bottom', 1): ('A', 60)}


@pytest.mark.parametrize('phrase', ['top 2 and bottom 3 categories', 'top 0 and bottom 0 categories'])
def test_invalid_combined_rankings_decline(monkeypatch, phrase):
    eng, v = declared(monkeypatch)
    text = phrase + ' by Metric for Q2 2025'
    reply = planrules.read(eng, parse(text, eng.term_names, eng), v, PRESETS['graph'])
    assert reply.kind != 'propose' or not isinstance(check_plan(reply.plan, v, text), CheckedPlan)


@pytest.mark.parametrize('modes', ['as reported vs current', 'as originally reported vs restated', 'current vs as reported'])
@pytest.mark.parametrize('ask_difference', [False, True])
def test_time_mode_comparison_keeps_both_figures_and_the_difference(monkeypatch, modes, ask_difference):
    sem = semantics(release=True)
    sem.tables['u.events'].update(since='DATE', until='DATE', active='BOOLEAN')
    sem.scd2 = {'u.events': dict(valid_from='since', valid_to='until', is_current='active', valid_to_inclusive=False)}
    eng, v = declared(monkeypatch, sem)
    text = 'Metric for June 2025 ' + modes + (' with difference' if ask_difference else '')
    checked = read_checked(eng, v, text)
    comparison, = checked.plan.comparisons
    difference, = (d for d in checked.plan.derived if d.kind == 'difference')
    assert comparison.kind == 'time_mode'
    assert {comparison.base, comparison.other} == {'as_reported', 'current'}
    with duckdb.connect(':memory:') as con:
        con.execute('CREATE TABLE events(occurred DATE, value INT, category VARCHAR, entity_id INT, source VARCHAR, private VARCHAR, since DATE, until DATE, active BOOL)')
        con.execute("INSERT INTO events VALUES ('2025-06-10', 10, 'A', 1, 'x', NULL, '2025-06-01', '2025-06-20', FALSE), ('2025-06-10', 100, 'A', 1, 'x', NULL, '2025-06-20', NULL, TRUE)")
        con.execute('CREATE TABLE calendar_rows(period DATE, "freeze" DATE, release_date DATE, released BOOL)')
        con.execute("INSERT INTO calendar_rows VALUES ('2025-06-30', '2025-06-15', '2025-06-15', TRUE)")
        row, = execute(checked, con, [('0', dt.date(2025, 6, 30), True, dt.date(2025, 6, 15))])
    identifier, = comparison.inputs
    expected = (100, 10) if comparison.base == 'current' else (10, 100)
    assert (row[identifier], row[f'{comparison.id}/{identifier}/other']) == expected
    assert row[difference.id] == expected[0] - expected[1]


def test_measure_without_release_policy_declines_the_time_mode_comparison(monkeypatch):
    eng, v = declared(monkeypatch)
    text = 'Metric for June 2025 as reported vs current'
    reply = planrules.read(eng, parse(text, eng.term_names, eng), v, PRESETS['graph'])
    decision = check_plan(reply.plan, v, text) if reply.kind == 'propose' else reply
    assert not isinstance(decision, CheckedPlan)
    assert any('release' in reason or 'mode' in reason for reason in decision.missing)


def test_explicit_separate_layout_has_independent_context_panels(monkeypatch):
    sem = semantics()
    ctx = sem.contexts['ctx']
    sem.contexts['certified'] = Context('certified', 'Certified Metric', '', 'Certified Metric', ctx.measure, ctx.expression,
        ctx.aggregation, ctx.grain, ctx.fact, ctx.values, ctx.filters, ctx.selectors, ctx.joins)
    eng, v = declared(monkeypatch, sem, [('Certified Metric', 'certified')])
    checked = read_checked(eng, v, 'Metric and Certified Metric separate for Q2 2025')
    assert checked.plan.layout == 'separate'
    with connection(ROWS) as con:
        rows = execute(checked, con)
    assert len(rows) == 2
    assert {r['_panel'] for r in rows} == {'ctx', 'certified'}
    assert sorted(value for r in rows for m in checked.plan.measures if (value := r[m.id]) is not None) == [450, 450]


@pytest.mark.parametrize('label', ['FY26', 'FY2026'])
def test_fiscal_year_selects_its_declared_calendar_boundaries(monkeypatch, label):
    sem = semantics()
    sem.d.CALENDARS['fiscal'] = {'year_start_month': 4, 'quarter_label': 'FY{yy}-Q{n}'}
    eng, v = declared(monkeypatch, sem)
    checked = read_checked(eng, v, 'Metric for ' + label)
    assert checked.plan.time.kind == 'single' and checked.plan.time.period.kind == 'fiscal_year'
    assert checked.plan.time.period.calendar.id == 'fiscal'
    with connection([*ROWS, (dt.date(2025, 3, 31), 999, 'A', 1, 'x'), (dt.date(2026, 4, 1), 999, 'A', 1, 'x')]) as con:
        row, = execute(checked, con)
    assert row[checked.plan.measures[0].id] == 450


def test_a_supplied_hierarchy_dimension_binds_the_declared_level(monkeypatch):
    sem = semantics(joined=True)
    join = replace(sem.contexts['ctx'].joins[0], kind='hierarchy')
    ctx = sem.contexts['ctx']
    sem.contexts['ctx'] = Context(ctx.id, ctx.label, '', ctx.term, ctx.measure, ctx.expression, ctx.aggregation,
        ctx.grain, ctx.fact, ctx.values, ctx.filters, ctx.selectors, (join,))
    sem.declared = {join.id: join}
    sem.d.GROUP_BY = {'by region': 'u.members.grouping'}
    eng, v = declared(monkeypatch, sem)
    checked = read_checked(eng, v, 'Metric by region for Q2 2025')
    raw = checked.plan.model_dump(mode='json', by_alias=True)
    raw['dimensions'][0]['kind'] = 'hierarchy'
    raw['dimensions'][0]['bindings'][0]['level'] = 1
    checked = check_plan(QueryPlan.model_validate(raw), v, 'Metric by region for Q2 2025')
    assert isinstance(checked, CheckedPlan)
    dimension, = checked.plan.dimensions
    binding, = dimension.bindings
    assert dimension.kind == 'hierarchy' and binding.level == 1
    assert tuple(p.id for p in binding.path) == (join.id,)
    with connection(ROWS) as con:
        con.execute('CREATE TABLE members(entity_id INT, source VARCHAR, grouping VARCHAR)')
        con.execute("INSERT INTO members VALUES (1, 'x', 'North'), (2, 'x', 'North'), (3, 'y', 'South')")
        rows = execute(checked, con)
    assert {r[dimension.id]: r[checked.plan.measures[0].id] for r in rows} == {'North': 210, 'South': 240}


def test_per_entity_count_uses_the_complete_counted_key(monkeypatch):
    sem = semantics()
    sem.entities = {'observation': {'label': 'Observations', 'table': 'u.events', 'key': ['entity_id', 'source']}}
    sem.contexts['context:count:observation'] = Context('context:count:observation', 'Observation count', '',
        'Observation count', 'count:observation', 'count(DISTINCT (events.entity_id, events.source))', 'count', 'day',
        'u.events', ('u.events.entity_id', 'u.events.source'), (), (), ())
    sem.d.ENTITY_WORDS = {'observations': 'observation'}
    eng, base = declared(monkeypatch, sem)
    v = build_vocabulary(sem, base.binding, ['u'], ['u'], [MeasureSpec('u.metric', 'USD', 'entities', True),
        MeasureSpec('count:observation', None, 'observation', False)], aliases=[*sem.d.GROUP_BY.items()])
    checked = read_checked(eng, v, 'number of observations by customer for Q2 2025')
    measure, = checked.plan.measures
    dimension, = checked.plan.dimensions
    assert measure.kind == 'distinct_count' and measure.entity.id == 'observation'
    assert dimension.bindings[0].column.id == 'u.events.entity_id'
    rows = [*ROWS, (dt.date(2025, 6, 12), 7, 'A', 1, 'y'), (dt.date(2025, 6, 13), 8, 'A', 1, None)]
    with connection(rows) as con:
        result = execute(checked, con)
    assert {r[dimension.id]: r[measure.id] for r in result} == {1: 2, 2: 1, 3: 1}
