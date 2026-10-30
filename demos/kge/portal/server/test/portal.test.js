import assert from "node:assert/strict";
import { after, before, test } from "node:test";
import { createApp } from "../src/app.js";
import { openSqliteRepository } from "../src/repository/sqlite.js";
import { catalogue } from "./fixtures/catalogue.js";

const repository = openSqliteRepository({ PORTAL_SQLITE_PATH: ":memory:" });
let units = ["retail"];
let principal = "analyst_retail";
let catalogueFetches = 0;
const kge = {
  catalogueIdentity: async () => ({ release: catalogue.release, has_catalogue: true }),
  catalogue: async () => { catalogueFetches += 1; return catalogue; },
  identity: async () => ({ principal, units }),
  request: async (_req, path, options = {}) => {
    if (path === "/search") return Response.json({ hits: [{ kind: "product", unit: "retail", id: "retail-accounts", title: "Retail Accounts", score: 1, why: { kind: "term", text: "accounts" } }] });
    if (path === "/data/export") return new Response("id,amount\n1,20\n", { status: 200, headers: { "content-type": "text/csv", "content-disposition": "attachment; filename=data.csv" } });
    if (path.startsWith("/data/")) {
      const body = JSON.parse(options.body || "{}");
      if (!units.includes(body.unit)) return new Response(JSON.stringify({ detail: "product not found" }), { status: 404, headers: { "content-type": "application/json" } });
      if (/delete/i.test(body.sql || "")) return new Response(JSON.stringify({ detail: "read-only" }), { status: 400, headers: { "content-type": "application/json" } });
      return new Response(JSON.stringify({ columns: ["id"], rows: [{ id: 1 }], row_count: 1, capped_at: 20, run_trace: {} }), { status: 200, headers: { "content-type": "application/json" } });
    }
    return new Response(JSON.stringify(path === "/readable-units" ? { units } : { principal, answer_text: "A governed answer", figures: [], citations: [] }), { status: 200, headers: { "content-type": "application/json" } });
  },
};
const { app } = createApp({ repository, kge, uiPath: "not-present" });
let server;
let base;

before(async () => {
  server = app.listen(0, "127.0.0.1");
  await new Promise((resolve) => server.once("listening", resolve));
  base = `http://127.0.0.1:${server.address().port}`;
  while (repository.catalogueRelease() !== catalogue.release) await new Promise((resolve) => setTimeout(resolve, 1));
});
after(() => { server.close(); repository.close(); });

async function request(path, options = {}) {
  return fetch(`${base}${path}`, { headers: { "content-type": "application/json" }, ...options });
}

test("migrations create only reserved portal tables", () => {
  assert.deepEqual(repository.tableNames(), ["_portal_governed", "_portal_schema", "_portal_products", "_portal_fields", "_portal_lineage", "_portal_quality_rules", "_portal_catalogue_sync", "_portal_users", "_portal_favourites", "_portal_views", "_portal_cart", "_portal_requests", "_portal_feedback", "_portal_ask_feedback", "_portal_publish_status"]);
});

test("browse requests never fetch the whole catalogue", async () => {
  const before = catalogueFetches;
  for (let index = 0; index < 5; index += 1) assert.equal((await request("/api/products")).status, 200);
  assert.equal(catalogueFetches, before);
});

test("browse and details cannot cross readable units", async () => {
  const listing = await (await request("/api/products")).json();
  assert.deepEqual(listing.map((product) => product.unit_id), ["retail"]);
  assert.equal((await request("/api/products/wealth-holdings")).status, 404);
  units = [];
  assert.deepEqual(await (await request("/api/products")).json(), []);
  units = ["retail"];
});

test("search, metadata and product route groups use readable units", async () => {
  assert.equal((await (await request("/search", { method: "POST", body: JSON.stringify({ searchParam: "accounts" }) })).json()).searchList.length, 1);
  assert.equal((await (await request("/fields/retail-accounts")).json()).length, 2);
  assert.ok((await (await request("/lineage/retail-accounts")).json()).edges.length > 0);
  assert.equal((await (await request("/dataQuality/retail-accounts/completeness")).json()).length, 2);
  assert.equal((await request("/fields/wealth-holdings")).status, 404);
});

