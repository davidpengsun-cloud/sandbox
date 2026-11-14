import assert from "node:assert/strict";
import http from "node:http";
import { Readable, Writable } from "node:stream";
import { finished } from "node:stream/promises";
import test from "node:test";
import { CONTEXT_ASK_SUBTITLE, CONTEXT_ASK_TITLE, appSettings } from "../src/apps.js";
import { contextPage, contextProxy, UNAVAILABLE } from "../src/context.js";
import { kgeClient } from "../src/kge.js";

async function call(kge, path, headers = {}, { method = "GET", body, address = "192.0.2.1", env = {}, options = { env } } = {}) {
  const chunks = [];
  const responseHeaders = new Headers();
  const res = new Writable({ write(chunk, _, done) { chunks.push(chunk); done(); } });
  res.removeHeader = (key) => responseHeaders.delete(key);
  res.status = (status) => { res.statusCode = status; return res; };
  res.set = (key, value) => { responseHeaders.set(key, value); return res; };
  res.append = (key, value) => responseHeaders.append(key, value);
  res.json = (body) => res.end(JSON.stringify(body));
  res.send = (body) => res.end(body);
  const req = Object.assign(Readable.from(body === undefined ? [] : [body]), { url: path, method, headers, socket: { remoteAddress: address } });
  await contextProxy(kge, undefined, undefined, options)(req, res, (error) => { throw error; });
  await finished(res);
  const text = Buffer.concat(chunks).toString();
  return { status: res.statusCode || 200, headers: responseHeaders, text, json: () => JSON.parse(text) };
}
const json = (body, status = 200) => Response.json(body, { status });

test("Context evaluation preserves the caller's credentials and KGE's result or 403", async () => {
  for (const [who, status, outcome] of [
    ["analyst_retail", 403, { detail: "Only identities with the building process or admins may start an evaluation run." }],
    ["knowledge_engineer", 200, { graph: { rows: [{ id: "G001", ok: true }] } }],
    ["admin", 200, { graph: { rows: [{ id: "G001", ok: true }] } }],
  ]) {
    const headers = { authorization: `Bearer ${who}`, cookie: `kge_session=${who}` };
    let calls = 0;
    const result = await call({ request: async (req, path, options) => {
      calls += 1;
      assert.equal(path, "/eval");
      assert.equal(req.headers, headers);
      assert.equal(options.method, "POST");
      assert.equal(options.trusted, false);
      return json(outcome, status);
    } }, "/eval", headers, { method: "POST" });
    assert.equal(calls, 1);
    assert.equal(result.status, status);
    assert.deepEqual(result.json(), outcome);
  }
});

for (const path of ["/session", "/session/token"]) {
  for (const cloud of [false, true]) {
    test(`Context ${path} forwards each caller's address and relays sign-in and lockout (${cloud ? "Cloud Run" : "local"})`, async () => {
      const serviceAuth = { headers: async () => ({ "x-caller-token": "portal-service", "x-serverless-authorization": "Bearer ingress" }) };
      const client = kgeClient("https://context.example.com", serviceAuth);
      let expected;
      const kge = { request: (req, target, options) => client.request(req, target, { ...options, transport: async (url, init) => {
        assert.equal(url.pathname, path);
        assert.equal(init.method, "POST");
        assert.equal(init.headers["x-client-address"], expected.address);
        assert.equal(init.headers["x-caller-token"], "portal-service");
        assert.equal(init.headers["x-serverless-authorization"], "Bearer ingress");
        let forwarded = "";
        for await (const chunk of init.body) forwarded += chunk;
        assert.equal(forwarded, expected.body);
        return new Response(JSON.stringify(expected.result), { status: expected.status, headers: {
          "content-type": "application/json", ...(expected.status === 429 ? { "retry-after": "300" } : {}),
          ...(expected.status === 200 && path === "/session" ? { "set-cookie": "session=opened; HttpOnly; Path=/" } : {}),
        } });
      } }) };
      const success = path === "/session" ? { id: "reader@example.com" } : { access_token: ISSUED, token_type: "Bearer", expires_in: 900 };
      const attempts = [
        ...Array.from({ length: 5 }, () => ["192.0.2.1", "wrong", 401]),
        ["192.0.2.2", "right", 200], ["192.0.2.1", "right", 429],
      ];
      for (const [address, password, status] of attempts) {
        const body = JSON.stringify({ username: "reader@example.com", password });
        expected = { address, body, status, result: status === 200 ? success : { detail: status === 429 ? "too many sign-in attempts" : "wrong username or password" } };
        const headers = { "content-type": "application/json", "content-length": String(Buffer.byteLength(body)),
          "x-client-address": "forged", "x-caller-token": "forged", "x-forwarded-for": `forged, ${cloud ? address : "forged"}` };
        const response = await call(kge, path, headers, { method: "POST", body, address: cloud ? "192.0.2.99" : address, env: cloud ? { K_SERVICE: "portal" } : {} });
        assert.equal(response.status, status);
        assert.deepEqual(response.json(), expected.result);
        if (status === 429) assert.equal(response.headers.get("retry-after"), "300");
        if (status === 200 && path === "/session") assert.match(response.headers.get("set-cookie"), /session=opened/);
      }
    });
  }
}

