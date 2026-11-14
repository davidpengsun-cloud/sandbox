import assert from "node:assert/strict";
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { DatabaseSync } from "node:sqlite";
import { after, test } from "node:test";
import { openSqliteRepository } from "../src/repository/sqlite.js";
import { catalogue } from "./fixtures/catalogue.js";

const scratch = mkdtempSync(join(tmpdir(), "portal-repository-"));
after(() => rmSync(scratch, { recursive: true }));

test("a portal publish shows the new path and a portal-only card only in its unit", () => {
  const repository = openSqliteRepository({ PORTAL_SQLITE_PATH: ":memory:" });
  const policy = {
    ...catalogue.products[0], id: "retail.policies", table: "policies", name: "Policies", path: "shelf/policies",
    product_type: "Documents", asset_type: "Unstructured", fields: [], catalogue_state: "Not in the knowledge graph",
    portal_only: true, documents: [{ id: "DD-RETAIL", title: "Retail Banking Data Dictionary" }],
    lineage: { origin_paths: [], destination_paths: [], upstream: [], downstream: [] },
  };
  const changed = { ...catalogue.products[0], path: "Meridian > Conduct > Register", catalogue_state: "Portal ahead" };
  repository.syncCatalogue("portal-1", [changed, policy, catalogue.products[1]]);
  const retail = repository.listProducts(["retail"]);
  assert.equal(retail.find((item) => item.id === "retail-accounts").path, "Meridian > Conduct > Register");
  assert.equal(retail.find((item) => item.id === "retail-accounts").catalogue_state, "Portal ahead");
  const card = retail.find((item) => item.id === "retail.policies");
  assert.equal(card.catalogue_state, "Not in the knowledge graph");
  assert.deepEqual(card.documents, [{ id: "DD-RETAIL", title: "Retail Banking Data Dictionary" }]);
  assert.equal(repository.listProducts(["wealth"]).find((item) => item.id === "retail.policies"), undefined);
  repository.close();
});

test("a release without a catalogue hides the saved catalogue", () => {
  const repository = openSqliteRepository({ PORTAL_SQLITE_PATH: ":memory:" });
  repository.syncCatalogue(catalogue.release, catalogue.products);
  repository.syncCatalogue("release-without-catalogue", null);
  assert.equal(repository.catalogueRelease(), "release-without-catalogue");
  assert.deepEqual(repository.listProducts(["retail"]), []);
  repository.close();
});

test("an incompatible old schema is refused without deleting any portal records", () => {
  const filename = join(scratch, "old.db");
  const old = new DatabaseSync(filename);
  old.exec("CREATE TABLE _portal_products (id TEXT, unit_id TEXT, name TEXT, description TEXT, domain TEXT, community TEXT, application TEXT, product_type TEXT); CREATE TABLE _portal_favourites (principal_id TEXT NOT NULL, product_id TEXT NOT NULL, PRIMARY KEY(principal_id, product_id)); INSERT INTO _portal_favourites VALUES ('person','old-product')");
  old.close();
  assert.throws(() => openSqliteRepository({ PORTAL_SQLITE_PATH: filename }), /needs a migration/);
  const migrated = new DatabaseSync(filename);
  assert.equal(migrated.prepare("PRAGMA table_info(_portal_products)").all().length, 8);
  assert.deepEqual(migrated.prepare("SELECT * FROM _portal_favourites").all().map((row) => ({ ...row })), [{ principal_id: "person", product_id: "old-product" }]);
  migrated.close();
});

test("a stale schema marker preserves compatible portal records", () => {
  const filename = join(scratch, "old-version.db");
  let repository = openSqliteRepository({ PORTAL_SQLITE_PATH: filename });
  repository.syncCatalogue(catalogue.release, catalogue.products);
  repository.addFavourite("person", "retail-accounts");
  repository.close();
  const old = new DatabaseSync(filename);
  old.prepare("UPDATE _portal_schema SET derived_version=0 WHERE singleton=1").run();
  old.close();
  repository = openSqliteRepository({ PORTAL_SQLITE_PATH: filename });
  assert.equal(repository.listProducts(["retail"])[0].id, "retail-accounts");
  repository.close();
  const migrated = new DatabaseSync(filename);
  assert.equal(migrated.prepare("SELECT derived_version FROM _portal_schema WHERE singleton=1").get().derived_version, 2);
  assert.deepEqual(migrated.prepare("SELECT * FROM _portal_favourites").all().map((row) => ({ ...row })), [{ principal_id: "person", product_id: "retail-accounts" }]);
  migrated.close();
});

test("search results keep lineage to every product in readable units", () => {
  const repository = openSqliteRepository({ PORTAL_SQLITE_PATH: ":memory:" });
  const customer = structuredClone(catalogue.products[0]);
  customer.id = "retail-customer-master";
  customer.name = "Customer Master";
  customer.lineage.upstream = [];
  repository.syncCatalogue(catalogue.release, [...catalogue.products, customer]);
  const account = repository.listProducts(["retail"], "Accounts").find((product) => product.id === "retail-accounts");
  assert.deepEqual(account.lineage.upstream, ["retail-customer-master"]);
  repository.close();
});


