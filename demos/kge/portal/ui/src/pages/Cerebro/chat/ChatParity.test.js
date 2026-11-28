// #607, the first acceptance item: Cerebro's answer card is on par with Context's Ask page, by reuse; since #626 it is
// the full answer, the Full answer tab of the side panel the compact card's Open shows. For Context's own fixtures,
// each reply is rendered as Context's Ask renders it for the role (Context/Answer.js with the props Ask.js passes, and
// Behind the answer for an engineer) and as Cerebro's chat renders the same reply from its ask tool message; every
// element Context's card renders is in Cerebro's, with the same text and the same buttons. #624: the SQL part is the
// panel's own (its SQL tab), so its Context card leaves it out, and it is compared with the panel's; #651: ✓/✗ are
// Like and Dislike under the reply, in neither.
import React from "react";
import { afterEach, beforeEach, expect, test } from "vitest";
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import Answer from "../../Context/Answer";
import BehindAnswer from "../../Context/BehindAnswer";
import modes from "../../Context/fixtures/answer_modes.json";
import behind from "../../Context/fixtures/behind_answer.json";
import ChatView from "./ChatView";
import { askTurn, mockAgent } from "./chatTestAgent";

const ID = "reader@example.com";
const READS = ["leasing", "wealth"];
const MAP = { error: "No map in this test." };

// Behind the answer reads the resolved map of an engineer's term; both sides get the same reply.
const realFetch = globalThis.fetch;
beforeEach(() => {
  globalThis.fetch = (url, init) => (String(url).startsWith("/api/context/map/")
    ? Promise.resolve(new Response(JSON.stringify(MAP), { status: 200, headers: { "content-type": "application/json" } }))
    : realFetch(url, init));
});
afterEach(() => { globalThis.fetch = realFetch; });

const clarification = {
  question: "Revenue for June 2025",
  principal: ID,
  arm: "graph",
  behaviour: "clarify",
  reason: "Revenue has more than one certified meaning here.",
  answer_text: "Revenue has more than one certified meaning here. Which do you mean?",
  answer_mode: "certified",
  history: [{ turn_id: "turn-1" }],
  clarification: { spans: [{ turn: "turn-1", start: 0, end: 7 }], choices: [{ id: "gross", label: "Gross revenue" }, { id: "net", label: "Net revenue" }] },
  receipt: "stub-receipt-clarify.EEEE",
};
const refusedUnit = {
  ...modes.decline,
  question: "NNA for retail in March 2025",
  reason: "You may not read retail.",
  answer_text: "You may not read retail. No figure is given.",
  denied_warehouses: ["retail"],
};
const rich = {
  ...modes.certified,
  display: {
    v: 1,
    headline: { text: "Net New Assets by region for Q1 2025, largest first [1].", by: "template" },
    table: { caption: "Net New Assets by region, calendar_quarter 2025-Q1", columns: [{ key: "region", label: "Region", type: "text" }, { key: "value", label: "NNA", type: "number", currency: "USD" }], rows: [["PACIFIC", 1176801.46], ["MIDWEST", 1062980]] },
    chart: null,
    chart_note: "one figure per region",
    notes: ["as reported", "certified columns only"],
  },
  citations: [{ n: 1, doc: "nna-policy.pdf", title: "NNA policy", version: 3, cited_as: "authority", tier: "gold" }],
  figures: [{ cite: 1, value: 1, currency: "USD" }],
};
const glossary = {
  question: "What is net new assets?",
  principal: ID,
  arm: "graph",
  behaviour: "glossary_definition",
  definition: "Assets brought in by new and existing clients, less assets taken out.",
  steward: "wealth.steward@example.com",
  answer_text: "Certified: Net new assets: assets brought in by new and existing clients, less assets taken out.",
  answer_mode: "certified",
  receipt: "stub-receipt-glossary.FFFF",
};
const engineerReply = { ...behind.rules_after_refused_model_plan, unit: "wealth", column: "nna.amount", figure: 2239781.46, currency: "USD", sql: "SELECT SUM(amount) FROM nna WHERE quarter = '2025-Q1'", run_trace: { stages: [{ stage: "plan", state: "done", ms: 12 }] }, receipt: "stub-receipt-engineer.GGGG" };

