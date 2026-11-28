import assert from "node:assert/strict";
import { Writable } from "node:stream";
import { finished } from "node:stream/promises";
import test from "node:test";
import { contextProxy } from "../src/context.js";

async function proxy(response, url = "/onboarding-guide?role=steward", method = "GET", productName, env = {}) {
  const chunks = [];
  const headers = new Headers({ "x-portal-notice": "old notice" });
  const res = new Writable({ write(chunk, _encoding, done) { chunks.push(chunk); done(); } });
  res.removeHeader = (name) => headers.delete(name);
  res.status = (code) => { res.statusCode = code; return res; };
  res.set = (name, value) => { headers.set(name, value); return res; };
  res.append = (name, value) => headers.append(name, value);
  res.send = (body) => res.end(body);
  res.json = (body) => res.end(JSON.stringify(body));
  let failure;
  let requested;
  await contextProxy({ request: async (_req, path, options) => {
    requested = { path, options };
    return response;
  } }, undefined, productName, { env })({ url, method, headers: { accept: "text/html" } }, res, (error) => { failure = error; res.end(); });
  await finished(res);
  return { body: Buffer.concat(chunks).toString(), headers, status: res.statusCode, failure, requested };
}

const guide = `<!doctype html><title>[Client name] Knowledge — Docs</title>
<a href="/" target="_top">Context / Sign in</a>
<main><a href="/onboarding-guide?role=steward&amp;x=a%20b#steps">Steward</a>
<a href='/onboarding-guide?role=business'>Business</a>
<p>Sign in on the Context page. Context access admins are portal admins.</p>
<code>kge connect</code><code>KGE_URL</code></main>
<script>if (response.status === 403) { location.replace('/onboarding-guide'); return; }</script>`;

test("the portal guide renames the product and keeps role navigation, refresh redirects and top-level sign-in in the portal", async () => {
  const result = await proxy(new Response(guide, { headers: {
    "content-type": "text/html; charset=utf-8", "cache-control": "no-store",
    "set-cookie": "kge_session=fresh; Path=/", "content-length": String(Buffer.byteLength(guide)),
  } }));
  assert.equal(result.status, 200);
  assert.equal(result.failure, undefined);
  assert.equal(result.requested.path, "/onboarding-guide?role=steward");
  assert.equal(result.requested.options.trusted, false);
  assert.match(result.body, /<title>Client ALTA — Docs<\/title>/);
  assert.match(result.body, /<a href="\/context" target="_top">Context \/ Sign in<\/a>/);
  assert.match(result.body, /href="\/api\/context\/onboarding-guide\?role=steward&amp;x=a%20b#steps"/);
  assert.match(result.body, /href='\/api\/context\/onboarding-guide\?role=business'/);
  assert.match(result.body, /location\.replace\('\/api\/context\/onboarding-guide'\)/);
  assert.match(result.body, /Sign in on the Context page\. Context access admins/);
  assert.doesNotMatch(result.body, /\bKGE\b/);
  assert.match(result.body, /<code>kge connect<\/code><code>KGE_URL<\/code>/);
  assert.equal(result.headers.get("content-type"), "text/html; charset=utf-8");
  assert.equal(result.headers.get("cache-control"), "no-store");
  assert.equal(result.headers.get("set-cookie"), "kge_session=fresh; Path=/");
  assert.equal(result.headers.get("content-length"), null, "the upstream length no longer describes the rewritten body");
  assert.equal(result.headers.get("x-portal-notice"), null);
});

test("guide HTTP redirects preserve status and query while staying on the portal", async () => {
  for (const [location, expected] of [
    ["/onboarding-guide?role=business#steps", "/api/context/onboarding-guide?role=business#steps"],
    ["/onboarding-guide", "/api/context/onboarding-guide"],
    ["http://context.portal.invalid/docs", "/api/context/docs"],
    ["/", "/"],
  ]) {
    const result = await proxy(new Response(null, { status: 303, headers: { location } }));
    assert.equal(result.status, 303);
    assert.equal(result.headers.get("location"), expected);
    assert.equal(result.body, "");
  }
});

