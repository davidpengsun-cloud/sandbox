import assert from "node:assert/strict";
import test from "node:test";
import { BigQuery } from "@google-cloud/bigquery";
import { createApp } from "../src/app.js";
import { openBigQueryRepository } from "../src/repository/bigquery.js";
import { openSqliteRepository } from "../src/repository/sqlite.js";
import { catalogue } from "./fixtures/catalogue.js";
import { bigQueryFake } from "./fixtures/bigquery-fake.js";

const ADMIN = "admin.meridian@example.com";
const ID = "retail-accounts";
const OTHER = "retail-branch";

const adapters = {
  sqlite: () => ({ repository: openSqliteRepository({ PORTAL_SQLITE_PATH: ":memory:" }) }),
  bigquery: () => { const fake = bigQueryFake(); return { repository: openBigQueryRepository({ PROFILE: "meridian" }, { client: fake.client }), fake }; },
};

function release(name, change = () => {}) {
  const value = structuredClone(catalogue);
  const branch = structuredClone(value.products[0]);
  Object.assign(branch, { id: OTHER, table: "branch", name: "Retail Branch", description: "Retail Branch governed description", lineage: { ...branch.lineage, upstream: [] } });
  value.products.push(branch);
  value.release = name;
  change(value, (id) => value.products.find((product) => product.id === id));
  return value;
}

async function harness(context, adapter) {
  const { repository, fake } = adapters[adapter]();
  await repository.setRole(ADMIN, "admin");
  let served = release("r1");
  const kge = {
    async identity() { return { principal: ADMIN, units: ["retail"] }; },
    async catalogueIdentity() { return { release: served.release, has_catalogue: served.has_catalogue }; },
    async catalogue() { return served; },
    async request() { return Response.json({ detail: "not used" }, { status: 404 }); },
  };
  const { app, refreshCatalogue } = createApp({ repository, kge, catalogueSync: false, env: { PORTAL_SEARCH_PROVIDER: "portal", PORTAL_ASK_PROVIDER: "portal" }, uiPath: "not-present" });
  const server = app.listen(0);
  context.after(() => { server.close(); repository.close(); });
  const request = async (path, body, method = body === undefined ? "GET" : "POST") => fetch(`http://127.0.0.1:${server.address().port}${path}`, { method, headers: { "content-type": "application/json", cookie: "session=verified" }, ...(body === undefined ? {} : { body: JSON.stringify(body) }) });
  const json = async (path, body, method) => { const response = await request(path, body, method); assert.ok(response.ok, `${method || "GET"} ${path}: ${response.status}`); return response.status === 204 ? undefined : response.json(); };
  const publish = async (value) => { served = value; const result = await refreshCatalogue(); assert.ok(result?.changed, `release ${value.release} synced`); };
  await publish(served);
  return { repository, fake, request, json, publish, card: (id = ID) => json(`/api/products/${id}`), edit: (patch, id = ID) => json(`/api/admin/products/${id}/metadata`, patch, "PATCH") };
}

