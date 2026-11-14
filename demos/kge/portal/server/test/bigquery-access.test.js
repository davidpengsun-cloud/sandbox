import assert from "node:assert/strict";
import { test } from "node:test";
import { BigQuery } from "@google-cloud/bigquery";
import { openBigQueryRepository } from "../src/repository/bigquery.js";
import { catalogue } from "./fixtures/catalogue.js";
import { checked } from "./googlesql.js";

function standIn() {
  const calls = [], people = new Map(), audit = [];
  const client = { query: async (job) => {
    calls.push(job);
    if (job.query.startsWith("DECLARE before_roles")) {
      assert.match(job.query, /BEGIN TRANSACTION;[\s\S]*UPDATE .*_portal_schema[\s\S]*SET before_roles[\s\S]*INSERT .*_portal_role_audit[\s\S]*COMMIT TRANSACTION;/);
      const { principal, role, by, id } = job.params;
      const before = [...(people.get(principal) || [])].sort();
      const held = new Set(before);
      if (/DELETE FROM [^;]+ WHERE principal_id=@principal;/.test(job.query)) held.clear();
      if (/MERGE [^;]+_portal_users/.test(job.query)) held.add(role); else held.delete(role);
      people.set(principal, [...held].sort());
      const entry = { id, at: { value: "2026-10-31T12:00:00.000Z" }, actor: by, principal_id: principal, before_roles: JSON.stringify(before), after_roles: JSON.stringify([...held].sort()) };
      audit.unshift(entry);
      return [[entry]];
    }
    if (job.query.startsWith("SELECT DISTINCT role")) return [[...(people.get(job.params.principal) || [])].map((role) => ({ role }))];
    if (job.query.startsWith("SELECT principal_id,role")) return [[...people].sort().flatMap(([principal_id, roles]) => roles.map((role) => ({ principal_id, role })))];
    if (job.query.startsWith("SELECT *") && job.query.includes("_portal_role_audit")) return [audit];
    if (job.params?.entries) return [[{ migrated: job.params.entries.length, skipped: 0 }]];
    return [[]];
  } };
  return { calls, client: checked(client), people };
}

test("BigQuery stores additive grants with their before/after audit in one serialized transaction", async () => {
  const { calls, client } = standIn();
  const repository = openBigQueryRepository({ PROFILE: "meridian" }, { client });
  assert.equal(await repository.role("admin"), "viewer");
  assert.deepEqual(await repository.users(), []);
  const first = await repository.changeRole(" Person@EXAMPLE.com ", "publisher", true, "admin@example.com");
  assert.deepEqual(first.before, []); assert.deepEqual(first.after, ["publisher"]);
  assert.equal(first.by, "admin@example.com"); assert.equal(first.principal, "person@example.com");
  assert.equal(first.at, "2026-10-31T12:00:00.000Z");
  await repository.changeRole("person@example.com", "approver", true, "admin@example.com");
  await repository.changeRole("person@example.com", "admin", true, "admin@example.com");
  assert.equal(await repository.role("person@example.com"), "admin");
  await repository.changeRole("person@example.com", "admin", false, "admin@example.com");
  assert.deepEqual(await repository.users(), [{ principal_id: "person@example.com", role: "publisher", roles: ["approver", "publisher"] }]);
  await repository.setRole("person@example.com", "viewer");
  assert.deepEqual(await repository.roles("person@example.com"), ["viewer"]);
  assert.equal((await repository.roleAudit()).length, 5);
  const writes = calls.filter((job) => job.query.startsWith("DECLARE"));
  assert.equal(writes.length, 5);
  for (const write of writes) {
    assert.equal(Object.keys(write.params).length, 4);
    assert.deepEqual(Object.values(write.types), ["STRING", "STRING", "STRING", "STRING"]);
    assert.doesNotMatch(write.query, /person@example|admin@example/);
  }
  assert.ok(!calls.some((job) => job.params?.rows));
  const before = calls.length;
  await assert.rejects(repository.changeRole("person", "owner", true, "actor"), /role/);
  await assert.rejects(repository.setRole("bad name", "admin"), /username/i);
  await assert.rejects(repository.changeRole("person", "admin", null, "actor"), /grant/);
  assert.equal(calls.length, before);
});

