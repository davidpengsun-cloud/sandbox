import assert from "node:assert/strict";
import test from "node:test";
import { createApp } from "../src/app.js";
import { openSqliteRepository } from "../src/repository/sqlite.js";
import { openBigQueryRepository } from "../src/repository/bigquery.js";
import { catalogue } from "./fixtures/catalogue.js";
import { bigQueryFake } from "./fixtures/bigquery-fake.js";
import { catalogueDiscovery, localKnowledge, searchInput } from "../src/knowledge.js";

const terms = ["credit exposure", "active customer", "fee income"];
const document = (unit) => ({ ...structuredClone(catalogue.products.find((p) => p.unit_id === unit)),
  id: `${unit}.DD-${unit.toUpperCase()}`, table: `DD-${unit.toUpperCase()}`, name: `${unit} dictionary`,
  description: "Data dictionary", product_type: "Documents", fields: [],
});
const loans = () => ["SAGE Loan", "Loan"].map((name, index) => ({ ...structuredClone(catalogue.products[0]),
  id: `retail.loan${index}`, table: `loan${index}`, name, description: "", application: "", fields: [],
}));

test("multi-word discovery keeps intersections first and ranks partial products by coverage and field", async () => {
  const base = structuredClone(catalogue.products[0]);
  const products = [
    { ...base, id: "both", table: "both", name: "Overdue loan", description: "Balances", fields: [] },
    { ...base, id: "name", table: "name", name: "Loan", description: "Balances", fields: [] },
    { ...base, id: "description", table: "description", name: "Balances", description: "Loan", fields: [] },
  ];
  const partial = catalogueDiscovery(products, searchInput({ query: "overdue loan missing" }));
  assert.deepEqual(partial.map((hit) => hit.id), ["both", "name", "description"]);
  assert.ok(.8 > partial[0].score && partial[0].score > partial[1].score && partial[1].score > partial[2].score);
  assert.deepEqual(catalogueDiscovery(products, searchInput({ query: "overdue loan" })).map((hit) => [hit.id, hit.score]), [["both", .8]]);
  assert.deepEqual(catalogueDiscovery(products, searchInput({ query: "overdue balances" })).map((hit) => [hit.id, hit.score]), [["both", .8]]);
  assert.deepEqual(catalogueDiscovery(products, searchInput({ query: "zzz qqq" })), []);
  const local = localKnowledge({ publication: async () => [{ product_id: "both", published: false }] });
  const req = { portalIdentity: { units: ["retail"], products } };
  const input = searchInput({ query: "overdue loan missing" });
  assert.deepEqual((await local.visibleHits(req, [], input)).map((hit) => hit.id), ["name", "description"]);
  req.portalIdentity.products = [...products, base];
  const full = { kind: "product", unit: "retail", id: "retail.accounts", title: "Accounts", score: 1, why: { kind: "term", text: input.query } };
  assert.deepEqual(await local.visibleHits(req, [full], input), [full]);
  const visiblePartial = await local.visibleHits(req, [], searchInput({ query: "overdue balances" }));
  assert.deepEqual(visiblePartial.map((hit) => hit.id), ["description", "name"]);
  assert.ok(visiblePartial.every((hit) => hit.score < .8));
});

test("KGE hits require a readable published parent by identity regardless of type or kind", async () => {
  const rows = [document("retail"), document("wealth")].map((p) => ({ ...p, product_type: "Table" }));
  let published = true;
  const local = localKnowledge({ publication: async () => [{ product_id: rows[0].id, published }] });
  const req = { portalIdentity: { units: ["retail"], products: rows } };
  const hit = { kind: "document", unit: "retail", id: "DD-RETAIL", title: "Dictionary", score: .35, why: { kind: "passage", text: "delinquency passage" } };
  const mapped = ["product", "column", "document", "term", "metric"].flatMap((kind) => [rows[0].id, rows[0].table, `retail.${rows[0].table}`]
    .map((id) => ({ ...hit, kind, id: kind === "column" ? `${id}.field` : id })));
  const orphans = ["product", "column", "document", "term", "metric"].map((kind) => ({ ...hit, kind, id: "unmapped" }));
  const input = searchInput({ query: "delinquency", limit: 100 });
  for (const mappedHit of mapped) assert.deepEqual(await local.visibleHits(req, [mappedHit], input), [mappedHit]);
  assert.deepEqual(await local.visibleHits(req, [...mapped, ...orphans, { ...hit, unit: "wealth", id: "DD-WEALTH" }], input),
    (await local.visibleHits(req, mapped, input)));
  assert.equal((await local.visibleHits(req, [hit], input)).length, 1);
  published = false;
  assert.deepEqual(await local.visibleHits(req, [...mapped, ...orphans], input), []);
});

