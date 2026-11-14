import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import { openProviders, validateAskSettings } from "../src/providers.js";
import { askChain, catalogueAsk, fallbackNotice, localKnowledge } from "../src/knowledge.js";
import { hasUncataloguedNumber } from "../src/catalogue-agent.js";
import { mockAskModel } from "../src/ask-model/mock.js";
import { bigQueryFake } from "./fixtures/bigquery-fake.js";
import { openBigQueryRepository } from "../src/repository/bigquery.js";
import { catalogue } from "./fixtures/catalogue.js";
import { createApp } from "../src/app.js";
import { kgeClient } from "../src/kge.js";

const cases = JSON.parse(readFileSync(new URL("./fixtures/ask-fallback-eval.json", import.meta.url), "utf8"));
const account = { ...catalogue.products[0], name: "Client Account Master", domain: "Retail", fields: [
  { name: "id", business_name: "Account identifier", description: "Account identifier", terms: [] },
  { name: "region_code", business_name: "Region code", description: "Region code identifies the account's reporting region.", terms: ["ISO 3166 region identifier"] },
  { name: "nna", business_name: "Certified NNA", description: "Certified NNA means certified net new assets.", terms: ["Net new assets"] },
] };
const req = () => ({ portalIdentity: { units: ["retail"], fallback: true } });
const repository = { listProducts: async () => [account, ...catalogue.products.slice(1)], publication: async () => [], promptDirection: async () => ({ text: "" }) };
const modelEnv = { PORTAL_ASK_MODEL: "gemini-2.5-flash", PORTAL_ASK_MODEL_PROVIDER: "mock" };

for (const start of ["kge", "portal", "none"]) for (const state of ["up", "down"]) for (const model of ["set", "unset", "failing"]) {
  test(`Ask chain ${start}, KGE ${state}, model ${model}`, async () => {
    let kgeCalls = 0, modelCalls = 0;
    const upstream = { answer_text: "A governed answer with 42.", behaviour: "figure", figures: [{ value: 42 }], citations: [], phrased_by: "rules" };
    const kge = { request: async () => { kgeCalls += 1; if (state === "down") throw new TypeError("offline"); return Response.json(upstream); } };
    const ask = openProviders({ PORTAL_ASK_PROVIDER: start, ...(model !== "unset" ? modelEnv : {}) }, kge, "kge", repository, { reply: () => { modelCalls += 1; if (model === "failing") throw new Error("model failed"); return cases[0].reply; } }).ask;
    const result = await ask.run(req(), { question: cases[0].question });
    const connected = start === "kge" && state === "up";
    assert.equal(kgeCalls, start === "kge" ? 1 : 0);
    assert.equal(modelCalls, !connected && start !== "none" && model !== "unset" ? 1 : 0);
    assert.equal(result.provider, connected ? "kge" : "portal");
    assert.equal(result.phrased_by, connected ? "rules" : start !== "none" && model === "set" ? "portal" : "catalogue");
    assert.equal(result.fallback, start === "kge" && state === "down");
    assert.equal(result.notice, start === "kge" && state === "down" ? fallbackNotice : "");
    if (connected) for (const key of Object.keys(upstream)) assert.deepEqual(result[key], upstream[key]);
    else { assert.equal(result.behaviour, "catalogue"); assert.deepEqual(result.figures, []); }
  });
}

