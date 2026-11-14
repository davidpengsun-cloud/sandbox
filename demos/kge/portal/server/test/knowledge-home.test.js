import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { mkdtemp, readFile, readdir, writeFile } from "node:fs/promises";
import http, { createServer, request as httpRequest } from "node:http";
import https from "node:https";
import { PassThrough, Readable } from "node:stream";
import { tmpdir } from "node:os";
import { join, relative } from "node:path";
import { fileURLToPath } from "node:url";
import { after, before, test } from "node:test";
import { createApp } from "../src/app.js";
import { PRODUCT_NAME, appSettings } from "../src/apps.js";
import { kgeClient } from "../src/kge.js";
import { contextProxy } from "../src/context.js";
import { openSqliteRepository } from "../src/repository/sqlite.js";
import { localCredentials } from "./fixtures/identity.js";

const SERVER = fileURLToPath(new URL("..", import.meta.url));
const FLAGS = ["PORTAL_KNOWLEDGE_HOME", "PORTAL_APP_CERTIFY", "PORTAL_APP_CONTEXT", "PORTAL_APP_DISCOVER"];
const APPS = { certify: "PORTAL_APP_CERTIFY", context: "PORTAL_APP_CONTEXT", discover: "PORTAL_APP_DISCOVER" };

const seen = [];
const kge = createServer((req, res) => {
  const chunks = [];
  req.on("data", (chunk) => chunks.push(chunk));
  req.on("end", () => {
    const body = Buffer.concat(chunks);
    seen.push({ method: req.method, url: req.url, headers: req.headers, body });
    if (req.url === "/whoami") return res.writeHead(200, { "content-type": "application/json" }).end(JSON.stringify({ id: "analyst_retail" }));
    if (req.url === "/readable-units") return res.writeHead(200, { "content-type": "application/json" }).end(JSON.stringify({ units: ["retail"] }));
    if (req.url.startsWith("/busy")) return res.writeHead(429, { "content-type": "application/json", "retry-after": "9" }).end(JSON.stringify({ detail: "busy" }));
    if (req.url.startsWith("/refused")) return res.writeHead(403, { "content-type": "application/json" }).end(JSON.stringify({ detail: "not yours" }));
    if (req.url.startsWith("/export")) return res.writeHead(200, { "content-type": "text/csv; charset=utf-8", "content-disposition": "attachment; filename=rows.csv", "set-cookie": "kge_session=fresh; Path=/" }).end("a,b\n1,2\n");
    res.writeHead(201, { "content-type": "application/json", "x-not-relayed": "yes" });
    res.end(JSON.stringify({ method: req.method, url: req.url, bytes: body.length }));
  });
});
const service = { headers: async () => ({ "x-serverless-authorization": "Bearer service-token", "x-caller-token": "portal-caller-token" }) };
let kgeUrl;
let directory;
const servers = [];

before(async () => {
  await new Promise((resolve) => kge.listen(0, "127.0.0.1", resolve));
  kgeUrl = `http://127.0.0.1:${kge.address().port}`;
  directory = await mkdtemp(join(tmpdir(), "knowledge-home-"));
});
after(() => { kge.close(); for (const server of servers) server.close(); });

async function start(flags = {}, options = {}) {
  const repository = openSqliteRepository({ PORTAL_SQLITE_PATH: ":memory:" });
  const env = options.env || { KGE_URL: kgeUrl, PORTAL_SYNC_TOKEN: "s".repeat(32), ...flags };
  const { app } = createApp({ repository, env, serviceAuth: service, catalogueSync: false, uiPath: "not-present", ...options.app });
  const server = app.listen(0, "127.0.0.1");
  servers.push(server);
  await new Promise((resolve) => server.once("listening", resolve));
  return `http://127.0.0.1:${server.address().port}`;
}

const person = { cookie: "kge_session_meridian=person", authorization: "Bearer person-token" };

test("each flag is on by default and Certify's link is unset", () => {
  assert.deepEqual(appSettings({}), { knowledgeHome: true, apps: { certify: true, context: true, discover: true }, tableQuery: false, tablePreview: false, dqFilters: true, logoTitle: false, certifyUrl: null, productName: "Client ALTA", productShortName: "ALTA", productLongName: "ALTA: AI-powered Library for Trusted Assets", productSubtitle: "Search curated data assets, see how fresh and trustworthy each one is, and request access from the people who own it.", contextAskTitle: "Ask a governed question", contextAskSubtitle: "Certified definitions and figures, answered only from data you may read." });
  assert.equal(appSettings({ PORTAL_CERTIFY_URL: "" }).certifyUrl, null);
});

