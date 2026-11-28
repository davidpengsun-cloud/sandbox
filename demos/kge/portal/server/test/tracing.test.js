import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createServer } from "node:http";
import test from "node:test";
import { propagation, ROOT_CONTEXT, SpanKind, SpanStatusCode, trace } from "@opentelemetry/api";
import { InMemorySpanExporter } from "@opentelemetry/sdk-trace";
import { createApp } from "../src/app.js";
import { kgeClient } from "../src/kge.js";
import { TRACE_HEADERS } from "../src/outbound.js";
import { originOf, SCOPE, setupTracing, traceDestinations, traceHeaders, traceRequest, traceConversation } from "../src/tracing.js";
import { openSqliteRepository } from "../src/repository/sqlite.js";
import { catalogue } from "./fixtures/catalogue.js";

const PLANTED = {
  question: "plantedquestionQ7731",
  product: "planted-product-P4410",
  person: "planted.person.E9902@example.com",
  sql: "SELECT plantedsqlS5521",
  reply: "plantedreplyR3307",
};
const PARENT = "00-0af7651916cd43dd8448eb211c80319c-b7ad6b71a9203331-01";
// What a caller may send besides a traceparent; none of it is passed on.
const INCOMING = { traceparent: PARENT, tracestate: "vendor=planted", baggage: "user=planted" };

function parseTraceparent(value) {
  const match = /^00-([0-9a-f]{32})-([0-9a-f]{16})-([0-9a-f]{2})$/.exec(value || "");
  return match ? { traceId: match[1], spanId: match[2], flags: match[3] } : null;
}

function reset() {
  trace.disable();
  propagation.disable();
}

// Each test registers its own provider and clears the global one after.
function tracingFor(context, env, options = {}) {
  reset();
  const provider = setupTracing(env, options);
  context.after(async () => {
    await provider?.shutdown().catch(() => {});
    reset();
  });
  return provider;
}

function memoryTracing(context, options = {}) {
  const memory = new InMemorySpanExporter();
  const provider = tracingFor(context, { TRACING: "console" }, { exporter: memory, ...options });
  return { provider, spans: () => memory.getFinishedSpans() };
}

// What an exporter could serialise from a span: everything it can reach.
function everything(span) {
  return JSON.stringify({
    name: span.name, kind: span.kind, context: span.spanContext(), parent: span.parentSpanContext, status: span.status,
    attributes: span.attributes, events: span.events, links: span.links, resource: span.resource.attributes, scope: span.instrumentationScope,
  });
}

// A stand-in KGE that records each request's traceparent and answers like KGE.
async function standInKge(context) {
  const seen = [];
  const server = createServer(async (req, res) => {
    let body = "";
    for await (const chunk of req) body += chunk;
    seen.push({ method: req.method, url: req.url, traceparent: req.headers.traceparent, tracestate: req.headers.tracestate, baggage: req.headers.baggage, body });
    res.writeHead(200, { "content-type": "application/json" });
    const path = req.url.split("?")[0];
    if (path === "/whoami") return res.end(JSON.stringify({ id: PLANTED.person }));
    if (path === "/readable-units") return res.end(JSON.stringify({ units: ["retail", "sme"] }));
    if (path === "/assist") return res.end(JSON.stringify({ answer_text: PLANTED.reply, figures: [], citations: [], sql: PLANTED.sql }));
    return res.end(JSON.stringify({ answer: PLANTED.reply, sql: PLANTED.sql, run_id: "run-1" }));
  });
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  context.after(() => server.close());
  return { seen, url: `http://127.0.0.1:${server.address().port}` };
}

// A stand-in OTLP collector that records each export.
async function collector(context) {
  const posts = [];
  const server = createServer(async (req, res) => {
    let body = "";
    for await (const chunk of req) body += chunk;
    posts.push({ url: req.url, headers: req.headers, body: JSON.parse(body) });
    res.writeHead(200, { "content-type": "application/json" });
    res.end("{}");
  });
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  context.after(() => server.close());
  return { posts, url: `http://127.0.0.1:${server.address().port}/v1/traces` };
}

async function portal(context, tracing, kgeUrl, env = {}) {
  const repository = openSqliteRepository({ PORTAL_SQLITE_PATH: ":memory:" });
  repository.syncCatalogue(catalogue.release, catalogue.products, true);
  repository.setRole(PLANTED.person, "admin");
  const { app } = createApp({ repository, tracing, catalogueSync: false, env: { KGE_URL: kgeUrl, PORTAL_APP_CONTEXT: "on", TRACING: tracing ? "console" : "off", ...env }, uiPath: "not-present" });
  const server = app.listen(0, "127.0.0.1");
  await new Promise((resolve) => server.once("listening", resolve));
  context.after(() => { server.close(); repository.close(); });
  const base = `http://127.0.0.1:${server.address().port}`;
  return (path, body, headers = {}) => fetch(`${base}${path}`, body === undefined ? { headers } : { method: "POST", headers: { "content-type": "application/json", authorization: "Bearer reader", ...headers }, body: JSON.stringify(body) });
}

test("tracing off configures nothing: no provider, no propagator, no traceparent, no wrapped response", async (context) => {
  reset();
  for (const env of [{ TRACING: "off" }, { TRACING: " off " }]) assert.equal(setupTracing(env), null);
  assert.throws(() => setupTracing({}), /requires a configured telemetry store/);
  assert.equal(trace.getTracer(SCOPE).startSpan("x").isRecording(), false);
  assert.deepEqual(propagation.fields(), []);
  const kge = await standInKge(context);
  // Even with KGE listed in TRACE_PROPAGATE_TO: tracing off sends no trace context.
  const call = await portal(context, undefined, kge.url, { TRACE_PROPAGATE_TO: kge.url });
  assert.equal((await call("/api/ask", { question: "How many accounts?" }, INCOMING)).status, 200);
  assert.equal((await call("/api/context/ask", { question: "How many accounts?" }, INCOMING)).status, 200);
  assert.ok(kge.seen.length >= 2);
  assert.ok(kge.seen.every((request) => request.traceparent === undefined && request.tracestate === undefined && request.baggage === undefined));
  const res = { end() {}, once() { throw new Error("no listener when off"); } };
  const { end } = res;
  const req = { headers: { traceparent: PARENT } };
  traceRequest(null, req, res, "POST", "/ask");
  assert.equal(res.end, end);
  assert.equal(req.traceContext, undefined);
});

