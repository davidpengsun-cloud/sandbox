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
  assert.equal(products[0].name, "Seed business name");
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
