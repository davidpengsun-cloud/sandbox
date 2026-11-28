import assert from "node:assert/strict";
import { test } from "node:test";
import { AIMessage, HumanMessage } from "@langchain/core/messages";
import { MemorySaver } from "@langchain/langgraph";
import { CHOICE_OPTION_MAX, CHOICE_QUESTION_MAX, CHOICE_REFUSED, NOT_ANSWERED, NOT_OFFERED, NO_CONVERSATION, ONE_AT_A_TIME, RESTATE, TURN_ENDED, cerebroAgent, outcomeOf } from "../src/cerebro/agent.js";
import { conversationKey, memoryStore, openConversations, ownerOf } from "../src/conversations/index.js";
import { NO_THANKS, OFFER_QUESTION, comparisonQuestion, offerOf, offerPick, termQuestion, termsOf } from "../src/cerebro/offer.js";
import { givesFigure } from "../src/cerebro/status.js";
import { buildGraph, savedConversation } from "../src/cerebro/agent.js";
import { readTurn } from "../src/cerebro/route.js";
import { OPTION_LABEL_MAX, QUESTIONS, QUESTIONS_HEADING, SUGGESTED, acceptPicks, questionsCard, questionsFromPicks, visibleGroups } from "../src/cerebro/questions.js";
import { isStepRead, kgeReading, stepReads, ONE_GROUP_QUESTION, RETAIL_QUESTION, SAMPLE_MEANINGS, SAMPLE_PERIODS, SAMPLE_SEGMENTS, SAMPLE_SHOW, SHOW_QUESTION, sampleQuestions } from "./fixtures/cerebro-stand-in.js";

// #624: the one HITL card (the model's own choose call, Ask's clarification), the "Compare the figures?" offer after a
// difference answer (#617's per-term period ranges), and routing a difference between two terms to ask. A stand-in
// model and a stubbed KGE client (the portal's kge object: sessionBearer and request), synthetic data only. #625: every
// turn is saved in the conversations below, so a pick resumes the turn it answers from the server's own checkpoint.
const store = memoryStore();
const conversations = openConversations({ store });

// A stand-in model: decide(messages, n) gives each call's step ({ call: [name, args] }, { calls: [...] } or { say }).
function standIn(decide) {
  const model = { calls: 0, read: [] };
  model.bindTools = (definitions) => { model.offered = definitions.map((d) => d.function.name); return model; };
  model.invoke = async (messages) => {
    model.calls += 1;
    model.read.push(messages);
    const step = decide(messages, model.calls);
    if (step.call) return new AIMessage({ content: "", tool_calls: [{ id: `c-${model.calls}`, name: step.call[0], args: step.call[1] }] });
    if (step.calls) return new AIMessage({ content: "", tool_calls: step.calls.map(([name, args], i) => ({ id: `c-${model.calls}-${i}`, name, args })) });
    return new AIMessage(step.say ?? "");
  };
  return model;
}
const toolsThisTurn = (messages) => messages.slice(messages.findLastIndex((m) => m._getType() === "human")).filter((m) => m._getType() === "tool");
const asksOnce = (question) => standIn((messages) => (toolsThisTurn(messages).length ? { say: "I could not find that." } : { call: ["ask", { question }] }));

// The stubbed KGE client: reply(name, args) gives { value } or { status }; every call is recorded.
function kgeStub(reply, { discovery = false, step = stepReads() } = {}) {
  const calls = [];
  const reads = [];
  return {
    calls,
    reads,
    sessionBearer: async () => "bearer-1",
    request: async (_ctx, mount, init) => {
      const message = JSON.parse(init.body);
      const { name, arguments: args } = message.params;
      // #714: the step before ask's own reads are answered by step and kept apart from the calls a test counts.
      if (isStepRead(name, args)) {
        reads.push({ mount, name, args });
        return new Response(JSON.stringify({ jsonrpc: "2.0", id: message.id, result: { structuredContent: step(name, args).value, isError: false } }), { status: 200, headers: { "content-type": "application/json" } });
      }
      if (name === "search" && !discovery) return new Response(JSON.stringify({ jsonrpc: "2.0", id: message.id, result: { structuredContent: { hits: [] }, isError: false } }), { status: 200, headers: { "content-type": "application/json" } });
      calls.push({ mount, name, args });
      const out = reply(name, args) ?? { value: {} };
      if (out.status) return new Response("{}", { status: out.status, headers: { "content-type": "application/json" } });
      return new Response(JSON.stringify({ jsonrpc: "2.0", id: message.id, result: { structuredContent: out.value, isError: false } }), { status: 200, headers: { "content-type": "application/json" } });
    },
  };
}

/** One turn of the conversation thread names (a new one unless named): the person's question, or their answer to the
 *  card (resume). */
// #621: the person's message is a full, standalone question by default, so the portal frames none of these turns.
async function turn(model, kge, { roles = ["business"], question = "What was revenue in June 2025?", thread = globalThis.crypto.randomUUID(), resume, principal = "person-1" } = {}) {
  const events = [];
  const runTurn = cerebroAgent({ model, kge, conversations, retryAfterMs: 0 });
  const asked = resume === undefined ? { messages: [{ type: "human", content: question }] } : { resume };
  for await (const event of runTurn({ thread, ...asked, roles, signal: new AbortController().signal, req: { portalIdentity: { principal } } })) events.push(event);
  // #651: the reply's record follows the outcome; the outcome is the last event before it.
  const custom = events.filter(([e]) => e === "custom").map(([, d]) => d).filter((d) => d.kind !== "reply");
  const values = events.filter(([e]) => e === "values").at(-1)?.[1];
  return { events, outcome: custom.at(-1), values, paused: events.some(([, d]) => d?.__interrupt__), interrupt: values?.__interrupt__?.[0]?.value, thread };
}
/** A copy of a turn's saved conversation under a new id, so one paused or offered point can be answered more than once. */
async function fork(result, principal = "person-1") {
  const thread = globalThis.crypto.randomUUID();
  const saved = await store.read(conversationKey(ownerOf(principal), result.thread));
  await store.write(conversationKey(ownerOf(principal), thread), saved.body, { ifGeneration: "0", metadata: saved.metadata });
  return thread;
}
const toolResult = (message) => JSON.parse(message.content);

// #617's difference answer: both definitions, how they differ, and the months each term's data covers.
const term = (name, first, latest) => ({ term: name, definition: `${name} as declared.`, period_range: { first, latest, released_months: true, words: `${first} to ${latest}` } });
const DIFFERENCE = {
  behaviour: "cite_certified", answer_mode: "certified", answer_text: "Certified: current NNA and certified NNA differ in their version dates.",
  difference: { terms: [term("current NNA", "2024-01-01", "2025-06-30"), term("certified NNA", "2024-04-01", "2025-08-01")], rows: [], summary: "They differ in their version dates." },
  history: [{ turn_id: "t-1", question: "What is the difference between current NNA and certified NNA?" }],
};
const FIGURES = { behaviour: "figure", answer_mode: "certified", answer_text: "Certified: current NNA was USD 12 and certified NNA USD 11.", sql: "SELECT 1", sql_certified: true };
const ONE_TERM = { behaviour: "figure", answer_mode: "certified", answer_text: "Certified: it was USD 12.", sql: "SELECT 2", sql_certified: true };
const DIFF_QUESTION = "What is the difference between current NNA and certified NNA?";

/** A turn that answers the difference question and posts the offer; its conversation, for the page to send back. */
async function offered(reply = DIFFERENCE) {
  const model = asksOnce(DIFF_QUESTION);
  const kge = kgeStub(() => ({ value: reply }));
  const result = await turn(model, kge, { question: `${DIFF_QUESTION} Compare definitions.` });
  return { ...result, model, kge };
}

test("cancel the replayed offer without asking or changing the answered comparison", async () => {
  const initial = await offered();
  const callCount = initial.kge.calls.length;
  const modelCalls = initial.model.calls;
  const cancelled = await turn(initial.model, initial.kge, { thread: initial.thread, resume: { cancel: true } });
  assert.equal(initial.kge.calls.length, callCount);
  assert.equal(initial.model.calls, modelCalls);
  assert.deepEqual(toolResult(cancelled.values.messages.at(-1)), { cancelled: true });
  assert.deepEqual(cancelled.values.messages.slice(0, initial.values.messages.length), initial.values.messages);
  assert.equal(cancelled.paused, false);
});

test("#624 requirement 7: the offer follows the difference card, ends the turn with no pause, and holds no figure", async () => {
  const { values, outcome, paused, model } = await offered();
  assert.equal(model.calls, 1, "no second model call");
  assert.equal(paused, false, "the offer never pauses the turn");
  assert.deepEqual(outcome, { kind: "outcome", outcome: "answered", steps: 3 });
  assert.deepEqual(values.messages.map((m) => [m.type, m.name ?? m.tool_calls?.map((c) => c.name).join()]), [["human", undefined], ["ai", "ask"], ["tool", "ask"], ["ai", "choose"]]);
  const offer = values.messages.at(-1).tool_calls[0];
  assert.match(offer.id, /^offer-/);
  assert.equal(offer.args.source, "offer");
  assert.equal(offer.args.question, "Compare the figures?");
  assert.equal(offer.args.ask_call, values.messages[1].tool_calls[0].id);
  assert.deepEqual(offer.args.options, [{ id: "latest_month", label: "June 2025" }, { id: "last_quarter", label: "Q2 2025" }, { id: "no", label: "No thanks" }]);
  // Its choices lie within both terms' ranges: April 2024 (the later first) to June 2025 (the earlier latest).
  assert.equal(offer.args.months.length, 15);
  assert.equal(offer.args.months[0], "2024-04");
  assert.equal(offer.args.months.at(-1), "2025-06");
  assert.ok(offer.args.months.every((m) => m >= "2024-04" && m <= "2025-06"));
  for (const words of [offer.args.question, ...offer.args.options.map((o) => o.label), ...offer.args.months]) assert.equal(givesFigure(words), false, words);
  assert.doesNotMatch(JSON.stringify(offer.args), /USD|definition|differ/, "nothing of the answer rides on the offer");
});

