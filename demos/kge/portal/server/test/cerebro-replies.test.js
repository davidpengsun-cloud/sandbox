import assert from "node:assert/strict";
import { mkdtemp, rm } from "node:fs/promises";
import { createServer } from "node:http";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { after, before, test } from "node:test";
import { AIMessage, HumanMessage, ToolMessage } from "@langchain/core/messages";
import { createApp } from "../src/app.js";
import { openSqliteRepository } from "../src/repository/sqlite.js";
import { EMPTY_REPLY, NOT_SAVED, answerCard, buildGraph, cerebroAgent, outcomeOf, saveVerdict, savedConversation, toPage } from "../src/cerebro/agent.js";
import { cerebroStream } from "../src/cerebro/route.js";
import { FAILURES, READOUT_CLOSING, givesFigure, readoutMessage } from "../src/cerebro/status.js";
import { ConversationSaver, conversationKey, memoryStore, openConversations, ownerOf } from "../src/conversations/index.js";
import { localStore } from "../src/conversations/local.js";
import { kgeStub, standIn } from "./fixtures/cerebro-stand-in.js";

// #651: each reply's record (sent, duration, outcome, failure, steps, readouts, verdict) kept beside the checkpoint and
// carried by the stream and the open route; Like and Dislike; the readout in words; the answer card; an empty model
// reply as a failure; the health route's model. Synthetic data only; no call leaves the process.

const folders = [];
after(async () => { for (const made of folders) await rm(made, { recursive: true, force: true }); });
const folder = async () => {
  const made = await mkdtemp(join(tmpdir(), "cerebro-replies-"));
  folders.push(made);
  return made;
};
const newThread = () => globalThis.crypto.randomUUID();
const req = (principal = "ana") => ({ portalIdentity: { principal } });
const collect = async (events) => { const out = []; for await (const event of events) out.push(event); return out; };
const customs = (events) => events.filter(([e]) => e === "custom").map(([, d]) => d);
const lastValues = (events) => events.filter(([e]) => e === "values").at(-1)?.[1];
const quietly = async (work) => {
  const original = console.error;
  console.error = () => {};
  try { return await work(); } finally { console.error = original; }
};

// A figure with its checked table (one row), as Ask gives it with display requested.
const FIGURE = { behaviour: "figure", answer_mode: "certified", answer_text: "Certified: revenue was 42.", figure: 42,
  display: { v: 1, headline: { text: "Revenue was 42.", by: "template" }, table: { caption: "Revenue", columns: [{ key: "value", label: "Revenue", type: "number" }], rows: [[42]] } } };
const CLARIFY = { behaviour: "clarify", answer_mode: "not_sure", answer_text: "Not sure: which transactions?", reason: "Which transactions?",
  clarification: { choices: [{ id: "all", label: "All transactions" }, { id: "card", label: "Card transactions" }] } };
// A readout's worth of Ask's progress: the measure, the calendar and the unit read.
const PROGRESS = [
  { event: "context", context: { term: "Revenue", time_mode: "as_reported" } },
  { event: "node_added", node: { id: "Words:june 2025", type: "Words", name: "June 2025" } },
  { event: "edge_added", edge: { from: "Words:june 2025", to: "Calendar:calendar", type: "matches" } },
  { event: "node_added", node: { id: "Unit:retail", type: "Unit", label: "Retail", used: true, state: "done" } },
];
// KGE stubbed: the portal's framing get_context finds no declared term, so it leaves nothing in the turn.
const kgeWith = (reply) => kgeStub((name, args) => (name === "get_context" ? { error: "No declared term matches." } : reply(name, args)));
// Asks the person's question once; after a card, says nothing more.
const asker = () => standIn((messages) => (messages.slice(messages.findLastIndex((m) => m._getType() === "human")).some((m) => m._getType() === "tool")
  ? { say: "Which do you mean?" } : { call: ["ask", { question: messages.findLast((m) => m._getType() === "human").content }] }));

function agent({ conversations, model = asker(), kge = kgeWith(() => ({ value: FIGURE })) } = {}) {
  const run = cerebroAgent({ model, kge, conversations, retryAfterMs: 0 });
  return {
    ask: (thread, content, extra = {}) => collect(run({ thread, messages: [{ type: "human", id: extra.id ?? `h-${globalThis.crypto.randomUUID()}`, content }], rewind: extra.rewind, req: req(), roles: ["business"] })),
    resume: (thread, resume) => collect(run({ thread, resume, req: req(), roles: ["business"] })),
  };
}

