/* What Cerebro's chat reads from the agent's stream (cerebro.md §4.3, §7.1). The agent's `ask` tool returns KGE's
   Ask reply as its tool message; the chat shows that reply as Context's Ask shows it, the same card for the same role
   (#607), with its SQL for every role who may ask and its plan and trace for engineers only (#624; §2.1). The `get_context`
   tool returns KGE's compact context package; the chat shows its definitions and citable sources. Nothing here
   decides anything of its own: it reads what KGE returned and threads the conversation. */

import { getContentString } from "../../../components/agentchat";
import { periodWords } from "../../Context/Answer";
import { general, groupNames, resultNumber } from "../../Context/Result";
import {
  amount, answerModeLabel, businessName, declineText, figureMetadata, isAmount, plain, quarterWords, readablePeriods,
  realCurrency, stripAnswerLabel, withoutNoCurrency,
} from "../../Context/contextState";

export const ANSWER_TOOL = "ask";
export const CONTEXT_TOOL = "get_context";
export const FEEDBACK_URL = "/api/cerebro/ask/feedback";

/** True when the person holds Cerebro's engineer role. */
export const isEngineer = (roles) => Array.isArray(roles) && roles.includes("engineer");

/** A tool message's JSON object, when it is one tool's: content that is not a JSON object is null. */
export function toolObject(message, name) {
  if (!message || message.type !== "tool" || message.name !== name) return null;
  // A tool message's content is a string, or (MCP, LangChain) a list of text parts.
  let value = Array.isArray(message.content) ? getContentString(message.content) : message.content;
  if (typeof value === "string") {
    try {
      value = JSON.parse(value);
    } catch {
      return null;
    }
  }
  return value && typeof value === "object" && !Array.isArray(value) ? value : null;
}

/** The Ask reply an `ask` tool message carries, or null: content that is not a JSON object is no answer. */
export function answerOf(message) {
  const value = toolObject(message, ANSWER_TOOL);
  if (!value) return null;
  return typeof value.answer_text === "string" || value.receipt !== undefined ? value : null;
}

/** The fields of a reply that are the engineer's alone (§2.1): KGE sends them to engineers only; the card leaves them
 *  out for anyone else whatever arrives. The SQL is every role's (#624): the card shows it. */
const ENGINEER_FIELDS = ["plan", "reference_sql", "sql_run", "trace", "run_trace", "plan_vocabulary", "guard", "path"];
export function forRole(answer, engineer) {
  if (engineer || !answer) return answer;
  return Object.fromEntries(Object.entries(answer).filter(([key]) => !ENGINEER_FIELDS.includes(key)));
}

/** #621: whether a tool call's id is the server's framing get_context (agent.js isFrame): its terms are the model's to
 *  frame the question with, and the person sees no card for it. */
export const isFrameCall = (id) => typeof id === "string" && id.startsWith("frame-");

/** The context package a `get_context` tool message carries, or null (#621: none for the server's framing call). */
export function contextOf(message) {
  if (isFrameCall(message?.tool_call_id)) return null;
  const value = toolObject(message, CONTEXT_TOOL);
  return value && (typeof value.block === "string" || Array.isArray(value.sources)) ? value : null;
}

// A term line of the package's "Terms:" section: two spaces, the term, a colon, its definition, and " Steward: <who>."
// when one is recorded (KGE agent.py, define). Deeper lines are the term's certification, not its meaning.
const TERM_LINE = /^ {2}(?! )(.+?): (.*)$/;
const STEWARD = /\s*Steward: (.+?)\.\s*$/;
const NO_DEFINITION = "(no definition recorded)";

/** The package's definitions, [{ term, meaning, steward }]: from its "Terms:" section, or, where the block is a JSON
 *  list of contexts (the plan vocabulary's compact form), each context's label and definition. */
const fromContexts = (contexts) => (Array.isArray(contexts) ? contexts : [])
  .filter((c) => c && typeof c === "object" && (c.label || c.id))
  .map((c) => ({ term: String(c.label || c.id), meaning: typeof c.definition === "string" ? c.definition : "", steward: "" }));
