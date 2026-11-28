import assert from "node:assert/strict";
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { DatabaseSync } from "node:sqlite";
import { after, test } from "node:test";
import { openSqliteRepository } from "../src/repository/sqlite.js";
import { catalogue } from "./fixtures/catalogue.js";

const scratch = mkdtempSync(join(tmpdir(), "portal-access-"));
after(() => rmSync(scratch, { recursive: true }));
const open = (name) => openSqliteRepository({ PORTAL_SQLITE_PATH: join(scratch, `${name}.db`) });

test("roles are explicit additive grants, default to viewer and never follow names", () => {
  const repository = open("grants");
  for (const name of ["admin", "steward", "analyst_retail"]) assert.deepEqual(repository.roles(name), ["viewer"]);
  assert.deepEqual(repository.users(), []);
  const first = repository.changeRole("  PUBLISHER.Example@EXAMPLE.COM  ", "publisher", true, "Admin.Id@example.com");
  assert.deepEqual(first.before, []);
  assert.deepEqual(first.after, ["publisher"]);
  assert.equal(first.by, "Admin.Id@example.com", "the actor is KGE's identity as given, never re-normalised");
  assert.equal(first.principal, "publisher.example@example.com");
  assert.ok(Number.isFinite(Date.parse(first.at)));
  repository.changeRole(first.principal, "approver", true, first.by);
  repository.changeRole(first.principal, "admin", true, first.by);
  assert.deepEqual(repository.roles(first.principal), ["admin", "approver", "publisher"]);
  assert.equal(repository.role(first.principal), "admin");
  repository.changeRole(first.principal, "admin", false, first.by);
  assert.equal(repository.role(first.principal), "publisher");
  repository.changeRole(first.principal, "publisher", false, first.by);
  assert.equal(repository.role(first.principal), "approver");
  repository.changeRole(first.principal, "approver", false, first.by);
  assert.deepEqual(repository.roles(first.principal), ["viewer"]);
  assert.deepEqual(repository.users(), []);
  assert.equal(repository.roleAudit().length, 6);
  assert.ok(repository.roleAudit().every((entry) => entry.by === "Admin.Id@example.com"));
  assert.deepEqual(repository.roleAudit()[0].before, ["approver"]);
  assert.deepEqual(repository.roleAudit()[0].after, []);
  repository.close();
});

test("role writes validate before changing grants or audit and normalized usernames are unique", () => {
  const repository = open("validation");
  repository.changeRole("Straße@example.com", "publisher", true, "operator@example.com");
  repository.changeRole("STRASSE@example.com", "approver", true, "operator@example.com");
  assert.equal(repository.users().length, 1);
  assert.deepEqual(repository.roles("strasse@example.com"), ["approver", "publisher"]);
  for (const role of ["owner", "access_admin", "", null]) assert.throws(() => repository.changeRole("valid", role, true, "actor"), /role/i);
  for (const username of ["", "a b", "a\nb", "access_admin", "ROLE:reader", "a".repeat(255)]) assert.throws(() => repository.setRole(username, "admin"), /username|reserved|254|whitespace|control/i);
  assert.throws(() => repository.changeRole("valid", "admin", "true", "actor"), /grant/);
  assert.throws(() => repository.changeRole("valid", "admin", true, ""));
  assert.equal(repository.roleAudit().length, 2);
  repository.setRole("Straße@example.com", "viewer");
  assert.deepEqual(repository.users(), [{ principal_id: "strasse@example.com", role: "viewer", roles: ["viewer"] }]);
  repository.close();
});

