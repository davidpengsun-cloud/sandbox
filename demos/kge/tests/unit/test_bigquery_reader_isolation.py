"""Published catalogue boundaries and completed REST answers on the warehouse reader."""
import datetime as dt
import io
import json
import re
import time

import pytest

from kge.adapters.warehouse.bigquery import BigQueryWarehouses
from kge.ports import WarehouseRefused


def answer(names, rows, kinds=None, **extra):
    return {"jobComplete": True,
            "schema": {"fields": [{"name": name, "type": kind} for name, kind in
                                   zip(names, kinds or ["STRING"] * len(names))]},
            "rows": [{"f": [{"v": value} for value in row]} for row in rows], **extra}


def catalogue(*tables):
    return answer(["tables"], [(json.dumps([{"name": table} for table in tables]),)])


def reader(monkeypatch, versions=None):
    warehouses = BigQueryWarehouses("unused", ["retail", "retail_x"], client="fixture", project="fixture",
                                    versions=versions)
    monkeypatch.setattr(warehouses, "token", lambda: ("offline", "fixture"))
    return warehouses


@pytest.mark.parametrize("unit,version", [("retail", 0), ("retail", 1), ("retail_x", 0)])
def test_every_metadata_read_stays_in_the_exact_declared_unit_version(monkeypatch, unit, version):
    warehouses = reader(monkeypatch, {unit: version})
    prefixes = ["retail", "retail_v1", "retail_x"]
    sent = []

    def query(sql, **kwargs):
        sent.append(sql)
        if "_kge_catalogue" in sql:
            match = re.search(r"WHERE unit = (.+) AND version = (\d+)", sql)
            assert json.loads(match[1]) == unit and int(match[2]) == version
            return catalogue("parent", "child")
        if "__TABLES__" in sql:
            return answer(["table_id", "row_count", "size_bytes"],
                          [(f"{p}_parent", str(n + 1), str(n + 10)) for n, p in enumerate(prefixes)])
        if "FOREIGN KEY" in sql:
            return answer(["table_name", "constraint_name", "column_name", "position", "reference"],
                          [(f"{p}_child", f"{p}_fk", "parent_id", "1", f"{p}_parent") for p in prefixes]
                          + [(f"{warehouses.prefix(unit)}_child", "foreign_scope", "parent_id", "1", "unpublished_parent")])
        if "PRIMARY KEY" in sql:
            return answer(["table_name", "column_name"], [(f"{p}_parent", "id") for p in prefixes])
        if ".COLUMNS`" in sql:
            return answer(["table_name", "column_name", "data_type"],
                          [(f"{p}_parent", "id", "INT64") for p in prefixes])
        return answer(["table_name"], [(f"{p}_{table}",) for p in prefixes for table in ("parent", "child")])

    monkeypatch.setattr(warehouses, "_query", query)
    assert warehouses.columns(unit) == [("parent", "id", "BIGINT")]
    assert warehouses.catalogue_columns(unit) == [("parent", "id", "INT64")]
    assert warehouses.calendar_tables(unit) == {"parent", "child"}
    assert warehouses.primary_keys(unit) == [("parent", ["id"])]
    assert warehouses.foreign_keys(unit) == [("child", ["parent_id"], "parent", ["id"])]
    position = prefixes.index(warehouses.prefix(unit))
    assert warehouses.table_facts(unit, "parent") == {"records": position + 1, "storage": position + 10}
    with pytest.raises(WarehouseRefused, match="no such table"):
        warehouses.table_facts(unit, "v1_parent" if version == 0 else "x_parent")
    assert sum("_kge_catalogue" in sql for sql in sent) == 1
    assert all("STARTS_WITH" not in sql and "CREATE" not in sql for sql in sent)
    for sql in sent[1:]:
        assert f'"{warehouses.prefix(unit)}_parent"' in sql and " IN (" in sql


@pytest.mark.parametrize("value", [[], [("not-json",)], [(json.dumps({"name": "parent"}),)],
                                    [(json.dumps([{"name": "parent"}, {"name": "parent"}]),)]])
