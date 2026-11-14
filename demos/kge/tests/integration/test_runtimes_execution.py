"""Unit and acceptance tests for #347 Slice 3: runtime resolution, IMPLEMENTS execution, provenance, partition filters, health checks, and discoverability."""
from __future__ import annotations

import duckdb
import pytest
from fastapi.testclient import TestClient

import kge.app as appmod
from kge.services.ask.engine.agent import Agent
from kge.services.ask import service as ask_service
from tests.scratch import open_engine, scratch_build


def _create_live_duckdb(path, *, include_fee_inc=True, fee_inc_type="DECIMAL(14,2)", fee_value=987.65):
    con = duckdb.connect(str(path))
    try:
        cols = [
            "rev_id INTEGER",
            "acct_id INTEGER",
            "m_start DATE",
            "int_inc_usd DECIMAL(14,2)",
            "tot_rev_usd DECIMAL(14,2)",
        ]
        if include_fee_inc:
            cols.append(f"fee_inc_usd {fee_inc_type}")
        con.execute(f"CREATE TABLE rev_fact_live ({', '.join(cols)})")
        if include_fee_inc:
            val = "'bad'" if fee_inc_type == "VARCHAR" else str(fee_value)
            con.execute(f"INSERT INTO rev_fact_live VALUES (1, 101, DATE '2025-06-01', 500.00, 1487.65, {val})")
        else:
            con.execute("INSERT INTO rev_fact_live VALUES (1, 101, DATE '2025-06-01', 500.00, 1487.65)")
        con.execute("CREATE TABLE period_dim_live (m_start DATE, cal_yr INTEGER, cal_qtr VARCHAR)")
        con.execute("INSERT INTO period_dim_live VALUES (DATE '2025-06-01', 2025, '2025-Q2')")
        con.execute("CREATE TABLE extra_runtime_only_tbl (id INTEGER, note VARCHAR)")
    finally:
        con.close()


