import assert from "node:assert/strict";
import { createServer } from "node:http";
import { after, before, beforeEach, test } from "node:test";
import { AIMessage, AIMessageChunk } from "@langchain/core/messages";
import { ChatGenerationChunk } from "@langchain/core/outputs";
import { FakeStreamingChatModel } from "@langchain/core/utils/testing";
import { createApp } from "../src/app.js";
import { openSqliteRepository } from "../src/repository/sqlite.js";
import { ASKED_AGAIN, CHOICE_RULE, MAX_HISTORY_TURNS, PHRASE_RULE, RECOVER_RULE, fromPage, historyOf, toEvents } from "../src/cerebro/agent.js";
import { progressEvent } from "../src/cerebro/tools.js";

// #607: Cerebro's multi-turn chat. A fake KGE that answers MCP as KGE does: ask with a progressToken as an event stream
// of its pipeline's events and then the reply, get_context in JSON. Every ask reply's history is the history it was
// sent plus one entry for this turn, as KGE's continuation contract grows; the replies are scripted by question.
const SYNC = "s".repeat(32);
let kgeSaw;
beforeEach(() => { kgeSaw = { asks: [], contexts: [], replies: [] }; });

const ENGINEER_ONLY_SQL_MARK = "SELECT secret FROM warehouse";
// The pipeline events KGE's stream may carry. Only stage (engineers), node_added and state_changed reach the page, cut
// to their drawn fields; the plan, the context, the answer and the links never do.
const PIPELINE = [
  { event: "plan", plan: { measure: "nna", sql: ENGINEER_ONLY_SQL_MARK } },
  { event: "stage", stage: "plan", state: "running", detail: { sql: ENGINEER_ONLY_SQL_MARK } },
  { event: "node_added", id: "n-1", state: "active", cursor: 3, node: { id: "term:nna", label: "Net new assets", type: "term", state: "active", sql: ENGINEER_ONLY_SQL_MARK, extra: { a: 1 } } },
  { event: "state_changed", id: "n-1", state: "done", node: { id: "term:nna", label: "Net new assets", type: "term", state: "done" } },
  { event: "edge_added", edge: { from: "term:nna", to: "table:flows" } },
  { event: "context", context: { block: ENGINEER_ONLY_SQL_MARK } },
  { event: "answer", answer: { answer_text: "Certified: 12." } },
];
const SHOWN = [
  { event: "node_added", id: "n-1", state: "active", node: { id: "term:nna", label: "Net new assets", type: "term", state: "active" } },
  { event: "state_changed", id: "n-1", state: "done", node: { id: "term:nna", label: "Net new assets", type: "term", state: "done" } },
];
const STAGE = { event: "stage", stage: "plan", state: "running" };

const CHOICES = [{ id: "all", label: "All transactions" }, { id: "excl", label: "Excluding fund clearing" }];
// How KGE answers each question: a mode, and whether the reply carries a history (not sure resets it), a clarification,
// a refusal (isError), or a stream that ends with no reply.
const SCRIPT = {
  "Transaction total for June?": { answer_mode: "not_sure", behaviour: "clarify", clarify: true },
  "How did the book do in 2025?": { answer_mode: "not_sure", history: false },
  "How did the book do?": { answer_mode: "not_sure", history: false },
  "And the year before that?": { error: "403: You may not read the 2023 unit." },
  "Stream cut": { drop: true },
  "Stream broken": { broken: true },
};
const DISPLAY = { v: 1, headline: { text: "Net new assets were 12.", by: "template" }, table: { caption: "NNA", columns: [], rows: [] } };

function askReply(args) {
  const script = SCRIPT[args.question] ?? { answer_mode: "certified" };
  const turnId = `t-${kgeSaw.asks.length}`;
  const entry = { turn_id: turnId, question: args.question, plan: { measure: "nna" }, binding: { release_identity: "r-1" } };
  if (script.clarify) entry.clarification = { spans: [{ turn: turnId, start: 0, end: 17 }], slot: "meaning", choices: CHOICES };
  const reply = { answer_mode: script.answer_mode, answer_text: `${script.answer_mode}: ${args.question}`, receipt: `r-${turnId}`, display: DISPLAY, sql: ENGINEER_ONLY_SQL_MARK };
  if (script.behaviour) Object.assign(reply, { behaviour: script.behaviour, clarification: entry.clarification });
  if (script.history !== false) reply.history = [...(args.history ?? []), entry];
  return reply;
}

