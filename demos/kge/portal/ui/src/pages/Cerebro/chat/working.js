/* #622: a turn's working block, as plain data the page holds (chatSession.js) and WorkingBlock.js draws. The server's
   custom events (step, ask_progress, readout, warehouse, outcome) are applied here; nothing is sent back. Every line is
   plain words: no id, no SQL, no physical name, never a figure. */

/** The watchdog: after WATCHDOG_SILENCE_MS with no event the block says it is still waiting; after WATCHDOG_LIMIT_MS
 *  with none the page stops the turn with the timeout card. TICK_MS is how often the elapsed seconds move. */
export const WATCHDOG_SILENCE_MS = 20000;
export const WATCHDOG_LIMIT_MS = 120000;
export const TICK_MS = 1000;
/** At most this many lines under the step while a turn runs; older ones roll up. */
export const MAX_LINES = 5;
/** The path line names the newest PATH_SHOWN nodes. */
export const PATH_SHOWN = 6;

export const FIRST_STEP = { key: "understanding", text: "Understanding your question" };
export const STILL_WAITING = "Still waiting for the warehouse…";
export const STILL_WAITING_OTHER = "Still waiting for the answer…";
/** The watchdog's line: the warehouse only while the warehouse runs, so it says honestly what is awaited. */
export const stillWaiting = (block) => (block?.step?.key === "warehouse" ? STILL_WAITING : STILL_WAITING_OTHER);
export const CONNECTION_LOST = { case: "connection_lost", message: "Connection lost", action: "try_again" };
export const TIMED_OUT = { case: "timeout", message: "This took too long; try a narrower period or fewer groups.", action: "try_again" };
const FAILED = { case: "failed", message: "The assistant couldn't respond.", action: "try_again" };
// #625: "reload" is the conversation changed in another tab; the card reopens it from the server.
const ACTIONS = ["try_again", "rephrase", "narrow", "reload"];

// A word an event may carry to the page: text with no internal name (an UPPER_SNAKE or snake_case word, a dotted
// schema.table) and no figure-shaped number beyond a year or a day.
const INTERNAL = /_|\b[A-Za-z][\w$]*\.[A-Za-z][\w$]*\b/;
const FIGURE = /\d[\d,]*\.\d|\d{1,3}(?:,\d{3})+|[$€£¥]\s*\d|\d+\s*%|\b\d{5,}\b/;
export function plainWords(value) {
  if (typeof value !== "string") return "";
  const text = value.trim();
  return text && !INTERNAL.test(text) && !FIGURE.test(text) ? text : "";
}
const plainList = (values) => (Array.isArray(values) ? values : [values]).map(plainWords).filter(Boolean);

export function newBlock(question, now) {
  return { question, state: "running", started: now, ended: null, step: FIRST_STEP, done: [], steps: 1, readout: null, path: [], warehouse: [], silent: false, error: null };
}

function withStep(block, data) {
  const text = plainWords(data.text);
  if (!text) return block;
  const key = typeof data.step === "string" ? data.step : text;
  if (block.step && block.step.key === key && block.step.text === text) return block;
  return { ...block, step: { key, text }, done: block.step ? [...block.done, block.step.text] : block.done, steps: block.steps + 1 };
}

function withReadout(block, readout) {
  if (!readout || typeof readout !== "object") return block;
  const clean = {
    measure: plainWords(readout.measure), mode: plainWords(readout.mode), period: plainWords(readout.period),
    filters: plainList(readout.filters ?? []), grouping: plainList(readout.grouping ?? []), source: plainWords(readout.source),
  };
  return { ...block, readout: clean };
}

// A graph node the ask reached (an event's node with a type; a pipeline step has none), by its label. The question's
// own words (Words nodes) are not the graph's.
function withPath(block, event) {
  if (!event || event.event !== "node_added" || !event.node?.type || event.node.type === "Words") return block;
  const label = plainWords(event.node.label || event.node.name);
  if (!label || block.path.includes(label)) return block;
  return { ...block, path: [...block.path, label] };
}

