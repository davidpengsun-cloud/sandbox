import assert from "node:assert/strict";
import { mkdtemp, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";
import YAML from "yaml";
import { openCatalogueSource } from "../src/catalogue-source/index.js";
import { validateCatalogue } from "../src/catalogue-source/contract.js";
import { catalogue } from "./fixtures/catalogue.js";

const sourceFile = (name = "Retail Accounts") => ({
  format: 1,
  units: ["retail"],
  products: catalogue.products.slice(0, 1).map(({ unit_id, ...product }) => ({ ...product, id: `${unit_id}.${product.table}`, unit: unit_id, name })),
});

test("file catalogue adapter conforms, rejects invalid copies and detects content changes", async () => {
  const directory = await mkdtemp(join(tmpdir(), "portal-catalogue-"));
  const path = join(directory, "catalogue.yaml");
  await writeFile(path, YAML.stringify(sourceFile()));
  const source = openCatalogueSource({ PORTAL_CATALOGUE_PROVIDER: "file", PORTAL_CATALOGUE_FILE: path });
  const first = validateCatalogue(await source.load());
  assert.equal(first.products[0].unit_id, "retail");
  await writeFile(path, YAML.stringify({ ...sourceFile(), unexpected: true }));
  await assert.rejects(source.load(), /unknown keys/);
  await writeFile(path, YAML.stringify(sourceFile("Changed")));
  const changed = validateCatalogue(await source.load());
  assert.notEqual(changed.release, first.release);
  assert.equal(changed.products[0].name, "Changed");
});

test("catalogue factory chooses KGE, file, or gives a setting-level startup error", () => {
  const kge = { catalogueIdentity: async () => ({}), catalogue: async () => ({}) };
  assert.equal(typeof openCatalogueSource({ KGE_URL: "http://kge" }, kge).load, "function");
  assert.throws(() => openCatalogueSource({}), /KGE_URL or PORTAL_CATALOGUE_FILE/);
  assert.throws(() => openCatalogueSource({ PORTAL_CATALOGUE_PROVIDER: "file" }), /PORTAL_CATALOGUE_FILE/);
});

test("the standalone file named in README and Compose loads through the full contract into SQLite", async () => {
  const { readFile } = await import("node:fs/promises");
  const { fileURLToPath } = await import("node:url");
  const { openSqliteRepository } = await import("../src/repository/sqlite.js");
  const name = "examples/meridian-portal/catalogue.yaml";
  const root = new URL("../../../", import.meta.url);
  assert.ok((await readFile(new URL("README.md", root), "utf8")).includes(`PORTAL_CATALOGUE_FILE=${name}`));
  assert.ok((await readFile(new URL("kit/compose/compose.yaml", root), "utf8")).includes(`../${name}`));
  const source = openCatalogueSource({ PORTAL_CATALOGUE_FILE: fileURLToPath(new URL(name, root)) });
  const value = await source.load();
  assert.equal(value.products.length, 59);
  assert.ok(value.products.every((p) => p.data_format && p.fields.length && p.fields.every((f) => f.quality.rule)));
  const repository = openSqliteRepository({ PORTAL_SQLITE_PATH: ":memory:" });
  try {
    await repository.syncCatalogue(value.release, value.products);
    assert.equal(await repository.catalogueRelease(), value.release);
  } finally { repository.close(); }
});

test("an enrichment file is rejected with missing contract field names before storage", async () => {
  const { fileURLToPath } = await import("node:url");
  const source = openCatalogueSource({ PORTAL_CATALOGUE_FILE: fileURLToPath(new URL("../../../clients/meridian/catalogue.yaml", import.meta.url)) });
  await assert.rejects(source.load(), /required property 'last_refresh'.*required property 'data_type'/s);
});

test("file catalogue source metadata supplies APM_ID and rejects invalid source fields", async () => {
  const directory = await mkdtemp(join(tmpdir(), "portal-source-metadata-"));
  const path = join(directory, "catalogue.yaml");
  const value = sourceFile();
  value.sources = [{ id: "retail", APM_ID: "APM-RETAIL-001" }];
  delete value.products[0].APM_ID;
  await writeFile(path, YAML.stringify(value));
  const source = openCatalogueSource({ PORTAL_CATALOGUE_FILE: path });
  assert.equal((await source.load()).products[0].APM_ID, "APM-RETAIL-001");
  for (const [sources, error] of [
    [[{ id: "retail", APM_ID: 123 }], /sources\[0\].APM_ID must be a string/],
    [[{ id: "unknown" }], /must name a distinct declared unit/],
    [[{ id: "retail" }, { id: "retail" }], /must name a distinct declared unit/],
    [[{ id: "retail", unexpected: true }], /unknown keys: unexpected/],
  ]) {
    await writeFile(path, YAML.stringify({ ...value, sources }));
    await assert.rejects(source.load(), error);
  }
});

test("the full contract names every missing storage-bound field", () => {
  const candidate = structuredClone(catalogue);
  for (const key of ["last_refresh", "ingested_at", "records", "path", "data_format", "storage", "last_dq_run"]) {
    const value = structuredClone(candidate);
    delete value.products[0][key];
    assert.throws(() => validateCatalogue(value), new RegExp(`required property '${key}'`));
  }
  for (const key of ["data_type", "description"]) {
    const value = structuredClone(candidate);
    delete value.products[0].fields[0][key];
    assert.throws(() => validateCatalogue(value), new RegExp(`required property '${key}'`));
  }
});

test("the catalogue contract accepts unavailable counts without accepting invalid numbers", () => {
  const value = structuredClone(catalogue);
  value.products[0].records = null;
  value.products[0].storage = "";
  assert.equal(validateCatalogue(value), value);
  for (const records of [-1, 1.5, "0"]) {
    value.products[0].records = records;
    assert.throws(() => validateCatalogue(value), /records/);
  }
});

test("seed identifiers cannot shadow a released product in a different unit", async () => {
  const directory = await mkdtemp(join(tmpdir(), "portal-invalid-id-"));
  const path = join(directory, "catalogue.yaml");
  const value = sourceFile();
  value.products[0].id = "wealth.assets_under_advice";
  await writeFile(path, YAML.stringify(value));
  const source = openCatalogueSource({ PORTAL_CATALOGUE_FILE: path });
  await assert.rejects(source.load(), /id must equal unit.table/);
});

test("joined seeding quarantines a well-formed id absent from the released tables", async (context) => {
  const { createApp } = await import("../src/app.js");
  const { openSqliteRepository } = await import("../src/repository/sqlite.js");
  const directory = await mkdtemp(join(tmpdir(), "portal-seed-membership-"));
  const path = join(directory, "catalogue.yaml");
  const seed = sourceFile();
  seed.products.push({ ...seed.products[0], id: "retail.ghost", table: "ghost", name: "Ghost" });
  await writeFile(path, YAML.stringify(seed));
  const released = { ...catalogue, release: "current-release", products: [{ ...catalogue.products[0], id: "retail.accounts" }] };
  const repository = openSqliteRepository({ PORTAL_SQLITE_PATH: ":memory:" });
  const kge = { catalogueIdentity: async () => ({ release: released.release, has_catalogue: true }), catalogue: async () => released };
  const { stopCatalogueSync } = createApp({ repository, kge, env: { PORTAL_CATALOGUE_FILE: path }, uiPath: "not-present" });
  context.after(() => { stopCatalogueSync(); repository.close(); });
  const deadline = Date.now() + 5000;
  while (repository.catalogueRelease() !== released.release && Date.now() < deadline) await new Promise((resolve) => setTimeout(resolve, 10));
  assert.equal(repository.catalogueRelease(), released.release);
  assert.deepEqual(repository.listProducts(["retail"]).map((p) => p.id), ["retail.accounts"]);
});


test("invalid seed entries are logged and skipped without blocking a joined release", async (context) => {
  const { createApp } = await import("../src/app.js");
  const { openSqliteRepository } = await import("../src/repository/sqlite.js");
  const directory = await mkdtemp(join(tmpdir(), "portal-seed-invalid-"));
  const path = join(directory, "catalogue.yaml");
  const seed = sourceFile("Seed business name");
  seed.products.push({ ...seed.products[0], id: "retail.wrong" });
  seed.products.push({ ...seed.products[0], id: "retail.missing", table: "missing", records: "invalid" });
  await writeFile(path, YAML.stringify(seed));
  const released = { ...catalogue, release: "valid-release", products: [{ ...catalogue.products[0], id: "retail.accounts" }] };
  const repository = openSqliteRepository({ PORTAL_SQLITE_PATH: ":memory:" });
  const errors = [];
  context.mock.method(console, "error", (message) => errors.push(message));
  const kge = { catalogueIdentity: async () => ({ release: released.release, has_catalogue: true }), catalogue: async () => released };
  const { stopCatalogueSync } = createApp({ repository, kge, env: { PORTAL_CATALOGUE_FILE: path }, uiPath: "not-present" });
  context.after(() => { stopCatalogueSync(); repository.close(); });
  const deadline = Date.now() + 5000;
  while (repository.catalogueRelease() !== released.release && Date.now() < deadline) await new Promise((resolve) => setTimeout(resolve, 10));
  assert.equal(repository.catalogueRelease(), released.release);
  const products = repository.listProducts(["retail"]);
  assert.deepEqual(products.map((p) => p.id), ["retail.accounts"]);
  assert.equal(products[0].name, released.products[0].name);
  assert.equal(products[0].records, released.products[0].records);
  assert.ok(errors.some((message) => /seed.*products\[1\].*id must equal unit.table/i.test(message)));
  assert.ok(errors.some((message) => /seed.*products\[2\].*records/i.test(message)));
});

test("an unreadable seed document does not defer the joined release", async (context) => {
  const { createApp } = await import("../src/app.js");
  const { openSqliteRepository } = await import("../src/repository/sqlite.js");
  const directory = await mkdtemp(join(tmpdir(), "portal-seed-broken-"));
  const path = join(directory, "catalogue.yaml");
  await writeFile(path, "products: [broken");
  const repository = openSqliteRepository({ PORTAL_SQLITE_PATH: ":memory:" });
  const errors = [];
  context.mock.method(console, "error", (message) => errors.push(message));
  const kge = { catalogueIdentity: async () => ({ release: catalogue.release, has_catalogue: true }), catalogue: async () => catalogue };
  const { stopCatalogueSync } = createApp({ repository, kge, env: { PORTAL_CATALOGUE_FILE: path }, uiPath: "not-present" });
  context.after(() => { stopCatalogueSync(); repository.close(); });
  const deadline = Date.now() + 5000;
  while (repository.catalogueRelease() !== catalogue.release && Date.now() < deadline) await new Promise((resolve) => setTimeout(resolve, 10));
  assert.equal(repository.catalogueRelease(), catalogue.release);
  assert.equal(repository.listProducts(["retail"]).length, catalogue.products.filter((p) => p.unit_id === "retail").length);
  assert.ok(errors.some((message) => message.startsWith("Catalogue seed skipped:")));
});


test("file catalogue accepts cadence, boolean PII and rule descriptions but refuses unknown keys and wrong types", async () => {
  const directory = await mkdtemp(join(tmpdir(), "portal-hotfix-fields-"));
  const path = join(directory, "catalogue.yaml");
  const value = sourceFile();
  const product = value.products[0], field = product.fields[0];
  product.refresh_cadence = "Daily";
  field.pii = true;
  field.quality.description = "Must be present.";
  await writeFile(path, YAML.stringify(value));
  const source = openCatalogueSource({ PORTAL_CATALOGUE_FILE: path });
  const [loaded] = (await source.load()).products;
  assert.equal(loaded.refresh_cadence, "Daily");
  assert.equal(loaded.fields[0].pii, true);
  assert.equal(loaded.fields[0].quality.description, "Must be present.");
  for (const [target, key, bad] of [[product, "refresh_cadence", 7], [field, "pii", "Yes"], [field, "pii", 1], [field.quality, "description", 7], [field.quality, "unknown", "value"]]) {
    const before = target[key];
    target[key] = bad;
    await writeFile(path, YAML.stringify(value));
    await assert.rejects(source.load(), new RegExp(key));
    if (before === undefined) delete target[key]; else target[key] = before;
  }
  product.refresh_cadence = ""; field.pii = false; field.quality.description = "";
  await writeFile(path, YAML.stringify(value));
  assert.equal((await source.load()).products[0].fields[0].pii, false);
  delete product.refresh_cadence; delete field.pii; delete field.quality.description;
  await writeFile(path, YAML.stringify(value));
  assert.equal((await source.load()).products[0].fields[0].pii, undefined);
});


test("file catalogue accepts a schema, several rules a column and a blank fail %, and still refuses unknown keys", async () => {
  const directory = await mkdtemp(join(tmpdir(), "portal-pack-values-"));
  const path = join(directory, "catalogue.yaml");
  const value = sourceFile();
  const product = value.products[0], [first, second] = product.fields;
  Object.assign(product, { path: "gs://meridian-docs/policies", data_format: "PDF", APM_ID: "APM-DOC-7", schema: "policies" });
  first.quality = [{ ...first.quality }, { dimension: "Validity", rule: "id is well formed", rule_type: "custom", fail_percent: null }];
  delete second.quality.fail_percent;
  await writeFile(path, YAML.stringify(value));
  const source = openCatalogueSource({ PORTAL_CATALOGUE_FILE: path });
  const [loaded] = (await source.load()).products;
  assert.deepEqual([loaded.path, loaded.data_format, loaded.APM_ID, loaded.schema], ["gs://meridian-docs/policies", "PDF", "APM-DOC-7", "policies"]);
  assert.deepEqual(loaded.fields[0].quality.map((rule) => rule.fail_percent), [1, null]);
  assert.equal(loaded.fields[1].quality.fail_percent, undefined);
  for (const [target, key, bad, message] of [[product, "unknown", "value", /unknown keys: unknown/], [product, "schema", 7, /schema/], [first.quality[1], "unknown", "value", /quality\[1\] has unknown keys: unknown/], [first.quality[1], "fail_percent", "blank", /fail_percent/], [first.quality[1], "fail_percent", 101, /fail_percent/], [second, "quality", [], /at least one rule/], [second, "quality", ["rule"], /quality\[0\] must be a mapping/]]) {
    const before = target[key];
    target[key] = bad;
    await writeFile(path, YAML.stringify(value));
    await assert.rejects(source.load(), message);
    if (before === undefined) delete target[key]; else target[key] = before;
  }
  delete product.schema; delete product.APM_ID;
  value.sources = [{ id: "retail", APM_ID: "APM-SOURCE" }];
  await writeFile(path, YAML.stringify(value));
  const [unset] = (await source.load()).products;
  assert.equal(unset.schema, undefined);
  assert.equal(unset.APM_ID, "APM-SOURCE");
});