test("full product matches from either provider suppress both providers' partials", async () => {
  const products = loans();
  const local = localKnowledge({ publication: async () => [] });
  const req = { portalIdentity: { units: ["retail"], products } };
  const input = searchInput({ query: "SAGE loan" });
  const partial = { kind: "product", unit: "retail", id: products[1].id, title: "Loan", score: .35, why: { kind: "description", text: "Loan" } };
  assert.deepEqual((await local.visibleHits(req, [partial], input)).map((hit) => hit.id), [products[0].id]);
  products[0].name = "SAGE";
  const full = { ...partial, id: products[0].id, title: "SAGE Loan", score: .8, why: { kind: "name", text: "SAGE Loan" } };
  assert.deepEqual(await local.visibleHits(req, [full, partial], input), [full]);
  assert.deepEqual(new Set((await local.visibleHits(req, [partial], input)).map((hit) => hit.id)), new Set(products.map((p) => p.id)));
  const lineage = { ...partial, score: .48, why: { kind: "description", text: "SAGE loan", via: [products[0].id] } };
  assert.deepEqual(await local.visibleHits(req, [full, lineage], input), [full, lineage]);
});

async function harness(context, adapter, loanPair = false) {
  const fake = adapter === "bigquery" ? bigQueryFake() : undefined;
  const repository = fake ? openBigQueryRepository({ PROFILE: "meridian" }, { client: fake.client }) : openSqliteRepository({ PORTAL_SQLITE_PATH: ":memory:" });
  const products = structuredClone(catalogue.products);
  Object.assign(products[0], { application: "SAGE", community: "Client Data", APM_ID: "APM-102" });
  products[0].fields[1].terms = terms;
  products.push(document("retail"), document("wealth"));
  if (loanPair) products.splice(0, products.length, ...loans());
  await repository.syncCatalogue("search", products);
  let units = ["retail"], refusal = false, outage = false;
  const calls = [];
  const kge = {
    identity: async () => ({ principal: "reader", units }),
    request: async (_req, path, options) => {
      assert.equal(path, "/search");
      const input = JSON.parse(options.body);
      calls.push(input);
      if (refusal) return Response.json({ detail: "forbidden" }, { status: 403 });
      if (outage) throw new TypeError("Context unavailable");
      let hits = [];
      if (terms.includes(input.query)) hits = [{ kind: "product", unit: "retail", id: "retail.accounts", title: "accounts", score: 1, why: { kind: "term", text: input.query } }];
      if (["delinquency", "net new money"].includes(input.query)) hits = ["retail", "wealth"].map((unit) => ({ kind: "document", unit, id: `DD-${unit.toUpperCase()}`, title: `${unit} passage`, score: .35, why: { kind: "passage", text: input.query } }));
      if (input.query === "credit") hits = [{ kind: "column", unit: "retail", id: "retail.accounts.amount", title: "amount", score: .8, why: { kind: "description", text: "Credit indicator" } }];
      if (input.query === "SAGE loan") hits = [{ kind: "product", unit: "retail", id: "retail.loan1", title: "Loan", score: .35, why: { kind: "description", text: "Loan" } }];
      return Response.json({ hits });
    },
  };
  const { app } = createApp({ repository, kge, catalogueSync: false, env: {}, uiPath: "not-present" });
  const server = app.listen(0, "127.0.0.1");
  await new Promise((resolve) => server.once("listening", resolve));
  context.after(() => { server.close(); repository.close(); });
  const request = async (path, body, method = body === undefined ? "GET" : "POST") => fetch(`http://127.0.0.1:${server.address().port}${path}`, { method, headers: { "content-type": "application/json" }, ...(body === undefined ? {} : { body: JSON.stringify(body) }) });
  const json = async (path, body, method) => { const response = await request(path, body, method); assert.equal(response.status, 200); return response.json(); };
  return { repository, fake, calls, products, request, json, units: (value) => { units = value; }, refuse: () => { refusal = true; }, fail: () => { outage = true; } };
}

