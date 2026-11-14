import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import test from "node:test";
import { openFileCatalogueSource } from "../src/catalogue-source/file.js";
import { openProviders } from "../src/providers.js";
import { agentCandidates, catalogueAsk, localKnowledge, productScope } from "../src/knowledge.js";
import { agentBudget, budgeted, catalogueAgent, retrievedProduct } from "../src/catalogue-agent.js";
import { mockAskModel } from "../src/ask-model/mock.js";
import { catalogue } from "./fixtures/catalogue.js";

const cases = JSON.parse(readFileSync(new URL("./fixtures/ask-fallback-eval.json", import.meta.url), "utf8")).filter((row) => row.catalogue === "meridian");
const meridianFile = fileURLToPath(new URL("../../../examples/meridian-portal/catalogue.yaml", import.meta.url));
const meridian = (await openFileCatalogueSource(meridianFile, { onInvalidBadge: () => {} }).load()).products;
const meridianUnits = [...new Set(meridian.map((p) => p.unit_id))];
const modelEnv = { PORTAL_ASK_MODEL: "gemini-2.5-flash", PORTAL_ASK_MODEL_PROVIDER: "mock" };
const lines = (text) => text.split("\n");
const promptSize = (input) => input.catalogue.reduce((sum, item) => sum + JSON.stringify(item).length, 0);

test("the Meridian evaluation set holds the six question shapes and both not-recorded cases", () => {
  assert.deepEqual(cases.map((row) => row.id), ["meridian-certified-nna", "meridian-quality-rules", "meridian-last-refresh", "meridian-owner", "meridian-document", "meridian-connector", "meridian-no-quality-rule", "meridian-no-refresh-date"]);
});

for (const row of cases) test(`fallback evaluation on Meridian: ${row.id}`, async () => {
  const products = meridian.map((p) => row.blank?.[p.id] ? { ...p, ...Object.fromEntries(row.blank[p.id].map((key) => [key, ""])) } : p);
  const seen = [];
  const repository = { listProducts: async () => products, publication: async () => [], promptDirection: async () => ({ text: "" }) };
  const ask = openProviders({ PORTAL_ASK_PROVIDER: "kge", ...modelEnv }, { request: async () => { throw new TypeError("KGE stopped"); } }, "kge", repository, { reply: (input) => { seen.push(input); return row.reply; } }).ask;
  const answer = await ask.run({ portalIdentity: { units: meridianUnits, fallback: true } }, { question: row.question, domain: "All data assets" });
  assert.equal(answer.fallback, true);
  assert.equal(answer.phrased_by, "portal");
  assert.equal(answer.behaviour, "catalogue");
  assert.deepEqual(answer.figures, []);
  assert.deepEqual(answer.citations.map((c) => c.id), row.expected_citations);
  assert.deepEqual([...new Set(answer.citations.map((c) => c.product.id))], row.expected_products);
  assert.ok(answer.answer_text.includes(row.expected_text));
  assert.equal(seen.length, 1);
  const sent = seen[0].catalogue.map((p) => p.product_id);
  for (const id of row.expected_products) assert.ok(sent.includes(id), `${id} retrieved for ${row.id}`);
  assert.ok(sent.length <= agentBudget.assets && sent.length < products.length);
  assert.ok(promptSize(seen[0]) <= agentBudget.characters);
});

const ruled = { ...catalogue.products[0], product_type: "View", table: "accounts", path: "Meridian > retail > Accounts", data_format: "Parquet", badges: ["GOLD", "CERTIFIED"], classification: "confidential",
  last_refresh: "2026-10-20T06:00:00Z", refresh_cadence: "Daily", last_dq_run: "2026-10-19", lineage: { origin_paths: [], destination_paths: [], upstream: ["retail-ledger", "wealth-holdings"], downstream: ["retail-ledger"] },
  fields: [
    { name: "nna", business_name: "Certified NNA", data_type: "DECIMAL(16,2)", description: "Certified net new assets.", terms: ["Net new assets"], classification: "Highly Confidential", pii: true,
      quality: [{ rule: "nna is populated", description: "Every row carries an amount.", rule_type: "Completeness_Checks", dimension: "Completeness", fail_percent: 1.65 }, { rule: "nna is non-negative", rule_type: "Validity_Checks", dimension: "Validity", fail_percent: null }] },
    { name: "region_code", business_name: "Region code", data_type: "VARCHAR", description: "Reporting region.", terms: [], quality: { dimension: "Governance", rule: "No field quality rule is recorded", rule_type: "Not_Assessed", fail_percent: 0 } },
    { name: "id", business_name: "Account identifier", data_type: "INTEGER", description: "Account identifier", terms: [], classification: "", pii: false, quality: [] },
  ] };

