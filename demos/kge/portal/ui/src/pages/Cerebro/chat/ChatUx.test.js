// #607: Cerebro's chat does what Context's Ask page does, by reuse: a clarification's choices ask the restated
// question, "Asked as" shows the question KGE answered where it differs, a get_context reply is a card of its
// definitions and citable sources, the step under way shows while an ask runs, the input offers Context's suggestions,
// each question can be edited and asked again from that point, and the follow-up line says what a follow-up builds on.
import React from "react";
import { expect, test, vi } from "vitest";
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import ChatView, { contextRead, meOf } from "./ChatView";
import { askTurn, contextTurn, mockAgent } from "./chatTestAgent";
import { askedAs, citableSources, definitionsOf, followUpCount, pastQuestions, pendingAsks, sameQuestion } from "./chatAnswer";

const box = () => screen.getByRole("textbox", { name: "Ask the Assistant" });
async function ask(question) {
  fireEvent.change(box(), { target: { value: question } });
  await act(async () => fireEvent.click(screen.getByRole("button", { name: "Send" })));
}
function renderChat(turns, { roles = ["business"], ...props } = {}) {
  const agent = mockAgent(turns);
  render(<ChatView state={{ roles, userId: "reader@example.com" }} actions={{ setLastPath: vi.fn() }} fetch={agent.fetch} {...props} />);
  return agent;
}
// An option's text, as shown: the match is marked inside it.
const option = (text) => screen.getAllByRole("option").find((o) => o.textContent.includes(text));
const humans = (request) => request.input.messages.filter((m) => m.type === "human").map((m) => m.content);
const figure = (question, extra = {}) => ({ question, behaviour: "figure", figure: 42, currency: "USD", answer_text: `Certified: ${question}: USD 42.00.`, answer_mode: "certified", receipt: "r", ...extra });

const CLARIFY = {
  question: "Revenue for June 2025",
  behaviour: "clarify",
  reason: "Revenue has more than one certified meaning here.",
  answer_text: "Which revenue do you mean?",
  answer_mode: "certified",
  history: [{ turn_id: "turn-1" }],
  clarification: { spans: [{ turn: "turn-1", start: 0, end: 7 }], choices: [{ id: "gross", label: "Gross revenue" }, { id: "net", label: "Net revenue" }] },
  receipt: "r",
};

test("a clarification's choices are its own buttons; a click asks the restated question, and they wait while it runs", async () => {
  const agent = renderChat([askTurn(CLARIFY), askTurn((q) => figure(q), { hold: true })]);
  await ask("Revenue for June 2025");
  const card = await screen.findByRole("article", { name: "Answer" });
  const choices = within(card).getByRole("group", { name: "Choose a meaning" });
  expect(within(choices).getAllByRole("button").map((b) => b.textContent)).toEqual(["Gross revenue", "Net revenue"]);
  await act(async () => fireEvent.click(within(choices).getByRole("button", { name: "Net revenue" })));
  await waitFor(() => expect(agent.requests).toHaveLength(2));
  expect(humans(agent.requests[1])).toEqual(["Revenue for June 2025", "Net revenue for June 2025"]);
  const first = screen.getAllByRole("article", { name: "Answer" })[0];
  expect(within(first).getByRole("button", { name: "Gross revenue" })).toBeDisabled();
  await act(async () => agent.release());
  await waitFor(() => expect(screen.getAllByRole("article", { name: "Answer" })).toHaveLength(2));
  // As Context's Ask: an older card's choices stay closed once a later question has been asked.
  expect(within(screen.getAllByRole("article", { name: "Answer" })[0]).getByRole("button", { name: "Gross revenue" })).toBeDisabled();
});

test("only the latest clarification's choices are open: a later clarification closes the earlier one's", async () => {
  renderChat([askTurn(CLARIFY)]);
  await ask("Revenue for June 2025");
  await screen.findByRole("article", { name: "Answer" });
  await ask("Revenue for June 2025");
  await waitFor(() => expect(screen.getAllByRole("article", { name: "Answer" })).toHaveLength(2));
  const [older, latest] = screen.getAllByRole("article", { name: "Answer" });
  expect(within(older).getByRole("button", { name: "Net revenue" })).toBeDisabled();
  expect(within(latest).getByRole("button", { name: "Net revenue" })).toBeEnabled();
});

