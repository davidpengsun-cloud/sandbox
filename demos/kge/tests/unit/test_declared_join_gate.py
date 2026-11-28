"""Pack join authority survives exact-condition mutations and IMPLEMENTS renaming."""
import pytest
import sqlglot

from kge.pipeline.sqlguard import canonical, check, conjuncts
from kge.ports import QueryRefused
from tests.unit.test_runtime_allowlist import runtime


def declared(kind='left', window=True):
    manager, allow = runtime()
    condition = 'tran_fact.src = excl.src'
    if window:
        condition += ' AND tran_fact.dw_tran_dt BETWEEN excl.eff AND excl.exp'
    conditions = frozenset(canonical(c, {}) for c in conjuncts(sqlglot.parse_one(condition)))
    allow.required = {'optional': {'join_contract': {'source': 'tran_fact', 'target': 'excl',
                                                   'type': kind, 'condition': conditions}}}
    allow.predicates.update(conditions)
    allow.declared.update(conditions)
    allow.links.add(conditions)
    return manager, allow


def statement(kind='left', window=True):
    return ('SELECT t.amt FROM tran_fact t ' + kind.upper() + ' JOIN excl v ON t.src = v.src'
            + (' AND t.dw_tran_dt BETWEEN v.eff AND v.exp' if window else ''))


@pytest.mark.parametrize('kind', ['inner', 'left', 'right', 'full'])
@pytest.mark.parametrize('window', [False, True])
@pytest.mark.parametrize('dialect', ['duckdb', 'bigquery'])
def test_declared_type_and_window_pass_reference_data_and_runtime(kind, window, dialect):
    manager, allow = declared(kind, window)
    sql = statement(kind, window)
    assert check(sql, allow, dialect=dialect) == []
    assert check(sql, allow, dialect=dialect, inner_only=True) == []
    rewritten, physical, _ = manager._rename_sql_for_runtime(sql, 'u', 'live', allow)
    rewritten = sqlglot.transpile(rewritten, read='duckdb', write=dialect)[0]
    assert check(rewritten, physical, dialect=dialect) == []
    assert check(rewritten, physical, dialect=dialect, inner_only=True) == []
    assert physical.required['optional']['join_contract']['type'] == kind


@pytest.mark.parametrize('mutation', ['inner', 'right', 'full', 'semi', 'anti', 'dropped_window', 'altered_window',
                                     'changed_key', 'on_true', 'or_true', 'window_where', 'derived', 'derived_cte'])
@pytest.mark.parametrize('dialect', ['duckdb', 'bigquery'])
def test_mutations_refused_on_reference_data_and_runtime(mutation, dialect):
    manager, allow = declared()
    sql = statement()
    if mutation in ('inner', 'right', 'full', 'semi', 'anti'):
        sql = sql.replace('LEFT JOIN', mutation.upper() + ' JOIN')
    elif mutation == 'dropped_window':
        sql = statement(window=False)
    elif mutation == 'altered_window':
        sql = sql.replace('BETWEEN v.eff AND v.exp', 'BETWEEN v.exp AND v.eff')
    elif mutation == 'changed_key':
        sql = sql.replace('t.src = v.src', 't.amt = v.src')
    elif mutation == 'on_true':
        sql = sql.split(' ON ')[0] + ' ON TRUE'
    elif mutation == 'or_true':
        sql += ' OR TRUE'
    elif mutation == 'window_where':
        sql = sql.replace('AND t.dw_tran_dt', 'WHERE t.dw_tran_dt')
    elif mutation == 'derived':
        sql = sql.replace('excl v', '(SELECT src, eff, exp FROM excl) v')
    else:
        sql = 'WITH d AS (SELECT src, eff, exp FROM excl) ' + sql.replace('excl v', 'd v')
    assert check(sql, allow, dialect=dialect)
    assert check(sql, allow, dialect=dialect, inner_only=True)
    with pytest.raises(QueryRefused, match='reference SQL failed its gate'):
        manager._rename_sql_for_runtime(sql, 'u', 'live', allow)


def test_inner_window_can_be_a_top_level_where_conjunct():
    _, allow = declared('inner')
    sql = statement('inner').replace('AND t.dw_tran_dt', 'WHERE t.dw_tran_dt')
    assert check(sql, allow, inner_only=True) == []
    assert check(sql.replace('WHERE t.dw_tran_dt', 'WHERE TRUE OR t.dw_tran_dt'), allow)


def test_window_cannot_borrow_another_alias_of_its_dimension():
    _, allow = declared()
    sql = statement() + ' LEFT JOIN excl other ON t.src = other.src AND t.dw_tran_dt BETWEEN other.eff AND other.exp'
    assert check(sql, allow)


def test_missing_runtime_window_mapping_refuses_instead_of_dropping_authority():
    manager, allow = declared()
    del manager.rt_columns['live', 'excl']['exp']
    from kge.ports import QueryRefused
    with pytest.raises(QueryRefused):
        manager._rename_sql_for_runtime(statement(), 'u', 'live', allow)


@pytest.mark.parametrize('kind', ['inner', 'left', 'right', 'full'])
def test_server_join_authority_survives_copies_and_runtime_mapping_without_public_fields(kind):
    from dataclasses import asdict, replace
    from kge.pipeline.sqlguard import DataAllowlist
    manager, allow = declared(kind)
    allow._join_contracts = {identifier: need['join_contract'] for identifier, need in allow.required.items()}
    allow.required = {}
    copied = DataAllowlist(**vars(replace(allow)))
    assert copied._join_contracts == allow._join_contracts
    assert asdict(copied)['required'] == {} and '_join_contracts' not in asdict(copied)
    assert check(statement(kind), copied) == []
    rewritten, physical, _ = manager._rename_sql_for_runtime(statement(kind), 'u', 'live', copied)
    assert physical._join_contracts['optional']['type'] == kind
    assert physical.required == {} and check(rewritten, physical) == []
    with pytest.raises(QueryRefused):
        manager._rename_sql_for_runtime(statement(kind, window=False), 'u', 'live', copied)
