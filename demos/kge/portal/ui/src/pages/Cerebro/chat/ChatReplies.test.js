// #651: each reply's record on the page, on a stand-in for the portal server's Cerebro routes (chatTestServer.js) with a
// mock agent (chatTestAgent.js), synthetic data only. Each reply shows when it was sent and how long it took just above
// it, under the question's Edit (#672), and Copy, Like and Dislike at its foot (B2) as grey icon buttons (#672), saved and
// shown again after a reload; the readout message sits above its
// card while the warehouse runs and after (B3); only a reply marked card: true is an answer card and joins the answers
// list (B4); a failed turn keeps its failure card with Try again after a reload, and a finished turn its steps and
// duration (A4); on a narrow screen "Answers (n)" opens the panel and the input stays (B8).
import React from "react";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { afterEach, expect, test, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import ChatView, { COPIED_MS, replyText, replyTimes, sentText } from "./ChatView";
import { ChatProvider, repliesOf, verdictUrl } from "./chatSession";
import { artifactsOf, isCard } from "./artifacts";
import { askTurn, mockAgent, rawTurn, toolTurn } from "./chatTestAgent";
import { blockOfReply, refusalOf, savedSteps, tookText } from "./working";

afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals(); delete window.matchMedia; });

const figure = (question, extra = {}) => ({
  question, behaviour: "figure", figure: 42, currency: "USD", answer_text: `Certified: ${question}: USD 42.00.`, answer_mode: "certified",
  sql: "SELECT 42", sql_certified: true, receipt: "r-42", ...extra,
});
const box = () => screen.getByRole("textbox", { name: "Ask the Assistant" });
async function ask(question) {
  fireEvent.change(box(), { target: { value: question } });
  await act(async () => fireEvent.click(screen.getByRole("button", { name: "Send" })));
}
const state = { roles: ["business"], userId: "reader@example.com" };
const ID = "00000000-0000-4000-8000-feed00000651";
function renderOpen(agent, id = ID, props = {}) {
  return render(
    <ChatProvider actions={{}} fetch={agent.fetch} openId={id} {...props}>
      <ChatView state={state} actions={{}} />
    </ChatProvider>,
  );
}
const READOUT = "This is net new assets, as reported at each month's freeze. It covers June, by product family.";

