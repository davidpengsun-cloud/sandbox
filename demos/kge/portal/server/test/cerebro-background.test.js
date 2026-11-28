import assert from "node:assert/strict";
import { test } from "node:test";
import { NARROWER, RUN_GONE, STOPPED, cerebroAgent, historyOf, pendingRun, savedConversation } from "../src/cerebro/agent.js";
import { TURN_HOLD_MS, followRun } from "../src/cerebro/runs.js";
import { openConversations } from "../src/conversations/index.js";
import { memoryStore } from "../src/conversations/memory.js";
import { errorOf } from "../src/cerebro/status.js";
import { kgeStub, standIn } from "./fixtures/cerebro-stand-in.js";

// #630: Cerebro runs a long warehouse question in the background and picks it up again. A stand-in model and a stubbed
// KGE (synthetic data only): KGE's ask gives a run where its answer is not ready within QUESTION_START_WAIT, and
// question_run follows it. The portal keeps the run's id in the saved conversation, follows it as the person, writes its
// ending in place of the pending card, and stops it on Stop.

const PERSON = "reader";
const RUN = "feedc0de".repeat(4);
const SLOW = "Revenue by branch for every day of 2025";
const FAST = "Total retail revenue for May 2025";
const figure = (question) => ({ question, behaviour: "figure", figure: 42, answer_text: `Certified: ${question}: USD 42.00.`, answer_mode: "certified", sql: "SELECT 42", sql_certified: true, receipt: "r", history: [{ turn_id: "t-1", question }] });
const running = { id: RUN, state: "running", poll_after: 2, expires_at: 1_900_000_000, events: [], cursor: 0 };

/** KGE: a slow question's ask gives its run; question_run and stop_question_run answer from `run` (a function). */
function kge({ run = () => ({ ...running }), stop = () => ({ ...running, state: "failed", reason: "stopped", error: "Stopped." }) } = {}) {
  return kgeStub((name, args) => {
    if (name === "ask") return { value: args.question === SLOW ? { run: { ...running } } : figure(args.question) };
    if (name === "question_run") return run(args);
    if (name === "stop_question_run") return { value: stop(args) };
    return { value: {} };
  });
}
const asks = () => standIn((messages) => {
  const human = messages.findLast((m) => m._getType() === "human");
  const asked = messages.slice(messages.indexOf(human)).some((m) => m._getType() === "tool" && m.name === "ask");
  return asked ? { say: "unused" } : { call: ["ask", { question: human.content }] };
});

async function turn(agent, { thread, question, roles = ["business"] }) {
  const events = [];
  for await (const event of agent({ thread, messages: [{ type: "human", content: question, id: `h-${events.length}-${Math.random()}` }], roles, req: { portalIdentity: { principal: PERSON } } })) events.push(event);
  return { events, outcome: events.filter(([mode, data]) => mode === "custom" && data?.kind === "outcome").at(-1)?.[1] };
}
const pendingOf = async (conversations, thread) => (await savedConversation(conversations, PERSON, thread)).messages.find((m) => m.type === "tool" && m.name === "ask" && pendingRun(JSON.parse(m.content)));
const follow = (conversations, stub, thread, messageId, extra = {}) => followRun({ conversations, kge: stub, req: { portalIdentity: { principal: PERSON } }, roles: ["business"], id: thread, messageId, ...extra });

test("every Cerebro ask is sent with background: true; a fast answer is the answer card as before", async () => {
  const stub = kge();
  const conversations = openConversations();
  const agent = cerebroAgent({ model: asks(), kge: stub, conversations, retryAfterMs: 0 });
  const thread = globalThis.crypto.randomUUID();
  const { outcome } = await turn(agent, { thread, question: FAST });
  assert.equal(stub.calls.find((c) => c.name === "ask").args.background, true);
  assert.equal(outcome.outcome, "answered");
  const saved = await savedConversation(conversations, PERSON, thread);
  assert.equal(JSON.parse(saved.messages.at(-1).content).answer_text, figure(FAST).answer_text);
});

