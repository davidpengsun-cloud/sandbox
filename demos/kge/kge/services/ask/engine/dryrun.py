"""ask's own routing and declines without a warehouse query or a model call (#707): get_context's open items run ask as
it is, on the deployment's own Combined, and stop where ask would send a warehouse its first query or a model its first
request. What ask decides before that point, a definition it cites, a refusal, a period it has not released or a plan
it cannot make, is ask's own decision in ask's own words; a question that reaches a query is one ask answers, or
declines only on what the query finds; one that reaches a model is one ask's model settles.

The release calendar is read from the calendar the engine pinned as it opened (Graph.release_periods), the rows ask's
own calendar query reads, so a period ask would refuse is refused here in the same words; where the chosen runtime's
calendar was not pinned, or is no longer current, ask would query the warehouse for it, so the dry run stops there.
Outside dry_run nothing here changes what ask does."""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from typing import Callable

DRY = ContextVar("kge_ask_dry_run", default=None)
"""In a dry run, whether a runtime's pinned release calendar is current (Graph.release_calendar_current); else None."""


class WouldRun(BaseException):
    """ask reached a warehouse query. A BaseException, so no handler on ask's path that turns a failed query into a
    decline mistakes it for one."""


class WouldAskModel(WouldRun):
    """ask reached a model request: what it decides from there is its model's."""


def dry() -> bool:
    """Whether this context runs ask dry."""
    return DRY.get() is not None


def calendar_current(runtime_id: str) -> bool:
    """In a dry run, whether the runtime's pinned release calendar is the one ask's own calendar query would read."""
    current = DRY.get()
    return current is not None and current(runtime_id)


def stop_before_query() -> None:
    """Where ask sends a warehouse a query: stop there when running dry."""
    if DRY.get() is not None:
        raise WouldRun()


def stop_before_model() -> None:
    """Where ask sends a model a request: stop there when running dry."""
    if DRY.get() is not None:
        raise WouldAskModel()


@contextmanager
def dry_run(current: Callable[[str], bool]):
    """Run ask in this context without a warehouse query or a model call; current says whether a runtime's pinned
    release calendar is current."""
    token = DRY.set(current)
    try:
        yield
    finally:
        DRY.reset(token)
