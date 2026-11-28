"""The tracing port (#443): the safe attribute set, the setting's refusal, stage spans at the run trace's own clock
readings, model and warehouse spans under the open stage, a failure recorded by its kind alone, a traceparent continued
and carried to another thread, and nothing recorded outside a span. The OpenTelemetry API is the port (#475): the
safe-keys rule is a span processor before every exporter. On the SDK's in-memory exporter."""
from __future__ import annotations

import threading

import pytest
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

import kge.adapters.tracing as tracing
from kge.adapters.tracing.otel import Traced
from kge.pipeline import trace as recording
from kge.pipeline.questionrun import RunPending
from kge.services.ask.engine import runtrace
from tests.tracing_setup import cleared, tracing_to

PLANT = "Zyxplant revenue for jane.doe@example.com SELECT secret FROM t"


@pytest.fixture
def exported():
    held = InMemorySpanExporter()
    with tracing_to(held):
        yield held


@pytest.fixture
def fresh():
    """Tracing off before and after: a test that sets it up through the setting leaves no provider behind."""
    cleared()
    yield
    cleared()


def batched_to(exporter) -> Traced:
    """Spans batched to exporter, as the OTLP and Cloud Trace exporters batch them, until the test's fresh clears it."""
    cleared()
    built = Traced(exporter, batch=True)
    tracing.install(built)
    return built


def span_id(span) -> int:
    return span.get_span_context().span_id


class Clock:
    def __init__(self):
        self.now = 100.0

    def __call__(self):
        return self.now


def test_only_safe_attributes_reach_a_span():
    kept = tracing.safe({"kge.stage": "plan", "question": PLANT, "kge.run_id": "x" * 500, "kge.error": None,
                         "gen_ai.usage.input_tokens": 12, "kge.outcome": "ok", "http.route": "/map/{term}"})
    assert kept == {"kge.stage": "plan", "gen_ai.usage.input_tokens": 12, "kge.outcome": "ok", "http.route": "/map/{term}"}


@pytest.mark.parametrize("value", ["off", " off "])
def test_off_records_nothing(monkeypatch, fresh, value):
    monkeypatch.setenv("TRACING", value)
    tracing.setup()
    tracing.check_settings()
    assert tracing._set_up == {} and tracing.flush().is_set()
    span = recording.new_span("kge.ask", {"kge.path": "ask"},
                            parent="00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01")
    assert not span.is_recording() and recording.traceparent(span) is None


def test_store_is_the_default(monkeypatch):
    monkeypatch.delenv("TRACING", raising=False)
    assert tracing.mode() == "store"


@pytest.mark.parametrize("value", ["jaeger", "OFF", "Console", "OTLP"])
def test_a_mistyped_setting_is_refused_as_the_portal_refuses_it(monkeypatch, value):
    monkeypatch.setenv("TRACING", value)
    with pytest.raises(tracing.TracingSettingError, match="^TRACING must be off, console, otlp, cloudtrace or store$"):
        tracing.check_settings()


REFUSED_ENDPOINTS = ["ftp://collector:4318/v1/traces", "collector:4318", "http://", "http://:4318/v1/traces",
                     "http://c:abc/v1", "http://c:99999/v1", "http://c/v1?x=1", "http://c/v1#top",
                     "http://collector invalid:4318/v1/traces", "http://coll\tector:4318/v1/traces",
                     "http://collector:4318/v1/\ntraces"]
TAKEN_ENDPOINTS = [("", tracing.DEFAULT_ENDPOINT), ("   ", tracing.DEFAULT_ENDPOINT),
                   ("https://collector.example.com/v1/traces", "https://collector.example.com/v1/traces"),
                   (" http://[::1]:4318/v1/traces ", "http://[::1]:4318/v1/traces"), ("http://c:/v1", "http://c:/v1")]


@pytest.mark.parametrize("value", REFUSED_ENDPOINTS)
def test_an_endpoint_that_is_not_an_http_address_is_refused_at_start(monkeypatch, fresh, value):
    monkeypatch.setenv("TRACING", "otlp")
    monkeypatch.setenv("TRACING_ENDPOINT", value)
    with pytest.raises(tracing.TracingSettingError, match="^TRACING_ENDPOINT must be an http or https OTLP traces address"):
        tracing.check_settings()