test("B2, #672: a turn reads the question, Edit, when it was sent and how long it took, the reply, then Copy, Like and Dislike", async () => {
  const writeText = vi.fn(async () => {});
  vi.stubGlobal("navigator", { ...navigator, clipboard: { writeText } });
  const agent = mockAgent([askTurn((q) => figure(q), { say: "That is the month's figure." })]);
  render(<ChatView state={state} actions={{ setLastPath: vi.fn() }} fetch={agent.fetch} />);
  await ask("NNA for June?");
  const reply = await screen.findByRole("group", { name: "This reply" });
  const meta = document.querySelector(".cerebro-reply-meta");
  expect(meta).toHaveTextContent(/^Sent .+ · Took \d+ s$/);
  expect(meta.querySelector("time")).toHaveAttribute("dateTime", expect.stringMatching(/^\d{4}-\d\d-\d\dT/));
  const question = screen.getByText("NNA for June?", { selector: ".agentchat-bubble" });
  const edit = screen.getByRole("button", { name: "Edit “NNA for June?”" });
  const card = screen.getByRole("article", { name: "Answer" });
  const said = screen.getByText("That is the month's figure.");
  const [copy, like, dislike] = ["Copy this reply", "Like", "Dislike"].map((name) => within(reply).getByRole("button", { name }));
  const order = [question, edit, meta, card, said, copy, like, dislike];
  order.slice(1).forEach((node, i) => expect(order[i].compareDocumentPosition(node) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy());
  // #672: Edit right under the question; the time and duration next, the last of the question's block, so the reply
  // follows it at once.
  expect(question.nextElementSibling).toBe(edit);
  expect(edit.nextElementSibling).toBe(meta);
  expect(meta.nextElementSibling).toBeNull();
  expect(meta.closest(".agentchat-human").nextElementSibling).toContainElement(card);
  await act(async () => fireEvent.click(copy));
  expect(writeText).toHaveBeenCalledWith("NNA for June?: USD 42.00.\n\nThat is the month's figure.");
  expect(within(reply).getByRole("button", { name: "Copied" })).toBeInTheDocument();
  expect(like).toHaveAttribute("aria-pressed", "false");
});

test("#672: Copy, Like and Dislike are icon buttons with their names, no emoji and no words but Copy's tooltip", async () => {
  vi.stubGlobal("navigator", { ...navigator, clipboard: { writeText: vi.fn(async () => {}) } });
  const agent = mockAgent([askTurn((q) => figure(q))]);
  render(<ChatView state={state} actions={{ setLastPath: vi.fn() }} fetch={agent.fetch} sendFeedback={vi.fn(async () => ({}))} />);
  await ask("NNA for June?");
  const reply = await screen.findByRole("group", { name: "This reply" });
  const buttons = within(reply).getAllByRole("button");
  expect(buttons.map((b) => b.getAttribute("aria-label"))).toEqual(["Copy this reply", "Like", "Dislike"]);
  for (const button of buttons) {
    expect(button).toHaveClass("cerebro-icon-btn");
    expect(button.querySelector("svg.cerebro-reply-icon")).toHaveAttribute("aria-hidden", "true");
    expect(button.querySelector("svg.cerebro-reply-icon")).toHaveAttribute("fill", "none");
  }
  expect(reply.textContent).not.toMatch(/\p{Extended_Pictographic}/u);
  const copy = buttons[0];
  const tip = copy.querySelector(".cerebro-tip");
  expect(tip).toHaveTextContent(/^Copy$/);
  expect(tip).toHaveAttribute("aria-hidden", "true");
  expect(buttons[1].textContent).toBe("");
  expect(buttons[2].textContent).toBe("");
  await act(async () => fireEvent.click(copy));
  expect(within(reply).getByRole("button", { name: "Copied" })).toHaveAttribute("data-copied", "true");
  expect(tip).toHaveTextContent(/^Copied$/);
  await act(async () => fireEvent.click(buttons[1]));
  await waitFor(() => expect(buttons[1]).toHaveAttribute("aria-pressed", "true"));
  expect(buttons[1]).toHaveClass("cerebro-icon-btn", "cerebro-verdict");
  expect(buttons[1]).toBeDisabled();
  expect(buttons[2]).toBeDisabled();
});

const read = (name) => readFileSync(fileURLToPath(new URL(name, import.meta.url)), "utf8");
/** chat.css's rules whose selector names one of `names`, each as [selector, declarations]. */
function rulesOf(css, names) {
  const plain = css.replace(/\/\*[\s\S]*?\*\//g, "");
  const out = [];
  for (const m of plain.matchAll(/([^{}]+)\{([^{}]*)\}/g)) {
    const selector = m[1].trim();
    if (names.some((name) => new RegExp(`\\.${name}(?![\\w-])`).test(selector))) out.push([selector, m[2].trim()]);
  }
  return out;
}
test("#672: the reply's time and icons are drawn in the page's grey ink, never the accent or a colour of their own", () => {
  const css = read("./chat.css");
  const rules = rulesOf(css, ["cerebro-reply-actions", "cerebro-icon-btn", "cerebro-verdict", "cerebro-reply-icon", "cerebro-tip", "cerebro-reply-meta"]);
  expect(rules.length).toBeGreaterThan(5);
  for (const [selector, body] of rules) {
    expect(body, selector).not.toMatch(/accent|--v3-link|warn|bad|orange|#[0-9a-f]{3,8}\b|rgb|hsl/i);
    // The tooltip is the page's ink on its paper, as a tooltip is; every other colour is the grey ink.
    if (/\.cerebro-tip$/.test(selector)) continue;
    for (const colour of body.matchAll(/(?:^|;)\s*(?:color|fill|stroke):([^;]+)/g)) {
      expect(colour[1].trim(), selector).toMatch(/^(?:var\(--v3-ink-[23]\)|currentColor|none)$/);
    }
  }
  const declared = (selector) => rules.find(([s]) => s === selector)?.[1] ?? "";
  expect(declared(".cerebro-reply-actions .cerebro-icon-btn")).toMatch(/color:var\(--v3-ink-3\)/);
  expect(declared('.cerebro-reply-actions .cerebro-verdict[aria-pressed="true"]')).toMatch(/color:var\(--v3-ink-2\)/);
  expect(declared('.cerebro-reply-actions .cerebro-verdict[aria-pressed="true"] .cerebro-reply-icon')).toMatch(/fill:currentColor/);
  // The page's other style sheets draw none of these classes (the graph's toolbar has its own .cerebro-icon).
  const others = ["../cerebro.css", "../graph/graph.css", "../curate/curate.css"].map(read).join("\n");
  for (const name of ["cerebro-icon-btn", "cerebro-reply-icon", "cerebro-tip", "cerebro-verdict", "cerebro-reply-actions"]) {
    expect(others).not.toMatch(new RegExp(`\\.${name}(?![\\w-])`));
  }
  // Left-aligned with the reply: the time spans the column, as the reply does, not under the question's right edge.
  expect(declared(".cerebro-reply-meta")).toMatch(/align-self:stretch/);
  expect(declared(".cerebro-reply-meta")).not.toMatch(/text-align/);
});

test("B2: a verdict is saved on the verdict route and shown again after a reload", async () => {
  const sendFeedback = vi.fn(async () => ({}));
  const agent = mockAgent([askTurn((q) => figure(q))]);
  const first = render(<ChatView state={state} actions={{ setLastPath: vi.fn() }} fetch={agent.fetch} sendFeedback={sendFeedback} />);
  await ask("NNA for June?");
  const reply = await screen.findByRole("group", { name: "This reply" });
  await act(async () => fireEvent.click(within(reply).getByRole("button", { name: "Dislike" })));
  await waitFor(() => expect(within(reply).getByRole("button", { name: "Dislike" })).toHaveAttribute("aria-pressed", "true"));
  const id = agent.requests[0].config.configurable.thread_id;
  const human = agent.requests[0].input.messages[0].id;
  expect(agent.server.calls).toContain(`PUT ${verdictUrl(id, human)}`);
  expect(sendFeedback).toHaveBeenCalledWith({ receipt: "r-42", verdict: "not_what_i_meant" });
  first.unmount();
  renderOpen(agent, id);
  const again = await screen.findByRole("group", { name: "This reply" });
  expect(within(again).getByRole("button", { name: "Dislike" })).toHaveAttribute("aria-pressed", "true");
  expect(within(again).getByRole("button", { name: "Like" })).toHaveAttribute("aria-pressed", "false");
});

test("B2: a verdict the server does not save says so and changes nothing", async () => {
  const agent = mockAgent([askTurn((q) => figure(q))]);
  const fetch = (url, init) => (init?.method === "PUT" ? Promise.resolve(new Response(JSON.stringify({ detail: "No such reply." }), { status: 404 })) : agent.fetch(url, init));
  const sendFeedback = vi.fn(async () => ({}));
  render(<ChatView state={state} actions={{ setLastPath: vi.fn() }} fetch={fetch} sendFeedback={sendFeedback} />);
  await ask("NNA for June?");
  const reply = await screen.findByRole("group", { name: "This reply" });
  await act(async () => fireEvent.click(within(reply).getByRole("button", { name: "Like" })));
  expect(await within(reply).findByRole("alert")).toHaveTextContent("Your verdict was not saved.");
  expect(within(reply).getByRole("button", { name: "Like" })).toHaveAttribute("aria-pressed", "false");
  expect(sendFeedback).not.toHaveBeenCalled();
});

test("B3: the readout message shows above the card while the warehouse runs, and stays above it after and after a reload", async () => {
  const agent = mockAgent([askTurn((q) => figure(q), { hold: true, readout: { readout: { measure: "Net new assets" }, message: READOUT } })]);
  const first = render(<ChatView state={state} actions={{ setLastPath: vi.fn() }} fetch={agent.fetch} />);
  await ask("NNA for June?");
  const said = await screen.findByText(READOUT);
  expect(said).toHaveClass("cerebro-readout");
  expect(screen.queryByRole("article", { name: "Answer" })).toBeNull();
  await act(async () => agent.release());
  const card = await screen.findByRole("article", { name: "Answer" });
  expect(screen.getByText(READOUT).compareDocumentPosition(card) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  await screen.findByRole("group", { name: "This reply" });
  const id = agent.requests[0].config.configurable.thread_id;
  expect(Object.values(agent.server.conversations.get(id).replies)[0].readouts).toEqual([{ tool_call_id: "tc-1", message: READOUT }]);
  first.unmount();
  renderOpen(agent, id);
  const card2 = await screen.findByRole("article", { name: "Answer" });
  const again = screen.getByText(READOUT);
  expect(again.compareDocumentPosition(card2) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
});

test("B3: a readout message is React text, never markup", async () => {
  const markup = "<img src=x onerror=alert(1)> is the measure.";
  const agent = mockAgent([askTurn((q) => figure(q), { readout: { message: markup } })]);
  render(<ChatView state={state} actions={{ setLastPath: vi.fn() }} fetch={agent.fetch} />);
  await ask("NNA?");
  expect(await screen.findByText(markup)).toBeInTheDocument();
  expect(document.querySelector(".cerebro-readout img")).toBeNull();
});

test("B4: only a reply marked card: true is an answer card and joins the answers list; card: false is a message only", async () => {
  const agent = mockAgent([askTurn((q) => figure(q), { card: false }), askTurn((q) => figure(q))]);
  render(<ChatView state={state} actions={{ setLastPath: vi.fn() }} fetch={agent.fetch} />);
  await ask("Hello, what can you do?");
  await screen.findByRole("group", { name: "This reply" });
  expect(screen.queryByRole("article", { name: "Answer" })).toBeNull();
  expect(document.querySelector(".cerebro-reply-text").textContent).toBe("Hello, what can you do?");
  expect(document.getElementById("cerebro-panel")).toBeNull();
  await ask("NNA for June?");
  await screen.findByRole("article", { name: "Answer" });
  const list = within(document.getElementById("cerebro-panel")).getByRole("list", { name: "Answers in this chat" });
  expect(within(list).getAllByRole("button").map((b) => b.querySelector(".cerebro-mini-answer-title").textContent)).toEqual(["NNA · June"]);
});

test("B4: isCard and artifactsOf read the tool message's card mark; with none, a reply with figures is a card", () => {
  const tool = (id, reply, card) => ({ type: "tool", id, tool_call_id: `c-${id}`, name: "ask", content: JSON.stringify(reply), ...(card === undefined ? {} : { card }) });
  expect(isCard(tool("a", figure("q"), true))).toBe(true);
  expect(isCard(tool("a", figure("q"), false))).toBe(false);
  expect(isCard(tool("a", figure("q")))).toBe(true);
  expect(isCard(tool("a", { behaviour: "glossary_definition", term: "t", answer_text: "x" }))).toBe(false);
  expect(isCard({ type: "ai", card: true })).toBe(false);
  const messages = [{ type: "human", id: "h", content: "q" }, tool("one", figure("One"), false), tool("two", figure("Two"), true)];
  expect(artifactsOf(messages).map((a) => a.key)).toEqual(["two"]);
});

test("A4: a failed turn keeps its failure card with Try again after a reload, and Try again asks it again in its place", async () => {
  const failure = { case: "warehouse_failed", message: "The warehouse query failed.", action: "try_again" };
  const agent = mockAgent([askTurn((q) => figure(q))]);
  agent.server.seed({
    id: ID, title: "NNA for June?",
    messages: [{ type: "human", id: "h-1", content: "NNA for June?" }],
    replies: { "h-1": { sent: "2026-11-22T10:00:00Z", duration_ms: 7000, outcome: "failed", failure, steps: [{ kind: "step", text: "Planning the query" }], readouts: [] } },
  });
  renderOpen(agent);
  const card = await screen.findByRole("article", { name: "Not answered" });
  expect(within(card).getByRole("alert")).toHaveTextContent("The warehouse query failed.");
  expect(screen.getByText(/^✗ Not answered · 7 s$/)).toBeInTheDocument();
  expect(document.querySelector(".cerebro-reply-meta")).toHaveTextContent(/Took 7 s$/);
  await act(async () => fireEvent.click(within(card).getByRole("button", { name: "Try again" })));
  await waitFor(() => expect(agent.requests).toHaveLength(1));
  // The server holds the question, so Try again rewinds to it.
  expect(agent.requests[0].context).toEqual({ rewind: "h-1" });
  await screen.findByRole("article", { name: "Answer" });
  expect(screen.queryByRole("article", { name: "Not answered" })).toBeNull();
});

test("A4: a finished turn's steps and duration survive a reload: its top says how long it took, its Steps tab lists them", async () => {
  const agent = mockAgent([askTurn((q) => figure(q))]);
  agent.server.seed({
    id: ID, title: "NNA for June?",
    messages: [
      { type: "human", id: "h-1", content: "NNA for June?" },
      { type: "ai", id: "ai-1", content: "", tool_calls: [{ id: "tc-1", name: "ask", args: { question: "NNA for June?" } }] },
      { type: "tool", id: "tool-1", tool_call_id: "tc-1", name: "ask", card: true, content: JSON.stringify(figure("NNA for June?")) },
    ],
    replies: { "h-1": { sent: "2026-11-22T10:00:00Z", duration_ms: 65000, outcome: "answered", steps: [{ kind: "step", text: "Checking the client's terms" }, { kind: "step", text: "Planning the query" }], readouts: [], verdict: "like" } },
  });
  renderOpen(agent);
  const card = await screen.findByRole("article", { name: "Answer" });
  expect(document.querySelector(".cerebro-reply-meta")).toHaveTextContent(/Took 1 min 5 s$/);
  expect(within(screen.getByRole("group", { name: "This reply" })).getByRole("button", { name: "Like" })).toHaveAttribute("aria-pressed", "true");
  // A saved verdict stays locked after a reload.
  expect(within(screen.getByRole("group", { name: "This reply" })).getByRole("button", { name: "Like" })).toBeDisabled();
  expect(within(screen.getByRole("group", { name: "This reply" })).getByRole("button", { name: "Dislike" })).toBeDisabled();
  fireEvent.click(within(card).getByRole("button", { name: /^Open/ }));
  const panel = document.getElementById("cerebro-panel");
  fireEvent.click(within(panel).getByRole("tab", { name: "Steps" }));
  expect(within(panel.querySelector("[role='tabpanel']")).getAllByRole("listitem").map((li) => li.textContent))
    .toEqual(["✓ Checking the client's terms", "✓ Planning the query"]);
});

test("B6, B8: on a narrow screen the panel waits behind Answers (n); it opens the list, and the input stays", async () => {
  window.matchMedia = (query) => ({ matches: true, media: query, addEventListener() {}, removeEventListener() {} });
  const agent = mockAgent([askTurn((q) => figure(q)), toolTurn("search", { hits: [{ title: "Wealth flows", kind: "product" }] }), askTurn((q) => figure(q))]);
  render(<ChatView state={state} actions={{ setLastPath: vi.fn() }} fetch={agent.fetch} />);
  await ask("First?");
  await screen.findByRole("group", { name: "This reply" });
  await ask("Find it");
  await ask("Second?");
  await waitFor(() => expect(screen.getAllByRole("article", { name: "Answer" })).toHaveLength(2));
  expect(document.getElementById("cerebro-panel")).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Answers (2)" }));
  const panel = document.getElementById("cerebro-panel");
  expect(panel).toHaveClass("full");
  const items = within(within(panel).getByRole("list", { name: "Answers in this chat" })).getAllByRole("button");
  expect(items.map((b) => b.querySelector(".cerebro-mini-answer-title").textContent)).toEqual(["Second", "First"]);
  fireEvent.click(items[1]);
  expect(within(document.getElementById("cerebro-panel")).getByRole("heading", { name: "First" })).toBeInTheDocument();
  expect(within(document.getElementById("cerebro-panel")).getAllByRole("tab").map((t) => t.textContent)).toEqual(["SQL", "Full answer", "Sources", "Steps"]);
  expect(box()).toBeInTheDocument();
  fireEvent.keyDown(document, { key: "Escape" });
  expect(document.getElementById("cerebro-panel")).toBeNull();
});

test("the reply helpers, as plain data", () => {
  expect(tookText(6400)).toBe("Took 6 s");
  expect(tookText(125000)).toBe("Took 2 min 5 s");
  expect(tookText(-1)).toBe("");
  expect(refusalOf({ status: 503, detail: " Busy. " })).toEqual({ case: "refused", message: "Busy.", action: "try_again" });
  expect(refusalOf(null).message).toBe("The assistant could not take this question.");
  expect(savedSteps({ steps: [{ text: "Planning the query" }, { text: "F_NNA_FIN" }, "Checking"] })).toEqual(["Planning the query", "Checking"]);
  expect(blockOfReply({ outcome: "answered" })).toBeNull();
  expect(blockOfReply(null)).toBeNull();
  expect(blockOfReply({ sent: "2026-11-22T10:00:00Z", duration_ms: 3000, outcome: "failed", failure: { case: "x", message: "No.", action: "try_again" }, steps: [] }))
    .toMatchObject({ state: "failed", ended: Date.parse("2026-11-22T10:00:03Z"), error: { message: "No." } });
  expect(replyTimes({ sent: "2026-11-22T10:00:00Z", duration_ms: 5 }, null)).toEqual({ sent: Date.parse("2026-11-22T10:00:00Z"), took: 5 });
  expect(replyTimes(undefined, { started: 10, ended: 30, state: "answered" })).toEqual({ sent: 10, took: 20 });
  expect(replyTimes(undefined, { started: 10, ended: null, state: "running" })).toEqual({ sent: 10, took: null });
  expect(repliesOf([1])).toEqual({});
  const messages = [
    { type: "human", id: "h", content: "q" },
    { type: "ai", id: "a", content: "", tool_calls: [{ id: "c", name: "ask" }] },
    { type: "tool", id: "t", tool_call_id: "c", name: "ask", content: JSON.stringify(figure("Q")) },
    { type: "ai", id: "b", content: "Done." },
  ];
  expect(replyText(messages, 0, 3, { c: "Readout." })).toBe("Readout.\n\nQ: USD 42.00.\n\nDone.");
});

// #651: a turn whose agent asked twice in parallel: two ask calls in one message, two answer cards. `tc` names the calls.
const parallelTurn = (tc = ["tc-a", "tc-b"]) => (messages) => {
  const question = messages.at(-1).content;
  const call = { type: "ai", id: "ai-par", content: "", tool_calls: tc.map((id, i) => ({ id, name: "ask", args: { question: `${question} ${i + 1}` } })) };
  const results = tc.map((id, i) => ({ type: "tool", id: `tool-${id}`, tool_call_id: id, name: "ask", card: true, content: JSON.stringify(figure(`Part ${i + 1}`, { receipt: `r-${i + 1}` })) }));
  return [["values", { messages: [...messages, call] }], ["values", { messages: [...messages, call, ...results] }]];
};

test("#651: one Like posts KGE's feedback for every answer card of the reply (parallel asks), and locks both buttons", async () => {
  const sendFeedback = vi.fn(async () => ({}));
  const agent = mockAgent([parallelTurn()]);
  render(<ChatView state={state} actions={{ setLastPath: vi.fn() }} fetch={agent.fetch} sendFeedback={sendFeedback} />);
  await ask("NNA and AUM?");
  await waitFor(() => expect(screen.getAllByRole("article", { name: "Answer" })).toHaveLength(2));
  const reply = await screen.findByRole("group", { name: "This reply" });
  await act(async () => fireEvent.click(within(reply).getByRole("button", { name: "Like" })));
  await waitFor(() => expect(within(reply).getByRole("button", { name: "Like" })).toHaveAttribute("aria-pressed", "true"));
  expect(sendFeedback.mock.calls.map(([body]) => body)).toEqual([{ receipt: "r-1", verdict: "confirmed" }, { receipt: "r-2", verdict: "confirmed" }]);
  expect(within(reply).getByRole("button", { name: "Like" })).toBeDisabled();
  expect(within(reply).getByRole("button", { name: "Dislike" })).toBeDisabled();
  expect(agent.server.verdicts.map((v) => v.verdict)).toEqual(["like"]);
});

test("#651: a reply of an older conversation with a card but no record offers Like and Dislike; the route makes its record", async () => {
  const sendFeedback = vi.fn(async () => ({}));
  const agent = mockAgent([askTurn((q) => figure(q))]);
  agent.server.seed({
    id: ID, title: "NNA for June?",
    messages: [
      { type: "human", id: "h-old", content: "NNA for June?" },
      { type: "ai", id: "ai-1", content: "", tool_calls: [{ id: "tc-1", name: "ask", args: { question: "NNA for June?" } }] },
      { type: "tool", id: "tool-1", tool_call_id: "tc-1", name: "ask", card: true, content: JSON.stringify(figure("NNA for June?")) },
    ],
    replies: {},
  });
  renderOpen(agent, ID, { sendFeedback });
  const reply = await screen.findByRole("group", { name: "This reply" });
  await act(async () => fireEvent.click(within(reply).getByRole("button", { name: "Dislike" })));
  await waitFor(() => expect(within(reply).getByRole("button", { name: "Dislike" })).toHaveAttribute("aria-pressed", "true"));
  expect(agent.server.calls).toContain(`PUT ${verdictUrl(ID, "h-old")}`);
  expect(agent.server.conversations.get(ID).replies["h-old"]).toEqual({ verdict: "dislike" });
  expect(sendFeedback).toHaveBeenCalledWith({ receipt: "r-42", verdict: "not_what_i_meant" });
  expect(within(reply).getByRole("button", { name: "Like" })).toBeDisabled();
});

test("#651: a reply with neither a card nor a record offers no Like or Dislike", async () => {
  const agent = mockAgent([askTurn((q) => figure(q))]);
  agent.server.seed({
    id: ID, title: "Hello",
    messages: [{ type: "human", id: "h-old", content: "Hello" }, { type: "ai", id: "ai-1", content: "Hello to you." }],
    replies: {},
  });
  renderOpen(agent);
  const reply = await screen.findByRole("group", { name: "This reply" });
  expect(within(reply).queryByRole("button", { name: "Like" })).toBeNull();
});

test("#651: Copy says Copied, announced politely, and reads Copy again after COPIED_MS", async () => {
  vi.useFakeTimers({ shouldAdvanceTime: true });
  try {
    vi.stubGlobal("navigator", { ...navigator, clipboard: { writeText: vi.fn(async () => {}) } });
    const agent = mockAgent([askTurn((q) => figure(q))]);
    render(<ChatView state={state} actions={{ setLastPath: vi.fn() }} fetch={agent.fetch} />);
    await ask("NNA for June?");
    const reply = await screen.findByRole("group", { name: "This reply" });
    const region = within(reply).getByRole("status");
    expect(region).toHaveAttribute("aria-live", "polite");
    expect(region).toHaveTextContent("");
    await act(async () => fireEvent.click(within(reply).getByRole("button", { name: "Copy this reply" })));
    expect(within(reply).getByRole("button", { name: "Copied" })).toBeInTheDocument();
    expect(region).toHaveTextContent("Reply copied.");
    expect(reply.querySelector(".cerebro-tip")).toHaveTextContent(/^Copied$/);
    await act(async () => vi.advanceTimersByTime(COPIED_MS + 10));
    expect(within(reply).getByRole("button", { name: "Copy this reply" }).querySelector(".cerebro-tip")).toHaveTextContent(/^Copy$/);
    expect(region).toHaveTextContent("");
  } finally {
    vi.useRealTimers();
  }
});

test("#651: Sent shows the time alone for today's reply and the date too for an earlier one", () => {
  const now = new Date(2026, 10, 22, 15, 0).getTime();
  const today = new Date(2026, 10, 22, 9, 5).getTime();
  const time = new Date(today).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  expect(sentText(today, now)).toBe(`Sent ${time}`);
  const before = new Date(2026, 10, 20, 9, 5).getTime();
  expect(sentText(before, now)).toBe(`Sent ${new Date(before).toLocaleDateString([], { day: "numeric", month: "short" })} ${time}`);
  const lastYear = new Date(2025, 10, 20, 9, 5).getTime();
  expect(sentText(lastYear, now)).toContain("2025");
});

test("#651: the answers list's buttons are named by all they show: the title, the badge and the figure", async () => {
  const agent = mockAgent([askTurn((q) => figure(q))]);
  render(<ChatView state={state} actions={{ setLastPath: vi.fn() }} fetch={agent.fetch} />);
  await ask("NNA for June?");
  await screen.findByRole("article", { name: "Answer" });
  const list = within(document.getElementById("cerebro-panel")).getByRole("list", { name: "Answers in this chat" });
  const [item] = within(list).getAllByRole("button");
  expect(item).not.toHaveAttribute("aria-label");
  expect(within(list).getByRole("button", { name: /NNA · June.*Certified.*USD.*42/ })).toBe(item);
});

test("#651: Esc closes the answers list only from inside it; with a card open, Esc closes the panel from anywhere", async () => {
  const agent = mockAgent([askTurn((q) => figure(q))]);
  render(<ChatView state={state} actions={{ setLastPath: vi.fn() }} fetch={agent.fetch} />);
  await ask("NNA for June?");
  const card = await screen.findByRole("article", { name: "Answer" });
  expect(document.getElementById("cerebro-panel")).not.toBeNull();
  // The list alone, focus in the question box: Esc there leaves it.
  box().focus();
  fireEvent.keyDown(box(), { key: "Escape" });
  expect(document.getElementById("cerebro-panel")).not.toBeNull();
  // Focus inside the list: Esc closes it.
  const item = within(document.getElementById("cerebro-panel")).getAllByRole("button").at(-1);
  item.focus();
  fireEvent.keyDown(item, { key: "Escape" });
  expect(document.getElementById("cerebro-panel")).toBeNull();
  // A card open, focus back in the question box: Esc still closes the panel.
  fireEvent.click(within(card).getByRole("button", { name: /^Open/ }));
  expect(document.getElementById("cerebro-panel")).not.toBeNull();
  box().focus();
  fireEvent.keyDown(box(), { key: "Escape" });
  expect(document.getElementById("cerebro-panel")).toBeNull();
});

// #651: a turn of one ask call `tc`, its readout message `said` (if any) and its reply.
const readoutTurn = (tc, said) => (messages) => {
  const question = messages.at(-1).content;
  const call = { type: "ai", id: `ai-${tc}-${messages.length}`, content: "", tool_calls: [{ id: tc, name: "ask", args: { question } }] };
  const result = { type: "tool", id: `tool-${tc}-${messages.length}`, tool_call_id: tc, name: "ask", card: true, content: JSON.stringify(figure(question)) };
  return [
    ["values", { messages: [...messages, call] }],
    ...(said ? [["custom", { kind: "readout", tool_call_id: tc, readout: {}, message: said }]] : []),
    ["values", { messages: [...messages, call, result] }],
  ];
};

test("#651: stream readouts belong to the open conversation: an ask call id repeated in another conversation shows none of the first's", async () => {
  const agent = mockAgent([readoutTurn("tc-same", READOUT), readoutTurn("tc-same", "")]);
  render(<ChatView state={state} actions={{ setLastPath: vi.fn() }} fetch={agent.fetch} />);
  await ask("NNA for June?");
  await screen.findByRole("group", { name: "This reply" });
  expect(screen.getByText(READOUT)).toBeInTheDocument();
  await act(async () => fireEvent.click(screen.getByRole("button", { name: /New chat/ })));
  await waitFor(() => expect(screen.queryByText(READOUT)).toBeNull());
  await ask("AUM for June?");
  await screen.findByRole("group", { name: "This reply" });
  expect(agent.requests[1].config.configurable.thread_id).not.toBe(agent.requests[0].config.configurable.thread_id);
  expect(screen.queryByText(READOUT)).toBeNull();
  expect(document.querySelector(".cerebro-readout")).toBeNull();
});

test("#651 (A3): a connection lost after the server saved the question but before any values: Try again rewinds to it", async () => {
  vi.spyOn(console, "error").mockImplementation(() => {});
  const agent = mockAgent([rawTurn([]), askTurn((q) => figure(q))]);
  render(<ChatView state={state} actions={{ setLastPath: vi.fn() }} fetch={agent.fetch} />);
  await ask("NNA for June?");
  const card = await screen.findByRole("article", { name: "Not answered" });
  const id = agent.requests[0].config.configurable.thread_id;
  const human = agent.requests[0].input.messages[0].id;
  expect(agent.server.conversations.get(id).messages.map((m) => m.id)).toEqual([human]);
  await act(async () => fireEvent.click(within(card).getByRole("button", { name: "Try again" })));
  await waitFor(() => expect(agent.requests).toHaveLength(2));
  // The conversation was read again, and it holds the question: Try again rewinds to it, so it is kept once.
  expect(agent.server.calls).toContain(`GET /api/cerebro/conversations/${id}`);
  expect(agent.requests[1].context).toEqual({ rewind: human });
  await screen.findByRole("article", { name: "Answer" });
  expect(agent.server.conversations.get(id).messages.filter((m) => m.type === "human").map((m) => m.content)).toEqual(["NNA for June?"]);
  expect(screen.queryByRole("article", { name: "Not answered" })).toBeNull();
});

test("#651 (A3): Try again for an unsent question the server does not hold, or with no conversation yet, sends it afresh", async () => {
  vi.spyOn(console, "error").mockImplementation(() => {});
  const agent = mockAgent([askTurn((q) => figure(q))]);
  let refuse = 1;
  const fetch = async (url, init) => {
    if (url === "/api/cerebro/stream" && refuse > 0) {
      refuse -= 1;
      return new Response(JSON.stringify({ detail: "Busy." }), { status: 503, headers: { "content-type": "application/json" } });
    }
    return agent.fetch(url, init);
  };
  // An earlier question is held, this one never reached the server.
  agent.server.seed({ id: ID, title: "Earlier", messages: [{ type: "human", id: "h-earlier", content: "Earlier?" }, { type: "ai", id: "ai-0", content: "Yes." }], replies: {} });
  render(<ChatProvider actions={{}} fetch={fetch} openId={ID}><ChatView state={state} actions={{}} /></ChatProvider>);
  await screen.findByText("Earlier?");
  await ask("NNA for June?");
  const card = await screen.findByRole("article", { name: "Not answered" });
  await act(async () => fireEvent.click(within(card).getByRole("button", { name: "Try again" })));
  await waitFor(() => expect(agent.requests).toHaveLength(1));
  expect(agent.requests[0].context).toBeUndefined();
  await screen.findByRole("article", { name: "Answer" });
  expect(agent.server.conversations.get(ID).messages.filter((m) => m.type === "human").map((m) => m.content)).toEqual(["Earlier?", "NNA for June?"]);
});