test("a confirm pause shows no waiting status for the paused ask", async () => {
  const pause = (messages) => {
    const call = { type: "ai", id: "ai-p", content: "", tool_calls: [{ id: "tc-p", name: "ask", args: { question: "Revenue?" } }] };
    return [["values", { messages: [...messages, call], __interrupt__: [{ id: "int-1", value: { kind: "confirm_first" } }] }]];
  };
  renderChat([pause]);
  await ask("Revenue?");
  await screen.findByRole("button", { name: "Confirm" });
  expect(screen.queryByTestId("ask-progress")).not.toBeInTheDocument();
  expect(screen.queryByRole("status")).not.toBeInTheDocument();
});

test("after Stop, the stopped ask shows no spinner on the next question's turn", async () => {
  const agent = renderChat([askTurn((q) => figure(q), { hold: true })]);
  await ask("First?");
  await screen.findByTestId("ask-progress");
  await act(async () => fireEvent.click(screen.getByRole("button", { name: "Stop" })));
  await waitFor(() => expect(screen.queryByTestId("ask-progress")).not.toBeInTheDocument());
  await ask("Second?");
  await waitFor(() => expect(screen.getAllByTestId("ask-progress")).toHaveLength(1));
  await act(async () => agent.release());
  await screen.findByRole("article", { name: "Answer" });
  await waitFor(() => expect(screen.queryByTestId("ask-progress")).not.toBeInTheDocument());
  expect(pendingAsks([{ type: "ai", tool_calls: [{ id: "old", name: "ask" }] }, { type: "human", content: "x" }])).toEqual([]);
});

test("Asked as shows the question KGE answered when it differs from the person's words, and not when it is the same", async () => {
  renderChat([askTurn(figure("Net New Assets for March 2025")), askTurn((q) => figure(`${q.toUpperCase()}  ?`))]);
  await ask("nna march 2025");
  const card = await screen.findByRole("article", { name: "Answer" });
  expect(within(card).getByText("Asked as: Net New Assets for March 2025")).toBeInTheDocument();
  await ask("Revenue for June 2025.");
  await waitFor(() => expect(screen.getAllByRole("article", { name: "Answer" })).toHaveLength(2));
  expect(screen.getAllByText(/^Asked as:/)).toHaveLength(1);
});

test("askedAs: the reply's question, else the ask call's; case, spacing and closing punctuation aside", () => {
  expect(askedAs({ question: "Revenue, June 2025" }, { args: { question: "x" } }, "revenue june")).toBe("Revenue, June 2025");
  expect(askedAs({}, { args: { question: "Revenue for June 2025" } }, "rev june")).toBe("Revenue for June 2025");
  expect(askedAs({ question: "Revenue  for June 2025?" }, null, "revenue for june 2025")).toBeNull();
  expect(askedAs({}, null, "anything")).toBeNull();
  expect(askedAs({ question: "Revenue?" }, null, "")).toBeNull();
  expect(sameQuestion("What is NNA ?!", "what is nna")).toBe(true);
  expect(sameQuestion("What is NNA", "What is NNB")).toBe(false);
});

const PACKAGE = {
  arm: "treatment",
  block: [
    "Question: what is net new assets?",
    "",
    "Terms:",
    "  net new assets: Assets brought in by clients, less assets taken out. Steward: wealth.steward@example.com.",
    "    certified in wealth: nna.amount (decimal; measure; grain month, currency USD, calendar calendar, conversion none; certified by s on 2025-01-01)",
    "  revenue: (no definition recorded)",
    "",
    "Columns by role (measure, filter, period, keys):",
    "  wealth: nna.amount",
  ].join("\n"),
  sources: [
    { n: 1, doc: "nna-policy.pdf", citable: true, title: "NNA policy", tier: "gold", owner: "wealth", version: 3, excerpt: "secret excerpt" },
    { n: 2, doc: "bronze-notes.md", citable: false, title: "Bronze notes", tier: "bronze" },
  ],
  defines: {},
  certified: { wealth: [["measure", "nna.amount"]] },
  units: ["wealth"],
  meta: {},
  decided: { behaviour: "refuse", reason: "decided-internal-reason" },
  withheld: ["withheld-doc.pdf"],
  needed: ["needed-unit-x"],
  in_window: [1],
};

