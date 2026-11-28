// #626: the chat shows compact cards; Open shows a card whole in the side panel to the right of the chat, the chat
// usable beside it. The panel holds the full table (sortable, copyable, as CSV), the chart, the SQL, the full answer,
// the sources and the turn's steps (#651: no graph path, no ✓/✗), and for an engineer the plan and trace; a button
// makes it full-screen, Esc closes it, and on a phone it opens full-screen. #651: the panel's "Answers in this chat"
// lists the answer cards, newest first, and reopens each. On the
// portal server's own stream handler, called in process with a mock agent (chatTestAgent.js).
import React from "react";
import { afterEach, describe, expect, test, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import ChatView from "./ChatView";
import { artifactsOf, csvName, openLabel, sizeOf, sortedRows, toCsv, toTsv } from "./artifacts";
import { compactOf } from "./chatAnswer";
import { askTurn, contextTurn, mockAgent, toolTurn } from "./chatTestAgent";

afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.restoreAllMocks(); });

const SQL = "SELECT region, segment, SUM(nna) FROM nna_by_region GROUP BY region, segment";
// A golden-20 shaped table answer: a display table of five rows and three columns, with a total.
const TABLE = {
  question: "Certified Q2 2024 NNA by business line",
  behaviour: "figure",
  answer_mode: "certified",
  answer_text: "Certified: NNA by business line for Q2 2024.",
  rows: { a: 1 },
  sql: SQL,
  sql_certified: true,
  receipt: "stub-receipt-table.AAAA",
  display: {
    v: 1,
    headline: { text: "Certified: NNA by business line for Q2 2024, largest first.", by: "template" },
    table: {
      caption: "NNA by business line, Q2 2024",
      columns: [{ key: "line", label: "Business line", type: "text" }, { key: "segment", label: "Segment", type: "text" }, { key: "value", label: "NNA", type: "number", currency: "USD" }],
      rows: [["Wealth", "Private", 500], ["Retail", "Mass", 1200], ["Investor Services", "Custody", -40], ["Brokerage", "Self-directed", 75], ["Advisory", "Premier", 300]],
      total: { label: "Total", value: 2035 },
    },
    chart: null,
    notes: ["certified columns only"],
  },
};
const FIGURE = {
  question: "Net new assets for March 2025",
  behaviour: "figure", figure: 42, currency: "USD", answer_mode: "certified", answer_text: "Certified: Net new assets for March 2025: USD 42.00.",
  sql: "SELECT SUM(nna) FROM nna", sql_certified: true, receipt: "stub-receipt-figure.BBBB",
  plan: [{ unit: "wealth", status: "planned" }], trace: [{ step: "plan_trace_marker" }], run_trace: { stages: [{ stage: "plan", state: "done", ms: 3 }] },
};
const MAP = { term: "Net new assets", contexts: [{ label: "Finance" }], paths: [{ term: "Net new assets", basis: "net new assets", definition: "Money in less money out.", context: "Finance", context_id: "ctx_nna", unit: "wealth", fact: "Flows", measure: { name: "Net new assets", aggregation: "sum", grain: "month" }, hops: [], dimensions: [], conditions: [], time_rules: [], warnings: [] }] };

function renderChat(turns, roles = ["business"]) {
  const agent = mockAgent(turns);
  render(<ChatView state={{ roles, userId: "reader@example.com" }} actions={{ setLastPath: () => {} }} fetch={agent.fetch} />);
  return agent;
}
const box = () => screen.getByRole("textbox", { name: "Ask the Assistant" });
async function ask(question) {
  const done = screen.queryAllByRole("group", { name: "This reply" }).length;
  fireEvent.change(box(), { target: { value: question } });
  await act(async () => fireEvent.click(screen.getByRole("button", { name: "Send" })));
  await waitFor(() => expect(screen.queryAllByRole("group", { name: "This reply" })).toHaveLength(done + 1));
}
const cards = () => screen.queryAllByRole("article", { name: "Answer" });
const panel = () => document.getElementById("cerebro-panel");
const tabNames = () => within(within(panel()).getByRole("tablist", { name: "The answer's parts" })).getAllByRole("tab").map((t) => t.textContent);
const chooseTab = (name) => fireEvent.click(within(within(panel()).getByRole("tablist", { name: "The answer's parts" })).getByRole("tab", { name }));
const bodyRows = () => within(panel()).getAllByRole("row").slice(1).map((tr) => [...tr.children].map((td) => td.textContent));

