// #638: KGE's explained paths on explore_question and map_term (kge/services/graph/models.py, ExplainedPath). The card
// shows each path compactly, its term, its measure and its hops in KGE's order with a one-to-many hop marked; the side
// panel's Graph path tab (#626) shows the whole path: every hop with its kind, cardinality, type and window, and the
// dimensions, conditions and time rules. Words follow the role: KGE sends physical names in an engineer's reply alone,
// and a measure's expression, a condition's predicate and a hop's declared options are never shown to anyone else,
// whatever arrives. The page computes and orders nothing. Synthetic replies in KGE's shapes.
import React from "react";
import { afterEach, describe, expect, test } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import ChatView from "./ChatView";
import { mockAgent, toolTurn } from "./chatTestAgent";
import { exploreOf, pathsOf, termMapOf } from "./chatTools";

afterEach(cleanup);

const WARNING = "One-to-many: a Accounts row can match several Households rows, so a figure summed across this hop can count a row more than once.";
// A business user's path: business words, as KGE words it for them. It also carries, as no business reply should, the
// engineer's fields, to show the page leaves them out whatever arrives.
const BUSINESS_PATH = {
  term: "Net new assets", basis: "net new assets", definition: "Money clients bring in less money they take out.", context: "Retail flows",
  context_id: "ctx_retail_nna", unit: "Retail", fact: "Transactions",
  measure: { name: "Net new assets", aggregation: "sum", grain: "month", currency: "USD", snapshot: false, expression: "SUM(t.amt_lcl)" },
  hops: [
    { id: "join_txn_acct", kind: "lookup", source: "Transactions", target: "Accounts", cardinality: "many-to-one", type: "inner", declared: ["cardinality"], said: "Transactions to Accounts, a lookup: many-to-one, an inner join, no validity window.", order: 1, use: "always" },
    { id: "join_acct_hh", kind: "cross_reference", source: "Accounts", target: "Households", cardinality: "one-to-many", type: "left", window: "the month's freeze date", declared: ["type"], said: "Accounts to Households, a cross reference: one-to-many, a left join, valid on the month's freeze date.", order: 2, use: "question", warning: WARNING, condition: "acct.hh_id = hh.hh_id" },
  ],
  dimensions: [{ kind: "hierarchy", via: "join_acct_br", name: "Branch", said: "Branches roll up to regions.", levels: [{ name: "Region", code: "RG" }, { name: "Branch", broader: "Region" }] }],
  conditions: [{ kind: "exclusion", id: "excl_internal", label: "Internal transfers", on: "Transactions", predicate: "t.txn_type = 'INT'" }],
  time_rules: [{ kind: "scd2", on: "Accounts", said: "Accounts keeps every version of a row." }],
  warnings: [WARNING],
};
// The same path as KGE gives an engineer: physical names, the expression, the predicate, the declared options.
const ENGINEER_PATH = {
  ...BUSINESS_PATH, unit: "retail", fact: "retail.fct_txn",
  measure: { ...BUSINESS_PATH.measure, name: "Net new assets", expression: "SUM(fct_txn.amt_lcl)" },
  hops: [
    { ...BUSINESS_PATH.hops[0], source: "retail.fct_txn", target: "retail.dim_acct", declared: ["cardinality", "type"] },
    { ...BUSINESS_PATH.hops[1], source: "retail.dim_acct", target: "retail.dim_hh", condition: "dim_acct.hh_id = dim_hh.hh_id" },
  ],
  dimensions: [{ ...BUSINESS_PATH.dimensions[0], levels: [{ name: "Region", code: "RG", column: "dim_br.region_cd" }] }],
  conditions: [{ ...BUSINESS_PATH.conditions[0], predicate: "fct_txn.txn_type = 'INT'" }],
};
const NODE = { kind: "term", id: "term:nna", name: "Net new assets" };
const SUMMARY = "The question names Net new assets (term). Certified links to Transactions (table).";

