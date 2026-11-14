import assert from "node:assert/strict";
import test from "node:test";
import { createApp } from "../src/app.js";
import { openSqliteRepository } from "../src/repository/sqlite.js";
import { catalogue } from "./fixtures/catalogue.js";
import { catalogueAsk, catalogueSearch, knowledgeProvider, localKnowledge, productScope } from "../src/knowledge.js";
import { metadataInput } from "../src/catalogue-record.js";
import { knowledgeIdentity } from "../src/knowledge-identity.js";

const ADMIN = "admin.meridian@example.com";

function fixture(context, env = {}) {
  const repository = openSqliteRepository({ PORTAL_SQLITE_PATH: ":memory:" });
  repository.syncCatalogue(catalogue.release, catalogue.products, true);
  repository.setRole(ADMIN, "admin");
  let mode = "up";
  let now = 0;
  const calls = [];
  const kge = {
    async identity() {
      if (["down", "identity-only-down", "identity-only-down-refused"].includes(mode)) throw new TypeError("network unavailable");
      if (mode === "denied") throw Object.assign(new Error("denied"), { status: 401 });
      return { principal: ADMIN, units: ["retail"] };
    },
    async request(req, path, options) {
      calls.push({ path, body: options.body ? JSON.parse(options.body) : undefined, cookie: req.headers.cookie });
      if (mode === "down") throw new TypeError("network unavailable");
      if (mode === "mid-request") throw new TypeError("request unavailable");
      if (["refused", "identity-only-down-refused"].includes(mode)) return Response.json({ detail: "forbidden" }, { status: 403 });
      if (path === "/search") return Response.json({ hits: [{ kind: "product", unit: "retail", id: "retail-accounts", title: "Accounts", score: 0.95, why: { kind: "synonym", text: "deposits", term: "balance" } }, { kind: "column", unit: "retail", id: "retail.accounts.amount", title: "amount", score: .8, why: { kind: "name", text: "amount" } }] });
      if (mode === "unavailable") return Response.json({ answer_text: "A governed answer", behaviour: "figure", notice: "The assistant is unavailable; showing governed answers." });
      if (mode === "simple") return Response.json({ answer_text: "13,287.67", combined: false, figure: 13287.67, behaviour: "figure" });
      if (mode === "abstain") return Response.json({ answer_text: "No governed recipe is available.", combined: false, behaviour: "abstain" });
      return Response.json({ answer_text: "13,287.67", figures: [{ value: 13287.67 }], citations: [{ title: "Guide" }] });
    },
  };
  const { app } = createApp({ repository, kge, catalogueSync: false, env, clock: () => now, uiPath: "not-present" });
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
  assert.equal(result.hits[0].kind, "product");
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
  for (const question of ["Who owns Retail Accounts?", "Which fields are in retail.accounts?", "What does Retail Accounts contain?"]) {
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
  const kge = { identity: async () => ({ principal: ADMIN, units: ["retail"] }), catalogueIdentity: async () => next, catalogue: async () => next };
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

for (const flag of ["on", "off"]) test(`mid-request outage carries a notice without an app name in Search and Ask, Context ${flag}`, async (context) => {
  const f = fixture(context, { PORTAL_APP_CONTEXT: flag });
  f.mode("mid-request");
  for (const [path, body] of [["/api/search", { query: "accounts" }], ["/ask", { question: "Who owns Retail Accounts?" }]]) {
    const response = await f.request(path, body);
    const result = await response.json();
    assert.equal(response.status, 200);
    assert.equal(result.provider, "portal");
    assert.equal(result.fallback, true);
    assert.match(result.notice, /Live data is unavailable/);
    assert.doesNotMatch(result.notice, /Context/);
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
    assert.deepEqual(Object.keys(product).sort(), ["id", "unit", "table", "name", "description", "product_owner", "business_owner", "product_owner_email", "business_owner_email", "badges", "domain", "community", "application", "product_type", "fields"].sort());
    for (const field of product.fields) assert.deepEqual(Object.keys(field).sort(), ["name", "business_name", "description"].sort());
  }
});

test("outage sign-in and sign-out return 502 and sign-out evicts cached identity", async (context) => {
  const f = fixture(context, { PORTAL_CLIENT_NAME: "Meridian" });
  await f.request("/api/search", { query: "accounts" });
  f.mode("down");
  for (const [method, body] of [["POST", { username: ADMIN, password: "test" }], ["DELETE", undefined]]) {
    const response = await f.request("/session", body, method);
    assert.equal(response.status, 502);
    assert.equal((await response.json()).detail, "Meridian ALTA is unavailable.");
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
  const f = fixture(context, { PORTAL_APP_CONTEXT: "on", PORTAL_TABLE_QUERY: "on", PORTAL_TABLE_PREVIEW: "on" });
  await f.request("/api/products", undefined, "GET");
  f.mode("down");
  for (const [path, body, method] of [["/api/preview/retail-accounts", undefined, "GET"], ["/api/export/retail-accounts", undefined, "GET"], ["/api/query", { unit: "retail", sql: "SELECT 1" }, "POST"], ["/get-data-preview", { productId: "retail-accounts" }, "POST"]]) {
    const response = await f.request(path, body, method);
    assert.equal(response.status, 503);
    assert.deepEqual(await response.json(), { detail: "Context is unavailable." });
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

test("catalogue exact subjects respect product scope and preserve metadata", () => {
  const account = { ...catalogue.products[0], product_owner: "Account Steward", business_owner: "Account Business" };
  const revenue = { ...account, id: "retail.monthly_revenue", table: "monthly_revenue", name: "Monthly Revenue", product_owner: "Revenue Steward" };
  const branch = { ...account, id: "retail.branch", table: "branch", name: "Branch", product_owner: "Branch Steward" };
  const productTable = { ...account, id: "retail.product", table: "product", name: "Product" };
  const lending = { ...catalogue.products[1], product_owner: "Lending Steward", business_owner: "Lending Business" };
  const readable = [account, revenue, branch, productTable, lending];
  for (const question of [
    "Who is the data owner?",
    "Who owns this product?",
    "Who owns this data product?",
    "Who owns the data product?",
    "Who is responsible for the current table?",
    "Who owns Accounts?",
    "Who owns Account?",
    "Which fields relate to credit risk?",
    "Does this table hold claims?",
    "Which columns are used by finance?",
    "What are the columns?",
  ]) {
    const answer = catalogueAsk(readable, { question, productId: account.id });
    assert.equal(answer.behaviour, "abstain", question);
    assert.match(answer.answer_text, /^Catalogue only:/);
    assert.deepEqual(answer.products, ["retail.accounts"]);
    assert.equal(answer.productId, account.id);
    assert.doesNotMatch(answer.answer_text, /Account Steward|Account Business|contains these fields/);
  }
  for (const question of ["Who owns Retail Accounts?", "Who owns RETAIL-ACCOUNTS?", "Who owns retail.accounts?", "Which fields are in rEtAiL aCcOuNtS?"]) {
    const answer = catalogueAsk(readable, { question, productId: account.id });
    assert.equal(answer.behaviour, "catalogue", question);
    assert.match(answer.answer_text, /Retail Accounts/);
  }
  for (const question of ["Who owns SME Lending?", "Who owns SME data?"]) {
    const answer = catalogueAsk(readable, { question, products: ["retail.accounts"] });
    assert.equal(answer.behaviour, "abstain", question);
    assert.doesNotMatch(answer.answer_text, /Lending Steward|Lending Business/);
    assert.doesNotMatch(answer.answer_text, /Account Steward|Account Business|scoped to/);
    assert.deepEqual(answer.products, ["retail.accounts"]);
  }
  const fields = catalogueAsk(readable, { question: "What does SME Lending contain?", productId: account.id });
  assert.equal(fields.behaviour, "abstain");
  assert.doesNotMatch(fields.answer_text, /SME Lending contains these fields/);
  assert.doesNotMatch(fields.answer_text, /Retail Accounts/);
  const retailOnly = [account, revenue, branch, productTable];
  for (const question of ["Who owns the sme client table?", "Who owns the household table?", "Who owns wealth data?", "Who owns wealth accounts?", "Describe wealth data", "Who owns treasury data?", "Who is the owner of wealth data?", "Who is responsible for wealth data?", "Who owns wealth data in this product?"]) {
    const answer = catalogueAsk(retailOnly, { question, productId: account.id });
    assert.equal(answer.behaviour, "abstain", question);
    assert.doesNotMatch(answer.answer_text, /Account Steward|Account Business|governed description|Last refresh|scoped to|outside.*scope/i);
  }
  const figure = catalogueAsk(readable, { products: ["retail.monthly_revenue"], question: "Total Monthly Revenue?" });
  assert.equal(figure.behaviour, "abstain");
  assert.match(figure.answer_text, /require Context/);
  assert.doesNotMatch(figure.answer_text, /scoped to|outside.*scope/i);
  const scope = { products: ["retail.accounts", "retail.monthly_revenue"], domain: "Retail" };
  assert.equal(catalogueAsk(readable, { ...scope, question: "Who owns Monthly Revenue?" }).behaviour, "catalogue");
  for (const [question, owner] of [["Who owns Branch?", "Branch Steward"], ["Who owns SME Lending?", "Lending Steward"]]) {
    const answer = catalogueAsk(readable, { ...scope, question });
    assert.equal(answer.behaviour, "abstain");
    assert.ok(!answer.answer_text.includes(owner));
    assert.doesNotMatch(answer.answer_text, /Account Steward|scoped to/);
    assert.equal(answer.domain, "Retail");
    assert.deepEqual(answer.products, scope.products);
  }
  const duplicate = catalogueAsk(readable, { products: ["retail.accounts", "retail-accounts"], question: "Which fields are in retail-accounts?" });
  assert.equal(duplicate.behaviour, "catalogue");
  assert.deepEqual(duplicate.products, ["retail.accounts"]);
  assert.equal(catalogueAsk(readable, { products: [], question: "What are the columns?" }).behaviour, "abstain");
  const noDefault = catalogueAsk(readable, { products: [], question: "Who owns Branch?" });
  assert.equal(noDefault.behaviour, "abstain");
  assert.doesNotMatch(noDefault.answer_text, /Branch Steward/);
  assert.deepEqual(noDefault.products, []);
  assert.equal(catalogueAsk(readable, { domain: "All data assets", question: "Who owns Branch?" }).behaviour, "catalogue");
  assert.throws(() => catalogueAsk(readable, { products: ["unknown.table"], question: "Owner?" }), { status: 404 });
  assert.throws(() => catalogueAsk(readable, { products: "retail.accounts", question: "Owner?" }), { status: 400 });
  assert.throws(() => catalogueAsk(readable, { productId: 42, question: "Owner?" }), { status: 404 });
  assert.deepEqual(catalogueAsk(readable, { productId: "retail.accounts", productIds: [revenue.id], question: "Owner?" }).products, []);
});

test("catalogue ownership requires an exact readable subject within scope", () => {
  const retail = { ...catalogue.products[0], product_owner: "Retail Steward", business_owner: "Retail Business" };
  const wealth = { ...retail, id: "wealth-accounts", unit_id: "wealth", name: "Wealth Accounts", product_owner: "Wealth Steward", business_owner: "Wealth Business" };
  for (const context of [{}, { products: ["retail.accounts"] }, { productId: retail.id }, { products: [] }]) {
    const question = "Who owns wealth accounts?";
    const unavailable = catalogueAsk([retail], { question, ...context });
    assert.notEqual(unavailable.behaviour, "catalogue");
    assert.doesNotMatch(unavailable.answer_text, /Retail Steward|Retail Business|scoped to|outside.*scope/i);
    const readable = catalogueAsk([retail, wealth], { question, ...context });
    assert.equal(readable.behaviour, Object.keys(context).length ? "abstain" : "catalogue");
    if (!Object.keys(context).length) assert.match(readable.answer_text, /Wealth Accounts: Data domain owner: Wealth Business; Data steward: Wealth Steward\./);
    else assert.doesNotMatch(readable.answer_text, /Wealth Steward|Wealth Business/);
    assert.doesNotMatch(readable.answer_text, /Retail Steward|Retail Business/);
  }
});

test("catalogue field questions do not replace an unknown subject with a shared table word", () => {
  const account = catalogue.products[0];
  for (const context of [{}, { products: ["retail.accounts"] }, { productId: account.id }]) {
    for (const question of ["What fields does the wealth accounts table hold?",
    "What fields do wealth accounts have?", "Which columns does wealth data contain?", "Which fields are in wealth accounts?", "Show columns for wealth data"]) {
      const answer = catalogueAsk([account], { question, ...context });
      assert.equal(answer.behaviour, Object.keys(context).length ? "abstain" : "abstain_catalogue_only", question);
      assert.doesNotMatch(answer.answer_text, /Retail Accounts|amount \(amount\)|contains these fields/);
      assert.deepEqual(answer.figures, []);
      assert.deepEqual(answer.citations, []);
    }
    for (const question of ["What fields does Retail Accounts hold?", "Which columns does RETAIL-ACCOUNTS contain?", "What does Retail Accounts contain?", "Which fields are in Retail Accounts?", "Which columns are in RETAIL.ACCOUNTS?"]) {
      const answer = catalogueAsk([account], { question, ...context });
      assert.equal(answer.behaviour, "catalogue", question);
      assert.match(answer.answer_text, /Retail Accounts contains these fields:.*amount \(amount\)/);
    }
  }
  for (const question of ["What fields does this table hold?", "Which columns does the selected product contain?", "Which fields relate to credit risk?", "Which columns are used by finance?", "Does this table hold claims?", "What fields does the Retail Accounts table hold?", "Which columns does retail data contain?", "Show columns for Retail Accounts", "Which products hold amount?"]) {
    for (const context of [{}, { productId: account.id }]) {
      const answer = catalogueAsk([account], { question, ...context });
      assert.equal(answer.behaviour, context.productId ? "abstain" : "abstain_catalogue_only", question);
      assert.match(answer.answer_text, /^Catalogue only:/);
      assert.doesNotMatch(answer.answer_text, /Retail Accounts contains these fields/);
    }
  }
});

test("Ask fallback does not substitute retail fields for an unavailable subject", async (context) => {
  const f = fixture(context);
  await f.request("/ask", { question: "What does Retail Accounts contain?" });
  for (const mode of ["mid-request", "down", "mid-request"]) {
    f.mode(mode);
    for (const page of [{}, { products: ["retail.accounts"] }]) {
      for (const question of ["What fields does the wealth accounts table hold?",
    "What fields do wealth accounts have?", "Which columns does wealth data contain?", "Which fields are in wealth accounts?", "Show columns for wealth data"]) {
        const response = await f.request("/ask", { question, ...page });
        assert.equal(response.status, 200);
        const answer = await response.json();
        assert.equal(answer.behaviour, page.products ? "abstain" : "abstain_catalogue_only", `${mode}: ${question}`);
        assert.equal(answer.provider, "portal");
        assert.equal(answer.fallback, true);
        assert.doesNotMatch(answer.answer_text, /Retail Accounts|amount \(amount\)|contains these fields/);
        assert.deepEqual(answer.figures, []);
        assert.deepEqual(answer.citations, []);
      }
      const matching = await (await f.request("/ask", { question: "What fields does Retail Accounts hold?", ...page })).json();
      assert.equal(matching.behaviour, "catalogue");
      assert.match(matching.answer_text, /Retail Accounts contains these fields/);
    }
    const contextual = await (await f.request("/ask-data-product", { productId: "retail-accounts", question: "Which fields relate to credit risk?" })).json();
    assert.equal(contextual.behaviour, "abstain");
    assert.match(contextual.answer_text, /^Catalogue only:/);
    assert.doesNotMatch(contextual.answer_text, /Retail Accounts contains these fields/);
  }
  f.mode("up");
  const recovered = await (await f.request("/ask", { question: "Which columns does wealth data contain?" })).json();
  assert.equal(recovered.provider, "kge");
  assert.equal(recovered.fallback, false);
});

test("portal-alone Ask does not offer descriptions, refresh metadata or SLA details", () => {
  const account = catalogue.products[0];
  for (const question of ["Describe wealth data", "Describe this product", "What is the description of Retail Accounts?", "Tell me about Retail Accounts", "What is the refresh cadence?", "What is the refresh cadence of the selected product?", "What is the SLA?"]) {
    for (const context of [{}, { productId: account.id }, { products: [] }]) {
      const answer = catalogueAsk([account], { question, ...context });
      assert.notEqual(answer.behaviour, "catalogue", question);
      assert.ok(!answer.answer_text.includes(account.description));
      assert.ok(!answer.answer_text.includes(account.last_refresh));
      assert.doesNotMatch(answer.answer_text, /Last refresh|scoped to|outside.*scope/i);
      assert.deepEqual(answer.figures, []);
      assert.deepEqual(answer.citations, []);
    }
  }
});

test("upstream Assist still supplies details while unavailable Assist does not invent them", async () => {
  const account = catalogue.products[0];
  const req = { portalIdentity: { units: ["retail"] } };
  const local = localKnowledge({ listProducts: async () => [account], published: async () => true });
  const body = { question: "What is the refresh cadence?", products: ["retail.accounts"] };
  const details = `${account.description}; last refresh ${account.last_refresh}`;
  const upstream = { request: async (_req, path, options) => {
    assert.equal(path, "/assist");
    assert.deepEqual(JSON.parse(options.body), body);
    return Response.json({ answer_text: details, behaviour: "catalogue", source: "catalogue" });
  } };
  const answer = await knowledgeProvider("ask", local, upstream).run(req, body);
  assert.equal(answer.answer_text, details);
  assert.equal(answer.provider, "kge");
  assert.equal(answer.fallback, false);
  const fallback = await knowledgeProvider("ask", local, { request: async () => { throw new TypeError("outage"); } }).run(req, body);
  assert.equal(fallback.behaviour, "abstain");
  assert.equal(fallback.fallback, true);
  assert.ok(!fallback.answer_text.includes(account.description));
  assert.ok(!fallback.answer_text.includes(account.last_refresh));
});

test("Ask normalizes product scope before KGE and preserves it through an outage", async (context) => {
  const f = fixture(context);
  const product = await (await f.request("/ask-data-product", { productId: "retail.accounts", question: "Who owns this product?" })).json();
  assert.equal(product.productId, "retail-accounts");
  assert.deepEqual(product.products, ["retail.accounts"]);
  assert.deepEqual(f.calls.at(-1).body, { question: "Who owns this product?", products: ["retail.accounts"] });
  const explore = await (await f.request("/ask-domain", { productIds: ["retail-accounts"], domain: "Retail", question: "What are the columns?" })).json();
  assert.equal(explore.domain, "Retail");
  assert.deepEqual(explore.products, ["retail.accounts"]);
  f.mode("mid-request");
  const degraded = await (await f.request("/ask-domain", { productIds: ["retail.accounts"], domain: "Retail", question: "Which columns are in Retail Accounts?" })).json();
  assert.equal(degraded.behaviour, "catalogue");
  assert.equal(degraded.fallback, true);
  assert.deepEqual(degraded.products, ["retail.accounts"]);
  assert.equal(degraded.domain, "Retail");
  for (const mode of ["mid-request", "down", "mid-request"]) {
    f.mode(mode);
    for (const question of ["Who owns wealth data?", "Who owns wealth accounts?", "Describe wealth data", "Who owns this product?", "Which fields relate to credit risk?"]) {
      const repeated = await (await f.request("/ask-data-product", { productId: "retail-accounts", question })).json();
      assert.equal(repeated.behaviour, "abstain");
      assert.match(repeated.answer_text, /^Catalogue only:/);
      assert.equal(repeated.fallback, true);
      assert.deepEqual(repeated.products, ["retail.accounts"]);
      if (question.includes("wealth")) assert.doesNotMatch(repeated.answer_text, /Steward|Administrator|governed description|Last refresh|scoped to|outside.*scope/i);
    }
  }
  const empty = await (await f.request("/ask-domain", { productIds: [], question: "Who owns Retail Accounts?" })).json();
  assert.equal(empty.behaviour, "abstain");
  assert.deepEqual(empty.products, []);
  f.mode("up");
  const recovered = await (await f.request("/ask-data-product", { productId: "retail-accounts", question: "Who owns wealth accounts?" })).json();
  assert.equal(recovered.provider, "kge");
  assert.equal(recovered.fallback, false);
  assert.equal(recovered.answer_text, "13,287.67");
  assert.deepEqual(f.calls.at(-1).body, { question: "Who owns wealth accounts?", products: ["retail.accounts"] });
  await f.request("/ask-domain", { domain: "All data assets", question: "Owner?" });
  assert.deepEqual(f.calls.at(-1).body, { question: "Owner?" });
});


test("local catalogue scope never admits unreadable or unpublished products", async () => {
  const local = localKnowledge({
    listProducts: async (units) => catalogue.products.filter((p) => units.includes(p.unit_id)),
    published: async (id) => id === "retail-accounts" ? 0 : true,
  });
  const req = { portalIdentity: { units: ["retail", "sme"] } };
  await assert.rejects(local.ask(req, { question: "Who owns this?", productId: "retail.accounts" }), { status: 404 });
  await assert.rejects(local.ask(req, { question: "Who owns this?", products: ["wealth.holdings"] }), { status: 404 });
  const answer = await local.ask(req, { question: "Who owns SME Lending?", productId: "sme.lending" });
  assert.equal(answer.behaviour, "catalogue");
  assert.equal(answer.productId, "sme-lending");
  assert.deepEqual(answer.products, ["sme.lending"]);
  assert.deepEqual(productScope([{ id: "standalone" }], { products: ["standalone"] }).metadata.products, ["standalone"]);
});

test("knowledge provider preserves normalized scope on KGE answers and catalogue fallback", async () => {
  const req = { portalIdentity: { units: ["retail"] } };
  const local = localKnowledge({ listProducts: async () => [catalogue.products[0]], published: async () => true });
  const body = { question: "Who owns Retail Accounts?", products: ["retail.accounts"], productId: "retail-accounts", domain: "Retail" };
  let forwarded;
  const upstream = { request: async (_req, _path, options) => { forwarded = JSON.parse(options.body); return Response.json({ answer_text: "Owner", products: ["retail.accounts"], behaviour: "catalogue" }); } };
  const answer = await knowledgeProvider("ask", local, upstream).run(req, body);
  assert.deepEqual(forwarded, { question: body.question, products: body.products });
  assert.equal(answer.productId, body.productId);
  assert.equal(answer.domain, body.domain);
  assert.deepEqual(answer.products, body.products);
  const fallback = await knowledgeProvider("ask", local, { request: async () => { throw new TypeError("outage"); } }).run(req, body);
  assert.equal(fallback.fallback, true);
  assert.equal(fallback.behaviour, "catalogue");
  assert.equal(fallback.productId, body.productId);
  assert.equal(fallback.domain, body.domain);
  assert.deepEqual(fallback.products, body.products);
});

test("Ask controllers forward canonical scope and share feedback visibility without a listening server", async (context) => {
  const repository = openSqliteRepository({ PORTAL_SQLITE_PATH: ":memory:" });
  context.after(() => repository.close());
  repository.syncCatalogue(catalogue.release, catalogue.products, true);
  const calls = [];
  const kge = { request: async (_req, path, options) => {
    calls.push({ path, body: JSON.parse(options.body) });
    return Response.json({ answer_text: "A governed answer", behaviour: "figure" });
  } };
  const { app } = createApp({ repository, kge, catalogueSync: false, env: {}, uiPath: "not-present" });
  const find = (stack, path) => {
    for (const layer of stack) {
      if (layer.route?.path === path) return layer.route.stack.at(-1).handle;
      if (layer.handle?.stack) {
        const result = find(layer.handle.stack, path);
        if (result) return result;
      }
    }
  };
  const run = async (path, body) => {
    const response = { code: 200, status(code) { this.code = code; return this; }, set() { return this; }, json(value) { this.value = value; return this; }, end() { return this; } };
    await find(app.router.stack, path)({ body, portalIdentity: { principal: ADMIN, units: ["retail"] } }, response, (error) => { throw error; });
    return response;
  };
  const question = "Who owns this product?";
  for (const [path, body] of [["/ask-data-product", { productId: "retail-accounts" }], ["/ask-domain", { productIds: ["retail.accounts"], domain: "Retail" }], ["/ask", { products: ["retail-accounts"] }], ["/api/ask", { productId: "retail.accounts" }]]) {
    const response = await run(path, { ...body, question, arm: "untrusted", stages: { test: true }, purpose: "untrusted", principal: "someone-else", extra: true });
    assert.equal(response.code, 200);
    assert.deepEqual(calls.at(-1), { path: "/assist", body: { question, products: ["retail.accounts"] } });
    assert.deepEqual(response.value.products, ["retail.accounts"]);
    if (body.productId) assert.equal(response.value.productId, "retail-accounts");
    if (body.domain) assert.equal(response.value.domain, "Retail");
  }
  await run("/ask-domain", { question, domain: "All data assets" });
  assert.deepEqual(calls.at(-1).body, { question });
  await run("/ask-domain", { question, productIds: [] });
  assert.deepEqual(calls.at(-1).body, { question, products: [] });
  for (const path of ["/ask", "/api/ask", "/ask-data-product", "/ask-domain"]) {
    for (const id of ["wealth.holdings", "unknown.table"]) {
      for (const invalid of [{ productId: id }, { products: ["retail.accounts", id] }, { productIds: [id] }]) {
        const response = await run(path, { question, productId: "retail.accounts", ...invalid });
        assert.equal(response.code, 404);
        assert.deepEqual(response.value, { message: "Product not found." });
      }
    }
  }
  const saved = [];
  const saveFeedback = repository.askFeedback;
  repository.askFeedback = (...args) => { saved.push(args); return saveFeedback(...args); };
  const feedback = { session_id: 229, user_query_seq_id: 1, feedback_rating: 1, productId: "retail.accounts" };
  for (const productId of ["retail.accounts", "retail-accounts", "retail.accounts"]) {
    const answer = await run("/ask-data-product", { question, productId });
    assert.equal((await run("/feedback-add", { ...feedback, productId })).code, 201);
    assert.deepEqual(saved.at(-1)[3], { product: answer.value.products[0] });
  }
  const explore = { ...feedback, productId: undefined, productIds: ["retail-accounts", "retail.accounts"], domain: "Retail" };
  const answer = await run("/ask-domain", { ...explore, question });
  assert.equal((await run("/feedback-add", explore)).code, 201);
  assert.deepEqual(saved.at(-1)[3], { domain: answer.value.domain, products: answer.value.products });
  repository.setPublished("retail-accounts", false);
  for (const path of ["/ask", "/api/ask", "/ask-data-product", "/ask-domain"]) assert.equal((await run(path, { question, productId: "retail.accounts" })).code, 404);
  assert.equal((await run("/feedback-add", feedback)).code, 404);
});

test("Assist model notice reaches the page and a stopped backend keeps the fallback notice", async (context) => {
  const f = fixture(context);
  f.mode("unavailable");
  const answer = await (await f.request("/ask", { question: "Total revenue?" })).json();
  assert.equal(f.calls.at(-1).path, "/assist");
  assert.equal(answer.notice, "The assistant is unavailable; showing governed answers.");
  assert.equal(answer.provider, "kge");
  f.mode("down");
  const fallback = await (await f.request("/ask", { question: "Who owns Account?" })).json();
  assert.equal(fallback.notice, "Live data is unavailable. Using the portal catalogue; data answers and figures are unavailable.");
  assert.equal(fallback.provider, "portal");
});

for (const kind of ["search", "ask"]) test(`${kind} fallback names live data rather than an app`, async () => {
  const provider = knowledgeProvider(kind, { [kind]: async () => ({}) }, {
    request: async () => { throw new TypeError("network unavailable"); },
  });
  const result = await provider.run({ portalIdentity: { units: ["retail"] } }, { query: "accounts", question: "Who owns accounts?" });
  assert.equal(result.fallback, true);
  assert.match(result.notice, /Live data is unavailable/);
  assert.doesNotMatch(result.notice, /Context/);
});
