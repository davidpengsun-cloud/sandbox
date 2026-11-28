"""Declared joins admit the owner's original SQL and retain observable sample rows."""
from decimal import Decimal

import pytest

from kge.estate.golden import warehouses
from kge.estate.golden_sage import golden_20
from kge.estate.golden_wealth import read
from kge.pipeline.sqlguard import schema_allowlist
from kge.ports import AUTHORED
from kge.services.data.engine.governance import declared_allowlist
from tests.scratch import BUILD


PROBES = "t.DW_TRAN_ID BETWEEN 8800001 AND 8800003"
ACCOUNT = """LEFT JOIN ACCT_DIM2_1 a ON t.DW_ACCT_ID = a.DW_ACCT_ID
AND t.ACCT_SRCE_SYS_ID = a.SRCE_SYS_ID
AND t.DW_TRAN_MNTH_END_DT BETWEEN a.DW_EFF_DT AND a.DW_EXPR_DT"""
ROLLUP = """LEFT JOIN CLIENT_CD_ROLLUP_1 r ON a.CLNT_CD = r.CLNT_CD
AND t.DW_TRAN_MNTH_END_DT BETWEEN r.DW_EFF_DT AND r.DW_EXPR_DT"""
SOURCE = """LEFT JOIN SRCE_SYS_1 s ON t.ACCT_SRCE_SYS_ID = s.SRCE_SYS_ID
AND s.CURR_IN = 1 AND s.RCV_IN = 1"""
DAY = "LEFT JOIN DATE_DIM_1 d ON t.DW_TRAN_DT = d.CALENDAR_DATE"


def allowlist(eng):
    return declared_allowlist(eng, 'wealth', schema_allowlist(eng.g, 'wealth', eng.RESTRICTED_COLUMNS))


def admitted(eng, statement):
    result = eng.w.gate('wealth', statement, allowlist(eng), dialect=AUTHORED)
    assert result['passed'], result['problems']
    return eng.w.rows('wealth', statement, AUTHORED)


@pytest.fixture(scope='module')
def reference_session():
    session = warehouses(BUILD, 'meridian').open('wealth')
    yield session
    session.close()


@pytest.mark.parametrize('item', [entry[0] for entry in golden_20()], ids=lambda item: item.id)
def test_original_golden_sql_passes_the_gate_and_equals_its_direct_run(eng, reference_session, item):
    expected = read(reference_session, item, item.sql)['table']['rows']
    actual = admitted(eng, item.sql)
    assert len(actual) == len(expected)
    for got, want in zip(actual, expected):
        assert len(got) == len(want)
        for value, reference in zip(got, want):
            if isinstance(reference, (int, float, Decimal)) and not isinstance(reference, bool):
                assert float(value) == pytest.approx(float(reference), abs=0.005)
            else:
                assert (value.isoformat() if hasattr(value, 'isoformat') else value) == reference


@pytest.mark.parametrize('joins, matched, expected', [
    (ACCOUNT, 'a.DW_ACCT_ID', 40),
    (ACCOUNT + ' ' + ROLLUP, 'r.CLNT_CD', 23),
    (SOURCE, 's.SRCE_SYS_ID', 40),
    (DAY, 'd.CALENDAR_DATE', 0),
], ids=['account', 'client_code', 'source_system', 'date'])
def test_each_optional_join_retains_nonmatching_sample_figures(eng, joins, matched, expected):
    statement = (f'SELECT SUM(t.NNA_AM), SUM(CASE WHEN {matched} IS NOT NULL THEN t.NNA_AM ELSE 0 END) '
                 f'FROM F_NNA_FIN t {joins} WHERE {PROBES}')
    total, matched_total = admitted(eng, statement)[0]
    assert total == Decimal('51') and matched_total == Decimal(expected)
    assert total > matched_total


@pytest.mark.parametrize('table, key, predicate', [
    ('ACCT_DIM2_1', 'DW_ACCT_ID', 'DW_ACCT_ID = 8800001'),
    ('CLIENT_CD_ROLLUP_1', 'CLNT_CD', "CLNT_CD = 'JOIN_MATCH'"),
    ('TRAN_FACT_EXCL_MFCLR_G', 'DW_TRAN_ID', 'DW_TRAN_ID = 8800004'),
])
def test_each_window_has_a_second_sample_version_outside_it(eng, table, key, predicate):
    statement = f'SELECT COUNT({key}), MIN(DW_EXPR_DT), MAX(DW_EFF_DT) FROM {table} WHERE {predicate}'
    count, expired, effective = admitted(eng, statement)[0]
    assert count == 2 and expired.isoformat() == '1900-12-31' and effective.isoformat() == '2000-01-01'


def test_the_financial_release_window_selects_one_sample_transaction_version(eng):
    statement = """SELECT SUM(t.TRAN_AM) FROM TRAN_FACT_EXCL_MFCLR_G t
INNER JOIN MONTH_DIM_1 m ON t.MNTH_END_DT = m.CLDR_EOM_DT
WHERE m.FIN_MTRC_RLSE_DT BETWEEN t.DW_EFF_DT AND t.DW_EXPR_DT AND t.DW_TRAN_ID = 8800004"""
    assert admitted(eng, statement) == [(Decimal('29'),)]
    dropped = statement.replace('m.FIN_MTRC_RLSE_DT BETWEEN t.DW_EFF_DT AND t.DW_EXPR_DT AND ', '')
    assert not eng.w.gate('wealth', dropped, allowlist(eng), dialect=AUTHORED)['passed']


@pytest.mark.parametrize('original, changed', [
    (ACCOUNT, ACCOUNT.replace('LEFT JOIN', 'RIGHT JOIN')),
    (ACCOUNT, ACCOUNT.replace('AND t.DW_TRAN_MNTH_END_DT BETWEEN a.DW_EFF_DT AND a.DW_EXPR_DT', '')),
    (ACCOUNT, ACCOUNT.replace('a.DW_EXPR_DT', 'a.DW_EFF_DT')),
    (ACCOUNT, ACCOUNT.replace('a.DW_ACCT_ID', 'a.HSHLD_DK')),
    (ACCOUNT + ' ' + ROLLUP, ACCOUNT + ' ' + ROLLUP.replace(
        'AND t.DW_TRAN_MNTH_END_DT BETWEEN r.DW_EFF_DT AND r.DW_EXPR_DT', '')),
    (SOURCE, SOURCE.replace('LEFT JOIN', 'INNER JOIN')),
    (SOURCE, SOURCE.replace(' AND s.RCV_IN = 1', '')),
    (DAY, DAY.replace('t.DW_TRAN_DT = d.CALENDAR_DATE', 'TRUE')),
    (DAY, DAY.replace('DATE_DIM_1 d', '(SELECT * FROM DATE_DIM_1) d')),
])
def test_data_and_reference_paths_refuse_changed_join_contracts(eng, original, changed):
    base = f'SELECT SUM(t.NNA_AM) FROM F_NNA_FIN t {{}} WHERE {PROBES}'
    assert eng.w.gate('wealth', base.format(original), allowlist(eng), dialect=AUTHORED)['passed']
    assert not eng.w.gate('wealth', base.format(changed), allowlist(eng), dialect=AUTHORED)['passed']
