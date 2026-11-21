"""Declared day spines retain empty periods and derive fiscal and ISO week boundaries."""
import datetime as dt

import pytest
from pydantic import ValidationError
import sqlglot

from kge.pipeline.sqlguard import check
from kge.semantics.compile import compile_plan
from kge.semantics.coverage import ground_plan
from kge.semantics.plan import Period
from kge.semantics.plancheck import CheckedPlan, check_plan
from kge.semantics.planperiods import bounds, shifted_mapping
from tests.plan_fixtures import model, monthly_time, name, semantics, vocabulary
from tests.unit.test_plan_compiler import connection


def declared():
    sem = semantics()
    sem.tables['u.days'] = {'date': 'DATE', 'calendar_year': 'INTEGER'}
    spine = {'table': 'u.days', 'column': 'date', 'attributes': {'year': 'u.days.calendar_year'}}
    sem.d.CALENDARS['cal']['spine'] = spine
    sem.d.CALENDARS['fiscal'] = {'year_start_month': 4, 'quarter_label': 'FY{yy}-Q{n}', 'spine': spine}
    return sem


def compiled(plan, text, sem=None):
    vocab = vocabulary(sem or declared())
    selected = ground_plan(plan, text, 'turn', vocab)
    checked = check_plan(selected, vocab, text)
    assert isinstance(checked, CheckedPlan), checked
    result = compile_plan(checked)
    assert check(result.answer.sql, result.answer.allow) == []
    return result


def calendar_connection(rows, first, last):
    con = connection(rows)
    con.execute('CREATE TABLE days(date DATE PRIMARY KEY, calendar_year INTEGER)')
    con.executemany('INSERT INTO days VALUES (?, ?)', [(first + dt.timedelta(days=offset),
        (first + dt.timedelta(days=offset)).year) for offset in range((last - first).days)])
    return con


@pytest.mark.parametrize('kind,value,first,last', [
    ('week', '2025-W01', '2024-12-30', '2025-01-06'),
    ('week', '2020-W53', '2020-12-28', '2021-01-04'),
    ('week', '2024-W52', '2024-12-23', '2024-12-30'),
])
def test_iso_week_uses_its_week_year(kind, value, first, last):
    period = Period(kind=kind, value=value, calendar=name('cal'))
    assert bounds(period, {'cal': {'year_start_month': 1}}) == (dt.date.fromisoformat(first), dt.date.fromisoformat(last))


@pytest.mark.parametrize('value', ['2025-W00', '2025-W54', '2025-W53', '2025-1', '2025-W1'])
def test_invalid_iso_week_is_refused(value):
    with pytest.raises(ValidationError):
        Period(kind='week', value=value, calendar=name('cal'))


@pytest.mark.parametrize('grain,calendar,text,time,expected', [
    ('day', 'cal', 'Metric by day for 2025-04-01 through 2025-04-03',
     {'kind': 'range', 'first': {'kind': 'day', 'value': '2025-04-01', 'calendar': name('cal')},
      'last': {'kind': 'day', 'value': '2025-04-03', 'calendar': name('cal')}},
     [('2025-04-01', 10), ('2025-04-02', None), ('2025-04-03', 5)]),
    ('month', 'cal', 'Metric by month for April through June 2025', monthly_time(),
     [('2025-04-01', 15), ('2025-05-01', None), ('2025-06-01', 7)]),
    ('fiscal_quarter', 'fiscal', 'Metric by fiscal quarter for FY26',
     {'kind': 'single', 'period': {'kind': 'fiscal_year', 'value': 'FY26', 'calendar': name('fiscal')}},
     [('2025-04-01', 22), ('2025-07-01', None), ('2025-10-01', None), ('2026-01-01', None)]),
    ('fiscal_year', 'fiscal', 'Metric by fiscal year for FY25 through FY26',
     {'kind': 'range', 'first': {'kind': 'fiscal_year', 'value': 'FY25', 'calendar': name('fiscal')},
      'last': {'kind': 'fiscal_year', 'value': 'FY26', 'calendar': name('fiscal')}},
     [('2024-04-01', None), ('2025-04-01', 22)]),
])
def test_spine_buckets_are_derived_from_declared_dates(grain, calendar, text, time, expected):
    plan = model(time=time, dimensions=[{'kind': 'time', 'id': 't', 'grain': grain, 'calendar': name(calendar)}])
    result = compiled(plan, text)
    assert 'FROM days AS calendar_days' in result.answer.sql
    rows = [(dt.date(2025, 4, 1), 10, 'A', 1, 'x'), (dt.date(2025, 4, 3), 5, 'A', 1, 'x'),
            (dt.date(2025, 6, 1), 7, 'A', 1, 'x')]
    with calendar_connection(rows, dt.date(2024, 4, 1), dt.date(2026, 4, 1)) as con:
        actual = con.execute(result.answer.sql).fetchall()
    assert [(row[0].isoformat()[:10], row[1]) for row in actual] == expected


