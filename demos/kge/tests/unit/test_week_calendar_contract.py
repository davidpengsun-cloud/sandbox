"""Pack calendar contracts and ISO week arithmetic on independent, small declarations."""
import datetime as dt
from pathlib import Path

import pytest

from kge.adapters.warehouse.bigquery import rewrite_sql
from kge.compiler.checks import SECTIONS, calendars, shape
from kge.compiler.pack import Pack
from kge.compiler.vocabulary import calendar_entries, measure_entries
from kge.semantics.plan import Period
from kge.semantics.planperiods import bounds, moved, periods, shifted_mapping, week_start
from tests.plan_fixtures import model, name, semantics, vocabulary


CALENDAR = {'id': 'cal', 'year_start_month': 1, 'quarter_label': '{yyyy}-Q{n}'}
SPINE = {'table': 'u.days', 'column': 'day'}


def shaped(section, declaration):
    findings = []
    shape([declaration], SECTIONS[section], section, findings)
    return findings


def calendar_pack(calendar=None, column_type='DATE', primary_key=('day',)):
    table = {'name': 'days', 'columns': [{'name': 'day', 'type': column_type}, {'name': 'year', 'type': 'INTEGER'}],
             'primary_key': list(primary_key)}
    return Pack('sample', Path('scratch'), data={'calendars': [calendar or {**CALENDAR, 'spine': SPINE}], 'measures': []},
                lock={'sources': {'u': {'tables': [table]}}})


@pytest.mark.parametrize('weekday,index', [(None, 0), ('monday', 0), ('tuesday', 1), ('wednesday', 2),
                                          ('thursday', 3), ('friday', 4), ('saturday', 5), ('sunday', 6)])
def test_calendar_accepts_each_declared_week_start_and_defaults_to_monday(weekday, index):
    declaration = {**CALENDAR, **({'week_start': weekday} if weekday else {})}
    assert shaped('calendars', declaration) == []
    assert week_start(declaration) == index


@pytest.mark.parametrize('weekday', ['', 'Monday', 'workday', 1, True, None])
def test_check_pack_refuses_a_week_start_outside_the_closed_weekday_set(weekday):
    findings = shaped('calendars', {**CALENDAR, 'week_start': weekday})
    assert len(findings) == 1
    assert findings[0][0] == 'calendars[0].week_start'


@pytest.mark.parametrize('spine,path', [
    ({'table': 'u.days'}, 'column'),
    ({'column': 'day'}, 'table'),
    ({'table': '', 'column': 'day'}, 'table'),
    ({'table': 'u.days', 'column': ''}, 'column'),
    ({**SPINE, 'expression': 'day'}, 'expression'),
])
def test_check_pack_requires_a_plain_table_and_column_for_the_spine(spine, path):
    findings = shaped('calendars', {**CALENDAR, 'spine': spine})
    assert [at for at, _ in findings] == ['calendars[0].spine.' + path]


def test_check_pack_accepts_a_date_primary_key_as_a_calendar_spine():
    pack = calendar_pack()
    assert shaped('calendars', pack.data['calendars'][0]) == []
    assert calendars(pack) == []


@pytest.mark.parametrize('spine,path', [
    ({'table': 'u.unknown', 'column': 'day'}, 'table'),
    ({'table': 'other.days', 'column': 'day'}, 'table'),
    ({'table': 'u.days', 'column': 'unknown'}, 'column'),
])
def test_check_pack_refuses_missing_calendar_table_or_column(spine, path):
    findings = calendars(calendar_pack({**CALENDAR, 'spine': spine}))
    assert len(findings) == 1
    assert findings[0].rule == 'S09'
    assert findings[0].path == 'pack.yaml:calendars[0].spine.' + path


@pytest.mark.parametrize('column_type', ['VARCHAR', 'INTEGER', 'TIMESTAMP'])
def test_check_pack_refuses_a_calendar_spine_that_is_not_a_date(column_type):
    finding, = calendars(calendar_pack(column_type=column_type))
    assert finding.path == 'pack.yaml:calendars[0].spine.column'
    assert 'DATE' in finding.message


@pytest.mark.parametrize('primary_key', [(), ('year',), ('day', 'year')])
def test_check_pack_requires_the_date_itself_to_be_the_complete_calendar_key(primary_key):
    finding, = calendars(calendar_pack(primary_key=primary_key))
    assert finding.path == 'pack.yaml:calendars[0].spine.column'
    assert 'primary key' in finding.message


@pytest.mark.parametrize('empty', ['zero', 'null'])
def test_measure_empty_policy_survives_vocabulary_emission_and_binding(empty):
    fields = SECTIONS['measures'][0]
    findings = []
    shape(empty, fields['empty'].kind, 'measures[0].empty', findings)
    assert findings == []
    sem = semantics()
    sem.d.VOCABULARY = measure_entries({'measures': [{'id': 'u.metric', 'empty': empty}]})
    assert sem.d.VOCABULARY[0]['params'] == {'measure': 'u.metric', 'empty': empty}
    assert vocabulary(sem).measures['u.metric'].empty == empty