@pytest.mark.parametrize("value, used", TAKEN_ENDPOINTS)
def test_an_http_endpoint_is_taken(monkeypatch, value, used):
    monkeypatch.setenv("TRACING_ENDPOINT", value)
    assert tracing.endpoint() == used


@pytest.mark.parametrize("mode", ["console", "cloudtrace"])
def test_the_endpoint_is_read_in_otlp_mode_alone(monkeypatch, fresh, mode):
    import google.auth
    monkeypatch.setenv("TRACING", mode)
    monkeypatch.setenv("TRACING_ENDPOINT", "not an address")
    monkeypatch.setenv("VERTEX_PROJECT", "your-pilot-project-id")
    monkeypatch.setattr(google.auth, "default", lambda **kwargs: (object(), None))
    tracing.check_settings()
    tracing._set_up["provider"].provider.shutdown()


def test_an_unsafe_key_set_on_a_span_is_dropped(exported):
    span = recording.new_span("kge.ask", {"kge.path": "ask"})
    span.set_attributes({"question": PLANT, "db.statement": PLANT, "kge.outcome": "ok", "kge.stage": PLANT * 10})
    span.end()
    held = exported.get_finished_spans()[0]
    assert dict(held.attributes) == {"kge.path": "ask", "kge.outcome": "ok"}


def test_the_processor_lets_no_planted_content_reach_an_exporter(exported):
    """Whatever a caller does through the API, the exporter receives the safe keys alone: no event, no exception, no
    status description, no unsafe link attribute, and no other library's span."""
    from opentelemetry import trace
    from opentelemetry.trace import Link, Status, StatusCode
    span = recording.new_span("kge.ask", {"kge.path": "ask", "kge.question": PLANT})
    span.add_event(PLANT, {"text": PLANT})
    span.record_exception(ValueError(PLANT))
    span.set_status(Status(StatusCode.ERROR, PLANT))
    span.end()
    linked = recording.tracer().start_span("kge.question_run.pass",
                                           links=[Link(span.get_span_context(), {"q": PLANT, "kge.stage": "plan"})])
    linked.end()
    other = trace.get_tracer("some.library").start_span(PLANT, attributes={"db.statement": PLANT})
    other.end()
    named = trace.get_tracer("kge", "9.9", attributes={"q": PLANT}).start_span(f"kge.{PLANT}")
    named.end()
    routed = recording.new_span(f"POST /ask {PLANT}")
    routed.end()
    spans = exported.get_finished_spans()
    assert [s.name for s in spans] == ["kge.ask", "kge.question_run.pass", "span", "span"]
    assert all(s.instrumentation_scope.version is None and not s.instrumentation_scope.attributes for s in spans)
    assert "Zyxplant" not in "".join(s.to_json() for s in spans)
    assert spans[0].status.status_code == StatusCode.ERROR and spans[0].status.description is None
    assert not spans[0].events and dict(spans[1].links[0].attributes) == {"kge.stage": "plan"}


def test_stage_spans_are_the_run_traces_own_spells(exported):
    clock = Clock()
    t = runtrace.Trace("ask", clock=clock)
    t.span = recording.new_span("kge.ask", {"kge.path": "ask"}, start_ns=t.at(clock.now))
    for name, ms in (("understand", 3), ("plan", 40), ("understand", 2), ("execute", 7)):
        t.stage(name)
        clock.now += ms / 1000
    out = t.finish({})
    t.span.end()
    spans = exported.get_finished_spans()
    stages = [s for s in spans if s.name.startswith("kge.stage.")]
    assert [s.attributes["kge.stage"] for s in stages] == ["understand", "plan", "understand", "execute"]
    for stage in out["stages"]:
        spells = [s for s in stages if s.attributes["kge.stage"] == stage["stage"]]
        assert sum(s.end_time - s.start_time for s in spells) / 1e6 == pytest.approx(stage["ms"], abs=0.01)
    question = next(s for s in spans if s.name == "kge.ask")
    assert {s.parent.span_id for s in stages} == {question.context.span_id}