test("a mistyped TRACING or TRACING_ENDPOINT is refused at startup", async (context) => {
  reset();
  context.after(reset);
  for (const mode of ["on", "OFF", "Console", "OTLP", "jaeger"]) {
    assert.throws(() => setupTracing({ TRACING: mode }), /^Error: TRACING must be off, store, console, otlp or cloudtrace$/, mode);
  }
  assert.throws(() => createApp({ env: { TRACING: "OTLP" }, catalogueSync: false, uiPath: "not-present" }), /TRACING must be/);
  // The same refused and taken addresses as KGE's tests/unit/test_tracing.py.
  for (const endpoint of ["ftp://collector:4318/v1/traces", "collector:4318", "http://", "http://:4318/v1/traces", "http://c:abc/v1", "http://c:99999/v1", "http://c/v1?x=1", "http://c/v1#top", "http://collector invalid:4318/v1/traces", "http://coll\tector:4318/v1/traces", "http://collector:4318/v1/\ntraces"]) {
    assert.throws(() => setupTracing({ TRACING: "otlp", TRACING_ENDPOINT: endpoint }), /^Error: TRACING_ENDPOINT must be an http or https OTLP traces address/, endpoint);
  }
  assert.equal(trace.getTracer(SCOPE).startSpan("x").isRecording(), false, "a refused setting registers nothing");
  const taken = ["", "   ", "https://collector.example.com/v1/traces", " http://[::1]:4318/v1/traces ", "http://c:/v1"].map((endpoint) => ({ TRACING: "otlp", TRACING_ENDPOINT: endpoint }));
  // Read in otlp mode alone.
  taken.push({ TRACING: "console", TRACING_ENDPOINT: "not an address" }, { TRACING: "cloudtrace", TRACING_ENDPOINT: "not an address", BIGQUERY_PROJECT: "p" });
  for (const env of taken) {
    reset();
    const provider = setupTracing(env);
    assert.ok(provider, JSON.stringify(env));
    await provider.shutdown();
  }
  reset();
  const provider = setupTracing({ TRACING: "console" });
  assert.throws(() => setupTracing({ TRACING: "console" }), /Tracing is already set up/);
  await provider.shutdown();
});

test("a question's span is exported before its response completes, and KGE continues its trace", async (context) => {
  const kge = await standInKge(context);
  const exported = [];
  const slow = {
    export(spans, done) { setTimeout(() => { exported.push(...spans); done({ code: 0 }); }, 150); },
    shutdown: async () => {},
  };
  const provider = tracingFor(context, { TRACING: "console" }, { exporter: slow });
  const call = await portal(context, provider, kge.url, { TRACE_PROPAGATE_TO: kge.url });
  const response = await call("/api/ask", { question: "How many accounts?" });
  assert.equal(response.status, 200);
  assert.equal(exported.length, 1);
  const [span] = exported;
  assert.equal(span.name, "POST /api/ask");
  assert.equal(span.kind, SpanKind.SERVER);
  assert.deepEqual(span.status, { code: SpanStatusCode.OK });
  assert.match(span.attributes["gen_ai.conversation.id"], /^[0-9a-f-]{36}$/);
  assert.deepEqual(span.attributes, { "http.request.method": "POST", "http.route": "/api/ask", "http.response.status_code": 200, "kge.telemetry.origin": "portal", "kge.agent": "discover_ask", "gen_ai.conversation.id": span.attributes["gen_ai.conversation.id"] });
  assert.equal(span.resource.attributes["service.name"], "kge-portal");
  assert.equal(span.instrumentationScope.name, "kge-portal");
  assert.equal(span.parentSpanContext, undefined);
  const assist = kge.seen.find((request) => request.url === "/assist");
  const sent = parseTraceparent(assist.traceparent);
  assert.deepEqual([sent.traceId, sent.spanId, sent.flags], [span.spanContext().traceId, span.spanContext().spanId, "01"]);
});

test("every question route ignores a forged incoming trace and forwards its fresh root to KGE", async (context) => {
  const kge = await standInKge(context);
  const { provider, spans } = memoryTracing(context);
  const call = await portal(context, provider, kge.url, { TRACE_PROPAGATE_TO: kge.url });
  const routes = [
    ["/ask", { question: "q", products: ["retail-accounts"] }, "POST /ask"],
    ["/api/ask", { question: "q" }, "POST /api/ask"],
    ["/ask-data-product", { question: "q", productId: "retail-accounts" }, "POST /ask-data-product"],
    ["/ask-domain", { question: "q", domain: "Retail" }, "POST /ask-domain"],
    ["/api/context/ask?background=true", { question: "q" }, "POST /api/context/ask"],
    ["/api/context/ask/feedback", { receipt: "r-1", verdict: "confirmed" }, "POST /api/context/ask/feedback"],
    ["/api/context/answer", { question: "q" }, "POST /api/context/answer"],
    ["/api/context/agent", { question: "q" }, "POST /api/context/agent"],
  ];
  for (const [path, body] of routes) assert.equal((await call(path, body, { traceparent: PARENT })).status, 200, path);
  assert.equal((await call("/api/ask", { question: "q" }, { traceparent: PARENT.replace(/-01$/, "-00") })).status, 200);
  assert.equal((await call("/api/context/ask/runs/Run-1?after=3", undefined, { traceparent: "garbage" })).status, 200);
  // Not a question: no span.
  assert.equal((await call("/api/context/whoami", undefined)).status, 200);
  const names = spans().map((span) => span.name);
  assert.deepEqual(names, [...routes.map(([, , name]) => name), "POST /api/ask"]);
  for (const span of spans()) {
    assert.notEqual(span.spanContext().traceId, "0af7651916cd43dd8448eb211c80319c", span.name);
    assert.equal(span.parentSpanContext, undefined, span.name);
  }
  assert.equal(new Set(spans().map((span) => span.spanContext().traceId)).size, spans().length, "each request gets a distinct root");
  // Every question KGE saw carries its span's trace.
  const sent = kge.seen.filter((seen) => seen.traceparent).map((seen) => parseTraceparent(seen.traceparent));
  for (const span of spans()) assert.ok(sent.some((s) => s.spanId === span.spanContext().spanId && s.traceId === span.spanContext().traceId && s.flags === "01"), span.name);
});

