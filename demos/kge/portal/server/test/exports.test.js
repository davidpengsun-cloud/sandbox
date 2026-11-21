import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { after, before, test } from "node:test";
import Ajv2019 from "ajv/dist/2019.js";
import Ajv2020 from "ajv/dist/2020.js";
import YAML from "yaml";
import { createApp } from "../src/app.js";
import { ODCS_VERSION, odcsDocument, useCaseOf } from "../src/exports/odcs.js";
import { ODPS_VERSION, odpsDocument } from "../src/exports/odps.js";
import { openSqliteRepository } from "../src/repository/sqlite.js";

// The published schemas (fixtures/spec/README.md), compiled in Ajv's strict
// mode but for strictTypes, which the published schemas do not meet. Ajv has
// no format validators of its own; these check the five formats they use.
const formats = {
  uri: (value) => URL.canParse(value),
  "uri-reference": (value) => URL.canParse(value, "https://example.com/"),
  date: /^\d{4}-\d{2}-\d{2}$/,
  "date-time": (value) => /^\d{4}-\d{2}-\d{2}T/.test(value) && !Number.isNaN(Date.parse(value)),
  email: /^[^@\s]+@[^@\s]+\.[^@\s]+$/,
};
const spec = (name) => JSON.parse(readFileSync(new URL(`./fixtures/spec/${name}`, import.meta.url), "utf8"));
const odpsSchema = spec("odps-v4.2.json");
const odcsSchema = spec("odcs-v3.2.0.json");
const validOdps = new Ajv2020({ allErrors: true, strictTypes: false, formats }).compile(odpsSchema);
const validOdcs = new Ajv2019({ allErrors: true, strictTypes: false, formats }).compile(odcsSchema);
const assertValid = (validate, document) => assert.ok(validate(document), JSON.stringify(validate.errors, null, 2));

// A synthetic catalogue: invented units, assets and use cases. The reader
// reads the harbour unit only, so the orchard asset is never exported to them.
const lantern = {
  id: "uc-lantern-count", title: "Lantern count", objective: "Harbour staff count lit lanterns each evening.", domain: "Harbour", owner: "Harbour master", url: "https://example.com/lantern-count",
  kpi: { name: "Lit lanterns", definition: "Lanterns lit at dusk.", grain: "Day", unit: "lanterns" }, example_questions: ["How many lanterns were lit last night?"],
};
const orchardOnly = { id: "uc-pear-harvest", title: "Pear harvest", objective: "Growers track pears picked." };
const field = (name, extra = {}) => ({ name, business_name: name.replaceAll("_", " "), data_type: "STRING", description: `${name} as recorded`, terms: [name], classification: "internal", pii: false, quality: [], ...extra });
const product = (id, unit, name, useCases, extra = {}) => ({
  id, unit_id: unit, table: id.split("-").slice(1).join("_"), name, description: `${name}, as the catalogue describes it`, product_owner: "Quay steward", business_owner: "Quay lead",
  product_owner_email: "steward@example.com", business_owner_email: "", badges: ["Gold"], quality_score: 91, last_refresh: "2026-11-01T05:00:00Z", ingested_at: "2026-11-01T05:30:00Z",
  counts: { subscribe: 1, access: 2, views: 3 }, rating: 4, domain: `${unit} domain`, community: `${unit} community`, application: `${unit} application`, product_type: "Table",
  records: 640, path: `${unit} > ${name}`, data_format: "Parquet", storage: "4 MB", last_dq_run: "2026-11-01T05:00:00Z", asset_type: "Structured", usage_purpose: "Analytical",
  dataset_type: "Reference data", APM_ID: "", project: "", refresh_cadence: "Daily", schema: "", use_cases: useCases,
  fields: [
    field("lantern_id", { quality: [{ dimension: "Completeness", rule: "lantern_id is present", rule_type: "completeness", fail_percent: 0.5, description: "Every row names its lantern." }] }),
    field("keeper_name", { classification: "confidential", pii: true, quality: { dimension: "", rule: "No field quality rule is recorded.", rule_type: "Not_Assessed", fail_percent: null, description: "" } }),
  ],
  lineage: { origin_paths: [], destination_paths: [], upstream: [], downstream: [] }, ...extra,
});
const products = [
  product("harbour-lanterns", "harbour", "Harbour Lanterns", [lantern]),
  product("harbour-quays", "harbour", "Harbour Quays", [lantern], { product_type: "Documents", refresh_cadence: "", fields: [] }),
  product("orchard-pears", "orchard", "Orchard Pears", [lantern, orchardOnly]),
];