test("GET /api/config publishes the client's display name as the product name", async () => {
  const base = await start({ PORTAL_CLIENT_NAME: "  Meridian Group  ", PORTAL_LOGO_TITLE: "on" });
  const config = await (await fetch(`${base}/api/config`)).json();
  assert.equal(config.productName, "Meridian Group ALTA");
  assert.equal(config.logoTitle, true);
  assert.doesNotMatch(JSON.stringify(config), /\[Client name\]/);
});

test("GET /api/config publishes PORTAL_PRODUCT_NAME as the short name and in the product name", async () => {
  const base = await start({ PORTAL_CLIENT_NAME: "Meridian", PORTAL_PRODUCT_NAME: "  Beacon  " });
  const config = await (await fetch(`${base}/api/config`)).json();
  assert.equal(config.productName, "Meridian Beacon");
  assert.equal(config.productShortName, "Beacon");
});

test("a flag other than on or off refuses start-up, naming the setting", () => {
  for (const name of FLAGS) {
    for (const value of ["ON", "true", "1", "yes", " on", ""]) {
      assert.throws(() => appSettings({ [name]: value }), new RegExp(`^Error: ${name} must be on or off$`));
      assert.throws(() => createApp({ env: { [name]: value }, catalogueSync: false, repository: {} }), new RegExp(`${name} must be on or off`));
    }
  }
});

test("PORTAL_CERTIFY_URL takes an http or https address, with a query, and refuses anything else naming the setting", () => {
  const link = "https://apps.powerapps.com/play/e/default/a/1234?tenantId=abcd&hidenavbar=true";
  assert.equal(appSettings({ PORTAL_CERTIFY_URL: link }).certifyUrl, link);
  assert.equal(appSettings({ PORTAL_CERTIFY_URL: "http://certify.example.com" }).certifyUrl, "http://certify.example.com/");
  for (const value of ["not a url", "javascript:alert(1)", "ftp://certify.example.com", "file:///etc/passwd", "https://"]) {
    assert.throws(() => appSettings({ PORTAL_CERTIFY_URL: value }), /^Error: PORTAL_CERTIFY_URL must be an http or https address/);
  }
});

test("with the ALTA home off exactly one app may be on: each alone starts", () => {
  for (const app of Object.keys(APPS)) {
    const flags = { PORTAL_KNOWLEDGE_HOME: "off", ...Object.fromEntries(Object.entries(APPS).map(([other, name]) => [name, other === app ? "on" : "off"])) };
    const settings = appSettings(flags);
    assert.equal(settings.knowledgeHome, false);
    assert.deepEqual(Object.entries(settings.apps).filter(([, on]) => on).map(([name]) => name), [app]);
  }
});

test("with the ALTA home off and more than one app on, or none, the portal refuses to start and names the flags to change", () => {
  const two = { PORTAL_KNOWLEDGE_HOME: "off", PORTAL_APP_DISCOVER: "off" };
  assert.throws(() => appSettings(two), (error) => /PORTAL_KNOWLEDGE_HOME=off/.test(error.message) && /PORTAL_APP_CERTIFY=on, PORTAL_APP_CONTEXT=on/.test(error.message) && /set all but one of them to off, or set PORTAL_KNOWLEDGE_HOME=on/.test(error.message) && !/PORTAL_APP_DISCOVER/.test(error.message));
  assert.throws(() => appSettings({ PORTAL_KNOWLEDGE_HOME: "off" }), /3 are \(PORTAL_APP_CERTIFY=on, PORTAL_APP_CONTEXT=on, PORTAL_APP_DISCOVER=on\)/);
  assert.throws(() => appSettings({ PORTAL_KNOWLEDGE_HOME: "off", PORTAL_APP_CERTIFY: "off", PORTAL_APP_CONTEXT: "off", PORTAL_APP_DISCOVER: "off" }), /none is: set one of PORTAL_APP_CERTIFY, PORTAL_APP_CONTEXT, PORTAL_APP_DISCOVER to on, or set PORTAL_KNOWLEDGE_HOME=on/);
  assert.throws(() => createApp({ env: two, catalogueSync: false, repository: {} }), /exactly one app must be on/);
  assert.doesNotThrow(() => appSettings({ PORTAL_APP_CERTIFY: "off", PORTAL_APP_CONTEXT: "off", PORTAL_APP_DISCOVER: "off" }));
});

