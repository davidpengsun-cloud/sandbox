import { randomUUID } from "node:crypto";
import { link, mkdir, open, readdir, readFile, rename, rm, stat, unlink } from "node:fs/promises";
import { dirname, join, relative, sep } from "node:path";
import { createRequire } from "node:module";
import { busy, conflict } from "./conflict.js";

// #625: the conversation store in a local folder (store.js has the port), for the demo and Docker Compose. Each object
// is one file at its key: a header line (its generation, metadata and time of writing, as JSON) and then its body, so a
// listing reads the header alone. Generations are emulated atomically: a write takes the object's lock (one write at a
// time in this process, and a lock file beside the object across processes), checks the generation it was given,
// writes a temporary file and renames it over the object. A lock not free within LOCK_WAIT_MS refuses the write as busy
// (never a conflict: nothing is known to have changed). A lock older than STALE_LOCK_MS, left by a process that died, is
// taken over atomically: it is renamed to a name of this writer's own first, so of several writers that found it stale
// only the one whose rename succeeded removes it; one that finds it renamed a fresh lock (taken over meanwhile) puts it
// back. Each lock file holds its writer's token, and a writer removes only a lock that still holds its own.

const LOCK_WAIT_MS = 5000;
const STALE_LOCK_MS = 30000;
const HEADER_CHUNK = 4096;
const GUARD = ".conversation-locks.sqlite";
const require = createRequire(import.meta.url);

function checkedKey(key) {
  const parts = String(key).split("/");
  if (!key || parts.some((part) => !part || part === "." || part === ".." || part.includes("\\") || part.includes("\0")) ||
      (parts.length === 1 && [GUARD, `${GUARD}-journal`, `${GUARD}-wal`, `${GUARD}-shm`].includes(parts[0].toLowerCase()))) {
    throw new Error("A conversation key is a relative path of plain segments.");
  }
  return parts;
}

async function readHeader(path) {
  const handle = await open(path, "r");
  try {
    let text = "";
    let position = 0;
    for (;;) {
      const chunk = Buffer.alloc(HEADER_CHUNK);
      const { bytesRead } = await handle.read(chunk, 0, HEADER_CHUNK, position);
      if (!bytesRead) break;
      const part = chunk.subarray(0, bytesRead).toString("utf8");
      const end = part.indexOf("\n");
      if (end >= 0) return JSON.parse(text + part.slice(0, end));
      text += part;
      position += bytesRead;
    }
    return JSON.parse(text);
  } finally {
    await handle.close();
  }
}

