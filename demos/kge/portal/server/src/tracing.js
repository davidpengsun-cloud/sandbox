import { context, createContextKey, defaultTextMapGetter, defaultTextMapSetter, isSpanContextValid, ROOT_CONTEXT, SpanKind, SpanStatusCode, trace } from "@opentelemetry/api";
import { ExportResultCode, W3CTraceContextPropagator } from "@opentelemetry/core";
import { OTLPTraceExporter } from "@opentelemetry/exporter-trace-otlp-http";
import { resourceFromAttributes } from "@opentelemetry/resources";
import { AlwaysOnSampler, BatchSpanProcessor, ConsoleSpanExporter, TracerProvider } from "@opentelemetry/sdk-trace";
import { checkedAddress } from "./brand.js";
import { googleAuth } from "./outbound.js";

// The portal's half of a question's trace: one SERVER span per Ask request.
// The OpenTelemetry API is the port; the exporters are the adapters, chosen
// once by setupTracing(). Trace context crosses the portal's edge here alone:
// traceRequest() starts a fresh question trace, and traceHeaders() gives one
// to a call bound for an origin TRACE_PROPAGATE_TO lists, both through W3C
// below. The API's global propagator stays its no-op, so no library the
// portal loads (BigQuery, google-auth, the exporters) can add a trace header.

const SERVICE = "kge-portal";
export const SCOPE = "kge-portal";
const DEFAULT_ENDPOINT = "http://localhost:4318/v1/traces";
const CLOUD_TRACE = "https://telemetry.googleapis.com/v1/traces";
const SCOPES = ["https://www.googleapis.com/auth/cloud-platform"];
const FLUSH_MS = 2000;
const EXPORT_MS = 10000;
const QUEUE = 2048;
const MODES = ["off", "console", "otlp", "cloudtrace", "store"];
const W3C = new W3CTraceContextPropagator();
const TRUSTED_QUESTION = createContextKey("kge.saved.question");
const PROPAGATE_TO = "TRACE_PROPAGATE_TO must be a comma-separated list of http or https addresses, such as https://kge.example.com";

// Spans carry names, ids, timings, counts and outcomes only. Any other key,
// name, event, link or status message is dropped before an exporter sees it,
// so no caller can export a question, a product or a person.
const KIND = /^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$/;
const METHOD = /^[A-Z]{3,7}$/;
const ROUTE = /^\/[A-Za-z0-9/_:.-]{0,200}$/;
const CONVERSATION = /^(?:[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}|conv-[0-9]{1,20})$/i;
const AGENTS = new Set(["cerebro", "discover_ask", "context_ask", "context_playground"]);
const ALLOWED = {
  "http.request.method": (value) => typeof value === "string" && METHOD.test(value),
  "http.route": (value) => typeof value === "string" && ROUTE.test(value),
  "http.response.status_code": (value) => Number.isInteger(value),
  "error.type": (value) => typeof value === "string" && KIND.test(value),
  "kge.agent": (value) => AGENTS.has(value),
  "gen_ai.conversation.id": (value) => typeof value === "string" && CONVERSATION.test(value),
};

const bare = (spanContext) => spanContext && { traceId: spanContext.traceId, spanId: spanContext.spanId, traceFlags: spanContext.traceFlags, isRemote: spanContext.isRemote };

function safeSpan(span, client) {
  const attributes = { ...Object.fromEntries(Object.entries(span.attributes).filter(([key, value]) => ALLOWED[key]?.(value))),
    "kge.telemetry.origin": "portal", ...(client ? { "kge.client": client } : {}) };
  const method = attributes["http.request.method"];
  const route = attributes["http.route"];
  return {
    name: method && route && span.name === `${method} ${route}` ? span.name : "span",
    kind: span.kind,
    spanContext: () => bare(span.spanContext()),
    parentSpanContext: bare(span.parentSpanContext),
    startTime: span.startTime,
    endTime: span.endTime,
    duration: span.duration,
    ended: span.ended,
    status: { code: span.status.code },
    attributes,
    links: [],
    events: [],
    resource: span.resource,
    instrumentationScope: { name: SCOPE },
    droppedAttributesCount: 0,
    droppedEventsCount: 0,
    droppedLinksCount: 0,
  };
}

// The spans handed to the batch processor and not yet exported, each with a
// promise settled once its own export has finished, passed or failed. The
// batch processor's forceFlush does not wait for a batch it started on its
// own (nor for an asynchronous resource it waits on before exporting), and
// batches may finish out of order, so a flush waits on its own spans instead.
class Unexported {
  constructor() {
    this.spans = new Map();
  }
  get size() {
    return this.spans.size;
  }
  add(span) {
    let settle;
    const exported = new Promise((resolve) => { settle = resolve; });
    this.spans.set(span, { exported, settle });
  }
  settle(spans) {
    for (const span of spans) {
      this.spans.get(span)?.settle();
      this.spans.delete(span);
    }
  }
  exported() {
    return Promise.all([...this.spans.values()].map(({ exported }) => exported));
  }
}

