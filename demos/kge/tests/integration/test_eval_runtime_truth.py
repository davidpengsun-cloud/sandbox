"""Every eval scores an item against its approved reference SQL run on the runtime the answer used (issue #565).

A test install with a named runtime, live, serving Retail's monthly revenue and calendar tables from a DuckDB file of
its own (synthetic rows, built the way tests/integration/test_runtimes_execution.py builds bq_live), and the reference
warehouse for everything else. On it: a right answer on either runtime passes, a wrong figure fails, a reference SQL
that cannot run on the answering runtime is reported apart as "expectation not available on live", never as a
failure or a known miss, an item without approved reference SQL is a draft off the reference warehouse, and asking and
truth use one runtime list. With only the reference runtime, the verdicts are the stored expectation's, as before.
"""
from __future__ import annotations

import json

import duckdb
import pytest

from kge.eval import harness, sets
from kge.eval.run_golden import APART, asking_runtimes, judge, score
from tests.scratch import open_engine, scratch_build

WHO = "analyst_retail"
FEE = "What was Retail fee income for June 2025?"
LIVE_FEE = 987.65
APPROVAL = {"reference_sql_approved_by": "owner", "reference_sql_approved_on": "2026-11-19"}
FEE_SQL = "SELECT SUM(fee_income) AS fee_income FROM monthly_revenue WHERE month_start = DATE '2025-06-01'"
INTEREST_SQL = "SELECT SUM(interest_income) AS interest_income FROM monthly_revenue WHERE month_start = DATE '2025-06-01'"


def live_file(path):
    con = duckdb.connect(str(path))
    try:
        con.execute("CREATE TABLE rev_fact_live (rev_id INTEGER, acct_id INTEGER, m_start DATE, "
                    "int_inc_usd DECIMAL(14,2), tot_rev_usd DECIMAL(14,2), fee_inc_usd DECIMAL(14,2))")
        con.execute(f"INSERT INTO rev_fact_live VALUES (1, 101, DATE '2025-06-01', 500.00, 1487.65, {LIVE_FEE})")
        con.execute("CREATE TABLE period_dim_live (m_start DATE, cal_yr INTEGER, cal_qtr VARCHAR)")
        con.execute("INSERT INTO period_dim_live VALUES (DATE '2025-06-01', 2025, '2025-Q2')")
    finally:
        con.close()


def subgraph(path, default=("live", "reference")):
    """The live runtime: Retail's monthly revenue and calendar month, its interest income left unmapped."""
    def col(table, name, data_type, implements):
        return {"id": f"live.{table}.{name}", "runtime": "live", "table": f"live.{table}", "name": name,
                "data_type": data_type, "implements": implements}
    ref = [("monthly_revenue", "revenue_id", "INTEGER"), ("monthly_revenue", "account_id", "INTEGER"),
           ("monthly_revenue", "month_start", "DATE"), ("monthly_revenue", "fee_income", "DECIMAL(14,2)"),
           ("monthly_revenue", "interest_income", "DECIMAL(14,2)"), ("monthly_revenue", "total_revenue_usd", "DECIMAL(14,2)"),
           ("calendar_month", "month_start", "DATE"), ("calendar_month", "calendar_quarter", "VARCHAR"),
           ("calendar_month", "calendar_year", "INTEGER")]
    return {
        "runtimes": [
            {"id": "reference", "label": "Reference warehouse", "kind": "reference", "location": "warehouses",
             "execution_identity": "", "billing_project": "", "default_order": 1},
            {"id": "live", "label": "Live copy", "kind": "external", "dialect": "duckdb", "location": str(path),
             "execution_identity": "", "billing_project": "", "default_order": 0},
        ],
        "runtime_tables": [
            {"id": "live.rev_fact_live", "runtime": "live", "name": "rev_fact_live", "location": str(path),
             "implements": "retail.monthly_revenue"},
            {"id": "live.period_dim_live", "runtime": "live", "name": "period_dim_live", "location": str(path),
             "implements": "retail.calendar_month"},
        ],
        "runtime_columns": [
            col("rev_fact_live", "rev_id", "INTEGER", "retail.monthly_revenue.revenue_id"),
            col("rev_fact_live", "acct_id", "INTEGER", "retail.monthly_revenue.account_id"),
            col("rev_fact_live", "m_start", "DATE", "retail.monthly_revenue.month_start"),
            col("rev_fact_live", "fee_inc_usd", "DECIMAL(14,2)", "retail.monthly_revenue.fee_income"),
            col("rev_fact_live", "tot_rev_usd", "DECIMAL(14,2)", "retail.monthly_revenue.total_revenue_usd"),
            col("period_dim_live", "m_start", "DATE", "retail.calendar_month.month_start"),
            col("period_dim_live", "cal_qtr", "VARCHAR", "retail.calendar_month.calendar_quarter"),
            col("period_dim_live", "cal_yr", "INTEGER", "retail.calendar_month.calendar_year"),
        ],
        "certified_in": {"retail.fee-income": "live", "Fee Income": "live"},
        "partitioned": {},
        "reference_columns": [{"unit": "retail", "tbl": t, "name": c, "data_type": d} for t, c, d in ref],
        "default_runtimes": list(default),
    }


