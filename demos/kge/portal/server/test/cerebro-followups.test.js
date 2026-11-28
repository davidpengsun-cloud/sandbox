import assert from "node:assert/strict";
import { test } from "node:test";
import { AIMessage, HumanMessage, ToolMessage } from "@langchain/core/messages";
import { NOTHING_OPEN, isStepRead } from "./fixtures/cerebro-stand-in.js";
import { ASK_WHICH, NO_FIGURE, SPOKEN_MAX, answers, buildGraph, historyOf, isFrame, spoken, toPage } from "../src/cerebro/agent.js";

// #619: natural multi-turn. The card is the whole reply (one model call when ask answers), the agent speaks only where
// there is no card (one short sentence, never a figure), the model sees the earlier answers (#625: the server's own
// conversation), a follow-up reaches ask as a full question with the history, and a not-sure answer keeps the thread.
const ALLOWED = new Set(["ask", "get_context"]);

// A stand-in model: it records what it is shown and answers with decide(messages); every call has its own id.
let ids = 0;
function standIn(decide) {
  const model = { shown: [] };
  model.bindTools = () => model;
  model.invoke = async (messages) => {
    model.shown.push(messages.map((m) => [m._getType(), m.content, m.tool_calls?.map((c) => c.args.question)]));
    const step = decide(messages);
    return step.ask ? new AIMessage({ content: step.text ?? "", tool_calls: [{ id: `c-${ids += 1}`, name: "ask", args: { question: step.ask } }] }) : new AIMessage(step.say);
  };
  return model;
}
const latest = (messages) => messages.findLastIndex((m) => m._getType() === "human");
// #621: the portal's framing get_context (a short or follow-up question's) is read, not decided on.
const toolsThisTurn = (messages) => messages.slice(latest(messages)).filter((m) => m._getType() === "tool" && !isFrame(m.tool_call_id));

// A synthetic Ask: each reply scripted by question; an answered reply's history grows by one entry as KGE's does.
// #621: get_context (the portal's framing) is recorded and answers with one declared term.
const TERMS = { arm: "treatment", block: "Terms:\n  Retail revenue: revenue from retail sales." };
function synthetic(script) {
  const sent = [];
  const call = async (name, args, extras = {}) => {
    // #714: the step before ask's own read finds nothing bound and nothing open, and is not among the calls counted.
    if (isStepRead(name, args)) return { value: NOTHING_OPEN };
    sent.push({ name, question: args.question, history: extras.history });
    if (name === "get_context") return { value: TERMS };
    const reply = script(args.question);
    if (reply.error) return { error: reply.error };
    const asked = sent.filter((s) => s.name === "ask").length;
    const history = reply.history === false ? undefined : [...(extras.history ?? []), { turn_id: `t-${asked}`, question: args.question }];
    const { history: _drop, ...rest } = reply;
    return { value: { question: args.question, ...rest, ...(history ? { history } : {}) } };
  };
  return { call, sent };
}
const FIGURE = { behaviour: "figure", answer_mode: "certified", answer_text: "Certified: retail revenue was USD 3,615.55." };
const NOT_SURE = { behaviour: "abstain_with_reason", answer_mode: "not_sure", answer_text: "Not sure: which book?", history: false };
const CLARIFY = { behaviour: "clarify", answer_mode: "not_sure", answer_text: "Which NNA?", clarification: { choices: [{ id: "a", label: "All" }] } };

// The conversation as the agent keeps it: each question runs on the messages so far, and the final state is kept.
function conversation(model, call) {
  const graph = buildGraph(model, { call, allowed: ALLOWED });
  let held = [];
  return async (question) => {
    const messages = [...held, new HumanMessage({ id: `h-${held.length}`, content: question })];
    const calls = model.shown.length;
    const state = await graph.invoke({ messages, turnStart: messages.length, replay: false }, { configurable: { thread_id: `t-${ids += 1}` } });
    const turn = state.messages.slice(messages.length).map((m) => toPage(m));
    held = state.messages;
    return { turn, calls: model.shown.length - calls, said: turn.filter((m) => m.type === "ai").map((m) => m.content).filter(Boolean) };
  };
}