const kge = createServer(async (req, res) => {
  const send = (status, value) => res.writeHead(status, { "content-type": "application/json" }).end(JSON.stringify(value));
  let body = "";
  for await (const chunk of req) body += chunk;
  const cookie = req.headers.cookie || "";
  if (req.url === "/whoami") return cookie ? send(200, { id: "person-1", cerebro_roles: [cookie.split("=")[1]] }) : send(401, {});
  if (req.url === "/readable-units") return send(200, { units: [] });
  if (req.url === "/session/bearer") return req.headers["x-portal-sync-token"] === SYNC ? send(200, { access_token: `bearer-${kgeSaw.asks.length}`, token_type: "bearer", expires_in: 900 }) : send(403, {});
  const call = JSON.parse(body);
  const { name, arguments: args, _meta: meta } = call.params;
  if (name === "get_context") {
    kgeSaw.contexts.push(args);
    const value = { arm: "treatment", block: "Terms:\nNet new assets: inflows less outflows.", plan_vocabulary: { contexts: [{ id: "nna", label: "Net new assets", definition: "Inflows less outflows." }] } };
    return send(200, { jsonrpc: "2.0", id: call.id, result: { structuredContent: value, isError: false } });
  }
  kgeSaw.asks.push({ args, meta });
  const script = SCRIPT[args.question] ?? {};
  const value = askReply(args);
  kgeSaw.replies.push(value);
  const result = script.error ? { content: [{ type: "text", text: script.error }], isError: true } : { structuredContent: value, isError: false };
  if (meta?.progressToken === undefined) return send(200, { jsonrpc: "2.0", id: call.id, result });
  // MCP's streamable HTTP answer: one JSON-RPC message per server-sent event, CRLF-framed as some servers send it.
  res.writeHead(200, { "content-type": "text/event-stream" });
  const frame = (message) => res.write(`event: message\r\ndata: ${JSON.stringify(message)}\r\n\r\n`);
  PIPELINE.forEach((event, i) => frame({ jsonrpc: "2.0", method: "notifications/progress", params: { progressToken: meta.progressToken, progress: i + 1, message: JSON.stringify(event) } }));
  res.write("data: not json\n\n");
  frame({ jsonrpc: "2.0", method: "notifications/progress", params: { progressToken: meta.progressToken, progress: 9 } });
  frame({ jsonrpc: "2.0", method: "notifications/progress", params: { progressToken: meta.progressToken, progress: 10, message: "not json" } });
  // Dropped after the frames are on the wire, so the stream has begun when it breaks.
  if (script.broken) return setTimeout(() => res.socket.destroy(), 50);
  if (!script.drop) res.write(`event: message\ndata: ${JSON.stringify({ jsonrpc: "2.0", id: call.id, result })}`);
  res.end();
});
const servers = [];
let kgeUrl;
before(async () => { await new Promise((resolve) => kge.listen(0, "127.0.0.1", resolve)); kgeUrl = `http://127.0.0.1:${kge.address().port}`; });
after(() => { kge.close(); for (const server of servers) server.close(); });

// A scripted model on the mock's base (FakeStreamingChatModel, as MockCerebroChat): each step is decided from the
// conversation it reads, and streamed as the real model streams (text, or tool call chunks).
let callIds = 0;
class ScriptedChat extends FakeStreamingChatModel {
  constructor(decide) {
    super({ responses: [] });
    this.decide = decide;
    this.read = [];
  }

  bindTools() { return this; }

  step(messages) {
    this.read.push(messages.map((m) => [m._getType(), m.content]));
    const step = this.decide(messages);
    return step.call ? { content: "", tool_calls: [{ id: `call-${callIds += 1}`, name: step.call[0], args: step.call[1] }] } : { content: step.say };
  }

  async _generate(messages) {
    const message = this.step(messages);
    return { generations: [{ text: message.content, message: new AIMessage(message) }] };
  }

  async *_streamResponseChunks(messages, _options, runManager) {
    const { content, tool_calls: calls = [] } = this.step(messages);
    const chunk = new ChatGenerationChunk({ text: content, message: new AIMessageChunk({ content, tool_call_chunks: calls.map((c, index) => ({ ...c, args: JSON.stringify(c.args), index, type: "tool_call_chunk" })) }) });
    yield chunk;
    await runManager?.handleLLMNewToken(content, undefined, undefined, undefined, undefined, { chunk });
  }
}
const ask = (question) => ({ call: ["ask", { question }] });
const getContext = (question) => ({ call: ["get_context", { question, compact: false }] });
const say = (text) => ({ say: text });