def _runtime_subgraph(live_db_path, *, default_runtimes=("bq_live", "reference"), certified_runtime="bq_live",
                      partitioned=False, include_interest_mapping=False):
    rt_cols = [
        {"id": "bq_live.rev_fact_live.rev_id", "runtime": "bq_live", "table": "bq_live.rev_fact_live",
         "name": "rev_id", "data_type": "INTEGER", "implements": "retail.monthly_revenue.revenue_id"},
        {"id": "bq_live.rev_fact_live.acct_id", "runtime": "bq_live", "table": "bq_live.rev_fact_live",
         "name": "acct_id", "data_type": "INTEGER", "implements": "retail.monthly_revenue.account_id"},
        {"id": "bq_live.rev_fact_live.m_start", "runtime": "bq_live", "table": "bq_live.rev_fact_live",
         "name": "m_start", "data_type": "DATE", "implements": "retail.monthly_revenue.month_start"},
        {"id": "bq_live.rev_fact_live.fee_inc_usd", "runtime": "bq_live", "table": "bq_live.rev_fact_live",
         "name": "fee_inc_usd", "data_type": "DECIMAL(14,2)", "implements": "retail.monthly_revenue.fee_income"},
        {"id": "bq_live.rev_fact_live.tot_rev_usd", "runtime": "bq_live", "table": "bq_live.rev_fact_live",
         "name": "tot_rev_usd", "data_type": "DECIMAL(14,2)", "implements": "retail.monthly_revenue.total_revenue_usd"},
        {"id": "bq_live.period_dim_live.m_start", "runtime": "bq_live", "table": "bq_live.period_dim_live",
         "name": "m_start", "data_type": "DATE", "implements": "retail.calendar_month.month_start"},
        {"id": "bq_live.period_dim_live.cal_qtr", "runtime": "bq_live", "table": "bq_live.period_dim_live",
         "name": "cal_qtr", "data_type": "VARCHAR", "implements": "retail.calendar_month.calendar_quarter"},
        {"id": "bq_live.period_dim_live.cal_yr", "runtime": "bq_live", "table": "bq_live.period_dim_live",
         "name": "cal_yr", "data_type": "INTEGER", "implements": "retail.calendar_month.calendar_year"},
    ]
    if include_interest_mapping:
        rt_cols.append({
            "id": "bq_live.rev_fact_live.int_inc_usd", "runtime": "bq_live", "table": "bq_live.rev_fact_live",
            "name": "int_inc_usd", "data_type": "DECIMAL(14,2)", "implements": "retail.monthly_revenue.interest_income",
        })
    ref_cols = [
        {"unit": "retail", "tbl": "monthly_revenue", "name": "revenue_id", "data_type": "INTEGER"},
        {"unit": "retail", "tbl": "monthly_revenue", "name": "account_id", "data_type": "INTEGER"},
        {"unit": "retail", "tbl": "monthly_revenue", "name": "month_start", "data_type": "DATE"},
        {"unit": "retail", "tbl": "monthly_revenue", "name": "fee_income", "data_type": "DECIMAL(14,2)"},
        {"unit": "retail", "tbl": "monthly_revenue", "name": "interest_income", "data_type": "DECIMAL(14,2)"},
        {"unit": "retail", "tbl": "monthly_revenue", "name": "total_revenue_usd", "data_type": "DECIMAL(14,2)"},
        {"unit": "retail", "tbl": "calendar_month", "name": "month_start", "data_type": "DATE"},
        {"unit": "retail", "tbl": "calendar_month", "name": "calendar_quarter", "data_type": "VARCHAR"},
        {"unit": "retail", "tbl": "calendar_month", "name": "calendar_year", "data_type": "INTEGER"},
    ]
    certified = {}
    if certified_runtime:
        certified["retail.fee-income"] = certified_runtime
        certified["Fee Income"] = certified_runtime
    return {
        "runtimes": [
            {"id": "reference", "label": "Reference warehouse", "kind": "reference", "dialect": "duckdb",
             "location": "warehouses", "execution_identity": "", "billing_project": "", "default_order": 1},
            {"id": "bq_live", "label": "Live warehouse copy", "kind": "external", "dialect": "duckdb",
             "location": str(live_db_path), "execution_identity": "", "billing_project": "proj-live",
             "default_order": 0},
        ],
        "runtime_tables": [
            {"id": "bq_live.rev_fact_live", "runtime": "bq_live", "name": "rev_fact_live",
             "location": str(live_db_path), "implements": "retail.monthly_revenue"},
            {"id": "bq_live.period_dim_live", "runtime": "bq_live", "name": "period_dim_live",
             "location": str(live_db_path), "implements": "retail.calendar_month"},
        ],
        "runtime_columns": rt_cols,
        "certified_in": certified,
        "partitioned": {"retail.monthly_revenue": "month_start"} if partitioned else {},
        "reference_columns": ref_cols,
        "default_runtimes": list(default_runtimes),
    }


@pytest.fixture(scope="module")
def eng(tmp_path_factory):
    b = scratch_build(tmp_path_factory.mktemp("runtimes_exec"))
    e = open_engine(b)
    yield e
    e.close()


