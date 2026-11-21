"""The run trace's recording side: the trace a run has open, and the calls that write into it, which do nothing when no
trace is open; and the JSON-lines files run records are kept in. The trace itself, what it holds and how a run opens one,
is kge/services/ask/engine/runtrace.py's: it sets _current here, so a model call (kge/adapters/model/) or a vector search
(kge/adapters/retrieval/) recorded from below the engine lands in the run's own trace.

Spans go through the OpenTelemetry API, the tracing port, under KGE's tracer (SCOPE): with tracing off the API's no-op
tracer records nothing, and set up (kge/adapters/tracing/) the SDK exports KGE's spans alone, with the safe keys alone."""
from __future__ import annotations

import hashlib
import json
import threading
import time
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
from typing import Any, Callable, Iterator, Mapping, Sequence

from opentelemetry import context as otel_context
from opentelemetry import trace as otel_trace
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator
from opentelemetry.trace import Link, NonRecordingSpan, Span, Status, StatusCode

from kge.ports import GraphReadRecorder

SCOPE = "kge"
W3C = TraceContextTextMapPropagator()
"""The W3C trace context, read and written here alone: OpenTelemetry's global propagator is a no-op
(kge/pipeline/telemetry.py), so no library adds trace context of its own."""

_current: ContextVar[Any] = ContextVar("run_trace", default=None)
_watch: ContextVar[list[str] | None] = ContextVar("transient_watch", default=None)
_cancel_event: ContextVar[threading.Event | None] = ContextVar("query_cancel_event", default=None)
_cancel_hooks: ContextVar[list | None] = ContextVar("query_cancel_hooks", default=None)


def tracer() -> otel_trace.Tracer:
    """KGE's tracer from OpenTelemetry's global provider: the SDK's once tracing is set up, else the API's no-op."""
    return otel_trace.get_tracer(SCOPE)


def present(attributes: dict[str, Any]) -> dict[str, Any]:
    """attributes without the ones not known (None), which a span does not take."""
    return {key: value for key, value in attributes.items() if value is not None}


def failed(span: Span, kind: str) -> None:
    """Mark span failed by the kind of failure (an exception's type name), never its message."""
    span.set_attributes({"kge.outcome": "failed", "kge.error": kind})
    span.set_status(Status(StatusCode.ERROR))


def new_span(name: str, attributes: dict[str, Any] | None = None, *, parent: Span | str | None = None,
           start_ns: int | None = None, link: bool = False) -> Span:
    """Start a span under parent (a span, or a W3C traceparent), else under the current span. link: the current span is
    linked to it, where it is not its parent (a later pass of a question run)."""
    if isinstance(parent, str):
        context = W3C.extract({"traceparent": parent})
    elif parent is not None:
        context = otel_trace.set_span_in_context(parent)
    else:
        context = None
    here = otel_trace.get_current_span().get_span_context()
    links = [Link(here)] if link and here.is_valid else None
    return tracer().start_span(name, context=context, attributes=present(attributes or {}), links=links,
                               start_time=start_ns, record_exception=False, set_status_on_exception=False)


def as_current(span: Span):
    """span current inside the block, so spans opened there are its children; neither ended nor marked on leaving."""
    return otel_trace.use_span(span, end_on_exit=False, record_exception=False, set_status_on_exception=False)


def traceparent(span: Span) -> str | None:
    """span's W3C traceparent, for another process or a later pass to continue its trace; None for the API's span that
    records nothing (tracing off), which carries its parent's ids alone."""
    if isinstance(span, NonRecordingSpan):
        return None
    carrier: dict[str, str] = {}
    W3C.inject(carrier, context=otel_trace.set_span_in_context(span))
    return carrier.get("traceparent")


