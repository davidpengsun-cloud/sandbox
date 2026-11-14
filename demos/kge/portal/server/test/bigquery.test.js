import assert from "node:assert/strict";
import { test } from "node:test";
import { BigQuery } from "@google-cloud/bigquery";
import { openBigQueryRepository } from "../src/repository/bigquery.js";
import { checked } from "./googlesql.js";

function standIn() {
  const calls = [];
  return { calls, client: checked({ query: async (job) => { calls.push(job); return [[]]; } }) };
}

const hostile = "x' OR 1=1; DROP TABLE t; --";

test("the BigQuery adapter creates only _portal_ tables in the client's warehouse dataset, idempotently", async () => {
  const { calls, client } = standIn();
  const repository = openBigQueryRepository({ PROFILE: "meridian", BIGQUERY_DATASET_PREFIX: "kge" }, { client });
  await repository.listProducts(["retail"]);
  const created = calls.filter((job) => job.query.startsWith("CREATE TABLE"));
  assert.equal(created.length, 19);
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
    if (job.query.startsWith("SELECT\nARRAY")) return [[{ products: [product], visible_ids: ["retail-accounts", "retail-customer-master"], governed_payload: null }]];
    if (job.query.startsWith("SELECT product_id,direction")) return [[{ product_id: product.id, direction: "upstream", value: "retail-customer-master" }]];
    return [[]];
  } };
  const repository = openBigQueryRepository({ PROFILE: "meridian" }, { client });
  const [account] = await repository.listProducts(["retail"], "Accounts");
  assert.deepEqual(account.lineage.upstream, ["retail-customer-master"]);
  const readable = calls.find((job) => job.query.startsWith("SELECT\nARRAY"));
  assert.deepEqual(readable.params.units, ["retail"]);
  assert.match(readable.query, /ARRAY\(SELECT id FROM [^\n]+WHERE unit_id IN UNNEST\(@units\)\) visible_ids/);
});

