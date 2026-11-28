// Context's Playground (#623): the switch's settings, one call of a tool through the portal's Playground route, and
// what the right panel shows of a reply: its text, sources, the graph slice it touched, its time, and the call as an
// agent makes it. Pure functions but `callTool`, which the page and its tests drive.
import { stripAnswerLabel } from "./contextState";

export const SHOWN_TOKEN = "<access_token>";

/** The settings the person may use, from GET /api/context/playground/tools, with the MCP base an agent calls. */
export async function loadTools(fetcher = fetch, signal) {
  const response = await fetcher("/api/context/playground/tools", { signal, headers: { accept: "application/json" } });
  const body = await response.json().catch(() => ({}));
  if (!response.ok) return { error: body.detail || "The Playground's tools are unavailable right now.", settings: [] };
  return { base: typeof body.base === "string" ? body.base : "", settings: Array.isArray(body.settings) ? body.settings : [] };
}

/** One tool call: { setting, tool, request, reply, value | error, ms }, an error of its own where the portal refused. */
// KGE lets one person have MAX_IN_FLIGHT (4) work requests at once; an engineer's Try all starts five that take a slot
// (ask, get_context, search, retrieve, assist), and an Ask streaming beside it takes one more. A call KGE turns away as
// busy (429) is sent again, the same request, after a short wait, so every column still starts at once and ends with
// a reply; the column shows the call that answered.
export const BUSY_WAITS_MS = [500, 1000, 2000, 4000];
export const busyRefusal = (result) => typeof result?.error === "string" && (/^429\b/.test(result.error) || /\(429\)\.?$/.test(result.error));
const pause = (ms, signal) => new Promise((resolve) => {
  if (signal?.aborted) return resolve();
  const timer = setTimeout(resolve, ms);
  signal?.addEventListener?.("abort", () => { clearTimeout(timer); resolve(); }, { once: true });
});

export async function callTool(setting, question, { fetcher = fetch, signal, waits = BUSY_WAITS_MS } = {}) {
  let result = await callOnce(setting, question, { fetcher, signal });
  for (const wait of waits) {
    if (result.cancelled || !busyRefusal(result)) break;
    await pause(wait, signal);
    if (signal?.aborted) return { setting, cancelled: true };
    result = await callOnce(setting, question, { fetcher, signal });
  }
  return result;
}

async function callOnce(setting, question, { fetcher, signal }) {
  const started = typeof performance !== "undefined" ? performance.now() : Date.now();
  try {
    const response = await fetcher("/api/context/playground/call", { method: "POST", signal, headers: { "content-type": "application/json", accept: "application/json" }, body: JSON.stringify({ setting, question }) });
    const body = await response.json().catch(() => ({}));
    if (body && typeof body === "object" && body.request) return body;
    return { setting, error: body?.detail || `The ${setting} call could not be made (${response.status}).`, ms: Math.round((typeof performance !== "undefined" ? performance.now() : Date.now()) - started) };
  } catch (error) {
    if (signal?.aborted) return { setting, cancelled: true };
    return { setting, error: "The Playground could not be reached. Try again shortly." };
  }
}

export const timeWords = (ms) => !(Number(ms) > 0) ? "" : ms < 1000 ? `${Math.round(ms)} ms` : `${(ms / 1000).toFixed(1)} s`;
const list = (value) => (Array.isArray(value) ? value : []);
const words = (value) => (typeof value === "string" ? value : value == null ? "" : String(value));

/** The reply in words, per tool; never more than the reply holds. */
export function replyText(tool, value) {
  if (!value || typeof value !== "object") return "";
  if (tool === "ask" || tool === "assist") return stripAnswerLabel(words(value.answer_text ?? value.reason), value.answer_mode);
  if (tool === "get_context") return words(value.block);
  if (tool === "map_term") return [value.term && `${value.term}${value.definition ? `: ${value.definition}` : ""}`, value.steward && `Steward: ${value.steward}`].filter(Boolean).join("\n");
  if (tool === "search") return list(value.hits).length ? `${list(value.hits).length} hit${list(value.hits).length === 1 ? "" : "s"}` : "No hits.";
  if (tool === "explore_question") return words(value.summary);
  if (tool === "retrieve") return list(value.exact).length ? `${list(value.exact).length} passage${list(value.exact).length === 1 ? "" : "s"}` : "No passages.";
  return words(value.text);
}

