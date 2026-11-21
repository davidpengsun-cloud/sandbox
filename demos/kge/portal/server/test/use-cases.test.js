import assert from "node:assert/strict";
import { mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { IncomingMessage, ServerResponse } from "node:http";
import { Socket } from "node:net";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { Writable } from "node:stream";
import { DatabaseSync } from "node:sqlite";
import { after, test } from "node:test";
import YAML from "yaml";
import { createApp } from "../src/app.js";
import { metadataPatch, useCaseId } from "../src/catalogue-record.js";
import { openKgeCatalogueSource } from "../src/catalogue-source/kge.js";
import { openCatalogueSource } from "../src/catalogue-source/index.js";
import { openFileCatalogueSource } from "../src/catalogue-source/file.js";
import { validateCatalogue } from "../src/catalogue-source/contract.js";
import { openBigQueryRepository } from "../src/repository/bigquery.js";
import { openSqliteRepository } from "../src/repository/sqlite.js";
import { bigQueryFake } from "./fixtures/bigquery-fake.js";
import { catalogue } from "./fixtures/catalogue.js";
import { checked, refuseGluedKeyword } from "./googlesql.js";

const scratch = mkdtempSync(join(tmpdir(), "portal-use-cases-"));
after(() => rmSync(scratch, { recursive: true }));

const first = [
  { id: "uc-revenue-review", title: "Month-end revenue review", objective: "Finance compares each month's revenue with the plan.", domain: "Finance", owner: "Finance controller", url: "https://example.com/revenue-review",
    kpi: { name: "Monthly revenue", definition: "Income booked in the month.", grain: "Month", unit: "USD" }, example_questions: ["What was revenue in June 2025?"] },
  { id: "uc-branch-pack", title: "Branch performance pack", objective: "Regional leads rank branches by account growth." },
];
const changed = [{ id: "uc-churn", title: "Churn early warning", objective: "Service teams spot accounts that stopped transacting." }];
const inline = [{ title: "Month-end revenue review", description: "Finance compares each month's revenue with the plan.", url: "https://example.com/revenue-review" }];
const inlineRead = [{ id: "uc-month-end-revenue-review", title: "Month-end revenue review", objective: "Finance compares each month's revenue with the plan.", url: "https://example.com/revenue-review" }];

async function request(app, path) {
  const req = new IncomingMessage(new Socket());
  req.complete = true;
  req.method = "GET";
  req.url = path;
  req.headers = {};
  const chunks = [];
  const res = new ServerResponse(req);
  res.assignSocket(new Writable({ write(chunk, _encoding, done) { chunks.push(Buffer.from(chunk)); done(); } }));
  const finished = new Promise((resolve, reject) => { res.once("finish", resolve); res.once("error", reject); });
  req.push(null);
  app.handle(req, res);
  await finished;
  return JSON.parse(Buffer.concat(chunks).toString().split("\r\n\r\n").slice(1).join("\r\n\r\n"));
}

for (const backend of ["sqlite", "bigquery"]) {
  for (const mode of [false, true, "follow"]) {
    test(`${backend} (${JSON.stringify(mode)}): use cases are stored in their declared order, follow each sync and read as none when removed`, async (t) => {
      const fake = bigQueryFake();
      const repository = backend === "sqlite" ? openSqliteRepository({ PORTAL_SQLITE_PATH: ":memory:" }) : openBigQueryRepository({ PROFILE: "meridian" }, { client: checked(fake.client) });
      t.after(() => repository.close());
      const clearGoverned = () => backend === "sqlite" ? repository.setGoverned(null) : (fake.state.governed = null);
      const products = structuredClone(catalogue.products);
      products[0].use_cases = structuredClone(first);
      await repository.syncCatalogue("first", products, mode);
      const kge = { identity: async () => ({ principal: "reader", units: ["retail", "sme"] }) };
      const { app } = createApp({ repository, kge, catalogueSync: false, env: {}, uiPath: "not-present" });
      assert.deepEqual((await request(app, `/product/${products[0].id}`)).USE_CASES, first);
      assert.deepEqual((await request(app, `/product/${products[1].id}`)).USE_CASES, []);
      await clearGoverned();
      assert.deepEqual((await repository.product(products[0].id, ["retail"])).use_cases, first);
      products[0].use_cases = structuredClone(changed);
      await repository.syncCatalogue("changed", products, mode);
      assert.deepEqual((await request(app, `/product/${products[0].id}`)).USE_CASES, changed);
      await clearGoverned();
      assert.deepEqual((await repository.product(products[0].id, ["retail"])).use_cases, changed);
      delete products[0].use_cases;
      await repository.syncCatalogue("removed", products, mode);
      assert.deepEqual((await request(app, `/product/${products[0].id}`)).USE_CASES, []);
      await clearGoverned();
      assert.deepEqual((await repository.product(products[0].id, ["retail"])).use_cases, []);
    });
  }
}

test("SQLite adds the use_cases column to a database made by the previous kit, keeping its rows and activity", () => {
  const filename = join(scratch, "previous-kit.db");
  let repository = openSqliteRepository({ PORTAL_SQLITE_PATH: filename });
  const base = structuredClone(catalogue.products[0]);
  repository.syncCatalogue("previous", [base]);
  repository.addFavourite("person", base.id);
  repository.close();
  const old = new DatabaseSync(filename);
  old.exec("ALTER TABLE _portal_products DROP COLUMN use_cases");
  old.close();
  for (let attempt = 0; attempt < 2; attempt++) {
    repository = openSqliteRepository({ PORTAL_SQLITE_PATH: filename });
    repository.setGoverned(null);
    const kept = repository.product(base.id, [base.unit_id]);
    assert.equal(kept.name, base.name);
    assert.deepEqual(kept.use_cases, []);
    assert.equal(repository.favourites("person", [base.unit_id]).length, 1);
    repository.close();
  }
  const stored = new DatabaseSync(filename);
  const columns = stored.prepare("PRAGMA table_info(_portal_products)").all().map((column) => column.name);
  assert.equal(columns.at(-1), "use_cases");
  stored.close();
  repository = openSqliteRepository({ PORTAL_SQLITE_PATH: filename });
  repository.syncCatalogue("next", [{ ...base, use_cases: first }], "follow");
  repository.setGoverned(null);
  assert.deepEqual(repository.product(base.id, [base.unit_id]).use_cases, first);
  assert.equal(repository.favourites("person", [base.unit_id]).length, 1);
  repository.close();
});

test("BigQuery adds the use_cases column to a table made by the previous kit at start-up, and the sync writes it", async () => {
  const previousKit = "id unit_id table_name name description product_owner business_owner badges quality_score last_refresh ingested_at subscribe_count access_count view_count baseline_rating domain community application product_type record_count path data_format storage last_dq_run asset_type usage_purpose dataset_type APM_ID project product_owner_email business_owner_email refresh_cadence schema";
  const calls = [];
  const client = checked({ query: async (job) => {
    calls.push(job);
    if (job.query.includes("INFORMATION_SCHEMA.COLUMNS")) return [previousKit.split(" ").map((column_name, index) => ({ table_name: "_portal_products", column_name, ordinal_position: index + 1, is_nullable: "YES" }))];
    return [[]];
  } });
  const repository = openBigQueryRepository({ PROFILE: "meridian" }, { client });
  await repository.listProducts(["retail"]);
  const added = calls.filter((job) => job.query.startsWith("ALTER TABLE") && job.query.includes("ADD COLUMN IF NOT EXISTS use_cases STRING"));
  assert.equal(added.length, 1);
  assert.ok(added[0].query.includes("ALTER TABLE `kge_meridian_warehouses._portal_products`"));
  assert.ok(!calls.some((job) => /DROP TABLE|DROP COLUMN|DELETE FROM `[^`]*\._portal_products`/.test(job.query)));
  await repository.syncCatalogue("next", [{ ...structuredClone(catalogue.products[0]), use_cases: first }], "follow");
  const write = calls.findLast((job) => job.params?.products);
  assert.equal(write.types.products[0].use_cases, "STRING");
  assert.equal(write.params.products[0].use_cases, JSON.stringify(first));
  assert.ok(write.query.includes("use_cases=s.use_cases WHEN NOT MATCHED"));
});

test("every MERGE a BigQuery catalogue sync sends has whitespace at each clause boundary, in all three sync modes", async () => {
  const product = { ...structuredClone(catalogue.products[0]), use_cases: first };
  assert.ok(product.fields.length && product.fields.some((field) => field.quality) && product.lineage.upstream.length + product.lineage.downstream.length + product.lineage.origin_paths.length);
  const merges = {};
  for (const [label, mode] of [["replace", false], ["seed", true], ["follow", "follow"]]) {
    const queries = [];
    const client = { query: async (job) => { queries.push(job.query); return [[]]; } };
    await openBigQueryRepository({ PROFILE: "meridian" }, { client }).syncCatalogue(`r-${label}`, [product], mode);
    const statements = queries.flatMap((sql) => sql.split(";")).map((sql) => sql.trim()).filter((sql) => sql.startsWith("MERGE"));
    merges[label] = statements.filter((sql) => sql.includes(" USING (SELECT * FROM UNNEST(@")).map((sql) => sql.split("`")[1]);
    for (const sql of queries) assert.doesNotThrow(() => refuseGluedKeyword(sql), `${label}: ${sql}`);
    for (const sql of statements) {
      assert.match(sql, /^MERGE `[^`]+` t USING \(.+?\) s ON \S.*?\S (?:WHEN MATCHED THEN UPDATE SET \S+ )?WHEN NOT MATCHED THEN INSERT ROW$/, `${label}: ${sql}`);
      for (const clause of ["USING", "ON", "WHEN", "THEN", "SET", "INSERT", "ROW"]) assert.doesNotMatch(sql, new RegExp(`[^\\s]${clause}\\b|\\b${clause}[^\\s(]`), `${label}: ${clause} in ${sql}`);
    }
  }
  const tables = ["products", "fields", "quality_rules", "lineage"].map((name) => `kge_meridian_warehouses._portal_${name}`);
  assert.deepEqual(merges, { replace: [], seed: tables, follow: tables });
});

test("the glued-keyword check refuses a clause keyword written without a space, and passes names that only end in one", () => {
  for (const sql of ["MERGE `d.t` t USING (SELECT 1) s ON t.id=s.id WHEN MATCHED THEN UPDATE SET use_cases=s.use_casesWHEN NOT MATCHED THEN INSERT ROW",
    "MERGE `d.t` t USING (SELECT 1) s ON t.id=s.id WHEN MATCHED THEN UPDATE SET pii=s.piiWHEN NOT MATCHED THEN INSERT ROW",
    "UPDATE `d.t` SET a=1 WHERE idAND b=2", "SELECT a FROM `d.t` WHERE x=1 ANDb=2"]) assert.throws(() => refuseGluedKeyword(sql), /Unrecognized name/);
  for (const sql of ["SELECT a FROM `d.t` UNION ALL SELECT b FROM `d.u`", "BEGIN TRANSACTION; COMMIT TRANSACTION;", "SELECT JSON_VALUE(old,'$.idWHEN') FROM `d.t_useWHEN` WHERE APM_ID=@APM_ID",
    "MERGE `d.t` t USING (SELECT 1) s ON t.id=s.id WHEN MATCHED THEN UPDATE SET use_cases=s.use_cases WHEN NOT MATCHED THEN INSERT ROW"]) assert.doesNotThrow(() => refuseGluedKeyword(sql));
});

test("a row stored before #599 in the inline shape reads as resolved use cases", () => {
  const filename = join(scratch, "inline-row.db");
  let repository = openSqliteRepository({ PORTAL_SQLITE_PATH: filename });
  const base = structuredClone(catalogue.products[0]);
  repository.syncCatalogue("previous", [base]);
  repository.close();
  const old = new DatabaseSync(filename);
  old.prepare("UPDATE _portal_products SET use_cases=? WHERE id=?").run(JSON.stringify(inline), base.id);
  old.close();
  repository = openSqliteRepository({ PORTAL_SQLITE_PATH: filename });
  repository.setGoverned(null);
  assert.deepEqual(repository.product(base.id, [base.unit_id]).use_cases, inlineRead);
  repository.close();
});

test("use cases are not portal-editable metadata", () => {
  assert.throws(() => metadataPatch({ use_cases: first }), /portal-owned metadata/);
});

test("the provider contract takes resolved use cases: an id, title and objective, with domain, owner, an https url, a KPI and example questions, and nothing else", () => {
  const envelope = (useCases) => ({ contract_version: "1", release: "r", has_catalogue: true, products: [{ ...structuredClone(catalogue.products[0]), use_cases: useCases }] });
  assert.doesNotThrow(() => validateCatalogue(envelope(first)));
  assert.doesNotThrow(() => validateCatalogue(envelope([])));
  const one = { id: "uc-x", title: "X", objective: "Why." };
  for (const bad of [
    [{ title: "No id", objective: "Why." }],
    [{ id: "uc-x", objective: "No title." }],
    [{ id: "uc-x", title: "No objective" }],
    [{ id: "uc x", title: "Space in id", objective: "Why." }],
    [{ title: "Inline", description: "The shape before #599." }],
    [{ ...one, url: "http://example.com" }],
    [{ ...one, url: "javascript:alert(1)" }],
    [{ ...one, kpi: { definition: "No name." } }],
    [{ ...one, kpi: { name: "K", target: "Unknown key" } }],
    [{ ...one, example_questions: [""] }],
    [{ ...one, purpose: "Unknown key" }],
  ]) assert.throws(() => validateCatalogue(envelope(bad)), /contract v1/);
});

const catalogueFile = (useCases, assetUseCases) => {
  const path = join(scratch, `catalogue-${Math.random().toString(36).slice(2)}.yaml`);
  const { unit_id, use_cases: _ignored, ...rest } = structuredClone(catalogue.products[0]);
  writeFileSync(path, YAML.stringify({ format: 1, units: [unit_id], ...(useCases ? { use_cases: useCases } : {}), products: [{ ...rest, id: `${unit_id}.${rest.table}`, unit: unit_id, use_cases: assetUseCases }] }));
  return path;
};

test("a portal catalogue file defines each use case once and an asset lists their ids, resolved in the asset's order", async () => {
  const path = catalogueFile(first, ["uc-branch-pack", "uc-revenue-review"]);
  const loaded = await openCatalogueSource({ PORTAL_CATALOGUE_FILE: path }).load();
  assert.deepEqual(loaded.products[0].use_cases, [first[1], first[0]]);
});

test("a portal catalogue file naming an unknown use case, listing one twice or defining one twice stops the portal, naming the id", async () => {
  const load = (path) => openCatalogueSource({ PORTAL_CATALOGUE_FILE: path }).load();
  await assert.rejects(load(catalogueFile(first, ["uc-missing"])), /products\[0\]\.use_cases\[0\] names unknown use case uc-missing/);
  await assert.rejects(load(catalogueFile(first, ["uc-branch-pack", "uc-branch-pack"])), /products\[0\]\.use_cases\[1\] lists use case uc-branch-pack twice/);
  await assert.rejects(load(catalogueFile([...first, { ...changed[0], id: "uc-branch-pack" }], ["uc-branch-pack"])), /use_cases\[2\]\.id uc-branch-pack is defined twice/);
  await assert.rejects(load(catalogueFile([{ id: "uc-x", title: "X" }], ["uc-x"])), /use_cases\[0\]\.objective must be a non-empty string/);
  await assert.rejects(load(catalogueFile([{ id: "uc-x", title: "X", objective: "Y", kpi: { unit: "USD" } }], ["uc-x"])), /use_cases\[0\]\.kpi is missing required fields: name/);
  await assert.rejects(load(catalogueFile([{ id: "uc-x", title: "X", objective: "Y", target: 1 }], ["uc-x"])), /use_cases\[0\] has unknown keys: target/);
  await assert.rejects(load(catalogueFile(first, "uc-branch-pack")), /products\[0\]\.use_cases must be a list/);
});

test("an inline use case from before #599 still loads, with a warning naming the asset", async () => {
  const warnings = [];
  const path = catalogueFile(undefined, inline);
  const loaded = await openFileCatalogueSource(path, { onWarning: (message) => warnings.push(message) }).load();
  assert.deepEqual(loaded.products[0].use_cases, inlineRead);
  assert.equal(warnings.length, 1);
  assert.match(warnings[0], /^products\[0\]\.use_cases\[0\]: \S+ gives a use case inline; define it under use_cases and list its id/);
  await assert.rejects(openFileCatalogueSource(catalogueFile(undefined, [{ title: "Plain web", description: "Not https.", url: "http://example.com" }]), { onWarning: () => {} }).load(), /url must be an https address/);
});

test("an inline entry whose id an asset also lists is refused as listed twice; a blank top-level use_cases reads as none", async () => {
  const load = (path) => openFileCatalogueSource(path, { onWarning: () => {} }).load();
  await assert.rejects(load(catalogueFile(inlineRead, ["uc-month-end-revenue-review", ...inline])), /products\[0\]\.use_cases\[1\] lists use case uc-month-end-revenue-review twice/);
  await assert.rejects(load(catalogueFile(inlineRead, [...inline, "uc-month-end-revenue-review"])), /products\[0\]\.use_cases\[1\] lists use case uc-month-end-revenue-review twice/);
  const path = catalogueFile(undefined, inline);
  writeFileSync(path, YAML.stringify({ ...YAML.parse((await import("node:fs")).readFileSync(path, "utf8")), use_cases: null }));
  assert.deepEqual((await load(path)).products[0].use_cases, inlineRead);
});

test("two inline titles that slug alike on one asset both load; the same inline title twice is refused", async () => {
  const load = (path) => openFileCatalogueSource(path, { onWarning: () => {} }).load();
  const alike = [{ title: "A/B", description: "One." }, { title: "a b", description: "Another." }];
  assert.equal((await load(catalogueFile(undefined, alike))).products[0].use_cases.length, 2);
  await assert.rejects(load(catalogueFile(undefined, [alike[0], { title: " A/B ", description: "The same title." }])), /products\[0\]\.use_cases\[1\] lists use case uc-a-b twice/);
});

test("an inline title with no a-z or 0-9 is known by its code points, as KGE's compiler names it", () => {
  assert.equal(useCaseId("Риск"), "uc-420-438-441-43a");
  assert.notEqual(useCaseId("日本語"), useCaseId("Риск"));
});

test("a KGE serving the inline shape from before #599 still satisfies the contract", async () => {
  const source = openKgeCatalogueSource({ catalogueIdentity: async () => ({ release: "r", has_catalogue: true }),
    catalogue: async () => ({ contract_version: "1", release: "r", has_catalogue: true, products: [{ ...structuredClone(catalogue.products[0]), use_cases: inline }] }) });
  const loaded = validateCatalogue(await source.load());
  assert.deepEqual(loaded.products[0].use_cases, inlineRead);
});
