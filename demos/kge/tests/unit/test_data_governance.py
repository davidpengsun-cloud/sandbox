from __future__ import annotations

import io
import json
import shutil
from types import SimpleNamespace

import anyio
import pytest
from sqlglot import exp, parse_one
from fastapi import HTTPException
from mcp.server.fastmcp.exceptions import ToolError

from kge.adapters.warehouse.bigquery import BigQueryWarehouses
from kge.adapters.warehouse.duckdb import DuckDBEstate, Warehouses
from kge.pipeline import trace
from kge.pipeline.landed_tables import write_landed_tables
from kge.landing.zone import Zone
from kge.pipeline.sqlguard import Allowlist
from kge.semantics.query import Context, Join, Rule, Semantics
from kge.services.ask.engine import runtrace
from kge.services.data import mcp, routes, service
from tests.bigquery_fake import FakeBigQuery


SCHEMA = {"facts": {"id", "amount", "is_current", "period"}, "excluded": {"id"},
          "month_end": {"period", "released", "release_date", "freeze_date"}, "unlanded": {"id"}, "aum": {"id", "period", "quarter_end", "amount"}}
DDL = ["CREATE TABLE facts(id INTEGER, amount INTEGER, is_current BOOLEAN, period INTEGER)",
       "INSERT INTO facts VALUES (1, 10, true, 1), (2, 900, true, 1), (3, 800, false, 1), (4, 700, true, 2)",
       "CREATE TABLE excluded(id INTEGER)", "INSERT INTO excluded VALUES (2)",
       "CREATE TABLE month_end(period INTEGER, released BOOLEAN, release_date DATE, freeze_date DATE)",
       "INSERT INTO month_end VALUES (1, true, DATE '2026-01-01', DATE '2026-01-01'), (2, false, NULL, NULL)",
       "CREATE TABLE unlanded(id INTEGER)", "INSERT INTO unlanded VALUES (99)",
       "CREATE TABLE aum(id INTEGER, period INTEGER, quarter_end DATE, amount INTEGER)",
       "INSERT INTO aum VALUES (1, 1, DATE '2025-03-31', 10), (2, 2, DATE '2025-06-30', 900)"]


