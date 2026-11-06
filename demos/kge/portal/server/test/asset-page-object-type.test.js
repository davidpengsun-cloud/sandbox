import assert from "node:assert/strict";
import test from "node:test";
import { IncomingMessage, ServerResponse } from "node:http";
import { Socket } from "node:net";
import { Writable } from "node:stream";
import { createApp } from "../src/app.js";
import { metadataPatch } from "../src/catalogue-record.js";
import { BRAND_SUBTITLE } from "../src/brand.js";
import { openSqliteRepository } from "../src/repository/sqlite.js";
import { openBigQueryRepository } from "../src/repository/bigquery.js";
import { bigQueryFake } from "./fixtures/bigquery-fake.js";
import { catalogue } from "./fixtures/catalogue.js";

async function request(app, path, body) {
  const req = new IncomingMessage(new Socket());
  req.complete = true;
  req.method = body === undefined ? "GET" : "POST";
  req.url = path;
  req.headers = { "content-type": "application/json", ...(body === undefined ? {} : { "content-length": String(Buffer.byteLength(JSON.stringify(body))) }) };
  const chunks = [];
  const res = new ServerResponse(req);
  res.assignSocket(new Writable({ write(chunk, _encoding, done) { chunks.push(Buffer.from(chunk)); done(); } }));
  const finished = new Promise((resolve, reject) => { res.once("finish", resolve); res.once("error", reject); });
  req.push(body === undefined ? null : Buffer.from(JSON.stringify(body)));
  if (body !== undefined) req.push(null);
  app.handle(req, res);
  await finished;
  const response = Buffer.concat(chunks).toString().split("\r\n\r\n").slice(1).join("\r\n\r\n");
  return { status: res.statusCode, json: () => JSON.parse(response) };
}


for (const subtitle of [undefined, "", "   ", "  Find trusted assets.  "]) {
  test(`getConfig serves trimmed subtitle or default for ${JSON.stringify(subtitle)}`, async (t) => {
    const repository = openSqliteRepository({ PORTAL_SQLITE_PATH: ":memory:" });
    t.after(() => repository.close());
    const kge = { identity: async () => ({ principal: "reader", units: ["retail"] }) };
    const { app } = createApp({ repository, kge, catalogueSync: false, env: { PORTAL_PRODUCT_SUBTITLE: subtitle }, uiPath: "not-present" });
    assert.equal((await request(app, "/getConfig")).json().Application_Desc, subtitle?.trim() || BRAND_SUBTITLE);
  });
}

for (const backend of ["sqlite", "bigquery"]) {
  test(`${backend}: asset, fields and quality responses carry hotfix metadata, keep type/filter values and access scope`, async (t) => {
    const fake = bigQueryFake();
    const repository = backend === "sqlite" ? openSqliteRepository({ PORTAL_SQLITE_PATH: ":memory:" }) : openBigQueryRepository({ PROFILE: "meridian" }, { client: fake.client });
    t.after(() => repository.close());
    const products = structuredClone(catalogue.products);
    products[0].refresh_cadence = "Daily";
    products[0].product_type = "API/MCP";
    products[0].fields[0].pii = true;
    products[0].fields[0].classification = "Restricted";
    products[0].fields[0].quality.description = "Must be present.";
    await repository.syncCatalogue("hotfix", products);
    const kge = { identity: async () => ({ principal: "reader", units: ["retail"] }) };
    const { app } = createApp({ repository, kge, catalogueSync: false, env: {}, uiPath: "not-present" });
    const json = async (path) => { fake.state.statements.length = 0; const res = await request(app, path); assert.equal(res.status, 200); return { value: res.json(), jobs: fake.state.statements.length }; };
    const product = await json(`/product/${products[0].id}`);
    assert.equal(product.value.REFRESH_CADENCE, "Daily");
    assert.equal(product.value.PRODUCTTYPE, "API/MCP");
    const fields = await json(`/fields/${products[0].id}`);
    assert.deepEqual(fields.value.map((row) => row.PII), ["Yes", ""]);
    assert.equal(fields.value[0].Classification, "Highly Confidential");
    const quality = await json(`/dataQuality/${products[0].id}/all`);
    assert.deepEqual(quality.value.map((row) => row["Rule description"]), ["Must be present.", ""]);
    const keys = Object.keys(quality.value[0]);
    assert.equal(keys.indexOf("Rule description"), keys.indexOf("Data Quality Rule Name") + 1);
    if (backend === "bigquery") { assert.equal(product.jobs, 6); assert.equal(fields.jobs, 5); assert.equal(quality.jobs, 5); }
    for (const path of [`/fields/wealth-holdings`, `/dataQuality/wealth-holdings/all`, `/product/wealth-holdings`]) assert.equal((await request(app, path)).status, 404);
  });
}


