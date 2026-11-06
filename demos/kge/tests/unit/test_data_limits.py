from __future__ import annotations

import io
import json
import time
import urllib.error

import pytest

from kge.adapters.warehouse.duckdb import DuckDBEstate, Warehouses
from kge.adapters.warehouse.bigquery import BigQueryWarehouses
from kge.pipeline.sqlguard import Allowlist, check
from kge.ports import QueryLimits, QueryRefused
from kge.services.data import service


@pytest.fixture
def warehouse(tmp_path):
    estate = DuckDBEstate(tmp_path)
    with estate.stage("retail", ["CREATE TABLE accounts(account_id INTEGER)", "INSERT INTO accounts VALUES (1), (2)"]):
        pass
    estate.publish("retail")
    reader = Warehouses(tmp_path, ["retail"])
    yield reader
    reader.close()


def test_real_query_is_interrupted_and_the_next_read_succeeds(warehouse):
    started = time.monotonic()
    with pytest.raises(QueryRefused, match="time limit"):
        warehouse.query("retail", "WITH RECURSIVE n AS (SELECT 1 AS i UNION ALL SELECT i+1 FROM n WHERE i<10000000) SELECT count(i) FROM n", limits=QueryLimits(seconds=0.05))
    assert time.monotonic() - started < 2
    assert warehouse.query("retail", "SELECT account_id FROM accounts", limits=QueryLimits(rows=1)) == (["account_id"], [(1,)])


def test_bounded_connection_caps_work_and_cannot_access_external_resources(warehouse):
    _, rows = warehouse.query("retail", "SELECT current_setting('threads'), current_setting('memory_limit'), current_setting('max_temp_directory_size'), current_setting('enable_external_access'), current_setting('lock_configuration')", limits=QueryLimits())
    assert rows == [(1, "256.0 MiB", "0 bytes", False, True)]
    with pytest.raises(QueryRefused, match="memory limit"):
        warehouse.query("retail", "SELECT i FROM range(1000000) t(i) ORDER BY hash(i)", limits=QueryLimits(memory_bytes=1048576))


@pytest.mark.parametrize("dialect, sql", [("duckdb", "SELECT current_setting('secret_directory')"),
    ("duckdb", "SELECT version()"), ("duckdb", "SELECT current_database()"),
    ("duckdb", "SELECT current_schema()"), ("duckdb", "SELECT current_query()"),
    ("duckdb", "SELECT current_user"), ("bigquery", "SELECT SESSION_USER()")])
def test_server_information_functions_are_refused_by_the_gate(dialect, sql):
    problems = check(sql, Allowlist("retail", set(), {}), dialect)
    assert problems and "server-information" in " ".join(problems)


def bq(monkeypatch, replies):
    warehouse = BigQueryWarehouses("unused", ["retail"], client="fixture", project="fixture")
    monkeypatch.setattr(warehouse, "token", lambda: ("offline", "fixture"))
    calls = []

    def urlopen(request, timeout):
        calls.append((request.method, request.full_url, json.loads(request.data) if request.data else None, timeout))
        reply = replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return io.BytesIO(json.dumps(reply).encode())

    monkeypatch.setattr("urllib.request.urlopen", urlopen)
    return warehouse, calls


def test_bigquery_bounded_read_sets_job_deadline_and_billing_cap_and_follows_pages(monkeypatch):
    warehouse, calls = bq(monkeypatch, [
        {"location": "europe-west2"},
        {"jobReference": {"jobId": "bounded", "location": "europe-west2"}},
        {"jobComplete": True, "schema": {"fields": [{"name": "account_id", "type": "INTEGER"}]}, "rows": [{"f": [{"v": "1"}]}], "pageToken": "next"},
        {"jobComplete": True, "rows": [{"f": [{"v": "2"}]}, {"f": [{"v": "3"}]}], "pageToken": "unused"},
    ])
    assert warehouse.query("retail", "SELECT account_id FROM accounts", limits=QueryLimits(rows=2)) == (["account_id"], [(1,), (2,)])
    configuration = calls[1][2]["configuration"]
    assert 0 < int(configuration["jobTimeoutMs"]) <= 5000
    assert configuration["query"]["maximumBytesBilled"] == str(100 * 1024 * 1024)
    assert "retail_accounts" in configuration["query"]["query"]
    assert "location=europe-west2" in calls[2][1]
    assert "pageToken=next" in calls[3][1] and "maxResults=1" in calls[3][1]
    assert all(0 < call[3] <= 5 for call in calls)


