"""Third-party telemetry is off, with no exception (#475): every SDK's own switch is off once a process starts, no
auto-instrumentation is installed or loaded, and no outgoing call from a model, warehouse, graph store, object store or
secrets adapter carries trace context, tracing on or off, unless its destination is one TRACE_PROPAGATE_TO lists, and
then the traceparent alone. Calls are caught at the transports themselves (urllib's handlers and the Anthropic SDK's
HTTP transport), so a header any library added on its own would be seen."""
from __future__ import annotations

import email.message
import importlib.metadata
import importlib.util
import io
import json
import re
import subprocess
import sys
import urllib.request
import urllib.response
from pathlib import Path
from types import SimpleNamespace

import httpx2
import pytest
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from kge.pipeline import telemetry
from kge.pipeline import trace as recording
from tests.tracing_setup import cleared, tracing_to

DEMO = Path(__file__).resolve().parents[2]
TRACE_HEADERS = telemetry.TRACE_HEADERS
FORBIDDEN = ("sentry_sdk", "posthog", "ddtrace", "newrelic", "logfire", "traceloop", "openlit", "langfuse", "wandb",
             "analytics", "mixpanel", "bugsnag", "rollbar", "honeybadger", "elasticapm", "applicationinsights",
             "opentelemetry.instrumentation", "opentelemetry.distro", "opentelemetry.auto_instrumentation")
LISTED = "https://kge.example.com"


def test_every_third_party_switch_is_off_at_start_up():
    """A process started with every switch on (as an operator's .env might set them) has each off once the settings
    module is imported, as every entry point imports it; the global propagator then injects nothing into a call."""
    code = ("import json, os\n"
            "import kge.pipeline.settings\n"
            "from kge.pipeline.telemetry import SWITCHED_OFF\n"
            "from opentelemetry import propagate, trace\n"
            "from opentelemetry.trace import NonRecordingSpan, SpanContext, TraceFlags\n"
            "span = NonRecordingSpan(SpanContext(0x4bf92f3577b34da6a3ce929d0e0e4736, 0x00f067aa0ba902b7, False,"
            " TraceFlags(1)))\n"
            "carrier = {}\n"
            "propagate.inject(carrier, context=trace.set_span_in_context(span))\n"
            "from kge.pipeline.telemetry import CLEARED\n"
            "print(json.dumps({'env': {k: os.environ.get(k) for k in [*SWITCHED_OFF, *CLEARED]}, 'injected': carrier}))\n")
    on = {name: "true" for name in telemetry.SWITCHED_OFF} | {name: "planted.Provider" for name in telemetry.CLEARED}
    env = {k: v for k, v in __import__("os").environ.items() if k != "TRACING"} | on | {"PYTHONPATH": str(DEMO)}
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=env, check=True, cwd=DEMO)
    seen = json.loads(out.stdout)
    assert seen["env"] == telemetry.SWITCHED_OFF | dict.fromkeys(telemetry.CLEARED)
    assert seen["injected"] == {}


def test_no_propagation_preserves_context_for_fastapi_with_sdk_enabled():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from opentelemetry import context, propagate
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    telemetry.off()
    current = context.get_current()
    supplied = context.set_value("local-test", "kept")
    assert telemetry.NO_PROPAGATION.extract({"traceparent": "forged"}) is current
    assert telemetry.NO_PROPAGATION.extract({}, context=supplied) is supplied
    carrier = {}
    propagate.inject(carrier, context=supplied)
    assert carrier == {}
    exporter = InMemorySpanExporter()
    with tracing_to(exporter):
        for _ in range(2):
            app = FastAPI()
            @app.get("/health")
            def health():
                return {"ok": True}
            with TestClient(app) as client:
                assert client.get("/health", headers={"traceparent": "00-" + "a" * 32 + "-" + "b" * 16 + "-01"}).json() == {"ok": True}
        assert all(span.context.trace_id != int("a" * 32, 16) for span in exporter.get_finished_spans())


