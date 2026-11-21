"""Relative and version comparisons agree with arithmetic on tiny governed inputs."""
import datetime as dt
from dataclasses import replace

import duckdb
import pytest

from kge.pipeline.sqlguard import check
from kge.semantics.compile import compile_plan
from kge.semantics.plancheck import CheckedPlan, check_plan
from tests.plan_fixtures import grounded, model, monthly_time, name, semantics, time_dimension, vocabulary
from tests.unit.test_plan_compiler import connection


OFFSETS = (
    ('day', '2025-06-10', '2025-06-10', '2025-06-09', '2024-06-10'),
    ('week', '2025-W24', '2025-06-10', '2025-06-03', '2024-06-11'),
    ('month', '2025-06', '2025-06-10', '2025-05-10', '2024-06-10'),
    ('calendar_quarter', '2025-Q2', '2025-06-10', '2025-03-10', '2024-06-10'),
    ('year', '2025', '2025-06-10', '2024-06-10', '2024-06-10'),
)


def comparison(kind):
    return dict(kind=kind, id='c', inputs=['m'], evidence=[])


def arithmetic():
    return [dict(kind=kind, id=identifier, numerator='c/m/base', denominator='c/m/other', evidence=[])
            for kind, identifier in (('difference', 'delta'), ('percent_change', 'growth'))]


def execute(plan, text, assignments, rows, vocab=None, release_rows=None):
    vocab = vocab or vocabulary()
    decision = check_plan(grounded(plan, text, vocab, assignments), vocab, text)
    assert isinstance(decision, CheckedPlan), decision
    compiled = compile_plan(decision, release_rows)
    assert compiled.answer is not None
    assert check(compiled.answer.sql, compiled.answer.allow) == []
    with connection(rows) as con:
        cursor = con.execute(compiled.answer.sql)
        return [dict(zip([column[0] for column in cursor.description], row, strict=True))
                for row in cursor.fetchall()]


@pytest.mark.parametrize('kind', ('previous_period', 'previous_year'))
@pytest.mark.parametrize('grain,period,current,prior,annual', OFFSETS)
def test_single_period_offset_grid_has_hand_computed_figures(kind, grain, period, current, prior, annual):
    phrase = kind.replace('_', ' ')
    selection = 'year ' + period if grain == 'year' else period
    plan = model(time=dict(kind='single', period=dict(kind=grain, value=period, calendar=name('cal'))),
                 comparisons=[comparison(kind)], derived=arithmetic())
    prior_date = annual if kind == 'previous_year' else prior
    rows = [(dt.date.fromisoformat(date), value, 'A', entity, 'x')
            for date, value, entity in ((current, 14, 1), (current, 20, 2),
                                        (prior_date, 10, 1), (prior_date, 15, 2), (current, -99, 3))]
    values, = execute(plan, f'Metric for {selection} {phrase} difference percent change',
                      {period: 'time', 'year': 'time', phrase: 'c', 'difference': 'delta', 'percent change': 'growth'}, rows)
    assert (values['m'], values['c/m/other'], values['delta']) == (34, 25, 9)
    assert values['growth'] == pytest.approx(.36)


@pytest.mark.parametrize('kind', ('previous_period', 'previous_year'))
@pytest.mark.parametrize('grain,period,current,prior,annual', OFFSETS)
def test_offset_comparison_rekeys_each_time_breakdown(kind, grain, period, current, prior, annual):
    word = 'quarter' if grain == 'calendar_quarter' else grain
    phrase = kind.replace('_', ' ')
    plan = model(time=dict(kind='single', period=dict(kind=grain, value=period, calendar=name('cal'))),
                 dimensions=[dict(time_dimension(), grain=grain)],
                 comparisons=[comparison(kind)], derived=arithmetic())
    rows = [(dt.date.fromisoformat(date), value, 'A', 1, 'x')
            for date, value in ((current, 34), (annual if kind == 'previous_year' else prior, 25))]
    values, = execute(plan, f'Metric by {word} for {period} {phrase} difference percent change',
                      {'by': 't', word: 't', period: 'time', phrase: 'c',
                       'difference': 'delta', 'percent change': 'growth'}, rows)
    assert (values['m'], values['c/m/other'], values['delta']) == (34, 25, 9)
    assert values['growth'] == pytest.approx(.36)


