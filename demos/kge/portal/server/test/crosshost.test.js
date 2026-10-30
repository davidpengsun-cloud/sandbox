import assert from "node:assert/strict";
import { createServer } from "node:http";
import { after, before, test } from "node:test";
import { createApp } from "../src/app.js";
import { openSqliteRepository } from "../src/repository/sqlite.js";
import { catalogue } from "./fixtures/catalogue.js";

const COOKIE = "kge_session_meridian";
const people = {
  "token-retail": { id: "analyst_retail", units: ["retail"] },
  "token-wealth": { id: "advisor_wealth", units: ["wealth"] },
  "token-admin": { id: "admin", units: ["corporate", "retail", "sme", "wealth"] },
  "token-stranger": { id: "stranger", units: [] },
};
const passwords = { analyst_retail: "token-retail", advisor_wealth: "token-wealth", admin: "token-admin", stranger: "token-stranger" };
const seen = [];

function session(req) {
  const match = /(?:^|;\s*)kge_session_meridian=([^;]+)/.exec(req.headers.cookie || "");
  return match ? people[match[1]] : undefined;
}

function body(req) {
  return new Promise((resolve) => {
    let text = "";
    req.on("data", (chunk) => { text += chunk; });
    req.on("end", () => resolve(text ? JSON.parse(text) : {}));
  });
}

const kgeServer = createServer(async (req, res) => {
  seen.push({ method: req.method, url: req.url, cookie: req.headers.cookie || "" });
  const json = (status, value, headers = {}) => { res.writeHead(status, { "content-type": "application/json", ...headers }); res.end(JSON.stringify(value)); };
  if (req.method === "POST" && req.url === "/session") {
    const { username, password } = await body(req);
    if (passwords[username] !== password) return json(401, { detail: "wrong username or password" });
    return json(200, { principal: username }, { "set-cookie": `${COOKIE}=${password}; HttpOnly; Path=/; SameSite=lax` });
  }
  if (req.method === "DELETE" && req.url === "/session") return json(200, { signed_out: true }, { "set-cookie": `${COOKIE}=; Max-Age=0; Path=/` });
  if (req.method === "GET" && req.url === "/catalogue/release") return json(200, { release: catalogue.release, has_catalogue: true });
  if (req.method === "GET" && req.url === "/catalogue/portal") return json(200, catalogue);
  const who = session(req);
  if (!who) return json(401, { detail: "sign in" });
  if (req.method === "GET" && req.url === "/profile") return json(200, { title: "Aster Bank governance graph" });
  if (req.method === "GET" && req.url === "/whoami") return json(200, who.units.length ? { id: who.id, role: who.id, reads: who.units } : { id: who.id });
  if (req.method === "GET" && req.url === "/readable-units") return json(200, { units: who.units });
  if (req.method === "POST" && req.url === "/ask") {
    const { question } = await body(req);
    return json(200, { answer_text: `KGE answers ${who.id}: ${question}`, figures: [{ value: 1 }], citations: [{ doc: "d1" }], refusals: [] });
  }
  if (req.method === "POST" && req.url.startsWith("/data/")) {
    const value = await body(req);
    if (!who.units.includes(value.unit)) return json(404, { detail: "product not found" });
    if (req.url === "/data/query" && /delete|update|insert/i.test(value.sql)) return json(400, { detail: "read-only: SELECT only" });
    if (req.url === "/data/export") {
      res.writeHead(200, { "content-type": "text/csv", "content-disposition": "attachment; filename=retail-accounts.csv", "x-export-capped-at": "10000", "x-export-note": "Export limited to 10000 rows; it may be incomplete." });
      return res.end("id,amount\n1,20\n");
    }
    return json(200, { columns: ["id", "amount"], rows: [{ id: 1, amount: 20 }], row_count: 1, capped_at: 20, run_trace: {} });
  }
  json(404, { detail: "not found" });
});

const repository = openSqliteRepository({ PORTAL_SQLITE_PATH: ":memory:" });
let portal;
let base;

