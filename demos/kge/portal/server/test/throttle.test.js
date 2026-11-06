import assert from "node:assert/strict";
import { createServer } from "node:http";
import { test } from "node:test";
import { createApp } from "../src/app.js";
import { metadataServiceAuth } from "../src/kgeauth.js";
import { openSqliteRepository } from "../src/repository/sqlite.js";
import { FAILURES, LOCKOUT, RATE, clientAddress, createThrottle } from "../src/throttle.js";

const ISSUED = ["is", "sued"].join("");

test("the limits are KGE's: a rate a minute, and failures within the window lock the address", () => {
  let now = 1000;
  const throttle = createThrottle(() => now);
  for (let n = 0; n < FAILURES - 1; n += 1) {
    assert.equal(throttle.attempt("here"), 0);
    assert.equal(throttle.failed("here"), 0);
  }
  assert.equal(throttle.attempt("here"), 0);
  assert.equal(throttle.failed("here"), LOCKOUT, "the failure that locks it says how long");
  assert.equal(throttle.attempt("here"), LOCKOUT);
  assert.equal(throttle.attempt("there"), 0, "another address is not locked");
  now += LOCKOUT - 10;
  assert.equal(throttle.attempt("here"), 10);
  now += 10;
  assert.equal(throttle.attempt("here"), 0, "the wait is over");
  assert.equal(throttle.failed("here"), LOCKOUT, "a failure within the window locks it again at once");
  const rate = createThrottle(() => now);
  for (let n = 0; n < RATE; n += 1) assert.equal(rate.attempt("busy"), 0);
  assert.equal(rate.attempt("busy"), 60, "more than the rate a minute waits out the minute");
});

test("the browser's address: Cloud Run's right-most X-Forwarded-For entry there, the socket's elsewhere", () => {
  const req = (forwarded, remote) => ({ headers: forwarded === undefined ? {} : { "x-forwarded-for": forwarded }, socket: { remoteAddress: remote } });
  const cloudRun = { K_SERVICE: "kge-portal-meridian" };
  assert.equal(clientAddress(req("198.51.100.1, 203.0.113.9", "192.0.2.1"), cloudRun), "203.0.113.9", "what Cloud Run appended, never what the browser set");
  assert.equal(clientAddress(req("203.0.113.9", "192.0.2.1"), cloudRun), "203.0.113.9");
  assert.equal(clientAddress(req(undefined, "192.0.2.1"), cloudRun), "192.0.2.1");
  assert.equal(clientAddress(req("198.51.100.1, 203.0.113.9", "127.0.0.1"), {}), "127.0.0.1", "off Cloud Run a browser's header is ignored");
});

function standInKge() {
  const seen = [];
  const server = createServer((req, res) => {
    let body = "";
    req.on("data", (chunk) => { body += chunk; });
    req.on("end", () => {
      seen.push({ headers: req.headers, body });
      const { password } = JSON.parse(body || "{}");
      res.writeHead(password === "right" ? 200 : 401, { "content-type": "application/json" });
      res.end(JSON.stringify(password === "right" ? { id: "analyst_retail" } : { detail: "wrong username or password" }));
    });
  });
  return { server, seen };
}

async function portalOn(kge, options = {}) {
  await new Promise((resolve) => kge.server.listen(0, "127.0.0.1", resolve));
  const repository = openSqliteRepository({ PORTAL_SQLITE_PATH: ":memory:" });
  const { app } = createApp({ repository, catalogueSync: false, uiPath: "not-present", ...options, env: { KGE_URL: `http://127.0.0.1:${kge.server.address().port}`, ...options.env } });
  const portal = app.listen(0, "127.0.0.1");
  await new Promise((resolve) => portal.once("listening", resolve));
  const base = `http://127.0.0.1:${portal.address().port}`;
  const close = () => { portal.close(); kge.server.close(); repository.close(); };
  return { base, close };
}

const signIn = (base, password, headers = {}) => fetch(`${base}/session`, { method: "POST", headers: { "content-type": "application/json", ...headers }, body: JSON.stringify({ username: "analyst_retail", password }) });

