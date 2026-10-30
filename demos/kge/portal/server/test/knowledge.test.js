import assert from "node:assert/strict";
import test from "node:test";
import { createApp } from "../src/app.js";
import { openSqliteRepository } from "../src/repository/sqlite.js";
import { catalogue } from "./fixtures/catalogue.js";
import { catalogueAsk, catalogueSearch, knowledgeProvider } from "../src/knowledge.js";
import { metadataInput } from "../src/catalogue-record.js";
import { knowledgeIdentity } from "../src/knowledge-identity.js";

function fixture(context) {
  const repository = openSqliteRepository({ PORTAL_SQLITE_PATH: ":memory:" });
  repository.syncCatalogue(catalogue.release, catalogue.products, true);
  let mode = "up";
  let now = 0;
  const calls = [];
  const kge = {
    async identity() {
      if (["down", "identity-only-down", "identity-only-down-refused"].includes(mode)) throw new TypeError("network unavailable");
      if (mode === "denied") throw Object.assign(new Error("denied"), { status: 401 });
      return { principal: "admin", units: ["retail"] };
    },
    async request(req, path, options) {
      calls.push({ path, body: options.body ? JSON.parse(options.body) : undefined, cookie: req.headers.cookie });
      if (mode === "down") throw new TypeError("network unavailable");
      if (mode === "mid-request") throw new TypeError("request unavailable");
      if (["refused", "identity-only-down-refused"].includes(mode)) return Response.json({ detail: "forbidden" }, { status: 403 });
      if (path === "/search") return Response.json({ hits: [{ kind: "product", unit: "retail", id: "retail-accounts", title: "Accounts", score: 0.95, why: { kind: "synonym", text: "deposits", term: "balance" } }, { kind: "column", unit: "retail", id: "retail.accounts.amount", title: "amount", score: .8, why: { kind: "name", text: "amount" } }] });
      if (mode === "simple") return Response.json({ answer_text: "13,287.67", combined: false, figure: 13287.67, behaviour: "figure" });
      if (mode === "abstain") return Response.json({ answer_text: "No governed recipe is available.", combined: false, behaviour: "abstain" });
      return Response.json({ answer_text: "13,287.67", figures: [{ value: 13287.67 }], citations: [{ title: "Guide" }] });
    },
  };
  const { app } = createApp({ repository, kge, catalogueSync: false, env: {}, clock: () => now, uiPath: "not-present" });
  const server = app.listen(0);
  context.after(() => { server.close(); repository.close(); });
  const request = (path, body, method = "POST", cookie = "session=verified") => fetch(`http://127.0.0.1:${server.address().port}${path}`, { method, headers: { "content-type": "application/json", cookie }, ...(body === undefined ? {} : { body: JSON.stringify(body) }) });
  return { repository, calls, request, mode: (value) => { mode = value; }, advance: () => { now += 300001; } };
}

test("one Search and Ask contract uses KGE, falls back with verified units and recovers", async (context) => {
  const f = fixture(context);
  let result = await (await f.request("/api/search", { query: "deposits" })).json();
  assert.equal(result.provider, "kge");
  assert.equal(result.hits[0].why.kind, "synonym");
  assert.equal(result.searchList[0].id, "retail-accounts");
  const governed = await (await f.request("/ask", { question: "Total retail revenue for June 2025" })).json();
  assert.equal(governed.figures[0].value, 13287.67);
  assert.equal(f.calls[0].cookie, "session=verified");
  const legacy = await (await f.request("/search", { searchParam: "deposits", limit: 1 })).json();
  assert.equal(legacy.provider, "kge");
  assert.equal(legacy.searchList[0].PRODUCTID, "retail-accounts");
  assert.equal(legacy.hits[0].why.kind, "synonym");
  assert.deepEqual(f.calls.at(-1).body, { query: "deposits", limit: 1 });
  f.mode("down");
  const response = await f.request("/api/search", { query: "amount" });
  result = await response.json();
  assert.equal(result.provider, "portal");
  assert.equal(result.fallback, true);
  assert.match(response.headers.get("x-portal-notice"), /unavailable/);
  assert.ok(result.hits.length && result.hits.every((hit) => hit.unit === "retail"));
  assert.equal(result.hits[0].kind, "column");
  const answer = await (await f.request("/ask", { question: "Total revenue?" })).json();
  assert.deepEqual(answer.figures, []);
  assert.match(answer.notice, /catalogue/);
  assert.equal((await f.request("/api/search", { query: "accounts" }, "POST", "session=unknown")).status, 502);
  f.mode("up");
  assert.equal((await (await f.request("/api/search", { query: "deposits" })).json()).fallback, false);
  f.mode("refused");
  assert.equal((await f.request("/ask", { question: "anything" })).status, 403);
  f.mode("down"); f.advance();
  assert.equal((await f.request("/api/search", { query: "accounts" })).status, 502);
});

