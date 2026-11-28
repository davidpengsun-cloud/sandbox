/* Tests only: a mock agent behind the stand-in for the portal server's Cerebro routes (chatTestServer.js, #625: the
   server keeps the conversation, the page sends the new message alone), whose turns answer each question with an `ask` call and its reply, or with a get_context
   call and its package, or any tool's call and reply, or ask the person back (#624: hitlTurn, pickTurn, offerTurn), and can send ask progress and other custom events and hold a turn
   open until released. As the server's agent does (#622), each turn ends with its `outcome` event: answered, or paused
   where it ends on an interrupt; a turn marked with `silent()` ends with none (a lost connection). */
import { conversationServer } from "./chatTestServer";
import { monthWords } from "./chatAnswer";
import { hasFigures } from "./artifacts";

export { hasFigures };

const HOLD = Symbol("hold");
const askReply = (id, n, reply, card) => ({
  type: "tool", id: `tool-${n}`, tool_call_id: id, name: "ask", content: JSON.stringify(reply), card: card ?? hasFigures(reply),
});
const lastHuman = (messages) => [...messages].reverse().find((m) => m.type === "human")?.content;

/** A turn that asks KGE: the ask call, any progress events, a hold, then the reply and the agent's own words. */
export function askTurn(reply, { args, events = [], hold = false, say = "", card, readout } = {}) {
  return (messages, n) => {
    const question = lastHuman(messages);
    const id = `tc-${n}`;
    const call = { type: "ai", id: `ai-${n}`, content: "", tool_calls: [{ id, name: "ask", args: args ?? { question } }] };
    const result = askReply(id, n, typeof reply === "function" ? reply(question) : reply, card);
    const after = say ? [{ type: "ai", id: `ai-${n}-say`, content: say }] : [];
    return [
      ["values", { messages: [...messages, call] }],
      // #651 (B3): the readout and its message, as the server sends it before the warehouse runs.
      ...(readout ? [["custom", { kind: "readout", tool_call_id: id, readout: readout.readout ?? {}, message: readout.message }]] : []),
      ...events.map((event) => ["custom", { kind: "ask_progress", tool_call_id: id, event }]),
      ...(hold ? [HOLD] : []),
      ["values", { messages: [...messages, call, result, ...after] }],
    ];
  };
}

/** A turn that reads the context package: the get_context call and its package. */
export function contextTurn(pkg) {
  return (messages, n) => {
    const id = `ctx-${n}`;
    const call = { type: "ai", id: `ai-${n}`, content: "", tool_calls: [{ id, name: "get_context", args: { question: lastHuman(messages) } }] };
    const result = { type: "tool", id: `tool-${n}`, tool_call_id: id, name: "get_context", content: JSON.stringify(pkg) };
    return [["values", { messages: [...messages, call, result] }]];
  };
}

/** A turn of any tool: its call, any custom events ({ kind, ... }) and a hold, then its reply. */
export function toolTurn(name, reply, { args, custom = [], hold = false } = {}) {
  return (messages, n) => {
    const id = `${name}-${n}`;
    const call = { type: "ai", id: `ai-${n}`, content: "", tool_calls: [{ id, name, args: args ?? { question: lastHuman(messages) } }] };
    const result = { type: "tool", id: `tool-${n}`, tool_call_id: id, name, content: JSON.stringify(reply) };
    return [
      ["values", { messages: [...messages, call] }],
      ...custom.map((event) => ["custom", { tool_call_id: id, ...event }]),
      ...(hold ? [HOLD] : []),
      ["values", { messages: [...messages, call, result] }],
    ];
  };
}

/** #624: a turn that asks the person back, as the server's agent does: a clarification (`source` "clarify": the ask
 *  call and its clarify reply, then the portal's choose call naming it) or the agent's own question (`source` "agent":
 *  the choose call alone), then the pause, its interrupt of kind "hitl". */
