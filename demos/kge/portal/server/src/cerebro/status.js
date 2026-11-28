// #622: what the working block shows while a turn runs, built in code with no model call: the steps in plain words, the
// readout of what an ask is computing, each warehouse statement's state, and the turn's one outcome. None of it ever
// carries a figure, an id beyond the tool call's, SQL or a physical name.

// #619: the figure rule, shared by the agent's own words (agent.js spoken) and the readout. Any digit left once a date's
// or a period's numbers are taken out gives a figure. Those are 2025-06, FY26, FY26-Q1, Q1 2025, H1, a month's day
// beside its whole name, and a year beside a month, a period word or a period's preposition.
const MONTH = "(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)";
const YEAR = "(?:19|20)\\d{2}";
const PERIODS = [
  /\b(?:19|20)\d{2}-\d{2}(?:-\d{2})?\b/g,
  new RegExp(`\\bFY\\d{2,4}(?:\\s*-?\\s*[QH][1-4])?(?:\\s+${YEAR})?\\b`, "gi"),
  new RegExp(`\\b[QH][1-4](?:\\s+${YEAR})?\\b`, "gi"),
  new RegExp(`\\b\\d{1,2}(?:st|nd|rd|th)?\\s+(?:of\\s+)?${MONTH}\\b(?:\\s+${YEAR}\\b)?`, "gi"),
  new RegExp(`\\b${MONTH}\\s+(?:\\d{1,2}(?:st|nd|rd|th)?,?\\s+)?${YEAR}\\b`, "gi"),
  new RegExp(`\\b${MONTH}\\s+\\d{1,2}(?:st|nd|rd|th)?\\b(?![\\d,.])`, "gi"),
  new RegExp(`\\b(?:in|for|during|since|until|from|to|through|between|and|or|calendar|fiscal|year|years)\\s+${YEAR}\\b(?![\\d,.%])`, "gi"),
];
export const givesFigure = (sentence) => /\p{Nd}/u.test(PERIODS.reduce((rest, period) => rest.replace(period, " "), sentence));

// The steps, each a key the page tracks and its plain words. A new step means the one before it is done.
export const STEPS = {
  understanding: "Understanding your question",
  reply: "Working out the reply",
  context: "Checking the client's terms",
  planning: "Planning the query",
  graph: "Looking at the knowledge graph",
  search: "Searching the catalogue",
  mapping: "Looking up the term",
  warehouse: "Running it on the warehouse",
  checking: "Checking the figures",
};
const TOOL_STEPS = { get_context: "context", ask: "planning", explore: "graph", explore_question: "graph", read_graph: "graph", search: "search", map_term: "mapping", retrieve: "search", assist: "planning" };

/** A step event: "reply" is the understanding step's second wording, after a tool step. */
export function stepEvent(key, toolCallId) {
  return { kind: "step", step: key === "reply" ? "understanding" : key, text: STEPS[key], ...(toolCallId ? { tool_call_id: String(toolCallId) } : {}) };
}
/** The step a tool call shows, or undefined for a tool with none. */
export const toolStep = (name, toolCallId) => (TOOL_STEPS[name] ? stepEvent(TOOL_STEPS[name], toolCallId) : undefined);

// The failures a turn can end in (#622 requirement 8): each case's plain sentence and the page's next step.
export const FAILURES = {
  not_planned: { action: "rephrase" },
  unsafe_query: { message: "The query couldn't be built safely.", action: "rephrase" },
  warehouse_failed: { message: "The warehouse query failed.", action: "try_again" },
  too_long: { message: "This took too long; try a narrower period or fewer groups.", action: "narrow" },
  kge_unreachable: { message: "Couldn't reach the knowledge graph.", action: "try_again" },
  meaning_unverified: { message: "Couldn't verify which meaning to use. Please try again.", action: "try_again" },
  model_failed: { message: "The assistant couldn't respond.", action: "try_again" },
  model_quota: { message: "You've reached the model usage limit. Please try again later.", action: "try_again" },
  // #651 A5: the model gave neither words nor a tool call; nothing blank is saved as an answer.
  empty_reply: { message: "The assistant gave no reply: try again.", action: "try_again" },
};

// KGE's own words for its limits and refusals (kge/adapters/warehouse/jobs.py and localjobs.py, the warehouse
// adapters' QueryRefused, kge/adapters/deadline.py's request budget, kge/pipeline/sqlguard.py's gate and dry run, and
// the ask engine's "SQL gate:" decline), matched on the text KGE gives. WAREHOUSE names the warehouse's own failures,
// never the bare word, which a refusal can use too.
const TOO_LONG = /time limit|time budget|took longer than|within its limits|ran out of time|bytes billed|maximum bytes|too large/i;
const UNSAFE = /SQL gate|gate refused|dry run failed|did not compile|could not compile/i;
const WAREHOUSE = /warehouse (?:execution |query |job )?(?:failed|error)|warehouse is (?:temporarily )?unavailable|warehouse did not|warehouse job could not|warehouse could not|BigQuery error|Catalog Error|Binder Error|Conversion Error|Constraint Error|Parser Error/i;