def test_no_auto_instrumentation_or_telemetry_library_is_installed_or_loaded():
    import kge.app
    for group in ("opentelemetry_instrumentor", "opentelemetry_distro", "opentelemetry_configurator",
                  "opentelemetry_pre_instrument", "opentelemetry_post_instrument"):
        assert not list(importlib.metadata.entry_points(group=group)), group
    for name in FORBIDDEN:
        try:
            found = importlib.util.find_spec(name)
        except ModuleNotFoundError:
            found = None
        assert found is None, f"{name} is installed: a telemetry or auto-instrumentation library"
    assert not [m for m in sys.modules if m.startswith("opentelemetry.instrumentation")]
    assert kge.app.app._telemetry | telemetry.FASTAPI_TELEMETRY_OFF == kge.app.app._telemetry
    assert not kge.app.app._native_telemetry.enabled()


HTTP_CLIENTS = re.compile(r"\bhttpx\.(?:get|post|put|patch|delete|head|options|request|stream|Client|AsyncClient)\b"
                          r"|\brequests\.(?:get|post|put|patch|delete|head|options|request|Session)\("
                          r"|\bAuthorizedSession\b|^\s*(?:import|from) requests\b", re.MULTILINE)
CLIENT_ALLOWED = {
    "kge/adapters/http.py": "the one transport: http_client and async_http_client strip and add as open_http does",
    "kge/adapters/tracing/otel.py": "the Cloud Trace exporter's AuthorizedSession: spans to Cloud Trace, no propagator",
}


def test_every_outgoing_call_in_kge_goes_through_the_one_transport():
    """urllib is opened only in kge/adapters/http.py (open_http), and an httpx, requests or google-auth AuthorizedSession
    client is made only there and in the Cloud Trace exporter, so the trace context is decided in one place."""
    sources = {str(p.relative_to(DEMO)): p.read_text() for p in (DEMO / "kge").rglob("*.py")}
    opened = sorted(path for path, text in sources.items()
                    if path != "kge/adapters/http.py" and any(s in text for s in ("urlopen(", "build_opener(", "http.client")))
    assert opened == []
    clients = sorted(path for path, text in sources.items() if path not in CLIENT_ALLOWED and HTTP_CLIENTS.search(text))
    assert clients == []
    assert all(HTTP_CLIENTS.search(sources[path]) for path in CLIENT_ALLOWED), "an allowance no longer used"
    built = sorted(f"{path}: {call[:60]}" for path, text in sources.items() for call in sdk_clients(text)
                   if "http_client=sdk_http_client(" not in call)
    assert built == [], "an SDK client is built without kge/adapters/http.py's sdk_http_client"
    assert any(sdk_clients(text) for text in sources.values()), "the SDK check finds the clients it checks"


SDK_CLIENT = re.compile(r"\b(?:Anthropic\w*|AsyncAnthropic\w*|OpenAI|AsyncOpenAI|AzureOpenAI)\(")


def sdk_clients(text: str) -> list[str]:
    """Each call building an SDK client (Anthropic's, OpenAI's) in text, from its name to its closing parenthesis."""
    calls = []
    for found in SDK_CLIENT.finditer(text):
        depth, end = 0, found.end() - 1
        for end in range(found.end() - 1, len(text)):
            depth += {"(": 1, ")": -1}.get(text[end], 0)
            if depth == 0:
                break
        calls.append(text[found.start():end + 1])
    return calls


def test_the_sdk_client_sends_kge_user_agent_and_no_stainless_headers(monkeypatch):
    """The Anthropic SDK, left to itself, sends its X-Stainless-* headers (its version, the runtime, the machine) and a
    User-Agent naming it; Claude on Vertex AI as KGE builds it sends KGE's User-Agent and none of them."""
    from anthropic import AnthropicVertex

    from kge.adapters.http import USER_AGENT
    from kge.adapters.model.providers import LLM
    calls = []

    def seen(transport, request):
        calls.append({k.lower(): v for k, v in request.headers.items()})
        return httpx2.Response(200, json={"id": "m", "type": "message", "role": "assistant", "model": "claude",
                                          "content": [{"type": "text", "text": "ok"}], "stop_reason": "end_turn",
                                          "usage": {"input_tokens": 1, "output_tokens": 1}}, request=request)
    monkeypatch.setattr(httpx2.HTTPTransport, "handle_request", seen)
    ask = {"model": "claude", "max_tokens": 5, "messages": [{"role": "user", "content": "hello"}]}
    AnthropicVertex(region="us-east5", project_id="p", access_token="t").messages.create(**ask)
    plain = calls.pop()
    assert any(name.startswith("x-stainless-") for name in plain) and "python" in plain["user-agent"].lower()
    model = LLM("vertex-claude", "stand-in", region="us-east5", project="p")
    monkeypatch.setattr(type(model.chat), "account", lambda self: (SimpleNamespace(token="t", expired=False), "p"))
    model.chat.anthropic_vertex().messages.create(**ask)
    ours = calls.pop()
    assert [name for name in ours if name.startswith("x-stainless-")] == [], ours
    assert ours["user-agent"] == USER_AGENT and ours["authorization"] == "Bearer t"