test("a get_context reply is a card of its definitions, stewards and citable sources, and nothing of its decisions", async () => {
  renderChat([contextTurn(PACKAGE)]);
  await ask("What is net new assets?");
  const card = await screen.findByRole("article", { name: "Definitions" });
  expect(within(card).getByText("net new assets")).toBeInTheDocument();
  expect(within(card).getByText("Assets brought in by clients, less assets taken out.")).toBeInTheDocument();
  expect(within(card).getByText("Steward: wealth.steward@example.com")).toBeInTheDocument();
  expect(within(card).getByText("revenue")).toBeInTheDocument();
  expect(within(card).getByText("No definition is recorded.")).toBeInTheDocument();
  const sources = within(card).getByRole("region", { name: "Sources" });
  expect(sources).toHaveTextContent("[1] NNA policy · nna-policy.pdf v3");
  expect(sources).not.toHaveTextContent("bronze-notes.md");
  for (const hidden of ["decided-internal-reason", "withheld-doc.pdf", "needed-unit-x", "nna.amount", "Columns by role", "secret excerpt"]) {
    expect(document.body.textContent).not.toContain(hidden);
  }
  expect(screen.queryByRole("region", { name: /^Tool result/ })).not.toBeInTheDocument();
});

test("an engineer sees the definitions card and the raw context package too", async () => {
  renderChat([contextTurn(PACKAGE)], { roles: ["engineer"] });
  await ask("What is net new assets?");
  expect(await screen.findByRole("article", { name: "Definitions" })).toBeInTheDocument();
  expect(screen.getByRole("region", { name: "Tool result get_context" })).toBeInTheDocument();
});

test("definitionsOf and citableSources read the package as KGE sends it, compact or plan vocabulary", () => {
  expect(definitionsOf({ block: "Terms:\n  churn: Customers lost.\n\nNot glossary terms:\n  x: y" })).toEqual([{ term: "churn", meaning: "Customers lost.", steward: "" }]);
  expect(definitionsOf({ block: JSON.stringify({ contexts: [{ id: "ctx.nna", label: "Net new assets", definition: "In less out." }, { id: "ctx.bare" }, null] }) }))
    .toEqual([{ term: "Net new assets", meaning: "In less out.", steward: "" }, { term: "ctx.bare", meaning: "", steward: "" }]);
  expect(definitionsOf({ block: "Terms:\n  a:b: Two parts. Steward: s.\n" })).toEqual([{ term: "a:b", meaning: "Two parts.", steward: "s" }]);
  expect(definitionsOf({ plan_vocabulary: { contexts: [{ id: "c", label: "Churn", definition: "Lost." }] }, block: "Terms:\n  x: y" })).toEqual([{ term: "Churn", meaning: "Lost.", steward: "" }]);
  expect(definitionsOf({ block: "{not json" })).toEqual([]);
  expect(definitionsOf({ block: "" })).toEqual([]);
  expect(definitionsOf(null)).toEqual([]);
  expect(citableSources({ sources: [{ n: 1, doc: "a", citable: true }, { n: 2, doc: "b", citable: "yes" }, { doc: "", citable: true }, null] }))
    .toEqual([{ n: 1, doc: "a", title: "", version: undefined }]);
  expect(citableSources({})).toEqual([]);
});