def test_runtime_precedence_and_location_or_unknown_rejection(eng, tmp_path, monkeypatch):
    live_db = tmp_path / "live.duckdb"
    _create_live_duckdb(live_db, fee_value=987.65)
    sub = _runtime_subgraph(live_db, default_runtimes=("reference",))
    eng.w.configure_runtimes(sub)

    assert eng.w.default_runtime_list() == ["reference"]
    monkeypatch.setenv("DEFAULT_RUNTIMES", "bq_live, reference")
    assert eng.w.default_runtime_list() == ["bq_live", "reference"]

    ans_env = eng.answer("What was Retail fee income for June 2025?", "analyst_retail", "analysis")
    assert ans_env["behaviour"] == "figure"
    assert ans_env["figure"] == 987.65
    assert ans_env["runtime"]["id"] == "bq_live"
    assert ans_env["live_data"] is True
    assert ans_env.get("queried_at")
    assert "rev_fact_live" in ans_env["sql"]
    assert "fee_inc_usd" in ans_env["sql"]
    assert "monthly_revenue" in ans_env["reference_sql"]
    assert ans_env["physical_tables"] == ["rev_fact_live"]

    ans_req = eng.answer("What was Retail fee income for June 2025?", "analyst_retail", "analysis",
                         runtimes=["reference"])
    assert ans_req["behaviour"] == "figure"
    assert ans_req["runtime"]["id"] == "reference"
    assert ans_req["figure"] != 987.65
    assert ans_req["live_data"] is False
    assert ans_req["runtime_certified"] is False
    assert ans_req["certified_in"] == "bq_live"

    for bad in ([str(live_db)], ["path/to/file.duckdb"], ["gs://bucket/file"], ["unknown_runtime"]):
        refused = eng.answer("What was Retail fee income for June 2025?", "analyst_retail", "analysis", runtimes=bad)
        assert refused["behaviour"] == "refuse"
        assert "location" in refused["reason"] or "unknown runtime" in refused["reason"].lower()

    sub_upper = _runtime_subgraph(live_db, default_runtimes=("reference",))
    sub_upper["runtimes"][1]["id"] = "BQ_Live"
    for rtt in sub_upper["runtime_tables"]:
        rtt["runtime"] = "BQ_Live"
    for rtc in sub_upper["runtime_columns"]:
        rtc["runtime"] = "BQ_Live"
    eng.w.configure_runtimes(sub_upper)
    ans_upper = eng.answer("What was Retail fee income for June 2025?", "analyst_retail", "analysis",
                           runtimes=["BQ_Live"])
    assert ans_upper["behaviour"] == "figure"
    assert ans_upper["runtime"]["id"] == "BQ_Live"


def test_fallback_along_runtime_list_and_never_guess_refusal(eng, tmp_path, monkeypatch):
    monkeypatch.delenv("DEFAULT_RUNTIMES", raising=False)
    live_db = tmp_path / "live_partial.duckdb"
    _create_live_duckdb(live_db, fee_value=555.0)
    sub = _runtime_subgraph(live_db, default_runtimes=("bq_live", "reference"))
    eng.w.configure_runtimes(sub)

    fallback = eng.answer("What was Retail net interest income for June 2025?", "analyst_retail", "analysis",
                          runtimes=["bq_live", "reference"])
    assert fallback["behaviour"] == "figure"
    assert fallback["runtime"]["id"] == "reference"

    refused = eng.answer("What was Retail net interest income for June 2025?", "analyst_retail", "analysis",
                         runtimes=["bq_live"])
    assert refused["behaviour"] == "refuse"
    assert "retail.monthly_revenue.interest_income" in refused["reason"]
    assert "IMPLEMENTS" in refused["reason"]

    missing_table_fallback = eng.answer("What was Retail average ledger balance for June 2025?", "analyst_retail", "analysis",
                                        runtimes=["bq_live", "reference"])
    assert missing_table_fallback["behaviour"] == "figure"
    assert missing_table_fallback["runtime"]["id"] == "reference"

    missing_table_refused = eng.answer("What was Retail average ledger balance for June 2025?", "analyst_retail", "analysis",
                                       runtimes=["bq_live"])
    assert missing_table_refused["behaviour"] == "refuse"
    assert "daily_balance" in missing_table_refused["reason"]
    assert "IMPLEMENTS" in missing_table_refused["reason"]


def test_runtimes_never_grant_unit_access(eng, tmp_path, monkeypatch):
    monkeypatch.delenv("DEFAULT_RUNTIMES", raising=False)
    live_db = tmp_path / "live_access.duckdb"
    _create_live_duckdb(live_db)
    sub = _runtime_subgraph(live_db, default_runtimes=("bq_live", "reference"))
    eng.w.configure_runtimes(sub)

    denied = eng.answer("What was SME fee income for June 2025?", "analyst_retail", "analysis",
                        runtimes=["bq_live", "reference"])
    assert denied["behaviour"] in ("refuse", "blocked_access")