class Wire:
    """The transports themselves: urllib's HTTP and HTTPS handlers and the Anthropic SDK's HTTP transport, each call's
    destination and headers kept, and answered 200 with an empty JSON object (a model's with a stand-in reply)."""

    def __init__(self, monkeypatch, redirect: dict | None = None):
        self.calls: list[tuple[str, dict]] = []
        self.redirect = redirect or {}
        monkeypatch.setattr(urllib.request.HTTPSHandler, "https_open", lambda handler, req: self.urllib(req))
        monkeypatch.setattr(urllib.request.HTTPHandler, "http_open", lambda handler, req: self.urllib(req))
        monkeypatch.setattr(httpx2.HTTPTransport, "handle_request", lambda transport, request: self.httpx(request))

    def urllib(self, req):
        self.calls.append((req.full_url, {k.lower(): v for k, v in req.header_items()}))
        headers = email.message.Message()
        headers["content-type"] = "application/json"
        if req.full_url in self.redirect:
            headers["location"] = self.redirect[req.full_url]
            reply = urllib.response.addinfourl(io.BytesIO(b"{}"), headers, req.full_url, code=302)
            reply.msg = "Found"
            return reply
        reply = urllib.response.addinfourl(io.BytesIO(b"{}"), headers, req.full_url, code=200)
        reply.msg = "OK"
        return reply

    def httpx(self, request):
        self.calls.append((str(request.url), {k.lower(): v for k, v in request.headers.items()}))
        return httpx2.Response(200, json={"id": "m", "type": "message", "role": "assistant", "model": "claude",
                                          "content": [{"type": "text", "text": "ok"}], "stop_reason": "end_turn",
                                          "usage": {"input_tokens": 1, "output_tokens": 1}}, request=request)

    def traced(self) -> list[tuple[str, set]]:
        return [(url, set(headers) & TRACE_HEADERS) for url, headers in self.calls if set(headers) & TRACE_HEADERS]


class Account:
    token, valid, expired = "ya29.stand-in", True, False

    def refresh(self, request) -> None:
        pass


def every_adapter(monkeypatch) -> list[str]:
    """One call from each adapter that leaves the process, each failure after the call left kept out of the way: the
    model providers (Vertex AI's Gemini and Claude, Anthropic's and OpenAI's APIs), Vertex AI's embeddings, the BigQuery
    loader, warehouses and graph store, Cloud Storage and Secret Manager."""
    from kge.adapters.bigquery import BigQueryLoader
    from kge.adapters.graphstore.bigquery import Client
    from kge.adapters.model import make_llm
    from kge.adapters.model.embed import VertexEmbedder
    from kge.adapters.objstore.gcs import GcsStore
    from kge.adapters.signin.manager import SecretManagerReader
    monkeypatch.setattr("google.auth.default", lambda scopes=None, **k: (Account(), "pilot-project"))
    monkeypatch.setattr("time.sleep", lambda seconds: None)
    monkeypatch.setenv("VERTEX_PROJECT", "pilot-project")
    reached = []

    def call(name, fn):
        reached.append(name)
        try:
            fn()
        except Exception:
            pass
    for provider, name in (("vertex-gemini", "gemini-x"), ("vertex-claude", ""), ("anthropic", "claude-x"),
                           ("openai", "gpt-x")):
        monkeypatch.setenv("MODEL_PROVIDER", provider)
        monkeypatch.setenv("MODEL_NAME", name)
        monkeypatch.setenv("MODEL_API_KEY", "stand-in-key")
        call(provider, lambda: make_llm().complete("hello", max_tokens=5))
    call("embeddings", lambda: VertexEmbedder(project="pilot-project", region="us-central1").embed("hello"))
    call("bigquery", lambda: BigQueryLoader(project="pilot-project").post(
        "https://bigquery.googleapis.com/bigquery/v2/projects/pilot-project/queries", {"authorization": "Bearer t"}, {}))
    call("graph store", lambda: Client("pilot-project", sleeper=lambda s: None).query("SELECT 1", [], read=True))
    call("object store", lambda: GcsStore("gs://bucket", "bucket").get("key"))
    call("secrets", lambda: SecretManagerReader(token=lambda: "t").latest("projects/p/secrets/s"))
    return reached


