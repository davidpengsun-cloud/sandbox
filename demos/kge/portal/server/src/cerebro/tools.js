// Cerebro's agent tools (cerebro.md §4.4): KGE's own MCP tools, called as the signed-in person. Once per agent turn the
// portal exchanges the person's session for their own short-lived bearer (KGE POST /session/bearer, the trusted portal
// alone) and sends it in x-kge-authorization to KGE's MCP mounts; it never reaches the browser, the model or a log.
// The tools follow the matrix: every Cerebro role gets ask, get_context, the graph read, explore and explore_question,
// search and map_term (#622: KGE serves map_term's compact form to all but engineers), and suggest and feedback where
// TOOLS declares them; retrieve and assist are retrieval-side, engineers only.
import { failureOf } from "./status.js";

const text = (description) => ({ type: "string", description });
const object = (properties, required = []) => ({ type: "object", properties, required, additionalProperties: false });

// Each tool KGE serves, its MCP mount and the arguments the model may pass (KGE refuses any other argument).
export const TOOLS = {
  // #624 requirement 8: ask answers definitions and the difference between two declared terms too (#617).
  ask: { mount: "/mcp/answer", description: "Answer a business question with governed figures and definitions, including the difference between two declared terms. Never compute or change a figure.", parameters: object({ question: text("The person's question, in their words") }, ["question"]) },
  get_context: { mount: "/mcp", description: "The governed context for a question: the terms, tables and documents it touches, with no figures.", parameters: object({ question: text("The question") }, ["question"]) },
  read_graph: { mount: "/mcp/graph", description: "A page of the knowledge graph the person may read: nodes and links, no figures.", parameters: object({ node: text("A node's id to centre on"), cursor: text("The next page's cursor"), limit: { type: "integer", minimum: 1, maximum: 500 } }) },
  explore: { mount: "/mcp/graph", description: "The neighbourhood of one node of the knowledge graph.", parameters: object({ node: text("The node's id") }, ["node"]) },
  explore_question: { mount: "/mcp/graph", description: "The part of the knowledge graph a question touches.", parameters: object({ question: text("The question") }, ["question"]) },
  retrieve: { mount: "/mcp", description: "Retrieve passages and definitions for a query (engineers).", parameters: object({ query: text("The query"), k: { type: "integer", minimum: 1, maximum: 20 } }, ["query"]) },
  map_term: { mount: "/mcp", description: "A business term's data sets, documents, steward and contexts.", parameters: object({ term: text("The term") }, ["term"]) },
  search: { mount: "/mcp/search", description: "Ranked catalogue hits for a query: data products, metrics, terms and documents.", parameters: object({ query: text("The query") }, ["query"]) },
  assist: { mount: "/mcp/assist", description: "The assistant's answer path, as Discover's Ask (engineers).", parameters: object({ question: text("The question") }, ["question"]) },
};

// Per role (EM decisions, matrix §2.1). KGE serves MCP feedback on /mcp/answer (A1): the person's ✓/✗ on an answer's
// receipt, which the page sends, so TOOLS does not declare it and the model is never offered it. A tool allowed here
// but not declared in TOOLS is never offered to the model.
export const EVERY_ROLE = ["ask", "get_context", "read_graph", "explore", "explore_question", "search", "map_term", "suggest", "feedback"];
export const ENGINEER_ONLY = ["retrieve", "assist"];

export const isEngineer = (roles) => roles.includes("engineer");
export function allowedTools(roles) {
  return new Set(isEngineer(roles) ? [...EVERY_ROLE, ...ENGINEER_ONLY] : EVERY_ROLE);
}

/** The tool definitions the model is offered: the person's allow-list, of the tools KGE serves. */
export function toolDefinitions(allowed) {
  return Object.entries(TOOLS).filter(([name]) => allowed.has(name))
    .map(([name, tool]) => ({ type: "function", function: { name, description: tool.description, parameters: tool.parameters } }));
}

// An answer's plan and trace are the engineer's row of the matrix (§2.1, §4.4): for anyone else they never enter the
// conversation, so neither the model nor the page sees them. path is the structured query path,
// versions/agent/physical_tables the run's internals. #624 requirement 5: the SQL behind an answer (sql, a combined
// answer's figures' sql) and sql_certified go to every role who may ask; KGE's compact answer keeps them. history is not
// among them: it is KGE's SQL-free continuation contract (a compact answer keeps it for every role, as Context's Ask page
// gets it), and the saved conversation keeps it so the next ask can send it unchanged (#607, #625).
const ENGINEER_FIELDS = ["reference_sql", "plan", "trace", "run_trace", "path", "versions", "agent", "physical_tables", "failure_plain"];
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