test("a context package with nothing to show draws no card", async () => {
  renderChat([contextTurn({ block: "Columns by role:\n  x", sources: [{ n: 1, doc: "b", citable: false }] }), askTurn(figure("Next?"))]);
  await ask("Anything?");
  await waitFor(() => expect(screen.getAllByText("Anything?").length).toBeGreaterThan(0));
  await ask("Next?");
  await screen.findByRole("article", { name: "Answer" });
  expect(screen.queryByRole("article", { name: "Definitions" })).not.toBeInTheDocument();
});

test("while an ask runs, Context's waiting status shows the step KGE reports, once, and goes with the answer", async () => {
  const events = [
    { event: "stage", stage: "understand", state: "running" },
    { event: "stage", stage: "understand", state: "done", ms: 3 },
    { event: "stage", stage: "plan", state: "running" },
  ];
  const agent = renderChat([askTurn(figure("Revenue?"), { events, hold: true })]);
  await ask("Revenue?");
  const status = await screen.findByTestId("ask-progress");
  await waitFor(() => expect(status).toHaveTextContent("planning the query"));
  expect(screen.getAllByRole("status")).toHaveLength(1);
  expect(within(status).getByRole("status")).toHaveTextContent("Working out the answer");
  await act(async () => agent.release());
  await screen.findByRole("article", { name: "Answer" });
  await waitFor(() => expect(screen.queryByTestId("ask-progress")).not.toBeInTheDocument());
});

test("a running pipeline node's label is the step; a graph node's is not", async () => {
  const events = [
    { event: "stage", stage: "resolve", state: "running" },
    { event: "node_added", node: { id: "g1", type: "Term", label: "Graph term", state: "running" } },
    { event: "node_added", node: { id: "n1", kind: "step", label: "Resolving revenue", state: "pending" } },
    { event: "state_changed", id: "n1", state: "running", node: { id: "n1", label: "Resolving revenue" } },
  ];
  const agent = renderChat([askTurn(figure("Revenue?"), { events, hold: true })]);
  await ask("Revenue?");
  const status = await screen.findByTestId("ask-progress");
  await waitFor(() => expect(status).toHaveTextContent("Resolving revenue"));
  expect(status).not.toHaveTextContent("Graph term");
  await act(async () => agent.release());
  await screen.findByRole("article", { name: "Answer" });
});

test("pendingAsks: ask calls with no tool message back yet", () => {
  const call = (id, name = "ask") => ({ type: "ai", tool_calls: [{ id, name }] });
  expect(pendingAsks([call("a"), { type: "tool", tool_call_id: "a" }, call("b"), call("c", "get_context")])).toEqual(["b"]);
  expect(pendingAsks(undefined)).toEqual([]);
});

const GOLDEN = ["Revenue by region for March 2025?", "Revenue for June 2025?", "Headcount by office?"];

test("the input offers Context's suggestions: two, tagged, with the match marked and the top one's rest faint", () => {
  renderChat([askTurn(figure("x"))], { questions: GOLDEN });
  fireEvent.change(box(), { target: { value: "rev" } });
  const list = screen.getByRole("listbox", { name: "Question suggestions" });
  const options = within(list).getAllByRole("option");
  expect(options.map((o) => o.querySelector(".agentchat-suggestion-text").textContent)).toEqual(GOLDEN.slice(0, 2));
  expect(options.map((o) => o.querySelector(".agentchat-suggestion-tag").textContent)).toEqual(["Sample question", "Sample question"]);
  expect(options[0].querySelector("strong.suggestion-match")).toHaveTextContent("Rev");
  expect(options[0]).toHaveAttribute("aria-selected", "true");
  expect(document.querySelector(".agentchat-ghost-rest")).toHaveTextContent("enue by region for March 2025?");
  fireEvent.keyDown(box(), { key: "ArrowDown" });
  expect(options[1]).toHaveAttribute("aria-selected", "true");
  fireEvent.keyDown(box(), { key: "ArrowUp" });
  expect(options[0]).toHaveAttribute("aria-selected", "true");
  fireEvent.keyDown(box(), { key: "ArrowUp" });
  expect(options[1]).toHaveAttribute("aria-selected", "true");
  fireEvent.keyDown(box(), { key: "Tab" });
  expect(box()).toHaveValue("Revenue for June 2025?");
  expect(screen.queryByRole("listbox")).not.toBeInTheDocument();
  fireEvent.change(box(), { target: { value: "head" } });
  expect(screen.getByRole("listbox")).toBeInTheDocument();
  fireEvent.keyDown(box(), { key: "Escape" });
  expect(screen.queryByRole("listbox")).not.toBeInTheDocument();
  fireEvent.keyDown(box(), { key: "Enter" });
  fireEvent.change(box(), { target: { value: "zzz" } });
  expect(screen.queryByRole("listbox")).not.toBeInTheDocument();
});

