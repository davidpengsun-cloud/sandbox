"""The earlier Wealth window is additive, deterministic and uses existing versions."""
import csv
import datetime as dt
import io
import random
from collections import Counter

import pytest

from kge.adapters.warehouse import open_estate
from kge.estate import generate, sage, spec


def csv_rows(rows):
    stream = io.StringIO(newline="")
    csv.writer(stream, lineterminator="\n").writerows(rows)
    return Counter(stream.getvalue().splitlines(keepends=True))


@pytest.fixture(scope="module")
def history(tmp_path_factory):
    estate = open_estate(tmp_path_factory.mktemp("wealth-history") / "warehouses", "meridian")
    con = generate.create_schema(estate, "wealth")
    master = random.Random(7)
    for _ in range(3):
        master.randint(0, 10**9)
    generate.populate_wealth(con, random.Random(master.randint(0, 10**9)))
    fill = sage.Filler(random.Random("7:sage"))
    generate.populate_wealth_expansion(con, random.Random("7:wealth"), fill)
    before = {table: con.execute(f"SELECT * FROM {table}").fetchall() for table in spec.SCHEMAS["wealth"]}
    generate.populate_wealth_history(con, fill, 7)
    yield con, before, fill
    con.close()


def test_every_existing_row_is_byte_identical_and_only_earlier_facts_are_added(history):
    con, before, _ = history
    added = {}
    for table, rows in before.items():
        after = csv_rows(con.execute(f"SELECT * FROM {table}").fetchall())
        original = csv_rows(rows)
        assert not original - after, table
        added[table] = sum((after - original).values())
    assert {table for table, count in added.items() if count} == {
        "MONTH_DIM_1", "TRAN_FACT_1", "TRAN_FACT_EXCL_MFCLR_G", "F_NNA_FIN", "FISCAL_QTR_DIM_1",
        "HLDG_FACT_1", "AUA_FACT_1", "SUIT_RVW_FACT_1", "QTR_FEE_REV_FACT_1", "AUM_MNTH_END_FACT_1"}
    for table, date in (("TRAN_FACT_1", "DW_TRAN_DT"), ("TRAN_FACT_EXCL_MFCLR_G", "DW_TRAN_DT"),
                        ("F_NNA_FIN", "DW_TRAN_MNTH_END_DT"), ("HLDG_FACT_1", "AS_OF_DT"),
                        ("AUA_FACT_1", "AS_OF_DT"), ("SUIT_RVW_FACT_1", "RVW_DT"),
                        ("AUM_MNTH_END_FACT_1", "CLDR_EOM_DT")):
        columns = [c[0] for c in spec.SCHEMAS["wealth"][table]["columns"]]
        old = set(before[table])
        for row in con.execute(f"SELECT * FROM {table}").fetchall():
            if row not in old:
                assert dt.date(2023, 7, 1) <= row[columns.index(date)] < dt.date(2024, 7, 1), table


def test_earlier_months_are_released_and_have_daily_calendar_and_all_three_extracts(history):
    con, _, _ = history
    months = con.execute("SELECT CLDR_EOM_DT, FIN_MTRC_RLSE_DT, DW_BRKRG_MNTH_FRM_TODAY_CT FROM MONTH_DIM_1 "
                         "WHERE CLDR_EOM_DT < DATE '2024-07-01' ORDER BY 1").fetchall()
    assert len(months) == 12
    assert months[0][0] == dt.date(2023, 7, 31) and months[-1][0] == dt.date(2024, 6, 30)
    for day, released, distance in months:
        assert released == day + dt.timedelta(days=sage.RELEASE_LAG) and distance < 0
        for table, column in (("TRAN_FACT_1", "MNTH_END_DT"), ("TRAN_FACT_EXCL_MFCLR_G", "MNTH_END_DT"),
                              ("F_NNA_FIN", "DW_TRAN_MNTH_END_DT")):
            assert con.execute(f"SELECT count(*) FROM {table} WHERE {column} = ?", [day]).fetchone()[0] > 0
    assert con.execute("SELECT count(*) FROM DATE_DIM_1 WHERE CALENDAR_DATE BETWEEN DATE '2023-07-01' "
                       "AND DATE '2024-06-30'").fetchone()[0] == 366


def test_historical_flows_have_one_valid_account_and_consistent_certified_extract(history):
    con, _, _ = history
    assert con.execute("SELECT count(*) FROM (SELECT f.DW_TRAN_ID FROM TRAN_FACT_1 f "
                       "LEFT JOIN ACCT_DIM2_1 a ON f.DW_ACCT_ID = a.DW_ACCT_ID AND f.ACCT_SRCE_SYS_ID = a.SRCE_SYS_ID "
                       "AND f.DW_TRAN_DT BETWEEN a.DW_EFF_DT AND a.DW_EXPR_DT "
                       "WHERE f.DW_TRAN_DT < DATE '2024-07-01' GROUP BY f.DW_TRAN_ID HAVING count(a.DW_ACCT_ID) <> 1)"
                       ).fetchone()[0] == 0
    assert con.execute("SELECT count(*) FROM ACCT_DIM2_1 a JOIN ACCT_DIM2_1 b ON a.DW_ACCT_ID = b.DW_ACCT_ID "
                       "AND a.SRCE_SYS_ID = b.SRCE_SYS_ID AND a.DW_EFF_DT < b.DW_EFF_DT "
                       "AND a.DW_EXPR_DT >= b.DW_EFF_DT").fetchone()[0] == 0
    actual = con.execute("SELECT DW_TRAN_MNTH_END_DT, sum(NNA_AM) FROM F_NNA_FIN "
                         "WHERE DW_TRAN_MNTH_END_DT < DATE '2024-07-01' GROUP BY 1 ORDER BY 1").fetchall()
    expected = con.execute("SELECT f.MNTH_END_DT, sum(CASE WHEN f.DW_TRAN_DIRCTN_CD = 'O' THEN -f.TRAN_AM ELSE f.TRAN_AM END) "
                           "FROM TRAN_FACT_1 f JOIN ACCT_DIM2_1 a ON f.DW_ACCT_ID = a.DW_ACCT_ID "
                           "AND f.ACCT_SRCE_SYS_ID = a.SRCE_SYS_ID AND f.DW_TRAN_DT BETWEEN a.DW_EFF_DT AND a.DW_EXPR_DT "
                           "WHERE f.MNTH_END_DT < DATE '2024-07-01' AND f.NNA_IN = 'Y' AND f.CURR_IN = 1 "
                           "AND a.TEST_ACCT_IN = 'N' GROUP BY 1 ORDER BY 1").fetchall()
    assert actual == expected


def test_history_replays_from_an_isolated_seed_without_changing_input_versions(history, tmp_path):
    original, before, fill = history
    accounts = [a["_v"][:] for a in fill.accounts]
    estate = open_estate(tmp_path / "warehouses", "meridian")
    con = generate.create_schema(estate, "wealth")
    try:
        for table, rows in before.items():
            generate.insert_batched(con, table, rows)
        generate.populate_wealth_history(con, fill, 7)
        assert [a["_v"] for a in fill.accounts] == accounts
        for table in before:
            assert csv_rows(con.execute(f"SELECT * FROM {table}").fetchall()) == csv_rows(
                original.execute(f"SELECT * FROM {table}").fetchall()), table
    finally:
        con.close()


@pytest.mark.parametrize("day,expected", [(dt.date(2024, 2, 29), dt.date(2023, 2, 28)),
                                          (dt.date(2025, 6, 30), dt.date(2024, 6, 30))])
def test_previous_calendar_year_clips_leap_day(day, expected):
    assert generate.previous_year(day) == expected
