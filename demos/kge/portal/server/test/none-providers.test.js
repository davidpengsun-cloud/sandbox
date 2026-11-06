import { BRAND_SUBTITLE } from "../src/brand.js";
import assert from "node:assert/strict";
import { mkdtemp, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";
import YAML from "yaml";
import { createApp } from "../src/app.js";
import { catalogue } from "./fixtures/catalogue.js";
import { localCredentials, password } from "./fixtures/identity.js";

for (const [profile, name] of [[undefined, undefined], ["aster-bank", undefined], ["meridian", undefined], ["aster-bank", "  Cedar Trust  "], [undefined, "Cedar Trust"], ["aster-bank", "  "]]) test(`portal ${profile || "unbranded"} ${name || "default name"} without KGE enables catalogue Ask and refuses data routes`, async (context) => {
  const directory = await mkdtemp(join(tmpdir(), "portal-alone-"));
  const path = join(directory, "catalogue.yaml");
  const products = catalogue.products.slice(0, 1).map(({ unit_id, ...product }) => ({ ...product, id: `${unit_id}.${product.table}`, unit: unit_id }));
  await writeFile(path, YAML.stringify({ format: 1, units: ["retail"], products }));
  const { app, repository } = createApp({ env: { ...await localCredentials(directory), PROFILE: profile, NAME: name, PORTAL_TABLE_QUERY: "on", PORTAL_TABLE_PREVIEW: "on", PORTAL_APP_CONTEXT: "off", PORTAL_CATALOGUE_FILE: path, PORTAL_BACKEND: "sqlite", PORTAL_SQLITE_PATH: ":memory:" }, uiPath: "not-present" });
  const server = app.listen(0);
  context.after(() => server.close());
  while (!await repository.catalogueRelease()) await new Promise((resolve) => setTimeout(resolve, 1));
  const base = `http://127.0.0.1:${server.address().port}`;
  const signed = await fetch(`${base}/session`, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ username: "analyst_retail", password }) });
  assert.equal(signed.status, 200);
  const cookie = signed.headers.getSetCookie()[0].split(";")[0];
  const request = (route, options = {}) => fetch(`${base}${route}`, { headers: { "content-type": "application/json", cookie }, ...options });
  assert.deepEqual(await (await request("/getConfig")).json(), { Application_Title: "ALTA: AI-powered Library for Trusted Assets", Application_Desc: BRAND_SUBTITLE, preview: false, export: false, executeQuery: false, ask: true });
  for (const [route, options] of [["/api/preview/retail.accounts", {}], ["/api/query", { method: "POST", body: JSON.stringify({ unit: "retail", sql: "select 1" }) }]]) {
    const response = await request(route, options);
    assert.equal(response.status, 503);
    assert.match(await response.text(), /unavailable without Context/);
  }
  const answer = await (await request("/ask", { method: "POST", body: JSON.stringify({ question: "Total revenue?" }) })).json();
  assert.equal(answer.provider, "portal");
  assert.doesNotMatch(answer.notice, /Context/);
  assert.deepEqual(answer.figures, []);
  assert.match(answer.answer_text, /Catalogue only/);
});

test("the documented standalone catalogue serves real Meridian products after password sign-in", async (context) => {
  const { fileURLToPath } = await import("node:url");
  const directory = await mkdtemp(join(tmpdir(), "portal-meridian-"));
  const path = fileURLToPath(new URL("../../../examples/meridian-portal/catalogue.yaml", import.meta.url));
  const { app, repository } = createApp({ env: { ...await localCredentials(directory), PORTAL_CATALOGUE_FILE: path, PORTAL_BACKEND: "sqlite", PORTAL_SQLITE_PATH: ":memory:" }, uiPath: "not-present" });
  const server = app.listen(0);
  context.after(() => { server.close(); repository.close(); });
  const deadline = Date.now() + 5000;
  while (!await repository.catalogueRelease() && Date.now() < deadline) await new Promise((resolve) => setTimeout(resolve, 10));
  assert.ok(await repository.catalogueRelease());
  const base = `http://127.0.0.1:${server.address().port}`;
  const signed = await fetch(`${base}/session`, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ username: "analyst_retail", password }) });
  assert.equal(signed.status, 200);
  const cookie = signed.headers.getSetCookie()[0].split(";")[0];
  const products = await (await fetch(`${base}/api/products`, { headers: { cookie } })).json();
  assert.equal(products.length, 10);
  assert.ok(products.every((product) => product.unit_id === "retail" && product.data_format === "DuckDB table"));
  const account = products.find((product) => product.id === "retail.account");
  assert.ok(account.fields.every((field) => field.terms.length && field.data_type));
  assert.ok(account.lineage.upstream.length >= 2 && account.lineage.downstream.length >= 3);
  assert.equal((await fetch(`${base}/api/products/wealth.ADV_DIM_1`, { headers: { cookie } })).status, 404);
});

