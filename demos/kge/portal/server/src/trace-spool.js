import { constants, promises as fs } from "node:fs";
import { join, parse, resolve, sep } from "node:path";

const RETAIN_MS = 7 * 24 * 60 * 60 * 1000;
const MAX_BYTES = 32 * 1024 * 1024;
const MAX_BATCH = 2 * 1024 * 1024;
const NAME = /^(\d{13})-([0-9a-f-]{36})\.json$/;
const TEMP_NAME = /^(\d{13})-([0-9a-f-]{36})\.json\.tmp$/;
const STALE_TEMP_MS = 5 * 60 * 1000;

// Only safe, already-filtered span batches reach this durable local queue. A client-bound queue is replayed through
// the same authenticated ingestion endpoint after a restart; unbound data is retained but never guessed into a client.
export function traceSpool(root, client, send, { now = Date.now, log = (line) => console.error(line) } = {}) {
  const folder = join(root, client ? `client-${client}` : "_unbound");
  let tail = Promise.resolve();
  let expiryTimer;
  let retryTimer;
  let closed = false;
  let warned = false;
  const serial = (work) => {
    const done = tail.then(work);
    tail = done.catch(() => {});
    return done;
  };
  async function safeDirectory(path) {
    const absolute = resolve(path);
    const base = parse(absolute).root;
    let current = base;
    for (const part of absolute.slice(base.length).split(sep).filter(Boolean)) {
      current = join(current, part);
      try { await fs.mkdir(current, { mode: 0o700 }); } catch (error) { if (error.code !== "EEXIST") throw error; }
      const info = await fs.lstat(current);
      if (!info.isDirectory() || info.isSymbolicLink()) throw new Error("Telemetry spool directory is invalid");
    }
  }
  async function prepare() {
    await safeDirectory(folder);
    const handle = await fs.open(folder, constants.O_RDONLY | constants.O_DIRECTORY | constants.O_NOFOLLOW);
    try { await handle.chmod(0o700); } finally { await handle.close(); }
  }
  async function entries() {
    await prepare();
    const result = [];
    for (const entry of await fs.readdir(folder, { withFileTypes: true })) {
      const match = NAME.exec(entry.name);
      if (!match || !entry.isFile()) continue;
      const path = join(folder, entry.name);
      if (Number(match[1]) <= now()) { await fs.unlink(path).catch(() => {}); continue; }
      const info = await fs.lstat(path);
      if (info.isFile() && !info.isSymbolicLink()) result.push({ path, expiry: Number(match[1]), bytes: info.size });
    }
    return result.sort((a, b) => a.expiry - b.expiry || a.path.localeCompare(b.path));
  }
  async function expiries() {
    await safeDirectory(root);
    const pending = [];
    for (const dir of await fs.readdir(root, { withFileTypes: true })) {
      if (!dir.isDirectory() || !/^(?:_unbound|client-[a-z][a-z0-9-]{0,62})$/.test(dir.name)) continue;
      const location = join(root, dir.name);
      await safeDirectory(location);
      for (const entry of await fs.readdir(location, { withFileTypes: true })) {
        const temp = TEMP_NAME.exec(entry.name);
        const match = NAME.exec(entry.name) || temp;
        if (!match || !entry.isFile()) continue;
        const path = join(location, entry.name);
        const expiry = temp ? Number(match[1]) - RETAIN_MS + STALE_TEMP_MS : Number(match[1]);
        if (expiry <= now()) await fs.unlink(path).catch(() => {});
        else pending.push({ expiry });
      }
    }
    return pending.sort((a, b) => a.expiry - b.expiry);
  }
  function schedule(items) {
    clearTimeout(expiryTimer);
    if (closed || !items.length) return;
    expiryTimer = setTimeout(() => { serial(async () => schedule(await expiries())).catch(() => log("Telemetry spool cleanup failed.")); }, Math.min(2 ** 31 - 1, Math.max(1, items[0].expiry - now())));
    expiryTimer.unref?.();
  }
  async function replay() {
    const items = await entries();
    if (send && client) {
      for (const item of items) {
        try {
          if (item.bytes > MAX_BATCH) throw new Error("Telemetry spool batch exceeds limit");
          const handle = await fs.open(item.path, constants.O_RDONLY | constants.O_NOFOLLOW);
          let info; let text;
          try {
            info = await handle.stat();
            if (!info.isFile() || info.size > MAX_BATCH) throw new Error("Telemetry spool batch is invalid");
            text = await handle.readFile("utf8");
          } finally { await handle.close(); }
          const batch = JSON.parse(text);
          await send(batch.spans);
          const current = await fs.lstat(item.path);
          if (current.isFile() && !current.isSymbolicLink() && current.dev === info.dev && current.ino === info.ino) await fs.unlink(item.path);
          warned = false;
        } catch {
          if (!warned) log("Telemetry replay unavailable; safe batches remain queued.");
          warned = true;
          break;
        }
      }
    }
    schedule(await expiries());
  }
  async function write(spans) {
    const body = Buffer.from(JSON.stringify({ spans }));
    if (body.length > MAX_BATCH) throw new Error("Telemetry batch exceeds spool limit");
    const items = await entries();
    if (items.reduce((n, item) => n + item.bytes, 0) + body.length > MAX_BYTES) throw new Error("Telemetry spool is full");
    const key = `${now() + RETAIN_MS}-${globalThis.crypto.randomUUID()}.json`;
    const path = join(folder, key);
    const temporary = `${path}.tmp`;
    let handle;
    try {
      handle = await fs.open(temporary, "wx", 0o600);
      await handle.writeFile(body);
      await handle.sync();
      await handle.close(); handle = undefined;
      await fs.rename(temporary, path);
    } finally {
      await handle?.close().catch(() => {});
      await fs.unlink(temporary).catch(() => {});
    }
    schedule(await expiries());
  }
  return {
    async start() {
      await serial(replay);
      if (send && client && !closed) {
        retryTimer = setInterval(() => { serial(replay).catch(() => log("Telemetry spool replay failed.")); }, 60_000);
        retryTimer.unref?.();
      }
    },
    export: (spans) => serial(async () => {
      if (send) {
        try { await send(spans); await replay(); return; } catch {
          if (!warned) log("Telemetry delivery unavailable; safe batches are queued locally.");
          warned = true;
        }
      }
      await write(spans);
    }),
    async shutdown() { closed = true; clearTimeout(expiryTimer); clearInterval(retryTimer); await tail; },
  };
}