test("authentication refusal evicts the outage cache and logout forgets it", async () => {
  let status;
  const auth = knowledgeIdentity({ identity: async () => { if (status) throw Object.assign(new Error("failure"), { status }); return { principal: "person", units: ["retail"] }; } });
  const req = { headers: { cookie: "session=person" } };
  await auth.identity(req);
  status = 401;
  await assert.rejects(auth.identity(req));
  status = 503;
  await assert.rejects(auth.identity(req));
  status = undefined;
  await auth.identity(req);
  auth.forget(req); status = 503;
  await assert.rejects(auth.identity(req));
});

test("KGE single figures and abstentions keep their answer when optional arrays are absent", async (context) => {
  const f = fixture(context);
  f.mode("simple");
  const answer = await (await f.request("/ask", { question: "Total retail revenue for June 2025" })).json();
  assert.equal(answer.provider, "kge");
  assert.equal(answer.figure, 13287.67);
  assert.deepEqual(answer.figures, []);
  assert.deepEqual(answer.citations, []);
  f.mode("abstain");
  const abstention = await (await f.request("/ask", { question: "Unknown recipe" })).json();
  assert.equal(abstention.provider, "kge");
  assert.equal(abstention.behaviour, "abstain");
  assert.equal(abstention.fallback, false);
});

test("catalogue answers identify owners and fields but never return data or figures", () => {
  const products = [catalogue.products[0]];
  for (const question of ["Who owns Retail Accounts?", "Which products hold amount?", "What does Retail Accounts contain?"]) {
    const result = catalogueAsk(products, { question });
    assert.equal(result.behaviour, "catalogue");
    assert.match(result.answer_text, /Retail Accounts/);
    assert.deepEqual(result.figures, []);
    assert.deepEqual(result.citations, []);
  }
  for (const question of ["Total retail revenue for June 2025", "How many records in Retail Accounts?", "Show amount figures", "Select amount from accounts"]) assert.equal(catalogueAsk(products, { question }).behaviour, "abstain_catalogue_only");
  assert.equal(catalogueSearch(products, { query: "amount", limit: 20 }).hits[0].kind, "column");
});

test("empty search browses products without columns consuming the result limit", () => {
  const products = Array.from({ length: 10 }, (_, i) => ({ ...catalogue.products[0], id: `retail.product${i}` }));
  const hits = catalogueSearch(products, { query: "", limit: 10 }).hits;
  assert.equal(hits.length, 10);
  assert.ok(hits.every((hit) => hit.kind === "product"));
  assert.equal(new Set(hits.map((hit) => hit.id)).size, 10);
  assert.ok(catalogueSearch(products, { query: "", limit: 10, kinds: ["column"] }).hits.every((hit) => hit.kind === "column"));
});