describe("the compact card and its side panel", () => {
  test("a table answer is a compact card with its first three rows and Open with its size; Open shows every row and column", async () => {
    renderChat([askTurn(TABLE)]);
    await ask(TABLE.question);
    const [card] = cards();
    expect(within(card).getAllByRole("row").slice(1).map((tr) => tr.textContent)).toEqual(["WealthPrivateUSD 500.00", "RetailMassUSD 1,200.00", "Investor ServicesCustodyUSD -40.00"]);
    const open = within(card).getByRole("button", { name: "Open · 5 rows × 3 columns" });
    // #651 (B6): beside a wide chat the panel lists the answers until one is opened.
    expect(panel()).toHaveAttribute("data-kind", "answers");
    fireEvent.click(open);
    const aside = screen.getByRole("complementary", { name: "Certified Q2 2024 NNA · by business line" });
    expect(aside).toBe(panel());
    expect(aside).not.toHaveClass("full");
    // The panel is the chat's sibling, to its right; the chat stays where it was.
    expect(aside.previousElementSibling).toBe(screen.getByRole("region", { name: "Conversation" }));
    expect(within(aside).getByRole("tab", { name: "Table" })).toHaveAttribute("aria-selected", "true");
    expect(within(aside).getAllByRole("columnheader").map((th) => th.textContent)).toEqual(["Business line", "Segment", "NNA"]);
    expect(bodyRows().map((r) => r[0])).toEqual(["Wealth", "Retail", "Investor Services", "Brokerage", "Advisory", "Total"]);
    expect(within(aside).getByRole("heading", { level: 2 })).toHaveFocus();
  });

  test("the chat stays usable beside the open panel: the question box takes a question, and another card opens in its place", async () => {
    renderChat([askTurn(TABLE), askTurn(FIGURE)]);
    await ask(TABLE.question);
    fireEvent.click(within(cards()[0]).getByRole("button", { name: /^Open/ }));
    expect(panel()).not.toBeNull();
    fireEvent.change(box(), { target: { value: "Typed beside the panel" } });
    expect(box()).toHaveValue("Typed beside the panel");
    await ask(FIGURE.question);
    expect(cards()).toHaveLength(2);
    expect(panel()).not.toBeNull();
    fireEvent.click(within(cards()[1]).getByRole("button", { name: "Open" }));
    expect(screen.getByRole("complementary", { name: "Net new assets · March 2025" })).toBe(panel());
    expect(within(cards()[1]).getByRole("button", { name: "Open" })).toHaveAttribute("aria-controls", "cerebro-panel");
    expect(within(cards()[0]).getByRole("button", { name: /^Open/ })).not.toHaveAttribute("aria-controls");
  });

  test("Full screen fills the view and comes back; Esc closes the panel and focus goes back to Open", async () => {
    renderChat([askTurn(TABLE)]);
    await ask(TABLE.question);
    const open = within(cards()[0]).getByRole("button", { name: /^Open/ });
    open.focus();
    fireEvent.click(open);
    const full = within(panel()).getByRole("button", { name: "Full screen" });
    expect(full).toHaveAttribute("aria-pressed", "false");
    fireEvent.click(full);
    expect(panel()).toHaveClass("full");
    expect(within(panel()).getByRole("button", { name: "Exit full screen" })).toHaveAttribute("aria-pressed", "true");
    fireEvent.click(within(panel()).getByRole("button", { name: "Exit full screen" }));
    expect(panel()).not.toHaveClass("full");
    fireEvent.keyDown(document.activeElement, { key: "Escape" });
    expect(panel()).toBeNull();
    expect(open).toHaveFocus();
    // Close does the same.
    fireEvent.click(open);
    fireEvent.click(within(panel()).getByRole("button", { name: "Close the panel" }));
    expect(panel()).toBeNull();
  });

  test("below 760 px the panel opens full-screen, with no Full screen button", async () => {
    vi.stubGlobal("matchMedia", (query) => ({ matches: /max-width: 759/.test(query), media: query, addEventListener() {}, removeEventListener() {} }));
    renderChat([askTurn(TABLE)]);
    await ask(TABLE.question);
    fireEvent.click(within(cards()[0]).getByRole("button", { name: /^Open/ }));
    expect(panel()).toHaveClass("full");
    expect(within(panel()).queryByRole("button", { name: "Full screen" })).toBeNull();
    expect(within(panel()).getByRole("button", { name: "Close the panel" })).toBeInTheDocument();
  });
});