test("B2, A4: an answered turn's record follows its outcome on the stream, rides on values and the open route, and survives a new process (#651)", async () => {
  const root = await folder();
  const conversations = openConversations({ store: localStore(root) });
  const thread = newThread();
  const before = Date.now();
  const events = await agent({ conversations }).ask(thread, "Revenue for June 2025?", { id: "h-first" });
  const kinds = customs(events).map((d) => d.kind);
  assert.deepEqual(kinds.slice(-2), ["outcome", "reply"], "the record right after the outcome");
  const { id, reply } = customs(events).at(-1);
  assert.equal(id, "h-first");
  assert.equal(reply.outcome, "answered");
  assert.equal(reply.failure, undefined);
  assert.ok(Date.parse(reply.sent) >= before - 5 && Date.parse(reply.sent) <= Date.now());
  assert.ok(Number.isInteger(reply.duration_ms) && reply.duration_ms >= 0);
  assert.deepEqual(reply.steps.map((s) => s.step), ["understanding", "planning", "checking"]);
  assert.deepEqual(reply.readouts, []);
  assert.equal(reply.verdict, undefined);
  // The stream's values carry the records the conversation holds so far: this turn's is new, so none yet.
  assert.deepEqual(lastValues(events).replies, {});
  // A reload, and a new process over the same folder: the record is there, beside the messages.
  for (const opened of [conversations, openConversations({ store: localStore(root) })]) {
    const saved = await savedConversation(opened, "ana", thread);
    assert.deepEqual(saved.replies, { "h-first": reply });
    assert.deepEqual(saved.messages, lastValues(events).messages);
  }
  // The next turn's values carry the first reply's record; another person sees nothing.
  const next = await agent({ conversations }).ask(thread, "Revenue for July 2025?", { id: "h-second" });
  assert.deepEqual(lastValues(next).replies, { "h-first": reply });
  assert.deepEqual(Object.keys((await savedConversation(conversations, "ana", thread)).replies).sort(), ["h-first", "h-second"]);
  assert.equal(await savedConversation(conversations, "bo", thread), undefined);
});

// The record's readouts are cerebro-speed.test.js's (d), on KGE's recorded stream.
test("B3: the readout event carries its message, built in code from Ask's progress, with no figure (#651)", async () => {
  const graphEvents = [];
  const model = asker();
  const call = async (_name, _args, extras) => {
    for (const event of PROGRESS) extras?.progress?.(event);
    return { value: FIGURE };
  };
  const graph = buildGraph(model, { call, allowed: new Set(["ask"]) });
  for await (const [, chunk] of await graph.stream({ messages: [new HumanMessage({ id: "h", content: "Revenue for June 2025 in retail?" })], turnStart: 1 }, { configurable: { thread_id: "t" }, streamMode: ["custom"] })) graphEvents.push(chunk);
  const readouts = graphEvents.filter((d) => d?.kind === "readout");
  assert.ok(readouts.length >= 1);
  assert.equal(readouts.at(-1).message, "This reads Revenue. It covers June 2025 on an as-reported basis. The source is Retail.");
  for (const { message } of readouts) assert.equal(givesFigure(message), false);
});

test("B3: readoutMessage builds two to four plain sentences from the fields, drops any that gives a figure, and sends what there is (#651)", () => {
  assert.equal(readoutMessage({ measure: "Net new assets", mode: "As reported", period: "June 2025", grouping: ["region"], filters: ["retail"], source: "Wealth" }),
    "This reads Net new assets. It covers June 2025 on an as-reported basis. It is grouped by region and filtered to retail. The source is Wealth.");
  assert.equal(readoutMessage({ measure: "Revenue", mode: "Current", grouping: ["region", "desk", "tier"], filters: [] }),
    "This reads Revenue. It is read on a current basis. It is grouped by region, desk and tier.");
  assert.equal(readoutMessage({ period: "FY26-Q1", filters: ["Closed periods only"] }), "It covers FY26-Q1. It is filtered to Closed periods only.");
  // A period that is a date's or a period's numbers alone is no figure (givesFigure); a bare number is, and goes.
  assert.equal(readoutMessage({ measure: "Revenue", period: "2025-06" }), "This reads Revenue. It covers 2025-06.");
  assert.equal(readoutMessage({ measure: "Revenue", period: "2025" }), `This reads Revenue. ${READOUT_CLOSING}`, "\"It covers 2025.\" gives a figure, so it is dropped");
  assert.equal(readoutMessage({ measure: "Top 10 clients", source: "Wealth" }), `The source is Wealth. ${READOUT_CLOSING}`);
  // Fewer fields, fewer sentences, but never one alone: the fixed closing sentence, with no figure, makes two; none, nothing.
  assert.equal(readoutMessage({ measure: "Fees" }), `This reads Fees. ${READOUT_CLOSING}`);
  assert.equal(READOUT_CLOSING, "The figures come from the checked query for this question.");
  assert.equal(givesFigure(READOUT_CLOSING), false);
  for (const fields of [{ measure: "Fees" }, { period: "June 2025" }, { mode: "Current" }, { grouping: ["region"] }, { source: "Retail" }, { measure: "Revenue", period: "2025" }]) {
    const count = readoutMessage(fields).split(/(?<=\.) /).length;
    assert.ok(count >= 2 && count <= 4, `${JSON.stringify(fields)}: ${count} sentences`);
  }
  for (const empty of [{}, null, undefined, "x", { measure: " ", filters: [3, ""] }]) assert.equal(readoutMessage(empty), "");
});