for (const row of cases.filter((row) => !row.catalogue)) test(`fallback evaluation: ${row.id}`, async () => {
  const seen = [];
  const ask = openProviders({ PORTAL_ASK_PROVIDER: "kge", ...modelEnv }, { request: async () => { throw new TypeError("KGE stopped"); } }, "kge", repository, { reply: (input) => { seen.push(input); return row.reply; } }).ask;
  const answer = await ask.run(req(), { question: row.question });
  assert.equal(answer.fallback, true);
  assert.deepEqual([...new Set(answer.citations.map((c) => c.product.id))], row.expected_products);
  assert.equal(answer.phrased_by, row.decline ? "catalogue" : "portal");
  assert.deepEqual(answer.figures, []);
  assert.deepEqual(Object.keys(seen[0]).sort(), ["catalogue", "question"]);
  assert.deepEqual(seen[0].catalogue.map((p) => p.product_id), [account.id]);
  assert.doesNotMatch(JSON.stringify(seen[0].catalogue), /Wealth Holdings|records|quality_score|last_refresh/);
  if (row.decline) {
    assert.match(answer.behaviour, /^abstain/);
    assert.deepEqual(answer, { ...catalogueAsk([account], { question: row.question }), provider: "portal", fallback: true, notice: fallbackNotice });
  } else {
    assert.ok(answer.answer_text.includes(row.expected_text));
    if (row.id === "owner") assert.equal(answer.answer_text, `${account.name}: ${row.expected_text}`);
    for (const statement of answer.answer_text.split("\n\n")) assert.match(statement, /^Client Account Master/);
    if (row.reply.statements[0].field) assert.equal(answer.citations[0].field, row.reply.statements[0].field);
  }
  if (row.never_named) assert.ok(!JSON.stringify(answer).includes(row.never_named));
});

test("KGE answer content passes byte-for-byte without a catalogue or model read", async () => {
  const upstream = { answer_text: "  KGE [1]\n\n£13,287.67 — unchanged.\n", behaviour: "combined", figures: [{ value: 13287.67 }], citations: [{ n: 1, title: "Certified source" }], phrased_by: "vertex", sources: [{ title: "Authoritative" }], source: "ask", notice: "KGE notice" };
  const ask = openProviders({ ...modelEnv }, { request: async () => Response.json(upstream) }, "kge", { listProducts: () => assert.fail("KGE answers must not retrieve catalogue") }, { reply: () => assert.fail("KGE answers must not call model") }).ask;
  const answer = await ask.run(req(), { question: "Anything" });
  const content = Object.fromEntries(Object.keys(upstream).map((key) => [key, answer[key]]));
  assert.equal(JSON.stringify(content), JSON.stringify(upstream));
  assert.equal(Buffer.compare(Buffer.from(answer.answer_text), Buffer.from(upstream.answer_text)), 0);
});

for (const status of [400, 401, 403, 404, 429]) test(`KGE ${status} ends the chain`, async () => {
  const ask = openProviders(modelEnv, { request: async () => Response.json({ detail: "Refused" }, { status, headers: { "retry-after": "5" } }) }, "kge", repository, { reply: () => assert.fail("No model after refusal") }).ask;
  await assert.rejects(ask.run(req(), { question: cases[0].question }), { status, retryAfter: "5" });
});
for (const status of [408, 500, 503]) test(`KGE ${status} hands over to portal`, async () => {
  const ask = openProviders(modelEnv, { request: async () => Response.json({}, { status }) }, "kge", repository, { reply: cases[0].reply }).ask;
  assert.equal((await ask.run(req(), { question: cases[0].question })).phrased_by, "portal");
});
for (const behaviour of ["refuse", "abstain", "blocked_access"]) test(`KGE ${behaviour} ends the chain`, async () => {
  const ask = openProviders(modelEnv, { request: async () => Response.json({ answer_text: "KGE decision", behaviour }) }, "kge", repository, { reply: () => assert.fail("No model after a KGE decision") }).ask;
  assert.equal((await ask.run(req(), { question: cases[0].question })).behaviour, behaviour);
});

for (const [name, reply] of [
  ["no statements", { statements: [] }],
  ["a discarded excerpt", { statements: [{ product_id: account.id, text: "Total NNA is 987654." }] }],
]) test(`KGE stopped and portal returns ${name}: rules answer the owner question`, async () => {
  let kgeCalls = 0, modelCalls = 0;
  const kge = { request: async () => { kgeCalls += 1; throw new TypeError("KGE stopped"); } };
  const ask = openProviders({ PORTAL_ASK_PROVIDER: "kge", ...modelEnv }, kge, "kge", repository, { reply: () => { modelCalls += 1; return reply; } }).ask;
  const body = { question: `Who owns ${account.name}?` };
  const answer = await ask.run(req(), body);
  assert.equal(kgeCalls, 1);
  assert.equal(modelCalls, 1);
  assert.deepEqual(answer, { ...catalogueAsk([account], body), provider: "portal", fallback: true, notice: fallbackNotice });
  assert.equal(answer.answer_text, `${account.name}: Data domain owner: Administrator; Data steward: Steward.`);
  assert.equal(answer.behaviour, "catalogue");
  assert.equal(answer.phrased_by, "catalogue");
});