test("a stale row filed under another unit cannot shadow the released unit", () => {
  const repository = openSqliteRepository({ PORTAL_SQLITE_PATH: ":memory:" });
  const released = catalogue.products.find((p) => p.unit_id === "wealth");
  const shadow = { ...released, unit_id: "retail", table: "shadow" };
  repository.syncCatalogue("poisoned", [shadow], true);
  repository.syncCatalogue("released", [released], true);
  assert.deepEqual(repository.listProducts(["retail"]), []);
  assert.equal(repository.product(released.id, ["wealth"]).table, released.table);
  repository.close();
});

test("unavailable figures stay blank through sync and reopen without changing the stored schema", () => {
  for (const seedOnly of [false, true]) {
    const filename = join(scratch, `blank-figures-${seedOnly}.db`);
    let repository = openSqliteRepository({ PORTAL_SQLITE_PATH: filename });
    const product = { ...catalogue.products[0], records: null, storage: "" };
    repository.syncCatalogue("unavailable", [product], seedOnly);
    assert.equal(repository.product(product.id, [product.unit_id]).records, null);
    repository.close();
    const stored = new DatabaseSync(filename);
    assert.equal(stored.prepare("SELECT record_count FROM _portal_products").get().record_count, 0);
    assert.equal(stored.prepare("PRAGMA table_info(_portal_products)").all().find((column) => column.name === "record_count").notnull, 1);
    stored.close();
    repository = openSqliteRepository({ PORTAL_SQLITE_PATH: filename });
    assert.equal(repository.product(product.id, [product.unit_id]).records, null);
    assert.equal(repository.product(product.id, [product.unit_id]).storage, "");
    repository.syncCatalogue("available", [{ ...product, records: 37, storage: "123 B" }], seedOnly);
    assert.equal(repository.product(product.id, [product.unit_id]).records, 37);
    repository.syncCatalogue("unavailable-again", [product], seedOnly);
    assert.equal(repository.product(product.id, [product.unit_id]).records, null);
    assert.equal(repository.product(product.id, [product.unit_id]).storage, "");
    repository.close();
  }
});

// The quality rules table as it shipped before a column could carry several
// rules: keyed by product and field, fail_percent required, with or without
// #310's rule description.
function oneRuleQualityTable(db, described = true) {
  db.exec(`ALTER TABLE _portal_quality_rules RENAME TO _portal_quality_rules_new;
    CREATE TABLE _portal_quality_rules (product_id TEXT NOT NULL, field_name TEXT NOT NULL, dimension TEXT NOT NULL, rule_name TEXT NOT NULL, rule_type TEXT NOT NULL, fail_percent REAL NOT NULL${described ? ", description TEXT NOT NULL DEFAULT ''" : ""}, PRIMARY KEY(product_id, field_name));
    INSERT INTO _portal_quality_rules SELECT product_id,field_name,dimension,rule_name,rule_type,fail_percent${described ? ",description" : ""} FROM _portal_quality_rules_new WHERE rule_ordinal=0;
    DROP TABLE _portal_quality_rules_new;`);
}

test("catalogue attributes survive additive SQLite upgrade, sync and reopen", () => {
  const filename = join(scratch, "attributes.db");
  let repository = openSqliteRepository({ PORTAL_SQLITE_PATH: filename });
  repository.syncCatalogue("before-attributes", catalogue.products);
  repository.addFavourite("person", catalogue.products[0].id);
  repository.close();
  const old = new DatabaseSync(filename);
  for (const column of ["asset_type", "usage_purpose", "dataset_type", "APM_ID", "project", "product_owner_email", "business_owner_email", "refresh_cadence", "schema"]) old.exec(`ALTER TABLE _portal_products DROP COLUMN ${column}`);
  old.exec("ALTER TABLE _portal_fields DROP COLUMN pii; ALTER TABLE _portal_fields DROP COLUMN classification");
  oneRuleQualityTable(old, false);
  old.close();
  for (let attempt = 0; attempt < 2; attempt++) {
    repository = openSqliteRepository({ PORTAL_SQLITE_PATH: filename });
    assert.equal(repository.favourites("person", ["retail"]).length, 1);
    repository.close();
  }
  const attributes = { asset_type: "Unstructured", usage_purpose: "Operational", dataset_type: "Policy documents", APM_ID: "APM-274", project: "Customer 360", product_owner_email: "dana@example.com", business_owner_email: "lee@example.com" };
  const classified = (product) => ({ ...product, ...attributes, fields: product.fields.map((field, index) => ({ ...field, classification: index ? "" : "Restricted" })) });
  repository = openSqliteRepository({ PORTAL_SQLITE_PATH: filename });
  repository.syncCatalogue("with-attributes", catalogue.products.map(classified), "follow");
  repository.close();
  const stored = new DatabaseSync(filename);
  const row = stored.prepare(`SELECT ${Object.keys(attributes).join(",")} FROM _portal_products WHERE id=?`).get(catalogue.products[0].id);
  assert.deepEqual({ ...row }, attributes);
  assert.deepEqual(stored.prepare("SELECT name,classification FROM _portal_fields WHERE product_id=? ORDER BY ordinal").all(catalogue.products[0].id).map(({ name, classification }) => [name, classification]), [["id", "Restricted"], ["amount", ""]]);
  stored.close();
  repository = openSqliteRepository({ PORTAL_SQLITE_PATH: filename });
  repository.setGoverned(null);
  const product = repository.product(catalogue.products[0].id, ["retail"]);
  for (const [key, value] of Object.entries(attributes)) assert.equal(product[key], value);
  assert.equal(product.fields[0].classification, "Restricted");
  repository.close();
});