test("a slow answer ends the turn at the pending card: the run's id, the question and when it was sent are saved, with no second model call", async () => {
  const stub = kge();
  const model = asks();
  const conversations = openConversations();
  const agent = cerebroAgent({ model, kge: stub, conversations, retryAfterMs: 0 });
  const thread = globalThis.crypto.randomUUID();
  const before = Date.now();
  const { outcome } = await turn(agent, { thread, question: SLOW });
  assert.equal(outcome.outcome, "background");
  assert.equal(model.calls, 1, "the pending card is the reply: the model is not called again");
  const pending = await pendingOf(conversations, thread);
  const value = JSON.parse(pending.content);
  assert.equal(value.run.id, RUN);
  assert.equal(value.run.state, "running");
  assert.equal(value.run.poll_after, 2);
  assert.equal(value.question, SLOW);
  assert.ok(value.run.started_at >= before && value.run.started_at <= Date.now());
  assert.ok(!("events" in value.run), "the run's events are not kept");
});

test("the chat stays free: the next question goes ahead while the run is pending, and its ask skips the pending card's history", async () => {
  const stub = kge();
  const conversations = openConversations();
  const agent = cerebroAgent({ model: asks(), kge: stub, conversations, retryAfterMs: 0 });
  const thread = globalThis.crypto.randomUUID();
  await turn(agent, { thread, question: FAST });
  await turn(agent, { thread, question: SLOW });
  const { outcome } = await turn(agent, { thread, question: "Total retail revenue for June 2025" });
  assert.equal(outcome.outcome, "answered");
  const third = stub.calls.filter((c) => c.name === "ask").at(-1);
  assert.deepEqual(third.args.history, figure(FAST).history, "the last answer's history, past the pending card");
  assert.ok(await pendingOf(conversations, thread), "the pending card stays until its run ends");
});

test("following a run: running while it runs, then done: the answer card takes the pending card's place in the saved conversation", async () => {
  let state = "running";
  const stub = kge({ run: (args) => ({ value: state === "running" ? { ...running, cursor: args.after } : { ...running, state: "done", answer: figure(SLOW), cursor: 3 } }) });
  const conversations = openConversations();
  const agent = cerebroAgent({ model: asks(), kge: stub, conversations, retryAfterMs: 0 });
  const thread = globalThis.crypto.randomUUID();
  await turn(agent, { thread, question: SLOW });
  const pending = await pendingOf(conversations, thread);
  const first = await follow(conversations, stub, thread, pending.id, { after: 2 });
  assert.deepEqual(first, { state: "running", poll_after: 2, expires_at: running.expires_at, cursor: 2 });
  const asked = stub.calls.filter((c) => c.name === "question_run");
  assert.deepEqual(asked.map((c) => c.args), [{ run_id: RUN, after: 2 }], "the run's id from the saved conversation, as the person");
  assert.equal(asked[0].mount, "/mcp/answer");

  state = "done";
  const ended = await follow(conversations, stub, thread, pending.id);
  assert.equal(ended.state, "ended");
  assert.equal(ended.saved, true);
  assert.equal(JSON.parse(ended.message.content).answer_text, figure(SLOW).answer_text);
  const saved = await savedConversation(conversations, PERSON, thread);
  const at = saved.messages.findIndex((m) => m.id === pending.id);
  assert.ok(at > 0, "the same message, in the same place");
  assert.equal(JSON.parse(saved.messages[at].content).answer_text, figure(SLOW).answer_text);
  assert.equal(saved.interrupt, null);
  assert.equal(await pendingOf(conversations, thread), undefined);
  // The next ask sends the answer's history, as after any answer card.
  await turn(cerebroAgent({ model: asks(), kge: stub, conversations, retryAfterMs: 0 }), { thread, question: FAST });
  assert.deepEqual(stub.calls.filter((c) => c.name === "ask").at(-1).args.history, figure(SLOW).history);
  // Followed again, the ended card is given back as kept, with no call to KGE.
  const calls = stub.calls.length;
  const again = await follow(conversations, stub, thread, pending.id);
  assert.equal(again.state, "ended");
  assert.equal(stub.calls.length, calls);
});