for (const provider of ["kge", "portal", "none"]) for (const [name, reply] of [
  ["no statements", { statements: [] }],
  ["a discarded excerpt", { statements: [{ product_id: account.id, text: "Total NNA is 987654." }] }],
]) test(`${provider} returns scoped rules answers and declines after ${name}`, async () => {
  const seen = [];
  let kgeCalls = 0;
  const kge = { request: async () => { kgeCalls += 1; throw new TypeError("KGE stopped"); } };
  const ask = openProviders({ PORTAL_ASK_PROVIDER: provider, ...modelEnv }, kge, "kge", repository, { reply: (input) => {
    seen.push(input);
    assert.notEqual(provider, "none", "none must not call the model");
    return reply;
  } }).ask;
  for (const [scope, selected] of [
    [{ domain: "Other" }, []],
    [{ domain: "Retail" }, [account]],
    [{ domain: "All data assets" }, [account]],
    [{ productId: account.id }, [account]],
    [{ products: [] }, []],
    [{ products: [account.id], domain: "Other" }, [account]],
  ]) for (const question of ["Who owns client accounts?", "What fields are in client accounts?"]) {
    const body = { question, ...scope };
    const answer = await ask.run(req(), body);
    assert.deepEqual(answer, { ...catalogueAsk(selected, body), provider: "portal", fallback: provider === "kge", notice: provider === "kge" ? fallbackNotice : "" });
    assert.equal(answer.phrased_by, "catalogue");
    assert.equal(answer.behaviour, selected.length ? "catalogue" : scope.products ? "abstain" : "abstain_catalogue_only");
    if (selected.length) assert.match(answer.answer_text, /Client Account Master(?:: Data domain owner: Administrator; Data steward: Steward\.| contains these fields:)/);
    else assert.doesNotMatch(answer.answer_text, /Client Account Master|Administrator|Steward|region_code/);
    if (provider !== "none") assert.deepEqual(seen.at(-1).catalogue.map((p) => p.product_id), selected.map((p) => p.id));
  }
  assert.equal(kgeCalls, provider === "kge" ? 12 : 0);
  assert.equal(seen.length, provider === "none" ? 0 : 12);
});

for (const text of ["Total NNA is 987654.", "Total NNA is forty billion.", "Wealth Holdings owns this field."]) test(`unsafe model excerpt discarded: ${text}`, async () => {
  const ask = openProviders(modelEnv, undefined, "file", repository, { reply: { statements: [{ product_id: account.id, text }] } }).ask;
  const body = { question: cases[0].question };
  const answer = await ask.run(req(), body);
  assert.deepEqual(answer, { ...catalogueAsk([account], body), provider: "portal", fallback: false, notice: "" });
});

test("number guard accepts catalogue numbers and rejects new numeric and written figures", () => {
  assert.equal(hasUncataloguedNumber("ISO 3166", "ISO 3166 region identifier"), false);
  for (const value of ["12,345.67", "-12.5%", "forty billion", "1e9"]) assert.equal(hasUncataloguedNumber(value, "Catalogue metadata"), true);
});

test("field citations permit a number already retrieved in catalogue text", async () => {
  const local = localKnowledge(repository, mockAskModel({ statements: [{ product_id: account.id, field: "region_code", text: "ISO 3166 region identifier" }] }));
  const answer = await local.ask(req(), { question: "What region identifier is used?" });
  assert.match(answer.answer_text, /ISO 3166/);
  assert.equal(answer.citations[0].field, "region_code");
});