// A failure's kind: an HTTP status, or an error's type name, never its
// message, which may echo what was sent.
function errorKind(error) {
  for (const status of [error?.code, error?.status]) if (Number.isInteger(status)) return String(status);
  const name = error?.constructor?.name;
  return typeof name === "string" && /^[A-Za-z][A-Za-z0-9_]{0,63}$/.test(name) ? name : "Error";
}

// The adapter's exporter, settling each span once its export has finished.
// The first failed export is logged by its kind and size alone; later ones
// are not, so a collector that is down does not flood the log.
export class TrackedExporter {
  constructor(exporter, unexported, log) {
    this.exporter = exporter;
    this.unexported = unexported;
    this.log = log;
    this.warned = false;
  }
  export(spans, done) {
    this.exporter.export(spans, (result) => {
      this.unexported.settle(spans);
      if (result?.code !== ExportResultCode.SUCCESS && !this.warned) {
        this.warned = true;
        this.log(`Trace export failed (${errorKind(result?.error)}, ${spans.length} spans); further failures are not logged.`);
      }
      done(result);
    });
  }
  shutdown() {
    return this.exporter.shutdown();
  }
}

// A span processor placed in front of the exporting one: only the portal's
// own spans pass, and only as safeSpan() rebuilds them. A flush ends once
// every span handed on before it has been exported. A span that would
// overflow the queue is dropped here, as the batch processor would drop it.
export class SafeSpanProcessor {
  constructor(next, unexported, limit = QUEUE, client = undefined) {
    this.client = client;
    this.next = next;
    this.unexported = unexported;
    this.limit = limit;
  }
  onStart() {}
  onEnd(span) {
    if (span.instrumentationScope?.name !== SCOPE || span.attributes["kge.internal.suppress"] === true || this.unexported.size >= this.limit) return;
    const safe = safeSpan(span, this.client);
    this.unexported.add(safe);
    this.next.onEnd(safe);
  }
  async forceFlush() {
    const exported = this.unexported.exported();
    try {
      await this.next.forceFlush();
    } finally {
      await exported;
    }
  }
  shutdown() {
    return this.next.shutdown();
  }
}

// Cloud Trace: the runtime's service account through google-auth-library's
// application default credentials, which cache and refresh the access token
// and send no client-info (outbound.js), and the project the spans belong
// to: BIGQUERY_PROJECT, else the credentials'.
// With no project to name, the portal does not start (ready rejects, and
// index.js stops on it), rather than export spans Cloud Trace cannot place.
function cloudTrace(env, options) {
  const auth = options.auth || googleAuth({ scopes: SCOPES });
  const named = env.BIGQUERY_PROJECT
    ? Promise.resolve(env.BIGQUERY_PROJECT)
    : Promise.resolve().then(() => auth.getProjectId()).then((id) => id || undefined, () => undefined);
  const ready = named.then((id) => {
    if (!id) throw new Error("TRACING=cloudtrace needs a project: set BIGQUERY_PROJECT");
  });
  ready.catch(() => {});
  const project = named;
  const headers = async () => {
    const token = await auth.getAccessToken();
    if (!token) throw new Error("no access token");
    return { authorization: `Bearer ${token}` };
  };
  return {
    exporter: new OTLPTraceExporter({ url: options.cloudTraceUrl || CLOUD_TRACE, headers, timeoutMillis: EXPORT_MS }),
    attributes: { "gcp.project_id": project },
    ready,
  };
}

function adapter(mode, env, options) {
  if (mode === "store") {
    if (typeof options.storeExport !== "function") throw new Error("TRACING=store requires a configured telemetry store");
    const iso = (time) => new Date(time[0] * 1000 + Math.floor(time[1] / 1e6)).toISOString();
    const exporter = {
      export(spans, done) {
        const batch = spans.map((span) => ({ id: span.spanContext().spanId, trace_id: span.spanContext().traceId,
          parent_id: span.parentSpanContext?.spanId ?? null, name: span.name, started_at: iso(span.startTime),
          ended_at: iso(span.endTime), attributes: span.attributes }));
        Promise.resolve().then(() => options.storeExport(batch)).then(() => done({ code: ExportResultCode.SUCCESS }),
          (error) => done({ code: ExportResultCode.FAILED, error }));
      },
      shutdown: async () => { await options.storeShutdown?.(); },
    };
    return { exporter, attributes: {}, ready: options.storeReady ?? Promise.resolve() };
  }
  if (mode === "console") return { exporter: new ConsoleSpanExporter(), attributes: {}, ready: Promise.resolve() };
  if (mode === "otlp") {
    // Read in otlp mode alone; blank is unset, as KGE reads it.
    const address = (env.TRACING_ENDPOINT ?? "").trim();
    const url = address ? checkedEndpoint(address) : DEFAULT_ENDPOINT;
    return { exporter: new OTLPTraceExporter({ url, timeoutMillis: EXPORT_MS }), attributes: {}, ready: Promise.resolve() };
  }
  return cloudTrace(env, options);
}