@pytest.mark.parametrize("failure", [TimeoutError(), urllib.error.HTTPError("offline", 400, "billing limit", {}, None)])
def test_bigquery_timeout_or_work_refusal_cancels_the_job(monkeypatch, failure):
    warehouse, calls = bq(monkeypatch, [{"location": "europe-west2"}, {"jobReference": {"jobId": "bounded", "location": "europe-west2"}}, failure, {}])
    with pytest.raises(QueryRefused, match="limit"):
        warehouse.query("retail", "SELECT account_id FROM accounts", limits=QueryLimits())
    assert calls[-1][0] == "POST" and "/jobs/bounded/cancel?location=europe-west2" in calls[-1][1]
    assert calls[-1][3] == 1


def test_recursive_queries_refused_before_the_warehouse(monkeypatch):
    from types import SimpleNamespace
    monkeypatch.setattr(service, "readable", lambda *_: None)
    monkeypatch.setattr(service, "schema_allowlist", lambda *_: None)
    engine = SimpleNamespace(g=None, RESTRICTED_COLUMNS=[], w=SimpleNamespace(sql_dialect="duckdb"))
    with pytest.raises(service.DataRefused, match="recursive"):
        service.query(engine, "reader", "retail", "WITH RECURSIVE n AS (SELECT 1 AS i UNION ALL SELECT i+1 FROM n) SELECT count(i) FROM n")


@pytest.mark.parametrize("operation", ["preview", "export", "query"])
def test_data_services_translate_execution_limit_to_a_clear_400(monkeypatch, operation):
    from types import SimpleNamespace
    def refused(*args, **kwargs):
        assert isinstance(kwargs["limits"], QueryLimits)
        raise QueryRefused("query exceeded the 5 second time limit")
    engine = SimpleNamespace(UNIT_ORDER=["retail"], RESTRICTED_COLUMNS=[], g=None,
                             w=SimpleNamespace(sql_dialect="duckdb", query=refused, gate=lambda *a, **k: {"passed": True}))
    monkeypatch.setattr(service, "readable", lambda *_: None)
    monkeypatch.setattr(service, "schema_allowlist", lambda *_: Allowlist("retail", {"accounts"}, {"accounts": {"account_id"}}, schema={"accounts": {"account_id"}}))
    monkeypatch.setattr(service, "traced", lambda path, call, **kwargs: call())
    monkeypatch.setattr(service, "governed", lambda _engine, _unit, sql, allow, _dialect, _tables: (sql, allow))
    argument = "SELECT account_id FROM accounts" if operation == "query" else "accounts"
    with pytest.raises(service.DataRefused, match="5 second") as caught:
        getattr(service, operation)(engine, "reader", "retail", argument)
    assert caught.value.status == 400


def test_bigquery_dry_run_has_the_same_time_and_scan_budget(monkeypatch):
    warehouse, calls = bq(monkeypatch, [{"totalBytesProcessed": str(100 * 1024 * 1024 + 1)}])
    allow = Allowlist("retail", {"accounts"}, {"accounts": {"account_id"}}, schema={"accounts": {"account_id"}})
    result = warehouse.gate("retail", "SELECT account_id FROM accounts", allow, limits=QueryLimits())
    assert not result["passed"] and "scan work limit" in " ".join(result["problems"])
    assert calls[0][2]["dryRun"] is True and calls[0][2]["maximumBytesBilled"] == str(100 * 1024 * 1024)
    assert 0 < calls[0][3] <= 5


