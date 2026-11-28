// #625 on the page: the portal's server keeps each person's conversations, and the page sends the new message alone
// with the conversation's id. On a stand-in for the server's Cerebro routes (chatTestServer.js) with a mock agent
// (chatTestAgent.js): the stream request carries one message and the thread id, a resume none, an edited question its
// rewind; a conversation opened from the server shows its messages and a question back to the person still waiting,
// whose answer resumes it; a turn refused because the conversation changed in another tab offers Reload; a trimmed
// conversation says so at its top; an answer that could not be saved offers Try again; a reply the server kept only the first rows of says so in the panel and on its
// card; and where the server keeps nothing, the open conversation is deleted as the tab closes.
import React from "react";
import { afterEach, expect, test, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import ChatView from "./ChatView";
import { ChatProvider, TRIMMED_NOTE, interruptOf, newConversationId } from "./chatSession";
import { keptRows, openLabel, sizeOf, truncatedOf } from "./artifacts";
import { HOLD_HERE, askTurn, hitlTurn, mockAgent, pickTurn, rawTurn } from "./chatTestAgent";
import { titleOf } from "./chatTestServer";

afterEach(() => { cleanup(); vi.restoreAllMocks(); });

const figure = (question, extra = {}) => ({
  question, behaviour: "figure", figure: 42, currency: "USD", answer_text: `Certified: ${question}: USD 42.00.`, answer_mode: "certified",
  sql: "SELECT 42", sql_certified: true, receipt: "r", ...extra,
});
const box = () => screen.getByRole("textbox", { name: "Ask the Assistant" });
async function ask(question) {
  fireEvent.change(box(), { target: { value: question } });
  await act(async () => fireEvent.click(screen.getByRole("button", { name: "Send" })));
}
const state = { roles: ["business"], userId: "reader@example.com" };
// Conversation ids are UUIDs, as the page makes them and the server takes them.
const [PAUSED, GONE, LONG, THEIRS, ONE] = ["1", "2", "3", "4", "5"].map((n) => `00000000-0000-4000-8000-feed0000000${n}`);
function renderChat(agent) {
  render(<ChatView state={state} actions={{ setLastPath: vi.fn() }} fetch={agent.fetch} />);
  return agent;
}
/** The page as the address names a conversation: the provider opens it as the page starts. */
function renderOpen(agent, id, actions = {}) {
  render(
    <ChatProvider actions={actions} fetch={agent.fetch} openId={id}>
      <ChatView state={state} actions={actions} />
    </ChatProvider>,
  );
  return agent;
}
const bubbles = () => [...document.querySelectorAll(".agentchat-bubble")].map((b) => b.textContent);
const answers = () => screen.queryAllByRole("article", { name: "Answer" });

test("the stream request carries exactly one message, the person's new question, and the conversation's id", async () => {
  const agent = renderChat(mockAgent([askTurn((q) => figure(q))]));
  await ask("First?");
  await waitFor(() => expect(answers()).toHaveLength(1));
  await ask("Second?");
  await waitFor(() => expect(answers()).toHaveLength(2));
  for (const [i, question] of [[0, "First?"], [1, "Second?"]]) {
    const { input, command, config } = agent.requests[i];
    expect(input.messages).toEqual([{ type: "human", id: expect.any(String), content: question }]);
    expect(command).toBeUndefined();
    expect(config.configurable.thread_id).toMatch(/^[\w-]+$/);
  }
  expect(agent.requests[1].config.configurable.thread_id).toBe(agent.requests[0].config.configurable.thread_id);
  // The page shows the whole conversation, which the server kept: its first question too.
  expect(bubbles()).toEqual(["First?", "Second?"]);
});

test("a conversation opened from the server shows its messages and the question still waiting; the pick resumes it with no message", async () => {
  const agent = mockAgent([pickTurn((q) => figure(q))]);
  const human = { type: "human", id: "h-1", content: "Revenue for June 2025" };
  const paused = hitlTurn()([human], 1).at(-1)[1];
  agent.server.seed({ id: PAUSED, title: "Revenue for June 2025", messages: paused.messages, interrupt: paused.__interrupt__ });
  renderOpen(agent, PAUSED);
  expect(screen.getByText("Opening this chat…")).toBeInTheDocument();
  const card = await screen.findByRole("region", { name: "Which revenue do you mean?" });
  expect(screen.queryByText("Opening this chat…")).toBeNull();
  expect(bubbles()).toEqual(["Revenue for June 2025"]);
  expect(agent.server.calls).toContain(`GET /api/cerebro/conversations/${PAUSED}`);
  // Edit waits while the question back to the person is open, as it does live.
  expect(screen.queryByRole("button", { name: /^Edit/ })).toBeNull();
  await act(async () => fireEvent.click(within(card).getByRole("button", { name: "Choose Net revenue." })));
  await waitFor(() => expect(agent.requests).toHaveLength(1));
  expect(agent.requests[0]).toMatchObject({ input: null, command: { resume: { pick: "net" } }, config: { configurable: { thread_id: PAUSED } } });
  await screen.findByRole("article", { name: "Answer" });
  expect(within(screen.getByRole("region", { name: "Which revenue do you mean?" })).getByText("You: Net revenue")).toBeInTheDocument();
  expect(agent.ran[0].at(-1).tool_calls[0].name).toBe("choose");
});

test("interruptOf reads the open route's interrupt as the stream hook gives one", () => {
  const one = { id: "i", value: { kind: "hitl" } };
  expect(interruptOf([one])).toBe(one);
  expect(interruptOf([one, one])).toEqual([one, one]);
  expect(interruptOf(one)).toBe(one);
  expect(interruptOf([])).toBeNull();
  expect(interruptOf(null)).toBeNull();
});

test("a conversation the server no longer keeps says so, and New chat starts afresh", async () => {
  const agent = mockAgent([askTurn((q) => figure(q))]);
  const setChat = vi.fn();
  renderOpen(agent, GONE, { setChat });
  await screen.findByText("This chat is no longer kept. Start a new one.");
  await ask("Fresh?");
  await screen.findByRole("article", { name: "Answer" });
  expect(agent.requests[0].config.configurable.thread_id).toBe(GONE);
  expect(screen.queryByText(/no longer kept/)).toBeNull();
});

test("a turn refused because the conversation changed in another tab shows its message with Reload, which reopens it from the server", async () => {
  const changed = { case: "conversation_changed", message: "This chat changed in another tab: reload it.", action: "reload" };
  const agent = renderChat(mockAgent([askTurn((q) => figure(q)), rawTurn([HOLD_HERE, ["custom", { kind: "outcome", outcome: "failed", steps: 1, error: changed }]])]));
  await ask("First?");
  await waitFor(() => expect(answers()).toHaveLength(1));
  await ask("Second?");
  await waitFor(() => expect(agent.requests).toHaveLength(2));
  // Meanwhile another tab asked in the same conversation.
  const id = agent.requests[0].config.configurable.thread_id;
  const theirs = agent.server.conversations.get(id).messages.slice(0, 3);
  agent.server.seed({ id, title: "First?", messages: [...theirs, { type: "human", id: "h-other", content: "From the other tab?" }] });
  await act(async () => agent.release());
  const card = await screen.findByRole("article", { name: "Not answered" });
  expect(within(card).getByRole("alert")).toHaveTextContent("This chat changed in another tab: reload it.");
  expect(within(card).queryByRole("button", { name: "Try again" })).toBeNull();
  await act(async () => fireEvent.click(within(card).getByRole("button", { name: "Reload" })));
  await waitFor(() => expect(bubbles()).toEqual(["First?", "From the other tab?"]));
  expect(agent.server.calls.filter((c) => c === `GET /api/cerebro/conversations/${id}`)).toHaveLength(1);
  expect(answers()).toHaveLength(1);
});

test("a turn whose answer could not be saved shows its message with Try again, which asks the question again", async () => {
  const notSaved = { case: "not_saved", message: "This answer could not be saved: try again.", action: "try_again" };
  const agent = renderChat(mockAgent([rawTurn([["custom", { kind: "outcome", outcome: "failed", steps: 1, error: notSaved }]]), askTurn((q) => figure(q))]));
  await ask("Unsaved?");
  const card = await screen.findByRole("article", { name: "Not answered" });
  expect(within(card).getByRole("alert")).toHaveTextContent("This answer could not be saved: try again.");
  expect(within(card).queryByRole("button", { name: "Reload" })).toBeNull();
  await act(async () => fireEvent.click(within(card).getByRole("button", { name: "Try again" })));
  await waitFor(() => expect(agent.requests).toHaveLength(2));
  expect(agent.requests[1].input.messages.map((m) => m.content)).toEqual(["Unsaved?"]);
  // The server may hold nothing of the unsaved turn, so no rewind to it is named (one would be refused).
  expect(agent.requests[1].context?.rewind).toBeUndefined();
  await waitFor(() => expect(answers()).toHaveLength(1));
});

test("a conversation whose oldest turns the server no longer keeps says so at its top, opened or asked in", async () => {
  const agent = mockAgent([askTurn((q) => figure(q))]);
  agent.server.seed({ id: LONG, title: "Long", trimmed: 6, messages: [{ type: "human", id: "h-9", content: "Ninth?" }] });
  renderOpen(agent, LONG);
  const note = await screen.findByText(TRIMMED_NOTE);
  const conversation = screen.getByRole("region", { name: "Conversation" });
  // At the top: before the conversation's first message.
  expect(note.compareDocumentPosition(within(conversation).getByText("Ninth?")) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  await ask("Tenth?");
  await screen.findByRole("article", { name: "Answer" });
  expect(screen.getByText(TRIMMED_NOTE)).toBeInTheDocument();
  cleanup();
  renderChat(mockAgent([askTurn((q) => figure(q))]));
  await ask("Short?");
  await screen.findByRole("article", { name: "Answer" });
  expect(screen.queryByText(TRIMMED_NOTE)).toBeNull();
});

const TABLE = {
  question: "NNA by business line", behaviour: "figure", answer_mode: "certified", answer_text: "Certified: NNA by business line.", receipt: "r",
  display: {
    v: 1,
    headline: { text: "Certified: NNA by business line.", by: "template" },
    table: { caption: "NNA by business line", columns: [{ key: "line", label: "Business line", type: "text" }, { key: "value", label: "NNA", type: "number", currency: "USD" }], rows: [["Wealth", 500], ["Retail", 1200]] },
    chart: null,
  },
};

test("a reply the server kept only the first rows of says First N of M rows on its card's Open and in the panel's Table tab", async () => {
  renderChat(mockAgent([askTurn({ ...TABLE, truncated: { rows_kept: 2, rows_total: 12500 } }), askTurn(TABLE)]));
  await ask("NNA by business line?");
  const card = await screen.findByRole("article", { name: "Answer" });
  const open = within(card).getByRole("button", { name: /^Open/ });
  expect(open).toHaveTextContent("Open · First 2 of 12,500 rows × 2 columns");
  await act(async () => fireEvent.click(open));
  const panel = document.getElementById("cerebro-panel");
  expect(within(panel).getByRole("tab", { name: "Table" })).toHaveAttribute("aria-selected", "true");
  expect(within(panel).getByText("First 2 of 12,500 rows")).toBeInTheDocument();
  // A whole reply says nothing of the kind.
  await ask("Again?");
  await waitFor(() => expect(answers()).toHaveLength(2));
  expect(within(answers()[1]).getByRole("button", { name: /^Open/ })).toHaveTextContent("Open · 2 rows × 2 columns");
});

test("truncatedOf reads the mark on the tool message, in its additional_kwargs or in its content, and nothing else", () => {
  const mark = { rows_kept: 10, rows_total: 40 };
  const tool = (extra) => ({ type: "tool", name: "ask", content: "{}", ...extra });
  expect(truncatedOf(tool({ truncated: mark }))).toEqual(mark);
  expect(truncatedOf(tool({ additional_kwargs: { truncated: mark } }))).toEqual(mark);
  expect(truncatedOf(tool({ content: JSON.stringify({ answer_text: "x", truncated: mark }) }))).toEqual(mark);
  expect(truncatedOf(tool({ content: "not json, truncated" }))).toBeNull();
  expect(truncatedOf(tool({ truncated: { rows_kept: 40, rows_total: 40 } }))).toBeNull();
  expect(truncatedOf(tool({ truncated: { rows_kept: "10", rows_total: 40 } }))).toBeNull();
  expect(truncatedOf({ type: "ai", truncated: mark })).toBeNull();
  expect(truncatedOf(null)).toBeNull();
  expect(sizeOf(null, mark)).toBeNull();
  expect(openLabel({ rows: 10, columns: 1, of: 40 })).toBe("Open · First 10 of 40 rows × 1 column");
  expect(keptRows(1000, 25000)).toBe("First 1,000 of 25,000 rows");
});

/** The page's fetch, recording each call's method and keepalive. */
function recording(agent) {
  const seen = [];
  const fetch = (url, init = {}) => { seen.push({ url, method: init.method || "GET", keepalive: init.keepalive }); return agent.fetch(url, init); };
  return { fetch, seen, deletes: () => seen.filter((c) => c.method === "DELETE") };
}

test("where the server keeps nothing (retention 0), the open conversation is deleted with keepalive as the tab closes; otherwise nothing is", async () => {
  for (const retentionDays of [0, 30]) {
    const agent = mockAgent([askTurn((q) => figure(q))], { retentionDays });
    const { fetch, deletes } = recording(agent);
    render(<ChatView state={state} actions={{ setLastPath: vi.fn() }} fetch={fetch} />);
    await waitFor(() => expect(agent.server.calls).toContain("GET /api/cerebro/conversations?page_size=20"));
    // An empty conversation is nothing to delete.
    await act(async () => window.dispatchEvent(new Event("pagehide")));
    expect(deletes()).toEqual([]);
    await ask("Keep nothing?");
    await screen.findByRole("article", { name: "Answer" });
    const id = agent.requests[0].config.configurable.thread_id;
    await act(async () => window.dispatchEvent(new Event("pagehide")));
    if (retentionDays === 0) {
      expect(deletes()).toEqual([{ url: `/api/cerebro/conversations/${id}`, method: "DELETE", keepalive: true }]);
      await waitFor(() => expect(agent.server.conversations.has(id)).toBe(false));
    } else {
      expect(deletes()).toEqual([]);
      expect(agent.server.conversations.has(id)).toBe(true);
    }
    cleanup();
  }
});

test("the stand-in server titles a conversation by its first question, at most 80 characters, cut at a word", () => {
  expect(titleOf("  Revenue   for June? ")).toBe("Revenue for June?");
  const long = `${"word ".repeat(30)}end`;
  const title = titleOf(long);
  expect(title.length).toBeLessThanOrEqual(80);
  expect(title.endsWith("word")).toBe(true);
  expect(titleOf("x".repeat(100))).toHaveLength(80);
});

test("the stand-in server refuses what the server refuses: two messages, another type, a message with a resume, a rewind to nothing, another person's conversation", async () => {
  const agent = mockAgent([askTurn((q) => figure(q))]);
  const post = (body) => agent.fetch("/api/cerebro/stream", { method: "POST", body: JSON.stringify(body) });
  const config = { configurable: { thread_id: ONE } };
  const human = (content) => ({ type: "human", id: content, content });
  expect((await post({ input: { messages: [human("a")] } })).status).toBe(400);
  expect((await post({ input: { messages: [human("a")] }, config: { configurable: { thread_id: "not-a-uuid" } } })).status).toBe(400);
  expect((await post({ input: { messages: [human("a"), human("b")] }, config })).status).toBe(400);
  expect((await post({ input: { messages: [{ type: "ai", content: "x" }] }, config })).status).toBe(400);
  expect((await post({ input: { messages: [human("a")] }, command: { resume: 1 }, config })).status).toBe(400);
  expect((await post({ input: { messages: [human("a")] }, config, context: { rewind: "nothing" } })).status).toBe(400);
  expect((await post({ input: null, command: { resume: 1 }, config })).status).toBe(404);
  agent.server.seed({ id: THEIRS, owner: "someone else" });
  expect((await post({ input: { messages: [human("a")] }, config: { configurable: { thread_id: THEIRS } } })).status).toBe(404);
  expect((await agent.fetch(`/api/cerebro/conversations/${THEIRS}`)).status).toBe(404);
  expect((await agent.fetch(`/api/cerebro/conversations/${THEIRS}`, { method: "DELETE" })).status).toBe(404);
  expect((await agent.fetch("/api/cerebro/elsewhere")).status).toBe(404);
});

test("a new conversation's id is a version 4 UUID, made from random bytes where the page has no randomUUID (plain HTTP)", () => {
  const uuid = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;
  expect(newConversationId()).toMatch(uuid);
  const real = globalThis.crypto.randomUUID;
  Object.defineProperty(globalThis.crypto, "randomUUID", { value: undefined, configurable: true });
  try {
    const ids = new Set(Array.from({ length: 20 }, () => newConversationId()));
    expect(ids.size).toBe(20);
    for (const id of ids) expect(id).toMatch(uuid);
  } finally {
    Object.defineProperty(globalThis.crypto, "randomUUID", { value: real, configurable: true });
  }
});