test("each asset's context carries its catalogue metadata as quotable lines (requirement 1)", () => {
  const context = retrievedProduct(ruled, new Set(["retail-ledger"]));
  assert.equal(context.product_id, ruled.id);
  for (const line of [`ID: ${ruled.id}`, "Table: accounts", "Object type: View", "Path: Meridian > retail > Accounts", "Data format: Parquet", "Badges: GOLD, CERTIFIED", "Classification: Confidential",
    "Last refreshed: 2026-10-20T06:00:00Z", "Refresh cadence: Daily", "Last data quality run: 2026-10-19", "Upstream: retail-ledger", "Downstream: retail-ledger",
    "Data domain owner: Administrator; Data steward: Steward."]) assert.ok(lines(context.text).includes(line), line);
  const bare = retrievedProduct({ ...ruled, badges: [], classification: undefined, last_refresh: "", refresh_cadence: undefined, last_dq_run: null, lineage: { upstream: [], downstream: [] } });
  for (const line of ["Badges: none recorded", "Classification: not recorded", "Last refreshed: not recorded", "Refresh cadence: not recorded", "Last data quality run: not recorded", "Upstream: none recorded", "Downstream: none recorded"]) assert.ok(lines(bare.text).includes(line), line);
  assert.doesNotMatch(JSON.stringify(context), /quality_score|records|12000|ingested_at|Steward \(|subscribe/);
});

test("each field's context carries its type, PII flag, classification and every quality rule (requirement 2)", () => {
  const [nna, region, id] = retrievedProduct(ruled).fields;
  assert.deepEqual(lines(nna.text), ["nna", "Certified NNA", "Certified net new assets.", "Net new assets", "Data type: DECIMAL(16,2)", "PII: yes", "Classification: Highly Confidential",
    "Quality rule: nna is populated; Description: Every row carries an amount.; Type: Completeness_Checks; Dimension: Completeness; Failing: 1.65%.",
    "Quality rule: nna is non-negative; Type: Validity_Checks; Dimension: Validity."]);
  assert.deepEqual(lines(region.text).slice(-4), ["Data type: VARCHAR", "PII: no", "Classification: not recorded", "Quality rules: no quality rule is recorded."]);
  assert.doesNotMatch(region.text, /No field quality rule|Not_Assessed|Failing/);
  assert.equal(lines(id.text).at(-1), "Quality rules: no quality rule is recorded.");
  const meridianField = retrievedProduct(meridian.find((p) => p.id === "wealth.F_NNA_FIN")).fields.find((f) => f.name === "NNA_AM");
  assert.ok(lines(meridianField.text).includes("Quality rule: NNA_AM is populated; Type: Custom_Data_Checks; Dimension: Completeness; Failing: 0%."));
});

const ledger = { ...catalogue.products[0], id: "retail-ledger", table: "ledger", name: "Retail Ledger", description: "General ledger postings.", last_refresh: "2024-03-15", last_dq_run: "2024-03-14", lineage: { upstream: [], downstream: [] }, fields: [] };
for (const [name, reply, passes] of [
  ["a date from the cited asset in its excerpt passes", { statements: [{ product_id: ruled.id, text: "Last refreshed: 2026-10-20T06:00:00Z" }] }, true],
  ["a percentage from the cited field passes", { statements: [{ product_id: ruled.id, field: "nna", text: "Failing: 1.65%" }] }, true],
  ["a date from the cited asset outside the excerpt passes", { statements: [{ product_id: ruled.id, text: "Refresh cadence: Daily" }], note: "2026-10-19" }, true],
  ["a date from a sent but uncited asset is refused", { statements: [{ product_id: ruled.id, text: "Refresh cadence: Daily" }], note: "2024-03-15" }, false],
  ["a percentage from an uncited field is refused", { statements: [{ product_id: ruled.id, text: "Refresh cadence: Daily" }], note: "1.65%" }, false],
  ["a number in no item is refused", { statements: [{ product_id: ruled.id, text: "Refresh cadence: Daily" }], note: "987654" }, false],
  ["a reordered date of the cited asset is refused", { statements: [{ product_id: ruled.id, text: "Refresh cadence: Daily" }], note: "2026-19-10" }, false],
  ["a reordered date in no item is refused", { statements: [{ product_id: ruled.id, text: "Refresh cadence: Daily" }], note: "2026-10-02" }, false],
  ["the exact cited date passes", { statements: [{ product_id: ruled.id, text: "Last data quality run: 2026-10-19" }], note: "2026-10-19" }, true],
  ["a timestamp from the cited item passes", { statements: [{ product_id: ruled.id, text: "Refresh cadence: Daily" }], note: "2026-10-20T06:00:00Z" }, true],
  ["a timestamp changed from the cited item's is refused", { statements: [{ product_id: ruled.id, text: "Refresh cadence: Daily" }], note: "2026-10-20T00:06:00Z" }, false],
  ["an uncited asset's date as the excerpt is refused", { statements: [{ product_id: ruled.id, text: "Last refreshed: 2024-03-15" }] }, false],
]) test(`number guard reads the cited items' context (requirement 3): ${name}`, async () => {
  const retrieved = budgeted([ruled, ledger], new Set());
  assert.deepEqual(retrieved.map((item) => item.product.id), [ruled.id, ledger.id]);
  const answer = await catalogueAgent(mockAskModel(reply), retrieved, { question: "When?" }, { metadata: {} });
  if (passes) {
    assert.equal(answer.phrased_by, "portal");
    assert.equal(answer.citations[0].product.id, ruled.id);
  } else assert.equal(answer, null);
});

test("a refused planted date hands over to the rules layer with the same scope", async () => {
  const repository = { listProducts: async () => [ruled, ledger], publication: async () => [], promptDirection: async () => ({ text: "" }) };
  const local = localKnowledge(repository, mockAskModel({ statements: [{ product_id: ruled.id, text: "Data domain owner: Administrator; Data steward: Steward." }], note: "2024-03-15" }));
  const body = { question: "Who owns Retail Accounts?", domain: "retail domain" };
  assert.deepEqual(await local.ask({ portalIdentity: { units: ["retail"] } }, body), catalogueAsk([ruled, ledger], body));
});

test("All data assets sends only the retrieved assets within the budget (requirement 5)", async () => {
  const seen = [];
  const repository = { listProducts: async () => meridian, publication: async () => [], promptDirection: async () => ({ text: "" }) };
  const local = localKnowledge(repository, mockAskModel((input) => { seen.push(input); return { statements: [] }; }));
  const identity = { units: meridianUnits };
  for (const question of ["Where can I find certified net new assets?", "Who owns the view F_NNA_FIN?", "What is the weather tomorrow?"]) {
    const body = { question, domain: "All data assets" };
    await local.ask({ portalIdentity: identity }, body);
    const input = seen.at(-1);
    const { candidates } = agentCandidates(meridian, body, productScope(meridian, body));
    assert.deepEqual(input.catalogue.map((p) => p.product_id), budgeted(candidates, new Set()).map((item) => item.product.id));
    assert.ok(input.catalogue.length >= 1 && input.catalogue.length <= agentBudget.assets, question);
    assert.ok(promptSize(input) <= agentBudget.characters, question);
  }
  assert.equal(seen[0].catalogue[0].product_id, "wealth.F_NNA_FIN", "the asset the question names leads");
  assert.deepEqual(seen[1].catalogue.map((p) => p.product_id), ["wealth.F_NNA_FIN"], "a question naming one asset by ID sends that asset alone");
  const whole = meridian.reduce((sum, p) => sum + JSON.stringify(retrievedProduct(p)).length, 0);
  assert.ok(whole > agentBudget.characters, "the whole catalogue would not fit the budget");
});

test("the budget skips an asset that would overrun it and caps the asset count", () => {
  const big = { ...ruled, id: "retail-big", fields: Array.from({ length: 400 }, (_, i) => ({ ...ruled.fields[0], name: `f${i}` })) };
  const small = Array.from({ length: 12 }, (_, i) => ({ ...ledger, id: `retail-small-${i}` }));
  const kept = budgeted([big, ...small], new Set(), { assets: 8, characters: 20000 });
  assert.deepEqual(kept.map((item) => item.product.id), small.slice(0, 8).map((p) => p.id));
});

test("only readable, published, in-scope assets reach the prompt, the lineage and the answer (requirement 6)", async () => {
  const outside = { ...ledger, id: "retail-outside", name: "Outside Domain Ledger", domain: "Other domain" };
  const hidden = { ...ledger, id: "retail-hidden", name: "Unpublished Ledger" };
  const wealth = catalogue.products.find((p) => p.id === "wealth-holdings");
  const anchor = { ...ruled, lineage: { upstream: ["wealth-holdings", "retail-hidden", "retail-outside", "retail-ledger"], downstream: ["retail-outside"] } };
  const seen = [];
  let reply = { statements: [] };
  const repository = { listProducts: async () => [anchor, ledger, outside, hidden, wealth], publication: async () => [{ product_id: "retail-hidden", published: false }], promptDirection: async () => ({ text: "" }) };
  const local = localKnowledge(repository, mockAskModel((input) => { seen.push(input); return reply; }));
  const ask = (question) => local.ask({ portalIdentity: { units: ["retail"] } }, { question, domain: "retail domain" });
  await ask("Which ledger postings are upstream of Retail Accounts, and what about Wealth Holdings and Outside Domain Ledger?");
  const prompt = JSON.stringify(seen.at(-1));
  assert.deepEqual(seen.at(-1).catalogue.map((p) => p.product_id).sort(), [anchor.id, ledger.id].sort());
  assert.doesNotMatch(prompt.replace(seen.at(-1).question.replace(/"/g, '\\"'), ""), /wealth-holdings|Wealth Holdings|retail-hidden|Unpublished Ledger|retail-outside|Outside Domain Ledger/);
  assert.ok(lines(seen.at(-1).catalogue.find((p) => p.product_id === anchor.id).text).includes("Upstream: retail-ledger"));
  assert.ok(lines(seen.at(-1).catalogue.find((p) => p.product_id === anchor.id).text).includes("Downstream: none recorded"));
  for (const statement of [{ product_id: "retail-outside", text: "Outside Domain Ledger" }, { product_id: "retail-hidden", text: "Unpublished Ledger" }, { product_id: "wealth-holdings", text: "Wealth Holdings" }, { product_id: anchor.id, text: "Upstream: wealth-holdings" }]) {
    reply = { statements: [statement] };
    const answer = await ask("Who owns Retail Accounts?");
    assert.equal(answer.phrased_by, "catalogue");
    assert.doesNotMatch(JSON.stringify(answer), /wealth-holdings|Wealth Holdings|retail-hidden|Unpublished Ledger|retail-outside|Outside Domain Ledger/);
  }
});

test("a full ID the question names ranks before every asset sharing its table name", () => {
  const shared = Array.from({ length: 9 }, (_, i) => ({ ...ledger, id: `unit${i}.orders`, unit_id: `unit${i}`, table: "orders", name: `Orders ${i}` }));
  const body = { question: "Who owns unit8.orders?" };
  const { candidates } = agentCandidates(shared, body, productScope(shared, body));
  assert.equal(candidates[0].id, "unit8.orders");
  const sent = budgeted(candidates, new Set()).map((item) => item.product.id);
  assert.equal(sent.length, agentBudget.assets);
  assert.ok(sent.includes("unit8.orders"), "the named asset, last in catalogue order, is still sent");
});

test("an explicit multi-product scope sends only the matched products, or all of them when none matches", async () => {
  const seen = [];
  const repository = { listProducts: async () => [ruled, ledger], publication: async () => [], promptDirection: async () => ({ text: "" }) };
  const local = localKnowledge(repository, mockAskModel((input) => { seen.push(input); return { statements: [] }; }));
  const ask = (question) => local.ask({ portalIdentity: { units: ["retail"] } }, { question, products: [ruled.id, ledger.id] });
  await ask("Who owns Retail Ledger?");
  assert.deepEqual(seen.at(-1).catalogue.map((p) => p.product_id), [ledger.id]);
  await ask("Who owns this?");
  assert.deepEqual(seen.at(-1).catalogue.map((p) => p.product_id), [ruled.id, ledger.id]);
});

test("an explicit scope with no match sends its products in catalogue order, not the request's", async () => {
  const nine = Array.from({ length: 9 }, (_, i) => ({ ...ledger, id: `retail-scoped-${"ABCDEFGHI"[i]}`, table: `scoped_${i}`, name: `Scoped ${"ABCDEFGHI"[i]}` }));
  const seen = [];
  const repository = { listProducts: async () => nine, publication: async () => [], promptDirection: async () => ({ text: "" }) };
  const local = localKnowledge(repository, mockAskModel((input) => { seen.push(input); return { statements: [] }; }));
  await local.ask({ portalIdentity: { units: ["retail"] } }, { question: "Who owns this?", products: nine.map((p) => p.id).reverse() });
  assert.deepEqual(seen[0].catalogue.map((p) => p.product_id), nine.slice(0, 8).map((p) => p.id));
});

for (const [name, note, passes] of [
  ["the exact cited offset timestamp passes", "2026-10-20T06:00:00+01:00", true],
  ["a changed offset is refused", "2026-10-20T06:00:00+03:00", false],
  ["an uncited combination of cited dates and offsets is refused", "2026-10-20T06:00:00+02:00", false],
]) test(`number guard reads offset timestamps whole: ${name}`, async () => {
  const offsets = [{ ...ledger, id: "retail-offset-a", last_refresh: "2026-10-20T06:00:00+01:00", last_dq_run: "2026-10-19T06:00:00+02:00" }];
  const reply = { statements: [{ product_id: "retail-offset-a", text: "Retail Ledger" }], note };
  const answer = await catalogueAgent(mockAskModel(reply), budgeted(offsets, new Set()), { question: "When?" }, { metadata: {} });
  if (passes) assert.equal(answer.phrased_by, "portal");
  else assert.equal(answer, null);
});