def test_planning_and_execution_share_one_deadline(monkeypatch):
    from types import SimpleNamespace
    times = iter([1.0, 6.1])
    monkeypatch.setattr(service.time, "monotonic", lambda: next(times))
    engine = SimpleNamespace(w=SimpleNamespace(gate=lambda *a, **kw: {"passed": True}))
    with pytest.raises(service.DataRefused, match="time limit"):
        service.execute(engine, "retail", "SELECT 1", None, "duckdb", 1)


def test_bigquery_can_cancel_even_when_submission_loses_its_response(monkeypatch):
    warehouse, calls = bq(monkeypatch, [{"location": "europe-west2"}, TimeoutError(), {}])
    with pytest.raises(QueryRefused, match="time limit"):
        warehouse.query("retail", "SELECT account_id FROM accounts", limits=QueryLimits())
    submitted = calls[1][2]["jobReference"]
    assert submitted["location"] == "europe-west2"
    assert f"/jobs/{submitted['jobId']}/cancel?location=europe-west2" in calls[-1][1]


@pytest.mark.parametrize("dialect, sql", [
    ("duckdb", "SELECT repeat('x', 1000000 * 1000)"),
    ("duckdb", "SELECT lpad('x', 1000000 * 1000, 'x')"),
    ("duckdb", "SELECT rpad('x', 1000000 * 1000, 'x')"),
    ("duckdb", "SELECT range(1000000 * 1000)"),
    ("duckdb", "SELECT list(account_id) FROM accounts"),
    ("duckdb", "SELECT string_agg(CAST(account_id AS VARCHAR), ',') FROM accounts"),
    ("duckdb", "SELECT list_resize([1], 1000000 * 1000)"),
    ("duckdb", "SELECT printf('%*s', 1000000 * 1000, 'x')"),
    ("bigquery", "SELECT REPEAT('x', 1000000 * 1000)"),
    ("bigquery", "SELECT GENERATE_ARRAY(1, 1000000 * 1000)"),
    ("bigquery", "SELECT ARRAY_AGG(account_id) FROM accounts"),
])
def test_memory_expansion_is_refused_before_planning(dialect, sql):
    allow = Allowlist("retail", {"accounts"}, {"accounts": {"account_id"}}, schema={"accounts": {"account_id"}})
    assert "memory-expanding" in " ".join(check(sql, allow, dialect))


def test_deadline_survives_a_gap_between_statements(warehouse):
    started = time.monotonic()
    with pytest.raises(QueryRefused, match="time limit"):
        with warehouse.bounded_connection("retail", "duckdb", QueryLimits(seconds=0.05)) as con:
            con.execute("SELECT 1")
            time.sleep(0.07)
            con.execute("WITH RECURSIVE n AS (SELECT 1 AS i UNION ALL SELECT i+1 FROM n WHERE i<10000000) SELECT count(i) FROM n")
    assert time.monotonic() - started < 0.5


def test_five_second_budget_includes_planning_and_actual_execution(warehouse):
    from types import SimpleNamespace
    allow = Allowlist("retail", {"accounts"}, {"accounts": {"account_id"}}, schema={"accounts": {"account_id"}})
    started = time.monotonic()
    with pytest.raises(service.DataRefused, match="time limit"):
        service.execute(SimpleNamespace(w=warehouse), "retail",
                        "WITH RECURSIVE n AS (SELECT 1 AS i UNION ALL SELECT i+1 FROM n WHERE i<10000000) SELECT count(i) FROM n",
                        allow, "duckdb", 1)
    elapsed = time.monotonic() - started
    print(f"five-second query budget: {elapsed:.3f} seconds")
    assert 4.5 < elapsed < 5.8
    assert warehouse.query("retail", "SELECT account_id FROM accounts", limits=QueryLimits(rows=1))[1] == [(1,)]


