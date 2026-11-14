"""Meridian's reachable versioned tables retain their validity on both warehouse spellings."""
from types import SimpleNamespace

import pytest
import sqlglot
import yaml

from kge.adapters.warehouse.runtimes import RuntimeManager
from kge.pipeline.settings import ROOT
from kge.pipeline.sqlguard import Allowlist, check


NEW_TABLES = ('CLIENT_CD_ROLLUP_1', 'TRAN_FACT_EXCL_MFCLR_G', 'SRCE_SYS_1')
PACK = yaml.safe_load((ROOT / 'clients/meridian/pack.yaml').read_text())
SCHEMA = yaml.safe_load((ROOT / 'clients/meridian/fixture/schema.yaml').read_text())


def test_context_reachable_versioned_tables_have_inclusive_policies():
    tables = {'wealth.' + t['name'] for t in SCHEMA['warehouses']['wealth']['tables']
              if {'DW_EFF_DT', 'DW_EXPR_DT', 'CURR_IN'} <= {c['name'] for c in t['columns']}}
    assert len(tables) == 13
    entities = {e['table'] for e in PACK['entities']}
    assert tables <= entities
    policies = {p['table']: p for p in PACK['policies']['scd2']}
    joins = {j['id']: j for j in PACK['joins']}
    reached = {c['fact'] for c in PACK['contexts']} | {t for c in PACK['contexts'] for jid in (*c.get('joins', []), *c.get('reaches', [])) for t in (joins[jid]['from'], joins[jid]['to'])}
    retired = {'wealth.' + t for t in ('BANK_DEP_ACCT_ADDR_1', 'LGCY_ACCT_1', 'D_ACCT_SEGMT', 'INS_CONTR_1', 'INS_SEC_PROD_CD_1')}
    assert not retired & reached
    assert not retired & policies.keys()
    for table in tables & reached:
        assert {k: policies[table][k] for k in ('valid_from', 'valid_to', 'is_current', 'valid_to_inclusive')} == {
            'valid_from': 'DW_EFF_DT', 'valid_to': 'DW_EXPR_DT', 'is_current': 'CURR_IN', 'valid_to_inclusive': True}


@pytest.mark.parametrize('table', NEW_TABLES)
@pytest.mark.parametrize('dialect', ['duckdb', 'bigquery'])
def test_new_meridian_as_of_join_passes_reference_and_runtime(table, dialect):
    policy = next(p for p in PACK['policies']['scd2'] if p['table'] == 'wealth.' + table)
    entity = next(e for e in PACK['entities'] if e['table'] == policy['table'])
    key, = entity['key']
    columns = {key, policy['valid_from'], policy['valid_to'], policy['is_current']}
    schema = {'probe': {'entity_key', 'as_of'}, table: columns}
    allow = Allowlist('wealth', set(schema), schema, schema=schema,
                      joins={frozenset({('probe', 'entity_key'), (table, key)})},
                      windows={(table, policy['valid_from'], policy['valid_to'])})
    sql = (f'SELECT p.entity_key FROM probe p JOIN {table} v ON p.entity_key = v.{key} '
           f"AND p.as_of BETWEEN v.{policy['valid_from']} AND v.{policy['valid_to']}")
    assert check(sql, allow, dialect=dialect) == []
    manager = RuntimeManager(SimpleNamespace(units=['wealth'], curated=[]))
    manager.ref_table_units = dict.fromkeys(schema, 'wealth')
    manager.ref_columns = {(t, c): 'INTEGER' for t, cs in schema.items() for c in cs}
    manager.rt_tables = {('live', t.lower()): {'table': 'runtime.APP_' + t} for t in schema}
    manager.rt_columns = {('live', t.lower()): {c.lower(): {'column': 'RT_' + c} for c in cs} for t, cs in schema.items()}
    rewritten, physical, _ = manager._rename_sql_for_runtime(sql, 'wealth', 'live', allow)
    assert physical.windows == {('runtime.APP_' + table, 'RT_' + policy['valid_from'], 'RT_' + policy['valid_to'])}
    rewritten = sqlglot.transpile(rewritten, read='duckdb', write=dialect)[0]
    assert check(rewritten, physical, dialect=dialect) == []
    allow.windows.clear()
    physical.windows.clear()
    assert any('not a declared join' in p for p in check(sql, allow, dialect=dialect))
    assert any('not a declared join' in p for p in check(rewritten, physical, dialect=dialect))
