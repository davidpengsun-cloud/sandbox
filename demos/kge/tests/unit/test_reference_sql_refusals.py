"""An approved reference SQL that BigQuery rejects is an expectation not available (QueryRefused, with BigQuery's own
message), on the served warehouse and on an external runtime alike; a transient BigQuery failure still raises as it was."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from kge.adapters.bigquery import QueryFailed
from kge.adapters.warehouse.bigquery import BigQueryWarehouses
from kge.adapters.warehouse.runtimes import REFERENCE, RuntimeManager
from kge.pipeline.sqlguard import Allowlist, schema_allowlist
from kge.ports import REFERENCE as PORTS_REFERENCE
from kge.ports import QueryRefused

REJECTED = [
    QueryFailed("BigQuery error (HTTP 400): Unrecognized name: sample_amount", "invalidQuery", 400),
    QueryFailed("BigQuery error (HTTP 403): Access Denied: Table sample_table", "accessDenied", 403),
    QueryFailed("BigQuery error (HTTP 404): Not found: Table sample_table", "notFound", 404),
]
TRANSIENT = [
    QueryFailed("BigQuery error (HTTP 429): Exceeded rate limits", "rateLimitExceeded", 429),
    QueryFailed("BigQuery error (HTTP 500): An internal error occurred", "internalError", 500),
    QueryFailed("BigQuery error (HTTP 503): Backend error", "backendError", 503),
    QueryFailed("BigQuery backendError: try again", "backendError", 0),
]
DRY, RUN = "dry", "run"


def test_reference_runtime_id_is_one_constant():
    assert REFERENCE is PORTS_REFERENCE and REFERENCE == "reference"


def failing(failure: QueryFailed, at: str):
    def run(sql, dry_run=False, **options):
        if at == (DRY if dry_run else RUN):
            raise failure
        if dry_run:
            return {"totalBytesProcessed": "10"}
        return {"schema": {"fields": [{"name": "total", "type": "INTEGER"}]}, "rows": [{"f": [{"v": "7"}]}]}
    return run


def external(failure: QueryFailed, at: str) -> RuntimeManager:
    manager = RuntimeManager(SimpleNamespace(units=["u"], curated=[], project="billing"))
    manager.runtimes["live"] = {"id": "live", "dialect": "bigquery", "kind": "external",
                                "location": "sample_data.sample_set", "billing_project": "billing", "units": ["u"]}
    manager.rt_tables = {("live", "flow"): {"table": "FLOW"}}
    manager.rt_columns = {("live", "flow"): {"amount": {"column": "net_amount", "type": "INTEGER"}}}
    manager.ref_table_units = {"flow": "u"}
    schema = {"flow": {"amount"}}
    manager.reference_allowlists["u"] = Allowlist("u", set(schema), schema, schema=schema)
    manager._bigquery_loader = lambda _: (SimpleNamespace(run=failing(failure, at)), "sample_data", "sample_set")
    return manager


def served(failure: QueryFailed, at: str, tmp_path: Path, monkeypatch) -> BigQueryWarehouses:
    warehouse = BigQueryWarehouses(tmp_path, ["retail"], project="sample-project", dataset_prefix="kge")
    columns = {"FLOW": {"AMOUNT"}}
    graph = SimpleNamespace(unit_tables=lambda unit: set(columns), unit_joins=lambda unit: [],
                            table_columns=lambda unit, table: columns[table])
    warehouse.configure_runtimes({"reference_allowlists": {"retail": schema_allowlist(graph, "retail", ())}})
    monkeypatch.setattr(warehouse, "_query", failing(failure, at))
    return warehouse


def resolve_external(manager: RuntimeManager):
    return manager.resolve_reference_sql("SELECT SUM(amount) AS total FROM flow", unit="u", runtime_id="live",
                                         readable_units={"u"})


def resolve_served(warehouse: BigQueryWarehouses):
    return warehouse.resolve_reference_sql("SELECT SUM(AMOUNT) AS total FROM ${SCHEMA}.FLOW", unit="retail")


@pytest.mark.parametrize("at", [DRY, RUN])
@pytest.mark.parametrize("failure", REJECTED, ids=lambda f: f.reason)
def test_external_runtime_rejection_is_refused_with_bigquerys_message(failure, at):
    with pytest.raises(QueryRefused) as caught:
        resolve_external(external(failure, at))
    assert str(caught.value) == str(failure)
    assert caught.value.__cause__ is failure


@pytest.mark.parametrize("at", [DRY, RUN])
@pytest.mark.parametrize("failure", TRANSIENT, ids=lambda f: f"{f.reason}-{f.code}")
def test_external_runtime_transient_failure_still_raises(failure, at):
    assert failure.transient
    with pytest.raises(QueryFailed) as caught:
        resolve_external(external(failure, at))
    assert caught.value is failure


@pytest.mark.parametrize("at", [DRY, RUN])
@pytest.mark.parametrize("failure", REJECTED, ids=lambda f: f.reason)
def test_served_warehouse_rejection_is_refused_with_bigquerys_message(failure, at, tmp_path, monkeypatch):
    with pytest.raises(QueryRefused) as caught:
        resolve_served(served(failure, at, tmp_path, monkeypatch))
    assert str(caught.value) == str(failure)
    assert caught.value.__cause__ is failure


@pytest.mark.parametrize("at", [DRY, RUN])
@pytest.mark.parametrize("failure", TRANSIENT, ids=lambda f: f"{f.reason}-{f.code}")
def test_served_warehouse_transient_failure_still_raises(failure, at, tmp_path, monkeypatch):
    with pytest.raises(QueryFailed) as caught:
        resolve_served(served(failure, at, tmp_path, monkeypatch))
    assert caught.value is failure


def test_a_reference_sql_bigquery_accepts_still_resolves(tmp_path, monkeypatch):
    never = QueryFailed("unused", "invalidQuery", 400)
    assert resolve_external(external(never, "neither"))["figure"] == 7
    assert resolve_served(served(never, "neither", tmp_path, monkeypatch))["figure"] == 7


@pytest.mark.parametrize("sql", ["SELECT SUM(month_start) AS total FROM ${SCHEMA}.monthly_revenue",
                                 "SELECT SAFE_DIVIDE_X(net_revenue, 2) AS total FROM ${SCHEMA}.monthly_revenue"])
def test_a_reference_sql_duckdb_cannot_bind_is_unavailable_on_the_reference_warehouse_and_on_a_runtime(tmp_path, sql):
    from kge.eval.run_golden import judge
    from tests.unit.test_runtimes_eval import _build_two_runtime_warehouses
    w, _ = _build_two_runtime_warehouses(tmp_path)
    item = {"id": "X", "kind": "within", "unit": "retail", "reference_sql": sql,
            "reference_sql_approved_by": "A steward", "reference_sql_approved_on": "2026-11-19", "expect": {"figure": 1.0}}
    try:
        for rt in (REFERENCE, "ext_copy"):
            got = judge(item, {"behaviour": "figure", "figure": 1.0, "runtime": {"id": rt}}, w=w, cache={})
            assert got["status"] == "unavailable" and got["why"].startswith(f"expectation not available on {rt}: "), got
    finally:
        w.close()