for (const reply of [null, {}, { statements: null }, { statements: [null] }, { statements: [{ product_id: account.id, text: false }] }, { statements: [{ product_id: "wealth-holdings", text: "Wealth Holdings" }] }, { statements: [{ product_id: account.id, field: "secret", text: "Secret field" }] }, { statements: [{ product_id: account.id, text: "" }] }]) test(`invalid model reply falls through: ${JSON.stringify(reply)}`, async () => {
  const local = localKnowledge(repository, mockAskModel(reply));
  const answer = await local.ask(req(), { question: cases[0].question });
  assert.equal(answer.phrased_by, "catalogue");
  assert.doesNotMatch(JSON.stringify(answer), /Wealth Holdings|secret/);
});

test("model retrieval respects product, empty and domain scope and publication", async () => {
  const seen = [];
  const local = localKnowledge({ ...repository, publication: async () => [{ product_id: "sme-lending", published: false }] }, mockAskModel((input) => { seen.push(input); return { statements: [] }; }));
  for (const scope of [{ productId: account.id }, { products: [] }, { domain: "Retail" }, { domain: "Other" }]) await local.ask(req(), { question: "What is available?", ...scope });
  assert.deepEqual(seen.map((input) => input.catalogue.map((p) => p.product_id)), [[account.id], [], [account.id], []]);
  for (const scope of [{ productId: "wealth-holdings" }, { products: ["sme-lending"] }]) await assert.rejects(local.ask({ portalIdentity: { units: ["retail", "sme"] } }, { question: "Who owns this?", ...scope }), { status: 404 });
  assert.equal(seen.length, 4, "access refusals do not reach the model");
});

for (const domain of ["retail", "Retai"]) test(`rules and agent require an exact domain match: ${domain}`, async () => {
  const body = { question: "Who owns client accounts?", domain };
  const rules = catalogueAsk([account], body);
  assert.equal(rules.behaviour, "abstain_catalogue_only");
  assert.deepEqual(rules.citations, []);
  assert.doesNotMatch(rules.answer_text, /Client Account Master|Administrator|Steward/);
  const seen = [];
  const local = localKnowledge(repository, mockAskModel((input) => {
    seen.push(input);
    return { statements: [{ product_id: account.id, text: "Data domain owner: Administrator; Data steward: Steward." }] };
  }));
  const answer = await local.ask(req(), body);
  assert.equal(seen.length, 1);
  assert.deepEqual(seen[0].catalogue, []);
  assert.deepEqual(answer, rules);
  assert.deepEqual(answer.citations, []);
  assert.doesNotMatch(answer.answer_text, /Client Account Master|Administrator|Steward/);
});

test("none keeps owner and fields shapes with partial names and plural matching", () => {
  for (const question of ["Who owns client accounts?", "Who owns CLIENT ACCOUNT?", "What fields are in client accounts?", "What does client accounts contain?"]) assert.equal(catalogueAsk([account], { question }).behaviour, "catalogue");
  for (const question of ["Who owns wealth accounts?", "Who owns this product?", "Which product holds region code?"]) assert.match(catalogueAsk([account], { question }).behaviour, /^abstain/);
  const exact = { ...account, id: "retail.client_accounts", name: "Client Accounts", product_owner: "Exact owner" };
  const answer = catalogueAsk([account, exact], { question: "Who owns client accounts?" });
  assert.match(answer.answer_text, /Exact owner/);
  assert.doesNotMatch(answer.answer_text, /Client Account Master/);
});