test("planted question text, product ids, SQL, replies and people never reach the exporter", async (context) => {
  const kge = await standInKge(context);
  const { provider, spans } = memoryTracing(context);
  const call = await portal(context, provider, kge.url, { TRACE_PROPAGATE_TO: kge.url });
  const question = `What is ${PLANTED.question} for ${PLANTED.person}?`;
  const state = { tracestate: `planted=${PLANTED.question}`, baggage: `planted=${PLANTED.question}` };
  await call("/api/ask", { question, productIds: [PLANTED.product] }, { traceparent: PARENT, ...state });
  await call("/ask", { question, products: [PLANTED.product] });
  await call("/ask", { question, products: ["retail-accounts"] });
  await call("/ask-domain", { userPrompt: question, domain: PLANTED.product });
  await call(`/api/context/ask?q=${PLANTED.question}`, { question, sql: PLANTED.sql, principal: PLANTED.person }, state);
  await call(`/api/context/ask/runs/${PLANTED.product}?after=${PLANTED.question}`);
  // A caller that sets content as a name, attribute, event, link or status message is still kept out.
  const tracer = trace.getTracer(SCOPE, PLANTED.reply);
  const linked = tracer.startSpan("POST /ask", { attributes: { "http.request.method": "POST", "http.route": "/ask" } });
  linked.end();
  const span = tracer.startSpan(`POST /ask ${PLANTED.question}`, {
    attributes: { question: PLANTED.question, "http.request.method": "POST", "http.route": "/ask", "enduser.id": PLANTED.person },
    links: [{ context: linked.spanContext(), attributes: { product: PLANTED.product } }],
  });
  span.setAttribute("db.statement", PLANTED.sql);
  span.setAttribute("http.response.status_code", PLANTED.reply);
  span.setAttribute("error.type", `${PLANTED.reply}: ${PLANTED.question}`);
  span.addEvent(PLANTED.question, { sql: PLANTED.sql });
  span.recordException(new Error(PLANTED.reply));
  span.setStatus({ code: SpanStatusCode.ERROR, message: PLANTED.reply });
  span.end();
  const named = tracer.startSpan(`POST /${PLANTED.question}`, { attributes: { "http.request.method": "POST", "http.route": "/ask" } });
  named.end();
  // Another library's spans are not the portal's to export.
  trace.getTracer("other-library").startSpan(PLANTED.question, { attributes: { "http.route": "/ask" } }).end();
  await provider.forceFlush();
  assert.ok(kge.seen.some((seen) => seen.body.includes(PLANTED.question)), "the question reached KGE");
  assert.ok(kge.seen.some((seen) => seen.traceparent), "the trace reached KGE");
  assert.ok(kge.seen.every((seen) => seen.tracestate === undefined), "a caller's tracestate reached KGE");
  assert.ok(kge.seen.every((seen) => seen.baggage === undefined), "baggage reached KGE");
  assert.equal(spans().length, 8);
  const output = spans().map(everything).join("");
  for (const [kind, value] of Object.entries(PLANTED)) assert.equal(output.includes(value), false, `${kind} reached a span`);
  for (const value of ["P4410", "Q7731", "E9902", "S5521", "R3307", "planted"]) assert.equal(output.includes(value), false, value);
  const keys = new Set(spans().flatMap((s) => Object.keys(s.attributes)));
  assert.deepEqual([...keys].sort(), ["gen_ai.conversation.id", "http.request.method", "http.response.status_code", "http.route", "kge.agent", "kge.telemetry.origin"]);
  assert.ok(spans().every((s) => s.events.length === 0 && s.links.length === 0 && s.status.message === undefined));
  assert.ok(spans().every((s) => s.instrumentationScope.name === SCOPE && s.instrumentationScope.version === undefined));
  assert.deepEqual(spans().slice(-3).map((s) => s.name), ["POST /ask", "span", "span"]);
  assert.deepEqual(spans().at(-2).status, { code: SpanStatusCode.ERROR });
});

test("allowlisted keys pass; a server error records only its kind; an abandoned request ends as aborted", async (context) => {
  const { provider, spans } = memoryTracing(context);
  const response = (statusCode) => {
    const listeners = {};
    const res = { statusCode, headersSent: false, ended: 0, end() { res.ended += 1; return res; }, once: (name, fn) => { listeners[name] = fn; } };
    return { res, listeners };
  };
  const failed = response(503);
  traceRequest(provider, { headers: {} }, failed.res, "POST", "/api/ask");
  failed.res.end("body");
  // The in-memory exporter answers on a timer; the response ends once the flush has.
  await new Promise((resolve) => setTimeout(resolve, 50));
  assert.equal(failed.res.ended, 1);
  failed.listeners.close();
  const served = spans()[0];
  assert.match(served.attributes["gen_ai.conversation.id"], /^[0-9a-f-]{36}$/);
  assert.deepEqual(served.attributes, { "http.request.method": "POST", "http.route": "/api/ask", "http.response.status_code": 503, "error.type": "503", "kge.telemetry.origin": "portal", "kge.agent": "discover_ask", "gen_ai.conversation.id": served.attributes["gen_ai.conversation.id"] });
  assert.deepEqual(served.status, { code: SpanStatusCode.ERROR });

  // A client that leaves before the response is sent; tracing a request twice starts one span.
  const left = response(200);
  const req = { headers: {} };
  traceRequest(provider, req, left.res, "POST", "/api/context/ask");
  traceRequest(provider, req, left.res, "POST", "/api/context/ask");
  left.listeners.close();
  left.listeners.close();
  // One that leaves after the headers went keeps its status.
  const late = response(200);
  traceRequest(provider, { headers: {} }, late.res, "GET", "/api/context/ask/runs/:id");
  late.res.headersSent = true;
  late.listeners.close();
  await provider.forceFlush();
  assert.equal(spans().length, 3);
  const [aborted, lateSpan] = spans().slice(1);
  assert.equal(aborted.name, "POST /api/context/ask");
  assert.match(aborted.attributes["gen_ai.conversation.id"], /^[0-9a-f-]{36}$/);
  assert.deepEqual(aborted.attributes, { "http.request.method": "POST", "http.route": "/api/context/ask", "error.type": "aborted", "kge.telemetry.origin": "portal", "kge.agent": "context_ask", "gen_ai.conversation.id": aborted.attributes["gen_ai.conversation.id"] });
  assert.deepEqual(aborted.status, { code: SpanStatusCode.ERROR });
  assert.equal(lateSpan.attributes["http.response.status_code"], 200);
  assert.equal(lateSpan.attributes["error.type"], "aborted");
});

