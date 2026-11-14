from types import SimpleNamespace

import pytest
import sqlglot

from kge.adapters.warehouse.runtimes import RuntimeManager
from kge.pipeline.sqlguard import Allowlist, CheckedAllowlist, canonical, check


def runtime():
    manager = RuntimeManager(SimpleNamespace(units=['u'], curated=[]))
    columns = {
        'tran_fact': {'dw_tran_dt': 'DW_TRAN_DT', 'amt': 'AMT', 'src': 'SRCE_SYS_CD',
                      'client_ssn': 'CLIENT_SSN', 'uncertified': 'UNCERTIFIED'},
        'month_dim': {'cldr_eom_dt': 'CLDR_EOM_DT'},
        'excl': {'src': 'SRCE_SYS_CD', 'cur': 'IS_CURRENT', 'eff': 'EXCL_EFF_DT', 'exp': 'EXCL_EXP_DT'},
    }
    tables = {'tran_fact': 'APP_TRAN_FACT', 'month_dim': 'APP_MONTH_DIM', 'excl': 'APP_NNA_EXCL'}
    manager.ref_table_units = dict.fromkeys(columns, 'u')
    manager.ref_columns = {(t, c): 'INTEGER' for t, cs in columns.items() for c in cs}
    manager.rt_tables = {('live', t): {'table': 'nna_app_runtime.' + p} for t, p in tables.items()}
    manager.rt_columns = {('live', t): {c: {'column': p} for c, p in cs.items()} for t, cs in columns.items()}
    schema = {t: set(cs) for t, cs in columns.items()}
    predicate = canonical(sqlglot.parse_one('LAST_DAY(tran_fact.dw_tran_dt) = month_dim.cldr_eom_dt'), {})
    pair = frozenset({('tran_fact', 'src'), ('excl', 'src')})
    allow = Allowlist(
        unit='u', tables=set(columns), schema=schema,
        columns={t: cs - {'client_ssn', 'uncertified'} for t, cs in schema.items()},
        predicates={predicate}, links={frozenset({predicate})}, declared={predicate}, joins={pair},
        required={'j_excl': {'pairs': {pair}, 'conjuncts': {}, 'through': 'excl',
                             'window': {'is_current': 'cur', 'valid_from': 'eff', 'valid_to': 'exp',
                                        'valid_to_inclusive': False}}},
        restricted={('tran_fact', 'client_ssn')}, cites={('tran_fact', 'client_ssn'): 'restricted policy'},
    )
    return manager, allow


def test_declared_non_equi_join_passes_on_runtime():
    manager, allow = runtime()
    sql = 'SELECT SUM(t.amt) FROM tran_fact t JOIN month_dim m ON LAST_DAY(t.dw_tran_dt) = m.cldr_eom_dt'
    assert check(sql, allow) == []
    rewritten, physical, _ = manager._rename_sql_for_runtime(sql, 'u', 'live', allow)
    assert check(rewritten, physical) == []


def test_required_join_without_window_is_refused_on_runtime():
    manager, allow = runtime()
    sql = 'SELECT t.amt FROM tran_fact t JOIN excl e ON t.src = e.src'
    rewritten, physical, _ = manager._rename_sql_for_runtime(sql, 'u', 'live', allow)
    assert any('without its window' in p for p in check(rewritten, physical))


@pytest.mark.parametrize('column, reason', [('client_ssn', 'restricted personal data'),
                                         ('uncertified', 'not certified for this question')])
def test_runtime_preserves_column_policy_and_window_flags(column, reason):
    manager, allow = runtime()
    rewritten, physical, _ = manager._rename_sql_for_runtime('SELECT t.' + column + ' FROM tran_fact t', 'u', 'live', allow)
    assert physical.restricted == {('nna_app_runtime.APP_TRAN_FACT', 'CLIENT_SSN')}
    assert physical.cites == {('nna_app_runtime.APP_TRAN_FACT', 'CLIENT_SSN'): 'restricted policy'}
    assert physical.required['j_excl']['window']['valid_to_inclusive'] is False
    assert any(reason in p for p in check(rewritten, physical))


@pytest.mark.parametrize('condition', ['a.id = b.id AND a.x BETWEEN b.lo AND b.hi',
                                       'a.id = b.id OR a.x BETWEEN b.lo AND b.hi',
                                       'a.id = b.id WHERE a.x BETWEEN b.lo AND b.hi'])
