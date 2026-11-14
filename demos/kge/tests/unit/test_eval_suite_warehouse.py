"""Tests for UAT question suite expansion, served warehouse reference_sql execution, and CLI eval post (#342)."""
from __future__ import annotations

import json
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace

import duckdb
import pytest

from kge.adapters.warehouse import (
    check_readonly_reference_sql,
    strip_schema_placeholder,
)
from kge.adapters.warehouse.bigquery import BigQueryWarehouses
from kge.adapters.warehouse.duckdb import Warehouses
from kge.eval import run_golden, sets
from kge.pipeline import settings as settings_mod
from kge.ports import QueryRefused
from kge.pipeline.sqlguard import schema_allowlist



def reference_allowlist(columns, restricted=()):
    graph = SimpleNamespace(unit_tables=lambda unit: set(columns), unit_joins=lambda unit: [],
                            table_columns=lambda unit, table: columns[table])
    return schema_allowlist(graph, "retail", restricted)

def test_warehouse_setting_and_suite_keys_removed():
    """WAREHOUSE is not a setting, and schema_map, warehouse_profiles, scalability_guardrails are not in SUITE_KEYS."""
    assert "WAREHOUSE" not in settings_mod.ENVIRONMENT_NAMES
    assert not hasattr(settings_mod.Settings(), "WAREHOUSE")
    assert sets.SUITE_KEYS == ("suite_id", "suite_version", "title", "defaults")


def test_templated_suite_expansion_and_also_asked_as(tmp_path: Path):
    suite_payload = {
        "suite_id": "sage_nna_200",
        "suite_version": "1.0",
        "title": "Templated NNA Suite",
        "defaults": {"unit": "USD", "tolerance": {"abs": 1.0, "rel": 0.0001}},
        "questions": [
            {
                "id": "Q001",
                "category": "exec_headline",
                "expected_behavior": "figure",
                "question": "What was total NNA in 2025?",
                "also_asked_as": ["Give me 2025 total NNA."],
                "reference_sql": "SELECT SUM(NNA_USD) AS val FROM ${SCHEMA}.TRAN_FACT_1 WHERE TRANS_DT BETWEEN '2025-01-01' AND '2025-12-31'",
                "required_signals": ["TRAN_FACT_1"],
                "forbidden_signals": ["HOLDING_FACT"],
            },
            {
                "id": "Q002",
                "category": "abstention",
                "expected_behavior": "abstain",
                "question": "What was competitor Vanguard NNA in 2025?",
                "required_signals": ["competitor"],
            },
        ],
        "templates": [
            {
                "id_prefix": "T_MKT",
                "category": "channel_slice",
                "expected_behavior": "figure",
                "question_template": "What was {mkt} NNA in {year}?",
                "reference_sql": "SELECT SUM(f.NNA_USD) AS val FROM ${SCHEMA}.TRAN_FACT_1 f WHERE f.MKT = '{mkt}' AND EXTRACT(YEAR FROM f.TRANS_DT) = {year}",
                "matrix": [
                    {"id": "Q003", "mkt": "PCG", "year": 2025, "also_asked_as": ["PCG NNA for 2025?"]},
                    {"id": "Q004", "mkt": "INST", "year": 2024},
                ],
            }
        ],
    }
    suite_file = tmp_path / "suite.json"
    suite_file.write_text(json.dumps(suite_payload))

    loaded = sets.read(suite_file)
    assert loaded["version"] == "v1"
    items = loaded["items"]
    ids = [i["id"] for i in items]
    assert ids == ["Q001", "Q001_alt1", "Q002", "Q003", "Q003_alt1", "Q004"]

    q1 = items[0]
    assert q1["kind"] == "figure"
    assert q1["unit"] == "USD"
    assert q1["expect"]["tolerance"] == {"abs": 1.0, "rel": 0.0001}
    assert "${SCHEMA}.TRAN_FACT_1" in q1["expect"]["reference_sql"]
    assert q1["expect"]["required_signals"] == ["TRAN_FACT_1"]
    assert q1["expect"]["forbidden_signals"] == ["HOLDING_FACT"]

    q3 = next(i for i in items if i["id"] == "Q003")
    assert q3["question"] == "What was PCG NNA in 2025?"
    assert "f.MKT = 'PCG'" in q3["expect"]["reference_sql"]


