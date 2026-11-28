import assert from "node:assert/strict";
import { test } from "node:test";
import { RESTATE, cerebroAgent } from "../src/cerebro/agent.js";
import { memoryStore, openConversations } from "../src/conversations/index.js";
import { INTENT_OPTIONS, INTENT_QUESTION, comparedQuestions, framedQuestion, isGoldenQuestion, needsIntentChoice, shapeOf } from "../src/cerebro/intent.js";
import { readingOf } from "../src/cerebro/meaning.js";
import { kgeReading, kgeStub, periodDefault, standIn, stepReads } from "./fixtures/cerebro-stand-in.js";

// #707 requirement 9 (#714): whether the output is open (definitions, figures or both) is Cerebro's own check, with its
// request words. A grouping and a comparison are told from other words by KGE's reading alone: "Show me … by X" does
// not open the output where KGE read X as a grouping, and a comparison KGE binds counts whatever its wording. Invented
// names (FUM, "desk"), so nothing here holds for one pack only.
const read = (question, bindings = [], items = []) => readingOf(kgeReading(question, bindings, items), question);
const term = (text) => [text, "term", { term: "Pooled FUM", by: "explicit" }];
const open = (question, bindings, items) => needsIntentChoice(question, read(question, bindings, items));

test("an open request that names no output is open; a definition, a figure or another output named in Cerebro's request words is not", () => {
  for (const question of ["Tell me about pooled FUM", "Show me pooled FUM", "Give me details on pooled FUM", "I need some information about pooled FUM", "show me pooled FUM for Q1 2025"]) {
    assert.equal(open(question, [term("pooled FUM")]), true, question);
  }
  for (const question of ["Show me the definition of pooled FUM", "Show me how much pooled FUM there was", "Tell me about the total pooled FUM", "Show me the documents about pooled FUM", "Tell me about the owners of pooled FUM",
    "What was pooled FUM in June 2025?", "pooled FUM for June 2025"]) {
    assert.equal(open(question, [term("pooled FUM")]), false, question);
  }
  for (const blank of ["", "   "]) assert.equal(needsIntentChoice(blank, read(blank)), false);
  assert.equal(needsIntentChoice(undefined, undefined), false);
  assert.equal(needsIntentChoice("Show me pooled FUM", undefined), true, "the request words need no reading");
});

test("\"Show me … by X\" does not open the output where KGE read X as a grouping, bound or open; where it read none, it does", () => {
  const question = "Show me pooled FUM by desk";
  assert.equal(open(question, [term("pooled FUM"), ["by desk", "grouping", { label: "desk.code" }]]), false, "a grouping KGE bound");
  assert.equal(open(question, [term("pooled FUM"), ["by", "operator"], ["desk", "unread"]], [["grouping", "clarify", { text: "desk", options: [{ label: "Desk code" }, { label: "Desk name" }] }]]), false,
    "a grouping word KGE reports as naming declared dimensions");
  for (const by of ["by Finance", "by this Friday", "by email", "by far"]) {
    const asked = `Show me pooled FUM ${by}`;
    assert.equal(open(asked, [term("pooled FUM"), ["by", "unread"]]), true, `${asked}: KGE read no grouping, whatever the word after "by"`);
  }
  assert.equal(open("Tell me about pooled FUM by desk", [term("pooled FUM"), ["by desk", "grouping"]]), true, "\"tell me about\" keeps the question open");
  assert.equal(open("Show me pooled FUM for Q1 through Q3 2025", [term("pooled FUM")]), false, "a period range asks for figures (#700)");
  assert.equal(open("Show me pooled FUM for Q1-2025", [term("pooled FUM")]), true, "one period alone does not");
  assert.equal(needsIntentChoice(question, undefined), true, "with no reading, nothing is known to group");
});