def test_between_requires_a_declared_scd2_window(condition):
    schema = {'a': {'id', 'x'}, 'b': {'id', 'lo', 'hi'}}
    allow = Allowlist('u', set(schema), schema, schema=schema, joins={frozenset({('a', 'id'), ('b', 'id')})})
    assert any('not a declared join' in p for p in check('SELECT a.id FROM a JOIN b ON ' + condition, allow))


@pytest.mark.parametrize('condition', [
    't.src = v.src OR TRUE',
    't.src = v.src OR 1 = 1',
    'NOT (t.src = v.src)',
    't.src = v.src OR t.amt > 0',
    '((t.src = v.src) OR (TRUE))',
    '((t.src = v.src) OR (1 = 1))',
    '(NOT ((t.src = v.src)))',
    '((t.src = v.src) OR (t.amt > 0))',
])
@pytest.mark.parametrize('clause', ['ON', 'WHERE'])
@pytest.mark.parametrize('dialect', ['duckdb', 'bigquery'])
def test_declared_key_under_or_or_not_is_refused_on_reference_and_runtime(condition, clause, dialect):
    manager, allow = runtime()
    allow.required.clear()
    sql = 'SELECT t.amt FROM tran_fact t JOIN excl v ' + clause + ' ' + condition
    reason = 'must be an AND-conjunct of the ON or WHERE, not under OR or NOT'
    assert any(reason in p for p in check(sql, allow, dialect=dialect))
    rewritten, physical, _ = manager._rename_sql_for_runtime(sql, 'u', 'live', allow)
    rewritten = sqlglot.transpile(rewritten, read='duckdb', write=dialect)[0]
    assert any(reason in p for p in check(rewritten, physical, dialect=dialect))


@pytest.mark.parametrize('condition', ['t.src = v.src', 't.src = v.src AND t.dw_tran_dt BETWEEN v.eff AND v.exp'])
@pytest.mark.parametrize('dialect', ['duckdb', 'bigquery'])
def test_outer_join_on_declared_key_passes_on_schema_reference_and_runtime(condition, dialect):
    manager, declaring = runtime()
    allow = Allowlist(unit='u', tables=set(declaring.schema), columns=declaring.schema, schema=declaring.schema,
                      joins={frozenset({('tran_fact', 'src'), ('excl', 'src')})}, windows={('excl', 'eff', 'exp')})
    sql = 'SELECT t.amt FROM tran_fact t LEFT JOIN excl v ON ' + condition
    assert check(sql, allow, dialect=dialect) == []
    rewritten, physical, _ = manager._rename_sql_for_runtime(sql, 'u', 'live', allow)
    rewritten = sqlglot.transpile(rewritten, read='duckdb', write=dialect)[0]
    assert check(rewritten, physical, dialect=dialect) == []
    refused = check('SELECT t.amt FROM tran_fact t LEFT JOIN excl v ON t.src = v.src OR TRUE', allow, dialect=dialect)
    assert any('must be an AND-conjunct' in p for p in refused)


@pytest.mark.parametrize('condition', ['t.src = v.src', 't.src = v.src AND t.dw_tran_dt = v.eff',
                                       '((t.src = v.src) AND (t.dw_tran_dt = v.eff))'])
@pytest.mark.parametrize('dialect', ['duckdb', 'bigquery'])
def test_top_level_declared_keys_pass_on_reference_and_runtime(condition, dialect):
    manager, allow = runtime()
    allow.required.clear()
    allow.joins.add(frozenset({('tran_fact', 'dw_tran_dt'), ('excl', 'eff')}))
    sql = 'SELECT t.amt FROM tran_fact t JOIN excl v ON ' + condition
    assert check(sql, allow, dialect=dialect) == []
    rewritten, physical, _ = manager._rename_sql_for_runtime(sql, 'u', 'live', allow)
    rewritten = sqlglot.transpile(rewritten, read='duckdb', write=dialect)[0]
    assert check(rewritten, physical, dialect=dialect) == []


