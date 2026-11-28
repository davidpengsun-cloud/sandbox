import { readFileSync } from "node:fs";
import { namesPeriodRange } from "../../src/cerebro/periods.js";
import { literalsOf } from "./source-literals.js";

// Tests only: the evaluation suite's oracle (#707 requirement 16 and acceptance 16; review of PR #728, finding 2).
// expectedOf(wording, suite) says what Cerebro must do with one wording before its first ask, derived from #707's
// requirements and the decisions on #714, over what KGE said for the wording (the fixture's kge.reading and kge_tools,
// recorded from the build by demos/kge/tests/cerebro_suite.py). It is a second, separate statement of the rules: it
// calls none of the meaning check, the intent check or the step (meaning.js, intent.js, beforeAsk.js, agent.js), so a
// fault in them shows as a wording whose outcome differs from this one, never as a moved snapshot. Each rule names the
// requirement it comes from.
//
// Two things are read from the portal's source as data, never run as its logic: Cerebro's request words (#707 decision
// 10: the four named lists of intent.js, held word for word by cerebro-meaning.test.js) and the portal's time words
// (agent.js TIME_WORD and periods.js, which #707 leaves as they are).
//
// verdictsOf(wording, suite, expected, outcome, hotfix) then reads the hotfix's recorded outcome and the outcome now as a
// pass or a fail against the expected one (acceptance 16: none moves from pass to fail).

const sourceOf = (name) => readFileSync(new URL(`../../src/cerebro/${name}.js`, import.meta.url), "utf8");
function named(source, name) {
  const found = literalsOf(source).regexes.filter((regex) => regex.name === name);
  if (found.length !== 1) throw new Error(`the oracle reads one ${name}, and found ${found.length}`);
  return new RegExp(found[0].body, found[0].flags);
}
const intent = sourceOf("intent");
const OPEN_REQUEST = named(intent, "OPEN_REQUEST");
const SHOW_REQUEST = named(intent, "SHOW_REQUEST");
const DEFINITION = named(intent, "DEFINITION");
const FIGURE = named(intent, "FIGURE");
const OTHER_OUTPUT = named(intent, "OTHER_OUTPUT");
const TIME_WORD = named(sourceOf("agent"), "TIME_WORD");
const YEAR = /\b(?:19|20)\d{2}\b/;

const wordsOf = (text) => String(text).match(/[\p{L}\p{N}]+/gu) ?? [];
const lower = (text) => wordsOf(text).join(" ").toLowerCase();
const blank = (text, places) => places.reduce((left, { start, end }) => left.slice(0, start) + " ".repeat(end - start) + left.slice(end), text);

/** Every binding and open item of a wording's reading, each with its options and, where KGE gives one, its place. */
function readingOf(wording, suite) {
  const placed = (entry) => (Number.isInteger(entry.start) && Number.isInteger(entry.end) && wording.question.slice(entry.start, entry.end) === entry.text ? { start: entry.start, end: entry.end } : undefined);
  return {
    bindings: wording.kge.reading.bindings.map((binding) => ({ ...binding, at: placed(binding) })),
    items: wording.kge.reading.open_items.map((item) => ({ ...item, at: placed(item), options: suite.kge_options[item.options] ?? [] })),
  };
}

/**
 * The open meanings of a wording, one for each mention (#707 requirements 7, 13 and 15; acceptance 9), each
 * { key, why }:
 * - Requirement 7: "whether a meaning is open comes from get_context's open items": a term item that lists two or
 *   more meanings is open,
 * - but for acceptance 9 ("current vs. certified Q3 2024 NNA shows no Meaning group") and requirement 13 ("the
 *   bindings for what the question already settles"): not where the person wrote, directly before the name with at
 *   most periods between, the time mode that is the bound meaning's own.
 * - Requirement 15: "when a qualifying word also names something else in the pack, such as a source or a filter value,
 *   the meaning counts as open": a name KGE bound explicitly together with a qualifier (words that are not the bound
 *   term's own name), where the name alone has several meanings (map_term) and the qualifier is also a filter phrase
 *   (search, kind filter).
 * - Requirement 13 with decision 5 ("an open meaning always goes on the card"): one name written twice with no
 *   qualifier, bound explicitly to a term whose own name it is not, where the name alone has several meanings and no
 *   time mode of the person's names the term.
 */