test("opening an installed single-role database preserves its grants and removes the formerly code-seeded ones with an audit", () => {
  const filename = join(scratch, "legacy.db");
  const db = new DatabaseSync(filename);
  db.exec("CREATE TABLE _portal_users (principal_id TEXT PRIMARY KEY, role TEXT NOT NULL DEFAULT 'viewer'); INSERT INTO _portal_users VALUES ('old.publisher@example.com','publisher'),('admin','admin'),('steward','approver'),('analyst_retail','viewer'),('other','admin')");
  db.close();
  let repository = open("legacy");
  assert.equal(repository.role("old.publisher@example.com"), "publisher");
  assert.equal(repository.role("other"), "admin");
  for (const name of ["admin", "steward", "analyst_retail"]) assert.deepEqual(repository.roles(name), ["viewer"]);
  assert.deepEqual(repository.users().map((user) => user.principal_id), ["old.publisher@example.com", "other"]);
  const removed = repository.roleAudit().filter((entry) => entry.by === "upgrade").map(({ principal, before, after }) => ({ principal, before, after })).sort((a, b) => a.principal.localeCompare(b.principal));
  assert.deepEqual(removed, [{ principal: "admin", before: ["admin"], after: [] }, { principal: "analyst_retail", before: ["viewer"], after: [] }, { principal: "steward", before: ["approver"], after: [] }]);
  assert.equal(repository.roleAudit().length, 3);
  repository.changeRole("old.publisher@example.com", "approver", true, "actor");
  repository.changeRole("admin", "admin", true, "operator@example.com");
  repository.close();
  repository = open("legacy");
  assert.deepEqual(repository.roles("old.publisher@example.com"), ["approver", "publisher"]);
  assert.equal(repository.role("admin"), "admin", "the upgrade runs once: a later deliberate grant survives a restart");
  assert.equal(repository.roleAudit().filter((entry) => entry.by === "upgrade").length, 3);
  repository.close();
});

test("the seeded-grant removal takes only the exact seeded pairs, keeps other grants of the same people and is idempotent", () => {
  const filename = join(scratch, "multi.db");
  const db = new DatabaseSync(filename);
  db.exec("CREATE TABLE _portal_users (principal_id TEXT NOT NULL, role TEXT NOT NULL, PRIMARY KEY(principal_id, role)); INSERT INTO _portal_users VALUES ('steward','approver'),('steward','publisher'),('analyst_retail','publisher'),('admin','approver')");
  db.close();
  let repository = open("multi");
  assert.deepEqual(repository.roles("steward"), ["publisher"]);
  assert.deepEqual(repository.roles("analyst_retail"), ["publisher"]);
  assert.deepEqual(repository.roles("admin"), ["approver"]);
  assert.deepEqual(repository.roleAudit().map(({ by, principal, before, after }) => ({ by, principal, before, after })), [{ by: "upgrade", principal: "steward", before: ["approver", "publisher"], after: ["publisher"] }]);
  repository.close();
  repository = open("multi");
  assert.equal(repository.roleAudit().length, 1);
  repository.close();
});

test("a failed seeded-grant audit leaves the grants and the ledger untouched", () => {
  const filename = join(scratch, "upgrade-rollback.db");
  const db = new DatabaseSync(filename);
  db.exec("CREATE TABLE _portal_users (principal_id TEXT NOT NULL, role TEXT NOT NULL, PRIMARY KEY(principal_id, role)); INSERT INTO _portal_users VALUES ('admin','admin')");
  db.exec("CREATE TABLE _portal_role_audit (id TEXT PRIMARY KEY, at TEXT NOT NULL, actor TEXT NOT NULL, principal_id TEXT NOT NULL, before_roles TEXT NOT NULL, after_roles TEXT NOT NULL)");
  db.exec("CREATE TRIGGER refuse_audit BEFORE INSERT ON _portal_role_audit BEGIN SELECT RAISE(ABORT, 'audit unavailable'); END");
  assert.throws(() => open("upgrade-rollback"), /audit unavailable/);
  assert.equal(db.prepare("SELECT count(*) count FROM _portal_users WHERE principal_id='admin' AND role='admin'").get().count, 1);
  assert.equal(db.prepare("SELECT count(*) count FROM _portal_upgrades").get().count, 0);
  db.exec("DROP TRIGGER refuse_audit");
  db.close();
  const repository = open("upgrade-rollback");
  assert.deepEqual(repository.roles("admin"), ["viewer"]);
  assert.equal(repository.roleAudit().length, 1);
  repository.close();
});