const repository = openSqliteRepository({ PORTAL_SQLITE_PATH: ":memory:" });
const kge = { identity: async () => ({ principal: "reader", units: ["harbour"] }) };
const { app } = createApp({ repository, kge, catalogueSync: false, env: {}, uiPath: "not-present" });
let server;
let base;
before(async () => {
  await repository.syncCatalogue("export-release-7", structuredClone(products));
  server = app.listen(0, "127.0.0.1");
  await new Promise((resolve) => server.once("listening", resolve));
  base = `http://127.0.0.1:${server.address().port}`;
});
after(() => { server.close(); repository.close(); });

const download = async (path) => {
  const response = await fetch(`${base}${path}`);
  return { status: response.status, type: response.headers.get("content-type"), disposition: response.headers.get("content-disposition"), body: await response.text() };
};

test("each export names its standard's version once, as the published schema spells it", () => {
  assert.equal(ODPS_VERSION, "4.2");
  assert.equal(ODCS_VERSION, "v3.2.0");
  assert.match(odpsSchema.title, new RegExp(`v${ODPS_VERSION.replace(".", "\\.")}`));
  assert.ok(odcsSchema.properties.apiVersion.enum.includes(ODCS_VERSION));
  assert.equal(odcsSchema.properties.apiVersion.default, ODCS_VERSION);
  assert.equal(validOdps({ schema: "not a link", version: ODPS_VERSION, product: {} }), false);
  assert.equal(validOdcs({ apiVersion: "v9.9.9", kind: "DataContract", id: "x", version: "1" }), false);
});

test("an asset the person reads downloads as <asset id>.odps.yaml, a valid ODPS document of its catalogue data", async () => {
  const sent = await download("/product/harbour-lanterns/odps");
  assert.equal(sent.status, 200);
  assert.match(sent.type, /^application\/yaml/);
  assert.equal(sent.disposition, 'attachment; filename="harbour-lanterns.odps.yaml"');
  const document = YAML.parse(sent.body);
  assertValid(validOdps, document);
  assert.equal(document.version, "4.2");
  assert.deepEqual(document.product.details.en, {
    name: "Harbour Lanterns", productID: "harbour-lanterns", visibility: "internal", status: "production", type: "dataset", categories: ["harbour domain"],
    description: "Harbour Lanterns, as the catalogue describes it", tags: ["GOLD"], outputFileFormats: ["Parquet"],
    useCases: [{ useCase: { useCaseTitle: "Lantern count", useCaseDescription: "Harbour staff count lit lanterns each evening.", useCaseURL: "https://example.com/lantern-count" } }],
  });
  assert.deepEqual(document.product.dataAccess, { default: { outputPortType: "SQL", name: { en: "lanterns" } } });
  assert.deepEqual(document.product.dataQuality.executable[0].spec.rules, [{ field: "lantern_id", rule: "lantern_id is present", ruleType: "completeness", dimension: "Completeness", description: "Every row names its lantern.", failPercent: 0.5 }]);
  assert.deepEqual(document.product.SLA, { declarative: { updateFrequency: { dimensions: [{ dimension: "updateFrequency", objective: "Daily" }] } } });
  assert.deepEqual(document.product["x-owner"], { productOwner: "Quay steward", productOwnerEmail: "steward@example.com", businessOwner: "Quay lead" });
  assert.deepEqual(document.product["x-domain"], { community: "harbour community", application: "harbour application" });
  assert.equal(document.product["x-asset"].qualityScore, 91);
  const [table] = document.product["x-tables"];
  assert.deepEqual({ ...table, columns: table.columns.map((column) => column.name) }, { unit: "harbour", table: "lanterns", path: "harbour > Harbour Lanterns", records: 640, storage: "4 MB", dataFormat: "Parquet", columns: ["lantern_id", "keeper_name"] });
  assert.deepEqual(table.columns[1], { name: "keeper_name", businessName: "keeper name", description: "keeper_name as recorded", dataType: "STRING", classification: "Confidential", pii: true, terms: ["keeper_name"] });
  assert.deepEqual(document.product["x-useCases"], [{ id: "uc-lantern-count", title: "Lantern count", domain: "Harbour", owner: "Harbour master", url: "https://example.com/lantern-count", kpi: lantern.kpi, exampleQuestions: lantern.example_questions }]);
  assert.doesNotMatch(sent.body, /orchard|Orchard/);
});