def test_partition_column_policy_enforces_prunable_filter_and_default_period(eng, tmp_path, monkeypatch):
    monkeypatch.delenv("DEFAULT_RUNTIMES", raising=False)
    live_db = tmp_path / "live_part.duckdb"
    _create_live_duckdb(live_db, fee_value=321.0)
    sub = _runtime_subgraph(live_db, default_runtimes=("bq_live", "reference"), partitioned=True)
    eng.w.configure_runtimes(sub)
    eng.semantics.partitioned["retail.monthly_revenue"] = {
        "id": "part_monthly_rev",
        "table": "retail.monthly_revenue",
        "column": "month_start",
    }

    try:
        with_period = eng.answer("What was Retail fee income for June 2025?", "analyst_retail", "analysis",
                                 runtimes=["reference"])
        assert with_period["behaviour"] == "figure"
        assert "month_start" in with_period["sql"]
        assert "2025-06-01" in with_period["sql"]

        old_named = dict(eng.NAMED_PERIODS)
        eng.NAMED_PERIODS.pop("default", None)
        try:
            no_period = eng.answer("What was Retail fee income?", "analyst_retail", "analysis",
                                   runtimes=["reference"])
            assert no_period["behaviour"] == "refuse"
            assert "partition column month_start" in no_period["reason"]
        finally:
            eng.NAMED_PERIODS.update(old_named)

        eng.NAMED_PERIODS["default"] = "2025-06"
        try:
            with_default = eng.answer("What was Retail fee income?", "analyst_retail", "analysis",
                                      runtimes=["reference"])
            assert with_default["behaviour"] == "figure"
            assert "2025-06-01" in with_default["sql"]
        finally:
            if "default" not in old_named:
                eng.NAMED_PERIODS.pop("default", None)
    finally:
        eng.semantics.partitioned.pop("retail.monthly_revenue", None)
        eng.w._runtimes.partitioned.clear()

    eng.w._runtimes.partitioned["monthly_revenue"] = "month_start"
    try:
        err = eng.w._runtimes.check_partition_policy(
            "SELECT SUM(fee_income) FROM monthly_revenue WHERE CAST(month_start AS VARCHAR) = '2025-06-01'"
        )
        assert err is not None and "never wrapped in a function or cast" in err
        ok = eng.w._runtimes.check_partition_policy(
            "SELECT SUM(fee_income) FROM monthly_revenue WHERE month_start >= DATE '2025-06-01' AND month_start < DATE '2025-07-01'"
        )
        assert ok is None
    finally:
        eng.w._runtimes.partitioned.clear()