// The one place the backend is chosen. Off configures nothing and returns
// null, so the API stays a no-op; otherwise it registers an always-on
// provider (never a global propagator), and returns the provider for flushing,
// its ready settling once the adapter can export (Cloud Trace: its project).
// options.exporter stands in for the adapter's exporter in tests.
export function setupTracing(env = process.env, options = {}) {
  traceDestinations(env);
  const mode = (env.TRACING ?? "").trim() || "store";
  if (!MODES.includes(mode)) throw new Error("TRACING must be off, store, console, otlp or cloudtrace");
  if (mode === "off") return null;
  const chosen = adapter(mode, env, options);
  const unexported = new Unexported();
  const log = options.log || ((line) => console.error(line));
  const exporter = new TrackedExporter(options.exporter || chosen.exporter, unexported, log);
  const client = (env.TRACE_CLIENT_ID ?? "").trim();
  if (client && !/^[a-z][a-z0-9-]{0,62}$/.test(client)) throw new Error("TRACE_CLIENT_ID must be a client identifier");
  const provider = new TracerProvider({
    resource: resourceFromAttributes({ "service.name": SERVICE, ...chosen.attributes }),
    sampler: new AlwaysOnSampler(),
    forceFlushTimeoutMillis: FLUSH_MS,
    spanProcessors: [new SafeSpanProcessor(new BatchSpanProcessor({ exporter, maxQueueSize: QUEUE, maxExportBatchSize: 512, exportTimeoutMillis: options.exportTimeoutMillis || EXPORT_MS }), unexported, QUEUE, client || undefined)],
  });
  if (!options.isLocal && !trace.setGlobalTracerProvider(provider)) throw new Error("Tracing is already set up");
  provider.ready = chosen.ready;
  return provider;
}

function checkedEndpoint(value) {
  try {
    // The URL parser drops a tab or newline inside an address; KGE refuses any whitespace or control character.
    if (/[\s\x00-\x1f\x7f]/.test(value)) throw new Error("spaced");
    return checkedAddress("TRACING_ENDPOINT", value);
  } catch {
    throw new Error("TRACING_ENDPOINT must be an http or https OTLP traces address, such as http://localhost:4318/v1/traces");
  }
}

// url's origin, scheme://host[:port] in lower case with the scheme's default
// port left out; null for anything but an http or https address with a host.
export function originOf(url) {
  try {
    const parsed = url instanceof URL ? url : new URL(String(url).trim());
    return ["http:", "https:"].includes(parsed.protocol) && parsed.hostname ? parsed.origin : null;
  } catch {
    return null;
  }
}