test("portal metadata survives a release refresh and KGE overlays only governed fields", async (context) => {
  const f = fixture(context);
  const patch = { description: "Portal description", product_owner: "business_team", badges: ["Featured"], fields: [{ name: "amount", business_name: "Posted amount", description: "Portal field" }] };
  assert.equal((await f.request("/api/admin/products/retail-accounts/metadata", patch, "PATCH")).status, 200);
  assert.equal((await f.request("/api/admin/products/retail-accounts/metadata", { records: 9 }, "PATCH")).status, 400);
  const next = structuredClone(catalogue);
  next.release = "next";
  next.products[0].records = 99;
  next.products[0].lineage.upstream = ["wealth-holdings"];
  next.products[0].fields[1].terms = ["Governed term"];
  next.products[0].fields.push({ ...next.products[0].fields[1], name: "posted_at", business_name: "Posting date" });
  next.products[0].fields[1].quality.fail_percent = 7;
  f.repository.syncCatalogue(next.release, next.products, true);
  f.repository.setGoverned(next);
  const product = await (await f.request("/api/products/retail-accounts", undefined, "GET")).json();
  assert.equal(product.description, patch.description);
  assert.equal(product.records, 99);
  assert.deepEqual(product.lineage.upstream, []);
  assert.deepEqual(product.badges, ["FEATURED", "Certified"]);
  assert.equal(product.fields[1].business_name, "Posted amount");
  assert.deepEqual(product.fields[1].terms, ["Governed term"]);
  assert.equal(product.fields[1].quality.fail_percent, 7);
  assert.equal(f.repository.product(product.id, ["retail"]).records, 99);
  assert.equal(f.repository.product(product.id, ["retail"]).fields.at(-1).name, "posted_at");
  const input = await (await f.request("/api/admin/onboarding-input", { client: "meridian" })).json();
  assert.equal(input.products.length, 1);
  assert.equal(input.products[0].description, patch.description);
  assert.ok(!("records" in input.products[0]));
  assert.ok(!("terms" in input.products[0].fields[1]));
  assert.equal(metadataInput([product], "meridian").client, "meridian");
});

test("SQLite keeps edited business records and activity after a refresh and restart", async (context) => {
  const { mkdir, mkdtemp, rm } = await import("node:fs/promises");
  const { fileURLToPath } = await import("node:url");
  const { join } = await import("node:path");
  const root = fileURLToPath(new URL("../../../build/review/", import.meta.url));
  await mkdir(root, { recursive: true });
  const scratch = await mkdtemp(join(root, "portal-record-"));
  context.after(() => rm(scratch, { recursive: true, force: true }));
  const env = { PORTAL_SQLITE_PATH: join(scratch, "record.db") };
  let repository = openSqliteRepository(env);
  repository.syncCatalogue(catalogue.release, catalogue.products, true);
  repository.updateMetadata("retail-accounts", { description: "Business record" });
  repository.addFavourite("person", "retail-accounts");
  repository.feedback("person", "retail-accounts", 5, "Useful");
  repository.request("person", "retail-accounts", "access");
  repository.syncCatalogue("new-seed", [], true);
  repository.close();
  repository = openSqliteRepository(env);
  assert.equal(repository.product("retail-accounts", ["retail"]), undefined);
  repository.syncCatalogue("restored", catalogue.products, true);
  assert.equal(repository.product("retail-accounts", ["retail"]).description, "Business record");
  assert.equal(repository.favourites("person", ["retail"]).length, 1);
  assert.equal(repository.requests("person", ["retail"]).length, 1);
  assert.equal(repository.averageFeedback("retail-accounts").average, 5);
  repository.close();
});

for (const reason of ["name", "description", "passage"]) test(`KGE ${reason} reasons and restriction flags survive the provider`, async () => {
  const hit = { kind: "column", unit: "retail", id: "retail.customer.email", title: "email", score: .8,
    restricted: true, why: { kind: reason, text: "Matching text", ...(reason === "passage" ? { similarity: .8, chunk: "doc:1" } : {}) } };
  const provider = knowledgeProvider("search", { search: () => { throw new Error("Unexpected fallback"); } },
    { request: async () => Response.json({ hits: [hit] }) });
  const result = await provider.run({ portalIdentity: { units: ["retail"] } }, { query: "email" });
  assert.equal(result.provider, "kge");
  assert.equal(result.fallback, false);
  assert.deepEqual(result.hits, [hit]);
});

test("portal catalogue descriptions are not labelled as retrieved passages", () => {
  const { hits } = catalogueSearch(catalogue.products, { query: "governed", limit: 20 });
  assert.ok(hits.length);
  assert.ok(hits.every((hit) => hit.why.kind === "description" && hit.why.text.includes("governed")));
});