test("an answered Ask card ends the turn: one model call, and no agent text after the card (#619)", async () => {
  for (const behaviour of ["figure", "combined", "sum_with_verdict", "sum_without_verdict", "parts_with_reason", "cite", "cite_certified", "glossary_definition"]) {
    const model = standIn((messages) => (toolsThisTurn(messages).length ? { say: "Retail revenue was USD 3,615.55, as the card shows." } : { ask: "Total retail revenue for May 2025", text: "Let me look." }));
    const { call } = synthetic(() => ({ ...FIGURE, behaviour }));
    const { turn, calls, said } = await conversation(model, call)("Total retail revenue for May 2025");
    assert.equal(calls, 1, `${behaviour}: no second model call`);
    assert.deepEqual(turn.map((m) => m.type), ["ai", "tool"], `${behaviour}: the card is the turn's last message`);
    assert.deepEqual(said, [], `${behaviour}: the tool step's text is never shown`);
  }
});

test("a clarification with no choices, a not sure, a refusal or an error is no answer: the agent speaks once, one sentence, never a figure (#619)", async () => {
  for (const reply of [{ ...CLARIFY, clarification: { choices: [] } }, NOT_SURE, { behaviour: "refuse", answer_mode: "certified", answer_text: "No." }, { error: "403: no" }]) {
    const model = standIn((messages) => (toolsThisTurn(messages).length ? { say: "Which do you mean? It was USD 12 last time. Pick one." } : { ask: "NNA?" }));
    const { turn, calls, said } = await conversation(model, synthetic(() => reply).call)("NNA?");
    assert.equal(calls, 2, "the model speaks where there is no card");
    assert.equal(turn.at(-1).type, "ai");
    assert.deepEqual(said, ["Which do you mean?"], "its first sentence alone");
  }
  const model = standIn((messages) => (toolsThisTurn(messages).length ? { say: "It was USD 12 last time." } : { ask: "NNA?" }));
  const { said } = await conversation(model, synthetic(() => NOT_SURE).call)("NNA?");
  assert.deepEqual(said, [NO_FIGURE], "a sentence with a figure never reaches the page");
});

test("an AI-Generated answer is delivered as Ask gives it: its figure and its SQL in the card at once, no pause, no model call after (#624)", async () => {
  const generated = { ...FIGURE, answer_mode: "ai_generated", answer_text: "AI-Generated: retail revenue was USD 3,615.55.", sql: "SELECT SUM(amount) FROM retail_sales", sql_certified: false, plan: { p: 1 } };
  const model = standIn((messages) => (toolsThisTurn(messages).length ? { say: "Ask it another way." } : { ask: "revenue in May" }));
  const graph = buildGraph(model, { call: synthetic(() => generated).call, allowed: ALLOWED });
  const state = await graph.invoke({ messages: [new HumanMessage("revenue in May")], turnStart: 1, replay: false }, { configurable: { thread_id: "generated" } });
  assert.equal(state.__interrupt__, undefined, "nothing waits for a confirm");
  assert.equal(model.shown.length, 1, "the card ends the turn");
  assert.deepEqual(state.messages.map((m) => m._getType()), ["human", "ai", "tool"]);
  const card = JSON.parse(state.messages[2].content);
  assert.equal(card.answer_text, generated.answer_text, "the figure comes with the first reply");
  assert.equal(card.sql, generated.sql, "a business user's card carries the SQL");
  assert.equal(card.sql_certified, false, "marked not certified");
  assert.equal(card.plan, undefined, "the plan stays the engineer's");
  for (const field of ["confirm_first", "confirmed_text", "declined"]) assert.equal(field in card, false, field);
});

