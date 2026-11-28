// #613, part 4, and #626: Cerebro's chat card shows the answer's label, its figure or its table's first rows, and Open,
// and nothing else, in readable words: no table, column or unit id, no Markdown pipe table, no appended glossary
// definition. The full answer (Context's card: notes, definitions, the complete text), its citations, SQL and ✓/✗ are
// one click away, in the side panel Open shows.
// Synthetic replies in KGE's shapes (kge/services/ask/models.py, engine/wording.py): answer_text carries the internals
// KGE's templates put in it, and the structured fields carry the figure, the table and the citations.
import React from "react";
import { afterEach, describe, expect, test, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
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

// #651 (B4): a reply with no figures (the server marks it card: false) is a message only: no card, no Open.
async function message(reply) {
  const agent = mockAgent([askTurn(reply)]);
  const { container } = render(<ChatView state={{ roles: ["business"], userId: "reader@example.com" }} actions={{ setLastPath: () => {} }} fetch={agent.fetch} />);
  fireEvent.change(within(container).getByRole("textbox", { name: "Ask the Assistant" }), { target: { value: reply.question } });
  await act(async () => fireEvent.click(within(container).getByRole("button", { name: "Send" })));
  await within(container).findByRole("group", { name: "This reply" });
  expect(within(container).queryByRole("article", { name: "Answer" })).toBeNull();
  expect(within(container).queryByRole("button", { name: /^Open/ })).toBeNull();
  const shown = container.querySelector(".cerebro-reply-text");
  expectReadable(shown.textContent);
  return shown;
}

// The card's parts (#626): the compact card and Open, nothing of Context's card, no SQL and no ✓/✗ (the panel's).
function expectCompactOnly(article, kind, open = "Open") {
  expect([...article.children].map((el) => el.className)).toEqual(["cerebro-compact", "cerebro-chat-open"]);
  expect(article.querySelector(".cerebro-compact")).toHaveAttribute("data-kind", kind);
  expect(article.querySelector(".answer-card")).toBeNull();
  expectReadable(article.textContent);
  for (const name of ["Copy answer", "Cite", "More", "Behind the answer", "Confirmed", "Not what I meant"]) expect(within(article).queryByRole("button", { name })).toBeNull();
  expect(within(article).queryByRole("list", { name: "Citations" })).toBeNull();
  return within(article).getByRole("button", { name: open });
}

// Open's side panel, and a tab of it chosen by name.
function opened(article) {
  fireEvent.click(within(article).getByRole("button", { name: /^Open/ }));
  return document.getElementById("cerebro-panel");
}
const tab = (panel, name) => fireEvent.click(within(within(panel).getByRole("tablist", { name: "The answer's parts" })).getByRole("tab", { name }));
const tabs = (panel) => within(within(panel).getByRole("tablist", { name: "The answer's parts" })).getAllByRole("tab").map((t) => t.textContent);

describe("the compact card on each kind of answer", () => {
  test("a figure: its label and the figure; the source, the appended definition, the citation and ✓/✗ in the panel", async () => {
    const article = await card(FIGURE);
    expectCompactOnly(article, "figure");
    const compact = article.querySelector(".cerebro-compact");
    expect(within(compact).getByText("Certified")).toHaveAttribute("data-answer-mode", "certified");
    expect(compact.querySelector(".cerebro-compact-label")).toHaveTextContent("Net new assets · March 2025 · as reported");
    expect(compact.querySelector(".figrow")).toHaveTextContent("USD5,929,714.83");
    expect(article.textContent).not.toContain(DEFINITION);
    const panel = opened(article);
    // No table, chart or SQL: the panel opens on the full answer, what More held.
    expect(tabs(panel)).toEqual(["Full answer", "Sources", "Steps"]);
    const full = panel.querySelector(".answer-card");
    expect(full.querySelector(".answer-source")).toHaveTextContent("wealth · F_NNA_FIN.NNA_AM");
    expect(within(full).queryByRole("button", { name: "Confirmed" })).toBeNull();
    // #651 (B2): no ✓/✗ in the panel; Like and Dislike sit under the reply.
    for (const name of ["Confirmed", "Not what I meant"]) expect(within(panel).queryByRole("button", { name })).toBeNull();
    expect(within(await screen.findByRole("group", { name: "This reply" })).getByRole("button", { name: "Like" })).toBeInTheDocument();
    tab(panel, "Sources");
    expect(panel.querySelector("[role='tabpanel']")).toHaveTextContent("Net new assets policy");
  });

  test("a table: its headline's first line, its first rows with readable column names, and Open with its size", async () => {
    const article = await card(TABLE);
    expectCompactOnly(article, "table", "Open · 2 rows × 2 columns");
    const compact = article.querySelector(".cerebro-compact");
    expect(compact.querySelector(".cerebro-compact-label")).toHaveTextContent(/^Net new assets by region for Q1 2025, largest first \[1\]\.$/);
    const table = within(compact).getByRole("table");
    expect(within(table).getByText("Net new assets by region, Q1 2025")).toBeInTheDocument();
    expect(within(table).getAllByRole("columnheader").map((th) => th.textContent)).toEqual(["Region", "Net new assets"]);
    expect(within(table).getAllByRole("row").slice(1).map((tr) => tr.textContent)).toEqual(["NORTHUSD 1,176,801.46", "SOUTHUSD 1,062,980.00"]);
    // The notes are the full answer's.
    expect(article.textContent).not.toContain("certified columns only");
    const panel = opened(article);
    expect(tabs(panel)).toEqual(["Table", "Full answer", "Sources", "Steps"]);
    expect(within(panel).getByRole("tab", { name: "Table" })).toHaveAttribute("aria-selected", "true");
    tab(panel, "Full answer");
    expect(panel.querySelector(".answer-card")).toHaveTextContent("certified columns only");
    tab(panel, "Sources");
    expect(panel.querySelector("[role='tabpanel']")).toHaveTextContent("Net new assets policy");
  });

  test("a chart: the card shows its table's first rows; the chart is the panel's, beside the table", async () => {
    const article = await card(CHART);
    expectCompactOnly(article, "chart", "Open · 2 rows × 2 columns");
    expect(within(article).queryByRole("figure", { name: "Bar chart" })).toBeNull();
    expect(within(article).getAllByRole("row").slice(1).map((tr) => tr.textContent)).toEqual(["NORTHUSD 1,176,801.46", "SOUTHUSD 1,062,980.00"]);
    const panel = opened(article);
    expect(tabs(panel)).toEqual(["Table", "Chart", "Full answer", "Sources", "Steps"]);
    tab(panel, "Chart");
    const chart = within(panel).getByRole("figure", { name: "Bar chart" });
    expect([...chart.querySelectorAll(".bar-row")].map((r) => r.textContent)).toEqual(["NORTHUSD 1,176,801.46", "SOUTHUSD 1,062,980.00"]);
  });

  test("a chart capped at max_bars says, in the panel, how many bars it shows and that the Table tab has every row", async () => {
    const article = await card({ ...CHART, display: { ...CHART.display, chart: { ...CHART.display.chart, max_bars: 1 } } });
    const panel = opened(article);
    tab(panel, "Chart");
    expect(within(panel).getByRole("figure", { name: "Bar chart" }).querySelectorAll(".bar-row")).toHaveLength(1);
    expect(panel.querySelector(".cerebro-compact-note")).toHaveTextContent("1 of 2 bars shown; the Table tab has every row.");
    tab(panel, "Table");
    expect(within(panel).getAllByRole("row")).toHaveLength(3);
  });

  test("Open names the panel it controls only while that card is open in it", async () => {
    const article = await card(FIGURE);
    const open = within(article).getByRole("button", { name: "Open" });
    expect(open).not.toHaveAttribute("aria-controls");
    fireEvent.click(open);
    expect(open).toHaveAttribute("aria-controls", "cerebro-panel");
    expect(document.getElementById("cerebro-panel")).toBeInTheDocument();
    fireEvent.click(within(document.getElementById("cerebro-panel")).getByRole("button", { name: "Close the panel" }));
    expect(open).not.toHaveAttribute("aria-controls");
    expect(document.getElementById("cerebro-panel")).toBeNull();
  });

  test("#651: a decline is a message only: its readable reason, and nothing of the text's internals", async () => {
    const shown = await message(DECLINE);
    expect(shown.textContent).toBe("You may not read retail.");
  });

  test("#651: a clarification with no question back after it is a message only: its question, and no choice buttons (#624)", async () => {
    const shown = await message(CLARIFICATION);
    expect(shown).toHaveTextContent(/^Revenue has more than one certified meaning here\.$/);
    expect(within(shown).queryByRole("button")).toBeNull();
  });

  test("#651: a glossary definition is a message only: the term and the definition sentence, no provenance", async () => {
    const shown = await message(GLOSSARY);
    expect(shown).toHaveTextContent(/^Net new assets/);
    expect(shown).toHaveTextContent(DEFINITION);
    expect(shown.textContent).not.toMatch(/glossary|steward|certified through/i);
  });

  test("#651: a cited definition is a message only: the term and the definition, no Cited line", async () => {
    const shown = await message(CITED_DEFINITION);
    expect(shown).toHaveTextContent(/^Net New Assets/);
    expect(shown).toHaveTextContent(DEFINITION);
    expect(shown.textContent).not.toContain("Cited:");
  });

  test("an engineer's plan, SQL and trace are in the panel, Behind the answer", async () => {
    // Behind the answer reads the term's resolved map; this test answers it without the network.
    vi.stubGlobal("fetch", async () => new Response(JSON.stringify({ error: "No map in this test." }), { status: 200, headers: { "content-type": "application/json" } }));
    const article = await card({ ...FIGURE, sql: "SELECT SUM(NNA_AM) FROM F_NNA_FIN", run_trace: { stages: [{ stage: "plan", state: "done", ms: 3 }] } }, "engineer");
    expect([...article.children].map((el) => el.className)).toEqual(["cerebro-compact", "cerebro-chat-open"]);
    expectReadable(article.querySelector(".cerebro-compact").textContent);
    const panel = opened(article);
    expect(tabs(panel)).toEqual(["SQL", "Full answer", "Sources", "Steps", "Behind the answer"]);
    tab(panel, "Behind the answer");
    expect(panel.querySelector("#tab-plan")).toHaveTextContent("SELECT SUM(NNA_AM) FROM F_NNA_FIN");
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

  test("#619: a card is named by what it answered, never The figure; a figure keeps its time mode; a table its headline", () => {
    const followUp = { behaviour: "figure", answer_mode: "certified", answer_text: "Certified: The figure: USD 3,615.55.", figure: 3615.55, question: "Total retail revenue for May 2025 for region NW" };
    expect(compactOf(followUp).label).toBe("Retail revenue · May 2025 · region NW");
    expect(compactOf({ ...followUp, question: undefined }).label).toBe("");
    expect(compactOf({ ...followUp, question: undefined, term: "retail_revenue" }).label).toMatch(/revenue/i, "the term where there is no question");
    expect(compactOf(followUp, "Retail revenue · May 2025").label).toBe("Retail revenue · May 2025");
    expect(compactOf({ ...FIGURE, context: { ...FIGURE.context, time_mode: "as_reported" } }, "Net new assets · March 2025").label).toBe("Net new assets · March 2025 · as reported");
    expect(compactOf({ ...FIGURE, context: { ...FIGURE.context, time_mode: "current" } }, "Current NNA").label).toBe("Current NNA");
    const rows = { behaviour: "figure", answer_mode: "certified", answer_text: "The figure: by family.", rows: { a: 1 }, group_by: "family", question: "Revenue for May 2025 by product family" };
    expect(compactOf(rows).label).toBe("Revenue · May 2025 · by product family");
    expect(compactOf({ ...rows, answer_text: "Revenue by family, May 2025." }, "Revenue · May 2025").label).toBe("Revenue by family, May 2025.", "a table keeps its own words");
    const clarify = { behaviour: "clarify", answer_mode: "not_sure", reason: "Which NNA do you mean?", clarification: { choices: [] } };
    expect(compactOf(clarify, "NNA · June 2025").label).toBe("Which NNA do you mean?", "a clarification keeps its question");
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
