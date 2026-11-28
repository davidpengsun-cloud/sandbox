// #630 and #621 on the page, with the server's own pieces: the real Cerebro agent (server/src/cerebro/agent.js) with a
// stand-in model and a stubbed KGE, behind the portal's stream handler (route.js), and the run follower (runs.js)
// behind the page's run routes, all in process, under the page's ChatView. A slow question shows the pending card,
// then its answer card; the chat takes a new question meanwhile; Stop cancels the run; each ending shows its card; a
// reload picks the run up on a new server over the same store. #621: "What can I ask about X?" shows its questions as
// choices that ask themselves, and the server's framing get_context shows no card. Synthetic data only.
import React from "react";
import { afterEach, expect, test, vi } from "vitest";
import { act, cleanup, configure, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import ChatView from "./ChatView";
import { ChatProvider } from "./chatSession";
import { cerebroAgent, savedConversation } from "../../../../../server/src/cerebro/agent.js";
import { followRun } from "../../../../../server/src/cerebro/runs.js";
import { openConversations } from "../../../../../server/src/conversations/index.js";
import { memoryStore } from "../../../../../server/src/conversations/memory.js";
import { cerebroStream } from "../../../../../server/src/cerebro/route.js";
import { kgeStub, standIn } from "../../../../../server/test/fixtures/cerebro-stand-in.js";
import { mockAgent } from "./chatTestAgent";

afterEach(cleanup);
// The page asks about a run at most once a second (RUN_POLL_FLOOR_MS), so a run's ending can take a few seconds to show.
configure({ asyncUtilTimeout: 6000 });

const PRINCIPAL = "reader";
const RUN = "feedc0de".repeat(4);
const SLOW = "Revenue by branch for every day of 2025";
const running = { id: RUN, state: "running", poll_after: 0.05, expires_at: 1_900_000_000, events: [], cursor: 0 };
const figure = (question) => ({ question, behaviour: "figure", figure: 42, currency: "USD", answer_text: `Certified: ${question}: USD 42.00.`, answer_mode: "certified", sql: "SELECT 42", sql_certified: true, receipt: "r" });
const json = (body, status = 200) => new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });

/** The page's fetch onto the server's pieces, for a business user, over `store`. */
function server(model, kge, store = memoryStore()) {
  const conversations = openConversations({ store });
  const agent = cerebroAgent({ model, kge, conversations, retryAfterMs: 0 });
  const handler = cerebroStream({ runTurn: (turn) => agent({ ...turn, roles: ["business"], req: { portalIdentity: { principal: PRINCIPAL } } }) });
  const requests = [];
  async function fetch(url, init = {}) {
    const path = String(url).split("?")[0];
    if (path === "/api/cerebro/conversations") return json({ ...(await conversations.list(PRINCIPAL)), retention_days: 30 });
    const run = /^\/api\/cerebro\/conversations\/([^/]+)\/runs\/([^/]+?)(\/stop)?$/.exec(path);
    if (run) {
      requests.push({ run: decodeURIComponent(run[2]), stop: Boolean(run[3]) });
      return json(await followRun({ conversations, kge, req: { portalIdentity: { principal: PRINCIPAL } }, roles: ["business"], id: run[1], messageId: decodeURIComponent(run[2]), stop: Boolean(run[3]) }));
    }
    if (path.startsWith("/api/cerebro/conversations/")) {
      const saved = await savedConversation(conversations, PRINCIPAL, path.split("/").pop());
      return saved ? json(saved) : json({ detail: "No such conversation." }, 404);
    }
    const body = JSON.parse(init.body);
    requests.push(body);
    let controller;
    const stream = new ReadableStream({ start: (c) => (controller = c) });
    const encoder = new TextEncoder();
    const res = { on() {}, setHeader() {}, writeHead() {}, write: (chunk) => controller.enqueue(encoder.encode(chunk)), end: () => controller.close() };
    void handler({ body }, res);
    return new Response(stream, { status: 200, headers: { "content-type": "text/event-stream" } });
  }
  return { fetch, requests, conversations };
}