def trace_headers(url: str) -> dict[str, str]:
    """The trace context an outgoing call to url carries: the current span's traceparent alone, where url's origin is
    one TRACE_PROPAGATE_TO lists (kge/adapters/tracing destinations) and a span is recorded; else none. Never a
    tracestate or baggage. The one place KGE adds trace context to a call (kge/adapters/http.py open_http)."""
    span = otel_trace.get_current_span()
    if not span.is_recording():
        return {}
    from kge.adapters.tracing import destinations, origin_of
    if origin_of(url) not in destinations():
        return {}
    parent = traceparent(span)
    return {"traceparent": parent} if parent else {}


def finished(name: str, start_ns: int, end_ns: int, attributes: dict[str, Any], *, parent: Span | None = None,
             failure: str | None = None) -> None:
    """A finished child span, with the times its caller measured, under parent or else the current span; nothing where
    there is neither."""
    if parent is None and not otel_trace.get_current_span().get_span_context().is_valid:
        return
    span = new_span(name, attributes, parent=parent, start_ns=start_ns)
    if failure:
        failed(span, failure)
    span.end(end_time=end_ns)


def carried(fn: Callable[[], Any]) -> Callable[[], Any]:
    """fn, run on another thread as a child of the span current here."""
    held = otel_context.get_current()

    def run():
        token = otel_context.attach(held)
        try:
            return fn()
        finally:
            otel_context.detach(token)
    return run


def current() -> Any:
    """The open trace (kge/services/ask/engine/runtrace.py Trace), or None."""
    return _current.get()


class QueryCancelled(RuntimeError):
    """Raised when the caller stops a running query."""


@contextmanager
def cancellable(event: threading.Event | None = None) -> Iterator[tuple[threading.Event, list]]:
    """Open a cancellation scope so Stop can interrupt DuckDB or cancel a BigQuery job."""
    ev = event if event is not None else threading.Event()
    hooks: list = []
    t_ev = _cancel_event.set(ev)
    t_hk = _cancel_hooks.set(hooks)
    try:
        yield ev, hooks
    finally:
        hooks.clear()
        _cancel_event.reset(t_ev)
        _cancel_hooks.reset(t_hk)


def is_cancel_scope_active() -> bool:
    """Whether the current thread/context is inside a cancellable() scope."""
    return _cancel_event.get() is not None


def register_cancel_hook(fn) -> bool:
    """Register a cleanup/interrupt callback for the active query in this context."""
    hooks = _cancel_hooks.get()
    if hooks is not None and fn not in hooks:
        hooks.append(fn)
    ev = _cancel_event.get()
    if ev is not None and ev.is_set():
        try:
            fn()
        except Exception:
            pass
        raise QueryCancelled("Stopped by caller")
    return hooks is not None


@contextmanager
def cancel_hook(fn):
    """Keep one interrupt callback only while its operation is active, firing at most once."""
    lock = threading.Lock()
    active = True

    def cancel():
        nonlocal active
        with lock:
            if not active:
                return
            active = False
        fn()

    try:
        register_cancel_hook(cancel)
        yield cancel
    finally:
        with lock:
            active = False
        hooks = _cancel_hooks.get()
        if hooks is not None and cancel in hooks:
            hooks.remove(cancel)


def check_cancelled() -> None:
    """Raise QueryCancelled if the active query was stopped."""
    ev = _cancel_event.get()
    if ev is not None and ev.is_set():
        raise QueryCancelled("Stopped by caller")


def trigger_cancel(ev: threading.Event, hooks: list) -> None:
    """Signal cancellation and invoke all registered warehouse cancel hooks."""
    ev.set()
    for fn in list(hooks):
        try:
            fn()
        except Exception:
            pass



def data_read(principal: str, unit: str, tables: list[str], kind: str, row_count: int,
              refused: bool, reason: str | None = None) -> None:
    """Record metadata for an attempted read, including refusals, without returned rows."""
    t = current()
    if t is None:
        return
    record = {"principal": principal, "unit": unit, "tables": sorted(tables), "kind": kind,
              "row_count": row_count, "refused": refused, **({"reason": reason} if reason else {})}
    t.data_reads.append(record)
    profile = getattr(t.graph, "profile", None)
    if profile is not None:
        append_jsonl(profile.run_dir() / "data-reads.jsonl", {"run_id": t.run_id, "started_at": t.started_at, **record})