const CASES = [
  ["Certified", modes.certified, "business"],
  ["AI-Generated", modes.ai_generated, "business"],
  ["a decline", modes.decline, "business"],
  ["a refusal with its way forward", refusedUnit, "business"],
  ["a clarification", clarification, "business"],
  ["a rich answer with its period, currency, notes and citation", rich, "business"],
  ["a glossary definition", glossary, "steward"],
  ["an engineer's answer", engineerReply, "engineer"],
];

/** The card as Context's Ask renders it for the role (Ask.js: the exchange's Answer, and Behind the answer). */
function ContextAsk({ answer, engineer }) {
  const me = { id: ID, reads: READS, cerebro_roles: engineer ? ["engineer"] : ["business"] };
  const api = async () => MAP;
  return (
    <div data-testid="context">
      <Answer
        answer={answer}
        onCite={engineer ? () => {} : undefined}
        onOpenGraph={engineer ? () => {} : undefined}
        agentArms={undefined}
        mapping={undefined}
        me={me}
        onFeedback={() => {}}
        feedback={undefined}
        feedbackError={undefined}
        onChoice={() => {}}
      />
      {engineer ? <BehindAnswer api={api} answer={answer} me={me} source={null} requestedTab={null} onCurrency={() => {}} /> : null}
    </div>
  );
}

async function cerebroCard(answer, role) {
  // #651 (B4): the server marks only a reply with figures `card: true`; parity is of the full answer the card opens,
  // so every fixture is sent as a card here.
  const agent = mockAgent([askTurn(answer, { card: true })]);
  const { container } = render(
    <div data-testid="cerebro">
      <ChatView state={{ roles: [role], userId: ID, reads: READS }} actions={{ setLastPath: () => {} }} fetch={agent.fetch} />
    </div>,
  );
  fireEvent.change(within(container).getByRole("textbox", { name: "Ask the Assistant" }), { target: { value: answer.question } });
  await act(async () => fireEvent.click(within(container).getByRole("button", { name: "Send" })));
  const article = await within(container).findByRole("article", { name: "Answer" });
  // #626: the chat shows a compact card; the full answer, Context's card, is in the side panel Open shows.
  expect(article.querySelector(".answer-card")).toBeNull();
  fireEvent.click(within(article).getByRole("button", { name: /^Open/ }));
  const panel = container.querySelector("#cerebro-panel");
  tab(panel, "Full answer");
  return panel;
}
const tab = (panel, name) => fireEvent.click(within(within(panel).getByRole("tablist", { name: "The answer's parts" })).getByRole("tab", { name }));

// Context's card without what Cerebro's card shows itself (#624): the SQL part, ✓/✗ with the separator before them, and
// a clarification's choices, which Cerebro asks on the HITL card.
function withoutCardOwn(card) {
  const copy = card.cloneNode(true);
  copy.querySelector(":scope > .answer-sql")?.remove();
  copy.querySelector(".choices")?.remove();
  const actions = copy.querySelector(".answer-actions");
  actions?.querySelectorAll(".feedback, [aria-label='Helpful?']").forEach((b) => b.remove());
  while (actions?.lastElementChild?.classList.contains("answer-action-sep")) actions.lastElementChild.remove();
  return copy;
}
const parts = (card) => [...card.children].map((el) => ({ tag: el.tagName, className: el.className, text: el.textContent }));
const buttons = (card) => within(card).queryAllByRole("button").map((b) => ({ name: b.getAttribute("aria-label") || b.textContent, disabled: b.disabled }));

