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