test("a hanging exporter delays a question by the 2 s flush cap at most, and other requests not at all", async (context) => {
  const kge = await standInKge(context);
  const pending = [];
  const hanging = { export: (_spans, done) => pending.push(done), shutdown: async () => {} };
  const provider = tracingFor(context, { TRACING: "console" }, { exporter: hanging, exportTimeoutMillis: 2500 });
  context.after(() => pending.forEach((done) => done({ code: 0 })));
  const call = await portal(context, provider, kge.url);
  let started = Date.now();
  assert.equal((await call("/api/ask", { question: "q" })).status, 200);
  const waited = Date.now() - started;
  assert.ok(waited >= 1900 && waited < 4000, `waited ${waited} ms`);
  started = Date.now();
  assert.equal((await call("/api/context/whoami", undefined)).status, 200);
  const other = Date.now() - started;
  assert.ok(other < 1000, `other request waited ${other} ms`);
});

test("otlp posts OTLP JSON to TRACING_ENDPOINT with the portal's service name and scope", async (context) => {
  const kge = await standInKge(context);
  const otlp = await collector(context);
  const provider = tracingFor(context, { TRACING: "otlp", TRACING_ENDPOINT: otlp.url });
  // Even with the collector's own origin listed, an export carries no trace context.
  const call = await portal(context, provider, kge.url, { TRACE_PROPAGATE_TO: `${kge.url},${otlp.url}` });
  assert.equal((await call("/api/ask", { question: PLANTED.question }, INCOMING)).status, 200);
  assert.equal(otlp.posts.length, 1);
  const [post] = otlp.posts;
  for (const field of ["traceparent", "tracestate", "baggage"]) assert.equal(post.headers[field], undefined, field);
  assert.ok(kge.seen.some((seen) => seen.traceparent), "the trace reached KGE");
  assert.equal(post.url, "/v1/traces");
  assert.equal(post.headers["content-type"], "application/json");
  const [resourceSpans] = post.body.resourceSpans;
  assert.deepEqual(resourceSpans.resource.attributes, [{ key: "service.name", value: { stringValue: "kge-portal" } }]);
  assert.equal(resourceSpans.scopeSpans[0].scope.name, "kge-portal");
  const [span] = resourceSpans.scopeSpans[0].spans;
  assert.equal(span.name, "POST /api/ask");
  assert.notEqual(span.traceId, "0af7651916cd43dd8448eb211c80319c");
  assert.ok(!span.parentSpanId);
  assert.equal(JSON.stringify(post.body).includes(PLANTED.question), false);
});

test("cloudtrace sends the service account's bearer token and gcp.project_id", async (context) => {
  const kge = await standInKge(context);
  const cloud = await collector(context);
  let minted = 0;
  let asked = 0;
  const auth = {
    getAccessToken: async () => `access-${(minted += 1)}`,
    getProjectId: async () => { asked += 1; return "credentials-project"; },
  };
  const provider = tracingFor(context, { TRACING: "cloudtrace", BIGQUERY_PROJECT: "pilot-project" }, { auth, cloudTraceUrl: cloud.url });
  const call = await portal(context, provider, kge.url, { TRACE_PROPAGATE_TO: `${kge.url},${cloud.url}` });
  await call("/api/ask", { question: "q" }, INCOMING);
  await call("/api/ask", { question: "q" });
  assert.equal(cloud.posts.length, 2);
  for (const post of cloud.posts) for (const field of ["traceparent", "tracestate", "baggage"]) assert.equal(post.headers[field], undefined, field);
  assert.deepEqual(cloud.posts.map((post) => post.headers.authorization), ["Bearer access-1", "Bearer access-2"]);
  assert.deepEqual(cloud.posts[0].body.resourceSpans[0].resource.attributes, [
    { key: "service.name", value: { stringValue: "kge-portal" } },
    { key: "gcp.project_id", value: { stringValue: "pilot-project" } },
  ]);
  assert.equal(asked, 0);

  // Without a project setting, the credentials name it, once.
  const fallback = tracingFor(context, { TRACING: "cloudtrace" }, { auth, cloudTraceUrl: cloud.url });
  for (const _ of [1, 2]) {
    trace.getTracer(SCOPE).startSpan("POST /ask", { attributes: { "http.request.method": "POST", "http.route": "/ask" } }).end();
    await fallback.forceFlush();
  }
  assert.equal(asked, 1);
  assert.deepEqual(cloud.posts.at(-1).body.resourceSpans[0].resource.attributes[1], { key: "gcp.project_id", value: { stringValue: "credentials-project" } });

  // No token: the export fails, the question still answers, and the first
  // failure is logged by its kind and size alone.
  const logged = [];
  const noToken = tracingFor(context, { TRACING: "cloudtrace", BIGQUERY_PROJECT: "pilot-project" }, { auth: { getAccessToken: async () => null, getProjectId: async () => null }, cloudTraceUrl: cloud.url, log: (line) => logged.push(line) });
  const before = cloud.posts.length;
  const again = await portal(context, noToken, kge.url);
  assert.equal((await again("/api/ask", { question: PLANTED.question })).status, 200);
  assert.equal((await again("/api/ask", { question: PLANTED.question })).status, 200);
  assert.equal(cloud.posts.length, before);
  assert.deepEqual(logged, ["Trace export failed (Error, 1 spans); further failures are not logged."]);

  // No project from the setting or the credentials: the portal does not start.
  for (const getProjectId of [async () => null, async () => { throw new Error("no project"); }]) {
    const noProject = tracingFor(context, { TRACING: "cloudtrace" }, { auth: { getAccessToken: async () => "t", getProjectId }, cloudTraceUrl: cloud.url });
    await assert.rejects(noProject.ready, /^Error: TRACING=cloudtrace needs a project: set BIGQUERY_PROJECT$/);
  }

  // Without options.auth, setup builds google-auth-library's own client.
  assert.ok(tracingFor(context, { TRACING: "cloudtrace", BIGQUERY_PROJECT: "p" }));
});