async function show(turn, roles = ["business"]) {
  const agent = mockAgent([turn]);
  render(<ChatView state={{ roles, userId: "reader@example.com" }} actions={{ setLastPath: () => {}, openNode: () => {} }} fetch={agent.fetch} />);
  fireEvent.change(screen.getByRole("textbox", { name: "Ask the Assistant" }), { target: { value: "What is net new assets?" } });
  await act(async () => fireEvent.click(screen.getByRole("button", { name: "Send" })));
  await screen.findByRole("group", { name: "This reply" });
}
const panel = () => document.getElementById("cerebro-panel");
function openPath(card) {
  fireEvent.click(within(card).getByRole("button", { name: "Open" }));
  return panel();
}

describe("the compact card", () => {
  test("explore_question: the term, the measure and the hops in KGE's order, the one-to-many hop marked", async () => {
    await show(toolTurn("explore_question", { node: NODE, summary: SUMMARY, link: "/cerebro?mode=graph&node=term%3Anna", paths: [BUSINESS_PATH] }));
    const card = screen.getByRole("article", { name: "In the knowledge graph" });
    const path = within(card).getByRole("region", { name: "Explained path" });
    expect(path.querySelector(".cerebro-path-term")).toHaveTextContent("Net new assets");
    expect(path.querySelector(".cerebro-path-measure")).toHaveTextContent("Net new assets (sum, month, USD)");
    const hops = within(path).getAllByRole("listitem");
    expect(hops.map((li) => li.textContent)).toEqual(["Transactions → Accounts", "Accounts → Householdsone-to-many"]);
    const mark = within(hops[1]).getByText("one-to-many");
    expect(mark).toHaveClass("badge", "warn");
    expect(mark).toHaveAttribute("title", WARNING);
    expect(within(hops[0]).queryByText(/one-to-many/)).toBeNull();
    // The node's own card is still there.
    expect(card.querySelector(".cerebro-compact-label")).toHaveTextContent("Net new assets term");
    expect(within(card).getByRole("button", { name: "Open" })).toBeInTheDocument();
  });

  test("explore_question with paths but no node matched is still a card, its summary and its path", async () => {
    await show(toolTurn("explore_question", { summary: "No node matched the question.", link: "/cerebro?mode=graph", paths: [BUSINESS_PATH] }));
    const card = screen.getByRole("article", { name: "In the knowledge graph" });
    expect(card.querySelector(".cerebro-compact-text")).toHaveTextContent("No node matched the question.");
    expect(within(card).getByRole("region", { name: "Explained path" })).toHaveTextContent("Transactions → Accounts");
    expect(within(card).queryByRole("figure")).toBeNull();
    // #651 (B4): it is no answer, so the panel's "Answers in this chat" does not list it; its card opens it.
    expect(document.getElementById("cerebro-panel")).toBeNull();
    expect(within(card).getByRole("button", { name: "Open" })).toBeInTheDocument();
  });

  test("map_term: the map's parts and its path", async () => {
    await show(toolTurn("map_term", { term: "Net new assets", contexts: [{ label: "Retail flows" }], paths: [BUSINESS_PATH] }));
    const card = screen.getByRole("article", { name: "Term map" });
    expect(card).toHaveTextContent("Retail flows");
    expect(within(card).getByRole("list", { name: "Hops of Net new assets" })).toHaveTextContent("Accounts → Households");
  });

  test("an empty or absent paths shows no path part, and its panel no Graph path tab", async () => {
    await show(toolTurn("explore_question", { node: NODE, summary: SUMMARY, link: "/x", paths: [] }));
    const card = screen.getByRole("article", { name: "In the knowledge graph" });
    expect(within(card).queryByRole("region", { name: "Explained path" })).toBeNull();
    const aside = openPath(card);
    expect(within(aside).getAllByRole("tab").map((t) => t.textContent)).toEqual(["Graph"]);
    expect(within(aside).queryByRole("region", { name: "Explained path" })).toBeNull();
    cleanup();
    await show(toolTurn("map_term", { term: "Net new assets", contexts: [{ label: "Retail flows" }] }));
    const map = screen.getByRole("article", { name: "Term map" });
    expect(within(map).queryByRole("region", { name: "Explained path" })).toBeNull();
    expect(within(openPath(map)).getAllByRole("tab").map((t) => t.textContent)).toEqual(["Term map"]);
  });
});