def test_startup_live_schema_check_marks_broken_runtime_unavailable_and_reports_on_healthz(eng, tmp_path, monkeypatch):
    monkeypatch.delenv("DEFAULT_RUNTIMES", raising=False)
    broken_db = tmp_path / "broken.duckdb"
    _create_live_duckdb(broken_db, include_fee_inc=False)
    sub = _runtime_subgraph(broken_db, default_runtimes=("bq_live", "reference"))
    eng.w.configure_runtimes(sub)

    health = eng.w.runtimes_health()
    assert health["bq_live"]["available"] is False
    assert any("fee_inc_usd" in p for p in health["bq_live"]["problems"])
    assert health["reference"]["available"] is True

    ans = eng.answer("What was Retail fee income for June 2025?", "analyst_retail", "analysis")
    assert ans["behaviour"] == "figure"
    assert ans["runtime"]["id"] == "reference"

    refused = eng.answer("What was Retail fee income for June 2025?", "analyst_retail", "analysis",
                         runtimes=["bq_live"])
    assert refused["behaviour"] == "refuse"
    assert "unavailable" in refused["reason"]

    retyped_db = tmp_path / "retyped.duckdb"
    _create_live_duckdb(retyped_db, include_fee_inc=True, fee_inc_type="VARCHAR")
    eng.w.configure_runtimes(_runtime_subgraph(retyped_db, default_runtimes=("bq_live", "reference")))
    health_retyped = eng.w.runtimes_health()
    assert health_retyped["bq_live"]["available"] is False
    assert any("type changed" in p for p in health_retyped["bq_live"]["problems"])

    sub_ident = _runtime_subgraph(retyped_db, default_runtimes=("bq_live", "reference"))
    sub_ident["runtimes"][1]["execution_identity"] = "exec-svc-identity"
    eng.w.configure_runtimes(sub_ident)
    health_ident = eng.w.runtimes_health()
    assert health_ident["bq_live"]["available"] is False
    assert health_ident["bq_live"]["reason"] == "execution_identity cannot be assumed"

    missing_db = tmp_path / "does_not_exist.duckdb"
    eng.w.configure_runtimes(_runtime_subgraph(missing_db, default_runtimes=("bq_live", "reference")))

    ag = Agent(eng, llm=None)
    with appmod.serving(ag):
        client = TestClient(appmod.app)
        hz = client.get("/healthz").json()
        assert hz["status"] == "ok"
        assert hz["runtimes"]["bq_live"]["available"] is False
        assert hz["runtimes"]["bq_live"]["reason"] == "runtime is unreachable"
        assert str(missing_db) not in str(hz)
        assert hz["runtimes"]["reference"]["available"] is True


def test_discoverability_get_runtimes_and_mcp_list_runtimes_and_routes(eng, tmp_path, monkeypatch):
    monkeypatch.delenv("DEFAULT_RUNTIMES", raising=False)
    live_db = tmp_path / "live_disc.duckdb"
    _create_live_duckdb(live_db, fee_value=777.0)
    sub = _runtime_subgraph(live_db, default_runtimes=("bq_live", "reference"))
    eng.w.configure_runtimes(sub)

    listed = ask_service.list_runtimes(eng, "analyst_retail")
    by_id = {r["id"]: r for r in listed["runtimes"]}
    assert set(by_id) == {"bq_live", "reference"}
    assert by_id["bq_live"]["certified"] is True
    assert by_id["reference"]["certified"] is False
    assert "retail.monthly_revenue" in by_id["bq_live"]["tables"]
    assert all(t.startswith("retail.") for t in by_id["bq_live"]["tables"])
    assert all(t.startswith("retail.") for t in by_id["reference"]["tables"])

    ag = Agent(eng, llm=None)
    token = ag.token("analyst_retail")
    headers = {"Authorization": f"Bearer {token}"}
    with appmod.serving(ag):
        client = TestClient(appmod.app)
        resp = client.get("/runtimes", headers=headers)
        assert resp.status_code == 200
        assert {r["id"] for r in resp.json()["runtimes"]} == {"bq_live", "reference"}

        ask_resp = client.post("/ask", headers=headers,
                               json={"question": "What was Retail fee income for June 2025?", "runtimes": ["bq_live"]})
        assert ask_resp.status_code == 200
        body = ask_resp.json()
        assert body["figure"] == 777.0
        assert body["runtime"]["id"] == "bq_live"
        assert "location" not in body["runtime"]
        assert body["runtime_certified"] is True
        assert body["live_data"] is True

        ctx_resp = client.post("/context", headers=headers,
                               json={"question": "What was Retail fee income for June 2025?", "runtimes": ["bq_live"]})
        assert ctx_resp.status_code == 200
        assert ctx_resp.json()["runtime"]["id"] == "bq_live"

        bad_ctx = client.post("/context", headers=headers,
                              json={"question": "What was Retail fee income for June 2025?", "runtimes": ["path/to/file.duckdb"]})
        assert bad_ctx.status_code == 200
        assert bad_ctx.json()["decided"]["behaviour"] == "refuse"

    eng.w.configure_runtimes(_runtime_subgraph(live_db, default_runtimes=("reference",), certified_runtime=""))