@pytest.mark.parametrize('tables', ['tran_fact t JOIN excl v', 'excl v JOIN tran_fact t'])
@pytest.mark.parametrize('condition', [
    't.src = v.src AND t.dw_tran_dt BETWEEN v.eff AND v.exp',
    '(t.dw_tran_dt BETWEEN v.eff AND v.exp) AND (v.src = t.src)',
    '((t.src = v.src AND (t.dw_tran_dt BETWEEN v.eff AND v.exp)))',
])
@pytest.mark.parametrize('dialect', ['duckdb', 'bigquery'])
def test_declared_scd2_between_passes_on_reference_and_runtime(tables, condition, dialect):
    manager, allow = runtime()
    allow.predicates.clear()
    allow.declared.clear()
    allow.required.clear()
    allow.windows = {('excl', 'eff', 'exp')}
    assert not allow.declares
    sql = 'SELECT t.amt FROM ' + tables + ' ON ' + condition
    assert check(sql, allow, dialect=dialect) == []
    rewritten, physical, _ = manager._rename_sql_for_runtime(sql, 'u', 'live', allow)
    assert physical.windows == {('nna_app_runtime.APP_NNA_EXCL', 'EXCL_EFF_DT', 'EXCL_EXP_DT')}
    assert not physical.declares
    rewritten = sqlglot.transpile(rewritten, read='duckdb', write=dialect)[0]
    assert check(rewritten, physical, dialect=dialect) == []


@pytest.mark.parametrize('condition', [
    't.src = v.src OR t.dw_tran_dt BETWEEN v.eff AND v.exp',
    'NOT (t.src = v.src AND t.dw_tran_dt BETWEEN v.eff AND v.exp)',
    'NOT (t.src = v.src) AND t.dw_tran_dt BETWEEN v.eff AND v.exp',
    't.src = v.src AND NOT (t.dw_tran_dt BETWEEN v.eff AND v.exp)',
    '(t.src = v.src AND t.dw_tran_dt BETWEEN v.eff AND v.exp) OR t.amt = 1',
    't.src = v.src AND (t.dw_tran_dt BETWEEN v.eff AND v.exp OR t.amt = 1)',
    '(t.src = v.src OR t.amt = 1) AND t.dw_tran_dt BETWEEN v.eff AND v.exp',
    't.dw_tran_dt BETWEEN v.eff AND v.exp',
    't.src = v.src AND t.dw_tran_dt BETWEEN v.exp AND v.eff',
    't.src = v.src AND t.dw_tran_dt BETWEEN v.eff AND v.cur',
    't.src = v.src AND t.dw_tran_dt BETWEEN v.eff AND t.amt',
    't.src = v.src WHERE t.dw_tran_dt BETWEEN v.eff AND v.cur',
    't.src = v.src AND (t.dw_tran_dt BETWEEN v.eff AND v.cur OR TRUE)',
    't.src = v.src AND NOT (t.dw_tran_dt BETWEEN v.eff AND v.cur)',
    't.src = v.src GROUP BY t.amt HAVING MAX(t.dw_tran_dt) BETWEEN MAX(v.eff) AND MAX(v.exp)',
])
@pytest.mark.parametrize('dialect', ['duckdb', 'bigquery'])
def test_declared_scd2_between_requires_top_level_window_and_key_on_reference_and_runtime(condition, dialect):
    manager, allow = runtime()
    allow.predicates.clear()
    allow.declared.clear()
    allow.required.clear()
    allow.windows = {('excl', 'eff', 'exp')}
    sql = 'SELECT t.amt FROM tran_fact t JOIN excl v ON ' + condition
    assert any('not a declared join' in p for p in check(sql, allow, dialect=dialect))
    rewritten, physical, _ = manager._rename_sql_for_runtime(sql, 'u', 'live', allow)
    rewritten = sqlglot.transpile(rewritten, read='duckdb', write=dialect)[0]
    assert any('not a declared join' in p for p in check(rewritten, physical, dialect=dialect))