const ISSUED = ["is", "sued"].join("");

test("Context grants trusted address forwarding only to credential POSTs", async () => {
  const client = kgeClient("https://context.example.com", { headers: async () => ({ "x-caller-token": "portal-service", "x-serverless-authorization": "Bearer ingress" }) });
  const lookalikes = [["/SESSION", "POST"], ["/session/", "POST"], ["/whoami/..%2Fsession", "POST"], ["/x/..%2F..%2Fsession/token", "POST"]];
  for (const [path, method] of [["/session", "GET"], ["/session", "DELETE"], ["/session/token", "GET"], ["/session/other", "POST"], ["/catalogue/portal", "GET"], ["/onboard/run", "POST"], ["/whoami", "GET"], ["/bootstrap", "GET"], ...lookalikes]) {
    const sent = [];
    const kge = { request: (req, target, options) => client.request(req, target, { ...options, transport: async (_url, init) => {
      sent.push(init.headers);
      return json({});
    } }) };
    const result = await call(kge, path, { authorization: "Bearer person", cookie: "session=person", "x-caller-token": "forged", "x-client-address": "forged" }, { method });
    assert.equal(sent.length, path === "/bootstrap" ? 5 : 1, path);
    assert.ok(result.status < 500, `${method} ${path} answered ${result.status}`);
    for (const headers of sent) {
      assert.equal(headers["x-caller-token"], undefined, path);
      assert.equal(headers["x-client-address"], undefined, path);
      assert.equal(headers["x-serverless-authorization"], "Bearer ingress", path);
      assert.equal(headers.authorization, "Bearer person", path);
      assert.equal(headers.cookie, "session=person", path);
    }
  }
});

test("Context forwards suggested-link Accept and Reject bodies and credentials unchanged", async () => {
  for (const [tag, decision] of [["suggested/high", "accept"], ["suggested/lower", "reject"]]) {
    const body = JSON.stringify({ tag, decision });
    const headers = { authorization: "Bearer steward", cookie: "kge_session=steward",
      "content-type": "application/json", "content-length": String(Buffer.byteLength(body)) };
    const outcome = { decision, superseded: tag, by: tag + "/human", mention_created: decision === "accept" };
    const calls = [];
    const result = await call({ request: async (req, path, options) => {
      calls.push(path);
      assert.equal(path, "/steward/decide");
      assert.equal(req.headers, headers);
      assert.equal(options.method, "POST");
      assert.equal(options.trusted, false);
      assert.equal(options.body, req);
      assert.equal(options.duplex, "half");
      let forwarded = "";
      for await (const chunk of options.body) forwarded += chunk;
      assert.equal(forwarded, body);
      return json(outcome);
    } }, "/steward/decide", headers, { method: "POST", body });
    assert.deepEqual(calls, ["/steward/decide"]);
    assert.equal(result.status, 200);
    assert.deepEqual(result.json(), outcome);
  }
});