test("audit failure rolls the grant back in the same transaction", () => {
  const repository = open("audit-rollback");
  const db = new DatabaseSync(join(scratch, "audit-rollback.db"));
  db.exec("CREATE TRIGGER refuse_audit BEFORE INSERT ON _portal_role_audit BEGIN SELECT RAISE(ABORT, 'audit unavailable'); END");
  assert.throws(() => repository.changeRole("person", "admin", true, "actor"), /audit unavailable/);
  assert.deepEqual(repository.users(), []);
  assert.deepEqual(repository.roleAudit(), []);
  db.close(); repository.close();
});

test("identity migration merges saved sets and grants, preserves all events and decisions, and repeats harmlessly", () => {
  let repository = open("migration");
  repository.setRole("legacy", "publisher");
  repository.setRole("person@example.com", "approver");
  repository.addFavourite("legacy", "one"); repository.addFavourite("person@example.com", "one");
  repository.addFavourite("legacy", "two");
  repository.addCart("legacy", "one"); repository.addCart("person@example.com", "one");
  for (const principal of ["legacy", "person@example.com"]) {
    repository.view(principal, "one"); repository.request(principal, "one", "access");
    repository.feedback(principal, "one", 5, "keep this"); repository.askFeedback(principal, 1, 2, { unit: "retail" }, "up", "keep this too");
  }
  repository.decide(1, "APPROVED", "legacy");
  const report = repository.migrateIdentities({ legacy: " PERSON@EXAMPLE.COM " }, "operator");
  assert.equal(report.migrated, 1); assert.equal(report.skipped, 0);
  assert.equal(report.rows.decisions, 1);
  assert.deepEqual(repository.roles("person@example.com"), ["approver", "publisher"]);
  assert.equal(repository.role("legacy"), "viewer");
  assert.ok(repository.roleAudit().every((entry) => entry.principal === "person@example.com" && entry.by === "person@example.com"));
  repository.close();
  repository = open("migration");
  assert.deepEqual(repository.migrateIdentities({ legacy: "person@example.com" }), { migrated: 0, skipped: 1, rows: {} });
  const db = new DatabaseSync(join(scratch, "migration.db"));
  for (const name of ["favourites", "views", "requests", "feedback", "ask_feedback"]) {
    const held = db.prepare(`SELECT * FROM _portal_${name}`).all();
    assert.equal(held.length, 2, name);
    assert.ok(held.every((row) => row.principal_id === "person@example.com"), name);
  }
  assert.equal(db.prepare("SELECT count(*) count FROM _portal_cart").get().count, 1);
  const request = db.prepare("SELECT * FROM _portal_requests WHERE id=1").get();
  assert.equal(request.status, "APPROVED"); assert.equal(request.decided_by, "person@example.com"); assert.ok(request.decided_at);
  assert.equal(db.prepare("SELECT comment FROM _portal_feedback WHERE id=1").get().comment, "keep this");
  assert.equal(db.prepare("SELECT count(*) count FROM _portal_identity_migrations").get().count, 1);
  db.close(); repository.close();
});

