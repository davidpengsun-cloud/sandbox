// #622: each turn's working block (the moving step, its seconds, at most five lines, then a final state), the error
// card and its actions, the watchdog, the queue, and the spinner's motion, on the portal server's own stream handler
// with a stand-in agent (chatTestAgent.js).
import React from "react";
import { readFileSync } from "node:fs";
import { afterEach, describe, expect, test, vi } from "vitest";
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import ChatView from "./ChatView";
import { HOLD_HERE, askTurn, hitlTurn, mockAgent, pickTurn, rawTurn, silent } from "./chatTestAgent";
import { MAX_LINES, WATCHDOG_LIMIT_MS, WATCHDOG_SILENCE_MS, plainWords, readoutText } from "./working";

const box = () => screen.getByRole("textbox", { name: "Ask the Assistant" });
const form = () => box().closest("form");
async function ask(question) {
  fireEvent.change(box(), { target: { value: question } });
  await act(async () => fireEvent.click(screen.getByRole("button", { name: "Send" })));
}
function renderChat(turns, { roles = ["business"] } = {}) {
  const agent = mockAgent(turns);
  render(<ChatView state={{ roles, userId: "reader@example.com" }} actions={{ setLastPath: vi.fn() }} fetch={agent.fetch} />);
  return agent;
}
// #625: the conversation a turn ran on, as the server kept it with the new question (the page sends the question alone).
const humans = (messages) => messages.filter((m) => m.type === "human").map((m) => m.content);
const figure = (question, extra = {}) => ({ question, behaviour: "figure", figure: 42, currency: "USD", answer_text: `Certified: ${question}: USD 42.00.`, answer_mode: "certified", receipt: "r", ...extra });
const working = () => screen.findByRole("region", { name: "Working on your question" });
// No spinner, spinning or blinking, anywhere on the page.
const noSpinner = () => expect(document.querySelector(".waiting-status, .is-animated, .is-blinking")).toBeNull();

// A turn of custom events around an ask call: the events, then (unless held) the reply, then the outcome.
function eventsTurn(custom, { reply, outcome = { outcome: "answered", steps: 4 }, hold = false } = {}) {
  return (messages, n) => {
    const call = { type: "ai", id: `ai-${n}`, content: "", tool_calls: [{ id: `tc-${n}`, name: "ask", args: { question: "q" } }] };
    const items = [["values", { messages: [...messages, call] }], ...custom.map((c) => ["custom", { tool_call_id: `tc-${n}`, ...c }])];
    if (hold) items.push(HOLD_HERE);
    if (reply) items.push(["values", { messages: [...messages, call, { type: "tool", id: `t-${n}`, tool_call_id: `tc-${n}`, name: "ask", content: JSON.stringify(reply) }] }]);
    if (outcome) items.push(["custom", { kind: "outcome", ...outcome }]);
    return rawTurn(items)();
  };
}
const raw = (turn) => { turn.raw = true; return turn; };

const STEPS = [
  { kind: "step", step: "understanding", text: "Understanding your question" },
  { kind: "step", step: "context", text: "Checking the client's terms" },
  { kind: "step", step: "understanding", text: "Working out the reply" },
  { kind: "step", step: "planning", text: "Planning the query" },
  { kind: "step", step: "warehouse", text: "Running it on the warehouse" },
];
const READOUT = { kind: "readout", readout: { measure: "Net new assets", mode: "Certified", period: "June 2025", filters: ["region NW", "F_NNA_FIN.NNA_AM", "1,234.5"], grouping: ["product family"], source: "Retail" } };

afterEach(() => { vi.useRealTimers(); vi.restoreAllMocks(); });