test("B4: an ask reply is a card only where its checked SQL returned rows; definitions, clarifications, no rows, refusals and pending runs are messages (#651)", () => {
  const tool = (name, reply) => new ToolMessage({ id: `m-${name}`, tool_call_id: "c-1", name, content: JSON.stringify(reply) });
  const cards = [
    FIGURE,
    { behaviour: "sum_with_verdict", answer_mode: "certified", rows: { north: 1, south: 2 } },
    { behaviour: "combined", answer_mode: "certified", figures: [{ label: "a", value: 3 }] },
    { behaviour: "parts_with_reason", answer_mode: "certified", parts: { retail: 1 } },
    { behaviour: "figure", answer_mode: "ai_generated", result: { columns: ["a"], rows: [[1]] } },
    { behaviour: "figure", answer_mode: "certified", figure: 7 },
  ];
  for (const reply of cards) {
    assert.equal(answerCard(reply), true, JSON.stringify(reply));
    assert.equal(toPage(tool("ask", reply)).card, true);
    assert.equal(toPage(tool("ask", reply), true).card, true, "an engineer's too");
  }
  const messages = [
    { ...FIGURE, display: { ...FIGURE.display, table: { ...FIGURE.display.table, rows: [] } } },
    { behaviour: "sum_with_verdict", answer_mode: "certified", rows: {} },
    { behaviour: "combined", answer_mode: "certified", figures: [{ label: "a", value: null }] },
    { behaviour: "figure", answer_mode: "certified", figure: null },
    { behaviour: "figure", answer_mode: "not_sure", figure: 4 },
    { behaviour: "cite", answer_mode: "certified", answer_text: "Revenue: income from sales." },
    { behaviour: "cite_certified", answer_mode: "certified", difference: { rows: [{ attribute: "a" }] } },
    { behaviour: "glossary_definition", definition: "Income." },
    CLARIFY,
    { behaviour: "refuse", answer_text: "No." },
    { behaviour: "abstain", answer_text: "Ask answers governed figures." },
    { error: "The warehouse query failed.", failure: "warehouse_failed" },
    { run: { id: "r-1", state: "running", poll_after: 2 }, question: "q" },
    null,
  ];
  for (const reply of messages) {
    assert.equal(answerCard(reply), false, JSON.stringify(reply));
    assert.equal(toPage(tool("ask", reply)).card, false);
  }
  // Another tool's reply is no answer card, whatever it holds; an unreadable result neither.
  assert.equal("card" in toPage(tool("get_context", FIGURE)), false);
  assert.equal(toPage(new ToolMessage({ tool_call_id: "c", name: "ask", content: "not json" })).card, false);
});

test("A5: an empty model reply is never saved as a blank answer: the turn fails as empty_reply and keeps no blank message (#651)", async () => {
  const conversations = openConversations();
  const thread = newThread();
  const events = await agent({ conversations, model: standIn(() => ({ say: "   " })) }).ask(thread, "Hello?", { id: "h-1" });
  const outcome = customs(events).filter((d) => d.kind === "outcome").at(-1);
  assert.deepEqual(outcome.error, { case: "empty_reply", message: "The assistant gave no reply: try again.", action: "try_again" });
  assert.deepEqual(FAILURES[EMPTY_REPLY], { message: outcome.error.message, action: "try_again" });
  const saved = await savedConversation(conversations, "ana", thread);
  assert.deepEqual(saved.messages.map((m) => m.type), ["human"], "the blank reply is not kept");
  assert.equal(saved.replies["h-1"].outcome, "failed");
  assert.deepEqual(saved.replies["h-1"].failure, outcome.error);
  // A model that speaks still answers in words.
  const spoke = await agent({ conversations, model: standIn(() => ({ say: "Hello there." })) }).ask(newThread(), "Hello?");
  assert.equal(customs(spoke).filter((d) => d.kind === "outcome").at(-1).outcome, "answered");
  // Where the step before already says why there is no answer (a not-sure reply), that stays the outcome.
  const unsure = { behaviour: "figure", answer_mode: "not_sure", reason: "The period is not clear.", answer_text: "Not sure." };
  const twice = standIn((messages, n) => (n === 1 ? { call: ["ask", { question: "q" }] } : { say: "" }));
  const notPlanned = await agent({ conversations, model: twice, kge: kgeWith(() => ({ value: unsure })) }).ask(newThread(), "q");
  assert.deepEqual(customs(notPlanned).filter((d) => d.kind === "outcome").at(-1).error.case, "not_planned");
  // outcomeOf reads the case alone where nothing else ends the turn; a model engineer's detail is kept for an engineer.
  const values = { messages: [new HumanMessage({ id: "h", content: "q" })], turnStart: 1, failure: { case: EMPTY_REPLY, detail: "none" } };
  assert.deepEqual(outcomeOf(values, { engineer: true }).error, { ...outcome.error, detail: "none" });
});

test("A4: a turn whose model fails keeps its failed record, sent on the stream after the outcome and there after a reload (#651)", async () => {
  const conversations = openConversations();
  const runTurn = cerebroAgent({ model: { invoke: async () => { throw new Error("model quota exhausted"); } }, conversations, retryAfterMs: 0 });
  const thread = newThread();
  const written = [];
  const res = { headersSent: false, on() {}, setHeader() {}, writeHead() {}, write: (text) => written.push(text), end() {} };
  await quietly(() => cerebroStream({ runTurn: (turn) => runTurn({ ...turn, roles: ["business"] }) })(
    { body: { input: { messages: [{ type: "human", id: "h-fail", content: "Revenue?" }] }, config: { configurable: { thread_id: thread } } }, portalIdentity: { principal: "ana" } }, res));
  const frames = written.filter((t) => t.startsWith("event: custom")).map((t) => JSON.parse(t.match(/^data: (.*)$/m)[1]));
  assert.deepEqual(frames.slice(-2).map((d) => d.kind), ["outcome", "reply"]);
  const failure = { case: "model_failed", message: "The assistant couldn't respond.", action: "try_again" };
  assert.deepEqual(frames.at(-1).reply.failure, failure);
  const saved = await savedConversation(conversations, "ana", thread);
  assert.equal(saved.replies["h-fail"].outcome, "failed");
  assert.deepEqual(saved.replies["h-fail"].failure, failure);
  assert.deepEqual(saved.replies["h-fail"].steps.map((s) => s.step), ["understanding"]);
});

