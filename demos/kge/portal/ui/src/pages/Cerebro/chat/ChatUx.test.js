// #607: Cerebro's chat does what Context's Ask page does, by reuse: "Asked as" shows the question KGE answered where it
// differs, a get_context reply is a card of its definitions and citable sources, the step under way shows while an ask
// runs, the input offers Context's suggestions, each question can be edited and asked again from that point, and the
// follow-up line says what a follow-up builds on.
import React from "react";
import { expect, test, vi } from "vitest";
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import ChatView, { contextRead, meOf } from "./ChatView";
import { askTurn, contextTurn, hitlTurn, mockAgent } from "./chatTestAgent";
import { answeredAs, citableSources, definitionsOf, pastQuestions, pendingAsks, titleOf } from "./chatAnswer";

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
// #625: the conversation a turn ran on, as the server kept it with the new question (the page sends the question alone).
const humans = (messages) => messages.filter((m) => m.type === "human").map((m) => m.content);
const figure = (question, extra = {}) => ({ question, behaviour: "figure", figure: 42, currency: "USD", answer_text: `Certified: ${question}: USD 42.00.`, answer_mode: "certified", receipt: "r", ...extra });

// #624: a clarification is a question back to the person on the HITL card (ChatHitl.test.js), never choice buttons on
// an answer card; a pause on it leaves no spinner.
test("a pause on the person leaves no spinner: its block says it needs the person's choice", async () => {
  renderChat([hitlTurn()]);
  await ask("Revenue?");
  await screen.findByRole("button", { name: "Choose Gross revenue." });
  // The block's live region stays and says the final state; no spinner is left.
  await waitFor(() => expect(screen.getByRole("status")).toHaveTextContent("Paused · needs your choice"));
  expect(document.querySelector(".waiting-status")).toBeNull();
  expect(screen.getByText("Paused · needs your choice")).toBeInTheDocument();
});

test("after Stop, the stopped turn's block says so with no spinner, and the next question has its own block", async () => {
  const agent = renderChat([askTurn((q) => figure(q), { hold: true })]);
  await ask("First?");
  await screen.findByRole("status");
  await act(async () => fireEvent.click(screen.getByRole("button", { name: "Stop" })));
  await waitFor(() => expect(document.querySelector(".waiting-status")).toBeNull());
  expect(screen.getByText(/^Stopped after \d+ s$/)).toBeInTheDocument();
  await ask("Second?");
  await waitFor(() => expect(document.querySelectorAll(".waiting-status")).toHaveLength(1));
  await act(async () => agent.release());
  await screen.findByRole("article", { name: "Answer" });
  await waitFor(() => expect(document.querySelector(".waiting-status")).toBeNull());
  expect(screen.getByText(/^Stopped after \d+ s$/)).toBeInTheDocument();
  expect(pendingAsks([{ type: "ai", tool_calls: [{ id: "old", name: "ask" }] }, { type: "human", content: "x" }])).toEqual([]);
});

test("the card names what it answered, from the full question sent to Ask; no Asked as line, never The figure (#619)", async () => {
  renderChat([askTurn(() => figure("x", { answer_text: "The figure: USD 3,615.55." }))]);
  await ask("What about May?");
  const card = await screen.findByRole("article", { name: "Answer" });
  expect(within(card).getByText("What about May")).toBeInTheDocument();
  expect(within(card).queryByText(/Asked as/)).not.toBeInTheDocument();
  expect(within(card).queryByText(/^The figure/)).not.toBeInTheDocument();
});

