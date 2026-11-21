// Cerebro's agent tools (cerebro.md §4.4): KGE's own MCP tools, called as the signed-in person. Once per agent turn the
// portal exchanges the person's session for their own short-lived bearer (KGE POST /session/bearer, the trusted portal
// alone) and sends it in x-kge-authorization to KGE's MCP mounts; it never reaches the browser, the model or a log.
// The tools follow the matrix: every Cerebro role gets ask, get_context, the graph read, explore and explore_question,
// and suggest and feedback where TOOLS declares them; retrieve, map_term, search and assist are retrieval-side, engineers only.

const text = (description) => ({ type: "string", description });
const object = (properties, required = []) => ({ type: "object", properties, required, additionalProperties: false });

// Each tool KGE serves, its MCP mount and the arguments the model may pass (KGE refuses any other argument).
export const TOOLS = {
  ask: { mount: "/mcp/answer", description: "Answer a business question with governed figures. Never compute or change a figure.", parameters: object({ question: text("The person's question, in their words") }, ["question"]) },
  get_context: { mount: "/mcp", description: "The governed context for a question: the terms, tables and documents it touches, with no figures.", parameters: object({ question: text("The question") }, ["question"]) },
  read_graph: { mount: "/mcp/graph", description: "A page of the knowledge graph the person may read: nodes and links, no figures.", parameters: object({ node: text("A node's id to centre on"), cursor: text("The next page's cursor"), limit: { type: "integer", minimum: 1, maximum: 500 } }) },
  explore: { mount: "/mcp/graph", description: "The neighbourhood of one node of the knowledge graph.", parameters: object({ node: text("The node's id") }, ["node"]) },
  explore_question: { mount: "/mcp/graph", description: "The part of the knowledge graph a question touches.", parameters: object({ question: text("The question") }, ["question"]) },
  retrieve: { mount: "/mcp", description: "Retrieve passages and definitions for a query (engineers).", parameters: object({ query: text("The query"), k: { type: "integer", minimum: 1, maximum: 20 } }, ["query"]) },
  map_term: { mount: "/mcp", description: "What a business term maps to (engineers).", parameters: object({ term: text("The term") }, ["term"]) },
  search: { mount: "/mcp/search", description: "Ranked knowledge hits for a query (engineers).", parameters: object({ query: text("The query") }, ["query"]) },
  assist: { mount: "/mcp/assist", description: "The assistant's answer path, as Discover's Ask (engineers).", parameters: object({ question: text("The question") }, ["question"]) },
};

// Per role (EM decisions, matrix §2.1). KGE serves MCP feedback on /mcp/answer (A1): the person's ✓/✗ on an answer's
// receipt, which the page sends, so TOOLS does not declare it and the model is never offered it. A tool allowed here
// but not declared in TOOLS is never offered to the model.
export const EVERY_ROLE = ["ask", "get_context", "read_graph", "explore", "explore_question", "suggest", "feedback"];
export const ENGINEER_ONLY = ["retrieve", "map_term", "search", "assist"];

export const isEngineer = (roles) => roles.includes("engineer");
export function allowedTools(roles) {
  return new Set(isEngineer(roles) ? [...EVERY_ROLE, ...ENGINEER_ONLY] : EVERY_ROLE);
}

/** The tool definitions the model is offered: the person's allow-list, of the tools KGE serves. */
export function toolDefinitions(allowed) {
  return Object.entries(TOOLS).filter(([name]) => allowed.has(name))
    .map(([name, tool]) => ({ type: "function", function: { name, description: tool.description, parameters: tool.parameters } }));
}

// An answer's plan, SQL and trace are the engineer's row of the matrix (§2.1, §4.4): for anyone else they never enter
// the conversation, so neither the model nor the page sees them. path is the structured query path,
// versions/agent/physical_tables the run's internals. history is not among them: it is KGE's SQL-free continuation
// contract (a compact answer keeps it for every role, as Context's Ask page gets it), and the page carries it back so
// the next ask can send it unchanged (#607).
const ENGINEER_FIELDS = ["sql", "reference_sql", "plan", "trace", "run_trace", "path", "versions", "agent", "physical_tables"];
export function forRole(reply, engineer) {
  if (engineer || !reply || typeof reply !== "object") return reply;
  const out = { ...reply };
  for (const field of ENGINEER_FIELDS) delete out[field];
  if (out.display && typeof out.display === "object") out.display = cardDisplay(out.display);
  if (Array.isArray(out.parts)) delete out.parts; // a refusal's parts are each unit's plan
  return out;
}

