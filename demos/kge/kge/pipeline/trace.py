"""The run trace's recording side: the trace a run has open, and the calls that write into it, which do nothing when no
trace is open; and the JSON-lines files run records are kept in. The trace itself, what it holds and how a run opens one,
is kge/services/ask/engine/runtrace.py's: it sets _current here, so a model call (kge/adapters/model/) or a vector search
(kge/adapters/retrieval/) recorded from below the engine lands in the run's own trace."""
from __future__ import annotations

import hashlib
import json
import threading
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
from typing import Any, Iterator

from kge.ports import GraphReadRecorder

_current: ContextVar[Any] = ContextVar("run_trace", default=None)
_watch: ContextVar[list[str] | None] = ContextVar("transient_watch", default=None)
_cancel_event: ContextVar[threading.Event | None] = ContextVar("query_cancel_event", default=None)
_cancel_hooks: ContextVar[list | None] = ContextVar("query_cancel_hooks", default=None)


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
             ok: bool = True, error: str | None = None) -> None:
    t = _current.get()
    if t:
        t.llm_call(model, served_model, tokens_in, tokens_out, ms, ok, error)


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