def test_model_and_warehouse_spans_sit_under_the_open_stage(exported):
    with runtrace.start("ask") as t:
        t.stage("plan")
        runtrace.llm_call("vertex-gemini:gemini-x", "gemini-x-001", 120, 30, 15.0)
        runtrace.llm_call("vertex-gemini:gemini-x", None, None, None, 5.0, ok=False, error="refused")
        t.stage("execute")
        recording.warehouse_span("bigquery", 250, job_id="ask_1", bytes_processed=1024, slot_ms=90)
        recording.warehouse_span("duckdb", 3, state="failed")
    spans = {s.name + ":" + str(i): s for i, s in enumerate(exported.get_finished_spans())}
    by_id = {s.context.span_id: s for s in spans.values()}
    models = [s for s in spans.values() if s.name == "kge.model"]
    assert [by_id[s.parent.span_id].name for s in models] == ["kge.stage.plan"] * 2
    assert models[0].attributes["gen_ai.usage.input_tokens"] == 120
    assert (models[0].end_time - models[0].start_time) / 1e6 == pytest.approx(15.0, abs=0.01)
    assert models[1].attributes["kge.error"] == "refused" and models[1].status.description is None
    jobs = [s for s in spans.values() if s.name == "kge.warehouse"]
    assert [by_id[s.parent.span_id].name for s in jobs] == ["kge.stage.execute"] * 2
    assert jobs[0].attributes["kge.warehouse.bytes_processed"] == 1024 and jobs[0].attributes["kge.warehouse.slot_ms"] == 90
    assert jobs[1].attributes["kge.outcome"] == "failed"
    assert t.warehouse_jobs == []


def test_a_failure_is_recorded_by_its_kind_never_its_message(exported):
    with pytest.raises(ValueError):
        with runtrace.start("ask") as t:
            t.stage("gate")
            raise ValueError(PLANT)
    with pytest.raises(RunPending):
        with runtrace.start("answer"):
            raise RunPending()
    spans = exported.get_finished_spans()
    text = "".join(s.to_json() for s in spans)
    assert "Zyxplant" not in text and "jane.doe" not in text and "SELECT" not in text
    asked = next(s for s in spans if s.name == "kge.ask")
    assert asked.attributes["kge.error"] == "ValueError" and asked.status.description is None and not asked.events
    assert next(s for s in spans if s.name == "kge.answer").attributes["kge.outcome"] == "pending"
    assert any(s.name == "kge.stage.gate" and s.end_time for s in spans)


def test_nothing_is_recorded_outside_a_span(exported):
    recording.warehouse_span("duckdb", 4)
    recording.llm_call("mock:mock", "mock", 1, 1, 2.0)
    recording.finished("kge.model", 1, 2, {"kge.stage": "plan"})
    assert exported.get_finished_spans() == ()


def test_a_traceparent_is_continued_and_carried_to_another_thread(exported):
    caller = "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01"
    span = recording.new_span("POST /ask", parent=caller)
    seen = []
    with recording.as_current(span):
        worker = threading.Thread(target=recording.carried(lambda: seen.append(recording.warehouse_span("duckdb", 1))))
    worker.start()
    worker.join()
    span.end()
    assert recording.traceparent(span).split("-")[1] == caller.split("-")[1]
    spans = exported.get_finished_spans()
    assert {s.context.trace_id for s in spans} == {int(caller.split("-")[1], 16)}
    assert [s.name for s in spans] == ["kge.warehouse", "POST /ask"]
    garbage = recording.new_span("GET /", parent="not a traceparent")
    garbage.end()
    assert recording.traceparent(garbage).split("-")[1] != caller.split("-")[1]


def test_a_later_pass_links_the_request_that_made_it(exported):
    start = recording.new_span("kge.question_run")
    parent = recording.traceparent(start)
    start.end()
    poll = recording.new_span("GET /ask/runs/{run_id}")
    with recording.as_current(poll):
        later = recording.new_span("kge.question_run.pass", {"kge.question_run": "r1"}, parent=parent, link=True)
        later.end()
    poll.end()
    spans = {s.name: s for s in exported.get_finished_spans()}
    assert spans["kge.question_run.pass"].context.trace_id == spans["kge.question_run"].context.trace_id
    assert spans["kge.question_run.pass"].links[0].context.span_id == spans["GET /ask/runs/{run_id}"].context.span_id


def test_a_cloud_trace_that_cannot_start_stops_the_service_at_start(monkeypatch, fresh):
    import google.auth
    monkeypatch.setenv("TRACING", "cloudtrace")

    def no_credentials(**kwargs):
        raise google.auth.exceptions.DefaultCredentialsError("no credentials here")
    monkeypatch.setattr(google.auth, "default", no_credentials)
    with pytest.raises(tracing.TracingSettingError, match="TRACING=cloudtrace: the tracer could not start"):
        tracing.check_settings()