export function definitionsOf(pkg) {
  if (Array.isArray(pkg?.plan_vocabulary?.contexts)) return fromContexts(pkg.plan_vocabulary.contexts);
  const block = typeof pkg?.block === "string" ? pkg.block : "";
  if (!block.trim()) return [];
  if (block.trim().startsWith("{")) {
    try {
      const parsed = JSON.parse(block);
      return fromContexts(parsed?.contexts);
    } catch {
      return [];
    }
  }
  const out = [];
  let inTerms = false;
  for (const line of block.split("\n")) {
    if (line === "Terms:") { inTerms = true; continue; }
    if (!inTerms) continue;
    if (!line.trim() || !line.startsWith(" ")) { inTerms = false; continue; }
    const found = TERM_LINE.exec(line);
    if (!found) continue;
    const rest = found[2];
    const steward = STEWARD.exec(rest)?.[1] || "";
    const meaning = rest.replace(STEWARD, "").trim();
    out.push({ term: found[1].trim(), meaning: meaning === NO_DEFINITION ? "" : meaning, steward });
  }
  return out;
}

/** The package's sources a person may be shown: the citable ones alone, each its number, document, title and version. */
export function citableSources(pkg) {
  return (Array.isArray(pkg?.sources) ? pkg.sources : [])
    .filter((s) => s && typeof s === "object" && s.citable === true && typeof s.doc === "string" && s.doc)
    .map((s) => ({ n: s.n, doc: s.doc, title: typeof s.title === "string" ? s.title : "", version: s.version }));
}

/** The tool call a tool message answers, from the assistant messages before it. */
export function callOf(messages, message) {
  const id = message?.tool_call_id;
  if (!id) return null;
  for (const m of messages || []) {
    const call = Array.isArray(m?.tool_calls) ? m.tool_calls.find((c) => c?.id === id) : null;
    if (call) return call;
  }
  return null;
}

// #619: the card names what it answered, from the full question the agent sent to Ask (its call's question, else the
// question KGE echoes): "Total retail revenue for May 2025 for region NW" reads "Retail revenue · May 2025 · region NW".
// The question's lead ("What was", "Total") is dropped, and each trailing "for", "in", "at", "as of", "during", "over" or
// "by" phrase that names a period or a filter is a part of its own; any other such phrase stays in the name before it,
// so "provision for credit losses" stays whole.
const TITLE_LEAD = /^(?:(?:what|how much)\s+(?:was|were|is|are|did|does|do)\s+|show(?:\s+me)?\s+|give\s+me\s+|tell\s+me\s+)?(?:the\s+)?(?:total\s+)?/i;
const TITLE_PART = /\s+(?=(?:for|in|at|as of|during|over|by)\s)/i;
const TITLE_JOIN = /^(?:for|in|at|as of|during|over)\s+/i;
const PERIOD_WORDS = /\b(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?|(?:19|20)\d{2}|FY\d{2,4}|[QH][1-4]|quarter|month|year|week|day|today|yesterday|current|latest|ytd|mtd|qtd)\b/i;
const FILTER_WORDS = /^(?:by\s|(?:region|segment|branch|unit|desk|channel|country|product|family|team|client|customer|household)s?\b)|\b[A-Z][A-Z0-9_-]{1,}\b/;
const partOfItsOwn = (phrase) => /^by\s/i.test(phrase) || PERIOD_WORDS.test(phrase) || FILTER_WORDS.test(phrase.replace(TITLE_JOIN, ""));
export function titleOf(question) {
  const q = String(question ?? "").replace(/\s+/g, " ").trim().replace(/[\s?.!…]+$/u, "");
  if (!q || hasInternalName(q)) return "";
  const pieces = q.replace(TITLE_LEAD, "").split(TITLE_PART);
  let at = pieces.length;
  while (at > 1 && partOfItsOwn(pieces[at - 1])) at -= 1;
  const parts = [pieces.slice(0, at).join(" "), ...pieces.slice(at).map((part) => part.replace(TITLE_JOIN, ""))].map((part) => part.trim()).filter(Boolean);
  if (!parts.length) return "";
  parts[0] = parts[0].charAt(0).toUpperCase() + parts[0].slice(1);
  return parts.join(" · ");
}

/** What a card answered: the title of the question the agent sent to Ask (its call's), else of the reply's question.
 *  Where the reply's question holds the call's (a clarification's pick, which KGE restates in full), the reply's. */
export function answeredAs(answer, call) {
  const sent = typeof call?.args?.question === "string" ? call.args.question.trim() : "";
  const echoed = typeof answer?.question === "string" ? answer.question.trim() : "";
  const fuller = echoed && (!sent || (echoed.length > sent.length && echoed.toLowerCase().includes(sent.toLowerCase())));
  return titleOf(fuller ? echoed : sent);
}

/** The ask calls of the turn under way (since the person's last question) with no tool message back yet. An earlier
 *  turn's call left unanswered (a stop, a failed stream) is never on its way. */