test("the portal process prints the refusal and exits non-zero, without a stack trace", () => {
  const run = spawnSync(process.execPath, ["--experimental-sqlite", "src/index.js"], {
    cwd: SERVER, env: { ...process.env, PORT: "0", PORTAL_SQLITE_PATH: ":memory:", PORTAL_KNOWLEDGE_HOME: "off", PORTAL_APP_CERTIFY: "on", PORTAL_APP_CONTEXT: "on", PORTAL_APP_DISCOVER: "off" }, encoding: "utf8", timeout: 20000,
  });
  assert.equal(run.status, 1, run.stdout + run.stderr);
  assert.match(run.stderr, /^Portal cannot start: With PORTAL_KNOWLEDGE_HOME=off exactly one app must be on, and 2 are \(PORTAL_APP_CERTIFY=on, PORTAL_APP_CONTEXT=on\)/);
  assert.doesNotMatch(run.stderr, /at createApp|\.js:\d+/);
  assert.doesNotMatch(run.stdout, /listening/);
});

test("GET /api/config answers without sign-in, with the flags, Certify's link and the product name", async () => {
  const base = await start({ PORTAL_APP_CERTIFY: "off", PORTAL_CERTIFY_URL: "https://apps.powerapps.com/play/e/1?tenantId=t" });
  const response = await fetch(`${base}/api/config`);
  assert.equal(response.status, 200);
  assert.deepEqual(await response.json(), { knowledgeHome: true, apps: { certify: false, context: true, discover: true }, certifyUrl: "https://apps.powerapps.com/play/e/1?tenantId=t", productName: PRODUCT_NAME, productShortName: "ALTA", logoTitle: false, contextMcpUrl: `${kgeUrl}/mcp` });
  assert.equal(PRODUCT_NAME, "Client ALTA");
  assert.equal(seen.filter((call) => call.url === "/whoami").length, 0);
  const alone = await start({ PORTAL_KNOWLEDGE_HOME: "off", PORTAL_APP_CERTIFY: "off", PORTAL_APP_DISCOVER: "off" });
  assert.deepEqual(await (await fetch(`${alone}/api/config`)).json(), { knowledgeHome: false, apps: { certify: false, context: true, discover: false }, certifyUrl: null, productName: PRODUCT_NAME, productShortName: "ALTA", logoTitle: false, contextMcpUrl: `${kgeUrl}/mcp` });
});

test("contextMcpUrl is KGE's public MCP address where one is set, and null without KGE", async () => {
  const published = await start({ KGE_PUBLIC_URL: "https://kge.example.com/" });
  assert.equal((await (await fetch(`${published}/api/config`)).json()).contextMcpUrl, "https://kge.example.com/mcp");
  const alone = await start({}, { env: await localCredentials(directory) });
  assert.equal((await (await fetch(`${alone}/api/config`)).json()).contextMcpUrl, null);
});

test("/api/context relays method, query and a raw body over 1 MB to KGE, with the person's credentials and the service auth", async () => {
  const base = await start();
  const upload = Buffer.alloc(2.5 * 1024 * 1024, 0x7b);
  seen.length = 0;
  const response = await fetch(`${base}/api/context/onboard/run?step=checks&x=a%20b`, { method: "POST", headers: { ...person, "content-type": "application/json", accept: "application/json" }, body: upload });
  assert.equal(response.status, 201);
  assert.deepEqual(await response.json(), { method: "POST", url: "/onboard/run?step=checks&x=a%20b", bytes: upload.length });
  assert.equal(response.headers.get("x-not-relayed"), null);
  const call = seen.at(-1);
  assert.ok(call.body.equals(upload));
  assert.equal(call.headers.cookie, person.cookie);
  assert.equal(call.headers.authorization, person.authorization);
  assert.equal(call.headers["x-serverless-authorization"], "Bearer service-token");
  assert.equal(call.headers["content-type"], "application/json");
  assert.equal(call.headers["x-caller-token"], undefined, "a person never borrows the portal's trusted caller token");
  assert.equal(call.headers["x-client-address"], undefined);
  assert.equal(seen.filter((c) => c.url === "/whoami").length, 0, "the proxy holds no authorisation logic of its own");

  const multipart = await fetch(`${base}/api/context/onboard/source`, { method: "PUT", headers: { ...person, "content-type": "multipart/form-data; boundary=xyz" }, body: "--xyz\r\n\r\nschema\r\n--xyz--" });
  assert.equal((await multipart.json()).method, "PUT");
  assert.equal(seen.at(-1).headers["content-type"], "multipart/form-data; boundary=xyz");

  for (const method of ["GET", "DELETE", "PATCH"]) {
    const relayed = await fetch(`${base}/api/context/steward/queue?unit=retail`, { method, headers: person });
    assert.equal(relayed.status, 201);
    assert.equal(seen.at(-1).method, method);
    assert.equal(seen.at(-1).url, "/steward/queue?unit=retail");
    assert.equal(seen.at(-1).body.length, 0);
  }
  const head = await fetch(`${base}/api/context/versions`, { method: "HEAD", headers: person });
  assert.equal(head.status, 201);
  assert.equal(seen.at(-1).method, "HEAD");
});