test("migration refuses changed maps and chains, and any late failure rolls every table back", () => {
  const repository = open("migration-refusals");
  repository.addFavourite("old", "one"); repository.view("old", "one");
  for (const map of [null, [], { old: "access_admin" }, { old: "middle", middle: "new" }, { old: "other", other: "old" }]) assert.throws(() => repository.migrateIdentities(map));
  const db = new DatabaseSync(join(scratch, "migration-refusals.db"));
  db.exec("CREATE TRIGGER refuse_migration BEFORE INSERT ON _portal_identity_migrations BEGIN SELECT RAISE(ABORT, 'ledger unavailable'); END");
  assert.throws(() => repository.migrateIdentities({ old: "new" }), /ledger unavailable/);
  assert.equal(db.prepare("SELECT principal_id FROM _portal_favourites").get().principal_id, "old");
  assert.equal(db.prepare("SELECT principal_id FROM _portal_views").get().principal_id, "old");
  db.exec("DROP TRIGGER refuse_migration");
  repository.migrateIdentities({ old: "new" });
  assert.throws(() => repository.migrateIdentities({ other: "elsewhere", old: "changed" }), /already migrated/);
  assert.equal(db.prepare("SELECT count(*) count FROM _portal_identity_migrations").get().count, 1);
  assert.throws(() => repository.migrateIdentities({ new: "newer" }), /chains/);
  assert.throws(() => repository.migrateIdentities({ another: "old" }), /chains/);
  assert.deepEqual(repository.migrateIdentities({}), { migrated: 0, skipped: 0, rows: {} });
  assert.equal(repository.migrateIdentities({ unchanged: "unchanged" }).migrated, 1);
  assert.equal(repository.migrateIdentities({ first: "shared", second: "shared", shared: "shared" }).migrated, 3);
  assert.equal(repository.migrateIdentities({ first: "shared", second: "shared", shared: "shared" }).skipped, 3);
  db.close(); repository.close();
});

test("a new catalogue replaces inherited person owners with titles and preserves portal edits", () => {
  const repository = open("owner-titles");
  const before = structuredClone(catalogue.products);
  for (const product of before) { product.product_owner = "legacy-steward"; product.business_owner = "legacy-analyst"; }
  repository.syncCatalogue("before", before, true);
  repository.updateMetadata(before[0].id, { product_owner: "Risk Office" });
  const after = before.map((product) => ({ ...product, product_owner: "Steward", business_owner: "Analyst" }));
  repository.syncCatalogue("after", after, true);
  const products = repository.listProducts([...new Set(after.map((product) => product.unit_id))]);
  assert.equal(products.find((product) => product.id === before[0].id).product_owner, "Risk Office");
  assert.ok(products.filter((product) => product.id !== before[0].id).every((product) => product.product_owner === "Steward"));
  assert.ok(products.every((product) => product.business_owner === "Analyst"));
  repository.close();
});

test("a new catalogue updates owner emails a portal edit did not change, and keeps the ones it did", () => {
  const repository = open("owner-emails");
  const before = structuredClone(catalogue.products);
  for (const product of before) Object.assign(product, { product_owner_email: "old.steward@example.com", business_owner_email: "" });
  repository.syncCatalogue("before", before, true);
  repository.updateMetadata(before[0].id, { product_owner_email: "risk.office@example.com", business_owner_email: "risk.lead@example.com" });
  const after = before.map((product) => ({ ...product, product_owner_email: "steward@example.com", business_owner_email: "analyst@example.com" }));
  repository.syncCatalogue("after", after, "follow");
  const products = repository.listProducts([...new Set(after.map((product) => product.unit_id))]);
  const edited = products.find((product) => product.id === before[0].id);
  assert.deepEqual([edited.product_owner_email, edited.business_owner_email], ["risk.office@example.com", "risk.lead@example.com"]);
  for (const product of products.filter((item) => item.id !== before[0].id)) assert.deepEqual([product.product_owner_email, product.business_owner_email], ["steward@example.com", "analyst@example.com"]);
  repository.updateMetadata(before[1].id, { product_owner_email: "" });
  assert.equal(repository.product(before[1].id, [before[1].unit_id]).product_owner_email, "");
  for (const bad of ["dana", "a@b", 7]) assert.throws(() => repository.updateMetadata(before[0].id, { product_owner_email: bad }), /Invalid product metadata/);
  repository.close();
});