test("#624 requirement 7: nothing is offered where the ranges do not overlap, for one term, or after a figure or a definition with no ranges", async () => {
  const apart = { ...DIFFERENCE, difference: { ...DIFFERENCE.difference, terms: [term("current NNA", "2023-01-01", "2023-12-01"), term("certified NNA", "2024-01-01", "2024-06-01")] } };
  const single = { ...DIFFERENCE, difference: { terms: ["current NNA"], unresolved: ["fees"], clarification: "Which term?" } };
  const unranged = { ...DIFFERENCE, difference: { ...DIFFERENCE.difference, terms: [term("current NNA", "2024-01-01", "2025-06-01"), { term: "certified NNA", period_range: null }] } };
  for (const reply of [apart, single, unranged, { behaviour: "glossary_definition", answer_mode: "certified", answer_text: "Certified: NNA is inflows less outflows." }, FIGURES,
    { ...DIFFERENCE, answer_mode: "not_sure" }]) {
    const { values } = await offered(reply);
    assert.equal(values.messages.some((m) => m.tool_calls?.some((c) => c.name === "choose")), false, JSON.stringify(reply.difference ?? reply.behaviour));
  }
});

test("#624 requirement 7: picking a period sends one question in S08's form for that period and posts its card", async () => {
  for (const [resume, asked, label] of [
    [{ pick: "latest_month" }, "Compare current NNA vs. certified NNA for June 2025, with the dollar and percent difference", "June 2025"],
    [{ pick: "last_quarter" }, "Compare current NNA vs. certified NNA for Q2 2025 by month, with the dollar and percent difference", "Q2 2025"],
    [{ month: "2024-11" }, "Compare current NNA vs. certified NNA for November 2024, with the dollar and percent difference", "November 2024"],
  ]) {
    const first = await offered();
    const kge = kgeStub(() => ({ value: FIGURES }));
    const model = standIn(() => ({ say: "unused" }));
    const { values, outcome, events } = await turn(model, kge, { thread: first.thread, resume });
    assert.equal(model.calls, 0, "no model call");
    assert.equal(events.some(([, d]) => d?.__interrupt__), false, "the resumed turn shows no pause");
    assert.deepEqual(kge.calls.map((c) => [c.name, c.args.question]), [["ask", asked]]);
    assert.deepEqual(kge.calls[0].args.history, DIFFERENCE.history, "the difference answer's history goes with it");
    const after = values.messages.slice(first.values.messages.length);
    assert.deepEqual(after.map((m) => [m.type, m.name ?? m.tool_calls?.map((c) => c.name).join()]), [["tool", "choose"], ["ai", "ask"], ["tool", "ask"]]);
    assert.deepEqual(toolResult(after[0]), { picked: { id: resume.pick ?? resume.month, label } });
    assert.match(after[1].tool_calls[0].id, /^pick-/);
    assert.equal(toolResult(after[2]).answer_text, FIGURES.answer_text, "the figures' card posts");
    assert.equal(toolResult(after[2]).sql, "SELECT 1");
    assert.deepEqual(outcome, { kind: "outcome", outcome: "answered", steps: 3 });
  }
});

test("#624 requirement 7: where the comparison gives no figure or table, one question per term, both cards posted together", async () => {
  const clarify = { behaviour: "clarify", answer_mode: "not_sure", answer_text: "Not sure: which book?", clarification: { choices: [{ id: "a", label: "All" }] } };
  const abstain = { behaviour: "abstain_with_reason", answer_mode: "not_sure", answer_text: "Not sure: cannot compare." };
  const definition = { ...DIFFERENCE };
  for (const failed of [clarify, abstain, definition]) {
    const first = await offered();
    const kge = kgeStub((_name, args) => ({ value: args.question.startsWith("Compare") ? failed : ONE_TERM }));
    const { values, outcome, paused } = await turn(standIn(() => ({ say: "unused" })), kge, { thread: first.thread, resume: { pick: "last_quarter" } });
    assert.equal(paused, false);
    assert.deepEqual(kge.calls.map((c) => c.args.question), [
      "Compare current NNA vs. certified NNA for Q2 2025 by month, with the dollar and percent difference",
      "current NNA for Q2 2025 by month", "certified NNA for Q2 2025 by month"]);
    const after = values.messages.slice(first.values.messages.length);
    assert.deepEqual(after.map((m) => [m.type, m.name ?? m.tool_calls?.map((c) => c.name).join()]), [["tool", "choose"], ["ai", "ask,ask"], ["tool", "ask"], ["tool", "ask"]]);
    assert.deepEqual(after[1].tool_calls.map((c) => c.args.question), ["current NNA for Q2 2025 by month", "certified NNA for Q2 2025 by month"]);
    assert.doesNotMatch(JSON.stringify(values.messages), /Compare current NNA/, "the failed comparison is not posted");
    assert.equal(outcome.outcome, "answered");
  }
  // A figure the comparison gives is its card, even not sure: no fallback.
  const unsure = kgeStub(() => ({ value: { behaviour: "figure", answer_mode: "not_sure", answer_text: "Not sure: USD 1 against USD 2.", sql: "SELECT 4" } }));
  const kept = await turn(standIn(() => ({ say: "unused" })), unsure, { thread: (await offered()).thread, resume: { pick: "latest_month" } });
  assert.equal(unsure.calls.length, 1);
  assert.equal(toolResult(kept.values.messages.at(-1)).sql, "SELECT 4");
  // A reply that is a failure though it carries no error (the gate's decline) ends the turn failed, with no fallback.
  const gate = kgeStub(() => ({ value: { behaviour: "abstain_with_reason", answer_mode: "not_sure", reason: "SQL gate refused the query.", answer_text: "Not sure: SQL gate refused the query." } }));
  const declined = await turn(standIn(() => ({ say: "unused" })), gate, { thread: (await offered()).thread, resume: { pick: "latest_month" } });
  assert.equal(gate.calls.length, 1);
  assert.equal(declined.outcome.error.case, "unsafe_query");
  // KGE failing the comparison ends the turn at its error, with no fallback.
  const first = await offered();
  const down = kgeStub(() => ({ status: 502 }));
  const { values, outcome } = await turn(standIn(() => ({ say: "unused" })), down, { thread: first.thread, resume: { pick: "latest_month" } });
  assert.equal(down.calls.length, 1);
  assert.equal(toolResult(values.messages.at(-1)).failure, "kge_unreachable");
  assert.equal(outcome.error.case, "kge_unreachable");
  // An error KGE gives with no failure case (a refused call), a refusal or a blocked access ends the turn failed, with no
  // fallback.
  for (const [reply, message] of [[{ status: 403 }, NOT_ANSWERED], [{ value: { behaviour: "refuse", reason: "Read is not permitted." } }, "Read is not permitted."],
    [{ value: { behaviour: "blocked_access", reason: "Access to this unit is blocked." } }, "Access to this unit is blocked."]]) {
    const stopped = kgeStub(() => reply);
    const out = await turn(standIn(() => ({ say: "unused" })), stopped, { thread: (await offered()).thread, resume: { pick: "latest_month" } });
    assert.equal(stopped.calls.length, 1, JSON.stringify(reply));
    assert.equal(out.outcome.outcome, "failed", JSON.stringify(reply));
    assert.equal(out.outcome.error.message, message);
  }
  // A failure on one of the fallback's two questions is the turn's failure; the other card still posts.
  const half = kgeStub((_name, args) => (args.question.startsWith("Compare") ? { value: abstain } : args.question.startsWith("current") ? { value: ONE_TERM } : { status: 502 }));
  const fallback = await turn(standIn(() => ({ say: "unused" })), half, { thread: (await offered()).thread, resume: { pick: "latest_month" } });
  assert.equal(half.calls.length, 3);
  assert.equal(fallback.outcome.outcome, "answered", "a card in the step wins (#622)");
});

test("#624 requirement 7: No thanks or a typed question moves on; a pick off the offer asks nothing", async () => {
  const first = await offered();
  const none = kgeStub(() => ({ value: FIGURES }));
  const declined = await turn(standIn(() => ({ say: "unused" })), none, { thread: await fork(first), resume: { pick: "no" } });
  assert.equal(none.calls.length, 0);
  assert.deepEqual(toolResult(declined.values.messages.at(-1)), { picked: { id: "no", label: "No thanks" } });
  assert.deepEqual(declined.outcome, { kind: "outcome", outcome: "answered", steps: 1 });

  // A typed question after the offer goes ahead as usual; the offer, unanswered, never reaches the model.
  const typed = standIn((messages) => (toolsThisTurn(messages).length ? { say: "unused" } : { call: ["ask", { question: "NNA for May 2025?" }] }));
  const asked = kgeStub(() => ({ value: ONE_TERM }));
  const next = await turn(typed, asked, { thread: await fork(first), question: "NNA for May 2025?" });
  assert.deepEqual(asked.calls.map((c) => c.args.question), ["NNA for May 2025?"]);
  assert.equal(next.outcome.outcome, "answered");
  assert.equal(typed.read[0].some((m) => m.tool_calls?.some((c) => c.name === "choose")), false, "the unanswered offer is not in the model's prompt");
  assert.ok(typed.read[0].some((m) => m._getType() === "tool" && m.name === "ask"), "the difference answer still is");

  for (const resume of [{ month: "2024-03" }, { month: "2025-07" }, { pick: "next_year" }, { pick: "1" }]) {
    const out = await turn(standIn(() => ({ say: "unused" })), none, { thread: await fork(first), resume });
    assert.equal(none.calls.length, 0, JSON.stringify(resume));
    assert.deepEqual(toolResult(out.values.messages.at(-1)), { error: NOT_OFFERED });
    assert.equal(out.outcome.outcome, "failed");
  }
  // Something else typed on the offer goes to the model.
  const words = standIn((messages) => (messages.at(-1).name === "choose" ? { say: "Which period?" } : { say: "unused" }));
  const other = await turn(words, none, { thread: await fork(first), resume: { other: "the year to date" } });
  assert.equal(words.calls, 1);
  assert.deepEqual(toolResult(other.values.messages.at(-2)), { other: "the year to date" });
  // #625: the offer is answered once; a second answer finds nothing waiting.
  await turn(standIn(() => ({ say: "unused" })), none, { thread: first.thread, resume: { pick: "no" } });
  await assert.rejects(turn(standIn(() => ({ say: "unused" })), none, { thread: first.thread, resume: { pick: "no" } }), (error) => error.status === 400);
  assert.equal(none.calls.length, 0);
});

