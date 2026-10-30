import assert from "node:assert/strict";
import { test } from "node:test";
import { BigQuery } from "@google-cloud/bigquery";
import { openBigQueryRepository } from "../src/repository/bigquery.js";

function standIn() {
  const calls = [];
  return { calls, client: { query: async (job) => { calls.push(job); return [[]]; } } };
}

const hostile = "x' OR 1=1; DROP TABLE t; --";

test("the BigQuery adapter creates only _portal_ tables in the client's warehouse dataset, idempotently", async () => {
  const { calls, client } = standIn();
  const repository = openBigQueryRepository({ PROFILE: "meridian", BIGQUERY_DATASET_PREFIX: "kge" }, { client });
  await repository.listProducts(["retail"]);
  const created = calls.filter((job) => job.query.startsWith("CREATE TABLE"));
  assert.equal(created.length, 15);
  for (const job of created) assert.match(job.query, /^CREATE TABLE IF NOT EXISTS `kge_meridian_warehouses\._portal_[a-z_]+`/);
  for (const job of calls.filter((item) => item.query.startsWith("MERGE"))) assert.match(job.query, /WHEN NOT MATCHED THEN INSERT ROW$/);
  assert.ok(calls.some((job) => job.query === "ALTER TABLE `kge_meridian_warehouses._portal_requests` ADD COLUMN IF NOT EXISTS decided_by STRING, ADD COLUMN IF NOT EXISTS decided_at TIMESTAMP"));
});