function openMeanings(wording, reading, suite) {
  const question = wording.question;
  const inOrder = reading.bindings.filter((binding) => binding.at).sort((a, b) => a.at.start - b.at.start);
  const open = [];
  for (const item of reading.items) {
    if (item.item !== "term" || !item.at || item.options.length < 2) continue;
    const at = inOrder.findIndex((binding) => binding.kind === "term" && binding.at.start === item.at.start && binding.at.end === item.at.end);
    let before = at - 1;
    while (before >= 0 && inOrder[before].kind === "period") before -= 1;
    const mode = at >= 0 ? inOrder[before] : undefined;
    const run = mode ? inOrder.slice(before, at + 1) : [];
    const adjoining = run.every((binding, n) => n === run.length - 1 || !question.slice(binding.at.end, run[n + 1].at.start).trim());
    const own = mode?.kind === "time_mode" && (!inOrder[at].time_mode || mode.value === inOrder[at].time_mode) && adjoining;
    if (!own) open.push({ key: lower(item.text), why: "requirement 7: a term item with several meanings" });
  }
  const explicit = new Map();
  for (const binding of inOrder) if (binding.kind === "term" && binding.by === "explicit" && binding.term) explicit.set(binding.term, [...(explicit.get(binding.term) ?? []), binding]);
  for (const [term, bindings] of explicit) {
    const said = bindings.flatMap((binding) => wordsOf(binding.text));
    const ownName = new Set(wordsOf(term).map((word) => word.toLowerCase()));
    const saidSet = new Set(said.map((word) => word.toLowerCase()));
    if ([...saidSet].every((word) => ownName.has(word)) && saidSet.size === ownName.size) continue;
    const name = said.at(-1);
    const qualifier = said.filter((word) => word.toLowerCase() !== name.toLowerCase()).join(" ");
    const family = suite.kge_tools.map_term[name] ?? [];
    const several = family.length >= 2 && family.some((meaning) => lower(meaning.label) === lower(term));
    if (!qualifier) {
      const modeNamesIt = reading.bindings.some((binding) => binding.kind === "time_mode" && binding.term === term);
      if (bindings.length >= 2 && !modeNamesIt && several && !open.some((each) => each.key === lower(name))) open.push({ key: lower(name), why: "requirement 13: one name written twice, with several meanings" });
      continue;
    }
    const filters = suite.kge_tools.search_filter[qualifier] ?? [];
    if (several && filters.some((title) => (` ${lower(title)} `).includes(` ${lower(qualifier)} `))) open.push({ key: lower(`${qualifier} ${name}`), why: "requirement 15: the qualifier also names a filter value" });
  }
  return open;
}

/** What KGE read of the question's shape (#707 requirement 9: "it uses get_context's bindings only to tell a grouping
 *  or a comparison from other words"). */
const shapeOf = (reading) => ({
  comparison: reading.bindings.some((binding) => binding.kind === "comparison"),
  grouping: reading.bindings.some((binding) => binding.kind === "grouping") || reading.items.some((item) => item.item === "grouping"),
});

/**
 * The expected outcome of one wording through Cerebro, before its first ask: { outcome: "refused" | "no_data" |
 * "none" } or { outcome: "card", groups: [kinds, in the card's order] }, with why, the rule that gave each part.
 */
