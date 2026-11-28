import { DEFAULT_CALLS_BEFORE_ASK } from "../apps.js";
import { asksForNoFigure, needsIntentChoice } from "./intent.js";
import { meaningGroup, openMeanings, optionsOf, readingOf } from "./meaning.js";

// The one step before ask (#707 requirements 12 to 14, #714). For a new question Cerebro asks KGE how it reads it
// (get_context with open items, with the conversation's history, as ask will read it), settles what KGE's tools can
// within the operators' limit on tool calls, and says what is left: KGE's refusal, a period with no data, the open
// groups of the one card, or nothing. No model call is made here, and nothing here is a word list: every fact is KGE's,
// but for whether the output is open, which is Cerebro's own check (intent.js).

/** #707 decision 9: the default limit on Cerebro's KGE tool calls before ask (PORTAL_CEREBRO_CALLS_BEFORE_ASK, apps.js). */
export { DEFAULT_CALLS_BEFORE_ASK };
/** The Period group offers the latest covered periods, this many (#707 requirement 12). */
export const PERIODS_OFFERED = 3;
export const REFUSED = "You may not read the data this question needs.";
const NO_DATA = "There is no data for that period.";
/** The longest of KGE's own words the portal says for a refusal or a period it cannot answer. */
export const SAID_MAX = 600;

const SHOW_GROUP = { id: "show", label: "Show", kind: "show", options: [
  { id: "definitions", label: "Definitions", words: "definitions" }, { id: "figures", label: "Figures", words: "figures" },
  { id: "both", label: "Both", words: "both" }] };

/** KGE's tools for the step, counted: each call spends one of the limit, and once it is spent a call gives undefined,
 *  as a failed call does; refused() is how many calls the spent limit turned away. */
function counted(call, limit, history) {
  let left = limit;
  let refused = 0;
  const spend = async (run) => {
    if (left <= 0) {
      refused += 1;
      return undefined;
    }
    left -= 1;
    const result = await run();
    return result && !result.error && !result.failure ? result.value : undefined;
  };
  return {
    reading: (question) => spend(() => call.reading(question, history)),
    meanings: async (name) => {
      const value = await spend(() => call.meanings(name));
      return value && optionsOf(value.resolves_to);
    },
    filters: async (phrase) => {
      const value = await spend(() => call.filters(phrase));
      return value && (Array.isArray(value.hits) ? value.hits : []).filter((hit) => hit?.kind === "filter" && !hit.restricted && typeof hit.title === "string").map((hit) => hit.title);
    },
    spent: () => limit - left,
    refused: () => refused,
  };
}

/** What Cerebro says where KGE reports a period with no data: KGE's own reason where it gives one, else that there is
 *  no data for the period, in the person's own words for it where KGE read one period; and the latest covered period
 *  by name. */
function noData(item, reading) {
  const periods = reading.bindings.filter((binding) => binding.kind === "period" && binding.span);
  const said = item.reason ?? (periods.length === 1 ? `There is no data for ${periods[0].text}.` : NO_DATA);
  return item.latest && !said.includes(item.latest) ? `${said} The latest covered period is ${item.latest}.` : said;
}

const ID = /^[\w.:-]{1,64}$/;

/** The Period group: the latest covered periods KGE lists, newest first. */
function periodGroup(item) {
  const options = item.options.filter((option) => ID.test(option.value ?? "")).slice(0, PERIODS_OFFERED);
  return options.length ? { id: "period", label: "Period", kind: "period", options: options.map((option) => ({ id: option.value, label: option.label, words: option.label })) } : undefined;
}

/**
 * A grouping group for a grouping word KGE reports as naming several declared dimensions (one dimension is nothing to
 * choose between, and makes no group): every dimension it reports, with no cap (#707 decision 20), under the person's
 * own word as the group's label. A pick puts a phrase KGE says names the dimension in the word's place, one that keeps
 * the person's own word where KGE gives one. The label is KGE's; the words are a phrase ask reads, which the label
 * itself need not be.
 */