test("the opening screen combines metadata under the signed-in person's credentials", async () => {
  const calls = [];
  const headers = { authorization: "Bearer person", cookie: "kge_session=person" };
  const result = await call({ request: async (req, path, options) => {
    calls.push({ req, path, options });
    return json({ source: path });
  } }, "/bootstrap", headers);
  assert.equal(result.status, 200);
  assert.deepEqual(result.json(), {
    person: { source: "/whoami" }, profile: { source: "/profile" }, mode: { source: "/mode" },
    presets: { source: "/presets" }, build: { source: "/health" },
    ask: { title: "Ask a governed question", subtitle: "Certified definitions and figures, answered only from data you may read." },
  });
  assert.equal(result.headers.get("cache-control"), "no-store");
  assert.equal(calls.length, 5);
  for (const { req, options } of calls) {
    assert.equal(req.headers, headers);
    assert.equal(options.trusted, false);
    assert.equal(options.body, undefined);
  }
  const healthz = await call({ request: async (_req, path) => json({ source: path }) }, "/healthz", headers);
  assert.equal(healthz.status, 200);
  assert.deepEqual(healthz.json(), { source: "/health" });
});

test("the opening screen carries the Ask header's title and subtitle from the portal's settings, not from KGE", async () => {
  const headers = { authorization: "Bearer person" };
  for (const [options, ask] of [
    [{ env: {} }, { title: CONTEXT_ASK_TITLE, subtitle: CONTEXT_ASK_SUBTITLE }],
    [{ env: {}, ...appSettings({}) }, { title: CONTEXT_ASK_TITLE, subtitle: CONTEXT_ASK_SUBTITLE }],
    [{ env: {}, ...appSettings({ PORTAL_CONTEXT_ASK_TITLE: " Ask Meridian " }) }, { title: "Ask Meridian", subtitle: CONTEXT_ASK_SUBTITLE }],
    [{ env: {}, ...appSettings({ PORTAL_CONTEXT_ASK_SUBTITLE: "Answers from certified figures." }) }, { title: CONTEXT_ASK_TITLE, subtitle: "Answers from certified figures." }],
    [{ env: {}, ...appSettings({ PORTAL_CONTEXT_ASK_TITLE: "Ask Meridian", PORTAL_CONTEXT_ASK_SUBTITLE: "Answers from certified figures." }) }, { title: "Ask Meridian", subtitle: "Answers from certified figures." }],
  ]) {
    const calls = [];
    const result = await call({ request: async (_req, path) => {
      calls.push(path);
      return json(path === "/whoami" ? { id: "person", ask: "from KGE" } : { source: path });
    } }, "/bootstrap", headers, { options });
    assert.equal(result.status, 200);
    assert.deepEqual(calls.sort(), ["/health", "/mode", "/presets", "/profile", "/whoami"]);
    assert.deepEqual(result.json().ask, ask);
    assert.deepEqual(result.json().person, { id: "person", ask: "from KGE" });
  }
});

for (const role of ["analyst", "knowledge_engineer", "business_team", "steward", "access_admin", "no_role"]) {
  test(`KGE's refusal for ${role} retains its status and message in a resource call and the combined screen`, async () => {
    const refusal = { detail: `${role} may not perform this operation` };
    for (const status of [401, 403, 409, 422, 429, 503]) {
      const kge = { request: async (_req, path) => path === "/whoami" || path === "/onboard" ? json(refusal, status) : json({}) };
      for (const path of ["/bootstrap", "/onboard"]) {
        const result = await call(kge, path, { authorization: `Bearer ${role}` });
        assert.equal(result.status, status);
        assert.deepEqual(result.json(), refusal);
      }
    }
  });
}

