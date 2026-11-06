import assert from "node:assert/strict";
import { createServer } from "node:http";
import { after, before, test } from "node:test";
import { createApp } from "../src/app.js";
import { metadataServiceAuth, noServiceAuth, openServiceAuth } from "../src/kgeauth.js";
import { openSqliteRepository } from "../src/repository/sqlite.js";
import { catalogue } from "./fixtures/catalogue.js";

function token(exp) {
  const part = (value) => Buffer.from(JSON.stringify(value)).toString("base64url");
  return `${part({ alg: "RS256" })}.${part({ aud: "https://kge.stand-in", exp })}.signature`;
}

const minted = [];
const metadata = createServer((req, res) => {
  minted.push({ url: req.url, flavor: req.headers["metadata-flavor"] });
  res.writeHead(200, { "content-type": "text/plain" });
  res.end(token(Math.floor(Date.now() / 1000) + 3600));
});
const seen = [];
const kge = createServer((req, res) => {
  seen.push({ url: req.url, service: req.headers["x-serverless-authorization"] || "", sync: req.headers["x-portal-sync-token"] || "", cookie: req.headers.cookie || "", authorization: req.headers.authorization || "" });
  if (!/^Bearer [^.]+\.[^.]+\.signature$/.test(req.headers["x-serverless-authorization"] || "")) { res.writeHead(403); return res.end(); }
  res.writeHead(200, { "content-type": "application/json" });
  res.end(JSON.stringify(req.url === "/catalogue/release" ? { release: catalogue.release, has_catalogue: true } : req.url === "/catalogue/portal" ? catalogue : req.url === "/readable-units" ? { units: ["retail"] } : { id: "analyst_retail" }));
});
const repository = openSqliteRepository({ PORTAL_SQLITE_PATH: ":memory:" });
let portal;
let base;
let metadataUrl;

before(async () => {
  await new Promise((resolve) => metadata.listen(0, "127.0.0.1", resolve));
  await new Promise((resolve) => kge.listen(0, "127.0.0.1", resolve));
  metadataUrl = `http://127.0.0.1:${metadata.address().port}/identity`;
  const kgeUrl = `http://127.0.0.1:${kge.address().port}`;
  const serviceAuth = metadataServiceAuth("https://kge.stand-in", { metadataUrl });
  ({ app: portal } = createApp({ repository, env: { KGE_URL: kgeUrl, PORTAL_SYNC_TOKEN: "s".repeat(32) }, serviceAuth, uiPath: "not-present" }));
  portal = portal.listen(0, "127.0.0.1");
  await new Promise((resolve) => portal.once("listening", resolve));
  base = `http://127.0.0.1:${portal.address().port}`;
  while (repository.catalogueRelease() !== catalogue.release) await new Promise((resolve) => setTimeout(resolve, 1));
});
after(() => { portal.close(); kge.close(); metadata.close(); repository.close(); });

test("off Cloud Run, or with no audience, the portal sends no service token", () => {
  assert.equal(openServiceAuth({ KGE_URL: "https://kge.stand-in" }), noServiceAuth);
  assert.equal(openServiceAuth({ K_SERVICE: "kge-portal-meridian" }), noServiceAuth);
  assert.notEqual(openServiceAuth({ K_SERVICE: "kge-portal-meridian", KGE_URL: "https://kge.stand-in/" }), noServiceAuth);
  assert.notEqual(openServiceAuth({ K_SERVICE: "kge-portal-meridian", KGE_AUDIENCE: "https://kge.stand-in" }), noServiceAuth);
});

test("off Cloud Run the portal refuses to start without a strong catalogue sync token", () => {
  assert.throws(() => createApp({ env: { KGE_URL: "http://127.0.0.1:1", PORTAL_SYNC_TOKEN: "short" } }), /PORTAL_SYNC_TOKEN must be at least 32 characters/);
  assert.throws(() => createApp({ env: { KGE_URL: "http://127.0.0.1:1" } }), /PORTAL_SYNC_TOKEN must be at least 32 characters/);
});

test("on Cloud Run the portal reaches a private KGE with its own ID token, beside the person's cookie", async () => {
  const response = await fetch(`${base}/api/products`, { headers: { cookie: "kge_session_meridian=person", authorization: "Bearer person-token" } });
  assert.equal(response.status, 200);
  assert.deepEqual((await response.json()).map((item) => item.id), ["retail-accounts"]);
  assert.ok(seen.length >= 2);
  assert.ok(seen.some((call) => call.url === "/catalogue/portal" && call.sync === "s".repeat(32)));
  for (const call of seen.filter((item) => !item.url.startsWith("/catalogue/"))) {
    assert.match(call.service, /^Bearer /);
    assert.equal(call.cookie, "kge_session_meridian=person");
    assert.equal(call.authorization, "Bearer person-token");
  }
  assert.equal(minted.length, 1, "the token is cached until shortly before it expires");
  assert.equal(minted[0].flavor, "Google");
  assert.match(minted[0].url, /audience=https%3A%2F%2Fkge.stand-in/);
});

test("a token near its expiry is minted again", async () => {
  let clock = 0;
  const calls = [];
  const auth = metadataServiceAuth("https://kge.stand-in", {
    now: () => clock,
    fetch: async (url) => { calls.push(url); return new Response(token(1000)); },
  });
  await auth.headers();
  clock = 1000 * 1000 - 6 * 60 * 1000;
  await auth.headers();
  assert.equal(calls.length, 1);
  clock = 1000 * 1000 - 4 * 60 * 1000;
  await auth.headers();
  assert.equal(calls.length, 2);
});
