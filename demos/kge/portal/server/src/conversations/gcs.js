import { randomUUID } from "node:crypto";
import { USER_AGENT, googleAuth } from "../outbound.js";
import { conflict } from "./conflict.js";

// #625: the conversation store in a Cloud Storage bucket (store.js has the port), through the JSON API with no client
// library: the runtime's service account's token from google-auth-library (outbound.js: no client-info, the portal's
// own User-Agent), or none at STORAGE_EMULATOR_HOST, as KGE's adapter (kge/adapters/objstore/gcs.py) does. Every write
// is conditional on the generation read (ifGenerationMatch, 0 to create); a 412 is the port's conflict. The fetch is
// injectable, so the adapter is tested offline with a fake client and never calls the cloud in a test.
//
// Retries are the adapter's, as the portal's BigQuery repository's are (repository/bigquery.js): a call answered 429
// or 5xx, or that reached no answer, is made again after a short jittered exponential back-off, up to ATTEMPTS calls in
// all, and is a 503 once they are spent. A 412, a 404 or any other 4xx is never retried. Every call carries the portal's
// own User-Agent (outbound.js), and an answer whose body is not read is drained, so its connection is freed.

const SCOPES = ["https://www.googleapis.com/auth/devstorage.read_write"];
const CLOUD = "https://storage.googleapis.com";

export const ATTEMPTS = 4;
export const REMOVE_ROUNDS = 3;
const transient = (status) => status === 429 || status >= 500;
const pause = (ms) => new Promise((resolve) => { setTimeout(resolve, ms); });

/** An answer's body, read and discarded, so its connection is freed. */
async function drained(response) {
  try {
    await response.body?.cancel?.();
  } catch {
    // Nothing more to free.
  }
  return response;
}

async function unavailable(response) {
  await drained(response);
  return Object.assign(new Error(`Cloud Storage returned ${response.status}`), { status: 503 });
}

