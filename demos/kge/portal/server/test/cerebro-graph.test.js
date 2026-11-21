import assert from "node:assert/strict";
import { createServer } from "node:http";
import { mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { after, before, test } from "node:test";
import { createApp } from "../src/app.js";
import { openSqliteRepository } from "../src/repository/sqlite.js";
import { localCredentials } from "./fixtures/identity.js";

// A fake KGE that records each call and answers by path.
// The portal's sign-in reads /whoami (not recorded): the Cerebro role is the cookie's last part, a steward by default.
const ROLES = ["business", "steward", "engineer", "admin"];
const roleOf = (cookie = "") => {
  const named = cookie.split("=").pop();
  return [ROLES.includes(named) ? named : "steward"];
};
const seen = [];
const kge = createServer((req, res) => {
  const chunks = [];
  req.on("data", (chunk) => chunks.push(chunk));
  req.on("end", () => {
    const body = Buffer.concat(chunks).toString();
    const send = (status, value, headers = {}) => res.writeHead(status, { "content-type": "application/json", ...headers }).end(JSON.stringify(value));
    if (req.url === "/whoami") return send(200, { id: "person-1", cerebro_roles: roleOf(req.headers.cookie) });
    if (req.url === "/readable-units") return send(200, { units: [] });
    seen.push({ method: req.method, url: req.url, headers: req.headers, body });
    if (req.url.includes("node=refused")) return send(404, { detail: "No such node." });
    if (req.url.includes("cursor=busy")) return send(429, { detail: "busy" }, { "retry-after": "7" });
    if (req.url.includes("cursor=nobody")) return send(401, { detail: "Sign in." });
    if (req.url === "/health") {
      if (health.status !== 200) return send(health.status, { detail: "Sign in." });
      if (health.raw !== undefined) return res.writeHead(200, { "content-type": "text/plain" }).end(health.raw);
      return send(200, health.body);
    }
    if (req.url.startsWith("/graph/explore")) return send(422, { detail: "Give a node or a question, not both.", echo: body });
    if (req.url.startsWith("/graph")) return send(200, { nodes: [{ id: "term:revenue", kind: "term" }], links: [], cursor: "page-2" });
    return send(404, { detail: "Not found." });
  });
});
// KGE's /health reply, set per test.
const ASK = { default: "strict", modes: ["off", "strict", "open"], model: "gemini-2.5-pro" };
let health = { status: 200, body: { status: "ok", build: "abc123", release: "r-7", ask: ASK } };
const service = { headers: async () => ({ "x-serverless-authorization": "Bearer service-token", "x-caller-token": "portal-caller-token" }) };
const person = { cookie: "kge_session_meridian=person", authorization: "Bearer person-token" };
let kgeUrl;
let directory;
const servers = [];

before(async () => {
  await new Promise((resolve) => kge.listen(0, "127.0.0.1", resolve));
  kgeUrl = `http://127.0.0.1:${kge.address().port}`;
  directory = await mkdtemp(join(tmpdir(), "cerebro-graph-"));
});
after(async () => { kge.close(); for (const server of servers) server.close(); await rm(directory, { recursive: true, force: true }); });

async function start(flags = {}, env = { KGE_URL: kgeUrl, PORTAL_SYNC_TOKEN: "s".repeat(32), ...flags }) {
  const repository = openSqliteRepository({ PORTAL_SQLITE_PATH: ":memory:" });
  const { app } = createApp({ repository, env, serviceAuth: service, catalogueSync: false, uiPath: "not-present" });
  const server = app.listen(0, "127.0.0.1");
  servers.push(server);
  await new Promise((resolve) => server.once("listening", resolve));
  return `http://127.0.0.1:${server.address().port}`;
}

test("GET /api/cerebro/graph forwards cursor, limit and node only, as the person, and passes KGE's page through", async () => {
  const base = await start();
  seen.length = 0;
  const response = await fetch(`${base}/api/cerebro/graph?cursor=page-1&limit=50&node=term%3Arevenue&question=what+is+revenue&extra=1`, { headers: person });
  assert.equal(response.status, 200);
  assert.deepEqual(await response.json(), { nodes: [{ id: "term:revenue", kind: "term" }], links: [], cursor: "page-2" });
  assert.equal(seen.length, 1);
  const call = seen[0];
  assert.equal(call.method, "GET");
  assert.equal(call.url, "/graph?cursor=page-1&limit=50&node=term%3Arevenue");
  assert.equal(call.headers["x-kge-authorization"], "Bearer person-token");
  assert.equal(call.headers.authorization, "Bearer person-token");
  assert.equal(call.headers.cookie, "kge_session_meridian=person");
  assert.equal(call.headers["x-caller-token"], undefined);
  assert.equal(call.headers["x-serverless-authorization"], "Bearer service-token");
});

test("GET /api/cerebro/graph with no parameters reads the first page; a cookie alone sends no x-kge-authorization", async () => {
  const base = await start();
  seen.length = 0;
  const response = await fetch(`${base}/api/cerebro/graph`, { headers: { cookie: person.cookie } });
  assert.equal(response.status, 200);
  assert.equal(seen[0].url, "/graph");
  assert.equal(seen[0].headers.cookie, person.cookie);
  assert.equal(seen[0].headers["x-kge-authorization"], undefined);
});

test("KGE's refusals pass through with their status, body and Retry-After unchanged", async () => {
  const base = await start();
  for (const [query, status, body, retry] of [
    ["node=refused", 404, { detail: "No such node." }, null],
    ["cursor=busy", 429, { detail: "busy" }, "7"],
    ["cursor=nobody", 401, { detail: "Sign in." }, null],
  ]) {
    const response = await fetch(`${base}/api/cerebro/graph?${query}`, { headers: person });
    assert.equal(response.status, status);
    assert.deepEqual(await response.json(), body);
    assert.equal(response.headers.get("retry-after"), retry);
  }
});

test("POST /api/cerebro/explore forwards the body as sent to KGE's POST /graph/explore, as the person", async () => {
  const base = await start();
  seen.length = 0;
  const sent = JSON.stringify({ question: "Which tables hold revenue?" });
  const response = await fetch(`${base}/api/cerebro/explore?node=ignored`, { method: "POST", headers: { ...person, "content-type": "application/json" }, body: sent });
  assert.equal(response.status, 422);
  assert.deepEqual(await response.json(), { detail: "Give a node or a question, not both.", echo: sent });
  assert.equal(seen[0].method, "POST");
  assert.equal(seen[0].url, "/graph/explore");
  assert.equal(seen[0].body, sent);
  assert.equal(seen[0].headers["content-type"], "application/json");
  assert.equal(seen[0].headers["x-kge-authorization"], "Bearer person-token");
  assert.equal(seen[0].headers["x-caller-token"], undefined);
  seen.length = 0;
  await fetch(`${base}/api/cerebro/explore`, { method: "POST", headers: person });
  assert.equal(seen[0].body, "");
});

test("Cerebro's routes are refused when Cerebro is off or KGE is not joined, and never reach KGE", async () => {
  for (const base of [
    await start({ PORTAL_APP_CEREBRO: "off" }),
    await start({}, await localCredentials(directory)),
  ]) {
    seen.length = 0;
    for (const [path, method] of [["/api/cerebro/graph", "GET"], ["/api/cerebro/explore", "POST"], ["/api/cerebro/health", "GET"]]) {
      const response = await fetch(`${base}${path}`, { method, headers: person });
      assert.equal(response.status, 404);
      assert.match((await response.json()).detail, /^Cerebro is not set up here$/);
    }
    assert.equal(seen.length, 0);
  }
});

test("Cerebro's graph read and health are on with Context and Discover off", async () => {
  const base = await start({ PORTAL_APP_CONTEXT: "off", PORTAL_APP_DISCOVER: "off" });
  const response = await fetch(`${base}/api/cerebro/graph?limit=5`, { headers: person });
  assert.equal(response.status, 200);
  const askHealth = await fetch(`${base}/api/cerebro/health`, { headers: person });
  assert.equal(askHealth.status, 200);
  assert.deepEqual(await askHealth.json(), ASK);
});

test("GET /api/cerebro/health answers KGE /health's ask object alone, read as the person", async () => {
  const base = await start();
  seen.length = 0;
  const response = await fetch(`${base}/api/cerebro/health?extra=1`, { headers: person });
  assert.equal(response.status, 200);
  assert.deepEqual(await response.json(), ASK);
  assert.equal(seen.length, 1);
  assert.equal(seen[0].method, "GET");
  assert.equal(seen[0].url, "/health");
  assert.equal(seen[0].headers["x-kge-authorization"], "Bearer person-token");
  assert.equal(seen[0].headers["x-caller-token"], undefined);
});

test("GET /api/cerebro/health is null without an ask object, passes a refusal through, and is a 502 on a reply that is not JSON", async () => {
  const base = await start({ PORTAL_CEREBRO_NAME: "Cerebro" });
  try {
    health = { status: 200, body: { status: "ok", build: "abc123" } };
    let response = await fetch(`${base}/api/cerebro/health`, { headers: person });
    assert.equal(response.status, 200);
    assert.equal(await response.json(), null);
    health = { status: 401 };
    response = await fetch(`${base}/api/cerebro/health`, { headers: person });
    assert.equal(response.status, 401);
    assert.deepEqual(await response.json(), { detail: "Sign in." });
    health = { status: 200, raw: "not json" };
    response = await fetch(`${base}/api/cerebro/health`, { headers: person });
    assert.equal(response.status, 502);
    assert.deepEqual(await response.json(), { detail: "Cerebro is unavailable right now. Try again shortly." });
  } finally {
    health = { status: 200, body: { status: "ok", build: "abc123", release: "r-7", ask: ASK } };
  }
});

test("other paths and methods under /api/cerebro are not found, and an unreachable KGE is a 502 at the sign-in", async () => {
  const base = await start({ PORTAL_CEREBRO_NAME: "Cerebro" });
  seen.length = 0;
  for (const [path, method] of [["/api/cerebro/graph", "POST"], ["/api/cerebro/explore", "GET"], ["/api/cerebro/health", "POST"], ["/api/cerebro/other", "GET"]]) {
    const response = await fetch(`${base}${path}`, { method, headers: person });
    assert.equal(response.status, 404);
  }
  assert.equal(seen.length, 0);
  const down = await start({}, { KGE_URL: "http://127.0.0.1:9", PORTAL_SYNC_TOKEN: "s".repeat(32), PORTAL_CEREBRO_NAME: "Cerebro" });
  for (const path of ["/api/cerebro/graph", "/api/cerebro/health"]) {
    const response = await fetch(`${down}${path}`, { headers: person });
    assert.equal(response.status, 502);
    const body = await response.text();
    assert.match(body, /unavailable/);
    assert.doesNotMatch(body, /127\.0\.0\.1/);
  }
});

test("the graph read, explore, feedback and health open to business, steward and engineer; admin alone is refused before KGE", async () => {
  const base = await start({ PORTAL_CEREBRO_NAME: "Cerebro" });
  const calls = [["/api/cerebro/graph", "GET", "/graph"], ["/api/cerebro/explore", "POST", "/graph/explore"],
    ["/api/cerebro/ask/feedback", "POST", "/ask/feedback"], ["/api/cerebro/health", "GET", "/health"]];
  for (const role of ["business", "steward", "engineer", "admin"]) {
    for (const [path, method, target] of calls) {
      seen.length = 0;
      const response = await fetch(`${base}${path}`, { method, headers: { cookie: `kge_session_meridian=${role}`, ...(method === "POST" ? { "content-type": "application/json" } : {}) }, ...(method === "POST" ? { body: "{}" } : {}) });
      if (role === "admin") {
        assert.equal(response.status, 403, `${role} ${path}`);
        assert.deepEqual(await response.json(), { detail: "Cerebro needs a Cerebro role.", message: "Cerebro needs a Cerebro role." });
        assert.equal(seen.length, 0);
      } else {
        assert.notEqual(response.status, 403, `${role} ${path}`);
        assert.deepEqual(seen.map((call) => call.url), [target], `${role} ${path}`);
      }
    }
  }
});
