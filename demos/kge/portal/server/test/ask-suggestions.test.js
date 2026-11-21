import assert from "node:assert/strict";
import test from "node:test";
import { createApp } from "../src/app.js";
import { appSettings } from "../src/apps.js";
import { openSqliteRepository } from "../src/repository/sqlite.js";
import { openBigQueryRepository } from "../src/repository/bigquery.js";
import { bigQueryFake } from "./fixtures/bigquery-fake.js";
import { checked } from "./googlesql.js";

const path = "/api/admin/ask-suggestions";
const blank = { asset: [], domain: [], changed_by: null, changed_at: null };
const identity = (principal = "operator", extra = {}) => ({ principal, units: ["retail"], who: {}, ...extra });

function open(adapter, context) {
  const fake = bigQueryFake();
  const repository = adapter === "sqlite" ? openSqliteRepository({ PORTAL_SQLITE_PATH: ":memory:" }) : openBigQueryRepository({ PROFILE: "meridian" }, { client: checked(fake.client) });
  context.after(() => repository.close());
  return { repository, fake };
}

async function request(app, method, portalIdentity = identity(), body, routePath = path) {
  const route = app.router.stack.find((layer) => layer.route?.path === routePath && layer.route.methods[method.toLowerCase()]).route;
  const req = { portalIdentity, body, headers: {} };
  const res = { statusCode: 200, status(code) { this.statusCode = code; return this; }, set() { return this; }, json(value) { this.body = value; return this; } };
  let index = 0;
  const next = (error) => {
    if (error) throw error;
    return route.stack[index++]?.handle(req, res, next);
  };
  await next();
  return res;
}

const appFor = (repository, env = {}) => createApp({ repository, kge: {}, env, catalogueSync: false, uiPath: "not-present" }).app;