// A display block's queries and their checks hold each query's SQL: the engineer's, as the answer's own SQL is (#602).
// KGE already leaves them out of a compact answer's block; this keeps them out wherever the portal's role says so.
const DISPLAY_ENGINEER = ["queries", "checked"];
function cardDisplay(display) {
  const out = { ...display };
  for (const field of DISPLAY_ENGINEER) delete out[field];
  return out;
}

/**
 * The question that binds the meaning the person picked (#595): given the clarification the previous turn's ask gave
 * (its history's last entry holds the clarified question and its declared choices), and the question the agent now
 * asks, the clarified question with the choice in place of the unclear words. Only the clarified question restated
 * with one choice's label, or that label alone, is a pick (case, spacing and closing punctuation aside); any other
 * question is asked as it is. The history sent with it is the one every ask sends (the agent's historyOf); KGE checks
 * the history and the pick again, as any history.
 */
export function pickedMeaning(previous, question) {
  const entry = previous?.behaviour === "clarify" && Array.isArray(previous.history) ? previous.history.at(-1) : undefined;
  const choices = Array.isArray(entry?.clarification?.choices) ? entry.clarification.choices : [];
  if (!entry || typeof entry.question !== "string" || typeof question !== "string" || !choices.length) return undefined;
  const chars = Array.from(entry.question);
  const spans = (entry.clarification.spans ?? []).filter((s) => s && s.turn === entry.turn_id && Number.isInteger(s.start)
    && Number.isInteger(s.end) && s.start >= 0 && s.start < s.end && s.end <= chars.length).sort((a, b) => b.start - a.start);
  if (!spans.length) return undefined;
  const restated = (label) => spans.reduce((out, s) => [...out.slice(0, s.start), ...Array.from(label), ...out.slice(s.end)], chars).join("");
  const plain = (text) => text.toLowerCase().replace(/\s+/g, " ").trim().replace(/[\s?.!]+$/, "");
  const asked = plain(question);
  const named = choices.filter((c) => typeof c?.label === "string" && c.label.trim()
    && (plain(restated(c.label)) === asked || plain(c.label) === asked));
  return named.length === 1 ? restated(named[0].label) : undefined;
}

// #607: the pipeline events an ask's progress shows the page, live: the stages (KGE sends them to engineers alone, and
// the portal keeps them to engineers too) and the path's nodes as they are added and change state. Each is cut to the
// fields the progress line draws; the plan, the context and the answer are never forwarded.
const PROGRESS = new Set(["stage", "node_added", "state_changed"]);
const scalar = (value) => typeof value === "string" || typeof value === "number";
const fieldsOf = (value, fields) => Object.fromEntries(fields.filter((key) => scalar(value[key])).map((key) => [key, value[key]]));
export function progressEvent(event, engineer) {
  if (!event || typeof event !== "object" || !PROGRESS.has(event.event) || (event.event === "stage" && !engineer)) return undefined;
  const out = fieldsOf(event, ["event", "stage", "state", "id"]);
  if (event.node && typeof event.node === "object") out.node = fieldsOf(event.node, ["id", "label", "type", "state"]);
  return out;
}

const MODE_LABELS = { certified: "Certified", reading: "The model's reading", not_sure: "Not sure" };
/** The confirm pause's value: the figure-free question KGE asks the person, and the answer's label; nothing else. */
export function confirmValue(reply) {
  return { question: String(reply.answer_text ?? "Is this what you meant?"), label: MODE_LABELS[reply.answer_mode] ?? "The model's reading" };
}

/** The ask tool's content after the person's decision: the full answer once confirmed, the figure-free one otherwise. */
export function decided(reply, decision) {
  if (decision === "confirmed") {
    // The display block was built on the figure-free question, so the confirmed card shows the full text instead (#602).
    const { confirmed_text: full, display: _display, ...rest } = reply;
    return { ...rest, confirm_first: false, answer_text: typeof full === "string" ? full : reply.answer_text };
  }
  return { answer_mode: reply.answer_mode, answer_text: reply.answer_text, ...(reply.receipt !== undefined ? { receipt: reply.receipt } : {}), declined: true };
}

// What the portal adds to a call, never the model. Every ask requests the display block the card renders as Context's
// rich answer does (#602), and sends the conversation's history unchanged (#607). get_context is compact (#607): the
// definitions the card shows, with no SQL, plans or decision internals.
const EXTRAS = {
  ask: (history) => ({ display: true, ...(Array.isArray(history) ? { history } : {}) }),
  get_context: () => ({ compact: true }),
};