for (const adapter of ["sqlite", "bigquery"]) {
  test(`${adapter}: retyping then unpublishing a dictionary removes its title and passage from hits and searchList`, async (context) => {
    const h = await harness(context, adapter);
    await h.repository.setRole("reader", "publisher");
    await h.json("/api/admin/products/retail.DD-RETAIL/metadata", { product_type: "Table" }, "PATCH");
    const retyped = await h.json("/search", { searchParam: "delinquency" });
    assert.equal(retyped.searchList[0].PRODUCTTYPE, "Table");
    assert.equal(retyped.hits[0].why.text, "delinquency");
    assert.equal((await h.request("/api/admin/products/retail.DD-RETAIL/published", { published: false }, "PUT")).status, 204);
    for (const path of ["/search", "/api/search"]) {
      if (h.fake) h.fake.state.statements.length = 0;
      const hidden = await h.json(path, { query: "delinquency" });
      assert.deepEqual(hidden.hits, []);
      assert.deepEqual(hidden.searchList, []);
      if (h.fake) assert.equal(h.fake.state.statements.length, path === "/search" ? 7 : 6);
    }
  });

  test(`${adapter}: Enter drops KGE Loan partial when the catalogue fully matches SAGE Loan`, async (context) => {
    const h = await harness(context, adapter, true);
    for (const path of ["/search", "/api/search"]) {
      if (h.fake) h.fake.state.statements.length = 0;
      const result = await h.json(path, { query: "SAGE loan" });
      assert.equal(result.provider, "kge");
      assert.deepEqual(result.hits.map((hit) => hit.id), ["retail.loan0"]);
      assert.deepEqual(result.searchList.map((p) => p.PRODUCTNAME ?? p.name), ["SAGE Loan"]);
      if (h.fake) assert.equal(h.fake.state.statements.length, path === "/search" ? 7 : 6);
    }
  });

  test(`${adapter}: outage fallback uses discovery intersections and rejects noise`, async (context) => {
    const h = await harness(context, adapter, true);
    h.fail();
    for (const path of ["/search", "/api/search"]) {
      for (const query of ["SAGE loan", "...", "___", "?!", "zzqx blorf", "SAGE loan missing"]) {
        if (h.fake) h.fake.state.statements.length = 0;
        const result = await h.json(path, { query });
        assert.equal(result.provider, "portal");
        assert.equal(result.fallback, true);
        const expected = query === "SAGE loan" ? ["SAGE Loan"] : query === "SAGE loan missing" ? ["SAGE Loan", "Loan"] : [];
        assert.deepEqual(result.hits.map((hit) => hit.title), expected);
        assert.deepEqual(result.searchList.map((p) => p.PRODUCTNAME ?? p.name), expected);
        if (h.fake) assert.equal(h.fake.state.statements.length, path === "/search" ? 7 : 6);
      }
    }
  });

  test(`${adapter}: autocomplete and Enter share glossary and source metadata matches`, async (context) => {
    const h = await harness(context, adapter);
    assert.deepEqual(await h.repository.publication([]), []);
    assert.deepEqual((await h.json("/getSearchData/0")).map((item) => item.value).sort(), ["Retail Accounts", "retail dictionary"].sort());
    assert.deepEqual(await h.json("/getSearchData/..."), []);
    assert.deepEqual((await h.json("/search", { query: "SAGE", kinds: ["column"] })).hits, []);
    for (const query of [...terms, "SAGE", "Client Data", "APM-102"]) {
      const before = h.calls.length;
      const suggestions = await h.json(`/getSearchData/${encodeURIComponent(query)}`);
      assert.deepEqual(suggestions, [{ value: "Retail Accounts" }], query);
      assert.equal(h.calls.length, before, "autocomplete never calls KGE");
      for (const path of ["/search", "/api/search"]) {
        const result = await h.json(path, { searchParam: query });
        assert.deepEqual(result.searchList.map((p) => p.PRODUCTID ?? p.id), ["retail-accounts"], query);
        assert.equal(result.provider, "kge");
        if (terms.includes(query)) {
          assert.equal(result.hits.filter((hit) => hit.kind === "product").length, 1);
          assert.ok(result.hits.some((hit) => hit.score === 1 && hit.why.kind === "term"));
        }
      }
    }
    await h.repository.setPublished("retail-accounts", false);
    assert.deepEqual(await h.json("/getSearchData/SAGE"), []);
    assert.deepEqual((await h.json("/search", { searchParam: "SAGE" })).searchList, []);
    assert.deepEqual((await h.json("/search", { searchParam: "credit exposure" })).hits, []);
    await h.repository.setPublished("retail-accounts", true);
    h.units([]);
    assert.deepEqual(await h.json("/getSearchData/SAGE"), []);
    assert.deepEqual((await h.json("/search", { searchParam: "SAGE" })).searchList, []);
    h.units(["retail"]);
    h.refuse();
    assert.equal((await h.request("/search", { searchParam: "SAGE" })).status, 403);
  });

  test(`${adapter}: document-only results resolve catalogue rows and passage and column parents`, async (context) => {
    const h = await harness(context, adapter);
    for (const query of ["delinquency", "net new money"]) {
      const result = await h.json("/search", { searchParam: query });
      assert.deepEqual(result.searchList.map((p) => [p.PRODUCTID, p.PRODUCTTYPE]), [["retail.DD-RETAIL", "Documents"]]);
      assert.deepEqual(result.hits.map((hit) => hit.product), [{ id: "retail.DD-RETAIL", name: "retail dictionary" }]);
      assert.deepEqual(result.searchList[0].matches, result.hits);
    }
    const columns = await h.json("/search", { searchParam: "credit" });
    const column = columns.hits.find((hit) => hit.kind === "column");
    assert.deepEqual(column.product, { id: "retail-accounts", name: "Retail Accounts" });
    await h.repository.setPublished("retail.DD-RETAIL", false);
    const hidden = await h.json("/search", { searchParam: "delinquency" });
    assert.deepEqual(hidden.searchList, []);
    assert.deepEqual(hidden.hits, []);
  });
}