const STATES = ["queued", "running", "done", "failed"];
function withWarehouse(block, data) {
  const statement = Number(data.statement);
  if (!Number.isInteger(statement) || statement < 0 || !STATES.includes(data.state)) return block;
  const ms = Number.isFinite(data.elapsed_ms) && data.elapsed_ms >= 0 ? data.elapsed_ms : null;
  const one = { statement, state: data.state, ms };
  const rest = block.warehouse.filter((w) => w.statement !== statement);
  return { ...block, warehouse: [...rest, one].sort((a, b) => a.statement - b.statement) };
}

/** A custom event applied to a running block: any event also ends a silence. */
export function applyEvent(block, data) {
  const heard = block.silent ? { ...block, silent: false } : block;
  switch (data?.kind) {
    case "step": return withStep(heard, data);
    case "readout": return withReadout(heard, data.readout);
    case "ask_progress": return withPath(heard, data.event);
    case "warehouse": return withWarehouse(heard, data);
    default: return heard;
  }
}

/** An error as the card shows it: the server's case, plain message and action, or a fixed one. `detail` is kept for
 *  the engineer's view only (the card decides). */
export function errorOf(error) {
  if (!error || typeof error !== "object") return FAILED;
  const message = typeof error.message === "string" && error.message.trim() ? error.message.trim() : FAILED.message;
  return {
    case: typeof error.case === "string" ? error.case : FAILED.case,
    message,
    action: ACTIONS.includes(error.action) ? error.action : "try_again",
    ...(typeof error.suggestion === "string" && error.suggestion.trim() ? { suggestion: error.suggestion.trim() } : {}),
    ...(typeof error.detail === "string" && error.detail.trim() ? { detail: error.detail.trim() } : {}),
  };
}

// #630: "background" is a turn that ended at a pending card: its question runs on in the background, and the pending
// card takes the block's place (ChatView.js), so the block draws nothing of its own.
const ENDINGS = ["answered", "paused", "stopped", "failed", "background"];
/** The block in its final state: `ending` is { state, steps?, error? }. An unknown state is a failure. */
export function endBlock(block, ending, now) {
  const state = ENDINGS.includes(ending?.state) ? ending.state : "failed";
  const steps = Number.isInteger(ending?.steps) && ending.steps > 0 ? ending.steps : block.steps;
  // #651 (A3): a turn whose question never reached the server's saved values is marked unsent, so Try again sends it
  // afresh instead of asking to rewind to it.
  return { ...block, state, ended: now, silent: false, steps, error: state === "failed" ? errorOf(ending?.error) : null, ...(ending?.unsent ? { unsent: true } : {}) };
}

/** #651 (A2): the error card for a request the server refused before its stream began (a 400, 403, 404 or 503 with a
 *  JSON `detail`): the server's reason, as React text, and Try again. Without a reason, a fixed sentence. */
export const REFUSED = "The assistant could not take this question.";
export function refusalOf(refusal) {
  const detail = typeof refusal?.detail === "string" ? refusal.detail.trim() : "";
  return { case: "refused", message: detail || REFUSED, action: "try_again" };
}

/** #651 (A4, B5): a turn's finished steps as the server saved them in its reply record (`steps`, its `step` events),
 *  in plain words. */
export const savedSteps = (record) => (Array.isArray(record?.steps) ? record.steps : [])
  .map((s) => plainWords(typeof s === "string" ? s : s?.text)).filter(Boolean);

/** #651 (A4): a finished turn's block rebuilt from its reply record after a reload: its outcome, steps and duration,
 *  and a failure's error card. Null where the record says nothing a block can show. */
