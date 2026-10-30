from __future__ import annotations

import anyio
import pytest

from kge.pipeline.sqlguard import Allowlist
from kge.services.data import mcp, routes, service
from kge.services.data.models import ExportResponse, RowsResponse


def Allow():
    return Allowlist("retail", {"accounts"}, {"accounts": {"id", "amount"}}, schema={"accounts": {"id", "amount", "secret"}})


class Warehouses:
    sql_dialect = "duckdb"

    def __init__(self):
        self.calls = []

    def gate(self, unit, sql, allow, dialect, limits):
        self.calls.append(("gate", unit, sql, dialect))
        lowered = sql.lower()
        problems = []
        if "delete" in lowered:
            problems = ["read-only: SELECT or WITH ... SELECT, nothing that writes"]
        elif "secret" in lowered:
            problems = ["column accounts.secret is not certified for this question"]
        elif "customers" in lowered:
            problems = ["table customers is not in the retail warehouse"]
        return {"passed": not problems, "problems": problems}

    def query(self, unit, sql, dialect, limits):
        assert 0 < limits.seconds <= 5 and limits.scan_bytes == 104_857_600 and limits.memory_bytes == 268_435_456
        self.calls.append(("query", unit, sql, dialect))
        return ["id", "amount"], [(1, 20), (2, 30)]


class Engine:
    UNIT_ORDER = ["retail", "wealth"]
    RESTRICTED_COLUMNS = []
    g = object()

    def __init__(self):
        self.w = Warehouses()


@pytest.fixture
def data(monkeypatch):
    engine = Engine()
    monkeypatch.setattr(service.identity, "readable_units", lambda _engine, who: ["retail"] if who == "analyst" else [])
    monkeypatch.setattr(service, "schema_allowlist", lambda *_: Allow())
    monkeypatch.setattr(service, "traced", lambda _path, call, graph=None: {**call(), "run_trace": {"path": _path}})
    monkeypatch.setattr(service, "governed", lambda _engine, _unit, sql, allow, _dialect, _tables: (sql, allow))
    return engine


def test_preview_export_and_caps_use_the_same_gated_warehouse_seam(data, monkeypatch):
    preview = service.preview(data, "analyst", "retail", "accounts")
    assert preview.columns == ["id", "amount"] and preview.row_count == 2 and preview.capped_at == 20
    assert data.w.calls[0][0] == "gate" and data.w.calls[1][0] == "query"
    with pytest.raises(service.DataRefused) as over:
        service.preview(data, "analyst", "retail", "accounts", 101)
    assert over.value.status == 422
    monkeypatch.setenv("DATA_EXPORT_ROW_CAP", "1")
    exported = service.export(data, "analyst", "retail", "accounts")
    assert exported.row_count == 1 and exported.filename == "retail-accounts.csv"
    queried = service.query(data, "analyst", "retail", "SELECT accounts.id FROM accounts")
    assert queried.capped_at == service.QUERY_ROW_CAP
    assert f"LIMIT {service.QUERY_ROW_CAP}" in data.w.calls[-2][2].upper()


def test_other_unit_and_outside_table_or_column_are_refused_before_a_read(data):
    for unit, expected in (("wealth", 404), ("missing", 404)):
        with pytest.raises(service.DataRefused) as hidden:
            service.preview(data, "analyst", unit, "accounts")
        assert hidden.value.status == expected
    with pytest.raises(service.DataRefused) as product:
        service.preview(data, "analyst", "retail", "customers")
    assert product.value.status == 404
    for sql, phrase in (("DELETE FROM accounts", "read-only"),
                        ("SELECT accounts.secret FROM accounts", "not certified"),
                        ("SELECT customers.id FROM customers", "not in the retail warehouse")):
        with pytest.raises(service.DataRefused) as refused:
            service.query(data, "analyst", "retail", sql)
        assert refused.value.status == 400 and phrase in refused.value.detail
    assert all(call[0] != "query" for call in data.w.calls)


