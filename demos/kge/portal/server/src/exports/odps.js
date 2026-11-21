import { useCaseEntries } from "../catalogue-record.js";
import { classification, compact, fieldRules, httpsUri, number, text, texts } from "./values.js";

// The Open Data Product Standard version every ODPS export is written to.
export const ODPS_VERSION = "4.2";
export const ODPS_SCHEMA_URL = `https://opendataproducts.org/v${ODPS_VERSION}/schema/odps.json`;

// ODPS names how data is delivered; the catalogue names the asset's kind.
const portTypes = { table: "SQL", view: "SQL", "api/mcp": "API", documents: "file" };
const portType = (productType) => portTypes[text(productType).toLowerCase()] || text(productType) || "SQL";

const column = (field) => ({
  name: text(field.name),
  businessName: text(field.business_name),
  description: text(field.description),
  dataType: text(field.data_type),
  classification: classification(field),
  pii: field.pii === true ? true : undefined,
  terms: texts(field.terms),
});

const qualityRule = (field) => (rule) => ({
  field: text(field.name),
  rule: text(rule.rule),
  ruleType: text(rule.rule_type),
  dimension: text(rule.dimension),
  description: text(rule.description),
  failPercent: number(rule.fail_percent),
});

// One readable asset, as the portal holds it, as an ODPS document. Values ODPS
// has no place for are kept under its x- extensions. ODPS requires a
// visibility, status and type the catalogue does not record, so the export
// assumes them: an asset is shown to the units that read it ("internal"), is
// "production" when published and "draft" when not, and is a "dataset" whatever
// its kind; the catalogue's own kind is in x-asset. The asset's domain is its
// ODPS category; its community and application stay in x-domain. The owners
// stay in x-owner: ODPS's dataHolder is an organisation that needs a legal
// name, which the catalogue does not record. A use case's address is written
// only when it is an https address, as the page shows it.
export function odpsDocument(product) {
  const fields = Array.isArray(product.fields) ? product.fields : [];
  const rules = fields.flatMap((field) => fieldRules(field).map(qualityRule(field)));
  const useCases = useCaseEntries(product.use_cases);
  const cadence = text(product.refresh_cadence);
  const document = {
    schema: ODPS_SCHEMA_URL,
    version: ODPS_VERSION,
    product: {
      details: {
        en: {
          name: text(product.name) || product.id,
          productID: product.id,
          visibility: "internal",
          status: product.published === false ? "draft" : "production",
          type: "dataset",
          description: text(product.description),
          categories: texts(product.domain),
          tags: texts(product.badges),
          outputFileFormats: texts(product.data_format),
          useCases: useCases.map((entry) => ({ useCase: { useCaseTitle: entry.title, useCaseDescription: entry.objective, useCaseURL: httpsUri(entry.url) } })),
        },
      },
      dataAccess: {
        default: { outputPortType: portType(product.product_type), name: { en: text(product.table) || product.id } },
      },
      dataQuality: rules.length ? { executable: [{ type: "Custom", description: "The field quality rules the catalogue records, with each rule's last failing percentage.", spec: { rules } }] } : undefined,
      SLA: cadence ? { declarative: { updateFrequency: { dimensions: [{ dimension: "updateFrequency", objective: cadence }] } } } : undefined,
      "x-owner": {
        productOwner: text(product.product_owner),
        productOwnerEmail: text(product.product_owner_email),
        businessOwner: text(product.business_owner),
        businessOwnerEmail: text(product.business_owner_email),
      },
      "x-domain": { community: text(product.community), application: text(product.application ?? product.source) },
      "x-asset": {
        productType: text(product.product_type),
        assetType: text(product.asset_type),
        usagePurpose: text(product.usage_purpose),
        datasetType: text(product.dataset_type),
        apmId: text(product.APM_ID),
        project: text(product.project),
        catalogueState: text(product.catalogue_state),
        published: product.published === false ? false : true,
        qualityScore: number(product.quality_score),
        lastQualityRun: text(product.last_dq_run),
        lastRefresh: text(product.last_refresh),
        refreshCadence: cadence,
        ingestedAt: text(product.ingested_at),
      },
      "x-tables": [{
        unit: text(product.unit_id),
        schema: text(product.schema),
        table: text(product.table),
        path: text(product.path),
        records: number(product.records),
        storage: text(product.storage),
        dataFormat: text(product.data_format),
        columns: fields.map(column),
      }],
      "x-useCases": useCases.map((entry) => ({ id: entry.id, title: entry.title, domain: entry.domain, owner: entry.owner, url: httpsUri(entry.url), kpi: entry.kpi, exampleQuestions: entry.example_questions })),
    },
  };
  return compact(document);
}
