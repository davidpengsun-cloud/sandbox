"""Tracing on: OpenTelemetry's SDK, set up once (kge/adapters/tracing/__init__.py setup) as the global tracer provider,
sampling every question, and one exporter: the console's (standard error, each span as it ends), an OTLP endpoint's
over HTTP, or Cloud Trace's OTLP endpoint, signed in with google-auth's application default credentials, the runtime's
service account. Every question is recorded and exported: the sampler is ALWAYS_ON, never the parent's choice, so a
caller's unsampled traceparent keeps its trace id but cannot drop the question's spans. The OTLP exporters batch, and
the HTTP middleware flushes before each question's response completes (flush), waiting FLUSH_SECONDS at most, since
Cloud Run gives an instance no CPU once its response is sent.

Between the spans and every exporter sits SafeSpans, a span processor: only KGE's own spans (SCOPE) pass, each with the
attributes SAFE_KEYS allows and nothing else, no events, and a status without its description, so another library's
spans, an exception's message or a key a caller should not have set never reach an exporter; a span's name is kept only
in the shapes KGE names spans (NAMED: kge.<part>, or an HTTP method and a route template), else "span", and its scope
is rebuilt as KGE's name alone."""
from __future__ import annotations

import re
import sys
import threading

from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import ReadableSpan, SpanProcessor, TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter, SimpleSpanProcessor, SpanExporter
from opentelemetry.sdk.trace.sampling import ALWAYS_ON
from opentelemetry.sdk.util.instrumentation import InstrumentationScope
from opentelemetry.trace import Link, Status

from kge.adapters.tracing import CLOUD_TRACE_ENDPOINT, FLUSH_SECONDS, safe
from kge.pipeline.settings import settings
from kge.pipeline.trace import SCOPE, register_telemetry, span_telemetry, telemetry_current

SERVICE = "kge"
NAMED = re.compile(r"kge(\.[a-z_]+)+|[A-Z]{3,7}( /[A-Za-z0-9_/{}:.-]{0,120})?")


class SafeSpans(SpanProcessor):
    """The safe-keys rule as a span processor in front of the exporting one (exporting): KGE's spans alone, each
    rebuilt with SAFE_KEYS' attributes (safe), its links' likewise, no events, and its status code without a
    description."""

    def __init__(self, exporting: SpanProcessor):
        self.exporting = exporting

    def on_start(self, span, parent_context=None) -> None:
        current = telemetry_current()
        if current:
            register_telemetry(span, current["kge.client"], current["kge.agent"], current["gen_ai.conversation.id"])
        self.exporting.on_start(span, parent_context=parent_context)

    def on_end(self, span: ReadableSpan) -> None:
        scope = span.instrumentation_scope
        if scope is None or scope.name != SCOPE:
            return
        attributes = dict(span.attributes or {})
        attributes.pop("kge.client", None)
        attributes.pop("kge.agent", None)
        attributes.pop("gen_ai.conversation.id", None)
        attributes.pop("kge.telemetry.origin", None)
        attributes.update(span_telemetry(span.context.span_id, pop=True) if span.context else {})
        project = span.resource.attributes.get("gcp.project_id") if span.resource else None
        resource = {"service.name": SERVICE}
        if isinstance(project, str) and re.fullmatch(r"[a-z][0-9a-z-]{4,28}[a-z0-9]", project):
            resource["gcp.project_id"] = project
        self.exporting.on_end(ReadableSpan(
            name=span.name if NAMED.fullmatch(span.name) else "span", context=span.context, parent=span.parent, resource=Resource(resource),
            attributes=safe(attributes),
            links=[Link(link.context, safe(dict(link.attributes or {}))) for link in span.links],
            kind=span.kind, status=Status(span.status.status_code), start_time=span.start_time, end_time=span.end_time,
            instrumentation_scope=InstrumentationScope(SCOPE)))

    def shutdown(self) -> None:
        self.exporting.shutdown()

    def force_flush(self, timeout_millis: int = 30000) -> bool:
        return self.exporting.force_flush(timeout_millis)


class Traced:
    """The SDK's tracer provider for exporter, behind SafeSpans, and its bounded flush."""

    def __init__(self, exporter: SpanExporter, *, batch: bool, resource: dict | None = None):
        self.provider = TracerProvider(resource=Resource({"service.name": SERVICE, **(resource or {})}),
                                       sampler=ALWAYS_ON)
        exporting = BatchSpanProcessor(exporter) if batch else SimpleSpanProcessor(exporter)
        self.provider.add_span_processor(SafeSpans(exporting))
        self._flush_lock = threading.Lock()
        self._running: threading.Event | None = None
        self._next: threading.Event | None = None

    def flush(self) -> threading.Event:
        """The SDK's force_flush on a thread of its own: its batch processor does not hold to the timeout it is given,
        so the caller bounds its own wait on the event instead. One flush thread at a time, and a caller's event is
        always a flush that starts after it asked: with none under way it starts one; with one under way, which may
        have drained the queue before the caller's span ended, it is given the next flush, which that thread runs as
        soon as the current one ends, shared by every caller that comes meanwhile."""
        with self._flush_lock:
            if self._running is None:
                self._running = threading.Event()
                threading.Thread(target=self._flushes, daemon=True, name="trace-flush").start()
                return self._running
            if self._next is None:
                self._next = threading.Event()
            return self._next

    def _flushes(self) -> None:
        while True:
            try:
                self.provider.force_flush(int(FLUSH_SECONDS * 1000))
            except Exception:
                pass
            with self._flush_lock:
                self._running.set()
                self._running, self._next = self._next, None
                if self._running is None:
                    return


def provider_for_mode(mode: str, endpoint: str | None) -> Traced:
    """The exporter TRACING names, the adapter: console, otlp (to endpoint) or cloudtrace."""
    if mode == "store":
        from kge.adapters.telemetry.store import Exporter, for_profile
        from kge.pipeline.profile import resolve
        return Traced(Exporter(for_profile(resolve())), batch=True)
    if mode == "console":
        return Traced(ConsoleSpanExporter(out=sys.stderr), batch=False)
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
    if mode == "otlp":
        return Traced(OTLPSpanExporter(endpoint=endpoint), batch=True)
    session, project = cloud_session()
    return Traced(OTLPSpanExporter(endpoint=CLOUD_TRACE_ENDPOINT, session=session), batch=True,
                  resource={"gcp.project_id": project})


def cloud_session():
    """A requests session signed in as the runtime's service account, and the project its spans are written to: the
    project the service names (VERTEX_PROJECT, else BIGQUERY_PROJECT), else the credentials' own."""
    import google.auth
    from google.auth.transport.requests import AuthorizedSession
    credentials, found = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
    project = (settings().VERTEX_PROJECT or settings().BIGQUERY_PROJECT or found or "").strip()
    if not project:
        raise ValueError("TRACING=cloudtrace needs a project: set VERTEX_PROJECT")
    return AuthorizedSession(credentials), project