test("/api/context relays KGE's status, error body, content type, Retry-After, downloads and cookies", async () => {
  const base = await start();
  const busy = await fetch(`${base}/api/context/busy`, { headers: person });
  assert.equal(busy.status, 429);
  assert.equal(busy.headers.get("retry-after"), "9");
  assert.deepEqual(await busy.json(), { detail: "busy" });
  const refused = await fetch(`${base}/api/context/refused`, { method: "POST", headers: { ...person, "content-type": "application/json" }, body: "{}" });
  assert.equal(refused.status, 403);
  assert.deepEqual(await refused.json(), { detail: "not yours" });
  const exported = await fetch(`${base}/api/context/export`, { headers: { ...person, accept: "text/csv" } });
  assert.equal(exported.status, 200);
  assert.equal(exported.headers.get("content-type"), "text/csv; charset=utf-8");
  assert.equal(exported.headers.get("content-disposition"), "attachment; filename=rows.csv");
  assert.deepEqual(exported.headers.getSetCookie(), ["kge_session=fresh; Path=/"]);
  assert.equal(await exported.text(), "a,b\n1,2\n");
  assert.equal(seen.at(-1).headers.accept, "text/csv");
});

test("/api/context never leaves KGE's host, whatever the path", async () => {
  const base = await start();
  const raw = (path) => new Promise((resolve, reject) => {
    const url = new URL(base);
    httpRequest({ host: url.hostname, port: url.port, path, headers: person }, (res) => {
      let text = "";
      res.on("data", (chunk) => { text += chunk; });
      res.on("end", () => resolve({ status: res.statusCode, text }));
    }).on("error", reject).end();
  });
  seen.length = 0;
  const slashes = await raw("/api/context//evil.example/steal");
  assert.equal(slashes.status, 201);
  assert.equal(JSON.parse(slashes.text).url, "/evil.example/steal");
  const backslash = await raw("/api/context/\\\\evil.example/steal");
  assert.equal(backslash.status, 201);
  assert.equal(JSON.parse(backslash.text).url, "/evil.example/steal");
  for (const path of ["/api/context/a\\b", "/api/context", "/api/context/", "/api/context/?x=1"]) {
    const refused = await raw(path);
    assert.equal(refused.status, 404, path);
    assert.deepEqual(JSON.parse(refused.text), { detail: "Not found." });
  }
  assert.ok(seen.every((call) => !call.url.includes("\\")));
});

test("/api/context removes loopback Host treatment for every local destination and preserves remote Hosts", async (t) => {
  const calls = [];
  t.mock.method(globalThis, "fetch", async (url, options) => {
    calls.push({ url, headers: options.headers, transport: "fetch" });
    return new Response(null, { status: 204 });
  });
  for (const [protocol, client] of [["http:", http], ["https:", https]]) {
    t.mock.method(client, "request", (url, options, callback) => {
      calls.push({ url, headers: options.headers, transport: protocol });
      return new PassThrough().on("finish", () => {
        const response = Readable.from([]);
        response.statusCode = 204;
        response.rawHeaders = [];
        callback(response);
      });
    });
  }
  const destinations = [
    "http://127.0.0.1:8000", "http://127.42.3.4:8000", "http://127.255.255.255",
    "http://localhost:8000", "http://LOCALHOST:8000", "http://[::1]:8000",
    "http://[0:0:0:0:0:0:0:1]:8000",
    "https://localhost:8000",
    "https://context-pilot.run.app", "http://kge.example.com:8000",
  ];
  for (const baseUrl of destinations) {
    const remote = baseUrl.includes("run.app") || baseUrl.includes("kge.example.com");
    const proxy = contextProxy(kgeClient(baseUrl), baseUrl);
    for (const host of ["127.0.0.1:3000", "localhost", "[::1]", "portal.example.com"]) {
      for (const path of ["/principals", "/mode", "/profile"]) {
        const res = { removeHeader() {}, status(code) { assert.equal(code, 204); }, end() {} };
        await proxy({ url: path, method: "GET", headers: { host } }, res, (error) => { throw error; });
        const call = calls.at(-1);
        assert.equal(call.url.href, new URL(path, baseUrl).href);
        if (remote) {
          assert.equal(call.transport, "fetch");
          assert.equal(call.headers.host, undefined, "fetch retains the destination's own Host");
          assert.equal(call.url.host, new URL(baseUrl).host);
        } else {
          assert.equal(call.transport, new URL(baseUrl).protocol);
          assert.equal(call.headers.host, "context.portal.invalid");
        }
      }
    }
  }
});