def test_cloud_trace_signs_in_and_names_its_project(monkeypatch, fresh):
    import google.auth
    monkeypatch.setenv("TRACING", "cloudtrace")
    monkeypatch.setenv("VERTEX_PROJECT", "your-pilot-project-id")
    monkeypatch.setattr(google.auth, "default", lambda **kwargs: (object(), "credentials-project"))
    tracing.check_settings()
    built = tracing._set_up["provider"]
    assert built.provider.resource.attributes["gcp.project_id"] == "your-pilot-project-id"
    built.provider.shutdown()


@pytest.mark.parametrize("backend", ["duckdb", "bigquery"])
def test_a_background_job_is_one_span_under_its_pass_however_often_it_is_polled(exported, backend, tmp_path, monkeypatch):
    from kge.adapters.warehouse.bigquery import BigQueryWarehouses
    from kge.adapters.warehouse.duckdb import DuckDBEstate, Warehouses
    from tests import bigquery_fake
    from tests.unit.test_question_runs import complete, native, statement
    if backend == "bigquery":
        fake = native(monkeypatch)
        w = BigQueryWarehouses(tmp_path, ["u"], project=bigquery_fake.PROJECT, client="sample")
        table = f"`{bigquery_fake.PROJECT}.{w.dataset_id}.u_events`"
        fake.run(None, f"CREATE TABLE {table} (value INT64)")
        fake.run(None, f"INSERT INTO {table} VALUES (7), (8)")
    else:
        estate = DuckDBEstate(tmp_path)
        with estate.stage("u", ["CREATE TABLE events(value INTEGER)"]) as session:
            session.execute("INSERT INTO events VALUES (7), (8)")
        estate.publish("u")
        w = Warehouses(tmp_path, ["u"])
    exported.clear()
    passing = recording.new_span("kge.question_run.pass", {"kge.question_run": "r1"})
    with recording.as_current(passing):
        handle = w.submit(statement(), job_id=f"ask_traced_{backend}")
        assert complete(w, handle)["state"] == "done"
        w.poll(handle)
    passing.end()
    w.release(handle)
    w.close()
    jobs = [s for s in exported.get_finished_spans() if s.name == "kge.warehouse"]
    assert len(jobs) == 1
    job = jobs[0]
    assert job.parent.span_id == span_id(passing)
    assert job.attributes["db.system.name"] == backend and job.attributes["kge.warehouse.state"] == "done"
    assert job.attributes["kge.warehouse.job_id"].startswith("ask_traced_")
    if backend == "bigquery":
        assert job.attributes["kge.warehouse.bytes_processed"] == 678 and job.attributes["kge.warehouse.slot_ms"] == 12345
        assert job.attributes["kge.duration_ms"] == 401000
        fake.con.close()


def test_an_unsampled_caller_cannot_drop_a_questions_spans(exported):
    caller = "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-00"
    span = recording.new_span("POST /ask", parent=caller)
    with recording.as_current(span):
        with runtrace.start("ask") as trace:
            trace.stage("plan")
    span.end()
    spans = exported.get_finished_spans()
    assert {s.name for s in spans} == {"POST /ask", "kge.ask", "kge.stage.plan"}
    assert {s.context.trace_id for s in spans} == {int(caller.split("-")[1], 16)}
    assert all(s.context.trace_flags.sampled for s in spans)