test("console tracing prints the portal's spans", async (context) => {
  const provider = tracingFor(context, { TRACING: "console" });
  const printed = [];
  const dir = console.dir;
  console.dir = (value) => printed.push(value);
  try {
    trace.getTracer(SCOPE).startSpan("POST /ask", { attributes: { "http.request.method": "POST", "http.route": "/ask" } }).end();
    await provider.forceFlush();
  } finally {
    console.dir = dir;
  }
  assert.equal(printed.length, 1);
  assert.equal(printed[0].name, "POST /ask");
  assert.equal(printed[0].resource.attributes["service.name"], "kge-portal");
});

test("kge.request sends the request span's traceparent alone to a listed KGE, and nothing otherwise", async (context) => {
  const sent = [];
  const listed = { TRACE_PROPAGATE_TO: "HTTP://KGE.test:80/any/path" };
  const client = kgeClient("http://kge.test", undefined, "", listed);
  const transport = async (_url, init) => { sent.push(init.headers); return new Response("{}"); };
  // A caller's own trace fields are never passed on, whatever their case.
  const planted = { headers: { traceparent: PARENT, Tracestate: "a=b", BAGGAGE: "c=d", "X-Cloud-Trace-Context": "1/2;o=1", b3: "1-2-1", "sentry-trace": "1-2-1", "X-Request-Id": "planted", "x-correlation-id": "planted" }, transport };
  await client.request({ headers: { traceparent: PARENT } }, "/ask", planted);
  const { provider } = memoryTracing(context);
  await client.request({ headers: { traceparent: PARENT } }, "/ask", planted);
  const span = trace.getTracer(SCOPE).startSpan("POST /ask");
  const traceContext = trace.setSpan(ROOT_CONTEXT, span);
  await client.request({ headers: {}, traceContext }, "/ask", planted);
  // KGE at an origin not listed, or no list at all: nothing.
  await kgeClient("http://kge.test:8443", undefined, "", listed).request({ headers: {}, traceContext }, "/ask", { transport });
  await kgeClient("http://kge.test").request({ headers: {}, traceContext }, "/ask", { transport });
  span.end();
  await provider.forceFlush();
  const fields = (headers) => Object.keys(headers).filter((name) => TRACE_HEADERS.has(name.toLowerCase()));
  assert.deepEqual(sent.map(fields), [[], [], ["traceparent"], [], []]);
  assert.equal(sent[2].traceparent, `00-${span.spanContext().traceId}-${span.spanContext().spanId}-01`);
});

test("a flush waits for the batch the question's own span starts before the response ends", async (context) => {
  const log = [];
  const slow = {
    export(spans, done) { log.push("export started"); setTimeout(() => { log.push("export done"); done({ code: 0 }); }, 150); },
    shutdown: async () => {},
  };
  const provider = tracingFor(context, { TRACING: "console" }, { exporter: slow });
  const tracer = trace.getTracer(SCOPE);
  for (let i = 0; i < 511; i += 1) tracer.startSpan("POST /ask").end();
  assert.deepEqual(log, []);
  let ended;
  const finished = new Promise((resolve) => { ended = resolve; });
  const res = { statusCode: 200, headersSent: false, end() { log.push("response end"); ended(); return res; }, once() {} };
  traceRequest(provider, { headers: {} }, res, "POST", "/api/ask");
  res.end();
  await finished;
  assert.ok(log.indexOf("export done") >= 0 && log.indexOf("export done") < log.indexOf("response end"), log.join(" > "));
});

test("a flush waits for a batch held back by Cloud Trace's project lookup before the response ends", async (context) => {
  const log = [];
  const slow = {
    export(spans, done) { log.push("export started"); setTimeout(() => { log.push("export done"); done({ code: 0 }); }, 50); },
    shutdown: async () => {},
  };
  const auth = {
    getProjectId: () => new Promise((resolve) => setTimeout(() => resolve("your-pilot-project-id"), 300)),
    getAccessToken: async () => "token",
  };
  const provider = tracingFor(context, { TRACING: "cloudtrace" }, { exporter: slow, auth });
  const tracer = trace.getTracer(SCOPE);
  for (let i = 0; i < 511; i += 1) tracer.startSpan("POST /ask").end();
  let ended;
  const finished = new Promise((resolve) => { ended = resolve; });
  const res = { statusCode: 200, headersSent: false, end() { log.push("response end"); ended(); return res; }, once() {} };
  traceRequest(provider, { headers: {} }, res, "POST", "/api/ask");
  res.end();
  await finished;
  assert.ok(log.indexOf("export done") >= 0 && log.indexOf("export done") < log.indexOf("response end"), log.join(" > "));
});