describe("the working block", () => {
  test("shows from the first second pinned above the input (#651, B5): the moving step, its seconds, and no status in the input bar", async () => {
    const agent = renderChat([askTurn(figure("Revenue?"), { hold: true })]);
    await ask("Revenue?");
    const block = await working();
    const question = screen.getByText("Revenue?", { selector: ".agentchat-bubble" });
    expect(question.closest(".agentchat-human")).not.toContainElement(block);
    expect(block.closest(".cerebro-live-status")).not.toBeNull();
    expect(block.compareDocumentPosition(form()) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(within(block).getByRole("status")).toHaveTextContent("Understanding your question");
    expect(block.querySelector(".waiting-status-detail")).toHaveTextContent(/^\d+ s$/);
    expect(block.querySelector(".waiting-status")).toHaveClass("is-animated");
    // The input bar shows Stop, never Send and never a second status line.
    expect(within(form()).queryByRole("status")).toBeNull();
    expect(form().querySelector(".waiting-status")).toBeNull();
    expect(within(form()).getByRole("button", { name: "Stop" })).toBeInTheDocument();
    expect(within(form()).queryByRole("button", { name: "Send" })).toBeNull();
    expect(within(form()).queryByRole("button", { name: "Ask next" })).toBeNull();
    expect(screen.getAllByRole("status")).toHaveLength(1);
    await act(async () => agent.release());
    await screen.findByRole("article", { name: "Answer" });
    noSpinner();
  });

  test("steps done roll up: at most five lines, the newest kept; a streamed readout shows before the result, in plain words", async () => {
    const agent = renderChat([raw(eventsTurn([...STEPS, READOUT, { kind: "warehouse", statement: 1, state: "running", elapsed_ms: 1200 }], { hold: true, reply: figure("q") }))]);
    await ask("NNA in June 2025 for region NW by product family?");
    const block = await working();
    await waitFor(() => expect(within(block).getByRole("status")).toHaveTextContent("Running it on the warehouse"));
    const lines = within(block).getAllByRole("listitem").map((li) => li.textContent);
    expect(lines.length).toBeLessThanOrEqual(MAX_LINES);
    expect(lines).toEqual([
      "✓ Checking the client's terms", "✓ Working out the reply", "✓ Planning the query",
      "Computing Net new assets (Certified) · June 2025 · for region NW · by product family · from Retail",
      "Warehouse query: running · 1 s",
    ]);
    expect(block.textContent).not.toMatch(/NNA_AM|F_NNA|1,234|SELECT|tc-1/);
    expect(screen.queryByRole("article", { name: "Answer" })).toBeNull();
    await act(async () => agent.release());
    await screen.findByRole("article", { name: "Answer" });
  });

  test("warehouse events update each statement's state with its time", async () => {
    const agent = renderChat([raw(eventsTurn([
      { kind: "warehouse", statement: 1, state: "queued", elapsed_ms: 0 },
      { kind: "warehouse", statement: 2, state: "queued", elapsed_ms: 0 },
      { kind: "warehouse", statement: 1, state: "running", elapsed_ms: 900 },
      { kind: "warehouse", statement: 1, state: "done", elapsed_ms: 3400 },
      { kind: "warehouse", statement: 2, state: "running", elapsed_ms: 2100 },
    ], { hold: true, reply: figure("q") }))]);
    await ask("Revenue?");
    const block = await working();
    await waitFor(() => expect(block).toHaveTextContent("Warehouse query 2: running · 2 s"));
    expect(block).toHaveTextContent("Warehouse query 1: done · 3 s");
    expect(block).not.toHaveTextContent("waiting");
    await act(async () => agent.release());
  });

  test("with reduce motion on, the block's spinner blinks; once answered no spinner is left", async () => {
    window.matchMedia = vi.fn((query) => ({ matches: query.includes("reduce"), media: query, addEventListener() {}, removeEventListener() {} }));
    try {
      const agent = renderChat([askTurn(figure("Revenue?"), { hold: true })]);
      await ask("Revenue?");
      const block = await working();
      expect(block.querySelector(".waiting-status")).toHaveClass("is-blinking");
      await act(async () => agent.release());
      await screen.findByRole("article", { name: "Answer" });
      noSpinner();
    } finally {
      delete window.matchMedia;
    }
  });
});

describe("every block ends in its final state", () => {
  test("answered (#651, B5): the block leaves the thread; its steps and readout are the answer's Steps tab, its time the reply's top", async () => {
    renderChat([raw(eventsTurn([STEPS[1], STEPS[3], READOUT], { reply: figure("q"), outcome: { outcome: "answered", steps: 4 } }))]);
    await ask("Revenue?");
    const card = await screen.findByRole("article", { name: "Answer" });
    await waitFor(() => expect(screen.queryByRole("region", { name: "Working on your question" })).toBeNull());
    expect(screen.queryByRole("button", { name: /Worked for/ })).toBeNull();
    noSpinner();
    expect(document.querySelector(".cerebro-reply-meta")).toHaveTextContent(/Took \d+ s/);
    fireEvent.click(within(card).getByRole("button", { name: /^Open/ }));
    const panel = document.getElementById("cerebro-panel");
    fireEvent.click(within(panel).getByRole("tab", { name: "Steps" }));
    expect(within(panel.querySelector("[role='tabpanel']")).getAllByRole("listitem").map((li) => li.textContent)).toEqual([
      "✓ Understanding your question", "✓ Checking the client's terms", "✓ Planning the query",
      "Computing Net new assets (Certified) · June 2025 · for region NW · by product family · from Retail",
    ]);
  });

  test("earlier turns keep their time and duration at their top", async () => {
    renderChat([askTurn((q) => figure(q))]);
    await ask("First?");
    await screen.findByRole("article", { name: "Answer" });
    await ask("Second?");
    await waitFor(() => expect(screen.getAllByRole("article", { name: "Answer" })).toHaveLength(2));
    await waitFor(() => expect([...document.querySelectorAll(".cerebro-reply-meta")].filter((m) => /Took \d+ s/.test(m.textContent))).toHaveLength(2));
    noSpinner();
  });

  const CASES = [
    ["not_planned", { message: "Ask answers governed figures and definitions. Use the product page or Search in the catalogue.", action: "rephrase", suggestion: "What does net new assets mean?" }, ["Rephrase", "Ask “What does net new assets mean?”"]],
    ["unsafe_query", { message: "The query couldn't be built safely.", action: "rephrase" }, ["Rephrase"]],
    ["warehouse_failed", { message: "The warehouse query failed.", action: "try_again" }, ["Try again"]],
    ["too_long", { message: "This took too long; try a narrower period or fewer groups.", action: "narrow" }, ["Try again", "Narrow it"]],
    ["kge_unreachable", { message: "Couldn't reach the knowledge graph.", action: "try_again" }, ["Try again"]],
    ["model_failed", { message: "The assistant couldn't respond.", action: "try_again" }, ["Try again"]],
  ];
  test.each(CASES)("failed (%s): ✗ Not answered with the readout kept, then the error card and its action", async (name, error, buttons) => {
    renderChat([raw(eventsTurn([STEPS[3], READOUT], { outcome: { outcome: "failed", steps: 2, error: { case: name, ...error, detail: "raw reason" } } }))]);
    await ask("NNA by product family?");
    const final = await screen.findByRole("button", { name: /^✗ Not answered · \d+ s$/ });
    noSpinner();
    const region = final.closest("section");
    expect(region).toHaveTextContent("Computing Net new assets (Certified) · June 2025");
    const card = screen.getByRole("article", { name: "Not answered" });
    expect(within(card).getByRole("alert")).toHaveTextContent(error.message);
    expect(within(card).getAllByRole("button").map((b) => b.getAttribute("aria-label") || b.textContent)).toEqual(buttons);
    // The run's detail is the engineer's alone.
    expect(card).not.toHaveTextContent("raw reason");
    expect(within(card).queryByRole("button", { name: "Run detail" })).toBeNull();
  });

  test("an engineer can open the run's detail", async () => {
    renderChat([raw(eventsTurn([], { outcome: { outcome: "failed", error: { case: "unsafe_query", message: "The query couldn't be built safely.", action: "rephrase", detail: "gate: no FROM" } } }))], { roles: ["engineer"] });
    await ask("Revenue?");
    const card = await screen.findByRole("article", { name: "Not answered" });
    const detail = within(card).getByRole("button", { name: "Run detail" });
    expect(card).not.toHaveTextContent("gate: no FROM");
    fireEvent.click(detail);
    expect(detail).toHaveAttribute("aria-expanded", "true");
    expect(card).toHaveTextContent("gate: no FROM");
  });

  test("the stream closing with no outcome is a lost connection: Try again resends the same question in its place", async () => {
    const agent = renderChat([silent(askTurn(figure("x"))), askTurn((q) => figure(q))]);
    await ask("Revenue for June 2025?");
    const card = await screen.findByRole("article", { name: "Not answered" });
    expect(within(card).getByRole("alert")).toHaveTextContent("Connection lost");
    expect(screen.getByText(/^✗ Not answered · \d+ s$/)).toBeInTheDocument();
    noSpinner();
    await act(async () => fireEvent.click(within(card).getByRole("button", { name: "Try again" })));
    await waitFor(() => expect(agent.requests).toHaveLength(2));
    expect(humans(agent.ran[1])).toEqual(["Revenue for June 2025?"]);
    await screen.findByRole("article", { name: "Answer" });
    expect(screen.queryByRole("article", { name: "Not answered" })).toBeNull();
  });

  test("#651 (A2): a refusal before the stream shows the server's reason, and Try again sends the question afresh (A3)", async () => {
    const stub = mockAgent([askTurn((q) => figure(q))]);
    let refuse = true;
    const fetch = async (url, init) => {
      if (url === "/api/cerebro/stream" && refuse) {
        refuse = false;
        return new Response(JSON.stringify({ detail: "The assistant is busy; try again in a minute." }), { status: 503, headers: { "content-type": "application/json" } });
      }
      return stub.fetch(url, init);
    };
    vi.spyOn(console, "error").mockImplementation(() => {});
    render(<ChatView state={{ roles: ["business"] }} actions={{}} fetch={fetch} />);
    await ask("Revenue?");
    const card = await screen.findByRole("article", { name: "Not answered" });
    expect(within(card).getByRole("alert")).toHaveTextContent("The assistant is busy; try again in a minute.");
    expect(card).not.toHaveTextContent("Connection lost");
    await act(async () => fireEvent.click(within(card).getByRole("button", { name: "Try again" })));
    await waitFor(() => expect(stub.requests).toHaveLength(1));
    // Sent afresh: no rewind to a question the server never saved, and the server ran it as the conversation's first.
    expect(stub.requests[0].context).toBeUndefined();
    expect(humans(stub.ran[0])).toEqual(["Revenue?"]);
    await screen.findByRole("article", { name: "Answer" });
    expect(screen.queryByRole("article", { name: "Not answered" })).toBeNull();
    expect([...document.querySelectorAll(".agentchat-bubble")].map((b) => b.textContent)).toEqual(["Revenue?"]);
  });

  test.each([[400], [403], [404]])("#651 (A2): a %i refusal with a reason shows the reason", async (status) => {
    const fetch = vi.fn(async (url) => (url === "/api/cerebro/stream"
      ? new Response(JSON.stringify({ detail: `Refused with ${status}.` }), { status, headers: { "content-type": "application/json" } })
      : new Response(JSON.stringify({ conversations: [] }), { status: 200, headers: { "content-type": "application/json" } })));
    vi.spyOn(console, "error").mockImplementation(() => {});
    render(<ChatView state={{ roles: ["business"] }} actions={{}} fetch={fetch} />);
    await ask("Revenue?");
    const card = await screen.findByRole("article", { name: "Not answered" });
    expect(within(card).getByRole("alert")).toHaveTextContent(`Refused with ${status}.`);
  });

  test("a stream that fails outright is a lost connection too, never the fixed sentence", async () => {
    const fetch = vi.fn(async () => new Response("down", { status: 502 }));
    vi.spyOn(console, "error").mockImplementation(() => {});
    render(<ChatView state={{ roles: ["business"] }} actions={{}} fetch={fetch} />);
    await ask("Revenue?");
    const card = await screen.findByRole("article", { name: "Not answered" });
    expect(within(card).getByRole("alert")).toHaveTextContent("Connection lost");
    expect(screen.getAllByRole("alert")).toHaveLength(1);
    expect(document.body.textContent).not.toContain("The assistant could not answer");
    noSpinner();
  });

  test("Rephrase puts the question in the box; a suggestion asks itself", async () => {
    const error = { case: "not_planned", message: "Ask could not plan it.", action: "rephrase", suggestion: "What is NNA?" };
    const agent = renderChat([raw(eventsTurn([], { outcome: { outcome: "failed", error } })), askTurn((q) => figure(q))]);
    await ask("NNA trend?");
    const card = await screen.findByRole("article", { name: "Not answered" });
    fireEvent.click(within(card).getByRole("button", { name: "Rephrase" }));
    expect(box()).toHaveValue("NNA trend?");
    expect(box()).toHaveFocus();
    fireEvent.change(box(), { target: { value: "" } });
    await act(async () => fireEvent.click(within(card).getByRole("button", { name: "Ask “What is NNA?”" })));
    await waitFor(() => expect(agent.requests).toHaveLength(2));
    expect(humans(agent.ran[1]).at(-1)).toBe("What is NNA?");
  });

  test("the watchdog: 20 s of silence says it is still waiting; at the hard limit the turn stops with the timeout card", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const agent = renderChat([askTurn(figure("x"), { hold: true }), askTurn((q) => figure(q))]);
    await ask("Revenue?");
    const block = await working();
    await act(async () => vi.advanceTimersByTime(WATCHDOG_SILENCE_MS - 1000));
    expect(within(block).getByRole("status")).toHaveTextContent("Understanding your question");
    await act(async () => vi.advanceTimersByTime(1500));
    expect(within(block).getByRole("status")).toHaveTextContent("Still waiting for the answer…");
    await act(async () => vi.advanceTimersByTime(WATCHDOG_LIMIT_MS - WATCHDOG_SILENCE_MS));
    const card = await screen.findByRole("article", { name: "Not answered" });
    expect(within(card).getByRole("alert")).toHaveTextContent("This took too long; try a narrower period or fewer groups.");
    expect(screen.getByText(/^✗ Not answered · 1[12]\d s$/)).toBeInTheDocument();
    await waitFor(() => expect(within(form()).queryByRole("button", { name: "Stop" })).toBeNull());
    noSpinner();
    await act(async () => fireEvent.click(within(card).getByRole("button", { name: "Try again" })));
    await waitFor(() => expect(agent.requests).toHaveLength(2));
    expect(humans(agent.ran[1])).toEqual(["Revenue?"]);
    agent.release();
  });

  test("an event ends the silence", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const call = { type: "ai", id: "ai-1", content: "", tool_calls: [{ id: "tc-1", name: "ask", args: { question: "q" } }] };
    const agent = renderChat([raw((messages) => [
      ["values", { messages: [...messages, call] }], HOLD_HERE,
      ["custom", { kind: "step", step: "planning", text: "Planning the query" }], HOLD_HERE,
      ["custom", { kind: "outcome", outcome: "answered", steps: 2 }],
    ])]);
    await ask("Revenue?");
    const block = await working();
    await act(async () => vi.advanceTimersByTime(WATCHDOG_SILENCE_MS + 10));
    expect(within(block).getByRole("status")).toHaveTextContent("Still waiting for the answer…");
    await act(async () => agent.release());
    await waitFor(() => expect(within(block).getByRole("status")).toHaveTextContent("Planning the query"));
    // The limit counts from the last event: the turn is not stopped at 120 s from its start.
    await act(async () => vi.advanceTimersByTime(WATCHDOG_LIMIT_MS - WATCHDOG_SILENCE_MS));
    expect(screen.queryByRole("article", { name: "Not answered" })).toBeNull();
    await act(async () => agent.release());
    await waitFor(() => expect(document.querySelector(".cerebro-reply-meta")).toHaveTextContent(/Took \d+ min \d+ s/));
    noSpinner();
  });
});