test("A4: a turn whose save fails still ends failed as before, its record saying so too (#651)", async () => {
  const down = openConversations({ store: { ...memoryStore(), write: async () => { throw new Error("disk full"); } } });
  const events = await quietly(() => agent({ conversations: down }).ask(newThread(), "Revenue?", { id: "h-1" }));
  const outcomes = customs(events).filter((d) => d.kind === "outcome");
  assert.deepEqual(outcomes.at(-1).error, NOT_SAVED);
  assert.equal(outcomes.some((o) => o.outcome === "answered"), false);
  assert.deepEqual(customs(events).at(-1).reply.failure, { case: NOT_SAVED.case, message: NOT_SAVED.message, action: NOT_SAVED.action });
  // Refused only at the last save, after the answered outcome: a failed outcome and a failed record follow, last.
  const store = memoryStore();
  let release;
  const gate = new Promise((resolve) => { release = resolve; });
  const late = openConversations({ store: { ...store, write: async () => { await gate; throw new Error("disk full"); } } });
  const lateEvents = [];
  await quietly(async () => {
    for await (const event of cerebroAgent({ model: asker(), kge: kgeWith(() => ({ value: FIGURE })), conversations: late, retryAfterMs: 0 })({ thread: newThread(), messages: [{ type: "human", id: "h-2", content: "q" }], req: req(), roles: ["business"] })) {
      lateEvents.push(event);
      if (event[1]?.kind === "outcome") release();
    }
  });
  assert.deepEqual(customs(lateEvents).slice(-4).map((d) => [d.kind, d.outcome ?? d.reply.outcome]), [["outcome", "answered"], ["reply", "answered"], ["outcome", "failed"], ["reply", "failed"]]);
});

test("storage: writing a record schedules nothing; a paused turn keeps its interrupt and no record until it is resumed; the next turn runs as before (#651)", async () => {
  const store = memoryStore();
  const conversations = openConversations({ store });
  const thread = newThread();
  const kge = kgeWith((_name, args) => ({ value: /^Transactions/.test(args.question) ? CLARIFY : FIGURE }));
  const run = agent({ conversations, kge });
  const graphOf = () => buildGraph({ invoke: async () => { throw new Error("no model"); } }, { checkpointer: conversations.saver("ana") });
  const config = { configurable: { thread_id: thread } };
  // An answered turn: its record is written, and nothing is due next.
  await run.ask(thread, "Revenue for June 2025?", { id: "h-1" });
  let state = await graphOf().getState(config);
  assert.deepEqual(state.next, []);
  assert.equal(state.tasks.some((t) => t.interrupts?.length), false);
  // A paused turn: its interrupt is kept, the open route shows it, and it has no record yet.
  const askedAt = Date.now();
  const paused = await run.ask(thread, "Transactions for June 2025?", { id: "h-2" });
  assert.equal(customs(paused).filter((d) => d.kind === "outcome").at(-1).outcome, "paused");
  assert.equal(customs(paused).some((d) => d.kind === "reply"), false, "no record while it waits");
  state = await graphOf().getState(config);
  assert.ok(state.tasks.some((t) => t.interrupts?.length), "the interrupt is not lost");
  const opened = await savedConversation(conversations, "ana", thread);
  assert.ok(opened.interrupt);
  assert.deepEqual(Object.keys(opened.replies), ["h-1"]);
  // A Like while it waits keeps the interrupt too.
  assert.deepEqual(await saveVerdict(conversations, "ana", thread, "h-1", "like"), { verdict: "like" });
  assert.ok((await graphOf().getState(config)).tasks.some((t) => t.interrupts?.length));
  // The resume writes the record, sent when the question was asked, with the steps before and after the pause.
  await new Promise((resolve) => { setTimeout(resolve, 20); });
  const resumedAt = Date.now();
  const resumed = await run.resume(thread, { pick: "card" });
  const record = customs(resumed).at(-1);
  assert.equal(record.id, "h-2");
  assert.equal(record.reply.outcome, "answered");
  assert.equal(record.reply.sent, (await savedConversation(conversations, "ana", thread)).replies["h-2"].sent);
  assert.ok(Date.parse(record.reply.sent) >= askedAt && Date.parse(record.reply.sent) < resumedAt, "sent when it was asked, not resumed");
  // Before the pause: the framing get_context it tried (no term), and the plan; after it, the pick's ask.
  assert.deepEqual(record.reply.steps.map((s) => s.step), ["understanding", "context", "planning", "understanding", "planning", "checking"]);
  state = await graphOf().getState(config);
  assert.deepEqual(state.next, []);
  const after = await savedConversation(conversations, "ana", thread);
  assert.equal(after.interrupt, null);
  assert.equal(after.replies["h-1"].verdict, "like", "the verdict given while it waited stands");
  // The next turn runs as before.
  const next = await run.ask(thread, "Revenue for July 2025?", { id: "h-3" });
  assert.equal(customs(next).filter((d) => d.kind === "outcome").at(-1).outcome, "answered");
  assert.deepEqual(Object.keys((await savedConversation(conversations, "ana", thread)).replies).sort(), ["h-1", "h-2", "h-3"]);
});