test("BigQuery migrates all seven tables, decisions and audit with a transactional idempotence ledger", async () => {
  const { calls, client } = standIn();
  const repository = openBigQueryRepository({ PROFILE: "meridian" }, { client });
  assert.deepEqual(await repository.migrateIdentities({ Old: " Person@EXAMPLE.COM ", other: "person@example.com" }, "Operator"), { migrated: 2, skipped: 0 });
  const job = calls.find((call) => call.params?.entries);
  assert.deepEqual(job.params.entries, [{ principal: "Old", username: "person@example.com" }, { principal: "other", username: "person@example.com" }]);
  assert.equal(job.params.by, "operator");
  assert.match(job.query, /^BEGIN TRANSACTION;/);
  assert.match(job.query, /UPDATE .*_portal_schema.*SET derived_version=derived_version/);
  assert.match(job.query, /ASSERT NOT EXISTS[\s\S]*already migrated/);
  assert.match(job.query, /CREATE TEMP TABLE migration_work.*WHERE NOT EXISTS/);
  for (const table of ["users", "favourites", "cart"]) {
    assert.match(job.query, new RegExp(`MERGE [^;]*_portal_${table}[^;]*SELECT DISTINCT[^;]*WHEN NOT MATCHED THEN INSERT ROW`));
    assert.match(job.query, new RegExp(`DELETE FROM [^;]*_portal_${table}[^;]*principal<>username`));
  }
  for (const table of ["views", "requests", "feedback", "ask_feedback", "role_audit"]) assert.match(job.query, new RegExp(`UPDATE [^;]*_portal_${table}[^;]*SET principal_id=m.username`));
  assert.match(job.query, /UPDATE [^;]*_portal_requests[^;]*SET decided_by=m.username/);
  assert.match(job.query, /UPDATE [^;]*_portal_role_audit[^;]*SET actor=m.username/);
  assert.match(job.query, /INSERT [^;]*_portal_identity_migrations[\s\S]*COMMIT TRANSACTION;[\s\S]*SELECT COUNT/);
  assert.doesNotMatch(job.query, /Person@|Old|Operator/);
  const parameters = new BigQuery({ projectId: "test-project" }).buildQueryParams_(job.params, job.types).params;
  assert.equal(parameters.find((parameter) => parameter.name === "entries").parameterType.arrayType.type, "STRUCT");
  assert.deepEqual(await repository.migrateIdentities({}), { migrated: 0, skipped: 0 });
  const count = calls.length;
  for (const map of [null, [], { old: "access_admin" }, { old: "middle", middle: "new" }, { old: "other", other: "old" }]) await assert.rejects(repository.migrateIdentities(map));
  assert.equal(calls.length, count);
});

test("BigQuery aborts rather than reporting a successful access change when the transaction fails", async () => {
  const client = { query: async (job) => {
    if (job.query.startsWith("DECLARE") || job.params?.entries) throw new Error("transaction aborted");
    return [[]];
  } };
  const repository = openBigQueryRepository({ PROFILE: "meridian" }, { client });
  await assert.rejects(repository.changeRole("person", "admin", true, "actor"), /transaction aborted/);
  await assert.rejects(repository.migrateIdentities({ old: "new" }), /transaction aborted/);
});

test("BigQuery updates inherited owner names from the previous catalogue before replacing it", async () => {
  const { calls, client } = standIn();
  const repository = openBigQueryRepository({ PROFILE: "meridian" }, { client });
  await repository.syncCatalogue("titles", catalogue.products, true);
  const job = calls.find((call) => call.params?.products);
  assert.match(job.query, /product_owner=IF\(p.product_owner=JSON_VALUE\(old,'\$\.product_owner'\),fresh.product_owner,p.product_owner\)/);
  assert.match(job.query, /business_owner=IF\(p.business_owner=JSON_VALUE\(old,'\$\.business_owner'\),fresh.business_owner,p.business_owner\)/);
  for (const column of ["product_owner_email", "business_owner_email"]) assert.ok(job.query.includes(`${column}=IF(IFNULL(p.${column},'')=IFNULL(JSON_VALUE(old,'$.${column}'),''),fresh.${column},p.${column})`), column);
  assert.ok(job.query.indexOf("JSON_QUERY_ARRAY") < job.query.indexOf("DELETE FROM"));
});