export function gcsStore({ bucket, prefix = "", emulator, fetch: send = globalThis.fetch, auth, sleep = pause, random = Math.random } = {}) {
  const host = String(emulator ?? "").trim();
  const base = (host ? (host.includes("://") ? host : `http://${host}`) : CLOUD).replace(/\/+$/, "");
  const root = String(prefix).replace(/^\/+|\/+$/g, "");
  const nameOf = (key) => (root ? `${root}/${key}` : key);
  const keyOf = (name) => (root ? name.slice(root.length + 1) : name);
  const objectUrl = (key) => `${base}/storage/v1/b/${encodeURIComponent(bucket)}/o/${encodeURIComponent(nameOf(key))}`;
  let credentials;
  const authorization = async () => {
    if (host) return {};
    credentials ??= auth ?? googleAuth({ scopes: SCOPES });
    const token = await credentials.getAccessToken();
    const value = typeof token === "string" ? token : token?.token;
    if (!value) throw new Error("Cloud Storage: no access token");
    return { authorization: `Bearer ${value}` };
  };
  const backOff = (attempt) => sleep(Math.min(2000, 100 * (2 ** attempt)) + Math.floor(random() * 50));
  async function call(url, init = {}) {
    for (let attempt = 1; ; attempt += 1) {
      const headers = { ...(await authorization()), ...init.headers, "user-agent": USER_AGENT };
      let response;
      try {
        response = await send(url, { ...init, headers, redirect: "manual" });
      } catch (error) {
        if (attempt >= ATTEMPTS) throw Object.assign(new Error(`Cloud Storage could not be reached: ${error?.message || error}`), { status: 503 });
        await backOff(attempt - 1);
        continue;
      }
      if (!transient(response.status) || attempt >= ATTEMPTS) return response;
      await drained(response);
      await backOff(attempt - 1);
    }
  }

  async function read(key) {
    // The object's generation and metadata first, then its body at exactly that generation; an object changed in
    // between is read again.
    for (let attempt = 0; attempt < 3; attempt += 1) {
      const about = await call(objectUrl(key));
      if (about.status === 404) return drained(about).then(() => undefined);
      if (!about.ok) throw await unavailable(about);
      const object = await about.json();
      const generation = String(object.generation);
      const media = await call(`${objectUrl(key)}?alt=media&ifGenerationMatch=${encodeURIComponent(generation)}`);
      if (media.status === 404 || media.status === 412) {
        await drained(media);
        continue;
      }
      if (!media.ok) throw await unavailable(media);
      return { body: Buffer.from(await media.arrayBuffer()), generation, metadata: object.metadata ?? {} };
    }
    throw conflict(key);
  }

  return {
    read,
    async write(key, body, { ifGeneration, metadata = {} } = {}) {
      const boundary = `conversation-${randomUUID()}`;
      const about = JSON.stringify({ name: nameOf(key), contentType: "application/json", metadata });
      const payload = Buffer.concat([
        Buffer.from(`--${boundary}\r\ncontent-type: application/json; charset=UTF-8\r\n\r\n${about}\r\n--${boundary}\r\ncontent-type: application/json\r\n\r\n`),
        Buffer.from(body),
        Buffer.from(`\r\n--${boundary}--\r\n`),
      ]);
      const query = new URLSearchParams({ uploadType: "multipart", ...(ifGeneration !== undefined ? { ifGenerationMatch: String(ifGeneration) } : {}) });
      const response = await call(`${base}/upload/storage/v1/b/${encodeURIComponent(bucket)}/o?${query}`, {
        method: "POST", headers: { "content-type": `multipart/related; boundary=${boundary}` }, body: payload,
      });
      if (response.status === 412) {
        await drained(response);
        throw conflict(key);
      }
      if (!response.ok) throw await unavailable(response);
      return { generation: String((await response.json()).generation) };
    },
    async list(listPrefix = "", { pageToken, pageSize = 1000 } = {}) {
      const query = new URLSearchParams({ prefix: nameOf(listPrefix), maxResults: String(pageSize), fields: "nextPageToken,items(name,generation,metadata,updated)" });
      if (pageToken) query.set("pageToken", pageToken);
      const response = await call(`${base}/storage/v1/b/${encodeURIComponent(bucket)}/o?${query}`);
      if (!response.ok) throw await unavailable(response);
      const page = await response.json();
      const items = (Array.isArray(page.items) ? page.items : []).filter((item) => typeof item?.name === "string" && item.name.endsWith(".json"))
        .map((item) => ({ key: keyOf(item.name), generation: String(item.generation), metadata: item.metadata ?? {}, updated: item.updated }));
      return { items, next: page.nextPageToken || undefined };
    },
    async remove(key) {
      // The store bucket is versioned: a plain delete leaves the object's every generation behind as noncurrent
      // versions for 30 days. So every generation of this exact object is listed (versions=true) and deleted by its
      // generation, the live one among them. A generation already gone (404) is no error, nor is a bucket that answers
      // the list with 404: there is nothing left to remove. A save that lands between the list and the deletes makes a
      // generation the list did not name, so the versions are listed again until none is left, REMOVE_ROUNDS times at
      // most; one still there after that is a 503, never a removal reported that did not happen.
      const name = nameOf(key);
      for (let round = 0; round < REMOVE_ROUNDS; round += 1) {
        const generations = await versionsOf(name);
        if (!generations?.length) return;
        for (const generation of generations) {
          const response = await call(`${objectUrl(key)}?${new URLSearchParams({ generation })}`, { method: "DELETE" });
          if (response.status === 404 || response.ok) {
            await drained(response);
            continue;
          }
          throw await unavailable(response);
        }
      }
      if ((await versionsOf(name))?.length) throw Object.assign(new Error(`Cloud Storage: ${key} kept changing while it was removed`), { status: 503 });
    },
  };

  /** Every generation of the object name, live and noncurrent; undefined where the bucket answers 404. */
  async function versionsOf(name) {
    const generations = [];
    let pageToken;
    do {
      const query = new URLSearchParams({ prefix: name, versions: "true", fields: "nextPageToken,items(name,generation)" });
      if (pageToken) query.set("pageToken", pageToken);
      const response = await call(`${base}/storage/v1/b/${encodeURIComponent(bucket)}/o?${query}`);
      if (response.status === 404) {
        await drained(response);
        return undefined;
      }
      if (!response.ok) throw await unavailable(response);
      const page = await response.json();
      for (const item of Array.isArray(page.items) ? page.items : []) {
        if (item?.name === name && item.generation !== undefined) generations.push(String(item.generation));
      }
      pageToken = page.nextPageToken || undefined;
    } while (pageToken);
    return generations;
  }
}
