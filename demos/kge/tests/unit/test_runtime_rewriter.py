from decimal import Decimal
from types import SimpleNamespace

import duckdb
import pytest
import sqlglot
from sqlglot import exp

from kge.adapters.bigquery import BigQueryLoader, typed_rows
from kge.adapters.warehouse import QueryRefused, extract_figure
from kge.adapters.warehouse.bigquery import rewrite_sql
from kge.adapters.warehouse.runtimes import RuntimeManager
from kge.pipeline.sqlguard import Allowlist
from kge.semantics.compile import compile_plan
from kge.semantics.plan import Propose
from kge.semantics.plancheck import CheckedPlan, check_plan
from kge.services.ask.engine.planner import Planner, result_of
from kge.services.ask.engine.wording import Hidden
from tests import bigquery_fake
from tests.plan_fixtures import grounded, model, vocabulary
from tests.unit.test_plan_compiler import connection
from tests.unit.test_planner_jobs import jobs, setup


def runtime():
    manager = RuntimeManager(SimpleNamespace(units=['u'], curated=[]))
    columns = {'flow': {'id': 'flow_key', 'source_key': 'system_key', 'amount': 'net_amount'},
               'excluded': {'id': 'excluded_key', 'source_key': 'excluded_system'}}
    manager.ref_table_units = dict.fromkeys(columns, 'u')
    manager.ref_columns = {(t, c): 'INTEGER' for t, cs in columns.items() for c in cs}
    manager.rt_tables = {('live', t): {'table': t + '_live'} for t in columns}
    manager.rt_columns = {('live', t): {c: {'column': p} for c, p in cs.items()} for t, cs in columns.items()}
    schema = {t: set(cs) for t, cs in columns.items()}
    allow = Allowlist(unit='u', tables=set(columns), columns=schema, schema=schema, joins=set())
    manager.reference_allowlists['u'] = allow
    return manager, allow


CORRELATED = """SELECT SUM(f.amount) FROM flow f WHERE NOT EXISTS (
    SELECT 1 FROM excluded x WHERE x.id = f.id AND x.source_key = f.source_key)"""
NESTED = """SELECT SUM(f.amount) FROM flow f WHERE NOT EXISTS (
    SELECT 1 FROM excluded x WHERE EXISTS (
        SELECT 1 FROM excluded y WHERE y.id = x.id AND y.source_key = f.source_key))"""
SHADOWED = """SELECT SUM(f.amount) FROM flow f WHERE NOT EXISTS (
    SELECT 1 FROM excluded flow WHERE flow.id = f.id AND flow.source_key = f.source_key)"""


@pytest.mark.parametrize('sql', [CORRELATED, NESTED, SHADOWED])
def test_extract_needed_inherits_correlated_outer_aliases(sql):
    manager, _ = runtime()
    units, tables, columns = manager._extract_needed([{'sql': sql, 'unit': 'u'}])
    assert units == {'u'} and tables == {'flow', 'excluded'}
    assert ('flow', 'source_key') in columns
    assert ('excluded', 'id') in columns


def test_extract_needed_correlated_alias_does_not_require_an_inner_column():
    manager, _ = runtime()
    sql = '''SELECT SUM(excluded.amount) FROM flow excluded WHERE NOT EXISTS (
        SELECT 1 FROM excluded x WHERE x.id = excluded.id AND x.source_key = excluded.amount)'''
    _, _, columns = manager._extract_needed([{'sql': sql, 'unit': 'u'}])
    assert columns == {('flow', 'amount'), ('flow', 'id'), ('excluded', 'id'), ('excluded', 'source_key')}


@pytest.mark.parametrize('sql', [CORRELATED, NESTED, SHADOWED])
def test_rename_sql_for_runtime_correlated_columns_are_renamed_once(sql):
    manager, allow = runtime()
    rewritten, _, tables = manager._rename_sql_for_runtime(sql, 'u', 'live', allow)
    assert tables == ['excluded_live', 'flow_live']
    assert 'f.system_key' in rewritten
    with duckdb.connect(':memory:') as con:
        con.execute('CREATE TABLE flow(id INT, source_key INT, amount INT)')
        con.execute('CREATE TABLE excluded(id INT, source_key INT)')
        con.execute('INSERT INTO flow VALUES (1, 10, 100), (2, 20, 250)')
        con.execute('INSERT INTO excluded VALUES (1, 10)')
        expected = con.execute(sql).fetchall()
        con.execute('CREATE TABLE flow_live AS SELECT id AS flow_key, source_key AS system_key, amount AS net_amount FROM flow')
        con.execute('CREATE TABLE excluded_live AS SELECT id AS excluded_key, source_key AS excluded_system FROM excluded')
        assert con.execute(rewritten).fetchall() == expected == [(250,)]


@pytest.mark.parametrize('cast_type', ['TEXT', 'VARCHAR'])
def test_rewrite_sql_restores_composite_json_string_roundtrip(cast_type):
    original = 'SELECT COUNT(DISTINCT TO_JSON_STRING(STRUCT(t.source_key, t.account_key))) AS accounts FROM flow t'
    authored = sqlglot.transpile(original, read='bigquery', write='duckdb')[0].replace('AS TEXT', 'AS ' + cast_type)
    rewritten = rewrite_sql(authored, lambda unit: unit, 'u')
    assert 'TO_JSON_STRING(STRUCT(' in rewritten
    assert 'CAST(TO_JSON(' not in rewritten
    tree = sqlglot.parse_one(rewritten, read='bigquery')
    assert next(tree.find_all(exp.Table)).name == 'u_flow'


