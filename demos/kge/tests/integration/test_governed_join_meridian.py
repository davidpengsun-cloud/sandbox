"""The data-query route applies declared joins before policy wrapping and warehouse admission."""
from decimal import Decimal

import pytest
from fastapi import HTTPException

from kge.estate.golden import warehouses
from kge.estate.golden_sage import golden_20
from kge.estate.golden_wealth import read
from kge.pipeline.sqlguard import check
from kge.ports import AUTHORED
from kge.services.data import routes
from kge.services.data.engine.governance import constrain
from tests.integration.test_join_declarations_meridian import ACCOUNT, DAY, PROBES, ROLLUP, SOURCE, allowlist
from tests.scratch import BUILD


@pytest.fixture
def data_query(eng, monkeypatch):
    monkeypatch.setattr(routes.served, 'engine', lambda: eng)
    return lambda sql: routes.query(routes.OwnQuery(unit='wealth', sql=sql), 'advisor_wealth')


@pytest.fixture(scope='module')
def reference_session():
    session = warehouses(BUILD, 'meridian').open('wealth')
    yield session
    session.close()


def assert_figures(actual, expected):
    assert len(actual) == len(expected)
    for got, want in zip(actual, expected):
        assert len(got) == len(want)
        for value, reference in zip(got, want):
            if isinstance(reference, (int, float, Decimal)) and not isinstance(reference, bool):
                assert float(value) == pytest.approx(float(reference), abs=0.005)
            else:
                assert (value.isoformat() if hasattr(value, 'isoformat') else value) == reference


def governed_rows(eng, item):
    sql, policy = constrain(eng, 'wealth', item.sql, allowlist(eng), AUTHORED, set())
    assert check(sql, policy, inner_only=True) == []
    gate = eng.w.gate('wealth', sql, policy, dialect=AUTHORED)
    assert gate['passed'], gate['problems']
    return eng.w.rows('wealth', sql, AUTHORED)


@pytest.mark.parametrize('joins,matched,expected', [
    (ACCOUNT, 'a.DW_ACCT_ID', 40),
    (ACCOUNT + ' ' + ROLLUP, 'r.CLNT_CD', 23),
    (SOURCE, 's.SRCE_SYS_ID', 40),
    (DAY, 'd.CALENDAR_DATE', 0),
], ids=['account', 'client_code', 'source_system', 'date'])
def test_data_query_route_retains_optional_rows_and_window_figures(data_query, joins, matched, expected):
    statement = (f'SELECT SUM(t.NNA_AM) AS total, '
                 f'SUM(CASE WHEN {matched} IS NOT NULL THEN t.NNA_AM ELSE 0 END) AS matched_total '
                 f'FROM F_NNA_FIN t {joins} WHERE {PROBES}')
    result = data_query(statement)
    assert result.rows == [{'total': Decimal('51'), 'matched_total': Decimal(expected)}]
    assert result.row_count == 1


def test_data_query_route_keeps_the_financial_release_window(data_query):
    statement = """SELECT SUM(t.TRAN_AM) AS total FROM TRAN_FACT_EXCL_MFCLR_G t
INNER JOIN MONTH_DIM_1 m ON t.MNTH_END_DT = m.CLDR_EOM_DT
WHERE m.FIN_MTRC_RLSE_DT BETWEEN t.DW_EFF_DT AND t.DW_EXPR_DT AND t.DW_TRAN_ID = 8800004"""
    assert data_query(statement).rows == [{'total': Decimal('29')}]


@pytest.mark.parametrize('original,changed', [
    (ACCOUNT, ACCOUNT.replace('LEFT JOIN', 'RIGHT JOIN')),
    (ACCOUNT, ACCOUNT.replace('AND t.DW_TRAN_MNTH_END_DT BETWEEN a.DW_EFF_DT AND a.DW_EXPR_DT', '')),
    (ACCOUNT, ACCOUNT.replace('a.DW_EXPR_DT', 'a.DW_EFF_DT')),
    (ACCOUNT, ACCOUNT.replace('a.DW_ACCT_ID', 'a.HSHLD_DK')),
    (ACCOUNT + ' ' + ROLLUP, ACCOUNT + ' ' + ROLLUP.replace(
        'AND t.DW_TRAN_MNTH_END_DT BETWEEN r.DW_EFF_DT AND r.DW_EXPR_DT', '')),
    (SOURCE, SOURCE.replace('LEFT JOIN', 'INNER JOIN')),
    (SOURCE, SOURCE.replace(' AND s.RCV_IN = 1', '')),
    (DAY, DAY.replace('t.DW_TRAN_DT = d.CALENDAR_DATE', 'TRUE')),
    (DAY, DAY.replace('DATE_DIM_1 d', '(SELECT CALENDAR_DATE FROM DATE_DIM_1) d')),
])
def test_data_query_route_refuses_changed_contracts(data_query, original, changed):
    template = f'SELECT SUM(t.NNA_AM) AS total FROM F_NNA_FIN t {{}} WHERE {PROBES}'
    assert data_query(template.format(original)).rows == [{'total': Decimal('51')}]
    with pytest.raises(HTTPException) as error:
        data_query(template.format(changed))
    assert error.value.status_code == 400


@pytest.mark.parametrize('item', [entry[0] for entry in golden_20()], ids=lambda item: item.id)
def test_original_reference_sql_survives_governance_with_observed_policy_figures(eng, reference_session, item):
    expected = read(reference_session, item, item.sql)['table']['rows']
    assert_figures(governed_rows(eng, item), expected)


def test_unrestricted_current_reference_and_governed_current_have_the_same_figures(eng, reference_session):
    """Current NNA includes test accounts, as the golden S08 SQL reads it, so governed current matches the reference."""
    item = next(entry[0] for entry in golden_20() if entry[0].id == 'S08')
    expected = read(reference_session, item, item.sql)['table']['rows']
    assert_figures(governed_rows(eng, item), expected)