test("/api/context/principals and /mode reach loopback KGE with a non-loopback Host", async () => {
  const base = await start({ DEMO_MODE: "1" });
  for (const path of ["/principals", "/mode"]) {
    const response = await fetch(`${base}/api/context${path}`);
    assert.equal(response.status, 201);
    assert.equal(seen.at(-1).url, path);
    assert.equal(seen.at(-1).headers.host, "context.portal.invalid");
  }
});

test("/api/context refuses unauthenticated demo sign-in and canonical path aliases before reaching KGE", async () => {
  const base = new URL(await start({ DEMO_MODE: "1" }));
  seen.length = 0;
  for (const path of [
    "/session/demo", "/SESSION/DeMo", "//session///demo/", "/session/./demo",
    "/other/../session/demo", "/session/demo?principal=admin", "/session/demo#alias", "/%73ession/%64emo",
    "/session%2fdemo", "/session/%2e/demo", "/other/%2e%2e/session/demo",
    "/other%2f%2e%2e%2fSESSION%2fDEMO", "/session%5cdemo",
  ]) {
    const response = await new Promise((resolve, reject) => {
      httpRequest({ host: base.hostname, port: base.port, path: `/api/context${path}`, method: "POST" }, (res) => {
        let body = "";
        res.on("data", (chunk) => { body += chunk; });
        res.on("end", () => resolve({ status: res.statusCode, headers: res.headers, body }));
      }).on("error", reject).end();
    });
    assert.equal(response.status, 404, path);
    assert.deepEqual(JSON.parse(response.body), { detail: "Demo sign-in is unavailable through the portal. Sign in with your credentials." }, path);
    assert.equal(response.headers["set-cookie"], undefined, path);
    assert.equal(seen.length, 0, path);
  }
  for (const path of ["/session", "/session/token", "/session/demo-status", "/session%252fdemo", "/map/100%25", "/onboard/x%25y"]) {
    assert.equal((await fetch(`${base}api/context${path}`, { method: "POST" })).status, 201, path);
    assert.equal(seen.at(-1).url, path);
  }
});

test("/api/context is off, 404 'Context is not set up here', when the Context flag is off or KGE is not configured", async () => {
  const off = await start({ PORTAL_APP_CONTEXT: "off" });
  const alone = await start({}, { env: await localCredentials(directory) });
  for (const base of [off, alone]) {
    seen.length = 0;
    const response = await fetch(`${base}/api/context/onboard/run`, { method: "POST", headers: { ...person, "content-type": "application/json" }, body: "{}" });
    assert.equal(response.status, 404);
    assert.equal((await response.json()).detail, "Context is not set up here");
    assert.equal(seen.length, 0);
  }
});

test("/api/context answers 502 with the Context availability message for connection failures and timeouts", async () => {
  const down = await start({}, { env: { KGE_URL: "http://127.0.0.1:1", PORTAL_SYNC_TOKEN: "s".repeat(32) } });
  const unreachable = await fetch(`${down}/api/context/profile`, { headers: person });
  assert.equal(unreachable.status, 502);
  assert.deepEqual(await unreachable.json(), { detail: "Context is unavailable right now. Try again shortly." });
  const slow = await start({}, { app: { kge: { request: async () => { throw Object.assign(new Error("timed out"), { name: "TimeoutError" }); } } } });
  const overran = await fetch(`${slow}/api/context/eval`, { method: "POST", headers: person, body: "x" });
  assert.equal(overran.status, 502);
  assert.deepEqual(await overran.json(), { detail: "Context is unavailable right now. Try again shortly." });
});

test("the Context proxy asks kge.request for the long time budget", async () => {
  const calls = [];
  const base = await start({}, { app: { kge: { request: async (_req, path, options) => { calls.push({ path, options }); return new Response(null, { status: 204 }); } } } });
  const response = await fetch(`${base}/api/context/agent`, { method: "POST", headers: person, body: "{}" });
  assert.equal(response.status, 204);
  assert.equal(calls[0].path, "/agent");
  assert.equal(calls[0].options.long, true);
  assert.equal(calls[0].options.trusted, false);
  assert.equal(calls[0].options.duplex, "half");
});