@pytest.mark.parametrize('alias', ['', ' f'])
def test_rewrite_sql_drops_unmapped_database_on_existing_path(alias):
    rewritten = rewrite_sql('SELECT SUM(' + ('f.' if alias else '') + 'amount) FROM other.flow' + alias,
                            lambda unit: '', 'u')
    table = next(sqlglot.parse_one(rewritten, read='bigquery').find_all(exp.Table))
    assert tuple(part.name for part in table.parts) == ('flow',)
    assert table.alias == ('f' if alias else 'flow')


@pytest.mark.parametrize('dialect', ['duckdb', 'bigquery'])
def test_reference_allowlist_keeps_bare_names_and_refuses_other_datasets(dialect):
    manager, allow = runtime()
    assert manager.check_reference_sql('SELECT amount FROM flow', 'u', {'u'}, dialect=dialect) is allow
    with pytest.raises(QueryRefused, match="outside this unit's warehouse"):
        manager.check_reference_sql('SELECT amount FROM other.flow', 'u', {'u', 'other'}, dialect=dialect)


def test_rewrite_sql_casts_the_average_window_instead_of_its_function():
    sql = '''SELECT AVG(net_amount) OVER (ORDER BY reporting_month
        ROWS BETWEEN 2 PRECEDING AND CURRENT ROW) AS rolling_average FROM flow'''
    rewritten = rewrite_sql(sql, lambda unit: unit, 'u', duckdb_types=True)
    tree = sqlglot.parse_one(rewritten, read='bigquery')
    window = next(tree.find_all(exp.Window))
    assert isinstance(window.this, exp.Avg)
    assert isinstance(window.parent, exp.Cast)
    assert 'AS FLOAT64)' in rewritten
    ordinary = rewrite_sql('SELECT AVG(net_amount) FROM flow', lambda unit: unit, 'u', duckdb_types=True)
    assert 'CAST(AVG(net_amount) AS FLOAT64)' in ordinary
    assert not list(sqlglot.parse_one(rewrite_sql(sql, lambda unit: unit, 'u'), read='bigquery').find_all(exp.Cast))


def test_rewrite_sql_sanitized_reason_reaches_planner_result(monkeypatch, tmp_path):
    fake, warehouses = setup(monkeypatch, tmp_path, False)
    try:
        fake.con.execute('DELETE FROM u_events')
        v = vocabulary()
        plan = model(measures=[dict(model().measures[0].model_dump(), id='m0')])
        checked = check_plan(grounded(plan, 'Metric', v, {'Metric': 'm0'}), v, 'Metric')
        assert isinstance(checked, CheckedPlan), checked
        compiled = compile_plan(checked)
        assert 'm0/reason' in compiled.answer.sql
        with connection(rows=[]) as con:
            reference = result_of(compiled, con.execute(compiled.answer.sql).fetchall(), {})
        assert reference.rows[0].cells[0].null_reasons == ('missing_observation',)
        fake.requests.clear()
        answer = Planner(v, warehouses, Hidden([], set(), [])).answer(
            'Metric', Propose(kind='propose', base_turn=None, plan=checked.plan))
        assert answer['kind'] == 'result', answer
        cell = answer['result']['rows'][0]['cells'][0]
        assert cell['value']['kind'] == 'null'
        assert cell['null_reasons'] == ['missing_observation']
        assert jobs(fake) == (1, 1)
        assert any('m0__reason' in sql for sql in fake.statements)
        assert not any('`m0/reason`' in sql for sql in fake.statements)
    finally:
        fake.con.close()


@pytest.mark.parametrize('as_dict', [False, True])
def test_extract_figure_preserves_exact_decimal_from_typed_rows(as_dict):
    value = Decimal('139439185492.92')
    rows = typed_rows({'schema': {'fields': [{'name': 'total', 'type': 'NUMERIC'}]},
                       'rows': [{'f': [{'v': str(value)}]}]})
    figure = extract_figure([{'total': rows[0][0]}] if as_dict else rows)
    assert isinstance(figure, Decimal)
    assert figure == value and rows == [(value,)]
    assert extract_figure([(True, 'label')]) is None
    assert extract_figure([(12,)]) == 12.0


def test_reference_sql_external_bigquery_keeps_decimal_and_job_budget(monkeypatch):
    fake = bigquery_fake.install(monkeypatch)
    manager, _ = runtime()
    manager.runtimes['live'] = {'id': 'live', 'dialect': 'bigquery', 'kind': 'external', 'units': ['u']}
    manager._bq_loaders['live'] = (BigQueryLoader(project=bigquery_fake.PROJECT), bigquery_fake.PROJECT, 'runtime_data')
    try:
        fake.run(None, 'CREATE TABLE `pilot-project.runtime_data.flow_live` (net_amount NUMERIC, flow_key INT64, system_key INT64)')
        fake.run(None, 'INSERT INTO `pilot-project.runtime_data.flow_live` VALUES (139439185492.92, 1, 10)')
        fake.requests.clear()
        result = manager.resolve_reference_sql('SELECT SUM(amount) AS total FROM flow', unit='u', runtime_id='live', readable_units={'u'})
        assert jobs(fake) == (1, 1)
        assert result['rows'] == [(Decimal('139439185492.92'),)]
        assert isinstance(result['figure'], Decimal)
        assert result['figure'] == Decimal('139439185492.92')
    finally:
        fake.con.close()