def plan_ledger(block: dict) -> None:
    """Record the rules ledger's positions and the applied spans for the open trace (kge/services/ask/engine/planledger.py)."""
    t = current()
    if t is not None:
        t.ledger = block


def plan_rewrite(rule: str, path: str, reason: str, before, after, attempt: int, spans=()) -> None:
    """Record a normalization at its mutation, retaining hashes rather than model values."""
    t = current()
    if t is not None:
        def hashed(value):
            return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()).hexdigest()
        t.plan_rewrites.append({'rule': rule, 'path': path, 'reason': reason, 'attempt': attempt,
                                'before_hash': hashed(before), 'after_hash': hashed(after),
                                'spans': [{'turn_hash': hashed(s['turn']), 'start': s['start'], 'end': s['end']} for s in spans]})


def stage(name: str) -> None:
    t = _current.get()
    if t:
        t.stage(name)


def use(config, llm=None) -> None:
    t = _current.get()
    if t:
        t.use(config, llm)


def llm_call(model: str, served_model: str | None, tokens_in: int | None, tokens_out: int | None, ms: float,
             ok: bool = True, error: str | None = None, detail: Mapping[str, Any] | None = None) -> None:
    """A model call: into the open run trace, which also makes its span; with none open (a call made before any
    question's run, as /assist's routing), a span alone, under the current span (the request's), and nowhere outside
    one. The run trace is not touched. detail: a structured call's own (its call, attempt, budget, thinking sent,
    thinking and contract tokens, and why it stopped), added to the call's record; none for any other call."""
    t = _current.get()
    if t:
        if detail:
            t.llm_call(model, served_model, tokens_in, tokens_out, ms, ok, error, detail=detail)
        else:
            t.llm_call(model, served_model, tokens_in, tokens_out, ms, ok, error)
    else:
        model_span(model, served_model, tokens_in, tokens_out, ms, ok, error, detail=detail)


def model_span(model: str, served_model: str | None, tokens_in: int | None, tokens_out: int | None, ms: float,
               ok: bool = True, error: str | None = None, *, parent=None, stage: str | None = None,
               run_id: str | None = None, detail: Mapping[str, Any] | None = None) -> None:
    """One model call as a span ending now, from the duration its adapter measured: the model asked and the one that
    served, its tokens (a structured call's thinking tokens too), duration and outcome, and a structured call's call
    type, never its prompt or reply. Under parent, else the current span."""
    end = time.time_ns()
    detail = detail or {}
    finished("kge.model", end - int(ms * 1e6), end,
             {"gen_ai.request.model": model, "gen_ai.response.model": served_model,
              "gen_ai.usage.input_tokens": tokens_in, "gen_ai.usage.output_tokens": tokens_out,
              "kge.stage": stage, "kge.duration_ms": round(ms, 2), "kge.outcome": "ok" if ok else "failed",
              "kge.run_id": run_id,
              **({"gen_ai.usage.reasoning_tokens": detail["tokens_thinking"]} if detail.get("tokens_thinking") is not None else {}),
              **({"kge.call": detail["call"]} if detail.get("call") else {})},
             parent=parent, failure=None if ok else (error or "failed"))


def model_attempt(call: str, attempt: int, outcome: str, errors: Sequence[str] = ()) -> None:
    """A structured call's attempt that failed and is corrected or given up on: its call, which attempt, the outcome
    (truncated, contract, or the check that refused it) and the errors sent back, into the open run trace; nothing with
    none open."""
    t = _current.get()
    if t is not None and hasattr(t, "model_attempt"):
        t.model_attempt(call, attempt, outcome, errors)