test("the watchdog names the warehouse only while the warehouse runs", async () => {
  vi.useFakeTimers({ shouldAdvanceTime: true });
  const agent = renderChat([eventsTurn([STEPS[3], STEPS[4]], { hold: true })]);
  await ask("Revenue?");
  const block = await working();
  await waitFor(() => expect(within(block).getByRole("status")).toHaveTextContent("Running it on the warehouse"));
  await act(async () => vi.advanceTimersByTime(WATCHDOG_SILENCE_MS + 10));
  expect(within(block).getByRole("status")).toHaveTextContent("Still waiting for the warehouse…");
  await act(async () => agent.release());
});

describe("a question sent while a turn runs is queued", () => {
  test("shown as Next, removable, and sent when the turn ends", async () => {
    const agent = renderChat([askTurn((q) => figure(q), { hold: true })]);
    await ask("First?");
    await working();
    // The box takes a question while the turn runs; once typed, Ask next shows beside Stop.
    fireEvent.change(box(), { target: { value: "Dropped?" } });
    expect(within(form()).getAllByRole("button").map((b) => b.textContent).filter((t) => ["Ask next", "Stop", "Send"].includes(t))).toEqual(["Ask next", "Stop"]);
    fireEvent.submit(form());
    fireEvent.change(box(), { target: { value: "Second?" } });
    fireEvent.click(within(form()).getByRole("button", { name: "Ask next" }));
    expect(box()).toHaveValue("");
    const next = screen.getByRole("list", { name: "Questions to ask next" });
    expect(within(next).getAllByRole("listitem").map((li) => li.querySelector(".cerebro-next-text").textContent)).toEqual(["Next: Dropped?", "Next: Second?"]);
    fireEvent.click(within(next).getByRole("button", { name: "Remove “Dropped?”" }));
    expect(box()).toHaveFocus();
    expect(screen.getByRole("list", { name: "Questions to ask next" })).not.toHaveTextContent("Dropped?");
    expect(agent.requests).toHaveLength(1);
    await act(async () => agent.release());
    await waitFor(() => expect(agent.requests).toHaveLength(2));
    expect(humans(agent.ran[1])).toEqual(["First?", "Second?"]);
    expect(screen.queryByRole("list", { name: "Questions to ask next" })).toBeNull();
    await act(async () => agent.release());
    await waitFor(() => expect(screen.getAllByRole("article", { name: "Answer" })).toHaveLength(2));
    expect(agent.requests).toHaveLength(2);
  });

  test("a turn paused on the person keeps the queued question until they choose", async () => {
    const agent = renderChat([hitlTurn({ hold: true }), pickTurn((q) => figure(q)), askTurn((q) => figure(q))]);
    await ask("First?");
    await working();
    fireEvent.change(box(), { target: { value: "Second?" } });
    fireEvent.submit(form());
    await act(async () => agent.release());
    await screen.findByText("Paused · needs your choice");
    await act(async () => new Promise((r) => setTimeout(r, 50)));
    expect(agent.requests).toHaveLength(1);
    expect(screen.getByText("Next: Second?")).toBeInTheDocument();
    await act(async () => fireEvent.click(screen.getByRole("button", { name: "Choose Gross revenue." })));
    await waitFor(() => expect(agent.requests).toHaveLength(3));
    expect(agent.requests[1].command?.resume).toEqual({ pick: "gross" });
    expect(humans(agent.ran[2]).at(-1)).toBe("Second?");
  });

  test("Stop ends the turn and the queued question goes next", async () => {
    const agent = renderChat([askTurn((q) => figure(q), { hold: true }), askTurn((q) => figure(q))]);
    await ask("First?");
    await working();
    fireEvent.change(box(), { target: { value: "Second?" } });
    fireEvent.submit(form());
    expect(screen.getByText("Next: Second?")).toBeInTheDocument();
    await act(async () => fireEvent.click(screen.getByRole("button", { name: "Stop" })));
    await waitFor(() => expect(agent.requests).toHaveLength(2));
    expect(humans(agent.ran[1]).at(-1)).toBe("Second?");
    await screen.findByRole("article", { name: "Answer" });
    expect(screen.getByText(/^Stopped after \d+ s$/)).toBeInTheDocument();
    noSpinner();
  });
});

