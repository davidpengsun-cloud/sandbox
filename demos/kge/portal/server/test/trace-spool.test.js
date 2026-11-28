import assert from "node:assert/strict";
import { promises as fs } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";
import { traceSpool } from "../src/trace-spool.js";
import { trace } from "@opentelemetry/api";
import { setupTracing, traceRequest } from "../src/tracing.js";

const RETAIN = 7 * 24 * 60 * 60 * 1000;
const sample = [{ id: "a1b2c3d4e5f6a7b8", trace_id: "a1b2c3d4e5f6a7b8a1b2c3d4e5f6a7b8", name: "POST /ask", attributes: { "kge.agent": "discover_ask" } }];
async function root(t) {
  const path = await fs.mkdtemp(join(tmpdir(), "telemetry-spool-"));
  t.after(() => fs.rm(path, { recursive: true, force: true }));
  return path;
}

test("standalone stores a private batch durably and replays it only to its bound client", async (t) => {
  const path = await root(t); let time = 1_760_000_000_000;
  const offline = traceSpool(path, "alpha", undefined, { now: () => time });
  await offline.start(); await offline.export(sample); await offline.shutdown();
  const dir = join(path, "client-alpha");
  const [name] = await fs.readdir(dir);
  assert.ok(name); assert.equal((await fs.stat(join(dir, name))).mode & 0o777, 0o600);
  assert.deepEqual(JSON.parse(await fs.readFile(join(dir, name), "utf8")), { spans: sample });
  const other = []; const wrong = traceSpool(path, "beta", async (spans) => other.push(spans), { now: () => time });
  await wrong.start(); await wrong.shutdown(); assert.deepEqual(other, []);
  const delivered = []; const online = traceSpool(path, "alpha", async (spans) => delivered.push(spans), { now: () => time });
  await online.start(); await online.shutdown();
  assert.deepEqual(delivered, [sample]); assert.deepEqual(await fs.readdir(dir), []);
});

test("expired batches are removed, unbound batches are not guessed into a client, and oversized batches are refused", async (t) => {
  const path = await root(t); let time = 1_760_000_000_000;
  const unbound = traceSpool(path, "", undefined, { now: () => time });
  await unbound.start(); await unbound.export(sample);
  await assert.rejects(unbound.export([{ value: "x".repeat(2 * 1024 * 1024) }]), /exceeds spool limit/);
  await unbound.shutdown();
  const delivered = []; const bound = traceSpool(path, "alpha", async (spans) => delivered.push(spans), { now: () => time });
  await bound.start(); await bound.shutdown(); assert.deepEqual(delivered, []);
  time += RETAIN;
  const expired = traceSpool(path, "", undefined, { now: () => time });
  await expired.start(); await expired.shutdown();
  assert.deepEqual(await fs.readdir(join(path, "_unbound")), []);
});

test("failed replay preserves the batch for a later restart", async (t) => {
  const path = await root(t);
  const offline = traceSpool(path, "alpha", undefined); await offline.start(); await offline.export(sample); await offline.shutdown();
  const failed = traceSpool(path, "alpha", async () => { throw new Error("offline"); });
  await failed.start(); await failed.shutdown(); assert.equal((await fs.readdir(join(path, "client-alpha"))).length, 1);
  const delivered = []; const online = traceSpool(path, "alpha", async (spans) => delivered.push(spans));
  await online.start(); await online.shutdown(); assert.deepEqual(delivered, [sample]);
});