for (const provider of ["none", "portal"]) test(`${provider} owner replies use page labels, missing values and unchanged citations`, async () => {
  for (const [owners, expected] of [
    [{ business_owner: "Domain Owner", product_owner: "Product Steward" }, "Data domain owner: Domain Owner; Data steward: Product Steward."],
    [{ business_owner: "Domain Owner" }, "Data domain owner: Domain Owner; Data steward: not recorded."],
    [{ product_owner: "Product Steward" }, "Data domain owner: not recorded; Data steward: Product Steward."],
    [{}, "Data domain owner: not recorded; Data steward: not recorded."],
    [{ business_owner: null, product_owner: null }, "Data domain owner: not recorded; Data steward: not recorded."],
    [{ business_owner: "", product_owner: "" }, "Data domain owner: not recorded; Data steward: not recorded."],
  ]) {
    const product = { ...account, steward: "Certification Role" };
    delete product.business_owner;
    delete product.product_owner;
    Object.assign(product, owners);
    const original = structuredClone(product);
    const ask = openProviders({ PORTAL_ASK_PROVIDER: provider, ...modelEnv }, undefined, "file", { ...repository, listProducts: async () => [product] }, { reply: (input) => {
      assert.equal(provider, "portal", "none must not call the model");
      assert.ok(input.catalogue[0].text.includes(expected));
      assert.doesNotMatch(input.catalogue[0].text, /Certification Role/);
      return { statements: [{ product_id: product.id, text: expected }] };
    } }).ask;
    const answer = await ask.run(req(), { question: "Who owns client accounts?" });
    assert.equal(answer.answer_text, `${product.name}: ${expected}`);
    assert.doesNotMatch(answer.answer_text, /Steward:|Certification Role/);
    assert.equal(answer.phrased_by, provider === "portal" ? "portal" : "catalogue");
    assert.deepEqual(answer.figures, []);
    assert.deepEqual(answer.citations, provider === "portal" ? [{ n: 1, id: product.id, title: product.name, unit: product.unit_id, product: { id: product.id, name: product.name } }] : []);
    assert.deepEqual(product, original);
  }
});

test("Ask settings validate names, values and unchanged defaults", () => {
  assert.deepEqual(validateAskSettings({}), { provider: "portal", model: "", modelProvider: "vertex" });
  assert.equal(validateAskSettings({ KGE_URL: "http://unused" }).provider, "kge");
  for (const provider of ["kge", "portal", "none"]) assert.equal(validateAskSettings({ PORTAL_ASK_PROVIDER: provider }).provider, provider);
  for (const [name, value] of [["PORTAL_ASK_PROVIDER", "rules"], ["PORTAL_ASK_MODEL_PROVIDER", "other"], ["PORTAL_ASK_MODEL", "bad/model"], ["PORTAL_ASK_MODEL", " "]]) assert.throws(() => validateAskSettings({ [name]: value }), new RegExp(name));
  assert.throws(() => openProviders({ PORTAL_ASK_PROVIDER: "kge" }, undefined, "file", repository), /PORTAL_ASK_PROVIDER.*KGE_URL/);
});

test("BigQuery Ask uses four catalogue jobs and one direction read per model attempt across scopes and outcomes", async () => {
  for (const count of [1, 25]) {
    const fake = bigQueryFake();
    const repository = openBigQueryRepository({ PROFILE: "meridian" }, { client: fake.client });
    await repository.syncCatalogue("eval", Array.from({ length: count }, (_, i) => ({ ...account, id: `retail.account_${i}`, table: `account_${i}` })), true);
    for (const outcome of ["decline", "discarded", "failure", "none"]) for (const scope of [{}, { domain: "Other" }, { domain: "Retail" }, { products: [] }, { products: ["retail.account_0"] }]) {
      fake.state.statements.length = 0;
      const local = localKnowledge(repository, outcome === "none" ? undefined : mockAskModel(() => {
        if (outcome === "failure") throw new Error("offline");
        return { statements: outcome === "decline" ? [] : [{ product_id: "retail.account_0", text: "Total NNA is 987654." }] };
      }));
      const answer = await local.ask(req(), { question: "Who owns client accounts?", ...scope });
      assert.equal(answer.behaviour, scope.domain === "Other" ? "abstain_catalogue_only" : scope.products?.length === 0 ? "abstain" : "catalogue");
      assert.equal(answer.phrased_by, "catalogue");
      if (answer.behaviour !== "catalogue") assert.doesNotMatch(answer.answer_text, /Client Account Master/);
      assert.equal(fake.state.statements.filter((job) => job.query.includes("_portal_prompt_direction")).length, outcome === "none" ? 0 : 1);
      assert.equal(fake.state.statements.filter((job) => !job.query.includes("_portal_prompt_direction")).length, 4, `${count} products, model ${outcome}`);
      assert.equal(fake.state.statements.length, outcome === "none" ? 4 : 5);
    }
  }
});

