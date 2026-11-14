import { classificationLabel, placeholderRule, qualityRules } from "./catalogue-record.js";

// Text is lowercased first, so a timestamp's T and Z match as t and z;
// a date or timestamp, with its offset, is one token.
const numbers = (text) => text.toLowerCase().match(/\d{4}-\d{2}-\d{2}(?:t[\d:.]+(?:z|[+-]\d{2}:?\d{2})?)?|[-+]?\d+(?:[.,]\d+)*(?:%|[eE][-+]?\d+)?|\b(?:zero|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|thirteen|fourteen|fifteen|sixteen|seventeen|eighteen|nineteen|twenty|thirty|forty|fifty|sixty|seventy|eighty|ninety|hundred|thousand|million|billion|trillion)\b/g) || [];
export const hasUncataloguedNumber = (text, catalogueText) => {
  const known = new Set(numbers(catalogueText));
  return numbers(text).some((number) => !known.has(number));
};

// The agent's prompt holds at most this many assets and this many characters of
// their context: the best-matching first, an asset that would overrun skipped.
export const agentBudget = { assets: 8, characters: 60000 };

const notRecorded = "not recorded";
const recorded = (value) => typeof value === "number" && Number.isFinite(value) ? String(value) : typeof value === "string" && value.trim() ? value.trim() : notRecorded;
const listed = (values) => values.length ? values.join(", ") : "none recorded";
function ruleText(rule) {
  const parts = [`Quality rule: ${recorded(rule.rule)}`];
  if (typeof rule.description === "string" && rule.description.trim()) parts.push(`Description: ${rule.description.trim()}`);
  if (typeof rule.rule_type === "string" && rule.rule_type.trim()) parts.push(`Type: ${rule.rule_type.trim()}`);
  if (typeof rule.dimension === "string" && rule.dimension.trim()) parts.push(`Dimension: ${rule.dimension.trim()}`);
  if (rule.fail_percent !== null && rule.fail_percent !== undefined && rule.fail_percent !== "" && Number.isFinite(Number(rule.fail_percent))) parts.push(`Failing: ${Number(rule.fail_percent)}%`);
  return `${parts.join("; ")}.`;
}
function fieldContext(field) {
  const rules = qualityRules(field.quality).filter((rule) => rule && typeof rule === "object" && !placeholderRule(rule));
  return {
    name: field.name, business_name: field.business_name,
    text: [field.name, field.business_name, field.description, ...(field.terms || []),
      `Data type: ${recorded(field.data_type)}`, `PII: ${field.pii === true ? "yes" : "no"}`, `Classification: ${classificationLabel(field.classification) || notRecorded}`,
      ...(rules.length ? rules.map(ruleText) : ["Quality rules: no quality rule is recorded."])].filter(Boolean).join("\n"),
  };
}
// One asset's context: its catalogue metadata as lines the model quotes whole.
// Lineage names only the assets in `lineageIds`, the readable ones in scope.
export function retrievedProduct(product, lineageIds = new Set()) {
  const lineage = (ids) => listed((Array.isArray(ids) ? ids : []).filter((id) => lineageIds.has(id)));
  return {
    product_id: product.id, name: product.name,
    text: [product.name, product.description, `Domain: ${product.domain || ""}`, `Data domain owner: ${product.business_owner || notRecorded}; Data steward: ${product.product_owner || notRecorded}.`,
      `ID: ${product.id}`, `Table: ${recorded(product.table)}`, `Object type: ${recorded(product.product_type)}`, `Path: ${recorded(product.path)}`, `Data format: ${recorded(product.data_format)}`,
      `Badges: ${listed(Array.isArray(product.badges) ? product.badges : [])}`, `Classification: ${classificationLabel(product.classification) || notRecorded}`,
      `Last refreshed: ${recorded(product.last_refresh)}`, `Refresh cadence: ${recorded(product.refresh_cadence)}`, `Last data quality run: ${recorded(product.last_dq_run)}`,
      `Upstream: ${lineage(product.lineage?.upstream)}`, `Downstream: ${lineage(product.lineage?.downstream)}`,
      `Fields: ${product.fields.map((field) => `${field.business_name} (${field.name})`).join(", ")}.`].filter(Boolean).join("\n"),
    fields: product.fields.map(fieldContext),
  };
}

export const catalogueSelection = (products, body, scope) => scope.scoped ? scope.selected : body.domain && body.domain !== "All data assets" ? products.filter((p) => p.domain === body.domain) : products;

// The ranked candidates cut to the budget, each with its context.
export function budgeted(candidates, lineageIds, budget = agentBudget) {
  const kept = [];
  let size = 0;
  for (const product of candidates) {
    if (kept.length >= budget.assets) break;
    const context = retrievedProduct(product, lineageIds);
    const length = JSON.stringify(context).length;
    if (size + length > budget.characters) continue;
    kept.push({ product, context });
    size += length;
  }
  return kept;
}

// `retrieved` is the budgeted candidates: only they reach the model, and a
// statement may cite only them. A number anywhere in the reply must appear in
// the context of an item the reply cites.
export async function catalogueAgent(model, retrieved, body, scope, signal, direction = "") {
  const catalogue = retrieved.map((item) => item.context);
  const reply = await model.complete({ question: body.question, catalogue, ...(direction ? { direction } : {}) }, { signal });
  if (!reply || !Array.isArray(reply.statements) || !reply.statements.length) return null;
  const citations = [];
  const statements = [];
  const cited = [];
  for (const statement of reply.statements) {
    if (!statement || typeof statement.text !== "string") return null;
    const item = retrieved.find((entry) => entry.context.product_id === statement.product_id);
    const product = item?.context;
    const field = statement.field === undefined ? undefined : product?.fields.find((f) => f.name === statement.field);
    const text = statement.text.trim();
    if (!product || (statement.field !== undefined && !field) || !text || !(field || product).text.includes(text)) return null;
    cited.push(product.product_id, product.name, product.text, ...(field ? [field.name, field.business_name, field.text] : []));
    const n = citations.length + 1;
    statements.push(`${product.name}${field ? ` · ${field.business_name} (${field.name})` : ""}: ${text}`);
    citations.push({ n, id: field ? `${product.product_id}.${field.name}` : product.product_id, title: field ? `${product.name}: ${field.business_name}` : product.name, unit: item.product.unit_id, product: { id: product.product_id, name: product.name }, ...(field ? { field: field.name } : {}) });
  }
  if (hasUncataloguedNumber(JSON.stringify(reply), JSON.stringify(cited))) return null;
  return { answer_text: statements.join("\n\n"), behaviour: "catalogue", figures: [], citations, phrased_by: "portal", ...scope.metadata };
}