test("#624 requirement 4: the model's own choose call is the HITL card; a pick or something else goes back to the model", async () => {
  const options = ["Revenue for the wealth book?", "Revenue for the retail book?"];
  const model = standIn((messages) => {
    const last = messages.at(-1);
    if (last._getType() === "human") return { call: ["choose", { question: "Which book?", options }] };
    if (last.name === "choose") return { call: ["ask", { question: JSON.parse(last.content).picked?.label ?? JSON.parse(last.content).other }] };
    return { say: "unused" };
  });
  const kge = kgeStub(() => ({ value: ONE_TERM }));
  const first = await turn(model, kge, { question: "Revenue for the book?" });
  assert.equal(model.offered.at(-1), "choose", "choose is offered beside KGE's tools");
  assert.equal(first.paused, true);
  assert.deepEqual(first.interrupt, { kind: "hitl", tool_call_id: "c-1", source: "agent", question: "Which book?", options: [{ id: "1", label: options[0] }, { id: "2", label: options[1] }], other: true },
    "the model's own call, as it made it");
  assert.deepEqual(first.outcome, { kind: "outcome", outcome: "paused", steps: 1 });
  assert.equal(kge.calls.length, 0, "choose never reaches KGE");

  const picked = await turn(model, kge, { thread: await fork(first), resume: { pick: "2" } });
  assert.deepEqual(toolResult(picked.values.messages.find((m) => m.name === "choose")), { picked: { id: "2", label: options[1] } });
  assert.deepEqual(kge.calls.map((c) => c.args.question), [options[1]]);
  assert.equal(picked.outcome.outcome, "answered");
  const words = await turn(model, kge, { thread: await fork(first), resume: { other: "the advisory book" } });
  assert.equal(kge.calls.at(-1).args.question, "the advisory book");
  assert.equal(words.outcome.outcome, "answered");
});

test("#624 requirement 4: a choose call off its shape, a second at once, or one beside a failure is answered, never paused on", async () => {
  for (const args of [{ question: "Which?", options: ["Only one?"] }, { question: "Which?", options: ["a", "b", "c", "d", "e"] }, { question: "", options: ["a", "b"] },
    { question: "Which?", options: ["a", 2] }, { question: "Which?", options: ["a", "b"], source: "offer" }, null,
    // #619's rule on the model's own words: no figure, a short question and short options.
    { question: "Was it USD 12 or USD 13?", options: ["a", "b"] }, { question: "Which?", options: ["The 12 accounts?", "b"] },
    { question: "Which?", options: ["Revenue for June 2025?", "Revenue of 4.5m?"] },
    { question: `${"W".repeat(CHOICE_QUESTION_MAX)}?`, options: ["a", "b"] }, { question: "Which?", options: [`${"a".repeat(CHOICE_OPTION_MAX)}?`, "b"] }]) {
    const model = standIn((messages) => (messages.at(-1)._getType() === "human" ? { call: ["choose", args] } : { say: "Which book do you mean?" }));
    const { paused, values } = await turn(model, kgeStub(() => undefined));
    assert.equal(paused, false, JSON.stringify(args));
    assert.deepEqual(toolResult(values.messages.find((m) => m.type === "tool")), { error: CHOICE_REFUSED });
    assert.equal(model.calls, 2, "the model reads the refusal and speaks");
  }
  assert.deepEqual([CHOICE_QUESTION_MAX, CHOICE_OPTION_MAX], [200, 80], "the limits the review set");
  // At the limits, and with a period's numbers, a card is asked.
  for (const args of [{ question: `${"W".repeat(CHOICE_QUESTION_MAX - 1)}?`, options: [`${"a".repeat(CHOICE_OPTION_MAX - 1)}?`, "b"] },
    { question: "Which month?", options: ["Revenue for June 2025?", "Revenue for Q2 2025?"] }]) {
    const model = standIn((messages) => (messages.at(-1)._getType() === "human" ? { call: ["choose", args] } : { say: "x" }));
    const { paused, interrupt } = await turn(model, kgeStub(() => undefined));
    assert.equal(paused, true, JSON.stringify(args));
    assert.deepEqual(interrupt.options.map((o) => o.label), args.options);
  }
  const two = standIn((messages) => (messages.at(-1)._getType() === "human" ? { calls: [["choose", { question: "A?", options: ["a?", "b?"] }], ["choose", { question: "B?", options: ["c?", "d?"] }]] } : { say: "x" }));
  const first = await turn(two, kgeStub(() => undefined));
  assert.equal(first.paused, true);
  assert.equal(first.interrupt.question, "A?");
  assert.deepEqual(toolResult(first.values.messages.find((m) => m.type === "tool")), { error: ONE_AT_A_TIME });
  const failing = standIn(() => ({ calls: [["choose", { question: "A?", options: ["a?", "b?"] }], ["ask", { question: "q" }]] }));
  const failed = await turn(failing, kgeStub(() => ({ status: 502 })));
  assert.equal(failed.paused, false);
  assert.ok(failed.values.messages.some((m) => m.type === "tool" && m.name === "choose" && toolResult(m).error === TURN_ENDED));
  assert.equal(failed.outcome.error.case, "kge_unreachable");
});

test("#624 requirement 4: the clarification's card asks Ask's own plain question; a pick answered by another clarification pauses again", async () => {
  const choices = [{ id: "a", label: "All transactions" }, { id: 7, label: "Card only" }, { id: "x", label: " " }];
  for (const [reply, question] of [[{ reason: "Which meaning of transactions?" }, "Which meaning of transactions?"], [{ answer_text: "Not sure: which transactions?" }, "which transactions?"], [{}, "Which do you mean?"]]) {
    const { interrupt } = await turn(asksOnce("Transactions?"), kgeStub(() => ({ value: { behaviour: "clarify", answer_mode: "not_sure", clarification: { choices }, ...reply } })));
    assert.equal(interrupt.question, question);
    assert.deepEqual(interrupt.options, [{ id: "a", label: "All transactions" }, { id: "7", label: "Card only" }]);
  }
  const clarify = (label) => ({ value: { behaviour: "clarify", answer_mode: "not_sure", answer_text: `Not sure: ${label}?`, clarification: { choices } } });
  const first = await turn(asksOnce("Transactions?"), kgeStub(() => clarify("which")));
  const again = await turn(standIn(() => ({ say: "unused" })), kgeStub(() => clarify("which kind")), { thread: await fork(first), resume: { pick: "a" } });
  assert.equal(again.paused, true);
  assert.equal(again.interrupt.question, "which kind?");
  assert.deepEqual(again.outcome, { kind: "outcome", outcome: "paused", steps: 2 });
  // A pick whose reply is not sure goes back to the model, which may speak.
  const unsure = standIn(() => ({ say: "Which book do you mean?" }));
  const spoke = await turn(unsure, kgeStub(() => ({ value: { behaviour: "abstain_with_reason", answer_mode: "not_sure", answer_text: "Not sure: no term matches." } })), { thread: await fork(first), resume: { pick: "a" } });
  assert.equal(unsure.calls, 1);
  assert.equal(spoke.values.messages.at(-1).content, "Which book do you mean?");
  // A difference answer to a pick is followed by its offer.
  const offer = await turn(standIn(() => ({ say: "unused" })), kgeStub(() => ({ value: DIFFERENCE })), { thread: await fork(first), resume: { pick: "a" } });
  assert.equal(offer.values.messages.at(-1).tool_calls[0].args.source, "offer");
  assert.equal(offer.outcome.outcome, "answered");
});

test("#624: the model reads an answer's SQL only for an engineer; a combined answer's figures too", async () => {
  const combined = { behaviour: "combined", answer_mode: "certified", answer_text: "Certified: two figures.", sql: "SELECT top", figures: [{ label: "a", sql: "SELECT a" }, "odd"] };
  for (const [roles, reads] of [[["business"], false], [["engineer"], true]]) {
    const model = standIn((messages) => (toolsThisTurn(messages).length ? { say: "ok" } : { calls: [["ask", { question: "a" }], ["search", { query: "" }]] }));
    const kge = kgeStub((name) => (name === "ask" ? { value: { ...combined, behaviour: "clarify" } } : { value: { hits: [] } }));
    const { values } = await turn(model, kge, { roles });
    assert.match(JSON.stringify(values.messages), /SELECT a/, "the card carries it for every role");
    assert.equal(/SELECT/.test(JSON.stringify(model.read.map((m) => m.map((x) => x.content)))), reads, roles[0]);
  }
});

test("#624: a turn ended on a clarification the person answered in words is answered, not failed", () => {
  const values = { turnStart: 0, messages: [new AIMessage({ content: "", tool_calls: [{ id: "c", name: "ask", args: { question: "q" } }] }),
    { _getType: () => "tool", name: "ask", tool_call_id: "c", content: JSON.stringify({ behaviour: "clarify", answer_mode: "not_sure", answer_text: "Which?" }) }, new AIMessage("Which book?")] };
  assert.equal(outcomeOf(values).outcome, "answered");
});

test("#624: a turn whose last step's every call failed is never answered", () => {
  const step = (...results) => ({ messages: [new AIMessage({ content: "", tool_calls: results.map((_, i) => ({ id: `p-${i}`, name: "ask", args: {} })) }),
    ...results.map((r, i) => ({ _getType: () => "tool", name: "ask", tool_call_id: `p-${i}`, content: JSON.stringify(r) }))], turnStart: 0 });
  assert.equal(outcomeOf(step({ error: "The knowledge graph refused the ask call (403)." })).outcome, "failed");
  assert.equal(outcomeOf(step({ error: "a" }, { error: "b" })).error.message, NOT_ANSWERED);
  assert.equal(outcomeOf(step({ error: "a" }, ONE_TERM)).outcome, "answered", "a card in the step wins");
});

