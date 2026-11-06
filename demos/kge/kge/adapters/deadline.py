"""One request clock shared by model, graph and warehouse adapters.

Only remote I/O runs in a daemon: it owns no graph lease or local connection.
Server job deadlines stop remote work whose response arrives after the request.
"""
from __future__ import annotations

import math
import time
from concurrent.futures import Future, TimeoutError as FutureTimeout
from contextlib import contextmanager
from contextvars import ContextVar, copy_context
from functools import wraps
from threading import Thread

from kge.pipeline.settings import settings


class ExternalCallError(RuntimeError):
    """A call to an outside service failed: its text is one line, safe to show and to log. The API answers it with a
    503 (kge/app.py); the improvement job records it as a failed edit (kge/improve/edits.py). transient says the same
    call may pass if made again: no answer, a timeout, HTTP 429 or a 5xx; never a refusal such as a bad key (401, 403)
    or a bad request (400, 404)."""

    def __init__(self, message: str = "", transient: bool = False):
        super().__init__(message)
        self.transient = transient


class RequestDeadlineExceeded(ExternalCallError):
    """A request exhausted its budget; never retried or replaced by rules."""

    def __init__(self):
        super().__init__("The request took longer than its time budget; try again.")


MIN_CALL_SECONDS = 0.05
_deadline: ContextVar[float | None] = ContextVar("ask_deadline", default=None)


def request_timeout() -> float:
    """The image's 270-second request budget, with a positive override below 300."""
    raw = (settings().MODEL_REQUEST_TIMEOUT or "").strip() or "270"
    try:
        value = float(raw)
    except ValueError:
        value = 0
    if not math.isfinite(value) or not 0 < value < 300:
        raise ValueError("MODEL_REQUEST_TIMEOUT must be a finite number of seconds above 0 and below 300")
    return value


def request_remaining(floor: float = 0) -> float | None:
    """Time left, or no request scope; refuse an exhausted or unusably small budget."""
    expires = _deadline.get()
    if expires is None:
        return None
    remaining = expires - time.monotonic()
    if remaining <= 0 or remaining < floor:
        raise RequestDeadlineExceeded()
    return remaining


@contextmanager
def request_scope():
    """Share one clock across ASGI tasks and their workers; nested requests cannot replenish it."""
    token = None
    if _deadline.get() is None:
        token = _deadline.set(time.monotonic() + request_timeout())
    try:
        request_remaining()
        yield
    finally:
        if token is not None:
            _deadline.reset(token)


def request_budget(call):
    """Count all service work against one clock; nested scopes cannot replenish it."""
    @wraps(call)
    def bounded(*args, **kwargs):
        with request_scope():
            try:
                result = call(*args, **kwargs)
            except Exception:
                request_remaining()
                raise
            request_remaining()
            return result
    return bounded


def request_cap(seconds: float) -> float:
    """Keep an operation's own limit outside a request; cap it inside one."""
    remaining = request_remaining(MIN_CALL_SECONDS)
    return seconds if remaining is None else min(seconds, remaining)


def query_budget(body: dict, seconds: float) -> None:
    """Bound BigQuery's wait and job lifetime only while serving a request."""
    remaining = request_remaining(MIN_CALL_SECONDS)
    if remaining is not None:
        millis = max(1, int(min(seconds, remaining) * 1000))
        body["timeoutMs"] = min(int(body.get("timeoutMs", millis)), millis)
        body["jobTimeoutMs"] = str(min(int(body.get("jobTimeoutMs", millis)), millis))


def request_io(call):
    """Bound remote I/O, including slow reads; never leave engine work in a worker."""
    @wraps(call)
    def bounded(*args, **kwargs):
        remaining = request_remaining(MIN_CALL_SECONDS)
        if remaining is None:
            return call(*args, **kwargs)
        expires = _deadline.get()
        outcome = Future()
        context = copy_context()

        def run():
            try:
                request_remaining(MIN_CALL_SECONDS)
                outcome.set_result(call(*args, **kwargs))
            except BaseException as exc:
                outcome.set_exception(exc)

        Thread(target=context.run, args=(run,), daemon=True, name="ask-query").start()
        try:
            result = outcome.result(timeout=max(0, expires - time.monotonic()))
        except FutureTimeout:
            request_remaining()
            if outcome.done():
                return outcome.result()
            raise RequestDeadlineExceeded() from None
        except Exception:
            request_remaining()
            raise
        request_remaining()
        return result
    return bounded
