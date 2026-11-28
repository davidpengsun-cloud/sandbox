import assert from "node:assert/strict";
import { createServer } from "node:http";
import { after, before, beforeEach, test } from "node:test";
import { createApp } from "../src/app.js";
import { openSqliteRepository } from "../src/repository/sqlite.js";
import { NO_ROLE, PLAYGROUND, SHOWN_TOKEN, mcpRequest, playgroundSettings, replyMessage } from "../src/playground.js";

// A fake KGE: /whoami names the cookie's Cerebro role, /session/bearer mints a bearer for the trusted portal alone,
// and the MCP mounts answer tools/call as KGE's stateless servers do; `mode` makes the next call fail one way.
const SYNC = "s".repeat(32);
let seen;
let mode;
beforeEach(() => { seen = { mints: [], mcp: [] }; mode = ""; });
const ANSWER = { behaviour: "figure", answer_mode: "certified", answer_text: "Certified: revenue was 42.", value: 42, sql: "SELECT 42", sql_certified: true, plan: { measure: "revenue" }, trace: ["t"], run_trace: { stages: [] } };
const kge = createServer(async (req, res) => {
  const send = (status, value, type = "application/json") => res.writeHead(status, { "content-type": type }).end(typeof value === "string" ? value : JSON.stringify(value));
  let body = "";
  for await (const chunk of req) body += chunk;
  const cookie = req.headers.cookie || "";
  if (req.url === "/whoami") return cookie ? send(200, { id: "person-1", cerebro_roles: [cookie.split("=")[1]].filter((r) => r !== "none") }) : send(401, {});
  if (req.url === "/readable-units") return send(200, { units: [] });
  if (req.url === "/session/bearer") {
    seen.mints.push({ cookie, sync: req.headers["x-portal-sync-token"] });
    if (mode === "no-bearer" || req.headers["x-portal-sync-token"] !== SYNC) return send(403, {});
    return send(200, { access_token: `minted-${seen.mints.length}`, token_type: "bearer", expires_in: 900 });
  }
  if (req.url.startsWith("/mcp")) {
    seen.mcp.push({ path: req.url, headers: req.headers, body });
    const call = JSON.parse(body);
    const name = call.params.name;
    if (mode === "refused") return send(500, { detail: "boom" });
    if (mode === "drop") return req.socket.destroy();
    if (mode === "protocol") return send(200, { jsonrpc: "2.0", id: call.id, error: { code: -32602, message: "Unknown argument." } });
    if (mode === "tool-error") return send(200, { jsonrpc: "2.0", id: call.id, result: { content: [{ type: "text", text: "403: You may not read that unit." }], isError: true } });
    if (mode === "nothing") return send(200, "not json at all", "text/plain");
    if (mode === "text") return send(200, { jsonrpc: "2.0", id: call.id, result: { content: [{ type: "text", text: "plain words" }], isError: false } });
    const value = name === "ask" ? ANSWER : { tool: name, arguments: call.params.arguments };
    const message = { jsonrpc: "2.0", id: call.id, result: { content: [{ type: "text", text: JSON.stringify(value) }], ...(mode === "unstructured" ? {} : { structuredContent: value }), isError: false } };
    if (mode === "stream") return send(200, `event: message\ndata: ${JSON.stringify({ jsonrpc: "2.0", method: "notifications/progress", params: { progress: 1 } })}\n\nevent: message\ndata: ${JSON.stringify(message)}\n\n`, "text/event-stream");
    return send(200, message);
  }
  return send(404, {});
});
const servers = [];
let kgeUrl;
before(async () => { await new Promise((resolve) => kge.listen(0, "127.0.0.1", resolve)); kgeUrl = `http://127.0.0.1:${kge.address().port}`; });
after(() => { kge.close(); for (const server of servers) server.close(); });