test("an asset the person does not read, or no asset, is not exported", async () => {
  for (const id of ["orchard-pears", "no-such-asset"]) {
    const sent = await download(`/product/${id}/odps`);
    assert.equal(sent.status, 404);
    assert.equal(sent.disposition, null);
    assert.deepEqual(JSON.parse(sent.body), { message: "Product not found." });
  }
});

test("a use case downloads as <use case id>.odcs.yaml, a valid ODCS contract of its readable linked assets only", async () => {
  const sent = await download("/use-cases/uc-lantern-count/odcs");
  assert.equal(sent.status, 200);
  assert.match(sent.type, /^application\/yaml/);
  assert.equal(sent.disposition, 'attachment; filename="uc-lantern-count.odcs.yaml"');
  const contract = YAML.parse(sent.body);
  assertValid(validOdcs, contract);
  assert.deepEqual({ ...contract, schema: undefined }, {
    apiVersion: "v3.2.0", kind: "DataContract", id: "uc-lantern-count", name: "Lantern count", version: "export-release-7", domain: "Harbour",
    description: { purpose: "Harbour staff count lit lanterns each evening." },
    authoritativeDefinitions: [{ url: "https://example.com/lantern-count", type: "businessDefinition" }],
    context: { verifiedStatements: [{ question: "How many lanterns were lit last night?" }] },
    customProperties: [{ property: "owner", value: "Harbour master" }, { property: "kpi", value: lantern.kpi }],
    schema: undefined,
  });
  assert.deepEqual(contract.schema.map((object) => object.customProperties[0].value), ["harbour-lanterns", "harbour-quays"]);
  const [lanterns, quays] = contract.schema;
  assert.deepEqual({ ...lanterns, properties: undefined }, { name: "harbour.lanterns", physicalName: "lanterns", physicalType: "table", businessName: "Harbour Lanterns", description: "Harbour Lanterns, as the catalogue describes it", customProperties: [{ property: "assetId", value: "harbour-lanterns" }, { property: "unit", value: "harbour" }], properties: undefined });
  assert.deepEqual(lanterns.properties[1], { name: "keeper_name", physicalName: "keeper_name", businessName: "keeper name", description: "keeper_name as recorded", physicalType: "STRING", classification: "Confidential", tags: ["keeper_name"], customProperties: [{ property: "pii", value: true }] });
  assert.equal(quays.properties, undefined);
  assert.doesNotMatch(sent.body, /orchard|Orchard/);
});

test("a use case only an unreadable asset lists, or none lists, is not exported", async () => {
  for (const id of ["uc-pear-harvest", "uc-none"]) {
    const sent = await download(`/use-cases/${id}/odcs`);
    assert.equal(sent.status, 404);
    assert.deepEqual(JSON.parse(sent.body), { message: "Use case not found." });
  }
});