export const CHOICES = [{ id: "gross", label: "Gross revenue" }, { id: "net", label: "Net revenue" }];
export function hitlTurn({ source = "clarify", question = "Which revenue do you mean?", options = CHOICES, reply, hold = false } = {}) {
  return (messages, n) => {
    const askId = `tc-${n}`;
    const asked = source === "clarify" ? [
      { type: "ai", id: `ai-${n}`, content: "", tool_calls: [{ id: askId, name: "ask", args: { question: lastHuman(messages) } }] },
      { type: "tool", id: `tool-${n}`, tool_call_id: askId, name: "ask", content: JSON.stringify(reply ?? { question: lastHuman(messages), behaviour: "clarify", answer_mode: "not_sure", reason: question, answer_text: question, clarification: { choices: options }, receipt: "r" }) },
    ] : [];
    const callId = `hitl-${n}`;
    const args = { source, question, options: source === "agent" ? options.map((o) => o.label) : options, ...(source === "clarify" ? { ask_call: askId } : {}) };
    const choose = { type: "ai", id: `ai-${n}-choose`, content: "", tool_calls: [{ id: callId, name: "choose", args }] };
    const state = [...messages, ...asked, choose];
    // The agent's options are strings, numbered from 1 as the server numbers them.
    const shown = source === "agent" ? options.map((o, i) => ({ id: String(i + 1), label: o.label })) : options;
    const value = { kind: "hitl", tool_call_id: callId, source, question, options: shown, other: true };
    return [["values", { messages: state }], ...(hold ? [HOLD] : []), ["values", { messages: state, __interrupt__: [{ id: `int-${n}`, value }] }]];
  };
}

/** #624: the turn a pick resumes, as the server runs it: the choose call's result (the pick, or the person's words), then,
 *  for a pick, the restated question sent to Ask and its reply; for words, the agent's own reply. */
export function pickTurn(reply, { say = "Thanks, I will look at that.", question = (label) => label } = {}) {
  return (messages, n, resume) => {
    const choose = [...messages].reverse().find((m) => m.type === "ai" && m.tool_calls?.some((c) => c.name === "choose"));
    const call = choose.tool_calls.find((c) => c.name === "choose");
    const options = (call.args.options || []).map((o, i) => (typeof o === "string" ? { id: String(i + 1), label: o } : o));
    const picked = typeof resume?.month === "string" ? { id: resume.month, label: monthWords(resume.month) } : options.find((o) => o.id === resume?.pick);
    const answer = picked ? { picked } : { other: resume?.other };
    const result = { type: "tool", id: `tool-${n}-choose`, tool_call_id: call.id, name: "choose", content: JSON.stringify(answer) };
    if (!picked) return [["values", { messages: [...messages, result, { type: "ai", id: `ai-${n}-say`, content: say }] }]];
    const askId = `pick-${n}`;
    const ask = { type: "ai", id: `ai-${n}`, content: "", tool_calls: [{ id: askId, name: "ask", args: { question: question(picked.label) } }] };
    const answered = askReply(askId, n, typeof reply === "function" ? reply(question(picked.label)) : reply);
    return [["values", { messages: [...messages, result, ask, answered] }]];
  };
}

/** #713: a multiple-question card, hand-made sample groups: the portal's choose call of source "questions", its groups
 *  and the interrupt that pauses on it. "Branch region" is valid for branch-sales NNA only. */
export const QUESTIONS_HEADING = "Before I ask, choose one option in each group.";
export const QUESTION_GROUPS = [
  { id: "meaning", label: "Meaning", kind: "meaning", spans: [{ start: 13, end: 23 }], options: [
    { id: "certified-nna", label: "Certified NNA (default)", words: "certified NNA", suggested: true },
    { id: "net-new-assets", label: "NNA (current)", words: "current NNA" },
    { id: "branch-sales-nna", label: "Branch-sales NNA", words: "branch-sales NNA" }] },
  { id: "show", label: "Show", kind: "show", options: [{ id: "definitions", label: "Definitions", words: "Definitions" }, { id: "figures", label: "Figures", words: "Figures" }, { id: "both", label: "Both", words: "Both" }] },
  { id: "period", label: "Period", kind: "period", options: [{ id: "2025-06", label: "June 2025", words: "June 2025" }, { id: "2025-05", label: "May 2025", words: "May 2025" }] },
  { id: "segment", label: "Segment", kind: "grouping", options: [
    { id: "client-segment", label: "Client segment", words: "client segment" },
    { id: "branch-region", label: "Branch region", words: "branch region", when: { group: "meaning", options: ["branch-sales-nna"] } }] },
];
export function questionsTurn({ groups = QUESTION_GROUPS } = {}) {
  return (messages, n) => {
    const callId = `hitl-${n}`;
    const choose = { type: "ai", id: `ai-${n}-choose`, content: "", tool_calls: [{ id: callId, name: "choose", args: { source: "questions", question: QUESTIONS_HEADING, groups } }] };
    const state = [...messages, choose];
    const value = { kind: "hitl", tool_call_id: callId, source: "questions", question: QUESTIONS_HEADING, groups, other: true };
    return [["values", { messages: state }], ["values", { messages: state, __interrupt__: [{ id: `int-${n}`, value }] }]];
  };
}
/** #713: the turn a multiple-question card's Send resumes, as the server runs it: the picks as the choose call's result,
 *  then one ask per question (questions(picks) gives them) and each reply. */