test("#624 requirement 7: a daily range counts a month only where it covers all of it", () => {
  const daily = (name, first, latest) => ({ term: name, period_range: { first, latest, released_months: false, words: `${first} to ${latest}` } });
  const both = offerOf({ difference: { terms: [daily("A", "2025-01-15", "2025-06-30"), daily("B", "2025-01-01", "2025-07-15")] } });
  assert.deepEqual(both.months, ["2025-02", "2025-03", "2025-04", "2025-05", "2025-06"], "January is only partly A's; July only partly B's");
  assert.deepEqual(both.options.map((o) => o.label), ["June 2025", "Q2 2025", NO_THANKS]);
  const short = offerOf({ difference: { terms: [daily("A", "2025-01-01", "2025-06-29"), daily("B", "2024-01-01", "2025-12-31")] } });
  assert.deepEqual(short.options.map((o) => o.label), ["May 2025", "Q1 2025", NO_THANKS], "June ends a day short");
  assert.equal(short.months.at(-1), "2025-05");
  const leap = offerOf({ difference: { terms: [daily("A", "2024-02-01", "2024-02-29"), daily("B", "2024-01-01", "2024-03-31")] } });
  assert.deepEqual(leap.months, ["2024-02"], "a leap February's last day ends it");
  assert.equal(offerOf({ difference: { terms: [daily("A", "2025-03-02", "2025-03-31"), daily("B", "2025-01-01", "2025-12-31")] } }), undefined, "no whole month: no offer");
  assert.equal(offerOf({ difference: { terms: [daily("A", "2025-03-02", "2025-04-30"), daily("B", "2025-03-01", "2025-03-31")] } }), undefined, "no whole month both cover");
  const released = offerOf({ difference: { terms: [term("A", "2025-01-15", "2025-06-01"), term("B", "2025-01-01", "2025-06-30")] } });
  assert.deepEqual(released.months, ["2025-01", "2025-02", "2025-03", "2025-04", "2025-05", "2025-06"], "released months are whole");
});

test("#624 requirement 7: offerOf, offerPick and the questions, on their own", () => {
  assert.deepEqual(offerOf({ difference: { terms: [term("A", "2025-05-15", "2025-06-30"), term("B", "2025-01", "2025-12-01")] } }),
    { question: OFFER_QUESTION, options: [{ id: "latest_month", label: "June 2025" }, { id: "no", label: NO_THANKS }], months: ["2025-05", "2025-06"] }, "no whole quarter: none offered");
  const across = offerOf({ difference: { terms: [term("A", "2024-10-01", "2025-02-01"), term("B", "2024-01-01", "2025-08-01")] } });
  assert.deepEqual(across.options.map((o) => o.label), ["February 2025", "Q4 2024", NO_THANKS], "the last whole quarter, across a year's end");
  for (const bad of [null, {}, { difference: { terms: [] } }, { difference: { terms: [term("A", "2025-13-01", "2025-12-01"), term("B", "2025-01-01", "2025-02-01")] } },
    { difference: { terms: [term("A", "2025-03-01", "2025-01-01"), term("B", "2025-01-01", "2025-02-01")] } }, { difference: { terms: [term(" ", "2025-01-01", "2025-02-01"), term("B", "2025-01-01", "2025-02-01")] } },
    { difference: { terms: [{ term: "A", period_range: { first: 20250101, latest: "2025-02-01" } }, term("B", "2025-01-01", "2025-02-01")] } }]) {
    assert.equal(offerOf(bad), undefined, JSON.stringify(bad));
  }
  assert.equal(termsOf("text"), undefined);
  const offer = offerOf(DIFFERENCE);
  assert.equal(offerPick(offer, null), undefined);
  assert.equal(offerPick(undefined, { pick: "no" }), undefined);
  assert.deepEqual(offerPick(offer, { pick: "no" }), { id: "no", label: NO_THANKS, no: true });
  const terms = termsOf(DIFFERENCE);
  const quarter = offerPick(offer, { pick: "last_quarter" });
  assert.equal(comparisonQuestion(terms, quarter), "Compare current NNA vs. certified NNA for Q2 2025 by month, with the dollar and percent difference");
  assert.equal(termQuestion(terms[1], offerPick(offer, { month: "2025-01" })), "certified NNA for January 2025");
});

// #624 requirement 8: a stand-in that routes by the prompt's own rule: each quoted pattern (X and Y standing for the
// person's words) in a sentence that says which tool to call. It holds what the prompt says, not what a model does:
// routing by a real model is not held by a unit test.
function routedByPrompt() {
  return standIn((messages) => {
    if (toolsThisTurn(messages).length) return { say: "unused" };
    const question = messages.findLast((m) => m._getType() === "human").content;
    for (const sentence of messages[0].content.split(/(?<=\.)\s+/)) {
      const tool = /\bcall (\w+)/.exec(sentence)?.[1];
      if (!tool) continue;
      for (const [, pattern] of sentence.matchAll(/"([^"]*\bX\b[^"]*)"/g)) {
        const shape = pattern.replace(/[.*+?^${}()|[\]\\]/g, "\\$&").replace(/\b[XY]\b/g, "(.+)");
        if (new RegExp(`^${shape}\\??$`, "i").test(question)) return { call: [tool, { question }] };
      }
    }
    return { say: "No rule matched." };
  });
}

test("#624 requirement 8: the prompt routes the quoted difference patterns to ask and one term's meaning to get_context", async () => {
  const context = { arm: "treatment", block: "Terms:\n  NNA: inflows less outflows.", sources: [] };
  for (const [question, tool] of [["Current NNA vs certified NNA meaning", "ask"],
    ["What does NNA mean?", "get_context"]]) {
    const kge = kgeStub((name) => ({ value: name === "ask" ? DIFFERENCE : context }));
    const { outcome } = await turn(routedByPrompt(), kge, { question });
    assert.deepEqual(kge.calls.map((c) => [c.name, c.args.question]), [[tool, question]], question);
    assert.equal(outcome.outcome, "answered", question);
  }
});

test("#624, #625: a resume keeps the paused step's other calls and their results, from the server's own conversation, with no ask re-run", async () => {
  const model = standIn((messages) => (messages.at(-1)._getType() === "human"
    ? { calls: [["ask", { question: "NNA for May 2025?" }], ["choose", { question: "Which book?", options: ["Wealth?", "Retail?"] }]] } : { say: "unused" }));
  const first = await turn(model, kgeStub(() => ({ value: ONE_TERM })));
  assert.equal(first.paused, true);
  const reads = model.read.length;
  const again = kgeStub(() => undefined);
  const { values } = await turn(model, again, { thread: first.thread, resume: { pick: "1" } });
  const read = model.read[reads];
  assert.deepEqual(read.findLast((m) => m._getType() === "ai").tool_calls.map((c) => c.name), ["ask", "choose"]);
  assert.ok(read.some((m) => m.name === "ask"), "the step's answer reaches the model");
  assert.ok(values.messages.some((m) => m.name === "ask"));
  assert.equal(again.calls.length, 0, "nothing re-run");
});

// #624 review: a clarification outside the first clarify of a step is asked too, one card at a time.
const clarifying = (question, choices) => ({ behaviour: "clarify", answer_mode: "not_sure", reason: `Which ${question}?`, answer_text: `Not sure: which ${question}?`, clarification: { choices } });
const A_CHOICES = [{ id: "a1", label: "Card transactions" }, { id: "a2", label: "All transactions" }];
const B_CHOICES = [{ id: "b1", label: "Retail book" }, { id: "b2", label: "Wealth book" }];

test("#624: a clarification on one of the offer's two fallback questions is asked on its HITL card, and its pick posts its card", async () => {
  const first = await offered();
  const kge = kgeStub((_name, args) => ({ value: args.question.startsWith("Compare") ? { behaviour: "abstain_with_reason", answer_mode: "not_sure", answer_text: "Not sure: cannot compare." }
    : args.question.startsWith("current") ? clarifying("current NNA", A_CHOICES) : ONE_TERM }));
  const model = standIn(() => ({ say: "unused" }));
  const paused = await turn(model, kge, { thread: first.thread, resume: { pick: "latest_month" } });
  assert.equal(paused.paused, true, "the clarification pauses the turn at its card");
  assert.equal(paused.outcome.outcome, "paused");
  assert.equal(paused.interrupt.source, "clarify");
  assert.equal(paused.interrupt.question, "Which current NNA?");
  assert.deepEqual(paused.interrupt.options, A_CHOICES);
  const after = paused.values.messages.slice(first.values.messages.length);
  assert.deepEqual(after.map((m) => [m.type, m.name ?? m.tool_calls?.map((c) => c.name).join()]), [["tool", "choose"], ["ai", "ask,ask"], ["tool", "ask"], ["tool", "ask"], ["ai", "choose"]]);
  assert.equal(after.at(-1).tool_calls[0].args.ask_call, after[1].tool_calls[0].id, "the card names the clarifying question");
  assert.equal(paused.interrupt.tool_call_id, after.at(-1).tool_calls[0].id);

  const picked = await turn(model, kge, { thread: paused.thread, resume: { pick: "a2" } });
  assert.equal(model.calls, 0, "no model call");
  assert.equal(picked.paused, false);
  assert.equal(kge.calls.at(-1).args.question, "All transactions");
  assert.deepEqual(toolResult(picked.values.messages.at(-1)), ONE_TERM);
  assert.equal(picked.outcome.outcome, "answered");
});

test("#624: parallel clarifications in one step are asked one card at a time, the next once the first pick has run", async () => {
  const model = standIn((messages) => (messages.at(-1)._getType() === "human" ? { calls: [["ask", { question: "Transactions A?" }], ["ask", { question: "Transactions B?" }]] } : { say: "unused" }));
  const kge = kgeStub((_name, args) => ({ value: args.question === "Transactions A?" ? clarifying("transactions", A_CHOICES)
    : args.question === "Transactions B?" ? clarifying("book", B_CHOICES) : ONE_TERM }));
  const first = await turn(model, kge, { question: "Transaction totals for the book?" });
  assert.equal(first.paused, true);
  assert.deepEqual(first.interrupt.options, A_CHOICES, "the step's first clarification first");
  assert.equal(first.values.messages.filter((m) => m.tool_calls?.some((c) => c.name === "choose")).length, 1, "one card at a time");

  const second = await turn(model, kge, { thread: first.thread, resume: { pick: "a1" } });
  assert.equal(kge.calls.at(-1).args.question, "Card transactions");
  assert.equal(second.paused, true, "the next clarification's card follows the first pick's answer");
  assert.equal(second.interrupt.question, "Which book?");
  assert.deepEqual(second.interrupt.options, B_CHOICES);
  assert.deepEqual(second.outcome, { kind: "outcome", outcome: "paused", steps: 3 });
  const askB = first.values.messages.find((m) => m.tool_calls?.length === 2).tool_calls[1].id;
  assert.equal(second.values.messages.at(-1).tool_calls[0].args.ask_call, askB);
  assert.ok(second.values.messages.some((m) => m.type === "tool" && m.name === "ask" && toolResult(m).answer_text === ONE_TERM.answer_text), "the first pick's card is posted");

  const third = await turn(model, kge, { thread: second.thread, resume: { pick: "b2" } });
  assert.equal(kge.calls.at(-1).args.question, "Wealth book");
  assert.equal(third.paused, false, "no clarification is left");
  assert.equal(third.outcome.outcome, "answered");
  assert.equal(model.calls, 1, "the picks never call the model");
});