test("a use case id two titles share exports the titled one; the builders keep a sparse record valid", () => {
  const twin = { id: "uc-lantern-count", title: "Lantern audit", objective: "Auditors recount lanterns." };
  const listed = [
    { id: "a-one", unit_id: "a", table: "", product_type: "", use_cases: [lantern] },
    { id: "a-two", unit_id: "a", table: "two", product_type: "API/MCP", use_cases: [twin, { ...lantern, url: "not a link" }] },
  ];
  assert.deepEqual(useCaseOf(listed, "uc-lantern-count", "Lantern audit").products.map((p) => p.id), ["a-two"]);
  assert.deepEqual(useCaseOf(listed, "uc-lantern-count").products.map((p) => p.id), ["a-one", "a-two"]);
  assert.equal(useCaseOf(listed, "uc-missing"), null);
  const sparse = odcsDocument(useCaseOf(listed, "uc-lantern-count", "Lantern audit"), undefined);
  assertValid(validOdcs, sparse);
  assert.equal(sparse.version, "unreleased");
  assert.equal(sparse.customProperties, undefined);
  assert.equal(sparse.schema[0].name, "a.two");
  for (const [record, port] of [[listed[0], "SQL"], [{ ...listed[1], published: false }, "API"], [{ id: "a-three", product_type: "Stream" }, "Stream"]]) {
    const document = odpsDocument(record);
    assertValid(validOdps, document);
    assert.equal(document.product.dataAccess.default.outputPortType, port);
    assert.equal(document.product.dataAccess.default.name.en, record.table || record.id);
  }
  const unpublished = odpsDocument({ ...listed[1], published: false });
  assert.equal(unpublished.product.details.en.status, "draft");
  assert.equal(unpublished.product["x-asset"].published, false);
  assert.equal(unpublished.product.details.en.useCases[1].useCase.useCaseURL, undefined);
  assert.equal(unpublished.product["x-useCases"][1].url, undefined);
  assert.equal(unpublished.product.dataQuality, undefined);
  assert.equal(unpublished.product.SLA, undefined);
});

test("two linked assets with one table name in different units are two schema objects named <unit>.<table>", () => {
  const listed = [
    { id: "harbour-ledger", unit_id: "harbour", table: "ledger", product_type: "Table", use_cases: [lantern] },
    { id: "orchard-ledger", unit_id: "orchard", table: "ledger", product_type: "Table", use_cases: [lantern] },
    { id: "loose-notes", table: "", product_type: "Documents", use_cases: [lantern] },
  ];
  const contract = odcsDocument(useCaseOf(listed, "uc-lantern-count"), "r1");
  assertValid(validOdcs, contract);
  assert.deepEqual(contract.schema.map((object) => [object.name, object.physicalName]), [["harbour.ledger", "ledger"], ["orchard.ledger", "ledger"], ["loose-notes", undefined]]);
  assert.deepEqual(contract.schema[2].customProperties, [{ property: "assetId", value: "loose-notes" }]);
});

test("a use case's address is exported only when it is an https address, as the page shows it", () => {
  for (const url of ["javascript:alert(1)", "see the harbour wiki", "http://example.com/lantern-count", "https://example.com/two words"]) {
    const record = { id: "harbour-lanterns", unit_id: "harbour", table: "lanterns", use_cases: [{ ...lantern, url }] };
    const contract = odcsDocument(useCaseOf([record], "uc-lantern-count"), "r1");
    assertValid(validOdcs, contract);
    assert.equal(contract.authoritativeDefinitions, undefined, url);
    const document = odpsDocument(record);
    assertValid(validOdps, document);
    assert.equal(document.product.details.en.useCases[0].useCase.useCaseURL, undefined, url);
    assert.equal(document.product["x-useCases"][0].url, undefined, url);
  }
  const record = { id: "harbour-lanterns", unit_id: "harbour", table: "lanterns", use_cases: [{ ...lantern, url: " https://example.com/lantern-count " }] };
  assert.deepEqual(odcsDocument(useCaseOf([record], "uc-lantern-count"), "r1").authoritativeDefinitions, [{ url: "https://example.com/lantern-count", type: "businessDefinition" }]);
  assert.equal(odpsDocument(record).product["x-useCases"][0].url, "https://example.com/lantern-count");
});
