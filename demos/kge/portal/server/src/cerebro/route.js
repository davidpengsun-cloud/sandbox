// Cerebro's stream endpoint (cerebro.md §4.3), option B of the useStream check: the page's useStream hook runs on the
// hook's custom-backend adapter (FetchStreamTransport), which POSTs { input, command, config, context } to one URL and
// reads LangGraph's server-sent events back. No LangGraph server: #625, the portal keeps each conversation itself
// (agent.js, conversations/), so the page sends only the person's new message as input.messages, or the person's answer
// to the HITL card as command.resume, and the conversation's id as config.configurable.thread_id; Edit or Try again on
// an earlier question names it as context.rewind. Nothing the page sends is read as an earlier answer.
//
// This module imports nothing, so the page's check test can drive the same handler. The agent itself is agent.js's
// cerebroAgent, injected as runTurn. app.js mounts this at POST /api/cerebro/stream behind the portal's sign-in and a
// Cerebro role, never open.

// A conversation's id is a UUID the page makes for a new chat; a message's id, a short plain string.
const CONVERSATION_ID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const MESSAGE_ID = /^[\w.:-]{1,128}$/;
// "Ask about this" sends the graph node the question is about as context.node, a graph read id `<kind>:<identifier>`
// (cerebro.md §4.3, §7.3), no longer than the page's address takes one.
const NODE_MAX = 512;
const NODE_ID = /^[a-z_]+:\S/;
// The person's own words on a HITL card (command.resume.other), at most this many characters.
export const OTHER_MAX = 2000;
// #713: the person's answer to a multiple-question card (command.resume { picks }): one pick per group, a group's id
// to an option's id, at most PICKS_MAX of them. Whether each pick is offered by the card the server saved in the
// conversation, and every group shown has one, is the agent's check (agent.js, questions.js).
export const PICKS_MAX = 6;
const PICK_ID = /^[\w.:-]{1,64}$/;
const picksOf = (resume) => {
  if (Object.keys(resume).some((key) => key !== "picks")) return false;
  const { picks } = resume;
  if (!picks || typeof picks !== "object" || Array.isArray(picks)) return false;
  const entries = Object.entries(picks);
  return entries.length > 0 && entries.length <= PICKS_MAX && entries.every(([group, option]) => PICK_ID.test(group) && typeof option === "string" && PICK_ID.test(option));
};

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

const textOnly = (c) => c === undefined || typeof c === "string" || (Array.isArray(c) && c.every((b) => b && b.type === "text" && typeof b.text === "string"));

/**
 * The turn a request asks for, or a reason it is refused: the conversation's id (thread), and either the person's one
 * new message (messages, with rewind where it names an earlier question to go back to) or their answer to the HITL card
 * (resume). Any other message, more than one, or a message that carries an earlier answer is refused.
 */
export function readTurn(body) {
  if (!body || typeof body !== "object") return { error: "Send a JSON body." };
  const thread = body.config?.configurable?.thread_id;
  if (typeof thread !== "string" || !CONVERSATION_ID.test(thread)) return { error: "Send the conversation's id, a UUID, as config.configurable.thread_id." };
  const resume = body.command && typeof body.command === "object" ? body.command.resume : undefined;
  const messages = body.input?.messages;
  const rewind = body.context && typeof body.context === "object" ? body.context.rewind : undefined;
  if (rewind !== undefined && (typeof rewind !== "string" || !MESSAGE_ID.test(rewind))) return { error: "Send context.rewind as the id of the question to go back to." };
  const { node, error } = nodeOf(body.context);
  if (error) return { error };
  if (resume !== undefined) {
    if (!(body.input === undefined || body.input === null || (Array.isArray(messages) && messages.length === 0))) return { error: "A resume carries no message." };
    if (rewind !== undefined) return { error: "A resume goes back to no question." };
    if (resume && typeof resume === "object" && "cancel" in resume
        && (resume.cancel !== true || Object.keys(resume).length !== 1)) {
      return { error: "Send cancellation alone as command.resume.cancel: true." };
    }
    if (resume && typeof resume === "object" && resume.other !== undefined && (typeof resume.other !== "string" || resume.other.length > OTHER_MAX)) {
      return { error: `Send command.resume.other as text of at most ${OTHER_MAX} characters.` };
    }
    if (resume && typeof resume === "object" && "picks" in resume && !picksOf(resume)) {
      return { error: `Send a multiple-question card's answer as command.resume { picks } alone: at most ${PICKS_MAX} picks, each a group's id and an option's id.` };
    }
    return { thread, resume, ...(node ? { node } : {}) };
  }
  if (!Array.isArray(messages) || messages.length !== 1) return { error: "Send the person's one new message as input.messages." };
  const [message] = messages;
  if (!message || typeof message !== "object" || message.type !== "human" || "tool_calls" in message || "tool_call_id" in message) {
    return { error: "Send only the person's new message: the conversation's earlier answers are the server's." };
  }
  if (!textOnly(message.content)) return { error: "A message carries text only." };
  if (message.id !== undefined && (typeof message.id !== "string" || !MESSAGE_ID.test(message.id))) return { error: "A message's id is a short plain string." };
  return { thread, messages: [message], ...(rewind !== undefined ? { rewind } : {}), ...(node ? { node } : {}) };
}

