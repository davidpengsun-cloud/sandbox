import assert from "node:assert/strict";
import { test } from "node:test";
import { AIMessage, HumanMessage } from "@langchain/core/messages";
import { buildGraph, isFrame } from "../src/cerebro/agent.js";
import { gatewayMessages } from "../src/cerebro/gatewayModel.js";

// #698: what a follow-up sends the model. An ask reply carries its explained path (graph_path, some 26 KB live) and its
// feedback receipt (some 1 KB), and the portal frames a short follow-up with get_context (some 2.6 KB); the page keeps
// all three, and before this fix every later turn sent them to the model again (~8,400 input tokens a follow-up on the
// pilot). The model's input is measured as the gateway sends it (gatewayModel.js gatewayMessages), in UTF-8 bytes, over
// a golden question and two short follow-ups. Synthetic replies of the live sizes, in the Meridian example's words.
const ALLOWED = new Set(["ask", "get_context"]);
const QUESTIONS = ["What was certified Q2 2025 NNA by business line?", "What about Q3 2024?", "Only for Core Affluent"];
const ASKED = ["What was certified Q2 2025 NNA by business line?", "What was certified Q3 2024 NNA by business line?",
  "What was certified Q3 2024 NNA by business line for Core Affluent?"];

const graphPath = (n) => ({ nodes: Array.from({ length: 120 }, (_, i) => ({ id: `node-${n}-${i}`, label: `Business line ${i} of turn ${n}`,
  kind: i % 3 ? "Column" : "Term", unit: "wealth", detail: "Certified net new assets by business line, as the release declares it." })),
edges: Array.from({ length: 119 }, (_, i) => ({ from: `node-${n}-${i}`, to: `node-${n}-${i + 1}`, kind: "MAPS_TO" })), unused: 0 });
const receipt = (n) => `rcpt.${n}.${"A".repeat(1000)}`;
const framing = (question) => ({ question, terms: [{ term: "Certified NNA", definition: "Finance frozen month-end net new assets in USD." }],
  sources: Array.from({ length: 6 }, (_, i) => ({ n: i + 1, doc: `doc-${i}`, title: `Net new assets policy, part ${i + 1}`, citable: false,
    text: "Net new assets are the money clients bring to the firm less the money they take away, by business line and month. ".repeat(3) })) });

function synthetic() {
  let asked = 0;
  return async (name, args, extras = {}) => {
    if (name === "get_context") return { value: framing(args.question) };
    asked += 1;
    return { value: { question: args.question, behaviour: "figure", answer_mode: "certified", answer_text: `Certified: certified NNA by business line (turn ${asked}).`,
      figures: [{ label: "Certified NNA", value: 1000 + asked }], table: { columns: ["business line", "nna_am"], rows: [["Core Affluent", 600 + asked], ["International", 400]] },
      graph_path: graphPath(asked), receipt: receipt(asked), history: [...(extras.history ?? []), { turn_id: `t-${asked}`, question: args.question }] } };
  };
}

test("a follow-up sends the model no earlier explained path, receipt or framing, so its input grows little", async (t) => {
  const read = [];
  const model = { bindTools: () => model };
  model.invoke = async (messages) => {
    read.push(messages);
    const turn = messages.filter((m) => m._getType() === "human").length - 1;
    return new AIMessage({ content: "", tool_calls: [{ id: `c-${read.length}`, name: "ask", args: { question: ASKED[turn] } }] });
  };
  const graph = buildGraph(model, { call: synthetic(), allowed: ALLOWED });
  let held = [];
  for (const [at, question] of QUESTIONS.entries()) {
    const messages = [...held, new HumanMessage({ id: `h-${at}`, content: question })];
    held = (await graph.invoke({ messages, turnStart: messages.length, replay: false }, { configurable: { thread_id: `t-${at}` } })).messages;
  }
  assert.equal(read.length, 3, "one model call a turn");
  const bytes = read.map((messages) => Buffer.byteLength(JSON.stringify(gatewayMessages(messages))));
  const growth = [bytes[1] - bytes[0], bytes[2] - bytes[1]];
  t.diagnostic(`model input bytes per turn: ${bytes.join(", ")}; growth per follow-up: ${growth.join(", ")} (~${growth.map((b) => Math.round(b / 4)).join(", ")} tokens at 4 bytes a token)`);
  for (const messages of read) {
    const sent = JSON.stringify(gatewayMessages(messages));
    assert.doesNotMatch(sent, /graph_path|node-\d+-\d+|rcpt\./, "no explained path or receipt reaches the model");
  }
  const third = read[2];
  const framed = third.filter((m) => m._getType() === "ai" && m.tool_calls?.some((c) => isFrame(c.id))).flatMap((m) => m.tool_calls.map((c) => c.args.question));
  assert.deepEqual(framed, ["Only for Core Affluent"], "only this turn's framing is read");
  assert.equal(JSON.stringify(gatewayMessages(third)).includes("What about Q3 2024?\",\"terms"), false, "an earlier turn's framing is not resent");
  // Each follow-up adds its own question, its ask and that answer, and its own framing (read once): more than nothing and
  // less than 4 KB. Before #698 each added some 31 KB here (the pilot measured ~8,400 tokens a follow-up).
  for (const added of growth) assert.ok(added > 0 && added < 4 * 1024, `a follow-up adds ${added} bytes`);
  // The saved conversation, which the page reads, keeps every explained path, receipt and framing.
  const saved = held.filter((m) => m._getType() === "tool");
  const answers = saved.filter((m) => m.name === "ask").map((m) => JSON.parse(m.content));
  assert.deepEqual(answers.map((answer) => [answer.graph_path?.nodes?.length, answer.receipt?.slice(0, 7)]), [[120, "rcpt.1."], [120, "rcpt.2."], [120, "rcpt.3."]]);
  assert.deepEqual(saved.filter((m) => isFrame(m.tool_call_id)).map((m) => JSON.parse(m.content).question), QUESTIONS.slice(1));
});
