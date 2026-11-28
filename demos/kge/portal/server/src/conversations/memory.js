// #625: the conversation store in the process's memory (store.js has the port): for tests, and where no folder or
// bucket is set. One instance's alone, lost when it restarts. Generations count up per store, as a bucket's do.

import { conflict } from "./conflict.js";

export function memoryStore() {
  const objects = new Map();
  let generations = 0;
  return {
    async read(key) {
      const held = objects.get(key);
      return held && { body: Buffer.from(held.body), generation: held.generation, metadata: { ...held.metadata } };
    },
    async write(key, body, { ifGeneration, metadata = {} } = {}) {
      const held = objects.get(key);
      if (ifGeneration !== undefined && String(ifGeneration) !== (held ? held.generation : "0")) throw conflict(key);
      generations += 1;
      const generation = String(generations);
      objects.set(key, { body: Buffer.from(body), generation, metadata: { ...metadata }, updated: new Date().toISOString() });
      return { generation };
    },
    async list(prefix = "", { pageToken, pageSize = 1000 } = {}) {
      const keys = [...objects.keys()].filter((key) => key.startsWith(prefix) && (!pageToken || key > pageToken)).sort();
      const page = keys.slice(0, pageSize);
      const items = page.map((key) => ({ key, generation: objects.get(key).generation, metadata: { ...objects.get(key).metadata }, updated: objects.get(key).updated }));
      return { items, next: keys.length > page.length ? page.at(-1) : undefined };
    },
    async remove(key) {
      objects.delete(key);
    },
  };
}