@pytest.mark.parametrize("traced", [True, False])
def test_no_outgoing_call_carries_trace_context_by_default(monkeypatch, traced):
    monkeypatch.delenv("TRACE_PROPAGATE_TO", raising=False)
    wire = Wire(monkeypatch)
    cleared()
    held = InMemorySpanExporter()
    with tracing_to(held) if traced else _off():
        span = recording.new_span("POST /ask")
        with recording.as_current(span):
            assert span.is_recording() is traced
            every_adapter(monkeypatch)
        span.end()
    hosts = {url.split("/")[2] for url, _ in wire.calls}
    assert {"aiplatform.googleapis.com", "us-central1-aiplatform.googleapis.com", "api.anthropic.com", "api.openai.com",
            "bigquery.googleapis.com", "storage.googleapis.com", "secretmanager.googleapis.com"} <= hosts, hosts
    assert wire.traced() == []


class _off:
    def __enter__(self):
        cleared()

    def __exit__(self, *exc):
        cleared()


def test_only_a_listed_destination_carries_the_traceparent(monkeypatch):
    """With TRACE_PROPAGATE_TO listing one origin and a span recorded, a call there carries its traceparent alone (no
    tracestate or baggage), a call anywhere else none, a redirect from there to elsewhere drops it, and with tracing off
    even the listed destination gets none."""
    from kge.adapters.http import open_http
    monkeypatch.setenv("TRACE_PROPAGATE_TO", f"{LISTED}/ignored/path, https://other.example.com:8443")
    wire = Wire(monkeypatch, redirect={f"{LISTED}/hop": "https://bigquery.googleapis.com/landed"})
    with tracing_to(InMemorySpanExporter()):
        span = recording.new_span("POST /ask")
        with recording.as_current(span):
            open_http(f"{LISTED}/context", timeout=5).read()
            open_http("https://KGE.example.com:443/again", timeout=5).read()
            open_http("https://bigquery.googleapis.com/x", timeout=5).read()
            open_http("https://kge.example.com:8443/x", timeout=5).read()
            open_http(f"{LISTED}/hop", timeout=5).read()
            every_adapter(monkeypatch)
        span.end()
    sent = {url: set(headers) & TRACE_HEADERS for url, headers in wire.calls}
    assert sent[f"{LISTED}/context"] == {"traceparent"} and sent["https://KGE.example.com:443/again"] == {"traceparent"}
    assert dict(wire.calls)[f"{LISTED}/context"]["traceparent"] == recording.traceparent(span)
    assert sent[f"{LISTED}/hop"] == {"traceparent"} and sent["https://bigquery.googleapis.com/landed"] == set()
    assert [url for url, found in sent.items() if found] == [f"{LISTED}/context", "https://KGE.example.com:443/again",
                                                              f"{LISTED}/hop"]
    wire.calls.clear()
    cleared()
    span = recording.new_span("POST /ask")
    with recording.as_current(span):
        open_http(f"{LISTED}/context", timeout=5).read()
    assert wire.traced() == []


