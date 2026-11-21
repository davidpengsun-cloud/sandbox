import assert from "node:assert/strict";
import { after, before, test } from "node:test";
import { createApp } from "../src/app.js";
import { openSqliteRepository } from "../src/repository/sqlite.js";
import { catalogue } from "./fixtures/catalogue.js";

const repository = openSqliteRepository({ PORTAL_SQLITE_PATH: ":memory:" });
let units = ["retail", "sme", "corporate"];
const kge = { identity: async () => ({ principal: "reader", units }) };
const { app } = createApp({ repository, kge, catalogueSync: false, env: {}, uiPath: "not-present" });
let server;
let base;

const revenue = { id: "uc-revenue-review", title: "Month-end revenue review", objective: "Finance compares each month's revenue with the plan.", domain: "Finance", owner: "Finance controller", url: "https://example.com/revenue-review",
  kpi: { name: "Monthly revenue", definition: "Interest and fee income booked in the month.", grain: "Month", unit: "USD" }, example_questions: ["What was revenue in June 2025?", "How did revenue compare with plan?"] };
const covenants = { id: "uc-covenants", title: "Covenant monitoring", objective: "Credit checks covenants each quarter." };
const declared = {
  "retail-accounts": [revenue, { id: "uc-branch-pack", title: "Branch performance pack", objective: "Regional leads rank branches by account growth.", kpi: { name: "Account growth" } }],
  "sme-lending": [revenue, revenue, covenants],
  "corporate-exposure": [covenants, { id: "uc-exposure", title: "Exposure limits", objective: "<b>Shown as text</b>" }],
  "wealth-holdings": [{ title: "Adviser book review", description: "Advisers review client books." }],
};

before(async () => {
  const products = structuredClone(catalogue.products).map((product) => ({ ...product, use_cases: declared[product.id] }));
  await repository.syncCatalogue("use cases", products);
  server = app.listen(0, "127.0.0.1");
  await new Promise((resolve) => server.once("listening", resolve));
  base = `http://127.0.0.1:${server.address().port}`;
});
after(() => { server.close(); repository.close(); });

const get = async (path) => (await fetch(`${base}${path}`)).json();
const post = async (path, body) => (await fetch(`${base}${path}`, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(body) })).json();
const ids = (rows) => rows.map((row) => row.PRODUCTID).sort();

test("one card per use case id, each asset linked once, ordered by count then title, with absent fields blank", async () => {
  const card = (entry, products) => ({ id: entry.id, title: entry.title, objective: entry.objective, domain: entry.domain || "", owner: entry.owner || "", url: entry.url || "",
    kpi: entry.kpi ? { definition: "", grain: "", unit: "", ...entry.kpi } : null, example_questions: entry.example_questions || [], products, count: products.length });
  const name = (id) => catalogue.products.find((product) => product.id === id).name;
  const linked = (...productIds) => productIds.map((id) => ({ id, name: name(id) }));
  const list = (await get("/getUseCases")).useCaseList.map((entry) => ({ ...entry, products: [...entry.products].sort((a, b) => a.id.localeCompare(b.id)) }));
  assert.deepEqual(list, [
    card(covenants, linked("corporate-exposure", "sme-lending")),
    card(revenue, linked("retail-accounts", "sme-lending")),
    card(declared["retail-accounts"][1], linked("retail-accounts")),
    card(declared["corporate-exposure"][1], linked("corporate-exposure")),
  ]);
});

test("an inline entry from before #599 reads as a use case whose id is made from its title", async () => {
  units = ["wealth"];
  try {
    assert.deepEqual((await get("/getUseCases")).useCaseList.map(({ id, title, objective, count }) => ({ id, title, objective, count })), [{ id: "uc-adviser-book-review", title: "Adviser book review", objective: "Advisers review client books.", count: 1 }]);
  } finally {
    units = ["retail", "sme", "corporate"];
  }
});

test("two inline titles that share an id are two cards, each under its own title", async () => {
  const products = (alike) => structuredClone(catalogue.products).map((product) => ({ ...product, use_cases: product.id === "wealth-holdings" ? alike : declared[product.id] }));
  units = ["wealth"];
  try {
    await repository.syncCatalogue("alike titles", products([{ title: "A/B", description: "First." }, { title: "a b", description: "Second." }]));
    assert.deepEqual((await get("/getUseCases")).useCaseList.map(({ id, title, objective, count }) => ({ id, title, objective, count })), [
      { id: "uc-a-b", title: "a b", objective: "Second.", count: 1 },
      { id: "uc-a-b", title: "A/B", objective: "First.", count: 1 },
    ]);
  } finally {
    await repository.syncCatalogue("use cases again", products(declared["wealth-holdings"]));
    units = ["retail", "sme", "corporate"];
  }
});

test("only published assets the viewer may read are counted, as for the community cards", async () => {
  repository.setPublished("sme-lending", false);
  units = ["retail", "sme"];
  try {
    const list = (await get("/getUseCases")).useCaseList;
    assert.deepEqual(list.map(({ title, count }) => ({ title, count })), [
      { title: "Branch performance pack", count: 1 },
      { title: "Month-end revenue review", count: 1 },
    ]);
    assert.deepEqual(list.find((entry) => entry.id === "uc-revenue-review").products.map((linked) => linked.id), ["retail-accounts"]);
    assert.equal((await get("/getDataCommunities")).totalCount, 1);
    units = [];
    assert.deepEqual(await get("/getUseCases"), { useCaseList: [] });
  } finally {
    repository.setPublished("sme-lending", true);
    units = ["retail", "sme", "corporate"];
  }
});

test("the search results for a use case list the visible assets that serve it, and sort and filter keep that scope", async () => {
  const found = await post("/getCommunityProducts", { useCase: "Covenant monitoring" });
  assert.deepEqual(ids(found.searchList), ["corporate-exposure", "sme-lending"]);
  assert.deepEqual(found.communityFilter.sort(), ["corporate community", "sme community"]);
  assert.deepEqual(ids((await post("/getCommunityProducts", { useCase: " Month-end revenue review " })).searchList), ["retail-accounts", "sme-lending"]);
  assert.deepEqual((await post("/getCommunityProducts", { useCase: "Adviser book review" })).searchList, []);
  assert.deepEqual(ids((await post("/filterSearchResults", { searchParam: "no catalogue match", browseScope: { useCase: "Covenant monitoring" }, filterParams: [{ parent: "COMMUNITY", value: "sme community" }] }))), ["sme-lending"]);
  assert.deepEqual(ids(await post("/sortSearchResults", { browseScope: { useCase: "Month-end revenue review" } })), ["retail-accounts", "sme-lending"]);
  assert.equal((await post("/getCommunityProducts", { useCase: ["Covenant monitoring"] })).searchList.length, 3);
});