test("#625: another person's card is not theirs to answer: refused as no such conversation, with no ask and no model call", async () => {
  const options = ["Revenue for the wealth book?", "Revenue for the retail book?"];
  const model = standIn((messages) => (messages.at(-1)._getType() === "human" ? { call: ["choose", { question: "Which book?", options }] } : { call: ["ask", { question: "unused" }] }));
  const kge = kgeStub(() => ({ value: ONE_TERM }));
  const first = await turn(model, kge, { question: "Revenue for the book?" });
  const calls = model.calls;
  await assert.rejects(turn(model, kge, { thread: first.thread, resume: { pick: "1" }, principal: "person-2" }), (error) => error.status === 404 && error.message === NO_CONVERSATION);
  assert.equal(kge.calls.length, 0);
  assert.equal(model.calls, calls, "no model call");
  // The person's own pick still works.
  const picked = await turn(model, kge, { thread: first.thread, resume: { pick: "1" } });
  assert.deepEqual(toolResult(picked.values.messages.find((m) => m.name === "choose")), { picked: { id: "1", label: options[0] } });
});

test("#624: an answered choose call reaches the model as its question and its options' labels alone, under its own id", async () => {
  const first = await turn(asksOnce("Transactions?"), kgeStub(() => ({ value: clarifying("transactions", A_CHOICES) })));
  await turn(standIn(() => ({ say: "unused" })), kgeStub(() => ({ value: ONE_TERM })), { thread: first.thread, resume: { pick: "a1" } });
  const reader = standIn(() => ({ say: "ok" }));
  await turn(reader, kgeStub(() => undefined), { thread: first.thread, question: "And now?" });
  const prompt = reader.read[0];
  const step = prompt.find((m) => m.tool_calls?.some((c) => c.name === "choose"));
  const call = step.tool_calls[0];
  assert.deepEqual(call.args, { question: "Which transactions?", options: ["Card transactions", "All transactions"] });
  assert.equal(call.id, first.values.messages.at(-1).tool_calls[0].id);
  const result = prompt.find((m) => m._getType() === "tool" && m.name === "choose");
  assert.equal(result.tool_call_id, call.id, "its result pairs with it");
  assert.deepEqual(JSON.parse(result.content), { picked: { id: "a1", label: "Card transactions" } });
});

// #714: the model's discover_meanings tool is gone: what a question leaves open is read from KGE before the model
// (cerebro-before-ask.test.js, cerebro-meaning.test.js).

// #713 (#707 requirement 12, this slice): the multiple-question card on sample data (fixtures/cerebro-stand-in.js
// sampleQuestions, hand-made groups). One card with a group per open question, nothing preselected, one Send; the
// answer accepted only as one offered pick per group shown, checked against the card the server saved in the
// conversation (#625); the question for ask built from
// all the picks, one ask per requested output; the picks kept in the saved conversation.
async function cardTurn(model, kge, { question = RETAIL_QUESTION, thread = globalThis.crypto.randomUUID(), resume, principal = "person-1", openQuestions = sampleQuestions } = {}) {
  const events = [];
  const runTurn = cerebroAgent({ model, kge, conversations, retryAfterMs: 0, openQuestions });
  const asked = resume === undefined ? { messages: [{ type: "human", content: question }] } : { resume };
  for await (const event of runTurn({ thread, ...asked, roles: ["business"], signal: new AbortController().signal, req: { portalIdentity: { principal } } })) events.push(event);
  const custom = events.filter(([e]) => e === "custom").map(([, d]) => d).filter((d) => d.kind !== "reply");
  const values = events.filter(([e]) => e === "values").at(-1)?.[1];
  return { events, outcome: custom.at(-1), values, paused: events.some(([, d]) => d?.__interrupt__), interrupt: values?.__interrupt__?.[0]?.value, thread };
}
const unusedModel = () => standIn(() => ({ say: "unused" }));
const cardOfTurn = (result) => result.values.messages.findLast((m) => m.tool_calls?.some((c) => c.name === "choose")).tool_calls[0];
const asked = (kge) => kge.calls.filter((c) => c.name === "ask").map((c) => c.args.question);

test("#713: two open questions make one card, a labelled group each, the suggested option first and marked, nothing preselected; no ask and no model call", async () => {
  const model = unusedModel();
  const kge = kgeStub(() => ({ value: ONE_TERM }));
  const first = await cardTurn(model, kge);
  assert.equal(model.calls, 0, "no model call before the card");
  assert.equal(kge.calls.length, 0, "no ask before the card");
  assert.equal(first.paused, true);
  assert.deepEqual(first.outcome, { kind: "outcome", outcome: "paused", steps: 1 });
  const call = cardOfTurn(first);
  assert.match(call.id, /^hitl-/);
  assert.equal(call.args.source, QUESTIONS);
  assert.equal(call.args.question, QUESTIONS_HEADING);
  assert.deepEqual(call.args.groups.map((g) => [g.id, g.label, g.kind]), [["meaning", "Meaning", "meaning"], ["segment", "Segment", "grouping"]]);
  assert.deepEqual(call.args.groups[0].options.map((o) => o.label), [`Certified NNA${SUGGESTED}`, "NNA (current)", "Branch-sales NNA"],
    "decision 3: the suggested default first and marked as on the one-question card, then the other meanings");
  assert.deepEqual(call.args.groups[1].options.map((o) => o.id), SAMPLE_SEGMENTS.map((o) => o.id));
  assert.doesNotMatch(JSON.stringify(call.args), /"(?:selected|picked|checked|default)"\s*:/, "nothing preselected: the suggested option is marked, never selected");
  assert.deepEqual(Object.keys(call.args).sort(), ["groups", "question", "source"], "the card is its data alone");
  assert.deepEqual(first.interrupt, { kind: "hitl", tool_call_id: call.id, source: QUESTIONS, question: QUESTIONS_HEADING, groups: call.args.groups, other: true },
    "the page gets every group and Something else");
});

test("#713: Send with one offered pick per group makes one ask, the question built from both picks; the answered card and the reloaded conversation show every pick", async () => {
  const first = await cardTurn(unusedModel(), kgeStub(() => undefined));
  const kge = kgeStub(() => ({ value: ONE_TERM }));
  const model = unusedModel();
  const sent = await cardTurn(model, kge, { thread: first.thread, resume: { picks: { meaning: "certified-nna", segment: "client-segment" } } });
  assert.equal(model.calls, 0, "the picks never call the model");
  assert.deepEqual(asked(kge), ["What was the certified NNA for Q1 2025 by client segment?"], "one ask, from all the picks together");
  assert.equal(sent.paused, false);
  assert.deepEqual(sent.outcome, { kind: "outcome", outcome: "answered", steps: 3 });
  const after = sent.values.messages.slice(first.values.messages.length);
  assert.deepEqual(after.map((m) => [m.type, m.name ?? m.tool_calls?.map((c) => c.name).join()]), [["tool", "choose"], ["ai", "ask"], ["tool", "ask"]]);
  const picks = { picks: [{ group: "meaning", label: "Meaning", id: "certified-nna", option: `Certified NNA${SUGGESTED}` }, { group: "segment", label: "Segment", id: "client-segment", option: "Client segment" }] };
  assert.deepEqual(toolResult(after[0]), picks, "the answered card's result holds every pick");
  assert.match(after[1].tool_calls[0].id, /^pick-/);
  const saved = await savedConversation(conversations, "person-1", first.thread);
  const kept = saved.messages.find((m) => m.type === "tool" && m.name === "choose");
  assert.deepEqual(JSON.parse(kept.content), picks, "#625: the reloaded conversation keeps every pick");
  assert.equal(saved.interrupt, null);
  assert.equal(saved.messages.find((m) => m.tool_calls?.some((c) => c.args?.source === QUESTIONS)).tool_calls[0].args.groups.length, 2);
});

test("#713: a Segment option valid for one meaning only is offered only once that meaning is picked", async () => {
  const first = await cardTurn(unusedModel(), kgeStub(() => undefined));
  const refused = kgeStub(() => ({ value: ONE_TERM }));
  for (const meaning of ["certified-nna", "net-new-assets"]) {
    const out = await cardTurn(unusedModel(), refused, { thread: await fork(first), resume: { picks: { meaning, segment: "branch-region" } } });
    assert.deepEqual(toolResult(out.values.messages.at(-1)), { error: NOT_OFFERED }, meaning);
    assert.equal(out.outcome.outcome, "failed");
  }
  assert.equal(refused.calls.length, 0, "no ask for a pick not offered");
  const kge = kgeStub(() => ({ value: ONE_TERM }));
  await cardTurn(unusedModel(), kge, { thread: await fork(first), resume: { picks: { meaning: "branch-sales-nna", segment: "branch-region" } } });
  assert.deepEqual(asked(kge), ["What was the branch-sales NNA for Q1 2025 by branch region?"]);
});