describe("the panel's Graph path tab", () => {
  test("every element of the path: each hop's kind, cardinality, type and window, the dimensions, conditions and time rules", async () => {
    await show(toolTurn("explore_question", { node: NODE, summary: SUMMARY, link: "/x", paths: [BUSINESS_PATH] }));
    const aside = openPath(screen.getByRole("article", { name: "In the knowledge graph" }));
    expect(screen.getByRole("complementary", { name: "Net new assets" })).toBe(aside);
    expect(within(aside).getByRole("tab", { name: "Graph path" })).toHaveAttribute("aria-selected", "true");
    const path = within(aside).getByRole("region", { name: "Path 1: Net new assets" });
    const dl = (label) => Object.fromEntries([...within(path).getAllByLabelText(label)[0].children].map((d) => [d.querySelector("dt").textContent, d.querySelector("dd").textContent]));
    expect(dl("The term")).toEqual({ Basis: "net new assets", Definition: "Money clients bring in less money they take out.", Context: "Retail flows", Unit: "Retail", Fact: "Transactions" });
    expect(dl("The measure")).toEqual({ Name: "Net new assets", Aggregation: "sum", Grain: "month", Currency: "USD", Snapshot: "no" });
    const hops = within(within(path).getByRole("region", { name: "Hops" })).getAllByRole("listitem");
    expect(hops.map((h) => h.querySelector(".cerebro-path-ends").textContent)).toEqual(["Transactions → Accounts", "Accounts → Households"]);
    expect(dl("Hop 1")).toEqual({ Kind: "lookup", Cardinality: "many-to-one", Type: "inner", Order: "1", Use: "always" });
    expect(dl("Hop 2")).toEqual({ Kind: "cross reference", Cardinality: "one-to-many", Type: "left", Window: "the month's freeze date", Order: "2", Use: "question" });
    expect(hops[0]).toHaveTextContent("Transactions to Accounts, a lookup: many-to-one, an inner join, no validity window.");
    expect(within(hops[1]).getByRole("note")).toHaveTextContent(WARNING);
    expect(within(hops[0]).queryByRole("note")).toBeNull();
    const dimensions = within(path).getByRole("region", { name: "Dimensions" });
    expect(dimensions).toHaveTextContent("Branch");
    expect(dimensions).toHaveTextContent("Branches roll up to regions.");
    const levels = within(dimensions).getByRole("table", { name: "Levels of Branch" });
    expect(within(levels).getAllByRole("columnheader").map((th) => th.textContent)).toEqual(["Level", "Code", "Broader"]);
    expect(within(levels).getAllByRole("row").slice(1).map((tr) => tr.textContent)).toEqual(["RegionRG", "BranchRegion"]);
    expect(within(path).getByRole("region", { name: "Conditions" })).toHaveTextContent("Internal transfers");
    expect(within(path).getByRole("region", { name: "Conditions" })).toHaveTextContent("exclusion");
    expect(within(path).getByRole("region", { name: "Time rules" })).toHaveTextContent("scd2 on Accounts: Accounts keeps every version of a row.");
    expect(within(path).getByRole("region", { name: "Warnings" })).toHaveTextContent(WARNING);
  });

  test("a business user sees no physical name, expression, predicate, declared option or id, even where the reply holds them", async () => {
    await show(toolTurn("explore_question", { node: NODE, summary: SUMMARY, link: "/x", paths: [BUSINESS_PATH] }));
    const aside = openPath(screen.getByRole("article", { name: "In the knowledge graph" }));
    const text = document.querySelector(".cerebro-chat").textContent;
    for (const hidden of ["SUM(", "t.txn_type", "acct.hh_id", "Declared", "Expression", "join_txn_acct", "join_acct_br", "excl_internal", "ctx_retail_nna", "Context id"]) {
      expect(text, hidden).not.toContain(hidden);
    }
    expect(within(aside).queryByLabelText("Predicate")).toBeNull();
    expect(within(aside).queryByLabelText("Expression")).toBeNull();
  });

  test("an engineer sees the physical names KGE sends, the expression, the predicate and the declared options", async () => {
    await show(toolTurn("map_term", { term: "Net new assets", contexts: [{ label: "Retail flows" }], paths: [ENGINEER_PATH] }), ["engineer"]);
    const card = screen.getByRole("article", { name: "Term map" });
    expect(within(card).getByRole("list", { name: "Hops of Net new assets" })).toHaveTextContent("retail.fct_txn → retail.dim_acct");
    const aside = openPath(card);
    expect(within(aside).getByRole("tab", { name: "Graph path" })).toHaveAttribute("aria-selected", "true");
    expect(within(aside).getByLabelText("Expression")).toHaveTextContent("SUM(fct_txn.amt_lcl)");
    expect(within(aside).getByLabelText("Predicate")).toHaveTextContent("fct_txn.txn_type = 'INT'");
    expect(within(aside).getAllByLabelText("Join condition")[0]).toHaveTextContent("dim_acct.hh_id = dim_hh.hh_id");
    expect(aside).toHaveTextContent("retail.fct_txn");
    expect(aside).toHaveTextContent("dim_br.region_cd");
    expect(aside).toHaveTextContent("cardinality, type");
    expect(aside).toHaveTextContent("ctx_retail_nna");
  });
});

