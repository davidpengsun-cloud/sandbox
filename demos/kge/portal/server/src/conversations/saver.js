import { createHash } from "node:crypto";
import { BaseCheckpointSaver, copyCheckpoint } from "@langchain/langgraph";
import { conversationKey } from "./store.js";
import { isConflict } from "./conflict.js";
import { CHECKPOINT_MAX_BYTES } from "./limits.js";

// #625: LangGraph's checkpointer on the conversation store. One object per conversation, keyed by its id (LangGraph's
// thread_id) under the person's own prefix (store.js conversationKey), holds its latest checkpoint, that checkpoint's
// metadata and pending writes (an interrupt's among them, so a HITL pause resumes from it on any instance with
// LangGraph's own resume, nothing re-run), and { title, created, updated, trimmed }. Older checkpoints are not kept.
//
// Saves never hold up a reply: put and putWrites change the object in memory and return; the store write runs behind
// them, one at a time, each carrying the newest state, and flush() waits for the last. Each write is conditional on
// the generation read. On a conflict the object is read again: where it still holds the checkpoint this turn started
// from (or the one it last wrote), only its generation moved, so the write is made again on the new generation, once;
// anything else (another tab ran a turn, the conversation was deleted, a second conflict) refuses the save, which the
// turn reports (conversation_changed) and never loses quietly. A write refused for any other reason (the store down,
// its lock busy) refuses the save too, which the turn reports as not saved (not_saved): onRefused hears every refusal.

// #651: beside the checkpoint, the object keeps what the page shows about each reply and LangGraph never reads (aside:
// { replies, paused }, written by agent.js). It is never part of the graph's state, so writing it changes no checkpoint:
// nothing is scheduled next, an interrupt stays as it was, and the next turn runs as before. Each put carries it forward.
// A verdict is set by its own conditional write (setVerdict), which changes the aside alone. A turn's write that meets
// one on a conflict merges the verdicts onto what it writes (mergeAside: a verdict changed since this saver last read or
// wrote the object is taken from the object as found), so a Like given while another question runs is not written over;
// where nothing but the aside moved, that is made again up to ASIDE_TRIES times, as many Likes as land meanwhile.
// replies is keyed by the person's message ids, which the page sends: it is held in an object with no prototype, read
// with Object.hasOwn, so an id such as __proto__ is a key like any other and never reaches Object.prototype.

// LangGraph's special writes (an error, a scheduled task, an interrupt, a resume), indexed apart from a task's own.
const SPECIAL = { __error__: -1, __scheduled__: -2, __interrupt__: -3, __resume__: -4 };

const VERDICT_TRIES = 3;
const ASIDE_TRIES = 5;
/** #651: a replies table with no prototype, its own records alone (as read back from JSON too). */
export function repliesTable(replies) {
  const table = Object.create(null);
  if (replies && typeof replies === "object" && !Array.isArray(replies)) {
    for (const [id, record] of Object.entries(replies)) Object.defineProperty(table, id, { value: record, enumerable: true, writable: true, configurable: true });
  }
  return table;
}
/** #651: a reply's own record in a replies table, or undefined. */
export const ownRecord = (replies, id) => (replies && typeof replies === "object" && Object.hasOwn(replies, id) && replies[id] && typeof replies[id] === "object" ? replies[id] : undefined);
/** #651: an aside, a deep copy, its replies held with no prototype. */
const asideOf = (aside) => {
  const copy = aside && typeof aside === "object" ? structuredClone(aside) : {};
  if (copy.replies !== undefined) copy.replies = repliesTable(copy.replies);
  return copy;
};
/**
 * #651: onto the aside a write carries (ours), each verdict that changed in the stored object (theirs) since base (the
 * aside as this saver last read or wrote it): set or cleared on ours' record, and a record made since base (a verdict on
 * a reply saved before #651) added where ours has none. Every other record, and paused, stays as ours has it.
 */
