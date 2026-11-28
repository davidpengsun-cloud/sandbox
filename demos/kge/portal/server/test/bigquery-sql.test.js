import assert from "node:assert/strict";
import { test } from "node:test";
import { openBigQueryRepository } from "../src/repository/bigquery.js";
import { catalogue } from "./fixtures/catalogue.js";
import { checked, definedColumns, refuseCorrelatedArray, refuseReserved } from "./googlesql.js";

function recorded() {
  const calls = [];
  return { calls, client: checked({ query: async (job) => { calls.push(job.query); return [[]]; } }) };
}

test("every statement the BigQuery repository sends quotes its reserved column names and de-correlates its subqueries (#208)", async () => {
  const { calls, client } = recorded();
  const repository = openBigQueryRepository({ PROFILE: "meridian" }, { client });
  await repository.listProducts(["retail"]);
  await repository.syncCatalogue("r1", catalogue.products, true);
  await repository.syncCatalogue("r2", catalogue.products);
  await repository.syncCatalogue("r3", []);
  await repository.syncCatalogue("r4", null);
  await repository.setGoverned(catalogue);
  await repository.governed();
  await repository.catalogueRelease();
  await repository.promptDirection();
  await repository.setPromptDirection("Prefer field meanings.", "admin@example.com");
  await repository.updateMetadata(catalogue.products[0].id, { description: "d", fields: [{ name: "f", description: "x" }] });
  await repository.changeRole("person@example.com", "publisher", true, "admin@example.com");
  await repository.changeRole("person@example.com", "publisher", false, "admin@example.com");
  await repository.setRole("person@example.com", "viewer");
  await repository.roleAudit();
  await repository.users();
  await repository.migrateIdentities({ old: "new@example.com" }, "admin@example.com");
  await repository.view("p", "id"); await repository.published("id"); await repository.setPublished("id", true);
  await repository.favourites("p", ["retail"]); await repository.addFavourite("p", "id"); await repository.removeFavourite("p", "id");
  await repository.cart("p", ["retail"]); await repository.addCart("p", "id"); await repository.removeCart("p", "id"); await repository.emptyCart("p");
  await repository.request("p", "id", "access"); await repository.requests("p", ["retail"]); await repository.queue(); await repository.pending("1"); await repository.decide("1", "APPROVED", "a");
  await repository.feedback("p", "id", 5, "c"); await repository.askFeedback("p", 1, 1, ["retail"], "up", "c"); await repository.averageFeedback("id"); await repository.activity("id"); await repository.activities(["id"]);
  const defined = calls.flatMap(definedColumns);
  assert.ok(defined.includes("`at`"), "the audit, migration and upgrade tables define `at` quoted");
  assert.ok(calls.some((sql) => sql.includes("ORDER BY `at` DESC,id DESC")));
  assert.match(calls.find((sql) => sql.includes("seeded_removal")), /ARRAY_AGG\(DISTINCT u\.role ORDER BY u\.role\) before_roles FROM UNNEST\(@seeds\) s JOIN [^;]*_portal_users` t ON t\.principal_id=s\.principal AND t\.role=s\.role JOIN [^;]*_portal_users` u ON u\.principal_id=s\.principal GROUP BY s\.principal,s\.role;/);
});

test("the checks refuse the statements live BigQuery refused before #208, and pass their fixes", () => {
  assert.throws(() => refuseReserved("CREATE TABLE IF NOT EXISTS `d._portal_role_audit` (id STRING NOT NULL, at TIMESTAMP NOT NULL, actor STRING NOT NULL)"), /Unexpected keyword AT/);
  assert.throws(() => refuseReserved("ALTER TABLE `d.t` ADD COLUMN IF NOT EXISTS at TIMESTAMP"), /Unexpected keyword AT/);
  assert.throws(() => refuseReserved("SELECT * FROM `d._portal_role_audit` ORDER BY at DESC,id DESC", new Set(["at"])), /Unexpected keyword AT/);
  for (const columns of [new Set(), new Set(["at", "end"])]) {
    refuseReserved("SELECT t.at, a.end, t.x.at FROM `d._portal_role_audit` t", columns);
    refuseReserved("SELECT t . at FROM `d._portal_role_audit` t ORDER BY t.at", columns);
    refuseReserved("SELECT `at`.x FROM `d.t` AS `at`", columns);
    assert.throws(() => refuseReserved("SELECT at.x FROM `d.t`", columns), /Unexpected keyword AT/);
  }
  for (const sql of [
    "SELECT t.at AS at FROM `d.t` t",
    "SELECT t.at, at FROM `d.t` t",
    "INSERT `d.t` (id, at) VALUES (@id, @at)",
  ]) assert.throws(() => refuseReserved(sql, new Set(["at"])), /Unexpected keyword AT/);
  refuseReserved("CREATE TABLE IF NOT EXISTS `d._portal_role_audit` (id STRING NOT NULL, `at` TIMESTAMP NOT NULL, synced_at TIMESTAMP NOT NULL)");
  refuseReserved("SELECT * FROM `d._portal_role_audit` ORDER BY `at` DESC,id DESC", new Set(["at"]));
  refuseReserved("SELECT DISTINCT role FROM `d.u` WHERE principal_id=@principal ORDER BY role", new Set(["at"]));
  assert.throws(() => refuseCorrelatedArray("CREATE TEMP TABLE seeded_removal AS SELECT s.principal,s.role,ARRAY(SELECT DISTINCT u.role FROM `d._portal_users` u WHERE u.principal_id=s.principal ORDER BY u.role) before_roles FROM UNNEST(@seeds) s WHERE EXISTS (SELECT 1 FROM `d._portal_users` t WHERE t.principal_id=s.principal AND t.role=s.role);"), /Correlated subqueries/);
  refuseCorrelatedArray("CREATE TEMP TABLE seeded_removal AS SELECT s.principal,s.role,ARRAY_AGG(DISTINCT u.role ORDER BY u.role) before_roles FROM UNNEST(@seeds) s JOIN `d._portal_users` t ON t.principal_id=s.principal AND t.role=s.role JOIN `d._portal_users` u ON u.principal_id=s.principal GROUP BY s.principal,s.role;");
  refuseCorrelatedArray("SELECT TO_JSON_STRING(ARRAY(SELECT held FROM UNNEST(r.before_roles) held WHERE held<>r.role ORDER BY held)) FROM seeded_removal r");
  refuseCorrelatedArray("SET before_roles = ARRAY(SELECT DISTINCT role FROM `d._portal_users` WHERE principal_id=@principal ORDER BY role);");
});
