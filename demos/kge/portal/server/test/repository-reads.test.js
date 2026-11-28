import assert from "node:assert/strict";
import { test } from "node:test";
import { DatabaseSync } from "node:sqlite";
import { BigQuery } from "@google-cloud/bigquery";
import { openBigQueryRepository } from "../src/repository/bigquery.js";
import { openSqliteRepository } from "../src/repository/sqlite.js";
import { catalogue } from "./fixtures/catalogue.js";
import { bigQueryFake } from "./fixtures/bigquery-fake.js";
import { checked } from "./googlesql.js";

const emptyLineage = () => ({ origin_paths: [], destination_paths: [], upstream: [], downstream: [] });
const cards = (count) => Array.from({ length: count }, (_, index) => ({
  ...structuredClone(catalogue.products[0]), id: `product-${index}`, name: `Product ${String(index).padStart(2, "0")}`,
  fields: catalogue.products[0].fields.map((field) => ({ ...structuredClone(field), description: `${index}: ${field.name}`, quality: { ...field.quality, fail_percent: index + 0.5 } })),
  lineage: { origin_paths: [`source-${index}`, `source-${index}-second`], destination_paths: [`destination-${index}`], upstream: index ? [`product-${index - 1}`] : [], downstream: index + 1 < count ? [`product-${index + 1}`] : [] },
}));

function open(adapter, context) {
  let repository, calls, clearGoverned;
  if (adapter === "bigquery") {
    const fake = bigQueryFake(); calls = fake.state.statements;
    repository = openBigQueryRepository({ PROFILE: "meridian" }, { client: checked(fake.client) });
    clearGoverned = () => { fake.state.governed = null; };
  } else {
    calls = [];
    const prepare = DatabaseSync.prototype.prepare;
    context.mock.method(DatabaseSync.prototype, "prepare", function (query) {
      const statement = prepare.call(this, query), all = statement.all;
      statement.all = function (...params) { calls.push({ query, params }); return all.apply(this, params); };
      return statement;
    });
    repository = openSqliteRepository({ PORTAL_SQLITE_PATH: ":memory:" });
    clearGoverned = () => repository.setGoverned(null);
  }
  context.after(() => repository.close());
  return { repository, calls, clearGoverned };
}