for (const [principal, units, count] of [["all_reader", ["retail", "sme", "corporate", "wealth"], 59], ["retail_reader", ["retail"], 10], ["wealth_reader", ["wealth"], 32]]) {
  test(`browse lists every readable product for ${principal}`, async (context) => {
    const { fileURLToPath } = await import("node:url");
    const { readFile } = await import("node:fs/promises");
    const directory = await mkdtemp(join(tmpdir(), "portal-browse-"));
    const credentials = await localCredentials(directory);
    const passwords = JSON.parse(await readFile(credentials.PORTAL_PASSWORDS_FILE, "utf8"));
    await writeFile(credentials.PORTAL_IDENTITIES_FILE, JSON.stringify([{ principal, role: "viewer", reads: units, processes: ["querying"] }]));
    await writeFile(credentials.PORTAL_PASSWORDS_FILE, JSON.stringify({ [principal]: passwords.analyst_retail }));
    const path = fileURLToPath(new URL("../../../examples/meridian-portal/catalogue.yaml", import.meta.url));
    const { app, repository } = createApp({ env: { ...credentials, PORTAL_CATALOGUE_FILE: path, PORTAL_BACKEND: "sqlite", PORTAL_SQLITE_PATH: ":memory:" }, uiPath: "not-present" });
    const server = app.listen(0);
    context.after(() => { server.close(); repository.close(); });
    const deadline = Date.now() + 5000;
    while (!await repository.catalogueRelease() && Date.now() < deadline) await new Promise((resolve) => setTimeout(resolve, 10));
    assert.ok(await repository.catalogueRelease());
    const base = `http://127.0.0.1:${server.address().port}`;
    const signed = await fetch(`${base}/session`, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ username: principal, password }) });
    assert.equal(signed.status, 200);
    const cookie = signed.headers.getSetCookie()[0].split(";")[0];
    const post = async (route, body) => {
      const response = await fetch(`${base}${route}`, { method: "POST", headers: { "content-type": "application/json", cookie }, body: JSON.stringify(body) });
      assert.equal(response.status, 200);
      return response.json();
    };
    for (const body of [{}, { query: "" }, { query: "  ", limit: 1 }]) {
      const result = await post("/search", body);
      assert.equal(result.searchList.length, count);
      assert.equal(new Set(result.searchList.map((p) => p.PRODUCTID)).size, count);
      assert.ok(result.searchList.every((p) => units.includes(p.UNIT_ID)));
    }
    const all = await post("/getCommunityProducts", {});
    assert.equal(all.searchList.length, count);
    assert.deepEqual(all.typeFilter, ["Table", "View", "API/MCP", "Documents"]);
    assert.deepEqual(all.assetFilter, ["Structured", "Unstructured"]);
    assert.deepEqual(all.usageFilter, ["Analytical", "Operational"]);
    for (const product of all.searchList) {
      assert.ok(all.typeFilter.includes(product.PRODUCTTYPE));
      assert.ok(all.assetFilter.includes(product.ASSETTYPE));
      assert.ok(all.usageFilter.includes(product.USAGEPURPOSE));
      assert.ok(product.DATASETTYPE);
      assert.ok(product.APM_ID);
    }
    for (const [values, field, isDomain] of [[all.communityFilter, "COMMUNITY", false], [all.domainFilter, "DOMAIN", true]]) {
      for (const value of values) {
        const expected = all.searchList.filter((p) => p[field] === value);
        assert.deepEqual((await post("/getCommunityProducts", { searchParam: value, isDomain })).searchList, expected);
      }
    }
    const tree = await (await fetch(`${base}/getDataCommunities`, { headers: { cookie } })).json();
    assert.equal(tree.totalCount, count);
    assert.equal(tree.communityList.reduce((sum, group) => sum + group.count, 0), count);
    assert.ok(tree.communityList.every((group) => group.children.length === group.count && group.children.every((child) => child.kind === "product")));
    for (const route of ["/filterSearchResults", "/sortSearchResults", "/groupSearchResults"]) {
      assert.equal((await post(route, {})).length, count);
    }
    const { parse } = await import("yaml");
    const listed = parse(await readFile(path, "utf8")).products.filter((p) => units.includes(p.unit));
    assert.deepEqual([...all.communityFilter].sort(), [...new Set(listed.map((p) => p.community))].sort());
    for (const [parent, values] of [["DOMAIN", all.domainFilter], ["COMMUNITY", all.communityFilter], ["PRODUCTSOURCE", all.sourceFilter], ["PRODUCTTYPE", all.typeFilter], ["ASSETTYPE", all.assetFilter], ["USAGEPURPOSE", all.usageFilter]]) {
      for (const value of values) {
        const filtered = await post("/filterSearchResults", { filterParams: [{ parent, value, label: value }] });
        assert.deepEqual(filtered.map((p) => p.PRODUCTID), all.searchList.filter((p) => p[parent] === value).map((p) => p.PRODUCTID));
      }
    }
    const [first, second] = all.communityFilter;
    if (second) assert.equal((await post("/filterSearchResults", { filterParams: [{ parent: "COMMUNITY", value: first }, { parent: "COMMUNITY", value: second }] })).length, all.searchList.filter((p) => [first, second].includes(p.COMMUNITY)).length);
    assert.equal((await post("/filterSearchResults", { filterParams: [{ parent: "COMMUNITY", value: first }, { parent: "DOMAIN", value: "No such domain" }] })).length, 0);
    const ranked = await post("/search", { query: "account", kinds: ["product"], limit: 2 });
    assert.deepEqual(ranked.typeFilter, all.typeFilter);
    assert.deepEqual(ranked.assetFilter, all.assetFilter);
    assert.deepEqual(ranked.usageFilter, all.usageFilter);
    assert.ok(ranked.hits.length > 0 && ranked.hits.length <= 2);
    assert.deepEqual(ranked.searchList.map((p) => p.PRODUCTID), ranked.hits.map((hit) => hit.id));
    assert.ok(ranked.hits.every((hit, index, hits) => index === 0 || hits[index - 1].score >= hit.score));
  });
}