test("only a missing install is not set up; network errors and timeouts use the availability message", async () => {
  assert.equal((await call(null, "/profile")).json().detail, "Context is not set up here");
  for (const name of ["Error", "TimeoutError", "AbortError"]) {
    const result = await call({ request: async () => { throw Object.assign(new Error("private transport detail"), { name }); } }, "/profile");
    assert.equal(result.status, 502);
    assert.deepEqual(result.json(), { detail: UNAVAILABLE });
  }
});

test("developer reference links and interactive API calls point at the BFF", async () => {
  const html = `<script>url: '/openapi.json', oauth2RedirectUrl: window.location.origin + '/docs/oauth2-redirect'</script>`;
  const page = await call({ request: async () => new Response(html, { headers: { "content-type": "text/html" } }) }, "/docs");
  assert.match(page.text, /'\/api\/context\/openapi.json'/);
  assert.match(page.text, /'\/api\/context\/docs\/oauth2-redirect'/);
  const schema = await call({ request: async () => json({ openapi: "3.1.0", servers: [{ url: "https://kge.example.com" }], paths: { "/ask": {} } }) }, "/openapi.json");
  assert.deepEqual(schema.json().servers, [{ url: "/api/context" }]);
  assert.deepEqual(schema.json().paths, { "/ask": {} });
});


test("old page bookmarks redirect to Context; asset paths and writes never serve a second page", () => {
  for (const [on, path, method, status] of [[true, "/", "GET", 302], [true, "/", "HEAD", 302], [true, "/other.js", "GET", 404], [true, "/", "POST", 404], [false, "/", "GET", 404]]) {
    let code;
    let body;
    const res = {
      status(value) { code = value; return this; },
      json(value) { body = value; },
      redirect(value, location) { code = value; body = location; },
    };
    contextPage(on)({ path, method }, res);
    assert.equal(code, status);
    if (status === 302) assert.equal(body, "/context");
    else assert.equal(body.detail, on ? "Not found." : "Context is not set up here");
  }
});

test("Context developer guide links to both proxied references", async () => {
  const html = '<a href="/context" target="_top">Home</a><a href="/docs" target="_top">Swagger API reference</a><a href="/redoc" target="_top">ReDoc API reference</a>';
  const guide = await call({ request: async () => new Response(html, { headers: { "content-type": "text/html" } }) }, "/onboarding-guide?role=developer");
  assert.match(guide.text, /href="\/api\/context\/docs"/);
  assert.match(guide.text, /href="\/api\/context\/redoc"/);
});

for (const [path, body] of [
  ["/onboard/accept", { unit: "leasing", items: "all" }],
  ["/onboard/certify", { proposal: "proposal-1", items: "all" }],
  ["/onboard/run", { step: "checks" }],
  ["/onboard/run", { step: "sign-off" }],
  ["/onboard/run", { step: "release" }],
  ["/onboard/run", { step: "release", hash: "previous" }],
  ["/onboard/run", { step: "improve" }],
  ["/onboard/rollback", {}],
]) {
  test(`React decision ${path} ${body.step || ""} sends one untrusted request with the person's credentials`, async () => {
    const wire = JSON.stringify(body);
    const headers = { authorization: "Bearer person", cookie: "kge_session=person", "content-type": "application/json", "content-length": String(Buffer.byteLength(wire)) };
    let calls = 0;
    const result = await call({ request: async (req, target, options) => {
      calls += 1;
      assert.equal(target, path);
      assert.equal(req.headers, headers);
      assert.equal(options.trusted, false);
      let forwarded = "";
      for await (const chunk of options.body) forwarded += chunk;
      assert.deepEqual(JSON.parse(forwarded), body);
      return json({ said: "Recorded by the signed-in person and role." }, 202);
    } }, path, headers, { method: "POST", body: wire });
    assert.equal(calls, 1);
    assert.equal(result.status, 202);
  });
}