const sourceTitle = (s) => (typeof s === "string" ? s : words(s?.title || s?.name || s?.doc || s?.id || s?.label));
/** The reply's sources, each a title and, where the reply says, its kind, unit and why. */
export function sourcesOf(tool, value) {
  if (!value || typeof value !== "object") return [];
  const of = (items, kind) => list(items).map((s) => ({ title: sourceTitle(s), kind: words(s?.kind || kind), unit: words(s?.unit), why: words(s?.why?.text ?? s?.why ?? s?.match ?? s?.cited_as) })).filter((s) => s.title);
  if (tool === "ask") return of(value.citations, "document");
  if (tool === "assist") return of(value.sources);
  if (tool === "get_context") return of(value.sources);
  if (tool === "map_term") return [...of(value.defined_by, "document"), ...of(list(value.data_sets).map((d) => ({ title: d.unit_label || d.unit, unit: d.unit })), "data set")];
  if (tool === "search") return of(value.hits);
  if (tool === "retrieve") return of(value.exact, "passage");
  return [];
}

/** The graph slice a reply touched: the nodes it names, each a kind and a name. */
export function graphSliceOf(tool, value) {
  if (!value || typeof value !== "object") return [];
  const node = (kind, name) => (name ? [{ kind, name: words(name) }] : []);
  if (tool === "ask") return list(value.graph_path?.nodes || value.path?.nodes).map((n) => ({ kind: words(n.type || n.kind || "node"), name: words(n.label || n.name || n.id) })).filter((n) => n.name);
  if (tool === "explore_question") return [...(value.node ? node(value.node.kind, value.node.name || value.node.id) : []),
    ...list(value.paths).flatMap((p) => [...node("term", p.term), ...node("context", p.context), ...node("fact", p.fact),
      ...node("measure", typeof p.measure === "object" ? p.measure?.name || p.measure?.label : p.measure)])];
  if (tool === "map_term") return [...node("term", value.term), ...list(value.contexts).flatMap((c) => node("context", c.label || c.id))];
  if (tool === "get_context") return Object.keys(value.defines && typeof value.defines === "object" ? value.defines : {}).map((name) => ({ kind: "term", name }));
  return [];
}

const quoted = (text) => `'${String(text).replace(/'/g, "'\\''")}'`;
/** The call as an agent makes it: the token step, the same request as one curl line, and a short Python example. */
export function agentCall(request, base = "") {
  const rpc = JSON.parse(request.body);
  const url = /^https?:/i.test(request.url) ? request.url : `${base.replace(/\/+$/, "") || "<kge>"}${request.url}`;
  const root = url.replace(/\/mcp(\/[^/]*)?$/, "");
  const token = `curl -s -X POST ${quoted(`${root}/session/token`)} -H 'Content-Type: application/json' -d '{"username":"<you>","password":"<password>"}'`;
  const curl = `curl -s -X POST ${quoted(url)} -H ${quoted(`Authorization: Bearer ${SHOWN_TOKEN}`)} -H 'Content-Type: application/json' -H ${quoted(`Accept: ${request.headers.accept}`)} -d ${quoted(request.body)}`;
  const python = [
    "import asyncio",
    "from mcp import ClientSession",
    "from mcp.client.streamable_http import streamablehttp_client",
    "",
    "async def main():",
    `    headers = {"Authorization": "Bearer ${SHOWN_TOKEN}"}  # access_token from POST /session/token`,
    `    async with streamablehttp_client(${JSON.stringify(url)}, headers=headers) as (read, write, _):`,
    "        async with ClientSession(read, write) as session:",
    "            await session.initialize()",
    `            result = await session.call_tool(${JSON.stringify(rpc.params.name)}, ${pythonLiteral(rpc.params.arguments)})`,
    "            print(result.structuredContent)",
    "",
    "asyncio.run(main())",
  ].join("\n");
  return { url, token, curl, python };
}
/** A JSON value written as Python: true, false and null as True, False and None. */
export function pythonLiteral(value) {
  if (value === null || value === undefined) return "None";
  if (value === true) return "True";
  if (value === false) return "False";
  if (Array.isArray(value)) return `[${value.map(pythonLiteral).join(", ")}]`;
  if (typeof value === "object") return `{${Object.entries(value).map(([k, v]) => `${JSON.stringify(k)}: ${pythonLiteral(v)}`).join(", ")}}`;
  return JSON.stringify(value);
}

/** The MCP ask an agent sends for a question Context's Ask asked over HTTP: its equivalent, not the request the page sent. */
export function askEquivalent(question, history, mount = "/mcp/answer") {
  const args = { question, display: true, ...(Array.isArray(history) && history.length ? { history } : {}) };
  return { method: "POST", url: mount, headers: { "content-type": "application/json", accept: "application/json, text/event-stream", authorization: `Bearer ${SHOWN_TOKEN}` },
    body: JSON.stringify({ jsonrpc: "2.0", id: 1, method: "tools/call", params: { name: "ask", arguments: args } }) };
}