def test_strip_schema_placeholder():
    sql = "SELECT SUM(f.NNA_USD) FROM ${SCHEMA}.TRAN_FACT_1 f JOIN ${SCHEMA.REF}.PROD_DIM p ON f.K = p.K"
    cleaned = strip_schema_placeholder(sql)
    assert cleaned == "SELECT SUM(f.NNA_USD) FROM TRAN_FACT_1 f JOIN PROD_DIM p ON f.K = p.K"


def test_duckdb_resolve_reference_sql_and_truth_cache(tmp_path: Path):
    db_path = tmp_path / "retail.duckdb"
    con = duckdb.connect(str(db_path))
    con.execute("CREATE TABLE TRAN_FACT_1 (MKT VARCHAR, TRANS_DT DATE, NNA_USD DOUBLE)")
    con.execute(
        "INSERT INTO TRAN_FACT_1 VALUES "
        "('PCG', DATE '2025-03-15', 1250.5), "
        "('PCG', DATE '2025-06-10', 749.5), "
        "('INST', DATE '2025-04-01', 500.0)"
    )
    con.close()

    w = Warehouses(tmp_path, ["retail"])
    w.configure_runtimes({"reference_allowlists": {"retail": reference_allowlist({
        "TRAN_FACT_1": {"MKT", "TRANS_DT", "NNA_USD"},
    })}})
    try:
        items = [
            {
                "id": "Q1",
                "kind": "figure",
                "question": "PCG NNA 2025?",
                "reference_sql": (
                    "SELECT MAX(TRANS_DT) AS latest_dt, CAST(SUM(NNA_USD) AS DECIMAL(18, 2)) AS dec_val, "
                    "SUM(NNA_USD) AS val FROM ${SCHEMA}.TRAN_FACT_1 "
                    "WHERE MKT = 'PCG' AND TRANS_DT >= DATE '2025-01-01'"
                ),
                "expect": {},
            },
            {
                "id": "Q1__alt1",
                "kind": "figure",
                "question": "2025 PCG NNA?",
                "reference_sql": (
                    "SELECT MAX(TRANS_DT) AS latest_dt, CAST(SUM(NNA_USD) AS DECIMAL(18, 2)) AS dec_val, "
                    "SUM(NNA_USD) AS val FROM ${SCHEMA}.TRAN_FACT_1 "
                    "WHERE MKT = 'PCG' AND TRANS_DT >= DATE '2025-01-01'"
                ),
                "expect": {},
            },
        ]
        cache_file = tmp_path / "cache.json"
        cache = run_golden.resolve_suite_truth(
            items,
            w,
            default_unit="retail",
            cache_path=cache_file,
        )
        assert len(cache) == 1
        assert items[0]["expect"]["figure"] == pytest.approx(2000.0)
        assert items[1]["expect"]["figure"] == pytest.approx(2000.0)
        assert cache_file.is_file()

        w.versions = {"retail": 2}
        reloaded_items = [
            {
                "id": "Q1_reloaded",
                "kind": "figure",
                "question": "PCG NNA 2025 after reload?",
                "reference_sql": items[0]["reference_sql"],
                "expect": {},
            }
        ]
        cache2 = run_golden.resolve_suite_truth(
            reloaded_items,
            w,
            default_unit="retail",
            cache_path=cache_file,
        )
        assert len(cache2) == 2
    finally:
        w.close()