@pytest.fixture(scope="module")
def install(tmp_path_factory):
    folder = tmp_path_factory.mktemp("runtime_truth")
    eng = open_engine(scratch_build(folder / "build"))
    live_file(folder / "live.duckdb")
    held = dict(eng.w._runtimes.reference_allowlists)
    try:
        yield eng, folder / "live.duckdb", held
    finally:
        eng.close()


@pytest.fixture
def live(install, monkeypatch):
    eng, path, held = install
    monkeypatch.delenv("DEFAULT_RUNTIMES", raising=False)
    eng.w.configure_runtimes({**subgraph(path), "reference_allowlists": held})
    yield eng
    eng.w.configure_runtimes({"reference_allowlists": held})


def fee_item(**extra) -> dict:
    return {"id": "RT01", "kind": "within", "question": FEE, "expect": {"unit": "retail", "figure": 1.0},
            "reference_sql": FEE_SQL, **APPROVAL, **extra}


def verdict(eng, item, ans, cache=None):
    return judge(item, ans, w=eng.w, runtimes=asking_runtimes(item), default_unit="retail",
                 readable_units=eng.g.readable_units(WHO), cache={} if cache is None else cache)


def test_a_right_answer_on_the_named_runtime_passes_against_that_runtimes_truth(live):
    ans = live.answer(FEE, WHO, "analysis")
    assert ans["runtime"]["id"] == "live" and ans["figure"] == LIVE_FEE
    got = verdict(live, fee_item(), ans)
    assert got["status"] == "passed" and got["ok"] is True and got["runtime"] == "live", got
    stored, _ = score(fee_item(), ans)
    assert stored is False


def test_a_right_answer_on_the_reference_warehouse_passes_against_its_truth(live):
    ans = live.answer(FEE, WHO, "analysis", runtimes=["reference"])
    assert ans["runtime"]["id"] == "reference" and ans["figure"] != LIVE_FEE
    got = verdict(live, fee_item(runtimes=["reference"]), ans)
    assert got["status"] == "passed" and got["runtime"] == "reference", got


def test_a_wrong_figure_fails_on_either_runtime(live):
    for runtimes in (None, ["reference"]):
        ans = live.answer(FEE, WHO, "analysis", runtimes=runtimes)
        wrong = {**ans, "figure": ans["figure"] + 100}
        got = verdict(live, fee_item(), wrong)
        assert got["status"] == "failed" and got["ok"] is False, got


def test_an_expectation_that_cannot_run_on_the_answering_runtime_is_reported_apart(live):
    ans = live.answer(FEE, WHO, "analysis")
    item = fee_item(reference_sql=INTEREST_SQL)
    got = verdict(live, item, ans)
    assert got["status"] == "unavailable" and got["ok"] is None, got
    assert got["why"].startswith("expectation not available on live: "), got
    assert got["status"] in APART