test("app refresh seeds existing records without overwriting portal descriptions", async (context) => {
  const repository = openSqliteRepository({ PORTAL_SQLITE_PATH: ":memory:" });
  repository.syncCatalogue(catalogue.release, catalogue.products, true);
  repository.updateMetadata("retail-accounts", { description: "Portal edit" });
  const next = structuredClone(catalogue);
  next.release = "external-job-release";
  next.products[0].description = "Externally accepted description";
  next.products[0].quality_score = 17;
  let refreshed;
  const complete = new Promise((resolve) => { refreshed = resolve; });
  const sync = repository.syncCatalogue;
  repository.syncCatalogue = (...args) => { const result = sync(...args); refreshed(); return result; };
  const kge = { identity: async () => ({ principal: "admin", units: ["retail"] }), catalogueIdentity: async () => next, catalogue: async () => next };
  const { app, stopCatalogueSync } = createApp({ repository, kge, env: {}, uiPath: "not-present" });
  const server = app.listen(0);
  context.after(() => { stopCatalogueSync(); server.close(); repository.close(); });
  await complete;
  const response = await fetch(`http://127.0.0.1:${server.address().port}/api/products/retail-accounts`, { headers: { cookie: "session=verified" } });
  const product = await response.json();
  assert.equal(product.description, "Portal edit");
  assert.equal(product.quality_score, 17);
  assert.equal(repository.product(product.id, ["retail"]).quality_score, 17);
});

test("mid-request outage carries notice in Search and Ask response and header", async (context) => {
  const f = fixture(context);
  f.mode("mid-request");
  for (const [path, body] of [["/api/search", { query: "accounts" }], ["/ask", { question: "Who owns Retail Accounts?" }]]) {
    const response = await f.request(path, body);
    const result = await response.json();
    assert.equal(response.status, 200);
    assert.equal(result.provider, "portal");
    assert.equal(result.fallback, true);
    assert.match(result.notice, /KGE is unavailable/);
    assert.equal(response.headers.get("x-portal-notice"), result.notice);
  }
});

test("KGE raw hits are filtered to readable units independently of upstream", async () => {
  const hit = { kind: "product", unit: "wealth", id: "wealth.hidden", title: "Hidden", score: .8, why: { kind: "name", text: "Hidden" } };
  const provider = knowledgeProvider("search", { search: () => { throw new Error("Unexpected fallback"); } }, { request: async () => Response.json({ hits: [hit, { ...hit, unit: "retail", id: "retail.visible" }] }) });
  const result = await provider.run({ portalIdentity: { units: ["retail"] } }, { query: "hidden" });
  assert.deepEqual(result.hits.map((h) => h.id), ["retail.visible"]);
});

for (const mode of ["up", "mid-request"]) test(`unpublished products and columns are absent from raw hits in ${mode} mode`, async (context) => {
  const f = fixture(context);
  f.mode(mode);
  f.repository.setPublished("retail-accounts", false);
  const result = await (await f.request("/api/search", { query: "accounts amount" })).json();
  assert.equal(result.provider, mode === "up" ? "kge" : "portal");
  assert.deepEqual(result.hits, []);
  assert.deepEqual(result.searchList, []);
});

test("catalogue Ask recognises product names but refuses mixed data requests", () => {
  const products = [{ ...catalogue.products[0], name: "Monthly Revenue", table: "monthly_revenue" }];
  assert.equal(catalogueAsk(products, { question: "Who owns Monthly Revenue?" }).behaviour, "catalogue");
  for (const question of ["Describe total Monthly Revenue", "Who owns Monthly Revenue and what is the sum?", "Describe the average for Monthly Revenue"]) {
    const result = catalogueAsk(products, { question });
    assert.equal(result.behaviour, "abstain_catalogue_only");
    assert.deepEqual(result.figures, []);
  }
});

test("onboarding export excludes quality score and every governed property", () => {
  const input = metadataInput(catalogue.products, "meridian");
  for (const product of input.products) {
    assert.deepEqual(Object.keys(product).sort(), ["id", "unit", "table", "name", "description", "product_owner", "business_owner", "badges", "domain", "community", "application", "product_type", "fields"].sort());
    for (const field of product.fields) assert.deepEqual(Object.keys(field).sort(), ["name", "business_name", "description"].sort());
  }
});

test("outage sign-in and sign-out return 502 and sign-out evicts cached identity", async (context) => {
  const f = fixture(context);
  await f.request("/api/search", { query: "accounts" });
  f.mode("down");
  for (const [method, body] of [["POST", { username: "admin", password: "test" }], ["DELETE", undefined]]) {
    const response = await f.request("/session", body, method);
    assert.equal(response.status, 502);
    assert.match((await response.json()).detail, /KGE is unavailable/);
  }
  assert.equal((await f.request("/api/search", { query: "accounts" })).status, 502);
});


