"""Traces: the OpenTelemetry API (opentelemetry-api) is the port, called directly by every caller (kge/pipeline/trace.py
names KGE's tracer, SCOPE); the exporters are the adapters, chosen by TRACING in one setup at start-up (setup). Unset or
off, the default, nothing is set up: no tracer provider, processor or exporter of the SDK, and the API's own no-op
tracer records nothing (kge's SDK module, otel.py, is never imported; another library may still import the SDK).
console writes spans to standard error, otlp sends them to the OTLP endpoint over HTTP that TRACING_ENDPOINT names
(http://localhost:4318/v1/traces unset), cloudtrace to Cloud Trace's OTLP endpoint, signed in as the runtime's service
account. Any other value is refused, naming the setting. Callers never ask which it is.

A span carries names, ids, timings, counts, sizes and outcomes alone: a span processor before every exporter
(otel.py SafeSpans) keeps the attributes SAFE_KEYS names and drops any other, and a string longer than SAFE_TEXT, so no
question, SQL, row value, document text, person's name or email, prompt or reply reaches an exporter."""
from __future__ import annotations

import threading
import urllib.parse

from kge.pipeline.settings import settings

__all__ = ["FLUSH_SECONDS", "SAFE_KEYS", "TracingSettingError", "check_settings", "destinations", "flush", "origin_of", "safe",
           "setup"]

MODES = ("off", "console", "otlp", "cloudtrace")
DEFAULT_ENDPOINT = "http://localhost:4318/v1/traces"
CLOUD_TRACE_ENDPOINT = "https://telemetry.googleapis.com/v1/traces"
SAFE_TEXT = 200
FLUSH_SECONDS = 2.0
SAFE_KEYS = frozenset({
    "kge.path", "kge.run_id", "kge.question_run", "kge.stage", "kge.arm", "kge.outcome", "kge.error",
    "kge.duration_ms", "kge.pass",
    "gen_ai.request.model", "gen_ai.response.model", "gen_ai.usage.input_tokens", "gen_ai.usage.output_tokens",
    "gen_ai.usage.reasoning_tokens", "kge.call",
    "db.system.name", "kge.warehouse.job_id", "kge.warehouse.state", "kge.warehouse.bytes_processed",
    "kge.warehouse.slot_ms", "kge.warehouse.statement", "kge.warehouse.kind",
    "http.request.method", "http.route", "http.response.status_code",
})

_DONE = threading.Event()
_DONE.set()
_set_up: dict = {}


class TracingSettingError(ValueError):
    """TRACING names no tracing this image offers."""


def mode() -> str:
    """TRACING as written, one of MODES in lower case (unset or empty: off), the portal's rule too."""
    raw = (settings().TRACING or "").strip() or "off"
    if raw not in MODES:
        raise TracingSettingError("TRACING must be off, console, otlp or cloudtrace")
    return raw


def endpoint() -> str:
    """TRACING_ENDPOINT, read in otlp mode alone: an http or https address with a host, a numeric port if any, no
    whitespace or control character inside it, and no query or fragment (unset, empty or blank: the default), the
    portal's rule too."""
    raw = (settings().TRACING_ENDPOINT or "").strip()
    if not raw:
        return DEFAULT_ENDPOINT
    parts = urllib.parse.urlsplit(raw)
    spaced = any(c.isspace() or ord(c) < 32 or ord(c) == 127 for c in raw)
    if (spaced or parts.scheme not in ("http", "https") or not parts.hostname or not numeric_port(parts) or "?" in raw
            or "#" in raw):
        raise TracingSettingError("TRACING_ENDPOINT must be an http or https OTLP traces address, such as "
                                  "http://localhost:4318/v1/traces")
    return raw


def numeric_port(parts: urllib.parse.SplitResult) -> bool:
    """Whether the address's port, where it names one, is a number in range."""
    try:
        return parts.port is None or parts.port >= 0
    except ValueError:
        return False


def origin_of(url: str) -> str | None:
    """url's origin, scheme://host[:port] in lower case, the port left out where it is the scheme's default; None for
    anything but an http or https address with a host."""
    try:
        parts = urllib.parse.urlsplit(url.strip())
        port = parts.port
    except ValueError:
        return None
    scheme = parts.scheme.lower()
    if scheme not in ("http", "https") or not parts.hostname:
        return None
    host = f"[{parts.hostname}]" if ":" in parts.hostname else parts.hostname.lower()
    default = 443 if scheme == "https" else 80
    return f"{scheme}://{host}" + (f":{port}" if port not in (None, default) else "")


def destinations() -> frozenset[str]:
    """TRACE_PROPAGATE_TO: the origins (comma-separated http or https addresses; a path, if any, is ignored) an outgoing
    call carries the trace context to. Unset or empty, the default: none, so no call carries one. Anything else is
    refused, naming the setting."""
    raw = (settings().TRACE_PROPAGATE_TO or "").strip()
    found = set()
    for item in filter(None, (part.strip() for part in raw.split(","))):
        origin = origin_of(item)
        if origin is None or any(c.isspace() or ord(c) < 32 for c in item) or "?" in item or "#" in item:
            raise TracingSettingError("TRACE_PROPAGATE_TO must be a comma-separated list of http or https addresses, "
                                      "such as https://kge.example.com")
        found.add(origin)
    return frozenset(found)


def check_settings() -> None:
    """Refuse a mistyped TRACING, or an exporter that cannot start (Cloud Trace with no credentials or project), and set
    tracing up, before the service starts rather than on its first request; and a mistyped TRACE_PROPAGATE_TO."""
    destinations()
    if mode() == "off":
        return
    try:
        setup()
    except TracingSettingError:
        raise
    except Exception as exc:
        raise TracingSettingError(f"TRACING={mode()}: the tracer could not start ({type(exc).__name__}: {exc})") from None


def safe(attributes: dict | None) -> dict:
    """The attributes a span may carry: SAFE_KEYS alone, each a number, a flag or a short string, never None."""
    kept = {}
    for key, value in (attributes or {}).items():
        if key not in SAFE_KEYS or value is None:
            continue
        if isinstance(value, (bool, int, float)) or (isinstance(value, str) and len(value) <= SAFE_TEXT):
            kept[key] = value
    return kept


def setup() -> None:
    """The one setup: with TRACING on, the SDK's tracer provider with the exporter it names, behind the safe-keys span
    processor, as OpenTelemetry's global provider, once per process; with it off, nothing."""
    chosen = mode()
    if chosen == "off" or _set_up:
        return
    from kge.adapters.tracing.otel import provider_for_mode
    install(provider_for_mode(chosen, endpoint() if chosen == "otlp" else None))


def install(provider) -> None:
    """provider (otel.py traced) as OpenTelemetry's global tracer provider, and the one flush exports through."""
    from opentelemetry import trace
    trace.set_tracer_provider(provider.provider)
    _set_up["provider"] = provider


def flush() -> threading.Event:
    """Start exporting the spans ended so far, on a thread of its own, and return the event set once it is done: a
    caller waits for it as long as it chooses (FLUSH_SECONDS before a response completes, since Cloud Run gives an
    instance no CPU once its response is sent), and never longer, whatever the exporter does. Already set with tracing
    off."""
    provider = _set_up.get("provider")
    return _DONE if provider is None else provider.flush()