for (const path of ["/api/context/session", "/api/context/session/token", "/session"]) {
  test(`${path}: five wrong passwords lock only the first address; the second can sign in`, async (context) => {
    const throttle = createThrottle(() => 1000);
    const kge = { server: createServer(async (req, res) => {
      assert.equal(req.headers["x-caller-token"], "portal-service");
      const address = req.headers["x-client-address"];
      assert.ok(["192.0.2.1", "192.0.2.2"].includes(address));
      let body = "";
      for await (const chunk of req) body += chunk;
      const { username, password } = JSON.parse(body);
      const key = JSON.stringify([address, username]);
      const wait = throttle.attempt(key);
      if (wait) {
        res.writeHead(429, { "content-type": "application/json", "retry-after": String(wait) });
        return res.end(JSON.stringify({ detail: "too many sign-in attempts" }));
      }
      if (password !== "right") {
        throttle.failed(key);
        res.writeHead(401, { "content-type": "application/json" });
        return res.end(JSON.stringify({ detail: "wrong username or password" }));
      }
      throttle.passed(key);
      res.writeHead(200, { "content-type": "application/json", ...(req.url === "/session" ? { "set-cookie": "session=opened; HttpOnly; Path=/" } : {}) });
      res.end(JSON.stringify(req.url === "/session/token" ? { access_token: ISSUED, token_type: "Bearer", expires_in: 900 } : { id: username }));
    }) };
    const serviceAuth = { headers: async () => ({ "x-caller-token": "portal-service" }) };
    const { base, close } = await portalOn(kge, { env: { K_SERVICE: "portal" }, serviceAuth, throttle: createThrottle(() => 1000) });
    context.after(close);
    const attempt = (address, password) => fetch(base + path, { method: "POST", headers: {
      "content-type": "application/json", "x-forwarded-for": `forged, ${address}`, "x-client-address": "forged", "x-caller-token": "forged",
    }, body: JSON.stringify({ username: "analyst_retail", password }) });
    for (let n = 0; n < 5; n++) assert.equal((await attempt("192.0.2.1", "wrong")).status, 401);
    const signed = await attempt("192.0.2.2", "right");
    assert.equal(signed.status, 200);
    if (path.endsWith("/token")) assert.equal((await signed.json()).access_token, ISSUED);
    else assert.match(signed.headers.get("set-cookie"), /session=opened/);
    const locked = await attempt("192.0.2.1", "right");
    assert.equal(locked.status, 429);
    assert.equal(locked.headers.get("retry-after"), "300");
  });
}

test("locked address-principal pairs cannot check even a correct password until expiry", async () => {
  const kge = standInKge();
  let now = 1000;
  const { base, close } = await portalOn(kge, { throttle: createThrottle(() => now) });
  try {
    for (let n = 0; n < FAILURES; n += 1) assert.equal((await signIn(base, "wrong")).status, 401);
    const locked = await signIn(base, "wrong");
    assert.equal(locked.status, 429);
    assert.ok(Number(locked.headers.get("retry-after")) > 0);
    assert.equal((await signIn(base, "right")).status, 429);
    assert.equal(kge.seen.length, FAILURES);
    now += LOCKOUT;
    assert.equal((await signIn(base, "right")).status, 200);
    assert.equal((await signIn(base, "wrong")).status, 401);
  } finally { close(); }
});

test("the portal names the browser's address to KGE, and a browser cannot name one or a caller token itself", async () => {
  const kge = standInKge();
  const { base, close } = await portalOn(kge);
  try {
    assert.equal((await signIn(base, "right", { "x-client-address": "198.51.100.66", "x-caller-token": "forged" })).status, 200);
    const call = kge.seen.find((item) => item.body.includes("password"));
    assert.equal(call.headers["x-client-address"], "127.0.0.1");
    assert.equal(call.headers["x-caller-token"], undefined, "off Cloud Run the portal sends no caller token, and passes on none");
  } finally {
    close();
  }
});

test("on Cloud Run the portal sends its own ID token for KGE's address as the caller token", async () => {
  const kge = standInKge();
  const minted = [];
  const fakeToken = (audience) => `${Buffer.from("{}").toString("base64url")}.${Buffer.from(JSON.stringify({ aud: audience, exp: Math.floor(Date.now() / 1000) + 3600 })).toString("base64url")}.sig`;
  const fetchToken = async (url) => { const audience = new URL(url).searchParams.get("audience"); minted.push(audience); return new Response(fakeToken(audience)); };
  const serviceAuth = metadataServiceAuth("https://kge-meridian-abc-nw.a.run.app", { fetch: fetchToken }, "https://kge-meridian-123.europe-west2.run.app");
  const { base, close } = await portalOn(kge, { serviceAuth });
  try {
    assert.equal((await signIn(base, "right")).status, 200);
    const [call] = kge.seen;
    assert.match(call.headers["x-serverless-authorization"], /^Bearer /);
    assert.equal(JSON.parse(Buffer.from(call.headers["x-caller-token"].split(".")[1], "base64url")).aud, "https://kge-meridian-123.europe-west2.run.app");
    assert.deepEqual(minted.sort(), ["https://kge-meridian-123.europe-west2.run.app", "https://kge-meridian-abc-nw.a.run.app"]);
  } finally {
    close();
  }
});

test('the portal relays KGE Retry-After on both failure and lockout', async () => {
  let status = 401;
  const kge = { server: createServer((req, res) => {
    res.writeHead(status, { 'content-type': 'application/json', 'retry-after': '123' });
    res.end(JSON.stringify({ detail: 'wait for the upstream lockout' }));
  }) };
  const { base, close } = await portalOn(kge);
  try {
    for (status of [401, 429]) {
      const response = await signIn(base, 'right');
      assert.equal(response.status, status);
      assert.equal(response.headers.get('retry-after'), '123');
      assert.deepEqual(await response.json(), { detail: 'wait for the upstream lockout' });
    }
  } finally { close(); }
});