/** KGE: the slow question goes to the background; question_run gives `ending()` once `done` is set, else running. */
function kge({ ending = () => ({ state: "done", answer: figure(SLOW) }) } = {}) {
  const state = { done: false };
  const stub = kgeStub((name, args) => {
    if (name === "ask") return { value: args.question === SLOW ? { run: { ...running } } : figure(args.question) };
    if (name === "question_run") return { value: state.done ? { ...running, ...ending() } : { ...running } };
    if (name === "stop_question_run") return { value: { ...running, state: "failed", reason: "stopped", error: "Stopped." } };
    return { value: {} };
  });
  return Object.assign(stub, { state });
}
const asksOnce = () => standIn((messages) => {
  const human = messages.findLast((m) => m._getType() === "human");
  return messages.slice(messages.indexOf(human)).some((m) => m._getType() === "tool" && m.name === "ask") ? { say: "unused" } : { call: ["ask", { question: human.content }] };
});
const box = () => screen.getByRole("textbox", { name: "Ask the Assistant" });
async function ask(question) {
  fireEvent.change(box(), { target: { value: question } });
  await act(async () => fireEvent.click(screen.getByRole("button", { name: "Send" })));
}
const show = (fetch, chat) => render(<ChatView state={{ roles: ["business"], userId: PRINCIPAL }} actions={{ setLastPath: vi.fn(), setChat: chat }} fetch={fetch} />);
const pendingCard = () => screen.findByRole("article", { name: "Running in the background" });

async function openTwoPendingRuns(reply) {
  const id = "d58573df-5d77-4f10-9e15-cbef9bd8c221";
  const agent = mockAgent([]);
  const pending = (key, seconds) => ({
    type: "tool", id: key, tool_call_id: key, name: "ask", card: true,
    content: JSON.stringify({ question: key, run: { ...running, id: key, poll_after: seconds } }),
  });
  agent.server.seed({ id, title: "Two pending questions", messages: [
    { type: "human", id: "human-a", content: "First question" }, pending("first", 1),
    { type: "human", id: "human-b", content: "Second question" }, pending("second", 2),
  ] });
  const polls = [], start = Date.now();
  const fetch = async (url, init) => {
    if (!String(url).includes("/runs/")) return agent.fetch(url, init);
    const key = String(url).split("/").pop();
    polls.push([key, Date.now() - start]);
    return reply(key, init.signal);
  };
  await act(async () => render(<ChatProvider actions={{}} openId={id} fetch={fetch}>
    <ChatView state={{ roles: ["business"], userId: PRINCIPAL }} actions={{}} />
  </ChatProvider>));
  expect(screen.getAllByRole("article", { name: "Running in the background" })).toHaveLength(2);
  return polls;
}

test("pending cards follow their own next poll and use each reply's delay", async () => {
  vi.useFakeTimers();
  try {
    const polls = await openTwoPendingRuns((key) => json({ state: "running", poll_after: key === "first" ? 4 : 2 }));
    await act(async () => vi.advanceTimersByTimeAsync(1000));
    expect(polls).toEqual([["first", 1000]]);
    await act(async () => vi.advanceTimersByTimeAsync(1000));
    expect(polls).toEqual([["first", 1000], ["second", 2000]]);
    await act(async () => vi.advanceTimersByTimeAsync(2000));
    expect(polls).toEqual([["first", 1000], ["second", 2000], ["second", 4000]]);
    await act(async () => vi.advanceTimersByTimeAsync(1000));
    expect(polls).toEqual([["first", 1000], ["second", 2000], ["second", 4000], ["first", 5000]]);
  } finally {
    cleanup();
    vi.useRealTimers();
  }
});

test("a slow poll leaves the other card free to check and finish on time", async () => {
  vi.useFakeTimers();
  let finish, firstSignal;
  const held = new Promise((resolve) => { finish = resolve; });
  try {
    const polls = await openTwoPendingRuns((key, signal) => {
      if (key === "first") {
        firstSignal = signal;
        return held;
      }
      return json({ state: "ended", saved: true, message: {
        type: "tool", id: key, tool_call_id: key, name: "ask", card: true, content: JSON.stringify(figure("Second question")),
      } });
    });
    await act(async () => vi.advanceTimersByTimeAsync(1000));
    expect(polls).toEqual([["first", 1000]]);
    await act(async () => vi.advanceTimersByTimeAsync(1000));
    expect(polls).toEqual([["first", 1000], ["second", 2000]]);
    expect(screen.getByRole("article", { name: "Answer" })).toHaveTextContent("42.00");
    expect(firstSignal.aborted).toBe(false);
    cleanup();
    expect(firstSignal.aborted).toBe(true);
    finish(json({ state: "running", poll_after: 1 }));
    await act(async () => vi.advanceTimersByTimeAsync(2000));
    expect(polls).toEqual([["first", 1000], ["second", 2000]]);
  } finally {
    cleanup();
    vi.useRealTimers();
  }
});