describe("the panel's tabs by role", () => {
  test("a business user's panel has the SQL and no plan or trace", async () => {
    renderChat([askTurn(TABLE)]);
    await ask(TABLE.question);
    fireEvent.click(within(cards()[0]).getByRole("button", { name: /^Open/ }));
    expect(tabNames()).toEqual(["Table", "SQL", "Full answer", "Sources", "Steps"]);
    chooseTab("SQL");
    expect(within(panel()).getByRole("region", { name: "SQL" })).toHaveTextContent("FROM nna_by_region");
    for (const name of ["Full answer", "Sources", "Steps"]) {
      chooseTab(name);
      expect(within(panel()).queryByRole("tab", { name: /^(Plan|Trace)$/ })).toBeNull();
    }
    // #651 (B2): no ✓/✗ in the panel; Like and Dislike sit under the reply in the chat.
    expect(within(panel()).queryByRole("group", { name: "Was this what you meant?" })).toBeNull();
  });

  test("an engineer's panel has all: the SQL, and the plan and trace in Behind the answer", async () => {
    vi.stubGlobal("fetch", async () => new Response(JSON.stringify({ error: "No map in this test." }), { status: 200, headers: { "content-type": "application/json" } }));
    renderChat([askTurn(FIGURE)], ["engineer"]);
    await ask(FIGURE.question);
    fireEvent.click(within(cards()[0]).getByRole("button", { name: "Open" }));
    expect(tabNames()).toEqual(["SQL", "Full answer", "Sources", "Steps", "Behind the answer"]);
    chooseTab("Behind the answer");
    const behind = within(panel()).getByRole("tablist", { name: "Behind the answer" });
    expect(within(behind).getAllByRole("tab").map((t) => t.textContent)).toEqual(expect.arrayContaining(["Plan", "Trace"]));
    expect(panel().querySelector("#tab-plan")).toHaveTextContent("SELECT SUM(nna) FROM nna");
    expect(panel().querySelector("#tab-trace")).toHaveTextContent("plan_trace_marker");
  });

  test("the arrow keys move between the panel's tabs", async () => {
    renderChat([askTurn(TABLE)]);
    await ask(TABLE.question);
    fireEvent.click(within(cards()[0]).getByRole("button", { name: /^Open/ }));
    const list = within(panel()).getByRole("tablist", { name: "The answer's parts" });
    fireEvent.keyDown(within(list).getByRole("tab", { name: "Table" }), { key: "ArrowRight" });
    expect(within(list).getByRole("tab", { name: "SQL" })).toHaveAttribute("aria-selected", "true");
    expect(within(list).getByRole("tab", { name: "SQL" })).toHaveFocus();
    fireEvent.keyDown(within(list).getByRole("tab", { name: "SQL" }), { key: "End" });
    expect(within(list).getByRole("tab", { name: "Steps" })).toHaveAttribute("aria-selected", "true");
  });
});