export function blockOfReply(record, question = "") {
  if (!record || typeof record !== "object" || !ENDINGS.includes(record.outcome)) return null;
  const started = Date.parse(record.sent);
  if (!Number.isFinite(started)) return null;
  const ms = Number.isFinite(record.duration_ms) && record.duration_ms >= 0 ? record.duration_ms : 0;
  const done = savedSteps(record);
  return {
    ...newBlock(question, started), state: record.outcome, ended: started + ms, done, steps: Math.max(done.length, 1), step: null,
    error: record.outcome === "failed" ? errorOf(record.failure) : null, saved: true,
  };
}

/** #651 (B2): how long a reply took, in whole seconds ("Took 6 s"), or a minute and seconds past a minute. */
export function tookText(ms) {
  if (!Number.isFinite(ms) || ms < 0) return "";
  const s = Math.round(ms / 1000);
  return s < 60 ? `Took ${s} s` : `Took ${Math.floor(s / 60)} min ${s % 60} s`;
}

/** Whole seconds since the block started, to `now` while it runs, to its end after. */
export const secondsOf = (block, now) => Math.max(0, Math.floor(((block.ended ?? now) - block.started) / 1000));

/** The block's final line (#622 requirement 7). */
export function finalText(block, now) {
  const s = secondsOf(block, now);
  if (block.state === "answered") return `✓ Worked for ${s} s · ${block.steps} step${block.steps === 1 ? "" : "s"}`;
  if (block.state === "paused") return "Paused · needs your choice";
  if (block.state === "stopped") return `Stopped after ${s} s`;
  if (block.state === "failed") return `✗ Not answered · ${s} s`;
  return "";
}

/** The final line as the block's live region announces it: no ✓ or ✗, and a comma for the dot ("Worked for 6 s, 4
 *  steps"). */
export const spokenFinal = (block, now) => finalText(block, now).replace(/^[✓✗]\s*/, "").replace(/ · /g, ", ");

/** The readout in one line: what is being computed, in the person's words. */
export function readoutText(readout) {
  if (!readout) return "";
  const what = [readout.measure, readout.mode ? `(${readout.mode})` : ""].filter(Boolean).join(" ");
  const parts = [
    what, readout.period,
    readout.filters.length ? `for ${readout.filters.join(", ")}` : "",
    readout.grouping.length ? `by ${readout.grouping.join(", ")}` : "",
    readout.source ? `from ${readout.source}` : "",
  ].filter(Boolean);
  return parts.length ? `Computing ${parts.join(" · ")}` : "";
}

const WAREHOUSE_WORDS = { queued: "waiting", running: "running", done: "done", failed: "failed" };
/** The lines under the step, oldest first: the steps done, the readout, the path, each warehouse query's state. */
export function blockLines(block) {
  const lines = block.done.map((text, i) => ({ key: `done-${i}`, kind: "done", text: `✓ ${text}` }));
  const readout = readoutText(block.readout);
  if (readout) lines.push({ key: "readout", kind: "readout", text: readout });
  if (block.path.length) {
    const shown = block.path.length > PATH_SHOWN ? ["…", ...block.path.slice(-PATH_SHOWN)] : block.path;
    lines.push({ key: "path", kind: "path", text: `Path: ${shown.join(" → ")}` });
  }
  const several = block.warehouse.length > 1;
  for (const w of block.warehouse) {
    const time = w.ms != null ? ` · ${Math.floor(w.ms / 1000)} s` : "";
    lines.push({ key: `wh-${w.statement}`, kind: "warehouse", text: `Warehouse query${several ? ` ${w.statement}` : ""}: ${WAREHOUSE_WORDS[w.state]}${time}` });
  }
  return lines;
}

/** #651 (B5): a finished turn's lines, as its Steps tab lists them: an answered turn's last step counts as done. */
export function stepLines(block) {
  if (!block) return [];
  const finished = block.state === "answered" && block.step?.text ? { ...block, done: [...block.done, block.step.text] } : block;
  return blockLines(finished);
}