test("spoken: one short plain sentence, never a figure; a date's or a period's numbers are no figure (#619)", () => {
  assert.equal(spoken("Do you mean May 2025 or June 2025? I can do either."), "Do you mean May 2025 or June 2025?");
  assert.equal(spoken([{ type: "text", text: "Which quarter, FY26-Q1 or Q2?" }, { type: "text", text: "Or H1?" }]), "Which quarter, FY26-Q1 or Q2?");
  assert.equal(spoken("Assets under advice at 30 June 2025 or June 30?"), "Assets under advice at 30 June 2025 or June 30?");
  for (const figure of ["It was 42.", "Revenue was USD 3,615.55.", "Up 12%.", "About 3.5m.", "There are 3 meanings.", "It was 1,200."]) assert.equal(spoken(figure), NO_FIGURE, figure);
  for (const figure of ["Sold in 12 markets last year.", "Revenue fell by 40 marginally.", "It has 10 declared terms.", "The total was 2024.", "It was 1999 dollars.", "It was ２４."]) {
    assert.equal(spoken(figure), NO_FIGURE, figure);
  }
  assert.equal(spoken("Do you mean the last 12 months or the calendar year?"), ASK_WHICH, "a question with a figure asks neutrally");
  assert.equal(spoken("Is it March 3 or March 4?"), "Is it March 3 or March 4?");
  assert.equal(spoken("Revenue for May 2025 or for 2024?"), "Revenue for May 2025 or for 2024?");
  assert.equal(spoken("Do you mean U.S. revenue or all regions? More."), "Do you mean U.S. revenue or all regions?", "an initial's full stop ends no sentence");
  assert.equal(spoken("Net revenue vs. gross revenue, e.g. fees. Next."), "Net revenue vs. gross revenue, e.g. fees.");
  for (const period of ["I have no answer for H1 2025.", "Do you mean Q1 2025 or Q2 2025?", "Did you mean 2025-06 or 2025-07?", "Which quarter, FY26-Q1 or FY26-Q2 2025?"]) {
    assert.equal(spoken(period), period, "a period's numbers are no figure");
  }
  assert.equal(spoken("The answer is no. It was 5."), "The answer is no.", "a short word's full stop ends its sentence");
  assert.equal(spoken("Pick plan A. The total is 400."), "Pick plan A.");
  assert.equal(spoken("It was ٣."), NO_FIGURE, "any script's digits");
  assert.equal(spoken(""), "");
  assert.equal(spoken({ not: "text" }), "");
  const long = spoken(`${"word ".repeat(80)}end.`);
  assert.ok(long.length <= SPOKEN_MAX && long.endsWith("…"));
  assert.ok(spoken("x".repeat(SPOKEN_MAX + 10)).length <= SPOKEN_MAX);
});

test("answers: a figure, a table or a definition; never a clarification, not sure, refusal or error (#619, #624)", () => {
  assert.equal(answers(FIGURE), true);
  assert.equal(answers({ ...CLARIFY, answer_mode: "certified" }), false, "a clarification is no answer, whatever its mode");
  for (const reply of [CLARIFY, NOT_SURE, { ...FIGURE, answer_mode: "not_sure" }, { ...FIGURE, error: "x" },
    { ...FIGURE, behaviour: "refuse" }, { ...FIGURE, behaviour: "abstain" }, { ...FIGURE, behaviour: undefined }, null, "text"]) {
    assert.equal(answers(reply), false, JSON.stringify(reply));
  }
});

test("a not-sure reply with no history keeps the thread: the next ask sends the last good history (#619)", () => {
  const reply = (value) => new ToolMessage({ tool_call_id: "c", name: "ask", content: JSON.stringify(value) });
  const of = (...messages) => historyOf([new HumanMessage("q"), ...messages]);
  const good = [{ turn_id: "t-1" }];
  assert.deepEqual(of(reply({ history: good }), reply(NOT_SURE)), good);
  assert.deepEqual(of(reply({ history: good }), reply({ answer_mode: "not_sure", history: [] })), good);
  assert.deepEqual(of(reply({ history: good }), reply({ answer_mode: "not_sure", history: [{ turn_id: "t-2" }] })), [{ turn_id: "t-2" }], "a not sure with a history is used");
  assert.equal(of(reply({ history: good }), reply({ answer_mode: "certified" })), undefined, "an answer with no history still resets it");
});

// The audit's conversations (#619), replayed with a stand-in model that rewrites a follow-up as the full question from
// the earlier question it is shown (FOLLOW_UP_RULE), and a synthetic Ask that, like Ask today, answers only full
// questions: a bare follow-up would come back not sure.
const FULL = /\b(?:20\d{2}|FY\d{2}-Q\d)\b/;
const REWRITE = {
  "What about May?": (earlier) => earlier.replace(/June/, "May"),
  "Only for region NW": (earlier) => `${earlier} for region NW`,
  "Break it down by product family": (earlier) => `${earlier} by product family`,
  "for May 2025": (earlier) => earlier.replace(/June 2025/, "May 2025"),
  "and April 2025": (earlier) => earlier.replace(/May 2025/, "April 2025"),
  "Only GROWTH households": (earlier) => `${earlier} for GROWTH households`,
};
function rewriter(messages) {
  if (toolsThisTurn(messages).length) return { say: "Which do you mean?" };
  const person = messages[latest(messages)].content;
  const before = messages.slice(0, latest(messages));
  const answered = new Set(before.filter((m) => m._getType() === "tool" && JSON.parse(m.content).answer_mode !== "not_sure").map((m) => m.tool_call_id));
  const earlier = before.flatMap((m) => m.tool_calls ?? []).filter((c) => c.name === "ask" && answered.has(c.id)).map((c) => c.args.question).at(-1);
  return { ask: REWRITE[person] && earlier ? REWRITE[person](earlier) : person };
}
const ASK_TODAY = (question) => (question === "Why?" ? NOT_SURE : FULL.test(question) ? FIGURE : { ...NOT_SURE, answer_text: `Please give a valid period: ${question}` });