def test_missing_or_invalid_catalogue_refuses_without_scanning_or_caching(monkeypatch, value):
    warehouses = reader(monkeypatch)
    calls = []
    monkeypatch.setattr(warehouses, "_query", lambda sql: calls.append(sql) or answer(["tables"], value))
    with pytest.raises(WarehouseRefused, match="catalogue"):
        warehouses.columns("retail")
    assert len(calls) == 1 and "_kge_catalogue" in calls[0]
    assert not warehouses._columns and not warehouses._declared_tables


def transport(monkeypatch, responses, on_get=None):
    sent = []

    def open_request(request, timeout):
        sent.append((request.method, request.full_url, json.loads(request.data) if request.data else None, timeout))
        if request.method == "GET" and on_get:
            on_get()
        return io.BytesIO(json.dumps(responses.pop(0)).encode())

    monkeypatch.setattr("urllib.request.urlopen", open_request)
    return sent


def test_columns_wait_for_completion_and_keep_the_first_completed_page_schema(monkeypatch):
    warehouses = reader(monkeypatch)
    sent = transport(monkeypatch, [catalogue("parent"),
        answer(["unfinished"], [("must not become data",)], jobComplete=False, jobReference={"jobId": "columns"}),
        answer(["table_name", "column_name", "data_type"], [("retail_parent", "id", "INT64")], pageToken="second"),
        {"jobComplete": True, "rows": [{"f": [{"v": "retail_parent"}, {"v": "opened"}, {"v": "DATE"}]}]},
    ])
    assert warehouses.columns("retail") == [("parent", "id", "BIGINT"), ("parent", "opened", "DATE")]
    assert [method for method, *_ in sent] == ["POST", "POST", "GET", "GET"]
    assert "pageToken=second" in sent[-1][1]
    assert warehouses.columns("retail") and len(sent) == 4


@pytest.mark.parametrize("failure", ["cancelled", "unfinished"])
def test_an_unsuccessful_catalogue_answer_never_becomes_a_cached_empty_schema(monkeypatch, failure):
    warehouses = reader(monkeypatch)
    now = [0.0]
    monkeypatch.setattr(time, "monotonic", lambda: now[0])
    monkeypatch.setenv("BIGQUERY_JOB_TIMEOUT", "1")
    response = {"jobComplete": False, "jobReference": {"jobId": "catalogue"}}
    if failure == "cancelled":
        response["status"] = {"state": "DONE", "errorResult": {"reason": "stopped", "message": "cancelled"}}
    transport(monkeypatch, [response, response], on_get=lambda: now.__setitem__(0, 2.0))
    with pytest.raises((RuntimeError, TimeoutError)):
        warehouses.columns("retail")
    assert not warehouses._columns and not warehouses._declared_tables
    transport(monkeypatch, [catalogue("parent"),
                           answer(["table_name", "column_name", "data_type"], [("retail_parent", "id", "INT64")])])
    assert warehouses.columns("retail") == [("parent", "id", "BIGINT")]


def test_shared_query_format_keeps_reader_numeric_and_timestamp_values(monkeypatch):
    warehouses = reader(monkeypatch)
    transport(monkeypatch, [answer(["amount", "created"], [("1.25", "1000000")], ["NUMERIC", "TIMESTAMP"])])
    assert warehouses.row("retail", "SELECT 1") == (1.25, dt.datetime(1970, 1, 1, 0, 0, 1))


def test_reader_uses_schema_arriving_only_on_the_final_result_page(monkeypatch):
    warehouses = reader(monkeypatch)
    transport(monkeypatch, [catalogue("parent"),
        {"jobComplete": True, "jobReference": {"jobId": "columns"}, "pageToken": "last",
         "rows": [{"f": [{"v": "retail_parent"}, {"v": "id"}, {"v": "INT64"}]}]},
        answer(["table_name", "column_name", "data_type"], [("retail_parent", "opened", "DATE")]),
    ])
    assert warehouses.columns("retail") == [("parent", "id", "BIGINT"), ("parent", "opened", "DATE")]
