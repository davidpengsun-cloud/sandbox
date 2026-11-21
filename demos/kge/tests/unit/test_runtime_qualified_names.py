import shutil
from types import SimpleNamespace

import duckdb
import pytest
import sqlglot
from sqlglot import exp

from kge.adapters.warehouse.runtimes import RuntimeManager
from kge.compiler.checks import _normalize_runtime_entry
from kge.estate import author_runtime, check_pack
from kge.pipeline.sqlguard import Allowlist, check, gate
from tests.unit.test_compiler import CLIENT, CLIENTS, compile_args, printed, rows, run, scratch
from tests.unit.test_runtimes_model import _set_1_5


def test_one_runtime_all_name_forms_check_pack_and_compile(tmp_path):
    names = {'retail.account': 'ACCOUNT', 'retail.monthly_revenue': 'finance.REVENUE',
             'corporate.counterparty': 'example-project.crm.COUNTERPARTY'}

    def edit(p, lock):
        _set_1_5(p)
        p['runtimes'] = [{'id': 'live', 'label': 'Live',
                          'connection': {'dialect': 'bigquery', 'location': 'example-project.default_data'},
                          'tables': {ref: {'name': name} for ref, name in names.items()}}]

    clients = scratch(tmp_path, edit)
    shutil.copytree(CLIENTS / CLIENT / 'fixture', clients / CLIENT / 'fixture')
    _, findings = check_pack.check_folder(clients / CLIENT)
    assert findings == []
    artifact = printed(run(*compile_args(tmp_path / 'out', clients)))
    tables = dict(rows(artifact, 'data/nodes/RtTable.csv')[1:])
    assert {ref: tables['live.' + ref] for ref in names} == names


@pytest.mark.parametrize('name', ['', 'a.b.c.d', '.TABLE', 'dataset.', 'a..TABLE', 'data-set.TABLE',
                                 'project.data-set.TABLE', 'dataset.bad-name', '1table', 'a b', 't;DROP', 42])
def test_s31_refuses_invalid_runtime_path_with_rule(name):
    lock = {'sources': {'u': {'tables': [{'name': 't', 'columns': []}]}}}
    runtime = {'id': 'live', 'label': 'Live', 'connection': {'dialect': 'bigquery', 'location': 'default'},
               'tables': {'u.t': {'name': name}}}
    _, findings = _normalize_runtime_entry(runtime, lock, SimpleNamespace(at=lambda path: path))
    assert any(f.rule == 'S31' and 'TABLE, dataset.TABLE or project.dataset.TABLE' in f.message for f in findings)


@pytest.mark.parametrize('name', ['other.ledger', 'example-project.other.ledger'])
def test_author_runtime_preserves_qualified_catalog_name(name):
    catalog = {'id': 'live', 'dialect': 'bigquery', 'location': 'placeholder',
               'tables': [{'name': name, 'columns': [{'name': 'id', 'type': 'INTEGER'}]}]}
    draft = author_runtime.draft_from_catalog(catalog, {'u.ledger': {'id': 'INTEGER'}}, confirmed=True)
    assert draft.absent == []
    assert draft.entry['tables'] == {'u.ledger': name}
    assert draft.proposals[0].reason == 'exact table name'


def runtime(names, dialect='duckdb'):
    manager = RuntimeManager(SimpleNamespace(units=['u'], curated=[]))
    manager.runtimes['live'] = {'id': 'live', 'dialect': dialect, 'location': 'example-project.default_data'}
    manager.rt_tables = {('live', t): {'table': name} for t, name in names.items()}
    columns = {'flow': {'id': 'flow_key', 'amount': 'net_amount'}, 'excluded': {'id': 'excluded_key'}}
    manager.rt_columns = {('live', t): {c: {'column': p, 'type': 'INTEGER'} for c, p in cs.items()}
                          for t, cs in columns.items()}
    schema = {t: set(cs) for t, cs in columns.items()}
    allow = Allowlist('u', set(schema), schema, schema=schema,
                      joins={frozenset({('flow', 'id'), ('excluded', 'id')})})
    return manager, allow


QUERIES = [
    'SELECT SUM(f.amount) FROM flow f JOIN excluded x ON x.id = f.id',
    'SELECT SUM(flow.amount) FROM flow JOIN excluded ON excluded.id = flow.id',
    'SELECT SUM(f.amount) FROM flow f WHERE NOT EXISTS (SELECT 1 FROM excluded x WHERE x.id = f.id)',
    'SELECT SUM(flow.amount) FROM flow WHERE NOT EXISTS (SELECT 1 FROM excluded WHERE excluded.id = flow.id)',
    'SELECT SUM(flow.amount) FROM flow flow WHERE NOT EXISTS (SELECT 1 FROM excluded excluded WHERE excluded.id = flow.id)',
]