def test_bigquery_resolve_reference_sql_uses_served_warehouse_and_server_caps(monkeypatch, tmp_path: Path):
    bq = BigQueryWarehouses(
        tmp_path,
        ["retail"],
        project="proj-a",
        dataset_prefix="kge",
        client="meridian",
    )
    bq.configure_runtimes({"reference_allowlists": {"retail": reference_allowlist({
        "TRAN_FACT_1": {"TRANS_DT", "NNA_USD"},
    })}})
    queries: list[tuple[str, bool, int | None]] = []

    def fake_query(sql: str, dry_run: bool = False, limits=None, job_timeout_ms=None, max_bytes_billed=None):
        queries.append((sql, dry_run, max_bytes_billed))
        if dry_run:
            return {"totalBytesProcessed": "5000000"}
        return {
            "schema": {"fields": [{"name": "total_nna", "type": "FLOAT64"}]},
            "rows": [{"f": [{"v": "42500.75"}]}],
        }

    monkeypatch.setattr(bq, "_query", fake_query)
    res = bq.resolve_reference_sql(
        "SELECT SUM(NNA_USD) AS total_nna FROM ${SCHEMA}.TRAN_FACT_1 WHERE TRANS_DT = DATE '2025-01-01'",
        unit="retail",
    )
    assert res["figure"] == pytest.approx(42500.75)
    assert res["bytes_estimated"] == 5_000_000
    assert "`retail_TRAN_FACT_1`" in res["sql"]
    assert len(queries) == 2
    assert queries[0][1] is True
    assert queries[1][1] is False
    assert queries[1][2] == 10_737_418_240

    monkeypatch.setenv("BIGQUERY_MAX_BYTES_BILLED", "1000000")
    with pytest.raises(QueryRefused, match="exceeds BIGQUERY_MAX_BYTES_BILLED cap"):
        bq.resolve_reference_sql(
            "SELECT SUM(NNA_USD) AS total_nna FROM ${SCHEMA}.TRAN_FACT_1 WHERE TRANS_DT = DATE '2025-01-01'",
            unit="retail",
        )


def test_reference_sql_security_gates_writes_functions_and_internal_tables(monkeypatch, tmp_path: Path):
    for bad_sql in (
        "DELETE FROM TRAN_FACT_1 WHERE 1=1",
        "DROP TABLE TRAN_FACT_1",
        "SELECT * FROM TRAN_FACT_1; DROP TABLE TRAN_FACT_1",
        "SELECT session_user() FROM TRAN_FACT_1",
        "SELECT repeat('a', 1000000) FROM TRAN_FACT_1",
        "SELECT * FROM access_grants",
        "SELECT * FROM _kge_catalogue",
        "SELECT * FROM gabcdefabcdefabcd_nodes",
    ):
        with pytest.raises(QueryRefused):
            check_readonly_reference_sql(bad_sql, dialect="bigquery")

    bq = BigQueryWarehouses(
        tmp_path,
        ["retail"],
        project="proj-a",
        dataset_prefix="kge",
        client="meridian",
    )
    called = []
    monkeypatch.setattr(bq, "_query", lambda *a, **kw: called.append((a, kw)))
    for bad in (
        "DELETE FROM ${SCHEMA}.TRAN_FACT_1 WHERE 1=1",
        "SELECT * FROM `other_proj.other_ds`.secret_table WHERE dt = DATE '2025-01-01'",
    ):
        with pytest.raises(QueryRefused):
            bq.resolve_reference_sql(bad, unit="retail")
    assert called == []


def test_choose_arms_supports_agent_arms_and_presets():
    assert run_golden.choose_arms("treatment", ["schema", "catalog", "graph"], ["control", "treatment"]) == ["treatment"]
    assert run_golden.choose_arms("graph,treatment", ["schema", "catalog", "graph"], ["control", "treatment"]) == ["graph", "treatment"]


def test_score_supports_uat_kinds_and_signals():
    item_fig = {
        "id": "Q10",
        "kind": "figure",
        "expect": {
            "figure": 1000.0,
            "tolerance": {"abs": 5.0, "rel": 0.01},
            "required_signals": ["TRAN_FACT_1"],
            "forbidden_signals": ["HOLDING_FACT"],
        },
    }
    ok, why = run_golden.score(
        item_fig,
        {"behaviour": "figure", "figure": 1008.0, "sql": "SELECT SUM(x) FROM TRAN_FACT_1"},
    )
    assert ok, why

    ok_bad, why_bad = run_golden.score(
        item_fig,
        {"behaviour": "figure", "figure": 1008.0, "sql": "SELECT SUM(x) FROM HOLDING_FACT"},
    )
    assert not ok_bad
    assert "missing signals" in why_bad or "forbidden signals" in why_bad