export function pendingAsks(messages) {
  const all = messages || [];
  const turn = all.slice(all.map((m) => m?.type).lastIndexOf("human") + 1);
  const answered = new Set(turn.filter((m) => m?.type === "tool" && m.tool_call_id).map((m) => m.tool_call_id));
  return turn.flatMap((m) => (m?.type === "ai" && Array.isArray(m.tool_calls) ? m.tool_calls : []))
    .filter((c) => c?.name === ANSWER_TOOL && c.id && !answered.has(c.id))
    .map((c) => c.id);
}

/** The person's questions in a conversation, in order, each once, for the typeahead's "You asked before". */
export function pastQuestions(messages) {
  const seen = new Set();
  return (messages || []).filter((m) => m?.type === "human").map((m) => getContentString(m.content).trim())
    .filter((q) => q && !seen.has(q.toLowerCase()) && seen.add(q.toLowerCase()))
    .map((question) => ({ question }));
}

/* #624: one card for every question back to the person. The portal's `choose` tool call (never sent to KGE) carries
   it: a clarification's choices (source "clarify"), the agent's own question (source "agent"), or, after a difference
   answer, the offer to compare the figures (source "offer"). Its tool message is the person's answer: { picked: { id,
   label } }, { other: text } or { error }. */
export const CHOOSE_TOOL = "choose";

/** A message's choose calls, in order. */
export const chooseCalls = (message) => (message?.type === "ai" && Array.isArray(message.tool_calls) ? message.tool_calls : [])
  .filter((c) => c?.name === CHOOSE_TOOL && c.id);

/** #713: a multiple-question card's groups as the card shows them: [{ id, label, kind, options: [{ id, label, when? }] }],
 *  each option's `when` ({ group, options }) naming the earlier pick it is valid for. */
const groupsOf = (groups) => (Array.isArray(groups) ? groups : []).map((g) => (g && typeof g === "object" && typeof g.id === "string" && typeof g.label === "string" ? {
  id: g.id,
  label: g.label,
  kind: typeof g.kind === "string" ? g.kind : "",
  options: (Array.isArray(g.options) ? g.options : []).filter((o) => o && typeof o.id === "string" && typeof o.label === "string").map((o) => ({
    id: o.id,
    label: o.label,
    ...(o.when && typeof o.when.group === "string" && Array.isArray(o.when.options) ? { when: { group: o.when.group, options: o.when.options.filter((x) => typeof x === "string") } } : {}),
  })),
} : null)).filter(Boolean);

/** A choose call's or a hitl interrupt's question as the card shows it: { source, question, options: [{ id, label }],
 *  months }, and for a multiple-question card (#713, source "questions") its groups. An option given as a string (the
 *  agent's) is numbered from 1, as the server numbers it. */
export function chooseOf(value) {
  const v = value && typeof value === "object" ? value : {};
  const options = (Array.isArray(v.options) ? v.options : []).map((o, i) => (typeof o === "string" ? { id: String(i + 1), label: o }
    : o && typeof o === "object" && typeof o.label === "string" ? { id: String(o.id ?? i + 1), label: o.label } : null)).filter(Boolean);
  const months = (Array.isArray(v.months) ? v.months : []).filter((m) => typeof m === "string" && /^\d{4}-\d{2}$/.test(m));
  const source = ["clarify", "agent", "offer", "suggest", "meaning", "questions"].includes(v.source) ? v.source : "agent";
  const question = typeof v.question === "string" && v.question.trim() ? v.question : "Which do you mean?";
  if (source === "questions") return { source, question, options, months, groups: groupsOf(v.groups) };
  return { source, question, options, months };
}

/** #713: a multiple-question card's answer, its picks as { group id: option id }, from the choose call's result. */
export const picksOf = (chosen) => (Array.isArray(chosen?.picks)
  ? Object.fromEntries(chosen.picks.filter((p) => p && typeof p.group === "string" && typeof p.id === "string").map((p) => [p.group, p.id])) : undefined);

/** The person's answer to a choose call, from its tool message: { picked: { id, label } }, { other }, { error }, or null
 *  while none has come back. */
export function chosenOf(messages, callId) {
  const message = (messages || []).find((m) => m?.type === "tool" && m.tool_call_id === callId && m.name === CHOOSE_TOOL);
  if (!message) return null;
  return toolObject(message, CHOOSE_TOOL) || { error: "The answer could not be read." };
}

/** The person's answer as the card shows it, "You: …": the option's label, every pick of a multiple-question card
 *  ("Meaning: …; Segment: …"), or the words they typed. */