/**
 * The failure an error a KGE tool call gave is, or undefined for a refusal the model may answer in words. text is
 * KGE's message (KGE's own tool errors are "<status>: <detail>"); status the HTTP status where the call got one. The
 * gate's decline is unsafe_query whatever its status. Any other 4xx is a refusal (access, a bad argument, a request
 * too large, a tool the person may not use), and so is an error with no status, which is the MCP framework's own (an
 * argument that fails validation, an unknown tool). A 5xx or a 429 is the warehouse's failure or its time limit where
 * KGE's words say so, else kge_unreachable. A call that never got a reply is tools.js's own kge_unreachable.
 */
export function failureOf(text, status) {
  const said = String(text ?? "");
  const code = status ?? Number(/^(\d{3}):/.exec(said)?.[1]);
  if (UNSAFE.test(said)) return "unsafe_query";
  if (!(code >= 500 || code === 429)) return undefined;
  if (TOO_LONG.test(said)) return "too_long";
  if (WAREHOUSE.test(said)) return "warehouse_failed";
  return "kge_unreachable";
}

/** The failure an ask's reply (not an error) is: the gate's decline, a time limit, or a statement the warehouse failed. */
export function replyFailure(reply, warehouseFailed, answered) {
  const reason = typeof reply?.reason === "string" ? reply.reason : "";
  if (/^SQL gate\b/i.test(reason)) return "unsafe_query";
  if (answered) return undefined;
  if (/time limit|time budget|within its limits/i.test(reason)) return "too_long";
  return warehouseFailed ? "warehouse_failed" : undefined;
}

/** The outcome's error for a case: its plain sentence and action, and the raw reason for an engineer alone. */
export function errorOf(kind, { message, detail, engineer } = {}) {
  const known = FAILURES[kind] ?? FAILURES.model_failed;
  return { case: FAILURES[kind] ? kind : "model_failed", message: message ?? known.message, action: known.action, ...(engineer && detail ? { detail: String(detail).slice(0, 2000) } : {}) };
}
export const outcomeEvent = (outcome, steps, error) => ({ kind: "outcome", outcome, steps, ...(error ? { error } : {}) });

// The readout (#622 requirement 6): what an ask is computing, from its `context` event and its graph path. A value is
// kept only where it is plain words: no underscore, nothing shaped as schema.table or UPPER_SNAKE, no figure.
const PHYSICAL = /_|\b\w+\.\w+\b/;
export function plainWords(value) {
  if (typeof value !== "string") return undefined;
  const text = value.replace(/\s+/g, " ").trim();
  if (!text || text.length > 80 || PHYSICAL.test(text) || givesFigure(text)) return undefined;
  return text;
}
const MODES = { current: "Current", as_reported: "As reported", reported: "As reported", restated: "Restated", certified: "Certified" };
// A Words node is the words the question spelled; KGE records one for every term it finds, used or not, so a word is
// read only where the path matched it (a matches or plans edge) to what it means. Words matched to a Context, Measure,
// Term or Acronym name the measure; a used Filter or RowSelector is a rule the path applied.
const MEASURES = new Set(["Context", "Measure", "Term", "Acronym"]);
const RULES = new Set(["Filter", "RowSelector"]);
const MATCHED = new Set(["matches", "plans"]);
// A comparison may carry just one context even when its result uses several measures or bases. Do not present that
// single context as the whole calculation; the checked result card supplies the actual compared figures.
const COMPARISON = /\b(?:vs\.?|versus|compar(?:e|ed|ing|ison)|differences?|different\s+from)\b/i;
const escape = (text) => text.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");

/**
 * One ask call's readout, built as its events arrive: take(event) gives the readout when it has grown or changed,
 * else undefined. Words the path matched to the Calendar are the period; to a measure's node, the measure's own; to
 * any other node, its grouping ("by <words>" in the question) or its filters, beside the rules the path used. Words
 * the path matched to nothing are left out. The source is the business unit the path read.
 */
