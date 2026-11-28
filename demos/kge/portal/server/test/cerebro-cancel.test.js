import assert from "node:assert/strict";
import { test } from "node:test";
import { AIMessage, ToolMessage } from "@langchain/core/messages";
import { cerebroAgent, historyOf, savedConversation } from "../src/cerebro/agent.js";
import { readTurn } from "../src/cerebro/route.js";
import { openConversations } from "../src/conversations/index.js";
import { kgeStub, standIn } from "./fixtures/cerebro-stand-in.js";

const thread = () => globalThis.crypto.randomUUID();
const req = { portalIdentity: { principal: "reader" } };
const collect = async (events) => { const result = []; for await (const event of events) result.push(event); return result; };
const question = "Which transactions in June 2025?";
const clarification = { behaviour: "clarify", answer_mode: "not_sure", reason: "Which transactions?", answer_text: "Not sure: which transactions?",
  clarification: { choices: [{ id: "card", label: "Card transactions" }, { id: "all", label: "All transactions" }] },
  history: [{ turn_id: "t1", question, clarification: { choices: [{ id: "card", label: "Card transactions" }, { id: "all", label: "All transactions" }], spans: [{ turn: "t1", start: 6, end: 18 }] } }] };

test("cancellation must be a standalone explicit action", () => {
  const body = (resume) => ({ command: { resume }, config: { configurable: { thread_id: thread() } } });
  assert.deepEqual(readTurn(body({ cancel: true })).resume, { cancel: true });
  for (const value of [{ cancel: false }, { cancel: "true" }, { cancel: true, pick: "card" }, { cancel: true, other: "x" }, { cancel: true, month: "2025-06" }]) {
    assert.match(readTurn(body(value)).error, /cancellation alone/i);
  }
  assert.deepEqual(readTurn(body({ pick: "card" })).resume, { pick: "card" });
  assert.deepEqual(readTurn(body({ other: "other" })).resume, { other: "other" });
});

test("cancelled clarification does not make its history the next question's context; cancelling an offer keeps the answer", () => {
  const first = new ToolMessage({ name: "ask", tool_call_id: "a0", content: JSON.stringify({ behaviour: "figure", history: [{ question: "earlier" }] }) });
  const cancelled = new ToolMessage({ name: "ask", tool_call_id: "a1", content: JSON.stringify(clarification) });
  const choose = new AIMessage({ content: "", tool_calls: [{ name: "choose", id: "c1", args: { source: "clarify", ask_call: "a1" } }] });
  const result = new ToolMessage({ name: "choose", tool_call_id: "c1", content: JSON.stringify({ cancelled: true }) });
  assert.deepEqual(historyOf([first, cancelled, choose, result]), [{ question: "earlier" }]);
  const newer = new ToolMessage({ name: "ask", tool_call_id: "a1", content: JSON.stringify({ behaviour: "figure", history: [{ question: "newer" }] }) });
  assert.deepEqual(historyOf([first, cancelled, choose, result, newer]), [{ question: "newer" }], "reused call ids do not suppress a later answer");
  const offer = new AIMessage({ content: "", tool_calls: [{ name: "choose", id: "c2", args: { source: "offer", ask_call: "a0" } }] });
  const rejected = new ToolMessage({ name: "choose", tool_call_id: "c2", content: JSON.stringify({ cancelled: true }) });
  assert.deepEqual(historyOf([first, offer, rejected]), [{ question: "earlier" }]);
});

test("cancel resolves the pending card without rerunning a model or KGE, persists, and allows a new question", async () => {
  const stub = kgeStub((name, args) => name === "ask"
    ? { value: args.question === question ? clarification : { behaviour: "figure", figure: 2, answer_text: "Certified: 2.", answer_mode: "certified" } }
    : { value: {} });
  const model = standIn((messages) => ({ call: ["ask", { question: messages.findLast((m) => m._getType() === "human").content }] }));
  const conversations = openConversations();
  const id = thread();
  const run = cerebroAgent({ model, kge: stub, conversations, retryAfterMs: 0 });
  await collect(run({ thread: id, messages: [{ type: "human", content: question }], req, roles: ["business"] }));
  const calls = stub.calls.length;
  const modelCalls = model.calls;
  const before = await savedConversation(conversations, "reader", id);
  const result = await collect(run({ thread: id, resume: { cancel: true }, req, roles: ["business"] }));
  assert.equal(model.calls, modelCalls);
  assert.equal(stub.calls.length, calls);
  const after = await savedConversation(conversations, "reader", id);
  assert.deepEqual(after.messages.slice(0, before.messages.length), before.messages);
  assert.deepEqual(JSON.parse(after.messages.at(-1).content), { cancelled: true });
  assert.ok(result.some(([type, data]) => type === "values" && data.messages.some((m) => m.type === "tool" && JSON.parse(m.content).cancelled)));
  await assert.rejects(() => collect(run({ thread: id, resume: { cancel: true }, req, roles: ["business"] })), /Nothing in this conversation/);
  await collect(run({ thread: id, messages: [{ type: "human", content: "Card transactions" }], req, roles: ["business"] }));
  const last = stub.calls.filter((c) => c.name === "ask").at(-1);
  assert.equal(last.args.question, "Card transactions");
  assert.equal(last.args.history, undefined);
});