@pytest.fixture(params=["duckdb", "bigquery"])
def governed(request, monkeypatch, tmp_path):
    if request.param == "duckdb":
        estate = DuckDBEstate(tmp_path / "warehouse")
        with estate.stage("unit", DDL):
            pass
        estate.publish("unit")
        warehouse = Warehouses(tmp_path / "warehouse", ["unit"])
    else:
        warehouse = BigQueryWarehouses("unused", ["unit"], client="fixture", project="fixture")
        monkeypatch.setattr(warehouse, "token", lambda: ("offline", "fixture"))
        fake = FakeBigQuery()
        for sql in DDL:
            tree = parse_one(sql, read="duckdb")
            for table in tree.find_all(exp.Table):
                table.set("this", exp.to_identifier(f"unit_{table.name}"))
            fake.run(None, tree.sql("bigquery"))
        answers = {}

        def urlopen(req, timeout):
            assert 0 < timeout <= 5
            body = json.loads(req.data) if req.data else {}
            if req.method == "GET" and "/datasets/" in req.full_url:
                result = {"location": "local"}
            elif req.method == "POST" and req.full_url.endswith("/jobs"):
                answer = fake.run(None, body["configuration"]["query"]["query"])
                answers[body["jobReference"]["jobId"]] = answer
                result = {"jobReference": body["jobReference"]}
            elif req.method == "GET":
                job = req.full_url.split("/queries/")[1].split("?")[0]
                result = answers[job]
            else:
                assert body["dryRun"]
                result = fake.run(None, body["query"], dry_run=True)
            return io.BytesIO(json.dumps(result).encode())

        monkeypatch.setattr("urllib.request.urlopen", urlopen)
    sem = Semantics.__new__(Semantics)
    sem.d = SimpleNamespace(RESTRICTED_COLUMNS=[], PERIODS={}, GROUP_BY={}, ACRONYMS={})
    sem.tables = {f"unit.{t}": {c: "INTEGER" for c in cols} for t, cols in SCHEMA.items()}
    sem.scd2 = {"unit.facts": {"is_current": "is_current", "valid_from": "period", "valid_to": "period"}}
    sem.releases = {"unit.month_end": {"table": "unit.month_end", "period": "period", "released": "released",
                                   "release_date": "release_date", "freeze_date": "freeze_date"}}
    join = Join("calendar", "calendar_rollup", "facts.period = month_end.period", None, None, "unit.facts", "unit.month_end", True)
    aum_join = Join("aum_calendar", "calendar_rollup", "aum.period = month_end.period", None, None, "unit.aum", "unit.month_end", True)
    sem.declared = {"calendar": join, "aum_calendar": aum_join}
    selector = Rule("excluded", "Excluded", "excluded.id = facts.id", "unit.excluded", "exclude")
    sem.contexts = {"measure": Context("measure", "Measure", "", "measure", "measure", "sum(facts.amount)", "sum", "day",
                                      "unit.facts", ("unit.facts.amount",), (), (selector,), (join,))}
    sem.entities, sem.cites = {}, {}
    landed = Zone(tmp_path / "landing", "fixture").land_files(
        {"structured/unit.json": json.dumps({"source": "unit", "tables": [{"name": t} for t in SCHEMA if t != "unlanded"]}).encode()}, {})
    write_landed_tables(tmp_path, landed)
    shutil.rmtree(tmp_path / "landing")
    graph = SimpleNamespace(profile=SimpleNamespace(id="fixture", client="fixture", landing=tmp_path / "landing", run_dir=lambda: tmp_path,
                                                   graph_path=lambda: tmp_path / "graph"),
                            release=lambda: landed.about())
    graph.q = lambda sql, params: [{"tbl": name} for name in SCHEMA if name != "unlanded"]
    engine = SimpleNamespace(g=graph, w=warehouse, semantics=sem, UNIT_ORDER=["unit"], RESTRICTED_COLUMNS=[])
    monkeypatch.setattr(runtrace, "fingerprint", lambda _graph: {})
    monkeypatch.setattr(service.identity, "readable_units", lambda _engine, who: ["unit"] if who == "reader" else [])
    monkeypatch.setattr(service, "schema_allowlist", lambda *_: Allowlist("unit", set(SCHEMA), {k: set(v) for k, v in SCHEMA.items()},
                                                                 schema=SCHEMA))
    yield engine
    warehouse.close()
    if request.param == "bigquery":
        fake.con.close()


@pytest.mark.parametrize("kind", ["preview", "export", "query"])
def test_every_read_applies_selectors_time_policy_and_records_rows(governed, kind):
    argument = "SELECT id, amount FROM facts ORDER BY id" if kind == "query" else "facts"
    result = getattr(service, kind)(governed, "reader", "unit", argument)
    assert [row["id"] for row in result.rows] == [1]
    record = {"principal": "reader", "unit": "unit", "tables": ["facts"], "kind": kind, "row_count": 1, "refused": False}
    assert result.run_trace["data_reads"] == [record]
    saved = trace.read_jsonl(governed.g.profile.run_dir() / "data-reads.jsonl")
    assert len(saved) == 1 and saved[0].items() >= record.items()


@pytest.mark.parametrize("sql", [
    "SELECT sum(amount) AS total FROM facts WHERE id > 0 OR TRUE",
    "WITH mine AS (SELECT f.amount FROM facts f) SELECT sum(amount) AS total FROM mine",
    "SELECT sum(amount) AS total FROM (SELECT amount FROM facts) nested",
    "SELECT sum(amount) AS total FROM facts WHERE id NOT IN (SELECT id FROM facts WHERE id = 2)",
    "WITH _kge_read_0 AS (SELECT 800 AS amount) SELECT sum(amount) AS total FROM facts",
])
def test_caller_sql_cannot_bypass_governance_before_aggregation(governed, sql):
    result = service.query(governed, "reader", "unit", sql)
    assert result.rows == [{"total": 10}]


