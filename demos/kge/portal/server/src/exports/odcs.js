import { useCaseEntries } from "../catalogue-record.js";
import { classification, compact, httpsUri, text, texts } from "./values.js";

// The Open Data Contract Standard version (apiVersion) every ODCS export is written to.
export const ODCS_VERSION = "v3.2.0";

const property = (field) => ({
  name: text(field.name),
  physicalName: text(field.name),
  businessName: text(field.business_name),
  description: text(field.description),
  physicalType: text(field.data_type),
  classification: classification(field),
  tags: texts(field.terms),
  customProperties: field.pii === true ? [{ property: "pii", value: true }] : undefined,
});

// One linked asset as an ODCS schema object. Its name is the asset's
// <unit>.<table>, the id KGE gives a product, so two assets with the same
// table name in different units stay apart; the table is its physicalName.
const schemaName = (product) => (text(product.table) ? [text(product.unit_id), text(product.table)].filter(Boolean).join(".") : product.id);

const schemaObject = (product) => ({
  name: schemaName(product),
  physicalName: text(product.table),
  physicalType: text(product.product_type).toLowerCase(),
  businessName: text(product.name),
  description: text(product.description),
  customProperties: [{ property: "assetId", value: product.id }, text(product.unit_id) ? { property: "unit", value: text(product.unit_id) } : undefined],
  properties: (Array.isArray(product.fields) ? product.fields : []).map(property),
});

// The use case of this id (and title, where two share an id) as the readable
// assets that list it declare it, with those assets in catalogue order: the
// first asset's entry is the definition, as on the use case's card. Null when
// no readable asset lists it.
export function useCaseOf(products, id, title = "") {
  let entry = null;
  const linked = [];
  for (const product of products) {
    const wanted = title || entry?.title;
    const found = useCaseEntries(product.use_cases).find((candidate) => candidate.id === id && (!wanted || candidate.title === wanted));
    if (!found) continue;
    entry = entry || found;
    linked.push(product);
  }
  return entry && { entry, products: linked };
}

// A use case and its linked assets, each already one the person may read, as
// an ODCS data contract. The catalogue does not version a use case, so the
// contract's version is the catalogue release it was read from. The owner and
// the KPI, which ODCS has no field for, are custom properties.
export function odcsDocument({ entry, products }, release) {
  const kpi = entry.kpi && compact({ name: entry.kpi.name, definition: entry.kpi.definition, grain: entry.kpi.grain, unit: entry.kpi.unit });
  return compact({
    apiVersion: ODCS_VERSION,
    kind: "DataContract",
    id: entry.id,
    name: entry.title,
    version: text(release) || "unreleased",
    domain: text(entry.domain),
    description: { purpose: text(entry.objective) },
    authoritativeDefinitions: httpsUri(entry.url) ? [{ url: httpsUri(entry.url), type: "businessDefinition" }] : undefined,
    context: (entry.example_questions || []).length ? { verifiedStatements: entry.example_questions.map((question) => ({ question })) } : undefined,
    customProperties: [
      text(entry.owner) ? { property: "owner", value: text(entry.owner) } : undefined,
      kpi ? { property: "kpi", value: kpi } : undefined,
    ],
    schema: products.map(schemaObject),
  });
}
