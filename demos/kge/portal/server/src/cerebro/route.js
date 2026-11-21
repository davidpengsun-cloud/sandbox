// Cerebro's stream endpoint (cerebro.md §4.3), option B of the useStream check: the page's useStream hook runs on the
// hook's custom-backend adapter (FetchStreamTransport), which POSTs { input, command, config, context } to one URL and
// reads LangGraph's server-sent events back. No LangGraph server, no thread store: the page sends the conversation as
// input.messages with every request, a resume as command.resume beside it, and the turn runs from that alone.
//
// This module imports nothing, so the page's check test can drive the same handler. The agent itself is agent.js's
// cerebroAgent, injected as runTurn. app.js mounts this at POST /api/cerebro/stream behind the portal's sign-in and a
// Cerebro role, never open.

const MAX_MESSAGES = 200;
// The message types a page may send; the system prompt is the server's alone.
const PAGE_TYPES = ["human", "ai", "tool"];
// "Ask about this" sends the graph node the question is about as context.node, a graph read id `<kind>:<identifier>`
// (cerebro.md §4.3, §7.3), no longer than the page's address takes one.
const NODE_MAX = 512;
const NODE_ID = /^[a-z_]+:\S/;

/** The node a request's context names, trimmed: none when it names none or holds a character the prompt never takes,
 *  or a reason when it is not a node id (a kind, a colon and an identifier). */
function nodeOf(context) {
  if (!context || typeof context !== "object" || context.node === undefined || context.node === null) return {};
  const node = typeof context.node === "string" ? context.node.trim() : "";
  if (node.length > NODE_MAX || !NODE_ID.test(node)) return { error: `Send context.node as a node id, <kind>:<identifier>, of at most ${NODE_MAX} characters.` };
  // A control, format or line-separator character never enters the agent's prompt: the node is dropped and the question
  // is answered as it was before nodes were passed, from its text alone.
  return /[\p{Cc}\p{Cf}\p{Zl}\p{Zp}]/u.test(node) ? {} : { node };
}

/** One server-sent event in the framing the hook's SSE decoder reads. */
export function encodeEvent(event, data) {
  return `event: ${event}\ndata: ${JSON.stringify(data)}\n\n`;
}

/** The conversation and the resume a request carries, or a reason it carries neither. */
export function readTurn(body) {
  if (!body || typeof body !== "object") return { error: "Send a JSON body." };
  const messages = body.input?.messages;
  if (!Array.isArray(messages) || messages.length === 0) return { error: "Send the conversation as input.messages." };
  if (messages.length > MAX_MESSAGES) return { error: `Send at most ${MAX_MESSAGES} messages.` };
  if (!messages.every((m) => m && typeof m === "object" && typeof m.type === "string")) {
    return { error: "Each message needs a type." };
  }
  if (!messages.every((m) => PAGE_TYPES.includes(m.type))) return { error: "Each message is human, ai or tool." };
  const textOnly = (c) => typeof c === "string" || (Array.isArray(c) && c.every((b) => b && b.type === "text" && typeof b.text === "string"));
  if (!messages.every((m) => m.content === undefined || textOnly(m.content))) return { error: "A message carries text only." };
  const { node, error } = nodeOf(body.context);
  if (error) return { error };
  return { messages, resume: body.command?.resume, ...(node ? { node } : {}) };
}

/**
 * The handler. runTurn({ messages, resume, node, signal, req }) is an async iterable of [event, data] pairs: "values" with the
 * conversation so far, and "messages" with [part, metadata] for each streamed part (the hook merges parts only from
 * events of that name). A pause sends the state as "values" first, then the same state with __interrupt__.
 */
export function cerebroStream({ runTurn }) {
  return async (req, res) => {
    // The body is express.json's (app.js caps it); a body it did not parse (another content type) is refused, never read raw.
    const turn = readTurn(req.body);
    if (turn.error) {
      res.writeHead(400, { "content-type": "application/json", "cache-control": "no-store" });
      res.end(JSON.stringify({ detail: turn.error }));
      return;
    }
    const abort = new AbortController();
    res.on("close", () => abort.abort());
    res.writeHead(200, {
      "content-type": "text/event-stream",
      "cache-control": "no-store",
      "x-accel-buffering": "no",
    });
    try {
      res.write(encodeEvent("metadata", { run_id: globalThis.crypto.randomUUID() }));
      for await (const [event, data] of runTurn({ messages: turn.messages, resume: turn.resume, node: turn.node, signal: abort.signal, req })) {
        if (abort.signal.aborted) break;
        res.write(encodeEvent(event, data));
      }
    } catch (error) {
      // The reason stays in the server's log (its name and message, never the conversation); the page gets a fixed
      // sentence (no stack, no upstream text).
      if (!abort.signal.aborted) console.error(`Cerebro turn failed: ${error?.name || "Error"}: ${error?.message || ""}`);
      if (!abort.signal.aborted) res.write(encodeEvent("error", { error: "AgentError", message: "The agent could not answer." }));
    }
    res.end();
  };
}