test("a question's flush waits for its own batch, not for a later one that finishes first", async (context) => {
  const log = [];
  const held = [];
  const exporter = {
    export(spans, done) {
      const batch = held.length;
      log.push(`batch ${batch} started`);
      held.push(() => { log.push(`batch ${batch} done`); done({ code: 0 }); });
    },
    shutdown: async () => {},
  };
  const provider = tracingFor(context, { TRACING: "console" }, { exporter });
  const tracer = trace.getTracer(SCOPE);
  for (let i = 0; i < 511; i += 1) tracer.startSpan("POST /ask").end();
  let ended;
  const finished = new Promise((resolve) => { ended = resolve; });
  const res = { statusCode: 200, headersSent: false, end() { log.push("response end"); ended(); return res; }, once() {} };
  traceRequest(provider, { headers: {} }, res, "POST", "/api/ask");
  res.end();
  for (let i = 0; i < 512; i += 1) tracer.startSpan("POST /ask").end();
  const later = provider.forceFlush();
  for (let i = 0; i < 50 && held.length < 2; i += 1) await new Promise((resolve) => setTimeout(resolve, 5));
  assert.equal(held.length, 2, log.join(" > "));
  held[1]();
  await new Promise((resolve) => setTimeout(resolve, 50));
  assert.equal(log.includes("response end"), false, log.join(" > "));
  held[0]();
  await Promise.all([finished, later]);
  assert.ok(log.indexOf("batch 0 done") < log.indexOf("response end"), log.join(" > "));
});

test("a failed batch does not let a question's response end before its own batch is exported", async (context) => {
  const log = [];
  const held = [];
  const exporter = {
    export(spans, done) {
      const batch = log.filter((line) => line.endsWith("started")).length;
      log.push(`batch ${batch} started`);
      if (batch === 1) {
        log.push("batch 1 failed");
        done({ code: 1, error: new Error("refused") });
      } else held.push(() => { log.push(`batch ${batch} done`); done({ code: 0 }); });
    },
    shutdown: async () => {},
  };
  const provider = tracingFor(context, { TRACING: "console" }, { exporter });
  const tracer = trace.getTracer(SCOPE);
  for (let i = 0; i < 1024; i += 1) tracer.startSpan("POST /ask").end();
  let ended;
  const finished = new Promise((resolve) => { ended = resolve; });
  const res = { statusCode: 200, headersSent: false, end() { log.push("response end"); ended(); return res; }, once() {} };
  traceRequest(provider, { headers: {} }, res, "POST", "/api/ask");
  res.end();
  await new Promise((resolve) => setTimeout(resolve, 100));
  assert.equal(log.includes("response end"), false, log.join(" > "));
  for (let i = 0; i < 100 && !log.includes("response end"); i += 1) {
    for (const release of held.splice(0)) release();
    await new Promise((resolve) => setTimeout(resolve, 5));
  }
  await finished;
  const last = log.filter((line) => line.endsWith("started")).at(-1).replace("started", "done");
  assert.ok(log.indexOf(last) >= 0 && log.indexOf(last) < log.indexOf("response end"), log.join(" > "));
});

test("TRACE_PROPAGATE_TO decides which KGE continues a question's trace: none by default, the traceparent alone when listed", async (context) => {
  const { provider, spans } = memoryTracing(context);
  const routes = [["/api/ask", { question: "q" }], ["/api/context/ask", { question: "q" }]];
  const none = (seen) => seen.traceparent === undefined && seen.tracestate === undefined && seen.baggage === undefined;
  // The default, unset or empty: no destination.
  const unlisted = await standInKge(context);
  for (const env of [{}, { TRACE_PROPAGATE_TO: "" }, { TRACE_PROPAGATE_TO: " , " }]) {
    const call = await portal(context, provider, unlisted.url, env);
    for (const [path, body] of routes) assert.equal((await call(path, body, INCOMING)).status, 200, path);
  }
  // Another origin listed, the same host on another port: still none.
  const other = await standInKge(context);
  const elsewhere = await portal(context, provider, unlisted.url, { TRACE_PROPAGATE_TO: other.url });
  for (const [path, body] of routes) assert.equal((await elsewhere(path, body, INCOMING)).status, 200, path);
  assert.ok(unlisted.seen.length >= 8);
  assert.ok(unlisted.seen.every(none), JSON.stringify(unlisted.seen.find((seen) => !none(seen))));
  assert.equal(other.seen.length, 0);

  // Listed (a path in the setting is ignored): the traceparent alone, continuing the question's trace.
  const kge = await standInKge(context);
  const listed = await portal(context, provider, kge.url, { TRACE_PROPAGATE_TO: `https://elsewhere.example.com, ${kge.url}/some/path` });
  await provider.forceFlush();
  const before = spans().length;
  for (const [path, body] of routes) assert.equal((await listed(path, body, INCOMING)).status, 200, path);
  await provider.forceFlush();
  const mine = spans().slice(before);
  assert.deepEqual(mine.map((span) => span.name), ["POST /api/ask", "POST /api/context/ask"]);
  assert.ok(kge.seen.length >= 2);
  for (const seen of kge.seen) {
    assert.equal(seen.tracestate, undefined, seen.url);
    assert.equal(seen.baggage, undefined, seen.url);
    const sent = parseTraceparent(seen.traceparent);
    assert.ok(sent, seen.url);
    assert.notEqual(sent.traceId, "0af7651916cd43dd8448eb211c80319c", seen.url);
    assert.ok(mine.some((span) => span.spanContext().spanId === sent.spanId && span.spanContext().traceId === sent.traceId), seen.url);
  }
});

test("TRACE_PROPAGATE_TO takes http or https origins and refuses anything else at start-up, tracing on or off", async (context) => {
  reset();
  context.after(reset);
  const message = /^Error: TRACE_PROPAGATE_TO must be a comma-separated list of http or https addresses, such as https:\/\/kge\.example\.com$/;
  assert.deepEqual([...traceDestinations({})], []);
  assert.deepEqual([...traceDestinations({ TRACE_PROPAGATE_TO: " , ,  " })], []);
  assert.deepEqual(
    [...traceDestinations({ TRACE_PROPAGATE_TO: "HTTPS://KGE.Example.com:443/x, http://kge.example.com:80,http://127.0.0.1:8181/ask, https://[::1]:9443" })],
    ["https://kge.example.com", "http://kge.example.com", "http://127.0.0.1:8181", "https://[::1]:9443"],
  );
  const refused = ["kge.example.com", "ftp://kge.example.com", "http:kge.example.com", "http:/kge.example.com", "http://", "http://:443", "http://kge:abc", "http://kge:99999",
    "https://kge.example.com/?x=1", "https://kge.example.com#top", "https://kge example.com", "https://kge\t.example.com", "https://kge.example.com\n/x", "https://kge.example.com,nope"];
  for (const value of refused) {
    const env = { TRACE_PROPAGATE_TO: value };
    assert.throws(() => traceDestinations(env), message, value);
    assert.throws(() => setupTracing(env), message, value);
    assert.throws(() => setupTracing({ ...env, TRACING: "console" }), message, value);
    assert.throws(() => createApp({ env, tracing: null, catalogueSync: false, uiPath: "not-present" }), message, value);
  }
  assert.equal(trace.getTracer(SCOPE).startSpan("x").isRecording(), false, "a refused setting registers nothing");
  assert.equal(originOf(new URL("https://A.Example.com:443/p")), "https://a.example.com");
  assert.equal(originOf("mailto:someone@example.com"), null);
  assert.equal(originOf("not an address"), null);
  // No destinations, or no span: no header.
  assert.deepEqual(traceHeaders("https://kge.example.com/ask"), {});
  assert.deepEqual(traceHeaders("https://kge.example.com/ask", ROOT_CONTEXT, new Set(["https://kge.example.com"])), {});
});