@pytest.mark.parametrize("kind", ["preview", "export", "query"])
def test_unlanded_reads_have_identical_transport_refusals_and_a_trace(governed, monkeypatch, kind):
    monkeypatch.setattr(routes, "served", SimpleNamespace(engine=lambda: governed))
    monkeypatch.setattr(mcp, "served", SimpleNamespace(engine=lambda: governed))
    monkeypatch.setattr(mcp, "mcp_principal", lambda _: "reader")
    monkeypatch.setattr(mcp, "may", lambda who, _: who)
    detail = "The current release has not landed table(s): unlanded."
    args = {"unit": "unit", **({"sql": "SELECT id FROM unlanded"} if kind == "query" else {"table": "unlanded"})}
    body = {"preview": routes.ProductRows, "export": routes.ProductExport, "query": routes.OwnQuery}[kind](**args)
    with pytest.raises(HTTPException) as http:
        getattr(routes, kind)(body, "reader")
    assert (http.value.status_code, http.value.detail) == (403, detail)
    fn = mcp.mcp_server()._tool_manager._tools[f"{kind}_data"].fn
    ctx = SimpleNamespace(request_context=SimpleNamespace(request=None))
    with pytest.raises(ToolError) as tool:
        anyio.run(lambda: fn(**args, ctx=ctx))
    assert str(tool.value) == f"403: {detail}"
    records = trace.read_jsonl(governed.g.profile.run_dir() / "data-reads.jsonl")
    assert len(records) == 2
    assert all(r["refused"] and r["row_count"] == 0 and r["tables"] == ["unlanded"] and r["kind"] == kind for r in records)


@pytest.mark.parametrize("sql", ["DELETE FROM facts", "SELECT * FROM facts", "SELECT id FROM facts; SELECT id FROM excluded",
                               "WITH RECURSIVE n AS (SELECT 1) SELECT 1", "SELECT id FROM another.facts",
                               "SELECT f.id FROM facts f WHERE EXISTS (SELECT 1 FROM facts g WHERE g.id = f.id)"])
def test_unsafe_queries_are_refused_and_traced(governed, sql):
    with pytest.raises(service.DataRefused) as refused:
        service.query(governed, "reader", "unit", sql)
    assert refused.value.status == 400
    assert refused.value.run_trace["data_reads"][0]["refused"]
    assert len(trace.read_jsonl(governed.g.profile.run_dir() / "data-reads.jsonl")) == 1


@pytest.mark.parametrize("kind", ["preview", "export", "query"])
def test_mcp_returns_the_same_governed_rows(governed, monkeypatch, kind):
    monkeypatch.setattr(mcp, "served", SimpleNamespace(engine=lambda: governed))
    monkeypatch.setattr(mcp, "mcp_principal", lambda _: "reader")
    monkeypatch.setattr(mcp, "may", lambda who, _: who)
    args = {"unit": "unit", **({"sql": "SELECT id FROM facts"} if kind == "query" else {"table": "facts"})}
    fn = mcp.mcp_server()._tool_manager._tools[f"{kind}_data"].fn
    result = anyio.run(lambda: fn(**args, ctx=SimpleNamespace(request_context=SimpleNamespace(request=None))))
    assert [row["id"] for row in result.rows] == [1]
    assert result.run_trace["data_reads"][0]["principal"] == "reader"
    assert len(trace.read_jsonl(governed.g.profile.run_dir() / "data-reads.jsonl")) == 1


def test_landed_table_membership_does_not_require_mappings_or_contexts(governed):
    governed.g.q = lambda sql, params: [{"tbl": "facts"}] if "CERTIFIED_BY" in sql else []
    assert service.preview(governed, "reader", "unit", "facts").row_count == 1
    governed.g.q = lambda *_: []
    assert service.preview(governed, "reader", "unit", "facts").row_count == 1
    governed.g.release = lambda: None
    with pytest.raises(service.DataRefused, match="this release predates the landed-tables record; release again"):
        service.preview(governed, "reader", "unit", "facts")


def test_include_selectors_are_applied_on_every_read(governed):
    context = governed.semantics.contexts["measure"]
    object.__setattr__(context, "selectors", (Rule("included", "Included", "excluded.id = facts.id", "unit.excluded", "include"),))
    for kind in ("preview", "export", "query"):
        argument = "SELECT id FROM facts" if kind == "query" else "facts"
        result = getattr(service, kind)(governed, "reader", "unit", argument)
        assert [row["id"] for row in result.rows] == [2]