for (const path of ["/golden", "/ab", "/versions", "/onboard", "/steward/queue", "/steward/queue?status=decided"]) {
  test(`React resource ${path} preserves the server's role and access filtering`, async () => {
    const headers = { authorization: "Bearer reader" };
    for (const status of [200, 403]) {
      let count = 0;
      const value = status === 200 ? { readable: "only" } : { detail: "This role may not read that view." };
      const result = await call({ request: async (req, target, options) => {
        count += 1;
        assert.equal(target, path); assert.equal(req.headers, headers); assert.equal(options.trusted, false);
        return json(value, status);
      } }, path, headers);
      assert.equal(result.status, status); assert.deepEqual(result.json(), value); assert.equal(count, 1);
    }
  });
}

test("Context streaming proxy destroys upstream body when client closes response early", async () => {
  let upstreamCancelled = false;
  let chunksPulled = 0;
  const stream = new ReadableStream({
    pull(controller) {
      chunksPulled += 1;
      controller.enqueue(new TextEncoder().encode(`{"event":"chunk","n":${chunksPulled}}\n`));
    },
    cancel() {
      upstreamCancelled = true;
    },
  });
  const kge = {
    request: async () => new Response(stream, {
      status: 200,
      headers: { "content-type": "application/x-ndjson" },
    }),
  };
  let seen = 0;
  const responseHeaders = new Headers();
  const res = new Writable({
    write(_chunk, _enc, done) {
      seen += 1;
      done();
      if (seen === 2) {
        res.destroy();
      }
    },
  });
  res.removeHeader = (key) => responseHeaders.delete(key);
  res.status = (status) => { res.statusCode = status; return res; };
  res.set = (key, value) => { responseHeaders.set(key, value); return res; };
  res.append = (key, value) => responseHeaders.append(key, value);
  res.json = (body) => res.end(JSON.stringify(body));
  res.send = (body) => res.end(body);
  const req = Object.assign(Readable.from([JSON.stringify({ question: "Q?" })]), {
    url: "/ask",
    method: "POST",
    headers: { accept: "application/x-ndjson", "content-type": "application/json" },
    socket: { remoteAddress: "192.0.2.1" },
  });
  await contextProxy(kge)(req, res, (error) => { throw error; });
  await new Promise((resolve) => setTimeout(resolve, 30));
  assert.equal(seen, 2);
  assert.equal(upstreamCancelled, true);
});

test("Context streaming proxy destroys upstream body when client closes before headers arrive", async () => {
  let upstreamCancelled = false;
  let chunksPulled = 0;
  const stream = new ReadableStream({
    pull(controller) {
      chunksPulled += 1;
      controller.enqueue(new TextEncoder().encode(`{"event":"chunk","n":${chunksPulled}}\n`));
    },
    cancel() {
      upstreamCancelled = true;
    },
  });
  const responseHeaders = new Headers();
  const res = new Writable({
    write(_chunk, _enc, done) {
      done();
    },
  });
  res.removeHeader = (key) => responseHeaders.delete(key);
  res.status = (status) => { res.statusCode = status; return res; };
  res.set = (key, value) => { responseHeaders.set(key, value); return res; };
  res.append = (key, value) => responseHeaders.append(key, value);
  res.json = (body) => res.end(JSON.stringify(body));
  res.send = (body) => res.end(body);
  const kge = {
    request: async () => {
      res.destroy();
      await new Promise((resolve) => setTimeout(resolve, 20));
      return new Response(stream, {
        status: 200,
        headers: { "content-type": "application/x-ndjson" },
      });
    },
  };
  const req = Object.assign(Readable.from([JSON.stringify({ question: "Q?" })]), {
    url: "/ask",
    method: "POST",
    headers: { accept: "application/x-ndjson", "content-type": "application/json" },
    socket: { remoteAddress: "192.0.2.1" },
  });
  await contextProxy(kge)(req, res, (error) => { throw error; });
  await new Promise((resolve) => setTimeout(resolve, 30));
  assert.equal(upstreamCancelled, true);
});