@pytest.mark.parametrize('tables', [
    'tran_fact t JOIN excl v ON t.src = v.src WHERE t.dw_tran_dt BETWEEN v.eff AND v.exp',
    'tran_fact t, excl v WHERE t.src = v.src AND t.dw_tran_dt BETWEEN v.eff AND v.exp',
    'tran_fact t JOIN excl v ON t.src = v.src AND t.dw_tran_dt BETWEEN v.eff AND v.exp AND t.amt = 1',
    'tran_fact t JOIN excl v ON t.src = v.src AND t.dw_tran_dt BETWEEN v.eff AND v.exp AND (t.amt = 1 OR t.amt = 2)',
    'tran_fact t JOIN excl v ON t.src = v.src AND t.dw_tran_dt BETWEEN v.eff AND v.exp AND NOT (t.amt = 1)',
    'tran_fact t JOIN excl v ON t.dw_tran_dt BETWEEN v.eff AND v.exp WHERE t.src = v.src',
    'tran_fact t JOIN excl v ON (t.src = v.src) WHERE ((t.dw_tran_dt BETWEEN v.eff AND v.exp))',
    'tran_fact t, excl v WHERE ((t.src = v.src) AND (t.dw_tran_dt BETWEEN v.eff AND v.exp))',
    'tran_fact t JOIN excl v ON t.src = v.src JOIN month_dim m ON t.dw_tran_dt = m.cldr_eom_dt AND m.cldr_eom_dt BETWEEN v.eff AND v.exp',
])
@pytest.mark.parametrize('dialect', ['duckdb', 'bigquery'])
def test_declared_scd2_between_uses_scope_key_on_reference_and_runtime(tables, dialect):
    manager, allow = runtime()
    allow.predicates.clear()
    allow.declared.clear()
    allow.required.clear()
    allow.windows = {('excl', 'eff', 'exp')}
    allow.joins.add(frozenset({('tran_fact', 'dw_tran_dt'), ('month_dim', 'cldr_eom_dt')}))
    sql = 'SELECT t.amt FROM ' + tables
    assert check(sql, allow, dialect=dialect) == []
    rewritten, physical, _ = manager._rename_sql_for_runtime(sql, 'u', 'live', allow)
    rewritten = sqlglot.transpile(rewritten, read='duckdb', write=dialect)[0]
    assert check(rewritten, physical, dialect=dialect) == []


@pytest.mark.parametrize('tables', [
    'tran_fact t JOIN excl v ON t.src = v.src, month_dim m WHERE m.cldr_eom_dt BETWEEN v.eff AND v.exp',
    'tran_fact t JOIN month_dim m ON t.dw_tran_dt = m.cldr_eom_dt, excl v WHERE m.cldr_eom_dt BETWEEN v.eff AND v.exp',
])
@pytest.mark.parametrize('dialect', ['duckdb', 'bigquery'])
def test_declared_scd2_between_never_links_tables_on_reference_and_runtime(tables, dialect):
    manager, allow = runtime()
    allow.required.clear()
    allow.windows = {('excl', 'eff', 'exp')}
    allow.joins.add(frozenset({('tran_fact', 'dw_tran_dt'), ('month_dim', 'cldr_eom_dt')}))
    sql = 'SELECT t.amt FROM ' + tables
    assert any('not joined to the others' in p for p in check(sql, allow, dialect=dialect))
    rewritten, physical, _ = manager._rename_sql_for_runtime(sql, 'u', 'live', allow)
    rewritten = sqlglot.transpile(rewritten, read='duckdb', write=dialect)[0]
    assert any('not joined to the others' in p for p in check(rewritten, physical, dialect=dialect))


@pytest.mark.parametrize('sql, allowed', [
    ('WITH c AS (SELECT a.id FROM a) SELECT g.* FROM c g', True),
    ('WITH unused AS (SELECT 1 AS one) SELECT g.* FROM a g', False),
    ('SELECT * FROM (SELECT a.id FROM a LIMIT 10)', True),
])
def test_stars_only_read_derived_scopes(sql, allowed):
    allow = Allowlist('u', {'a'}, {'a': {'id'}}, schema={'a': {'id'}})
    problems = check(sql, allow)
    assert problems == [] if allowed else any('SELECT * is not allowed' in p for p in problems)


def test_outer_cte_join_is_only_allowed_for_checked_plans_and_their_runtime():
    manager, _ = runtime()
    schema = {'tran_fact': {'amt'}}
    allow = Allowlist('u', set(schema), schema, schema=schema, declared={'1 = 1'})
    sql = '''WITH c AS (SELECT t.amt FROM tran_fact t), d AS (SELECT t.amt FROM tran_fact t)
             SELECT c.amt FROM c LEFT JOIN d ON c.amt = d.amt'''
    assert any('inner joins only' in p for p in check(sql, allow))
    checked = CheckedAllowlist(**vars(allow), alignment_keys={'amt'}, branches={
        c: {'scope': c, 'tables': {'t': 'tran_fact'}, 'predicates': set()} for c in ('c', 'd')})
    assert check(sql, checked) == []
    rewritten, physical, _ = manager._rename_sql_for_runtime(sql, 'u', 'live', checked)
    assert check(rewritten, physical) == []