test("a comparison KGE binds opens the output whatever its wording; with none bound, a comparing word alone does not", () => {
  for (const [question, word] of [["live FUM compared to pooled FUM", "compared to"], ["live FUM against pooled FUM", "against"], ["live vs. pooled Q3 2024 FUM", "vs"], ["Compare live FUM and pooled FUM", "Compare"]]) {
    assert.equal(open(question, [[word, "comparison"]]), true, question);
    assert.equal(needsIntentChoice(question, read(question, [])), false, `${question}: no comparison bound`);
  }
  assert.equal(open("Compare the figures for live FUM and pooled FUM", [["Compare", "comparison"]]), false, "the output is named");
  assert.equal(open("Show me live FUM versus pooled FUM by desk", [["versus", "comparison"], ["by desk", "grouping"]]), true, "a comparison keeps the question open, grouped or not");
  assert.deepEqual(shapeOf(read("a vs b by desk", [["vs", "comparison"], ["by desk", "grouping"]])), { comparison: true, grouping: true });
  assert.deepEqual(shapeOf(undefined), { comparison: false, grouping: false });
});

test("#707 decision 4: the question for one output changes its shape alone, and adds no word but the framing's own", () => {
  assert.equal(framedQuestion("Show me pooled FUM for June 2025", "figures"), "What was pooled FUM for June 2025?");
  assert.equal(framedQuestion("Show me pooled FUM for June 2025.", "definitions"), "What is the definition of pooled FUM for June 2025?");
  assert.equal(framedQuestion("  tell me about pooled FUM…", "figures"), "What was pooled FUM?");
  assert.equal(framedQuestion("live vs. pooled Q3 2024 FUM", "figures"), "live vs. pooled Q3 2024 FUM", "with no open request, the figures question is the person's own");
  assert.equal(framedQuestion("live vs. pooled Q3 2024 FUM?", "definitions"), "What is the definition of live vs. pooled Q3 2024 FUM?");
});

test("a comparison whose second side leaves its term out is asked with the term the person wrote, read from KGE's bindings alone", () => {
  const question = "live Q3 2024 FUM versus pooled";
  const bindings = [["live", "time_mode", { value: "current" }], ["Q3 2024", "period"], ["FUM", "term", { term: "Spot FUM", by: "explicit" }], ["versus", "comparison"], term("pooled")];
  assert.deepEqual(comparedQuestions(question, "figures", read(question, bindings)), ["What was live FUM versus pooled FUM for Q3 2024?"]);
  assert.deepEqual(comparedQuestions(question, "definitions", read(question, bindings)), ["What is the definition of live FUM for Q3 2024?", "What is the definition of pooled FUM for Q3 2024?"]);
  const led = "Compare live FUM vs pooled?";
  assert.deepEqual(comparedQuestions(led, "figures", read(led, [["Compare", "comparison"], ["live", "time_mode"], ["FUM", "term", { term: "Spot FUM", by: "explicit" }], ["vs", "comparison"], term("pooled")])), ["What was live FUM vs pooled FUM?"]);
  for (const [asked, read_] of [
    [question, undefined], [question, read(question, bindings.slice(0, 4))], [`${question} by desk`, read(`${question} by desk`, bindings)],
    [`Please ${question}`, read(`Please ${question}`, bindings)], [question, { bindings: [...read(question, bindings).bindings, { kind: "unread", text: "x" }], items: [] }],
    ["live FUM versus pooled FUM", read("live FUM versus pooled FUM", [["live", "time_mode"], ["FUM", "term", { term: "Spot FUM", by: "explicit" }], ["versus", "comparison"], term("pooled FUM")])],
  ]) assert.equal(comparedQuestions(asked, "figures", read_), undefined, asked);
});