test("every ending is clear: a time limit is the timeout card with KGE's message and the narrowing suggestion; another failure the warehouse error card; Stop is stopped", async () => {
  const cases = [
    [{ state: "expired", reason: "run_time_limit", error: "This question exceeded its time limit; ask a narrower question." }, { failure: "too_long", error: "This question exceeded its time limit; ask a narrower question.", suggestion: NARROWER }],
    [{ state: "failed", reason: "warehouse_time_limit", error: "This question exceeded its time limit; ask a narrower question." }, { failure: "too_long", error: "This question exceeded its time limit; ask a narrower question.", suggestion: NARROWER }],
    [{ state: "failed", reason: "warehouse_failure", error: "Binder Error: column sales.amt_x not found" }, { failure: "warehouse_failed", error: errorOf("warehouse_failed").message }],
    [{ state: "failed", error: "This question could not finish. Please try again." }, { failure: "warehouse_failed", error: errorOf("warehouse_failed").message }],
    [{ state: "failed", reason: "access_changed", error: "Access or the served release changed; ask the question again." }, { failure: "access_changed", error: "Access or the served release changed; ask the question again." }],
  ];
  for (const [ending, expected] of cases) {
    const stub = kge({ run: () => ({ value: { ...running, ...ending } }) });
    const conversations = openConversations();
    const thread = globalThis.crypto.randomUUID();
    await turn(cerebroAgent({ model: asks(), kge: stub, conversations, retryAfterMs: 0 }), { thread, question: SLOW });
    const pending = await pendingOf(conversations, thread);
    const ended = await follow(conversations, stub, thread, pending.id);
    const value = JSON.parse(ended.message.content);
    assert.equal(value.failure, expected.failure, ending.reason ?? ending.state);
    assert.equal(value.error, expected.error);
    assert.equal(value.suggestion, expected.suggestion);
    assert.equal(value.question, SLOW);
    assert.equal(value.run.id, RUN);
    assert.ok(!JSON.stringify(value).includes("amt_x"), "the warehouse's own words never reach a business user");
  }
  // An engineer reads the warehouse's own words.
  const stub = kge({ run: () => ({ value: { ...running, state: "failed", reason: "warehouse_failure", error: "Binder Error: column sales.amt_x not found" } }) });
  const conversations = openConversations();
  const thread = globalThis.crypto.randomUUID();
  await turn(cerebroAgent({ model: asks(), kge: stub, conversations, retryAfterMs: 0 }), { thread, question: SLOW, roles: ["engineer"] });
  const pending = await pendingOf(conversations, thread);
  const ended = await follow(conversations, stub, thread, pending.id, { roles: ["engineer"] });
  assert.match(JSON.parse(ended.message.content).error, /amt_x/);
});

test("a run done with a gate's own words gives a business reader the plain sentence, and an engineer's saved answer keeps it beside them for a later reader (#630 with #625)", async () => {
  const gate = "SQL gate: dry run failed: column sales.amt_x not found";
  const done = { ...running, state: "done", answer: { question: SLOW, behaviour: "abstain", answer_mode: "not_sure", reason: gate, answer_text: `Not answered: ${gate}.` } };
  for (const roles of [["business"], ["engineer"]]) {
    const stub = kge({ run: () => ({ value: done }) });
    const conversations = openConversations();
    const thread = globalThis.crypto.randomUUID();
    await turn(cerebroAgent({ model: asks(), kge: stub, conversations, retryAfterMs: 0 }), { thread, question: SLOW, roles });
    const pending = await pendingOf(conversations, thread);
    const ended = JSON.parse((await follow(conversations, stub, thread, pending.id, { roles })).message.content);
    const later = await savedConversation(conversations, PERSON, thread);
    const read = JSON.parse(later.messages.find((m) => m.id === pending.id).content);
    if (roles[0] === "engineer") assert.equal(ended.reason, gate, "the engineer reads the gate's words");
    else assert.ok(!JSON.stringify(ended).includes("amt_x"), "the business reader never reads the gate's words");
    assert.equal(read.reason, errorOf("unsafe_query").message, roles[0]);
    assert.ok(!JSON.stringify(read).includes("amt_x"), "a later reader who is no engineer reads the plain sentence");
  }
});

