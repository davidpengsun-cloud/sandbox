import assert from "node:assert/strict";
import { createServer } from "node:http";
import { mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { after, before, test } from "node:test";
import { createApp } from "../src/app.js";
import { openSqliteRepository } from "../src/repository/sqlite.js";
import { localCredentials } from "./fixtures/identity.js";

// A fake KGE steward service that records each call and answers by path.
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
    const send = (status, value) => res.writeHead(status, { "content-type": "application/json" }).end(JSON.stringify(value));
    if (req.url === "/whoami") return send(200, { id: "person-1", cerebro_roles: roleOf(req.headers.cookie) });
    if (req.url === "/readable-units") return send(200, { units: [] });
    seen.push({ method: req.method, url: req.url, headers: req.headers, body });
    if (req.url === "/steward/queue") return send(200, [{ tag: "t1", family: "candidate_term", value: "churn", doc: "d1" }]);
    if (req.url === "/steward/queue?status=decided") return send(200, [{ tag: "t0", decision: "accept", who: "sam", at: "2026-11-15" }]);
    if (req.url === "/steward/decide" && body.includes("forbidden")) return send(403, { detail: "Not a steward." });
    if (req.url === "/steward/decide") return send(200, { decision: "accept", echo: body });
    if (req.url === "/steward/gaps") return send(200, { days: 7, total: 3, gaps: [{ text: "churn by region", count: 3 }] });
    if (req.url === "/onboard" && req.method === "GET") return send(200, { candidate: { hash: "abc123" }, can: { certify: true } });
    if (req.url === "/onboard/accept" && body.includes("nobusiness")) return send(403, { detail: "Not a business owner." });
    if (["/onboard/accept", "/onboard/certify", "/onboard/run"].includes(req.url)) return send(200, { said: "Done.", echo: body });
    return send(404, { detail: "Not found." });
  });
});
const service = { headers: async () => ({ "x-serverless-authorization": "Bearer service-token", "x-caller-token": "portal-caller-token" }) };
const person = { cookie: "kge_session_meridian=steward", authorization: "Bearer steward-token" };
let kgeUrl;
let directory;
const servers = [];