test("#713: \"both\" in the Show group sends one definitions ask and one figures ask, each complete; definitions hides the Period group and needs no pick for it", async () => {
  const first = await cardTurn(unusedModel(), kgeStub(() => undefined), { question: SHOW_QUESTION });
  const call = cardOfTurn(first);
  assert.deepEqual(call.args.groups.map((g) => g.label), ["Meaning", "Show", "Period"]);
  assert.deepEqual(call.args.groups[2].options.map((o) => o.label), SAMPLE_PERIODS.map((o) => o.label));
  const both = kgeStub((_name, args) => ({ value: /definitions/.test(args.question) ? { behaviour: "glossary_definition", answer_mode: "certified", answer_text: "Certified: NNA is inflows less outflows." } : ONE_TERM }));
  const sent = await cardTurn(unusedModel(), both, { thread: await fork(first), resume: { picks: { meaning: "certified-nna", show: "both", period: "2025-06" } } });
  assert.deepEqual(asked(both), ["What is the definition of certified NNA?", "What was certified NNA for June 2025?"]);
  assert.deepEqual(sent.values.messages.slice(first.values.messages.length).map((m) => [m.type, m.name ?? m.tool_calls?.map((c) => c.name).join()]),
    [["tool", "choose"], ["ai", "ask,ask"], ["tool", "ask"], ["tool", "ask"]]);
  assert.equal(sent.outcome.outcome, "answered");

  const definitions = kgeStub(() => ({ value: { behaviour: "glossary_definition", answer_mode: "certified", answer_text: "Certified: NNA is inflows less outflows." } }));
  await cardTurn(unusedModel(), definitions, { thread: await fork(first), resume: { picks: { meaning: "net-new-assets", show: "definitions" } } });
  assert.deepEqual(asked(definitions), ["What is the definition of current NNA?"]);
  const figures = kgeStub(() => ({ value: ONE_TERM }));
  await cardTurn(unusedModel(), figures, { thread: await fork(first), resume: { picks: { meaning: "net-new-assets", show: "figures", period: "2025-04" } } });
  assert.deepEqual(asked(figures), ["What was current NNA for April 2025?"]);
  // A pick for the hidden Period group, or figures with no Period pick, is not one offered pick per group shown.
  const none = kgeStub(() => ({ value: ONE_TERM }));
  for (const picks of [{ meaning: "net-new-assets", show: "definitions", period: "2025-04" }, { meaning: "net-new-assets", show: "figures" }, { meaning: "net-new-assets", show: "both" }]) {
    const out = await cardTurn(unusedModel(), none, { thread: await fork(first), resume: { picks } });
    assert.deepEqual(toolResult(out.values.messages.at(-1)), { error: NOT_OFFERED }, JSON.stringify(picks));
  }
  assert.equal(none.calls.length, 0);
});

test("#713: a resume with a group missing, a pick not offered, or a pick for a card or group the server did not save is not accepted as a pick: no ask, no model call", async () => {
  const first = await cardTurn(unusedModel(), kgeStub(() => undefined));
  const good = { meaning: "certified-nna", segment: "client-segment" };
  const kge = kgeStub(() => ({ value: ONE_TERM }));
  const model = unusedModel();
  const cases = [
    ["a group missing", { picks: { meaning: "certified-nna" } }],
    ["a pick not offered", { picks: { meaning: "certified-nna", segment: "region" } }],
    ["a group the saved card did not offer", { picks: { ...good, period: "2025-06" } }],
    ["another group's option", { picks: { meaning: "client-segment", segment: "client-segment" } }],
    ["picks for another card than the one saved", { picks: { meaning: "certified-nna", show: "figures", period: "2025-06" } }],
    ["anything beside the picks", { picks: good, groups: [] }],
    ["picks that are not an object", { picks: ["certified-nna", "client-segment"] }],
    ["a one-question pick", { pick: "certified-nna" }],
    ["a month", { month: "2025-06" }],
  ];
  for (const [name, resume] of cases) {
    const out = await cardTurn(model, kge, { thread: await fork(first), resume });
    assert.deepEqual(toolResult(out.values.messages.at(-1)), { error: NOT_OFFERED }, name);
    assert.equal(out.outcome.outcome, "failed", name);
    assert.equal(out.outcome.error.message, NOT_OFFERED, name);
  }
  // A conversation whose saved state holds no card waiting (the card answered already, or never issued) takes no picks.
  const answered = await fork(first);
  await cardTurn(unusedModel(), kgeStub(() => ({ value: ONE_TERM })), { thread: answered, resume: { picks: good } });
  await assert.rejects(cardTurn(model, kge, { thread: answered, resume: { picks: good } }), (error) => error.status === 400);
  const plain = await turn(asksOnce("Revenue for June 2025?"), kgeStub(() => ({ value: ONE_TERM })), { question: "Revenue for June 2025?" });
  await assert.rejects(cardTurn(model, kge, { thread: plain.thread, resume: { picks: good } }), (error) => error.status === 400);
  // Another person's card is not theirs to answer.
  await assert.rejects(cardTurn(model, kge, { thread: await fork(first), resume: { picks: good }, principal: "person-2" }), (error) => error.status === 404);
  assert.equal(kge.calls.length, 0, "nothing asked");
  assert.equal(model.calls, 0, "no model call");
});

test("#713, #714: Something else on the card is one free-text answer for everything, read back through KGE with no model call: asked as written where nothing is open, a request to restate where something is", async () => {
  const first = await cardTurn(unusedModel(), kgeStub(() => undefined));
  const model = unusedModel();
  const kge = kgeStub(() => ({ value: ONE_TERM }));
  const out = await cardTurn(model, kge, { thread: await fork(first), resume: { other: "certified NNA by client segment for Q1 2025" } });
  assert.equal(model.calls, 0, "the answer never goes to the model");
  assert.deepEqual(kge.reads.map((read) => [read.name, read.args.question]), [["get_context", "certified NNA by client segment for Q1 2025"]], "read back through the same tools");
  assert.deepEqual(asked(kge), ["certified NNA by client segment for Q1 2025"]);
  assert.deepEqual(toolResult(out.values.messages.find((m) => m.type === "tool" && m.name === "choose")), { other: "certified NNA by client segment for Q1 2025" });
  assert.equal(out.outcome.outcome, "answered");
  const open = "NNA by client segment";
  const still = kgeStub(() => ({ value: ONE_TERM }), { step: stepReads(() => kgeReading(open, [["NNA", "term", { term: "NNA (current)", by: "default_alias" }]], [["term", "default", { text: "NNA", options: [{ label: "NNA (current)" }, { label: "Certified NNA" }] }]])) });
  const restated = await cardTurn(unusedModel(), still, { thread: await fork(first), resume: { other: open } });
  assert.deepEqual([asked(still), restated.paused, restated.values.messages.at(-1).content], [[], false, RESTATE], "#707 requirement 12: no second card and no ask");
  // Cancel works as on the one-question card.
  const cancelled = await cardTurn(unusedModel(), kge, { thread: first.thread, resume: { cancel: true } });
  assert.deepEqual(toolResult(cancelled.values.messages.at(-1)), { cancelled: true });
});

test("#713, #714, requirement 10: one open group is the one-question card, whose pick sends at once; none, or groups that hold no card, leave the turn as it was", async () => {
  const one = kgeStub(() => ({ value: { ...ONE_TERM, context: { term: "branch-sales NNA" } } }));
  const card = await cardTurn(unusedModel(), one, { question: ONE_GROUP_QUESTION });
  assert.deepEqual(card.interrupt, { kind: "hitl", tool_call_id: card.interrupt.tool_call_id, source: "meaning", question: "Which meaning of NNA do you intend? Suggested default: certified NNA.",
    options: [{ id: "certified-nna", label: "certified NNA (default)" }, { id: "net-new-assets", label: "current NNA" }, { id: "branch-sales-nna", label: "branch-sales NNA" }], other: true });
  const sent = await cardTurn(unusedModel(), one, { thread: card.thread, resume: { pick: "branch-sales-nna" } });
  assert.deepEqual([asked(one), sent.paused, sent.outcome.outcome], [["What was branch-sales NNA for June 2025?"], false, "answered"], "no Send button: the pick is the answer");
  const other = kgeStub(() => ({ value: ONE_TERM }));
  const mismatched = await cardTurn(unusedModel(), other, { thread: (await cardTurn(unusedModel(), other, { question: ONE_GROUP_QUESTION })).thread, resume: { pick: "branch-sales-nna" } });
  assert.deepEqual([mismatched.outcome.outcome, toolResult(mismatched.values.messages.at(-1))], ["failed", { error: "The answer could not be presented safely." }], "a figure that is not the picked meaning's is never shown as its answer");
  for (const [question, openQuestions] of [["Revenue for June 2025?", sampleQuestions], [RETAIL_QUESTION, () => []], [RETAIL_QUESTION, null], [RETAIL_QUESTION, () => { throw new Error("the seam failed"); }],
    [RETAIL_QUESTION, () => [{ id: "meaning", label: "Meaning", kind: "meaning", spans: [{ start: 0, end: 4 }], options: [] }, { id: "show", label: "Show", kind: "show", options: SAMPLE_SHOW }]]]) {
    const model = asksOnce(question);
    const kge = kgeStub(() => ({ value: ONE_TERM }));
    const out = await cardTurn(model, kge, { question, openQuestions });
    assert.equal(out.paused, false, question);
    assert.equal(out.values.messages.some((m) => m.tool_calls?.some((c) => c.name === "choose")), false, question);
    assert.equal(model.calls, 1, "the model runs as it did");
    assert.deepEqual(asked(kge), [question]);
  }
});

test("#713: a clarification on a pick's ask is asked on its own card after the multiple-question card", async () => {
  const first = await cardTurn(unusedModel(), kgeStub(() => undefined));
  const choices = [{ id: "a", label: "All books" }, { id: "b", label: "Wealth book" }];
  const kge = kgeStub(() => ({ value: { behaviour: "clarify", answer_mode: "not_sure", reason: "Which book?", answer_text: "Not sure: which book?", clarification: { choices } } }));
  const out = await cardTurn(unusedModel(), kge, { thread: first.thread, resume: { picks: { meaning: "certified-nna", segment: "advisor-team" } } });
  assert.equal(out.paused, true);
  assert.equal(out.interrupt.source, "clarify");
  assert.equal(out.interrupt.question, "Which book?");
  // A failed ask ends the turn at its error.
  const again = await cardTurn(unusedModel(), kgeStub(() => undefined));
  const down = kgeStub(() => ({ status: 502 }));
  const failed = await cardTurn(unusedModel(), down, { thread: again.thread, resume: { picks: { meaning: "certified-nna", segment: "advisor-team" } } });
  assert.equal(failed.outcome.error.case, "kge_unreachable");
});

test("#713: route.js reads a multiple-question card's answer as { picks } and refuses any other shape before the agent", () => {
  const thread = globalThis.crypto.randomUUID();
  const body = (resume) => ({ config: { configurable: { thread_id: thread } }, command: { resume } });
  assert.deepEqual(readTurn(body({ picks: { meaning: "certified-nna", segment: "client-segment" } })),
    { thread, resume: { picks: { meaning: "certified-nna", segment: "client-segment" } } });
  for (const resume of [{ picks: {} }, { picks: ["x"] }, { picks: null }, { picks: { meaning: 1 } }, { picks: { "bad key": "x" } }, { picks: { meaning: "a b" } },
    { picks: { meaning: "x" }, signature: "abc" }, { picks: { meaning: "x" }, pick: "x" },
    { picks: Object.fromEntries(Array.from({ length: 7 }, (_, i) => [`g${i}`, "x"])) }]) {
    assert.match(readTurn(body(resume)).error ?? "", /multiple-question card/, JSON.stringify(resume));
  }
});