test("a time limit's words other than KGE's own are the plain sentence; an access change is always the portal's sentence", async () => {
  const cases = [
    [{ state: "expired", reason: "run_time_limit", error: "Timed out reading sales.amt_x" }, errorOf("too_long").message],
    [{ state: "failed", reason: "warehouse_time_limit", error: "The warehouse exceeded its time limit; ask a narrower question." }, "The warehouse exceeded its time limit; ask a narrower question."],
    [{ state: "failed", reason: "access_changed", error: "Table sales.amt_x is no longer readable" }, "Access or the served release changed; ask the question again."],
  ];
  for (const [ending, error] of cases) {
    const stub = kge({ run: () => ({ value: { ...running, ...ending } }) });
    const conversations = openConversations();
    const thread = globalThis.crypto.randomUUID();
    await turn(cerebroAgent({ model: asks(), kge: stub, conversations, retryAfterMs: 0 }), { thread, question: SLOW });
    const pending = await pendingOf(conversations, thread);
    assert.equal(JSON.parse((await follow(conversations, stub, thread, pending.id)).message.content).error, error, ending.reason);
  }
});

test("Stop cancels the run: KGE's stop_question_run as the person, and the stopped card in place of the pending one", async () => {
  const stub = kge();
  const conversations = openConversations();
  const thread = globalThis.crypto.randomUUID();
  await turn(cerebroAgent({ model: asks(), kge: stub, conversations, retryAfterMs: 0 }), { thread, question: SLOW });
  const pending = await pendingOf(conversations, thread);
  const ended = await follow(conversations, stub, thread, pending.id, { stop: true });
  assert.deepEqual(stub.calls.filter((c) => c.name === "stop_question_run").map((c) => c.args), [{ run_id: RUN }]);
  assert.equal(stub.calls.filter((c) => c.name === "question_run").length, 0);
  const value = JSON.parse(ended.message.content);
  assert.equal(value.failure, "stopped");
  assert.equal(value.error, STOPPED);
  assert.equal(ended.saved, true);
  // A run that finished before Stop reached it is its answer.
  const done = kge({ stop: () => ({ ...running, state: "done", answer: figure(SLOW) }) });
  const other = globalThis.crypto.randomUUID();
  await turn(cerebroAgent({ model: asks(), kge: done, conversations, retryAfterMs: 0 }), { thread: other, question: SLOW });
  const second = await pendingOf(conversations, other);
  const answered = await follow(conversations, done, other, second.id, { stop: true });
  assert.equal(JSON.parse(answered.message.content).answer_text, figure(SLOW).answer_text);
});

test("a reload on another instance picks the run up: a new portal process over the same store follows it and posts the answer", async () => {
  const store = memoryStore();
  const stub = kge({ run: () => ({ value: { ...running, state: "done", answer: figure(SLOW) } }) });
  const first = openConversations({ store });
  const thread = globalThis.crypto.randomUUID();
  await turn(cerebroAgent({ model: asks(), kge: stub, conversations: first, retryAfterMs: 0 }), { thread, question: SLOW });
  const second = openConversations({ store });
  const reopened = await savedConversation(second, PERSON, thread);
  const pending = reopened.messages.find((m) => m.type === "tool" && m.name === "ask");
  assert.ok(pendingRun(JSON.parse(pending.content)), "the reopened conversation holds the pending card");
  const ended = await follow(second, stub, thread, pending.id);
  assert.equal(ended.saved, true);
  const after = await savedConversation(openConversations({ store }), PERSON, thread);
  assert.equal(JSON.parse(after.messages.find((m) => m.id === pending.id).content).answer_text, figure(SLOW).answer_text);
});

test("only the person's own conversation and its own ask calls: another person's conversation, an unknown call or a call that is not an ask is not there to find", async () => {
  const stub = kge();
  const conversations = openConversations();
  const thread = globalThis.crypto.randomUUID();
  await turn(cerebroAgent({ model: asks(), kge: stub, conversations, retryAfterMs: 0 }), { thread, question: SLOW });
  const pending = await pendingOf(conversations, thread);
  await assert.rejects(followRun({ conversations, kge: stub, req: { portalIdentity: { principal: "someone-else" } }, roles: ["business"], id: thread, messageId: pending.id }), { status: 404 });
  await assert.rejects(follow(conversations, stub, thread, "c-unknown"), { status: 404 });
  await assert.rejects(follow(conversations, stub, thread, "../etc"), { status: 404 });
  await assert.rejects(follow(conversations, stub, "not-a-uuid", pending.id), { status: 404 });
  assert.equal(stub.calls.filter((c) => c.name === "question_run").length, 0, "KGE is never asked");
});