test("BigQuery Ask controller reuses request products in four unscoped or six scoped jobs plus one direction read", async () => {
  for (const count of [1, 25]) {
    const fake = bigQueryFake();
    const repository = openBigQueryRepository({ PROFILE: "meridian" }, { client: fake.client });
    await repository.syncCatalogue("eval", Array.from({ length: count }, (_, i) => ({ ...account, id: `retail.account_${i}`, table: `account_${i}` })), true);
    for (const fail of [false, true]) {
      const env = { ...modelEnv, PORTAL_ASK_PROVIDER: "portal" };
      const providers = openProviders(env, undefined, "file", repository, { reply: () => {
        if (fail) throw new Error("model offline");
        return { statements: [{ product_id: "retail.account_0", field: "region_code", text: account.fields[1].description }] };
      } });
      const { app } = createApp({ repository, kge: {}, providers, env, catalogueSync: false, uiPath: "not-present" });
      const handle = app.router.stack.find((layer) => layer.route?.path === "/api/ask").route.stack.at(-1).handle;
      for (const scope of [{}, { products: ["retail.account_0"] }]) {
        const request = { ...req(), body: { question: "Which product holds region code?", ...scope } };
        const response = { set() { return this; }, json(value) { this.value = value; return this; } };
        fake.state.statements.length = 0;
        await handle(request, response, (error) => { throw error; });
        assert.equal(response.value.phrased_by, fail ? "catalogue" : "portal");
        assert.equal(fake.state.statements.filter((job) => job.query.includes("_portal_prompt_direction")).length, 1);
        assert.equal(fake.state.statements.filter((job) => !job.query.includes("_portal_prompt_direction")).length, scope.products ? 6 : 4, `${count} products, failure ${fail}, scope ${JSON.stringify(scope)}`);
        assert.equal(fake.state.statements.length, scope.products ? 7 : 5);
      }
    }
  }
});

const pause = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
const chain = { chainMs: 400, reserveMs: 40, minModelMs: 20 };
for (const [name, kgeMs, modelMs, layer, modelCalled] of [
  ["a fast model after a slow KGE answers from the portal layer", 100, 20, "portal", true],
  ["a slow model after a slow KGE is cut at the deadline for the rules layer", 100, 5000, "catalogue", true],
  ["a KGE that leaves too little time skips the model for the rules layer", 370, 20, "catalogue", false],
]) test(`Ask chain deadline: ${name}`, async (context) => {
  context.mock.timers.enable({ apis: ["setTimeout"] });
  let clock = 1000, modelCalls = 0, modelReady;
  const ready = new Promise((resolve) => { modelReady = resolve; });
  const kge = { request: async () => { clock += kgeMs; throw new TypeError("KGE stopped"); } };
  const reply = async (_input) => { modelCalls += 1; modelReady(); await pause(modelMs); return cases[0].reply; };
  const ask = openProviders({ PORTAL_ASK_PROVIDER: "kge", ...modelEnv }, kge, "kge", repository, { reply, chain: { ...chain, now: () => clock } }).ask;
  const started = clock;
  const request = { ...req(), receivedAt: started };
  const pending = ask.run(request, { question: cases[0].question });
  await Promise.race([ready, pending]);
  assert.equal(modelCalls, modelCalled ? 1 : 0);
  if (modelCalled) {
    const elapsed = Math.min(modelMs, request.askDeadline - clock - chain.reserveMs);
    clock += elapsed;
    context.mock.timers.tick(elapsed);
  }
  const result = await pending;
  const elapsed = clock - started;
  assert.ok(elapsed < chain.chainMs, `answered in ${elapsed} ms, within the ${chain.chainMs} ms chain`);
  assert.equal(request.askDeadline, started + chain.chainMs);
  assert.equal(modelCalls, modelCalled ? 1 : 0);
  assert.equal(result.provider, "portal");
  assert.equal(result.fallback, true);
  assert.equal(result.notice, fallbackNotice);
  assert.equal(result.phrased_by, layer);
  assert.deepEqual(result.figures, []);
});