test("BigQuery removes exactly the formerly code-seeded grants once, with an upgrade audit, in one transaction behind a ledger", async () => {
  const { calls, client } = standIn();
  const repository = openBigQueryRepository({ PROFILE: "meridian" }, { client });
  await repository.roles("person@example.com");
  await repository.changeRole("person@example.com", "publisher", true, "KGE.Id@example.com");
  const upgrades = calls.filter((job) => job.params?.name === "remove-code-seeded-grants");
  assert.equal(upgrades.length, 1);
  const [job] = upgrades;
  assert.ok(calls.indexOf(job) < calls.findIndex((call) => call.query.startsWith("SELECT DISTINCT role")));
  assert.match(job.query, /^BEGIN TRANSACTION;\nUPDATE [^;]*_portal_schema[^;]*SET derived_version=derived_version/);
  assert.match(job.query, /IF NOT EXISTS \(SELECT 1 FROM [^;]*_portal_upgrades[^;]*WHERE name=@name\) THEN/);
  assert.match(job.query, /CREATE TEMP TABLE seeded_removal[^;]*UNNEST\(@seeds\) s JOIN [^;]*_portal_users` t ON t\.principal_id=s\.principal AND t\.role=s\.role JOIN [^;]*_portal_users` u ON u\.principal_id=s\.principal GROUP BY s\.principal,s\.role;/);
  assert.match(job.query, /DELETE FROM [^;]*_portal_users[^;]* t WHERE EXISTS \(SELECT 1 FROM seeded_removal r WHERE r\.principal=t\.principal_id AND r\.role=t\.role\);/);
  assert.match(job.query, /INSERT [^;]*_portal_role_audit[^;]*'upgrade',r\.principal,TO_JSON_STRING\(r\.before_roles\),TO_JSON_STRING\(ARRAY\(SELECT held FROM UNNEST\(r\.before_roles\) held WHERE held<>r\.role/);
  assert.match(job.query, /INSERT [^;]*_portal_upgrades[^;]* VALUES \(@name,CURRENT_TIMESTAMP\(\)\);\nEND IF;\nCOMMIT TRANSACTION;$/);
  assert.doesNotMatch(job.query, /'admin'|'steward'|'analyst_retail'/);
  assert.deepEqual(job.params.seeds, [{ principal: "admin", role: "admin" }, { principal: "steward", role: "approver" }, { principal: "analyst_retail", role: "viewer" }]);
  const parameters = new BigQuery({ projectId: "test-project" }).buildQueryParams_(job.params, job.types).params;
  assert.equal(parameters.find((parameter) => parameter.name === "seeds").parameterType.arrayType.type, "STRUCT");
  assert.equal(calls.find((call) => call.query.startsWith("DECLARE")).params.by, "KGE.Id@example.com", "the actor is KGE's identity as given");
});

test("a failed BigQuery upgrade is retried on the next request and grants nothing meanwhile", async () => {
  let fail = true;
  const calls = [];
  const client = { query: async (job) => {
    calls.push(job);
    if (job.params?.name === "remove-code-seeded-grants" && fail) throw new Error("transaction aborted");
    return [[]];
  } };
  const repository = openBigQueryRepository({ PROFILE: "meridian" }, { client });
  await assert.rejects(repository.roles("admin"), /transaction aborted/);
  assert.ok(!calls.some((job) => job.query.startsWith("SELECT DISTINCT role")));
  fail = false;
  assert.deepEqual(await repository.roles("admin"), ["viewer"]);
  assert.equal(calls.filter((job) => job.params?.name === "remove-code-seeded-grants").length, 2);
});
