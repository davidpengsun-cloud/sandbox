"""A column named as its table (#179): BigQuery reads a bare name that is a source of its scope as that source's whole
row, a STRUCT, where DuckDB and the gate read the column, so a table routed under its own name as alias broke every
query that named such a column bare (the lock's storage, onboarding's sample, a model's query). Routing qualifies the
column by its source, on the code's SQL (rewrite_sql) and a model's (route_sql), and the BigQuery stand-in
(tests/bigquery_fake.py) refuses a row read as BigQuery does, so each path is run against it."""
from pathlib import Path

import pytest

from kge.adapters.warehouse import open_estate
from kge.adapters.warehouse.bigquery import BigQueryWarehouses, rewrite_sql, route_sql
from kge.pipeline.sqlguard import Allowlist
from tests import bigquery_fake
from tests.bigquery_fake import FakeBigQuery

DDL = ["CREATE TABLE calendar_date (calendar_date DATE PRIMARY KEY, day_name VARCHAR)"]
ROWS = [["2026-03-30", "Monday"], ["2026-03-31", "Tuesday"], ["2026-04-01", "Wednesday"]]


def test_the_stand_in_refuses_a_bare_name_that_is_a_source_of_its_scope_as_bigquery_does():
    fake = FakeBigQuery()
    fake.con.execute('CREATE TABLE "wealth_calendar_date" (calendar_date DATE, day_name VARCHAR)')
    with pytest.raises(RuntimeError, match="HTTP 400"):
        fake.run(None, "SELECT CAST(`calendar_date` AS STRING) FROM `wealth_calendar_date` AS `calendar_date`")
    fake.run(None, "SELECT CAST(`calendar_date`.`calendar_date` AS STRING) FROM `wealth_calendar_date` AS `calendar_date`")
    fake.run(None, "SELECT CAST(`calendar_date` AS STRING) FROM `wealth_calendar_date` AS d")
    fake.run(None, "SELECT max(day_name) AS calendar_date FROM `wealth_calendar_date` AS calendar_date ORDER BY calendar_date")
    with pytest.raises(RuntimeError, match="HTTP 400"):
        fake.run(None, "WITH calendar_date AS (SELECT 1 AS calendar_date) SELECT calendar_date FROM calendar_date")


@pytest.mark.parametrize("output", ["d.calendar_date", "d.calendar_date AS calendar_date"])
def test_an_order_by_naming_another_source_s_output_is_left_to_read_that_output(output):
    """ORDER BY calendar_date, where the select outputs d's calendar_date, reads that output: qualifying it by the
    calendar_date table would order by a column the gate never checked."""
    written = f"SELECT {output} FROM calendar_date JOIN date_dim d ON d.k = calendar_date.k ORDER BY calendar_date LIMIT 5"
    assert rewrite_sql(written, unit, "wealth").endswith("ORDER BY calendar_date NULLS LAST LIMIT 5")
    assert route_sql(written, unit, "wealth") == (
        f"SELECT {output} FROM `wealth_calendar_date` AS calendar_date JOIN `wealth_date_dim` d ON d.k = calendar_date.k "
        "ORDER BY calendar_date LIMIT 5")
    assert route_sql("SELECT calendar_date FROM calendar_date ORDER BY calendar_date", unit, "wealth") == (
        "SELECT calendar_date.calendar_date FROM `wealth_calendar_date` AS calendar_date ORDER BY calendar_date.calendar_date")


def unit(name: str) -> str:
    return name