test("rewind: the records of the questions it removes are gone from the values and the open route, and a reused id starts afresh (#651)", async () => {
  const conversations = openConversations();
  const thread = newThread();
  const run = agent({ conversations });
  await run.ask(thread, "First question?", { id: "h-1" });
  await run.ask(thread, "Second question?", { id: "h-2" });
  await saveVerdict(conversations, "ana", thread, "h-2", "dislike");
  await run.ask(thread, "Third question?", { id: "h-3" });
  const edited = await run.ask(thread, "Second, edited?", { id: "h-2", rewind: "h-2" });
  assert.deepEqual(Object.keys(lastValues(edited).replies), ["h-1"], "the rewound questions' records are not shown");
  const saved = await savedConversation(conversations, "ana", thread);
  assert.deepEqual(Object.keys(saved.replies).sort(), ["h-1", "h-2"]);
  assert.equal(saved.replies["h-2"].verdict, undefined, "the new question's record is its own");
  // A verdict on a question no longer in the conversation is no reply there.
  assert.equal(await saveVerdict(conversations, "ana", thread, "h-3", "like"), undefined);
});

test("verdicts: a turn's write that meets a Like on a conflict keeps it; setVerdict retries a conflict and gives up after three (#651)", async () => {
  const store = memoryStore();
  const conversations = openConversations({ store });
  const thread = newThread();
  const run = agent({ conversations });
  await run.ask(thread, "First?", { id: "h-1" });
  // A turn's saver read the conversation before the Like landed; its later write keeps the Like.
  const saver = conversations.saver("ana");
  const aside = await saver.aside(thread);
  assert.deepEqual(await saveVerdict(conversations, "ana", thread, "h-1", "like"), { verdict: "like" });
  await saver.putAside(thread, { ...aside, replies: { ...aside.replies, "h-9": { outcome: "answered" } } });
  await saver.flush();
  const kept = await new ConversationSaver(store, ownerOf("ana")).aside(thread);
  assert.equal(kept.replies["h-1"].verdict, "like");
  assert.ok(kept.replies["h-9"]);
  // And a cleared one stays cleared.
  const again = conversations.saver("ana");
  const held = await again.aside(thread);
  assert.deepEqual(await saveVerdict(conversations, "ana", thread, "h-1", null), { verdict: null });
  await again.putAside(thread, held);
  await again.flush();
  assert.equal((await new ConversationSaver(store, ownerOf("ana")).aside(thread)).replies["h-1"].verdict, undefined);
  // setVerdict on a store that always moves on is refused after three tries; one that moves once is retried.
  let moves = 1;
  const racing = { ...store, async write(key, body, options) {
    if (moves > 0) {
      moves -= 1;
      const now = await store.read(key);
      await store.write(key, now.body, { ifGeneration: now.generation, metadata: now.metadata });
    }
    return store.write(key, body, options);
  } };
  assert.equal((await new ConversationSaver(racing, ownerOf("ana")).setVerdict(thread, "h-1", "dislike")).verdict, "dislike");
  moves = 3;
  await assert.rejects(new ConversationSaver(racing, ownerOf("ana")).setVerdict(thread, "h-1", "like"), (error) => error.code === "conflict");
  moves = 0;
  assert.equal(await new ConversationSaver(store, ownerOf("ana")).setVerdict(newThread(), "h-1", "like"), undefined);
  assert.equal(await new ConversationSaver(store, ownerOf("ana")).setVerdict(thread, "h-none", "like"), undefined);
  await new ConversationSaver(store, ownerOf("ana")).putAside(newThread(), { replies: {} });
});

// The review's repro: a question whose id is __proto__, and a verdict on it, reach no object's prototype.
test("a message id such as __proto__ is a key like any other: its record is its own, and no verdict reaches Object.prototype (#651)", async () => {
  const conversations = openConversations();
  const thread = newThread();
  const run = agent({ conversations });
  await run.ask(thread, "First?", { id: "h-1" });
  try {
    const events = await run.ask(thread, "Second?", { id: "__proto__" });
    assert.equal(customs(events).at(-1).id, "__proto__");
    const open = await savedConversation(conversations, "ana", thread);
    assert.ok(Object.hasOwn(open.replies, "__proto__"), "the record is the reply's own key");
    assert.equal(open.replies.__proto__.outcome, "answered");
    assert.equal(Object.getPrototypeOf(open.replies), Object.prototype, "the open route's replies keep their prototype");
    // The route's refusal: none of an object's prototype names is a reply.
    for (const name of ["__proto__", "constructor", "prototype"]) assert.equal(await saveVerdict(conversations, "ana", thread, name, "like"), undefined, name);
    assert.equal(({}).verdict, undefined);
    // Below the route, the saver reads own keys alone, and holds replies with no prototype.
    const saver = new ConversationSaver(conversations.saver("ana").store, ownerOf("ana"));
    assert.equal((await saver.setVerdict(thread, "__proto__", "like")).verdict, "like");
    assert.equal(({}).verdict, undefined);
    assert.equal(await saver.setVerdict(thread, "constructor", "like"), undefined, "no record by that name");
    assert.equal(({}).verdict, undefined);
    const aside = await saver.aside(thread);
    assert.equal(Object.getPrototypeOf(aside.replies), null);
    assert.equal(aside.replies.__proto__.verdict, "like");
    assert.equal(aside.replies.constructor, undefined);
    // A turn after it keeps that record as its own, and pollutes nothing either.
    await run.ask(thread, "Third?", { id: "h-3" });
    assert.deepEqual(Object.keys((await savedConversation(conversations, "ana", thread)).replies).sort(), ["__proto__", "h-1", "h-3"]);
    assert.equal(({}).verdict, undefined);
    assert.equal(({}).outcome, undefined);
  } finally {
    delete Object.prototype.verdict;
    delete Object.prototype.outcome;
  }
});