test("only the onboarding guide HTML is rewritten; other HTML and JSON keep their streamed bodies", async () => {
  for (const [url, type, body, status] of [
    ["/ab/example/report.html", "text/html", guide, 200],
    ["/onboarding-guide-extra", "text/html", guide, 200],
    ["/onboarding-guide?role=operator", "application/json", '{"detail":"KGE role required"}', 403],
  ]) {
    const result = await proxy(new Response(body, { status, headers: { "content-type": type, "retry-after": "9" } }), url);
    assert.equal(result.status, status);
    assert.equal(result.body, body);
    assert.equal(result.headers.get("content-type"), type);
    assert.equal(result.headers.get("retry-after"), "9");
  }
});

test("HEAD does not read or send a guide body", async () => {
  const response = new Response(guide, { headers: { "content-type": "text/html" } });
  const result = await proxy(response, "/onboarding-guide", "HEAD");
  assert.equal(result.status, 200);
  assert.equal(result.body, "");
  assert.equal(response.bodyUsed, false);
});

test("a failed guide body read gives the availability message", async () => {
  const error = Object.assign(new Error("timed out"), { name: "TimeoutError" });
  const response = new Response(new ReadableStream({ start(controller) { controller.error(error); } }), { headers: { "content-type": "text/html" } });
  const result = await proxy(response);
  assert.equal(result.failure, undefined);
  assert.equal(result.status, 502);
  assert.equal(result.headers.get("content-type"), "application/json");
  assert.deepEqual(JSON.parse(result.body), { detail: "Context is unavailable right now. Try again shortly." });
});


test("a documentation slash redirect cannot leave the portal for the loopback Host override", async () => {
  const result = await proxy(new Response(null, { status: 307, headers: { location: "http://context.portal.invalid/docs" } }), "/docs/");
  assert.equal(result.status, 307);
  assert.equal(result.headers.get("location"), "/api/context/docs");
});

test("portal guides show the configured ALTA name, escape it, and use curated dataset", async () => {
  const upstream = `${guide}<p>Browse a governed dataset. Governed datasets are listed here.</p>`;
  const result = await proxy(new Response(upstream, { headers: { "content-type": "text/html" } }), "/onboarding-guide", "GET", "Meridian & <Group> ALTA");
  assert.match(result.body, /<title>Meridian &amp; &lt;Group&gt; ALTA — Docs<\/title>/);
  assert.match(result.body, /Browse a curated dataset\. Curated datasets/);
  assert.doesNotMatch(result.body, /\[Client name\] Knowledge|governed dataset|Data Discovery Portal/i);
});

test("developer docs show the public API and MCP addresses rather than the internal origin", async () => {
  const html = '<code id="connection-api">http://context.portal.invalid</code><code id="connection-mcp">http://context.portal.invalid/mcp</code><pre id="connection-config">internal config</pre>';
  const result = await proxy(new Response(html, { headers: { "content-type": "text/html" } }), "/onboarding-guide?role=developer", "GET", undefined, { KGE_PUBLIC_URL: "https://context.example.com/" });
  assert.match(result.body, /<code id="connection-api">https:\/\/context.example.com<\/code>/);
  assert.match(result.body, /<code id="connection-mcp">https:\/\/context.example.com\/mcp<\/code>/);
  assert.match(result.body, /&quot;url&quot;: &quot;https:\/\/context.example.com\/mcp&quot;/);
  assert.match(result.body, /Bearer &lt;access_token&gt;/);
  assert.doesNotMatch(result.body, /context.portal.invalid|api\/context/);
});

test("developer docs say when connection addresses have not been configured", async () => {
  const html = '<code id="connection-api">internal</code><code id="connection-mcp">internal</code>';
  const result = await proxy(new Response(html, { headers: { "content-type": "text/html" } }));
  assert.match(result.body, /The API address is not set up here/);
  assert.match(result.body, /The MCP address is not set up here/);
});
