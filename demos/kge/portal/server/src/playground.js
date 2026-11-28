// Context's Playground (#623): one question sent to one of the tools Cerebro's agent uses, over KGE's own MCP mounts,
// as the signed-in person. The portal exchanges the person's session for their short-lived bearer (KGE POST
// /session/bearer, the trusted portal alone), as Cerebro's agent does, sends one tools/call, and gives the page the
// request exactly as sent (the bearer shown as <access_token>, never the token), the raw reply, the result for the
// person's role and the time the call took. Cerebro's tool list and role rule are imported, never changed here.
import express from "express";
import { notSetUp } from "./apps.js";
import { cerebroRoles } from "./cerebro/chat.js";
import { TOOLS, allowedTools, forRole, isEngineer } from "./cerebro/tools.js";

export const SHOWN_TOKEN = "<access_token>";
const MAX_QUESTION = 2000;
// The roles that may ask, as Cerebro's door and the page's Playground tab hold them (cerebro.md §2.1); the Playground
// says so in its own words, not Cerebro's.
const DOOR_ROLES = ["business", "steward", "engineer"];
export const NO_ROLE = "The Playground's tools need a business, steward or engineer role; Ask works as before.";

// The Playground's switch, in its order: each setting's tool and the arguments its one text box fills (owner's
// answers on #623, 2026-11-22: Graph is explore_question with the question, Map is map_term with the text as typed).
// What Cerebro's agent adds to a call the Playground adds too: Ask's display block, get_context's compact form.
export const PLAYGROUND = [
  { setting: "ask", label: "Ask", tool: "ask", args: (text) => ({ question: text, display: true }) },
  { setting: "context", label: "Context", tool: "get_context", args: (text) => ({ question: text, compact: true }) },
  { setting: "map", label: "Map", tool: "map_term", args: (text) => ({ term: text }) },
  { setting: "search", label: "Search", tool: "search", args: (text) => ({ query: text }) },
  { setting: "graph", label: "Graph", tool: "explore_question", args: (text) => ({ question: text }) },
  { setting: "retrieve", label: "Retrieve", tool: "retrieve", args: (text) => ({ query: text }) },
  { setting: "assist", label: "Assist", tool: "assist", args: (text) => ({ question: text }) },
];

/** The Playground settings a person with these Cerebro roles may use: retrieve and assist are the engineer's. */
export function playgroundSettings(roles) {
  const allowed = allowedTools(roles);
  return PLAYGROUND.filter((entry) => allowed.has(entry.tool) && TOOLS[entry.tool]);
}

/** The MCP request for one setting and text: the JSON-RPC body exactly as sent, the mount, and the headers an agent sends. */
export function mcpRequest(entry, text, id = 1, base = "") {
  const body = JSON.stringify({ jsonrpc: "2.0", id, method: "tools/call", params: { name: entry.tool, arguments: entry.args(text) } });
  const mount = TOOLS[entry.tool].mount;
  return {
    method: "POST",
    url: base ? new URL(mount.replace(/^\/+/, ""), `${base.replace(/\/+$/, "")}/`).href : mount,
    headers: { "content-type": "application/json", accept: "application/json, text/event-stream", authorization: `Bearer ${SHOWN_TOKEN}` },
    body,
  };
}

/** The JSON-RPC message with the call's id in a reply that is plain JSON or MCP's event stream. */
export function replyMessage(raw, id) {
  const parse = (value) => { try { return JSON.parse(value); } catch { return undefined; } };
  const whole = parse(raw);
  if (whole && typeof whole === "object") return whole;
  return String(raw).split(/\r?\n\r?\n/)
    .map((frame) => parse(frame.split(/\r?\n/).filter((line) => line.startsWith("data:")).map((line) => line.slice(5).replace(/^ /, "")).join("\n")))
    .find((message) => message?.id === id);
}