test("#713: questionsCard holds the card's data to its shape, and its pick check and questions on their own", () => {
  const meaning = { id: "meaning", label: "Meaning", kind: "meaning", spans: [{ start: 10, end: 13 }], options: SAMPLE_MEANINGS };
  const show = { id: "show", label: "Show", kind: "show", options: SAMPLE_SHOW };
  const period = { id: "period", label: "Period", kind: "period", options: SAMPLE_PERIODS };
  const original = "Show me a NNA?";
  assert.ok(questionsCard([meaning, show, period], original));
  for (const [name, groups, text = original] of [
    ["one group", [meaning]], ["not a list", "meaning"], ["no question", [meaning, show], " "], ["seven groups", Array.from({ length: 7 }, (_, i) => ({ ...period, id: `p${i}` }))],
    ["an unknown kind", [meaning, { ...show, kind: "colour" }]], ["a bad id", [meaning, { ...show, id: "a b" }]], ["no label", [meaning, { ...show, label: " " }]],
    ["a long label", [meaning, { ...show, label: "x".repeat(41) }]], ["no options", [meaning, { ...show, options: [] }]],
    ["seven options", [meaning, { ...period, options: Array.from({ length: 7 }, (_, i) => ({ id: `o${i}`, label: `O${i}` })) }]],
    ["a meaning with no place", [{ ...meaning, spans: undefined }, show]], ["an empty place list", [{ ...meaning, spans: [] }, show]],
    ["a place outside the question", [{ ...meaning, spans: [{ start: 10, end: 40 }] }, show]], ["a place not whole numbers", [{ ...meaning, spans: [{ start: 1.5, end: 3 }] }, show]],
    ["places that overlap", [meaning, { ...period, spans: [{ start: 11, end: 12 }] }]], ["a duplicate group", [meaning, { ...show, id: "meaning" }]],
    ["a second Show group", [meaning, show, { ...show, id: "show2" }]], ["a second Period group", [meaning, period, { ...period, id: "period2" }]],
    ["a Show option not an output", [meaning, { ...show, options: [{ id: "tables", label: "Tables" }, ...SAMPLE_SHOW] }]],
    ["two suggested options", [{ ...meaning, options: SAMPLE_MEANINGS.map((o) => ({ ...o, suggested: true })) }, show]],
    ["a duplicate option", [meaning, { ...period, options: [SAMPLE_PERIODS[0], SAMPLE_PERIODS[0]] }]], ["an option that is not an object", [meaning, { ...period, options: [null] }]],
    ["an option with no id", [meaning, { ...period, options: [{ label: "June 2025" }] }]], ["an option with long words", [meaning, { ...period, options: [{ id: "x", label: "X", words: "w".repeat(201) }] }]],
    ["an option whose whole label, its default words, is too long", [meaning, { ...period, options: [{ id: "x", label: "w ".repeat(101) }] }]],
    ["a when naming a later group", [{ ...meaning, options: [{ id: "x", label: "X", when: { group: "show", options: ["figures"] } }] }, show]],
    ["a when naming no option", [meaning, { ...period, options: [{ id: "x", label: "X", when: { group: "meaning", options: ["nope"] } }] }]],
    ["a when with no options", [meaning, { ...period, options: [{ id: "x", label: "X", when: { group: "meaning", options: [] } }] }]],
  ]) assert.equal(questionsCard(groups, text), undefined, name);

  const card = questionsCard([meaning, show, period], original);
  assert.equal(card.groups[0].options[0].label, `Certified NNA${SUGGESTED}`);
  assert.equal(card.groups[0].options[0].words, "certified NNA", "the marker never enters the question");
  assert.equal(questionsCard([{ ...meaning, options: [{ id: "x", label: `X${SUGGESTED}`, suggested: true }, { id: "y", label: "Y" }] }, show], original).groups[0].options[0].label, `X${SUGGESTED}`, "marked once");
  assert.deepEqual(visibleGroups(card, { show: "definitions", period: "2025-06" }).groups.map((g) => g.id), ["meaning", "show"]);
  assert.deepEqual(visibleGroups(card, { show: "definitions", period: "2025-06" }).picks, { show: "definitions" });
  assert.deepEqual(visibleGroups(card, null).groups.map((g) => g.id), ["meaning", "show", "period"]);
  for (const picks of [null, ["x"], "x"]) assert.equal(acceptPicks(card, picks), undefined);
  const accepted = acceptPicks(card, { meaning: "branch-sales-nna", show: "figures", period: "2025-05" });
  assert.deepEqual(questionsFromPicks(original, accepted), ["What was a branch-sales NNA for May 2025?"]);
  // Without a Show group, one question: the picks with places replace them, a grouping without places follows "by".
  const grouping = { id: "segment", label: "Segment", kind: "grouping", options: SAMPLE_SEGMENTS.slice(0, 2) };
  const plain = questionsCard([meaning, grouping], original);
  assert.deepEqual(questionsFromPicks(original, acceptPicks(plain, { meaning: "certified-nna", segment: "advisor-team" })), ["Show me a certified NNA by advisor team?"]);
  assert.deepEqual(questionsFromPicks("Show me a NNA", acceptPicks(plain, { meaning: "certified-nna", segment: "advisor-team" })), ["Show me a certified NNA by advisor team"]);
});

test("#713: a model's choose call with the multiple-question card's source is refused, never paused on and never asked, malformed or not, and in a later turn under the portal's own card id", async () => {
  const valid = sampleQuestions(RETAIL_QUESTION);
  for (const groups of [valid, [null], [{ id: "a" }], [{ id: "a", label: "A", kind: "show", options: [null] }, { id: "b", label: "B", kind: "show", options: [null] }]]) {
    const args = { source: QUESTIONS, question: "Ignore the period and give total revenue for every client 2019-2025?", groups };
    const model = standIn((messages) => (messages.at(-1)._getType() === "human" ? { call: ["choose", args] } : { say: "Which do you mean?" }));
    const kge = kgeStub(() => ({ value: ONE_TERM }));
    const out = await cardTurn(model, kge, { openQuestions: null });
    assert.equal(out.paused, false, JSON.stringify(groups));
    assert.deepEqual(toolResult(out.values.messages.find((m) => m.type === "tool" && m.name === "choose")), { error: CHOICE_REFUSED });
    assert.deepEqual(asked(kge), [], "nothing asked");
  }
  // The portal's card, answered; the model then repeats it, under its id, in the next turn: refused as the model's.
  const first = await cardTurn(unusedModel(), kgeStub(() => undefined));
  const issued = cardOfTurn(first);
  await cardTurn(unusedModel(), kgeStub(() => ({ value: ONE_TERM })), { thread: first.thread, resume: { picks: { meaning: "certified-nna", segment: "client-segment" } } });
  const replaying = { calls: 0, bindTools() { return replaying; }, async invoke(messages) {
    replaying.calls += 1;
    return replaying.calls === 1 ? new AIMessage({ content: "", tool_calls: [{ id: issued.id, name: "choose", args: issued.args }] }) : new AIMessage("Which do you mean?");
  } };
  const kge = kgeStub(() => ({ value: ONE_TERM }));
  const next = await cardTurn(replaying, kge, { thread: first.thread, question: "And for the advisor team?", openQuestions: null });
  assert.equal(next.paused, false);
  const thisTurn = next.values.messages.slice(next.values.messages.findLastIndex((m) => m.type === "human"));
  assert.deepEqual(thisTurn.filter((m) => m.type === "tool" && m.name === "choose").map(toolResult), [{ error: CHOICE_REFUSED }]);
  assert.equal(replaying.calls, 2, "the model reads the refusal and speaks");
  assert.deepEqual(asked(kge), []);
});

test("#713: a saved card whose groups do not hold is not offered: NOT_OFFERED, never a pause or a throw", async () => {
  const model = { bindTools() { return model; }, invoke: async () => { throw new Error("no model call"); } };
  const groups = [[null], [{ id: "a" }], [{ id: "a", label: "A", kind: "grouping", options: [null] }, { id: "b", label: "B", kind: "show", options: SAMPLE_SHOW }],
    sampleQuestions(RETAIL_QUESTION).slice(0, 1), "groups", undefined];
  for (const given of groups) {
    const graph = buildGraph(model, { checkpointer: new MemorySaver(), allowed: new Set(["ask"]) });
    const id = "hitl-saved";
    const state = { messages: [new HumanMessage(RETAIL_QUESTION), new AIMessage({ content: "", tool_calls: [{ id, name: "choose", args: { source: QUESTIONS, question: QUESTIONS_HEADING, groups: given } }] })],
      turnStart: 1, replay: true, questions: id };
    const out = await graph.invoke(state, { configurable: { thread_id: globalThis.crypto.randomUUID() } });
    assert.equal(out.__interrupt__, undefined, JSON.stringify(given));
    assert.deepEqual(toolResult(out.messages.at(-1)), { error: NOT_OFFERED }, JSON.stringify(given));
    assert.equal(out.questions, null, "the card's standing ends with its answer");
  }
});

test("#713: a suggested label that already carries the marker is marked once and its words leave the marker out; a second grouping group with no places is refused", () => {
  const original = "What was NNA for Q1 2025 by segment?";
  const meaning = { id: "meaning", label: "Meaning", kind: "meaning", spans: [{ start: 9, end: 12 }], options: [
    { id: "certified-nna", label: "Certified NNA (default)", suggested: true }, { id: "net-new-assets", label: "Net New Assets" }] };
  const segment = { id: "segment", label: "Segment", kind: "grouping", spans: [{ start: 28, end: 35 }], options: SAMPLE_SEGMENTS.slice(0, 2) };
  const card = questionsCard([meaning, segment], original);
  assert.deepEqual(card.groups[0].options[0], { id: "certified-nna", label: `Certified NNA${SUGGESTED}`, words: "Certified NNA", suggested: true });
  assert.deepEqual(questionsFromPicks(original, acceptPicks(card, { meaning: "certified-nna", segment: "client-segment" })), ["What was Certified NNA for Q1 2025 by client segment?"]);
  assert.deepEqual(questionsCard(card.groups, original), card, "the saved card holds to its shape again unchanged");
  const unplaced = { id: "team", label: "Team", kind: "grouping", options: SAMPLE_SEGMENTS.slice(0, 2) };
  assert.equal(questionsCard([meaning, unplaced, { ...unplaced, id: "team2" }], original), undefined, "the same item would be named twice");
  assert.ok(questionsCard([meaning, segment, unplaced], original), "one with places and one without");
});

