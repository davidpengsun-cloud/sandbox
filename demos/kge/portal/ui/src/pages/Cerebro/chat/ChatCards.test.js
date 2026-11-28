// #622 requirement 3: the non-figure tools' replies are compact cards, as an Ask answer is: the graph tools' linked
// nodes and small graph, search's hits, map_term's compact form, get_context's terms with steward and sources. A
// business user sees no raw JSON, id or score; an engineer keeps the raw result under the card.
import React from "react";
import { expect, test, vi } from "vitest";
import { act, fireEvent, render, screen, within } from "@testing-library/react";
import ChatView from "./ChatView";
import { contextTurn, mockAgent, toolTurn } from "./chatTestAgent";
import { exploreOf, graphPageOf, searchOf, termMapOf } from "./chatTools";

async function show(turn, roles = ["business"]) {
  const agent = mockAgent([turn]);
  const actions = { setLastPath: vi.fn(), openNode: vi.fn() };
  render(<ChatView state={{ roles, userId: "reader@example.com" }} actions={actions} fetch={agent.fetch} />);
  fireEvent.change(screen.getByRole("textbox", { name: "Ask the Assistant" }), { target: { value: "Which tables hold net new assets?" } });
  await act(async () => fireEvent.click(screen.getByRole("button", { name: "Send" })));
  await screen.findByRole("group", { name: "This reply" });
  return actions;
}
// What a business user never reads: JSON, an id, a score, a physical name.
function expectNoRaw() {
  const text = document.querySelector(".agentchat-messages").textContent;
  expect(text).not.toMatch(/[{}"]|score|term:nna|table:wealth|0\.93/);
  expect(screen.queryByRole("region", { name: /^Tool result/ })).toBeNull();
}

const EXPLORE = {
  node: { kind: "term", id: "term:nna", name: "Net new assets" },
  summary: "The question names Net new assets (term). Certified links to Wealth flows (table), NNA policy (document), and others. Proposed links to Client segment (term).",
  link: "/cerebro?mode=graph&node=term%3Anna",
};

test("explore_question: the node, its linked nodes by status and a small read-only graph (#651: no Open graph)", async () => {
  const actions = await show(toolTurn("explore_question", EXPLORE));
  const card = screen.getByRole("article", { name: "In the knowledge graph" });
  expect(card.querySelector(".cerebro-compact-label")).toHaveTextContent("Net new assets term");
  expect(within(within(card).getByRole("region", { name: "certified links" })).getAllByRole("listitem").map((li) => li.textContent))
    .toEqual(["Wealth flows data set", "NNA policy document"]);
  expect(within(card).getByRole("region", { name: "proposed links" })).toHaveTextContent("Client segment");
  const graph = within(card).getByRole("figure", { name: "Net new assets's neighbourhood" });
  expect(within(graph).getByRole("img")).toHaveAccessibleName("Path of 4 nodes: Net new assets, Wealth flows, NNA policy, Client segment");
  expect(within(graph).queryByRole("button")).toBeNull();
  expect(actions.openNode).not.toHaveBeenCalled();
  expectNoRaw();
});

test("an engineer has the raw result under the card", async () => {
  await show(toolTurn("explore", EXPLORE), ["engineer"]);
  expect(screen.getByRole("article", { name: "In the knowledge graph" })).toBeInTheDocument();
  expect(screen.getByRole("region", { name: "Tool result explore" })).toHaveTextContent("term:nna");
});

// The page draws a card exactly where the server's agent ends the turn at one (agent.js, isCard): a reply it does not
// count is no card, and a business user sees nothing of it, not even an empty message.
const NO_CARDS = [
  ["explore_question naming no node", toolTurn("explore_question", { summary: "No node you may read matches the question.", link: "/cerebro?mode=graph" })],
  ["read_graph with no nodes", toolTurn("read_graph", { nodes: [], links: [] })],
  ["search with no hits", toolTurn("search", { hits: [] })],
  ["map_term with no data set, document or context", toolTurn("map_term", { term: "net new assets", principal: "x", steward: "Wealth finance", implementations: [], defined_by: [], contexts: [] })],
  ["get_context with no term and no citable source", contextTurn({ arm: "x", block: "", sources: [{ n: 1, doc: "d", citable: false }] })],
];
test.each(NO_CARDS)("%s: no card, and nothing drawn for a business user", async (_name, turn) => {
  await show(turn);
  expect(screen.queryByRole("article")).toBeNull();
  expect(document.querySelector(".agentchat-ai")).toBeNull();
  expect(screen.queryByText("Nothing in the catalogue matches.")).toBeNull();
});
test("a reply that is no card still shows an engineer its raw result", async () => {
  await show(toolTurn("search", { hits: [] }), ["engineer"]);
  expect(screen.queryByRole("article", { name: "Search results" })).toBeNull();
  expect(screen.getByRole("region", { name: "Tool result search" })).toBeInTheDocument();
});
test("explore_question with a node but no name shows its sentence and no graph", async () => {
  await show(toolTurn("explore_question", { node: { kind: "term", id: "term:x" }, summary: "A node you may read.", link: "/cerebro?mode=graph" }));
  const card = screen.getByRole("article", { name: "In the knowledge graph" });
  expect(card).toHaveTextContent("A node you may read.");
  expect(within(card).queryByRole("figure")).toBeNull();
});

test("read_graph: the page's nodes and their small graph", async () => {
  await show(toolTurn("read_graph", {
    nodes: [{ kind: "term", id: "term:nna", name: "Net new assets" }, { kind: "table", id: "table:wealth.flows", name: "Wealth flows" }],
    links: [{ source: "term:nna", target: "table:wealth.flows", kind: "implements", status: "certified", why: "x" }],
  }));
  const card = screen.getByRole("article", { name: "In the knowledge graph" });
  expect(within(card).getAllByRole("listitem").map((li) => li.textContent)).toEqual(["Net new assets term", "Wealth flows data set"]);
  expect(within(card).getByRole("img")).toHaveAccessibleName("Path of 2 nodes: Net new assets, Wealth flows");
  expectNoRaw();
});

test("search: each hit's title, kind and unit in business words, and why it matched", async () => {
  await show(toolTurn("search", { hits: [
    { kind: "product", unit: "wealth", id: "wealth.flows", title: "Wealth flows", score: 0.93, why: { kind: "term", text: "Implements net new assets", term: "nna" }, restricted: false },
    { kind: "passage", unit: "retail_bank", id: "doc:p1", title: "NNA policy", score: 0.5, why: { kind: "text", text: "Mentions net new assets" }, restricted: true },
  ] }));
  const card = screen.getByRole("article", { name: "Search results" });
  const hits = within(card).getAllByRole("listitem");
  expect(hits[0]).toHaveTextContent("Wealth flowsdata product · WealthImplements net new assets");
  expect(hits[1]).toHaveTextContent("NNA policydocument passage · Retail bankMentions net new assetsYou may not read it");
  expectNoRaw();
});

test("map_term, compact: the term, its data sets, documents, steward and contexts; no column or join", async () => {
  await show(toolTurn("map_term", {
    term: "net new assets", principal: "reader@example.com",
    implementations: [{ unit: "wealth", table: "F_NNA_FIN", column: "NNA_AM", readable: true }, { unit: "wealth", table: "F_NNA_FIN", column: "NNA_CUR", readable: true }],
    defined_by: [{ doc: "nna-policy", title: "NNA policy" }],
    contexts: [{ id: "ctx_nna", label: "Net new assets (wealth)" }],
    steward: "Wealth finance",
    no_join: [{ pair: "wealth-retail", reason: "different grain" }], certified_joins: [],
  }));
  const card = screen.getByRole("article", { name: "Term map" });
  expect(card.querySelector(".cerebro-compact-label")).toHaveTextContent("net new assets");
  const parts = Object.fromEntries([...card.querySelectorAll("dt")].map((dt) => [dt.textContent, dt.nextElementSibling.textContent]));
  expect(parts).toEqual({ "Data sets": "Wealth", Documents: "NNA policy", Steward: "Wealth finance", Contexts: "Net new assets (wealth)" });
  expect(card.textContent).not.toMatch(/NNA_AM|F_NNA|grain|ctx_nna/);
  expectNoRaw();
});

test("map_term in KGE's compact form names each data set by its unit's label", async () => {
  await show(toolTurn("map_term", {
    term: "Net New Assets", principal: "reader@example.com", compact: true, steward: "Wealth Data Steward",
    data_sets: [{ unit: "wealth", unit_label: "Wealth Management", domain: "Finance", currency: "USD" }],
    defined_by: [{ id: "MD-NET-NEW-ASSETS", title: "Metric Definition: Net New Assets" }],
    contexts: [{ label: "Net new assets", measure: "Net New Assets", unit: "wealth", unit_label: "Wealth Management", filters: ["Eligible"] }],
  }));
  const card = screen.getByRole("article", { name: "Term map" });
  const parts = Object.fromEntries([...card.querySelectorAll("dt")].map((dt) => [dt.textContent, dt.nextElementSibling.textContent]));
  expect(parts).toEqual({ "Data sets": "Wealth Management", Documents: "Metric Definition: Net New Assets", Steward: "Wealth Data Steward", Contexts: "Net new assets" });
  expectNoRaw();
});

test("get_context: terms with their steward and sources, no raw package", async () => {
  await show(contextTurn({ arm: "x", block: "Terms:\n  Net new assets: Money in less money out. Steward: Wealth finance.\n", sources: [{ n: 1, doc: "nna-policy", title: "NNA policy", citable: true }] }));
  const card = screen.getByRole("article", { name: "Definitions" });
  expect(card).toHaveTextContent("Net new assetsMoney in less money out.Steward: Wealth finance");
  expect(within(card).getByRole("region", { name: "Sources" })).toHaveTextContent("[1] NNA policy · nna-policy");
  expect(screen.queryByRole("region", { name: /^Tool result/ })).toBeNull();
});

test("the readers: kinds and units in business words; an internal name is left out for anyone but an engineer", () => {
  const tool = (name, value) => ({ type: "tool", name, content: JSON.stringify(value) });
  expect(exploreOf(tool("explore", { node: { kind: "table" }, summary: "Wealth flows is a table. Certified links to A (term), B (column)." }))).toEqual({
    node: null, named: [], summary: "Wealth flows is a table. Certified links to A (term), B (column).", links: [{ status: "certified", nodes: [{ name: "A", kind: "term" }, { name: "B", kind: "column" }] }],
  });
  expect(exploreOf(tool("explore", { summary: "No node." }))).toBeNull();
  expect(searchOf(tool("search", { hits: [] }))).toBeNull();
  expect(graphPageOf(tool("read_graph", { nodes: [] }))).toBeNull();
  expect(termMapOf(tool("map_term", { term: "t", steward: "S", data_sets: [], contexts: [] }))).toBeNull();
  expect(termMapOf(tool("map_term", { term: "t", contexts: [{ label: "C" }] }))).toEqual({ term: "t", dataSets: [], documents: [], steward: "", contexts: ["C"] });
  expect(exploreOf(tool("ask", { summary: "x" }))).toBeNull();
  expect(searchOf(tool("search", { hits: [{ title: "T", kind: "column", unit: "retail", why: "plain" }] }))).toEqual([{ title: "T", kind: "field", unit: "Retail", why: "plain", restricted: false }]);
  const map = { term: "t", data_sets: ["Wealth flows", "F_NNA_FIN"], documents: ["NNA policy"], steward: { name: "S" }, contexts: ["C"] };
  expect(termMapOf(tool("map_term", map))).toEqual({ term: "t", dataSets: ["Wealth flows"], documents: ["NNA policy"], steward: "S", contexts: ["C"] });
  expect(termMapOf(tool("map_term", map), true).dataSets).toEqual(["Wealth flows", "F_NNA_FIN"]);
  expect(termMapOf(tool("map_term", { principal: "x" }))).toBeNull();
});