def test_graph_store_and_retrieval_jobs_are_spans_with_the_stats_bigquery_reported(exported, monkeypatch):
    from kge.adapters import bigquery as client
    from kge.adapters.graphstore.bigquery import Client
    from kge.adapters.retrieval import bigquery as retrieval
    graph = Client("your-project-id", sleeper=lambda seconds: None)
    monkeypatch.setattr(graph, "_post", lambda url, body: {
        "jobComplete": True, "jobReference": {"projectId": "your-project-id", "jobId": "graph_job_1"},
        "totalBytesProcessed": "2048", "schema": {"fields": []}, "rows": []})
    monkeypatch.setattr(client, "bq_token", lambda project=None: ("token", "your-project-id"))
    monkeypatch.setattr(client.BigQueryLoader, "post", lambda self, url, headers, body, timeout=120: {
        "jobComplete": True, "jobReference": {"projectId": "your-project-id", "jobId": "search_job_1"},
        "totalBytesProcessed": "4096", "statistics": {"totalSlotMs": "321"},
        "schema": {"fields": [{"name": "n"}]}, "rows": [{"f": [{"v": "1"}]}]})
    span = recording.new_span("POST /ask")
    with recording.as_current(span):
        graph.query(f"SELECT '{PLANT}'", [], read=True)
        assert retrieval.BigQueryRetrieval(None).query(f"SELECT '{PLANT}'") == [{"n": "1"}]
    span.end()
    jobs = [s for s in exported.get_finished_spans() if s.name == "kge.warehouse"]
    assert [{k: s.attributes.get(k) for k in ("db.system.name", "kge.warehouse.kind", "kge.warehouse.state",
                                              "kge.warehouse.job_id", "kge.warehouse.bytes_processed",
                                              "kge.warehouse.slot_ms")} for s in jobs] == [
        {"db.system.name": "bigquery", "kge.warehouse.kind": "graph", "kge.warehouse.state": "done",
         "kge.warehouse.job_id": "graph_job_1", "kge.warehouse.bytes_processed": 2048, "kge.warehouse.slot_ms": None},
        {"db.system.name": "bigquery", "kge.warehouse.kind": "retrieval", "kge.warehouse.state": "done",
         "kge.warehouse.job_id": "search_job_1", "kge.warehouse.bytes_processed": 4096, "kge.warehouse.slot_ms": 321}]
    assert all(s.parent.span_id == span_id(span) for s in jobs)
    assert "Zyxplant" not in "".join(s.to_json() for s in jobs)


def test_a_report_outside_a_timed_job_records_nothing():
    recording.job_reported({"jobReference": {"jobId": "x"}, "totalBytesProcessed": "1"})
    with recording.timed_job("bigquery", "graph"):
        recording.job_reported({"totalBytesProcessed": "not a number", "statistics": {"query": {"totalSlotMs": "7"}}})


class Hanging:
    """An exporter that never answers."""

    def __init__(self):
        self.release = threading.Event()

    def export(self, spans):
        self.release.wait()
        from opentelemetry.sdk.trace.export import SpanExportResult
        return SpanExportResult.SUCCESS

    def shutdown(self):
        self.release.set()

    def force_flush(self, timeout_millis=30000):
        return True


def asgi_app(raises=False):
    from starlette.applications import Starlette
    from starlette.middleware import Middleware
    from starlette.responses import PlainTextResponse
    from starlette.routing import Route
    from kge.app import TracingMiddleware

    def answer(request):
        if raises:
            raise RuntimeError(PLANT)
        return PlainTextResponse("ok")
    return Starlette(routes=[Route("/ask", answer, methods=["POST"]), Route("/health", lambda request: PlainTextResponse("up"))],
                     middleware=[Middleware(TracingMiddleware)])


def test_a_hanging_exporter_holds_a_response_back_by_the_cap_at_most(monkeypatch, fresh):
    import time
    from starlette.testclient import TestClient
    import kge.app
    hanging = Hanging()
    batched_to(hanging)
    monkeypatch.setattr(kge.app, "FLUSH_SECONDS", 0.3)
    with TestClient(asgi_app()) as client:
        started = time.monotonic()
        assert client.post("/ask").status_code == 200
        waited = time.monotonic() - started
    hanging.release.set()
    assert 0.3 <= waited < 0.3 + 0.4


def test_with_tracing_off_the_middleware_takes_no_worker_thread(monkeypatch):
    import anyio
    import anyio.to_thread
    from kge.app import TracingMiddleware
    monkeypatch.setenv("TRACING", "off")
    sent = []

    async def plain(scope, receive, send):
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    async def receive():
        return {"type": "http.request", "body": b""}

    async def send(message):
        sent.append(message)

    async def main():
        limiter = anyio.to_thread.current_default_thread_limiter()
        holders = [object() for _ in range(int(limiter.total_tokens))]
        for holder in holders:
            await limiter.acquire_on_behalf_of(holder)
        try:
            with anyio.fail_after(0.1):
                await TracingMiddleware(plain)({"type": "http", "method": "POST", "headers": []}, receive, send)
        finally:
            for holder in holders:
                limiter.release_on_behalf_of(holder)
    anyio.run(main)
    assert [m["type"] for m in sent] == ["http.response.start", "http.response.body"]


