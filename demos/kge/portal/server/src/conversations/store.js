import { createHash } from "node:crypto";
import { dirname, isAbsolute, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { gcsStore } from "./gcs.js";
import { localStore } from "./local.js";
import { memoryStore } from "./memory.js";

// #625: where Cerebro keeps each person's conversations, the conversation store port, as KGE's object store has one
// (kge/adapters/objstore/): a local folder for the demo and Docker Compose, a Cloud Storage bucket for a cloud
// environment, and the process's memory where neither is set. No database. Callers take a store from
// openConversationStore and never import an adapter module or compare a backend's name.
//
// The port, every method async:
//   read(key)                                  { body: Buffer, generation: string, metadata } or undefined
//   write(key, body, { ifGeneration, metadata }) { generation }: refused with an error whose code is "conflict" when the
//                                              object's generation is not ifGeneration ("0": the object must not exist)
//   list(prefix, { pageToken, pageSize })      { items: [{ key, generation, metadata, updated }], next }, in key order
//   remove(key)                                none; removing an object that is not there is no error. The object is
//                                              removed from the store, every stored version of it (a versioned bucket's
//                                              earlier generations too), not only hidden
// A key is a relative path of plain segments; metadata is a flat object of short strings (never a person's name).
//
// The setting, PORTAL_CONVERSATIONS_URL: a folder path, a file:// URL or gs://bucket/prefix (a bucket with no prefix
// is refused at start). Unset, the SQLite backend
// keeps them in a folder beside its database (<dir of PORTAL_SQLITE_PATH>/conversations; the process's memory for an
// in-memory database), and the BigQuery backend in the process's memory only (one instance, lost on restart), with one
// warning at start. STORAGE_EMULATOR_HOST points the bucket at an emulator, with no credentials, as KGE's adapter does.
export const CONVERSATIONS_FORMS = "a folder path, a file:// URL or gs://bucket/prefix";

/** The conversation store PORTAL_CONVERSATIONS_URL names, or the default for the portal's backend. options.fetch and
 *  options.auth reach the bucket's adapter (tests pass a fake fetch; no live cloud call). */
export function openConversationStore(env = process.env, options = {}) {
  const url = String(env.PORTAL_CONVERSATIONS_URL ?? "").trim();
  if (url.startsWith("gs://")) {
    const [bucket, ...rest] = url.slice(5).split("/");
    if (!bucket) throw new Error(`PORTAL_CONVERSATIONS_URL must be ${CONVERSATIONS_FORMS}, not ${url}`);
    const prefix = rest.filter(Boolean).join("/");
    // A bucket alone is refused: the conversations get a prefix of their own, so nothing else in the bucket is ever
    // listed, swept or overwritten as one.
    if (!prefix) throw new Error(`PORTAL_CONVERSATIONS_URL must name a prefix inside the bucket, gs://${bucket}/<prefix>, not the bucket alone: ${url}`);
    return gcsStore({ bucket, prefix, emulator: env.STORAGE_EMULATOR_HOST, ...options });
  }
  if (url.startsWith("file://")) return localStore(fileURLToPath(url));
  if (url) {
    if (/^[a-z][a-z0-9+.-]*:\/\//i.test(url)) throw new Error(`PORTAL_CONVERSATIONS_URL must be ${CONVERSATIONS_FORMS}, not ${url}`);
    return localStore(isAbsolute(url) ? url : resolve(url));
  }
  if ((env.PORTAL_BACKEND || "sqlite") === "sqlite") {
    const database = env.PORTAL_SQLITE_PATH || "build/portal/portal.db";
    return database === ":memory:" ? memoryStore() : localStore(resolve(join(dirname(database), "conversations")));
  }
  console.warn("PORTAL_CONVERSATIONS_URL is unset: Cerebro keeps conversations in this process's memory only, lost when it restarts.");
  return memoryStore();
}

// Object names: <prefix>/<sha256 of the principal, hex>/<conversation id>.json, never the principal itself. A
// conversation id is a UUID the page makes for a new chat.
export const CONVERSATION_ID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
export const ownerOf = (principal) => createHash("sha256").update(String(principal ?? "")).digest("hex");
export const conversationKey = (owner, id) => `${owner}/${String(id).toLowerCase()}.json`;
export { memoryStore };
export { conflict, isConflict } from "./conflict.js";
