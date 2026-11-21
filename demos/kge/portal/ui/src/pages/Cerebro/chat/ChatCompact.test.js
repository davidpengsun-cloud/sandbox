// #613, part 4: Cerebro's chat card shows the answer's label, its figure, or its table or chart, and its citations, and
// nothing else by default, in readable words: no table, column or unit id, no Markdown pipe table, no appended glossary
// definition. The full answer (Context's card: notes, definitions, the complete text) is one click away, under More.
// Synthetic replies in KGE's shapes (kge/services/ask/models.py, engine/wording.py): answer_text carries the internals
// KGE's templates put in it, and the structured fields carry the figure, the table and the citations.
import React from "react";
import { afterEach, describe, expect, test, vi } from "vitest";
import { act, cleanup, fireEvent, render, within } from "@testing-library/react";
import ChatView from "./ChatView";
import { compactOf, hasInternalName, pipeTables, readableText } from "./chatAnswer";
import { askTurn, mockAgent } from "./chatTestAgent";

afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

const DEFINITION = "Money clients bring to the firm less money they take away.";
const RECEIPT = "stub-receipt-compact.ZZZZ";
const NNA_POLICY = { n: 1, doc: "nna-policy.pdf", title: "Net new assets policy", version: 3, cited_as: "authority", tier: "gold" };

const FIGURE = {
  question: "Net new assets for March 2025",
  principal: "reader@example.com",
  arm: "graph",
  behaviour: "figure",
  figure: 5929714.83,
  currency: "USD",
  unit: "wealth",
  column: "F_NNA_FIN.NNA_AM",
  months: ["2025-03"],
  context: { term: "Net new assets", time_mode: "as_reported", context: "ctx_nna_finance" },
  answer_text: `Certified: Net new assets for March 2025: USD 5,929,714.83 (wealth · F_NNA_FIN.NNA_AM; basis: as reported at the month's freeze date) [1].\nNet new assets: ${DEFINITION} [1]\nFrom wealth.f_nna_fin, the certified source for Net new assets.`,
  certified_definitions: [{ term: "Net new assets", definition: DEFINITION, documents: ["nna-policy.pdf"] }],
  citations: [NNA_POLICY],
  answer_mode: "certified",
  answered_by: "rules",
  receipt: RECEIPT,
};

const TABLE = {
  question: "Net new assets by region for Q1 2025",
  principal: "reader@example.com",
  arm: "graph",
  behaviour: "figure",
  rows: { NORTH: 1176801.46, SOUTH: 1062980 },
  group_by: "office.region_code",
  currency: "USD",
  unit: "wealth",
  column: "flow.amount_usd",
  answer_text: `Certified: Region | Net new assets\n--- | ---\nNORTH | USD 1,176,801.46\nSOUTH | USD 1,062,980 (wealth · flow.amount_usd).\nNet new assets: ${DEFINITION}`,
  certified_definitions: [{ term: "Net new assets", definition: DEFINITION, documents: [] }],
  display: {
    v: 1,
    headline: { text: `Certified: Net new assets by region for Q1 2025, largest first [1].\nNet new assets: ${DEFINITION}`, by: "template" },
    table: {
      caption: "Net new assets by region, calendar_quarter 2025-Q1",
      columns: [{ key: "region", label: "office.region_code", type: "text" }, { key: "value", label: "Net new assets", type: "number", currency: "USD" }],
      rows: [["NORTH", 1176801.46], ["SOUTH", 1062980]],
    },
    chart: null,
    chart_note: "one figure per region",
    notes: ["as reported", "certified columns only"],
  },
  citations: [NNA_POLICY],
  answer_mode: "certified",
  receipt: RECEIPT,
};

const CHART = { ...TABLE, display: { ...TABLE.display, chart: { type: "bar", x: "region", y: "value" } } };

const DECLINE = {
  question: "Net new assets for retail in March 2025",
  principal: "reader@example.com",
  arm: "graph",
  behaviour: "refuse",
  reason: "You may not read retail.",
  answer_text: "Refused: Net new assets needs data you may not read, so no warehouse was queried. Ask the business team that owns retail (R_TXN.AMT_LCL).",
  denied_warehouses: ["retail"],
  answer_mode: "certified",
  receipt: RECEIPT,
};