for (const adapter of ["sqlite", "bigquery"]) {
  test(`${adapter}: suggestions start empty, record their actor and time, and an empty list is kept as empty`, async (context) => {
    const { repository, fake } = open(adapter, context);
    assert.deepEqual(await repository.askSuggestions(), blank);
    const before = Date.now();
    const saved = await repository.setAskSuggestions({ asset: ["Who owns it?", "How fresh is it?"], domain: [] }, "operator");
    assert.deepEqual(saved.asset, ["Who owns it?", "How fresh is it?"]);
    assert.deepEqual(saved.domain, []);
    assert.equal(saved.changed_by, "operator");
    assert.ok(Date.parse(saved.changed_at) >= before && Date.parse(saved.changed_at) <= Date.now());
    assert.deepEqual(await repository.askSuggestions(), saved);
    const cleared = await repository.setAskSuggestions({ asset: [], domain: ["Which products join?"] }, "other-operator");
    assert.deepEqual([cleared.asset, cleared.domain, cleared.changed_by], [[], ["Which products join?"], "other-operator"]);
    assert.deepEqual(await repository.askSuggestions(), cleared);
    await assert.rejects(async () => repository.setAskSuggestions({ asset: [], domain: [] }, ""), /actor is required/);
    await assert.rejects(async () => repository.setAskSuggestions({ asset: Array(7).fill("Q?"), domain: [] }, "operator"), /up to 6 questions/);
    if (adapter === "bigquery") assert.ok(fake.state.statements.some((job) => /CREATE TABLE IF NOT EXISTS `[^`]*\._portal_ask_suggestions` \(singleton INT64 NOT NULL, asset STRING NOT NULL, domain STRING NOT NULL, changed_by STRING NOT NULL, changed_at TIMESTAMP NOT NULL\)/.test(job.query)), "the pilot's BigQuery dataset gains the table at start");
  });

  test(`${adapter}: an admin saves both lists, readers get the lists alone, and the actor is the signed-in admin`, async (context) => {
    const { repository } = open(adapter, context);
    await repository.setRole("operator", "admin");
    const app = appFor(repository);
    assert.deepEqual((await request(app, "GET")).body, blank);
    assert.deepEqual((await request(app, "GET", identity("reader"), undefined, "/api/ask-suggestions")).body, { asset: [], domain: [] });
    const saved = await request(app, "PUT", identity(), { asset: [" What is in it? ", "", "Who owns it?"], domain: ["Which products are here?"], changed_by: "forged" });
    assert.equal(saved.statusCode, 200);
    assert.deepEqual(saved.body.asset, ["What is in it?", "Who owns it?"], "questions are trimmed and blanks dropped");
    assert.deepEqual(saved.body.domain, ["Which products are here?"]);
    assert.equal(saved.body.changed_by, "operator");
    assert.deepEqual((await request(app, "GET")).body, saved.body);
    assert.deepEqual((await request(app, "GET", identity("reader"), undefined, "/api/ask-suggestions")).body, { asset: ["What is in it?", "Who owns it?"], domain: ["Which products are here?"] });
  });
}

test("the admin editor enforces six questions of 200 characters, two lists, and saves nothing it refuses", async (context) => {
  const { repository } = open("sqlite", context);
  await repository.setRole("operator", "admin");
  const app = appFor(repository);
  const ok = await request(app, "PUT", identity(), { asset: Array.from({ length: 6 }, (_, i) => `${"x".repeat(199)}${i}`), domain: [] });
  assert.equal(ok.statusCode, 200);
  assert.equal(ok.body.asset.length, 6);
  for (const body of [
    { asset: Array(7).fill("Too many?"), domain: [] },
    { asset: [], domain: ["y".repeat(201)] },
    { asset: "Not a list", domain: [] },
    { asset: [], domain: [42] },
    { asset: [] },
    undefined,
  ]) {
    const refused = await request(app, "PUT", identity(), body);
    assert.equal(refused.statusCode, 400, JSON.stringify(body));
    assert.match(refused.body.message, /two lists, asset and domain, each of up to 6 questions of up to 200 characters/);
  }
  assert.deepEqual(repository.askSuggestions().asset, ok.body.asset);
});

for (const role of ["viewer", "publisher", "approver"]) for (const method of ["GET", "PUT"]) {
  test(`${role} receives 403 on suggested questions ${method}`, async (context) => {
    const { repository } = open("sqlite", context);
    await repository.setRole("person", role);
    const response = await request(appFor(repository), method, identity("person"), { asset: ["Must not save."], domain: [] });
    assert.equal(response.statusCode, 403);
    assert.deepEqual(response.body, { message: "Portal role required." });
    assert.deepEqual(repository.askSuggestions(), blank);
  });
}

test("a fallback admin identity receives 403 on suggested questions PUT", async (context) => {
  const { repository } = open("sqlite", context);
  await repository.setRole("operator", "admin");
  const response = await request(appFor(repository), "PUT", identity("operator", { fallback: true, who: { admin: true } }), { asset: ["Must not save."], domain: [] });
  assert.equal(response.statusCode, 403);
  assert.deepEqual(repository.askSuggestions(), blank);
});

test("PORTAL_ASK_GUIDE defaults to on, accepts only on or off, names an invalid value, and reaches /api/config", async (context) => {
  assert.equal(appSettings({}).askGuide, true);
  assert.equal(appSettings({ PORTAL_ASK_GUIDE: "on" }).askGuide, true);
  assert.equal(appSettings({ PORTAL_ASK_GUIDE: "off" }).askGuide, false);
  for (const value of ["", "OFF", "false", "yes"]) {
    assert.throws(() => appSettings({ PORTAL_ASK_GUIDE: value }), /^Error: PORTAL_ASK_GUIDE must be on or off$/);
    assert.throws(() => createApp({ env: { PORTAL_ASK_GUIDE: value, PORTAL_BACKEND: "sqlite", PORTAL_SQLITE_PATH: ":memory:" }, catalogueSync: false, uiPath: "not-present" }), /PORTAL_ASK_GUIDE must be on or off/);
  }
  for (const [setting, shown] of [[undefined, true], ["on", true], ["off", false]]) {
    const { repository } = open("sqlite", context);
    const app = appFor(repository, setting === undefined ? {} : { PORTAL_ASK_GUIDE: setting });
    const route = app.router.stack.find((layer) => layer.route?.path === "/api/config").route;
    let body;
    route.stack[0].handle({}, { json(value) { body = value; } });
    assert.equal(body.askGuide, shown, `PORTAL_ASK_GUIDE=${setting}`);
  }
});