test("a not-planned turn says why once in each place: Ask's not-sure card and the error card, never the agent's sentence after them", async () => {
  const reason = "Ask answers governed figures and definitions. Use the product page or Search in the catalogue.";
  const notSure = { question: "q", behaviour: "abstain", answer_mode: "not_sure", reason, answer_text: `No answer: ${reason}`, receipt: "r" };
  const turn = (messages, n) => {
    const call = { type: "ai", id: `ai-${n}`, content: "", tool_calls: [{ id: `tc-${n}`, name: "ask", args: { question: "q" } }] };
    const result = { type: "tool", id: `t-${n}`, tool_call_id: `tc-${n}`, name: "ask", content: JSON.stringify(notSure) };
    const said = { type: "ai", id: `ai-${n}-say`, content: "Ask could not plan this; try the product page." };
    return [["values", { messages: [...messages, call, result, said] }],
      ["custom", { kind: "outcome", outcome: "failed", steps: 2, error: { case: "not_planned", message: reason, action: "rephrase" } }]];
  };
  const answered = (messages, n) => askTurn(figure("x"), { say: "One more thing of my own." })(messages, n);
  renderChat([raw(turn), answered]);
  await ask("Which tables hold net new assets?");
  const card = await screen.findByRole("article", { name: "Not answered" });
  expect(within(card).getByRole("alert")).toHaveTextContent(reason);
  // #651 (B4): the not-sure reply has no figures, so it is a message only.
  expect(document.querySelector(".cerebro-reply-text")).toHaveTextContent(reason);
  expect(screen.queryByText("Ask could not plan this; try the product page.")).toBeNull();
  // Another turn's own sentence still shows.
  await ask("Revenue?");
  await screen.findByText("One more thing of my own.");
  expect(screen.queryByText("Ask could not plan this; try the product page.")).toBeNull();
});