def test_an_item_without_approved_reference_sql_is_a_draft_off_the_reference_warehouse(live):
    ans = live.answer(FEE, WHO, "analysis")
    unapproved = {k: v for k, v in fee_item().items() if k not in APPROVAL}
    stored_only = {k: v for k, v in unapproved.items() if k != "reference_sql"}
    for item in (unapproved, stored_only):
        got = verdict(live, item, ans)
        assert got["status"] == "draft" and got["ok"] is None and got["why"].startswith("draft: "), got
    assert sets.approved(fee_item()) and not sets.approved(unapproved)


def test_asking_and_truth_use_one_runtime_list(live):
    item = fee_item(runtimes=["reference"])
    listed = asking_runtimes(item)
    assert listed == ["reference"] and asking_runtimes(item, ["live"]) == ["live"]
    ans = live.answer(FEE, WHO, "analysis", runtimes=listed)
    no_runtime = {k: v for k, v in ans.items() if k != "runtime"}
    got = verdict(live, item, no_runtime)
    assert got["status"] == "passed", got


def test_the_release_baseline_reports_apart_items_and_never_marks_them_known_misses(live, tmp_path):
    wrong_sql = FEE_SQL.replace("SUM(fee_income) AS", "SUM(fee_income) + 100 AS")
    items = [fee_item(), {**fee_item(reference_sql=INTEREST_SQL), "id": "RT02"}, {**fee_item(reference_sql=wrong_sql), "id": "RT03"}]
    arms = harness.Arms(live)
    recs = [harness.one(arms, "graph", item, "golden", 1, "run", WHO, arms.describe("graph"), {}, {}, set(), cache={})
            for item in items]
    by_id = {r["id"]: r for r in recs}
    assert by_id["RT01"]["ok"] is True and not by_id["RT01"].get("apart"), by_id["RT01"]
    assert by_id["RT02"].get("apart") == "unavailable" and by_id["RT02"]["ok"] is None, by_id["RT02"]
    assert by_id["RT02"]["why"].startswith("expectation not available on live: ")
    assert by_id["RT03"]["ok"] is False and not by_id["RT03"].get("apart"), by_id["RT03"]
    estate = tmp_path / "estate"
    estate.mkdir()
    (estate / "golden.json").write_text(json.dumps({"version": "v2", "items": items}))
    scored = [r for r in recs if not r.get("apart")]
    baseline = {"profile": "p", "sets": {"golden": {"file": "golden.json", "version": "v2"}},
                "items": {"golden": {r["id"]: {"graph": {"pass_rate": 1.0 if r["ok"] else 0.0}} for r in scored}}}
    asked = sets.recorded(estate / "golden.json", "v2", items, baseline, "graph", "p")
    assert asked[FEE] == [True, None, False]


def test_with_only_the_reference_runtime_the_verdicts_are_the_stored_expectations(install, monkeypatch):
    eng, _, held = install
    monkeypatch.delenv("DEFAULT_RUNTIMES", raising=False)
    eng.w.configure_runtimes({"reference_allowlists": held})
    ans = eng.answer(FEE, WHO, "analysis")
    stored = {"id": "RT04", "kind": "within", "question": FEE, "expect": {"unit": "retail", "figure": ans["figure"]}}
    wrong = {**stored, "expect": {"unit": "retail", "figure": ans["figure"] + 1}}
    for item in (stored, wrong, {**stored, "reference_sql": FEE_SQL}):
        got = verdict(eng, item, ans)
        ok, _ = score(item, ans)
        assert got["ok"] is ok and got["status"] == ("passed" if ok else "failed"), (item, got)
    approved = verdict(eng, fee_item(), ans)
    assert approved["status"] == "passed" and approved["runtime"] == "reference", approved
