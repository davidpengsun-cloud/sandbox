import { namesPeriodRange } from "./periods.js";

// The intent check (#707 requirement 9, #714): whether the output a question asks for is open (definitions, figures or
// both) is Cerebro's own check, made with Cerebro's request words below, the named exception to "no word lists in the
// portal" (#707 decision 10): the words that tell an open request, a request for definitions, a request for figures and
// a request for another output. Nothing else here is a word list. A grouping and a comparison are told from other
// words by KGE's reading alone (get_context's bindings and open items, meaning.js readingOf): "Show me … by X" does
// not open the output where KGE read X as a grouping, and a comparison KGE binds counts whatever its wording.
const OPEN_REQUEST = /\b(?:tell me about|show me|give me (?:information|details) (?:about|on)|(?:i (?:need|want)|provide) (?:some )?(?:information|details) (?:about|on))\b/i;
/** The open request that asks to be shown something, one of OPEN_REQUEST's own: with a grouping KGE read, or a period
 *  range, it asks for figures (#700). */
const SHOW_REQUEST = /\bshow me\b/i;
const DEFINITION = /\b(?:definition|definitions|define|meaning|meanings|means|defined)\b/i;
const FIGURE = /\b(?:how much|how many|what (?:was|were|is the value)|calculate|calculation|compute|computed|number|count|amount|amounts|figure|figures|value|values|total|totals|sum|sums|percent|percentage|dollar|dollars|ratio|share|trend|trends|growth|increase|decrease|change|variance|breakdown|grouped|per month|by month|by year|by quarter|year over year|month over month|quarter over quarter|year-on-year|month-on-month)\b/i;
const OTHER_OUTPUT = /\b(?:documents?|sources?|citations?|stewards?|owners?|tables?|columns?|lineage|relationships?|connections?|available data)\b/i;
const normalized = (s) => String(s ?? "").toLowerCase().replace(/[^\p{L}\p{N}]+/gu, " ").trim();

/** Whether the question is exactly one of the pack's golden questions, after the one normalisation (case, punctuation and
 *  spacing folded): a golden question is asked as written, with no card (#690, #697, owner 2026-11-27), decided once, at
 *  the one step before ask (agent.js). A rewording or a near match is not golden. */
export const isGoldenQuestion = (question, golden = []) => golden.some((example) => normalized(example) === normalized(question));

/** What KGE's reading says of the question's shape: whether it bound a comparison, and whether it read a grouping (a
 *  grouping it bound, or a grouping word it reports as naming declared dimensions). With no reading, neither. */
export const shapeOf = (reading) => ({
  comparison: Boolean(reading?.bindings?.some((binding) => binding.kind === "comparison")),
  grouping: Boolean(reading?.bindings?.some((binding) => binding.kind === "grouping") || reading?.items?.some((item) => item.item === "grouping")),
});

/**
 * Whether the output is open: an open request, or a comparison KGE bound, that names no definition, figure or other
 * output in Cerebro's request words. A "show me" request, comparing nothing, with a grouping KGE read or a period
 * range (periods.js) asks for figures (#700); "tell me about" and a comparison keep the question open. reading is
 * KGE's reading of the question (meaning.js readingOf), or undefined where there is none.
 */
export function needsIntentChoice(question, reading) {
  const text = typeof question === "string" ? question.trim() : "";
  const { comparison, grouping } = shapeOf(reading);
  if (!text || (!comparison && !OPEN_REQUEST.test(text)) || DEFINITION.test(text) || FIGURE.test(text) || OTHER_OUTPUT.test(text)) return false;
  return !(SHOW_REQUEST.test(text) && !comparison && (grouping || namesPeriodRange(text)));
}

/**
 * Whether the question asks, in Cerebro's request words, for definitions or another output (stewards, tables,
 * documents) and for no figure: such a question is not one the step before ask fills (#707 requirements 12 and 13:
 * the Period and grouping groups apply to figures, and the step is for a question bound for ask's figures), so it gets
 * no card and goes on to the model, which chooses its tool as before #714. A request word in a place KGE bound or
 * reports as open ("by source system", a grouping) is KGE's reading, not a request for another output. A question that
 * also asks for a figure in the request words keeps the step: the card is the fail-safe side. reading is KGE's reading
 * (meaning.js readingOf), or undefined where there is none.
 */