test("old Context bookmarks redirect to the portal workspace", async () => {
  const on = await start();
  for (const path of ["/context-app", "/context-app/"]) {
    const page = await fetch(`${on}${path}`, { redirect: "manual" });
    assert.equal(page.status, 302);
    assert.equal(page.headers.get("location"), "/context");
  }
  const off = await start({ PORTAL_APP_CONTEXT: "off" });
  assert.equal((await fetch(`${off}/context-app/`, { redirect: "manual" })).status, 404);
});

test("with Discover off its API routes answer 404 'Discover is not set up here', and sign-in, session, role and config keep working", async () => {
  const base = await start({ PORTAL_APP_DISCOVER: "off" });
  const json = { ...person, "content-type": "application/json" };
  for (const [path, method, body] of [["/api/products", "GET"], ["/api/search", "POST", "{}"], ["/api/ask", "POST", "{}"], ["/api/admin/users", "GET"], ["/ask", "POST", "{}"], ["/search", "POST", "{}"], ["/getConfig", "GET"], ["/product/retail.accounts", "GET"], ["/getFavourites", "GET"], ["/getPublishedStatus", "GET"]]) {
    const response = await fetch(`${base}${path}`, { method, headers: json, body });
    assert.equal(response.status, 404, path);
    assert.deepEqual(await response.json(), { detail: "Discover is not set up here", message: "Discover is not set up here" }, path);
  }
  const signedOut = await fetch(`${base}/api/products`);
  assert.equal(signedOut.status, 404);
  for (const path of ["/whoami", "/api/role", "/readable-units", "/api/config", "/brand", "/healthz"]) assert.equal((await fetch(`${base}${path}`, { headers: person })).status, 200, path);
  assert.equal((await fetch(`${base}/api/context/versions`, { headers: person })).status, 201);
});

test("each app flag off in turn leaves the others working", async () => {
  for (const app of Object.keys(APPS)) {
    const base = await start({ [APPS[app]]: "off" });
    const config = await (await fetch(`${base}/api/config`)).json();
    assert.equal(config.apps[app], false);
    assert.equal(config.knowledgeHome, true);
    assert.equal((await fetch(`${base}/api/products`, { headers: person })).status, app === "discover" ? 404 : 200, app);
    assert.equal((await fetch(`${base}/api/context/versions`, { headers: person })).status, app === "context" ? 404 : 201, app);
    assert.equal((await fetch(`${base}/context-app/`, { redirect: "manual" })).status, app === "context" ? 404 : 302, app);
  }
});

test("with the ALTA home off, the single app on is the only one served", async () => {
  for (const app of Object.keys(APPS)) {
    const flags = { PORTAL_KNOWLEDGE_HOME: "off", ...Object.fromEntries(Object.entries(APPS).map(([other, name]) => [name, other === app ? "on" : "off"])) };
    const base = await start(flags);
    const config = await (await fetch(`${base}/api/config`)).json();
    assert.equal(config.knowledgeHome, false);
    assert.deepEqual(config.apps, { certify: app === "certify", context: app === "context", discover: app === "discover" });
    assert.equal((await fetch(`${base}/api/products`, { headers: person })).status, app === "discover" ? 200 : 404, app);
    assert.equal((await fetch(`${base}/api/context/versions`, { headers: person })).status, app === "context" ? 201 : 404, app);
    assert.equal((await fetch(`${base}/context-app/`, { redirect: "manual" })).status, app === "context" ? 302 : 404, app);
    assert.equal((await fetch(`${base}/whoami`, { headers: person })).status, 200, app);
  }
});

const OPERATOR_ONLY = [
  /* Start-up and CLI refusals for operators, naming KGE_URL or KGE's file formats; a person never sees them. */
  ["brand.js", "${setting} must be KGE's http or https address, such as https://kge.example.com."],
  ["identity.js", "${setting} is required without KGE"],
  ["identity.js", "PORTAL_SESSION_SECRET must be at least 32 characters without KGE"],
  ["identity.js", "PORTAL_IDENTITIES_FILE must contain distinct KGE principals with role, reads and processes"],
  ["identity.js", "PORTAL_PASSWORDS_FILE must contain KGE scrypt password hashes"],
  ["passwords.js", "principals.json must contain KGE principal records."],
];

async function sources(dir) {
  const files = [];
  for (const entry of await readdir(dir, { withFileTypes: true })) {
    const path = join(dir, entry.name);
    if (entry.isDirectory() && entry.name !== "repository") files.push(...await sources(path));
    else if (entry.name.endsWith(".js")) files.push(path);
  }
  return files;
}

