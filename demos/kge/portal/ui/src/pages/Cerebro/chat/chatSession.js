import React, { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from "react";
import { conversationTitle, getContentString, questionSubmit, useAgentChatStream } from "../../../components/agentchat";
import { lastPathOf, answerOf, postFeedback } from "./chatAnswer";
import { CHAT_ID } from "../cerebroState";
import { CONNECTION_LOST, TIMED_OUT, WATCHDOG_LIMIT_MS, WATCHDOG_SILENCE_MS, applyEvent, endBlock, newBlock, refusalOf } from "./working";
import { runOf } from "./BackgroundRun";

/* Cerebro's chat, held by the page (cerebro.md §7.2): the agent's stream, the open conversation and the feedback sent.
   The page mounts it once, above every mode, so a mode switch neither drops the conversation nor stops an answer on its
   way; Chat mode shows it in the middle and Graph mode in the dock.
   #625: the portal's server keeps each person's conversations; the page holds no conversation of its own beyond the
   open one. The sessions rail lists the person's conversations from the list route, newest first, a page at a time
   ("Show more"); opening one reads it whole from the open route, with a question back to the person still waiting
   (its interrupt), so a reload mid-pause shows the card and its answer resumes the turn. New chat makes a new
   conversation id, and nothing is kept on the server until its first question; Delete removes one. Each request sends
   the one new message and the conversation id (the stream hook's thread id); a resume sends no message; an edited
   question names the message it replaces (context.rewind). The open conversation's id is in the address (`chat`),
   beside the mode and the node, so a reload reopens it. Nothing is kept in the browser.
   #651: the server keeps one record per reply, by the id of the person's question that opened the turn (`replies`:
   { sent, duration_ms, outcome, failure?, steps, readouts, verdict? }), in the open route, in each `values` event and
   in a `{ kind: "reply" }` custom event after the outcome; the page reads it for each reply's time, duration, steps,
   failure, readout messages and Like or Dislike, which it saves with the verdict route. A request the server refuses
   before its stream begins ends its turn with the server's reason (`detail`), and a turn whose question never reached
   the server's values is marked unsent: Try again reads the conversation again and rewinds to the question where the
   server saved it after all, else sends it afresh. */

export const CONVERSATIONS_URL = "/api/cerebro/conversations";
/** #630: a background run is asked about at most once a second, and at least every 30 seconds while the portal cannot say. */
export const RUN_POLL_FLOOR_MS = 1000;
export const RUN_POLL_MAX_MS = 30000;
function stopRunPoll(timing) {
  clearTimeout(timing.timer);
  timing.timer = null;
  timing.request?.abort();
  timing.request = null;
}
/** #630: the card for a pending run the server no longer holds, in its place. */
export const RUN_GONE = "This question is no longer running: ask it again.";
function goneMessage(messages, messageId) {
  const held = (messages || []).find((m) => m?.id === messageId);
  const pending = held ? runOf(held)?.pending : undefined;
  if (!held) return null;
  if (!pending) {
    const answer = answerOf(held);
    if (answer?.document_evidence_pending !== true) return null;
    const { document_evidence_pending: _pending, ...kept } = answer;
    return { ...held, content: JSON.stringify(kept) };
  }
  return { ...held, content: JSON.stringify({ run: { id: "gone", state: "failed" }, question: pending.question, error: RUN_GONE, failure: "run_gone" }) };
}
/** How many conversations the rail reads at a time. */
export const RAIL_PAGE = 20;
/** The line at the top of a conversation whose oldest turns the server no longer keeps (#625: 200 messages at most). */
export const TRIMMED_NOTE = "Earlier turns of this chat are no longer kept.";

/** A new conversation's id, a version 4 UUID, as the server takes one: crypto.randomUUID where the page has it (a secure
 *  context), else made from crypto.getRandomValues, which a page served over plain HTTP still has. */
export function newConversationId() {
  if (typeof globalThis.crypto?.randomUUID === "function") return globalThis.crypto.randomUUID();
  const bytes = globalThis.crypto.getRandomValues(new Uint8Array(16));
  bytes[6] = (bytes[6] & 0x0f) | 0x40;
  bytes[8] = (bytes[8] & 0x3f) | 0x80;
  const hex = [...bytes].map((b) => b.toString(16).padStart(2, "0")).join("");
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
}
/** A conversation id the address may carry: a UUID, as the server takes one (cerebroState.js CHAT_ID); anything else
 *  is none, so the page starts a new chat. */
export const conversationIdOf = (value) => (typeof value === "string" && CHAT_ID.test(value) ? value : null);

/** The open route's interrupt as the stream hook gives one: one interrupt alone, several as a list, none as null. */
export function interruptOf(value) {
  const list = Array.isArray(value) ? value.filter((i) => i && typeof i === "object") : value && typeof value === "object" ? [value] : [];
  if (!list.length) return null;
  return list.length === 1 ? list[0] : list;
}

const NO_MESSAGES = Object.freeze([]);
const NO_REPLIES = Object.freeze({});
const freshOpen = (id) => ({ id, messages: NO_MESSAGES, interrupt: null, trimmed: 0, status: "new", replies: NO_REPLIES });
/** #651: a `replies` map as the server sends it, or none. */
export const repliesOf = (value) => (value && typeof value === "object" && !Array.isArray(value) ? value : NO_REPLIES);
/** #651 (B2): the verdict route of a reply, by the id of the person's question that opened it. */
export const verdictUrl = (conversation, message) => `${CONVERSATIONS_URL}/${encodeURIComponent(conversation)}/replies/${encodeURIComponent(message)}/verdict`;
const trimmedOf = (value) => (Number.isInteger(value) && value > 0 ? value : 0);
const firstQuestion = (messages) => conversationTitle({ messages: (messages || []).filter((m) => m?.type === "human") });

export const ChatSessionContext = createContext(null);

/** The page's chat. `fetch` and `sendFeedback` are for tests (`fetch` serves the stream and the conversation routes);
 *  `health` is KGE's /health `ask` object for the picker; `questions` are the golden questions the person may ask, for
 *  the empty conversation's "Try one of these" and the input's suggestions, and `glossary` the glossary terms
 *  ({ term, units }) the suggestions offer, where KGE gives them. `openId` is the conversation the address names, opened
 *  as the page starts; `actions.setChat(id | null)` puts the open one in the address. */
const NO_QUESTIONS = [];
export function ChatProvider({ children, actions, fetch, health, openId, questions = NO_QUESTIONS, glossary = NO_QUESTIONS, sendFeedback: post = postFeedback }) {
  const routes = useCallback((url, init = {}) => (fetch || globalThis.fetch)(url, { credentials: "same-origin", ...init }), [fetch]);
  const [activeId, setActiveId] = useState(() => conversationIdOf(openId) || newConversationId());
  const [opened, setOpened] = useState(() => ({ ...freshOpen(activeId), status: conversationIdOf(openId) ? "loading" : "new" }));
  // The rail: the conversations read so far, the next page's token, and the list's retention (days; 0 keeps nothing).
  const [rail, setRail] = useState({ items: [], next: null, loading: true, error: "", retention: null });
  const [feedback, setFeedback] = useState({});
  // #622: one working block per turn, by the person's question it answers (its human message id): a list, since a
  // resume after a pause continues in a block of its own. Page-side only, never sent; kept across conversations and modes.
  const [blocks, setBlocks] = useState({});
  // The turn under way: its question's id and its block's place in that list. Events go to it while it runs.
  const turn = useRef(null);
  const patch = useCallback((fn) => {
    const at = turn.current;
    if (!at) return;
    setBlocks((old) => {
      const list = old[at.key];
      const block = list?.[at.n];
      if (!block || block.state !== "running") return old;
      const next = fn(block);
      return next === block ? old : { ...old, [at.key]: list.map((b, i) => (i === at.n ? next : b)) };
    });
  }, []);
  // The watchdog (#622 requirement 8): WATCHDOG_SILENCE_MS with no event marks the block as still waiting, and
  // WATCHDOG_LIMIT_MS with none stops the turn with the timeout card. Any event starts both afresh.
  const timers = useRef([]);
  const streamRef = useRef(null);
  const quiet = useCallback(() => { timers.current.forEach(clearTimeout); timers.current = []; }, []);
  const finish = useCallback((ending) => { quiet(); patch((block) => endBlock(block, ending, Date.now())); }, [patch, quiet]);
  const watch = useCallback(() => {
    quiet();
    timers.current = [
      setTimeout(() => patch((block) => ({ ...block, silent: true })), WATCHDOG_SILENCE_MS),
      setTimeout(() => { finish({ state: "failed", error: TIMED_OUT }); streamRef.current?.stop?.(); }, WATCHDOG_LIMIT_MS),
    ];
  }, [finish, patch, quiet]);
  useEffect(() => quiet, [quiet]);
  // The stream's custom events (#622 contract): step, ask_progress, readout, warehouse and outcome, each applied to the
  // turn's block; an outcome ends it.
  // #625: whether the turn under way has reached the server's stream (its values or its outcome): only then is the
  // conversation the server's, to be listed in the rail and named in the address.
  const delivered = useRef(false);
  // #625 sign-off: a turn that ended not saved (its outcome's case not_saved) may have left nothing on the server.
  const unsaved = useRef(false);
  // #651 (B3): each readout's message, by its ask call, as the stream sends it, for the open conversation alone: an ask
  // call's id is the model's, and may come again in another conversation or after a rewind, so a switch, a new chat and
  // a rewind clear them (the reply records keep the finished turns' readouts). (B2) each reply's record from the
  // `{ kind: "reply" }` event, by conversation and question.
  const [readoutsHeld, setReadouts] = useState({ conversation: null, said: {} });
  const [replyEvents, setReplyEvents] = useState({});
  const threadRef = useRef(null);
  const onCustomEvent = useCallback((data) => {
    if (!data || typeof data !== "object" || typeof data.kind !== "string") return;
    if (data.kind === "reply") {
      const conversation = threadRef.current;
      if (typeof data.id === "string" && data.id && data.reply && typeof data.reply === "object" && conversation) {
        setReplyEvents((old) => ({ ...old, [conversation]: { ...old[conversation], [data.id]: data.reply } }));
      }
      return;
    }
    if (data.kind === "readout" && typeof data.tool_call_id === "string" && typeof data.message === "string" && data.message.trim()) {
      const conversation = threadRef.current;
      const said = data.message.trim();
      setReadouts((old) => ({ conversation, said: { ...(old.conversation === conversation ? old.said : {}), [data.tool_call_id]: said } }));
    }
    if (data.kind === "outcome") {
      delivered.current = true;
      unsaved.current = data.error?.case === "not_saved";
      finish({ state: data.outcome, steps: data.steps, error: data.error });
      return;
    }
    patch((block) => applyEvent(block, data));
    watch();
  }, [finish, patch, watch]);
  // #651 (A2): the stream request's refusal before its stream began, read from its JSON `detail` (the stream hook says
  // only that it failed).
  const refusal = useRef(null);
  const streamFetch = useCallback(async (url, init) => {
    const response = await (fetch || globalThis.fetch)(url, init);
    if (!response.ok) {
      let detail = "";
      try {
        const body = await response.clone().json();
        detail = typeof body?.detail === "string" ? body.detail : "";
      } catch {
        detail = "";
      }
      refusal.current = { status: response.status, detail };
    }
    return response;
  }, [fetch]);
  // The stream's thread is the open conversation: a switch clears the stream, and the opened messages show instead.
  threadRef.current = activeId;
  const stream = useAgentChatStream({ fetch: streamFetch, threadId: activeId, onCustomEvent });
  streamRef.current = stream;
  // The stream's values are the open conversation's only once it has sent from it; until the switch's clear has run
  // they are still the last conversation's. Reopening the conversation from the server sets it back to none.
  const sentFrom = useRef(null);
  const streamed = stream.values && Array.isArray(stream.messages) && stream.messages.length ? stream.messages : null;
  const live = streamed && sentFrom.current === activeId ? streamed : null;
  const shown = opened.id === activeId ? opened : freshOpen(activeId);
  // #630: a background run's ending, once the page has it, in place of its pending card (endings, below), until the
  // server's own conversation carries it.
  const [endings, setEndings] = useState({});
  const heldMessages = live || shown.messages;
  const messages = useMemo(() => {
    if (!Object.keys(endings).length) return heldMessages;
    let changed = false;
    const out = heldMessages.map((m) => {
      const ended = m?.type === "tool" && m.name === "ask" && (runOf(m)?.pending || answerOf(m)?.document_evidence_pending === true) ? endings[`${activeId}:${m.id}`]?.message : undefined;
      if (!ended) return m;
      changed = true;
      return ended;
    });
    return changed ? out : heldMessages;
  }, [heldMessages, endings, activeId]);
  const liveTrimmed = live ? stream.values?.trimmed : undefined;
  const liveReplies = live && stream.values?.replies ? repliesOf(stream.values.replies) : null;
  const trimmed = Number.isInteger(liveTrimmed) ? trimmedOf(liveTrimmed) : shown.trimmed;
  // A question back to the person: the stream's while it carries this conversation, else the one the open route read.
  const interrupt = live ? stream.interrupt : shown.interrupt || undefined;
  const interruptRef = useRef(interrupt);
  interruptRef.current = interrupt;

  // The stream's messages are copied into the open conversation as they arrive, so a stop that clears the stream's
  // values still shows them.
  useEffect(() => {
    if (!live) return;
    setOpened((old) => (old.id === activeId && old.messages !== live
      ? {
        ...old, messages: live, interrupt: interruptRef.current ?? null, trimmed: Number.isInteger(liveTrimmed) ? trimmedOf(liveTrimmed) : old.trimmed, status: "ready",
        replies: liveReplies ? { ...old.replies, ...liveReplies } : old.replies,
      }
      : old));
  }, [live, activeId, liveTrimmed, liveReplies]);

  // #651: the open conversation's reply records: the opened ones, the stream's values', each reply event, and the
  // verdicts saved here since.
  const [verdicts, setVerdicts] = useState({});
  const ownEvents = replyEvents[activeId];
  const ownVerdicts = verdicts[activeId];
  const replies = useMemo(() => {
    const merged = { ...shown.replies, ...(liveReplies || {}), ...(ownEvents || {}) };
    for (const [id, verdict] of Object.entries(ownVerdicts || {})) merged[id] = { ...merged[id], verdict };
    return merged;
  }, [shown.replies, liveReplies, ownEvents, ownVerdicts]);
  const readouts = readoutsHeld.conversation === activeId ? readoutsHeld.said : NO_REPLIES;
  const clearReadouts = useCallback(() => setReadouts({ conversation: null, said: {} }), []);
  // #651 (B2): Like or Dislike saved on the server, one per reply (the page never clears one); true once saved. A reply
  // of an older conversation that has no record yet gets one from the route.
  const saveVerdict = useCallback(async (message, verdict) => {
    try {
      const response = await routes(verdictUrl(activeId, message), {
        method: "PUT", headers: { "content-type": "application/json" }, body: JSON.stringify({ verdict }),
      });
      if (!response.ok) return false;
      setVerdicts((old) => ({ ...old, [activeId]: { ...old[activeId], [message]: verdict } }));
      return true;
    } catch {
      return false;
    }
  }, [routes, activeId]);

  // The rail's pages, from the list route (#625): the first replaces what the rail holds, a later one is added after it.
  // Its `retention_days` says how long the server keeps a conversation; 0 means the open one is deleted as the tab closes.
  const readPage = useCallback(async (token) => {
    setRail((old) => ({ ...old, loading: true, error: "" }));
    try {
      const params = new URLSearchParams({ page_size: String(RAIL_PAGE) });
      if (token) params.set("page_token", token);
      const response = await routes(`${CONVERSATIONS_URL}?${params}`);
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const body = await response.json();
      const items = (Array.isArray(body?.conversations) ? body.conversations : []).filter((c) => c && typeof c.id === "string" && c.id);
      setRail((old) => {
        // A conversation this page started since is kept above the first page until the list names it.
        const kept = token ? old.items : old.items.filter((c) => c.startedHere && !items.some((i) => i.id === c.id));
        const known = new Set(kept.map((c) => c.id));
        return {
          items: [...kept, ...items.filter((c) => !known.has(c.id))],
          next: typeof body?.next === "string" && body.next ? body.next : null,
          loading: false, error: "",
          retention: Number.isInteger(body?.retention_days) ? body.retention_days : old.retention,
        };
      });
    } catch {
      setRail((old) => ({ ...old, loading: false, error: "Your chats could not be listed." }));
    }
  }, [routes]);
  const showMore = useCallback(() => { if (rail.next && !rail.loading) readPage(rail.next); }, [rail.next, rail.loading, readPage]);

  // Opening a conversation reads it whole from the open route: its messages, how many turns were trimmed, and any
  // question back to the person still waiting. Only the latest opening is shown.
  const opening = useRef(0);
  const openConversation = useCallback(async (id) => {
    const ticket = (opening.current += 1);
    setOpened({ ...freshOpen(id), status: "loading" });
    let next;
    try {
      const response = await routes(`${CONVERSATIONS_URL}/${encodeURIComponent(id)}`);
      if (response.status === 404) next = { ...freshOpen(id), status: "missing" };
      else if (!response.ok) next = { ...freshOpen(id), status: "failed" };
      else {
        const body = await response.json();
        next = {
          id, messages: Array.isArray(body?.messages) ? body.messages : NO_MESSAGES, interrupt: interruptOf(body?.interrupt),
          trimmed: trimmedOf(body?.trimmed), status: "ready", replies: repliesOf(body?.replies),
        };
      }
    } catch {
      next = { ...freshOpen(id), status: "failed" };
    }
    if (ticket === opening.current) setOpened(next);
  }, [routes]);
  // #651 (A3): whether the server holds the open conversation's question `message`, read again from the open route; no
  // conversation yet (404), or one that cannot be read, holds none.
  const holdsQuestion = useCallback(async (message) => {
    try {
      const response = await routes(`${CONVERSATIONS_URL}/${encodeURIComponent(activeId)}`);
      if (!response.ok) return false;
      const body = await response.json();
      return (Array.isArray(body?.messages) ? body.messages : []).some((m) => m?.type === "human" && String(m.id) === String(message));
    } catch {
      return false;
    }
  }, [routes, activeId]);

  // The page's actions change with the address; they are read through a ref so a mode switch writes nothing.
  const act = useRef(actions);
  act.current = actions;
  const setAddress = useCallback((id) => act.current?.setChat?.(id), []);
  // The rail's first page, and the conversation the address names, are read once, as the page starts.
  const started = useRef(false);
  useEffect(() => {
    if (started.current) return;
    started.current = true;
    readPage(null);
    if (conversationIdOf(openId)) openConversation(activeId);
  }, [readPage, openConversation, openId, activeId]);

  // The shared state (§7.2, §7.4): the open conversation, and the last answer's path. A conversation with no answer yet
  // clears the path, so a stale path is never highlighted.
  const hasAnswer = messages.some((m) => answerOf(m));
  const path = hasAnswer ? lastPathOf(messages) : null;
  const pathKey = JSON.stringify(path);
  const pathRef = useRef(path);
  pathRef.current = path;
  useEffect(() => { act.current?.setConversation?.(messages); }, [messages]);
  useEffect(() => { act.current?.setLastPath?.(pathRef.current); }, [pathKey]);

  const sendFeedback = useCallback(async (id, verdict, body) => {
    setFeedback((old) => ({ ...old, [id]: { sending: true } }));
    const result = await post(body);
    setFeedback((old) => ({
      ...old,
      [id]: result?.error ? { error: `Your feedback was not recorded: ${result.error}` } : { verdict },
    }));
  }, [post]);

  // #624: the person's answers to the questions back to them, by their choose call's id, as the page knows them before
  // (or without) a reply: an offer's "No thanks", which sends nothing, and a pick while its resume runs. No answer
  // (null) clears one: a resume that failed leaves the card answerable again.
  const [answered, setAnswered] = useState({});
  const answerLocally = useCallback((callId, answer) => {
    if (!callId) return;
    setAnswered((old) => {
      if (answer) return { ...old, [callId]: answer };
      if (!(callId in old)) return old;
      const { [callId]: _cleared, ...rest } = old;
      return rest;
    });
  }, []);

  // #625: the conversation changes only between answers (a switch would stop the one on its way). A question back to
  // the person no longer holds it: the server keeps the pause, and reopening the conversation shows it again.
  const busy = Boolean(stream.isLoading || interrupt);
  const switching = Boolean(stream.isLoading);
  const select = useCallback((id) => {
    if (switching || id === activeId) return;
    sentFrom.current = null;
    clearReadouts();
    setActiveId(id);
    setAddress(id);
    openConversation(id);
  }, [switching, activeId, openConversation, setAddress, clearReadouts]);
  // New chat: a new conversation id; nothing is kept on the server until its first question.
  const startFresh = useCallback(() => {
    const id = newConversationId();
    sentFrom.current = null;
    opening.current += 1;
    clearReadouts();
    setActiveId(id);
    setOpened(freshOpen(id));
    setAddress(null);
  }, [setAddress, clearReadouts]);
  const startNew = useCallback(() => {
    if (switching) return;
    if (!messages.length && shown.status === "new") return;
    startFresh();
  }, [switching, messages.length, shown.status, startFresh]);
  // Reload (a turn refused because the conversation changed in another tab): the open conversation read again from
  // the server, in place of what the stream showed.
  const reload = useCallback(() => {
    if (switching) return;
    sentFrom.current = null;
    openConversation(activeId);
  }, [switching, activeId, openConversation]);
  // Delete: the conversation is removed on the server and from the rail; the open one gives way to a new chat.
  const remove = useCallback(async (id) => {
    if (id === activeId && switching) return;
    let ok = false;
    try {
      const response = await routes(`${CONVERSATIONS_URL}/${encodeURIComponent(id)}`, { method: "DELETE" });
      ok = response.ok || response.status === 404;
    } catch {
      ok = false;
    }
    if (!ok) {
      setRail((old) => ({ ...old, error: "That chat could not be deleted. Try again." }));
      return;
    }
    setRail((old) => ({ ...old, error: "", items: old.items.filter((c) => c.id !== id) }));
    if (id === activeId) startFresh();
  }, [activeId, switching, routes, startFresh]);

  // #630 requirements 2 to 4: the background runs of the open conversation, followed between turns at KGE's poll
  // interval (the pending card's poll_after), through the portal, which reads each run's id from the saved conversation
  // and writes its ending there. A turn under way, or a question back to the person, holds the following; it goes on
  // after. A run whose ending the server could not keep yet (saved: false) is asked again until it is kept. Reopening the
  // conversation, here or after a reload on any instance, shows its pending cards, which are followed again.
  const runsKey = JSON.stringify(heldMessages.flatMap((m) => {
    if (m?.type !== "tool" || m.name !== "ask" || !m.id) return [];
    const known = endings[`${activeId}:${m.id}`];
    const effective = known?.message || m;
    const pending = runOf(effective)?.pending || runOf(m)?.pending;
    const evidence = answerOf(effective)?.document_evidence_pending === true;
    return (evidence || known?.saved === false || (pending && !known)) ? [[String(m.id), pending?.pollAfter || 1]] : [];
  }));
  const runUrl = useCallback((messageId) => `${CONVERSATIONS_URL}/${encodeURIComponent(activeId)}/runs/${encodeURIComponent(messageId)}`, [activeId]);
  const tookEnding = useCallback((messageId, body) => {
    if (body?.state !== "ended" || !body.message || typeof body.message !== "object") return false;
    setEndings((old) => {
      const key = `${activeId}:${messageId}`;
      const prior = old[key]?.message || messagesRef.current.find((m) => String(m?.id) === messageId);
      // A conflicted evidence write is retried; do not display its unsaved summary.
      const message = body.saved === false && answerOf(prior)?.document_evidence_pending === true ? prior : body.message;
      return { ...old, [key]: { message, saved: body.saved !== false } };
    });
    return true;
  }, [activeId]);
  // Each pending card follows its own KGE poll_after. An unavailable run or an unsaved ending backs off until KGE can
  // give its status again.
  const pollSchedule = useRef(new Map());
  useEffect(() => {
    const schedules = pollSchedule.current;
    return () => {
      for (const timing of schedules.values()) stopRunPoll(timing);
      schedules.clear();
    };
  }, [routes, runUrl, tookEnding]);
  useEffect(() => {
    const schedules = pollSchedule.current;
    const runs = new Map(JSON.parse(runsKey));
    for (const [messageId, timing] of schedules) {
      if (busy || !runs.has(messageId)) stopRunPoll(timing);
      if (!runs.has(messageId)) schedules.delete(messageId);
    }
    const schedule = (messageId, timing) => {
      timing.timer = setTimeout(async () => {
        timing.timer = null;
        const request = new AbortController();
        timing.request = request;
        let body;
        try {
          const response = await routes(runUrl(messageId), { signal: request.signal });
          // The server no longer holds that card (the conversation was trimmed or deleted elsewhere): it ends here.
          if (request.signal.aborted) return;
          if (response.status === 404) {
            schedules.delete(messageId);
            setEndings((old) => ({ ...old, [`${activeId}:${messageId}`]: { message: goneMessage(messagesRef.current, messageId), saved: true } }));
            return;
          }
          body = response.ok ? await response.json() : undefined;
        } catch {
          body = undefined;
        }
        if (request.signal.aborted) return;
        const ended = tookEnding(messageId, body);
        if (ended && body.saved !== false && body.evidence_pending !== true) {
          schedules.delete(messageId);
          return;
        }
        if (body?.state === "running") {
          const seconds = Number(body.poll_after);
          if (Number.isFinite(seconds) && seconds > 0) timing.interval = Math.max(RUN_POLL_FLOOR_MS, seconds * 1000);
          timing.delay = timing.interval;
        } else if (ended && body.evidence_pending === true && body.saved !== false) {
          timing.delay = RUN_POLL_FLOOR_MS;
        } else {
          timing.delay = Math.min(RUN_POLL_MAX_MS, 2 * timing.delay);
        }
        timing.due = Date.now() + timing.delay;
        timing.request = null;
        schedule(messageId, timing);
      }, Math.max(0, Math.ceil(timing.due - Date.now())));
    };
    for (const [messageId, every] of runs) {
      if (!schedules.has(messageId)) {
        const delay = Math.max(RUN_POLL_FLOOR_MS, every * 1000);
        schedules.set(messageId, { interval: delay, delay, due: Date.now() + delay, timer: null, request: null });
      }
      const timing = schedules.get(messageId);
      if (!busy && timing.timer === null && timing.request === null) schedule(messageId, timing);
    }
  }, [runsKey, busy, routes, runUrl, tookEnding, activeId]);
  // Stop: the run cancelled through the portal, its ending shown at once.
  const [stopping, setStopping] = useState({});
  const stopRun = useCallback(async (messageId) => {
    if (!messageId) return;
    setStopping((old) => ({ ...old, [messageId]: true }));
    try {
      const response = await routes(`${runUrl(messageId)}/stop`, { method: "POST" });
      if (response.ok) tookEnding(messageId, await response.json());
    } catch {
      // The run goes on; its card still offers Stop.
    } finally {
      setStopping((old) => {
        const { [messageId]: _done, ...rest } = old;
        return rest;
      });
    }
  }, [routes, runUrl, tookEnding]);

  const submit = stream.submit;
  const messagesRef = useRef(messages);
  messagesRef.current = messages;
  // A question or a resume sent starts its turn's working block, under the question it answers (the new question, or,
  // for a resume, which sends none, the conversation's last).
  const blocksRef = useRef(blocks);
  blocksRef.current = blocks;
  const send = useCallback((...args) => {
    sentFrom.current = activeId;
    delivered.current = false;
    unsaved.current = false;
    refusal.current = null;
    // #651: a rewind (an edited question, or Try again in its place) drops the turns after it, and their readouts.
    if (args[1]?.context?.rewind !== undefined) clearReadouts();
    const sent = Array.isArray(args[0]?.messages) ? args[0].messages : messagesRef.current;
    const human = [...sent].reverse().find((m) => m?.type === "human");
    if (human?.id) {
      const key = String(human.id);
      const n = (blocksRef.current[key] || []).length;
      const block = newBlock(getContentString(human.content), Date.now());
      blocksRef.current = { ...blocksRef.current, [key]: [...(blocksRef.current[key] || []), block] };
      turn.current = { key, n };
      setBlocks((old) => {
        const list = [...(old[key] || [])];
        list[n] = block;
        return { ...old, [key]: list };
      });
      watch();
    }
    return submit(...args);
  }, [activeId, submit, watch, clearReadouts]);
  // Stop ends the turn's block as stopped; the stream then closes.
  const stop = useCallback((...args) => { finish({ state: "stopped" }); return stream.stop?.(...args); }, [finish, stream]);

  // The stream closed with the block still running: no outcome came, so the connection was lost, unless the turn
  // paused on the person.
  const wasLoading = useRef(false);
  // #651 (A3): the stream's values are the server's (they carry `trimmed`), so the question reached its saved state.
  const fromServerNow = Number.isInteger(stream.values?.trimmed);
  useEffect(() => {
    if (stream.isLoading) { wasLoading.current = true; return; }
    if (!wasLoading.current) return;
    wasLoading.current = false;
    // #651: a refusal before the stream shows the server's reason; a question that never reached the server's values
    // is marked unsent (A3).
    if (stream.interrupt) finish({ state: "paused" });
    else finish({ state: "failed", error: refusal.current?.detail ? refusalOf(refusal.current) : CONNECTION_LOST, unsent: !(delivered.current || fromServerNow) });
  }, [stream.isLoading, stream.interrupt, finish, fromServerNow]);

  // #625: a turn that ended with the conversation on screen, whose stream delivered the server's values or its outcome,
  // has been kept by the server: the rail lists it first, by the title the server gave it (else its first question),
  // and the address names it. A request refused before its stream began (a 400 or a 503) adds neither: the values
  // shown then are the page's own (the question it sent), never the server's, which always carry `trimmed`.
  const ranFor = useRef(null);
  const fromServer = Number.isInteger(stream.values?.trimmed);
  useEffect(() => {
    if (stream.isLoading) {
      ranFor.current = sentFrom.current;
      if (fromServer) delivered.current = true;
      return;
    }
    const id = ranFor.current;
    ranFor.current = null;
    if (!id || id !== activeId || !live || !(delivered.current || fromServer)) return;
    // A new chat whose first turn was not saved is not the server's: it gets no row and no address. One already listed
    // keeps its row.
    if (unsaved.current && !rail.items.some((c) => c.id === id)) return;
    const title = typeof stream.values?.title === "string" && stream.values.title ? stream.values.title : firstQuestion(live);
    const updated = new Date().toISOString();
    setRail((old) => {
      const was = old.items.find((c) => c.id === id);
      const row = { ...(was || { id, created: updated, startedHere: true }), title, updated };
      return { ...old, items: [row, ...old.items.filter((c) => c.id !== id)] };
    });
    setAddress(id);
  }, [setAddress, stream.isLoading, activeId, live, stream.values, fromServer, rail.items]);

  // #625: where the server keeps nothing (retention 0), the open conversation is deleted as the tab closes.
  const leaving = useRef({});
  leaving.current = { retention: rail.retention, id: activeId, kept: messages.length > 0 };
  useEffect(() => {
    const onHide = () => {
      const { retention, id, kept } = leaving.current;
      if (retention !== 0 || !kept) return;
      try {
        Promise.resolve(routes(`${CONVERSATIONS_URL}/${encodeURIComponent(id)}`, { method: "DELETE", keepalive: true })).catch(() => {});
      } catch {
        // The tab is closing: a delete that cannot be sent is left to the server's daily sweep.
      }
    };
    window.addEventListener("pagehide", onHide);
    return () => window.removeEventListener("pagehide", onHide);
  }, [routes]);

  // #622 requirement 9: a question sent while a turn runs waits here ("Next:"), and goes as soon as the turn ends,
  // whatever its ending; Stop ends the turn, so the next one goes then. #625: there is no question limit; the model
  // reads a rolling window of the latest questions, so a ninth question goes on in the same chat.
  const [queue, setQueue] = useState([]);
  const queued = useRef(0);
  const enqueue = useCallback((text, context) => {
    const question = String(text ?? "").trim();
    if (question) setQueue((old) => [...old, { id: (queued.current += 1), text: question, context }]);
  }, []);
  const unqueue = useCallback((id) => setQueue((old) => old.filter((q) => q.id !== id)), []);
  // The stream reports loading only once its run has started, a moment after the submit: until then `starting` holds
  // the queue, so one question goes at a time and none goes twice. The submit settles when its run ends; one that
  // failed, or never reached loading, releases the queue then, so it cannot stall.
  const starting = useRef(false);
  const [released, setReleased] = useState(0);
  if (stream.isLoading) starting.current = false;
  const sendRef = useRef(send);
  sendRef.current = send;
  useEffect(() => {
    // A turn paused on the person waits for their choice: the next question goes after it (a send would drop the choice).
    if (stream.isLoading || interrupt || starting.current || !queue.length) return;
    const [next, ...rest] = queue;
    starting.current = true;
    setQueue(rest);
    const release = () => {
      if (!starting.current) return;
      starting.current = false;
      setReleased((n) => n + 1);
    };
    let sent;
    try {
      sent = sendRef.current(...questionSubmit(messagesRef.current, next.text, next.context));
    } catch {
      sent = null;
    }
    Promise.resolve(sent).catch(() => {}).finally(release);
  }, [stream.isLoading, interrupt, queue, released]);

  const openNode = useCallback((id) => act.current?.openNode?.(id), []);
  const value = useMemo(() => ({
    stream: { ...stream, messages, interrupt, submit: send, stop },
    sessions: rail.items, more: Boolean(rail.next), showMore, railLoading: rail.loading, railError: rail.error, retention: rail.retention,
    activeId, opening: shown.status, trimmed, select, startNew, remove, reload, busy, switching,
    health, questions, glossary, feedback, sendFeedback, path, blocks, queue, enqueue, unqueue, openNode,
    answered, answerLocally, stopRun, stopping, replies, readouts, saveVerdict, holdsQuestion,
  }), [stream, messages, interrupt, send, stop, rail, showMore, activeId, shown.status, trimmed, select, startNew, remove, reload, busy, switching,
    health, questions, glossary, feedback, sendFeedback, path, blocks, queue, enqueue, unqueue, openNode, answered, answerLocally, stopRun, stopping,
    replies, readouts, saveVerdict, holdsQuestion]);
  return <ChatSessionContext.Provider value={value}>{children}</ChatSessionContext.Provider>;
}

export const useChatSession = () => useContext(ChatSessionContext);