def agent(arm: str, mode: str, prompt_versions: dict | None = None) -> None:
    t = _current.get()
    if t:
        t.agent_run(arm, mode, prompt_versions)


@contextmanager
def prompting(version: str, prompt_hash: str, context_hash: str | None = None) -> Iterator[None]:
    """A model call made inside this block is recorded with the prompt's version and
    hashes (never the prompt), so two arms can be shown to have sent the same prompt."""
    t = _current.get()
    before = t.prompt if t else None
    if t:
        t.prompt = {"prompt_version": version, "prompt_hash": prompt_hash, **({"context_hash": context_hash} if context_hash else {})}
    try:
        yield
    finally:
        if t:
            t.prompt = before


def retrieved(rows: list[dict], via: str) -> None:
    t = _current.get()
    if t:
        t.retrieved(rows, via)


def transient_failure(exc: BaseException) -> None:
    """A failure the answer stepped around that may pass if asked again (kge/adapters/model/errors.py transient): a
    warehouse query or a model call BigQuery or Vertex AI turned away for now, which the answer reports as a warehouse
    problem or answers without. Recorded, by kind and never by its message, in the list a runner that asks again watches
    (watching, kge/eval/asking.py), so no such answer is scored; nothing is recorded with no runner watching, or for any
    other failure."""
    watch = _watch.get()
    if watch is None:
        return
    from kge.adapters.model.errors import transient
    if transient(exc):
        watch.append(type(exc).__name__)


@contextmanager
def watching() -> Iterator[list[str]]:
    """The failures that may pass if asked again (transient_failure) the code run inside this block stepped around, by
    kind, whether or not it opens a trace of its own."""
    seen: list[str] = []
    token = _watch.set(seen)
    try:
        yield seen
    finally:
        _watch.reset(token)


def number_check(passed: bool, stray_numbers: int = 0, bad_markers: int = 0, bad_documents: int | None = None) -> None:
    t = _current.get()
    if t:
        t.number_check(passed, stray_numbers, bad_markers, bad_documents)


_write = threading.Lock()


def append_jsonl(path: Path, record: dict) -> None:
    """One record, one line. Lines from threads writing at once stay whole."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(record, sort_keys=True, default=str) + "\n"
    with _write, path.open("a") as f:
        f.write(line)


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in Path(path).read_text().splitlines() if line.strip()]


_graph_recorder: ContextVar[GraphReadRecorder | None] = ContextVar("graph_read_recorder", default=None)
_graph_emit: ContextVar[Any] = ContextVar("graph_read_emit", default=None)
_graph_lock: ContextVar[Any] = ContextVar("graph_read_lock", default=None)
_warehouse_stream: ContextVar[Any] = ContextVar("warehouse_stream", default=None)


@contextmanager
def graph_recording(recorder: GraphReadRecorder | None):
    """Record one request, or suspend projection while checking a possible replacement plan."""
    lock_token = _graph_lock.set(threading.RLock())
    token = _graph_recorder.set(recorder)
    try:
        yield recorder
    finally:
        _graph_recorder.reset(token)
        _graph_lock.reset(lock_token)


def graph_read(kind: str, value: Any, used: bool = True) -> None:
    """Record a snapshot accessor's returned elements at the read, in request order."""
    recorder = _graph_recorder.get()
    if recorder is not None:
        with _graph_lock.get():
            record = recorder.record(kind, value, used)
            events = record.pop("_events", ())
            emit = record.pop("_emit", None)
            if emit is not None:
                for event in events:
                    emit(event)


@contextmanager
def graph_stream(emit):
    token = _graph_emit.set(emit)
    warehouse_token = _warehouse_stream.set({"statement": 0} if emit is not None else None)
    try:
        yield
    finally:
        _warehouse_stream.reset(warehouse_token)
        _graph_emit.reset(token)


def warehouse_job(metrics: dict) -> None:
    """One executed warehouse job's cost and duration, without SQL or returned rows."""
    if trace := current():
        trace.warehouse_jobs.append(metrics)