export function chosenText(chosen) {
  if (chosen?.cancelled === true) return "Cancelled";
  if (typeof chosen?.picked?.label === "string") return chosen.picked.label;
  if (Array.isArray(chosen?.picks)) return chosen.picks.filter((p) => p && typeof p.label === "string" && typeof p.option === "string").map((p) => `${p.label}: ${p.option}`).join("; ");
  if (typeof chosen?.other === "string") return chosen.other;
  return "";
}

/** True when a clarification's question back to the person is the choose call that follows its reply: its card is
 *  then that question, and the reply is no answer card of its own. */
export function askedBack(messages, message) {
  const id = message?.tool_call_id;
  return Boolean(id) && (messages || []).some((m) => chooseCalls(m).some((c) => c.args?.ask_call === id));
}

/** "June 2025" of "2025-06". */
export function monthWords(month) {
  const found = /^(\d{4})-(\d{2})$/.exec(String(month ?? ""));
  if (!found) return String(month ?? "");
  return new Date(Date.UTC(Number(found[1]), Number(found[2]) - 1, 15)).toLocaleDateString("en-US", { month: "long", year: "numeric", timeZone: "UTC" });
}

/** The display block Cerebro's card renders as Context's rich answer (#602), or null where the answer has no table. */
export function richOf(answer) {
  const display = answer?.display;
  return display && typeof display === "object" && !display.error && display.table && Array.isArray(display.table.columns)
    && Array.isArray(display.table.rows) ? display : null;
}