export function readoutOf(question) {
  const nodes = new Map();
  const matches = new Map();
  let context;
  let last = "";
  const asked = String(question ?? "");
  const comparison = COMPARISON.test(asked);
  // A node an edge names before its node_added arrives is known by its id's type ("Calendar:calendar").
  const nodeOf = (id) => nodes.get(id) ?? { id, type: String(id).split(":")[0] };
  const build = () => {
    const out = { filters: [], grouping: [] };
    const measure = plainWords(context?.label) ?? plainWords(context?.term)
      ?? plainWords([...nodes.values()].find((n) => n.type === "Measure" && n.used)?.label);
    if (measure && !comparison) out.measure = measure;
    const mode = MODES[context?.time_mode] ?? plainWords(typeof context?.time_mode === "string" ? context.time_mode.replace(/_/g, " ") : undefined);
    if (mode && !comparison) out.mode = mode;
    const periods = [];
    for (const words of [...nodes.values()].filter((n) => n.type === "Words")) {
      const targets = [...(matches.get(words.id) ?? [])].map(nodeOf);
      const phrase = plainWords(words.name ?? words.label);
      if (!phrase || !targets.length) continue;
      if (targets.some((t) => t.type === "Calendar")) periods.push(phrase);
      else if (targets.some((t) => MEASURES.has(t.type))) continue;
      else if (new RegExp(`\\bby\\s+(?:the\\s+)?${escape(phrase)}\\b`, "i").test(asked)) out.grouping.push(phrase);
      else out.filters.push(phrase);
    }
    for (const filter of [...nodes.values()].filter((n) => RULES.has(n.type) && n.used)) {
      const label = plainWords(filter.label);
      if (label && !out.filters.includes(label)) out.filters.push(label);
    }
    if (periods.length) out.period = periods.join(", ");
    const units = [...new Set([...nodes.values()].filter((n) => n.type === "Unit" && n.used && n.state !== "refused").map((n) => plainWords(n.label)).filter(Boolean))];
    if (units.length) out.source = units.join(", ");
    return out;
  };
  return {
    take(event) {
      if (!event || typeof event !== "object") return undefined;
      if (event.event === "context" && event.context && typeof event.context === "object") context = event.context;
      else if ((event.event === "node_added" || event.event === "state_changed") && event.node && typeof event.node.id === "string") {
        nodes.set(event.node.id, { ...nodes.get(event.node.id), ...event.node });
      } else if ((event.event === "edge_added" || event.event === "edge_changed") && event.edge && typeof event.edge.from === "string"
        && typeof event.edge.to === "string" && MATCHED.has(event.edge.type)) {
        matches.set(event.edge.from, new Set([...(matches.get(event.edge.from) ?? []), event.edge.to]));
      } else return undefined;
      const readout = build();
      if (!readout.measure && !readout.period && !readout.source && !readout.filters.length && !readout.grouping.length) return undefined;
      const now = JSON.stringify(readout);
      if (now === last) return undefined;
      last = now;
      return readout;
    },
  };
}

// #651 B3: the readout in words, above the card while the warehouse runs and in the thread after it: two to four plain
// sentences built here from the readout's fields, never by a model. Each field is already plain words (plainWords), and
// any sentence that still gives a figure (givesFigure) is dropped, so no number reaches the person unchecked. A readout
// with fewer fields gives fewer sentences; where one alone is left, READOUT_CLOSING follows it; one with none gives "".
export const READOUT_CLOSING = "The figures come from the checked query for this question.";
const listed = (items) => (items.length > 1 ? `${items.slice(0, -1).join(", ")} and ${items.at(-1)}` : items[0] ?? "");
const basis = (mode) => {
  const words = mode.toLowerCase().replace(/\s+/g, "-");
  return `${/^[aeiou]/.test(words) ? "an" : "a"} ${words} basis`;
};
export function readoutMessage(readout) {
  const r = readout && typeof readout === "object" ? readout : {};
  const text = (value) => (typeof value === "string" && value.trim() ? value.trim() : "");
  const words = (list) => (Array.isArray(list) ? list.map(text).filter(Boolean) : []);
  const [measure, mode, period, source] = [text(r.measure), text(r.mode), text(r.period), text(r.source)];
  const [grouping, filters] = [words(r.grouping), words(r.filters)];
  const sentences = [];
  if (measure) sentences.push(`This reads ${measure}.`);
  if (period) sentences.push(`It covers ${period}${mode ? ` on ${basis(mode)}` : ""}.`);
  else if (mode) sentences.push(`It is read on ${basis(mode)}.`);
  const shaped = [grouping.length ? `grouped by ${listed(grouping)}` : "", filters.length ? `filtered to ${listed(filters)}` : ""].filter(Boolean);
  if (shaped.length) sentences.push(`It is ${shaped.join(" and ")}.`);
  if (source) sentences.push(`The source is ${source}.`);
  const kept = sentences.filter((sentence) => !givesFigure(sentence));
  // Two sentences at least where there is one: a fixed one, with no figure, says where the figures come from.
  if (kept.length === 1) kept.push(READOUT_CLOSING);
  return kept.join(" ");
}