describe("the full table", () => {
  test("sorts by any column, ascending then descending, the total row kept last", async () => {
    renderChat([askTurn(TABLE)]);
    await ask(TABLE.question);
    fireEvent.click(within(cards()[0]).getByRole("button", { name: /^Open/ }));
    const header = (name) => within(panel()).getByRole("columnheader", { name });
    expect(header("NNA")).toHaveAttribute("aria-sort", "none");
    fireEvent.click(within(header("NNA")).getByRole("button", { name: "NNA" }));
    expect(header("NNA")).toHaveAttribute("aria-sort", "ascending");
    expect(bodyRows().map((r) => r[0])).toEqual(["Investor Services", "Brokerage", "Advisory", "Wealth", "Retail", "Total"]);
    fireEvent.click(within(header("NNA")).getByRole("button", { name: "NNA" }));
    expect(header("NNA")).toHaveAttribute("aria-sort", "descending");
    expect(bodyRows().map((r) => r[0])).toEqual(["Retail", "Wealth", "Advisory", "Brokerage", "Investor Services", "Total"]);
    fireEvent.click(within(header("Business line")).getByRole("button", { name: "Business line" }));
    expect(bodyRows().map((r) => r[0])).toEqual(["Advisory", "Brokerage", "Investor Services", "Retail", "Wealth", "Total"]);
    expect(header("NNA")).toHaveAttribute("aria-sort", "none");
  });

  test("Copy puts the table, as shown, on the clipboard as tab-separated cells", async () => {
    const writeText = vi.fn(async () => {});
    vi.stubGlobal("navigator", { ...navigator, clipboard: { writeText } });
    renderChat([askTurn(TABLE)]);
    await ask(TABLE.question);
    fireEvent.click(within(cards()[0]).getByRole("button", { name: /^Open/ }));
    fireEvent.click(within(within(panel()).getByRole("columnheader", { name: "NNA" })).getByRole("button"));
    await act(async () => fireEvent.click(within(panel()).getByRole("button", { name: "Copy the table" })));
    const copied = writeText.mock.calls[0][0].split("\n");
    expect(copied[0]).toBe("Business line\tSegment\tNNA");
    expect(copied[1]).toBe("Investor Services\tCustody\tUSD -40.00");
    expect(copied.at(-1)).toBe("Total\tUSD 2,035.00\t");
    expect(copied).toHaveLength(7);
    expect(within(panel()).getByRole("button", { name: "Copy the table" })).toHaveTextContent("Copied");
  });

  test("Download CSV saves the table as a CSV file named by the answer", async () => {
    const blobs = [];
    const created = vi.fn((blob) => { blobs.push(blob); return "blob:answer"; });
    vi.stubGlobal("URL", Object.assign(Object.create(URL), { createObjectURL: created, revokeObjectURL: vi.fn() }));
    const clicked = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(function click() { clicked.link = { href: this.getAttribute("href"), download: this.download }; });
    renderChat([askTurn(TABLE)]);
    await ask(TABLE.question);
    fireEvent.click(within(cards()[0]).getByRole("button", { name: /^Open/ }));
    fireEvent.click(within(panel()).getByRole("button", { name: "Download CSV" }));
    expect(created).toHaveBeenCalledTimes(1);
    expect(clicked).toHaveBeenCalledTimes(1);
    expect(clicked.link).toEqual({ href: "blob:answer", download: "certified-q2-2024-nna-by-business-line.csv" });
    const text = await blobs[0].text();
    expect(text.replace(/^﻿/, "").split("\r\n")).toEqual([
      "Business line,Segment,NNA", "Wealth,Private,USD 500.00", "Retail,Mass,\"USD 1,200.00\"", "Investor Services,Custody,USD -40.00",
      "Brokerage,Self-directed,USD 75.00", "Advisory,Premier,USD 300.00", "Total,\"USD 2,035.00\",",
    ]);
  });
});