@pytest.mark.parametrize('kind,other', [('previous_period', '2024-12-24'), ('previous_year', '2024-01-02')])
def test_iso_week_comparison_uses_week_year_at_calendar_year_boundary(kind, other):
    phrase = kind.replace('_', ' ')
    plan = model(time=dict(kind='single', period=dict(kind='week', value='2025-W01', calendar=name('cal'))),
                 dimensions=[dict(time_dimension(), grain='week')], comparisons=[comparison(kind)], derived=arithmetic())
    rows = [(dt.date.fromisoformat(date), value, 'A', 1, 'x') for date, value in (('2024-12-31', 34), (other, 25))]
    values, = execute(plan, f'Metric by week for 2025-W01 {phrase} difference percent change',
                      {'by': 't', 'week': 't', '2025-W01': 'time', phrase: 'c',
                       'difference': 'delta', 'percent change': 'growth'}, rows)
    assert values['t'] == dt.date(2024, 12, 30)
    assert (values['m'], values['c/m/other'], values['delta']) == (34, 25, 9)
    assert values['growth'] == pytest.approx(.36)


def test_annual_week_mapping_refuses_a_missing_week_in_the_previous_iso_year():
    vocab = vocabulary()
    plan = model(time=dict(kind='single', period=dict(kind='week', value='2020-W53', calendar=name('cal'))),
                 comparisons=[comparison('previous_year')])
    text = 'Metric for 2020-W53 previous year'
    decision = check_plan(grounded(plan, text, vocab, {'2020-W53': 'time', 'previous year': 'c'}), vocab, text)
    assert not isinstance(decision, CheckedPlan)
    assert any('week' in message.casefold() or 'mapping' in message for message in decision.missing)


@pytest.mark.parametrize('coarser', ('month', 'year'))
def test_annual_week_mapping_preserves_coarser_calendar_breakdowns(coarser):
    dimensions = [dict(kind='time', id='week', calendar=name('cal'), grain='week'),
                  dict(kind='time', id='coarse', calendar=name('cal'), grain=coarser)]
    plan = model(time=dict(kind='single', period=dict(kind='week', value='2025-W01', calendar=name('cal'))),
                 dimensions=dimensions, comparisons=[comparison('previous_year')], derived=arithmetic())
    text = f'Metric by week and {coarser} for 2025-W01 previous year difference percent change'
    rows = [(dt.date.fromisoformat(day), value, 'A', 1, 'x') for day, value in (
        ('2024-12-30', 34), ('2025-01-03', 9), ('2024-01-01', 25), ('2024-01-05', 5))]
    values = execute(plan, text, {'by': ['week', 'coarse'], 'week': 'week', coarser: 'coarse',
                     '2025-W01': 'time', 'previous year': 'c', 'difference': 'delta', 'percent change': 'growth'}, rows)
    coarse = [dt.date(2024, 12, 1), dt.date(2025, 1, 1)] if coarser == 'month' else [dt.date(2024, 1, 1), dt.date(2025, 1, 1)]
    assert [(row['week'], row['coarse'], row['m'], row['c/m/other'], row['delta']) for row in values] == [
        (dt.date(2024, 12, 30), coarse[0], 34, 25, 9), (dt.date(2024, 12, 30), coarse[1], 9, 5, 4)]
    assert [row['growth'] for row in values] == [pytest.approx(.36), pytest.approx(.8)]


def latest_annual_week_comparison():
    vocab = vocabulary(semantics(release=True))
    plan = model(time=dict(kind='latest_released', calendar=name('cal')),
                 dimensions=[dict(time_dimension(), grain='week')],
                 comparisons=[comparison('previous_year')], derived=arithmetic())
    text = 'Metric by week for latest released previous year difference percent change'
    assignments = {'by': 't', 'week': 't', 'latest released': 'time', 'previous year': 'c',
                   'difference': 'delta', 'percent change': 'growth'}
    checked = check_plan(grounded(plan, text, vocab, assignments), vocab, text)
    assert isinstance(checked, CheckedPlan), checked
    releases = [(str(tag), dt.date(year, month, day), True, dt.date(year, month, day))
                for tag in (0, 1) for year, month, day in ((2024, 6, 30), (2024, 7, 31), (2025, 6, 30))]
    return checked, releases


