// #624: the page and the server's agent together. The real Cerebro agent (server/src/cerebro/agent.js) with a stand-in
// model and a stubbed KGE, behind the portal server's own stream handler (route.js), called in process, under the page's
// ChatView: a clarification's pick and an offer's pick go through the cards the server issued and the resumes the page
// sends back, so the contract between them (the interrupt, the resume, the results) cannot drift on one side alone.
// #625: the server keeps the conversation (its conversations, in memory here), the page sends the new question alone
// and a resume with no message, and the signed choose call stays in the server's saved conversation.
import React from "react";
import { afterEach, expect, test, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import ChatView from "./ChatView";
import { cerebroAgent, savedConversation } from "../../../../../server/src/cerebro/agent.js";
import { openConversations } from "../../../../../server/src/conversations/index.js";
import { cerebroStream } from "../../../../../server/src/cerebro/route.js";
import { RETAIL_QUESTION, SAMPLE_MEANINGS, kgeStub, sampleQuestions, standIn } from "../../../../../server/test/fixtures/cerebro-stand-in.js";
import { ChatProvider } from "./chatSession";

afterEach(cleanup);

/** The page's fetch, onto the real agent for a business user: the stream route, and the conversation routes the page
 *  reads (the list, and an opened conversation), on the server's own conversations. */
const PRINCIPAL = "reader";
function server(model, kge, options = {}) {
  const conversations = openConversations();
  const agent = cerebroAgent({ model, kge, conversations, retryAfterMs: 0, ...options });
  const handler = cerebroStream({ runTurn: (turn) => agent({ ...turn, roles: ["business"], req: { portalIdentity: { principal: PRINCIPAL } } }) });
  const requests = [];
  const json = (body) => new Response(JSON.stringify(body), { status: 200, headers: { "content-type": "application/json" } });
  async function fetch(url, init = {}) {
    const path = String(url).split("?")[0];
    if (path === "/api/cerebro/conversations") return json({ ...(await conversations.list(PRINCIPAL)), retention_days: 30 });
    if (path.startsWith("/api/cerebro/conversations/")) {
      const saved = await savedConversation(conversations, PRINCIPAL, path.split("/").pop());
      return saved ? json(saved) : new Response(JSON.stringify({ detail: "No such conversation." }), { status: 404 });
    }
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
  /** The choose call the server kept, last in the conversation's saved messages. */
  const keptChoose = async () => {
    const thread = requests[0].config.configurable.thread_id;
    const saved = await savedConversation(conversations, PRINCIPAL, thread);
    return saved.messages.findLast((m) => m.tool_calls?.some((c) => c.name === "choose")).tool_calls.find((c) => c.name === "choose");
  };
  return { fetch, requests, keptChoose };
}

const toolsThisTurn = (messages) => messages.slice(messages.findLastIndex((m) => m._getType() === "human")).filter((m) => m._getType() === "tool");
const asksOnce = () => standIn((messages) => (toolsThisTurn(messages).length ? { say: "unused" }
  : { call: ["ask", { question: messages.findLast((m) => m._getType() === "human").content }] }));
const figure = (question) => ({ question, behaviour: "figure", figure: 42, currency: "USD", answer_text: `Certified: ${question}: USD 42.00.`, answer_mode: "certified", sql: "SELECT 42", sql_certified: true, receipt: "r" });
const box = () => screen.getByRole("textbox", { name: "Ask the Assistant" });
async function ask(question) {
  fireEvent.change(box(), { target: { value: question } });
  await act(async () => fireEvent.click(screen.getByRole("button", { name: "Send" })));
}
const show = (fetch) => render(<ChatView state={{ roles: ["business"], userId: "reader" }} actions={{ setLastPath: vi.fn() }} fetch={fetch} />);

test("a clarification's pick: the server's HITL card, the page's resume with no message, the choose call kept by the server, and the answer card", async () => {
  const choices = [{ id: "gross", label: "Gross revenue" }, { id: "net", label: "Net revenue" }];
  const kge = kgeStub((_name, args) => ({ value: args.question === "Revenue for June 2025?"
    ? { behaviour: "clarify", answer_mode: "not_sure", reason: "Which revenue do you mean?", answer_text: "Not sure: which revenue?", clarification: { choices } }
    : figure(args.question) }));
  const model = asksOnce();
  const { fetch, requests, keptChoose } = server(model, kge);
  show(fetch);
  await ask("Revenue for June 2025?");
  const card = await screen.findByRole("region", { name: "Which revenue do you mean?" });
  expect(within(within(card).getByRole("group")).getAllByRole("button").map((b) => b.textContent)).toEqual(["Choose Gross revenue.", "Choose Net revenue.", "Something else"]);
  expect(screen.queryByRole("article", { name: "Answer" })).toBeNull();

  await act(async () => fireEvent.click(within(card).getByRole("button", { name: "Choose Net revenue." })));
  const answer = await screen.findByRole("article", { name: "Answer" });
  expect(within(answer).queryByRole("button", { name: /revenue/i })).toBeNull();
  // #626: the SQL is in the side panel's SQL tab, opened from the compact card.
  await act(async () => fireEvent.click(within(answer).getByRole("button", { name: /^Open/ })));
  const panel = document.getElementById("cerebro-panel");
  await act(async () => fireEvent.click(within(within(panel).getByRole("tablist", { name: "The answer's parts" })).getByRole("tab", { name: "SQL" })));
  expect(within(panel).getByRole("region", { name: "SQL" })).toHaveTextContent("SELECT 42");
  expect(requests[1].command).toEqual({ resume: { pick: "net" } });
  expect(requests[1].input).toBeNull();
  expect(requests[1].config.configurable.thread_id).toBe(requests[0].config.configurable.thread_id);
  expect(requests[0].input.messages).toEqual([expect.objectContaining({ type: "human", content: "Revenue for June 2025?" })]);
  const kept = await keptChoose();
  expect(kept.id).toMatch(/^hitl-/);
  expect(kept.args).toMatchObject({ source: "clarify", question: "Which revenue do you mean?", options: choices });
  expect(kge.calls.map((c) => c.args.question)).toEqual(["Revenue for June 2025?", "Net revenue"]);
  expect(model.calls).toBe(1);
  expect(within(screen.getByRole("region", { name: "Which revenue do you mean?" })).getByText("You: Net revenue")).toBeInTheDocument();
  expect([...document.querySelectorAll(".agentchat-bubble")].map((b) => b.textContent)).toEqual(["Revenue for June 2025?"]);
});

test("an offer's pick: the server's offer after a difference answer, the page's resume, and the comparison's card", async () => {
  const question = "What is the difference between the definitions of current NNA and certified NNA?";
  const range = (term, first, latest) => ({ term, definition: `${term} as declared.`, period_range: { first, latest } });
  const difference = {
    question, behaviour: "cite_certified", answer_mode: "certified", receipt: "r",
    answer_text: "Certified: current NNA and certified NNA differ in their version dates.",
    difference: { terms: [range("current NNA", "2024-01-01", "2025-06-30"), range("certified NNA", "2024-04-01", "2025-08-01")], rows: [], summary: "They differ in their version dates." },
  };
  const kge = kgeStub((_name, args) => ({ value: args.question === question ? difference : figure(args.question) }));
  const model = asksOnce();
  const { fetch, requests, keptChoose } = server(model, kge);
  show(fetch);
  await ask(question);
  const card = await screen.findByRole("region", { name: "Compare the figures?" });
  expect(within(within(card).getByRole("group")).getAllByRole("button").map((b) => b.textContent)).toEqual(["June 2025", "Q2 2025", "No thanks", "Pick a month"]);
  await waitFor(() => expect(within(card).getByRole("button", { name: "June 2025" })).toBeEnabled());

  await act(async () => fireEvent.click(within(card).getByRole("button", { name: "June 2025" })));
  // #651 (B4): the comparison's figures are the one answer card; the difference answer is a message only.
    await waitFor(() => expect(screen.getAllByRole("article", { name: "Answer" })).toHaveLength(1));
  expect(requests[1].command).toEqual({ resume: { pick: "latest_month" } });
  expect(requests[1].input).toBeNull();
  expect((await keptChoose()).args).toMatchObject({ source: "offer" });
  expect(kge.calls.map((c) => c.args.question)).toEqual([question, "Compare current NNA vs. certified NNA for June 2025, with the dollar and percent difference"]);
  expect(model.calls).toBe(1);
  expect(within(screen.getByRole("region", { name: "Compare the figures?" })).getByText("You: June 2025")).toBeInTheDocument();
});

test("#713: the multiple-question card from the server: a Meaning and a Segment group, Send disabled until both have a pick, one ask, and every pick on the answered card and the reopened chat", async () => {
  const kge = kgeStub((_name, args) => ({ value: figure(args.question) }));
  const model = standIn(() => ({ say: "unused" }));
  const { fetch, requests, keptChoose } = server(model, kge, { openQuestions: sampleQuestions });
  show(fetch);
  await ask(RETAIL_QUESTION);
  const card = await screen.findByRole("region", { name: "Before I ask, choose one option in each group." });
  const group = (name) => within(card).getByRole("group", { name });
  const meanings = ["Certified NNA (default)", ...SAMPLE_MEANINGS.filter((o) => !o.suggested).map((o) => o.label)];
  expect(within(group("Meaning")).getAllByRole("button").map((b) => b.textContent)).toEqual(meanings);
  expect(within(group("Segment")).getAllByRole("button").map((b) => b.textContent)).toEqual(["Client segment", "Advisor team"]);
  expect(within(card).queryAllByRole("button", { pressed: true })).toEqual([]);
  const send = () => within(card).getByRole("button", { name: "Send your choices" });
  await waitFor(() => expect(within(group("Meaning")).getByRole("button", { name: "Certified NNA (default)" })).toBeEnabled());
  expect(send()).toBeDisabled();
  fireEvent.click(within(group("Meaning")).getByRole("button", { name: "Branch-sales NNA" }));
  expect(send()).toBeDisabled();
  fireEvent.click(within(group("Segment")).getByRole("button", { name: "Branch region" }));
  expect(send()).toBeEnabled();
  expect(kge.calls.filter((c) => c.name === "ask")).toEqual([]);
  await act(async () => fireEvent.click(send()));
  await screen.findByRole("article", { name: "Answer" });
  const kept = await keptChoose();
  expect(kept.args.source).toBe("questions");
  expect(requests[1].command).toEqual({ resume: { picks: { meaning: "branch-sales-nna", segment: "branch-region" } } });
  expect(kge.calls.filter((c) => c.name === "ask").map((c) => c.args.question)).toEqual(["What was the branch-sales NNA for Q1 2025 by branch region?"]);
  expect(model.calls).toBe(0);
  const you = "You: Meaning: Branch-sales NNA; Segment: Branch region";
  expect(within(screen.getByRole("region", { name: "Before I ask, choose one option in each group." })).getByText(you)).toBeInTheDocument();
  cleanup();

  render(
    <ChatProvider actions={{}} fetch={fetch} openId={requests[0].config.configurable.thread_id}>
      <ChatView state={{ roles: ["business"], userId: PRINCIPAL }} actions={{ setLastPath: vi.fn() }} />
    </ChatProvider>,
  );
  const reopened = await screen.findByRole("region", { name: "Before I ask, choose one option in each group." });
  expect(within(reopened).getByText(you)).toBeInTheDocument();
  expect(within(reopened).getAllByRole("button", { pressed: true }).map((b) => b.textContent)).toEqual(["Branch-sales NNA", "Branch region"]);
  for (const b of within(reopened).getAllByRole("button")) expect(b).toBeDisabled();
});