before(async () => {
  await new Promise((resolve) => kgeServer.listen(0, "127.0.0.1", resolve));
  const kgeUrl = `http://127.0.0.1:${kgeServer.address().port}`;
  const unlimited = { attempt: () => 0, failed: () => 0, passed: () => {} };
  const { app } = createApp({ repository, env: { KGE_URL: kgeUrl, PORTAL_SYNC_TOKEN: "s".repeat(32) }, throttle: unlimited, uiPath: "not-present" });
  portal = app.listen(0, "127.0.0.1");
  await new Promise((resolve) => portal.once("listening", resolve));
  base = `http://localhost:${portal.address().port}`;
  while (repository.catalogueRelease() !== catalogue.release) await new Promise((resolve) => setTimeout(resolve, 1));
});
after(() => { portal.close(); kgeServer.close(); repository.close(); });

async function signIn(username) {
  const response = await fetch(`${base}/session`, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ username, password: passwords[username] }) });
  assert.equal(response.status, 200);
  const cookie = response.headers.getSetCookie()[0];
  assert.match(cookie, /^kge_session_meridian=/);
  assert.doesNotMatch(cookie, /domain=/i);
  return cookie.split(";")[0];
}

function as(cookie) {
  return (path, options = {}) => fetch(`${base}${path}`, { ...options, headers: { "content-type": "application/json", ...(cookie ? { cookie } : {}) } });
}

test("health and the page need no sign-in", async () => {
  assert.equal((await fetch(`${base}/healthz`)).status, 200);
});

test("KGE mode ignores a forged portal principal before and after sign-in", async () => {
  for (const path of ["/whoami", "/api/products"]) assert.equal((await fetch(`${base}${path}`, { headers: { "X-Portal-Principal": "admin" } })).status, 401);
  const cookie = await signIn("analyst_retail");
  const headers = { cookie, "X-Portal-Principal": "admin" };
  assert.equal((await (await fetch(`${base}/whoami`, { headers })).json()).id, "analyst_retail");
  assert.deepEqual(await (await fetch(`${base}/api/role`, { headers })).json(), { role: "viewer" });
  assert.equal((await fetch(`${base}/api/products/wealth-holdings`, { headers })).status, 404);
});

test("a wrong password is KGE's refusal, and nothing is served signed out", async () => {
  const wrong = await fetch(`${base}/session`, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ username: "admin", password: "nope" }) });
  assert.equal(wrong.status, 401);
  assert.equal(wrong.headers.getSetCookie().length, 0);
  for (const path of ["/api/products", "/getFavourites", "/getMostViewedProducts", "/product/retail-accounts", "/avg-feedback/retail-accounts"]) assert.equal((await as()(path)).status, 401, path);
});

test("sign-in, browse, ask and sign-out go through the portal to KGE on another host", async () => {
  const cookie = await signIn("analyst_retail");
  const get = as(cookie);
  assert.equal((await (await get("/whoami")).json()).id, "analyst_retail");
  assert.deepEqual((await (await get("/api/products")).json()).map((item) => item.unit_id), ["retail"]);
  const answer = await (await get("/ask", { method: "POST", body: JSON.stringify({ question: "How many accounts?" }) })).json();
  assert.equal(answer.answer_text, "KGE answers analyst_retail: How many accounts?");
  assert.deepEqual(answer.citations, [{ doc: "d1" }]);
  const legacyAnswer = await (await get("/ask-data-product", { method: "POST", body: JSON.stringify({ question: "Balances?" }) })).json();
  assert.equal(legacyAnswer.answer_text, "KGE answers analyst_retail: Balances?");
  const out = await get("/session", { method: "DELETE" });
  assert.equal(out.status, 200);
  assert.match(out.headers.getSetCookie()[0], /Max-Age=0/);
  assert.ok(seen.some((call) => call.url === "/readable-units" && call.cookie === cookie));
});