/** The tool's result in a JSON-RPC reply, or the error KGE or the protocol gave. */
function resultOf(message, tool) {
  if (!message || typeof message !== "object") return { error: `The knowledge graph gave no reply to the ${tool} call.` };
  const result = message.result;
  if (!result) return { error: typeof message.error?.message === "string" && message.error.message ? message.error.message.slice(0, 500) : `The ${tool} call failed.` };
  const said = result.content?.find?.((c) => c?.type === "text")?.text;
  if (result.isError) return { error: typeof said === "string" ? said.slice(0, 500) : `The ${tool} call failed.` };
  if (result.structuredContent && typeof result.structuredContent === "object") return { value: result.structuredContent };
  try { return { value: JSON.parse(said) }; } catch { return { value: { text: String(said ?? "") } }; }
}

/**
 * One Playground call: { setting, question } in, { setting, tool, request, reply, value | error, ms } out. request is
 * the call exactly as sent but for the bearer; reply is KGE's reply as it came, the raw text. value is the result as
 * the person's role may see it (Cerebro's forRole: plan and trace are the engineer's), which KGE's own compact form
 * already is for anyone else.
 */
export function playgroundCall({ kge, base }) {
  let sequence = 0;
  return async (req, res) => {
    const roles = cerebroRoles(req.portalIdentity);
    const setting = typeof req.body?.setting === "string" ? req.body.setting : "";
    const text = typeof req.body?.question === "string" ? req.body.question.trim() : "";
    const entry = playgroundSettings(roles).find((item) => item.setting === setting);
    if (!entry) return res.status(PLAYGROUND.some((item) => item.setting === setting) ? 403 : 400).json({ detail: `The ${setting || "unnamed"} tool is not available to you.` });
    if (!text) return res.status(400).json({ detail: "Send a question." });
    if (text.length > MAX_QUESTION) return res.status(400).json({ detail: `Send a question of at most ${MAX_QUESTION} characters.` });
    const id = (sequence += 1);
    const request = mcpRequest(entry, text, id, base);
    const shown = { setting, tool: entry.tool, request };
    const started = performance.now();
    const took = () => Math.max(1, Math.round(performance.now() - started));
    let token;
    try { token = await kge.sessionBearer(req); } catch {
      return res.status(502).json({ ...shown, error: "Your sign-in could not be passed to the knowledge graph. Sign in again.", ms: took() });
    }
    const controller = new AbortController();
    res.once?.("close", () => { if (!res.writableFinished) controller.abort(); });
    let response, raw;
    try {
      response = await kge.request({ headers: {}, traceContext: req.traceContext }, TOOLS[entry.tool].mount, {
        method: "POST", trusted: false, long: entry.tool === "ask" || entry.tool === "assist", signal: controller.signal, body: request.body,
        headers: { "content-type": request.headers["content-type"], accept: request.headers.accept, "x-kge-authorization": `Bearer ${token}` },
      });
      raw = await response.text();
    } catch {
      if (controller.signal.aborted) return undefined;
      return res.status(502).json({ ...shown, error: `The knowledge graph could not be reached for the ${entry.tool} call.`, ms: took() });
    }
    const ms = took();
    if (!response.ok) return res.json({ ...shown, reply: raw, error: `The knowledge graph refused the ${entry.tool} call (${response.status}).`, ms });
    const outcome = resultOf(replyMessage(raw, id), entry.tool);
    return res.json({ ...shown, reply: raw, ...(outcome.error ? { error: outcome.error } : { value: forRole(outcome.value, isEngineer(roles)) }), ms });
  };
}

/** GET /api/context/playground/tools and POST /api/context/playground/call: Context's flag and KGE, the portal's sign-in, a Cerebro role. */
export function playgroundRoutes({ kge, settings, identity, base }) {
  const router = express.Router();
  router.use((_req, res, next) => (kge ? next() : notSetUp(res, "context", settings)), identity, (req, res, next) => (cerebroRoles(req.portalIdentity).some((role) => DOOR_ROLES.includes(role))
    ? next() : res.status(403).json({ detail: NO_ROLE, message: NO_ROLE })));
  router.get("/tools", (req, res) => res.json({ base, settings: playgroundSettings(cerebroRoles(req.portalIdentity)).map(({ setting, label, tool }) => ({ setting, label, tool, mount: TOOLS[tool].mount })) }));
  router.post("/call", express.json({ limit: "16kb" }), playgroundCall({ kge, base }));
  router.use((_req, res) => res.status(404).json({ detail: "Not found." }));
  return router;
}