// #622: a turn that fails ends with a failed outcome, as every turn ends with one: the one runTurn put on the error,
// else the model's (or KGE's, where the error says so) with no detail.
const FAILED = {
  model_failed: { case: "model_failed", message: "The assistant couldn't respond.", action: "try_again" },
  kge_unreachable: { case: "kge_unreachable", message: "Couldn't reach the knowledge graph.", action: "try_again" },
  model_quota: { case: "model_quota", message: "You've reached the model usage limit. Please try again later.", action: "try_again" },
};
const failedOutcome = (error, steps) => (error?.outcome?.kind === "outcome" ? error.outcome
  : { kind: "outcome", outcome: "failed", steps, error: FAILED[error?.failure === "kge_unreachable" ? "kge_unreachable" : error?.failure === "model_quota" ? "model_quota" : "model_failed"] });

const refuse = (res, status, detail) => {
  res.writeHead(status, { "content-type": "application/json", "cache-control": "no-store" });
  res.end(JSON.stringify({ detail }));
};

/**
 * The handler. runTurn({ thread, messages, resume, rewind, node, signal, req }) is an async iterable of [event, data]
 * pairs: "values" with the whole saved conversation so far (its messages, title and trimmed count), "messages" with
 * [part, metadata] for each streamed part (the hook merges parts only from events of that name), and "custom" with the
 * working block's events, the last an outcome. A pause sends the state as "values" first, then the same state with
 * __interrupt__. A turn that refuses the request before its first event (error.status: 404 for a conversation that is
 * not the person's, 400 or 503) is answered with that status and { detail }, never a stream.
 */
export function cerebroStream({ runTurn }) {
  return async (req, res) => {
    // The body is express.json's (app.js caps it); a body it did not parse (another content type) is refused, never read raw.
    const turn = readTurn(req.body);
    if (turn.error) return refuse(res, 400, turn.error);
    const abort = new AbortController();
    res.on("close", () => abort.abort());
    const events = runTurn({ thread: turn.thread, messages: turn.messages, resume: turn.resume, rewind: turn.rewind, node: turn.node, signal: abort.signal, req })[Symbol.asyncIterator]();
    let next;
    try {
      next = await events.next();
    } catch (error) {
      const status = Number(error?.status);
      if (Number.isInteger(status) && status >= 400 && status < 600 && !error.outcome) return refuse(res, status, error.message);
      next = { error };
    }
    res.setHeader("Content-Type", "text/event-stream");
    res.writeHead(200, {
      "cache-control": "no-store",
      "x-accel-buffering": "no",
    });
    let steps = 0;
    try {
      res.write(encodeEvent("metadata", { run_id: globalThis.crypto.randomUUID() }));
      if (next.error) throw next.error;
      for (; !next.done; next = await events.next()) {
        if (abort.signal.aborted) break;
        const [event, data] = next.value;
        if (event === "custom" && data?.kind === "step") steps += 1;
        res.write(encodeEvent(event, data));
      }
      // A turn the page left is ended, so its own clean-up (its last save) runs.
      if (!next.done) await events.return?.();
    } catch (error) {
      // The reason stays in the server's log (its name and message, never the conversation); the page gets the failed
      // outcome's plain sentence (no stack; KGE's or the model's own words only as an engineer's detail).
      if (!abort.signal.aborted) console.error(`Cerebro turn failed: ${error?.name || "Error"}: ${error?.message || ""}`);
      if (!abort.signal.aborted) res.write(encodeEvent("custom", failedOutcome(error, steps)));
      // #651 A4: the failed turn's reply record, as runTurn kept it, right after its outcome.
      if (!abort.signal.aborted && error?.reply?.kind === "reply") res.write(encodeEvent("custom", error.reply));
    }
    res.end();
  };
}