// #607, #622: the pipeline events an ask's progress shows the page, live: the stages (KGE sends them to engineers alone,
// and the portal keeps them to engineers too) and the path's nodes and links as they are added and change state. Each
// is cut to the fields the progress line draws; the plan (it carries SQL), the context and the answer are never
// forwarded (the readout and the warehouse's state are status.js's own events).
const PROGRESS = new Set(["stage", "node_added", "state_changed", "edge_added"]);
const scalar = (value) => typeof value === "string" || typeof value === "number";
const fieldsOf = (value, fields) => Object.fromEntries(fields.filter((key) => scalar(value[key])).map((key) => [key, value[key]]));
export function progressEvent(event, engineer) {
  if (!event || typeof event !== "object" || !PROGRESS.has(event.event) || (event.event === "stage" && !engineer)) return undefined;
  const out = fieldsOf(event, ["event", "stage", "state", "id"]);
  if (event.node && typeof event.node === "object") out.node = fieldsOf(event.node, ["id", "label", "type", "state", "name"]);
  if (event.edge && typeof event.edge === "object") out.edge = fieldsOf(event.edge, ["from", "to", "type", "label", "state"]);
  return out;
}

// #624 requirement 4: the one HITL card, a portal-only tool the model may call when it cannot decide which question to
// send to ask. It never reaches KGE: the agent pauses the turn on it (agent.js choose) and gives the model the person's
// answer as its result.
export const CHOOSE = "choose";
export const CHOOSE_DEFINITION = {
  type: "function",
  function: {
    name: CHOOSE,
    description: "Ask the person to pick when you cannot decide which question to send to ask: one short question and two to four options, each a full question in the person's words; the person may also answer something else.",
    parameters: object({
      question: text("One short question to the person"),
      options: { type: "array", items: { type: "string" }, minItems: 2, maxItems: 4, description: "Two to four options, each a full question in the person's words" },
    }, ["question", "options"]),
  },
};