describe("#625: no question limit for the queue", () => {
  const history = (n) => Array.from({ length: n }, (_, i) => ({ turn_id: `t${i}` }));
  test("a question queued after an answer whose history holds eight questions goes on in the same chat, never held for a new one", async () => {
    const agent = renderChat([askTurn((q) => figure(q, { history: history(8) }), { hold: true }), askTurn((q) => figure(q))]);
    await ask("First?");
    await working();
    fireEvent.change(box(), { target: { value: "Second?" } });
    fireEvent.submit(form());
    expect(screen.getByText("Next: Second?")).toBeInTheDocument();
    await act(async () => agent.release());
    await waitFor(() => expect(agent.requests).toHaveLength(2));
    expect(screen.queryByText(/in a new chat/)).toBeNull();
    expect(document.querySelector(".cerebro-new-chat")).toBeNull();
    expect(agent.requests[1].input.messages.map((m) => m.content)).toEqual(["Second?"]);
    expect(agent.requests[1].config.configurable.thread_id).toBe(agent.requests[0].config.configurable.thread_id);
    expect(humans(agent.ran[1])).toEqual(["First?", "Second?"]);
    await waitFor(() => expect(screen.getAllByRole("article", { name: "Answer" })).toHaveLength(2));
  });

  test("Try again on an earlier question asks it again at the end of the same chat", async () => {
    const lost = silent(askTurn(figure("x")));
    const agent = renderChat([lost, askTurn((q) => figure(q, { history: history(8) })), askTurn((q) => figure(q))]);
    await ask("First?");
    const card = await screen.findByRole("article", { name: "Not answered" });
    await ask("Second?");
    await waitFor(() => expect(agent.requests).toHaveLength(2));
    await waitFor(() => expect(within(card).getByRole("button", { name: "Try again" })).toBeEnabled());
    await act(async () => fireEvent.click(within(card).getByRole("button", { name: "Try again" })));
    await waitFor(() => expect(agent.requests).toHaveLength(3));
    expect(agent.requests[2].input.messages.map((m) => m.content)).toEqual(["First?"]);
    expect(agent.requests[2].context).toBeUndefined();
    expect(humans(agent.ran[2])).toEqual(["First?", "Second?", "First?"]);
  });

  test("a queued question whose send fails at once does not stall the queue: the next one still goes", async () => {
    vi.spyOn(console, "error").mockImplementation(() => {});
    const agent = mockAgent([askTurn((q) => figure(q), { hold: true }), askTurn((q) => figure(q))]);
    let calls = 0;
    const fetch = async (url, init) => {
      if (url === "/api/cerebro/stream") {
        calls += 1;
        if (calls === 2) throw new TypeError("fetch failed");
      }
      return agent.fetch(url, init);
    };
    render(<ChatView state={{ roles: ["business"], userId: "reader@example.com" }} actions={{ setLastPath: vi.fn() }} fetch={fetch} />);
    await ask("First?");
    await working();
    fireEvent.change(box(), { target: { value: "Second?" } });
    fireEvent.submit(form());
    fireEvent.change(box(), { target: { value: "Third?" } });
    fireEvent.submit(form());
    await act(async () => agent.release());
    await waitFor(() => expect(agent.requests).toHaveLength(2));
    expect(humans(agent.ran[1]).at(-1)).toBe("Third?");
    expect(calls).toBe(3);
  });
});