def test_a_failing_request_exports_its_spans_before_the_error_response(monkeypatch, fresh):
    from starlette.testclient import TestClient
    held = InMemorySpanExporter()
    batched_to(held)
    with TestClient(asgi_app(raises=True), raise_server_exceptions=False) as client:
        assert client.post("/ask").status_code == 500
        spans = held.get_finished_spans()
    assert [s.name for s in spans] == ["POST /ask"]
    assert spans[0].attributes["kge.error"] == "RuntimeError" and "Zyxplant" not in spans[0].to_json()
def test_flush_exports_a_batch_at_once():
    held = InMemorySpanExporter()
    batched = Traced(held, batch=True)
    batched.provider.get_tracer("kge").start_span("POST /ask").end()
    assert held.get_finished_spans() == ()
    assert batched.flush().wait(tracing.FLUSH_SECONDS)
    assert [s.name for s in held.get_finished_spans()] == ["POST /ask"]
    batched.provider.shutdown()


def test_a_model_call_outside_a_run_is_a_span_of_the_request_alone(exported):
    span = recording.new_span("POST /assist")
    with recording.as_current(span):
        recording.llm_call("vertex-gemini:gemini-x", "gemini-x-001", 90, 12, 8.0)
        recording.llm_call("vertex-gemini:gemini-x", None, None, None, 3.0, ok=False, error="NotJSON")
    span.end()
    models = [s for s in exported.get_finished_spans() if s.name == "kge.model"]
    assert [s.parent.span_id for s in models] == [span_id(span)] * 2
    assert models[0].attributes["gen_ai.usage.input_tokens"] == 90 and models[0].attributes["kge.outcome"] == "ok"
    assert models[1].attributes["kge.error"] == "NotJSON" and models[1].status.description is None
    assert recording.current() is None


def test_only_a_question_waits_for_a_hanging_exporter(monkeypatch, fresh):
    import time
    from starlette.testclient import TestClient
    hanging = Hanging()
    batched_to(hanging)
    with TestClient(asgi_app()) as client:
        started = time.monotonic()
        assert client.get("/health").status_code == 200
        waited = time.monotonic() - started
    hanging.release.set()
    assert waited < 0.5


class Gate:
    """An exporter whose first export waits for release, the rest pass straight through."""

    def __init__(self):
        self.release = threading.Event()
        self.entered = threading.Event()
        self.names = []
        self.calls = 0

    def export(self, spans):
        from opentelemetry.sdk.trace.export import SpanExportResult
        self.calls += 1
        if self.calls == 1:
            self.entered.set()
            self.release.wait()
        self.names.extend(s.name for s in spans)
        return SpanExportResult.SUCCESS

    def shutdown(self):
        self.release.set()

    def force_flush(self, timeout_millis=30000):
        return True


def flushing_threads():
    return [t for t in threading.enumerate() if t.name == "trace-flush"]


def test_concurrent_flushes_share_one_thread_while_the_endpoint_hangs():
    hanging = Hanging()
    batched = Traced(hanging, batch=True)
    batched.provider.get_tracer("kge").start_span("POST /ask").end()
    before = {t.ident for t in flushing_threads()}
    first = batched.flush()
    events = []
    callers = [threading.Thread(target=lambda: events.append(batched.flush())) for _ in range(8)]
    for caller in callers:
        caller.start()
    for caller in callers:
        caller.join()
    assert len([t for t in flushing_threads() if t.ident not in before]) == 1
    assert len({id(e) for e in events}) == 1 and events[0] is not first and not first.is_set()
    hanging.release.set()
    assert first.wait(5) and events[0].wait(5)
    fresh = batched.flush()
    assert fresh is not first and fresh is not events[0]
    assert fresh.wait(5)
    batched.provider.shutdown()


def test_a_span_ended_while_a_flush_is_under_way_is_exported_before_its_wait_ends():
    gate = Gate()
    batched = Traced(gate, batch=True)
    batched.provider.get_tracer("kge").start_span("POST /ask").end()
    before = {t.ident for t in flushing_threads()}
    first = batched.flush()
    assert gate.entered.wait(5)
    batched.provider.get_tracer("kge").start_span("GET /ask/runs/{run_id}").end()
    later = batched.flush()
    assert later is not first and not later.is_set()
    assert len([t for t in flushing_threads() if t.ident not in before]) == 1
    gate.release.set()
    assert later.wait(5)
    assert gate.names == ["POST /ask", "GET /ask/runs/{run_id}"] and first.is_set()
    batched.provider.shutdown()