const CLARIFICATION = {
  question: "Revenue for June 2025",
  principal: "reader@example.com",
  arm: "graph",
  behaviour: "clarify",
  reason: "Revenue has more than one certified meaning here.",
  answer_text: "Revenue has more than one certified meaning here. Gross revenue (sales.gross_amt); Net revenue (sales.net_amt)",
  answer_mode: "certified",
  history: [{ turn_id: "turn-1" }],
  clarification: { spans: [{ turn: "turn-1", start: 0, end: 7 }], choices: [{ id: "gross", label: "Gross revenue" }, { id: "net", label: "Net revenue" }] },
  receipt: RECEIPT,
};

const GLOSSARY = {
  question: "What is net new assets?",
  principal: "reader@example.com",
  arm: "graph",
  behaviour: "glossary_definition",
  term: "Net new assets",
  definition: DEFINITION,
  steward: "wealth.steward@example.com",
  contexts: ["ctx_nna_finance"],
  answer_text: `Certified: Net new assets: ${DEFINITION} This is the pack's glossary's definition: no document defines it, so nothing is cited as its definition. It is certified through its context ctx_nna_finance.`,
  answer_mode: "certified",
  receipt: RECEIPT,
};

const CITED_DEFINITION = {
  question: "What is NNA?",
  principal: "reader@example.com",
  arm: "graph",
  behaviour: "cite_certified",
  cite: ["MD-NET-NEW-ASSETS"],
  certified_definitions: [{ term: "Net New Assets", definition: DEFINITION, documents: ["MD-NET-NEW-ASSETS"] }],
  citations: [{ n: 1, doc: "MD-NET-NEW-ASSETS", title: "Net new assets definition", version: 1 }],
  answer_text: `Certified: Net New Assets: ${DEFINITION} [1]\nCited: MD-NET-NEW-ASSETS Net new assets definition [1], authority.`,
  answer_mode: "certified",
  receipt: RECEIPT,
};

// What a person must never read on the compact card: an id-shaped name, a parenthesised source, a pipe table, and the
// definition appended after a figure.
function expectReadable(text) {
  expect(text).not.toMatch(/[A-Za-z0-9]+_[A-Za-z0-9_]+/);
  expect(text).not.toMatch(/\b[a-z]+\.[a-z_]+\b(?!@)/);
  expect(text).not.toMatch(/\([^()]*·[^()]*\)/);
  expect(text).not.toMatch(/\||---/);
}

async function card(reply, role = "business") {
  const agent = mockAgent([askTurn(reply)]);
  const { container } = render(<ChatView state={{ roles: [role], userId: "reader@example.com" }} actions={{ setLastPath: () => {} }} fetch={agent.fetch} />);
  fireEvent.change(within(container).getByRole("textbox", { name: "Ask the Assistant" }), { target: { value: reply.question } });
  await act(async () => fireEvent.click(within(container).getByRole("button", { name: "Send" })));
  return within(container).findByRole("article", { name: "Answer" });
}

// The card's parts by default: the compact card and More, nothing of Context's card.
function expectCompactOnly(article, kind) {
  expect([...article.children].map((el) => el.className)).toEqual(["cerebro-compact", "cerebro-chat-more"]);
  expect(article.querySelector(".cerebro-compact")).toHaveAttribute("data-kind", kind);
  expect(article.querySelector(".answer-card")).toBeNull();
  const more = within(article).getByRole("button", { name: "More" });
  expect(more).toHaveAttribute("aria-expanded", "false");
  expectReadable(article.textContent);
  // No feedback, copy or activity line until More: the compact card is the answer alone.
  for (const name of ["Confirmed", "Not what I meant", "Copy answer", "Cite"]) expect(within(article).queryByRole("button", { name })).toBeNull();
  return more;
}

