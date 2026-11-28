"""External runtime median queries cross both the physical gate and the dialect translator."""
from types import SimpleNamespace

import duckdb
import pytest
import sqlglot

from kge.adapters.warehouse.runtimes import RuntimeManager
from kge.pipeline.sqlguard import Allowlist, check
from tests.unit.test_measure_capability_figures import compiled, declarations, measured


def runtime():
    manager = RuntimeManager(SimpleNamespace(units=['u'], curated=[]))
    manager.runtimes['live'] = {'id': 'live', 'dialect': 'bigquery', 'location': 'sample_project.metrics'}
    manager.rt_tables = {('live', 'events'): {'table': 'OBSERVATIONS'}}
    fields = {'value': 'sample_value', 'occurred': 'sample_date', 'category': 'sample_category',
              'entity_id': 'sample_entity', 'source': 'sample_source', 'private': 'restricted_value'}
    manager.rt_columns = {('live', 'events'): {name: {'column': column, 'type': 'DOUBLE'} for name, column in fields.items()}}
    return manager


def statement(checked):
    if checked:
        _, vocabulary = declarations()
        result = compiled(measured('median'), 'median Metric', vocabulary)
        return result.answer.__dict__ | {'unit': 'u'}
    fields = {'value'}
    allow = Allowlist('u', {'events'}, {'events': fields}, schema={'events': fields})
    return {'sql': 'SELECT MEDIAN(value) AS value FROM events', 'unit': 'u', 'allow': allow}


@pytest.mark.parametrize('checked', [False, True])
@pytest.mark.parametrize('values,expected', [
    ([2, 4, 10], 4), ([2, 4, 8, 10], 6), ([2, 3], 2.5),
    ([None, 2, None, 4], 3), ([None, None], None), ([], None),
])
def test_execute_statement_admits_the_exact_midpoint_translation(monkeypatch, checked, values, expected):
    manager, calls = runtime(), []
    with duckdb.connect(':memory:') as connection:
        connection.execute('CREATE TABLE OBSERVATIONS(sample_value DOUBLE)')
        if values:
            connection.executemany('INSERT INTO OBSERVATIONS VALUES (?)', [(value,) for value in values])

        def run(sql, **options):
            calls.append((sql, options))
            translated = sqlglot.parse_one(sql, read='bigquery').sql('duckdb')
            if options.get('dry_run'):
                connection.execute('EXPLAIN ' + translated)
                return {'totalBytesProcessed': '24'}
            result = connection.execute(translated)
            fields = [{'name': name, 'type': 'STRING' if str(kind) == 'VARCHAR' else 'FLOAT'}
                      for name, kind, *_ in result.description]
            return {'schema': {'fields': fields},
                    'rows': [{'f': [{'v': str(value) if value is not None else None} for value in row]}
                             for row in result.fetchall()]}

        monkeypatch.setattr(manager, '_bigquery_loader', lambda _: (SimpleNamespace(run=run), 'sample_project', 'metrics'))
        guard, rows, meta = manager.execute_statement(statement(checked), runtime_id='live')
    assert guard['passed'], guard
    assert rows == ([(expected, 'missing_observation' if expected is None else None)] if checked else [(expected,)])
    assert len(calls) == 2 and calls[0][1]['dry_run']
    assert 'PERCENTILE_CONT(' in meta['sql'] and '0.5' in meta['sql']
    assert 'ARRAY_AGG' not in meta['sql'] and 'APPROX' not in meta['sql']


def test_checked_runtime_rejects_changed_aggregation_and_percentile(monkeypatch):
    manager, source = runtime(), statement(True)
    monkeypatch.setattr(manager, '_bigquery_loader', lambda _: pytest.fail('tampered query reached the warehouse'))
    tampered = source | {'sql': source['sql'].replace('MEDIAN(', 'MAX(')}
    guard, rows, _ = manager.execute_statement(tampered, runtime_id='live')
    assert not guard['passed'] and rows is None
    physical, allow, _ = manager._rename_sql_for_runtime(source['sql'], 'u', 'live', source['allow'])
    native = manager._render_for_dialect(physical, 'bigquery')
    assert check(native, allow, 'bigquery') == []
    changed = native.replace(', 0.5)', ', 0.9)')
    assert changed != native
    assert check(changed, allow, 'bigquery')