test("a golden question matches only exactly, after folding case, punctuation and spacing", () => {
  const golden = ["Show me pooled FUM by desk for Q1 through Q3 2025.", "Compare live vs. pooled Q3 2024 FUM."];
  assert.equal(isGoldenQuestion("show me pooled fum by desk for q1 through q3 2025", golden), true);
  assert.equal(isGoldenQuestion("  Compare live vs pooled Q3 2024 FUM  ", golden), true);
  assert.equal(isGoldenQuestion("Compare live vs. pooled Q3 2025 FUM.", golden), false, "a near match is not golden");
  assert.equal(isGoldenQuestion("Show me pooled FUM by desk", golden), false);
  assert.equal(isGoldenQuestion("Show me pooled FUM"), false, "no list, no golden question");
});

// Through the agent: the one-question output card, as before #714, issued from the step before ask with no model call.
const conversations = openConversations({ store: memoryStore() });
const FIGURE = { behaviour: "figure", answer_mode: "certified", answer_text: "Certified: it was USD 12.", sql: "SELECT 1", sql_certified: true };
const DEFINITION = { behaviour: "glossary_definition", answer_text: "Pooled FUM: as declared.", term: "Pooled FUM" };
const VERSUS = "live Q3 2024 FUM versus pooled";
const READINGS = {
  "Show me pooled FUM for June 2025": kgeReading("Show me pooled FUM for June 2025", [term("pooled FUM"), ["June 2025", "period"]]),
  "Show me pooled FUM": kgeReading("Show me pooled FUM", [term("pooled FUM")], [periodDefault()]),
  [VERSUS]: kgeReading(VERSUS, [["live", "time_mode", { value: "current" }], ["Q3 2024", "period"], ["FUM", "term", { term: "Spot FUM", by: "explicit" }], ["versus", "comparison"], term("pooled")]),
  "What was pooled FUM for June 2025?": kgeReading("What was pooled FUM for June 2025?", [term("pooled FUM"), ["June 2025", "period"]]),
};
const kgeOf = (golden) => kgeStub((name, args) => (name === "ask" ? { value: /definition/.test(args.question) ? DEFINITION : FIGURE } : { value: {} }),
  { step: stepReads((question) => READINGS[question]), ...(golden ? { golden: { sets: [{ set: "sage", questions: golden, golden20: [] }] } } : {}) });
const asksAsWritten = () => standIn((messages) => (messages.some((m) => m._getType() === "tool" && m.name === "ask") ? { say: "Done." } : { call: ["ask", { question: messages.findLast((m) => m._getType() === "human").content }] }));
async function turn(kge, { question, resume, thread = globalThis.crypto.randomUUID(), model = asksAsWritten() } = {}) {
  const events = [];
  const runTurn = cerebroAgent({ model, kge, conversations, retryAfterMs: 0 });
  const sent = resume === undefined ? { messages: [{ type: "human", content: question }] } : { resume };
  for await (const event of runTurn({ thread, ...sent, roles: ["business"], signal: new AbortController().signal, req: { portalIdentity: { principal: "person-1" } } })) events.push(event);
  const values = events.filter(([e]) => e === "values").at(-1)?.[1];
  return { values, card: values?.__interrupt__?.[0]?.value, thread, model, outcome: events.filter(([e, d]) => e === "custom" && d.kind === "outcome").at(-1)[1],
    said: values.messages.filter((m) => m.type === "ai" && !m.tool_calls?.length).map((m) => m.content) };
}
const asked = (kge) => kge.calls.filter((c) => c.name === "ask").map((c) => c.args.question);

test("an open output pauses on the one-question card before the model or KGE's ask guesses; each pick asks the person's own question for that output", async () => {
  for (const [pick, questions] of [["figures", ["What was pooled FUM for June 2025?"]], ["definitions", ["What is the definition of pooled FUM for June 2025?"]],
    ["both", ["What is the definition of pooled FUM for June 2025?", "What was pooled FUM for June 2025?"]]]) {
    const kge = kgeOf();
    const first = await turn(kge, { question: "Show me pooled FUM for June 2025" });
    assert.deepEqual(first.card, { kind: "hitl", tool_call_id: first.card.tool_call_id, source: "intent", question: INTENT_QUESTION, options: INTENT_OPTIONS, other: true });
    assert.deepEqual([asked(kge), first.model.calls], [[], 0]);
    const sent = await turn(kge, { thread: first.thread, resume: { pick } });
    assert.deepEqual(asked(kge), questions, pick);
    assert.deepEqual([sent.model.calls, sent.outcome.outcome, sent.card], [0, "answered", undefined]);
    assert.deepEqual(JSON.parse(sent.values.messages.find((m) => m.type === "tool" && m.name === "choose").content), { picked: INTENT_OPTIONS.find((option) => option.id === pick) });
  }
});

