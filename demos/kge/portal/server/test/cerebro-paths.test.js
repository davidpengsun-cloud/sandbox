import assert from "node:assert/strict";
import test from "node:test";
import { isCard } from "../src/cerebro/agent.js";

// #638: KGE's explained path (#628) on explore_question and map_term. The portal shows what KGE returns: a reply
// carrying paths is a card that ends the turn, with or without a node; an empty paths list is no card of its own.
const PATH = {
  term: "Net New Assets", basis: "net new assets", definition: "Inflows less outflows.", context: "Wealth NNA", context_id: "ctx.nna",
  unit: "Wealth", fact: "Account flows", measure: { name: "Net New Assets", aggregation: "sum", grain: "month" },
  hops: [{ id: "j1", kind: "join", source: "Account", target: "Household", cardinality: "one-to-many", type: "inner", declared: [], said: "Account to Household",
    warning: "A household has many accounts." }],
  dimensions: [], conditions: [], time_rules: [], warnings: ["A household has many accounts."],
};

test("an explore_question reply with paths and no node is a card (#638)", () => {
  assert.equal(isCard("explore_question", { summary: "Net New Assets is computed …", link: "/graph", paths: [PATH] }), true);
  assert.equal(isCard("explore_question", { node: null, summary: "No node.", link: "/", paths: [] }), false, "an empty path is no card");
});

test("a map_term reply whose only content is paths is a card (#638)", () => {
  assert.equal(isCard("map_term", { term: "Net New Assets", principal: "p", compact: true, data_sets: [], defined_by: [], contexts: [], paths: [PATH] }), true);
  assert.equal(isCard("map_term", { term: "x", principal: "p", compact: true, data_sets: [], defined_by: [], contexts: [], paths: [] }), false);
});