export function localStore(root, { lockWaitMs = LOCK_WAIT_MS, staleLockMs = STALE_LOCK_MS, onStale } = {}) {
  const pathOf = (key) => join(root, ...checkedKey(key));
  const held = new Map();
  // One write of a key at a time in this process; the lock file holds other processes off.
  const serial = (key, work) => {
    const before = held.get(key) ?? Promise.resolve();
    const run = before.then(work, work);
    const tail = run.catch(() => {});
    held.set(key, tail);
    tail.then(() => { if (held.get(key) === tail) held.delete(key); });
    return run;
  };
  const ageOf = (path) => stat(path).then((s) => Date.now() - s.mtimeMs, () => undefined);
  /** Takes over a stale lock: true where this writer removed it, so it may try to make its own. */
  async function takeOver(lock) {
    await onStale?.(lock);
    const mine = `${lock}.${randomUUID()}.stale`;
    try {
      await rename(lock, mine);
    } catch (error) {
      if (error.code === "ENOENT") return true;
      throw error;
    }
    const age = await ageOf(mine);
    if (age === undefined || age > staleLockMs) {
      await rm(mine, { force: true });
      return true;
    }
    // Another writer took the stale lock over and made a fresh one first: it is theirs, so it goes back.
    await link(mine, lock).catch((error) => { if (error.code !== "EEXIST") throw error; });
    await rm(mine, { force: true });
    return false;
  }
  async function locked(path, work) {
    // SQLite's OS lock serializes stale-file takeovers, including across processes, and is released on process death.
    // Keep the database at a stable path: unlinking it would let two processes lock different inodes at once. A previous
    // version does not take this guard, so old and new processes must not write this folder at the same time.
    await mkdir(root, { recursive: true });
    const { DatabaseSync } = require("node:sqlite");
    const database = new DatabaseSync(join(root, GUARD));
    const started = Date.now();
    try {
      for (;;) {
        try {
          database.exec("BEGIN IMMEDIATE");
          break;
        } catch (error) {
          if (error.errcode !== 5 && error.errcode !== 6) throw error;
          if (Date.now() - started > lockWaitMs) throw busy(relative(root, path));
          await new Promise((done) => { setTimeout(done, 20); });
        }
      }
      try {
        return await fileLocked(path, work, started);
      } finally {
        database.exec("ROLLBACK");
      }
    } finally {
      database.close();
    }
  }
  async function fileLocked(path, work, started) {
    const lock = `${path}.lock`;
    const token = randomUUID();
    for (;;) {
      try {
        const handle = await open(lock, "wx");
        try { await handle.writeFile(token); } finally { await handle.close(); }
        break;
      } catch (error) {
        if (error.code !== "EEXIST") throw error;
        const age = await ageOf(lock);
        if (age !== undefined && age > staleLockMs && await takeOver(lock)) continue;
        if (Date.now() - started > lockWaitMs) throw busy(relative(root, path));
        await new Promise((done) => { setTimeout(done, 20); });
      }
    }
    try {
      return await work();
    } finally {
      const holder = await readFile(lock, "utf8").catch(() => undefined);
      if (holder === token) await rm(lock, { force: true });
    }
  }
  async function read(key) {
    let raw;
    try {
      raw = await readFile(pathOf(key));
    } catch (error) {
      if (error.code === "ENOENT") return undefined;
      throw error;
    }
    const end = raw.indexOf(0x0a);
    const header = JSON.parse(raw.subarray(0, end < 0 ? raw.length : end).toString("utf8"));
    return { body: end < 0 ? Buffer.alloc(0) : raw.subarray(end + 1), generation: String(header.generation), metadata: header.metadata ?? {} };
  }
  return {
    read,
    write(key, body, { ifGeneration, metadata = {} } = {}) {
      const path = pathOf(key);
      return serial(key, async () => {
        await mkdir(dirname(path), { recursive: true });
        return locked(path, async () => {
          const current = await read(key);
          if (ifGeneration !== undefined && String(ifGeneration) !== (current ? current.generation : "0")) throw conflict(key);
          const generation = String((current ? Number(current.generation) : 0) + 1);
          const header = JSON.stringify({ generation, metadata, updated: new Date().toISOString() });
          const temporary = `${path}.${randomUUID()}.tmp`;
          const handle = await open(temporary, "w");
          try {
            await handle.writeFile(Buffer.concat([Buffer.from(`${header}\n`), Buffer.from(body)]));
            await handle.sync();
          } finally {
            await handle.close();
          }
          try {
            await rename(temporary, path);
          } catch (error) {
            await rm(temporary, { force: true });
            throw error;
          }
          return { generation };
        });
      });
    },
    async list(prefix = "", { pageToken, pageSize = 1000 } = {}) {
      // Only the folder the prefix's whole segments name is walked.
      const segments = String(prefix).split("/").slice(0, -1).filter(Boolean);
      const start = join(root, ...segments);
      const keys = [];
      const walk = async (folder) => {
        let entries;
        try { entries = await readdir(folder, { withFileTypes: true }); } catch (error) {
          if (error.code === "ENOENT" || error.code === "ENOTDIR") return;
          throw error;
        }
        for (const entry of entries) {
          const path = join(folder, entry.name);
          if (entry.isDirectory()) await walk(path);
          else if (entry.isFile() && entry.name.endsWith(".json")) {
            const key = relative(root, path).split(sep).join("/");
            if (key.startsWith(prefix) && (!pageToken || key > pageToken)) keys.push(key);
          }
        }
      };
      await walk(start);
      keys.sort();
      const page = keys.slice(0, pageSize);
      const items = [];
      for (const key of page) {
        try {
          const header = await readHeader(pathOf(key));
          // A file this store did not write (no header line it can read) is not an object of the store: it is passed over,
          // as a missing one is, so a stray file never stops a list or the sweep (#625 sign-off).
          if (!header || typeof header !== "object" || header.generation === undefined) continue;
          items.push({ key, generation: String(header.generation), metadata: header.metadata ?? {}, updated: header.updated });
        } catch (error) {
          if (error.code !== "ENOENT" && !(error instanceof SyntaxError)) throw error;
        }
      }
      return { items, next: keys.length > page.length ? page.at(-1) : undefined };
    },
    async remove(key) {
      const path = pathOf(key);
      // Under the object's lock, so a write in flight never brings a removed object back.
      const gone = (error) => { if (error.code !== "ENOENT") throw error; };
      await serial(key, () => locked(path, () => unlink(path).catch(gone)).catch(gone));
    },
  };
}
