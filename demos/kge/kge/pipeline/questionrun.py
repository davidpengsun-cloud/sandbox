"""Durable checkpoints for governed questions, independent of their serving instance."""
from contextlib import contextmanager
from contextvars import ContextVar
import datetime
from decimal import Decimal
import json
import math
import time
import uuid

from kge.pipeline.settings import settings

_current = ContextVar("question_run", default=None)


class RunPending(BaseException):
    """Unwind the request after submission without holding an engine lease or worker."""


TIME_LIMIT = "This question exceeded its time limit; ask a narrower question."
RUN_TIME_LIMIT, WAREHOUSE_TIME_LIMIT, WAREHOUSE_FAILURE = "run_time_limit", "warehouse_time_limit", "warehouse_failure"
STOPPED, STOPPED_TEXT = "stopped", "Stopped."


def timeout():
    return positive("QUESTION_RUN_TIMEOUT", 900, 1200)


def interval():
    return positive("QUESTION_POLL_SECONDS", 2, 60)


def start_wait():
    """How long a new question's first response waits for its warehouse work before answering in the background."""
    raw = (settings().QUESTION_START_WAIT or "").strip() or "5"
    try:
        value = float(raw)
    except ValueError:
        value = -1
    if not math.isfinite(value) or not 0 <= value < 60:
        raise ValueError("QUESTION_START_WAIT must be a finite number of seconds, 0 or more and below 60")
    return value


def check_settings():
    """Refuse question-run settings the service could not use, before it starts."""
    timeout()
    interval()
    start_wait()


def positive(name, default, ceiling):
    try:
        value = float(getattr(settings(), name) or default)
    except ValueError:
        value = 0
    if not math.isfinite(value) or not 0 < value < ceiling:
        raise ValueError(f"{name} must be a finite number above 0 and below {ceiling} seconds")
    return value


def dumps(value):
    def typed(item):
        if isinstance(item, (datetime.datetime, datetime.date, Decimal)):
            return {"__kge_type__": type(item).__name__, "value": str(item)}
        raise TypeError(type(item).__name__)
    return json.dumps(value, default=typed)


def loads(value):
    def typed(item):
        types = {"datetime": datetime.datetime.fromisoformat, "date": datetime.date.fromisoformat, "Decimal": Decimal}
        if set(item) == {"__kge_type__", "value"} and item["__kge_type__"] in types:
            return types[item["__kge_type__"]](item["value"])
        return item
    return json.loads(value, object_hook=typed)


def read_record(record):
    """Keep answers completed by the run's deadline fetchable regardless of read time."""
    if record is not None and record.get("state") == "done" and "expires_at" in record:
        expires = record["expires_at"]
        completed = record.get("completed_at")
        if completed is None or completed > expires:
            record = {**record, "state": "expired",
                      "error": TIME_LIMIT, "reason": RUN_TIME_LIMIT,
                      "events": [event for event in record.get("events", []) if event.get("event") != "answer"]}
            record.pop("answer", None)
    return record


@contextmanager
def checkpoints(record, save):
    token = _current.set({"record": record, "save": save, "index": 0, "memo_index": 0})
    try:
        yield
    finally:
        _current.reset(token)


def active():
    return _current.get() is not None


def current_run_id() -> str | None:
    """The opaque id of the current durable run, if this pass belongs to one."""
    scope = _current.get()
    return scope["record"].get("id") if scope is not None else None


def remaining():
    scope = _current.get()
    return max(0, scope["record"]["expires_at"] - time.time()) if scope is not None else timeout()


def memo(key, compute):
    scope = _current.get()
    if scope is None:
        return compute()
    key = f"{scope['memo_index']}:{key}"
    scope["memo_index"] += 1
    held = scope["record"].setdefault("plan", {})
    if key not in held:
        held[key] = compute()
        scope["save"]()
    return held[key]


def memo_with_sidecar(key, compute):
    """Memo one bare value and its optional already-redacted observation in the same checkpoint.

    The bare value stays at the ordinary memo key for older workers. The sidecar lives separately under the same
    indexed key. A legacy memo without a sidecar returns None rather than recomputing evidence from a later epoch.
    Callers must supply only an already-redacted, serialisable observation; this primitive does not redact it.
    """
    scope = _current.get()
    if scope is None:
        return compute()
    key = f"{scope['memo_index']}:{key}"
    scope['memo_index'] += 1
    record = scope['record']
    held = record.setdefault('plan', {})
    if key not in held:
        value, sidecar = compute()
        sidecars = record.get('shadow_plan')
        had_sidecar = sidecars is not None and key in sidecars
        previous = sidecars.get(key) if had_sidecar else None
        held[key] = value
        if sidecar is not None:
            record.setdefault('shadow_plan', {})[key] = sidecar
        elif had_sidecar:
            del sidecars[key]
        try:
            scope['save']()
        except BaseException:
            del held[key]
            if sidecars is None:
                record.pop('shadow_plan', None)
            elif had_sidecar:
                sidecars[key] = previous
            else:
                sidecars.pop(key, None)
            raise
    return held[key], record.get('shadow_plan', {}).get(key)


def warehouse_once(key, read):
    """Reuse release or warehouse metadata within a run, including on another instance after a checkpoint."""
    scope = _current.get()
    if scope is None:
        return read()
    held = scope["record"].setdefault("warehouse_reads", {})
    key = json.dumps(key, separators=(",", ":"))
    if key not in held:
        held[key] = read()
    return held[key]


def _same_statement(job, sql, runtime_id, units):
    return (job["sql"] == sql and job["runtime"] == runtime_id
            and ("units" not in job or job["units"] == list(units)))


def next_statement_is(sql, runtime_id, units):
    """Check for a matching old job before replacing a warehouse step with release evidence."""
    scope = _current.get()
    if scope is None:
        return False
    jobs, index = scope["record"].get("jobs", ()), scope["index"]
    return index < len(jobs) and _same_statement(jobs[index], sql, runtime_id, units)


def remember_trace(part):
    scope = _current.get()
    if scope is not None:
        scope["record"].setdefault("trace_parts", []).append(part)
        scope["save"]()


def execute(warehouses, statement, runtime_id, measures, on_ready=None, on_stage=None):
    scope = _current.get()
    record = scope["record"]
    index = scope["index"]
    scope["index"] += 1
    jobs = record.setdefault("jobs", [])
    units = list(statement.get("units") or ([statement["unit"]] if statement.get("unit") else []))
    if index == len(jobs):
        jobs.append({"id": "ask_" + uuid.uuid4().hex, "sql": statement["sql"], "runtime": runtime_id, "units": units})
        scope["save"]()
    job = jobs[index]
    if not _same_statement(job, statement["sql"], runtime_id, units):
        raise ValueError("The question plan changed; ask the question again.")
    if "handle" not in job:
        job["handle"] = warehouses.submit(statement, runtime_id=runtime_id, measures=measures,
                                            job_id=job["id"], on_ready=on_ready, on_stage=on_stage)
        scope["save"]()
    if "result" not in job:
        raise RunPending()
    result = job["result"]
    return result["guard"], result["rows"], result["meta"]