test("the queued line is in a polite live region, there before anything is queued", async () => {
  const agent = renderChat([askTurn((q) => figure(q), { hold: true }), askTurn((q) => figure(q))]);
  const live = document.querySelector(".cerebro-next-live");
  expect(live).toHaveAttribute("aria-live", "polite");
  await ask("First?");
  await working();
  fireEvent.change(box(), { target: { value: "Second?" } });
  fireEvent.submit(form());
  expect(document.querySelector(".cerebro-next-live")).toBe(live);
  expect(live).toHaveTextContent("Next: Second?");
  await act(async () => agent.release());
  await waitFor(() => expect(agent.requests).toHaveLength(2));
  await act(async () => agent.release());
});

test("#651 (B5): the pinned block's live region announces each step; a stopped turn's final state is announced in the thread", async () => {
  const agent = renderChat([askTurn(figure("x"), { hold: true }), askTurn(figure("x"), { hold: true })]);
  await ask("Revenue?");
  const block = await working();
  const region = within(block).getByRole("status");
  expect(region).toHaveTextContent("Understanding your question");
  await act(async () => agent.release());
  await screen.findByRole("article", { name: "Answer" });
  await waitFor(() => expect(region).not.toBeInTheDocument());
  await ask("Again?");
  const second = await working();
  expect(within(second).getByRole("status")).toHaveTextContent("Understanding your question");
  await act(async () => fireEvent.click(screen.getByRole("button", { name: "Stop" })));
  const final = await screen.findByRole("region", { name: "How this question went" });
  expect(within(final).getByRole("status")).toHaveTextContent(/^Stopped after \d+ s$/);
  expect(final.closest(".agentchat-human")).not.toBeNull();
});

test("the final line that opens is at least 24 px tall", () => {
  const css = readFileSync("src/pages/Cerebro/chat/chat.css", "utf8");
  const rule = css.slice(css.indexOf("button.cerebro-working-final {"));
  expect(rule.slice(0, rule.indexOf("}"))).toMatch(/min-height:(2[4-9]|[3-9]\d)px/);
});

test("plain words: no internal name or figure reaches a line", () => {
  expect(plainWords("Net new assets")).toBe("Net new assets");
  expect(plainWords("June 2025")).toBe("June 2025");
  for (const bad of ["F_NNA_FIN", "wealth.nna", "USD 1,234", "12.5", "40%", "$3", "123456", 7]) expect(plainWords(bad)).toBe("");
  expect(readoutText({ measure: "Revenue", mode: "", period: "", filters: [], grouping: ["region"], source: "" })).toBe("Computing Revenue · by region");
  expect(readoutText(null)).toBe("");
});
