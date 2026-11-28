import { ConversationSaver } from "./saver.js";
import { CONVERSATION_ID, conversationKey, memoryStore, openConversationStore, ownerOf } from "./store.js";

// #625: the portal owns Cerebro's conversation state; KGE stays stateless. Each conversation belongs to its signed-in
// person, under their own prefix of the store (the sha256 of their principal, never the principal), so only they can
// list, open or delete it, and another person's id is simply not there. Retention follows KGE's past-questions
// retention (retention_days, the value Context's Ask page reads): a conversation not updated within that many days is
// deleted when its owner's list is read, and by a sweep of everyone's; 0 keeps nothing past a day.

export { CONVERSATION_ID, conversationKey, memoryStore, openConversationStore, ownerOf };
export { ConversationSaver };
export { isConflict } from "./conflict.js";

const DAY_MS = 24 * 60 * 60 * 1000;
const STORE_PAGE = 1000;
export const LIST_PAGE = 20;
export const LIST_PAGE_MAX = 100;

// A conversation's object name, <sha256 hex of its owner>/<conversation id>.json, in the lower case conversationKey
// writes. Only objects so named, carrying a conversation's metadata, are ever listed or deleted as conversations: a
// store's prefix may hold anything else (a stray file, another tool's object), and it is never touched.
const UUID = CONVERSATION_ID.source.replace(/^\^|\$$/g, "");
export const CONVERSATION_KEY = new RegExp(`^[0-9a-f]{64}/${UUID}\\.json$`);
const timeOf = (value) => (typeof value === "string" ? Date.parse(value) : Number.NaN);
/** Whether an item of the store is one of the portal's conversations: its name, and its metadata's times. */
export const isConversation = (item) => typeof item?.key === "string" && CONVERSATION_KEY.test(item.key)
  && Number.isFinite(timeOf(item.metadata?.updated)) && Number.isFinite(timeOf(item.metadata?.created));

/** Whether an item of the store is past retention: not updated within days (0: a day), by its metadata's time alone
 *  (never the object's own time: an object with no conversation metadata is no conversation). */
export function expired(item, days, now = Date.now()) {
  if (!Number.isInteger(days) || days < 0) return false;
  const at = timeOf(item?.metadata?.updated);
  return Number.isFinite(at) && now - at > Math.max(days, 1) * DAY_MS;
}

// A page of the list is named by the last conversation it showed (its time and id), so a page never repeats or skips
// one as new conversations are added before it.
const cursorOf = (item) => Buffer.from(JSON.stringify([item.updated, item.id])).toString("base64url");
function afterCursor(token) {
  try {
    const [updated, id] = JSON.parse(Buffer.from(String(token), "base64url").toString("utf8"));
    return typeof updated === "string" && typeof id === "string" ? { updated, id } : undefined;
  } catch {
    return undefined;
  }
}
const newestFirst = (a, b) => (a.updated < b.updated ? 1 : a.updated > b.updated ? -1 : a.id < b.id ? 1 : a.id > b.id ? -1 : 0);

export function openConversations({ store = memoryStore(), now = () => Date.now() } = {}) {
  async function* everything(prefix) {
    let pageToken;
    do {
      const page = await store.list(prefix, { pageToken, pageSize: STORE_PAGE });
      yield* page.items;
      pageToken = page.next;
    } while (pageToken);
  }
  return {
    /** The checkpointer for one turn of one person (onRefused: a save refused on a conflict). */
    saver(principal, options = {}) {
      return new ConversationSaver(store, ownerOf(principal), { now: () => new Date(now()), ...options });
    },

    /** A page of the person's own conversations, newest first ({ id, title, created, updated }), and the next page's
     *  token; those past retention are deleted first. */
    async list(principal, { pageToken, pageSize = LIST_PAGE, retentionDays } = {}) {
      const owner = ownerOf(principal);
      const kept = [];
      for await (const item of everything(`${owner}/`)) {
        if (!isConversation(item) || !item.key.startsWith(`${owner}/`)) continue;
        if (expired(item, retentionDays, now())) {
          await store.remove(item.key);
          continue;
        }
        const id = item.key.slice(owner.length + 1, -".json".length);
        kept.push({ id, title: typeof item.metadata.title === "string" ? item.metadata.title : "", created: item.metadata.created, updated: item.metadata.updated });
      }
      kept.sort(newestFirst);
      const after = pageToken ? afterCursor(pageToken) : undefined;
      const rest = after ? kept.filter((c) => newestFirst(c, after) > 0) : kept;
      const size = Math.min(Math.max(Number.parseInt(pageSize, 10) || LIST_PAGE, 1), LIST_PAGE_MAX);
      const page = rest.slice(0, size);
      return { conversations: page, next: rest.length > page.length ? cursorOf(page.at(-1)) : undefined };
    },

    /** Deletes the person's own conversation; false where they have none by that id. */
    async remove(principal, id) {
      if (!CONVERSATION_ID.test(String(id))) return false;
      const key = conversationKey(ownerOf(principal), id);
      if (!(await store.read(key))) return false;
      await store.remove(key);
      return true;
    },

    /** Deletes everyone's conversations past retention, and nothing else the store holds; the number deleted. */
    async sweep(retentionDays) {
      let removed = 0;
      for await (const item of everything("")) {
        if (!isConversation(item) || !expired(item, retentionDays, now())) continue;
        await store.remove(item.key);
        removed += 1;
      }
      return removed;
    },
  };
}
