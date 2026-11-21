import assert from "node:assert/strict";
import test from "node:test";
import { createApp } from "../src/app.js";
import { openSqliteRepository } from "../src/repository/sqlite.js";
import { catalogue } from "./fixtures/catalogue.js";

const routes = [
  ["/ask", { products: ["retail-accounts"] }],
  ["/api/ask", { products: ["retail.accounts"] }],
  ["/ask-data-product", { productId: "retail-accounts" }],
  ["/ask-domain", { productIds: ["retail-accounts", "retail.accounts"], domain: "Retail" }],
];

function fixture(context) {
  const repository = openSqliteRepository({ PORTAL_SQLITE_PATH: ":memory:" });
  repository.syncCatalogue(catalogue.release, catalogue.products, true);
  const principal = "admin.meridian@example.com";
  repository.setRole(principal, "admin");
  const calls = [];
  let reply = {};
  let status = 200;
  let identityUnavailable = false;
  let now = 0;
  const kge = {
    identity: async () => {
      if (identityUnavailable) throw new TypeError("Identity endpoint unavailable");
      return { principal, units: ["retail", "sme"] };
    },
    request: async (_req, path, options) => {
      calls.push({ path, body: JSON.parse(options.body) });
      return Response.json(reply, { status });
    },
  };
  const { app } = createApp({ repository, kge, catalogueSync: false, env: {}, clock: () => now, uiPath: "not-present" });
  const server = app.listen(0);
  context.after(() => { server.close(); repository.close(); });
  return {
    repository,
    calls,
    respond(value, code = 200) { reply = value; status = code; },
    stopIdentity() { identityUnavailable = true; },
    expireIdentity() { now += 300001; },
    request: (path, body, cookie = "session=verified") => fetch(`http://127.0.0.1:${server.address().port}${path}`, {
      method: "POST",
      headers: { "content-type": "application/json", cookie },
      body: JSON.stringify(body),
    }),
  };
}

for (const [route, scope] of routes) {
  test(`${route} passes connected questions and preserves the authoritative Assist answer`, async (context) => {
    const f = fixture(context);
    const authoritative = {
      answer_text: "The connected assistant's answer about SME lending.",
      behaviour: "figure",
      source: "ask",
      products: ["sme.lending"],
      sources: [{ kind: "product", id: "sme.lending", title: "SME Lending", unit: "sme", why: "Relevant to the question" }],
      figures: [{ unit: "sme", table: "lending", value: 42 }],
      citations: [{ id: "sme.lending.guide", title: "SME lending guide", unit: "sme" }],
      notice: "An authoritative Assist notice.",
    };
    f.respond(authoritative);
    for (const question of ["Who owns wealth data?", "Tell me something about an unknown subject", "Total SME lending?", "Describe this product", "Which fields relate to credit risk?"]) {
      const response = await f.request(route, {
        ...scope, question, arm: "browser-controlled", stages: { answer: false }, purpose: "browser-controlled", principal: "someone-else",
      });
      assert.equal(response.status, 200);
      assert.deepEqual(f.calls.at(-1), { path: "/assist", body: { question, products: ["retail.accounts"] } });
      const answer = await response.json();
      assert.deepEqual(answer, {
        ...authoritative,
        ...(scope.productId ? { productId: "retail-accounts" } : {}),
        ...(scope.domain ? { domain: scope.domain } : {}),
        provider: "kge", fallback: false,
      });
    }
    assert.equal(f.calls.length, 5);
    const response = await f.request(route, { question: "An arbitrary unscoped question" });
    assert.equal(response.status, 200);
    assert.deepEqual(f.calls.at(-1), { path: "/assist", body: { question: "An arbitrary unscoped question" } });
    assert.deepEqual(await response.json(), { ...authoritative, provider: "kge", fallback: false });
  });

  test(`${route} still asks Assist while verified identity is cached during an identity outage`, async (context) => {
    const f = fixture(context);
    const authoritative = {
      answer_text: "Assist remains authoritative while identity is unavailable.",
      behaviour: "figure", source: "ask", products: ["sme.lending"],
      sources: [{ kind: "product", id: "sme.lending", title: "SME Lending", unit: "sme", why: "Relevant to the question" }],
      figures: [{ unit: "sme", table: "lending", value: 42 }],
      citations: [{ id: "sme.lending.guide", title: "SME lending guide", unit: "sme" }],
    };
    f.respond(authoritative);
    const body = { ...scope, question: "Who owns wealth data?" };
    const warm = await f.request(route, body);
    assert.equal(warm.status, 200);
    await warm.json();
    f.stopIdentity();
    const response = await f.request(route, body);
    assert.equal(response.status, 200);
    assert.equal(f.calls.length, 2);
    assert.deepEqual(f.calls.at(-1), { path: "/assist", body: { question: body.question, products: ["retail.accounts"] } });
    assert.deepEqual(await response.json(), {
      ...authoritative,
      ...(scope.productId ? { productId: "retail-accounts" } : {}),
      ...(scope.domain ? { domain: scope.domain } : {}),
      provider: "kge", fallback: false, notice: "",
    });
    const unknown = await f.request(route, body, "session=never-verified");
    assert.equal(unknown.status, 502);
    await unknown.text();
    assert.equal(f.calls.length, 2);
    f.expireIdentity();
    const expired = await f.request(route, body);
    assert.equal(expired.status, 502);
    await expired.text();
    assert.equal(f.calls.length, 2);
  });

  test(`${route} preserves connected abstentions and refusals without local fallback`, async (context) => {
    const f = fixture(context);
    for (const behaviour of ["abstain", "refuse"]) {
      const authoritative = { answer_text: `Assist chose to ${behaviour}.`, behaviour, source: "ask", sources: [], figures: [], citations: [] };
      f.respond(authoritative);
      const response = await f.request(route, { ...scope, question: "Who owns Retail Accounts?" });
      assert.equal(response.status, 200);
      const answer = await response.json();
      for (const [key, value] of Object.entries(authoritative)) assert.deepEqual(answer[key], value);
      assert.equal(answer.provider, "kge");
      assert.equal(answer.fallback, false);
      assert.deepEqual(answer.products, ["retail.accounts"]);
    }
    f.respond({ detail: "Assist refused this request." }, 403);
    const refused = await f.request(route, { ...scope, question: "Who owns Retail Accounts?" });
    assert.equal(refused.status, 403);
    assert.match(await refused.text(), /Assist refused this request/);
    assert.equal(f.calls.length, 3);
  });

  test(`${route} keeps invalid, unreadable and unpublished scope at the HTTP boundary`, async (context) => {
    const f = fixture(context);
    f.respond({ answer_text: "This should never be requested." });
    for (const products of [["unknown.product"], ["wealth.holdings"], ["retail.accounts", "wealth.holdings"]]) {
      const response = await f.request(route, { question: "Any question", products });
      assert.equal(response.status, 404);
      assert.deepEqual(await response.json(), { message: "Product not found." });
    }
    f.repository.setPublished("retail-accounts", false);
    const hidden = await f.request(route, { ...scope, question: "Any question" });
    assert.equal(hidden.status, 404);
    assert.deepEqual(await hidden.json(), { message: "Product not found." });
    assert.equal(f.calls.length, 0);
  });
}