// What the portal adds to a call, never the model. Every ask requests the display block the card renders as Context's
// rich answer does (#602), and sends the conversation's history unchanged (#607). get_context is compact (#607): the
// definitions the card shows, with no SQL, plans or decision internals.
// #630: every ask runs in the background where KGE cannot answer it within QUESTION_START_WAIT, so a long warehouse
// question gives its run (agent.js backgroundOf) instead of holding the turn to PORTAL_ASK_TIMEOUT.
const EXTRAS = {
  ask: (history) => ({ display: true, background: true, ...(Array.isArray(history) ? { history } : {}) }),
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
 * It gives { value } or { error, failure }: failure is status.js's case where KGE could not be reached or hit a limit
 * (#622), and absent for a refusal the model may answer in words.
 * #630: call.questionRun(runId, after) and call.stopRun(runId) follow and stop a background run of the person's ask
 * (KGE's question_run and stop_question_run on /mcp/answer, never offered to the model; open wherever ask is), with
 * the same { value } or { error, failure }. #621: call.showcase() is the showcase questions KGE serves the person
 * (GET /golden), the list "Try one of these" shows, or [] where KGE does not give them.
 */
export function kgeTools({ kge, req, allowed, signal }) {
  let bearer;
  let id = 0;
  const token = async () => {
    bearer ??= kge.sessionBearer(req);
    return bearer;
  };
  const signInFailed = (error) => {
    const status = Number(error?.status);
    return { error: "Your sign-in could not be passed to the knowledge graph. Sign in again.", ...(status >= 400 && status < 500 ? {} : { failure: "kge_unreachable" }) };
  };
  async function mcp(name, mount, args, { long = false, progress } = {}) {
    let held;
    try { held = await token(); } catch (error) {
      return signInFailed(error);
    }
    const unreachable = { error: `The knowledge graph could not be reached for the ${name} call.`, failure: "kge_unreachable" };
    // The portal's own time limit on an ask (kge.js, PORTAL_ASK_TIMEOUT) is the question's length, not KGE's absence;
    // the person's Stop is never either, and ends the turn as it was asked to.
    const lost = (error) => {
      if (signal?.aborted) throw error;
      return long && timedOut(error) ? { error: `The ${name} call ran out of time.`, failure: "too_long" } : unreachable;
    };
    const callId = (id += 1);
    let invocationId;
    if ((name === "ask" || name === "question_run") && req.modelQuestionToken) {
      try {
        if (typeof kge.modelReserve !== "function") throw new Error("Model usage is unavailable.");
        invocationId = await kge.modelReserve(req, { signal });
      } catch (error) {
        if (signal?.aborted) throw error;
        return { error: "The question could not be started because usage tracking is unavailable.", failure: "kge_unreachable" };
      }
    }
    // An ask asks for progress, so KGE answers it as an event stream of its pipeline's events, then the reply.
    const params = { name, arguments: args, ...(progress ? { _meta: { progressToken: callId } } : {}) };
    const body = JSON.stringify({ jsonrpc: "2.0", id: callId, method: "tools/call", params });
    // No cookie and no browser header: the bearer alone names the person, and the trusted caller token is not sent.
    let response;
    try {
      response = await kge.request({ headers: {}, traceContext: req.traceContext }, mount, {
        method: "POST", trusted: false, long, signal, body,
        headers: { "content-type": "application/json", accept: "application/json, text/event-stream", "x-kge-authorization": `Bearer ${held}`,
          ...(["ask", "question_run", "stop_question_run"].includes(name) && req.modelQuestionToken
            ? { "x-kge-question-token": req.modelQuestionToken, "x-kge-conversation-id": req.modelConversationId } : {}),
          ...(invocationId ? { "x-kge-invocation-id": invocationId } : {}) },
      });
    } catch (error) {
      return lost(error);
    }
    if (!response.ok) {
      const failure = failureOf("", response.status);
      return { error: `The knowledge graph refused the ${name} call (${response.status}).`, ...(failure ? { failure } : {}) };
    }
    let reply;
    if (/^text\/event-stream/i.test(response.headers.get("content-type") ?? "")) {
      // The stream is read after the response starts: a timeout or a dropped connection mid-answer is this call's
      // failure, not the turn's, unless the person stopped the turn.
      try {
        reply = await streamed(response, callId, (event) => progress?.(event));
      } catch (error) {
        return lost(error);
      }
    } else {
      try { reply = await response.json(); } catch (error) {
        return lost(error);
      }
    }
    // No reply at all is the transport's failure; a JSON-RPC reply with no result (an error the protocol gave) is a
    // refusal the model reads, as a tool's own error with no status is.
    if (!reply || typeof reply !== "object") return unreachable;
    const result = reply.result;
    if (!result) {
      const said = typeof reply.error?.message === "string" && reply.error.message ? reply.error.message.slice(0, 500) : `The ${name} call failed.`;
      return { error: said };
    }
    if (result.isError) {
      const said = result.content?.find?.((c) => c?.type === "text")?.text;
      const error = typeof said === "string" ? said.slice(0, 500) : `The ${name} call failed.`;
      const failure = failureOf(error);
      return { error, ...(failure ? { failure } : {}) };
    }
    if (result.structuredContent && typeof result.structuredContent === "object") return { value: result.structuredContent };
    const first = result.content?.find?.((c) => c?.type === "text")?.text;
    try { return { value: JSON.parse(first) }; } catch { return { value: { text: String(first ?? "") } }; }
  }
  async function call(name, args, { history, progress } = {}) {
    if (!allowed.has(name) || !TOOLS[name]) return { error: `The ${name} tool is not available to you.` };
    const long = name === "ask" || name === "assist";
    return mcp(name, TOOLS[name].mount, { ...argsFor(name, args), ...EXTRAS[name]?.(history) }, { long, ...(name === "ask" ? { progress: progress ?? (() => {}) } : {}) });
  }
  // #707 requirement 13 (#714): the one step before ask reads KGE itself, with no model call. These three reads are the
  // portal's own, never offered to the model, and each needs its tool on the person's allow-list: get_context with the
  // question's bindings and open items, read with the conversation's history as ask will read it (compact, as every
  // get_context of the portal's); map_term for the meanings a name resolves to; search for the filter phrases a word
  // names (the filter kind, returned only when asked for).
  call.reading = async (question, history) => {
    if (!allowed.has("get_context")) return { error: "The get_context tool is not available to you." };
    return mcp("get_context", TOOLS.get_context.mount, { question, compact: true, open_items: true, ...(Array.isArray(history) ? { history } : {}) });
  };
  call.meanings = async (name) => {
    if (!allowed.has("map_term")) return { error: "The map_term tool is not available to you." };
    return mcp("map_term", TOOLS.map_term.mount, { term: name });
  };
  call.filters = async (phrase) => {
    if (!allowed.has("search")) return { error: "The search tool is not available to you." };
    return mcp("search", TOOLS.search.mount, { query: phrase, kinds: ["filter"] });
  };
  // The bounded citable passage mode is a portal-owned operation; it does not offer the engineer-only
  // retrieve tool to the model or change what its existing retrieve call may request.
  call.citablePassages = async (query) => {
    if (!allowed.has("get_context") || typeof query !== "string" || !query.trim()) return { error: "Passage retrieval is not available." };
    return mcp("retrieve", TOOLS.retrieve.mount, { query, purpose: "analysis", k: 5, include_passages: true });
  };
  const runId = (value) => (typeof value === "string" && RUN_ID.test(value) ? value : undefined);
  call.questionRun = async (run, after = 0) => {
    if (!allowed.has("ask")) return { error: "The ask tool is not available to you." };
    if (!runId(run)) return { error: "No such question run." };
    return mcp("question_run", TOOLS.ask.mount, { run_id: run, after: Number.isInteger(after) && after >= 0 ? after : 0 });
  };
  call.stopRun = async (run) => {
    if (!allowed.has("ask")) return { error: "The ask tool is not available to you." };
    if (!runId(run)) return { error: "No such question run." };
    return mcp("stop_question_run", TOOLS.ask.mount, { run_id: run });
  };
  let goldenQuestions = [];
  call.goldenQuestions = () => goldenQuestions;
  call.showcase = async () => {
    let held;
    try { held = await token(); } catch {
      return [];
    }
    try {
      const response = await kge.request({ headers: {}, traceContext: req.traceContext }, "/golden", { trusted: false, signal, deadline: Date.now() + SHOWCASE_MS, headers: { "x-kge-authorization": `Bearer ${held}` } });
      if (!response.ok) return [];
      const body = await response.json();
      goldenQuestions = (Array.isArray(body?.sets) ? body.sets : []).flatMap((set) => Array.isArray(set?.questions) ? set.questions.filter((question) => typeof question === "string") : []);
      return showcaseOf(body);
    } catch (error) {
      if (signal?.aborted) throw error;
      return [];
    }
  };
  return call;
}

// #621: how long a turn waits for the showcase questions before it goes on without them.
export const SHOWCASE_MS = 5000;

// A background run's id as KGE makes one (a UUID's hex); anything else never reaches KGE.
const RUN_ID = /^[0-9a-f]{32}$/;

/**
 * The showcase questions in KGE's GET /golden body, as the chat's "Try one of these" ranks them (graph.js): those its
 * A/B baseline answered as expected first, then the rest, its known misses last; text alone, each once.
 */
export function showcaseOf(body) {
  const text = (list) => (Array.isArray(list) ? list.filter((question) => typeof question === "string" && question.trim()) : []);
  const sets = Array.isArray(body?.sets) ? body.sets.filter((set) => set && typeof set === "object") : [];
  const ranked = sets.map((set) => {
    const all = text(set.golden20);
    const misses = new Set(text(set.misses));
    const answered = text(set.samples).filter((question) => all.includes(question) && !misses.has(question));
    return { answered, rest: all.filter((question) => !misses.has(question)), missed: all.filter((question) => misses.has(question)) };
  });
  const missed = new Set(ranked.flatMap((set) => set.missed));
  const answered = ranked.flatMap((set) => set.answered).filter((question) => !missed.has(question));
  const rest = ranked.flatMap((set) => set.rest).filter((question) => !missed.has(question));
  return [...new Set([...answered, ...rest, ...missed])];
}

/** Whether an error is the portal's own time limit on the call (kge.js AbortSignal.timeout), not a dropped line. */
const timedOut = (error) => error?.name === "TimeoutError" || error?.cause?.name === "TimeoutError";

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