test("standalone trace export persists only safe attributes and replays through the same batch contract", async (t) => {
  const path = await root(t);
  const spool = traceSpool(path, "alpha", undefined); await spool.start();
  const provider = setupTracing({ TRACING: "store" }, { isLocal: true, storeExport: spool.export, storeShutdown: spool.shutdown });
  const req = { headers: { "x-kge-conversation-id": "person@example.com", traceparent: "00-0af7651916cd43dd8448eb211c80319c-b7ad6b71a9203331-01" }, body: { question: "private question 123" } };
  const res = { statusCode: 200, end() {}, once() {} };
  traceRequest(provider, req, res, "POST", "/api/context/ask");
  trace.getSpan(req.traceContext).setAttribute("question", "private question 123");
  res.end(); await provider.forceFlush(); await provider.shutdown();
  const [name] = await fs.readdir(join(path, "client-alpha"));
  const saved = await fs.readFile(join(path, "client-alpha", name), "utf8");
  assert.doesNotMatch(saved, /private question|person@example|0af7651916cd43dd8448eb211c80319c/);
  const sent = []; const online = traceSpool(path, "alpha", async (spans) => sent.push(spans));
  await online.start(); await online.shutdown();
  assert.equal(sent.length, 1); assert.equal(sent[0][0].name, "POST /api/context/ask");
  assert.equal(sent[0][0].attributes["kge.agent"], "context_ask");
});

test("startup removes expired batches across prior client bindings and stale partial files", async (t) => {
  const path = await root(t); let time = 1_760_000_000_000;
  const old = traceSpool(path, "alpha", undefined, { now: () => time });
  await old.start(); await old.export(sample); await old.shutdown();
  const orphan = `${time + RETAIN}-aaaabbbb-aaaa-aaaa-aaaa-aaaabbbbaaaa.json.tmp`;
  await fs.writeFile(join(path, "client-alpha", orphan), "partial", { mode: 0o600 });
  time += RETAIN;
  const next = traceSpool(path, "beta", undefined, { now: () => time });
  await next.start(); await next.shutdown();
  assert.deepEqual(await fs.readdir(join(path, "client-alpha")), []);
});

test("a symlinked spool ancestor is refused rather than writing outside the configured path", async (t) => {
  const path = await root(t); const outside = await root(t);
  await fs.symlink(outside, join(path, "alias"));
  const spool = traceSpool(join(path, "alias", "nested"), "alpha", undefined);
  await assert.rejects(spool.start(), /spool directory is invalid/);
  await spool.shutdown();
  assert.deepEqual(await fs.readdir(outside), []);
});

test("replay ignores a symlinked batch and never opens its target", async (t) => {
  const path = await root(t); const outside = await root(t);
  const target = join(outside, "secret.json"); await fs.writeFile(target, "private", "utf8");
  const directory = join(path, "client-alpha"); await fs.mkdir(directory);
  const future = Date.now() + RETAIN;
  await fs.symlink(target, join(directory, `${future}-aaaabbbb-aaaa-aaaa-aaaa-aaaabbbbaaaa.json`));
  const sent = []; const spool = traceSpool(path, "alpha", async (spans) => sent.push(spans));
  await spool.start(); await spool.shutdown();
  assert.deepEqual(sent, []); assert.equal(await fs.readFile(target, "utf8"), "private");
});

test("a batch swapped for a symlink between listing and open is not replayed", async (t) => {
  const path = await root(t); const outside = await root(t);
  const target = join(outside, "secret.json"); await fs.writeFile(target, JSON.stringify({ spans: [{ value: "fixture_text" }] }));
  const offline = traceSpool(path, "alpha", undefined); await offline.start(); await offline.export(sample); await offline.shutdown();
  const dir = join(path, "client-alpha"); const [name] = await fs.readdir(dir); const item = join(dir, name);
  const original = fs.open; let swapped = false;
  fs.open = async (file, ...args) => {
    if (file === item && !swapped) {
      swapped = true;
      await fs.rename(item, `${item}.held`);
      await fs.symlink(target, item);
    }
    return original(file, ...args);
  };
  const sent = [];
  try {
    const online = traceSpool(path, "alpha", async (spans) => sent.push(spans), { log: () => {} });
    await online.start(); await online.shutdown();
  } finally { fs.open = original; }
  assert.equal(swapped, true); assert.deepEqual(sent, []);
  assert.match(await fs.readFile(target, "utf8"), /fixture_text/);
});