for (const transport of ["loopback", "fetch"]) {
  test(`Context Stop aborts ${transport} upstream before its 300 ms response headers`, { timeout: 3000 }, async (t) => {
    let sawRequest;
    const requested = new Promise((resolve) => { sawRequest = resolve; });
    let sawAbort;
    const aborted = new Promise((resolve) => { sawAbort = resolve; });
    let headersSent = false;
    const upstream = http.createServer((req, res) => {
      sawRequest();
      const delayed = setTimeout(() => {
        headersSent = true;
        res.writeHead(200, { "content-type": "application/x-ndjson" });
        res.end('{"event":"answer"}\n');
      }, 300);
      res.once("close", () => {
        clearTimeout(delayed);
        if (!res.writableFinished) sawAbort();
      });
    });
    const listen = (server) => new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
    const close = (server) => new Promise((resolve) => { server.closeAllConnections(); server.close(resolve); });
    await listen(upstream);
    t.after(() => close(upstream));
    const baseUrl = `http://127.0.0.1:${upstream.address().port}`;
    const proxy = http.createServer(contextProxy(kgeClient(baseUrl), transport === "loopback" ? baseUrl : undefined));
    await listen(proxy);
    t.after(() => close(proxy));
    const client = http.request(`http://127.0.0.1:${proxy.address().port}/ask`, { method: "POST" });
    client.on("error", () => {});
    client.end();
    await requested;
    await new Promise((resolve) => setTimeout(resolve, 100));
    client.destroy();
    await aborted;
    assert.equal(headersSent, false);
  });
}

test("Context relays a background question's live lines as they arrive, the run line before the upstream ends", async () => {
  let release;
  const held = new Promise((resolve) => { release = resolve; });
  const encoder = new TextEncoder();
  const stream = new ReadableStream({
    async start(controller) {
      controller.enqueue(encoder.encode('{"event":"run","id":"r","state":"running","cursor":0}\n'));
      controller.enqueue(encoder.encode('{"event":"stage","stage":"understand","state":"running","cursor":1}\n'));
      await held;
      controller.enqueue(encoder.encode('{"event":"answer","answer":{"figure":10}}\n'));
      controller.close();
    },
  });
  const kge = {
    request: async (_req, path) => {
      seenPath = path;
      return new Response(stream, { status: 202, headers: { "content-type": "application/x-ndjson", "x-accel-buffering": "no", "cache-control": "no-store" } });
    },
  };
  const written = [];
  let beforeEnd = null;
  let seenPath = null;
  const responseHeaders = new Headers();
  const res = new Writable({
    write(chunk, _enc, done) {
      written.push(chunk.toString());
      if (beforeEnd === null && written.join("").split("\n").filter(Boolean).length >= 2) {
        beforeEnd = written.join("");
        release();
      }
      done();
    },
  });
  res.removeHeader = (key) => responseHeaders.delete(key);
  res.status = (status) => { res.statusCode = status; return res; };
  res.set = (key, value) => { responseHeaders.set(key, value); return res; };
  res.append = (key, value) => responseHeaders.append(key, value);
  res.json = (body) => res.end(JSON.stringify(body));
  res.send = (body) => res.end(body);
  const req = Object.assign(Readable.from([JSON.stringify({ question: "Q?" })]), {
    url: "/ask?background=true",
    method: "POST",
    headers: { accept: "application/x-ndjson", "content-type": "application/json" },
    socket: { remoteAddress: "192.0.2.1" },
  });
  await contextProxy(kge)(req, res, (error) => { throw error; });
  await finished(res);
  assert.equal(res.statusCode, 202);
  assert.equal(seenPath, "/ask?background=true");
  assert.equal(responseHeaders.get("x-accel-buffering"), "no");
  const lines = beforeEnd.split("\n").filter(Boolean).map((line) => JSON.parse(line));
  assert.deepEqual(lines.map((line) => line.event), ["run", "stage"]);
  assert.match(written.join(""), /"event":"answer"/);
});