def test_latest_released_annual_week_comparison_maps_adjacent_calendar_months():
    checked, releases = latest_annual_week_comparison()
    compiled = compile_plan(checked, releases)
    assert compiled.answer is not None
    assert check(compiled.answer.sql, compiled.answer.allow) == []
    rows = [(dt.date.fromisoformat(day), value, 'A', 1, 'x') for day, value in (
        ('2025-06-01', 30), ('2025-06-10', 34), ('2025-06-30', 9),
        ('2024-06-02', 10), ('2024-06-11', 25), ('2024-07-01', 5))]
    with connection(rows) as con:
        con.execute('CREATE TABLE calendar_rows(period DATE, "freeze" DATE, release_date DATE, released BOOL)')
        for _, period, ready, released in releases[:3]:
            con.execute('INSERT INTO calendar_rows VALUES (?, ?, ?, ?)', [period, released, released, ready])
        cursor = con.execute(compiled.answer.sql)
        values = [dict(zip([column[0] for column in cursor.description], row, strict=True)) for row in cursor.fetchall()]
    assert [(row['t'], row['m'], row['c/m/other'], row['delta']) for row in values] == [
        (dt.date(2025, 5, 26), 30, 10, 20), (dt.date(2025, 6, 2), 0, 0, 0),
        (dt.date(2025, 6, 9), 34, 25, 9), (dt.date(2025, 6, 16), 0, 0, 0),
        (dt.date(2025, 6, 23), 0, 0, 0), (dt.date(2025, 6, 30), 9, 5, 4)]
    assert [row['growth'] for row in values] == [pytest.approx(2), None, pytest.approx(.36), None, None, pytest.approx(.8)]


@pytest.mark.parametrize('ready', (None, False))
def test_latest_annual_week_comparison_requires_the_adjacent_month_release(ready):
    checked, releases = latest_annual_week_comparison()
    releases = [row for row in releases if not (row[0] == '1' and row[1] == dt.date(2024, 7, 31))]
    if ready is not None:
        releases.append(('1', dt.date(2024, 7, 31), ready, dt.date(2024, 7, 31)))
    with pytest.raises(ValueError, match='July 2024 is (not booked|not yet released)'):
        compile_plan(checked, releases)


def test_year_over_year_preserves_independent_dimension_groups():
    dimension = dict(kind='column', id='category', bindings=[dict(context=name('ctx'),
                     column=name('u.events.category'), path=[], level=None)])
    plan = model(time=dict(kind='single', period=dict(kind='month', value='2025-06', calendar=name('cal'))),
                 dimensions=[dimension], comparisons=[comparison('previous_year')], derived=arithmetic())
    rows = [(dt.date(year, 6, 10), amount, category, 1, 'x')
            for year, amount, category in ((2024, 25, 'A'), (2024, 10, 'B'), (2025, 34, 'A'), (2025, 5, 'B'))]
    values = execute(plan, 'Metric by category 2025-06 previous year difference percent change',
                     {'by': 'category', 'category': 'category', '2025-06': 'time', 'previous year': 'c',
                      'difference': 'delta', 'percent change': 'growth'}, rows)
    actual = {row['category']: row for row in values}
    assert (actual['A']['m'], actual['A']['c/m/other'], actual['A']['delta']) == (34, 25, 9)
    assert actual['A']['growth'] == pytest.approx(.36)
    assert (actual['B']['m'], actual['B']['c/m/other'], actual['B']['delta']) == (5, 10, -5)
    assert actual['B']['growth'] == pytest.approx(-.5)