function mergeAside(doc, theirs, base) {
  const stored = theirs?.replies;
  if (!doc || !stored || typeof stored !== "object") return;
  const ours = repliesTable(doc.aside?.replies);
  for (const id of Object.keys(stored)) {
    const found = ownRecord(stored, id);
    if (!found) continue;
    const was = ownRecord(base?.replies, id);
    const verdict = found.verdict ?? null;
    if (was && (was.verdict ?? null) === verdict) continue;
    const mine = ownRecord(ours, id);
    if (mine) {
      if (verdict) mine.verdict = verdict;
      else delete mine.verdict;
    } else if (!was) Object.defineProperty(ours, id, { value: structuredClone(found), enumerable: true, writable: true, configurable: true });
  }
  doc.aside = { ...doc.aside, replies: ours };
}
/** #651: what a stored object holds besides its aside, to tell a write that changed the aside alone (a verdict). */
const coreOf = (doc) => createHash("sha256").update(JSON.stringify({ ...doc, aside: undefined })).digest("base64");

export class ConversationSaver extends BaseCheckpointSaver {
  constructor(store, owner, { now = () => new Date(), onRefused } = {}) {
    super();
    this.store = store;
    this.owner = owner;
    this.now = now;
    this.onRefused = onRefused;
    this.threads = new Map();
  }

  /** The conversation as this saver holds it: read from the store once, then changed in memory. */
  held(thread) {
    if (typeof thread !== "string" || !thread) throw new Error("A conversation needs its id as thread_id.");
    if (!this.threads.has(thread)) {
      const key = conversationKey(this.owner, thread);
      const entry = { key, writes: new Map() };
      entry.loaded = this.store.read(key).then((found) => {
        entry.generation = found?.generation;
        entry.doc = found ? JSON.parse(found.body.toString("utf8")) : undefined;
        entry.base = entry.doc?.checkpointId;
        // #651: the aside as stored, and the rest of the object, for a later conflict's merge (mergeAside, coreOf).
        entry.asideBase = asideOf(entry.doc?.aside);
        entry.core = entry.doc ? coreOf(entry.doc) : undefined;
        if (entry.doc?.checkpointId) entry.writes.set(entry.doc.checkpointId, entry.doc.writes ?? []);
        return entry;
      });
      this.threads.set(thread, entry);
    }
    return this.threads.get(thread).loaded;
  }

  /** { title, created, updated, trimmed } of a saved conversation, or undefined where there is none. */
  async info(thread) {
    const { doc } = await this.held(thread);
    return doc && { title: doc.title ?? "", created: doc.created, updated: doc.updated, trimmed: doc.trimmed ?? 0 };
  }

  async dump(value) {
    const [type, data] = await this.serde.dumpsTyped(value);
    const text = typeof data === "string" ? data : Buffer.from(data).toString(type === "json" ? "utf8" : "base64");
    return type === "json" ? { type, value: JSON.parse(text) } : { type, base64: text };
  }

  load(saved) {
    return this.serde.loadsTyped(saved.type, saved.type === "json" ? JSON.stringify(saved.value) : Buffer.from(saved.base64, "base64"));
  }

  async getTuple(config) {
    const thread = config.configurable?.thread_id;
    const { doc, writes } = await this.held(thread);
    const wanted = config.configurable?.checkpoint_id;
    if (!doc?.checkpoint || (wanted && wanted !== doc.checkpointId)) return undefined;
    const ns = config.configurable?.checkpoint_ns ?? "";
    const pending = [...(writes.get(doc.checkpointId) ?? [])].sort((a, b) => (a.path < b.path ? -1 : a.path > b.path ? 1 : 0)
      || (a.task < b.task ? -1 : a.task > b.task ? 1 : 0) || a.index - b.index);
    return {
      config: { configurable: { thread_id: thread, checkpoint_ns: ns, checkpoint_id: doc.checkpointId } },
      checkpoint: await this.load(doc.checkpoint),
      metadata: await this.load(doc.metadata),
      pendingWrites: await Promise.all(pending.map(async (w) => [w.task, w.channel, await this.load(w.value)])),
      ...(doc.parentId ? { parentConfig: { configurable: { thread_id: thread, checkpoint_ns: ns, checkpoint_id: doc.parentId } } } : {}),
    };
  }