// A Like given while another question runs, at its model call and at its tool call: a real turn, the PUT's own path.
for (const when of ["model call", "tool call"]) {
  test(`a Like given during another question's ${when} stands after the turn (#651)`, async () => {
    const conversations = openConversations();
    const thread = newThread();
    let hook = async () => {};
    const base = asker();
    const model = { ...base, bindTools: () => model, invoke: async (messages) => { if (when === "model call") await hook(); return base.invoke(messages); } };
    const kge = kgeWith(() => ({ value: FIGURE }));
    const request = kge.request;
    kge.request = async (...args) => { if (when === "tool call" && args[2]?.body && JSON.parse(args[2].body).params.name === "ask") await hook(); return request(...args); };
    const run = agent({ conversations, model, kge });
    await run.ask(thread, "Revenue for June 2025?", { id: "h-1" });
    await run.ask(thread, "Revenue for July 2025?", { id: "h-2" });
    let given = 0;
    hook = async () => {
      given += 1;
      if (given === 1) assert.deepEqual(await saveVerdict(conversations, "ana", thread, "h-1", "like"), { verdict: "like" });
      if (given === 2) assert.deepEqual(await saveVerdict(conversations, "ana", thread, "h-2", "dislike"), { verdict: "dislike" });
    };
    const events = await run.ask(thread, "Revenue for August 2025?", { id: "h-3" });
    assert.ok(given >= 1);
    assert.equal(customs(events).filter((d) => d.kind === "outcome").at(-1).outcome, "answered");
    const open = await savedConversation(conversations, "ana", thread);
    assert.equal(open.replies["h-1"].verdict, "like");
    if (given >= 2) assert.equal(open.replies["h-2"].verdict, "dislike");
    assert.equal(open.replies["h-3"].outcome, "answered");
    assert.equal(open.replies["h-3"].verdict, undefined);
  });
}

test("a turn's save makes itself again over Likes that land between its tries, up to five; past that it ends conversation_changed (#651)", async () => {
  const store = memoryStore();
  const conversations = openConversations({ store });
  const thread = newThread();
  await agent({ conversations }).ask(thread, "First?", { id: "h-1" });
  // Every write of the turn's meets a Like or a Dislike that landed first (and its retry another), as many as left.
  let left = 0;
  let flip = 0;
  const racing = { ...store, async write(key, body, options) {
    if (left > 0 && key === conversationKey(ownerOf("ana"), thread)) {
      left -= 1;
      flip += 1;
      await new ConversationSaver(store, ownerOf("ana")).setVerdict(thread, "h-1", flip % 2 ? "like" : "dislike");
    }
    return store.write(key, body, options);
  } };
  const busy = openConversations({ store: racing });
  left = 3;
  const kept = await agent({ conversations: busy }).ask(thread, "Second?", { id: "h-2" });
  assert.equal(left, 0);
  assert.equal(customs(kept).filter((d) => d.kind === "outcome").at(-1).outcome, "answered");
  assert.equal((await savedConversation(conversations, "ana", thread)).replies["h-1"].verdict, "like", "the last of three verdicts");
  left = 50;
  const lost = await agent({ conversations: busy }).ask(thread, "Third?", { id: "h-3" });
  left = 0;
  assert.equal(customs(lost).filter((d) => d.kind === "outcome").at(-1).error?.case, "conversation_changed");
});

test("a resume that answers #624's offer carries the answered turn's record on: sent, duration, steps and its verdict (#651)", async () => {
  const conversations = openConversations();
  const thread = newThread();
  const difference = { behaviour: "cite_certified", answer_mode: "certified", answer_text: "Certified: current NNA and certified NNA differ in their version dates.",
    difference: { terms: [["current NNA", "2024-01-01", "2025-06-30"], ["certified NNA", "2024-04-01", "2025-08-01"]].map(([term, first, latest]) => ({ term, definition: `${term} as declared.`, period_range: { first, latest, released_months: true, words: `${first} to ${latest}` } })), rows: [], summary: "They differ in their version dates." } };
  const run = agent({ conversations, kge: kgeWith(() => ({ value: difference })) });
  const asked = await run.ask(thread, "What is the difference between the definitions of current NNA and certified NNA?", { id: "h-1" });
  assert.equal(customs(asked).filter((d) => d.kind === "outcome").at(-1).outcome, "answered");
  const first = customs(asked).at(-1).reply;
  assert.deepEqual(await saveVerdict(conversations, "ana", thread, "h-1", "like"), { verdict: "like" });
  await new Promise((resolve) => { setTimeout(resolve, 5); });
  const resumed = await run.resume(thread, { pick: "no" });
  const { id, reply } = customs(resumed).at(-1);
  assert.equal(id, "h-1");
  assert.equal(reply.sent, first.sent, "sent when it was asked");
  assert.ok(reply.duration_ms >= first.duration_ms);
  assert.deepEqual(reply.steps.slice(0, first.steps.length), first.steps, "the answered turn's steps, then the resume's");
  assert.ok(reply.steps.length > first.steps.length);
  assert.equal(reply.verdict, "like");
  assert.deepEqual((await savedConversation(conversations, "ana", thread)).replies["h-1"], reply);
});