test("#713: the Show pick is read first wherever its group sits; repeated markers are stripped; a group id of __proto__ is a key like another; a choose call with no id is never the card", async () => {
  const original = "Show me a NNA?";
  const meaning = { id: "meaning", label: "Meaning", kind: "meaning", spans: [{ start: 10, end: 13 }], options: SAMPLE_MEANINGS };
  const show = { id: "show", label: "Show", kind: "show", options: SAMPLE_SHOW };
  const period = { id: "period", label: "Period", kind: "period", options: SAMPLE_PERIODS };
  const grouping = { id: "segment", label: "Segment", kind: "grouping", options: SAMPLE_SEGMENTS.slice(0, 2) };
  const last = questionsCard([meaning, period, grouping, show], original);
  assert.deepEqual(visibleGroups(last, { show: "definitions" }).groups.map((g) => g.id), ["meaning", "show"], "Show last still hides the figures-only groups");
  assert.deepEqual(visibleGroups(last, { show: "figures" }).groups.map((g) => g.id), ["meaning", "period", "segment", "show"]);
  assert.deepEqual(questionsFromPicks(original, acceptPicks(last, { meaning: "net-new-assets", show: "definitions" })), ["What is the definition of a current NNA?"]);
  assert.equal(acceptPicks(last, { meaning: "net-new-assets", show: "definitions", period: "2025-06" }), undefined, "a pick for a hidden group, listed before Show");
  assert.equal(acceptPicks(last, { meaning: "net-new-assets", show: "figures" }), undefined, "figures still need the Period pick");
  assert.equal(questionsCard([meaning, { ...show, options: [{ id: "definitions", label: "Definitions", when: { group: "meaning", options: ["certified-nna"] } }, { id: "figures", label: "Figures" }] }], original), undefined,
    "a Show option depends on nothing");

  const twice = questionsCard([{ ...meaning, options: [{ id: "c", label: "Certified NNA (default) (Default)", suggested: true }, { id: "n", label: "NNA (default)" }] }, show], original);
  assert.deepEqual(twice.groups[0].options.map((o) => [o.label, o.words]), [[`Certified NNA${SUGGESTED}`, "Certified NNA"], ["NNA", "NNA"]]);
  const given = questionsCard([{ ...meaning, options: [{ id: "c", label: "Certified NNA", words: "certified NNA (default)", suggested: true }, { id: "n", label: "NNA" }] }, show], original);
  assert.equal(given.groups[0].options[0].words, "certified NNA (default)", "explicit words are the caller's");

  const proto = questionsCard([meaning, { ...grouping, id: "__proto__" }], original);
  const picks = JSON.parse('{"meaning":"certified-nna","__proto__":"advisor-team"}');
  assert.deepEqual(visibleGroups(proto, picks).picks, JSON.parse('{"meaning":"certified-nna","__proto__":"advisor-team"}'));
  assert.deepEqual(questionsFromPicks(original, acceptPicks(proto, picks)), ["Show me a certified NNA by advisor team?"]);
  assert.equal(acceptPicks(proto, { meaning: "certified-nna" }), undefined, "its group still needs a pick");

  const model = { bindTools() { return model; }, invoke: async () => { throw new Error("no model call"); } };
  const graph = buildGraph(model, { checkpointer: new MemorySaver(), allowed: new Set(["ask"]) });
  const state = { messages: [new HumanMessage(RETAIL_QUESTION), new AIMessage({ content: "", tool_calls: [{ name: "choose", args: { source: QUESTIONS, question: QUESTIONS_HEADING, groups: sampleQuestions(RETAIL_QUESTION) } }] })],
    turnStart: 1, replay: true };
  const out = await graph.invoke(state, { configurable: { thread_id: globalThis.crypto.randomUUID() } });
  assert.equal(out.__interrupt__, undefined);
  assert.deepEqual(toolResult(out.messages.at(-1)), { error: NOT_OFFERED });
});

test("#713: meaning labels as long as the one-question card's are shown, a longer one cut at a word's end, never refused; a question keeps its own ending", () => {
  const current = "Net New Assets (current) — Money clients bring in, less what they take out…";
  const branch = "Branch Sales NNA — Eligible received inflows less outflows at the branch level…";
  assert.deepEqual([current.length, branch.length], [75, 79], "0.14.58-length labels");
  const original = "What was NNA for Q1 2025 by segment?";
  const segment = { id: "segment", label: "Segment", kind: "grouping", spans: [{ start: 28, end: 35 }], options: SAMPLE_SEGMENTS.slice(0, 2) };
  const atLimit = "C".repeat(OPTION_LABEL_MAX - SUGGESTED.length);
  const meaning = { id: "meaning", label: "Meaning", kind: "meaning", spans: [{ start: 9, end: 12 }], options: [
    { id: "certified-nna", label: atLimit, words: "certified NNA", suggested: true },
    { id: "net-new-assets", label: current, words: "current NNA" }, { id: "branch-sales-nna", label: branch, words: "branch-sales NNA" }] };
  const card = questionsCard([meaning, segment], original);
  assert.ok(card, "the card is shown");
  assert.deepEqual(card.groups[0].options.map((o) => o.label), [`${atLimit}${SUGGESTED}`, current, branch], "labels up to the cap are kept whole");
  assert.equal(card.groups[0].options[0].label.length, OPTION_LABEL_MAX, "the suggested label at the limit, marker included");

  const long = `${"Certified Net New Assets as reported at each month's freeze for the "}regulatory filing period`;
  const cut = questionsCard([{ ...meaning, options: [{ id: "certified-nna", label: long, suggested: true }, { id: "net-new-assets", label: `${long} and more words beyond`, words: "current NNA" }] }, segment], original);
  assert.ok(cut, "a longer label is cut, and the card still shown");
  const [suggested, other] = cut.groups[0].options;
  assert.ok(suggested.label.endsWith(`…${SUGGESTED}`) && suggested.label.length <= OPTION_LABEL_MAX, suggested.label);
  assert.ok(other.label.endsWith("…") && other.label.length <= OPTION_LABEL_MAX && !/\s…$/.test(other.label), other.label);
  assert.equal(suggested.words, long, "a cut label's default words are its whole label");
  assert.deepEqual(questionsCard(cut.groups, original), cut, "a cut card holds to its shape again unchanged");

  const period = { id: "period", label: "Period", kind: "period", options: SAMPLE_PERIODS };
  const show = { id: "show", label: "Show", kind: "show", options: SAMPLE_SHOW };
  const plain = { id: "meaning", label: "Meaning", kind: "meaning", spans: [{ start: 8, end: 11 }], options: SAMPLE_MEANINGS };
  for (const [text, built] of [["Show me NNA.", "What was certified NNA for June 2025?"], ["Show me NNA!", "What was certified NNA for June 2025?"],
    ["Show me NNA?", "What was certified NNA for June 2025?"], ["Show me NNA…", "What was certified NNA for June 2025?"], ["Show me NNA", "What was certified NNA for June 2025?"]]) {
    const asked = questionsCard([plain, show, period], text);
    assert.deepEqual(questionsFromPicks(text, acceptPicks(asked, { meaning: "certified-nna", show: "figures", period: "2025-06" })), [built], text);
  }
});

test("#714 (N3, N4, N5 of #713's review): a label with no space is cut between characters, never through one; a pick with places closes its cut; an ending of \"…\" is the question's own", () => {
  const original = "harbour Q1 2025 FUM by desk…";
  const emoji = "😀".repeat(60);
  const group = { id: "meaning", label: "Meaning", kind: "meaning", spans: [{ start: 16, end: 19 }], cut: [{ start: 0, end: 7 }], options: [{ id: "a", label: emoji, words: "Pooled FUM" }, { id: "b", label: "Spot FUM" }] };
  const desk = { id: "grouping", label: "Desk", kind: "grouping", options: Array.from({ length: 12 }, (_, n) => ({ id: `dimension-${n + 1}`, label: `Desk level ${n + 1}`, words: `desk level ${n + 1}` })) };
  const card = questionsCard([group, { id: "period", label: "Period", kind: "period", options: SAMPLE_PERIODS }, desk], original);
  const cutLabel = card.groups[0].options[0].label;
  assert.ok(cutLabel.endsWith("…") && cutLabel.length <= OPTION_LABEL_MAX && JSON.parse(JSON.stringify(cutLabel)) === cutLabel && !/[\uD800-\uDBFF]…$/.test(cutLabel), "no lone half of a character before the ellipsis");
  assert.equal(card.groups[2].options.length, 12, "#707 decision 20: a grouping group has no cap on its options");
  assert.equal(questionsCard([{ ...group, options: Array.from({ length: 7 }, (_, n) => ({ id: `m${n}`, label: `Meaning ${n}` })) }, desk], original), undefined, "any other group keeps its limit");
  assert.deepEqual(questionsFromPicks(original, acceptPicks(card, { meaning: "a", period: "2025-05", grouping: "dimension-2" })), ["Q1 2025 Pooled FUM by desk for May 2025 by desk level 2…"],
    "the cut is taken out and its gap closed; what is added comes before the question's own ending");
  assert.equal(questionsCard([{ ...group, cut: [{ start: 14, end: 17 }] }, desk], original), undefined, "a cut that overlaps a place is no card");
  assert.equal(questionsCard([{ ...group, spans: undefined }, desk], original), undefined);
  for (const refused of [{ group: "period", options: ["2025-05"] }, { group: "show", options: ["figures"] }]) {
    const show = { id: "show", label: "Show", kind: "show", options: SAMPLE_SHOW };
    const period = { id: "period", label: "Period", kind: "period", options: SAMPLE_PERIODS };
    const dependent = { id: "meaning", label: "Meaning", kind: "meaning", spans: [{ start: 16, end: 19 }], options: [{ id: "a", label: "Pooled FUM", when: refused }, { id: "b", label: "Spot FUM" }] };
    assert.equal(questionsCard([show, period, dependent], original), undefined, `round 3's nits 1 and 2: no option depends on ${refused.group}`);
  }
});