test("no string the portal server can send a person names KGE", async () => {
  const src = join(SERVER, "src");
  const found = [];
  for (const file of await sources(src)) {
    const code = (await readFile(file, "utf8")).split("\n").filter((line) => !/^\s*(\/\/|\/\*|\*)/.test(line)).join("\n");
    for (const [literal] of code.matchAll(/"(?:[^"\\\n]|\\.)*"|'(?:[^'\\\n]|\\.)*'|`(?:[^`\\]|\\.)*`/g)) {
      const text = literal.slice(1, -1);
      if (/KGE(?![_A-Z])/.test(text)) found.push([relative(src, file), text]);
    }
  }
  assert.deepEqual(found.filter(([file, text]) => !OPERATOR_ONLY.some(([f, t]) => f === file && t === text)), []);
  assert.ok(found.length >= OPERATOR_ONLY.length - 1, "the scan reads the server's literals");
});

test("the portal's own messages, with KGE down or absent, never name KGE", async () => {
  const down = await start({}, { env: { KGE_URL: "http://127.0.0.1:1", PORTAL_SYNC_TOKEN: "s".repeat(32) } });
  const bodies = [];
  const collect = async (response) => bodies.push(`${response.status} ${[...response.headers].map(([k, v]) => `${k}: ${v}`).join("\n")}\n${await response.text()}`);
  await collect(await fetch(`${down}/session`, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ username: "a", password: "b" }) }));
  await collect(await fetch(`${down}/session`, { method: "DELETE", headers: person }));
  for (const path of ["/whoami", "/api/products", "/api/role", "/product/x", "/getConfig", "/api/context/profile"]) await collect(await fetch(`${down}${path}`, { headers: person }));
  const off = await start({ PORTAL_APP_CONTEXT: "off", PORTAL_APP_DISCOVER: "off" });
  for (const path of ["/api/products", "/api/context/x", "/context-app/"]) await collect(await fetch(`${off}${path}`, { headers: person }));
  assert.equal(bodies.length, 11);
  for (const body of bodies) assert.doesNotMatch(body, /KGE/, body);
  assert.ok(bodies.some((body) => body.includes("Context is unavailable right now. Try again shortly.")));
});

test("the React app is served for the ALTA home, Certify, Context, the ALTA docs and Discover, without shadowing /api, /brand or /context-app", async () => {
  const ui = join(directory, "ui");
  const { mkdir } = await import("node:fs/promises");
  await mkdir(ui, { recursive: true });
  await writeFile(join(ui, "index.html"), "<!doctype html><div id=root>react</div>");
  const base = await start({}, { app: { uiPath: ui } });
  for (const path of ["/", "/certify", "/context", "/knowledge/docs/at-a-glance", "/knowledge/docs/start-here", "/search", "/Search?q=accounts", "/home", "/adminaccess", "/productDetails/retail.accounts"]) {
    const response = await fetch(`${base}${path}`, { headers: { accept: "text/html" } });
    assert.equal(response.status, 200, path);
    assert.equal(await response.text(), "<!doctype html><div id=root>react</div>", path);
  }
  assert.equal((await (await fetch(`${base}/api/config`)).json()).productName, PRODUCT_NAME);
  const brand = await fetch(`${base}/brand`);
  assert.equal(brand.status, 200);
  assert.match(brand.headers.get("content-type"), /application\/json/);
  for (const path of ["/api/not-a-route", "/brand/not-a-logo", "/context-app/not-a-file"]) {
    const missing = await fetch(`${base}${path}`, { headers: { ...person, accept: "text/html" } });
    assert.equal(missing.status, 404, path);
    assert.match(missing.headers.get("content-type"), /application\/json/, path);
  }
  const bookmark = await fetch(`${base}/context-app/`, { redirect: "manual" });
  assert.equal(bookmark.status, 302);
  assert.equal(bookmark.headers.get("location"), "/context");
  assert.equal((await (await fetch(`${base}/api/context/versions`, { headers: person })).json()).url, "/versions");
  const off = await start({ PORTAL_APP_CONTEXT: "off" }, { app: { uiPath: ui } });
  assert.equal((await fetch(`${off}/context-app/`)).status, 404);
  assert.equal((await fetch(`${off}/context`)).status, 404, "an unavailable Context is refused before the React page is served");
});