test("a run KGE no longer has ends as no longer running; KGE out of reach is asked again later, with nothing written", async () => {
  const gone = kge({ run: () => ({ error: "Error executing tool question_run: 404: Question run not found." }) });
  const conversations = openConversations();
  const thread = globalThis.crypto.randomUUID();
  await turn(cerebroAgent({ model: asks(), kge: gone, conversations, retryAfterMs: 0 }), { thread, question: SLOW });
  const pending = await pendingOf(conversations, thread);
  const ended = await follow(conversations, gone, thread, pending.id);
  assert.equal(JSON.parse(ended.message.content).error, RUN_GONE);

  const down = kge({ run: () => ({ status: 503 }) });
  const other = globalThis.crypto.randomUUID();
  await turn(cerebroAgent({ model: asks(), kge: down, conversations, retryAfterMs: 0 }), { thread: other, question: SLOW });
  const waiting = await pendingOf(conversations, other);
  const unknown = await follow(conversations, down, other, waiting.id);
  assert.equal(unknown.state, "unknown");
  assert.equal(unknown.poll_after, 2);
  assert.ok(await pendingOf(conversations, other), "the pending card stays");
});

test("an ending is never written over a question back to the person: saved is false, and the pause stays", async () => {
  const CLARIFY = "Revenue for the year 2025 please";
  const stub = kgeStub((name, args) => {
    if (name === "ask" && args.question === SLOW) return { value: { run: { ...running } } };
    if (name === "ask") return { value: { behaviour: "clarify", answer_mode: "not_sure", reason: "Which revenue do you mean?", answer_text: "Not sure: which revenue?", clarification: { choices: [{ id: "g", label: "Gross revenue" }, { id: "n", label: "Net revenue" }] } } };
    if (name === "question_run") return { value: { ...running, state: "done", answer: figure(SLOW) } };
    return { value: {} };
  });
  const conversations = openConversations();
  const thread = globalThis.crypto.randomUUID();
  await turn(cerebroAgent({ model: asks(), kge: stub, conversations, retryAfterMs: 0 }), { thread, question: SLOW });
  const pending = await pendingOf(conversations, thread);
  const { outcome } = await turn(cerebroAgent({ model: asks(), kge: stub, conversations, retryAfterMs: 0 }), { thread, question: CLARIFY });
  assert.equal(outcome.outcome, "paused");
  const ended = await follow(conversations, stub, thread, pending.id);
  assert.equal(ended.state, "ended");
  assert.equal(ended.saved, false, "asked again once the pause has gone");
  assert.equal(JSON.parse(ended.message.content).answer_text, figure(SLOW).answer_text, "the page still shows the answer");
  const saved = await savedConversation(conversations, PERSON, thread);
  assert.ok(saved.interrupt?.length, "the question back to the person still waits");
  assert.ok(await pendingOf(conversations, thread), "nothing written");
});

test("historyOf walks past a pending card, as past an error", () => {
  const msg = (content) => ({ _getType: () => "tool", name: "ask", content: JSON.stringify(content) });
  const answered = figure(FAST);
  assert.deepEqual(historyOf([msg(answered), msg({ run: { id: RUN, state: "running" }, question: SLOW })]), answered.history);
});

test("a follow or a Stop that lands while another turn of the conversation runs never ends that turn: nothing is written until it has", async () => {
  for (const stop of [false, true]) {
    let release;
    let gate;
    const stub = kge({ run: () => ({ value: { ...running, state: "done", answer: figure(SLOW) } }) });
    const model = standIn(() => ({}));
    model.invoke = async (messages) => {
      const human = messages.findLast((m) => m._getType() === "human");
      const asked = messages.slice(messages.indexOf(human)).some((m) => m._getType() === "tool" && m.name === "ask");
      if (gate && !asked) await gate;
      return asks().invoke(messages);
    };
    const conversations = openConversations();
    const thread = globalThis.crypto.randomUUID();
    const agent = cerebroAgent({ model, kge: stub, conversations, retryAfterMs: 0 });
    await turn(agent, { thread, question: SLOW });
    const pending = await pendingOf(conversations, thread);
    gate = new Promise((resolve) => { release = resolve; });
    const second = turn(agent, { thread, question: "Total retail revenue for May 2025" });
    await new Promise((resolve) => { setTimeout(resolve, 100); });
    const ended = await follow(conversations, stub, thread, pending.id, { stop });
    assert.equal(ended.state, "ended");
    assert.equal(ended.saved, false, "asked again once the turn has ended");
    release();
    const { outcome } = await second;
    assert.equal(outcome.outcome, "answered", "the turn under way is not ended");
    assert.ok(await pendingOf(conversations, thread), "the pending card waits for the next follow");
    const after = await follow(conversations, stub, thread, pending.id);
    assert.equal(after.saved, true);
  }
});

