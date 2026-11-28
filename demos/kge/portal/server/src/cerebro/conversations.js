import express from "express";
import { VERDICTS, saveVerdict, savedConversation } from "./agent.js";
import { personBearer } from "./graph.js";
import { backgroundUsage, followRun } from "./runs.js";

// #625: a person's Cerebro conversations, behind the same sign-in and Cerebro door as the stream (chat.js):
//   GET    /api/cerebro/conversations?page_token=&page_size=  { conversations: [{ id, title, created, updated }], next,
//                                                             retention_days }, newest first, the person's own alone
//   GET    /api/cerebro/conversations/:id                     { id, title, created, updated, trimmed, messages,
//                                                             interrupt }, as the stream's values give them
//   DELETE /api/cerebro/conversations/:id                     204, the object removed from the store
//   GET    /api/cerebro/conversations/:id/runs/:message?after= #630: the background run of the pending card :message
//                                                             (its tool message's id), followed (runs.js):
//                                                             { state: running | ended | unknown, ... }
//   POST   /api/cerebro/conversations/:id/runs/:message/stop  #630: Stop, the run cancelled, its ending as above
//   PUT    /api/cerebro/conversations/:id/replies/:message/verdict  #651: Like or Dislike on the reply to the person's
//                                                             message :message, { verdict: like | dislike | null } in,
//                                                             { verdict } out, kept on the reply's record
// Another person's conversation is not there to find: 404, as an id that never was. Retention is KGE's past-questions
// retention (retention_days); a list deletes the person's conversations past it, and a sweep everyone's.

export const RETENTION_TTL_MS = 10 * 60 * 1000;

/**
 * KGE's retention_days, the value Context's Ask page reads (KGE's GET /golden, served by its quality service, as the
 * signed-in person: their cookie and their own bearer, never the trusted caller token). It is the same for everyone,
 * so it is cached for RETENTION_TTL_MS. undefined where KGE is not behind the portal or does not say; the last value
 * read stands while KGE cannot be reached. known() is the last value read, for the sweep, which runs with no person.
 */
export function retentionOf(kge, { now = () => Date.now(), ttlMs = RETENTION_TTL_MS } = {}) {
  let held;
  let at = -Infinity;
  const listeners = [];
  const read = async (req) => {
    if (!kge) return undefined;
    if (held !== undefined && now() - at < ttlMs) return held;
    try {
      const response = await kge.request(req, "/golden", { trusted: false, headers: personBearer(req) });
      const days = response.ok ? (await response.json())?.retention_days : undefined;
      if (Number.isInteger(days) && days >= 0) {
        const first = held === undefined;
        held = days;
        at = now();
        if (first) for (const listener of listeners) listener(days);
      }
    } catch {
      // KGE unreachable: the last value read stands, and nothing is deleted on a guess.
    }
    return held;
  };
  return { read, known: () => held, onFirst: (listener) => listeners.push(listener) };
}

const DAY_MS = 24 * 60 * 60 * 1000;

/** The daily sweep of everyone's conversations past retention, the first once retention is first known. The window is
 *  KGE's, read as a signed-in person (retentionOf), so an instance's first sweep runs when someone first lists their
 *  chats there, not as it starts: until then it knows no window and deletes nothing (#625 sign-off). */
export function retentionSweep(conversations, retention, { everyMs = DAY_MS } = {}) {
  const sweep = async () => {
    const days = retention.known();
    if (days === undefined) return;
    try {
      const removed = await conversations.sweep(days);
      if (removed) console.info(`Cerebro conversations past retention deleted: ${removed}`);
    } catch (error) {
      console.error(`Cerebro conversation sweep failed: ${error?.name || "Error"}: ${error?.message || ""}`);
    }
  };
  retention.onFirst(() => { sweep(); });
  const timer = setInterval(sweep, everyMs);
  timer.unref();
  return { sweep, stop: () => clearInterval(timer) };
}

/** The routes, for a person the handlers in front let in (req.portalIdentity.principal). */
export function conversationRoutes({ conversations, retention, settings, kge, model, modelFor, issueQuestion, rolesOf = () => [] }) {
  const router = express.Router();
  const unavailable = (res, error) => {
    console.error(`Cerebro conversations unavailable: ${error?.name || "Error"}: ${error?.message || ""}`);
    return res.status(503).json({ detail: `${settings.cerebroName} cannot read conversations right now. Try again shortly.` });
  };
  const none = (res) => res.status(404).json({ detail: "No such conversation." });
  router.use((_req, res, next) => { res.set("cache-control", "no-store"); next(); });
  router.get("/", async (req, res) => {
    const days = await retention.read(req);
    try {
      const page = await conversations.list(req.portalIdentity.principal, {
        pageToken: typeof req.query.page_token === "string" && req.query.page_token ? req.query.page_token : undefined,
        pageSize: req.query.page_size,
        retentionDays: days,
      });
      return res.json({ conversations: page.conversations, next: page.next ?? null, retention_days: days ?? null });
    } catch (error) {
      return unavailable(res, error);
    }
  });
  router.get("/:id", async (req, res) => {
    try {
      const saved = await savedConversation(conversations, req.portalIdentity.principal, req.params.id, { roles: rolesOf(req.portalIdentity) });
      return saved ? res.json(saved) : none(res);
    } catch (error) {
      return unavailable(res, error);
    }
  });
  router.delete("/:id", async (req, res) => {
    try {
      return (await conversations.remove(req.portalIdentity.principal, req.params.id)) ? res.status(204).end() : none(res);
    } catch (error) {
      return unavailable(res, error);
    }
  });
  const follow = (stop) => async (req, res) => {
    if (!kge) return none(res);
    const after = Number(req.query.after);
    try {
      const result = await followRun({
        conversations, kge, req, model: modelFor ? modelFor(req) : model, issueQuestion, roles: rolesOf(req.portalIdentity), id: req.params.id, messageId: req.params.message, stop,
        after: Number.isInteger(after) && after >= 0 ? after : 0,
      });
      const usage = issueQuestion ? await backgroundUsage({ conversations, kge, req, id: req.params.id, messageId: req.params.message, result }).catch(() => undefined) : undefined;
      return res.json(usage ? { ...result, usage } : result);
    } catch (error) {
      if (error?.status === 404) return res.status(404).json({ detail: error.message });
      return unavailable(res, error);
    }
  };
  // #651 B2: the body is read here alone (16 KB), for a person the handlers in front let in; express.json refuses one
  // that is not JSON with app.js's 400.
  router.put("/:id/replies/:message/verdict", express.json({ limit: "16kb" }), async (req, res) => {
    const verdict = req.body && typeof req.body === "object" && !Array.isArray(req.body) ? req.body.verdict : undefined;
    if (verdict !== null && !VERDICTS.includes(verdict)) return res.status(400).json({ detail: "Send { verdict: \"like\" | \"dislike\" | null }." });
    try {
      const saved = await saveVerdict(conversations, req.portalIdentity.principal, req.params.id, req.params.message, verdict);
      return saved ? res.json(saved) : res.status(404).json({ detail: "No such reply in this conversation." });
    } catch (error) {
      return unavailable(res, error);
    }
  });
  router.get("/:id/runs/:message", follow(false));
  router.post("/:id/runs/:message/stop", follow(true));
  router.use((_req, res) => res.status(404).json({ detail: "Not found." }));
  return router;
}