test("a new message starts its record afresh whatever an earlier pause left (aside.paused), a reused id among them (#651)", async () => {
  const conversations = openConversations();
  const thread = newThread();
  const run = agent({ conversations, kge: kgeWith((_name, args) => ({ value: /^Transactions/.test(args.question) ? CLARIFY : FIGURE })) });
  await run.ask(thread, "Revenue for June 2025?", { id: "h-1" });
  const paused = await run.ask(thread, "Transactions for June 2025?", { id: "h-2" });
  assert.equal(customs(paused).filter((d) => d.kind === "outcome").at(-1).outcome, "paused");
  assert.equal((await conversations.saver("ana").aside(thread)).paused.id, "h-2");
  await new Promise((resolve) => { setTimeout(resolve, 20); });
  const again = Date.now();
  // Asked again under its id (Edit on the paused question): its record is this turn's alone.
  const edited = await run.ask(thread, "Revenue for July 2025?", { id: "h-2", rewind: "h-2" });
  const { reply } = customs(edited).at(-1);
  assert.equal(reply.outcome, "answered");
  assert.ok(Date.parse(reply.sent) >= again, "sent now, not when the paused question was asked");
  assert.deepEqual(reply.steps.map((s) => s.step), customs(edited).filter((d) => d.kind === "step").map((d) => d.step), "this turn's steps alone, none from before the pause");
  assert.equal(reply.steps.filter((s) => s.step === "understanding").length, 1);
});

test("a question saved before #651 has no record: a verdict on it makes one, { verdict }; an id that is no question in it is still none (#651)", async () => {
  const store = memoryStore();
  const conversations = openConversations({ store });
  const thread = newThread();
  await agent({ conversations }).ask(thread, "Revenue?", { id: "h-1" });
  // The object as it was saved before #651: no aside.
  const key = conversationKey(ownerOf("ana"), thread);
  const found = await store.read(key);
  const { aside, ...old } = JSON.parse(found.body.toString("utf8"));
  assert.ok(aside);
  await store.write(key, Buffer.from(JSON.stringify(old)), { ifGeneration: found.generation, metadata: found.metadata });
  assert.deepEqual((await savedConversation(conversations, "ana", thread)).replies, {});
  assert.deepEqual(await saveVerdict(conversations, "ana", thread, "h-1", null), { verdict: null }, "clearing none makes none");
  assert.deepEqual((await savedConversation(conversations, "ana", thread)).replies, {});
  assert.deepEqual(await saveVerdict(conversations, "ana", thread, "h-1", "like"), { verdict: "like" });
  assert.deepEqual((await savedConversation(conversations, "ana", thread)).replies, { "h-1": { verdict: "like" } });
  assert.equal(await saveVerdict(conversations, "ana", thread, "h-none", "like"), undefined);
  // The next turn keeps it.
  await agent({ conversations }).ask(thread, "Revenue again?", { id: "h-2" });
  assert.deepEqual((await savedConversation(conversations, "ana", thread)).replies["h-1"], { verdict: "like" });
});

// ---- The routes, end to end: a fake KGE for sign-in, the stand-in model, a memory store. ----

const kge = createServer(async (request, res) => {
  const send = (status, value) => res.writeHead(status, { "content-type": "application/json" }).end(JSON.stringify(value));
  let body = "";
  for await (const chunk of request) body += chunk;
  const person = /kge_session=([\w-]+)/.exec(request.headers.cookie || "")?.[1] ?? /^Bearer bearer-([\w-]+)$/.exec(request.headers["x-kge-authorization"] || "")?.[1];
  if (!person) return send(401, { detail: "Sign in." });
  if (request.url === "/whoami") return send(200, { id: person, cerebro_roles: ["business"] });
  if (request.url === "/readable-units") return send(200, { units: [] });
  if (request.url === "/session/bearer") return send(200, { access_token: `bearer-${person}`, token_type: "bearer", expires_in: 900 });
  if (request.url === "/golden") return send(200, { sets: [], retention_days: 30 });
  if (request.url.startsWith("/mcp")) {
    const call = JSON.parse(body);
    return send(200, { jsonrpc: "2.0", id: call.id, result: { structuredContent: FIGURE, isError: false } });
  }
  return send(404, {});
});
const servers = [];
let kgeUrl;
before(async () => { await new Promise((resolve) => kge.listen(0, "127.0.0.1", resolve)); kgeUrl = `http://127.0.0.1:${kge.address().port}`; });
after(() => { kge.close(); for (const server of servers) server.close(); });

