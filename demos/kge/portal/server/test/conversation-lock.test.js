import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { once } from "node:events";
import { mkdtemp, mkdir, readFile, rm, utimes, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { test } from "node:test";
import { localStore } from "../src/conversations/local.js";

async function stale(root) {
  await mkdir(join(root, "k"), { recursive: true });
  const lock = join(root, "k", "one.json.lock");
  await writeFile(lock, "dead writer");
  await utimes(lock, new Date(Date.now() - 60000), new Date(Date.now() - 60000));
}

test("stale takeover excludes another store until its conditional create finishes", async (t) => {
  const root = await mkdtemp(join(tmpdir(), "conversation-lock-"));
  t.after(() => rm(root, { recursive: true, force: true }));
  await stale(root);
  let enter, release;
  const entered = new Promise((resolve) => { enter = resolve; });
  const gate = new Promise((resolve) => { release = resolve; });
  const first = localStore(root, { onStale: async () => { enter(); await gate; } });
  const other = localStore(root, { lockWaitMs: 40 });
  const writing = first.write("k/one.json", "first", { ifGeneration: "0" });
  await entered;
  try {
    await assert.rejects(other.write("k/one.json", "second", { ifGeneration: "0" }), (error) => error.code === "busy");
    await assert.rejects(other.write("k/unrelated.json", "other", { ifGeneration: "0" }), (error) => error.code === "busy");
  } finally { release(); }
  await writing;
  await assert.rejects(other.write("k/one.json", "second", { ifGeneration: "0" }), (error) => error.code === "conflict");
  assert.equal((await first.read("k/one.json")).body.toString(), "first");
});

test("a killed process releases its guard so another process can recover a stale lock", async (t) => {
  const root = await mkdtemp(join(tmpdir(), "conversation-lock-crash-"));
  t.after(() => rm(root, { recursive: true, force: true }));
  await stale(root);
  const child = spawn(process.execPath, ["--experimental-sqlite", "--input-type=module", "-e", `
    const { localStore } = await import(process.argv[2]);
    const store = localStore(process.argv[1], { onStale: async () => {
      process.send("held");
      setInterval(() => {}, 1000);
      await new Promise(() => {});
    } });
    await store.write("k/one.json", "interrupted", { ifGeneration: "0" });
  `, root, new URL("../src/conversations/local.js", import.meta.url).href], { stdio: ["ignore", "ignore", "pipe", "ipc"] });
  t.after(() => child.kill("SIGKILL"));
  await once(child, "message");
  const store = localStore(root, { lockWaitMs: 40 });
  await assert.rejects(store.write("k/one.json", "before", { ifGeneration: "0" }), (error) => error.code === "busy");
  const exited = once(child, "exit");
  child.kill("SIGKILL");
  await exited;
  await store.write("k/one.json", "after", { ifGeneration: "0" });
  assert.equal((await store.read("k/one.json")).body.toString(), "after");
  await assert.rejects(readFile(join(root, "k", "one.json.lock")), (error) => error.code === "ENOENT");
});

test("the guard and its SQLite sidecars cannot be accessed as conversation keys", async (t) => {
  const root = await mkdtemp(join(tmpdir(), "conversation-lock-reserved-"));
  t.after(() => rm(root, { recursive: true, force: true }));
  const store = localStore(root);
  await store.write("one.json", "first", { ifGeneration: "0" });
  for (const key of ["", "-journal", "-wal", "-shm"].flatMap((suffix) => [`.conversation-locks.sqlite${suffix}`, `.CONVERSATION-LOCKS.SQLITE${suffix.toUpperCase()}`])) {
    await assert.rejects(store.remove(key), /plain segments/);
    assert.throws(() => store.write(key, "bad", { ifGeneration: "0" }), /plain segments/);
    await assert.rejects(store.read(key), /plain segments/);
  }
  await store.write("two.json", "second", { ifGeneration: "0" });
});