export function expectedOf(wording, suite) {
  const question = wording.question;
  const reading = readingOf(wording, suite);
  // Requirement 14: "order: refusal, then data coverage, then the card".
  if (reading.items.some((item) => item.item === "access")) return { expected: { outcome: "refused" }, why: ["requirement 14: KGE's refusal, no card"] };
  if (reading.items.some((item) => item.item === "period" && item.resolution === "unanswerable")) return { expected: { outcome: "no_data" }, why: ["requirement 14: a period with no data, said at once"] };
  // The independent review of 12d907dd, finding 1 (coordinator, 2026-11-28): a question whose request words read as
  // definitions or another output, and ask for no figure, gets no card and is not sent to ask by the step
  // (requirement 12: "the Period and grouping groups apply to figures"). A request word in a place KGE read is KGE's.
  const read = [...reading.bindings.filter((binding) => binding.kind !== "unread"), ...reading.items].filter((each) => each.at).map((each) => each.at);
  const unreadWords = blank(question, read);
  if ((DEFINITION.test(unreadWords) || OTHER_OUTPUT.test(unreadWords)) && !FIGURE.test(question)) return { expected: { outcome: "none" }, why: ["review finding 1: a question for definitions or another output, and no figure, gets no card"] };
  const meanings = openMeanings(wording, reading, suite);
  // Requirement 9: the output is open for an open request, or a comparison KGE bound, that names no definition, figure
  // or other output in Cerebro's request words; "Show me ... by X" asks for figures where KGE read X as a grouping,
  // and so does a "show me" over a period range (#700).
  const { comparison, grouping } = shapeOf(reading);
  const asksOpenly = comparison || OPEN_REQUEST.test(question);
  const namesOutput = DEFINITION.test(question) || FIGURE.test(question) || OTHER_OUTPUT.test(question);
  const shownFigures = SHOW_REQUEST.test(question) && !comparison && (grouping || namesPeriodRange(question));
  const show = Boolean(question.trim()) && asksOpenly && !namesOutput && !shownFigures;
  // Decision 20 and requirement 12: a grouping word KGE reports as naming several declared dimensions.
  const groupings = reading.items.filter((item) => item.item === "grouping" && item.at && item.options.length >= 2 && /[\p{L}\p{N}]/u.test(item.text));
  // Requirement 13 and its clarification on #714: "a period that ask settles by a default goes on the card only when
  // the card is already needed", and never where the question names a period in the portal's own time words.
  const defaulted = reading.items.find((item) => item.item === "period" && item.resolution === "default");
  const needed = meanings.length > 0 || show || groupings.length > 0;
  const namesPeriod = TIME_WORD.test(question) || YEAR.test(question);
  const offered = (defaulted?.options ?? []).filter((option) => typeof option.value === "string" && /^[\w.:-]{1,64}$/.test(option.value));
  const period = Boolean(defaulted) && needed && !namesPeriod && offered.length > 0;
  const groups = [...meanings.map(() => "meaning"), ...(show ? ["show"] : []), ...(period ? ["period"] : []), ...groupings.map(() => "grouping")];
  const why = [...meanings.map((meaning) => meaning.why), ...(show ? ["requirement 9: the output is open"] : []), ...(period ? ["requirement 13: a default period joins a card already needed"] : []),
    ...groupings.map(() => "decision 20: a grouping word naming several dimensions")];
  return groups.length ? { expected: { outcome: "card", groups }, why } : { expected: { outcome: "none" }, why: ["nothing is open: the question goes to ask as written"] };
}

// A qualifier the person negated or hedged ("not certified", "never certified", "still to be certified",
// "not-yet-certified", "non-current", "certified or not"): the words of the #697-#700 review wordings that turn a
// named meaning away or leave it in doubt. These lists are the suite's, for grading; the portal holds no such list
// (#707 requirement 8). They name no term: which words are a qualifier comes from KGE's reading.
const TURNED = /\b(?:not|never|no longer|non|un|yet to be|still to be|to be|soon to be|about to be|almost|nearly|barely|hardly|scarcely|partly|partially|provisionally|unofficially|supposedly|allegedly|so-called|pre|post|ex|formerly|previously|once|except|excluding|without|other than|rather than|instead of|anything but)\b/i;
const TURNED_AFTER = /^(?:or not|yet|no longer|not)\b/i;
const JOINED = ["un", "non", "de", "pre", "post"];
/** The words a turned qualifier may be joined to ("un-", "non-"), over the whole suite: every word KGE bound as a
 *  qualifier in any wording (an explicit term's words, a time mode's), and the words of every meaning it names (the
 *  terms it bound, the open terms' options, map_term's meanings). */
const vocabularies = new WeakMap();
function qualifierWords(suite) {
  if (!vocabularies.has(suite)) {
    const texts = [...suite.wordings.flatMap((wording) => wording.kge.reading.bindings.filter((binding) => (binding.kind === "term" && binding.by === "explicit") || binding.kind === "time_mode").flatMap((binding) => [binding.text, binding.term])),
      ...suite.kge_options.flat().map((option) => option.definition === undefined ? "" : option.label), ...Object.values(suite.kge_tools.map_term).flat().map((meaning) => meaning.label)];
    vocabularies.set(suite, new Set(texts.flatMap((text) => wordsOf(text ?? "").map((word) => word.toLowerCase())).filter((word) => word.length >= 4)));
  }
  return vocabularies.get(suite);
}