def warehouse_span(system: str, elapsed_ms: float | None, *, state: str = "done", job_id: str | None = None,
                   bytes_processed: int | None = None, slot_ms: int | None = None, statement: int | None = None,
                   kind: str | None = None) -> None:
    """One warehouse job as a span, from the duration its adapter measured, ending now: under the run's open stage, or
    the question's, or else the current span (an HTTP request's), and nowhere outside one. Ids, sizes, timings and its
    state alone, never its SQL or rows; the run trace is not touched."""
    t = current()
    end = time.time_ns()
    elapsed = max(0.0, float(elapsed_ms or 0))
    finished("kge.warehouse", end - int(elapsed * 1e6), end,
             {"db.system.name": system, "kge.warehouse.state": state, "kge.warehouse.job_id": job_id,
              "kge.warehouse.bytes_processed": bytes_processed, "kge.warehouse.slot_ms": slot_ms,
              "kge.warehouse.statement": statement, "kge.warehouse.kind": kind,
              "kge.duration_ms": round(elapsed, 2),
              "kge.stage": t.open[0] if t is not None and t.open else None,
              "kge.run_id": t.run_id if t is not None else None},
             parent=t.span_parent() if t is not None else None, failure=None if state == "done" else state)


_job: ContextVar[dict | None] = ContextVar("timed_job", default=None)


@contextmanager
def timed_job(system: str, kind: str) -> Iterator[None]:
    """One job an adapter runs inside the block, a graph store's query or a retrieval search, as a span
    (warehouse_span) timed around it, done or failed where the block raised, with the job id, bytes and slot time the
    client's own responses report (job_reported), never its SQL."""
    started = time.monotonic()
    state = "failed"
    held: dict = {}
    token = _job.set(held)
    try:
        yield
        state = "done"
    finally:
        _job.reset(token)
        warehouse_span(system, (time.monotonic() - started) * 1000, state=state, kind=kind, job_id=held.get("job_id"),
                       bytes_processed=held.get("bytes_processed"), slot_ms=held.get("slot_ms"))


def job_reported(data: dict) -> None:
    """What a BigQuery response the client already holds says of its job, for the timed_job open here: its id, the
    bytes it processed and its slot time where BigQuery reports them. Nothing outside a timed_job; no request made."""
    held = _job.get()
    if held is None or not isinstance(data, dict):
        return
    stats = data.get("statistics") or {}
    query = stats.get("query") or {}
    reference = data.get("jobReference") or {}
    if reference.get("jobId"):
        held["job_id"] = str(reference["jobId"])
    for key, values in (("bytes_processed", (data.get("totalBytesProcessed"), query.get("totalBytesProcessed"))),
                        ("slot_ms", (data.get("totalSlotMs"), stats.get("totalSlotMs"), query.get("totalSlotMs")))):
        value = next((v for v in values if v not in (None, "")), None)
        try:
            if value is not None:
                held[key] = int(value)
        except (TypeError, ValueError):
            pass


def stream_event(event: dict) -> None:
    """Publish an already governed public value only while this request is streaming."""
    check_cancelled()
    emit = _graph_emit.get()
    if emit is not None:
        emit(event)


def streaming() -> bool:
    return _graph_emit.get() is not None


@contextmanager
def warehouse_statement():
    """Number executed warehouse statements in request order, excluding dry runs and graph reads."""
    stream = _warehouse_stream.get()
    if stream is None:
        yield None
        return
    statement = None

    def progress(state: str, elapsed_ms: int, bytes_processed: int | None):
        nonlocal statement
        if statement is None:
            stream["statement"] += 1
            statement = stream["statement"]
        stream_event({"event": "progress", "stage": "warehouse", "state": state,
                      "elapsed_ms": elapsed_ms, "bytes_processed": bytes_processed, "statement": statement})

    yield progress