// The agent's behaviour under the rules, scripted: a question asks; a "mean?" question reads the declared terms; a not
// sure answer reads the declared terms and retries once in them; a clarification asks the person in one sentence.
function agentScript(messages) {
  const at = messages.findLastIndex((m) => m._getType() === "human");
  const human = messages[at].content;
  const replies = messages.slice(at + 1).filter((m) => m._getType() === "tool").map((m) => [m.name, JSON.parse(m.content)]);
  const [name, reply] = replies.at(-1) ?? [];
  const unsure = replies.some(([tool, r]) => tool === "ask" && r.answer_mode === "not_sure");
  if (!name) return /mean\?$/.test(human) ? getContext(human) : ask(human);
  if (name === "ask" && reply.behaviour === "clarify") return say("Which do you mean?");
  if (name === "ask" && reply.answer_mode === "not_sure" && !replies.some(([tool]) => tool === "get_context")) return getContext(human);
  if (name === "get_context" && unsure) return ask(`${reply.plan_vocabulary.contexts[0].label} for 2025`);
  if (name === "get_context") return say("Net new assets are inflows less outflows.");
  return say("");
}

async function start(model) {
  const repository = openSqliteRepository({ PORTAL_SQLITE_PATH: ":memory:" });
  const { app } = createApp({ repository, env: { KGE_URL: kgeUrl, PORTAL_SYNC_TOKEN: SYNC, PORTAL_ASK_MODEL: "gemini-2.5-flash", PORTAL_ASK_MODEL_PROVIDER: "mock" }, serviceAuth: { headers: async () => ({}) }, catalogueSync: false, uiPath: "not-present", cerebroModel: model, tracing: null });
  const server = app.listen(0, "127.0.0.1");
  servers.push(server);
  await new Promise((resolve) => server.once("listening", resolve));
  return `http://127.0.0.1:${server.address().port}`;
}
const frames = (text) => text.split("\n\n").filter(Boolean).map((frame) => [frame.match(/^event: (.*)$/m)[1], JSON.parse(frame.match(/^data: (.*)$/m)[1])]);

// The page: it holds the conversation, sends it whole with the person's next message, and keeps the turn's final state.
function page(base, role) {
  let messages = [];
  let n = 0;
  return async (content) => {
    const response = await fetch(`${base}/api/cerebro/stream`, {
      method: "POST", headers: { "content-type": "application/json", cookie: `kge_session=${role}` },
      body: JSON.stringify({ input: { messages: [...messages, { type: "human", id: `h-${n += 1}`, content }] } }),
    });
    assert.equal(response.status, 200);
    const events = frames(await response.text());
    const before = messages.length + 1;
    messages = events.filter(([event]) => event === "values").at(-1)[1].messages;
    const tools = messages.slice(before).filter((m) => m.type === "tool").map((m) => ({ ...m, reply: JSON.parse(m.content) }));
    return { events, custom: events.filter(([event]) => event === "custom").map(([, data]) => data), tools, messages };
  };
}