for (const adapter of Object.keys(adapters)) {
  test(`${adapter}: catalogue changes to description, domain, community, tier and other descriptive fields reach an existing card`, async (context) => {
    const h = await harness(context, adapter);
    assert.deepEqual((await h.card()).badges, ["GOLD", "Certified"]);
    await h.publish(release("r2", (_value, product) => Object.assign(product(ID), { name: "Accounts", description: "New description", domain: "Retail banking", community: "Branch network", application: "Core ledger", product_type: "VIEW", badges: ["Silver", "Certified"] })));
    const card = await h.card();
    assert.deepEqual({ name: card.name, description: card.description, domain: card.domain, community: card.community, source: card.source, product_type: card.product_type }, { name: "Accounts", description: "New description", domain: "Retail banking", community: "Branch network", source: "Core ledger", product_type: "VIEW" });
    assert.deepEqual(card.badges, ["SILVER", "Certified"]);
    assert.equal((await h.card(OTHER)).description, "Retail Branch governed description");
  });

  test(`${adapter}: a publisher's description survives a sync where KGE kept its value and gives way when KGE changes it`, async (context) => {
    const h = await harness(context, adapter);
    await h.edit({ description: "Publisher description" });
    await h.publish(release("r2", (_value, product) => { product(ID).quality_score = 50; }));
    assert.equal((await h.card()).description, "Publisher description");
    await h.publish(release("r3", (_value, product) => { product(ID).description = "Catalogue description"; }));
    assert.equal((await h.card()).description, "Catalogue description");
  });

  test(`${adapter}: an edit made since the last sync holds against the same sync's catalogue change, until the next one`, async (context) => {
    const h = await harness(context, adapter);
    await h.edit({ description: "Edited between syncs", fields: [{ name: "amount", description: "Edited field" }] });
    await h.publish(release("r2", (_value, product) => { product(ID).description = "Changed in the same cycle"; product(ID).fields[1].description = "Changed field"; }));
    let card = await h.card();
    assert.equal(card.description, "Edited between syncs");
    assert.equal(card.fields[1].description, "Edited field");
    await h.publish(release("r3", (_value, product) => { product(ID).description = "Changed again"; product(ID).fields[1].description = "Changed field again"; }));
    card = await h.card();
    assert.equal(card.description, "Changed again");
    assert.equal(card.fields[1].description, "Changed field again");
  });

  test(`${adapter}: a field row's business name and description follow the same rule`, async (context) => {
    const h = await harness(context, adapter);
    await h.edit({ fields: [{ name: "amount", description: "Publisher field description" }] });
    await h.publish(release("r2", (_value, product) => { product(ID).fields[0].business_name = "Account identifier"; }));
    let fields = (await h.card()).fields;
    assert.equal(fields[0].business_name, "Account identifier");
    assert.equal(fields[1].description, "Publisher field description");
    await h.publish(release("r3", (_value, product) => { product(ID).fields[0].business_name = "Account identifier"; product(ID).fields[1].description = "Catalogue field description"; }));
    fields = await h.json(`/api/products/${ID}/fields`);
    assert.equal(fields[1].description, "Catalogue field description");
    assert.equal(fields[0].business_name, "Account identifier");
  });

  test(`${adapter}: governed badges stay KGE's by either route while the tier follows the catalogue as a set`, async (context) => {
    const h = await harness(context, adapter);
    assert.equal((await h.request(`/api/admin/products/${ID}/metadata`, { badges: ["CERTIFIED"] }, "PATCH")).status, 400);
    await h.edit({ badges: ["GOLD", "SALES"] });
    assert.deepEqual((await h.card()).badges, ["GOLD", "SALES", "Certified"]);
    await h.publish(release("r2", (_value, product) => { product(ID).quality_score = 51; }));
    assert.deepEqual((await h.card()).badges, ["GOLD", "SALES", "Certified"]);
    await h.publish(release("r3", (_value, product) => { product(ID).badges = ["Silver", "Certified", "PII"]; }));
    assert.deepEqual((await h.card()).badges, ["SILVER", "SALES", "Certified", "PII"]);
    await h.publish(release("r4", (_value, product) => { product(ID).badges = ["Silver", "Sales team"]; }));
    assert.deepEqual((await h.card()).badges, ["SILVER", "SALES", "SALES TEAM"]);
    await h.edit({ badges: ["GOLD"] });
    await h.publish(release("r5", (_value, product) => { product(ID).badges = ["Bronze"]; }));
    assert.deepEqual((await h.card()).badges, ["GOLD"]);
    await h.publish(release("r6", (_value, product) => { product(ID).badges = ["Silver", "Restricted"]; }));
    assert.deepEqual((await h.card()).badges, ["SILVER", "Restricted"]);
  });

  test(`${adapter}: any portal edit to a card's badges protects the whole list, tier included, until a later catalogue change`, async (context) => {
    const h = await harness(context, adapter);
    await h.edit({ badges: ["GOLD", "SALES"] });
    await h.publish(release("r2", (_value, product) => { product(ID).badges = ["Silver", "Certified"]; }));
    assert.deepEqual((await h.card()).badges, ["GOLD", "SALES", "Certified"]);
    await h.publish(release("r3", (_value, product) => { product(ID).badges = ["Bronze", "Certified"]; }));
    assert.deepEqual((await h.card()).badges, ["BRONZE", "SALES", "Certified"]);
    await h.edit({ badges: ["SILVER", "SALES"] });
    await h.publish(release("r4", (_value, product) => { product(ID).badges = ["Bronze", "Sales team", "Certified"]; }));
    assert.deepEqual((await h.card()).badges, ["SILVER", "SALES", "Certified"]);
    await h.publish(release("r5", (_value, product) => { product(ID).badges = ["Bronze", "Certified"]; }));
    assert.deepEqual((await h.card()).badges, ["SILVER", "SALES", "Certified"]);
    await h.publish(release("r6", (_value, product) => { product(ID).badges = ["Gold", "Certified"]; }));
    assert.deepEqual((await h.card()).badges, ["GOLD", "SALES", "Certified"]);
  });

  test(`${adapter}: a non-tier badge KGE removes leaves the card, and KGE's several tiers give one`, async (context) => {
    const h = await harness(context, adapter);
    await h.publish(release("r2", (_value, product) => { product(ID).badges = ["Bronze", "Gold", "Sales team", "Certified"]; product(OTHER).badges = ["Silver", "Gold"]; }));
    assert.deepEqual((await h.card()).badges, ["BRONZE", "SALES TEAM", "Certified"]);
    assert.deepEqual((await h.card(OTHER)).badges, ["SILVER"]);
    await h.publish(release("r3", (_value, product) => { product(ID).badges = ["Gold", "Bronze", "Certified"]; }));
    assert.deepEqual((await h.card()).badges, ["GOLD", "Certified"]);
  });

  test(`${adapter}: a new card whose catalogue badges hold several tiers shows the first`, async (context) => {
    const { repository } = adapters[adapter]();
    context.after(() => repository.close());
    const value = release("multi", (_value, product) => { product(ID).badges = ["Silver", "Gold", "Certified"]; });
    await repository.syncCatalogue(value.release, value.products, "follow");
    assert.deepEqual((await repository.product(ID, ["retail"])).badges, ["SILVER", "Certified"]);
  });

  test(`${adapter}: a product leaving the release is hidden everywhere, its records kept, and shows again with its portal record`, async (context) => {
    const h = await harness(context, adapter);
    await h.json(`/api/favourites/${ID}`, {});
    await h.json(`/api/cart/${ID}`, {});
    await h.json("/api/requests", { productId: ID });
    await h.edit({ description: "Kept portal record" });
    const without = (name) => release(name, (value) => { value.products = value.products.filter((product) => product.id !== ID); });
    await h.publish(without("r2"));
    assert.deepEqual((await h.json("/api/products")).map((product) => product.id), [OTHER]);
    assert.deepEqual(await h.json("/api/products?q=Kept"), []);
    assert.ok(!(await h.json("/api/search", { query: "Retail" })).searchList.some((product) => product.id === ID));
    assert.equal((await h.request(`/api/products/${ID}`)).status, 404);
    assert.deepEqual(await h.json("/api/favourites"), []);
    assert.deepEqual(await h.json("/api/cart"), []);
    assert.deepEqual(await h.json("/api/requests"), []);
    assert.deepEqual(await h.json(`/metrics/${ADMIN}`), { products: 1, favourites: 0, requests: 0 });
    assert.equal((await h.repository.favourites(ADMIN, ["retail"])).length, 1);
    assert.equal((await h.repository.cart(ADMIN, ["retail"])).length, 1);
    assert.equal((await h.repository.requests(ADMIN, ["retail"])).length, 1);
    await h.publish(release("r3"));
    assert.equal((await h.card()).description, "Kept portal record");
    assert.deepEqual((await h.json("/api/favourites")).map((row) => row.product_id), [ID]);
    assert.deepEqual((await h.json("/api/cart")).map((row) => row.product_id), [ID]);
    assert.deepEqual((await h.json("/api/requests")).map((row) => row.product_id), [ID]);
    assert.deepEqual(await h.json(`/metrics/${ADMIN}`), { products: 2, favourites: 1, requests: 1 });
  });

  test(`${adapter}: a field absent from a release that returns takes the catalogue change made meanwhile`, async (context) => {
    const h = await harness(context, adapter);
    await h.publish(release("r2", (_value, product) => { product(ID).fields = product(ID).fields.filter((field) => field.name !== "amount"); }));
    await h.publish(release("r3", (_value, product) => { product(ID).fields[1].description = "Returned with a change"; }));
    assert.equal((await h.card()).fields.find((field) => field.name === "amount").description, "Returned with a change");
  });

  test(`${adapter}: a seeded value still on the card follows KGE at the first joined refresh; a value edited since stays`, async (context) => {
    const { repository } = adapters[adapter]();
    context.after(() => repository.close());
    const seed = release("seed", (_value, product) => { Object.assign(product(ID), { name: "Seed name", description: "Seed description", badges: ["Bronze"] }); product(OTHER).description = "Seed branch"; });
    await repository.syncCatalogue(seed.release, seed.products, true);
    await repository.updateMetadata(OTHER, { description: "Edited after the seed" });
    const joined = release("joined");
    await repository.syncCatalogue(joined.release, joined.products, "follow");
    const [account, branch] = [await repository.product(ID, ["retail"]), await repository.product(OTHER, ["retail"])];
    assert.equal(account.name, "Retail Accounts");
    assert.equal(account.description, joined.products[0].description);
    assert.deepEqual(account.badges, ["GOLD", "Certified"]);
    assert.equal(branch.description, "Edited after the seed");
  });

  test(`${adapter}: a card that returns takes the catalogue changes made while it was hidden`, async (context) => {
    const h = await harness(context, adapter);
    await h.publish(release("r2", (value) => { value.products = value.products.filter((product) => product.id !== ID); }));
    await h.publish({ ...release("r3"), has_catalogue: false, products: [] });
    await h.publish(release("r4", (_value, product) => { product(ID).description = "Changed while hidden"; }));
    assert.equal((await h.card()).description, "Changed while hidden");
  });

  test(`${adapter}: replaying the previous release's catalogue restores its values and visibility`, async (context) => {
    const h = await harness(context, adapter);
    const first = release("r1");
    await h.publish(release("r2", (value, product) => { product(ID).description = "Second release"; product(ID).badges = ["Bronze", "Certified"]; value.products = value.products.filter((item) => item.id !== OTHER); }));
    assert.equal((await h.card()).description, "Second release");
    assert.equal((await h.request(`/api/products/${OTHER}`)).status, 404);
    await h.publish(first);
    const card = await h.card();
    assert.equal(card.description, first.products[0].description);
    assert.deepEqual(card.badges, ["GOLD", "Certified"]);
    assert.equal((await h.card(OTHER)).description, "Retail Branch governed description");
  });

  test(`${adapter}: the first sync after an upgrade follows only where a card still holds the payload the old portal stored`, async (context) => {
    const { repository } = adapters[adapter]();
    context.after(() => repository.close());
    const before = release("old");
    await repository.syncCatalogue(before.release, before.products, true);
    await repository.updateMetadata(OTHER, { community: "Edited long ago", description: "Old portal edit", badges: ["BRONZE", "SALES"] });
    await repository.setGoverned({ release: before.release, products: before.products });
    const after = release("new", (_value, product) => { product(ID).description = "After upgrade"; product(ID).badges = ["Bronze", "Certified"]; Object.assign(product(OTHER), { domain: "New domain", community: "Catalogue community", description: "Catalogue description", badges: ["Silver"] }); });
    await repository.syncCatalogue(after.release, after.products, "follow");
    const [account, branch] = [await repository.product(ID, ["retail"]), await repository.product(OTHER, ["retail"])];
    assert.equal(account.description, "After upgrade");
    assert.equal(branch.domain, "New domain");
    assert.equal(branch.community, "Edited long ago");
    assert.equal(branch.description, "Old portal edit");
    assert.deepEqual(account.badges, ["BRONZE", "Certified"]);
    assert.deepEqual(branch.badges, ["BRONZE", "SALES"]);
    assert.ok((await repository.governed()).cards[ID]);
  });

  test(`${adapter}: the portal's own file only seeds, as before, and never rewrites an existing card`, async (context) => {
    const { repository } = adapters[adapter]();
    context.after(() => repository.close());
    const before = release("file-1");
    await repository.syncCatalogue(before.release, before.products, true);
    const after = release("file-2", (_value, product) => { Object.assign(product(ID), { description: "File change", badges: ["Bronze"], product_owner: "Office" }); product(ID).fields[0].description = "File field"; });
    await repository.syncCatalogue(after.release, after.products, true);
    const card = await repository.product(ID, ["retail"]);
    assert.equal(card.description, before.products[0].description);
    assert.deepEqual(card.badges, ["GOLD"]);
    assert.equal(card.fields[0].description, before.products[0].fields[0].description);
    assert.equal(card.product_owner, "Office");
  });
}

