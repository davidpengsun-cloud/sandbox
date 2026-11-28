import assert from "node:assert/strict";
import test from "node:test";
import { IncomingMessage, ServerResponse } from "node:http";
import { Socket } from "node:net";
import { Writable } from "node:stream";
import { createApp } from "../src/app.js";
import { openSqliteRepository } from "../src/repository/sqlite.js";
import { catalogue } from "./fixtures/catalogue.js";

async function request(app, path, body) {
  const req = new IncomingMessage(new Socket());
  req.complete = true;
  req.method = body === undefined ? "GET" : "POST";
  req.url = path;
  req.headers = { "content-type": "application/json", ...(body === undefined ? {} : { "content-length": String(Buffer.byteLength(JSON.stringify(body))) }) };
  const chunks = [];
  const res = new ServerResponse(req);
  res.assignSocket(new Writable({ write(chunk, _encoding, done) { chunks.push(Buffer.from(chunk)); done(); } }));
  const finished = new Promise((resolve, reject) => { res.once("finish", resolve); res.once("error", reject); });
  req.push(body === undefined ? null : Buffer.from(JSON.stringify(body)));
  if (body !== undefined) req.push(null);
  app.handle(req, res);
  await finished;
  const response = Buffer.concat(chunks).toString().split("\r\n\r\n").slice(1).join("\r\n\r\n");
  return { status: res.statusCode, json: () => JSON.parse(response) };
}

for (const [setting, dataProvider] of [[undefined, "kge"], ["off", "kge"], ["on", "kge"], ["on", "none"]]) {
  test(`table queries with PORTAL_TABLE_QUERY=${setting ?? "unset"} and data provider ${dataProvider} are enforced by the server and read at startup`, async (t) => {
    const calls = [];
    const repository = openSqliteRepository({ PORTAL_SQLITE_PATH: ":memory:" });
    const env = { PORTAL_DATA_PROVIDER: dataProvider, ...(setting === undefined ? {} : { PORTAL_TABLE_QUERY: setting }) };
    const kge = {
      identity: async () => ({ principal: "analyst_retail", units: ["retail"] }),
      request: async (_req, path, options) => {
        calls.push({ path, body: JSON.parse(options.body) });
        return Response.json({ columns: ["id"], rows: [{ id: 1 }], row_count: 1 });
      },
    };
    const { app } = createApp({ env, repository, kge, catalogueSync: false, uiPath: "not-present" });
    await repository.syncCatalogue(catalogue.release, catalogue.products);
    env.PORTAL_TABLE_QUERY = setting === "on" ? "off" : "on";
    t.after(() => repository.close());
    const config = (await request(app, "/getConfig")).json();
    assert.equal(config.executeQuery, setting === "on" && dataProvider === "kge");
    assert.equal(config.preview, false);
    assert.equal(config.export, false);
    for (const endpoint of ["/execute-query", "/api/query"]) {
      calls.length = 0;
      const response = await request(app, endpoint, { unit: "retail", sql: "SELECT id FROM accounts" });
      if (setting === "on" && dataProvider === "none") {
        assert.equal(response.status, 503);
        assert.deepEqual(calls, []);
      } else if (setting === "on") {
        assert.equal(response.status, 200);
        assert.deepEqual(response.json().rows, [{ id: 1 }]);
        assert.deepEqual(calls, [{ path: "/data/query", body: { unit: "retail", sql: "SELECT id FROM accounts" } }]);
        const refused = await request(app, endpoint, { unit: "wealth", sql: "SELECT id FROM holdings" });
        assert.equal(refused.status, 404);
        assert.equal(calls.length, 1);
      } else {
        assert.equal(response.status, 404);
        assert.deepEqual(response.json(), { message: "Table queries are not enabled." });
        assert.deepEqual(calls, []);
      }
    }
  });
}

for (const setting of [undefined, "off", "on"]) {
  test(`table previews with PORTAL_TABLE_PREVIEW=${setting ?? "unset"} gate every data alias at startup`, async (t) => {
    const calls = [];
    const repository = openSqliteRepository({ PORTAL_SQLITE_PATH: ":memory:" });
    t.after(() => repository.close());
    const env = { PORTAL_TABLE_QUERY: "off", ...(setting === undefined ? {} : { PORTAL_TABLE_PREVIEW: setting }) };
    const kge = {
      identity: async () => ({ principal: "analyst_retail", units: ["retail"] }),
      request: async (_req, path) => {
        calls.push(path);
        return Response.json({ columns: ["id"], rows: [{ id: 1 }], row_count: 1 });
      },
    };
    const { app } = createApp({ env, repository, kge, catalogueSync: false, uiPath: "not-present" });
    await repository.syncCatalogue(catalogue.release, catalogue.products);
    env.PORTAL_TABLE_PREVIEW = setting === "on" ? "off" : "on";
    const config = (await request(app, "/getConfig")).json();
    assert.equal(config.preview, setting === "on");
    assert.equal(config.export, setting === "on");
    for (const [endpoint, body, dataPath] of [
      ["/api/preview/retail-accounts?limit=1", undefined, "/data/preview"],
      ["/get-data-preview", { productId: "retail-accounts" }, "/data/preview"],
      ["/preview/retail-accounts", undefined, "/data/preview"],
      ["/api/export/retail-accounts", undefined, "/data/export"],
      ["/get-data-export/retail-accounts", undefined, "/data/export"],
      ["/api/context/data/preview", { unit: "retail", table: "accounts" }, "/data/preview"],
      ["/api/context/data/export", { unit: "retail", table: "accounts" }, "/data/export"],
      ["/api/context/data/query", { unit: "retail", sql: "SELECT id FROM accounts" }],
      ["/api/query", { unit: "retail", sql: "SELECT id FROM accounts" }],
      ["/execute-query", { unit: "retail", sqlText: "SELECT id FROM accounts" }],
    ]) {
      calls.length = 0;
      const response = await request(app, endpoint, body);
      const enabled = setting === "on" && Boolean(dataPath);
      assert.equal(response.status, enabled ? 200 : 404, endpoint);
      assert.deepEqual(calls, enabled ? [dataPath] : [], endpoint);
    }
  });
}

test("query opt-in uses catalogue columns while previews are refused, with no data lookup", async (t) => {
  const calls = [];
  const repository = openSqliteRepository({ PORTAL_SQLITE_PATH: ":memory:" });
  t.after(() => repository.close());
  const kge = {
    identity: async () => ({ principal: "analyst_retail", units: ["retail"] }),
    request: async (_req, path) => {
      calls.push(path);
      return Response.json({ columns: ["id"], rows: [{ id: 1 }] });
    },
  };
  const { app } = createApp({ env: { PORTAL_TABLE_QUERY: "on", PORTAL_TABLE_PREVIEW: "off" }, repository, kge, catalogueSync: false, uiPath: "not-present" });
  await repository.syncCatalogue(catalogue.release, catalogue.products);
  const fields = await request(app, "/api/products/retail-accounts/fields");
  assert.deepEqual(fields.json().map((field) => field.name), ["id", "amount"]);
  assert.equal((await request(app, "/api/preview/retail-accounts?limit=1")).status, 404);
  assert.deepEqual(calls, []);
  assert.equal((await request(app, "/execute-query", { unit: "retail", sql: "SELECT accounts.id FROM accounts LIMIT 20" })).status, 200);
  assert.deepEqual(calls, ["/data/query"]);
  assert.equal((await request(app, "/api/context/data/query", { unit: "retail", sql: "SELECT accounts.id FROM accounts LIMIT 20" })).status, 200);
  assert.deepEqual(calls, ["/data/query", "/data/query"]);
});