test("a one-unit person sees nothing of another unit's, in every list", async () => {
  const wealth = as(await signIn("advisor_wealth"));
  assert.equal((await wealth("/api/favourites/wealth-holdings", { method: "POST" })).status, 204);
  assert.equal((await wealth("/api/cart/wealth-holdings", { method: "POST" })).status, 204);
  assert.equal((await wealth("/api/requests", { method: "POST", body: JSON.stringify({ productId: "wealth-holdings" }) })).status, 201);
  assert.equal((await wealth("/api/feedback", { method: "POST", body: JSON.stringify({ productId: "wealth-holdings", rating: 4 }) })).status, 201);
  const retail = as(await signIn("analyst_retail"));
  for (const path of ["/api/products", "/getMostViewedProducts", "/getRelatedProducts", "/getCommunityProducts"]) {
    const response = await retail(path, { method: path === "/getCommunityProducts" ? "POST" : "GET", body: path === "/getCommunityProducts" ? "{}" : undefined });
    const text = await response.text();
    assert.doesNotMatch(text, /wealth|sme-|corporate/i, path);
  }
  const search = await (await retail("/search", { method: "POST", body: JSON.stringify({ searchParam: "" }) })).text();
  assert.doesNotMatch(search, /wealth|sme-|corporate/i);
  for (const path of ["/product/wealth-holdings", "/api/products/wealth-holdings", "/avg-feedback/wealth-holdings", "/api/feedback/wealth-holdings"]) assert.equal((await retail(path)).status, 404, path);
  for (const path of ["/fields/wealth-holdings", "/lineage/wealth-holdings", "/api/products/wealth-holdings/fields", "/api/products/wealth-holdings/lineage"]) assert.equal((await retail(path)).status, 404);
  assert.doesNotMatch(await (await retail("/getDataCommunities")).text(), /wealth|sme-|corporate/i);
  for (const [path, productId] of [["/saveFavouriteProduct", "wealth-holdings"], ["/add-to-cart", "wealth-holdings"], ["/submit-request", "wealth-holdings"], ["/subscribe", "wealth-holdings"], ["/submit-feedback", "wealth-holdings"]]) {
    assert.equal((await retail(path, { method: "POST", body: JSON.stringify({ productId, rating: 1 }) })).status, 404, path);
  }
  assert.equal((await retail("/api/favourites/wealth-holdings", { method: "POST" })).status, 404);
  for (const path of ["/api/favourites", "/api/cart", "/api/requests", "/getFavourites"]) assert.deepEqual(await (await retail(path)).json(), [], path);
});

test("a person KGE gives no units sees no product, even with a portal role", async () => {
  repository.setRole("stranger", "admin");
  const stranger = as(await signIn("stranger"));
  assert.deepEqual(await (await stranger("/api/products")).json(), []);
  assert.deepEqual(await (await stranger("/getPublishedStatus")).json(), []);
  assert.equal((await stranger("/updatePublishedStatus", { method: "POST", body: JSON.stringify({ technicalName: "retail-accounts", publishedStatus: "N" }) })).status, 404);
  assert.equal((await stranger("/getUserData")).status, 200);
});

test("a portal role gates publishing and changes no data a person sees", async () => {
  const retail = as(await signIn("analyst_retail"));
  assert.equal((await retail("/updatePublishedStatus", { method: "POST", body: JSON.stringify({ technicalName: "retail-accounts", publishedStatus: "N" }) })).status, 403);
  assert.equal((await retail("/getPublishedStatus")).status, 403);
  assert.equal((await retail("/getUserData")).status, 403);
  const admin = as(await signIn("admin"));
  const before = (await (await admin("/api/products")).json()).map((item) => item.id);
  assert.deepEqual(before, ["corporate-exposure", "retail-accounts", "sme-lending", "wealth-holdings"]);
  const updated = await admin("/updatePublishedStatus", { method: "POST", body: JSON.stringify({ technicalName: "sme-lending", publishedStatus: "N" }) });
  assert.equal(updated.status, 200);
  assert.ok((await updated.json()).length > 0);
  const listed = await (await admin("/getPublishedStatus")).json();
  assert.equal(listed.find((item) => item.TECHNICAL_NAME === "sme-lending").PUBLISHED_STATUS, false);
  assert.equal(listed.find((item) => item.TECHNICAL_NAME === "sme-lending").COMMUNITY_NAME, "sme community");
  assert.deepEqual([...new Set(listed.map((item) => item.COMMUNITY_NAME))].sort(), ["corporate community", "retail community", "sme community", "wealth community"]);
  assert.equal((await (await admin("/api/products")).json()).some((item) => item.id === "sme-lending"), false);
  assert.equal((await (await admin("/updatePublishedStatus", { method: "POST", body: JSON.stringify({ technicalName: "sme-lending", publishedStatus: "Y" }) })).status), 200);
  assert.equal((await (await admin("/api/products")).json()).length, 4);
  assert.deepEqual((await (await retail("/api/products")).json()).map((item) => item.unit_id), ["retail"]);
});