test("every BigQuery statement takes its values as parameters, never in its text", async () => {
  const { calls, client } = standIn();
  const repository = openBigQueryRepository({ PROFILE: "meridian" }, { client });
  const units = [hostile];
  await Promise.all([
    repository.listProducts(units, hostile), repository.listProducts(units, hostile, { unpublished: true }), repository.product(hostile, units),
    repository.fields(hostile, units), repository.lineage(hostile, units), repository.role("quoted'username"), repository.setRole("quoted'username", "publisher"),
    repository.setPublished(hostile, true), repository.promptDirection(), repository.setPromptDirection(hostile, hostile), repository.favourites(hostile, units), repository.addFavourite(hostile, hostile),
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
  assert.ok(!calls.some((job) => job.params?.rows));
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

test("a failed schema initialization is shared by concurrent callers and retried on the next call", async () => {
  const { calls, client } = standIn();
  const query = client.query;
  let seeds = 0;
  client.query = async (job) => {
    if (job.query.startsWith("MERGE") && job.query.includes("_portal_schema`") && ++seeds === 1) throw new Error("seed temporarily unavailable");
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

test("BigQuery persists unavailable figures compatibly while the governed payload keeps them blank", async () => {
  const { catalogue } = await import("./fixtures/catalogue.js");
  for (const seedOnly of [false, true]) {
    const calls = [];
    let products = [], payload, release;
    const client = { query: async (job) => {
      calls.push(job);
      if (job.params?.products) {
        assert.ok(job.params.products.every((product) => Number.isInteger(product.record_count)));
        products = seedOnly && products.length ? products : job.params.products;
        payload = job.params.catalogue;
        release = job.params.release;
      }
      if (job.query.startsWith("SELECT release_id")) return [[...(release ? [{ release_id: release }] : [])]];
      if (job.query.startsWith("SELECT payload")) return [[{ payload }]];
      if (job.query.startsWith("SELECT\nARRAY")) return [[{ products: products.filter((row) => !("id" in job.params) || row.id === job.params.id), visible_ids: products.map(({ id }) => id), governed_payload: payload }]];
      return [[]];
    } };
    let repository = openBigQueryRepository({ PROFILE: "meridian" }, { client });
    const product = { ...catalogue.products[0], records: null, storage: "" };
    await repository.syncCatalogue("unavailable", [product], seedOnly);
    assert.equal(products[0].record_count, 0);
    assert.equal(JSON.parse(payload).products[0].records, null);
    assert.equal((await repository.product(product.id, [product.unit_id])).records, null);
    const sync = calls.find((job) => job.params?.products);
    assert.equal(structTypes(sentTypes(sync).products).record_count, "INT64");
    repository = openBigQueryRepository({ PROFILE: "meridian" }, { client });
    assert.equal((await repository.product(product.id, [product.unit_id])).records, null);
    await repository.syncCatalogue("available", [{ ...product, records: 37, storage: "123 B" }], seedOnly);
    assert.equal((await repository.product(product.id, [product.unit_id])).records, 37);
    await repository.syncCatalogue("unavailable-again", [product], seedOnly);
    const blank = await repository.product(product.id, [product.unit_id]);
    assert.equal(blank.records, null);
    assert.equal(blank.storage, "");
  }
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
    assert.deepEqual(structTypes(types.fields), { product_id: "STRING", ordinal: "INT64", name: "STRING", business_name: "STRING", data_type: "STRING", description: "STRING", terms: "STRING", classification: "STRING", pii: "BOOL" });
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
    const sync = calls.find((job) => job.query.startsWith("BEGIN TRANSACTION") && job.params?.release);
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
  const upgrades = calls.filter((job) => job.params?.name === "remove-code-seeded-grants");
  assert.equal(upgrades.length, 1);
  assert.equal(calls.filter((job) => !upgrades.includes(job) && (/^(BEGIN TRANSACTION|INSERT|DELETE|UPDATE)/.test(job.query) || (job.query.startsWith("MERGE") && !/_portal_(schema|users)`/.test(job.query)))).length, 0);
});

test("BigQuery upgrades catalogue columns additively and persists every attribute", async () => {
  const { catalogue } = await import("./fixtures/catalogue.js");
  const calls = [];
  const attributes = { asset_type: "Structured", usage_purpose: "Analytical", dataset_type: "Customer master", APM_ID: "APM-102", project: "Customer 360" };
  const contacts = { product_owner_email: "dana@example.com", business_owner_email: "lee@example.com" };
  let stored, storedField;
  const client = checked({ query: async (job) => {
    calls.push(job);
    if (job.query.includes("INFORMATION_SCHEMA.COLUMNS")) {
      const create = calls.find((call) => call.query.startsWith("CREATE TABLE") && call.query.includes("_portal_schema`"));
      assert.ok(create);
      const original = "id unit_id table_name name description product_owner business_owner badges quality_score last_refresh ingested_at subscribe_count access_count view_count baseline_rating domain community application product_type record_count path data_format storage last_dq_run";
      return [original.split(" ").map((column_name, index) => ({ table_name: "_portal_products", column_name, ordinal_position: index + 1 }))];
    }
    if (job.params?.products) { stored = job.params.products[0]; storedField = job.params.fields[0]; }
    if (job.query.startsWith("SELECT\nARRAY")) return [[{ products: stored ? [stored] : [], visible_ids: stored ? [stored.id] : [], governed_payload: null }]];
    if (job.query.startsWith("SELECT f.*")) return [storedField ? [{ ...storedField, dimension: "Completeness", rule_name: "present", rule_type: "completeness", fail_percent: 0 }] : []];
    return [[]];
  } });
  let repository = openBigQueryRepository({ PROFILE: "meridian" }, { client });
  const fields = catalogue.products[0].fields.map((field, index) => ({ ...field, classification: index ? "" : "Restricted" }));
  await repository.syncCatalogue("attributes", [{ ...catalogue.products[0], ...attributes, ...contacts, fields }], "follow");
  const upgrade = calls.find((job) => job.query.startsWith("ALTER TABLE") && job.query.includes("_portal_products`"));
  for (const column of [...Object.keys(attributes), ...Object.keys(contacts)]) assert.ok(upgrade.query.includes(`ADD COLUMN IF NOT EXISTS ${column} STRING`));
  assert.ok(calls.some((job) => job.query.includes("ALTER TABLE `kge_meridian_warehouses._portal_fields` ADD COLUMN IF NOT EXISTS classification STRING, ADD COLUMN IF NOT EXISTS pii BOOL")));
  const write = calls.find((job) => job.params?.products);
  for (const [key, value] of Object.entries(attributes)) {
    assert.equal(stored[key], value);
    assert.equal(structTypes(sentTypes(write).products)[key], "STRING");
    assert.ok(write.query.includes(`${key}=s.${key}`));
  }
  repository = openBigQueryRepository({ PROFILE: "meridian" }, { client });
  const product = await repository.product(stored.id, [stored.unit_id]);
  for (const [key, value] of Object.entries(attributes)) assert.equal(product[key], value);
  for (const [key, value] of Object.entries(contacts)) { assert.equal(stored[key], value); assert.equal(product[key], value); }
  assert.equal(storedField.classification, "Restricted");
  assert.ok(write.query.includes("WHEN MATCHED THEN UPDATE SET classification=s.classification"));
  assert.equal(product.fields[0].classification, "Restricted");
  assert.equal(calls.filter((job) => job.query.startsWith("ALTER TABLE") && job.query.includes("_portal_products`")).length, 2);
});

test("BigQuery retries transient rate-limit errors with backoff, does not retry DML on 503/backendError, and marks exhausted transient errors as 503 (#285)", async () => {
  const { client } = standIn();
  const base = client.query;
  let rateLimited = 0;
  let dmlRateLimited = 0;
  let dmlBackendFailed = 0;
  const delays = [];
  client.query = async (job) => {
    if (job.query.startsWith("SELECT DISTINCT role") && rateLimited < 2) {
      rateLimited += 1;
      throw Object.assign(new Error("Exceeded rate limits: too many api requests per user per method for this user_method"), { code: 403, errors: [{ reason: "rateLimitExceeded" }] });
    }
    if (job.query.startsWith("INSERT `kge_meridian_warehouses._portal_views`") && dmlRateLimited < 1) {
      dmlRateLimited += 1;
      throw Object.assign(new Error("Exceeded rate limits"), { code: 429, errors: [{ reason: "jobRateLimitExceeded" }] });
    }
    if (job.query.startsWith("INSERT `kge_meridian_warehouses._portal_requests`")) {
      dmlBackendFailed += 1;
      throw Object.assign(new Error("Backend error"), { code: 503, errors: [{ reason: "backendError" }] });
    }
    if (job.query.startsWith("SELECT release_id")) {
      throw Object.assign(new Error("Service unavailable"), { code: 503 });
    }
    return base(job);
  };
  const repository = openBigQueryRepository({ PROFILE: "meridian" }, { client, sleep: async (ms) => { delays.push(ms); } });
  assert.equal(await repository.role("person"), "viewer");
  assert.equal(rateLimited, 2);
  assert.equal(delays.length, 2);
  await repository.view("person", "retail-accounts");
  assert.equal(dmlRateLimited, 1);
  assert.equal(delays.length, 3);
  await assert.rejects(repository.request("person", "retail-accounts", "access"), (error) => error.status === 503 && /Backend error/.test(error.message));
  assert.equal(dmlBackendFailed, 1);
  assert.equal(delays.length, 3);
  await assert.rejects(repository.catalogueRelease(), (error) => error.status === 503 && /Service unavailable/.test(error.message));
  assert.equal(delays.length, 7);
});


test("BigQuery migrates hotfix columns, types and syncs them in every mode without adding read jobs", async () => {
  const { catalogue } = await import("./fixtures/catalogue.js");
  for (const mode of [false, true, "follow"]) {
    const calls = [];
    let stored, field, quality;
    const client = checked({ query: async (job) => {
      calls.push(job);
      if (job.query.includes("INFORMATION_SCHEMA.COLUMNS")) {
        const shapes = {
          products: "id unit_id table_name name description product_owner business_owner badges quality_score last_refresh ingested_at subscribe_count access_count view_count baseline_rating domain community application product_type record_count path data_format storage last_dq_run asset_type usage_purpose dataset_type APM_ID project product_owner_email business_owner_email",
          fields: "product_id ordinal name business_name data_type description terms classification",
          quality_rules: "product_id field_name dimension rule_name rule_type fail_percent",
        };
        return [Object.entries(shapes).flatMap(([name, columns]) => columns.split(" ").map((column_name, index) => ({ table_name: `_portal_${name}`, column_name, ordinal_position: index + 1 })))];
      }
      if (job.params?.products) { stored = job.params.products[0]; field = job.params.fields[0]; quality = job.params.quality[0]; }
      if (job.query.startsWith("SELECT\nARRAY")) return [[{ products: stored ? [stored] : [], visible_ids: stored ? [stored.id] : [], governed_payload: null }]];
      if (job.query.startsWith("SELECT f.*")) { const { description: rule_description, ...rule } = quality || {}; return [field ? [{ ...field, ...rule, rule_description }] : []]; }
      return [[]];
    } });
    const repository = openBigQueryRepository({ PROFILE: "meridian" }, { client });
    await repository.listProducts(["retail"]);
    assert.ok(calls.some((job) => job.query.includes("ADD COLUMN IF NOT EXISTS refresh_cadence STRING")));
    assert.ok(calls.some((job) => job.query.includes("ADD COLUMN IF NOT EXISTS pii BOOL")));
    assert.ok(calls.some((job) => job.query.includes("ALTER TABLE `kge_meridian_warehouses._portal_quality_rules` ADD COLUMN IF NOT EXISTS description STRING")));
    assert.ok(!calls.some((job) => /DROP TABLE/.test(job.query)));
    assert.equal(calls.filter((job) => job.query.startsWith("ALTER TABLE") && job.query.includes("ADD COLUMN IF NOT EXISTS refresh_cadence")).length, 1);
    const product = structuredClone(catalogue.products[0]);
    product.refresh_cadence = "Daily"; product.fields[0].pii = true; product.fields[0].quality.description = "Must be present.";
    await repository.syncCatalogue("hotfix", [product], mode);
    const write = calls.findLast((job) => job.params?.products);
    assert.equal(write.types.products[0].refresh_cadence, "STRING");
    assert.equal(write.types.fields[0].pii, "BOOL");
    assert.equal(write.types.quality[0].description, "STRING");
    if (mode) {
      for (const value of ["refresh_cadence=s.refresh_cadence", "pii=s.pii", "description=s.description"]) assert.ok(write.query.includes(value));
    }
    calls.length = 0;
    const actual = await repository.product(product.id, [product.unit_id]);
    assert.equal(calls.length, 3);
    assert.equal(actual.refresh_cadence, "Daily");
    assert.equal(actual.fields[0].pii, true);
    assert.equal(actual.fields[0].quality.description, "Must be present.");
    delete product.refresh_cadence; delete product.fields[0].pii; delete product.fields[0].quality.description;
    await repository.syncCatalogue("legacy", [product], mode);
    const legacy = await repository.product(product.id, [product.unit_id]);
    assert.equal(legacy.refresh_cadence, "");
    assert.equal(legacy.fields[0].pii, false);
    assert.equal(legacy.fields[0].quality.description, "");
  }
});


test("BigQuery migrates the rules table in the one column script: rule ordinal, schema, and fail % made nullable only where required", async () => {
  const { catalogue } = await import("./fixtures/catalogue.js");
  const jobs = {};
  for (const nullable of ["NO", "YES"]) {
    const calls = [];
    const client = checked({ query: async (job) => {
      calls.push(job);
      if (job.query.includes("INFORMATION_SCHEMA.COLUMNS")) return [["product_id", "field_name", "dimension", "rule_name", "rule_type", "fail_percent", "description"].map((column_name, index) => ({ table_name: "_portal_quality_rules", column_name, ordinal_position: index + 1, is_nullable: column_name === "fail_percent" ? nullable : "NO" }))];
      return [[]];
    } });
    const repository = openBigQueryRepository({ PROFILE: "meridian" }, { client });
    await repository.listProducts(["retail"]);
    const script = calls.filter((job) => job.query.includes("ADD COLUMN IF NOT EXISTS rule_ordinal"));
    assert.equal(script.length, 1);
    assert.ok(script[0].query.includes("ALTER TABLE `kge_meridian_warehouses._portal_quality_rules` ADD COLUMN IF NOT EXISTS description STRING, ADD COLUMN IF NOT EXISTS rule_ordinal INT64"));
    assert.ok(script[0].query.includes("ADD COLUMN IF NOT EXISTS schema STRING"));
    assert.equal(script[0].query.includes("ALTER TABLE `kge_meridian_warehouses._portal_quality_rules` ALTER COLUMN fail_percent DROP NOT NULL"), nullable === "NO");
    assert.ok(!calls.some((job) => /DROP TABLE|DROP COLUMN/.test(job.query)));
    jobs[nullable] = calls.length;
    const product = structuredClone(catalogue.products[0]);
    product.schema = "accounts";
    product.fields[0].quality = [product.fields[0].quality, { dimension: "Validity", rule: "id is well formed", rule_type: "custom", fail_percent: null, description: "" }];
    for (const mode of [false, true, "follow"]) {
      await repository.syncCatalogue(`rules-${mode}`, [product], mode);
      const write = calls.findLast((job) => job.params?.quality);
      assert.deepEqual(write.params.quality.map((rule) => [rule.field_name, rule.rule_ordinal, rule.fail_percent]), [["id", 0, 1], ["id", 1, null], ["amount", 0, 1]]);
      assert.equal(write.types.quality[0].rule_ordinal, "INT64");
      assert.equal(write.types.quality[0].fail_percent, "FLOAT64");
      assert.equal(write.params.products[0].schema, "accounts");
      if (mode) assert.ok(write.query.includes("t.product_id=s.product_id AND t.field_name=s.field_name AND IFNULL(t.rule_ordinal,0)=s.rule_ordinal"));
      if (mode) for (const value of ["path=s.path", "data_format=s.data_format", "schema=s.schema"]) assert.ok(write.query.includes(value));
    }
  }
  assert.equal(jobs.NO, jobs.YES);
});