async function start(env = {}) {
  const repository = openSqliteRepository({ PORTAL_SQLITE_PATH: ":memory:" });
  const { app } = createApp({ repository, env: { KGE_URL: kgeUrl, PORTAL_SYNC_TOKEN: SYNC, ...env }, serviceAuth: { headers: async () => ({}) }, catalogueSync: false, uiPath: "not-present", tracing: null });
  const server = app.listen(0, "127.0.0.1");
  servers.push(server);
  await new Promise((resolve) => server.once("listening", resolve));
  return `http://127.0.0.1:${server.address().port}`;
}
const call = (base, role, body) => fetch(`${base}/api/context/playground/call`, { method: "POST", headers: { "content-type": "application/json", ...(role ? { cookie: `kge_session=${role}` } : {}) }, body: JSON.stringify(body) });
const tools = async (base, role) => (await (await fetch(`${base}/api/context/playground/tools`, { headers: { cookie: `kge_session=${role}` } })).json()).settings.map((s) => s.setting);

test("the switch holds Ask, Context, Map, Search and Graph for every role, and Retrieve and Assist for engineers alone", async () => {
  const base = await start();
  for (const role of ["business", "steward"]) assert.deepEqual(await tools(base, role), ["ask", "context", "map", "search", "graph"], role);
  assert.deepEqual(await tools(base, "engineer"), ["ask", "context", "map", "search", "graph", "retrieve", "assist"]);
  const listed = await (await fetch(`${base}/api/context/playground/tools`, { headers: { cookie: "kge_session=business" } })).json();
  assert.equal(listed.base, kgeUrl, "the MCP base an agent calls, for the page's agent example");
  assert.deepEqual(listed.settings[0], { setting: "ask", label: "Ask", tool: "ask", mount: "/mcp/answer" });
  assert.deepEqual(playgroundSettings(["business"]).map((s) => s.tool), ["ask", "get_context", "map_term", "search", "explore_question"]);
  const response = await call(base, "business", { setting: "assist", question: "q" });
  assert.equal(response.status, 403);
  assert.equal(seen.mcp.length, 0, "a tool outside the person's list never reaches KGE");
});

test("each setting calls its tool with the text, and the request shown is the one sent, the bearer shown as a placeholder", async () => {
  const base = await start();
  const expected = { ask: ["/mcp/answer", { question: "Revenue in March?", display: true }], context: ["/mcp", { question: "Revenue in March?", compact: true }],
    map: ["/mcp", { term: "Revenue in March?" }], search: ["/mcp/search", { query: "Revenue in March?" }], graph: ["/mcp/graph", { question: "Revenue in March?" }],
    retrieve: ["/mcp", { query: "Revenue in March?" }], assist: ["/mcp/assist", { question: "Revenue in March?" }] };
  for (const entry of PLAYGROUND) {
    seen.mcp = [];
    const response = await call(base, "engineer", { setting: entry.setting, question: "  Revenue in March?  " });
    assert.equal(response.status, 200, entry.setting);
    const text = await response.text();
    const out = JSON.parse(text);
    assert.equal(seen.mcp.length, 1);
    const sent = seen.mcp[0];
    assert.equal(sent.path, expected[entry.setting][0]);
    assert.equal(out.request.body, sent.body, "the body shown is byte for byte the body sent");
    const rpc = JSON.parse(sent.body);
    assert.equal(rpc.method, "tools/call");
    assert.equal(rpc.params.name, entry.tool);
    assert.deepEqual(rpc.params.arguments, expected[entry.setting][1]);
    assert.equal(out.request.url, `${kgeUrl}${expected[entry.setting][0]}`);
    assert.equal(out.request.headers.authorization, `Bearer ${SHOWN_TOKEN}`);
    assert.match(sent.headers["x-kge-authorization"], /^Bearer minted-\d+$/);
    assert.equal(sent.headers.cookie, undefined);
    assert.doesNotMatch(text, /minted-/, "the bearer never reaches the page");
    assert.equal(JSON.parse(out.reply).id, rpc.id, "the raw reply is KGE's");
    assert.ok(out.ms >= 1);
    assert.equal(out.tool, entry.tool);
  }
});