test("BigQuery: autocomplete reduces the previous request budget and Enter adds no per-product metadata jobs", async (context) => {
  const h = await harness(context, "bigquery");
  const jobs = h.fake.state.statements;
  const units = ["retail"];
  for (const query of ["SAGE", ...terms, "APM-102", "overdue loan", "zzqx blorf", "zzz qqq"]) {
    jobs.length = 0;
    await h.repository.listProducts(units);
    const oldRows = await h.repository.listProducts(units, query);
    for (const p of oldRows) await Promise.all([h.repository.activity(p.id), h.repository.published(p.id)]);
    await h.repository.favourites("reader", units);
    const before = jobs.length;
    assert.equal(before, 7 + 2 * oldRows.length);
    jobs.length = 0;
    await h.json(`/getSearchData/${encodeURIComponent(query)}`);
    assert.equal(jobs.length, 4, `autocomplete ${query}: was ${before}, now 4`);
    assert.deepEqual(jobs.at(-1).params.ids.sort(), ["retail-accounts", "retail.DD-RETAIL"].sort());
    jobs.length = 0;
    await h.json("/search", { searchParam: query });
    assert.equal(jobs.length, 7, `Enter ${query}: was ${10 + 3 * 2}, now 7 O(1) portal jobs`);
    jobs.length = 0;
    await h.json("/api/products");
    assert.equal(jobs.length, 5, "GET /api/products reuses request products and batches activity and publication in 5 O(1) jobs");
  }
});

test("BigQuery: product listing dispatches fields, lineage, activities and publication in one parallel second wave and skips catalogue loads on non-catalogue routes (#291)", async (context) => {
  const h = await harness(context, "bigquery");
  const jobs = h.fake.state.statements;
  const originalQuery = h.fake.client.query.bind(h.fake.client);
  let active = 0, peakWave = 0;
  h.fake.client.query = async (job) => {
    active += 1;
    peakWave = Math.max(peakWave, active);
    try {
      await new Promise((resolve) => setImmediate(resolve));
      return await originalQuery(job);
    } finally {
      active -= 1;
    }
  };
  jobs.length = 0;
  const listed = await h.json("/api/products");
  assert.ok(listed.length > 0);
  assert.equal(jobs.length, 5);
  assert.equal(peakWave, 4, "fields, lineage, activities and publication run concurrently in Wave 2");

  jobs.length = 0;
  await h.json("/api/role");
  assert.equal(jobs.filter((job) => job.query.includes("visible_ids")).length, 0, "/api/role skips listProducts");

  jobs.length = 0;
  assert.equal((await h.request("/api/favourites/retail-accounts", undefined, "DELETE")).status, 204);
  assert.equal(jobs.length, 1, "DELETE /api/favourites/:id runs only DELETE without listProducts");

  jobs.length = 0;
  assert.equal((await h.request("/api/cart", undefined, "DELETE")).status, 204);
  assert.equal(jobs.length, 1, "DELETE /api/cart runs only DELETE without listProducts");

  peakWave = 0;
  jobs.length = 0;
  await h.json("/api/favourites");
  assert.equal(jobs.length, 4, "GET /api/favourites runs favourites + 3 listProducts queries");
  assert.ok(peakWave >= 2, "GET /api/favourites starts favourites and listProducts concurrently");

  const unhandled = [];
  const onUnhandled = (reason) => unhandled.push(reason);
  process.on("unhandledRejection", onUnhandled);
  try {
    h.fake.client.query = async (job) => {
      await new Promise((resolve) => setImmediate(resolve));
      if (!job.query.includes("visible_ids")) throw new Error("Wave 2 query failed");
      return await originalQuery(job);
    };
    for (const [path, body] of [["/api/products", undefined], ["/api/search", { query: "credit" }], ["/api/favourites", undefined]]) {
      const response = await h.request(path, body);
      assert.equal(response.status, 502, `${path} maps a failing product read to 502`);
      assert.deepEqual(await response.json(), { message: "Client ALTA is unavailable." });
    }
    await new Promise((resolve) => setImmediate(resolve));
    assert.deepEqual(unhandled, [], "Wave 2 failures leave no unhandled promise rejections");
  } finally {
    process.off("unhandledRejection", onUnhandled);
    h.fake.client.query = originalQuery;
  }
});