@pytest.mark.parametrize('sql', QUERIES)
def test_duckdb_runtime_attached_datasets_query_and_startup(sql, monkeypatch):
    manager, allow = runtime({'flow': 'finance.FLOW', 'excluded': 'crm.EXCLUDED'})
    with duckdb.connect(':memory:') as con:
        con.execute("ATTACH ':memory:' AS finance")
        con.execute("ATTACH ':memory:' AS crm")
        con.execute('CREATE TABLE finance.FLOW(flow_key INTEGER, net_amount INTEGER)')
        con.execute('INSERT INTO finance.FLOW VALUES (1, 100), (2, 250)')
        con.execute('CREATE TABLE crm.EXCLUDED(excluded_key INTEGER)')
        con.execute('INSERT INTO crm.EXCLUDED VALUES (1)')
        monkeypatch.setattr(manager, '_open_duckdb', lambda rt: con)
        assert manager.check_runtime('live')['available']
        rewritten, physical_allow, tables = manager._rename_sql_for_runtime(sql, 'u', 'live', allow)
        assert tables == ['crm.EXCLUDED', 'finance.FLOW']
        assert gate(con, rewritten, physical_allow)['passed']
        assert con.execute(rewritten).fetchall() == [(250 if 'NOT EXISTS' in sql else 100,)]


@pytest.mark.parametrize('sql', QUERIES)
def test_bigquery_runtime_qualified_rendering_and_two_job_budget(sql, monkeypatch):
    manager, allow = runtime({'flow': 'example-project.finance.FLOW', 'excluded': 'crm.EXCLUDED'}, 'bigquery')
    calls = []

    def query(sql, **kwargs):
        calls.append((sql, kwargs))
        return {'totalBytesProcessed': '10', 'schema': {'fields': [{'name': 'total', 'type': 'INTEGER'}]},
                'rows': [{'f': [{'v': '250'}]}]}

    monkeypatch.setattr(manager, '_bigquery_loader', lambda rt: (SimpleNamespace(run=query), 'example-project', 'default_data'))
    guard, result, meta = manager.execute_statement({'sql': sql, 'unit': 'u', 'allow': allow}, runtime_id='live')
    assert guard['passed'], guard
    assert result == [(250,)]
    assert len(calls) == 2 and calls[0][1]['dry_run'] is True and 'dry_run' not in calls[1][1]
    assert '`example-project.finance.FLOW`' in meta['sql'] and '`crm.EXCLUDED`' in meta['sql']
    tree = sqlglot.parse_one(meta['sql'], read='bigquery')
    assert {tuple(p.name for p in t.parts) for t in tree.find_all(exp.Table)} == {
        ('example-project', 'finance', 'FLOW'), ('crm', 'EXCLUDED')}


def test_bigquery_startup_checks_each_dataset_once_and_keeps_full_paths(monkeypatch):
    manager, _ = runtime({'flow': 'example-project.finance.FLOW', 'excluded': 'crm.EXCLUDED'}, 'bigquery')
    calls = []

    def query(sql):
        calls.append(sql)
        columns = [('FLOW', 'flow_key'), ('FLOW', 'net_amount')] if '.finance.' in sql else [('EXCLUDED', 'excluded_key')]
        return {'rows': [{'f': [{'v': table}, {'v': col}, {'v': 'INT64'}]} for table, col in columns]}

    monkeypatch.setattr(manager, '_bigquery_loader', lambda rt: (SimpleNamespace(run=query), 'example-project', 'default_data'))
    assert manager.check_runtime('live')['available']
    assert len(calls) == 2
    assert any('`example-project.finance.INFORMATION_SCHEMA.COLUMNS`' in sql for sql in calls)
    assert any('`example-project.crm.INFORMATION_SCHEMA.COLUMNS`' in sql for sql in calls)
    manager.rt_tables[('live', 'flow')]['table'] = 'wrong-project.finance.FLOW'
    monkeypatch.setattr(manager, '_read_physical_schema', lambda rt: {('example-project', 'finance', 'FLOW'): {'flow_key': 'INTEGER'}})
    assert not manager.check_runtime('live')['available']