@pytest.mark.parametrize('empty', ['ZERO', '', 'missing', 0, False, None])
def test_check_pack_refuses_unknown_measure_empty_policy(empty):
    findings = []
    shape(empty, SECTIONS['measures'][0]['empty'].kind, 'measures[0].empty', findings)
    assert len(findings) == 1 and findings[0][0] == 'measures[0].empty'


def test_omitted_calendar_and_empty_options_do_not_emit_new_policy_rows():
    assert calendar_entries({'calendars': [CALENDAR]}) == []
    assert measure_entries({'measures': [{'id': 'u.metric'}]}) == []
    assert vocabulary().measures['u.metric'].empty is None


def test_calendar_options_have_stable_calendar_ids_and_keep_the_declared_data():
    declaration = {**CALENDAR, 'week_start': 'monday', 'spine': SPINE}
    row, = calendar_entries({'calendars': [declaration]})
    assert row == {'id': 'calendar-options:cal', 'kind': 'calendar_options', 'ordinal': 1,
                   'params': {'calendar': 'cal', 'week_start': 'monday', 'spine': SPINE}}
    shifted, = calendar_entries({'calendars': [{'id': 'other'}, declaration]})
    assert shifted['id'] == row['id']
    assert shifted['params'] == row['params']


@pytest.mark.parametrize('value', ['2025-W01', '2020-W53', '2024-W52'])
def test_week_period_accepts_valid_iso_week_years(value):
    assert Period.model_validate({'kind': 'week', 'value': value, 'calendar': name('cal')}).value == value


@pytest.mark.parametrize('value', ['2025-W1', '2025-w01', '2025-W00', '2025-W54', '2024-W53', '2025-01'])
def test_week_period_refuses_noncanonical_or_impossible_iso_weeks(value):
    with pytest.raises(ValueError):
        Period.model_validate({'kind': 'week', 'value': value, 'calendar': name('cal')})


def test_iso_week_one_uses_the_iso_year_even_when_its_monday_is_in_december():
    period = Period.model_validate({'kind': 'week', 'value': '2025-W01', 'calendar': name('cal')})
    first, last = bounds(period, {'cal': CALENDAR})
    assert (first, last) == (dt.date(2024, 12, 30), dt.date(2025, 1, 6))
    assert first.isocalendar()[:2] == (2025, 1)


def test_iso_week_labels_cannot_silently_use_a_non_iso_week_start():
    period = Period.model_validate({'kind': 'week', 'value': '2025-W01', 'calendar': name('cal')})
    with pytest.raises(ValueError, match='default week start'):
        bounds(period, {'cal': {**CALENDAR, 'week_start': 'sunday'}})


@pytest.mark.parametrize('calendar,expected', [
    (CALENDAR, (dt.date(2024, 12, 23), dt.date(2024, 12, 30), dt.date(2025, 1, 6))),
    ({**CALENDAR, 'week_start': 'sunday'}, (dt.date(2024, 12, 29), dt.date(2025, 1, 5))),
])
def test_week_ticks_include_partial_boundary_weeks_and_exclude_the_upper_endpoint(calendar, expected):
    assert periods((dt.date(2024, 12, 29), dt.date(2025, 1, 7)), 'week', calendar) == expected
    assert periods((dt.date(2024, 12, 30), dt.date(2025, 1, 6)), 'week', CALENDAR) == (dt.date(2024, 12, 30),)


@pytest.mark.parametrize('count,expected', [(-1, dt.date(2024, 12, 23)), (1, dt.date(2025, 1, 6))])
def test_moving_one_week_crosses_the_year_boundary_by_seven_days(count, expected):
    assert moved(dt.date(2024, 12, 30), count, 'WEEK') == expected


def test_previous_week_comparison_maps_the_entire_asked_window_by_seven_days():
    plan = model(time={'kind': 'single', 'period': {'kind': 'week', 'value': '2025-W01', 'calendar': name('cal')}},
                 dimensions=[{'kind': 'time', 'id': 'week', 'calendar': name('cal'), 'grain': 'week'}])
    assert shifted_mapping(plan, 'previous_period', {'cal': CALENDAR}) == (dt.date(2024, 12, 23), dt.date(2024, 12, 30))


def test_bigquery_translation_keeps_monday_weeks_for_both_date_trunc_forms():
    for sql in ("SELECT DATE_TRUNC('week', day) FROM u.days", "SELECT CAST(DATE_TRUNC('week', day) AS DATE) FROM u.days"):
        translated = rewrite_sql(sql, lambda unit: 'sample')
        assert 'DATE_TRUNC(day, WEEK(MONDAY))' in translated
        assert 'DATE_TRUNC(day, WEEK)' not in translated