test("an incompatible BigQuery schema refuses startup without dropping portal records", async () => {
  const calls = [];
  const client = { query: async (job) => {
    calls.push(job);
    return [job.query.includes("INFORMATION_SCHEMA.COLUMNS") ? [{ table_name: "_portal_products", column_name: "id", ordinal_position: 1 }] : []];
  } };
  const repository = openBigQueryRepository({ PROFILE: "meridian" }, { client });
  await assert.rejects(repository.listProducts(["retail"]), /needs a migration/);
  const dropped = calls.filter((job) => job.query.startsWith("DROP TABLE")).map((job) => job.query);
  assert.equal(dropped.length, 0);
  assert.ok(dropped.every((query) => /_portal_(products|fields|lineage|quality_rules|catalogue_sync)`/.test(query)));
  assert.ok(dropped.every((query) => !/_portal_(users|favourites|views|cart|requests|feedback|publish_status)/.test(query)));
});

test("an incompatible BigQuery schema is never rebuilt from a version marker", async () => {
  const calls = [];
  const client = { query: async (job) => {
    calls.push(job);
    if (job.query.includes("SELECT derived_version")) return [[{ derived_version: 0 }]];
    if (job.query.includes("INFORMATION_SCHEMA.COLUMNS")) return [[{ table_name: "_portal_products", column_name: "id", ordinal_position: 1 }]];
    return [[]];
  } };
  const repository = openBigQueryRepository({ PROFILE: "meridian" }, { client });
  await assert.rejects(repository.listProducts(["retail"]), /needs a migration/);
  assert.equal(calls.filter((job) => job.query.startsWith("DROP TABLE")).length, 0);
});

test("BigQuery search lineage is filtered against readable units rather than matches", async () => {
  const calls = [];
  const product = { id: "retail-accounts", unit_id: "retail", table_name: "accounts", name: "Retail Accounts", description: "accounts", product_owner: "owner", business_owner: "owner", badges: "[]", quality_score: 1, last_refresh: "now", ingested_at: "now", subscribe_count: 0, access_count: 0, view_count: 0, baseline_rating: 0, domain: "retail", community: "retail", application: "ledger", product_type: "TABLE", record_count: 12000, path: "Meridian > Retail > Accounts", data_format: "Parquet", storage: "1.2 GB", last_dq_run: "now" };
  const client = { query: async (job) => {
    calls.push(job);
    if (job.query.startsWith("SELECT *") && job.query.includes("@search")) return [[product]];
    if (job.query.startsWith("SELECT direction")) return [[{ direction: "upstream", value: "retail-customer-master" }]];
    if (job.query.startsWith("SELECT id FROM")) return [[{ id: "retail-accounts" }, { id: "retail-customer-master" }]];
    return [[]];
  } };
  const repository = openBigQueryRepository({ PROFILE: "meridian" }, { client });
  const [account] = await repository.listProducts(["retail"], "Accounts");
  assert.deepEqual(account.lineage.upstream, ["retail-customer-master"]);
  const readable = calls.find((job) => job.query.startsWith("SELECT id FROM"));
  assert.deepEqual(readable.params, { units: ["retail"] });
  assert.doesNotMatch(readable.query, /search/);
});

test("every BigQuery statement takes its values as parameters, never in its text", async () => {
  const { calls, client } = standIn();
  const repository = openBigQueryRepository({ PROFILE: "meridian" }, { client });
  const units = [hostile];
  await Promise.all([
    repository.listProducts(units, hostile), repository.listProducts(units, hostile, { unpublished: true }), repository.product(hostile, units),
    repository.fields(hostile, units), repository.lineage(hostile, units), repository.role(hostile), repository.setRole(hostile, hostile),
    repository.setPublished(hostile, true), repository.favourites(hostile, units), repository.addFavourite(hostile, hostile),
    repository.removeFavourite(hostile, hostile), repository.cart(hostile, units), repository.addCart(hostile, hostile),
    repository.removeCart(hostile, hostile), repository.emptyCart(hostile), repository.request(hostile, hostile, hostile),
    repository.requests(hostile, units), repository.feedback(hostile, hostile, 3, hostile), repository.averageFeedback(hostile, units),
    repository.askFeedback(hostile, 1, 2, { product: hostile }, "up", hostile),
    repository.view(hostile, hostile), repository.queue(units), repository.pending(hostile, units), repository.decide(hostile, hostile, hostile),
  ]);
  const statements = calls.filter((job) => !/^(CREATE|ALTER) TABLE/.test(job.query));
  assert.ok(statements.length >= 20);
  for (const job of statements) {
    assert.doesNotMatch(job.query, /DROP TABLE|OR 1=1/, job.query);
    if (/@[a-z]/.test(job.query)) assert.ok(job.params && Object.keys(job.params).length > 0, job.query);
  }
});

test("catalogue reads are filtered on readable units with parameters", async () => {
  const { calls, client } = standIn();
  const repository = openBigQueryRepository({ PROFILE: "meridian" }, { client });
  calls.length = 0;
  await repository.listProducts(["retail"], hostile);
  const job = calls.findLast((item) => item.query.includes("_portal_products") && item.query.startsWith("SELECT"));
  assert.match(job.query, /unit_id IN UNNEST\(@units\)/);
  assert.deepEqual(job.params.units, ["retail"]);
  assert.deepEqual(job.types, { units: ["STRING"] });
});

test("lists of a person's own rows are filtered on their readable units", async () => {
  const { calls, client } = standIn();
  const repository = openBigQueryRepository({ PROFILE: "meridian" }, { client });
  await Promise.all([repository.favourites("person", ["retail"]), repository.cart("person", ["retail"]), repository.requests("person", ["retail"])]);
  const owned = calls.filter((job) => /_portal_(favourites|cart|requests)` t JOIN/.test(job.query));
  assert.equal(owned.length, 3);
  for (const job of owned) {
    assert.match(job.query, /JOIN `kge_meridian_warehouses\._portal_products` p ON p\.id=t\.product_id AND p\.unit_id IN UNNEST\(@units\)/);
    assert.deepEqual(job.params, { principal: "person", units: ["retail"] });
    assert.deepEqual(job.types, { units: ["STRING"] });
  }
});

test("BigQuery seeds new records without deleting business metadata and parameterizes metadata writes", async () => {
  const { catalogue } = await import("./fixtures/catalogue.js");
  const { calls, client } = standIn();
  const repository = openBigQueryRepository({ PROFILE: "meridian" }, { client });
  await repository.syncCatalogue("seed", catalogue.products, true);
  const seed = calls.find((job) => job.query.startsWith("BEGIN TRANSACTION") && job.params?.products);
  assert.ok(seed);
  assert.doesNotMatch(seed.query, /DELETE FROM .*_portal_(products|fields|quality_rules|lineage)`/);
  for (const parameter of ["products", "fields", "quality", "lineage"]) {
    assert.ok(seed.query.includes(`USING (SELECT * FROM UNNEST(@${parameter})) s`), parameter);
  }
  const users = calls.find((job) => job.params?.rows);
  assert.match(users.query, /USING \(SELECT \* FROM UNNEST\(@rows\)\) s/);
  await repository.updateMetadata("retail-accounts", { description: hostile, fields: [{ name: "amount", business_name: hostile }] });
  const update = calls.findLast((job) => job.query.startsWith("BEGIN TRANSACTION"));
  assert.doesNotMatch(update.query, /OR 1=1/);
  assert.ok(Object.values(update.params).includes(hostile));
  await repository.setGoverned(catalogue);
  const overlay = calls.findLast((job) => job.query.startsWith("MERGE"));
  assert.match(overlay.query, /_portal_governed/);
  assert.deepEqual(JSON.parse(overlay.params.payload), catalogue);
  await assert.rejects(repository.updateMetadata("retail-accounts", { quality_score: 100 }), /portal-owned/);
});

test("a failed user seed is shared by concurrent callers and retried on the next call", async () => {
  const { calls, client } = standIn();
  const query = client.query;
  let seeds = 0;
  client.query = async (job) => {
    if (job.params?.rows && ++seeds === 1) throw new Error("seed temporarily unavailable");
    return query(job);
  };
  const repository = openBigQueryRepository({ PROFILE: "meridian" }, { client });
  const failed = await Promise.allSettled([repository.role("person"), repository.users()]);
  assert.ok(failed.every((result) => result.status === "rejected" && /seed temporarily unavailable/.test(result.reason.message)));
  assert.equal(seeds, 1);
  assert.equal(await repository.role("person"), "viewer");
  await Promise.all([repository.users(), repository.catalogueRelease()]);
  assert.equal(seeds, 2);
  assert.equal(calls.filter((job) => job.query.startsWith("CREATE TABLE") && job.query.includes("_portal_schema`")).length, 2);
});

test("BigQuery can seed a product without fields or lineage without untyped empty array parameters", async () => {
  const { catalogue } = await import("./fixtures/catalogue.js");
  const { calls, client } = standIn();
  const repository = openBigQueryRepository({ PROFILE: "meridian" }, { client });
  const product = { ...catalogue.products[0], fields: [], lineage: { origin_paths: [], destination_paths: [], upstream: [], downstream: [] } };
  await repository.syncCatalogue("empty-children", [product], true);
  const seed = calls.find((job) => job.params?.products);
  assert.ok(seed);
  assert.doesNotMatch(seed.query, /UNNEST\(@(fields|quality|lineage)\)/);
  assert.deepEqual(Object.keys(seed.params).sort(), ["catalogue", "products", "release"]);
});

// The query parameters the BigQuery client library would send for a recorded
// job, built by the library itself (no network): name -> parameterType.
const sent = (job) => new BigQuery({ projectId: "test-project" }).buildQueryParams_(job.params, job.types).params;
const sentTypes = (job) => Object.fromEntries(sent(job).map((parameter) => [parameter.name, parameter.parameterType]));
const structTypes = (type) => Object.fromEntries(type.arrayType.structTypes.map((field) => [field.name, field.type.type]));

test("a catalogue whose first scores are whole numbers still binds every float column as FLOAT64 (#159)", async () => {
  const { catalogue } = await import("./fixtures/catalogue.js");
  const products = catalogue.products.map((product, index) => ({
    ...product,
    quality_score: [0, 33.3, 16.7, 87.5][index],
    rating: [4, 4.5, 3.25, 5][index],
    fields: product.fields.map((field, ordinal) => ({ ...field, quality: { ...field.quality, fail_percent: index + ordinal === 0 ? 0 : 0.5 + index + ordinal / 10 } })),
  }));
  for (const seedOnly of [false, true]) {
    const { calls, client } = standIn();
    const repository = openBigQueryRepository({ PROFILE: "meridian" }, { client });
    assert.equal(await repository.syncCatalogue("float-release", products, seedOnly), true);
    const sync = calls.find((job) => job.params?.products);
    const types = sentTypes(sync);
    const product = structTypes(types.products);
    assert.equal(product.quality_score, "FLOAT64");
    assert.equal(product.baseline_rating, "FLOAT64");
    assert.deepEqual(Object.keys(sync.types).sort(), Object.keys(sync.params).sort());
    assert.deepEqual(Object.keys(product), Object.keys(sync.params.products[0]));
    for (const column of ["subscribe_count", "access_count", "view_count", "record_count"]) assert.equal(product[column], "INT64", column);
    for (const column of ["id", "unit_id", "table_name", "badges", "last_refresh", "path", "last_dq_run"]) assert.equal(product[column], "STRING", column);
    const quality = structTypes(types.quality);
    assert.deepEqual(Object.keys(quality), Object.keys(sync.params.quality[0]));
    assert.equal(quality.fail_percent, "FLOAT64");
    for (const name of ["fields", "lineage"]) assert.deepEqual(Object.keys(structTypes(types[name])), Object.keys(sync.params[name][0]), name);
    assert.deepEqual(structTypes(types.fields), { product_id: "STRING", ordinal: "INT64", name: "STRING", business_name: "STRING", data_type: "STRING", description: "STRING", terms: "STRING" });
    assert.deepEqual(structTypes(types.lineage), { product_id: "STRING", direction: "STRING", ordinal: "INT64", value: "STRING" });
    assert.equal(types.catalogue.type, "STRING");
    assert.equal(types.release.type, "STRING");
    assert.deepEqual(sent(sync).find((parameter) => parameter.name === "products").parameterValue.arrayValues.map((row) => row.structValues.quality_score.value), [0, 33.3, 16.7, 87.5]);
  }
});

test("a catalogue without fields or lineage types only the parameters it sends", async () => {
  const { catalogue } = await import("./fixtures/catalogue.js");
  const { calls, client } = standIn();
  const repository = openBigQueryRepository({ PROFILE: "meridian" }, { client });
  const product = { ...catalogue.products[0], quality_score: 0, fields: [], lineage: { origin_paths: [], destination_paths: [], upstream: [], downstream: [] } };
  await repository.syncCatalogue("bare", [product]);
  const sync = calls.find((job) => job.params?.products);
  assert.equal(structTypes(sentTypes(sync).products).quality_score, "FLOAT64");
  assert.deepEqual(Object.keys(sync.types).sort(), ["catalogue", "products", "release"]);
});

test("an empty or absent catalogue still writes typed release and catalogue parameters", async () => {
  for (const [products, seedOnly] of [[[], false], [[], true], [null, false]]) {
    const { calls, client } = standIn();
    const repository = openBigQueryRepository({ PROFILE: "meridian" }, { client });
    assert.equal(await repository.syncCatalogue("empty", products, seedOnly), true);
    const sync = calls.find((job) => job.query.startsWith("BEGIN TRANSACTION"));
    assert.deepEqual(sync.types, { catalogue: "STRING", release: "STRING" });
    assert.deepEqual(Object.keys(sync.params).sort(), ["catalogue", "release"]);
  }
});

test("a release the portal already holds is not synced again and writes nothing", async () => {
  const { catalogue } = await import("./fixtures/catalogue.js");
  const calls = [];
  const client = { query: async (job) => {
    calls.push(job);
    return [job.query.startsWith("SELECT release_id") ? [{ release_id: catalogue.release }] : []];
  } };
  const repository = openBigQueryRepository({ PROFILE: "meridian" }, { client });
  assert.equal(await repository.syncCatalogue(catalogue.release, catalogue.products), false);
  assert.equal(await repository.syncCatalogue(catalogue.release, catalogue.products, true), false);
  assert.ok(calls.some((job) => job.query.startsWith("SELECT release_id")));
  assert.equal(calls.filter((job) => /^(BEGIN TRANSACTION|INSERT|DELETE|UPDATE)/.test(job.query) || (job.query.startsWith("MERGE") && !/_portal_(schema|users)`/.test(job.query))).length, 0);
});