test("a slow answer shows the pending card, the chat takes the next question meanwhile, and the answer card takes the pending card's place", async () => {
  const stub = kge();
  const { fetch, requests, conversations } = server(asksOnce(), stub);
  show(fetch);
  await ask(SLOW);
  const card = await pendingCard();
  expect(card).toHaveTextContent(`${SLOW} · running in the background`);
  expect(card).toHaveTextContent(/· \d:\d\d/);
  expect(within(card).getByRole("button", { name: "Stop" })).toBeEnabled();
  expect(screen.queryByRole("article", { name: "Answer" })).toBeNull();
  // The chat is free: the next question goes ahead and is answered while the run goes on.
  await ask("Total retail revenue for May 2025");
  await waitFor(() => expect(screen.getAllByRole("article", { name: "Answer" })).toHaveLength(1));
  expect(screen.getByRole("article", { name: "Running in the background" })).toBeInTheDocument();
  await waitFor(() => expect(requests.filter((r) => r.run && !r.stop).length).toBeGreaterThan(0));

  stub.state.done = true;
  await waitFor(() => expect(screen.queryByRole("article", { name: "Running in the background" })).toBeNull());
  await waitFor(() => expect(screen.getAllByRole("article", { name: "Answer" })).toHaveLength(2));
  expect(screen.getByText(new RegExp(SLOW.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"), "i"), { selector: ".cerebro-compact-label, .cerebro-compact-text, p" })).toBeInTheDocument();
  // The server keeps the answer in the pending card's place; the page stops asking.
  const thread = requests.find((r) => r.config).config.configurable.thread_id;
  const saved = await savedConversation(conversations, PRINCIPAL, thread);
  expect(saved.messages.filter((m) => m.name === "ask").map((m) => JSON.parse(m.content).answer_text)).toEqual([`Certified: ${SLOW}: USD 42.00.`, "Certified: Total retail revenue for May 2025: USD 42.00."]);
  const asked = stub.calls.filter((c) => c.name === "question_run").length;
  await new Promise((resolve) => { setTimeout(resolve, 1500); });
  expect(stub.calls.filter((c) => c.name === "question_run").length).toBe(asked);
});

test("Stop cancels the run: KGE's stop_question_run, and the stopped card", async () => {
  const stub = kge();
  const { fetch } = server(asksOnce(), stub);
  show(fetch);
  await ask(SLOW);
  const card = await pendingCard();
  await act(async () => fireEvent.click(within(card).getByRole("button", { name: "Stop" })));
  const ended = await screen.findByRole("article", { name: "Not answered" });
  expect(ended).toHaveAttribute("data-case", "stopped");
  expect(ended).toHaveTextContent("Stopped.");
  expect(stub.calls.filter((c) => c.name === "stop_question_run").map((c) => c.args)).toEqual([{ run_id: RUN }]);
  expect(screen.queryByRole("article", { name: "Running in the background" })).toBeNull();
});

test("each ending shows its card: the timeout card with KGE's message and the narrowing suggestion; the warehouse error card", async () => {
  const endings = [
    [{ state: "expired", reason: "run_time_limit", error: "This question exceeded its time limit; ask a narrower question." }, "too_long", ["This question exceeded its time limit; ask a narrower question.", "Try a shorter period or fewer groups."]],
    [{ state: "failed", reason: "warehouse_failure", error: "Binder Error: column sales.amt_x not found" }, "warehouse_failed", ["The warehouse query failed."]],
  ];
  for (const [ending, kind, words] of endings) {
    const stub = kge({ ending: () => ending });
    stub.state.done = true;
    const { fetch } = server(asksOnce(), stub);
    show(fetch);
    await ask(SLOW);
    const card = await screen.findByRole("article", { name: "Not answered" });
    expect(card).toHaveAttribute("data-case", kind);
    for (const text of words) expect(card).toHaveTextContent(text);
    expect(card).not.toHaveTextContent("amt_x");
    cleanup();
  }
});

test("a reload picks the run up: the conversation reopened on a new server over the same store shows the pending card and then posts the answer", async () => {
  const store = memoryStore();
  const stub = kge();
  const first = server(asksOnce(), stub, store);
  const setChat = vi.fn();
  show(first.fetch, setChat);
  await ask(SLOW);
  await pendingCard();
  await waitFor(() => expect(setChat).toHaveBeenCalledWith(expect.any(String)));
  const thread = setChat.mock.calls.at(-1)[0];
  cleanup();

  const second = server(asksOnce(), stub, store);
  const actions = { setLastPath: vi.fn(), setChat: vi.fn() };
  render(
    <ChatProvider actions={actions} fetch={second.fetch} openId={thread}>
      <ChatView state={{ roles: ["business"], userId: PRINCIPAL }} actions={actions} />
    </ChatProvider>,
  );
  await pendingCard();
  stub.state.done = true;
  await waitFor(() => expect(screen.getAllByRole("article", { name: "Answer" })).toHaveLength(1));
  expect(screen.queryByRole("article", { name: "Running in the background" })).toBeNull();
  expect(second.requests.some((r) => r.run)).toBe(true);
});

test("#621: \"What can I ask about X?\" shows the showcase questions as choices that ask themselves; the framing get_context shows no card", async () => {
  const showcase = ["What was net new assets in June 2025?", "Net new assets by advisor for Q2 2025", "Total retail revenue by region for May 2025"];
  const stub = kgeStub((name, args) => {
    if (name === "explore_question") return { value: { node: { id: "Term:nna", label: "Net new assets" } } };
    if (name === "get_context") return { value: { block: "Terms:\n  Net new assets: inflows less outflows.\n" } };
    return { value: figure(args.question) };
  }, { golden: { sets: [{ golden20: showcase }] } });
  const model = standIn((messages) => {
    const human = messages.findLast((m) => m._getType() === "human");
    const after = messages.slice(messages.indexOf(human) + 1);
    if (after.some((m) => m._getType() === "tool" && m.name !== "get_context")) return { say: "unused" };
    return /what can i ask/i.test(human.content) ? { call: ["explore_question", { question: human.content }] } : { call: ["ask", { question: human.content }] };
  });
  const { fetch } = server(model, stub);
  show(fetch);
  await ask("What can I ask about net new assets?");
  const choices = await screen.findByRole("region", { name: "Questions you can ask about net new assets" });
  expect(within(choices).getAllByRole("button").map((b) => b.textContent)).toEqual([showcase[0], showcase[1]]);
  await waitFor(() => expect(within(choices).getByRole("button", { name: showcase[1] })).toBeEnabled());
  await act(async () => fireEvent.click(within(choices).getByRole("button", { name: showcase[1] })));
  await waitFor(() => expect(screen.getAllByRole("article", { name: "Answer" })).toHaveLength(1));
  expect([...document.querySelectorAll(".agentchat-bubble")].map((b) => b.textContent)).toEqual(["What can I ask about net new assets?", showcase[1]]);
  // A terse follow-up: the server frames it with get_context, which shows no card of its own.
  await ask("What about May?");
  await waitFor(() => expect(screen.getAllByRole("article", { name: "Answer" })).toHaveLength(2));
  expect(stub.calls.map((c) => c.name)).toEqual(["explore_question", "ask", "get_context", "ask"]);
  expect(document.querySelector(".cerebro-definition, [aria-label='Definitions']")).toBeNull();
});

test("a pending card the server no longer holds ends as no longer running, and is asked about no more", async () => {
  const stub = kge();
  const { fetch: served } = server(asksOnce(), stub);
  let asked = 0;
  const fetch = async (url, init) => {
    if (/\/runs\//.test(String(url))) {
      asked += 1;
      return json({ detail: "No such question in this conversation." }, 404);
    }
    return served(url, init);
  };
  show(fetch);
  await ask(SLOW);
  const card = await screen.findByRole("article", { name: "Not answered" });
  expect(card).toHaveAttribute("data-case", "run_gone");
  expect(card).toHaveTextContent("This question is no longer running: ask it again.");
  expect(screen.queryByRole("article", { name: "Running in the background" })).toBeNull();
  const seen = asked;
  await new Promise((resolve) => { setTimeout(resolve, 1500); });
  expect(asked).toBe(seen);
});
