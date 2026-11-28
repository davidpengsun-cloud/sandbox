"""Capture schema rows already read during this request for CPU-only shadow comparison."""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar

_columns = ContextVar('shadow_columns', default=None)


@contextmanager
def capture():
    """Hold each unit's already-read schema for this request, never across requests."""
    result = {}
    token = _columns.set(result)
    try:
        yield result
    finally:
        _columns.reset(token)


def note(unit, rows):
    """Keep immutable copies when a capture is active; no additional warehouse read."""
    result = _columns.get()
    if result is not None:
        result[unit] = tuple(tuple(row) for row in rows)