def test_selector_that_needs_an_unavailable_table_is_refused_on_both_transports(governed, monkeypatch):
    context = governed.semantics.contexts["measure"]
    object.__setattr__(context, "selectors", (Rule("complex", "Complex", "excluded.id = facts.id AND excluded.id = another.id", "unit.excluded", "exclude"),))
    monkeypatch.setattr(routes, "served", SimpleNamespace(engine=lambda: governed))
    monkeypatch.setattr(mcp, "served", SimpleNamespace(engine=lambda: governed))
    monkeypatch.setattr(mcp, "mcp_principal", lambda _: "reader")
    monkeypatch.setattr(mcp, "may", lambda who, _: who)
    body = routes.OwnQuery(unit="unit", sql="SELECT id FROM facts")
    with pytest.raises(HTTPException) as http:
        routes.query(body, "reader")
    assert http.value.status_code == 400 and "cannot be safely applied" in http.value.detail
    fn = mcp.mcp_server()._tool_manager._tools["query_data"].fn
    with pytest.raises(ToolError) as tool:
        anyio.run(lambda: fn(unit="unit", sql=body.sql, ctx=SimpleNamespace(request_context=SimpleNamespace(request=None))))
    assert str(tool.value) == f"400: {http.value.detail}"


def test_a_hidden_unlanded_table_in_a_cte_is_refused_and_all_tables_are_traced(governed):
    with pytest.raises(service.DataRefused) as caught:
        service.query(governed, "reader", "unit", "WITH hidden AS (SELECT id FROM unlanded) SELECT id FROM facts UNION ALL SELECT id FROM hidden")
    record = caught.value.run_trace["data_reads"][0]
    assert record["tables"] == ["facts", "hidden", "unlanded"] and record["refused"]


@pytest.mark.parametrize("kind", ["preview", "export", "query"])
def test_release_driver_direct_reads_hide_unreleased_periods(governed, kind):
    argument = "SELECT period FROM month_end ORDER BY period" if kind == "query" else "month_end"
    result = getattr(service, kind)(governed, "reader", "unit", argument)
    assert [row["period"] for row in result.rows] == [1]


def test_outer_table_selector_applies_only_inside_its_context_unit(governed):
    context = governed.semantics.contexts["measure"]
    object.__setattr__(context, "fact", "unit.month_end")
    assert service.query(governed, "reader", "unit", "SELECT id FROM facts").rows == [{"id": 1}]
    object.__setattr__(context, "fact", "another.month_end")
    assert service.query(governed, "reader", "unit", "SELECT id FROM facts ORDER BY id").rows == [{"id": 1}, {"id": 2}]


def test_constrain_discovers_physical_sources_without_a_prescan(governed):
    from kge.services.data.engine.governance import constrain
    tables = set()
    with pytest.raises(PermissionError, match="unlanded"):
        constrain(governed, "unit", "WITH nested AS (SELECT id FROM unlanded) SELECT id FROM nested",
                  service.schema_allowlist(None, None, None), governed.w.sql_dialect, tables)
    assert tables == {"unlanded"}


def test_shadowed_references_remain_in_a_refusal_trace(governed):
    with pytest.raises(service.DataRefused, match="may not borrow") as caught:
        service.query(governed, "reader", "unit", "WITH facts AS (SELECT id FROM excluded) SELECT id FROM facts")
    assert caught.value.run_trace["data_reads"][0]["tables"] == ["excluded", "facts"]


@pytest.mark.parametrize("sql, names", [
    ("SELECT id FROM facts; SELECT id FROM excluded", ["excluded", "facts"]),
    ("SELECT f.id FROM main.facts f", ["main.facts"]),
])
def test_refusal_trace_names_every_statement_and_preserves_qualification(governed, sql, names):
    with pytest.raises(service.DataRefused) as caught:
        service.query(governed, "reader", "unit", sql)
    assert caught.value.run_trace["data_reads"][0]["tables"] == names


def test_a_new_load_does_not_certify_tables_for_the_served_release(governed):
    profile = governed.g.profile
    candidate = Zone(profile.landing, profile.client).land_files(
        {"structured/unit.json": json.dumps({"source": "unit", "tables": [{"name": "unlanded"}]}).encode()}, {})
    write_landed_tables(profile.graph_path().parent, candidate)
    assert service.preview(governed, "reader", "unit", "facts").row_count == 1
    with pytest.raises(service.DataRefused, match="not landed"):
        service.preview(governed, "reader", "unit", "unlanded")