test("community counts and facets use communities, not domains or sources", async () => {
  const tree = await (await request("/getDataCommunities")).json();
  assert.equal(tree.totalCount, 1);
  assert.deepEqual(tree.communityList.map(({ kind, name, domain, count, children }) => ({ kind, name, domain, count, children: children.map(({ kind: childKind, id }) => ({ kind: childKind, id })) })), [
    { kind: "community", name: "retail community", domain: "retail domain", count: 1, children: [{ kind: "product", id: "retail-accounts" }] },
  ]);
  const search = await (await request("/search", { method: "POST", body: "{}" })).json();
  assert.deepEqual(search.communityFilter, ["retail community"]);
  assert.deepEqual(search.domainFilter, ["retail domain"]);
  assert.equal(search.searchList[0].APPLICATIONNAME, "retail community");
  assert.equal(search.searchList[0].COMMUNITY, "retail community");
  for (const [body, expected] of [
    [{ searchParam: "retail community", isDomain: false, domain: "retail domain" }, 1],
    [{ searchParam: "retail community", isDomain: false, domain: "other domain" }, 0],
    [{ searchParam: "retail domain", isDomain: true }, 1],
    [{ searchParam: "retail domain", isDomain: false }, 0],
    [{ searchParam: "retail application", isDomain: false }, 0],
  ]) {
    const result = await (await request("/getCommunityProducts", { method: "POST", body: JSON.stringify(body) })).json();
    assert.equal(result.searchList.length, expected);
  }
  for (const [field, value, expected] of [
    ["COMMUNITY", "retail community", 1],
    ["COMMUNITY", "retail domain", 0],
    ["DOMAIN", "retail domain", 1],
    ["PRODUCTSOURCE", "retail community", 0],
  ]) {
    const rows = await (await request("/filterSearchResults", { method: "POST", body: JSON.stringify({ filterParams: [{ parent: field, value }] }) })).json();
    assert.equal(rows.length, expected);
  }
  for (const [filters, expected] of [
    [[{ parent: "DOMAIN", value: "retail domain" }], 1],
    [[{ parent: "DOMAIN", value: "other domain" }], 0],
    [[{ parent: "COMMUNITY", value: "retail community" }], 1],
  ]) {
    const rows = await (await request("/filterSearchResults", { method: "POST", body: JSON.stringify({
      searchParam: "not a catalogue match",
      browseScope: { community: "retail community", domain: "retail domain" },
      filterParams: filters,
    }) })).json();
    assert.equal(rows.length, expected);
  }
  units = [];
  assert.deepEqual((await (await request("/getDataCommunities")).json()), { communityList: [], treeExpandList: [], totalCount: 0 });
  units = ["retail"];
});

test("legacy product details map all rich catalogue fields without flattening badges", async () => {
  const product = await (await request("/product/retail-accounts")).json();
  assert.equal(product.PRODUCTOWNER, "steward");
  assert.equal(product.BUSINESSOWNER, "admin");
  assert.equal(product.VIEWTIMES, 48);
  assert.equal(product.COUNTOFCOLUMNS, 2);
  assert.equal(product.AVGRATING, 4.2);
  assert.deepEqual(product.BADGES, ["GOLD", "Certified"]);
  assert.equal(product.BADGE_NAME, "GOLD");
  assert.equal(product.COMMUNITY, "retail community");
  assert.equal(product.COUNTOFRECORDS, 12000);
  assert.equal(product.DATA_FORMAT, "Parquet");
});

