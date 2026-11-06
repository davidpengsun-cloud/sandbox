"""Model I/O bounded by its own total and the shared answering request clock."""
from __future__ import annotations

import time
from concurrent.futures import Future, TimeoutError as FutureTimeout
from contextvars import copy_context
from threading import Thread

from kge.adapters.deadline import (MIN_CALL_SECONDS, _deadline, request_remaining,
                                   request_budget as request_budget, request_timeout as request_timeout)
from kge.adapters.model.errors import ExternalCallError, model_total_timeout

def request_model_call(call):
    """Wait at most the lesser of this call's total and the request's remaining time.

    A daemon may finish its current I/O after expiry; it holds no engine resources
    and cannot publish a late answer. Outside a request the existing path remains.
    """
    remaining = request_remaining(MIN_CALL_SECONDS)
    if remaining is None:
        return call()
    expires = min(time.monotonic() + model_total_timeout(), _deadline.get())
    outcome = Future()
    context = copy_context()

    def run():
        try:
            request_remaining(MIN_CALL_SECONDS)
            outcome.set_result(call())
        except BaseException as exc:
            outcome.set_exception(exc)

    Thread(target=context.run, args=(run,), daemon=True, name="ask-model").start()
    try:
        result = outcome.result(timeout=max(0, expires - time.monotonic()))
    except FutureTimeout:
        request_remaining()
        if outcome.done():
            return outcome.result()
        raise ExternalCallError("Model call deadline exceeded") from None
    request_remaining()
    if time.monotonic() >= expires:
        raise ExternalCallError("Model call deadline exceeded")
    return result