/** Whether the wording negates or hedges a qualifier it names. A qualifier is a place KGE bound to a meaning by the
 *  person's own words: a term bound explicitly, or a time mode. It is turned where a turning word stands directly
 *  before it with at most three words between ("not fully certified", "no longer being certified"), directly after
 *  it ("certified or not", "certified yet"), or joined to a qualifier's word or a meaning's ("uncertified",
 *  "decertified"; qualifierWords), whether or not KGE bound the joined word. */
export function turnsQualifier(wording, suite) {
  const question = wording.question;
  const tokens = [...question.matchAll(/[\p{L}\p{N}']+/gu)].map((match) => ({ word: match[0].toLowerCase(), start: match.index, end: match.index + match[0].length }));
  const qualifiers = wording.kge.reading.bindings.filter((binding) => ((binding.kind === "term" && binding.by === "explicit") || binding.kind === "time_mode")
    && Number.isInteger(binding.start) && question.slice(binding.start, binding.end) === binding.text);
  const named = qualifierWords(suite);
  if (tokens.some(({ word }) => JOINED.some((prefix) => word.startsWith(prefix) && named.has(word.slice(prefix.length))))) return true;
  return qualifiers.some((binding) => {
    const before = tokens.filter((token) => token.end <= binding.start);
    if (/n't$/.test(before.at(-1)?.word ?? "")) return true;
    if (TURNED.test(before.slice(-4).map((token) => token.word).join(" "))) return true;
    return TURNED_AFTER.test(tokens.filter((token) => token.start >= binding.end).slice(0, 2).map((token) => token.word).join(" "));
  });
}

/**
 * Pass or fail, against the expected outcome, for the hotfix's recorded outcome (hotfix: what it recorded of the
 * meaning card and of the output card, each "card", "none", "refusal", "mixed" or undefined) and for the outcome now.
 * - The outcome now passes where it equals the expected one.
 * - The hotfix passes where each thing it recorded equals the expected one: the meaning card where a Meaning group is
 *   expected and none where it is not, and the same for the output; where the expected outcome is KGE's refusal or a
 *   period with no data, a card or a silent pass is a fail (requirement 14: said at once).
 * - A wording that turns its qualifier (turnsQualifier) is guarded: the person named no meaning plainly, so they must
 *   not be given a figure for one. The hotfix's meaning card passes it (it asked). With no card it passes only where
 *   ask gives no figure (coordinator's decision 1 on #714, 2026-11-27: "no figure is given"), which KGE's own
 *   recording says (ask_terms is null where ask executes no plan). No card and a figure is a fail.
 * Returns { guarded, hotfix: "pass" | "fail" | "no record", now: "pass" | "fail", because }.
 */
export function verdictsOf(wording, suite, expected, outcome, hotfix) {
  const guarded = turnsQualifier(wording, suite);
  const figure = wording.ask_terms !== null;
  const has = (result, kind) => (result.outcome === "card" ? (result.groups.includes(kind) ? "card" : "none") : result.outcome);
  const because = [];
  const same = JSON.stringify(outcome) === JSON.stringify(expected);
  let now = same ? "pass" : "fail";
  if (!same) because.push("now: the outcome is not the expected one");
  if (guarded && has(outcome, "meaning") === "none" && figure) {
    now = "fail";
    because.push("now: a turned qualifier gets no card, and ask gives a figure");
  }
  const recorded = [["meaning", "meaning"], ["intent", "show"]].filter(([thing]) => hotfix && hotfix[thing] !== undefined);
  if (!recorded.length) return { guarded, hotfix: "no record", now, because };
  let was = "pass";
  for (const [thing, kind] of recorded) {
    const wanted = has(expected, kind);
    if (thing === "meaning" && guarded && wanted === "none") {
      // A guarded wording: the hotfix's card passes it; with no card, it passes only where ask gives no figure.
      if (hotfix[thing] === "card" || (hotfix[thing] === "none" && !figure)) continue;
      was = "fail";
      because.push(hotfix[thing] === "none" ? "hotfix: a turned qualifier got no card, and ask gives a figure" : `hotfix: meaning ${hotfix[thing]}, expected none or the card`);
      continue;
    }
    if (hotfix[thing] === wanted) continue;
    was = "fail";
    because.push(`hotfix: ${thing} ${hotfix[thing]}, expected ${wanted}`);
  }
  return { guarded, hotfix: was, now, because };
}