@pytest.mark.parametrize("kind", ["preview", "export", "query"])
def test_an_unreleased_aum_quarter_is_never_returned(governed, kind):
    argument = "SELECT id, amount FROM aum ORDER BY id" if kind == "query" else "aum"
    result = getattr(service, kind)(governed, "reader", "unit", argument)
    assert [(row["id"], row["amount"]) for row in result.rows] == [(1, 10)]


@pytest.mark.parametrize("catalogue", [False, True])
@pytest.mark.parametrize("condition", [
    "f.period = m.period OR 1=1", "(f.period = m.period OR TRUE)",
    "f.period = m.period AND 1=1", "TRUE", "1=1",
    "f.period = m.period AND f.id > 0", "NOT (f.period <> m.period)",
    "f.period + 0 = m.period",
])
def test_on_requires_column_equalities_for_catalogue_and_declared_joins(governed, monkeypatch, catalogue, condition):
    if catalogue:
        original = service.schema_allowlist
        def catalogue_allow(*args):
            allow = original(*args)
            allow.joins.add(frozenset({("facts", "period"), ("month_end", "period")}))
            return allow
        monkeypatch.setattr(service, "schema_allowlist", catalogue_allow)
        governed.semantics.declared = {}
    with pytest.raises(service.DataRefused, match="conjunction .* of column equalities") as refused:
        service.query(governed, "reader", "unit", f"SELECT f.id FROM facts f JOIN month_end m ON {condition}")
    assert refused.value.status == 400


def test_declared_aliased_self_join_is_accepted(governed):
    governed.semantics.declared["parent"] = Join("parent", "hierarchy", "facts.period = parent.id", "parent", None,
                                                "unit.facts", "unit.facts", True)
    result = service.query(governed, "reader", "unit", "SELECT c.id FROM facts c JOIN facts p ON c.period = p.id")
    assert result.rows == [{"id": 1}]


def test_pack_segment_parent_is_in_declared_allowlist():
    import yaml
    from kge.pipeline.settings import ROOT
    from kge.pipeline.sqlguard import check
    from kge.services.data.engine.governance import declared_allowlist
    raw = yaml.safe_load((ROOT / "clients/meridian/pack.yaml").read_text())
    def joins(value):
        if isinstance(value, dict):
            if value.get("id") == "segment_parent":
                yield value
            for child in value.values():
                yield from joins(child)
        elif isinstance(value, list):
            for child in value:
                yield from joins(child)
    spec = next(joins(raw))
    sem = Semantics.__new__(Semantics)
    sem.d = SimpleNamespace(RESTRICTED_COLUMNS=[], PERIODS={}, GROUP_BY={}, ACRONYMS={})
    columns = {"SEGMT_CD", "PARNT_SEGMT_CD"}
    sem.tables = {"wealth.CLNT_SEGMT_DIM_1": dict.fromkeys(columns, "VARCHAR")}
    sem.scd2, sem.entities, sem.cites = {}, {}, {}
    sem.declared = {spec["id"]: Join(spec["id"], spec["kind"], spec["condition"], spec["alias"], None,
                                      spec["from"], spec["to"], True)}
    allow = declared_allowlist(SimpleNamespace(semantics=sem), "wealth",
                              Allowlist("wealth", {"CLNT_SEGMT_DIM_1"}, {"CLNT_SEGMT_DIM_1": columns}, schema={"CLNT_SEGMT_DIM_1": columns}))
    assert not check("SELECT c.SEGMT_CD FROM CLNT_SEGMT_DIM_1 c JOIN CLNT_SEGMT_DIM_1 p ON c.PARNT_SEGMT_CD = p.SEGMT_CD",
                     allow, inner_only=True)
    assert not check("SELECT c.segmt_cd FROM clnt_segmt_dim_1 c JOIN clnt_segmt_dim_1 p ON c.parnt_segmt_cd = p.segmt_cd",
                     allow, inner_only=True)
    assert check("SELECT c.SEGMT_CD FROM CLNT_SEGMT_DIM_1 c JOIN CLNT_SEGMT_DIM_1 p ON c.SEGMT_CD = p.SEGMT_CD",
                 allow, inner_only=True)