@pytest.mark.parametrize('dialect', ['duckdb', 'bigquery'])
def test_gate_matches_full_paths_and_enforces_columns_and_joins(dialect):
    schema = {'p.one.T': {'id', 'secret'}, 'p.two.T': {'id'}, 'local': {'id'}}
    allow = Allowlist('u', set(schema), schema, schema=schema, restricted={('p.one.T', 'secret')},
                      joins={frozenset({('p.one.T', 'id'), ('p.two.T', 'id')})})
    quote = '`' if dialect == 'bigquery' else '"'
    first = '.'.join(quote + p + quote for p in ['p', 'one', 'T'])
    second = '.'.join(quote + p + quote for p in ['p', 'two', 'T'])
    assert check(f'SELECT a.id FROM {first} a JOIN {second} b ON a.id = b.id', allow, dialect) == []
    assert check('SELECT id FROM local', allow, dialect) == []
    assert check('SELECT id FROM p.other.T', allow, dialect)
    assert check('SELECT id FROM T', allow, dialect)
    assert any('restricted personal data' in p for p in check(f'SELECT secret FROM {first}', allow, dialect))
    assert any('not joined' in p for p in check(f'SELECT a.id FROM {first} a CROSS JOIN {second} b', allow, dialect))


@pytest.mark.parametrize('dialect, source', [
    ('duckdb', '"p.one".T'),
    ('duckdb', 'p."one.T"'),
    ('duckdb', '"P.ONE".t'),
    ('bigquery', '`p.one`.T'),
    ('bigquery', '`p`.`one.T`'),
    ('bigquery', '`P.ONE`.t'),
    ('bigquery', 'P.one.T'),
])
def test_gate_refuses_merged_identifier_parts_before_dry_run(dialect, source):
    schema = {'p.one.T': {'id'}}
    allow = Allowlist('u', set(schema), schema, schema=schema)
    sql = f'SELECT COUNT(*) FROM {source}'
    problems = check(sql, allow, dialect)
    assert len(problems) == 1 and problems[0].endswith("is outside this unit's warehouse")
    assert gate(None, sql, allow, dialect=dialect) == {
        'passed': False, 'problems': problems, 'failed_at': 'static',
        'checks': ['read-only', 'tables', 'columns', 'joins', 'dry run'],
    }


@pytest.mark.parametrize('dialect', ['duckdb', 'bigquery'])
@pytest.mark.parametrize('name', ['T', 'one.T', 'p.one.T'])
def test_gate_keeps_legitimate_path_forms_and_existing_case_rule(dialect, name):
    schema = {name: {'id'}}
    allow = Allowlist('u', set(schema), schema, schema=schema)
    sources = [name, name.lower()]
    if dialect == 'duckdb':
        sources += ['.'.join(f'"{part}"' for part in name.split('.'))]
    else:
        sources += [f'`{name}`', '.'.join(f'`{part}`' for part in name.split('.'))]
    calls = []
    con = SimpleNamespace(execute=lambda sql: calls.append(sql) or SimpleNamespace(fetchall=lambda: []))
    for source in sources:
        sql = f'SELECT COUNT(*) FROM {source}'
        assert check(sql, allow, dialect) == []
        assert gate(con, sql, allow, dialect=dialect)['passed']
    assert len(calls) == len(sources)


def test_duckdb_gate_refuses_a_separate_schema_with_a_dotted_name():
    schema = {'p.one.T': {'id'}}
    allow = Allowlist('u', set(schema), schema, schema=schema)
    with duckdb.connect(':memory:') as con:
        con.execute('CREATE SCHEMA "p.one"')
        con.execute('CREATE TABLE "p.one".T(id INTEGER)')
        con.execute('INSERT INTO "p.one".T VALUES (1)')
        assert con.execute('SELECT COUNT(*) FROM "p.one".T').fetchall() == [(1,)]
        refused = gate(con, 'SELECT COUNT(*) FROM "p.one".T', allow)
        assert refused['failed_at'] == 'static'
        assert refused['problems'][0].endswith("is outside this unit's warehouse")


@pytest.mark.parametrize('source', ['"p.one".FLOW', 'p."one.FLOW"'])
def test_duckdb_startup_refuses_merged_identifier_parts(source, monkeypatch):
    manager, _ = runtime({'flow': 'p.one.FLOW'})
    with duckdb.connect(':memory:') as con:
        if source.startswith('"'):
            con.execute('CREATE SCHEMA "p.one"')
        else:
            con.execute("ATTACH ':memory:' AS p")
        con.execute(f'CREATE TABLE {source}(flow_key INTEGER, net_amount INTEGER)')
        monkeypatch.setattr(manager, '_open_duckdb', lambda rt: con)
        result = manager.check_runtime('live')
        assert not result['available']
        assert result['problems'] == ['missing table p.one.FLOW (implements flow)']