test.each(CASES)("%s: every element Context's Ask renders is in Cerebro's card", async (_name, reply, role) => {
  const engineer = role === "engineer";
  render(<ContextAsk answer={reply} engineer={engineer} />);
  const shown = screen.getByTestId("context").querySelector(".answer-card");
  const theirs = withoutCardOwn(shown);
  const article = await cerebroCard(reply, role);
  const ours = article.querySelector(".answer-card");
  expect(ours).not.toBeNull();
  // Each child of Context's card, in order, with its text; the card's buttons with their names and states.
  expect(parts(ours)).toEqual(parts(theirs));
  expect(buttons(ours)).toEqual(buttons(theirs));
  // Named elements, so a failure says which one is missing.
  const badge = theirs.querySelector(".row .badge");
  expect(ours.querySelector(".row .badge")).toHaveTextContent(badge.textContent);
  for (const selector of [".ans", ".figrow", ".parts", ".rich-table", ".bound", ".way", ".answer-source", ".definition-source", "p.small"]) {
    const node = theirs.querySelector(selector);
    if (node) expect(ours.querySelector(selector), selector).toHaveTextContent(node.textContent);
  }
  // #651 (B2): ✓/✗ are neither the panel's nor the card's: Like and Dislike sit under the reply in the chat.
  expect(within(article).queryByRole("group", { name: "Was this what you meant?" })).toBeNull();
  expect(within(ours).queryByRole("button", { name: "Confirmed" })).toBeNull();
  expect(ours.querySelector(".answer-sql")).toBeNull();
  const sql = shown.querySelector(":scope > .answer-sql");
  if (sql) {
    tab(article, "SQL");
    expect(article.querySelector(".answer-sql").textContent).toBe(sql.textContent);
  } else {
    expect(within(article).queryByRole("tab", { name: "SQL" })).toBeNull();
  }
  if (engineer) {
    tab(article, "Behind the answer");
    const context = screen.getByTestId("context");
    for (const name of ["sources", "map", "plan", "graph", "trace"]) {
      await waitFor(() => expect(article.querySelector(`#tab-${name}`)?.textContent).toBe(context.querySelector(`#tab-${name}`).textContent));
    }
    expect(article.querySelector("#tab-plan")).toHaveTextContent(reply.sql);
    expect(article.querySelector("#tab-trace")).toHaveTextContent("model plan refused");
  } else {
    expect(within(article).queryByRole("tab", { name: "Behind the answer" })).not.toBeInTheDocument();
  }
});

test("the same engineer's reply shows a business user its SQL in the panel, once, and no plan or trace (#624, #626)", async () => {
  const article = await cerebroCard(engineerReply, "business");
  expect(article.querySelector(".answer-card")).not.toBeNull();
  expect(article.textContent).not.toContain("SELECT");
  expect(within(article).queryByRole("tab", { name: "Behind the answer" })).not.toBeInTheDocument();
  tab(article, "SQL");
  expect(within(article).getByRole("region", { name: "SQL" })).toHaveTextContent("SELECT SUM(amount)");
  expect(article.textContent.split("SELECT")).toHaveLength(2);
  for (const name of ["Full answer", "Sources", "Steps", "SQL"]) {
    tab(article, name);
    expect(article.textContent).not.toContain("model_plan_refused");
    expect(article.textContent).not.toContain("model plan refused");
  }
});

test("#651: an engineer's activity-line graph link opens Behind the answer on its Graph tab, as Context's Ask does", async () => {
  const article = await cerebroCard(engineerReply, "engineer");
  const link = article.querySelector(".activity-graph-link");
  expect(link).not.toBeNull();
  await act(async () => fireEvent.click(link));
  expect(within(article).getByRole("tab", { name: "Behind the answer" })).toHaveAttribute("aria-selected", "true");
  await waitFor(() => expect(article.querySelector("#tabbtn-graph")).toHaveAttribute("aria-selected", "true"));
  expect(article.querySelector("#tab-graph")).not.toBeNull();
  // A tab picked after drops the request: Behind the answer opens on its own first tab again.
  tab(article, "Sources");
  tab(article, "Behind the answer");
  await waitFor(() => expect(article.querySelector("#tabbtn-graph")).toHaveAttribute("aria-selected", "false"));
});