export function picksTurn(reply, { questions = () => ["What was the certified NNA?"] } = {}) {
  return (messages, n, resume) => {
    const choose = [...messages].reverse().find((m) => m.type === "ai" && m.tool_calls?.some((c) => c.name === "choose"));
    const call = choose.tool_calls.find((c) => c.name === "choose");
    const picks = call.args.groups.filter((g) => resume?.picks?.[g.id]).map((g) => ({ group: g.id, label: g.label, id: resume.picks[g.id], option: g.options.find((o) => o.id === resume.picks[g.id]).label }));
    const result = { type: "tool", id: `tool-${n}-choose`, tool_call_id: call.id, name: "choose", content: JSON.stringify({ picks }) };
    const calls = questions(resume?.picks ?? {}).map((question, i) => ({ id: `pick-${n}-${i}`, name: "ask", args: { question } }));
    const ask = { type: "ai", id: `ai-${n}`, content: "", tool_calls: calls };
    const replies = calls.map((c, i) => askReply(c.id, `${n}-${i}`, typeof reply === "function" ? reply(c.args.question) : reply));
    return [["values", { messages: [...messages, result, ask, ...replies] }]];
  };
}

/** #624: a difference answer followed by the offer to compare the figures: the ask call, its reply, and the portal's
 *  choose call of source "offer" with no result. The turn ends answered: the offer never pauses. */
export const OFFER_OPTIONS = [{ id: "latest_month", label: "June 2025" }, { id: "last_quarter", label: "Q2 2025" }, { id: "no", label: "No thanks" }];
export const OFFER_MONTHS = ["2025-01", "2025-02", "2025-03", "2025-04", "2025-05", "2025-06"];
export function offerTurn(reply, { options = OFFER_OPTIONS, months = OFFER_MONTHS } = {}) {
  return (messages, n) => {
    const askId = `tc-${n}`;
    const ask = { type: "ai", id: `ai-${n}`, content: "", tool_calls: [{ id: askId, name: "ask", args: { question: lastHuman(messages) } }] };
    const result = askReply(askId, n, reply);
    const offer = { type: "ai", id: `ai-${n}-offer`, content: "", tool_calls: [{ id: `offer-${n}`, name: "choose", args: { source: "offer", question: "Compare the figures?", options, months, ask_call: askId } }] };
    return [["values", { messages: [...messages, ask, result, offer] }]];
  };
}

/** A turn of the given stream items as they are: [event, data] pairs and `hold` (HOLD) marks; it sends no outcome of
 *  its own unless it lists one. */
export const HOLD_HERE = HOLD;
export function rawTurn(items) {
  const turn = () => items;
  turn.raw = true;
  return turn;
}

/** A turn that ends with no outcome: the stream closes as a lost connection would. */
export function silent(turn) {
  const quiet = (...args) => turn(...args);
  quiet.raw = true;
  return quiet;
}

/** The agent: `turns[i]` answers the i-th turn (the last one answers the rest). `fetch` is the page's, onto the
 *  stand-in server; `requests` are the stream requests the page sent, `ran` the conversation each turn ran on (the
 *  saved one and the new question), and `server` the stand-in itself (its saved conversations, `seed`, `calls`).
 *  `options` are the server's (`retentionDays`, `pageSize`, `principal`). */
export function mockAgent(turns, options = {}) {
  const gates = [];
  let n = 0;
  async function* runTurn({ messages, resume }) {
    const turn = turns[Math.min(n, turns.length - 1)];
    n += 1;
    let paused = false;
    for (const item of turn(messages, n, resume)) {
      if (item === HOLD) await new Promise((resolve) => gates.push(resolve));
      else {
        if (item[0] === "values" && item[1]?.__interrupt__) paused = true;
        yield item;
      }
    }
    if (!turn.raw) yield ["custom", { kind: "outcome", outcome: paused ? "paused" : "answered", steps: 1 }];
  }
  const server = conversationServer({ runTurn, ...options });
  return { requests: server.requests, ran: server.ran, fetch: server.fetch, server, release: () => gates.splice(0).forEach((resolve) => resolve()) };
}