test("preview, export and query are forwarded as the person after the portal unit filter", async () => {
  const retail = as(await signIn("analyst_retail"));
  assert.deepEqual(await (await retail("/api/preview/retail-accounts")).json(), { columns: ["id", "amount"], rows: [{ id: 1, amount: 20 }], row_count: 1, capped_at: 20, run_trace: {} });
  const exported = await retail("/api/export/retail-accounts");
  assert.match(await exported.text(), /^id,amount/);
  assert.equal(exported.headers.get("x-export-capped-at"), "10000");
  assert.match(exported.headers.get("x-export-note"), /incomplete/);
  assert.equal((await retail("/api/query", { method: "POST", body: JSON.stringify({ unit: "wealth", sql: "SELECT id FROM holdings" }) })).status, 404);
  const refused = await retail("/execute-query", { method: "POST", body: JSON.stringify({ unit: "retail", sql: "DELETE FROM accounts" }) });
  assert.equal(refused.status, 400);
  assert.equal((await refused.json()).detail, "read-only: SELECT only");
  assert.deepEqual(await (await retail("/getConfig")).json(), { Application_Title: "Data Discovery Portal", preview: true, export: true, executeQuery: true, ask: true });
});

test("an approver sees and decides only the open requests of units KGE lets them read", async () => {
  repository.setRole("advisor_wealth", "approver");
  const retail = as(await signIn("analyst_retail"));
  assert.equal((await retail("/api/requests", { method: "POST", body: JSON.stringify({ productId: "retail-accounts" }) })).status, 201);
  const wealth = as(await signIn("advisor_wealth"));
  const queue = await (await wealth("/api/admin/requests")).json();
  assert.ok(queue.length > 0 && queue.every((item) => item.unit_id === "wealth"), JSON.stringify(queue));
  const retailRequest = (await (await retail("/api/requests")).json())[0];
  assert.equal((await wealth(`/api/admin/requests/${retailRequest.id}/decision`, { method: "POST", body: JSON.stringify({ decision: "approve" }) })).status, 404);
  assert.equal((await retail(`/api/admin/requests/${retailRequest.id}/decision`, { method: "POST", body: JSON.stringify({ decision: "approve" }) })).status, 403);
  assert.equal((await retail("/api/admin/requests")).status, 403);
  const admin = as(await signIn("admin"));
  const open = await (await admin("/api/admin/requests")).json();
  assert.ok(open.some((item) => item.id === retailRequest.id));
  assert.equal((await admin(`/api/admin/requests/${retailRequest.id}/decision`, { method: "POST", body: JSON.stringify({ decision: "maybe" }) })).status, 400);
  const decided = await admin(`/api/admin/requests/${retailRequest.id}/decision`, { method: "POST", body: JSON.stringify({ decision: "approve" }) });
  assert.deepEqual(await decided.json(), { id: String(retailRequest.id), status: "APPROVED" });
  assert.equal((await admin(`/api/admin/requests/${retailRequest.id}/decision`, { method: "POST", body: JSON.stringify({ decision: "reject" }) })).status, 404);
  const mine = (await (await retail("/api/requests")).json()).find((item) => item.id === retailRequest.id);
  assert.equal(mine.status, "APPROVED");
  assert.equal(mine.decided_by, "admin");
  const wealthRequest = queue[0];
  assert.equal((await (await wealth(`/api/admin/requests/${wealthRequest.id}/decision`, { method: "POST", body: JSON.stringify({ decision: "reject" }) })).json()).status, "REJECTED");
  assert.deepEqual((await (await retail("/api/products")).json()).map((item) => item.unit_id), ["retail"]);
  assert.equal((await (await wealth("/readable-units")).json()).units.join(), "wealth");
  repository.setRole("advisor_wealth", "viewer");
});