test("a suggestion clicked is asked; the person's own questions come back as You asked before, and glossary terms as What is", async () => {
  const agent = renderChat([askTurn((q) => figure(q))], { questions: GOLDEN, glossary: [{ term: "Net new assets" }, { term: "Hidden term", units: ["retail"] }] });
  fireEvent.change(box(), { target: { value: "headc" } });
  await act(async () => fireEvent.mouseDown(option("Headcount by office?")));
  await screen.findByRole("article", { name: "Answer" });
  expect(humans(agent.requests[0])).toEqual(["Headcount by office?"]);
  await ask("Margin by desk for Q1?");
  await waitFor(() => expect(screen.getAllByRole("article", { name: "Answer" })).toHaveLength(2));
  fireEvent.change(box(), { target: { value: "margin" } });
  expect(option("Margin by desk for Q1?")).toHaveTextContent("You asked before");
  fireEvent.change(box(), { target: { value: "net new" } });
  expect(option("What is Net new assets?")).toHaveTextContent("Glossary term");
  fireEvent.change(box(), { target: { value: "hidden" } });
  expect(screen.queryByRole("listbox")).not.toBeInTheDocument();
});

test("pastQuestions lists the person's questions once each, in order", () => {
  expect(pastQuestions([{ type: "human", content: "A?" }, { type: "ai", content: "x" }, { type: "human", content: "a?" }, { type: "human", content: [{ type: "text", text: "B?" }] }, { type: "human", content: " " }]))
    .toEqual([{ question: "A?" }, { question: "B?" }]);
});

test("Edit asks a past question again, changed, with the conversation before it alone", async () => {
  const agent = renderChat([askTurn((q) => figure(q), { say: "Done." }), askTurn((q) => figure(q)), askTurn((q) => figure(q), { hold: true })]);
  await ask("First?");
  await screen.findByRole("article", { name: "Answer" });
  await ask("Second?");
  await waitFor(() => expect(screen.getAllByRole("article", { name: "Answer" })).toHaveLength(2));
  fireEvent.click(screen.getByRole("button", { name: "Edit “Second?”" }));
  const edit = screen.getByRole("form", { name: "Edit your question" });
  const field = within(edit).getByRole("textbox", { name: "Your question" });
  expect(field).toHaveValue("Second?");
  fireEvent.change(field, { target: { value: "Second, by region?" } });
  await act(async () => fireEvent.click(within(edit).getByRole("button", { name: "Ask again" })));
  await waitFor(() => expect(agent.requests).toHaveLength(3));
  const sent = agent.requests[2].input.messages;
  expect(humans(agent.requests[2])).toEqual(["First?", "Second, by region?"]);
  expect(sent.map((m) => m.type)).toEqual(["human", "ai", "tool", "ai", "human"]);
  expect(screen.queryByRole("button", { name: /^Edit/ })).not.toBeInTheDocument();
  await act(async () => agent.release());
  await waitFor(() => expect(screen.getAllByRole("button", { name: /^Edit/ })).toHaveLength(2));
  expect(screen.queryByText("Second?")).not.toBeInTheDocument();
});