test("a turn that failed leaves a node due in its checkpoint: the ending waits TURN_HOLD_MS from its last save, then is kept", async () => {
  for (const [ago, saved] of [[0, false], [TURN_HOLD_MS + 60_000, true]]) {
    const stub = kge({ run: () => ({ value: { ...running, state: "done", answer: figure(SLOW) } }) });
    const conversations = openConversations({ now: () => Date.now() - ago });
    const thread = globalThis.crypto.randomUUID();
    await turn(cerebroAgent({ model: asks(), kge: stub, conversations, retryAfterMs: 0 }), { thread, question: SLOW });
    const pending = await pendingOf(conversations, thread);
    const failing = standIn(() => ({}));
    failing.invoke = async () => { throw Object.assign(new Error("The model is down."), { status: 400 }); };
    const failed = await turn(cerebroAgent({ model: failing, kge: stub, conversations, retryAfterMs: 0 }), { thread, question: "Total retail revenue for May 2025" })
      .then(() => undefined, (error) => error);
    assert.equal(failed?.outcome?.outcome, "failed", "the turn failed (the route writes this outcome)");
    const ended = await follow(conversations, stub, thread, pending.id);
    assert.equal(ended.saved, saved, ago ? "a failed turn long since: kept" : "a turn just saved may still be under way: asked again later");
    assert.equal(Boolean(await pendingOf(conversations, thread)), !saved);
  }
});

test("background follow resumes the server-owned question trace after the original request ends", async (t) => {
  const { setupTracing, traceRequest, traceHeaders, traceMetadata } = await import("../src/tracing.js");
  const { InMemorySpanExporter } = await import("@opentelemetry/sdk-trace");
  const { trace, propagation } = await import("@opentelemetry/api");
  trace.disable(); propagation.disable();
  const exporter = new InMemorySpanExporter();
  const provider = setupTracing({ TRACING: "console" }, { exporter });
  t.after(async () => { await provider.shutdown(); trace.disable(); propagation.disable(); });
  const stub = kge();
  const conversations = openConversations();
  const thread = globalThis.crypto.randomUUID();
  const initial = { headers: {}, portalIdentity: { principal: PERSON } };
  const res = { statusCode: 200, end() {}, once() {} };
  traceRequest(provider, initial, res, "POST", "/api/cerebro/stream");
  const destination = "https://kge.example";
  const original = traceHeaders(destination, initial.traceContext, new Set([destination])).traceparent;
  const agent = cerebroAgent({ model: asks(), kge: stub, conversations, retryAfterMs: 0 });
  for await (const _ of agent({ thread, messages: [{ type: "human", content: SLOW, id: "background-human" }], roles: ["business"], req: initial })) { /* consume */ }
  res.end();
  const pending = await pendingOf(conversations, thread);
  const followReq = { headers: { traceparent: "00-abcdefabcdefabcdefabcdefabcdefab-abcdefabcdefabcd-01" }, portalIdentity: { principal: PERSON }, traceResumeAllowed: true };
  let restored;
  await followRun({ conversations, kge: stub, req: followReq, roles: ["business"], id: thread, messageId: pending.id,
    issueQuestion: async (req) => { restored = { parent: traceHeaders(destination, req.traceContext, new Set([destination])).traceparent, metadata: traceMetadata(req.traceContext) }; return "token"; } });
  assert.equal(restored.parent, original);
  assert.equal(restored.metadata["x-kge-trace-session"], thread);
  assert.equal(restored.metadata["x-kge-trace-agent"], "cerebro");
  assert.equal(stub.calls.at(-1).name, "question_run");
  await provider.forceFlush();
  assert.equal(exporter.getFinishedSpans().length, 1);
});
