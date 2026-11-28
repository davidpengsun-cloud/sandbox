// Cerebro's Chat mode (B7b) on the portal server's own stream handler, called in process with a mock agent, as
// streamCheck.test.js does: the answer shows in the middle, the input bar sends no ai_fallback, the SQL is in the
// card's side panel for every role (#624, #626) while the plan and trace are an engineer's only, and (#651) Like and
// Dislike under the reply save its verdict and post the answer's receipt as Context's Ask does.
import React from "react";
import { afterEach, expect, test, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { cerebroStream } from "../../../../../server/src/cerebro/route.js";
import ChatView from "./ChatView";
import { answerOf, framingOf, lastPathOf, postFeedback, richOf, turnCards } from "./chatAnswer";
import { askTurn, mockAgent as stubAgent } from "./chatTestAgent";

afterEach(cleanup);

const SQL = "SELECT SUM(amount) FROM sales_secret_table";
const REPLY = {
  answer_text: "Certified: Revenue was 42.",
  answer_mode: "certified",
  receipt: { id: "r-1", sig: "abc" },
  plan: [{ unit: "revenue", status: "planned" }],
  sql: SQL,
  trace: [{ step: "plan_trace_marker" }],
  graph_path: ["asset:sales", "column:amount"],
};

function mockAgent({ hold, reply = REPLY, say = "Here is the answer." } = {}) {
  const requests = [];
  let release;
  const gate = hold ? new Promise((r) => (release = r)) : null;
  async function* runTurn({ messages }) {
    const call = { type: "ai", id: "ai-1", content: "", tool_calls: [{ id: "tc-1", name: "ask", args: { question: "Revenue?" } }] };
    yield ["values", { messages: [...messages, call] }];
    if (gate) await gate;
    const result = { type: "tool", id: "tool-1", tool_call_id: "tc-1", name: "ask", content: JSON.stringify(reply), card: true };
    yield ["values", { messages: [...messages, call, result, { type: "ai", id: "ai-2", content: say }] }];
    yield ["custom", { kind: "outcome", outcome: "answered", steps: 2 }];
  }
  const handler = cerebroStream({ runTurn });
  async function portalFetch(_url, init) {
    const body = JSON.parse(init.body);
    requests.push(body);
    let controller;
    const stream = new ReadableStream({ start: (c) => (controller = c) });
    const encoder = new TextEncoder();
    let status = 200;
    const res = { on() {}, setHeader() {}, writeHead: (s) => (status = s), write: (chunk) => controller.enqueue(encoder.encode(chunk)), end: () => controller.close() };
    void handler({ body }, res);
    return new Response(stream, { status, headers: { "content-type": "text/event-stream" } });
  }
  return { requests, portalFetch, release: () => release?.() };
}

function renderChat(roles, props = {}) {
  const agent = mockAgent(props.agent);
  const actions = { setLastPath: vi.fn() };
  render(<ChatView state={{ roles }} actions={actions} fetch={agent.portalFetch} {...props} />);
  return { agent, actions };
}

async function ask(question = "Revenue?") {
  fireEvent.change(screen.getByRole("textbox", { name: "Ask the Assistant" }), { target: { value: question } });
  await act(async () => fireEvent.click(screen.getByRole("button", { name: "Send" })));
}

// #626: the card opens whole in the side panel; a tab of it is chosen by name.
function openPanel(answer) {
  fireEvent.click(within(answer).getByRole("button", { name: /^Open/ }));
  return document.getElementById("cerebro-panel");
}
const chooseTab = (panel, name) => fireEvent.click(within(within(panel).getByRole("tablist", { name: "The answer's parts" })).getByRole("tab", { name }));

test("the answer streams into the middle with its mode label, on the stream route, and the working block shows", async () => {
  const { agent, actions } = renderChat(["business"], { agent: { hold: true } });
  await ask();
  expect(screen.getByText("Revenue?", { selector: ".agentchat-bubble" })).toBeInTheDocument();
  await waitFor(() => expect(screen.getByRole("status")).toHaveTextContent("Understanding your question"));
  await act(async () => agent.release());
  const answer = await screen.findByRole("article", { name: "Answer" });
  expect(within(answer).getByText("Certified")).toHaveAttribute("data-answer-mode", "certified");
  expect(within(answer).getByText("Revenue was 42.")).toBeInTheDocument();
  expect(screen.getByText("Here is the answer.")).toBeInTheDocument();
  // #651 (B2, B5): the pinned status line goes, with no spinner left; the reply says when it was sent and how long it took.
  await waitFor(() => expect(document.querySelector(".cerebro-live-status")).toBeNull());
  expect(document.querySelector(".waiting-status")).toBeNull();
  expect(document.querySelector(".cerebro-reply-meta")).toHaveTextContent(/^Sent .+ · Took \d+ s$/);
  expect(agent.requests[0].input.messages).toEqual([expect.objectContaining({ type: "human", content: "Revenue?" })]);
  expect(actions.setLastPath).toHaveBeenCalledWith(REPLY.graph_path);
});

test("the input bar offers no AI-fallback control and no stream request carries ai_fallback", async () => {
  const { agent } = renderChat(["business"]);
  await ask("First?");
  await screen.findByRole("article", { name: "Answer" });
  // #651 (B7): the model chip alone, nothing to open.
  expect(screen.queryByRole("button", { name: /^Answer settings:/ })).toBeNull();
  expect(screen.queryAllByRole("radio")).toHaveLength(0);
  expect(document.getElementById("agentchat-settings")).not.toHaveTextContent(/fallback/i);
  await ask("Second?");
  await waitFor(() => expect(agent.requests).toHaveLength(2));
  for (const request of agent.requests) {
    expect(request.context).toBeUndefined();
    expect(JSON.stringify(request)).not.toContain("ai_fallback");
  }
});

test("a business user sees the SQL in the panel, once, and no plan or trace anywhere, not even in raw tool output (#624, #626)", async () => {
  renderChat(["business", "steward"]);
  await ask();
  const answer = await screen.findByRole("article", { name: "Answer" });
  // The chat card is compact: no SQL on it.
  expect(within(answer).queryByRole("region", { name: "SQL" })).toBeNull();
  const panel = openPanel(answer);
  const sql = within(panel).getByRole("region", { name: "SQL" });
  expect(sql.querySelector("pre").textContent).toBe("SELECT SUM(amount)\nFROM sales_secret_table");
  expect(within(sql).getByRole("button", { name: "Copy the SQL" })).toBeInTheDocument();
  // What More held, Context's card, leaves the SQL to its own tab: it is shown once.
  chooseTab(panel, "Full answer");
  expect(panel.querySelector(".answer-card")).not.toBeNull();
  expect(document.body.textContent).not.toContain("sales_secret_table");
  chooseTab(panel, "SQL");
  expect(document.body.textContent.split("sales_secret_table")).toHaveLength(2);
  expect(within(panel).queryByRole("tab", { name: "Behind the answer" })).toBeNull();
  expect(within(panel).queryByRole("tab", { name: /^(Plan|Trace)$/ })).toBeNull();
  expect(screen.queryByText("Plan, SQL and trace")).not.toBeInTheDocument();
  expect(document.body.textContent).not.toContain("plan_trace_marker");
  expect(screen.queryByRole("region", { name: /^Tool (call|result)/ })).not.toBeInTheDocument();
});

// #607: an engineer's plan, SQL and trace are Context's Behind the answer, in the card's side panel (#626).
test("an engineer sees the answer's plan, SQL and trace in Context's Behind the answer, in the panel", async () => {
  renderChat(["engineer"]);
  await ask();
  const answer = await screen.findByRole("article", { name: "Answer" });
  expect(within(answer).queryByRole("button", { name: "Behind the answer" })).toBeNull();
  const panel = openPanel(answer);
  chooseTab(panel, "Behind the answer");
  expect(panel.querySelector("#tab-plan")).toHaveTextContent(SQL);
  expect(panel.querySelector("#tab-trace")).toHaveTextContent("plan_trace_marker");
  expect(screen.getByRole("region", { name: "Tool call ask" })).toBeInTheDocument();
});

// #651 (B2): Like and Dislike sit under each reply, saved on the verdict route; on an answer card they also post the
// answer's receipt to KGE as ✓/✗ did (like: confirmed, dislike: not_what_i_meant). The panel has no ✓/✗.
test("Like under the reply saves the verdict and posts the answer's receipt exactly as given, confirmed (#624, #626, #651)", async () => {
  const sendFeedback = vi.fn(async () => ({}));
  const stub = stubAgent([askTurn({ ...REPLY, figure: 42, currency: "USD" })]);
  render(<ChatView state={{ roles: ["business"] }} actions={{ setLastPath: () => {} }} fetch={stub.fetch} sendFeedback={sendFeedback} />);
  await ask();
  const card = await screen.findByRole("article", { name: "Answer" });
  expect(within(card).queryByRole("group", { name: "Was this what you meant?" })).toBeNull();
  const panel = openPanel(card);
  expect(within(panel).queryByRole("button", { name: "Confirmed" })).toBeNull();
  const reply = await screen.findByRole("group", { name: "This reply" });
  await act(async () => fireEvent.click(within(reply).getByRole("button", { name: "Like" })));
  await waitFor(() => expect(within(reply).getByRole("button", { name: "Like" })).toHaveAttribute("aria-pressed", "true"));
  expect(stub.server.verdicts).toEqual([{ conversation: expect.any(String), message: stub.requests[0].input.messages[0].id, verdict: "like" }]);
  expect(sendFeedback).toHaveBeenCalledWith({ receipt: REPLY.receipt, verdict: "confirmed" });
  expect(within(reply).getByRole("button", { name: "Dislike" })).toHaveAttribute("aria-pressed", "false");
  // In the full answer, Context's card no longer offers ✓/✗ in Cerebro.
  chooseTab(panel, "Full answer");
  expect(panel.querySelector(".answer-card .feedback")).toBeNull();
});

test("Dislike whose KGE feedback is not recorded says so; the verdict is one per reply, locked once saved (#651)", async () => {
  const sendFeedback = vi.fn(async () => ({ error: "Not Found" }));
  const stub = stubAgent([askTurn({ ...REPLY, figure: 42, currency: "USD" })]);
  render(<ChatView state={{ roles: ["business"] }} actions={{ setLastPath: () => {} }} fetch={stub.fetch} sendFeedback={sendFeedback} />);
  await ask();
  const reply = await screen.findByRole("group", { name: "This reply" });
  await act(async () => fireEvent.click(within(reply).getByRole("button", { name: "Dislike" })));
  expect(sendFeedback).toHaveBeenCalledWith({ receipt: REPLY.receipt, verdict: "not_what_i_meant" });
  await waitFor(() => expect(within(reply).getByRole("alert")).toHaveTextContent("Your feedback was not recorded: Not Found"));
  // Both buttons are locked showing the choice: no second press changes it or clears it.
  expect(within(reply).getByRole("button", { name: "Dislike" })).toHaveAttribute("aria-pressed", "true");
  expect(within(reply).getByRole("button", { name: "Dislike" })).toBeDisabled();
  expect(within(reply).getByRole("button", { name: "Like" })).toBeDisabled();
  await act(async () => fireEvent.click(within(reply).getByRole("button", { name: "Dislike" })));
  await act(async () => fireEvent.click(within(reply).getByRole("button", { name: "Like" })));
  expect(stub.server.verdicts.map((v) => v.verdict)).toEqual(["dislike"]);
  expect(sendFeedback).toHaveBeenCalledTimes(1);
});

test("postFeedback sends { receipt, verdict } as JSON and turns a refusal into an error", async () => {
  const fetcher = vi.fn(async () => new Response(JSON.stringify({ detail: "No such receipt" }), { status: 404 }));
  const body = { receipt: { id: "r-1" }, verdict: "confirmed" };
  expect(await postFeedback(body, { fetcher })).toEqual({ error: "No such receipt" });
  const [url, init] = fetcher.mock.calls[0];
  expect(url).toBe("/api/cerebro/ask/feedback");
  expect(init).toEqual(expect.objectContaining({ method: "POST", body: JSON.stringify(body) }));
  expect(await postFeedback(body, { fetcher: async () => new Response("{}", { status: 200 }) })).toEqual({});
  expect(await postFeedback(body, { fetcher: async () => { throw new Error("down"); } })).toEqual({ error: expect.stringMatching(/unavailable/) });
});

test("only an ask tool message with a JSON reply is an answer", () => {
  expect(answerOf({ type: "tool", name: "ask", content: "not json" })).toBeNull();
  expect(answerOf({ type: "tool", name: "search", content: JSON.stringify(REPLY) })).toBeNull();
  expect(answerOf({ type: "tool", name: "ask", content: JSON.stringify(REPLY) })).toEqual(REPLY);
  expect(answerOf({ type: "tool", name: "ask", content: [{ type: "text", text: JSON.stringify(REPLY) }] })).toEqual(REPLY);
  expect(lastPathOf([{ type: "tool", name: "ask", content: "{\"answer_text\":\"x\"}" }])).toBeNull();
  expect(lastPathOf([{ type: "tool", name: "ask", content: JSON.stringify(REPLY) }, { type: "ai", content: "x" }])).toEqual(REPLY.graph_path);
});

test("Ask about this: the node is filled in to the question, the question carries it as context.node, and the send clears it", async () => {
  const asked = vi.fn();
  const agent = mockAgent();
  render(<ChatView state={{ roles: ["business"], node: "table:retail.orders", asking: "table:retail.orders" }} actions={{ setLastPath: vi.fn(), asked }} fetch={agent.portalFetch} />);
  const box = screen.getByRole("textbox", { name: "Ask the Assistant" });
  expect(box).toHaveValue("About the table retail.orders: ");
  fireEvent.change(box, { target: { value: "About the table retail.orders: who owns it?" } });
  await act(async () => fireEvent.click(screen.getByRole("button", { name: "Send" })));
  await screen.findByRole("article", { name: "Answer" });
  expect(agent.requests[0].context).toEqual({ node: "table:retail.orders" });
  expect(agent.requests[0].input.messages).toEqual([expect.objectContaining({ content: "About the table retail.orders: who owns it?" })]);
  expect(asked).toHaveBeenCalledTimes(1);
});

test("a node only selected (in the address, not asked about) fills nothing and is not sent", async () => {
  const { agent } = renderChat(["business"], { state: { roles: ["business"], node: "table:retail.orders", asking: null } });
  expect(screen.getByRole("textbox", { name: "Ask the Assistant" })).toHaveValue("");
  await ask("Revenue?");
  await screen.findByRole("article", { name: "Answer" });
  expect(agent.requests[0].context).toBeUndefined();
});

const GOLDEN = Array.from({ length: 7 }, (_, i) => `Golden question ${i + 1}?`);
const tryList = () => screen.queryByRole("region", { name: "Suggested questions" });

test("the empty chat shows five golden questions to try, Show all lists the rest, and a click asks the question", async () => {
  const { agent } = renderChat(["business"], { questions: GOLDEN });
  const list = tryList();
  expect(within(list).getByText("Try one of these")).toBeInTheDocument();
  expect(within(list).getAllByRole("button", { name: /^Golden question/ }).map((b) => b.textContent)).toEqual(GOLDEN.slice(0, 5));
  expect(screen.queryByText("Ask a question about your data to start.")).not.toBeInTheDocument();
  fireEvent.click(within(list).getByRole("button", { name: "Show all 7" }));
  expect(within(list).getAllByRole("button", { name: /^Golden question/ }).map((b) => b.textContent)).toEqual(GOLDEN);
  expect(within(list).getByRole("button", { name: "Show fewer" })).toHaveAttribute("aria-expanded", "true");
  await act(async () => fireEvent.click(within(list).getByRole("button", { name: "Golden question 6?" })));
  await screen.findByRole("article", { name: "Answer" });
  expect(agent.requests[0].input.messages).toEqual([expect.objectContaining({ type: "human", content: "Golden question 6?" })]);
  expect(tryList()).not.toBeInTheDocument();
  expect(screen.getByRole("textbox", { name: "Ask the Assistant" })).toHaveFocus();
});

test("five questions or fewer need no Show all; with no golden questions the chat keeps its plain invitation", () => {
  renderChat(["business"], { questions: GOLDEN.slice(0, 5) });
  expect(within(tryList()).getAllByRole("button")).toHaveLength(5);
  expect(screen.queryByRole("button", { name: /^Show all/ })).not.toBeInTheDocument();
  cleanup();
  renderChat(["business"], { questions: [] });
  expect(tryList()).not.toBeInTheDocument();
  expect(screen.getByText("Ask a question about your data to start.")).toBeInTheDocument();
});

test("the golden list hides once a question is asked and shows again on New chat", async () => {
  renderChat(["business"], { questions: GOLDEN });
  expect(tryList()).toBeInTheDocument();
  await ask("My own question?");
  await screen.findByRole("article", { name: "Answer" });
  expect(tryList()).not.toBeInTheDocument();
  await act(async () => fireEvent.click(screen.getByRole("button", { name: "New chat" })));
  expect(tryList()).toBeInTheDocument();
});

test("the golden list hides while Ask about this fills the question box", () => {
  const agent = mockAgent();
  render(<ChatView state={{ roles: ["business"], node: "table:retail.orders", asking: "table:retail.orders" }} actions={{ setLastPath: vi.fn(), asked: vi.fn() }} fetch={agent.portalFetch} questions={GOLDEN} />);
  expect(screen.getByRole("textbox", { name: "Ask the Assistant" })).toHaveValue("About the table retail.orders: ");
  expect(tryList()).not.toBeInTheDocument();
});

// #602: the card is the answer, rendered as Context's rich answer where Ask gave a display table.
const DISPLAY = {
  v: 1,
  headline: { text: "Revenue by region for March 2025, largest first.", by: "template" },
  table: { caption: "Revenue by region", columns: [{ key: "region", label: "Region", type: "text" }, { key: "value", label: "Revenue", type: "number", currency: "USD" }], rows: [["North", 30], ["South", 12]] },
  chart: null,
  chart_note: "one figure per region",
  notes: ["as reported"],
};

test("an answer with a display table renders as the rich table in the card, with its headline and caption", async () => {
  renderChat(["business"], { agent: { reply: { ...REPLY, display: DISPLAY }, say: "" } });
  await ask();
  const answer = await screen.findByRole("article", { name: "Answer" });
  expect(within(answer).getByText("Certified")).toBeInTheDocument();
  expect(within(answer).getByText(/Revenue by region for March 2025/)).toBeInTheDocument();
  const table = within(answer).getByRole("table");
  expect(within(table).getByText("Revenue by region")).toBeInTheDocument();
  expect(within(table).getByRole("columnheader", { name: "Region" })).toBeInTheDocument();
  expect(within(table).getByRole("cell", { name: "North" })).toBeInTheDocument();
  expect(within(table).getAllByRole("row")).toHaveLength(3);
  expect(within(answer).queryByText("Revenue was 42.")).not.toBeInTheDocument();
});

test("an answer with no display table, or a display that failed, renders its text as before", async () => {
  renderChat(["business"], { agent: { reply: { ...REPLY, display: { v: 1, error: "TypeError: x", table: null } } } });
  await ask();
  const answer = await screen.findByRole("article", { name: "Answer" });
  expect(within(answer).getByText("Revenue was 42.")).toBeInTheDocument();
  expect(within(answer).queryByRole("table")).not.toBeInTheDocument();
});

test("a turn with an Ask card shows the figure once: agent text that repeats the card is not shown", async () => {
  renderChat(["business"], { agent: { say: "Certified: Revenue was 42." } });
  await ask();
  await screen.findByRole("article", { name: "Answer" });
  await waitFor(() => expect(screen.getAllByText(/Revenue was 42/)).toHaveLength(1));
});

test("the agent's own framing around a repeated answer_text is kept, the repeat taken out", async () => {
  renderChat(["business"], { agent: { say: "Revenue was 42. Would you like it by region?" } });
  await ask();
  await screen.findByRole("article", { name: "Answer" });
  expect(await screen.findByText("Would you like it by region?")).toBeInTheDocument();
  expect(screen.getAllByText(/Revenue was 42/)).toHaveLength(1);
});

test("framingOf: a repeated sentence goes in any case, spacing, markdown or label; the agent's own lines and line breaks stay", () => {
  const card = "Certified: Revenue for June 2025: USD 13,287.67 (retail).\nRevenue: Income earned from sales.";
  const texts = [card, card.replace(/^Certified: /, "")];
  expect(framingOf("Revenue for June 2025: USD 13,287.67 (retail).", texts)).toBe("");
  expect(framingOf("**Certified:** revenue for june 2025:  USD 13,287.67 (retail).\n\nRevenue: Income earned from sales.\n\nWant it by region?", texts)).toBe("Want it by region?");
  expect(framingOf("Here it is:\nRevenue for June 2025: USD 13,287.67 (retail).", texts)).toBe("");
  expect(framingOf(`${card}\n\nWhich do you mean?\n- Net revenue\n- Gross revenue`, texts)).toBe("Which do you mean?\n- Net revenue\n- Gross revenue");
  expect(framingOf("Sorry, I cannot answer that. The graph has no such term.", ["I cannot answer that."])).toBe("Sorry, I cannot answer that. The graph has no such term.");
  expect(framingOf("Options:\n- a  term", texts)).toBe("Options:\n- a  term");
  expect(framingOf("ok", ["ok"])).toBe("ok");
  expect(framingOf("Graph only.", [])).toBe("Graph only.");
  expect(framingOf(undefined, texts)).toBe("");
});

test("turnCards: each assistant message is held to its own turn's cards, so a recap the person asked for stays", () => {
  const reply = (text, extra = {}) => ({ type: "tool", name: "ask", content: JSON.stringify({ ...REPLY, answer_text: text, ...extra }) });
  const messages = [
    { type: "human", content: "a" }, reply("Certified: Revenue was 42.", { display: DISPLAY }), { type: "ai", id: "ai-1", content: "x" },
    { type: "human", content: "recap please" }, { type: "ai", id: "ai-2", content: "First, revenue was 42." }, { type: "ai", content: "no id" },
  ];
  const cards = turnCards(messages, (t) => t.replace(/^Certified: /, ""));
  expect(cards.get("ai-1")).toEqual(["Certified: Revenue was 42.", "Revenue was 42.", DISPLAY.headline.text]);
  expect(cards.get("ai-2")).toEqual([]);
  expect(turnCards(undefined).size).toBe(0);
  expect(richOf({ display: DISPLAY })).toBe(DISPLAY);
  expect(richOf({ display: { ...DISPLAY, error: "x" } })).toBeNull();
  expect(richOf({ display: { v: 1, table: null } })).toBeNull();
  expect(richOf({})).toBeNull();
});

test("the rich card's headline drops the label the chip already shows", async () => {
  const display = { ...DISPLAY, headline: { text: "Certified: Revenue by region for March 2025.\nRevenue: income earned.", by: "template" } };
  renderChat(["business"], { agent: { reply: { ...REPLY, display }, say: "" } });
  await ask();
  const answer = await screen.findByRole("article", { name: "Answer" });
  expect(within(answer).getAllByText(/Certified/)).toHaveLength(1);
  // #613: the compact card's label is the headline's first line; the full headline is Context's, in the panel (#626).
  expect(answer.querySelector(".cerebro-compact-label")).toHaveTextContent(/^Revenue by region for March 2025\.$/);
  const panel = openPanel(answer);
  chooseTab(panel, "Full answer");
  expect(panel.querySelector(".ans").textContent).toMatch(/^Revenue by region for March 2025\.\nRevenue: income earned\.$/);
});