test("a scripted conversation threads KGE's history through every turn, unchanged, on the mock model (#607)", async () => {
  const model = new ScriptedChat(agentScript);
  const person = page(await start(model), "business");
  const sent = () => kgeSaw.asks.at(-1).args;
  const history = (i) => kgeSaw.replies[i].history;

  // A first question: no history; its card shows the reply with its history, its progress live.
  const first = await person("NNA for Q1 2025");
  assert.deepEqual(sent(), { question: "NNA for Q1 2025", display: true });
  assert.equal(first.tools[0].reply.answer_text, "certified: NNA for Q1 2025");
  assert.deepEqual(first.tools[0].reply.history, history(0));
  assert.equal(first.tools[0].reply.sql, undefined);
  assert.deepEqual(first.custom, SHOWN.map((event) => ({ kind: "ask_progress", tool_call_id: first.tools[0].tool_call_id, event })));

  // A follow-up by month, then by year: each ask sends the previous reply's history exactly.
  await person("and by month?");
  assert.deepEqual(sent(), { question: "and by month?", display: true, history: history(0) });
  await person("what about last year?");
  assert.deepEqual(sent(), { question: "what about last year?", display: true, history: history(1) });
  assert.equal(history(2).length, 3);

  // A refusal is an error reply: the next ask walks past it to the last answer's history.
  const refused = await person("And the year before that?");
  assert.deepEqual(refused.tools[0].reply, { error: "403: You may not read the 2023 unit." });
  await person("Transaction total for June?");
  assert.deepEqual(sent().history, history(2));

  // A clarification picked by chip: the person's message is the restated question; ask gets it with the
  // clarification's history, and the next follow-up carries the pick answer's history.
  const clarify = kgeSaw.replies.at(-1);
  assert.deepEqual(clarify.clarification.choices, CHOICES);
  await person("Excluding fund clearing for June?");
  assert.deepEqual(sent(), { question: "Excluding fund clearing for June?", display: true, history: clarify.history });
  const picked = kgeSaw.replies.at(-1).history;
  await person("and for July?");
  assert.deepEqual(sent(), { question: "and for July?", display: true, history: picked });

  // A term question reads the declared terms, compact, and asks nothing.
  const asks = kgeSaw.asks.length;
  const term = await person("What does NNA mean?");
  assert.equal(kgeSaw.asks.length, asks);
  assert.deepEqual(kgeSaw.contexts.at(-1), { question: "What does NNA mean?", compact: true });
  assert.equal(term.messages.at(-1).content, "Net new assets are inflows less outflows.");

  // Not sure, then one retry in the declared terms: the not sure reply has no history, so the retry sends none.
  const recovered = await person("How did the book do in 2025?");
  assert.deepEqual(recovered.tools.map((t) => [t.name, t.reply.answer_mode ?? "context"]), [["ask", "not_sure"], ["get_context", "context"], ["ask", "certified"]]);
  assert.deepEqual(kgeSaw.asks.at(-2).args.history, kgeSaw.replies.at(-3).history, "the question itself followed up on July");
  assert.deepEqual(sent(), { question: "Net new assets for 2025", display: true });

  // The model never read a history or a display block, nor the SQL.
  const read = JSON.stringify(model.read);
  assert.doesNotMatch(read, /"history"|"display"|turn_id|release_identity|SELECT/);
  assert.match(read, /certified: NNA for Q1 2025/);
  // Every ask asked for progress, by its own token.
  assert.ok(kgeSaw.asks.every(({ meta }) => Number.isInteger(meta?.progressToken)));
});

test("an engineer's progress adds the stages; nobody gets the plan, the context, the answer or a node's other fields (#607)", async () => {
  const person = page(await start(new ScriptedChat(agentScript)), "engineer");
  const { custom, tools } = await person("NNA for Q1 2025");
  assert.deepEqual(custom.map((data) => data.event), [STAGE, ...SHOWN]);
  assert.ok(custom.every((data) => data.kind === "ask_progress" && data.tool_call_id === tools[0].tool_call_id));
  assert.equal(tools[0].reply.sql, ENGINEER_ONLY_SQL_MARK, "an engineer's reply keeps its SQL");
  assert.equal(progressEvent({ event: "node_added", id: { not: "scalar" }, node: "not an object" }, true).id, undefined);
  for (const odd of [null, "stage", { event: "plan" }, { event: "stage" }]) assert.equal(progressEvent(odd, false), undefined);
});

test("a stream that ends with no reply is a failed ask call, and the conversation goes on (#607)", async () => {
  const person = page(await start(new ScriptedChat(agentScript)), "business");
  const { tools, custom } = await person("Stream cut");
  assert.deepEqual(tools[0].reply, { error: "The ask call failed." });
  assert.equal(custom.length, SHOWN.length, "the progress it sent was still shown");
});

test("a connection dropped mid-stream fails the ask call alone, not the turn (#607)", async () => {
  const person = page(await start(new ScriptedChat(agentScript)), "business");
  const { tools, events, messages, custom } = await person("Stream broken");
  assert.equal(custom.length, SHOWN.length, "the stream had started: its progress was shown");
  assert.deepEqual(tools[0].reply, { error: "The knowledge graph could not be reached for the ask call." });
  assert.equal(events.some(([event]) => event === "error"), false);
  assert.equal(messages.at(-1).type, "ai", "the model still closes the turn");
});

