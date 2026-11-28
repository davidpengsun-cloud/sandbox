import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import { catalogueAsk } from "../src/knowledge.js";

const cases = JSON.parse(readFileSync(new URL("../../../tests/fixtures/spec_portal_ask_contract.json", import.meta.url), "utf8"));
const products = [
  {
    id: "retail-account", unit_id: "retail", table: "account", name: "Account",
    product_owner: "Account Steward", business_owner: "Account Analyst",
    fields: [{ name: "account_id", business_name: "Account identifier" }],
  },
  {
    id: "retail-customer", unit_id: "retail", table: "customer", name: "Customer",
    product_owner: "Customer Steward", business_owner: "Customer Analyst",
    fields: [{ name: "customer_id", business_name: "Customer identifier" }],
  },
  {
    id: "sme-facility", unit_id: "sme", table: "facility", name: "Facility",
    product_owner: "Facility Steward", business_owner: "Facility Analyst",
    fields: [{ name: "facility_id", business_name: "Facility identifier" }],
  },
];
const answers = new Map(products.map((product) => [`${product.unit_id}.${product.table}`, {
  owner: `${product.name}: Data domain owner: ${product.business_owner}; Data steward: ${product.product_owner}.`,
  fields: `${product.name} contains these fields: ${product.fields.map((field) => `${field.business_name} (${field.name})`).join(", ")}.`,
}]));

for (const row of cases) {
  test(`shared Portal Ask contract: ${row.id}`, () => {
    const input = { question: row.question, ...(Object.hasOwn(row, "products") ? { products: row.products } : {}) };
    const result = catalogueAsk(products, input);
    const ownerMatches = [...answers].filter(([, answer]) => result.answer_text.includes(answer.owner)).map(([id]) => id);
    const fieldMatches = [...answers].filter(([, answer]) => result.answer_text.includes(answer.fields)).map(([id]) => id);
    const actualProducts = [...new Set([...ownerMatches, ...fieldMatches])];
    assert.deepEqual(actualProducts, row.expected_products);
    assert.deepEqual(result.figures, []);
    assert.deepEqual(result.citations, []);
    if (row.decision === "answer-owner") {
      assert.equal(result.behaviour, "catalogue");
      assert.deepEqual(ownerMatches, row.expected_products);
      assert.deepEqual(fieldMatches, []);
      assert.equal(result.answer_text, row.expected_products.map((id) => answers.get(id).owner).join("\n\n"));
    } else if (row.decision === "answer-fields") {
      assert.equal(result.behaviour, "catalogue");
      assert.deepEqual(fieldMatches, row.expected_products);
      assert.deepEqual(ownerMatches, []);
      assert.equal(result.answer_text, row.expected_products.map((id) => answers.get(id).fields).join("\n\n"));
    } else {
      assert.equal(row.decision, "abstain");
      assert.ok(["abstain", "abstain_catalogue_only"].includes(result.behaviour));
      assert.match(result.answer_text, /cannot answer this question/);
      assert.doesNotMatch(result.answer_text, /Data domain owner:|Data steward:|contains these fields/);
    }
    if (Object.hasOwn(row, "products")) assert.deepEqual(result.products, row.products);
    else assert.equal(result.products, undefined);
  });
}