def test_every_question_route_waits_for_the_flush_and_names_a_served_route(monkeypatch, fresh):
    import time
    from starlette.applications import Starlette
    from starlette.middleware import Middleware
    from starlette.responses import PlainTextResponse
    from starlette.routing import Route
    from starlette.testclient import TestClient
    import kge.app
    served = set(kge.app.app.openapi()["paths"]) | {getattr(route, "path", None) for route in kge.app.app.routes}
    assert kge.app.QUESTION_ROUTES <= served
    hanging = Hanging()
    batched_to(hanging)
    monkeypatch.setattr(kge.app, "FLUSH_SECONDS", 0.2)
    routes = [Route(path, lambda request: PlainTextResponse("ok"), methods=["POST"]) for path in kge.app.QUESTION_ROUTES]
    app = Starlette(routes=routes, middleware=[Middleware(kge.app.TracingMiddleware)])
    with TestClient(app) as client:
        for path in sorted(kge.app.QUESTION_ROUTES):
            started = time.monotonic()
            assert client.post(path.replace("{run_id}", "r1")).status_code == 200
            assert time.monotonic() - started >= 0.2, path
    hanging.release.set()


def test_request_limits_stay_outermost_and_tracing_sits_next_inside_them():
    """An oversized or late request is refused before any work, a span among it; every other request is traced."""
    import kge.app
    from kge.services.http import RequestLimitsMiddleware
    order = [entry.cls for entry in kge.app.app.user_middleware]
    assert order[:2] == [RequestLimitsMiddleware, kge.app.TracingMiddleware]


class Logged:
    """An exporter that writes the start and the end of each export to a shared log, beside what the client is sent,
    each export taking hold seconds."""

    def __init__(self, log, hold=0.2):
        self.log = log
        self.hold = hold
        self.spans = []

    def export(self, spans):
        import time
        from opentelemetry.sdk.trace.export import SpanExportResult
        self.log.append(("export started", [s.name for s in spans]))
        time.sleep(self.hold)
        self.spans.extend(spans)
        self.log.append(("export done", [s.name for s in spans]))
        return SpanExportResult.SUCCESS

    def shutdown(self):
        pass

    def force_flush(self, timeout_millis=30000):
        return True


def late_answer(monkeypatch, path, method, log, raises=False, handled=False):
    """An app behind the limits and tracing, in the service's order, whose route answers 200 and whose request deadline
    runs out once it has answered (or, raises, which raises the deadline before any response; handled, with the
    service's own handler for an outside call's failure in place); the request run straight through ASGI, every message the client is sent written to
    log. The scopes the route saw, and the seconds the request took."""
    import time
    import anyio
    from starlette.applications import Starlette
    from starlette.middleware import Middleware
    from starlette.responses import PlainTextResponse
    from starlette.routing import Route
    import kge.app
    import kge.services.http as http
    from kge.adapters.deadline import RequestDeadlineExceeded
    answered = []
    scopes = []

    def remaining(floor=0):
        if answered:
            raise RequestDeadlineExceeded()
        return 60.0
    monkeypatch.setattr(http, "request_remaining", remaining)

    def answer(request):
        scopes.append(request.scope)
        answered.append(True)
        if raises:
            raise RequestDeadlineExceeded()
        return PlainTextResponse("ok")
    from kge.adapters.deadline import ExternalCallError
    app = Starlette(routes=[Route(path, answer, methods=[method])],
                    middleware=[Middleware(http.RequestLimitsMiddleware), Middleware(kge.app.TracingMiddleware)],
                    exception_handlers={ExternalCallError: kge.app.outside_service_failed} if handled else {})

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        log.append((message["type"], message.get("status"), message.get("body")))

    scope = {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": method, "path": path,
             "raw_path": path.encode(), "root_path": "", "scheme": "http", "query_string": b"", "headers": [],
             "client": ("127.0.0.1", 1), "server": ("testserver", 80)}
    started = time.monotonic()
    anyio.run(app, scope, receive, send)
    return scopes, time.monotonic() - started