test("write features use portal tables", async () => {
  assert.equal((await request("/api/favourites/retail-accounts", { method: "POST" })).status, 204);
  assert.deepEqual(await (await request("/api/favourites")).json(), [{ product_id: "retail-accounts", unit_id: "retail" }]);
  assert.equal((await request("/api/cart/retail-accounts", { method: "POST" })).status, 204);
  assert.equal((await request("/api/requests", { method: "POST", body: JSON.stringify({ productId: "retail-accounts", kind: "subscription" }) })).status, 201);
  assert.equal((await request("/api/feedback", { method: "POST", body: JSON.stringify({ productId: "retail-accounts", rating: 5, comment: "Useful" }) })).status, 201);
  assert.deepEqual(await (await request("/api/feedback/retail-accounts")).json(), { average: 5, count: 1 });
  assert.equal((await request("/feedback-add", { method: "POST", body: JSON.stringify({ session_id: 1, user_query_seq_id: 2, productId: "retail-accounts", feedback_rating: 0, feedback_comment: "Needs context" }) })).status, 201);
  assert.deepEqual(await (await request("/api/feedback/retail-accounts")).json(), { average: 5, count: 1 });
  assert.equal((await request("/feedback-add", { method: "POST", body: JSON.stringify({ session_id: 1, user_query_seq_id: 3, productIds: ["retail-accounts"], domain: "Retail", feedback_rating: 1 }) })).status, 201);
  assert.equal((await request("/feedback-add", { method: "POST", body: JSON.stringify({ feedback_rating: 1 }) })).status, 400);
  assert.equal((await request("/feedback-add", { method: "POST", body: JSON.stringify({ session_id: 1, user_query_seq_id: 4, productIds: ["wealth-holdings"], domain: "Wealth", feedback_rating: 1 }) })).status, 404);
});

test("product ratings accept only whole numbers from one to five and blend with the baseline", async () => {
  for (const rating of [0, 6, 99, 2.5, "abc", null, true, false, [], {}]) {
    assert.equal((await request("/api/feedback", { method: "POST", body: JSON.stringify({ productId: "retail-accounts", rating }) })).status, 400);
    assert.equal((await request("/submit-feedback", { method: "POST", body: JSON.stringify({ productId: "retail-accounts", rating }) })).status, 400);
  }
  assert.equal((await request("/submit-feedback", { method: "POST", body: JSON.stringify({ productId: "retail-accounts", rating: 1 }) })).status, 201);
  const product = await (await request("/product/retail-accounts")).json();
  assert.equal(product.AVGRATING, 3.4);
});

test("warehouse-row routes forward, preserve refusals and apply the unit filter", async () => {
  assert.equal((await request("/api/preview/retail-accounts")).status, 200);
  assert.match(await (await request("/api/export/retail-accounts")).text(), /^id,amount/);
  assert.equal((await request("/api/query", { method: "POST", body: JSON.stringify({ unit: "wealth", sql: "SELECT id FROM holdings" }) })).status, 404);
  const refused = await request("/execute-query", { method: "POST", body: JSON.stringify({ unit: "retail", sql: "DELETE FROM accounts" }) });
  assert.equal(refused.status, 400);
  assert.equal((await refused.json()).detail, "read-only");
});

test("portal roles grant actions but not data", async () => {
  assert.equal((await request("/api/admin/products/retail-accounts/published", { method: "PUT", body: JSON.stringify({ published: false }) })).status, 403);
  units = ["wealth"];
  assert.deepEqual((await (await request("/api/products")).json()).map((product) => product.unit_id), ["wealth"]);
  units = ["retail"];
  principal = "admin";
  assert.equal((await request("/api/admin/products/retail-accounts/published", { method: "PUT", body: JSON.stringify({ published: true }) })).status, 204);
  assert.equal((await (await request("/getUserData")).json()).length, 3);
  principal = "analyst_retail";
});

test("session, identity and ask are relayed through KGE", async () => {
  assert.equal((await request("/session", { method: "POST", body: "{}" })).status, 200);
  assert.equal((await (await request("/whoami")).json()).principal, "analyst_retail");
  assert.equal((await (await request("/ask", { method: "POST", body: JSON.stringify({ question: "What changed?" }) })).json()).answer_text, "A governed answer");
});

test("both export routes preserve the upstream attachment filename", async () => {
  for (const path of ["/api/export/retail-accounts", "/get-data-export/retail-accounts"]) {
    const response = await request(path);
    assert.equal(response.status, 200);
    assert.equal(response.headers.get("content-disposition"), "attachment; filename=data.csv");
    assert.equal(await response.text(), "id,amount\n1,20\n");
  }
});

for (const path of ["/health", "/healthz"]) {
  test(`${path} answers unauthenticated GET and HEAD probes`, async () => {
    const reply = await request(path);
    assert.equal(reply.status, 200);
    assert.deepEqual(await reply.json(), { status: "ok" });
    const head = await request(path, { method: "HEAD" });
    assert.equal(head.status, 200);
    assert.equal(await head.text(), "");
  });
}