test("titleOf and answeredAs: the question's lead dropped, each for, in, at or by phrase a part (#619)", () => {
  expect(titleOf("Total retail revenue for May 2025 for region NW")).toBe("Retail revenue · May 2025 · region NW");
  expect(titleOf("What were assets under advice at 30 June 2025?")).toBe("Assets under advice · 30 June 2025");
  expect(titleOf("What was NNA in June 2025?")).toBe("NNA · June 2025");
  expect(titleOf("Wealth revenue for fiscal quarter FY26-Q1 for GROWTH households")).toBe("Wealth revenue · fiscal quarter FY26-Q1 · GROWTH households");
  expect(titleOf("Total retail revenue for May 2025 by product family")).toBe("Retail revenue · May 2025 · by product family");
  expect(titleOf("  ")).toBe("");
  expect(titleOf(undefined)).toBe("");
  expect(titleOf("Revenue from F_NNA_FIN.NNA_AM")).toBe("", "never an internal name");
  expect(titleOf("provision for credit losses in Q1")).toBe("Provision for credit losses · Q1");
  expect(titleOf("Loans over 90 days past due by region")).toBe("Loans over 90 days past due · by region");
  expect(titleOf("Year over year growth in revenue")).toBe("Year over year growth in revenue");
  expect(titleOf("customers at risk in May 2025")).toBe("Customers at risk · May 2025");
  expect(titleOf("What was current NNA in June 2025?")).toBe("Current NNA · June 2025");
  expect(answeredAs({ question: "Revenue for June 2025" }, { args: { question: "Total retail revenue for May 2025" } })).toBe("Retail revenue · May 2025");
  expect(answeredAs({ question: "Revenue for June 2025" }, { args: {} })).toBe("Revenue · June 2025");
  expect(answeredAs({}, null)).toBe("");
  expect(answeredAs({ question: "Net revenue for June 2025" }, { args: { question: "Net revenue" } })).toBe("Net revenue · June 2025", "a pick KGE restated in full");
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

test("while an ask runs, its block shows one live step and the graph nodes it reaches as its path, not its pipeline steps", async () => {
  const events = [
    { event: "stage", stage: "resolve", state: "running" },
    { event: "node_added", node: { id: "g1", type: "Term", label: "Graph term", state: "running" } },
    ...["A", "B", "C", "D", "E", "F"].map((label) => ({ event: "node_added", node: { id: label, type: "Measure", label } })),
    { event: "node_added", node: { id: "w1", type: "Words", label: "my words", state: "running" } },
    { event: "node_added", node: { id: "n1", kind: "step", label: "Resolving revenue", state: "pending" } },
    { event: "state_changed", id: "n1", state: "running", node: { id: "n1", label: "Resolving revenue" } },
  ];
  const agent = renderChat([askTurn(figure("Revenue?"), { events, hold: true })]);
  await ask("Revenue?");
  const block = await screen.findByRole("region", { name: "Working on your question" });
  await waitFor(() => expect(block).toHaveTextContent("Path: … → A → B → C → D → E → F"));
  expect(block).not.toHaveTextContent(/Resolving revenue|my words|Graph term/);
  expect(screen.getAllByRole("status")).toHaveLength(1);
  await act(async () => agent.release());
  await screen.findByRole("article", { name: "Answer" });
  // #651 (B5): the block was pinned above the input while it ran; once answered it leaves the thread for the Steps tab.
  expect(block.closest(".cerebro-live-status")).not.toBeNull();
  await waitFor(() => expect(screen.queryByRole("region", { name: "Working on your question" })).toBeNull());
  expect(document.querySelector(".waiting-status")).toBeNull();
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
  expect(humans(agent.ran[0])).toEqual(["Headcount by office?"]);
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

test("Edit asks a past question again, changed: the edited question alone, with context.rewind naming the one it replaces (#625)", async () => {
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
  const sent = agent.requests[2];
  expect(sent.input.messages.map((m) => [m.type, m.content])).toEqual([["human", "Second, by region?"]]);
  const second = agent.ran[1].findLast((m) => m.type === "human");
  expect(second.content).toBe("Second?");
  expect(sent.context).toEqual({ rewind: second.id });
  // The server ran it on the conversation before the question it replaces.
  expect(humans(agent.ran[2])).toEqual(["First?", "Second, by region?"]);
  expect(agent.ran[2].map((m) => m.type)).toEqual(["human", "ai", "tool", "ai", "human"]);
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
  expect(agent.requests[1].context).toEqual({ rewind: agent.ran[0][0].id });
  expect(agent.ran[1].map((m) => m.content)).toEqual(["First, again?"]);
});

test("#625: no question limit: the ninth question is sent in the same chat, with no New chat offer and no follow-up line", async () => {
  const history = (n) => Array.from({ length: Math.min(n, 8) }, (_, i) => ({ turn_id: `t${i}` }));
  let n = 0;
  const agent = renderChat([askTurn((q) => figure(q, { history: history((n += 1)) }))]);
  for (let i = 1; i <= 9; i += 1) {
    await ask(`Question ${i}?`);
    await waitFor(() => expect(screen.getAllByRole("article", { name: "Answer" })).toHaveLength(i));
    expect(document.querySelector(".cerebro-new-chat")).toBeNull();
  }
  expect(agent.requests).toHaveLength(9);
  const id = agent.requests[0].config.configurable.thread_id;
  for (const request of agent.requests) {
    expect(request.input.messages).toHaveLength(1);
    expect(request.config.configurable.thread_id).toBe(id);
  }
  expect(agent.requests[8].input.messages[0].content).toBe("Question 9?");
  expect(humans(agent.ran[8])).toHaveLength(9);
  expect(screen.queryByText(/follow-ups|follow-up builds on|eight/)).not.toBeInTheDocument();
  expect(document.querySelector(".cerebro-followup")).toBeNull();
  fireEvent.change(box(), { target: { value: "Ten?" } });
  expect(screen.getByRole("button", { name: "Send" })).toBeEnabled();
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