before(async () => {
  await new Promise((resolve) => kge.listen(0, "127.0.0.1", resolve));
  kgeUrl = `http://127.0.0.1:${kge.address().port}`;
  directory = await mkdtemp(join(tmpdir(), "cerebro-steward-"));
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

test("GET /api/cerebro/steward/queue reads the pending and the decided queue as the person, never the trusted caller", async () => {
  const base = await start({ PORTAL_APP_CONTEXT: "off" });
  seen.length = 0;
  const pending = await fetch(`${base}/api/cerebro/steward/queue?extra=1`, { headers: person });
  assert.equal(pending.status, 200);
  assert.deepEqual(await pending.json(), [{ tag: "t1", family: "candidate_term", value: "churn", doc: "d1" }]);
  const decided = await fetch(`${base}/api/cerebro/steward/queue?status=decided`, { headers: person });
  assert.equal(decided.status, 200);
  assert.equal((await decided.json())[0].who, "sam");
  assert.deepEqual(seen.map((call) => call.url), ["/steward/queue", "/steward/queue?status=decided"]);
  for (const call of seen) {
    assert.equal(call.headers["x-kge-authorization"], "Bearer steward-token");
    assert.equal(call.headers.cookie, person.cookie);
    assert.equal(call.headers["x-caller-token"], undefined);
  }
});

test("a status other than pending or decided is refused before KGE", async () => {
  const base = await start();
  seen.length = 0;
  for (const query of ["status=all", "status=pending&status=decided"]) {
    const response = await fetch(`${base}/api/cerebro/steward/queue?${query}`, { headers: person });
    assert.equal(response.status, 400);
  }
  assert.equal(seen.length, 0);
});

test("POST /api/cerebro/steward/decide forwards the body as sent, and KGE's refusal passes through", async () => {
  const base = await start({ PORTAL_APP_CONTEXT: "off" });
  seen.length = 0;
  const sent = JSON.stringify({ tag: "t1", decision: "accept", definition: "Customers who leave." });
  const response = await fetch(`${base}/api/cerebro/steward/decide`, { method: "POST", headers: { ...person, "content-type": "application/json" }, body: sent });
  assert.equal(response.status, 200);
  assert.deepEqual(await response.json(), { decision: "accept", echo: sent });
  assert.equal(seen[0].method, "POST");
  assert.equal(seen[0].body, sent);
  assert.equal(seen[0].headers["x-kge-authorization"], "Bearer steward-token");
  assert.equal(seen[0].headers["x-caller-token"], undefined);
  const refused = await fetch(`${base}/api/cerebro/steward/decide`, { method: "POST", headers: { ...person, "content-type": "application/json" }, body: JSON.stringify({ tag: "forbidden", decision: "reject" }) });
  assert.equal(refused.status, 403);
  assert.deepEqual(await refused.json(), { detail: "Not a steward." });
});

test("the steward routes are refused when Cerebro is off or KGE is not joined; other paths are not found", async () => {
  for (const base of [await start({ PORTAL_APP_CEREBRO: "off" }), await start({}, await localCredentials(directory))]) {
    seen.length = 0;
    const response = await fetch(`${base}/api/cerebro/steward/queue`, { headers: person });
    assert.equal(response.status, 404);
    assert.equal(seen.length, 0);
  }
  const base = await start();
  seen.length = 0;
  for (const [path, method] of [["/api/cerebro/steward/queue", "POST"], ["/api/cerebro/steward/decide", "GET"], ["/api/cerebro/steward/other", "GET"],
    ["/api/cerebro/steward/gaps", "POST"], ["/api/cerebro/steward/candidate", "POST"], ["/api/cerebro/steward/certify", "GET"], ["/api/cerebro/steward/run", "POST"], ["/api/cerebro/steward/rollback", "POST"]]) {
    assert.equal((await fetch(`${base}${path}`, { method, headers: person })).status, 404);
  }
  assert.equal(seen.length, 0);
});

const asTheSteward = (call) => {
  assert.equal(call.headers["x-kge-authorization"], "Bearer steward-token");
  assert.equal(call.headers.cookie, person.cookie);
  assert.equal(call.headers["x-caller-token"], undefined);
};

test("GET /api/cerebro/steward/gaps and /candidate read KGE's gaps and onboarding status as the person", async () => {
  const base = await start({ PORTAL_APP_CONTEXT: "off" });
  seen.length = 0;
  const gaps = await fetch(`${base}/api/cerebro/steward/gaps?days=90`, { headers: person });
  assert.equal(gaps.status, 200);
  assert.equal((await gaps.json()).gaps[0].count, 3);
  const candidate = await fetch(`${base}/api/cerebro/steward/candidate`, { headers: person });
  assert.equal((await candidate.json()).candidate.hash, "abc123");
  assert.deepEqual(seen.map((call) => call.url), ["/steward/gaps", "/onboard"]);
  seen.forEach(asTheSteward);
});

test("POST accept and certify forward the body as sent; KGE's refusal passes through", async () => {
  const base = await start({ PORTAL_APP_CONTEXT: "off" });
  seen.length = 0;
  const sent = JSON.stringify({ unit: "sales", items: "all" });
  for (const action of ["accept", "certify"]) {
    const response = await fetch(`${base}/api/cerebro/steward/${action}`, { method: "POST", headers: { ...person, "content-type": "application/json" }, body: sent });
    assert.equal(response.status, 200);
    assert.equal((await response.json()).echo, sent);
  }
  assert.deepEqual(seen.map((call) => [call.method, call.url, call.body]), [["POST", "/onboard/accept", sent], ["POST", "/onboard/certify", sent]]);
  seen.forEach(asTheSteward);
  const refused = await fetch(`${base}/api/cerebro/steward/accept`, { method: "POST", headers: { ...person, "content-type": "application/json" }, body: JSON.stringify({ unit: "nobusiness" }) });
  assert.equal(refused.status, 403);
  assert.deepEqual(await refused.json(), { detail: "Not a business owner." });
});

test("POST sign-off runs only the sign-off step, with the candidate's hash, whatever the body asks", async () => {
  const base = await start({ PORTAL_APP_CONTEXT: "off" });
  seen.length = 0;
  const signed = await fetch(`${base}/api/cerebro/steward/sign-off`, { method: "POST", headers: { ...person, "content-type": "application/json" }, body: JSON.stringify({ hash: "abc123" }) });
  assert.equal(signed.status, 200);
  const bare = await fetch(`${base}/api/cerebro/steward/sign-off`, { method: "POST", headers: person });
  assert.equal(bare.status, 200);
  const asked = await fetch(`${base}/api/cerebro/steward/sign-off`, { method: "POST", headers: { ...person, "content-type": "application/json" }, body: JSON.stringify({ step: "release", hash: "abc123" }) });
  assert.equal(asked.status, 200);
  assert.deepEqual(seen.map((call) => [call.url, JSON.parse(call.body)]), [
    ["/onboard/run", { step: "sign-off", hash: "abc123" }], ["/onboard/run", { step: "sign-off" }], ["/onboard/run", { step: "sign-off", hash: "abc123" }]]);
  assert.equal(seen[0].headers["content-type"], "application/json");
  seen.forEach(asTheSteward);
  seen.length = 0;
  for (const body of ["{not json", JSON.stringify({ hash: "../x" }), JSON.stringify([1]), JSON.stringify({ hash: 7 }), JSON.stringify({ hash: "a".repeat(5000) })]) {
    const response = await fetch(`${base}/api/cerebro/steward/sign-off`, { method: "POST", headers: { ...person, "content-type": "application/json" }, body });
    assert.equal(response.status, 400);
  }
  assert.equal(seen.length, 0);
});

test("Curate's routes open to stewards alone: business, engineer and admin are refused before KGE", async () => {
  const base = await start({ PORTAL_CEREBRO_NAME: "Cerebro" });
  const calls = [["/api/cerebro/steward/queue", "GET", "/steward/queue"], ["/api/cerebro/steward/decide", "POST", "/steward/decide"],
    ["/api/cerebro/steward/gaps", "GET", "/steward/gaps"], ["/api/cerebro/steward/candidate", "GET", "/onboard"],
    ["/api/cerebro/steward/accept", "POST", "/onboard/accept"], ["/api/cerebro/steward/certify", "POST", "/onboard/certify"],
    ["/api/cerebro/steward/sign-off", "POST", "/onboard/run"]];
  for (const role of ["business", "steward", "engineer", "admin"]) {
    for (const [path, method, target] of calls) {
      seen.length = 0;
      const response = await fetch(`${base}${path}`, { method, headers: { cookie: `kge_session_meridian=${role}`, ...(method === "POST" ? { "content-type": "application/json" } : {}) }, ...(method === "POST" ? { body: "{}" } : {}) });
      if (role === "steward") {
        assert.equal(response.status, 200, `${role} ${path}`);
        assert.deepEqual(seen.map((call) => call.url), [target], `${role} ${path}`);
      } else {
        assert.equal(response.status, 403, `${role} ${path}`);
        assert.equal((await response.json()).detail, "Cerebro needs a Cerebro role.");
        assert.equal(seen.length, 0);
      }
    }
  }
});