test("Ask chain deadline: the model call receives a signal that aborts when its budget ends", async (context) => {
  context.mock.timers.enable({ apis: ["setTimeout"] });
  let clock = 1000, seen, modelReady;
  const ready = new Promise((resolve) => { modelReady = resolve; });
  const model = { complete: (_input, { signal }) => { seen = signal; modelReady(); return new Promise(() => {}); } };
  const local = localKnowledge(repository, model, { reserveMs: 10, minModelMs: 5, maxModelMs: 50, now: () => clock });
  const started = clock;
  const pending = local.ask({ ...req(), askDeadline: started + 10000 }, { question: cases[0].question });
  await Promise.race([ready, pending]);
  clock += 50;
  context.mock.timers.tick(50);
  const result = await pending;
  assert.ok(clock - started < 1000, "held to the model's own maximum, not the chain's remainder");
  assert.equal(result.phrased_by, "catalogue");
  assert.equal(seen.aborted, true);
});

test("Ask chain default abort margin leaves the model its minimum after KGE uses its whole share", async (context) => {
  assert.equal(askChain.abortMarginMs, 250);
  let clock = 11000;
  const budgets = [];
  context.mock.method(AbortSignal, "timeout", (ms) => {
    budgets.push(ms);
    const controller = new AbortController();
    setTimeout(() => { clock += ms + 250; controller.abort(new Error("KGE timed out")); }, 1);
    return controller.signal;
  });
  const client = kgeClient("https://kge.invalid");
  const kge = { request: (req, path, options) => client.request(req, path, { ...options, transport: async (_url, { signal }) => new Promise((_resolve, reject) => {
    signal.throwIfAborted();
    signal.addEventListener("abort", () => reject(signal.reason), { once: true });
  }) }) };
  let modelCalls = 0, modelStartedAt;
  const ask = openProviders({ ...modelEnv, PORTAL_ASK_PROVIDER: "kge" }, kge, "kge", repository, {
    chain: { now: () => clock },
    reply: () => { modelCalls += 1; modelStartedAt = clock; return cases[0].reply; },
  }).ask;
  const request = { ...req(), headers: {}, receivedAt: 1000 };
  const answer = await ask.run(request, { question: cases[0].question });
  assert.equal(request.askDeadline, 1000 + askChain.chainMs);
  assert.deepEqual(budgets, [281750], "the KGE share retains the default 250 ms abort margin");
  assert.equal(modelCalls, 1);
  assert.ok(request.askDeadline - modelStartedAt - askChain.reserveMs >= askChain.minModelMs);
  assert.equal(answer.phrased_by, "portal");
  assert.equal(answer.fallback, true);
});