// TRACE_PROPAGATE_TO: the origins (comma-separated http or https addresses; a
// path, if any, is ignored) an outgoing call carries the trace context to.
// Unset or empty, the default: none. Anything else is refused at start-up,
// in KGE's words.
export function traceDestinations(env = process.env) {
  const found = new Set();
  for (const item of String(env.TRACE_PROPAGATE_TO ?? "").split(",").map((part) => part.trim()).filter(Boolean)) {
    // The URL parser drops a tab or newline and takes "http:host"; KGE refuses both.
    const origin = /[\s\x00-\x1f\x7f?#]/.test(item) || !/^https?:\/\//i.test(item) ? null : originOf(item);
    if (!origin) throw new Error(PROPAGATE_TO);
    found.add(origin);
  }
  return found;
}

// The trace context a call to url carries: the span in ctx's traceparent
// alone, where url's origin is one of destinations and the span is recorded;
// else nothing. Never a tracestate or baggage. The one place the portal adds
// trace context to a call (kge.js request()); every other trace header is
// removed (outbound.js).

export function traceHeaders(url, ctx = context.active(), destinations = new Set()) {
  if (!destinations.size || !destinations.has(originOf(url))) return {};
  const span = trace.getSpan(ctx);
  if (!span?.isRecording() && !ctx?.getValue(TRUSTED_QUESTION)) return {};
  const carrier = {};
  W3C.inject(trace.setSpan(ROOT_CONTEXT, span), carrier, defaultTextMapSetter);
  return { traceparent: carrier.traceparent };
}

// Only server-created, allowlisted identifiers may accompany an internal KGE traceparent.
export function traceMetadata(ctx) {
  const span = trace.getSpan(ctx);
  const saved = ctx?.getValue(TRUSTED_QUESTION);
  if (!span?.isRecording() && !saved) return {};
  const agent = saved?.agent ?? span.attributes?.["kge.agent"];
  const session = saved?.session ?? span.attributes?.["gen_ai.conversation.id"];
  return AGENTS.has(agent) && typeof session === "string" && CONVERSATION.test(session)
    ? { "x-kge-trace-agent": agent, "x-kge-trace-session": session } : {};
}

function fail(span, kind) {
  span.setAttribute("error.type", kind);
  span.setStatus({ code: SpanStatusCode.ERROR });
}

// One SERVER span per question request, named by method and route template,
// independent of any browser-supplied traceparent; req.traceContext carries it to
// kge.request(). The span ends, with the status, and the spans ended so far
// are flushed (at most FLUSH_MS) before the response completes: Cloud Run
// gives an instance no CPU once its response is sent. A client that goes
// first ends it as aborted. With tracing off this does nothing.
export function traceRequest(tracing, req, res, method, route) {
  if (!tracing || req.traceContext) return;
  // A browser may choose traceparent: never let it join a different question's trace.
  const parent = ROOT_CONTEXT;
  const agent = route === "/api/cerebro/stream" ? "cerebro" : route.startsWith("/api/context/playground") ? "context_playground" : route.startsWith("/api/context/") ? "context_ask" : "discover_ask";
  const supplied = req.headers?.["x-kge-conversation-id"];
  const session = agent === "context_ask" && typeof supplied === "string" && CONVERSATION.test(supplied) ? supplied : globalThis.crypto.randomUUID();
  const attributes = { "http.request.method": method, "http.route": route, "kge.agent": agent, "gen_ai.conversation.id": session };
  const span = tracing.getTracer(SCOPE).startSpan(`${method} ${route}`, { kind: SpanKind.SERVER, attributes }, parent);
  if (!span.isRecording()) return;
  req.traceContext = trace.setSpan(ROOT_CONTEXT, span);
  const end = res.end;
  res.end = function ending(...args) {
    res.end = end;
    span.setAttribute("http.response.status_code", res.statusCode);
    if (res.statusCode >= 500) fail(span, String(res.statusCode));
    else span.setStatus({ code: SpanStatusCode.OK });
    span.end();
    tracing.forceFlush({ timeoutMillis: FLUSH_MS }).catch(() => {}).finally(() => end.apply(res, args));
    return res;
  };
  res.once?.("close", () => {
    if (res.end === end) return;
    res.end = end;
    fail(span, "aborted");
    // A client that leaves before the headers were sent has no status to record.
    if (res.headersSent) span.setAttribute("http.response.status_code", res.statusCode);
    span.end();
  });
}

// Only a server-created question trace is saved in the private conversation aside, never browser input.
export function questionTrace(req) {
  const ctx = req?.traceContext;
  if (!ctx) return undefined;
  const span = trace.getSpan(ctx);
  if (!span?.isRecording() && !ctx.getValue(TRUSTED_QUESTION)) return undefined;
  const carrier = {};
  W3C.inject(ctx, carrier, defaultTextMapSetter);
  return carrier.traceparent;
}

// Called only after the conversation store checks its owner. A resumed logical question is not a new trace.
export function resumeQuestionTrace(req, parent, session) {
  if (!req || (!req.traceContext && !req.traceResumeAllowed) || typeof parent !== "string" ||
      typeof session !== "string" || !CONVERSATION.test(session)) return false;
  const restored = W3C.extract(ROOT_CONTEXT, { traceparent: parent }, defaultTextMapGetter);
  const info = trace.getSpanContext(restored);
  if (!info || !isSpanContextValid(info) || !(info.traceFlags & 1)) return false;
  const temporary = req.traceContext && trace.getSpan(req.traceContext);
  if (temporary?.isRecording()) temporary.setAttribute("kge.internal.suppress", true);
  req.traceContext = restored.setValue(TRUSTED_QUESTION, { agent: "cerebro", session });
  return true;
}

// The agent assigns its actual conversation only after validating it. Untrusted text never reaches spans.
export function traceConversation(req, id) {
  if (req?.traceContext && typeof id === "string" && CONVERSATION.test(id)) trace.getSpan(req.traceContext)?.setAttribute("gen_ai.conversation.id", id);
}

export function traced(tracing, method, route) {
  return (req, res, next) => {
    traceRequest(tracing, req, res, method, route);
    next();
  };
}