  async* list(config, options = {}) {
    if (options.limit !== undefined && options.limit <= 0) return;
    const tuple = config.configurable?.thread_id ? await this.getTuple({ configurable: { thread_id: config.configurable.thread_id } }) : undefined;
    const before = options.before?.configurable?.checkpoint_id;
    if (tuple && !(before && tuple.checkpoint.id >= before)) yield tuple;
  }

  async put(config, checkpoint, metadata) {
    const thread = config.configurable?.thread_id;
    const entry = await this.held(thread);
    const [saved, about] = await Promise.all([this.dump(copyCheckpoint(checkpoint)), this.dump(metadata)]);
    const values = checkpoint.channel_values ?? {};
    const at = this.now().toISOString();
    entry.doc = {
      v: 1,
      title: typeof values.title === "string" ? values.title : entry.doc?.title ?? "",
      created: entry.doc?.created ?? at,
      updated: at,
      trimmed: Number.isInteger(values.trimmed) ? values.trimmed : entry.doc?.trimmed ?? 0,
      checkpointId: checkpoint.id,
      parentId: config.configurable?.checkpoint_id,
      checkpoint: saved,
      metadata: about,
      ...(entry.doc?.aside ? { aside: entry.doc.aside } : {}),
    };
    // Writes LangGraph sent for this checkpoint before its put arrived are kept; any older checkpoint's go.
    for (const id of entry.writes.keys()) if (id !== checkpoint.id) entry.writes.delete(id);
    this.save(entry);
    return { configurable: { thread_id: thread, checkpoint_ns: config.configurable?.checkpoint_ns ?? "", checkpoint_id: checkpoint.id } };
  }

  async putWrites(config, writes, taskId, taskPath = "") {
    const entry = await this.held(config.configurable?.thread_id);
    const id = config.configurable?.checkpoint_id;
    const values = await Promise.all(writes.map(([, value]) => this.dump(value)));
    const list = entry.writes.get(id) ?? [];
    writes.forEach(([channel], at) => {
      const index = SPECIAL[channel] ?? at;
      const same = list.findIndex((w) => w.task === taskId && w.index === index);
      if (same >= 0 && index >= 0) return;
      const write = { task: taskId, index, channel, path: taskPath ?? "", value: values[at] };
      if (same >= 0) list[same] = write;
      else list.push(write);
    });
    entry.writes.set(id, list);
    if (entry.doc && entry.doc.checkpointId === id) {
      entry.doc.updated = this.now().toISOString();
      this.save(entry);
    }
  }

  /** #651: what the conversation keeps beside its checkpoint ({ replies, paused }), a copy; {} where there is none. */
  async aside(thread) {
    const { doc } = await this.held(thread);
    return asideOf(doc?.aside);
  }

  /** #651: sets it, saved behind as a put is (flush waits for it); nothing where the conversation has no checkpoint. */
  async putAside(thread, aside) {
    return this.updateAside(thread, () => aside);
  }

  /**
   * #651: sets it to change(the aside as this saver holds it now, a copy), so what a turn writes starts from the verdicts
   * an earlier write of the turn merged (mergeAside), never from a copy taken at its start.
   */
  async updateAside(thread, change) {
    const entry = await this.held(thread);
    if (!entry.doc) return;
    entry.doc.aside = asideOf(change(asideOf(entry.doc.aside)));
    this.save(entry);
  }

