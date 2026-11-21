"""Third-party telemetry and auto-instrumentation are off, with no exception: no library sends telemetry, usage pings,
crash reports or injected trace or correlation headers to anyone. Every SDK's own switch is turned off explicitly as a
process starts (off(), run as kge/pipeline/settings.py is imported, so by every entry point), whatever TRACING says.
KGE's own tracing (kge/adapters/tracing/, off unless TRACING turns it on) is the only tracing, and exports only what its
span processor allows.

Trace context leaves the process only where KGE's own code adds it, at the tracing port (kge/pipeline/trace.py
trace_headers), and only to the destinations TRACE_PROPAGATE_TO lists: OpenTelemetry's global propagator is a no-op, so
no SDK or HTTP client can inject a traceparent, tracestate or baggage header of its own."""
from __future__ import annotations

import os

from opentelemetry import propagate
from opentelemetry.propagators.composite import CompositePropagator

SWITCHED_OFF = {
    "ANTHROPIC_OPEN_TELEMETRY": "false",
    "ANTHROPIC_OPEN_TELEMETRY_PROPAGATION": "false",
    "FASTAPI_OTEL_AUTO_CONFIGURE": "false",
    "LANGSMITH_TRACING": "false",
    "LANGSMITH_TRACING_V2": "false",
    "LANGCHAIN_TRACING": "false",
    "LANGCHAIN_TRACING_V2": "false",
    "LANGSMITH_OTEL_ENABLED": "false",
    "LANGCHAIN_OTEL_ENABLED": "false",
    "OTEL_PYTHON_DISABLED_INSTRUMENTATIONS": "all",
    "HF_HUB_DISABLE_TELEMETRY": "1",
    "DO_NOT_TRACK": "1",
}
"""Each environment switch a library in the image reads for its own telemetry, tracing or auto-instrumentation, set off;
a value already set otherwise is overwritten."""

CLEARED = ("OTEL_PYTHON_TRACER_PROVIDER", "OTEL_PYTHON_METER_PROVIDER", "OTEL_PYTHON_LOGGER_PROVIDER",
           "OTEL_EXPORTER_OTLP_TRACES_HEADERS", "OTEL_EXPORTER_OTLP_HEADERS")
"""OpenTelemetry's settings that name a provider class for the API to load, or headers an OTLP exporter adds to every
export: unset, so only KGE's own tracing sets a provider, and an export carries only the headers KGE gives it."""

FASTAPI_TELEMETRY_OFF = {"tracing": False, "operation_spans": False, "metrics": False, "logs": False,
                         "auto_configure": False}
"""FastAPI's own telemetry, passed to the app as it is built (kge/app.py)."""

ANTHROPIC_TELEMETRY_OFF = {"traces": False, "propagation": False}
"""The Anthropic SDK's own spans and trace header, passed to its client as it is built (kge/adapters/model/)."""

TRACE_HEADERS = frozenset({
    "traceparent", "tracestate", "baggage", "x-cloud-trace-context", "grpc-trace-bin", "b3", "x-b3-traceid",
    "x-b3-spanid", "x-b3-parentspanid", "x-b3-sampled", "x-b3-flags", "uber-trace-id", "sentry-trace",
    "x-datadog-trace-id", "x-datadog-parent-id", "x-datadog-sampling-priority", "x-amzn-trace-id", "request-id",
    "request-context", "elastic-apm-traceparent", "newrelic", "x-request-id", "x-correlation-id",
})
"""Trace and correlation headers: none leaves the process but the traceparent the tracing port adds itself
(kge/adapters/http.py removes every other a caller or a library put on a request)."""

NO_PROPAGATION = CompositePropagator([])


def off() -> None:
    """Every switch in SWITCHED_OFF off, every setting in CLEARED unset, and OpenTelemetry's global propagator one that
    injects and extracts nothing."""
    os.environ.update(SWITCHED_OFF)
    for name in CLEARED:
        os.environ.pop(name, None)
    propagate.set_global_textmap(NO_PROPAGATION)
