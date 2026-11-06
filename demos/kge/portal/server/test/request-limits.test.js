import assert from "node:assert/strict";
import test from "node:test";
import { randomUUID } from "node:crypto";
import { createApp } from "../src/app.js";
import { kgeClient, relay } from "../src/kge.js";
import { openProviders } from "../src/providers.js";
import { knowledgeProvider } from "../src/knowledge.js";

const detail = "Too many requests at once; try again in a moment.";
const response = () => ({
  headers: {},
  status(value) { this.code = value; return this; },
  set(key, value) { this.headers[key.toLowerCase()] = value; return this; },
  removeHeader(key) { delete this.headers[key.toLowerCase()]; },
  json(value) { this.body = value; return this; },
  type(value) { return this.set("content-type", value); },
  append(key, value) { return this.set(key, value); },
  send(value) { this.body = JSON.parse(value.toString()); return this; },
});
const refused = () => Response.json({ detail: "upstream wording" }, { status: 429, headers: { "retry-after": "7" } });
const request = { headers: { authorization: "Bearer invented" }, portalIdentity: { units: [] } };
const appFor = (kge) => createApp({ kge, catalogueSync: false, repository: {}, env: {}, uiPath: "not-present" }).app;
const errorHandler = (app) => app.router.stack.findLast((layer) => layer.handle.length === 4).handle;
function assertLimited(res, field = "detail") {
  assert.equal(res.code, 429);
  assert.deepEqual(res.body, { [field]: detail });
  assert.equal(res.headers["retry-after"], "7");
}

test("identity refusal preserves Retry-After and the portal reports capacity, not an outage", async (context) => {
  context.mock.method(globalThis, "fetch", async () => refused());
  const kge = kgeClient("http://invented.invalid");
  await assert.rejects(kge.identity(request), (error) => error.status === 429 && error.retryAfter === "7");
  const app = appFor(kge);
  const identity = app.router.stack.find((layer) => !layer.route && layer.match("/api") && layer.handle.length === 3 && layer.handle.name === "").handle;
  const res = response();
  await identity(request, res, (error) => { throw error || new Error("refused identity advanced"); });
  assertLimited(res, "message");
  const direct = response();
  try { await kge.identity(request); } catch (error) { errorHandler(app)(error, request, direct, () => {}); }
  assertLimited(direct);
});

for (const kind of ["ask", "search"]) test(`${kind} propagates a 429 without falling back`, async () => {
  const kge = { request: async () => refused() };
  const provider = knowledgeProvider(kind, { [kind]: () => assert.fail("429 triggered fallback") }, kge);
  const res = response();
  await assert.rejects(provider.run(request, { question: "invented", query: "invented" }), (error) => {
    errorHandler(appFor(kge))(error, request, res, () => {});
    return error.status === 429;
  });
  assertLimited(res);
});

test("data routes return the capacity message and Retry-After", async () => {
  const kge = { request: async () => refused() };
  const provider = openProviders({}, kge, "kge", {}).data;
  const res = response();
  await provider.route(request, res, "/data/query", { sql: "invented" });
  assertLimited(res);
});

test("capacity without Retry-After is still distinct from unavailable", () => {
  const handler = errorHandler(appFor({}));
  const res = response();
  handler(Object.assign(new Error("KGE returned 429"), { status: 429 }), request, res, () => {});
  assert.equal(res.code, 429);
  assert.deepEqual(res.body, { detail });
  assert.equal(res.headers["retry-after"], undefined);
  const unavailable = response();
  handler(Object.assign(new Error("Context is unavailable."), { status: 503 }), request, unavailable, () => {});
  assert.equal(unavailable.code, 503);
  assert.deepEqual(unavailable.body, { detail: "Context is unavailable." });
});


test("sign-in lockouts retain their existing wording and retry delay", async () => {
  const res = response();
  await relay(refused(), res);
  assert.equal(res.code, 429);
  assert.equal(res.headers["retry-after"], "7");
  assert.deepEqual(res.body, { detail: "upstream wording" });
});

for (const selected of ["Certify", "Context", "Discover"]) test(`shared sign-in and identity outages name ${selected} when it runs alone`, async () => {
  const env = { PORTAL_KNOWLEDGE_HOME: "off", PORTAL_APP_CERTIFY: "off", PORTAL_APP_CONTEXT: "off", PORTAL_APP_DISCOVER: "off", [`PORTAL_APP_${selected.toUpperCase()}`]: "on" };
  const down = async () => { throw new TypeError("fetch failed"); };
  const app = createApp({ kge: { request: down, identity: down }, catalogueSync: false, repository: {}, env, uiPath: "not-present" }).app;
  const req = { ...request, socket: { remoteAddress: "127.0.0.1" }, body: { username: "invented", password: randomUUID() } };
  const session = app.router.stack.find((layer) => layer.route?.path === "/session" && layer.route.methods.post).route.stack[0].handle;
  const res = response();
  await session(req, res, (error) => errorHandler(app)(error, req, res, () => {}));
  assert.equal(res.code, 502);
  assert.deepEqual(res.body, { detail: `${selected} is unavailable.` });
  const identity = app.router.stack.find((layer) => !layer.route && layer.match("/api") && layer.handle.length === 3 && layer.handle.name === "").handle;
  const denied = response();
  await identity(req, denied, (error) => { throw error || new Error("failed identity advanced"); });
  assert.equal(denied.code, 502);
  assert.deepEqual(denied.body, { message: `${selected} is unavailable.` });
});
