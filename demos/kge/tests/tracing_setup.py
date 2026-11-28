"""Tracing set up for a test as the service sets it up (kge/adapters/tracing/ install), on an exporter the test holds,
and cleared again: OpenTelemetry's global tracer provider is set once per process, so a test that sets it clears it
after itself, and the next test starts from tracing off."""
from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

from opentelemetry import trace
from opentelemetry.util._once import Once


def cleared() -> None:
    """Tracing off again: the installed provider shut down, no global tracer provider, and none installed for the
    flush."""
    from kge.adapters import tracing
    installed = tracing._set_up.get("provider")
    if installed is not None:
        installed.provider.shutdown()
    trace._TRACER_PROVIDER_SET_ONCE = Once()
    trace._TRACER_PROVIDER = None
    tracing._set_up.clear()


@contextmanager
def tracing_to(exporter, *, batch: bool = False) -> Iterator:
    """Spans exported to exporter inside the block, through the safe-keys processor as on the service; batched as the
    OTLP and Cloud Trace exporters batch, where batch is set."""
    from kge.adapters import tracing
    from kge.adapters.tracing.otel import Traced
    cleared()
    built = Traced(exporter, batch=batch)
    tracing.install(built)
    try:
        yield built
    finally:
        cleared()