describe("the compact card on each kind of answer", () => {
  test("a figure: its label, the figure and its citation; the source and the appended definition under More", async () => {
    const article = await card(FIGURE);
    const more = expectCompactOnly(article, "figure");
    const compact = article.querySelector(".cerebro-compact");
    expect(within(compact).getByText("Certified")).toHaveAttribute("data-answer-mode", "certified");
    expect(compact.querySelector(".cerebro-compact-label")).toHaveTextContent("Net new assets · March 2025 · as reported");
    expect(compact.querySelector(".figrow")).toHaveTextContent("USD5,929,714.83");
    expect(within(compact).getByRole("list", { name: "Citations" })).toHaveTextContent("[1] Net new assets policy");
    expect(article.textContent).not.toContain(DEFINITION);
    fireEvent.click(more);
    expect(more).toHaveAttribute("aria-expanded", "true");
    expect(more).toHaveTextContent("Less");
    const full = article.querySelector(".answer-card");
    expect(full.querySelector(".answer-source")).toHaveTextContent("wealth · F_NNA_FIN.NNA_AM");
    expect(within(full).getByRole("button", { name: "Confirmed" })).toBeEnabled();
  });

  test("a table: its headline's first line, the table with readable column names, and its citation", async () => {
    const article = await card(TABLE);
    expectCompactOnly(article, "table");
    const compact = article.querySelector(".cerebro-compact");
    expect(compact.querySelector(".cerebro-compact-label")).toHaveTextContent(/^Net new assets by region for Q1 2025, largest first \[1\]\.$/);
    const table = within(compact).getByRole("table");
    expect(within(table).getByText("Net new assets by region, Q1 2025")).toBeInTheDocument();
    expect(within(table).getAllByRole("columnheader").map((th) => th.textContent)).toEqual(["Region", "Net new assets"]);
    expect(within(table).getAllByRole("row").slice(1).map((tr) => tr.textContent)).toEqual(["NORTHUSD 1,176,801.46", "SOUTHUSD 1,062,980.00"]);
    expect(within(compact).getByRole("list", { name: "Citations" })).toHaveTextContent("[1] Net new assets policy");
    // The notes are the full answer's.
    expect(article.textContent).not.toContain("certified columns only");
    fireEvent.click(within(article).getByRole("button", { name: "More" }));
    expect(article.querySelector(".answer-card")).toHaveTextContent("certified columns only");
  });

  test("a chart: its bars stand for the table, which is under More", async () => {
    const article = await card(CHART);
    expectCompactOnly(article, "chart");
    const chart = within(article).getByRole("figure", { name: "Bar chart" });
    expect([...chart.querySelectorAll(".bar-row")].map((r) => r.textContent)).toEqual(["NORTHUSD 1,176,801.46", "SOUTHUSD 1,062,980.00"]);
    expect(within(article).queryByRole("table")).toBeNull();
  });

  test("a chart capped at max_bars says how many bars it shows and that More has every row", async () => {
    const article = await card({ ...CHART, display: { ...CHART.display, chart: { ...CHART.display.chart, max_bars: 1 } } });
    expectCompactOnly(article, "chart");
    expect(within(article).getByRole("figure", { name: "Bar chart" }).querySelectorAll(".bar-row")).toHaveLength(1);
    expect(article.querySelector(".cerebro-compact-note")).toHaveTextContent("1 of 2 bars shown; More has every row.");
    const full = await card(CHART);
    expect(full.querySelector(".cerebro-compact-note")).toBeNull();
  });

  test("More names the region it controls only while that region is there", async () => {
    const article = await card(FIGURE);
    const more = within(article).getByRole("button", { name: "More" });
    expect(more).not.toHaveAttribute("aria-controls");
    fireEvent.click(more);
    const id = more.getAttribute("aria-controls");
    expect(id).toBeTruthy();
    expect(article.querySelector(`#${id}`)).toHaveClass("cerebro-chat-full");
    fireEvent.click(more);
    expect(more).not.toHaveAttribute("aria-controls");
  });

  test("a decline: the readable reason is its label, and nothing of the text's internals", async () => {
    const article = await card(DECLINE);
    expectCompactOnly(article, "decline");
    const compact = article.querySelector(".cerebro-compact");
    expect(within(compact).getByText("Refused")).toHaveClass("badge", "bad");
    expect(compact.querySelector(".cerebro-compact-label")).toHaveTextContent(/^You may not read retail\.$/);
    expect(compact.textContent).toBe("RefusedYou may not read retail.");
    fireEvent.click(within(article).getByRole("button", { name: "More" }));
    expect(article.querySelector(".way")).toHaveTextContent("A way forward.");
  });

  test("a clarification: the question is its label, with its choices, which ask the restated question", async () => {
    const agent = mockAgent([askTurn(CLARIFICATION), askTurn({ ...FIGURE, question: "Net revenue for June 2025" })]);
    const { container } = render(<ChatView state={{ roles: ["business"] }} actions={{ setLastPath: () => {} }} fetch={agent.fetch} />);
    fireEvent.change(within(container).getByRole("textbox", { name: "Ask the Assistant" }), { target: { value: CLARIFICATION.question } });
    await act(async () => fireEvent.click(within(container).getByRole("button", { name: "Send" })));
    const article = await within(container).findByRole("article", { name: "Answer" });
    expectCompactOnly(article, "clarification");
    const compact = article.querySelector(".cerebro-compact");
    expect(compact.querySelector(".cerebro-compact-label")).toHaveTextContent(/^Revenue has more than one certified meaning here\.$/);
    const choices = within(compact).getByRole("group", { name: "Choose a meaning" });
    expect(within(choices).getAllByRole("button").map((b) => b.textContent)).toEqual(["Gross revenue", "Net revenue"]);
    await act(async () => fireEvent.click(within(choices).getByRole("button", { name: "Net revenue" })));
    expect(agent.requests[1].input.messages.at(-1).content).toBe("Net revenue for June 2025");
  });

  test("a glossary definition: the term is the label and the definition sentence the content, no provenance", async () => {
    const article = await card(GLOSSARY);
    expectCompactOnly(article, "definition");
    const compact = article.querySelector(".cerebro-compact");
    expect(compact.querySelector(".cerebro-compact-label")).toHaveTextContent(/^Net new assets$/);
    expect(compact.querySelector(".cerebro-compact-text")).toHaveTextContent(DEFINITION);
    expect(compact.textContent).not.toMatch(/glossary|steward|certified through/i);
    fireEvent.click(within(article).getByRole("button", { name: "More" }));
    expect(article.querySelector(".definition-source")).toHaveTextContent("steward: wealth.steward@example.com");
  });

  test("a cited definition: the term, the definition and the document it cites by title", async () => {
    const article = await card(CITED_DEFINITION);
    expectCompactOnly(article, "definition");
    const compact = article.querySelector(".cerebro-compact");
    expect(compact.querySelector(".cerebro-compact-label")).toHaveTextContent(/^Net New Assets$/);
    expect(compact.querySelector(".cerebro-compact-text")).toHaveTextContent(DEFINITION);
    expect(within(compact).getByRole("list", { name: "Citations" })).toHaveTextContent(/^\[1\] Net new assets definition$/);
    expect(compact.textContent).not.toContain("Cited:");
  });

  test("an engineer's plan, SQL and trace stay as they are, under the card", async () => {
    // Behind the answer reads the term's resolved map; this test answers it without the network.
    vi.stubGlobal("fetch", async () => new Response(JSON.stringify({ error: "No map in this test." }), { status: 200, headers: { "content-type": "application/json" } }));
    const article = await card({ ...FIGURE, sql: "SELECT SUM(NNA_AM) FROM F_NNA_FIN", run_trace: { stages: [{ stage: "plan", state: "done", ms: 3 }] } }, "engineer");
    expect([...article.children].map((el) => el.className)).toEqual(["cerebro-compact", "cerebro-chat-more", "cerebro-chat-behind"]);
    expectReadable(article.querySelector(".cerebro-compact").textContent);
    fireEvent.click(within(article).getByRole("button", { name: "Behind the answer" }));
    expect(article.querySelector("#tab-plan")).toHaveTextContent("SELECT SUM(NNA_AM) FROM F_NNA_FIN");
  });
});

