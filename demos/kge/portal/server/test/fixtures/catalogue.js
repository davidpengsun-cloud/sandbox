const field = (name) => ({ name, business_name: name.replaceAll("_", " "), data_type: "VARCHAR", description: `${name} description`, terms: [name], classification: "", pii: false, quality: { dimension: "Completeness", rule: `${name} is present`, rule_type: "completeness", fail_percent: 1, description: "" } });
const product = (id, unit, name, upstream = []) => ({ id, unit_id: unit, table: id.split("-").slice(1).join("_"), name, description: `${name} governed description`, product_owner: "Steward", business_owner: "Administrator", badges: ["Gold", "Certified"], quality_score: 96, last_refresh: "2026-10-20T06:00:00Z", ingested_at: "2026-10-20T06:30:00Z", counts: { subscribe: 12, access: 24, views: 48 }, rating: 4.2, domain: `${unit} domain`, community: `${unit} community`, application: `${unit} application`, product_type: "TABLE", records: 12000, path: `Meridian > ${unit} > ${name}`, data_format: "Parquet", storage: "1.2 GB", last_dq_run: "2026-10-20T06:00:00Z", asset_type: "Structured", usage_purpose: "Analytical", dataset_type: "Master data", APM_ID: `APM-${unit}`, project: "", product_owner_email: "", business_owner_email: "", refresh_cadence: "", schema: "", fields: [field("id"), field("amount")], lineage: { origin_paths: [`${unit}/source`], destination_paths: [`${unit}/served`], upstream, downstream: [] } });

export const catalogue = {
  contract_version: "1",
  release: "fixture-release",
  has_catalogue: true,
  products: [
    product("retail-accounts", "retail", "Retail Accounts", ["retail-customer-master"]),
    product("sme-lending", "sme", "SME Lending"),
    product("corporate-exposure", "corporate", "Corporate Exposure"),
    product("wealth-holdings", "wealth", "Wealth Holdings"),
  ],
};
