import assert from "node:assert/strict";
import test from "node:test";
import { createApp } from "../src/app.js";
import { openSqliteRepository } from "../src/repository/sqlite.js";

// #613: Discover's Ask sends the history KGE returned on its previous answer; the portal passes it to /assist
// unchanged and adds no logic of its own. The stub plays KGE: it appends a turn and resolves a follow-up only when
// the history it is sent carries the earlier question's plan.
// A small invented catalogue of its own: two units, one product each.
const field = (name) => ({ name, business_name: name.replaceAll("_", " "), data_type: "VARCHAR", description: `${name} description`, terms: [name], classification: "", pii: false, quality: { dimension: "Completeness", rule: `${name} is present`, rule_type: "completeness", fail_percent: 1, description: "" } });
const product = (id, unit, name) => ({ id, unit_id: unit, table: id.split("-").slice(1).join("_"), name, description: `${name} description`, product_owner: "Steward", business_owner: "Owner", badges: [], quality_score: 90, last_refresh: "2026-10-20T06:00:00Z", ingested_at: "2026-10-20T06:30:00Z", counts: { subscribe: 1, access: 1, views: 1 }, rating: 4, domain: `${unit} domain`, community: `${unit} community`, application: `${unit} application`, product_type: "TABLE", records: 100, path: `Synthetic > ${unit} > ${name}`, data_format: "Parquet", storage: "1 MB", last_dq_run: "2026-10-20T06:00:00Z", asset_type: "Structured", usage_purpose: "Analytical", dataset_type: "Master data", APM_ID: `APM-${unit}`, project: "", product_owner_email: "", business_owner_email: "", refresh_cadence: "", schema: "", use_cases: [], fields: [field("id"), field("amount")], lineage: { origin_paths: [], destination_paths: [], upstream: [], downstream: [] } });
const catalogue = { contract_version: "1", release: "synthetic-release", has_catalogue: true, products: [product("north-orders", "north", "North Orders"), product("south-stock", "south", "South Stock")] };

const turn = (id, question) => ({ turn_id: id, question, plan: { measure: "synthetic_total" }, clarification: null, binding: { release: "r1" } });

function fixture(context, { kgeDown = false, kgeStatus = 200 } = {}) {
  const repository = openSqliteRepository({ PORTAL_SQLITE_PATH: ":memory:" });
  repository.syncCatalogue(catalogue.release, catalogue.products, true);
  const principal = "analyst@example.com";
  repository.setRole(principal, "admin");
  const calls = [];
  const kge = {
    identity: async () => ({ principal, units: ["north", "south"] }),
    request: async (_req, path, options) => {
      const body = JSON.parse(options.body);
      calls.push({ path, body });
      if (kgeDown) throw new TypeError("fetch failed");
      if (kgeStatus !== 200) return Response.json({ detail: [{ type: "too_long", loc: ["body", "history"] }] }, { status: kgeStatus });
      const history = body.history || [];
      const question = body.question;
      const answer_text = /^and by month/i.test(question)
        ? (history.at(-1)?.plan ? `Monthly ${history.at(-1).plan.measure}: 10, 12, 14` : "Which measure do you mean?")
        : `Answer to ${question}`;
      return Response.json({ answer_text, behaviour: history.length || !/^and/i.test(question) ? "figure" : "abstain", source: "ask", sources: [], history: [...history, turn(`t${calls.length}`, question)] });
    },
  };
  const { app } = createApp({ repository, kge, catalogueSync: false, env: {}, uiPath: "not-present" });
  const server = app.listen(0);
  context.after(() => { server.close(); repository.close(); });
  return {
    calls,
    request: (path, body) => fetch(`http://127.0.0.1:${server.address().port}${path}`, {
      method: "POST", headers: { "content-type": "application/json", cookie: "session=verified" }, body: JSON.stringify(body),
    }),
  };
}

for (const [route, scope] of [["/ask-data-product", { productId: "north-orders" }], ["/ask-domain", { productIds: ["north-orders"], domain: "North" }]]) {
  test(`${route} sends the previous answer's history to /assist unchanged and the stubbed KGE resolves the follow-up`, async (context) => {
    const f = fixture(context);
    const first = await f.request(route, { ...scope, question: "Total synthetic sales?" });
    assert.equal(first.status, 200);
    const firstAnswer = await first.json();
    assert.deepEqual(f.calls[0], { path: "/assist", body: { question: "Total synthetic sales?", products: ["north.orders"] } });
    assert.deepEqual(firstAnswer.history, [turn("t1", "Total synthetic sales?")]);

    const second = await f.request(route, { ...scope, question: "And by month?", history: firstAnswer.history });
    assert.equal(second.status, 200);
    const secondAnswer = await second.json();
    assert.deepEqual(f.calls[1], { path: "/assist", body: { question: "And by month?", products: ["north.orders"], history: firstAnswer.history } });
    assert.equal(secondAnswer.answer_text, "Monthly synthetic_total: 10, 12, 14");
    assert.deepEqual(secondAnswer.history, [...firstAnswer.history, turn("t2", "And by month?")]);
  });
}

test("/ask and /api/ask pass history through as given, unread", async (context) => {
  const f = fixture(context);
  const opaque = [{ turn_id: "x", anything: { nested: [1, "two", null] } }];
  for (const path of ["/ask", "/api/ask"]) {
    const response = await f.request(path, { question: "And by month?", history: opaque });
    assert.equal(response.status, 200);
    assert.deepEqual(f.calls.at(-1).body, { question: "And by month?", history: opaque });
  }
});

test("a history KGE rejects is its 422, passed back to the page", async (context) => {
  const f = fixture(context, { kgeStatus: 422 });
  const response = await f.request("/ask-data-product", { productId: "north-orders", question: "And by month?", history: Array.from({ length: 9 }, (_, i) => turn(`t${i}`, "q")) });
  assert.equal(response.status, 422);
  assert.equal(f.calls[0].body.history.length, 9);
});

test("the portal-alone catalogue fallback accepts history and answers from the catalogue", async (context) => {
  const f = fixture(context, { kgeDown: true });
  const response = await f.request("/ask-data-product", { productId: "north-orders", question: "Who owns north-orders?", history: [turn("t1", "Total synthetic sales?")] });
  assert.equal(response.status, 200);
  const answer = await response.json();
  assert.equal(answer.provider, "portal");
  assert.equal(answer.fallback, true);
  assert.equal(answer.behaviour, "catalogue");
  assert.equal(answer.history, undefined);
});