const argsFor = (name, args) => {
  const declared = TOOLS[name].parameters.properties;
  return Object.fromEntries(Object.entries(args && typeof args === "object" ? args : {}).filter(([key]) => key in declared));
};

/**
 * One turn's MCP client, for one person: the bearer is minted on the first tool call and reused for the rest of the
 * turn, then dropped with the turn. A call outside the allow-list never reaches KGE. call(name, args, { history,
 * progress }): an ask sends history as given, and hands each pipeline event KGE streams to progress as it arrives.
 */
export function kgeTools({ kge, req, allowed, signal }) {
  let bearer;
  let id = 0;
  return async function call(name, args, { history, progress } = {}) {
    if (!allowed.has(name) || !TOOLS[name]) return { error: `The ${name} tool is not available to you.` };
    bearer ??= kge.sessionBearer(req);
    let token;
    try { token = await bearer; } catch { return { error: "Your sign-in could not be passed to the knowledge graph. Sign in again." }; }
    const callId = (id += 1);
    // An ask asks for progress, so KGE answers it as an event stream of its pipeline's events, then the reply.
    const params = { name, arguments: { ...argsFor(name, args), ...EXTRAS[name]?.(history) }, ...(name === "ask" ? { _meta: { progressToken: callId } } : {}) };
    const body = JSON.stringify({ jsonrpc: "2.0", id: callId, method: "tools/call", params });
    // No cookie and no browser header: the bearer alone names the person, and the trusted caller token is not sent.
    let response;
    try {
      response = await kge.request({ headers: {}, traceContext: req.traceContext }, TOOLS[name].mount, {
        method: "POST", trusted: false, long: name === "ask" || name === "assist", signal, body,
        headers: { "content-type": "application/json", accept: "application/json, text/event-stream", "x-kge-authorization": `Bearer ${token}` },
      });
    } catch (error) {
      if (signal?.aborted) throw error;
      return { error: `The knowledge graph could not be reached for the ${name} call.` };
    }
    if (!response.ok) return { error: `The knowledge graph refused the ${name} call (${response.status}).` };
    let reply;
    if (/^text\/event-stream/i.test(response.headers.get("content-type") ?? "")) {
      // The stream is read after the response starts: a timeout or a dropped connection mid-answer is this call's
      // failure, not the turn's, unless the person stopped the turn.
      try {
        reply = await streamed(response, callId, (event) => progress?.(event));
      } catch (error) {
        if (signal?.aborted) throw error;
        return { error: `The knowledge graph could not be reached for the ${name} call.` };
      }
    } else {
      reply = await response.json();
    }
    const result = reply?.result;
    if (!result || result.isError) {
      const said = result?.content?.find?.((c) => c?.type === "text")?.text;
      return { error: typeof said === "string" ? said.slice(0, 500) : `The ${name} call failed.` };
    }
    if (result.structuredContent && typeof result.structuredContent === "object") return { value: result.structuredContent };
    const first = result.content?.find?.((c) => c?.type === "text")?.text;
    try { return { value: JSON.parse(first) }; } catch { return { value: { text: String(first ?? "") } }; }
  };
}

/**
 * A tools/call answered as MCP's event stream: each server-sent event's data is one JSON-RPC message. Progress
 * notifications carry a pipeline event as JSON in params.message, handed to onEvent as it arrives; the message with
 * the call's id is its reply, returned (undefined when the stream ends without one).
 */
async function streamed(response, id, onEvent) {
  const decoder = new TextDecoder();
  let buffer = "";
  let reply;
  const parse = (text) => { try { return JSON.parse(text); } catch { return undefined; } };
  const take = (frame) => {
    const message = parse(frame.split(/\r?\n/).filter((line) => line.startsWith("data:")).map((line) => line.slice(5).replace(/^ /, "")).join("\n"));
    if (message?.method === "notifications/progress") {
      const event = typeof message.params?.message === "string" ? parse(message.params.message) : undefined;
      if (event) onEvent(event);
    } else if (message?.id === id) {
      reply = message;
    }
  };
  for await (const chunk of response.body) {
    buffer += decoder.decode(chunk, { stream: true });
    const frames = buffer.split(/\r?\n\r?\n/);
    buffer = frames.pop();
    frames.forEach(take);
  }
  take(buffer + decoder.decode());
  return reply;
}