for (const adapter of ["sqlite", "bigquery"]) {
  for (const count of [3, 30]) test(`${adapter}: listing ${count} products runs exactly three reads with unchanged ordered results (#258)`, async (context) => {
    const { repository, calls, clearGoverned } = open(adapter, context), products = cards(count);
    await repository.syncCatalogue("initial", products);
    // Without an overlay, the stored child rows themselves must be hydrated.
    await clearGoverned();
    calls.length = 0;
    assert.equal(JSON.stringify(await repository.listProducts(["retail"])), JSON.stringify(products));
    assert.equal(calls.length, 3);
    assert.match(calls[1].query, /JOIN .*_portal_quality_rules/);
    assert.match(calls[1].query, /ORDER BY f.product_id,f.ordinal/);
    assert.match(calls[2].query, /ORDER BY product_id,direction,ordinal/);
    if (adapter === "bigquery") for (const job of calls.slice(1)) {
      assert.match(job.query, /product_id IN UNNEST\(@ids\)/);
      assert.deepEqual(job.params.ids, products.map(({ id }) => id));
      const [parameter] = new BigQuery({ projectId: "test" }).buildQueryParams_(job.params, job.types).params;
      assert.deepEqual(parameter.parameterType, { type: "ARRAY", arrayType: { type: "STRING" } });
    }
    for (const method of ["product", "fields", "lineage"]) {
      calls.length = 0;
      const expected = method === "product" ? products[1] : products[1][method];
      assert.equal(JSON.stringify(await repository[method](products[1].id, ["retail"])), JSON.stringify(expected));
      assert.equal(calls.length, 3);
      assert.match(calls[0].query, adapter === "bigquery" ? /AND id=@id/ : /AND id=\?/);
      if (adapter === "bigquery") {
        assert.equal(calls[0].params.id, products[1].id);
        for (const job of calls.slice(1)) assert.deepEqual(job.params.ids, [products[1].id]);
      } else for (const job of calls.slice(1)) assert.deepEqual(job.params, [products[1].id]);
    }
    await repository.updateMetadata(products[1].id, { description: "Changed after the first read" });
    assert.equal((await repository.product(products[1].id, ["retail"])).description, "Changed after the first read");
  });

  test(`${adapter}: search and detail retain governed overlays, active units and readable lineage (#258)`, async (context) => {
    const { repository, calls, clearGoverned } = open(adapter, context), products = cards(4);
    products[2].unit_id = "wealth";
    products[0].lineage.upstream = [products[1].id, products[2].id, products[3].id, "missing"];
    products[0].lineage.downstream = [...products[0].lineage.upstream];
    await repository.syncCatalogue("seed", products, true);
    await repository.updateMetadata(products[0].id, { description: "Portal description", badges: ["BRONZE", "SALES"], fields: [{ name: "id", business_name: "Portal field", description: "Portal field description" }] });
    const governed = structuredClone(products.slice(0, 3));
    Object.assign(governed[0], { quality_score: 17, records: null, storage: "", badges: ["GOLD", "Restricted"] });
    governed[0].fields[0].quality.fail_percent = 13.5;
    await repository.setGoverned({ products: governed });
    const expected = { ...governed[0], description: "Portal description", badges: ["BRONZE", "SALES", "Restricted"], fields: governed[0].fields.map((field, index) => index ? field : { ...field, business_name: "Portal field", description: "Portal field description" }), lineage: { ...governed[0].lineage, upstream: [products[1].id], downstream: [products[1].id] } };
    calls.length = 0;
    assert.equal(JSON.stringify(await repository.listProducts(["retail"], products[0].name)), JSON.stringify([expected]));
    assert.equal(calls.length, 3);
    if (adapter === "bigquery") for (const job of calls.slice(1)) assert.deepEqual(job.params.ids, [products[0].id]);
    assert.equal(JSON.stringify(await repository.product(products[0].id, ["retail"])), JSON.stringify(expected));
    assert.equal(JSON.stringify(await repository.fields(products[0].id, ["retail"])), JSON.stringify(expected.fields));
    assert.deepEqual(await repository.lineage(products[0].id, ["retail"]), expected.lineage);
    assert.deepEqual((await repository.listProducts(["retail"])).map(({ id }) => id), products.slice(0, 2).map(({ id }) => id));
    for (const id of [products[2].id, products[3].id, "missing"]) {
      assert.equal(await repository.product(id, ["retail"]), undefined);
      assert.deepEqual(await repository.fields(id, ["retail"]), []);
      assert.deepEqual(await repository.lineage(id, ["retail"]), emptyLineage());
    }
    // A previously stored id reassigned to an unreadable governed unit stays hidden.
    await repository.setGoverned({ products: [{ ...governed[0], unit_id: "wealth" }] });
    assert.equal(await repository.product(products[0].id, ["retail"]), undefined);
    await repository.setGoverned({ products: [] });
    assert.deepEqual(await repository.listProducts(["retail"]), []);
    await clearGoverned();
    const own = await repository.product(products[0].id, ["retail"]);
    assert.deepEqual(own.lineage.upstream, [products[1].id, products[3].id]);
    assert.deepEqual(await repository.listProducts([]), []);
    assert.equal(await repository.product(products[0].id, []), undefined);
    assert.deepEqual(await repository.listProducts(["retail"], "no matches"), []);
  });

  test(`${adapter}: empty children and missing ids keep their original defaults (#258)`, async (context) => {
    const { repository, calls, clearGoverned } = open(adapter, context), [product] = cards(1);
    product.fields = []; product.lineage = emptyLineage();
    await repository.syncCatalogue("empty-children", [product]);
    await clearGoverned();
    assert.equal(JSON.stringify(await repository.product(product.id, ["retail"])), JSON.stringify(product));
    calls.length = 0;
    assert.equal(await repository.product("missing", ["retail"]), undefined);
    assert.equal(calls.length, 3);
    if (adapter === "bigquery") for (const job of calls.slice(1)) {
      assert.deepEqual(job.params.ids, []);
      assert.deepEqual(job.types, { ids: ["STRING"] });
    }
  });
}