describe("pathsOf: what KGE returns, nothing computed", () => {
  const tool = (name, content) => ({ type: "tool", name, tool_call_id: "x", content: JSON.stringify(content) });

  test("keeps KGE's order and leaves out what KGE left out: no reordering by `order`, no inferred cardinality", () => {
    const hops = [{ source: "C", target: "D", order: 2 }, { source: "A", target: "B", order: 1, cardinality: "one-to-many" }];
    const [path] = pathsOf({ paths: [{ term: "T", hops }] });
    expect(path.hops).toEqual([{ source: "C", target: "D", order: "2" }, { source: "A", target: "B", order: "1", cardinality: "one-to-many" }]);
    expect(path.measure).toBeUndefined();
  });

  test("the engineer's fields are kept for an engineer alone", () => {
    const business = pathsOf({ paths: [ENGINEER_PATH] })[0];
    expect(business.measure.expression).toBeUndefined();
    expect(business.conditions[0].predicate).toBeUndefined();
    expect(business.hops.every((h) => h.declared === undefined && h.condition === undefined && h.id === undefined)).toBe(true);
    expect(business.context_id).toBeUndefined();
    const engineer = pathsOf({ paths: [ENGINEER_PATH] }, true)[0];
    expect(engineer.measure.expression).toBe("SUM(fct_txn.amt_lcl)");
    expect(engineer.conditions[0].predicate).toBe("fct_txn.txn_type = 'INT'");
    expect(engineer.hops[0].declared).toEqual(["cardinality", "type"]);
  });

  test("explore_question with paths and no node is a card; with neither it is none; map_term with paths alone is a card", () => {
    expect(exploreOf(tool("explore_question", { summary: "s", paths: [BUSINESS_PATH] }))).toMatchObject({ node: null, summary: "s", paths: [{ term: "Net new assets" }] });
    expect(exploreOf(tool("explore_question", { summary: "s", paths: [] }))).toBeNull();
    expect(exploreOf(tool("explore_question", { node: NODE, summary: "s" }))).not.toHaveProperty("paths");
    expect(termMapOf(tool("map_term", { term: "t", paths: [BUSINESS_PATH] }))).toMatchObject({ term: "t", paths: [{ term: "Net new assets" }] });
    expect(termMapOf(tool("map_term", { term: "t", paths: [] }))).toBeNull();
  });
});

test("the path's card is the chat's: the panel opens it from the card", async () => {
  await show(toolTurn("explore_question", { node: NODE, summary: SUMMARY, link: "/x", paths: [BUSINESS_PATH, { ...BUSINESS_PATH, term: "Net new assets, as reported", hops: [] }] }));
  fireEvent.click(within(screen.getByRole("article", { name: "In the knowledge graph" })).getByRole("button", { name: "Open" }));
  await waitFor(() => expect(panel()).not.toBeNull());
  expect(within(panel()).getAllByRole("region", { name: /^Path \d: / }).map((r) => r.getAttribute("aria-label"))).toEqual(["Path 1: Net new assets", "Path 2: Net new assets, as reported"]);
});
