"""Model I/O bounded by its own total and the shared answering request clock."""
from __future__ import annotations

import time
from concurrent.futures import Future, TimeoutError as FutureTimeout
from contextvars import copy_context
from threading import Thread

from kge.adapters.deadline import (MIN_CALL_SECONDS, _deadline, request_remaining,
                                   request_budget as request_budget, request_timeout as request_timeout)
from kge.adapters.model.errors import ExternalCallError, model_total_timeout
from kge.pipeline.trace import check_cancelled, is_cancel_scope_active

def request_model_call(call):
    """Wait at most the lesser of this call's total and the request's remaining time.

    A daemon may finish its current I/O after expiry; it holds no engine resources
    and cannot publish a late answer. Outside a request the existing path remains.
    """
    check_cancelled()
    remaining = request_remaining(MIN_CALL_SECONDS)
    if remaining is None and not is_cancel_scope_active():
        return call()
    expires = time.monotonic() + model_total_timeout()
    if _deadline.get() is not None:
        expires = min(expires, _deadline.get())
    outcome = Future()
    context = copy_context()

    def run():
        try:
            check_cancelled()
            request_remaining(MIN_CALL_SECONDS)
            outcome.set_result(call())
        except BaseException as exc:
            outcome.set_exception(exc)

    Thread(target=context.run, args=(run,), daemon=True, name="ask-model").start()
    while True:
        check_cancelled()
        try:
            result = outcome.result(timeout=min(.05, max(0, expires - time.monotonic())))
            break
        except FutureTimeout:
            check_cancelled()
            request_remaining()
            if outcome.done():
                result = outcome.result()
                break
            if time.monotonic() >= expires:
                raise ExternalCallError("Model call deadline exceeded") from None
    check_cancelled()
    request_remaining()
    if time.monotonic() >= expires:
        raise ExternalCallError("Model call deadline exceeded")
    return result