export function asksForNoFigure(question, reading) {
  const text = typeof question === "string" ? question : "";
  const read = [...(reading?.bindings ?? []).filter((binding) => binding.kind !== "unread"), ...(reading?.items ?? [])].filter((each) => each.span);
  const rest = read.reduce((left, { span }) => left.slice(0, span.start) + " ".repeat(span.end - span.start) + left.slice(span.end), text);
  return (DEFINITION.test(rest) || OTHER_OUTPUT.test(rest)) && !FIGURE.test(text);
}

export const INTENT_QUESTION = "What would you like to see?";
export const INTENT_OPTIONS = [
  { id: "definitions", label: "Show the definitions." },
  { id: "figures", label: "Show the figures." },
  { id: "both", label: "Show both the definitions and the figures." },
];

const LEAD = new RegExp(`^\\s*${OPEN_REQUEST.source}\\s+(.+?)[\\s?.!…]*$`, "is");
const bare = (text) => text.replace(/[\s?.!…]+$/, "");
const figuresFor = (subject) => `What was ${subject}?`;
const definitionFor = (subject) => `What is the definition of ${subject}?`;

/**
 * The question for one requested output, in the person's words: its shape alone changes, never its meaning (#707
 * decision 4). The words after an open request ("show me <subject>") are asked as "What was <subject>?" for figures
 * and "What is the definition of <subject>?" for definitions; a question with no open request is asked as it is for
 * figures, and its definition is asked of the whole of it. No word is added but the framing's own.
 */
export function framedQuestion(asked, kind) {
  const subject = asked.match(LEAD)?.[1];
  if (kind === "definitions") return definitionFor(subject ?? bare(asked));
  return subject ? figuresFor(subject) : asked;
}

/**
 * The questions for one requested output of a comparison whose second side leaves its term out ("<a> Q1 2026 <TERM>
 * versus <b>"), read from KGE's bindings alone: the whole question is, in order, a first side (a time mode or a term's
 * qualifier), at most one period, the term, the comparison and a second side, with an optional comparison word first
 * and nothing unread. Each side is then asked with the term the person wrote: one figures question, or a definition
 * question a side. Undefined for any other question.
 */
export function comparedQuestions(original, kind, reading) {
  const all = reading?.bindings ?? [];
  const bindings = all.filter((binding) => binding.span).sort((a, b) => a.span.start - b.span.start);
  if (!bindings.length || bindings.length !== all.length) return undefined;
  const parts = bindings[0].kind === "comparison" ? bindings.slice(1) : bindings;
  const side = (binding) => binding?.kind === "time_mode" || (binding?.kind === "term" && binding.by === "explicit");
  const [first, ...rest] = parts;
  const period = rest[0]?.kind === "period" ? rest.shift() : undefined;
  const [term, comparison, second, ...more] = rest;
  if (!side(first) || term?.kind !== "term" || comparison?.kind !== "comparison" || !side(second) || more.length) return undefined;
  if (bare(original).length !== second.span.end || original.slice(0, bindings[0].span.start).trim()) return undefined;
  const words = (binding) => original.slice(binding.span.start, binding.span.end);
  // A side that already holds the term's own word is no elided side: the question is asked as it is.
  const named = words(term).toLowerCase();
  if ([first, second].some((binding) => words(binding).toLowerCase().split(/[^\p{L}\p{N}]+/u).includes(named))) return undefined;
  const when = period ? ` for ${words(period)}` : "";
  const sides = [first, second].map((binding) => `${words(binding)} ${words(term)}`);
  if (kind === "definitions") return sides.map((each) => definitionFor(`${each}${when}`));
  return [figuresFor(`${sides[0]} ${words(comparison)} ${sides[1]}${when}`)];
}