@pytest.mark.parametrize("listed", [False, True])
def test_trace_headers_a_caller_planted_never_leave_but_the_ports_own(monkeypatch, listed):
    """Whatever trace or correlation headers a caller or a library put on a request, in either of urllib's header
    stores, none is sent, on the first request or a redirect; a listed destination gets the port's traceparent alone."""
    from kge.adapters.http import open_http
    if listed:
        monkeypatch.setenv("TRACE_PROPAGATE_TO", LISTED)
    else:
        monkeypatch.delenv("TRACE_PROPAGATE_TO", raising=False)
    wire = Wire(monkeypatch, redirect={f"{LISTED}/hop": "https://other.example.com/landed"})
    planted = {name: "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01" for name in TRACE_HEADERS}
    with tracing_to(InMemorySpanExporter()):
        span = recording.new_span("POST /ask")
        with recording.as_current(span):
            for url in (f"{LISTED}/x", "https://bigquery.googleapis.com/x", f"{LISTED}/hop"):
                request = urllib.request.Request(url, headers=planted)
                for name in ("Traceparent", "X-cloud-trace-context", "Baggage"):
                    request.add_unredirected_header(name, "planted")
                open_http(request, timeout=5).read()
        span.end()
    for url, headers in wire.calls:
        sent = set(headers) & TRACE_HEADERS
        if listed and url.startswith(LISTED):
            assert sent == {"traceparent"} and headers["traceparent"] == recording.traceparent(span), url
        else:
            assert sent == set(), url
    assert {url for url, _ in wire.calls} == {f"{LISTED}/x", "https://bigquery.googleapis.com/x", f"{LISTED}/hop",
                                              "https://other.example.com/landed"}


@pytest.mark.parametrize("listed", [False, True])
def test_the_httpx_clients_strip_and_add_as_open_http_does(monkeypatch, listed):
    """http_client and async_http_client (the reference agent's calls to the context service and its MCP): a caller's
    trace or correlation headers never leave, and a listed destination gets the port's traceparent alone."""
    import anyio
    import httpx

    from kge.adapters.http import async_http_client, http_client
    if listed:
        monkeypatch.setenv("TRACE_PROPAGATE_TO", LISTED)
    else:
        monkeypatch.delenv("TRACE_PROPAGATE_TO", raising=False)
    calls: list[tuple[str, dict]] = []

    def seen(request):
        calls.append((str(request.url), {k.lower(): v for k, v in request.headers.items()}))
        return httpx.Response(200, json={}, request=request)

    async def seen_async(transport, request):
        return seen(request)
    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", lambda transport, request: seen(request))
    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", seen_async)
    planted = {name: "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01" for name in TRACE_HEADERS}

    async def both(url):
        async with async_http_client(headers=planted) as client:
            await client.get(url)
    with tracing_to(InMemorySpanExporter()):
        span = recording.new_span("POST /ask")
        with recording.as_current(span):
            for url in (f"{LISTED}/context", "https://other.example.com/context"):
                with http_client(headers=planted) as client:
                    client.post(url, json={})
                anyio.run(both, url)
        span.end()
    assert len(calls) == 4
    for url, headers in calls:
        sent = set(headers) & TRACE_HEADERS
        if listed and url.startswith(LISTED):
            assert sent == {"traceparent"} and headers["traceparent"] == recording.traceparent(span), url
        else:
            assert sent == set(), url


def test_a_call_with_no_recorded_span_never_reads_the_propagation_list(monkeypatch):
    import kge.adapters.tracing as tracing
    monkeypatch.setattr(tracing, "destinations", lambda: (_ for _ in ()).throw(AssertionError("read")))
    cleared()
    assert recording.trace_headers(f"{LISTED}/x") == {}
    with tracing_to(InMemorySpanExporter()):
        assert recording.trace_headers(f"{LISTED}/x") == {}


@pytest.mark.parametrize("value", ["kge.example.com", "ftp://kge.example.com", "https://", "https://kge.example.com?x=1",
                                   "https://kge example.com"])
def test_a_mistyped_propagation_list_stops_the_service_at_start(monkeypatch, value):
    import kge.adapters.tracing as tracing
    monkeypatch.setenv("TRACING", "off")
    monkeypatch.setenv("TRACE_PROPAGATE_TO", value)
    with pytest.raises(tracing.TracingSettingError, match="^TRACE_PROPAGATE_TO must be a comma-separated list"):
        tracing.check_settings()
