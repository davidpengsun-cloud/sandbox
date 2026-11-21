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
                      "error": TIME_LIMIT,
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
    if index == len(jobs):
        jobs.append({"id": "ask_" + uuid.uuid4().hex, "sql": statement["sql"], "runtime": runtime_id})
        scope["save"]()
    job = jobs[index]
    if job["sql"] != statement["sql"] or job["runtime"] != runtime_id:
        raise ValueError("The question plan changed; ask the question again.")
    if "handle" not in job:
        job["handle"] = warehouses.submit(statement, runtime_id=runtime_id, measures=measures,
                                            job_id=job["id"], on_ready=on_ready, on_stage=on_stage)
        scope["save"]()
    if "result" not in job:
        raise RunPending()
    result = job["result"]
    return result["guard"], result["rows"], result["meta"]