// Text as compared for a repeat: lower case, markdown emphasis and quotes dropped, spacing collapsed, an answer label
// ("Certified:", "AI-Generated:") taken off its front.
const LABEL = /^(?:certified|ai-generated|the model's reading|not sure)\s*[:.—–·-]\s*/;
const squashed = (text) => String(text ?? "").toLowerCase().replace(/[*_`"“”]+/g, "").replace(/\s+/g, " ").trim().replace(LABEL, "");
const sentences = (line) => line.split(/(?<=[.!?])\s+(?=\S)/);
// What is left of a line once its repeats are gone, when it says nothing of its own: a label, or a lead-in ("Here it is:").
const leftover = (line) => !/[\p{L}\p{N}]/u.test(line) || LABEL.test(`${squashed(line)}:`) || (/:\s*$/.test(line) && line.trim().length <= 40);

/**
 * The agent's text without what repeats an answer card of its turn (#602: the card is the answer). texts are those
 * cards' texts; a sentence of the agent's that is all one card's words (case, spacing, markdown and its label aside) is
 * a repeat. The agent's own sentences, and its line breaks, stay; "" where nothing of its own is left.
 */
export function framingOf(text, texts) {
  const original = String(text ?? "");
  const cards = (texts || []).map(squashed).filter((t) => t.length >= 8);
  if (!cards.length || !original.trim()) return original.trim() ? original : "";
  const repeat = (sentence) => {
    const plain = squashed(sentence);
    return plain.length >= 8 && cards.some((card) => card.includes(plain));
  };
  let changed = false;
  const lines = original.split("\n").map((line) => {
    const all = sentences(line);
    const kept = all.filter((sentence) => !repeat(sentence));
    if (kept.length === all.length) return line;
    changed = true;
    const out = kept.join(" ");
    return leftover(out) ? "" : out;
  });
  if (!changed) return original;
  const own = lines.join("\n").replace(/\n{3,}/g, "\n\n").trim();
  return own.split("\n").every((line) => !line.trim() || leftover(line)) ? "" : own;
}

/**
 * For each assistant message, by its id, the texts of the answer cards of its own turn (the messages since the
 * person's last question): what that message must not repeat. A card of an earlier turn is no repeat, so a recap the
 * person asked for stays.
 */
export function turnCards(messages, strip = (text) => text, cardTexts = () => []) {
  const out = new Map();
  let turn = [];
  for (const message of messages || []) {
    if (message?.type === "human") { turn = []; continue; }
    const answer = answerOf(message);
    if (answer && typeof answer.answer_text === "string") {
      turn = [...turn, answer.answer_text, strip(answer.answer_text, answer.answer_mode)];
      const headline = richOf(answer)?.headline?.text;
      if (typeof headline === "string") turn.push(headline);
    } else if (message?.type === "ai" && message.id != null) {
      out.set(message.id, turn);
    } else if (message?.type === "tool") {
      // #622: a non-figure card's own words count as well (cardTexts, chatTools.toolCardTexts).
      turn = [...turn, ...cardTexts(message)];
    }
  }
  return out;
}

/** The last answer's graph_path in a conversation, for the shared state's highlighted path (§7.4). */
export function lastPathOf(messages) {
  for (let i = (messages || []).length - 1; i >= 0; i -= 1) {
    const answer = answerOf(messages[i]);
    // KGE sends { nodes, edges, unused } (contextpath.py); an array is kept as given.
    if (answer) return answer.graph_path && typeof answer.graph_path === "object" ? answer.graph_path : null;
  }
  return null;
}

/** POST the feedback body, as Context's Ask does: { receipt, verdict }. Resolves to { error } when it was not recorded. */
export async function postFeedback(body, { url = FEEDBACK_URL, fetcher = globalThis.fetch } = {}) {
  try {
    const response = await fetcher(url, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(body),
    });
    if (response.ok) return {};
    let detail = "";
    try {
      detail = (await response.json())?.detail;
    } catch {
      /* A reply with no JSON body. */
    }
    return { error: typeof detail === "string" && detail ? detail : "the service refused it." };
  } catch {
    return { error: "The service is unavailable right now. Try again shortly." };
  }
}

/* #613: the compact card. By default the chat shows an answer's label, its figure, or its table or chart, and its
   citations, and nothing else; Context's full card (notes, definitions, the complete text) is one click away under
   "More". The compact parts come from KGE's structured fields; answer_text is read only where a reply has nothing
   structured, and then only as readableText leaves it. */

// An internal name: a snake_case token (NNA_AM, amount_usd, F_NNA_FIN.NNA_AM, office.region_code) or an upper-case
// dotted one with two characters or more each side of its dot (SALES.AMT). Never part of an e-mail address, a path or a
// URL, and never a plain word, number, file name or abbreviation (example.com, nna-policy.pdf, 1.5, e.g., U.S., J.P.).
const IDENT = String.raw`(?<![\w.@/:-])(?:[A-Za-z][A-Za-z0-9]*_[A-Za-z0-9_]*(?:\.[A-Za-z][A-Za-z0-9_]*)*|[A-Za-z][A-Za-z0-9_]*\.[A-Za-z][A-Za-z0-9]*_[A-Za-z0-9_]*(?:\.[A-Za-z][A-Za-z0-9_]*)*|[A-Z][A-Z0-9_]+\.[A-Z][A-Z0-9_]+)(?![\w@/-]|\.[A-Za-z0-9_])`;
const IDENT_RE = new RegExp(IDENT);
const DOTTED = /(?<![\w.@/-])[A-Za-z][\w$]*\.[A-Za-z][\w$]*(?![\w@/-])/;
/** True where text holds an internal name. */
export const hasInternalName = (text) => IDENT_RE.test(String(text ?? ""));
// A parenthesised source: "(wealth · F_NNA_FIN.NNA_AM)", "(wealth · flow.amount_usd; basis: as reported ...)",
// "(nna.amount)". A parenthesis of words, "(Q1 · 2025)" or "(withheld)", stays.
const PAREN = /\s*\(([^()]*)\)/g;
const sourceParen = (inner) => IDENT_RE.test(inner) || (/·/.test(inner) && DOTTED.test(inner)) || /^\s*[a-z][\w$]*\.[a-z][\w$]*\s*$/.test(inner);
// A Markdown pipe table's separator row: | --- | :---: |.
const SEPARATOR = /^\s*\|?\s*:?-{3,}:?\s*(?:\|\s*:?-{3,}:?\s*)+\|?\s*$/;
const cellsOf = (line) => line.trim().replace(/^\|/, "").replace(/(?<!\\)\|$/, "").split(/(?<!\\)\|/).map((c) => c.trim().replace(/\\\|/g, "|"));

/** A text's Markdown pipe tables, taken out: { text, tables: [{ columns: [label], rows: [[cell]] }] }. */
export function pipeTables(text) {
  const lines = String(text ?? "").split("\n");
  const kept = [];
  const tables = [];
  for (let i = 0; i < lines.length; i += 1) {
    if (lines[i].includes("|") && SEPARATOR.test(lines[i + 1] ?? "")) {
      const columns = cellsOf(lines[i]);
      const rows = [];
      i += 2;
      while (i < lines.length && lines[i].includes("|") && lines[i].trim()) { rows.push(cellsOf(lines[i])); i += 1; }
      i -= 1;
      tables.push({ columns, rows });
    } else {
      kept.push(lines[i]);
    }
  }
  return { text: kept.join("\n"), tables };
}

/** The terms whose definitions an answer appends to its text (KGE wording.definition_lines): "Term: definition". */
const appendedTerms = (a) => [a?.term, a?.context?.term, ...(Array.isArray(a?.certified_definitions) ? a.certified_definitions.map((d) => d?.term) : [])]
  .filter((t) => typeof t === "string" && t.trim());
const appended = (line, terms) => terms.some((t) => line.trim().toLowerCase().startsWith(`${t.toLowerCase()}: `));
// A sentence's end: not the dot of a one-letter abbreviation (U.S., J.P. Morgan, A.M.).
const SENTENCE_END = /(?<=[.!?])(?<!(?:^|[^\w])[A-Za-z]\.)\s+(?=\S)/;
// Lines that are provenance, not the answer: what a definition cites, and where a figure came from (KGE outside_sources).
const PROVENANCE = /^(?:Cited|Not cited|Cites|Source):\s|^From .*, the certified sources? for /;

/**
 * answer_text as a person may read it on the compact card: its label (the badge's) off its front, no Markdown pipe
 * table, no appended glossary definition after the first line, no provenance line, no parenthesised source, and no
 * sentence that still names a table, column or unit by its id. Whatever is taken out is in the full answer under More.
 */
export function readableText(text, a = {}, { sentences = true } = {}) {
  const terms = appendedTerms(a);
  const { text: prose } = pipeTables(stripAnswerLabel(String(text ?? ""), a.answer_mode));
  // A definition's own text is its definition; any other answer's "Term: definition" line is appended to it.
  const own = DEFINITIONS.includes(a.behaviour);
  // The first line is the answer itself ("Assets under management: USD 12.00 [1]"), unless it is a term followed by a
  // definition the answer carries (a pipe table taken out from before it).
  const known = [a.definition, ...(Array.isArray(a.certified_definitions) ? a.certified_definitions.map((d) => d?.definition) : [])]
    .filter((d) => typeof d === "string" && d.trim()).map((d) => d.trim().toLowerCase());
  const definitionLine = (line) => { const at = line.indexOf(": "); const rest = line.slice(at + 2).trim().toLowerCase(); return at > 0 && known.some((d) => rest.startsWith(d)); };
  const lines = prose.split("\n").filter((line, i) => !(!own && appended(line, terms) && (i > 0 || definitionLine(line))) && !PROVENANCE.test(line.trim()));
  return lines.map((line) => {
    const cleaned = withoutNoCurrency(readablePeriods(line.replace(PAREN, (whole, inner) => (sourceParen(inner) ? "" : whole))))
      .replace(/\s+([.,;:])/g, "$1");
    if (!sentences) return cleaned;
    return cleaned.split(SENTENCE_END).filter((sentence) => !IDENT_RE.test(sentence)).join(" ");
  }).filter((line) => line.trim()).join("\n").trim();
}

/** The lead of a figure's text, before its value: "Net new assets for March 2025" of "…: USD 5,929,714.83 (…).". */
function leadOf(text, a) {
  const first = readableText(text, a).split("\n")[0];
  const at = first.indexOf(": ");
  return at > 0 && /\d/.test(first.slice(at)) ? first.slice(0, at) : first;
}

const DECLINE_BADGES = { refuse: "Refused", blocked_access: "No access", abstain: "Cannot answer", abstain_with_reason: "Cannot answer", abstain_route_steward: "Sent to a steward", clarify: "Clarify" };
const DEFINITIONS = ["glossary_definition", "cite_certified", "cite"];
const FIGURES = ["figure", "sum_with_verdict", "sum_without_verdict"];
const isDeclined = (b) => b === "refuse" || b === "blocked_access" || String(b ?? "").startsWith("abstain");

/** The badge: the answer's mode label as Context shows it, else what a decline is. */
function badgeOf(a) {
  const mode = answerModeLabel(a);
  if (mode) return { text: mode, tone: a.answer_mode === "certified" ? "ok" : "warn", mode: a.answer_mode };
  const text = DECLINE_BADGES[a.behaviour] || "";
  return text ? { text, tone: ["refuse", "blocked_access"].includes(a.behaviour) ? "bad" : "warn" } : null;
}

/** The citations as a person reads them: each its marker and title, or its document where it has no title. */
function citationsOf(a) {
  const listed = (Array.isArray(a.citations) ? a.citations : []).filter((c) => c && typeof c === "object" && (c.title || c.doc))
    .map((c) => ({ n: c.n ?? null, text: String(c.title || c.doc) }));
  if (listed.length) return listed;
  const docs = Array.isArray(a.cited_docs) ? a.cited_docs.map((d) => d?.doc) : Array.isArray(a.cite) ? a.cite : [];
  return docs.filter((d) => typeof d === "string" && d).map((doc) => ({ n: null, text: doc }));
}

// Display-table cells as Context's rich answer shows them (Answer.js, Rich).
function displayTable(display) {
  const { table, chart } = display;
  const keys = table.columns.map((c) => c.key);
  const ci = keys.indexOf("currency");
  const shown = (value, c, row) => resultNumber(value, c.key === "__rank__" ? { ...c, kind: "count" } : { ...c, amount: Boolean(realCurrency(c.currency) || (ci >= 0 && realCurrency(row[ci]))) });
  const cell = (row, c, j, i) => (table.total_rows?.includes(i) && row[j] == null ? "" : c.type === "number" ? shown(row[j], c, row)
    : c.type === "cite" && row[j] ? `[${row[j]}]` : quarterWords(String(row[j] ?? "")));
  const numeric = table.columns.map((c) => c.type === "number");
  const rows = table.rows.map((row, i) => table.columns.map((c, j) => cell(row, c, j, i)));
  if (table.total) {
    const first = table.columns.find((c) => c.type === "number") || {};
    rows.push([table.total.label, shown(table.total.value, first, table.rows[0] || []), ...table.columns.slice(2).map(() => "")]);
  }
  const caption = readableText(readablePeriods(table.caption), {});
  // #626: the side panel sorts the full table on KGE's own values (keys), its total row kept last (total).
  const out = { caption, columns: table.columns.map((c) => readableText(businessName(c.label), {}) || plain(c.key)), numeric, rows, keys: table.rows, ...(table.total ? { total: true } : {}) };
  if (chart) {
    const xi = keys.indexOf(chart.x), yi = keys.indexOf(chart.y);
    const bars = table.rows.map((row) => ({ label: quarterWords(String(row[xi] ?? "")), value: row[yi], text: Number.isFinite(row[yi]) ? shown(row[yi], table.columns[yi], row) : "" }))
      .filter((r) => Number.isFinite(r.value)).slice(0, chart.max_bars ?? table.rows.length);
    if (bars.length) return { chart: { bars, of: table.rows.length }, table: out };
  }
  return { table: out };
}

/**
 * What the compact card shows of an Ask reply: { kind, badge, label, figure?, table?, chart?, result?, text?,
 * definitions?, choices?, citations }. kind is figure, table, chart, decline, clarification, definition or text. Every
 * string is readable: built from KGE's structured fields, with answer_text read only through readableText.
 */
export function compactOf(answer, title = "") {
  const named = compactKind(answer);
  // #619: a figure is named by what it answered (the question's title, with the reading's time mode), a table or a
  // chart by its headline where it has one; never "The figure".
  const bare = !named.label || /^the figure\b/i.test(named.label);
  if (named.kind === "figure" && title) return { ...named, label: withTimeMode(title, answer) };
  if (["figure", "table", "chart"].includes(named.kind) && bare) {
    const term = answer?.context?.term || answer?.term;
    return { ...named, label: title || titleOf(answer?.question) || (typeof term === "string" ? plain(term) : "") };
  }
  return named;
}

const TIME_MODES = { as_reported: "as reported", current: "current" };
function withTimeMode(title, answer) {
  const mode = TIME_MODES[answer?.context?.time_mode];
  return mode && !title.toLowerCase().includes(mode) ? `${title} · ${mode}` : title;
}

function compactKind(answer) {
  const a = answer && typeof answer === "object" ? answer : {};
  const b = a.behaviour;
  const base = { badge: badgeOf(a), citations: citationsOf(a) };
  if (a.error) return { ...base, kind: "decline", badge: { text: "Not answered", tone: "bad" }, label: readableText(a.error, a) };
  if (b === "clarify") {
    const choices = (a.clarification?.choices || []).filter((c) => c && typeof c.label === "string").map((c) => ({ id: c.id, label: c.label }));
    return { ...base, kind: "clarification", label: readableText(a.reason, a) || readableText(a.answer_text, a) || "Which do you mean?", choices };
  }
  if (isDeclined(b)) {
    const reason = readableText(declineText(a.reason, a.question), a);
    return { ...base, kind: "decline", label: reason || readableText(a.answer_text, a) || "No answer is given." };
  }
  if (DEFINITIONS.includes(b)) {
    const certified = (Array.isArray(a.certified_definitions) ? a.certified_definitions : []).filter((d) => d && d.term && d.definition);
    let definitions = b === "glossary_definition" && a.definition ? [{ term: a.term || certified[0]?.term || "", text: a.definition }]
      : certified.map((d) => ({ term: d.term, text: d.definition }));
    if (!definitions.length && b !== "cite") {
      // A definition with no structured text: its first line, "Term: definition", as KGE words it.
      const first = readableText(String(a.answer_text ?? "").split("\n")[0], { answer_mode: a.answer_mode });
      const at = first.indexOf(": ");
      definitions = first ? [at > 0 && at < 60 ? { term: first.slice(0, at), text: first.slice(at + 2) } : { term: a.term || "", text: first }] : [];
    }
    // A definition's own sentences are the steward's words: no sentence of them is dropped.
    definitions = definitions.map((d) => ({ term: readableText(d.term, {}), text: readableText(d.text, {}, { sentences: false }) })).filter((d) => d.text);
    const label = definitions.length === 1 ? definitions[0].term || plain(a.term) : a.term ? plain(a.term) : "";
    if (!definitions.length) return { ...base, kind: "text", label, text: readableText(a.answer_text, a) };
    return { ...base, kind: "definition", label, definitions };
  }
  const about = figureMetadata(a);
  const lead = leadOf(a.answer_text, a);
  const display = richOf(a);
  if (display) {
    const headline = readableText(String(stripAnswerLabel(display.headline?.text ?? "", a.answer_mode)).split("\n")[0], a);
    const shaped = displayTable(display);
    return { ...base, kind: shaped.chart ? "chart" : "table", label: headline || shaped.table.caption || lead, ...shaped };
  }
  if (general(a)) return { ...base, kind: "table", label: lead, result: a.result };
  const term = a.context?.term || a.trace?.[0]?.term;
  const figureLabel = term ? [term, periodWords(a), a.context?.time_mode === "as_reported" ? "as reported" : a.context?.time_mode === "current" ? "current" : ""].filter(Boolean).join(" · ") : lead;
  if (FIGURES.includes(b) && a.rows && typeof a.rows === "object") {
    const amounted = isAmount(about);
    const names = groupNames(a);
    const rows = Object.entries(a.rows).map(([name, value]) => [names(name) ?? quarterWords(name), `${amounted ? `${about.currency} ` : ""}${amount(value, about)}`]);
    return { ...base, kind: "table", label: lead || readableText(a.question, {}), table: { caption: "", columns: [businessName(a.group_by) || "Group", "Figure"], numeric: [false, true], rows } };
  }
  if (b === "combined" && Array.isArray(a.figures) && a.figures.length) {
    const valueOf = (f) => { const m = { currency: f.currency, kind: f.kind }; return { currency: isAmount(m) ? f.currency : "", value: amount(f.value, m) }; };
    const nameOf = (f) => readableText(f.label, {}) || plain(f.term);
    if (a.figures.length === 1) return { ...base, kind: "figure", label: nameOf(a.figures[0]), figure: valueOf(a.figures[0]) };
    const rows = a.figures.map((f) => { const v = valueOf(f); return [nameOf(f), `${v.currency ? `${v.currency} ` : ""}${v.value}${f.cite ? ` [${f.cite}]` : ""}`]; });
    return { ...base, kind: "table", label: lead, table: { caption: "", columns: ["Figure", "Value"], numeric: [false, true], rows } };
  }
  if (FIGURES.includes(b) || (b === undefined && typeof a.figure === "number")) {
    const amounted = isAmount(about);
    return { ...base, kind: "figure", label: figureLabel, figure: a.figure == null ? { currency: "", value: "No value" } : { currency: amounted ? about.currency : "", value: amount(a.figure, about) } };
  }
  if (b === "parts_with_reason" && a.parts && typeof a.parts === "object" && !Array.isArray(a.parts)) {
    const parts = about.parts || [];
    const rows = Object.entries(a.parts).map(([unit, value], i) => [plain(unit), `${isAmount(parts[i]) ? `${parts[i].currency} ` : ""}${amount(value, parts[i] || {})}`]);
    return { ...base, kind: "table", label: lead, table: { caption: "", columns: ["Unit", "Figure"], numeric: [false, true], rows } };
  }
  // Anything else, and a reply with no behaviour: its text, made readable, with a pipe table as a table.
  const { tables } = pipeTables(stripAnswerLabel(String(a.answer_text ?? ""), a.answer_mode));
  const text = readableText(a.answer_text, a);
  if (tables.length) {
    const t = tables[0];
    const table = { caption: "", columns: t.columns.map((c) => readableText(businessName(c), {}) || c), numeric: t.columns.map((_, j) => t.rows.length > 0 && t.rows.every((r) => /^[A-Z]{3} -?[\d,.]+$|^-?[\d,.]+%?$/.test(r[j] ?? ""))), rows: t.rows.map((r) => r.map((c) => readableText(c, {}) || "")) };
    return { ...base, kind: "table", label: text, table };
  }
  return { ...base, kind: "text", label: "", text };
}
