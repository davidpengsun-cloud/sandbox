import assert from "node:assert/strict";
import { test } from "node:test";
import { ROUTING_EXAMPLES, SMALL_TALK_RULE, cerebroAgent, savedConversation } from "../src/cerebro/agent.js";
import { openConversations } from "../src/conversations/index.js";
import { kgeStub, standIn } from "./fixtures/cerebro-stand-in.js";

// #672: a greeting, a thanks or small talk gets one short reply with no tool call, and nothing reaches KGE's Ask for
// it; any question about the data still goes to the tools. The rule is the system prompt's alone. A stand-in model that
// keeps the rule only where the prompt states it, and a stubbed KGE, synthetic data only. #621's framing is the
// portal's, decided in code before the model is called: a message of two words or fewer, or a short one after an
// answer, still gets its one compact get_context, and nothing else reaches KGE.

const PERSON = "reader";
const SHOWCASE = ["What was net new assets in June 2025?", "Total retail revenue by region for May 2025", "Net new assets by advisor for Q2 2025"];
const GOLDEN = { sets: [{ golden20: SHOWCASE }], retention_days: 30 };
const figure = (question) => ({ question, behaviour: "figure", figure: 42, answer_text: `Certified: ${question}: USD 42.00.`, answer_mode: "certified", receipt: "r" });
const SMALL_TALK = ["hi", "thanks", "hello there"];
const REPLY = "Hello, ask me a question about your data.";

const human = (messages) => messages.findLast((m) => m._getType() === "human");
const thisTurn = (messages) => messages.slice(messages.indexOf(human(messages)) + 1);
/** The stand-in: where the system prompt states the small-talk rule, it answers the test's small talk in words alone;
 *  everything else it asks once. The words it knows are the test's, never the portal's. */
const model = () => standIn((messages) => {
  const question = String(human(messages).content).trim();
  if (messages[0].content.includes(SMALL_TALK_RULE) && SMALL_TALK.includes(question)) return { say: REPLY };
  return thisTurn(messages).some((m) => m._getType() === "tool" && m.name === "ask") ? { say: "unused" } : { call: ["ask", { question }] };
});
const kge = () => kgeStub((name, args) => (name === "ask" ? { value: figure(args.question) } : { value: { block: "Terms:\n  Net new assets: inflows less outflows.\n" } }), { golden: GOLDEN });

async function turn(agent, { thread, question }) {
  const events = [];
  for await (const event of agent({ thread, messages: [{ type: "human", content: question, id: `h-${Math.random()}` }], roles: ["business"], req: { portalIdentity: { principal: PERSON } } })) events.push(event);
  return { events, outcome: events.filter(([mode, data]) => mode === "custom" && data?.kind === "outcome").at(-1)?.[1] };
}

test("#672: the system prompt carries the small-talk rule, generic, and keeps 'cannot answer' to questions about the data", async () => {
  const stand = model();
  await turn(cerebroAgent({ model: stand, kge: kge(), conversations: openConversations(), retryAfterMs: 0 }), { thread: globalThis.crypto.randomUUID(), question: "hi" });
  const prompt = stand.read[0][0].content;
  assert.ok(prompt.includes(SMALL_TALK_RULE));
  assert.ok(prompt.indexOf(SMALL_TALK_RULE) < prompt.indexOf(ROUTING_EXAMPLES), "the rule comes before the routing examples it sets apart");
  assert.match(SMALL_TALK_RULE, /greeting, a thanks or small talk/);
  assert.match(SMALL_TALK_RULE, /with no tool call/);
  assert.match(SMALL_TALK_RULE, /Any question about data, figures, terms, the catalogue or the knowledge graph, however short, uses the tools/);
  assert.doesNotMatch(SMALL_TALK_RULE, /\bask\b|get_context|NNA|net new assets|revenue|Meridian|pack/i, "it names no tool, client, pack or term");
  assert.match(prompt, /For a question about the data, terms or the catalogue, without a tool's answer, say you cannot answer\./);
  assert.doesNotMatch(prompt, /\. Without a tool's answer, say you cannot answer/, "no bare rule left to contradict it");
});

/** The KGE calls a turn made beyond #621's framing get_context, which carries the person's words as they are. */
const beyondFraming = (calls, question) => calls.filter((c) => !(c.name === "get_context" && c.args.compact === true && c.args.question === question));

for (const question of SMALL_TALK) {
  test(`#672: "${question}" ends with one short reply and the model calls no tool; nothing reaches Ask`, async () => {
    const stand = model();
    const stub = kge();
    const conversations = openConversations();
    const thread = globalThis.crypto.randomUUID();
    const { outcome } = await turn(cerebroAgent({ model: stand, kge: stub, conversations, retryAfterMs: 0 }), { thread, question });
    assert.equal(outcome.outcome, "answered");
    assert.equal(stand.calls, 1, "one model call");
    assert.deepEqual(beyondFraming(stub.calls, question), [], "nothing but #621's framing reaches KGE");
    assert.ok(!stub.calls.some((c) => c.name === "ask"));
    const saved = await savedConversation(conversations, PERSON, thread);
    const own = saved.messages.filter((m) => m.type === "ai" && !(m.tool_calls?.length && m.tool_calls.every((c) => c.id.startsWith("frame-"))));
    assert.deepEqual(own.map((m) => [m.content, m.tool_calls?.length ?? 0]), [[REPLY, 0]], "the model's one message: a reply with no tool call");
    assert.equal(saved.messages.at(-1).content, REPLY);
  });
}

test("#672: \"thanks\" after an answer ends with a reply too, and the showcase questions still call ask", async () => {
  const stand = model();
  const stub = kge();
  const conversations = openConversations();
  const thread = globalThis.crypto.randomUUID();
  const agent = cerebroAgent({ model: stand, kge: stub, conversations, retryAfterMs: 0 });
  for (const question of SHOWCASE) {
    const { outcome } = await turn(agent, { thread, question });
    assert.equal(outcome.outcome, "answered", question);
  }
  assert.deepEqual(stub.calls.filter((c) => c.name === "ask").map((c) => c.args.question), SHOWCASE);
  const before = stub.calls.length;
  const { outcome } = await turn(agent, { thread, question: "thanks" });
  assert.equal(outcome.outcome, "answered");
  assert.deepEqual(beyondFraming(stub.calls.slice(before), "thanks"), [], "nothing but #621's framing reaches KGE");
  assert.equal(stand.read.at(-1).filter((m) => m._getType() === "human").length, SHOWCASE.length + 1);
  assert.ok(!stub.calls.slice(before).some((c) => c.name === "ask"));
  const saved = await savedConversation(conversations, PERSON, thread);
  assert.equal(saved.messages.at(-1).content, REPLY);
});