def test_bigquery_startup_refuses_a_table_with_a_dotted_name_in_one_job(monkeypatch):
    manager, _ = runtime({'flow': 'one.FLOW'}, 'bigquery')
    calls = []

    def query(sql):
        calls.append(sql)
        return {'rows': [{'f': [{'v': 'one.FLOW'}, {'v': column}, {'v': 'INT64'}]}
                         for column in ['flow_key', 'net_amount']]}

    monkeypatch.setattr(manager, '_bigquery_loader', lambda rt: (SimpleNamespace(run=query), 'p', 'one'))
    result = manager.check_runtime('live')
    assert not result['available']
    assert result['problems'] == ['missing table one.FLOW (implements flow)']
    assert calls == ['SELECT table_name, column_name, data_type FROM `p.one.INFORMATION_SCHEMA.COLUMNS`']


@pytest.mark.parametrize('name', ['Finance.FLOW', 'Finance.Reports.FLOW'])
def test_duckdb_runtime_mixed_case_dataset_parts_pass_startup_and_gate(name, monkeypatch):
    manager, allow = runtime({'flow': name})
    with duckdb.connect(':memory:') as con:
        con.execute("ATTACH ':memory:' AS Finance")
        con.execute('CREATE SCHEMA Finance.Reports')
        con.execute(f'CREATE TABLE {name}(flow_key INTEGER, net_amount INTEGER)')
        con.execute(f'INSERT INTO {name} VALUES (1, 250)')
        monkeypatch.setattr(manager, '_open_duckdb', lambda rt: con)
        assert manager.check_runtime('live')['available']
        sql, physical_allow, _ = manager._rename_sql_for_runtime('SELECT SUM(amount) FROM flow', 'u', 'live', allow)
        assert gate(con, sql, physical_allow)['passed']
        assert con.execute(sql).fetchall() == [(250,)]


@pytest.mark.parametrize('source, accepted', [
    ('finance.FLOW', False), ('`finance.FLOW`', False), ('`finance`.`FLOW`', False),
    ('Finance.FLOW', True), ('`Finance.FLOW`', True), ('`Finance`.`FLOW`', True),
])
def test_bigquery_gate_keeps_mixed_case_dataset_spelling(source, accepted):
    schema = {'Finance.FLOW': {'id'}}
    allow = Allowlist('u', set(schema), schema, schema=schema)
    sql = f'SELECT COUNT(*) FROM {source}'
    calls = []
    con = SimpleNamespace(execute=lambda sql: calls.append(sql) or SimpleNamespace(fetchall=lambda: []))
    problems = check(sql, allow, 'bigquery')
    result = gate(con, sql, allow, dialect='bigquery')
    assert result['passed'] is accepted
    assert len(calls) == int(accepted)
    if accepted:
        assert problems == []
    else:
        assert len(problems) == 1 and problems[0].endswith("is outside this unit's warehouse")
        assert result['failed_at'] == 'static'


def test_duckdb_runtime_bare_name_matches_only_the_current_database_and_schema(monkeypatch):
    manager, _ = runtime({'flow': 'FLOW'})
    with duckdb.connect(':memory:') as con:
        con.execute("ATTACH ':memory:' AS finance")
        con.execute('CREATE TABLE finance.FLOW(flow_key INTEGER, net_amount INTEGER)')
        monkeypatch.setattr(manager, '_open_duckdb', lambda rt: con)
        assert manager.check_runtime('live')['problems'] == ['missing table FLOW (implements flow)']
        con.execute('CREATE TABLE FLOW(flow_key INTEGER, net_amount INTEGER)')
        assert manager.check_runtime('live')['available']


def test_bigquery_refusal_renders_the_qualified_name_in_its_dialect():
    allow = Allowlist('u', {'local'}, {'local': {'id'}}, schema={'local': {'id'}})
    assert check('SELECT id FROM `p.one.T`', allow, 'bigquery') == [
        "table `p.one.T` is outside this unit's warehouse"]


@pytest.mark.parametrize('column', ['id', 'unknown.FLOW.id'])
def test_gate_refuses_unknown_database_before_execution(column):
    allow = Allowlist('u', {'FLOW'}, {'FLOW': {'id'}}, schema={'FLOW': {'id'}})
    sql = f'SELECT {column} FROM unknown.FLOW'
    calls = []
    con = SimpleNamespace(execute=lambda sql: calls.append(sql))
    assert gate(con, sql, allow)['failed_at'] == 'static'
    assert calls == []
    assert gate(con, sql, allow, dialect='bigquery')['failed_at'] == 'static'
    assert calls == []