def test_connection_setup_cannot_restart_the_deadline(warehouse, monkeypatch):
    from kge.adapters.warehouse import duckdb as adapter
    connect = adapter.duckdb.connect

    def slow_connect(*args, **kwargs):
        time.sleep(0.08)
        return connect(*args, **kwargs)

    monkeypatch.setattr(adapter.duckdb, "connect", slow_connect)
    with pytest.raises(QueryRefused, match="time limit"):
        with warehouse.bounded_connection("retail", "duckdb", QueryLimits(seconds=0.05)):
            pytest.fail("expired connection setup reached the query")


def test_late_result_is_refused_even_without_an_interruptible_statement(warehouse):
    with pytest.raises(QueryRefused, match="time limit"):
        with warehouse.bounded_connection("retail", "duckdb", QueryLimits(seconds=0.05)) as con:
            assert con.execute("SELECT 1").fetchone() == (1,)
            time.sleep(0.08)


def test_service_passes_the_original_deadline_and_refuses_late_results(monkeypatch):
    from types import SimpleNamespace
    clock = [10.0]
    monkeypatch.setattr(service.time, "monotonic", lambda: clock[0])

    def gate(*args, limits, **kwargs):
        assert limits.deadline == 15.0
        clock[0] = 13.0
        return {"passed": True}

    def query(*args, limits, **kwargs):
        assert limits.seconds == 5 and limits.deadline == 15.0
        clock[0] = 15.01
        return ["n"], [(1,)]

    engine = SimpleNamespace(w=SimpleNamespace(gate=gate, query=query))
    with pytest.raises(service.DataRefused, match="5 second time limit"):
        service.execute(engine, "retail", "SELECT 1", None, "duckdb", 1)


@pytest.mark.parametrize("sql, names", [("TABLE flow", ["flow"]), ('TABLE "wealth"."flow"', ["wealth.flow"])])
def test_refused_table_commands_record_the_referenced_table(monkeypatch, sql, names):
    from types import SimpleNamespace
    recorded = []
    monkeypatch.setattr(service, "readable", lambda *_: None)
    monkeypatch.setattr(service, "schema_allowlist", lambda *_: Allowlist("wealth", {"flow"}, {}))
    monkeypatch.setattr(service, "traced", lambda path, call, **kwargs: call())
    monkeypatch.setattr(service.trace, "data_read", lambda *args: recorded.append(args))
    engine = SimpleNamespace(g=None, RESTRICTED_COLUMNS=[], w=SimpleNamespace(sql_dialect="duckdb"))
    with pytest.raises(service.DataRefused):
        service.query(engine, "reader", "wealth", sql)
    assert recorded[0][2] == names
    assert recorded[0][4:6] == (0, True)


def test_bigquery_submission_uses_budget_left_after_auth_and_location(monkeypatch):
    warehouse, calls = bq(monkeypatch, [{"location": "europe-west2"}, {}, {"jobComplete": True}])
    clock = [10.0]
    monkeypatch.setattr(time, "monotonic", lambda: clock[0])

    def token():
        clock[0] += 2
        return "offline", "fixture"

    monkeypatch.setattr(warehouse, "token", token)
    warehouse.bounded_query("SELECT 1", QueryLimits())
    assert calls[1][2]["configuration"]["jobTimeoutMs"] == "3000"
    assert all(call[3] == 3 for call in calls)


def test_bigquery_late_response_is_refused_and_cancelled(monkeypatch):
    warehouse, calls = bq(monkeypatch, [{"location": "europe-west2"}, {}, {"jobComplete": True}, {}])
    clock = [10.0]
    monkeypatch.setattr(time, "monotonic", lambda: clock[0])
    urlopen = urllib.request.urlopen

    def late_response(request, timeout):
        response = urlopen(request, timeout)
        if "/queries/" in request.full_url:
            clock[0] = 15.01
        return response

    monkeypatch.setattr(urllib.request, "urlopen", late_response)
    with pytest.raises(QueryRefused, match="5 second time limit"):
        warehouse.bounded_query("SELECT 1", QueryLimits())
    assert "/cancel?" in calls[-1][1]