  /**
   * #651: a reply's verdict ("like", "dislike", or null to clear it), written straight to the stored object, conditional
   * on the generation read and made again on a conflict up to VERDICT_TRIES times. The record as saved, or undefined
   * where the conversation or the reply's record is not there. Where create is set (agent.js: the message is a question
   * still in the conversation) and the reply has no record (one saved before #651), a record { verdict } is made. Nothing
   * held in memory is used or changed.
   */
  async setVerdict(thread, id, verdict, { create = false } = {}) {
    const key = conversationKey(this.owner, thread);
    for (let tries = 1; ; tries += 1) {
      const found = await this.store.read(key);
      if (!found) return undefined;
      const doc = JSON.parse(found.body.toString("utf8"));
      const replies = repliesTable(doc.aside?.replies);
      let record = ownRecord(replies, id);
      if (!record) {
        if (!create) return undefined;
        if (!verdict) return {};
        record = {};
        Object.defineProperty(replies, id, { value: record, enumerable: true, writable: true, configurable: true });
      }
      if (verdict) record.verdict = verdict;
      else delete record.verdict;
      doc.aside = { ...(doc.aside && typeof doc.aside === "object" ? doc.aside : {}), replies };
      try {
        await this.store.write(key, Buffer.from(JSON.stringify(doc)), { ifGeneration: found.generation, metadata: { title: doc.title ?? "", created: doc.created, updated: doc.updated } });
        return record;
      } catch (error) {
        if (!isConflict(error) || tries >= VERDICT_TRIES) throw error;
      }
    }
  }

  async deleteThread(thread) {
    const entry = await this.held(thread);
    await this.store.remove(entry.key);
    this.threads.delete(thread);
  }

  /** Starts the background write, or marks the one running to write again when it ends. */
  save(entry) {
    if (entry.refused) return;
    entry.dirty = true;
    if (!entry.saving) entry.saving = this.drain(entry).finally(() => { entry.saving = undefined; });
  }

  async drain(entry) {
    while (entry.dirty && !entry.refused) {
      entry.dirty = false;
      try {
        await this.write(entry);
      } catch (error) {
        entry.refused = error;
        this.onRefused?.(error);
      }
    }
  }

  async write(entry) {
    let moved = false;
    for (let tries = 1; ; tries += 1) {
      const metadata = { title: entry.doc.title ?? "", created: entry.doc.created, updated: entry.doc.updated };
      // What this write carries, taken before it is awaited: a put that lands meanwhile changes entry.doc, and is
      // written by the next round of drain, never named as written by this one.
      const writing = entry.doc.checkpointId;
      // The object as it is written: the conversation and the current checkpoint's pending writes.
      const doc = { ...entry.doc, writes: entry.writes.get(entry.doc.checkpointId) ?? [] };
      const body = Buffer.from(JSON.stringify(doc));
      if (body.length > CHECKPOINT_MAX_BYTES) throw new Error("This conversation is too large to save.");
      const aside = asideOf(entry.doc.aside);
      try {
        const { generation } = await this.store.write(entry.key, body, { ifGeneration: entry.generation ?? "0", metadata });
        entry.generation = generation;
        entry.written = writing;
        entry.asideBase = aside;
        entry.core = coreOf(doc);
        return;
      } catch (error) {
        if (!isConflict(error)) throw error;
        const found = await this.store.read(entry.key);
        const stored = found ? JSON.parse(found.body.toString("utf8")) : undefined;
        const theirs = stored?.checkpointId;
        if (!found || !theirs) throw error;
        // #651: nothing but the aside moved (a verdict, setVerdict): merged and made again, up to ASIDE_TRIES times.
        const core = coreOf(stored);
        if (core === entry.core) {
          if (tries > ASIDE_TRIES) throw error;
        } else {
          // Still the checkpoint this turn started from, or one this saver wrote (a write whose answer was lost and was
          // made again lands here too): only the generation moved, which is made again once.
          if (moved || (theirs !== entry.base && theirs !== entry.written && theirs !== writing)) throw error;
          moved = true;
          entry.core = core;
        }
        entry.generation = found.generation;
        mergeAside(entry.doc, stored.aside, entry.asideBase);
        entry.asideBase = asideOf(stored.aside);
      }
    }
  }

  /** Waits for every save this saver started; throws the error that refused one (a conflict's code is "conflict", any
   *  other error a save that failed). */
  async flush() {
    for (const entry of this.threads.values()) {
      while (entry.saving) await entry.saving;
      if (entry.refused) throw entry.refused;
    }
  }
}