async function portal(store = memoryStore()) {
  const repository = openSqliteRepository({ PORTAL_SQLITE_PATH: ":memory:" });
  const { app } = createApp({ repository, env: { KGE_URL: kgeUrl, PORTAL_SYNC_TOKEN: "s".repeat(32), PORTAL_ASK_MODEL: "gemini-2.5-flash", PORTAL_ASK_MODEL_PROVIDER: "mock", PORTAL_SQLITE_PATH: ":memory:" },
    serviceAuth: { headers: async () => ({}) }, catalogueSync: false, uiPath: "not-present", cerebroModel: asker(), tracing: null, conversationStore: store });
  const server = app.listen(0, "127.0.0.1");
  servers.push(server);
  await new Promise((resolve) => server.once("listening", resolve));
  const base = `http://127.0.0.1:${server.address().port}`;
  const as = (person) => ({
    async stream(body, raw) {
      const response = await fetch(`${base}/api/cerebro/stream`, { method: "POST", headers: { "content-type": "application/json", cookie: `kge_session=${person}` }, body: raw ?? JSON.stringify(body) });
      if (response.status !== 200) return { status: response.status, body: await response.json() };
      const frames = (await response.text()).split("\n\n").filter(Boolean).map((frame) => [frame.match(/^event: (.*)$/m)[1], JSON.parse(frame.match(/^data: (.*)$/m)[1])]);
      return { status: 200, frames };
    },
    ask(thread, content, id) {
      return this.stream({ input: { messages: [{ type: "human", id, content }] }, config: { configurable: { thread_id: thread } } });
    },
    async verdict(thread, message, body, raw) {
      const response = await fetch(`${base}/api/cerebro/conversations/${thread}/replies/${message}/verdict`, { method: "PUT", headers: { "content-type": "application/json", cookie: `kge_session=${person}` }, body: raw ?? JSON.stringify(body) });
      return { status: response.status, body: await response.json() };
    },
    async open(thread) {
      const response = await fetch(`${base}/api/cerebro/conversations/${thread}`, { headers: { cookie: `kge_session=${person}` } });
      return { status: response.status, body: await response.json() };
    },
  });
  return { base, as };
}

test("B2: PUT .../replies/:message/verdict saves Like, Dislike or none on the reply's record; 400 for a bad body, 404 for no such reply or another person's, 503 when the store fails (#651)", async () => {
  const store = memoryStore();
  const site = await portal(store);
  const ana = site.as("ana");
  const thread = newThread();
  const asked = await ana.ask(thread, "Revenue for June 2025?", "h-1");
  assert.equal(asked.status, 200);
  assert.deepEqual(asked.frames.filter(([e]) => e === "custom").map(([, d]) => d.kind).slice(-2), ["outcome", "reply"]);
  assert.equal(asked.frames.filter(([e]) => e === "values").at(-1)[1].messages.find((m) => m.type === "tool").card, true, "B4: the figure's tool message is a card");
  assert.deepEqual(await ana.verdict(thread, "h-1", { verdict: "like" }), { status: 200, body: { verdict: "like" } });
  assert.equal((await ana.open(thread)).body.replies["h-1"].verdict, "like");
  assert.deepEqual(await ana.verdict(thread, "h-1", { verdict: "dislike" }), { status: 200, body: { verdict: "dislike" } });
  assert.deepEqual(await ana.verdict(thread, "h-1", { verdict: null }), { status: 200, body: { verdict: null } });
  assert.equal("verdict" in (await ana.open(thread)).body.replies["h-1"], false);
  for (const body of [{}, { verdict: "love" }, { verdict: 1 }, [], "like"]) assert.equal((await ana.verdict(thread, "h-1", body)).status, 400, JSON.stringify(body));
  const notJson = await ana.verdict(thread, "h-1", undefined, "{not json");
  assert.deepEqual(notJson, { status: 400, body: { detail: "Invalid request body." } });
  for (const [id, message] of [[newThread(), "h-1"], [thread, "h-none"], ["not-a-uuid", "h-1"], [thread, "bad%20id"], [thread, "__proto__"], [thread, "constructor"], [thread, "prototype"]]) {
    assert.equal((await ana.verdict(id, message, { verdict: "like" })).status, 404, `${id} ${message}`);
  }
  assert.equal(({}).verdict, undefined, "no verdict on Object.prototype");
  const bo = site.as("bo");
  assert.equal((await bo.verdict(thread, "h-1", { verdict: "like" })).status, 404, "another person's conversation is not there");
  assert.equal("verdict" in (await ana.open(thread)).body.replies["h-1"], false);
  // The store down: 503, as the other routes.
  const broken = await portal({ ...memoryStore(), read: async () => { throw new Error("store down"); } });
  const down = await quietly(() => broken.as("ana").verdict(thread, "h-1", { verdict: "like" }));
  assert.equal(down.status, 503);
  assert.match(down.body.detail, /cannot read conversations right now/);
});

test("A2: a refusal before the stream keeps its status and { detail }, never a stream (#651)", async () => {
  const site = await portal();
  const ana = site.as("ana");
  const thread = newThread();
  const rewindNowhere = await ana.stream({ input: { messages: [{ type: "human", id: "h-2", content: "q" }] }, config: { configurable: { thread_id: thread } }, context: { rewind: "h-1" } });
  assert.deepEqual(rewindNowhere, { status: 404, body: { detail: "No such conversation." } });
  await ana.ask(thread, "Revenue?", "h-1");
  const unknown = await ana.stream({ input: { messages: [{ type: "human", id: "h-3", content: "q" }] }, config: { configurable: { thread_id: thread } }, context: { rewind: "h-none" } });
  assert.deepEqual(unknown, { status: 400, body: { detail: "This conversation has no such question to go back to." } });
  assert.deepEqual(await ana.stream(undefined, "{not json"), { status: 400, body: { detail: "Invalid request body." } });
  const broken = await portal({ ...memoryStore(), read: async () => { throw new Error("store down"); } });
  const down = await quietly(() => broken.as("ana").ask(newThread(), "q", "h-1"));
  assert.equal(down.status, 503);
  assert.match(down.body.detail, /Conversations cannot be read right now/);
  assert.ok(new AIMessage("x"));
});