for (const [app, flag] of Object.entries(APPS)) test(`an outage names ${app} when it runs alone`, async () => {
  const base = await start({}, { env: {
    KGE_URL: "http://127.0.0.1:1", PORTAL_SYNC_TOKEN: "s".repeat(32),
    PORTAL_KNOWLEDGE_HOME: "off", PORTAL_APP_CERTIFY: "off", PORTAL_APP_CONTEXT: "off", PORTAL_APP_DISCOVER: "off", [flag]: "on",
  } });
  const name = app.charAt(0).toUpperCase() + app.slice(1);
  const session = await fetch(`${base}/session`, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ username: "a", password: "b" }) });
  assert.equal(session.status, 502);
  assert.deepEqual(await session.json(), { detail: `${name} is unavailable.` });
  const response = await fetch(`${base}${app === "discover" ? "/api/products" : "/api/role"}`, { headers: person });
  assert.equal(response.status, 502);
  assert.deepEqual(await response.json(), { message: `${name} is unavailable.` });
});

test("nested Docs pages resolve the manifest from the portal root", async () => {
  const html = await readFile(join(SERVER, "../ui/index.html"), "utf8");
  const manifest = html.match(/rel="manifest" href="([^"]+)"/)[1];
  for (const page of ["at-a-glance", "start-here"]) {
    assert.equal(new URL(manifest, `https://example.com/knowledge/docs/${page}`).pathname, "/manifest.json");
  }
});

test("Docs heading anchors reserve the global bar height and a visible gap", async () => {
  const css = await readFile(join(SERVER, "../ui/src/pages/knowledge/docs.css"), "utf8");
  const headings = css.match(/\.kd-doc h2\s*\{([^}]+)\}/)[1];
  assert.match(headings, /scroll-margin-top:\s*calc\(var\(--v3-topbar-h\)\s*\+\s*16px\)/);
  const topbar = await readFile(join(SERVER, "../ui/src/assets/styles/v3-topbar.css"), "utf8");
  assert.match(topbar, /height:\s*var\(--v3-topbar-h\)/);
});

test("the Context bootstrap carries the Ask header settings the portal started with", async () => {
  for (const [flags, ask] of [
    [{}, { title: "Ask a governed question", subtitle: "Certified definitions and figures, answered only from data you may read." }],
    [{ PORTAL_CONTEXT_ASK_TITLE: " ", PORTAL_CONTEXT_ASK_SUBTITLE: "" }, { title: "Ask a governed question", subtitle: "Certified definitions and figures, answered only from data you may read." }],
    [{ PORTAL_CONTEXT_ASK_TITLE: "  Ask Meridian  ", PORTAL_CONTEXT_ASK_SUBTITLE: "Answers from certified figures." }, { title: "Ask Meridian", subtitle: "Answers from certified figures." }],
  ]) {
    const base = await start(flags);
    seen.length = 0;
    const response = await fetch(`${base}/api/context/bootstrap`, { headers: person });
    assert.equal(response.status, 200);
    const body = await response.json();
    assert.deepEqual(body.ask, ask);
    assert.deepEqual(body.person, { id: "analyst_retail" });
    assert.deepEqual(seen.map(({ url }) => url).sort(), ["/health", "/mode", "/presets", "/profile", "/whoami"]);
  }
});

test("Context without KGE serves neither its React page, old bookmark nor BFF, and advertises no address", async () => {
  const ui = join(directory, "standalone-ui");
  const { mkdir } = await import("node:fs/promises");
  await mkdir(ui, { recursive: true });
  await writeFile(join(ui, "index.html"), "<!doctype html><div id=root>react</div>");
  const local = await localCredentials(directory);
  for (const env of [
    { ...local, PORTAL_APP_CONTEXT: "on", KGE_PUBLIC_URL: "https://example.com/kge" },
    { KGE_URL: kgeUrl, PORTAL_APP_CONTEXT: "off", PORTAL_SYNC_TOKEN: "s".repeat(32) },
  ]) {
    const base = await start({}, { env, app: { uiPath: ui } });
    seen.length = 0;
    assert.equal((await (await fetch(`${base}/api/config`)).json()).contextMcpUrl, null);
    for (const path of ["/context", "/context/", "/context/onboard", "/context-app/", "/api/context/bootstrap", "/api/context/onboard"]) {
      for (const method of ["GET", "HEAD", "POST"]) {
        const response = await fetch(`${base}${path}`, { method, redirect: "manual" });
        assert.equal(response.status, 404, `${method} ${path}`);
        assert.notEqual(response.headers.get("location"), "/context");
        if (method !== "HEAD") assert.equal((await response.json()).detail, "Context is not set up here");
      }
    }
    assert.equal(seen.length, 0, "no Context request reaches KGE");
    assert.equal((await fetch(`${base}/home`)).status, 200, "Discover's page remains available");
  }
});