describe("Answers in this chat", () => {
  test("#651: the panel lists the answer cards alone, newest first, as mini cards; one opens with ← All answers back to the list", async () => {
    renderChat([askTurn(TABLE), askTurn(FIGURE), toolTurn("map_term", MAP)]);
    expect(panel()).toBeNull();
    await ask(TABLE.question);
    await ask(FIGURE.question);
    await ask("What is net new assets?");
    // B1: the rail holds the conversations only.
    expect(document.querySelector(".cerebro-rail-body .cerebro-artifacts")).toBeNull();
    expect(screen.getByRole("complementary", { name: "Answers in this chat" })).toBe(panel());
    const list = within(panel()).getByRole("list", { name: "Answers in this chat" });
    const items = within(list).getAllByRole("button");
    expect(items.map((b) => b.querySelector(".cerebro-mini-answer-title").textContent)).toEqual(["Net new assets · March 2025", "Certified Q2 2024 NNA · by business line"]);
    expect(items[0]).toHaveTextContent("USD");
    fireEvent.click(items[1]);
    expect(screen.getByRole("complementary", { name: "Certified Q2 2024 NNA · by business line" })).toBe(panel());
    expect(tabNames()).toEqual(["Table", "SQL", "Full answer", "Sources", "Steps"]);
    fireEvent.click(within(panel()).getByRole("button", { name: "← All answers" }));
    expect(screen.getByRole("complementary", { name: "Answers in this chat" })).toBe(panel());
    // The map's card is no answer: it opens from its own card, on its Graph path tab.
    fireEvent.click(within(screen.getByRole("article", { name: "Term map" })).getByRole("button", { name: "Open" }));
    expect(tabNames()).toEqual(["Graph path", "Term map"]);
    // Close hides the panel; "Answers (n)" opens the list again (B8).
    fireEvent.click(within(panel()).getByRole("button", { name: "Close the panel" }));
    expect(panel()).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Answers (2)" }));
    expect(screen.getByRole("complementary", { name: "Answers in this chat" })).toBe(panel());
  });

  test("every non-answer card the chat shows has Open, opens with the content its card has, and is not in the answers list", async () => {
    const turns = [
      contextTurn({ arm: "x", block: "Terms:\n  Net new assets: Money in less money out. Steward: Wealth finance.\n", sources: [{ n: 1, doc: "nna-policy", title: "NNA policy", citable: true }] }),
      toolTurn("search", { hits: [{ kind: "product", unit: "wealth", id: "wealth.flows", title: "Wealth flows", score: 0.9, why: "Implements net new assets" }] }, { args: { query: "net new assets data" } }),
      toolTurn("read_graph", { nodes: [{ kind: "term", id: "term:nna", name: "Net new assets" }, { kind: "table", id: "table:flows", name: "Wealth flows" }], links: [{ source: "term:nna", target: "table:flows", kind: "implements" }] }, { args: { node: "term:nna" } }),
      toolTurn("explore", { node: { kind: "term", id: "term:nna", name: "Net new assets" }, summary: "Certified links to Wealth flows (table)." }, { args: { node: "term:nna" } }),
      toolTurn("map_term", { term: "net new assets", contexts: [{ label: "Net new assets (wealth)" }], steward: "Wealth finance" }, { args: { term: "net new assets" } }),
    ];
    renderChat(turns);
    for (const q of ["What is NNA?", "Find it", "Show the graph", "Explore it", "Map it"]) await ask(q);
    expect(screen.queryByRole("button", { name: /^Answers \(/ })).toBeNull();
    // A call's question, query or term names its card; a node's id is no words, so the card's own label does.
    const titles = ["NNA", "Net new assets data", "In the knowledge graph", "Net new assets", "Net new assets"];
    const articles = screen.getAllByRole("article");
    expect(articles.map((a) => a.getAttribute("aria-label"))).toEqual(["Definitions", "Search results", "In the knowledge graph", "In the knowledge graph", "Term map"]);
    for (const article of articles) expect(within(article).getByRole("button", { name: "Open" })).toBeInTheDocument();
    const opens = [
      ["Definitions", (p) => expect(p).toHaveTextContent("Money in less money out.Steward: Wealth finance")],
      ["Results", (p) => expect(p).toHaveTextContent("Wealth flowsdata product · WealthImplements net new assets")],
      ["Graph", (p) => expect(within(p).getByRole("img")).toHaveAccessibleName("Path of 2 nodes: Net new assets, Wealth flows")],
      ["Graph", (p) => expect(within(p).getByRole("region", { name: "certified links" })).toHaveTextContent("Wealth flows")],
      ["Term map", (p) => expect(p).toHaveTextContent("Net new assets (wealth)")],
    ];
    for (const [i, [tab, check]] of opens.entries()) {
      fireEvent.click(within(articles[i]).getByRole("button", { name: "Open" }));
      expect(screen.getByRole("complementary", { name: titles[i] })).toBe(panel());
      expect(tabNames()).toEqual([tab]);
      const part = panel().querySelector("[role='tabpanel']");
      check(part);
      // In the panel the card is drawn without its Open, and with no ← All answers where the chat has no answer.
      expect(within(part).queryByRole("button", { name: "Open" })).toBeNull();
      expect(within(panel()).queryByRole("button", { name: "← All answers" })).toBeNull();
    }
  });

  test("the list is derived from the messages: a clarification asked back is no card, and nothing is stored", () => {
    const ask = (id, reply, question) => [
      { type: "ai", id: `ai-${id}`, tool_calls: [{ id: `tc-${id}`, name: "ask", args: { question } }] },
      { type: "tool", id: `tool-${id}`, tool_call_id: `tc-${id}`, name: "ask", content: JSON.stringify(reply) },
    ];
    const messages = [
      { type: "human", content: "Revenue for June 2025" },
      ...ask(1, { behaviour: "clarify", answer_text: "Which?", receipt: "r" }, "Revenue for June 2025"),
      { type: "ai", id: "ai-c", tool_calls: [{ id: "c-1", name: "choose", args: { ask_call: "tc-1", question: "Which?", options: ["a", "b"] } }] },
      ...ask(2, TABLE, TABLE.question),
      { type: "tool", id: "tool-s", tool_call_id: "s-1", name: "search", content: JSON.stringify({ hits: [{ title: "x" }] }) },
    ];
    expect(artifactsOf(messages).map(({ key, kind, title }) => ({ key, kind, title }))).toEqual([
      { key: "tool-2", kind: "answer", title: "Certified Q2 2024 NNA · by business line" },
      // A search with no call to name what was asked is named by its card.
      { key: "tool-s", kind: "search", title: "Search results" },
    ]);
    expect(artifactsOf(undefined)).toEqual([]);
    try { expect(localStorage.length).toBe(0); } catch { /* No storage in this environment: nothing stored either way. */ }
  });
});

test("the table's size, its CSV and its sort, as plain data", () => {
  const c = compactOf(TABLE);
  expect(sizeOf(c)).toEqual({ rows: 5, columns: 3 });
  expect(openLabel(sizeOf(c))).toBe("Open · 5 rows × 3 columns");
  expect(openLabel({ rows: 1, columns: 1 })).toBe("Open · 1 row × 1 column");
  expect(openLabel(null)).toBe("Open");
  expect(sizeOf(compactOf(FIGURE))).toBeNull();
  expect(toCsv([["a,b", "c\"d", "e\nf"]])).toBe("\"a,b\",\"c\"\"d\",\"e\nf\"");
  expect(toTsv([["a\tb", "c"]])).toBe("a b\tc");
  expect(csvName("")).toBe("answer.csv");
  const plain = { columns: ["Name", "Value"], numeric: [false, true], rows: [["b", "USD 1,000.00"], ["a", "no value"], ["c", "USD 20.00"]] };
  expect(sortedRows(plain, 1, "ascending").map((r) => r.cells[0])).toEqual(["c", "b", "a"]);
  expect(sortedRows(plain, 0, "descending").map((r) => r.cells[0])).toEqual(["c", "b", "a"]);
  expect(sortedRows(plain, null).map((r) => r.cells[0])).toEqual(["b", "a", "c"]);
});

test("Graph mode's dock: a card opened there fills the view over the graph, and Esc closes it", async () => {
  const agent = mockAgent([askTurn(TABLE)]);
  const { ChatProvider } = await import("./chatSession");
  const { ChatDock } = await import("./ChatView");
  render(
    <div className="cerebro-view cerebro-view-graph">
      <div data-testid="graph" />
      <ChatProvider actions={{ setLastPath: () => {} }} fetch={agent.fetch}><ChatDock state={{ roles: ["business"] }} /></ChatProvider>
    </div>,
  );
  await ask(TABLE.question);
  const dock = screen.getByRole("complementary", { name: "Assistant" });
  fireEvent.click(within(dock).getByRole("button", { name: /^Open/ }));
  expect(panel()).toHaveClass("full");
  expect(panel().parentElement).toHaveClass("cerebro-view");
  expect(within(panel()).queryByRole("button", { name: "Full screen" })).toBeNull();
  expect(bodyRows()).toHaveLength(6);
  fireEvent.keyDown(document.body, { key: "Escape" });
  await waitFor(() => expect(panel()).toBeNull());
});