test("bigquery: edits landing between the sync's read and its write are kept, and the next catalogue change applies", async (context) => {
  const fake = bigQueryFake();
  const repository = openBigQueryRepository({ PROFILE: "meridian" }, { client: fake.client });
  context.after(() => repository.close());
  const first = release("r1");
  await repository.syncCatalogue(first.release, first.products, "follow");
  const query = fake.client.query;
  fake.client.query = async (job) => {
    if (job.query.startsWith("BEGIN TRANSACTION") && job.params?.productWrites) {
      Object.assign(fake.state.products.find((row) => row.id === ID), { description: "Racing edit", badges: JSON.stringify(["BRONZE", "SALES"]) });
      fake.state.fields.find((row) => row.product_id === ID && row.name === "amount").description = "Racing field edit";
    }
    return query(job);
  };
  const second = release("r2", (_value, product) => { product(ID).description = "Catalogue change"; product(ID).badges = ["Silver", "Certified"]; product(ID).fields[0].description = "Field change"; product(ID).fields[1].description = "Amount change"; });
  await repository.syncCatalogue(second.release, second.products, "follow");
  const sync = fake.state.statements.findLast((job) => job.params?.productWrites);
  assert.deepEqual(Object.keys(sync.types).sort(), Object.keys(sync.params).sort());
  const bound = Object.fromEntries(new BigQuery({ projectId: "test-project" }).buildQueryParams_(sync.params, sync.types).params.map((parameter) => [parameter.name, parameter.parameterType]));
  for (const name of ["productWrites", "fieldWrites"]) {
    assert.equal(bound[name].type, "ARRAY", name);
    assert.ok(bound[name].arrayType.structTypes.every((field) => field.type.type === "STRING"), name);
  }
  assert.deepEqual(bound.fieldWrites.arrayType.structTypes.map((field) => field.name), ["product_id", "name", "target", "held", "value"]);
  assert.deepEqual(sync.params.productWrites, [{ id: ID, target: "description", held: first.products[0].description, value: "Catalogue change" }, { id: ID, target: "badges", held: JSON.stringify(first.products[0].badges), value: JSON.stringify(["SILVER"]) }]);
  assert.deepEqual(sync.params.fieldWrites, [{ product_id: ID, name: "id", target: "description", held: "id description", value: "Field change" }, { product_id: ID, name: "amount", target: "description", held: "amount description", value: "Amount change" }]);
  let card = await repository.product(ID, ["retail"]);
  assert.equal(card.description, "Racing edit");
  assert.deepEqual(card.badges, ["BRONZE", "SALES", "Certified"]);
  assert.equal(card.fields[0].description, "Field change");
  assert.equal(card.fields[1].description, "Racing field edit");
  const memory = (await repository.governed()).cards[ID].card;
  assert.deepEqual({ description: memory.description, tier: memory.tier, badges: memory.badges, amount: memory.fields.amount.description }, { description: "Racing edit", tier: "BRONZE", badges: ["SALES"], amount: "Racing field edit" });
  fake.client.query = query;
  await repository.syncCatalogue("r3", release("r3", (_value, product) => { product(ID).description = "Next change"; product(ID).badges = ["Gold", "Certified"]; product(ID).fields[0].description = "Field change"; product(ID).fields[1].description = "Amount again"; }).products, "follow");
  card = await repository.product(ID, ["retail"]);
  assert.equal(card.description, "Next change");
  assert.deepEqual(card.badges, ["GOLD", "SALES", "Certified"]);
  assert.equal(card.fields[1].description, "Amount again");
});