def test_running_total_and_month_over_month_share_time_order():
    plan = model(time=monthly_time('2025-04', '2025-06'), dimensions=[time_dimension()],
                 comparisons=[comparison('previous_period')], derived=[*arithmetic(),
                     dict(kind='running_total', id='running', input='m', partition_by=[], order_by='t', evidence=[])])
    rows = [(dt.date(2025, month, 10), value, 'A', 1, 'x') for month, value in ((4, 10), (5, 25), (6, 34))]
    values = execute(plan, 'Metric by month 2025-04 through 2025-06 previous period difference percent change running total',
                     {'by': 't', 'month': 't', '2025-04': 'time', 'through': 'time', '2025-06': 'time',
                      'previous period': 'c', 'difference': 'delta', 'percent change': 'growth', 'running total': 'running'}, rows)
    assert [row['running'] for row in values] == [10, 35, 69]
    assert [row['delta'] for row in values] == [None, 15, 9]
    assert [row['growth'] for row in values] == [None, pytest.approx(1.5), pytest.approx(.36)]


@pytest.mark.parametrize('breakdown', (False, True))
def test_current_against_reported_versions_has_hand_computed_figures(breakdown):
    sem = semantics(release=True)
    sem.tables['u.events'].update(since='DATE', until='DATE', active='BOOLEAN')
    sem.scd2 = {'u.events': dict(valid_from='since', valid_to='until', is_current='active', valid_to_inclusive=False)}
    sem.types.update({f'u.events.{column}': kind for column, kind in sem.tables['u.events'].items()})
    sem.contexts['ctx'] = replace(sem.contexts['ctx'], grain='day')
    vocab = vocabulary(sem)
    plan = model(time=monthly_time('2025-05', '2025-06') if breakdown else monthly_time('2025-06', '2025-06'),
                 dimensions=[time_dimension()] if breakdown else [],
                 comparisons=[dict(kind='time_mode', id='c', inputs=['m'], base='current', other='as_reported', evidence=[])],
                 derived=arithmetic())
    text = 'Metric ' + ('by month 2025-05 through 2025-06' if breakdown else '2025-06 through 2025-06')
    text += ' current vs as reported difference percent change'
    assignments = {'by': 't', 'month': 't', '2025-05': 'time', 'through': 'time', '2025-06': 'time',
                   'current': ['m/mode', 'c'], 'vs': 'c', 'as reported': 'c',
                   'difference': 'delta', 'percent change': 'growth'}
    checked = check_plan(grounded(plan, text, vocab, assignments), vocab, text)
    assert isinstance(checked, CheckedPlan), checked
    releases = [('0', dt.date(2025, month, day), True, dt.date(2025, month, 15))
                for month, day in ((5, 31), (6, 30))]
    compiled = compile_plan(checked, releases)
    assert check(compiled.answer.sql, compiled.answer.allow) == []
    with duckdb.connect(':memory:') as con:
        con.execute('CREATE TABLE events(occurred DATE, value DECIMAL(18,2), category VARCHAR, entity_id INT, source VARCHAR, private VARCHAR, since DATE, until DATE, active BOOL)')
        con.execute('CREATE TABLE calendar_rows(period DATE, "freeze" DATE, release_date DATE, released BOOL)')
        for month, day, old, current in ((5, 31, 10, 25), (6, 30, 25, 34)):
            con.execute('INSERT INTO calendar_rows VALUES (?, ?, ?, TRUE)',
                        [dt.date(2025, month, day), dt.date(2025, month, 15), dt.date(2025, month, 15)])
            for value, since, until, active in ((old, dt.date(2025, month, 1), dt.date(2025, month, 20), False),
                                              (current, dt.date(2025, month, 20), None, True)):
                con.execute('INSERT INTO events VALUES (?, ?, ?, ?, ?, NULL, ?, ?, ?)',
                            [dt.date(2025, month, 10), value, 'A', 1, 'x', since, until, active])
        cursor = con.execute(compiled.answer.sql)
        values = [dict(zip([column[0] for column in cursor.description], row, strict=True)) for row in cursor.fetchall()]
    assert [(row['m'], row['c/m/other'], row['delta']) for row in values] == ([(25, 10, 15), (34, 25, 9)] if breakdown else [(34, 25, 9)])
    assert [row['growth'] for row in values] == ([pytest.approx(1.5), pytest.approx(.36)] if breakdown else [pytest.approx(.36)])