def test_week_breakdown_crosses_calendar_year_with_an_empty_week():
    time = {'kind': 'range', 'first': {'kind': 'day', 'value': '2024-12-23', 'calendar': name('cal')},
            'last': {'kind': 'day', 'value': '2025-01-12', 'calendar': name('cal')}}
    plan = model(time=time, dimensions=[{'kind': 'time', 'id': 't', 'grain': 'week', 'calendar': name('cal')}])
    result = compiled(plan, 'Metric by week for 2024-12-23 through 2025-01-12')
    rows = [(dt.date(2024, 12, 23), 8, 'A', 1, 'x'), (dt.date(2024, 12, 30), 10, 'A', 1, 'x'),
            (dt.date(2025, 1, 2), 4, 'A', 1, 'x'), (dt.date(2025, 1, 13), 20, 'A', 1, 'x')]
    with calendar_connection(rows, dt.date(2024, 12, 23), dt.date(2025, 1, 20)) as con:
        actual = con.execute(result.answer.sql).fetchall()
    assert [(row[0].isocalendar()[:2], row[1]) for row in actual] == [((2024, 52), 8), ((2025, 1), 14), ((2025, 2), None)]
    translated = sqlglot.transpile(result.answer.sql, read='duckdb', write='bigquery')[0]
    assert 'DATE_DIFF' in translated and 'WEEK)' not in translated
    assert check(translated, result.answer.allow, dialect='bigquery') == []


def test_week_over_week_shifts_seven_days_and_rekeys_prior_week():
    plan = model(time=monthly_time('2025-06', '2025-06'),
        dimensions=[{'kind': 'time', 'id': 't', 'grain': 'week', 'calendar': name('cal')}],
        comparisons=[{'kind': 'previous_period', 'id': 'c', 'inputs': ['m'], 'evidence': []}])
    vocab = vocabulary(declared())
    assert shifted_mapping(plan, 'previous_period', vocab.calendars) == (dt.date(2025, 5, 25), dt.date(2025, 6, 24))
    result = compiled(plan, 'Metric by week for June 2025 previous period')
    rows = [(dt.date(2025, 6, day), value, 'A', 1, 'x') for day, value in [(1, 1), (2, 2), (9, 3), (16, 4), (23, 5), (30, 6)]]
    with calendar_connection(rows, dt.date(2025, 5, 25), dt.date(2025, 7, 1)) as con:
        actual = con.execute(result.answer.sql).fetchall()
    assert [(row[0].isoformat(), row[1], row[2]) for row in actual] == [
        ('2025-05-26', 1, None), ('2025-06-02', 2, 1), ('2025-06-09', 3, 2),
        ('2025-06-16', 4, 3), ('2025-06-23', 5, 4), ('2025-06-30', 6, 5)]