test("the audit's conversations: each follow-up reaches ask as the full question with the history; one model call per answer (#619)", async () => {
  const runs = {
    A: [["Total retail revenue for June 2025", "Total retail revenue for June 2025"], ["What about May?", "Total retail revenue for May 2025"],
      ["Only for region NW", "Total retail revenue for May 2025 for region NW"], ["Break it down by product family", "Total retail revenue for May 2025 for region NW by product family"]],
    B: [["Total retail revenue for June 2025", "Total retail revenue for June 2025"], ["Only for region NW", "Total retail revenue for June 2025 for region NW"],
      ["for May 2025", "Total retail revenue for May 2025 for region NW"], ["and April 2025", "Total retail revenue for April 2025 for region NW"]],
    C: [["Wealth revenue for fiscal quarter FY26-Q1", "Wealth revenue for fiscal quarter FY26-Q1"], ["Only GROWTH households", "Wealth revenue for fiscal quarter FY26-Q1 for GROWTH households"]],
  };
  for (const [name, turns] of Object.entries(runs)) {
    const model = standIn(rewriter);
    const ask = synthetic(ASK_TODAY);
    const person = conversation(model, ask.call);
    for (const [i, [typed, full]] of turns.entries()) {
      const before = ask.sent.length;
      const { calls, said } = await person(typed);
      // #621: a follow-up is framed once, with the person's own words, before the model's ask; the first question is not.
      assert.deepEqual(ask.sent.slice(before).map((s) => [s.name, s.question]), [...(i ? [["get_context", typed]] : []), ["ask", full]], `${name}${i + 1}: framed, then asked`);
      const sent = ask.sent.at(-1);
      assert.equal(sent.question, full, `${name}${i + 1}: sent in full`);
      assert.equal(sent.history?.length ?? 0, i, `${name}${i + 1}: with the history so far`);
      assert.equal(calls, 1, `${name}${i + 1}: one model call`);
      assert.deepEqual(said, [], `${name}${i + 1}: no agent text after the card`);
    }
    assert.ok(model.shown.at(-1).some(([type, content]) => type === "tool" && /retail revenue|USD/.test(content)), `${name}: the model saw its earlier answers`);
  }
});

test("'What about May?' after June's revenue: sent as the full question with history; a follow-up after a not sure still sends the last good history (#619)", async () => {
  const model = standIn(rewriter);
  const ask = synthetic(ASK_TODAY);
  const person = conversation(model, ask.call);
  await person("Total retail revenue for June 2025");
  const june = ask.sent.at(-1);
  await person("What about May?");
  // #621: framed first, with the person's words, then asked in full with the history.
  assert.deepEqual(ask.sent.slice(-2), [{ name: "get_context", question: "What about May?", history: undefined },
    { name: "ask", question: "Total retail revenue for May 2025", history: [{ turn_id: "t-1", question: june.question }] }]);
  const unsure = await person("Why?");
  assert.equal(unsure.calls, 2, "a not sure is no card: the agent speaks");
  await person("Only for region NW");
  assert.equal(ask.sent.at(-1).question, "Total retail revenue for May 2025 for region NW");
  const asks = ask.sent.filter((s) => s.name === "ask");
  assert.deepEqual(ask.sent.at(-2), { name: "get_context", question: "Only for region NW", history: undefined });
  assert.deepEqual(ask.sent.at(-1).history, asks[1].history.concat([{ turn_id: "t-2", question: "Total retail revenue for May 2025" }]), "the last good history, past the not sure");
});
