"""Request deadlines on the existing offline BigQuery REST stand-ins."""

import json
import time
from threading import Event
from urllib.parse import parse_qs, urlsplit

import pytest

from kge.adapters.bigquery import BigQueryLoader
from kge.adapters.deadline import RequestDeadlineExceeded, request_budget
from kge.adapters.graphstore.bigquery import Client
from kge.adapters.graphstore.ladybug import LadybugStore
from kge.adapters.warehouse.bigquery import BigQueryWarehouses
from kge.adapters.warehouse.duckdb import RequestCursor
from kge.ports import QueryLimits
from tests.unit.test_bigquery import JsonResponse
from tests.unit.test_data_limits import bq
from tests.unit.test_graphstore_bigquery import Rest, client


@pytest.fixture
def clock(monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(time, "monotonic", lambda: now[0])
    monkeypatch.setenv("MODEL_REQUEST_TIMEOUT", "3")
    return now


@pytest.mark.parametrize("budget", [True, False])
def test_graph_poll_and_pages_take_only_request_time_left(monkeypatch, clock, budget):
    rest = Rest({"jobComplete": False, "jobReference": {"jobId": "slow"}},
                {"jobComplete": True, "pageToken": "next"}, {"jobComplete": True})
    graph = client(monkeypatch, rest)
    original = rest.get

    def poll(url):
        clock[0] += 2
        return original(url)

    monkeypatch.setattr(rest, "get", poll)
    def call():
        return graph.query("SELECT 1", [])
    if budget:
        with pytest.raises(RequestDeadlineExceeded):
            request_budget(call)()
        assert rest.sent[0][1]["jobTimeoutMs"] == "3000"
        assert [int(parse_qs(urlsplit(url).query)["timeoutMs"][0]) for url, body in rest.sent[1:]] == [3000, 1000]
    else:
        call()
        assert "jobTimeoutMs" not in rest.sent[0][1]
        assert all("timeoutMs=60000" in url for url, body in rest.sent[1:])
    assert rest.sent[0][1]["timeoutMs"] == (3000 if budget else 60000)


def test_graph_keeps_600_second_poll_deadline_outside_request(monkeypatch, clock):
    rest = Rest({"jobComplete": False, "jobReference": {"jobId": "slow"}}, {"jobComplete": False}, {})
    graph = client(monkeypatch, rest)
    original = rest.get

    def poll(url):
        clock[0] += 601
        return original(url)

    monkeypatch.setattr(rest, "get", poll)
    with pytest.raises(TimeoutError, match="600 seconds"):
        graph.query("SELECT 1", [])
    assert len(rest.sent) == 3
    assert "/jobs/slow/cancel" in rest.sent[2][0]


@pytest.mark.parametrize("budget", [True, False])
@pytest.mark.parametrize("limits", [None, QueryLimits(seconds=10)])
def test_warehouse_post_keeps_own_limits_or_request_time(monkeypatch, clock, budget, limits):
    warehouse, calls = bq(monkeypatch, [{"jobComplete": True}])
    original = warehouse._post

    def post(*args, **kwargs):
        result = original(*args, **kwargs)
        clock[0] += 4
        return result

    monkeypatch.setattr(warehouse, "_post", post)
    def call():
        return warehouse._query("SELECT 1", dry_run=limits is not None, limits=limits)
    if budget:
        with pytest.raises(RequestDeadlineExceeded):
            request_budget(call)()
        assert calls[0][2]["timeoutMs"] == 3000
        assert calls[0][2]["jobTimeoutMs"] == "3000"
        assert calls[0][3] == 3
    else:
        call()
        assert calls[0][3] == (10 if limits else 60)
        assert calls[0][2].get("jobTimeoutMs") == ("10000" if limits else None)


@pytest.mark.parametrize("budget", [True, False])
def test_shared_loader_poll_limits(monkeypatch, clock, budget):
    loader = BigQueryLoader(project="fixture")
    monkeypatch.setattr(loader, "token", lambda: ("offline", "fixture"))
    calls = []

    def send(req, timeout):
        body = json.loads(req.data) if req.data else None
        calls.append((req.full_url, body, timeout))
        clock[0] += 2
        return JsonResponse({"jobComplete": len(calls) == 2, "jobReference": {"jobId": "slow"}})

    monkeypatch.setattr("kge.adapters.http.open_http", send)
    def call():
        return loader.run("SELECT 1")
    if budget:
        with pytest.raises(RequestDeadlineExceeded):
            request_budget(call)()
        assert calls[0][1]["jobTimeoutMs"] == "3000"
        assert calls[0][1]["timeoutMs"] == 3000
        assert calls[1][2] == 1
        assert "timeoutMs=1000" in calls[1][0]
    else:
        call()
        assert calls[0][1]["timeoutMs"] == 60000
        assert "jobTimeoutMs" not in calls[0][1]
        assert [call[2] for call in calls] == [120, 120]
        assert "timeoutMs=10000" in calls[1][0]


@pytest.mark.parametrize("kind", ["graph", "warehouse", "loader"])
def test_no_query_starts_below_floor(monkeypatch, kind):
    monkeypatch.setenv("MODEL_REQUEST_TIMEOUT", ".025")
    warehouse, calls = bq(monkeypatch, [])
    graph = Client("fixture")
    loader = BigQueryLoader(project="fixture")
    monkeypatch.setattr(loader, "token", lambda: ("offline", "fixture"))
    query = {"graph": lambda: graph.query("SELECT 1", []), "warehouse": lambda: warehouse._query("SELECT 1"),
             "loader": lambda: loader.run("SELECT 1")}[kind]
    with pytest.raises(RequestDeadlineExceeded):
        request_budget(query)()
    assert not calls


def test_limited_warehouse_uses_job_timeout_without_waiting_to_cancel_after_expiry(monkeypatch):
    monkeypatch.setenv("MODEL_REQUEST_TIMEOUT", ".15")
    warehouse, calls = bq(monkeypatch, [{"location": "EU"}, {"jobReference": {"jobId": "slow"}},
                                        {"jobComplete": False}] * 20)
    finished = Event()

    def send(req, timeout):
        calls.append((req.method, req.full_url, json.loads(req.data) if req.data else None, timeout))
        if "/datasets/" in req.full_url:
            return JsonResponse({"location": "EU"})
        if req.method == "POST":
            return JsonResponse({"jobReference": {"jobId": "slow"}})
        time.sleep(.4)
        finished.set()
        return JsonResponse({"jobComplete": True})

    monkeypatch.setattr("kge.adapters.http.open_http", send)
    started = time.monotonic()
    with pytest.raises(RequestDeadlineExceeded):
        request_budget(lambda: warehouse.bounded_query("SELECT 1", QueryLimits(seconds=10)))()
    assert time.monotonic() - started < .3
    assert 0 < int(calls[1][2]["configuration"]["jobTimeoutMs"]) <= 150
    assert not any("/cancel" in c[1] for c in calls)
    assert finished.wait(1)


def test_local_queries_check_each_execution(monkeypatch, clock):
    class Connection:
        def execute(self, *args):
            pytest.fail("local query started after expiry")

    cursor = RequestCursor(Connection())

    @request_budget
    def query():
        clock[0] += 2.98
        cursor.execute("SELECT 1")

    with pytest.raises(RequestDeadlineExceeded):
        query()
    with pytest.raises(RequestDeadlineExceeded):
        request_budget(lambda: (clock.__setitem__(0, clock[0] + 3), LadybugStore().q(Connection())("RETURN 1")))()


def test_dry_run_cannot_turn_deadline_into_gate_refusal(monkeypatch):
    from kge.pipeline.sqlguard import Allowlist
    warehouse = BigQueryWarehouses("unused", ["retail"], project="fixture")
    monkeypatch.setattr(warehouse, "_query", lambda *a, **kw: (_ for _ in ()).throw(RequestDeadlineExceeded()))
    with pytest.raises(RequestDeadlineExceeded):
        warehouse.gate("retail", "SELECT 1", Allowlist("retail", set(), {}))


def test_local_dry_run_propagates_the_deadline(monkeypatch):
    from kge.pipeline.sqlguard import Allowlist, gate
    monkeypatch.setenv("MODEL_REQUEST_TIMEOUT", ".025")
    cursor = RequestCursor(None)
    with pytest.raises(RequestDeadlineExceeded):
        request_budget(lambda: gate(cursor, "SELECT 1", Allowlist("retail", set(), {})))()


def test_raw_local_graph_connection_checks_the_floor(monkeypatch):
    from kge.adapters.graphstore.ladybug import RequestConnection
    monkeypatch.setenv("MODEL_REQUEST_TIMEOUT", ".025")
    with pytest.raises(RequestDeadlineExceeded):
        request_budget(lambda: RequestConnection(None).execute("RETURN 1"))()


def test_shorter_warehouse_limit_still_cancels_inside_a_request(monkeypatch):
    from kge.ports import QueryRefused
    monkeypatch.setenv("MODEL_REQUEST_TIMEOUT", "10")
    warehouse, calls = bq(monkeypatch, [{"location": "EU"}, {"jobReference": {"jobId": "short"}}, TimeoutError(), {}])
    with pytest.raises(QueryRefused, match="1 second"):
        request_budget(lambda: warehouse.bounded_query("SELECT 1", QueryLimits(seconds=1)))()
    assert int(calls[1][2]["configuration"]["jobTimeoutMs"]) <= 1000
    assert "/cancel?" in calls[-1][1]
    assert calls[-1][3] == 1


def test_graph_retry_cannot_replenish_request_budget(monkeypatch, clock):
    aborted = {"jobComplete": True, "errors": [{"reason": "aborted", "message": "aborted"}]}
    rest = Rest(aborted, {"jobComplete": True})
    graph = client(monkeypatch, rest)
    original = rest.post

    def post(url, body):
        clock[0] += 2
        return original(url, body)

    monkeypatch.setattr(rest, "post", post)
    with pytest.raises(RequestDeadlineExceeded):
        request_budget(lambda: graph.query("SELECT 1", []))()
    assert [body["jobTimeoutMs"] for _, body in rest.sent] == ["3000", "1000"]


@pytest.mark.parametrize("budget", [True, False])
def test_shared_simple_query_limits(monkeypatch, clock, budget):
    from kge.adapters.bigquery import bq_query
    monkeypatch.setattr("kge.adapters.bigquery.bq_token", lambda _: ("offline", "fixture"))
    calls = []

    def send(req, timeout):
        calls.append((json.loads(req.data), timeout))
        clock[0] += 4
        return JsonResponse({"jobComplete": True, "rows": []})

    monkeypatch.setattr("kge.adapters.http.open_http", send)
    if budget:
        with pytest.raises(RequestDeadlineExceeded):
            request_budget(lambda: bq_query("SELECT 1"))()
        assert calls[0][0]["jobTimeoutMs"] == "3000"
        assert 2.9 < calls[0][1] <= 3
    else:
        assert bq_query("SELECT 1") == []
        assert "jobTimeoutMs" not in calls[0][0]
        assert calls[0][1] == 120


@pytest.mark.parametrize("failure", [
    {"errors": [{"reason": "stopped", "message": "cancelled"}]},
    {"status": {"errorResult": {"reason": "invalidQuery", "message": "bad query"}}},
])
def test_shared_loader_refuses_completed_failed_jobs(monkeypatch, failure):
    from kge.adapters.bigquery import BigQueryLoader, QueryFailed
    loader = BigQueryLoader(project="p")
    monkeypatch.setattr(loader, "token", lambda: ("t", "p"))
    monkeypatch.setattr(loader, "post", lambda *a: {"jobComplete": True, **failure})
    with pytest.raises(QueryFailed):
        loader.run("SELECT 1")


def test_shared_loader_ignores_unfinished_rows_and_preserves_completed_schema(monkeypatch):
    from kge.adapters.bigquery import BigQueryLoader
    loader = BigQueryLoader(project="p")
    monkeypatch.setattr(loader, "token", lambda: ("t", "p"))
    monkeypatch.setattr(loader, "post", lambda *a: {"jobComplete": False, "jobReference": {"jobId": "j", "location": "EU"},
                                                    "rows": [{"f": [{"v": "wrong"}]}]})
    pages = iter([{"jobComplete": True, "schema": {"fields": [{"name": "n", "type": "INTEGER"}]},
                   "rows": [{"f": [{"v": "1"}]}], "pageToken": "next"},
                  {"jobComplete": True, "rows": [{"f": [{"v": "2"}]}]}])
    urls = []
    def get(url, headers):
        urls.append(url)
        return next(pages)
    monkeypatch.setattr(loader, "get", get)
    assert loader.rows("SELECT n") == [(1,), (2,)]
    assert all("location=EU" in url for url in urls)
    assert "pageToken=next" in urls[1]


def test_shared_loader_deadline_also_bounds_result_pagination(monkeypatch):
    from kge.adapters.bigquery import BigQueryLoader
    clock = [0]
    loader = BigQueryLoader(project="p", clock=lambda: clock[0])
    monkeypatch.setattr(loader, "token", lambda: ("t", "p"))
    monkeypatch.setattr(loader, "post", lambda *a: {"jobComplete": True, "jobReference": {"jobId": "j"}, "pageToken": "p"})
    def get(*args, **kwargs):
        clock[0] = 301
        return {"jobComplete": True, "pageToken": "again"}
    monkeypatch.setattr(loader, "get", get)
    with pytest.raises(TimeoutError):
        loader.run("SELECT 1")


def test_prune_waits_for_children_before_parents_and_stops_on_failure(monkeypatch):
    from kge.adapters.bigquery import BigQueryLoader, QueryFailed
    loader = BigQueryLoader(project="p")
    monkeypatch.setattr(loader, "account", lambda: (None, "p"))
    monkeypatch.setattr(loader, "token", lambda: ("t", "p"))
    monkeypatch.setattr(loader, "tables", lambda _: [
        {"tableReference": {"tableId": name}, "labels": {"kge_unit": "old"}} for name in ("parent", "child")])
    monkeypatch.setattr(loader, "rows", lambda _: [("child", "parent")])
    events = []
    def post(url, headers, body):
        events.append(body["query"])
        return {"jobComplete": False, "jobReference": {"jobId": "drop"}}
    def get(url, headers):
        events.append("awaited")
        return {"jobComplete": True}
    monkeypatch.setattr(loader, "post", post)
    monkeypatch.setattr(loader, "get", get)
    loader.prune_units("d", {"current"})
    assert events == ["DROP TABLE IF EXISTS `p.d.child`", "awaited", "DROP TABLE IF EXISTS `p.d.parent`", "awaited"]
    events.clear()
    monkeypatch.setattr(loader, "get", lambda *a: {"jobComplete": True, "errors": [{"reason": "denied"}]})
    with pytest.raises(QueryFailed):
        loader.prune_units("d", {"current"})
    assert events == ["DROP TABLE IF EXISTS `p.d.child`"]