def test_csv_shape_and_mcp_use_the_same_service_function_and_error_mapping(data, monkeypatch):
    exported = ExportResponse(columns=["id", "amount"], rows=[{"id": 1, "amount": 20}], row_count=1, capped_at=10,
                              filename="retail-accounts.csv", run_trace={})
    monkeypatch.setattr(routes, "service_call", lambda *_: exported)
    response = routes.export(routes.ProductExport(unit="retail", table="accounts"), "analyst")
    assert response.media_type == "text/csv" and response.body == b"id,amount\n1,20\n"
    assert "x-export-capped-at" not in response.headers
    assert "x-export-note" not in response.headers
    assert response.headers["content-disposition"] == 'attachment; filename="retail-accounts.csv"'

    expected = RowsResponse(columns=["id"], rows=[{"id": 1}], row_count=1, capped_at=1000, run_trace={})
    calls = []
    monkeypatch.setattr(mcp, "mcp_principal", lambda _request: "analyst")
    monkeypatch.setattr(mcp, "may", lambda who, _role: who)
    monkeypatch.setattr(mcp, "served", type("Served", (), {"engine": lambda self: data})())
    monkeypatch.setattr(service, "query", lambda *args: calls.append(args) or expected)
    registered = mcp.mcp_server()._tool_manager._tools["query_data"].fn
    ctx = type("Context", (), {"request_context": type("RequestContext", (), {"request": object()})()})()
    assert anyio.run(lambda: registered(unit="retail", sql="SELECT id FROM accounts", ctx=ctx)) == expected
    assert calls[0][1:] == ("analyst", "retail", "SELECT id FROM accounts")

    def refused(*_args):
        raise service.DataRefused(400, "read-only")

    monkeypatch.setattr(service, "query", refused)
    with pytest.raises(Exception, match="400: read-only"):
        anyio.run(lambda: registered(unit="retail", sql="DELETE FROM accounts", ctx=ctx))


@pytest.mark.parametrize("sql", [
    "SELECT id FROM accounts WHERE " + " OR ".join(f"id = {i}" for i in range(30000)),
    " UNION ALL ".join("SELECT 1" for _ in range(3000)),
    "SELECT " + "(" * 200 + "1" + ")" * 200,
])
def test_adversarial_complexity_is_a_fast_transport_refusal(data, monkeypatch, sql):
    import time
    from fastapi import HTTPException
    started = time.monotonic()
    monkeypatch.setattr(routes.served, "engine", lambda: data)
    with pytest.raises(HTTPException) as http:
        routes.query(routes.OwnQuery(unit="retail", sql=sql), "analyst")
    assert http.value.status_code == 400
    monkeypatch.setattr(mcp, "mcp_principal", lambda _: "analyst")
    monkeypatch.setattr(mcp, "may", lambda who, _: who)
    registered = mcp.mcp_server()._tool_manager._tools["query_data"].fn
    ctx = type("Context", (), {"request_context": type("RequestContext", (), {"request": object()})()})()
    with pytest.raises(Exception, match="400: " + http.value.detail.split(" (")[0]):
        anyio.run(lambda: registered(unit="retail", sql=sql, ctx=ctx))
    assert time.monotonic() - started < 1
    assert data.w.calls == []
    assert service.query(data, "analyst", "retail", "SELECT id FROM accounts").row_count == 2


def test_analysis_is_interrupted_inside_the_request_deadline(data, monkeypatch):
    import time
    original = service.time.monotonic
    monkeypatch.setattr(service, "QueryLimits", lambda **kw: __import__("kge.ports", fromlist=["QueryLimits"]).QueryLimits(seconds=0.05, **kw))
    checked = service.check
    def expensive(*args, **kwargs):
        while True:
            checked(*args, **kwargs)
    monkeypatch.setattr(service, "check", expensive)
    started = original()
    with pytest.raises(service.DataRefused, match="time limit"):
        service.query(data, "analyst", "retail", "SELECT id FROM accounts")
    assert time.monotonic() - started < 0.5
    assert data.w.calls == []


@pytest.mark.parametrize("count", [0, 1, 9, 10])
def test_export_warns_only_at_the_cap(monkeypatch, count):
    result = ExportResponse(columns=["id"], rows=[{"id": i} for i in range(count)], row_count=count,
                            capped_at=10, filename="rows.csv", run_trace={})
    monkeypatch.setattr(routes, "service_call", lambda *_: result)
    response = routes.export(routes.ProductExport(unit="retail", table="accounts"), "analyst")
    assert ("x-export-capped-at" in response.headers) == (count == 10)
    assert ("x-export-note" in response.headers) == (count == 10)