test("with no period named, the output and the period are asked on one card, never one after the other; definitions needs no period", async () => {
  const kge = kgeOf();
  const first = await turn(kge, { question: "Show me pooled FUM" });
  assert.deepEqual(first.card.groups.map((group) => group.label), ["Show", "Period"]);
  await turn(kge, { thread: first.thread, resume: { picks: { show: "definitions" } } });
  assert.deepEqual(asked(kge), ["What is the definition of pooled FUM?"]);
  const figures = kgeOf();
  const again = await turn(figures, { question: "Show me pooled FUM" });
  await turn(figures, { thread: again.thread, resume: { picks: { show: "figures", period: "2025-06" } } });
  assert.deepEqual(asked(figures), ["What was pooled FUM for June 2025?"]);
});

test("an elided comparison's output pick asks each side with the term the person wrote", async () => {
  const kge = kgeOf();
  const first = await turn(kge, { question: VERSUS });
  assert.equal(first.card.source, "intent");
  await turn(kge, { thread: first.thread, resume: { pick: "both" } });
  assert.deepEqual(asked(kge), ["What is the definition of live FUM for Q3 2024?", "What is the definition of pooled FUM for Q3 2024?", "What was live FUM versus pooled FUM for Q3 2024?"]);
});

test("cancel never asks or guesses; a pick not offered is not taken; something else is read back and asked only where nothing is open", async () => {
  const kge = kgeOf();
  const first = await turn(kge, { question: "Show me pooled FUM for June 2025" });
  const cancelled = await turn(kge, { thread: first.thread, resume: { cancel: true } });
  assert.deepEqual([asked(kge), cancelled.model.calls, cancelled.card], [[], 0, undefined]);
  for (const [resume, questions, said] of [[{ pick: "everything" }, [], []], [{ other: "figures" }, ["What was pooled FUM for June 2025?"], []],
    [{ other: "What was pooled FUM for June 2025?" }, ["What was pooled FUM for June 2025?"], []], [{ other: "Show me pooled FUM" }, [], [RESTATE]], [{ other: "the usual" }, ["the usual"], []]]) {
    const other = kgeOf();
    const card = await turn(other, { question: "Show me pooled FUM for June 2025" });
    const answered = await turn(other, { thread: card.thread, resume });
    assert.deepEqual([asked(other), answered.said, answered.model.calls, answered.card], [questions, said, 0, undefined], JSON.stringify(resume));
  }
});

test("#707 requirement 8: a golden question is asked as written, with no card and no step, decided once at the step before ask; a rewording is not exempt", async () => {
  const golden = ["Show me pooled FUM for June 2025", VERSUS];
  for (const question of [...golden, "show me pooled fum for june 2025."]) {
    const kge = kgeOf(golden);
    const done = await turn(kge, { question });
    assert.equal(done.card, undefined, question);
    assert.deepEqual([asked(kge), kge.reads], [[question], []], "asked as written; the step reads nothing for it");
  }
  const kge = kgeOf(golden);
  const reworded = await turn(kge, { question: "Show me pooled FUM" });
  assert.deepEqual(reworded.card.groups.map((group) => group.label), ["Show", "Period"]);
  const unserved = kgeOf();
  assert.equal((await turn(unserved, { question: golden[0] })).card.source, "intent", "with no golden list served, no question is exempt");
});