describe("compactOf and readableText", () => {
  test("readableText strips the label, a parenthesised source, a pipe table, appended definitions and provenance", () => {
    expect(readableText(FIGURE.answer_text, FIGURE)).toBe("Net new assets for March 2025: USD 5,929,714.83 [1].");
    expect(readableText(TABLE.answer_text, TABLE)).toBe("");
    expect(readableText("Revenue was 42.", {})).toBe("Revenue was 42.");
  });

  test("readableText keeps legitimate text: words in brackets, file names, e-mail addresses, numbers, (withheld)", () => {
    const text = "Revenue for Q1 2025 (withheld) is USD 1,234.50 (Q1 · 2025). See nna-policy.pdf or ask wealth.steward@example.com, e.g. for 1.5 years.";
    expect(readableText(text, {})).toBe(text);
    expect(hasInternalName(text)).toBe(false);
  });

  test("readableText drops a sentence that still names a column, keeping the rest", () => {
    expect(readableText("No answer: column NNA_AM is not certified. Ask a steward.", {})).toBe("Ask a steward.");
    expect(readableText("Total: USD 31 (wealth · nna.amount).", {})).toBe("Total: USD 31.");
    expect(hasInternalName("F_NNA_FIN.NNA_AM")).toBe(true);
    expect(hasInternalName("flow.amount_usd")).toBe(true);
    expect(hasInternalName("SALES.AMT")).toBe(true);
  });

  test("readableText keeps one-letter abbreviations and does not end a sentence at them", () => {
    for (const text of ["Shares listed on a U.S. exchange. Excludes ADRs.", "Revenue in the U.S. is not declared.", "J.P. Morgan is the custodian.",
      "Held by Banque Example S.A. in Geneva.", "Prices fixed at 9 A.M. each day.", "Mail goes to a P.O. box. It is read weekly."]) {
      expect(readableText(text, {})).toBe(text);
      expect(hasInternalName(text)).toBe(false);
    }
    expect(readableText("Booked in the U.S. on SALES.AMT. Ask a steward.", {})).toBe("Ask a steward.");
    expect(hasInternalName("NNA_AM.X")).toBe(true);
  });

  test("a definition's own sentences are never filtered: the steward's words are shown whole", () => {
    const definition = "Assets of clients domiciled in the U.S. at month end. Uses the field NNA_AM as booked.";
    const c = compactOf({ behaviour: "glossary_definition", term: "US assets", definition });
    expect(c).toMatchObject({ kind: "definition", label: "US assets", definitions: [{ term: "US assets", text: definition }] });
  });

  test("a figure's own line is never taken for an appended definition, even with no period", () => {
    const aum = { behaviour: "figure", figure: 12, currency: "USD", term: "Assets under management", answer_mode: "certified",
      answer_text: "Certified: Assets under management: USD 12.00 [1]\nAssets under management: Money clients hold with the firm. [1]",
      certified_definitions: [{ term: "Assets under management", definition: "Money clients hold with the firm." }] };
    expect(readableText(aum.answer_text, aum)).toBe("Assets under management: USD 12.00 [1]");
    expect(compactOf(aum)).toMatchObject({ kind: "figure", label: "Assets under management" });
  });

  test("pipeTables takes a Markdown table out of text, escaped pipes kept in their cells", () => {
    const { text, tables } = pipeTables("Before\n| Region | NNA |\n| --- | ---: |\n| A \\| B | USD 1 |\nAfter");
    expect(text).toBe("Before\nAfter");
    expect(tables).toEqual([{ columns: ["Region", "NNA"], rows: [["A | B", "USD 1"]] }]);
  });

  test("a reply with only text: a pipe table becomes the card's table, and its prose its readable label", () => {
    const c = compactOf({ answer_text: "AI-Generated: By region:\nRegion | Revenue\n--- | ---\nNorth | USD 30\nSouth | USD 12", answer_mode: "ai_generated" });
    expect(c.kind).toBe("table");
    expect(c.badge).toEqual({ text: "AI-Generated", tone: "warn", mode: "ai_generated" });
    expect(c.label).toBe("By region:");
    expect(c.table).toEqual({ caption: "", columns: ["Region", "Revenue"], numeric: [false, true], rows: [["North", "USD 30"], ["South", "USD 12"]] });
  });

  test("a figure with no term takes its label from the text's lead; a figure with no value says so", () => {
    expect(compactOf({ ...FIGURE, context: undefined, months: undefined }).label).toBe("Net new assets for March 2025");
    expect(compactOf({ ...FIGURE, figure: null }).figure).toEqual({ currency: "", value: "No value" });
  });

  test("a combined answer's figures, a sum's parts, an abstention whose reason names a column, and an error", () => {
    const combined = {
      behaviour: "combined", answer_mode: "certified", answer_text: "Certified: For premier clients in 2024: assets USD 10 [1]; clients 4 [2].",
      figures: [{ label: "Assets", term: "assets", kind: "sum", column: "acct.bal_amt", currency: "USD", value: 10, cite: 1 }, { label: "Clients", term: "clients", kind: "count", column: "cust.cust_id", currency: "", value: 4, cite: 2 }],
      citations: [{ n: 1, doc: "a.pdf", title: "Assets" }, { n: 2, doc: "b.pdf" }],
    };
    const c = compactOf(combined);
    expect(c.kind).toBe("table");
    expect(c.label).toBe("For premier clients in 2024");
    expect(c.table.rows).toEqual([["Assets", "USD 10.00 [1]"], ["Clients", "4 [2]"]]);
    expect(c.citations).toEqual([{ n: 1, text: "Assets" }, { n: 2, text: "b.pdf" }]);
    expect(compactOf({ ...combined, figures: combined.figures.slice(0, 1) })).toMatchObject({ kind: "figure", label: "Assets", figure: { currency: "USD", value: "10.00" } });
    const parts = compactOf({ behaviour: "parts_with_reason", parts: { wealth: 5, retail: 7 }, plan: [{ unit: "wealth", currency: "USD" }, { unit: "retail", currency: "EUR" }], answer_text: "Net new assets for 2024: not added, the currencies differ. By unit: Wealth USD 5; Retail EUR 7." });
    expect(parts).toMatchObject({ kind: "table", label: "Net new assets for 2024" });
    expect(parts.table.rows).toEqual([["Wealth", "USD 5.00"], ["Retail", "EUR 7.00"]]);
    const abstain = compactOf({ behaviour: "abstain_with_reason", reason: "The column NNA_AM has no certified implementation.", answer_text: "No answer: the question could not be answered from what is certified." });
    expect(abstain).toMatchObject({ kind: "decline", badge: { text: "Cannot answer" }, label: "No answer: the question could not be answered from what is certified." });
    expect(compactOf({ behaviour: "abstain", reason: "F_X.Y_Z.", answer_text: "T_A.B_C" }).label).toBe("No answer is given.");
    expect(compactOf({ error: "The service is unavailable." })).toMatchObject({ kind: "decline", badge: { text: "Not answered", tone: "bad" }, label: "The service is unavailable." });
    expect(compactOf(null)).toMatchObject({ kind: "text", text: "", citations: [] });
  });

  test("a definition with no structured text reads its first line; search results list their documents", () => {
    expect(compactOf({ behaviour: "cite_certified", answer_text: `Net New Assets: ${DEFINITION}\nCited: MD-NNA.` })).toMatchObject({ kind: "definition", label: "Net New Assets", definitions: [{ term: "Net New Assets", text: DEFINITION }] });
    const several = compactOf({ behaviour: "cite_certified", certified_definitions: [{ term: "A", definition: "First." }, { term: "B", definition: "Second." }] });
    expect(several.definitions).toHaveLength(2);
    const found = compactOf({ behaviour: "cite", cited_docs: [{ doc: "nna-policy.pdf", shelf: "gold" }], answer_text: "Found by search, with no shelf rule in this arm: nna-policy.pdf (gold shelf)." });
    expect(found).toMatchObject({ kind: "text", citations: [{ n: null, text: "nna-policy.pdf" }] });
  });

  test("a grouped figure without a display block is the card's table, and a chart honours max_bars", () => {
    const { display: _d, ...grouped } = TABLE;
    const c = compactOf(grouped);
    expect(c).toMatchObject({ kind: "table", label: "Net new assets by region for Q1 2025", table: { columns: ["Region", "Figure"], rows: [["NORTH", "USD 1,176,801.46"], ["SOUTH", "USD 1,062,980.00"]] } });
    const capped = compactOf({ ...CHART, display: { ...CHART.display, chart: { x: "region", y: "value", max_bars: 1 }, table: { ...CHART.display.table, total: { label: "Total", value: 2239781.46 } } } });
    expect(capped.chart).toMatchObject({ of: 2, bars: [{ label: "NORTH" }] });
    expect(capped.table.rows.at(-1)).toEqual(["Total", "USD 2,239,781.46"]);
  });
});
