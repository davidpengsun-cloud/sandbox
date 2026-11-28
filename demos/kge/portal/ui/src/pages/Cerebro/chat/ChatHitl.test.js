// #624 on the page, on a stand-in for the portal server's routes with a mock agent (chatTestAgent.js): an answer is
// delivered at once with its label, its figure and its SQL, never held for a Confirm; every question back to the person
// is one HITL card (a clarification, the agent's own question), whose pick resumes the turn and shows in the transcript
// as the person's, never as a restated question; and after a difference answer, the offer to compare the figures.
import React from "react";
import { afterEach, describe, expect, test, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import ChatView from "./ChatView";
import { CHOICES, OFFER_MONTHS, QUESTIONS_HEADING, QUESTION_GROUPS, askTurn, hitlTurn, mockAgent, offerTurn, pickTurn, picksTurn, questionsTurn, rawTurn } from "./chatTestAgent";
import { chooseOf, chosenText, picksOf } from "./chatAnswer";

afterEach(cleanup);

const SQL = "SELECT SUM(amount) FROM flows WHERE month = '2025-03'";
const box = () => screen.getByRole("textbox", { name: "Ask the Assistant" });
async function ask(question) {
  fireEvent.change(box(), { target: { value: question } });
  await act(async () => fireEvent.click(screen.getByRole("button", { name: "Send" })));
}
function renderChat(turns, roles = ["business"]) {
  const agent = mockAgent(turns);
  render(<ChatView state={{ roles, userId: "reader@example.com" }} actions={{ setLastPath: vi.fn() }} fetch={agent.fetch} />);
  return agent;
}
// #625: the conversation a turn ran on, as the server kept it with the new question (the page sends the question alone).
const humans = (messages) => messages.filter((m) => m.type === "human").map((m) => m.content);
const figure = (question, extra = {}) => ({
  question, behaviour: "figure", figure: 42, currency: "USD", answer_text: `Certified: ${question}: USD 42.00.`, answer_mode: "certified",
  sql: SQL, sql_certified: true, receipt: "r", ...extra,
});
const AI_GENERATED = {
  question: "Certified NNA minus current NNA for March 2025",
  behaviour: "figure",
  figure: 12500,
  currency: "USD",
  answer_text: "AI-Generated: The difference between certified and current Net New Assets for March 2025 is USD 12,500.00.",
  answer_mode: "ai_generated",
  sql: SQL,
  sql_certified: false,
  receipt: "stub-receipt-ai-generated.BBBB",
};

test("an AI-Generated answer shows its label and its figure at once, its SQL first when opened, with no confirm step", async () => {
  const agent = renderChat([askTurn(AI_GENERATED)]);
  await ask("Certified NNA minus current NNA for March 2025?");
  const card = await screen.findByRole("article", { name: "Answer" });
  expect(within(card).getByText("AI-Generated")).toHaveAttribute("data-answer-mode", "ai_generated");
  expect(card.querySelector(".figrow")).toHaveTextContent("12,500.00");
  // #626: a figure has no table or chart, so its panel opens on the SQL, headed as #624 heads it.
  fireEvent.click(within(card).getByRole("button", { name: "Open" }));
  const panel = document.getElementById("cerebro-panel");
  expect(within(panel).getByRole("tab", { name: "SQL" })).toHaveAttribute("aria-selected", "true");
  expect(within(panel).getByRole("region", { name: "SQL: not certified (AI-Generated)" })).toHaveTextContent("FROM flows");
  for (const name of ["Confirm", "Decline"]) expect(screen.queryByRole("button", { name })).toBeNull();
  expect(screen.queryByRole("region", { name: "Waiting for you" })).toBeNull();
  expect(agent.requests).toHaveLength(1);
  expect(box()).toBeEnabled();
});

describe("the HITL card: one card for every question back to the person", () => {
  test("a pending clarification replaces the composer, receives keyboard focus, and cancellation resumes safely", async () => {
    const cancelTurn = (messages, n) => {
      const choose = [...messages].reverse().find((m) => m.tool_calls?.some((c) => c.name === "choose"));
      const call = choose.tool_calls.find((c) => c.name === "choose");
      return [["values", { messages: [...messages, { type: "tool", id: `cancel-${n}`, tool_call_id: call.id, name: "choose", content: JSON.stringify({ cancelled: true }) }] }]];
    };
    const agent = renderChat([hitlTurn(), cancelTurn]);
    await ask("Revenue for June 2025");
    const card = await screen.findByRole("region", { name: "Which revenue do you mean?" });
    expect(card.closest(".agentchat-messages")).toBeNull();
    expect(screen.queryByRole("textbox", { name: "Ask the Assistant" })).toBeNull();
    const first = within(card).getByRole("button", { name: "Choose Gross revenue." });
    expect(first).toHaveFocus();
    expect(first).toHaveAccessibleDescription("Which revenue do you mean?");
    expect(within(card).getByRole("group", { name: "Which revenue do you mean?" })).toBeInTheDocument();
    await act(async () => fireEvent.click(within(card).getByRole("button", { name: "Cancel clarification" })));
    await waitFor(() => expect(agent.requests).toHaveLength(2));
    expect(agent.requests[1].command).toEqual({ resume: { cancel: true } });
    await screen.findByText("You: Cancelled");
    expect(box()).toBeInTheDocument();
    expect(screen.queryByRole("article", { name: "Answer" })).toBeNull();
  });

  test("a clarification is its HITL card, a labelled region with its options and Something else, not buttons on an answer card", async () => {
    renderChat([hitlTurn()]);
    await ask("Revenue for June 2025");
    const card = await screen.findByRole("region", { name: "Which revenue do you mean?" });
    const options = within(card).getByRole("group");
    expect(within(options).getAllByRole("button").map((b) => b.textContent)).toEqual(["Choose Gross revenue.", "Choose Net revenue.", "Something else"]);
    for (const b of within(options).getAllByRole("button")) expect(b).toBeEnabled();
    // The clarify reply is no answer card of its own, and nothing offers its choices as answer-card buttons.
    expect(screen.queryByRole("article", { name: "Answer" })).toBeNull();
    expect(screen.queryByRole("group", { name: "Choose a meaning" })).toBeNull();
    expect(screen.getAllByRole("region", { name: "Which revenue do you mean?" })).toHaveLength(1);
  });

  test("a pick resumes the turn with { pick } and the conversation up to the choose call, and the answer card follows", async () => {
    const agent = renderChat([hitlTurn(), pickTurn((q) => figure(q))]);
    await ask("Revenue for June 2025");
    const card = await screen.findByRole("region", { name: "Which revenue do you mean?" });
    await act(async () => fireEvent.click(within(card).getByRole("button", { name: "Choose Net revenue." })));
    await waitFor(() => expect(agent.requests).toHaveLength(2));
    const resume = agent.requests[1];
    expect(resume.command).toEqual({ resume: { pick: "net" } });
    // #625: a resume sends no message; the turn resumes on the conversation the server kept, up to the choose call.
    expect(resume.input).toBeNull();
    expect(resume.config.configurable.thread_id).toBe(agent.requests[0].config.configurable.thread_id);
    const last = agent.ran[1].at(-1);
    expect(last.tool_calls.map((c) => c.name)).toEqual(["choose"]);
    expect(humans(agent.ran[1])).toEqual(["Revenue for June 2025"]);
    const answer = await screen.findByRole("article", { name: "Answer" });
    fireEvent.click(within(answer).getByRole("button", { name: "Open" }));
    expect(within(document.getElementById("cerebro-panel")).getByRole("region", { name: "SQL" })).toBeInTheDocument();
  });

  test("the transcript shows the pick as the person's, on the card with its options inactive, and no restated question", async () => {
    renderChat([hitlTurn(), pickTurn((q) => figure(q))]);
    await ask("Revenue for June 2025");
    await act(async () => fireEvent.click(within(await screen.findByRole("region", { name: "Which revenue do you mean?" })).getByRole("button", { name: "Choose Net revenue." })));
    await screen.findByRole("article", { name: "Answer" });
    const card = screen.getByRole("region", { name: "Which revenue do you mean?" });
    expect(within(card).getByText("You: Net revenue")).toBeInTheDocument();
    expect(within(card).getByRole("button", { name: "Choose Net revenue." })).toHaveAttribute("aria-pressed", "true");
    for (const b of within(card).getAllByRole("button")) expect(b).toBeDisabled();
    expect([...document.querySelectorAll(".agentchat-bubble")].map((b) => b.textContent)).toEqual(["Revenue for June 2025"]);
  });

  test("Something else opens a text field; Send resumes with { other }, the model reads it, and the transcript shows the words as the person's", async () => {
    const agent = renderChat([hitlTurn(), pickTurn(null, { say: "Thanks: I will look at retail revenue." })]);
    await ask("Revenue for June 2025");
    const card = await screen.findByRole("region", { name: "Which revenue do you mean?" });
    expect(within(card).queryByRole("textbox")).toBeNull();
    fireEvent.click(within(card).getByRole("button", { name: "Something else" }));
    const field = within(card).getByRole("textbox", { name: "Your answer" });
    expect(field).toHaveFocus();
    expect(within(card).getByRole("button", { name: "Send your answer" })).toBeDisabled();
    fireEvent.change(field, { target: { value: "Retail revenue" } });
    await act(async () => fireEvent.click(within(card).getByRole("button", { name: "Send your answer" })));
    await waitFor(() => expect(agent.requests).toHaveLength(2));
    expect(agent.requests[1].command).toEqual({ resume: { other: "Retail revenue" } });
    await screen.findByText("Thanks: I will look at retail revenue.");
    expect(within(screen.getByRole("region", { name: "Which revenue do you mean?" })).getByText("You: Retail revenue")).toBeInTheDocument();
    expect([...document.querySelectorAll(".agentchat-bubble")].map((b) => b.textContent)).toEqual(["Revenue for June 2025"]);
  });

  test("a question with no preset choices still allows a typed answer", async () => {
    renderChat([hitlTurn({ options: [] })]);
    await ask("Which measure?");
    const card = await screen.findByRole("region", { name: "Which revenue do you mean?" });
    fireEvent.click(within(card).getByRole("button", { name: "Something else" }));
    expect(within(card).getByRole("textbox", { name: "Your answer" })).toHaveFocus();
  });

  test("a truncated meaning description shows the complete choice, preserving its qualifier and id", async () => {
    const options = [{ id: "cert", label: "Certified NNA (default) — Assets under manag…" }];
    const agent = renderChat([hitlTurn({ source: "meaning", options }), pickTurn(null)]);
    await ask("What is NNA?");
    const card = await screen.findByRole("region", { name: "Which revenue do you mean?" });
    const choice = within(card).getByRole("button", { name: "Choose Certified NNA (default)." });
    expect(card).not.toHaveTextContent("Assets under manag");
    await act(async () => fireEvent.click(choice));
    await waitFor(() => expect(agent.requests[1].command).toEqual({ resume: { pick: "cert" } }));
  });

  test("the agent's own question, its options as strings, is the same card and resumes with the numbered pick", async () => {
    const agent = renderChat([hitlTurn({ source: "agent", question: "Which unit do you mean?" }), pickTurn((q) => figure(q))]);
    await ask("Revenue?");
    const card = await screen.findByRole("region", { name: "Which unit do you mean?" });
    expect(card).toHaveAttribute("data-source", "agent");
    await act(async () => fireEvent.click(within(card).getByRole("button", { name: `Choose ${CHOICES[1].label}.` })));
    await waitFor(() => expect(agent.requests).toHaveLength(2));
    expect(agent.requests[1].command).toEqual({ resume: { pick: "2" } });
    await screen.findByRole("article", { name: "Answer" });
    expect(within(screen.getByRole("region", { name: "Which unit do you mean?" })).getByText(`You: ${CHOICES[1].label}`)).toBeInTheDocument();
  });

  test("a pick on a clarification or the agent's question whose resume fails is cleared, and the card can be answered again", async () => {
    const failed = rawTurn([["custom", { kind: "outcome", outcome: "failed", steps: 1, error: { case: "model_failed", message: "The assistant couldn't respond.", action: "try_again" } }]]);
    const broken = () => { throw new Error("stream broke"); };
    for (const [source, question, again] of [["clarify", "Which revenue do you mean?", { pick: "net" }], ["agent", "Which unit do you mean?", { pick: "2" }]]) {
      for (const failing of [failed, broken]) {
        const agent = renderChat([hitlTurn({ source, question }), failing, pickTurn((q) => figure(q))]);
        await ask("Revenue for June 2025");
        await act(async () => fireEvent.click(within(await screen.findByRole("region", { name: question })).getByRole("button", { name: "Choose Net revenue." })));
        await waitFor(() => expect(agent.requests).toHaveLength(2));
        await waitFor(() => expect(within(screen.getByRole("region", { name: question })).getByRole("button", { name: "Choose Net revenue." })).toBeEnabled());
        expect(screen.getAllByRole("region", { name: question })).toHaveLength(1);
        const card = screen.getByRole("region", { name: question });
        expect(within(card).queryByText(/^You:/)).toBeNull();
        expect(within(card).getByRole("button", { name: "Choose Net revenue." })).toHaveAttribute("aria-pressed", "false");
        expect(within(card).getByRole("button", { name: "Something else" })).toBeEnabled();
        await act(async () => fireEvent.click(within(card).getByRole("button", { name: "Choose Net revenue." })));
        await waitFor(() => expect(agent.requests).toHaveLength(3));
        expect(agent.requests[2].command).toEqual({ resume: again });
        expect(agent.ran[2].at(-1).tool_calls.map((c) => c.name)).toEqual(["choose"]);
        await screen.findByRole("article", { name: "Answer" });
        const answered = screen.getByRole("region", { name: question });
        expect(within(answered).getByText("You: Net revenue")).toBeInTheDocument();
        for (const b of within(answered).getAllByRole("button")) expect(b).toBeDisabled();
        cleanup();
      }
    }
  });

  test("an answer the server did not take says why, on the card", async () => {
    const refused = (messages, n) => {
      const call = [...messages].reverse().find((m) => m.tool_calls?.some((c) => c.name === "choose")).tool_calls[0];
      return [["values", { messages: [...messages, { type: "tool", id: `t-${n}`, tool_call_id: call.id, name: "choose", content: JSON.stringify({ error: "That option is not one of the choices." }) }] }]];
    };
    renderChat([hitlTurn(), refused]);
    await ask("Revenue for June 2025");
    await act(async () => fireEvent.click(within(await screen.findByRole("region", { name: "Which revenue do you mean?" })).getByRole("button", { name: "Choose Net revenue." })));
    const card = screen.getByRole("region", { name: "Which revenue do you mean?" });
    await waitFor(() => expect(within(card).getByRole("alert")).toHaveTextContent("Not taken: That option is not one of the choices."));
  });
});

describe("the offer to compare the figures, after a difference answer", () => {
  const DIFFERENCE = {
    question: "What is the difference between current NNA and certified NNA?",
    behaviour: "cite_certified",
    certified_definitions: [
      { term: "Current NNA", definition: "Net new assets as the books stand now." },
      { term: "Certified NNA", definition: "Net new assets as reported at each month's freeze." },
    ],
    difference: { terms: [
      { term: "current NNA", period_range: { first: "2024-01-01", latest: "2025-06-01" } },
      { term: "certified NNA", period_range: { first: "2025-01-01", latest: "2025-07-01" } },
    ] },
    answer_text: "Certified: Current NNA is as the books stand now; certified NNA is as reported at each month's freeze.",
    answer_mode: "certified",
    receipt: "r",
  };
  const compare = (label) => `Compare current NNA vs. certified NNA for ${label}${label.startsWith("Q") ? " by month" : ""}, with the dollar and percent difference`;
  const offerCard = () => screen.findByRole("region", { name: "Compare the figures?" });

  test("it follows the difference card, its choices from the call, a month picker limited to its months, and no figure; it never blocks the box", async () => {
    renderChat([offerTurn(DIFFERENCE)]);
    await ask(DIFFERENCE.question);
    // #651 (B4): the difference answer has no figures, so it is a message only; the offer follows it.
    const card = await offerCard();
    expect(card).toHaveAttribute("data-source", "offer");
    const options = within(card).getByRole("group");
    expect(within(options).getAllByRole("button").map((b) => b.textContent)).toEqual(["June 2025", "Q2 2025", "No thanks", "Pick a month"]);
    fireEvent.click(within(card).getByRole("button", { name: "Pick a month" }));
    const month = within(card).getByRole("combobox", { name: "Month" });
    expect([...month.options].map((o) => o.value)).toEqual(OFFER_MONTHS);
    expect([...month.options].map((o) => o.textContent)).toEqual(["January 2025", "February 2025", "March 2025", "April 2025", "May 2025", "June 2025"]);
    expect(card.textContent).not.toMatch(/USD|\d[\d,]*\.\d{2}|%/);
    expect(within(card).queryByRole("button", { name: "Something else" })).toBeNull();
    // The offer is no pause: the question box stays open and the turn is over.
    expect(screen.queryByRole("button", { name: "Stop" })).toBeNull();
    fireEvent.change(box(), { target: { value: "Anything" } });
    expect(screen.getByRole("button", { name: "Send" })).toBeEnabled();
  });

  test("a pick sends a resume with { pick } and the conversation ending at the offer, and posts the comparison's card", async () => {
    const agent = renderChat([offerTurn(DIFFERENCE), pickTurn((q) => figure(q), { question: compare })]);
    await ask(DIFFERENCE.question);
    await act(async () => fireEvent.click(within(await offerCard()).getByRole("button", { name: "June 2025" })));
    await waitFor(() => expect(agent.requests).toHaveLength(2));
    const resume = agent.requests[1];
    expect(resume.command).toEqual({ resume: { pick: "latest_month" } });
    expect(resume.input).toBeNull();
    expect(agent.ran[1].at(-1).tool_calls[0]).toMatchObject({ name: "choose", args: { source: "offer" } });
    expect(humans(agent.ran[1])).toEqual([DIFFERENCE.question]);
    // #651 (B4): the comparison's figures are the one answer card; the difference answer is a message only.
    await waitFor(() => expect(screen.getAllByRole("article", { name: "Answer" })).toHaveLength(1));
    const card = screen.getByRole("region", { name: "Compare the figures?" });
    expect(within(card).getByText("You: June 2025")).toBeInTheDocument();
    for (const b of within(card).getAllByRole("button")) expect(b).toBeDisabled();
  });

  test("Pick a month sends { month } for the month chosen", async () => {
    const agent = renderChat([offerTurn(DIFFERENCE), pickTurn((q) => figure(q), { question: compare })]);
    await ask(DIFFERENCE.question);
    const card = await offerCard();
    fireEvent.click(within(card).getByRole("button", { name: "Pick a month" }));
    fireEvent.change(within(card).getByRole("combobox", { name: "Month" }), { target: { value: "2025-03" } });
    await act(async () => fireEvent.click(within(card).getByRole("button", { name: "Ask" })));
    await waitFor(() => expect(agent.requests).toHaveLength(2));
    expect(agent.requests[1].command).toEqual({ resume: { month: "2025-03" } });
    // #651 (B4): the comparison's figures are the one answer card; the difference answer is a message only.
    await waitFor(() => expect(screen.getAllByRole("article", { name: "Answer" })).toHaveLength(1));
    expect(within(screen.getByRole("region", { name: "Compare the figures?" })).getByText("You: March 2025")).toBeInTheDocument();
  });

  test("a pick whose resume fails (the turn ends with no answer to the offer, or the stream errors) is cleared, and the card can be answered again", async () => {
    const failed = rawTurn([["custom", { kind: "outcome", outcome: "failed", steps: 1, error: { case: "model_failed", message: "The assistant couldn't respond.", action: "try_again" } }]]);
    const broken = () => { throw new Error("stream broke"); };
    for (const failing of [failed, broken]) {
      const agent = renderChat([offerTurn(DIFFERENCE), failing, pickTurn((q) => figure(q), { question: compare })]);
      await ask(DIFFERENCE.question);
      await act(async () => fireEvent.click(within(await offerCard()).getByRole("button", { name: "June 2025" })));
      await waitFor(() => expect(agent.requests).toHaveLength(2));
      await waitFor(() => expect(within(screen.getByRole("region", { name: "Compare the figures?" })).getByRole("button", { name: "June 2025" })).toBeEnabled());
      const card = screen.getByRole("region", { name: "Compare the figures?" });
      expect(within(card).queryByText(/^You:/)).toBeNull();
      expect(within(card).getByRole("button", { name: "June 2025" })).toHaveAttribute("aria-pressed", "false");
      await act(async () => fireEvent.click(within(card).getByRole("button", { name: "June 2025" })));
      await waitFor(() => expect(agent.requests).toHaveLength(3));
      expect(agent.requests[2].command).toEqual({ resume: { pick: "latest_month" } });
      // #651 (B4): the comparison's figures are the one answer card; the difference answer is a message only.
    await waitFor(() => expect(screen.getAllByRole("article", { name: "Answer" })).toHaveLength(1));
      expect(within(screen.getByRole("region", { name: "Compare the figures?" })).getByText("You: June 2025")).toBeInTheDocument();
      cleanup();
    }
  });

  test("No thanks sends nothing and marks the card answered", async () => {
    const agent = renderChat([offerTurn(DIFFERENCE)]);
    await ask(DIFFERENCE.question);
    const card = await offerCard();
    await act(async () => fireEvent.click(within(card).getByRole("button", { name: "No thanks" })));
    await act(async () => new Promise((r) => setTimeout(r, 30)));
    expect(agent.requests).toHaveLength(1);
    expect(within(card).getByText("You: No thanks")).toBeInTheDocument();
    for (const b of within(card).getAllByRole("button")) expect(b).toBeDisabled();
  });

  test("a typed question goes ahead as usual and the offer becomes inactive", async () => {
    const agent = renderChat([offerTurn(DIFFERENCE), askTurn((q) => figure(q))]);
    await ask(DIFFERENCE.question);
    await offerCard();
    await ask("NNA for March 2025");
    await waitFor(() => expect(agent.requests).toHaveLength(2));
    expect(agent.requests[1].command).toBeUndefined();
    expect(humans(agent.ran[1])).toEqual([DIFFERENCE.question, "NNA for March 2025"]);
    // #651 (B4): the comparison's figures are the one answer card; the difference answer is a message only.
    await waitFor(() => expect(screen.getAllByRole("article", { name: "Answer" })).toHaveLength(1));
    const card = screen.getByRole("region", { name: "Compare the figures?" });
    for (const b of within(card).getAllByRole("button")) expect(b).toBeDisabled();
    expect(within(card).queryByText(/^You:/)).toBeNull();
  });
});

// #713 (#707 requirement 12): the multiple-question card, on hand-made sample groups (chatTestAgent.js questionsTurn):
// one card, a labelled group per open question, nothing preselected; a pick selects; Send waits for a pick in every group
// shown and sends them all; definitions hide the figures-only groups; an option valid for one
// meaning shows once it is picked; the answered card shows every pick.
describe("the multiple-question card", () => {
  const card = () => screen.findByRole("region", { name: QUESTIONS_HEADING });
  const group = (region, name) => within(region).getByRole("group", { name });
  const labels = (region, name) => within(group(region, name)).getAllByRole("button").map((b) => b.textContent);
  const pressed = (region) => within(region).queryAllByRole("button", { pressed: true }).map((b) => b.textContent);
  const send = (region) => within(region).getByRole("button", { name: "Send your choices" });
  const pick = (region, groupName, option) => fireEvent.click(within(group(region, groupName)).getByRole("button", { name: option }));

  test("one card with a labelled group per open question, one line per option, the suggested option marked but nothing selected, and Send disabled", async () => {
    const agent = renderChat([questionsTurn()]);
    await ask("What was the retail NNA by segment?");
    const region = await card();
    expect(region).toHaveAttribute("data-source", "questions");
    expect(screen.queryByRole("textbox", { name: "Ask the Assistant" })).toBeNull();
    expect(within(region).getAllByRole("group").map((g) => document.getElementById(g.getAttribute("aria-labelledby")).textContent)).toEqual(["Meaning", "Show", "Period", "Segment"]);
    expect(labels(region, "Meaning")).toEqual(["Certified NNA (default)", "NNA (current)", "Branch-sales NNA"]);
    expect(labels(region, "Show")).toEqual(["Definitions", "Figures", "Both"]);
    expect(labels(region, "Segment"), "Branch region waits for its meaning").toEqual(["Client segment"]);
    expect(pressed(region)).toEqual([]);
    expect(send(region)).toBeDisabled();
    expect(send(region)).toHaveTextContent("Send");
    expect(within(region).getByRole("button", { name: "Something else" })).toBeEnabled();
    expect(within(region).getByRole("button", { name: "Certified NNA (default)" })).toHaveFocus();
    expect(agent.requests).toHaveLength(1);
  });

  test("a pick selects and can be changed, sends nothing, and Send waits for every group, then sends every pick at once", async () => {
    const agent = renderChat([questionsTurn(), picksTurn((q) => figure(q), { questions: () => ["What was the certified NNA for June 2025 by client segment?"] })]);
    await ask("What was the retail NNA by segment?");
    const region = await card();
    pick(region, "Meaning", "NNA (current)");
    pick(region, "Meaning", "Certified NNA (default)");
    expect(pressed(region)).toEqual(["Certified NNA (default)"]);
    pick(region, "Show", "Figures");
    pick(region, "Period", "June 2025");
    expect(send(region)).toBeDisabled();
    pick(region, "Segment", "Client segment");
    expect(send(region)).toBeEnabled();
    expect(agent.requests).toHaveLength(1);
    await act(async () => fireEvent.click(send(region)));
    await waitFor(() => expect(agent.requests).toHaveLength(2));
    expect(agent.requests[1].command).toEqual({ resume: { picks: { meaning: "certified-nna", show: "figures", period: "2025-06", segment: "client-segment" } } });
    expect(agent.requests[1].input).toBeNull();
    await screen.findByRole("article", { name: "Answer" });
    const answered = screen.getByRole("region", { name: QUESTIONS_HEADING });
    expect(within(answered).getByText("You: Meaning: Certified NNA (default); Show: Figures; Period: June 2025; Segment: Client segment")).toBeInTheDocument();
    expect(pressed(answered)).toEqual(["Certified NNA (default)", "Figures", "June 2025", "Client segment"]);
    for (const b of within(answered).getAllByRole("button")) expect(b).toBeDisabled();
    expect(within(answered).queryByRole("button", { name: "Send your choices" })).toBeNull();
    expect([...document.querySelectorAll(".agentchat-bubble")].map((b) => b.textContent)).toEqual(["What was the retail NNA by segment?"]);
  });

  test("an option valid for one meaning only shows once that meaning is picked, and its pick goes when the meaning changes", async () => {
    renderChat([questionsTurn()]);
    await ask("What was the retail NNA by segment?");
    const region = await card();
    expect(within(region).queryByRole("button", { name: "Branch region" })).toBeNull();
    pick(region, "Meaning", "Branch-sales NNA");
    expect(labels(region, "Segment")).toEqual(["Client segment", "Branch region"]);
    pick(region, "Segment", "Branch region");
    pick(region, "Show", "Figures");
    pick(region, "Period", "May 2025");
    expect(send(region)).toBeEnabled();
    pick(region, "Meaning", "NNA (current)");
    expect(within(region).queryByRole("button", { name: "Branch region" })).toBeNull();
    expect(pressed(region)).toEqual(["NNA (current)", "Figures", "May 2025"]);
    expect(send(region)).toBeDisabled();
  });

  test("a Show pick of definitions hides the Period and grouping groups, which need no pick, and their picks are not sent", async () => {
    const agent = renderChat([questionsTurn(), picksTurn((q) => figure(q))]);
    await ask("Show me NNA");
    const region = await card();
    pick(region, "Show", "Figures");
    pick(region, "Period", "June 2025");
    pick(region, "Show", "Definitions");
    expect(within(region).queryByRole("group", { name: "Period" })).toBeNull();
    expect(within(region).queryByRole("group", { name: "Segment" })).toBeNull();
    pick(region, "Meaning", "NNA (current)");
    expect(send(region)).toBeEnabled();
    await act(async () => fireEvent.click(send(region)));
    await waitFor(() => expect(agent.requests).toHaveLength(2));
    expect(agent.requests[1].command.resume.picks).toEqual({ meaning: "net-new-assets", show: "definitions" });
  });

  test("with the Show group last, a Show pick of definitions still hides the Period and grouping groups above it", async () => {
    const [meaning, show, period, segment] = QUESTION_GROUPS;
    const agent = renderChat([questionsTurn({ groups: [meaning, period, segment, show] }), picksTurn((q) => figure(q))]);
    await ask("Show me NNA");
    const region = await card();
    expect(within(region).getAllByRole("group").map((g) => document.getElementById(g.getAttribute("aria-labelledby")).textContent)).toEqual(["Meaning", "Period", "Segment", "Show"]);
    pick(region, "Period", "June 2025");
    pick(region, "Show", "Definitions");
    expect(within(region).queryByRole("group", { name: "Period" })).toBeNull();
    expect(within(region).queryByRole("group", { name: "Segment" })).toBeNull();
    pick(region, "Meaning", "NNA (current)");
    expect(send(region)).toBeEnabled();
    await act(async () => fireEvent.click(send(region)));
    await waitFor(() => expect(agent.requests).toHaveLength(2));
    expect(agent.requests[1].command.resume.picks).toEqual({ meaning: "net-new-assets", show: "definitions" });
  });

  test("a meaning whose definition was cut shows its name alone, with the marker where it is the suggested one; a whole label stays whole", async () => {
    const [, show] = QUESTION_GROUPS;
    const options = (first, second) => [
      { id: "certified-nna", label: first, words: "certified NNA", suggested: true },
      { id: "net-new-assets", label: second, words: "current NNA" },
      { id: "branch-sales-nna", label: "Branch-sales NNA", words: "branch-sales NNA" }];
    const meaning = (first, second) => ({ id: "meaning", label: "Meaning", kind: "meaning", spans: [{ start: 8, end: 11 }], options: options(first, second) });
    const cutSuggested = "Certified NNA — Certified net new assets as reported at… (default)";
    const agent = renderChat([
      questionsTurn({ groups: [meaning(cutSuggested, "Net New Assets — Net new assets as the current books hold…"), show] }),
      picksTurn((q) => figure(q)),
      questionsTurn({ groups: [meaning("Certified NNA — As reported at month end (default)", "Net New Assets — As the books hold them now"), show] }),
    ]);
    await ask("Show me NNA");
    const region = await card();
    expect(labels(region, "Meaning")).toEqual(["Certified NNA (default)", "Net New Assets", "Branch-sales NNA"]);
    pick(region, "Meaning", "Certified NNA (default)");
    expect(pressed(region)).toEqual(["Certified NNA (default)"]);
    pick(region, "Show", "Definitions");
    await act(async () => fireEvent.click(send(region)));
    await waitFor(() => expect(agent.requests).toHaveLength(2));
    expect(agent.requests[1].command.resume.picks).toEqual({ meaning: "certified-nna", show: "definitions" });
    cleanup();

    renderChat([questionsTurn({ groups: [meaning("Certified NNA — As reported at month end (default)", "Net New Assets — As the books hold them now"), show] })]);
    await ask("Show me NNA");
    expect(labels(await card(), "Meaning")).toEqual(["Certified NNA — As reported at month end (default)", "Net New Assets — As the books hold them now", "Branch-sales NNA"]);
    cleanup();

    // Three dots are a cut as "…" is; a Show option is never shortened, whatever its label.
    renderChat([questionsTurn({ groups: [meaning("Certified NNA — Certified net new assets as... (default)", "Net New Assets — Net new assets as..."),
      { ...show, options: [{ id: "definitions", label: "Definitions — what each term… (default)", words: "Definitions" }, show.options[1]] }] })]);
    await ask("Show me NNA");
    const dotted = await card();
    expect(labels(dotted, "Meaning")).toEqual(["Certified NNA (default)", "Net New Assets", "Branch-sales NNA"]);
    expect(labels(dotted, "Show")).toEqual(["Definitions — what each term… (default)", "Figures"]);
  });

  test("Something else stays: one answer in the person's own words resumes with { other }", async () => {
    const agent = renderChat([questionsTurn(), pickTurn(null, { say: "Thanks." })]);
    await ask("Show me NNA");
    const region = await card();
    fireEvent.click(within(region).getByRole("button", { name: "Something else" }));
    fireEvent.change(within(region).getByRole("textbox", { name: "Your answer" }), { target: { value: "certified NNA for May 2025" } });
    await act(async () => fireEvent.click(within(region).getByRole("button", { name: "Send your answer" })));
    await waitFor(() => expect(agent.requests).toHaveLength(2));
    expect(agent.requests[1].command).toEqual({ resume: { other: "certified NNA for May 2025" } });
    await screen.findByText("Thanks.");
    expect(within(screen.getByRole("region", { name: QUESTIONS_HEADING })).getByText("You: certified NNA for May 2025")).toBeInTheDocument();
  });

  test("Cancel clarification resumes with { cancel: true }, as on the one-question card", async () => {
    const cancelTurn = (messages, n) => {
      const call = [...messages].reverse().find((m) => m.tool_calls?.some((c) => c.name === "choose")).tool_calls[0];
      return [["values", { messages: [...messages, { type: "tool", id: `cancel-${n}`, tool_call_id: call.id, name: "choose", content: JSON.stringify({ cancelled: true }) }] }]];
    };
    const agent = renderChat([questionsTurn(), cancelTurn]);
    await ask("Show me NNA");
    await act(async () => fireEvent.click(within(await card()).getByRole("button", { name: "Cancel clarification" })));
    await waitFor(() => expect(agent.requests[1].command).toEqual({ resume: { cancel: true } }));
    await screen.findByText("You: Cancelled");
  });

  test("an answer the server did not take says why, and a failed Send leaves the card to answer again", async () => {
    const refused = (messages, n) => {
      const call = [...messages].reverse().find((m) => m.tool_calls?.some((c) => c.name === "choose")).tool_calls[0];
      return [["values", { messages: [...messages, { type: "tool", id: `t-${n}`, tool_call_id: call.id, name: "choose", content: JSON.stringify({ error: "That choice is no longer on offer: ask the question again." }) }] }]];
    };
    renderChat([questionsTurn(), refused]);
    await ask("Show me NNA");
    let region = await card();
    pick(region, "Meaning", "NNA (current)");
    pick(region, "Show", "Definitions");
    await act(async () => fireEvent.click(send(region)));
    region = screen.getByRole("region", { name: QUESTIONS_HEADING });
    await waitFor(() => expect(within(region).getByRole("alert")).toHaveTextContent("Not taken: That choice is no longer on offer"));
    cleanup();

    const broken = () => { throw new Error("stream broke"); };
    const agent = renderChat([questionsTurn(), broken, picksTurn((q) => figure(q))]);
    await ask("Show me NNA");
    region = await card();
    pick(region, "Meaning", "NNA (current)");
    pick(region, "Show", "Definitions");
    await act(async () => fireEvent.click(send(region)));
    await waitFor(() => expect(agent.requests).toHaveLength(2));
    await waitFor(() => expect(within(screen.getByRole("region", { name: QUESTIONS_HEADING })).getByRole("button", { name: "NNA (current)" })).toBeEnabled());
    region = screen.getByRole("region", { name: QUESTIONS_HEADING });
    expect(within(region).queryByText(/^You:/)).toBeNull();
    pick(region, "Meaning", "NNA (current)");
    pick(region, "Show", "Definitions");
    await act(async () => fireEvent.click(send(region)));
    await waitFor(() => expect(agent.requests).toHaveLength(3));
    expect(agent.requests[2].command.resume).toEqual({ picks: { meaning: "net-new-assets", show: "definitions" } });
  });

  test("chooseOf, picksOf and chosenText read a multiple-question card and its answer; other cards read as before", () => {
    const read = chooseOf({ source: "questions", question: "Q?", groups: [null, { id: "g", label: "G", options: [{ id: "a", label: "A", when: { group: "x", options: ["y", 3] } }, { id: 1 }] }, { id: "h", label: "H", kind: "show" }] });
    expect(read).toEqual({ source: "questions", question: "Q?", options: [], months: [], groups: [
      { id: "g", label: "G", kind: "", options: [{ id: "a", label: "A", when: { group: "x", options: ["y"] } }] }, { id: "h", label: "H", kind: "show", options: [] }] });
    expect(chooseOf({ source: "questions", question: "Q?" }).groups).toEqual([]);
    expect(chooseOf({ source: "clarify", question: "Q?", options: [{ id: "a", label: "A" }] })).toEqual({ source: "clarify", question: "Q?", options: [{ id: "a", label: "A" }], months: [] });
    expect(picksOf({ picks: [{ group: "g", id: "a" }, { group: 1, id: "b" }, null] })).toEqual({ g: "a" });
    expect(picksOf({ picked: { id: "a" } })).toBeUndefined();
    expect(chosenText({ picks: [{ label: "Meaning", option: "Certified NNA" }, { label: "Show", option: "Both" }, { label: 1 }] })).toBe("Meaning: Certified NNA; Show: Both");
  });
});