def test_a_question_whose_deadline_runs_out_after_it_answers_is_traced_as_the_503_sent_and_exported_first(monkeypatch, fresh):
    """#475: the limits replace the app's 200 with their 503; the request's span says 503 and why, and is exported before
    the 503 starts, so Cloud Run's idle CPU cannot lose it."""
    from kge.services.http import REQUEST_SPAN, read_route
    assert read_route("POST", "/ask")
    log = []
    logged = Logged(log)
    batched_to(logged)
    scopes, _ = late_answer(monkeypatch, "/ask", "POST", log)
    starts = [entry for entry in log if entry[0] == "http.response.start"]
    assert [entry[1] for entry in starts] == [503]
    assert log.index(("export done", ["POST /ask"])) < log.index(starts[0])
    assert all(b"ok" != (entry[2] or b"") for entry in log if entry[0] == "http.response.body")
    [span] = logged.spans
    assert span.attributes["http.response.status_code"] == 503
    assert span.attributes["kge.error"] == "RequestDeadlineExceeded" and span.attributes["http.route"] == "/ask"
    assert span.status.status_code.name == "ERROR"
    assert REQUEST_SPAN not in scopes[0]


@pytest.mark.parametrize("path, method", [("/ask", "POST"), ("/catalogue", "GET")])
def test_a_deadline_raised_before_any_response_is_traced_as_the_503_the_limits_send(monkeypatch, fresh, path, method):
    """#475: the app raises the deadline before it starts a response; the limits answer 503, and the request's span says
    503 and why, ended once, and on a question's route exported before the 503 starts."""
    from kge.services.http import REQUEST_SPAN
    log = []
    logged = Logged(log)
    batched_to(logged)
    scopes, _ = late_answer(monkeypatch, path, method, log, raises=True)
    starts = [entry for entry in log if entry[0] == "http.response.start"]
    assert [entry[1] for entry in starts] == [503]
    if path == "/ask":
        assert log.index(("export done", [f"{method} {path}"])) < log.index(starts[0])
    else:
        assert not any(entry[0] == "export started" for entry in log)
        tracing.flush().wait(5)
    [span] = logged.spans
    assert span.name == f"{method} {path}" and span.attributes["http.route"] == path
    assert span.attributes["http.response.status_code"] == 503
    assert span.attributes["kge.error"] == "RequestDeadlineExceeded" and span.status.status_code.name == "ERROR"
    assert REQUEST_SPAN not in scopes[0]


def test_a_deadline_the_services_handler_answers_is_traced_as_503_with_its_reason(monkeypatch, fresh):
    """#475: in the service, the handler for an outside call's failure answers the raised deadline with its own 503;
    the request's span records 503 and the deadline, not the bare status."""
    log = []
    logged = Logged(log)
    batched_to(logged)
    late_answer(monkeypatch, "/ask", "POST", log, raises=True, handled=True)
    assert [entry[1] for entry in log if entry[0] == "http.response.start"] == [503]
    [span] = logged.spans
    assert span.attributes["http.response.status_code"] == 503
    assert span.attributes["kge.error"] == "RequestDeadlineExceeded"


def test_a_late_question_waits_for_a_hanging_exporter_by_the_cap_at_most(monkeypatch, fresh):
    import kge.app
    hanging = Hanging()
    monkeypatch.setattr(kge.app, "FLUSH_SECONDS", 0.3)
    batched_to(hanging)
    sent = []
    _, waited = late_answer(monkeypatch, "/ask", "POST", sent)
    hanging.release.set()
    assert [entry[1] for entry in sent if entry[0] == "http.response.start"] == [503]
    assert 0.3 <= waited < 0.3 + 0.4


def test_a_late_read_that_is_not_a_question_records_its_503_without_waiting_for_the_exporter(monkeypatch, fresh):
    hanging = Hanging()
    batched_to(hanging)
    sent = []
    _, waited = late_answer(monkeypatch, "/catalogue", "GET", sent)
    hanging.release.set()
    assert [entry[1] for entry in sent if entry[0] == "http.response.start"] == [503]
    assert waited < 0.5


def test_with_tracing_off_a_late_answer_is_refused_with_no_span_handed_over(monkeypatch, fresh):
    from kge.services.http import REQUEST_SPAN
    monkeypatch.setenv("TRACING", "off")
    sent = []
    scopes, _ = late_answer(monkeypatch, "/ask", "POST", sent)
    assert [entry[1] for entry in sent if entry[0] == "http.response.start"] == [503]
    assert REQUEST_SPAN not in scopes[0]