function groupingGroup(question, item, at, reading) {
  if (!item.span || item.options.length < 2) return undefined;
  // The word's own place: a mark KGE reports with it ("-<word>") stays in the question.
  const lead = question.slice(item.span.start, item.span.end).search(/[\p{L}\p{N}]/u);
  if (lead < 0) return undefined;
  const word = { start: item.span.start + lead, end: item.span.end };
  const before = question.slice(0, word.start).match(/([\p{L}\p{N}]+)\s*$/u)?.[1]?.toLowerCase();
  const said = question.slice(word.start, word.end).trim().toLowerCase();
  const naming = (phrases) => phrases.find((phrase) => phrase.toLowerCase().split(/\s+/).includes(said)) ?? phrases[0];
  const phrasesOf = (option) => (option.phrases?.length ? option.phrases : [option.label]);
  const ledBy = (option) => (before ? phrasesOf(option).filter((phrase) => phrase.toLowerCase().startsWith(`${before} `)) : []);
  // Where every dimension has a phrase that opens with the word before the place ("by <word>"), a pick puts the rest
  // of that phrase in the word's place. Otherwise a pick's words are a whole phrase, and it also takes the place of
  // the grouping operator KGE read directly before the word ("per <word>", "in each <word>"), so the question never
  // holds two.
  const led = item.options.every((option) => ledBy(option).length);
  let start = word.start;
  if (!led) {
    for (;;) {
      const operator = reading.bindings.find((binding) => binding.kind === "operator" && binding.span && binding.span.end <= start && !question.slice(binding.span.end, start).trim());
      if (!operator) break;
      start = operator.span.start;
    }
  }
  const label = question.slice(word.start, word.end).replace(/\s+/g, " ").trim();
  return {
    id: at ? `grouping-${at + 1}` : "grouping", label: `${label.charAt(0).toUpperCase()}${label.slice(1)}`.slice(0, 40), kind: "grouping", spans: [{ start, end: word.end }],
    options: item.options.map((option, n) => ({ id: `dimension-${n + 1}`, label: option.label, words: (led ? naming(ledBy(option)).slice(before.length + 1) : naming(phrasesOf(option))).trim() })),
  };
}

/**
 * The step's outcome for the person's question, one of:
 * - { kind: "unread" }: KGE gave no reading (a failed call, a KGE without the capability, no call left): nothing is
 *   known, so nothing is asked, and the turn goes on as it would with no step;
 * - { kind: "refused", message }: KGE reports an access refusal, in KGE's words; no card and no ask;
 * - { kind: "no_data", message }: KGE reports a period with no data; the message names the latest covered period; no
 *   card and no ask;
 * - { kind: "elsewhere" }: the question asks, in Cerebro's request words, for definitions or another output (stewards,
 *   tables, documents) and for no figure (intent.js asksForNoFigure): it gets no Period group and no card, and the
 *   turn goes on to the model, which chooses its tool as before #714, so a card's answer never ends at ask's decline.
 *   A "Something else" answer read back (cardNeeded) is not read this way;
 * - { kind: "clear" }: nothing is open;
 * - { kind: "open", groups, mentions, reading }: the open questions, in the card's order (the meanings, the output,
 *   the period, the groupings), each a group of the card (questions.js). An open meaning always opens the card. A
 *   grouping word KGE reports as open opens it. A period ask settles by its default joins only a card a meaning or
 *   the output or a grouping already opens (or where the caller says a card was already needed, cardNeeded: a
 *   "Something else" answer read back), and only where the question names no period in the portal's own time words
 *   (namesPeriod). An item that says ask cannot answer on another ground does not stop the card, and ask's own
 *   clarification is left to ask.
 * calls is how many KGE tool calls the step made.
 */
export async function beforeAsk(question, { call, history, limit = DEFAULT_CALLS_BEFORE_ASK, namesPeriod = () => false, cardNeeded = false } = {}) {
  const tools = counted(call, limit, history);
  const reading = readingOf(await tools.reading(question), question);
  if (!reading) return { kind: "unread", calls: tools.spent() };
  const refusal = reading.items.find((item) => item.item === "access");
  if (refusal) return { kind: "refused", message: (refusal.reason ?? REFUSED).slice(0, SAID_MAX), calls: tools.spent() };
  const uncovered = reading.items.find((item) => item.item === "period" && item.resolution === "unanswerable");
  if (uncovered) return { kind: "no_data", message: noData(uncovered, reading).slice(0, SAID_MAX), calls: tools.spent() };
  if (!cardNeeded && asksForNoFigure(question, reading)) return { kind: "elsewhere", reading, calls: tools.spent() };
  const mentions = await openMeanings(question, reading, tools);
  const meanings = mentions.map((mention, at) => meaningGroup(mention, at, mentions.length));
  const show = needsIntentChoice(question, reading) ? [SHOW_GROUP] : [];
  const settled = reading.items.find((item) => item.item === "period" && item.resolution === "default");
  const groupings = reading.items.filter((item) => item.item === "grouping").map((item, at) => groupingGroup(question, item, at, reading)).filter(Boolean);
  const period = settled && (cardNeeded || meanings.length || show.length || groupings.length) && !namesPeriod(question) ? periodGroup(settled) : undefined;
  const groups = [...meanings, ...show, ...(period ? [period] : []), ...groupings];
  const facts = { reading, calls: tools.spent() };
  return groups.length ? { kind: "open", groups, mentions, ...facts } : { kind: "clear", ...facts };
}