test("an engineer's Ask keeps plan and trace; a business user's has its SQL and neither", async () => {
  const base = await start();
  const engineer = await (await call(base, "engineer", { setting: "ask", question: "Revenue?" })).json();
  assert.deepEqual(engineer.value.plan, { measure: "revenue" });
  assert.deepEqual(engineer.value.trace, ["t"]);
  const business = await (await call(base, "business", { setting: "ask", question: "Revenue?" })).json();
  assert.equal(business.value.sql, "SELECT 42");
  assert.equal(business.value.sql_certified, true);
  assert.equal(business.value.plan, undefined);
  assert.equal(business.value.trace, undefined);
  assert.equal(business.value.run_trace, undefined);
});

test("a streamed reply, an unstructured one and plain text all give the result", async () => {
  const base = await start();
  mode = "stream";
  const streamed = await (await call(base, "steward", { setting: "search", question: "branch" })).json();
  assert.deepEqual(streamed.value, { tool: "search", arguments: { query: "branch" } });
  assert.match(streamed.reply, /^event: message/);
  mode = "unstructured";
  assert.deepEqual((await (await call(base, "steward", { setting: "map", question: "NNA" })).json()).value, { tool: "map_term", arguments: { term: "NNA" } });
  mode = "text";
  assert.deepEqual((await (await call(base, "steward", { setting: "map", question: "NNA" })).json()).value, { text: "plain words" });
  assert.equal(replyMessage("event: message\ndata: {\"id\":9}\n\n", 2), undefined);
});

test("each failure is the call's error with its time, never a crash", async () => {
  const base = await start();
  const cases = { refused: [200, /refused the search call \(500\)/], protocol: [200, /Unknown argument/], "tool-error": [200, /may not read that unit/],
    nothing: [200, /no reply/], drop: [502, /could not be reached/], "no-bearer": [502, /Sign in again/] };
  for (const [name, [status, said]] of Object.entries(cases)) {
    mode = name;
    const response = await call(base, "business", { setting: "search", question: "branch" });
    assert.equal(response.status, status, name);
    const out = await response.json();
    assert.match(out.error, said, name);
    assert.equal(out.value, undefined, name);
    assert.ok(out.ms >= 1, name);
    assert.equal(out.request.headers.authorization, `Bearer ${SHOWN_TOKEN}`, name);
  }
});

test("the route refuses what it cannot send: no sign-in, no Cerebro role, an unknown setting, an empty or a long question; Context off is not set up", async () => {
  const base = await start();
  assert.equal((await call(base, "", { setting: "search", question: "q" })).status, 401);
  const noRole = await call(base, "none", { setting: "search", question: "q" });
  assert.equal(noRole.status, 403);
  assert.equal((await noRole.json()).detail, NO_ROLE, "the Playground's own words, not Cerebro's");
  assert.equal((await call(base, "business", { setting: "read_graph", question: "q" })).status, 400);
  assert.equal((await call(base, "business", { question: "q" })).status, 400);
  assert.equal((await call(base, "business", { setting: "search", question: "  " })).status, 400);
  assert.equal((await call(base, "business", { setting: "search", question: "x".repeat(2001) })).status, 400);
  assert.equal((await fetch(`${base}/api/context/playground/other`, { headers: { cookie: "kge_session=business" } })).status, 404, "nothing under the Playground reaches Context's proxy");
  assert.equal(seen.mcp.length, 0);
  const off = await start({ PORTAL_APP_CONTEXT: "off" });
  const response = await call(off, "business", { setting: "search", question: "q" });
  assert.equal(response.status, 404);
  assert.match((await response.json()).detail, /not set up/);
});

test("mcpRequest without a base names the mount alone", () => {
  assert.equal(mcpRequest(PLAYGROUND[0], "q").url, "/mcp/answer");
});