for (const fail of [false, true]) test(`Ask chain deadline: spent arrival time and a stalled KGE leave fallback room after abort latency, model failure ${fail}`, async (context) => {
  let clock;
  const budgets = [];
  context.mock.method(AbortSignal, "timeout", (ms) => {
    budgets.push(ms);
    const controller = new AbortController();
    setTimeout(() => { clock += ms + 20; controller.abort(new Error("KGE timed out")); }, 1);
    return controller.signal;
  });
  for (const count of [1, 25]) {
    const fake = bigQueryFake();
    const repository = openBigQueryRepository({ PROFILE: "meridian" }, { client: fake.client });
    await repository.syncCatalogue("eval", Array.from({ length: count }, (_, i) => ({ ...account, id: `retail.account_${i}`, table: `account_${i}` })), true);
    fake.state.statements.length = 0;
    clock = 1300;
    const chain = { chainMs: 1000, reserveMs: 100, minModelMs: 100, abortMarginMs: 25, now: () => clock };
    const client = kgeClient("https://kge.invalid", { headers: async () => { clock += 50; return {}; } });
    let kgeCalls = 0, modelCalls = 0, modelStartedAt;
    const kge = { request: (req, path, options) => client.request(req, path, { ...options, transport: async (_url, { signal }) => {
      kgeCalls += 1;
      const stalled = () => new Promise((_resolve, reject) => {
        signal.throwIfAborted();
        signal.addEventListener("abort", () => reject(signal.reason), { once: true });
      });
      return fail ? { ok: true, json: stalled } : stalled();
    } }) };
    const reply = () => {
      modelCalls += 1;
      modelStartedAt = clock;
      clock += 20;
      if (fail) throw new Error("model offline");
      return { statements: [{ product_id: "retail.account_0", field: "region_code", text: account.fields[1].description }] };
    };
    const ask = openProviders({ ...modelEnv, PORTAL_ASK_PROVIDER: "kge" }, kge, "kge", repository, { reply, chain }).ask;
    const request = { ...req(), headers: {}, receivedAt: 1000 };
    const result = await ask.run(request, { question: "Which product holds region code?" });
    assert.equal(request.askDeadline, 2000);
    assert.equal(budgets.at(-1), 425, "arrival and service-auth time leave the reserve, minimum model time and abort margin");
    assert.equal(clock, 1815);
    assert.ok(request.askDeadline - modelStartedAt - chain.reserveMs >= chain.minModelMs, "the model still gets its minimum time after the KGE abort");
    assert.ok(clock < request.askDeadline, "fallback replies before the arrival-based deadline");
    assert.equal(kgeCalls, 1);
    assert.equal(modelCalls, 1);
    assert.equal(result.provider, "portal");
    assert.equal(result.phrased_by, fail ? "catalogue" : "portal");
    assert.equal(result.fallback, true);
    assert.equal(result.notice, fallbackNotice);
    assert.deepEqual(result.figures, []);
    assert.equal(fake.state.statements.filter((job) => job.query.includes("_portal_prompt_direction")).length, 1);
    assert.equal(fake.state.statements.filter((job) => !job.query.includes("_portal_prompt_direction")).length, 4);
    assert.equal(fake.state.statements.length, 5, `${count} products use the same fallback job budget`);
  }
});

test("KGE Ask keeps its configured timeout ceiling and skips an exhausted chain budget", async (context) => {
  const budgets = [];
  context.mock.method(AbortSignal, "timeout", (ms) => { budgets.push(ms); return new AbortController().signal; });
  const transport = async () => Response.json({});
  const request = { headers: {} };
  for (const [env, deadline, expected] of [[{}, 400000, 285000], [{ PORTAL_ASK_TIMEOUT: "290" }, 400000, 290000], [{}, 1025, 25]]) {
    const client = kgeClient("https://kge.invalid", undefined, "", env);
    await client.request(request, "/assist", { deadline, now: () => 1000, transport });
    assert.equal(budgets.at(-1), expected);
  }
  for (const deadline of [999, 1000]) await assert.rejects(kgeClient("https://kge.invalid").request(request, "/assist", {
    deadline, now: () => 1000, transport: () => assert.fail("an exhausted budget must not call KGE"),
  }), /ran out of time/);
  const ask = openProviders(modelEnv, kgeClient("https://kge.invalid"), "kge", repository, {
    chain: { chainMs: 1000, reserveMs: 100, minModelMs: 100, now: () => 2000 },
    reply: () => assert.fail("an exhausted chain must skip the model"),
  }).ask;
  const result = await ask.run({ ...req(), headers: {}, receivedAt: 1000 }, { question: "Who owns client accounts?" });
  assert.equal(result.provider, "portal");
  assert.equal(result.phrased_by, "catalogue");
  assert.equal(result.fallback, true);
  assert.equal(result.notice, fallbackNotice);
  assert.deepEqual(budgets, [285000, 290000, 25]);
});