// Third-party telemetry and auto-instrumentation: none may enter the portal's dependencies.
const FORBIDDEN = [
  /^@sentry\//, /^posthog/, /^newrelic$/, /^@newrelic\//, /^dd-trace$/, /^@datadog\//, /^applicationinsights$/, /^@azure\/monitor-opentelemetry/,
  /^@opentelemetry\/auto-instrumentations-/, /^@opentelemetry\/instrumentation/, /^@google-cloud\/opentelemetry-/,
  /^@google-cloud\/(trace-agent|debug-agent|profiler|error-reporting)$/, /^elastic-apm-node$/, /^@honeycombio\//, /^@?bugsnag/, /^rollbar$/,
  /^@segment\//, /^analytics-node$/, /^@amplitude\//, /^mixpanel$/,
];
function forbiddenIn(lock) {
  const marker = "node_modules/";
  return Object.keys(lock.packages || {}).filter((path) => path.includes(marker)).map((path) => path.slice(path.lastIndexOf(marker) + marker.length)).filter((name) => FORBIDDEN.some((pattern) => pattern.test(name)));
}

test("no third-party telemetry or auto-instrumentation package is in the lockfile", () => {
  const synthetic = { packages: { "": {}, "node_modules/@sentry/node": {}, "node_modules/a/node_modules/@opentelemetry/instrumentation-http": {}, "node_modules/posthog-node": {},
    "node_modules/@opentelemetry/sdk-metrics": {}, "node_modules/@opentelemetry/sdk-logs": {}, "node_modules/@opentelemetry/api-logs": {}, "node_modules/@opentelemetry/sdk-trace": {} } };
  assert.deepEqual(forbiddenIn(synthetic), ["@sentry/node", "@opentelemetry/instrumentation-http", "posthog-node"]);
  const lock = JSON.parse(readFileSync(new URL("../package-lock.json", import.meta.url), "utf8"));
  assert.ok(Object.keys(lock.packages).length > 50, "the lockfile lists the dependency closure");
  assert.deepEqual(forbiddenIn(lock), []);
});

test("after setupTracing, on or off, the global propagator injects and extracts nothing", async (context) => {
  for (const env of [{ TRACING: "off" }, { TRACING: "off", TRACE_PROPAGATE_TO: "http://kge.test" }, { TRACING: "console" }, { TRACING: "console", TRACE_PROPAGATE_TO: "http://kge.test" }]) {
    const provider = tracingFor(context, env, { exporter: new InMemorySpanExporter() });
    assert.deepEqual(propagation.fields(), [], JSON.stringify(env));
    const span = trace.getTracer(SCOPE).startSpan("POST /ask");
    assert.equal(span.isRecording(), Boolean(provider), JSON.stringify(env));
    const carrier = {};
    propagation.inject(trace.setSpan(ROOT_CONTEXT, span), carrier);
    assert.deepEqual(carrier, {}, JSON.stringify(env));
    assert.equal(trace.getSpan(propagation.extract(ROOT_CONTEXT, INCOMING)), undefined, JSON.stringify(env));
    span.end();
    await provider?.shutdown();
  }
});


test("conversation tags accept only opaque IDs and the exporter drops forged agent or session attributes", async (context) => {
  const { provider, spans } = memoryTracing(context);
  const req = { headers: {} };
  const res = { statusCode: 200, end() {}, once() {} };
  traceRequest(provider, req, res, "POST", "/api/cerebro/stream");
  const span = trace.getSpan(req.traceContext);
  const generated = span.attributes["gen_ai.conversation.id"];
  traceConversation(req, "person@example.com");
  assert.equal(span.attributes["gen_ai.conversation.id"], generated);
  traceConversation(req, "conv-176000");
  assert.equal(span.attributes["gen_ai.conversation.id"], "conv-176000");
  span.setAttribute("kge.agent", "person@example.com");
  span.setAttribute("gen_ai.conversation.id", "person@example.com");
  res.end(); await provider.forceFlush();
  assert.equal(spans().length, 1);
  assert.equal(spans()[0].attributes["kge.agent"], undefined);
  assert.equal(spans()[0].attributes["gen_ai.conversation.id"], undefined);
  assert.doesNotMatch(everything(spans()[0]), /person@example/);
});

