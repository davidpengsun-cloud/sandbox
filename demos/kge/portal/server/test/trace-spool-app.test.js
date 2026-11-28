import assert from "node:assert/strict";
import { promises as fs } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";
import { createApp } from "../src/app.js";
import { openSqliteRepository } from "../src/repository/sqlite.js";

test("default SQLite path retains an offline trace and replays it after restart", async (t) => {
  const root = await fs.mkdtemp(join(tmpdir(), "trace-app-")); const previous = process.cwd();
  let first; let second; let server;
  process.chdir(root); t.after(async () => {
    if (server?.listening) await new Promise((resolve) => server.close(resolve));
    await first?.stopTracing(); await second?.stopTracing();
    process.chdir(previous); await fs.rm(root, { recursive: true, force: true });
  });
  const repository = openSqliteRepository({ PORTAL_SQLITE_PATH: ":memory:" });
  const sent = []; let available = false;
  const kge = { request: async (_req, path, options) => {
    if (path === "/telemetry/spans") {
      if (!available) return new Response("unavailable", { status: 503 });
      sent.push(JSON.parse(options.body)); return new Response("{}");
    }
    return new Response("{}", { status: 401 });
  } };
  const env = { TRACE_CLIENT_ID: "alpha", PORTAL_APP_DISCOVER: "off" };
  first = createApp({ env, kge, repository, catalogueSync: false, uiPath: "absent" });
  await first.spoolReady;
  server = first.app.listen(0, "127.0.0.1"); await new Promise((resolve) => server.once("listening", resolve));
  const response = await fetch(`http://127.0.0.1:${server.address().port}/ask`, { method: "POST", headers: { "content-type": "application/json" }, body: "{}" });
  assert.equal(response.status, 404); await response.text();
  const dir = join(root, "build/portal/trace-spool/client-alpha");
  assert.equal((await fs.readdir(dir)).filter((name) => name.endsWith(".json")).length, 1);
  await new Promise((resolve) => server.close(resolve));
  await first.stopTracing();
  first = undefined;
  available = true;
  second = createApp({ env, kge, repository, catalogueSync: false, uiPath: "absent" });
  await second.spoolReady;
  assert.equal(sent.length, 1); assert.equal(sent[0].spans[0].name, "POST /ask");
  assert.deepEqual((await fs.readdir(dir)).filter((name) => name.endsWith(".json")), []);
  await second.stopTracing();
  second = undefined;
  repository.close();
});