def test_routing_qualifies_a_column_named_as_its_source_and_nothing_else():
    assert rewrite_sql('SELECT DISTINCT CAST("calendar_date" AS VARCHAR) FROM "calendar_date" WHERE "calendar_date" IS NOT NULL',
                       unit, "wealth") == ("SELECT DISTINCT CAST(calendar_date.`calendar_date` AS STRING) FROM wealth_calendar_date "
                                           "AS calendar_date WHERE NOT calendar_date.`calendar_date` IS NULL")
    assert route_sql("SELECT `calendar_date`, day_name FROM calendar_date ORDER BY Calendar_Date", unit, "wealth") == (
        "SELECT calendar_date.`calendar_date`, day_name FROM `wealth_calendar_date` AS calendar_date "
        "ORDER BY calendar_date.Calendar_Date")
    assert route_sql("SELECT d.calendar_date FROM calendar_date d", unit, "wealth") == (
        "SELECT d.calendar_date FROM `wealth_calendar_date` d")
    assert route_sql("SELECT max(day_name) AS calendar_date FROM calendar_date c ORDER BY calendar_date", unit, "wealth") == (
        "SELECT max(day_name) AS calendar_date FROM `wealth_calendar_date` c ORDER BY calendar_date")
    assert route_sql("SELECT calendar_date FROM calendar_date AS calendar_date", unit, "wealth") == (
        "SELECT calendar_date.calendar_date FROM `wealth_calendar_date` AS calendar_date")


def test_the_lock_s_storage_onboarding_s_sample_and_the_code_s_queries_read_the_column(tmp_path, monkeypatch):
    """storage (each table's figure in the lock), sample (a drafted column's values) and describe, on a published
    warehouse whose table is named as its key column, and a query of the code's own with every clause naming it."""
    monkeypatch.setenv("WAREHOUSE_BACKEND", "bigquery")
    fake = bigquery_fake.install(monkeypatch)
    estate = open_estate(tmp_path / "warehouses", "meridian")
    staged = estate.stage("wealth", DDL)
    staged.load("calendar_date", ["calendar_date", "day_name"], ROWS)
    staged.flush()
    estate.publish("wealth")
    session = estate.open("wealth")
    assert session.storage("calendar_date") == 3 * len("2026-03-30") + len("MondayTuesdayWednesday")
    assert sorted(session.sample("calendar_date", "calendar_date")) == ["2026-03-30", "2026-03-31", "2026-04-01"]
    assert session.describe("calendar_date")["rows"] == 3
    rows = session.execute("SELECT calendar_date, count(*) FROM calendar_date WHERE calendar_date >= DATE '2026-03-31' "
                           "GROUP BY calendar_date HAVING max(calendar_date) IS NOT NULL ORDER BY calendar_date").fetchall()
    assert [(str(d), n) for d, n in rows] == [("2026-03-31", 1), ("2026-04-01", 1)]
    assert any("calendar_date.`calendar_date`" in s for s in fake.statements)


def test_a_model_s_query_naming_the_column_bare_passes_the_gate_and_reads_the_column(monkeypatch):
    fake = FakeBigQuery()
    fake.con.execute('CREATE TABLE "wealth_calendar_date" (calendar_date DATE, day_name VARCHAR)')
    fake.con.executemany('INSERT INTO "wealth_calendar_date" VALUES (?, ?)', ROWS)
    w = BigQueryWarehouses(Path("warehouses"), ["wealth"], project="pilot-project", client="meridian")
    monkeypatch.setattr(w, "_post", lambda url, headers, body: fake.run(None, body["query"], dry_run=body.get("dryRun", False)))
    monkeypatch.setattr(w, "token", lambda: ("stand-in", "pilot-project"))
    allow = Allowlist("wealth", {"calendar_date"}, {"calendar_date": {"calendar_date", "day_name"}},
                      schema={"calendar_date": {"calendar_date", "day_name"}})
    sql = ("SELECT calendar_date, day_name FROM calendar_date WHERE calendar_date = DATE '2026-03-31' "
           "QUALIFY ROW_NUMBER() OVER (PARTITION BY calendar_date ORDER BY day_name) = 1")
    gated = w.gate("wealth", sql, allow, dialect="bigquery")
    assert gated["passed"], gated["problems"]
    columns, rows = w.query("wealth", sql, "bigquery")
    assert columns == ["calendar_date", "day_name"] and [(str(d), n) for d, n in rows] == [("2026-03-31", "Tuesday")]
