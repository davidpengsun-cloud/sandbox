export const ASK_HISTORY_PREFIX = "askHistory:";
export const CONTEXT_PERSON_PREFIX = "contextAsk:person:";
export const DEFAULT_RETENTION_DAYS = 30;
const DAY_MS = 24 * 60 * 60 * 1000;

export function clearAskHistory() {
  try {
    for (const key of Object.keys(window.sessionStorage)) {
      if (key.startsWith(ASK_HISTORY_PREFIX)) window.sessionStorage.removeItem(key);
    }
  } catch {
    /* Authentication still works when browser storage is unavailable. */
  }
  try {
    for (const key of Object.keys(window.localStorage)) {
      if (key.startsWith(CONTEXT_PERSON_PREFIX)) window.localStorage.removeItem(key);
    }
  } catch {
    /* Authentication still works when browser storage is unavailable. */
  }
}

export function personStorageKey(principal) {
  const norm = String(principal || "anonymous").trim().toLowerCase() || "anonymous";
  return `${CONTEXT_PERSON_PREFIX}${norm}`;
}

function normalizeRetentionDays(retentionDays) {
  const n = Number(retentionDays);
  if (!Number.isFinite(n)) return DEFAULT_RETENTION_DAYS;
  return Math.max(0, Math.floor(n));
}

export function loadPersonHistory(principal, { retentionDays = DEFAULT_RETENTION_DAYS, now = Date.now() } = {}) {
  const days = normalizeRetentionDays(retentionDays);
  const key = personStorageKey(principal);
  if (days <= 0) {
    try {
      window.localStorage?.removeItem(key);
    } catch {
      /* Storage may be unavailable. */
    }
    return { conversations: [], questions: [] };
  }
  const cutoff = now - days * DAY_MS;
  try {
    const raw = window.localStorage?.getItem(key);
    if (!raw) return { conversations: [], questions: [] };
    const parsed = JSON.parse(raw);
    const conversations = Array.isArray(parsed?.conversations)
      ? parsed.conversations.filter((c) => c && typeof c.id === "string" && Number(c.updatedAt || 0) >= cutoff)
      : [];
    const questions = Array.isArray(parsed?.questions)
      ? parsed.questions.filter((q) => q && typeof q.question === "string" && Number(q.askedAt || 0) >= cutoff)
      : [];
    return { conversations, questions };
  } catch {
    return { conversations: [], questions: [] };
  }
}

export function savePersonTurn(
  principal,
  { conversationId, title, turns = [], runtimes = null, question = "", units = [] } = {},
  { retentionDays = DEFAULT_RETENTION_DAYS, now = Date.now() } = {},
) {
  const days = normalizeRetentionDays(retentionDays);
  const key = personStorageKey(principal);
  if (days <= 0) {
    try {
      window.localStorage?.removeItem(key);
    } catch {
      /* Storage may be unavailable. */
    }
    return { conversations: [], questions: [] };
  }
  const current = loadPersonHistory(principal, { retentionDays: days, now });
  const qText = String(question || "").trim();
  const cleanUnits = Array.isArray(units) ? units.filter((u) => typeof u === "string" && u) : [];

  let nextQuestions = current.questions;
  if (qText) {
    const lower = qText.toLowerCase();
    nextQuestions = [
      { question: qText, units: cleanUnits, askedAt: now },
      ...current.questions.filter((item) => String(item.question || "").trim().toLowerCase() !== lower),
    ].slice(0, 50);
  }

  let nextConversations = current.conversations;
  if (conversationId) {
    const convTitle = String(title || qText || "Conversation").trim();
    const compactTurns = (Array.isArray(turns) ? turns : []).slice(0, 20).map((t) => ({
      id: t.id,
      q: t.q,
      units: t.units || [],
      purpose: t.purpose,
      arm: t.arm,
      busy: false,
      err: t.err || null,
      a: t.a || null,
    }));
    const entry = {
      id: conversationId,
      title: convTitle,
      updatedAt: now,
      runtimes: Array.isArray(runtimes) && runtimes.length ? runtimes : null,
      turns: compactTurns,
    };
    nextConversations = [
      entry,
      ...current.conversations.filter((c) => c.id !== conversationId),
    ].slice(0, 20);
  }

  const next = { conversations: nextConversations, questions: nextQuestions };
  try {
    window.localStorage?.setItem(key, JSON.stringify(next));
  } catch {
    /* Storage quota or privacy mode. */
  }
  return next;
}
