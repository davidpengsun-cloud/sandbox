"""Asking many questions at once: the one runner every job that asks a set of questions goes through (the A/B harness,
so make ab, make margin and the margin check onboard and test-drive run; make eval; make parity; test-drive's golden
check).

each(call, items) calls call on every item, EVAL_CONCURRENCY at a time (8 unset; kge/pipeline/settings.py
eval_concurrency), on a bounded pool of threads, and returns the results in the items' order, so what a caller writes
from them is what a serial run writes. Each call runs in a copy of the caller's context, so a run trace or a deadline
one call opens is its own. On the pilot nearly all of a question's time is spent waiting on BigQuery and Vertex AI, so
the questions wait side by side. EVAL_CONCURRENCY=1 calls them one at a time, in the caller's thread, as before.

A call that meets a failure that may pass if made again (kge/adapters/model/errors.py transient: a rate limit, a 5xx or
no answer from BigQuery or Vertex AI), raised from the call, or from what it was raised from (NoFallback on the pilot's
cloud path), or stepped around and recorded (kge/pipeline/trace.py transient_failure), is made again after a back-off
that doubles from BACKOFF_SECONDS, RETRIES times at most; its result is never the one returned. One that meets such a
failure on every try raises Unanswered, so the job stops rather than score a wrong answer."""
from __future__ import annotations

import random
import time
from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
from typing import Callable, Iterable, TypeVar

from kge.adapters.model.errors import transient
from kge.pipeline.settings import eval_concurrency
from kge.pipeline.trace import watching

RETRIES = 4
BACKOFF_SECONDS = 2.0
pause = time.sleep
Item = TypeVar("Item")
Result = TypeVar("Result")


class Unanswered(RuntimeError):
    """A question that met a failure that may pass if asked again on each of its tries: the job stops, and nothing
    counts it wrong."""


def again(exc: BaseException | None) -> bool:
    """Whether a failure, or one it was raised from, may pass if made again: NoFallback, which stops the step on the
    pilot's cloud path, is raised from the model call's own failure."""
    seen = set()
    while exc is not None and id(exc) not in seen:
        if transient(exc):
            return True
        seen.add(id(exc))
        exc = exc.__cause__ or (None if exc.__suppress_context__ else exc.__context__)
    return False


def retried(call: Callable[[Item], Result], item: Item, name: Callable[[Item], str] = str) -> Result:
    """call(item), made again after a back-off while it meets a failure that may pass if made again, RETRIES times at
    most; Unanswered, naming the item, once it has met one on every try."""
    met = None
    for attempt in range(RETRIES + 1):
        if attempt:
            pause(BACKOFF_SECONDS * 2 ** (attempt - 1) * (1 + random.random() / 2))
        try:
            with watching() as stepped_around:
                result = call(item)
        except Exception as exc:
            if not again(exc):
                raise
            met = f"{type(exc).__name__}: {(str(exc).splitlines() or [''])[0][:200]}"
            continue
        if not stepped_around:
            return result
        met = ", ".join(dict.fromkeys(stepped_around))
    raise Unanswered(f"{name(item)}: a failure that may pass if asked again, on each of {RETRIES + 1} tries (last: {met}): "
                     "nothing counts it wrong; run it again later")


def each(call: Callable[[Item], Result], items: Iterable[Item], name: Callable[[Item], str] = str,
         workers: int | None = None) -> list[Result]:
    """call on each item (retried), its results in the items' order: workers at a time (EVAL_CONCURRENCY unless given),
    each in a copy of the caller's context; with one, in the caller's thread."""
    items = list(items)
    workers = eval_concurrency() if workers is None else workers
    if workers <= 1 or len(items) <= 1:
        return [retried(call, item, name) for item in items]
    with ThreadPoolExecutor(max_workers=min(workers, len(items)), thread_name_prefix="asking") as pool:
        futures = [pool.submit(copy_context().run, retried, call, item, name) for item in items]
        try:
            return [f.result() for f in futures]
        finally:
            for f in futures:
                f.cancel()