@pytest.mark.parametrize("sql,reason", [("SELECT account_id::DATE FROM accounts", "cast"),
                                        (f"SELECT {2 ** 63 - 1}::BIGINT + account_id FROM accounts", "overflow")])
def test_execution_errors_name_the_failure_not_a_limit(warehouse, sql, reason):
    with pytest.raises(QueryRefused) as error:
        warehouse.query("retail", sql, limits=QueryLimits())
    assert "warehouse execution failed" in str(error.value)
    assert reason in str(error.value).lower()
    assert "within its limits" not in str(error.value)


@pytest.mark.parametrize("sql", [
    "SELECT 1 WHERE " + " OR ".join("1=1" for _ in range(129)),
    " UNION ALL ".join("SELECT 1" for _ in range(129)),
    "SELECT " + ",".join("1" for _ in range(2100)),
    "SELECT " + "+".join("1" for _ in range(200)),
])
def test_compact_queries_also_have_term_token_and_ast_depth_caps(sql):
    from kge.services.data.engine.limits import complexity
    assert len(sql) < 32768
    with pytest.raises(QueryRefused, match="complexity limit"):
        complexity(sql, "duckdb")


@pytest.mark.parametrize("dialect", ["duckdb", "bigquery"])
@pytest.mark.parametrize("kind", ["OR", "UNION"])
@pytest.mark.parametrize("nested", [0, 3])
def test_documented_128_term_boundary(dialect, kind, nested):
    from kge.services.data.engine.limits import complexity
    for count in (128, 129):
        sql = ("SELECT 1 WHERE " + " OR ".join("1=1" for _ in range(count)) if kind == "OR"
               else " UNION ALL ".join("SELECT 1 AS n" for _ in range(count)))
        for _ in range(nested):
            sql = f"SELECT 1 FROM ({sql}) nested"
        if count == 128:
            complexity(sql, dialect)
        else:
            with pytest.raises(QueryRefused, match="complexity limit"):
                complexity(sql, dialect)


@pytest.mark.parametrize("sql", [
    "SELECT 1 WHERE " + " OR ".join("1=1" for _ in range(30000)),
    "SELECT " + "(" * 200 + "1" + ")" * 200,
    " INTERSECT ".join("SELECT 1" for _ in range(1000)),
    "SELECT " + "coalesce(" * 33 + "1" + ", 1)" * 33,
    "SELECT " + "NOT " * 2000 + "TRUE",
])
def test_review_complexity_attacks_remain_refused(sql):
    from kge.services.data.engine.limits import complexity
    with pytest.raises(QueryRefused, match="complexity limit|does not parse"):
        complexity(sql, "duckdb")


def test_or_equalities_do_not_connect_tables_in_the_person_authored_data_gate():
    allow = Allowlist("retail", {"transactions", "accounts"},
                     {"transactions": {"account_id"}, "accounts": {"account_id"}},
                     joins={frozenset({("transactions", "account_id"), ("accounts", "account_id")})},
                     schema={"transactions": {"account_id"}, "accounts": {"account_id"}})
    for sql, reason in (
        ("SELECT t.account_id FROM transactions t JOIN accounts a ON t.account_id = a.account_id OR 1=1", "conjunction"),
        ("SELECT t.account_id FROM transactions t, accounts a WHERE t.account_id = a.account_id OR 1=1", "not joined"),
    ):
        assert reason in " ".join(check(sql, allow, inner_only=True))
        assert check(sql, allow) == []
    assert not check("SELECT t.account_id FROM transactions t JOIN accounts a ON (t.account_id = a.account_id)", allow, inner_only=True)