test("PORTAL_ASK accepts only on or off; off hides only the link to KGE, and Ask still answers", async (context) => {
  for (const value of ["yes", "OFF", ""]) assert.throws(() => createApp({ env: { PORTAL_ASK: value, PORTAL_BACKEND: "sqlite", PORTAL_SQLITE_PATH: ":memory:" }, catalogueSync: false, uiPath: "not-present" }), /PORTAL_ASK must be on or off/);
  const directory = await mkdtemp(join(tmpdir(), "portal-ask-off-"));
  const path = join(directory, "catalogue.yaml");
  const products = catalogue.products.slice(0, 1).map(({ unit_id, ...product }) => ({ ...product, id: `${unit_id}.${product.table}`, unit: unit_id }));
  await writeFile(path, YAML.stringify({ format: 1, units: ["retail"], products }));
  const { app, repository } = createApp({ env: { ...await localCredentials(directory), PORTAL_ASK: "off", PORTAL_CATALOGUE_FILE: path, PORTAL_BACKEND: "sqlite", PORTAL_SQLITE_PATH: ":memory:" }, uiPath: "not-present" });
  const server = app.listen(0);
  context.after(() => { server.close(); repository.close(); });
  while (!await repository.catalogueRelease()) await new Promise((resolve) => setTimeout(resolve, 1));
  const base = `http://127.0.0.1:${server.address().port}`;
  const signed = await fetch(`${base}/session`, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ username: "analyst_retail", password }) });
  const cookie = signed.headers.getSetCookie()[0].split(";")[0];
  const request = (route, options = {}) => fetch(`${base}${route}`, { headers: { "content-type": "application/json", cookie }, ...options });
  assert.equal((await (await request("/getConfig")).json()).ask, true);
  for (const route of ["/ask", "/api/ask", "/ask-data-product", "/ask-domain"]) {
    const response = await request(route, { method: "POST", body: JSON.stringify({ question: "Who owns the accounts table?" }) });
    assert.equal(response.status, 200, route);
    const body = await response.json();
    assert.equal(typeof body.answer_text, "string", route);
  }
  const feedback = await request("/feedback-add", { method: "POST", body: JSON.stringify({ session_id: 1, user_query_seq_id: 1, domain: "Retail", feedback_rating: 1 }) });
  assert.equal(feedback.status, 201);
  assert.equal((await request("/search", { method: "POST", body: JSON.stringify({}) })).status, 200);
});