test("SQLite adds hotfix columns to existing tables, preserves activity and syncs optional values in every mode", () => {
  for (const mode of [false, true, "follow"]) {
    const filename = join(scratch, `hotfix-${mode}.db`);
    let repository = openSqliteRepository({ PORTAL_SQLITE_PATH: filename });
    const base = structuredClone(catalogue.products[0]);
    repository.syncCatalogue("old", [base]);
    repository.addFavourite("person", base.id);
    repository.close();
    const old = new DatabaseSync(filename);
    old.exec("ALTER TABLE _portal_products DROP COLUMN refresh_cadence; ALTER TABLE _portal_products DROP COLUMN schema; ALTER TABLE _portal_fields DROP COLUMN pii");
    oneRuleQualityTable(old, false);
    old.close();
    repository = openSqliteRepository({ PORTAL_SQLITE_PATH: filename });
    repository.setGoverned(null);
    const legacy = repository.product(base.id, [base.unit_id]);
    assert.equal(legacy.refresh_cadence, "");
    assert.equal(legacy.fields[0].pii, false);
    assert.equal(legacy.fields[0].quality.description, "");
    assert.equal(repository.favourites("person", [base.unit_id]).length, 1);
    base.refresh_cadence = "Daily"; base.fields[0].pii = true; base.fields[0].quality.description = "Must be present.";
    repository.syncCatalogue("new", [base], mode);
    repository.close();
    repository = openSqliteRepository({ PORTAL_SQLITE_PATH: filename });
    repository.setGoverned(null);
    const actual = repository.product(base.id, [base.unit_id]);
    assert.equal(actual.refresh_cadence, "Daily");
    assert.equal(actual.fields[0].pii, true);
    assert.equal(actual.fields[0].quality.description, "Must be present.");
    delete base.refresh_cadence; delete base.fields[0].pii; delete base.fields[0].quality.description;
    repository.syncCatalogue("removed", [base], mode);
    repository.setGoverned(null);
    const removed = repository.product(base.id, [base.unit_id]);
    assert.equal(removed.refresh_cadence, "");
    assert.equal(removed.fields[0].pii, false);
    assert.equal(removed.fields[0].quality.description, "");
    repository.close();
  }
});


test("SQLite rebuilds a one-rule quality table keeping its rows, then holds several rules a column and a blank fail %", () => {
  const filename = join(scratch, "rules.db");
  let repository = openSqliteRepository({ PORTAL_SQLITE_PATH: filename });
  const base = structuredClone(catalogue.products[0]);
  base.fields[0].quality.description = "Kept.";
  repository.syncCatalogue("old", [base]);
  repository.addFavourite("person", base.id);
  repository.close();
  const old = new DatabaseSync(filename);
  old.exec("ALTER TABLE _portal_products DROP COLUMN schema");
  oneRuleQualityTable(old);
  old.close();
  for (let attempt = 0; attempt < 2; attempt++) {
    repository = openSqliteRepository({ PORTAL_SQLITE_PATH: filename });
    repository.setGoverned(null);
    const kept = repository.product(base.id, [base.unit_id]);
    assert.deepEqual(kept.fields.map((field) => field.quality), base.fields.map((field) => field.quality));
    assert.equal(kept.schema, "");
    assert.equal(repository.favourites("person", [base.unit_id]).length, 1);
    repository.close();
  }
  const stored = new DatabaseSync(filename);
  const columns = stored.prepare("PRAGMA table_info(_portal_quality_rules)").all();
  assert.deepEqual(columns.filter((column) => column.pk).map((column) => column.name), ["product_id", "field_name", "rule_ordinal"]);
  assert.equal(columns.find((column) => column.name === "fail_percent").notnull, 0);
  stored.close();
  repository = openSqliteRepository({ PORTAL_SQLITE_PATH: filename });
  const rules = [{ ...base.fields[0].quality }, { dimension: "Validity", rule: "id is well formed", rule_type: "custom", fail_percent: null, description: "" }];
  base.fields[0].quality = rules;
  base.schema = "accounts";
  repository.syncCatalogue("new", [base]);
  repository.setGoverned(null);
  const actual = repository.product(base.id, [base.unit_id]);
  assert.deepEqual(actual.fields[0].quality, rules);
  assert.equal(actual.schema, "accounts");
  repository.close();
});