test("Edit on the first question starts the conversation over from it; Cancel keeps it as it was", async () => {
  const agent = renderChat([askTurn((q) => figure(q))]);
  await ask("First?");
  await screen.findByRole("article", { name: "Answer" });
  fireEvent.click(screen.getByRole("button", { name: "Edit “First?”" }));
  fireEvent.click(within(screen.getByRole("form", { name: "Edit your question" })).getByRole("button", { name: "Cancel" }));
  expect(screen.queryByRole("form", { name: "Edit your question" })).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Edit “First?”" }));
  const edit = screen.getByRole("form", { name: "Edit your question" });
  fireEvent.change(within(edit).getByRole("textbox"), { target: { value: "  " } });
  expect(within(edit).getByRole("button", { name: "Ask again" })).toBeDisabled();
  fireEvent.submit(edit);
  expect(agent.requests).toHaveLength(1);
  fireEvent.change(within(edit).getByRole("textbox"), { target: { value: "First, again?" } });
  await act(async () => fireEvent.submit(edit));
  await waitFor(() => expect(agent.requests).toHaveLength(2));
  expect(agent.requests[1].input.messages.map((m) => [m.type, m.content])).toEqual([["human", "First, again?"]]);
});

test("the follow-up line says what a follow-up builds on, as Context's Ask does, and the eighth says the conversation is full", async () => {
  const history = (n) => Array.from({ length: n }, (_, i) => ({ turn_id: `t${i}` }));
  renderChat([askTurn((q) => figure(q)), askTurn((q) => figure(q, { history: history(2) })), askTurn((q) => figure(q, { history: history(8) }))]);
  await ask("One?");
  await screen.findByRole("article", { name: "Answer" });
  expect(document.querySelector(".cerebro-followup")).not.toBeVisible();
  await ask("Two?");
  await waitFor(() => expect(screen.getByRole("status")).toHaveTextContent("A follow-up builds on the last 2 questions."));
  await ask("Three?");
  await waitFor(() => expect(screen.getByRole("status")).toHaveTextContent("This conversation has reached its eight follow-ups."));
  fireEvent.change(box(), { target: { value: "Four?" } });
  expect(screen.getByRole("button", { name: "Send" })).toBeDisabled();
  fireEvent.submit(box().closest("form"));
  expect(screen.getAllByRole("article", { name: "Answer" })).toHaveLength(3);
  expect(followUpCount([{ type: "tool", name: "ask", content: JSON.stringify(figure("x", { history: history(1) })) }])).toBe(1);
  expect(followUpCount([])).toBe(0);
  // A declined reading is walked past, as the server walks past it when it threads the history.
  expect(followUpCount([
    { type: "tool", name: "ask", content: JSON.stringify(figure("x", { history: history(3) })) },
    { type: "human", content: "and by month?" },
    { type: "tool", name: "ask", content: JSON.stringify({ answer_mode: "ai_generated", answer_text: "Is this what you meant?", receipt: "r", declined: true }) },
  ])).toBe(3);
});

test("contextRead reads only the resolved map, as the person, and turns anything else into an error the tab shows", async () => {
  const fetcher = vi.fn(async () => new Response(JSON.stringify({ contexts: [] }), { status: 200 }));
  expect(await contextRead("/map/revenue", { fetcher })).toEqual({ contexts: [] });
  expect(fetcher).toHaveBeenCalledWith("/api/context/map/revenue", { credentials: "same-origin" });
  expect(await contextRead("/ask", { fetcher })).toEqual({ error: expect.stringMatching(/cannot be read here/) });
  expect(await contextRead("/map/x", { fetcher: async () => new Response("{}", { status: 403 }) })).toEqual({ error: expect.any(String) });
  expect(await contextRead("/map/x", { fetcher: async () => new Response("null", { status: 200 }) })).toEqual({ error: expect.any(String) });
  expect(await contextRead("/map/x", { fetcher: async () => { throw new Error("down"); } })).toEqual({ error: expect.any(String) });
});

test("meOf: the person as Context's components read them", () => {
  expect(meOf({ roles: ["engineer"], userId: "a@example.com", reads: ["retail"] })).toEqual({ id: "a@example.com", cerebro_roles: ["engineer"], reads: ["retail"] });
  expect(meOf(undefined)).toEqual({ id: "", cerebro_roles: [] });
});