test("/fields/:id returns Classification and maps Restricted to Highly Confidential", async (context) => {
  const { classificationLabel, overlay } = await import("../src/catalogue-record.js");
  assert.equal(classificationLabel("Restricted"), "Highly Confidential");
  assert.equal(classificationLabel("  highly   confidential "), "Highly Confidential");
  assert.equal(classificationLabel("highly_confidential"), "Highly Confidential");
  assert.equal(classificationLabel("highly-confidential"), "Highly Confidential");
  assert.equal(classificationLabel("Public"), "Public");
  assert.equal(classificationLabel("unknown"), "");
  assert.equal(classificationLabel(null), "");
  const legacyGoverned = structuredClone(catalogue.products[0]);
  delete legacyGoverned.project;
  delete legacyGoverned.fields[0].classification;
  const overlaid = overlay(
    { ...catalogue.products[0], project: "Legacy Project", fields: [{ ...catalogue.products[0].fields[0], classification: "Restricted" }] },
    legacyGoverned,
  );
  assert.equal(overlaid.project, "Legacy Project");
  assert.equal(overlaid.fields[0].classification, "Restricted");
  const directory = await mkdtemp(join(tmpdir(), "portal-fields-"));
  const path = join(directory, "catalogue.yaml");
  const { unit_id, ...baseProduct } = catalogue.products[0];
  const products = [{
    ...baseProduct,
    id: `${unit_id}.${baseProduct.table}`,
    unit: unit_id,
    fields: [
      { ...baseProduct.fields[0], name: "account_id", classification: "Internal" },
      { ...baseProduct.fields[0], name: "balance_cents", classification: "Restricted" },
      { ...baseProduct.fields[0], name: "opened_at", classification: "" },
    ],
  }];
  await writeFile(path, YAML.stringify({ format: 1, units: ["retail"], products }));
  const { app, repository } = createApp({ env: { ...await localCredentials(directory), PORTAL_CATALOGUE_FILE: path, PORTAL_BACKEND: "sqlite", PORTAL_SQLITE_PATH: ":memory:" }, uiPath: "not-present" });
  const server = app.listen(0);
  context.after(() => { server.close(); repository.close(); });
  while (!await repository.catalogueRelease()) await new Promise((resolve) => setTimeout(resolve, 1));
  const base = `http://127.0.0.1:${server.address().port}`;
  const signed = await fetch(`${base}/session`, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ username: "analyst_retail", password }) });
  const cookie = signed.headers.getSetCookie()[0].split(";")[0];
  const rows = await (await fetch(`${base}/fields/retail.accounts`, { headers: { cookie } })).json();
  assert.deepEqual(rows.map((r) => [r["Field Name"], r.Classification]), [
    ["account_id", "Internal"],
    ["balance_cents", "Highly Confidential"],
    ["opened_at", ""],
  ]);
});
