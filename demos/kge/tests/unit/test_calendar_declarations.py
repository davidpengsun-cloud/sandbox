"""Calendar spines, attributes and deterministic anchors are checked pack declarations."""
import copy
from pathlib import Path

import pytest

from kge.compiler.checks import SECTIONS, calendars, shape
from kge.compiler.emit import NODES
from kge.compiler.pack import Pack
from kge.services.ask.engine.graphview import calendar_declaration


def declared():
    spine = {'table': 'u.days', 'column': 'day', 'attributes': {
        'week': 'u.days.week', 'week_of_month': 'u.days.week_of_month', 'month': 'u.days.month',
        'quarter': 'u.days.quarter', 'year': 'u.days.year', 'month_of_year': 'u.months.month_number',
        'year_quarter': 'u.months.year_quarter'}}
    data = {'calendars': [{'id': 'calendar', 'year_start_month': 1, 'quarter_label': '{yyyy}-Q{n}',
                          'spine': spine, 'current_period': '2025-07-15'},
                         {'id': 'fiscal', 'year_start_month': 4, 'quarter_label': 'FY{yy}-Q{n}',
                          'spine': copy.deepcopy(spine), 'current_period': '2025-07-15'}], 'measures': []}
    day_columns = [{'name': 'day', 'type': 'DATE'}, *({'name': name, 'type': 'INTEGER'}
                   for name in ('week', 'week_of_month', 'month', 'quarter', 'year'))]
    lock = {'sources': {'u': {'tables': [{'name': 'days', 'primary_key': ['day'], 'columns': day_columns},
                                       {'name': 'months', 'primary_key': ['month_number'], 'columns': [
                                           {'name': 'month_number', 'type': 'INTEGER'}, {'name': 'year_quarter', 'type': 'VARCHAR'}]}]}}}
    return Pack('example', Path('.'), data=data, lock=lock)


def test_a_day_spine_can_supply_calendar_and_fiscal_periods_without_physical_fiscal_columns():
    pack = declared()
    assert calendars(pack) == []
    problems = []
    shape(pack.data['calendars'], SECTIONS['calendars'], 'calendars', problems)
    assert problems == []
    emitted = NODES['Calendar'](pack.data, pack.lock)
    for node, original in zip(emitted, pack.data['calendars']):
        row = {'start': node['year_start_month'], 'label': node['quarter_label'],
               'spine': node['spine'], 'current_period': node['current_period']}
        assert calendar_declaration(row) == {k: v for k, v in original.items() if k != 'id'}


@pytest.mark.parametrize('change, field, reason', [
    (lambda c, l: c['spine'].update(table='u.absent'), '.table', 'table in pack.lock'),
    (lambda c, l: c['spine'].update(column='absent'), '.column', 'DATE column'),
    (lambda c, l: l['sources']['u']['tables'][0]['columns'][0].update(type='VARCHAR'), '.column', 'DATE column'),
    (lambda c, l: l['sources']['u']['tables'][0].update(primary_key=['day', 'week']), '.column', 'single-column primary key'),
    (lambda c, l: c['spine']['attributes'].update(week='u.days.absent'), '.attributes.week', 'column in pack.lock'),
    (lambda c, l: c['spine']['attributes'].update(week='u.days.day'), '.attributes.week', 'text or numeric'),
])
def test_invalid_spine_references_fail_closed(change, field, reason):
    pack = declared()
    change(pack.data['calendars'][0], pack.lock)
    findings = calendars(pack)
    assert any(f.rule == 'S09' and f.path.endswith('spine' + field) and reason in f.message for f in findings), findings


def test_attributes_cannot_cross_warehouse_sources():
    pack = declared()
    pack.lock['sources']['other'] = copy.deepcopy(pack.lock['sources']['u'])
    pack.data['calendars'][0]['spine']['attributes']['week'] = 'other.days.week'
    assert any('own source' in f.message for f in calendars(pack))


@pytest.mark.parametrize('value', ['today', '2025-02-30', '2025-7-1', '2025-07'])
def test_current_period_is_an_explicit_valid_calendar_date(value):
    held = declared().data['calendars']
    held[0]['current_period'] = value
    findings = []
    shape(held, SECTIONS['calendars'], 'calendars', findings)
    assert any(at.endswith('.current_period') and 'date' in reason for at, reason in findings)


def test_optional_calendar_fields_are_absent_for_a_pack_without_a_spine_or_anchor():
    assert calendar_declaration({'start': 1, 'label': '{yyyy}-Q{n}', 'spine': None, 'current_period': None}) == {
        'year_start_month': 1, 'quarter_label': '{yyyy}-Q{n}'}


def test_new_calendar_attribute_names_need_only_pack_data():
    pack = declared()
    held = pack.data['calendars']
    held[0]['spine']['attributes']['additional_year'] = 'u.days.year'
    findings = []
    shape(held, SECTIONS['calendars'], 'calendars', findings)
    assert findings == []
    assert calendars(pack) == []