test("store-mode MCP trace proof stays separate from person authorization and never goes to another origin", async (context) => {
  const { provider } = memoryTracing(context);
  const span = provider.getTracer(SCOPE).startSpan("POST /api/cerebro/stream", { attributes: {
    "http.request.method": "POST", "http.route": "/api/cerebro/stream", "kge.agent": "cerebro",
    "gen_ai.conversation.id": "aaaabbbb-aaaa-aaaa-aaaa-aaaabbbbaaaa",
  } });
  const ctx = trace.setSpan(ROOT_CONTEXT, span);
  const client = kgeClient("http://kge.test", { headers: async () => ({ "x-caller-token": "service-proof" }) }, "", { TRACING: "store", TRACE_PROPAGATE_TO: "http://kge.test,http://other.test" });
  const sent = [];
  const transport = async (url, options) => { sent.push({ url: String(url), headers: options.headers }); return new Response("{}"); };
  await client.request({ headers: {}, traceContext: ctx }, "/mcp", { trusted: false, transport, headers: { "x-kge-authorization": "Bearer person" } });
  await client.request({ headers: {}, traceContext: ctx }, "http://other.test/mcp", { trusted: false, transport, headers: { "x-kge-authorization": "Bearer person" } });
  assert.equal(sent[0].headers["x-kge-authorization"], "Bearer person");
  assert.equal(sent[0].headers["x-caller-token"], undefined);
  assert.equal(sent[0].headers["x-kge-trace-proof"], "service-proof");
  assert.equal(sent[0].headers["x-kge-trace-agent"], "cerebro");
  assert.equal(sent[0].headers["x-kge-trace-session"], "aaaabbbb-aaaa-aaaa-aaaa-aaaabbbbaaaa");
  assert.equal(sent[1].headers["x-kge-trace-proof"], undefined);
  assert.equal(sent[1].headers["x-kge-trace-agent"], undefined);
  assert.ok(sent[1].headers.traceparent);
  assert.equal(sent[1].headers["x-kge-trace-session"], undefined);
  span.end();
});

test("Context's existing opaque conversation ID is kept; unsafe client headers never enter a span", async (context) => {
  const { provider, spans } = memoryTracing(context);
  for (const value of ["conv-176000", "person@example.com"]) {
    const req = { headers: { "x-kge-conversation-id": value } };
    const res = { statusCode: 200, end() {}, once() {} };
    traceRequest(provider, req, res, "POST", "/api/context/ask"); res.end();
  }
  await provider.forceFlush();
  assert.equal(spans()[0].attributes["gen_ai.conversation.id"], "conv-176000");
  assert.match(spans()[1].attributes["gen_ai.conversation.id"], /^[0-9a-f-]{36}$/);
  assert.doesNotMatch(spans().map(everything).join(""), /person@example/);
});


test("background question and repeated polling create one portal question trace", async (context) => {
  const kge = await standInKge(context); const { provider, spans } = memoryTracing(context);
  const call = await portal(context, provider, kge.url, { TRACE_PROPAGATE_TO: kge.url });
  const session = "conv-176000";
  assert.equal((await call("/api/context/ask?background=true", { question: "q" }, { "x-kge-conversation-id": session })).status, 200);
  for (let n = 0; n < 3; n += 1) assert.equal((await call(`/api/context/ask/runs/run-${n}`, undefined, { "x-kge-conversation-id": session })).status, 200);
  await provider.forceFlush();
  assert.equal(spans().length, 1); assert.equal(spans()[0].name, "POST /api/context/ask");
  assert.equal(spans()[0].attributes["gen_ai.conversation.id"], session);
  const polls = kge.seen.filter((item) => item.url.startsWith("/ask/runs/"));
  assert.equal(polls.length, 3); assert.ok(polls.every((item) => !item.traceparent));
});

test("store mode defaults to KGE propagation but honors an explicit empty or other-only destination", async (context) => {
  const { provider } = memoryTracing(context);
  const span = provider.getTracer(SCOPE).startSpan("POST /api/cerebro/stream", { attributes: {
    "http.request.method": "POST", "http.route": "/api/cerebro/stream", "kge.agent": "cerebro",
    "gen_ai.conversation.id": crypto.randomUUID(),
  } });
  const ctx = trace.setSpan(ROOT_CONTEXT, span); const all = [];
  const transport = async (_url, options) => { all.push(options.headers); return new Response("{}"); };
  for (const extra of [{}, { TRACE_PROPAGATE_TO: "" }, { TRACE_PROPAGATE_TO: "https://elsewhere.test" }]) {
    const client = kgeClient("https://kge.test", { headers: async () => ({ "x-caller-token": "proof" }) }, "", { TRACING: "store", ...extra });
    await client.request({ headers: {}, traceContext: ctx }, "/mcp", { trusted: false, transport });
  }
  assert.ok(all[0].traceparent); assert.equal(all[0]["x-kge-trace-agent"], "cerebro");
  for (const sent of all.slice(1)) {
    assert.equal(sent.traceparent, undefined); assert.equal(sent["x-kge-trace-agent"], undefined);
    assert.equal(sent["x-kge-trace-session"], undefined); assert.equal(sent["x-kge-trace-proof"], undefined);
  }
  span.end();
});

test("the README local environment example enables store tracing and KGE propagation", async (context) => {
  const example = readFileSync(new URL("../../../.env.example", import.meta.url), "utf8");
  const env = Object.fromEntries(example.split(/\r?\n/).filter((line) => /^[A-Za-z_][A-Za-z_0-9]*=/.test(line))
    .map((line) => { const index = line.indexOf("="); return [line.slice(0, index), line.slice(index + 1).replace(/\s+#.*$/, "").trim()]; }));
  assert.equal(env.TRACING, "store");
  assert.equal(Object.hasOwn(env, "TRACE_PROPAGATE_TO"), false);
  const { provider } = memoryTracing(context);
  const span = provider.getTracer(SCOPE).startSpan("POST /api/cerebro/stream", { attributes: {
    "http.request.method": "POST", "http.route": "/api/cerebro/stream", "kge.agent": "cerebro",
    "gen_ai.conversation.id": crypto.randomUUID(),
  } });
  const ctx = trace.setSpan(ROOT_CONTEXT, span);
  const headers = [];
  for (const override of [{}, { TRACING: "off" }, { TRACE_PROPAGATE_TO: "" }]) {
    const client = kgeClient("https://kge.test", { headers: async () => ({ "x-caller-token": "proof" }) }, "", { ...env, ...override });
    await client.request({ headers: {}, traceContext: ctx }, "/mcp", { trusted: false,
      transport: async (_url, options) => { headers.push(options.headers); return new Response("{}"); } });
  }
  assert.ok(headers[0].traceparent);
  assert.equal(headers[1].traceparent, undefined);
  assert.equal(headers[2].traceparent, undefined);
  span.end();
});