@pytest.mark.parametrize('coarser', ['month', 'year'])
def test_declared_spine_preserves_iso_year_mapping_across_calendar_boundaries(coarser):
    """One ISO week spans two calendar buckets; each keeps its own annual counterpart."""
    plan = model(time={'kind': 'single', 'period': {'kind': 'week', 'value': '2025-W01', 'calendar': name('cal')}},
        dimensions=[{'kind': 'time', 'id': 'week', 'grain': 'week', 'calendar': name('cal')},
                    {'kind': 'time', 'id': 'coarse', 'grain': coarser, 'calendar': name('cal')}],
        comparisons=[{'kind': 'previous_year', 'id': 'c', 'inputs': ['m'], 'evidence': []}])
    result = compiled(plan, f'Metric by week and {coarser} for 2025-W01 previous year')
    rows = [(dt.date.fromisoformat(day), amount, 'A', 1, 'x') for day, amount in [
        ('2024-12-30', 34), ('2025-01-03', 9), ('2024-01-01', 25), ('2024-01-05', 5)]]
    with calendar_connection(rows, dt.date(2023, 1, 1), dt.date(2026, 1, 1)) as con:
        cursor = con.execute(result.answer.sql)
        actual = [dict(zip([column[0] for column in cursor.description], row, strict=True)) for row in cursor.fetchall()]
    buckets = [dt.date(2024, 12, 1), dt.date(2025, 1, 1)] if coarser == 'month' else [dt.date(2024, 1, 1), dt.date(2025, 1, 1)]
    assert [(row['week'], row['coarse'], row['m'], row['c/m/other']) for row in actual] == [
        (dt.date(2024, 12, 30), buckets[0], 34, 25), (dt.date(2024, 12, 30), buckets[1], 9, 5)]


def test_gate_refuses_a_spine_date_window_or_bucket_change():
    plan = model(time=monthly_time(), dimensions=[{'kind': 'time', 'id': 't', 'grain': 'month', 'calendar': name('cal')}])
    result = compiled(plan, 'Metric by month for April through June 2025')
    for sql in (result.answer.sql.replace("'2025-04-01'", "'2025-03-01'"),
                result.answer.sql.replace("DATE_TRUNC('MONTH', \"calendar_days\".\"date\")", "DATE_TRUNC('YEAR', \"calendar_days\".\"date\")"),
                result.answer.sql.replace('CROSS JOIN calendar_ticks', 'CROSS JOIN calendar_ticks AS changed')):
        assert sql != result.answer.sql
        assert check(sql, result.answer.allow)


def test_an_undeclared_spine_table_is_refused_before_sql_executes():
    sem = declared()
    sem.d.CALENDARS['cal']['spine']['table'] = 'u.absent'
    plan = model(time=monthly_time(), dimensions=[{'kind': 'time', 'id': 't', 'grain': 'month', 'calendar': name('cal')}])
    with pytest.raises(ValueError, match='outside the readable scope'):
        compiled(plan, 'Metric by month for April through June 2025', sem)


def test_a_declared_spine_cannot_read_a_restricted_date_column():
    sem = declared()
    sem.d.RESTRICTED_COLUMNS.append(('u', 'days', 'date'))
    plan = model(time=monthly_time(), dimensions=[{'kind': 'time', 'id': 't', 'grain': 'month', 'calendar': name('cal')}])
    with pytest.raises(ValueError, match='outside the readable scope'):
        compiled(plan, 'Metric by month for April through June 2025', sem)


def test_a_shared_calendar_uses_its_spine_only_in_the_declared_source():
    sem = declared()
    sem.d.CALENDARS['cal']['spine']['table'] = 'other.calendar_days'
    sem.tables['other.calendar_days'] = {'date': 'DATE'}
    plan = model(time=monthly_time(), dimensions=[{'kind': 'time', 'id': 't', 'grain': 'month', 'calendar': name('cal')}])
    result = compiled(plan, 'Metric by month for April through June 2025', sem)
    assert 'calendar_days' not in result.answer.sql
    assert all('other' not in table for table in result.answer.allow.tables)
    rows = [(dt.date(2025, 4, 1), 10, 'A', 1, 'x'), (dt.date(2025, 6, 1), 5, 'A', 1, 'x')]
    with connection(rows) as con:
        actual = con.execute(result.answer.sql).fetchall()
    assert [(row[0].isoformat(), row[1]) for row in actual] == [('2025-04-01', 10), ('2025-05-01', None), ('2025-06-01', 5)]