test("a resumed turn counts the not-sure replies it had before its pause: the replayed third ask is not sent (#607)", async () => {
  const base = await start(new ScriptedChat(() => say("ok")));
  const notSure = JSON.stringify({ answer_mode: "not_sure", answer_text: "Not sure." });
  const askCall = (id) => ({ type: "ai", id: `a-${id}`, content: "", tool_calls: [{ id, name: "ask", args: { question: "How did the book do?" } }] });
  const messages = [{ type: "human", id: "h-1", content: "How did the book do?" },
    askCall("c-1"), { type: "tool", tool_call_id: "c-1", name: "ask", content: notSure },
    askCall("c-2"), { type: "tool", tool_call_id: "c-2", name: "ask", content: notSure }, askCall("c-3")];
  const response = await fetch(`${base}/api/cerebro/stream`, { method: "POST", headers: { "content-type": "application/json", cookie: "kge_session=business" },
    body: JSON.stringify({ input: { messages }, command: { resume: "confirmed" } }) });
  const last = frames(await response.text()).filter(([event]) => event === "values").at(-1)[1].messages;
  assert.equal(kgeSaw.asks.length, 0);
  assert.deepEqual(JSON.parse(last.find((m) => m.tool_call_id === "c-3").content), { error: ASKED_AGAIN });
});

test("after two not-sure replies in a turn, a third ask never reaches KGE; the next turn may ask again (#607)", async () => {
  const model = new ScriptedChat((messages) => {
    const replies = messages.filter((m) => m._getType() === "tool").map((m) => JSON.parse(m.content));
    const last = replies.at(-1);
    if (last?.error) return say("What do you mean by the book?");
    return ask("How did the book do?");
  });
  const turn = page(await start(model), "steward");
  const first = await turn("How did the book do?");
  assert.equal(kgeSaw.asks.length, 2, "the question and its one retry");
  assert.deepEqual(first.tools.map((t) => t.reply.answer_mode ?? t.reply.error), ["not_sure", "not_sure", ASKED_AGAIN]);
  assert.equal(first.messages.at(-1).content, "What do you mean by the book?");
  await turn("The book is the wealth desk.");
  assert.equal(kgeSaw.asks.length, 4, "a new turn starts its own count");
});

test("the history an ask sends: the latest ask reply's, unchanged, walking past errors; none without one or at eight (#607)", () => {
  const entries = (n) => Array.from({ length: n }, (_, i) => ({ turn_id: `t-${i}`, plan: { p: i } }));
  const reply = (value, name = "ask") => ({ type: "tool", tool_call_id: "c", name, content: typeof value === "string" ? value : JSON.stringify(value) });
  const of = (...messages) => historyOf([{ type: "human", content: "q" }, ...messages].map(fromPage));
  assert.equal(of(), undefined, "a first question sends none");
  assert.deepEqual(of(reply({ history: entries(2) })), entries(2));
  assert.deepEqual(of(reply({ history: entries(2) }), reply({ error: "refused" }), reply("not json"), reply({ history: entries(1) }, "get_context")), entries(2),
    "an error, an unparseable reply and another tool's reply are walked past");
  assert.equal(of(reply({ history: entries(2) }), reply({ answer_mode: "certified" })), undefined, "a reply with no history resets it");
  assert.deepEqual(of(reply({ history: entries(2) }), reply({ declined: true, answer_mode: "reading" })), entries(2),
    "a declined reading was never answered: the history before it, a picked meaning's included, still goes");
  assert.equal(of(reply({ history: [] })), undefined);
  assert.deepEqual(of(reply({ history: entries(MAX_HISTORY_TURNS - 1) })), entries(MAX_HISTORY_TURNS - 1));
  assert.equal(of(reply({ history: entries(MAX_HISTORY_TURNS) })), undefined, "eight entries: the question is asked afresh");
});

test("toEvents forwards LangGraph's custom chunks as the hook's custom events (#607)", async () => {
  const data = { kind: "ask_progress", tool_call_id: "c-1", event: SHOWN[0] };
  async function* stream() { yield ["custom", data]; }
  const events = [];
  for await (const event of toEvents(stream())) events.push(event);
  assert.deepEqual(events, [["custom", data]]);
});

test("the system prompt carries #607's rules: phrase in declared terms, recover once, chips for choices", async () => {
  const model = new ScriptedChat(() => say("ok"));
  await page(await start(model), "business")("q");
  const prompt = model.read[0][0][1];
  for (const rule of [PHRASE_RULE, RECOVER_RULE, CHOICE_RULE]) assert.ok(prompt.includes(rule));
  assert.match(prompt, /you may call get_context and send ask the question in the declared terms/);
  assert.match(prompt, /"current NNA" stays current/);
  assert.match(prompt, /never invent a term or a filter/);
  assert.match(prompt, /answer_mode not_sure, retry once/);
  assert.match(prompt, /Never retry more than once per question/);
  assert.match(prompt, /do not list the choices in your text/);
});