@pytest.mark.parametrize("backend", ["duckdb", "bigquery"])
@pytest.mark.parametrize("denial", ["restricted", "unit"])
def test_served_reference_truth_refused_without_execution(monkeypatch, tmp_path, backend, denial):
    if backend == "duckdb":
        duckdb.connect(str(tmp_path / "retail.duckdb")).close()
        warehouse = Warehouses(tmp_path, ["retail"])
    else:
        warehouse = BigQueryWarehouses(tmp_path, ["retail"], project="proj-a", dataset_prefix="kge", client="meridian")
    warehouse.configure_runtimes({"reference_allowlists": {"retail": reference_allowlist(
        {"TRAN_FACT_1": {"NNA_USD", "private_value"}}, [("retail", "TRAN_FACT_1", "private_value")],
    )}})
    monkeypatch.setattr(warehouse, "query" if backend == "duckdb" else "_query",
                        lambda *a, **kw: pytest.fail("refused truth was executed"))
    column = "private_value" if denial == "restricted" else "NNA_USD"
    item = {"id": "refused", "kind": "figure", "unit": "retail", "expect": {},
            "reference_sql": f"SELECT SUM({column}) FROM TRAN_FACT_1"}
    try:
        cache = run_golden.resolve_suite_truth([item], warehouse, readable_units={"retail"} if denial == "restricted" else set())
        entry, = cache.values()
        assert entry["refused"] is True
        expected = "restricted" if denial == "restricted" else "access denied for unit retail"
        assert expected in entry["error"]
        ok, why = run_golden.score(item, {"behaviour": "figure", "figure": 1})
        assert not ok and "ground_truth_refused" in why and expected in why
    finally:
        warehouse.close()


@pytest.mark.parametrize("kind", ["figure", "within", "list", "point", "refusal", "abstain", "shelf", "cross"])
def test_truth_refusal_is_reported_for_every_item_kind(kind):
    expect = {"behaviour": "refuse", "cite": []}
    original, _ = run_golden.score({"kind": kind, "expect": expect}, {"behaviour": "refuse"})
    ok, why = run_golden.score({"kind": kind, "expect": {**expect, "ground_truth_error": "restricted column"}},
                               {"behaviour": "refuse"})
    assert ok is (False if kind in ("within", "figure") else original)
    assert why.startswith("ground_truth_refused: restricted column")


@pytest.mark.parametrize("backend", ["duckdb", "bigquery"])
@pytest.mark.parametrize("suite", [False, True])
def test_cli_persists_and_prints_restricted_truth_refusal(monkeypatch, tmp_path, capsys, backend, suite):
    if backend == "duckdb":
        duckdb.connect(str(tmp_path / "retail.duckdb")).close()
        warehouse = Warehouses(tmp_path, ["retail"])
    else:
        warehouse = BigQueryWarehouses(tmp_path, ["retail"], project="proj-a", dataset_prefix="kge", client="meridian")
    warehouse.configure_runtimes({"reference_allowlists": {"retail": reference_allowlist(
        {"TRAN_FACT_1": {"private_value"}}, [("retail", "TRAN_FACT_1", "private_value")],
    )}})
    queries = []
    monkeypatch.setattr(warehouse, "query" if backend == "duckdb" else "_query", lambda *a, **kw: queries.append(a))
    estate = tmp_path / "estate"
    estate.mkdir()
    (estate / "documents.json").write_text("[]")
    golden = estate / "golden.json"
    golden.write_text(json.dumps([{
        "id": "restricted", "kind": "figure", "unit": "retail", "question": "Total private value?",
        "reference_sql": "SELECT SUM(private_value) FROM TRAN_FACT_1", "expect": {},
    }]))
    profile = SimpleNamespace(estate=lambda: estate, run_dir=lambda: tmp_path / "run", asking=lambda who: who)
    profile.serve = lambda: profile
    engine = SimpleNamespace(
        g=SimpleNamespace(readable_units=lambda who: {"retail"}, as_roles=nullcontext),
        w=warehouse, UNIT_ORDER=["retail"], close=warehouse.close,
        answer=lambda *a, **kw: {"behaviour": "refuse", "runtime": {"id": "reference"}},
    )
    monkeypatch.setattr(run_golden, "resolve", lambda name: profile)
    monkeypatch.setattr(run_golden.Engine, "serving", lambda prof: engine)
    args = ["--arm", "graph,schema", "--as", "analyst"]
    if suite:
        args += ["--suite", str(golden)]
    assert run_golden.main(args) == 0
    result = json.loads((profile.run_dir() / "eval.json").read_text())
    assert result["refused"] == [{
        "id": "restricted", "runtime": "reference", "reason": "column TRAN_FACT_1.private_value is restricted personal data",
    }]
    assert capsys.readouterr().out.count(
        "restricted [reference] refused: column TRAN_FACT_1.private_value is restricted personal data",
    ) == 1
    assert queries == []