test("hotfix catalogue metadata remains outside portal admin editing", () => {
  for (const patch of [{ refresh_cadence: "Daily" }, { fields: [{ name: "id", pii: true }] }, { fields: [{ name: "id", quality: { description: "Forged" } }] }]) assert.throws(() => metadataPatch(patch), /metadata/);
});


const clearGoverned = (backend, repository, fake) => backend === "sqlite" ? repository.setGoverned(null) : (fake.state.governed = null);
const packValues = { path: "gs://meridian-docs/policies", data_format: "PDF", APM_ID: "APM-DOC-7", schema: "policies" };
const twoRules = [{ dimension: "Completeness", rule: "id is present", rule_type: "completeness", fail_percent: 2, description: "" }, { dimension: "Validity", rule: "id is well formed", rule_type: "custom", fail_percent: null, description: "Checked monthly." }];

for (const backend of ["sqlite", "bigquery"]) {
  test(`${backend}: supplied path, data format, APM ID and schema show on the page and cards; unset keep today's values`, async (t) => {
    const fake = bigQueryFake();
    const repository = backend === "sqlite" ? openSqliteRepository({ PORTAL_SQLITE_PATH: ":memory:" }) : openBigQueryRepository({ PROFILE: "meridian" }, { client: fake.client });
    t.after(() => repository.close());
    const products = structuredClone(catalogue.products);
    Object.assign(products[0], packValues);
    await repository.syncCatalogue("pack-values", products, "follow");
    const kge = { identity: async () => ({ principal: "reader", units: ["retail", "sme"] }) };
    const { app } = createApp({ repository, kge, catalogueSync: false, env: {}, uiPath: "not-present" });
    const json = async (path) => { fake.state.statements.length = 0; const res = await request(app, path); assert.equal(res.status, 200); return { value: res.json(), jobs: fake.state.statements.length }; };
    const supplied = await json(`/product/${products[0].id}`);
    assert.deepEqual([supplied.value.PATH, supplied.value.DATA_FORMAT, supplied.value.APM_ID, supplied.value.SCHEMANAME], [packValues.path, packValues.data_format, packValues.APM_ID, packValues.schema]);
    assert.equal(supplied.value.UNIT_ID, "retail");
    assert.equal(supplied.value.SCHEMA, packValues.schema);
    const unset = (await json(`/product/${products[1].id}`)).value;
    assert.deepEqual([unset.PATH, unset.DATA_FORMAT, unset.APM_ID, unset.SCHEMANAME], [catalogue.products[1].path, catalogue.products[1].data_format, catalogue.products[1].APM_ID, catalogue.products[1].unit_id]);
    assert.equal(unset.SCHEMA, "");
    const cards = await json("/getMostViewedProducts");
    const card = (id) => cards.value.find((item) => item.PRODUCTID === id);
    assert.deepEqual([card(products[0].id).PATH, card(products[0].id).DATA_FORMAT, card(products[0].id).APM_ID, card(products[0].id).SCHEMANAME], [packValues.path, packValues.data_format, packValues.APM_ID, packValues.schema]);
    assert.equal(card(products[1].id).SCHEMANAME, "sme");
    if (backend === "bigquery") { assert.equal(supplied.jobs, 6); assert.equal(cards.jobs, 6); }
    assert.equal((await request(app, `/product/${products[3].id}`)).status, 404);
  });

  test(`${backend}: a column's rules all show in the DQ table and a blank fail % stays blank, never 0`, async (t) => {
    const fake = bigQueryFake();
    const repository = backend === "sqlite" ? openSqliteRepository({ PORTAL_SQLITE_PATH: ":memory:" }) : openBigQueryRepository({ PROFILE: "meridian" }, { client: fake.client });
    t.after(() => repository.close());
    const products = structuredClone(catalogue.products);
    products[0].fields[0].quality = structuredClone(twoRules);
    products[0].fields[1].quality = { dimension: "Completeness", rule: "amount is present", rule_type: "completeness", description: "" };
    products[1].fields[0].quality = [{ ...twoRules[1] }, { ...twoRules[0], fail_percent: 4 }];
    products[1].fields[1].quality = [{ ...twoRules[1] }, { ...twoRules[1], rule: "amount is well formed" }];
    await repository.syncCatalogue("rules", products);
    const kge = { identity: async () => ({ principal: "reader", units: ["retail", "sme", "corporate"] }) };
    const { app } = createApp({ repository, kge, catalogueSync: false, env: {}, uiPath: "not-present" });
    const json = async (path) => { fake.state.statements.length = 0; const res = await request(app, path); assert.equal(res.status, 200); return { value: res.json(), jobs: fake.state.statements.length }; };
    const all = await json(`/dataQuality/${products[0].id}/all`);
    assert.deepEqual(all.value.map((row) => [row["Data Element Name"], row["Data Quality Rule Name"], row["Failing %"], row["Data Quality Score"]]), [["id", "id is present", 2, 98], ["id", "id is well formed", "", ""], ["amount", "amount is present", "", ""]]);
    assert.equal(all.value[1]["Rule description"], "Checked monthly.");
    assert.deepEqual((await json(`/dataQuality/${products[0].id}/custom`)).value.map((row) => row["Data Quality Rule Name"]), ["id is well formed"]);
    const fields = await json(`/fields/${products[0].id}`);
    assert.deepEqual(fields.value.map((row) => row["Data Quality Score"]), [98, ""]);
    assert.deepEqual((await json(`/fields/${products[1].id}`)).value.map((row) => row["Data Quality Score"]), [96, ""]);
    const single = await json(`/dataQuality/${products[2].id}/all`);
    assert.deepEqual(single.value.map((row) => [row["Failing %"], row["Data Quality Score"]]), [[1, 99], [1, 99]]);
    if (backend === "bigquery") { assert.equal(all.jobs, 5); assert.equal(fields.jobs, 5); }
    for (const governed of [true, false]) {
      if (!governed) clearGoverned(backend, repository, fake);
      const stored = await repository.fields(products[0].id, ["retail"]);
      assert.deepEqual(stored[0].quality.map((rule) => [rule.rule, rule.fail_percent, rule.description]), [["id is present", 2, ""], ["id is well formed", null, "Checked monthly."]]);
      assert.equal(stored[1].quality.fail_percent, null);
    }
  });

  test(`${backend}: a catalogue refresh keeps supplied values in follow mode and after a seed, and takes a later derived value`, async (t) => {
    for (const seeded of [false, true]) {
      const fake = bigQueryFake();
      const repository = backend === "sqlite" ? openSqliteRepository({ PORTAL_SQLITE_PATH: ":memory:" }) : openBigQueryRepository({ PROFILE: "meridian" }, { client: fake.client });
      t.after(() => repository.close());
      const derived = structuredClone(catalogue.products.slice(0, 2));
      const supplied = structuredClone(derived);
      Object.assign(supplied[0], packValues);
      supplied[0].fields[0].quality = structuredClone(twoRules);
      if (seeded) await repository.syncCatalogue("seed", derived, true);
      await repository.syncCatalogue("first", supplied, "follow");
      const kge = { identity: async () => ({ principal: "reader", units: ["retail", "sme"] }) };
      const { app } = createApp({ repository, kge, catalogueSync: false, env: {}, uiPath: "not-present" });
      const shown = async () => { const value = (await request(app, `/product/${supplied[0].id}`)).json(); return [value.PATH, value.DATA_FORMAT, value.APM_ID, value.SCHEMANAME]; };
      const expected = [packValues.path, packValues.data_format, packValues.APM_ID, packValues.schema];
      assert.deepEqual(await shown(), expected);
      const next = structuredClone(supplied);
      next[0].description = "A later release";
      await repository.syncCatalogue("second", next, "follow");
      assert.deepEqual(await shown(), expected);
      const product = await repository.product(supplied[0].id, ["retail"]);
      assert.deepEqual([product.path, product.data_format, product.APM_ID, product.schema], expected);
      assert.equal(product.fields[0].quality.length, 2);
      clearGoverned(backend, repository, fake);
      const stored = await repository.product(supplied[0].id, ["retail"]);
      assert.deepEqual([stored.path, stored.data_format, stored.APM_ID, stored.schema], expected);
      await repository.syncCatalogue("third", derived, "follow");
      assert.deepEqual(await shown(), [derived[0].path, derived[0].data_format, derived[0].APM_ID, derived[0].unit_id]);
    }
  });
}