test("export does not invent a field description from its business name", () => {
  const products = structuredClone(catalogue.products);
  products[0].fields[0].description = "";
  products[0].fields[0].business_name = "New business name";
  const field = metadataInput(products, "meridian").products[0].fields[0];
  assert.equal(field.business_name, "New business name");
  assert.equal("description" in field, false);
});

test("withdrawn products hide owned state and onboarding export until reintroduced", async (context) => {
  const f = fixture(context);
  const id = "retail-accounts";
  await f.request(`/api/favourites/${id}`, {}, "POST");
  await f.request("/api/requests", { productId: id });
  await f.request("/api/feedback", { productId: id, rating: 5 });
  await f.request(`/api/admin/products/${id}/metadata`, { description: "Keep my edit" }, "PATCH");
  f.repository.setGoverned({ ...catalogue, release: "withdrawn", products: [] });
  f.repository.syncCatalogue("withdrawn", [], true);
  assert.deepEqual(await (await f.request("/api/products", undefined, "GET")).json(), []);
  assert.deepEqual(await (await f.request("/api/favourites", undefined, "GET")).json(), []);
  assert.deepEqual(await (await f.request("/api/requests", undefined, "GET")).json(), []);
  assert.equal((await f.request(`/api/products/${id}`, undefined, "GET")).status, 404);
  assert.equal((await f.request(`/api/favourites/${id}`, {})).status, 404);
  assert.equal((await f.request("/api/requests", { productId: id })).status, 404);
  assert.deepEqual((await (await f.request("/api/admin/onboarding-input", { client: "meridian" })).json()).products, []);
  const returned = structuredClone(catalogue);
  returned.release = "returned";
  returned.products[0].records = 73;
  f.repository.setGoverned(returned);
  f.repository.syncCatalogue(returned.release, returned.products, true);
  const product = await (await f.request(`/api/products/${id}`, undefined, "GET")).json();
  assert.equal(product.description, "Keep my edit");
  assert.equal(product.records, 73);
  assert.equal((await (await f.request("/api/favourites", undefined, "GET")).json()).length, 1);
  assert.equal((await (await f.request("/api/requests", undefined, "GET")).json()).length, 1);
});

test("admins cannot set KGE-owned badges, including case and whitespace variants", async (context) => {
  const f = fixture(context);
  for (const badge of ["CERTIFIED", " certified ", "GOVERNED", "RESTRICTED", "PII", "CERTIFIED\u200b", "C\u200bERTIFIED", "СERTIFIED", "CERTIFIED\u00a0", "ＣＥＲＴＩＦＩＥＤ", "GΟVERNED", "REЅTRICTED", "РІI", "certıfied", "CERTIFIED\ufe0f"]) {
    const response = await f.request("/api/admin/products/retail-accounts/metadata", { badges: [badge] }, "PATCH");
    assert.equal(response.status, 400);
    assert.match(await response.text(), /portal-owned badges/);
  }
  assert.equal((await f.request("/api/admin/products/retail-accounts/metadata", { badges: ["Featured"] }, "PATCH")).status, 200);
});

test("data outages return unavailable and successful data clears an identity fallback notice", async (context) => {
  const f = fixture(context);
  await f.request("/api/products", undefined, "GET");
  f.mode("down");
  for (const [path, body, method] of [["/api/preview/retail-accounts", undefined, "GET"], ["/api/export/retail-accounts", undefined, "GET"], ["/api/query", { unit: "retail", sql: "SELECT 1" }, "POST"], ["/get-data-preview", { productId: "retail-accounts" }, "POST"]]) {
    const response = await f.request(path, body, method);
    assert.equal(response.status, 503);
    assert.match(await response.text(), /KGE is unavailable/);
  }
  f.mode("identity-only-down");
  const response = await f.request("/api/preview/retail-accounts", undefined, "GET");
  assert.equal(response.status, 200);
  assert.equal(response.headers.get("x-portal-notice"), null);
  f.mode("identity-only-down-refused");
  const refused = await f.request("/api/query", { unit: "retail", sql: "DELETE FROM accounts" });
  assert.equal(refused.status, 403);
  assert.equal(refused.headers.get("x-portal-notice"), null);
});
