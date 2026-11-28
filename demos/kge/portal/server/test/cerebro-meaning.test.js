import assert from "node:assert/strict";
import { test } from "node:test";
import { createHash } from "node:crypto";
import { readFileSync } from "node:fs";
import { SUGGESTED_UNTIL_709, meaningCard, meaningGroup, openMeanings, optionsOf, readingOf, suggestedOf } from "../src/cerebro/meaning.js";
import { oneGroup } from "../src/cerebro/questions.js";
import { kgeReading } from "./fixtures/cerebro-stand-in.js";
import { literalsOf, wordsIn } from "./fixtures/source-literals.js";

// #707 requirements 7, 8, 13 and 15 (#714): the meaning check reads KGE. Whether a meaning is open, and what a card
// offers, come from KGE's reading (get_context's bindings and open items), map_term's meanings of a name and search's
// filter phrases. The vocabulary here is invented (FUM, "pooled", "harbour"), so nothing holds for one pack only; the
// acceptance wordings on Meridian's own names are cerebro-before-ask.test.js's and the evaluation suite's.
const FUM = [
  { id: "fum-spot", label: "Spot FUM", definition: "Funds under management at the day's close.", default_alias: true },
  { id: "fum-pooled", label: "Pooled FUM", definition: "Funds under management in pooled vehicles, at each month's release." },
  { id: "fum-certified", label: "Certified FUM", definition: "Funds under management as the finance office froze them at month end, in the reporting currency, from its certified reference table of record." },
];
const spot = (text, by = "default_alias", fields = {}) => [text, "term", { term: "Spot FUM", by, ...fields }];
const pooled = (text) => [text, "term", { term: "Pooled FUM", by: "explicit" }];
const open = (text = "FUM", fields = {}) => ["term", "default", { text, options: FUM, ...fields }];
/** The step's tools over a table of names and words, each call recorded; a name or word mapped to undefined is a call
 *  that failed or that the limit cut. */
function toolsOf({ meanings = { FUM }, filters = {} } = {}) {
  const calls = [];
  return { calls,
    meanings: async (name) => { calls.push(["meanings", name]); return name in meanings ? meanings[name] && optionsOf(meanings[name]) : []; },
    filters: async (phrase) => { calls.push(["filters", phrase]); return phrase in filters ? filters[phrase] : []; } };
}
const check = async (question, bindings, items, tools = toolsOf()) => {
  const mentions = await openMeanings(question, readingOf(kgeReading(question, bindings, items), question), tools);
  return { mentions, tools, tokens: mentions.map((mention) => mention.token), places: mentions.map((mention) => mention.spans.map((span) => question.slice(span.start, span.end))) };
};

test("a name KGE bound by its default alias, with several readable meanings, is open, from the open item alone and with no other call", async () => {
  const { mentions, tools, places } = await check("Total FUM in March 2025", [["Total", "operator"], spot("FUM"), ["March 2025", "period"]], [open()]);
  assert.deepEqual(places, [["FUM"]]);
  assert.deepEqual(mentions[0].options.map((option) => option.label), ["Spot FUM", "Pooled FUM", "Certified FUM"], "KGE's meanings, in KGE's order");
  assert.deepEqual(tools.calls, [], "the open item gives the meanings: no map_term and no search");
  const lower = await check("what was fum?", [spot("fum")], [open("fum")]);
  assert.deepEqual(lower.tokens, ["fum"], "the name as the person wrote it");
  const single = await check("Total FUM", [spot("FUM")], [["term", "default", { text: "FUM", options: FUM.slice(0, 1) }]]);
  assert.deepEqual(single.mentions, [], "one readable meaning is nothing to ask, and shows no other");
});

test("a meaning the person named is settled: a term bound by its own name, together or apart, and a time mode written before the name", async () => {
  for (const [question, bindings] of [
    ["Pooled FUM in March 2025", [pooled("Pooled FUM"), ["March 2025", "period"]]],
    ["pooled Q2 2025 FUM", [pooled("pooled"), ["Q2 2025", "period"], pooled("FUM")]],
    ["pooled Northern Desk FUM for Q2 2024", [pooled("pooled"), ["Northern Desk", "filter"], pooled("FUM"), ["Q2 2024", "period"]]],
    ["Compare pooled FUM for Q1 with pooled FUM for Q2", [["Compare", "comparison"], pooled("pooled FUM"), pooled("pooled FUM")]],
  ]) {
    const { mentions, tools } = await check(question, bindings, []);
    assert.deepEqual([mentions, tools.calls], [[], []], `${question}: settled by the bindings, with no call`);
  }
  const mode = await check("What was live FUM?", [["live", "time_mode", { value: "current" }], spot("FUM", "default_alias", { time_mode: "current" })], [open()]);
  assert.deepEqual(mode.mentions, [], "the mode the person wrote is the bound meaning's own");
  const across = await check("live Q3 2024 FUM", [["live", "time_mode", { value: "current" }], ["Q3 2024", "period"], spot("FUM", "default_alias", { time_mode: "current" })], [open()]);
  assert.deepEqual(across.mentions, [], "only periods KGE read stand between them");
  const other = await check("What was frozen FUM?", [["frozen", "time_mode", { value: "as_reported" }], spot("FUM", "default_alias", { time_mode: "current" })], [open()]);
  assert.deepEqual(other.tokens, ["FUM"], "a mode that is not the bound meaning's leaves it open");
  const apart = await check("live figures: FUM", [["live", "time_mode", { value: "current" }], spot("FUM", "default_alias", { time_mode: "current" })], [open()]);
  assert.deepEqual(apart.tokens, ["FUM"], "a word KGE did not read between them leaves it open");
});

test("#707 requirement 15: a qualifier that also names a filter value keeps the meaning open; one that names nothing else is settled", async () => {
  const question = "What was the harbour FUM for Q1 2025?";
  const bindings = [pooled("harbour FUM"), ["Q1 2025", "period"]];
  const two = await check(question, bindings, [], toolsOf({ filters: { harbour: ["Harbour Desk", "division name Harbour Branches"] } }));
  assert.deepEqual(two.tools.calls, [["meanings", "FUM"], ["filters", "harbour"]]);
  assert.deepEqual([two.tokens, two.places, two.mentions[0].said, two.mentions[0].bound, two.mentions[0].cut.map((span) => question.slice(span.start, span.end))], [["FUM"], [["FUM"]], "harbour FUM", "Pooled FUM", ["harbour"]],
    "a pick's words take the name's place; the qualifier is cut only for the meaning KGE bound the phrase to");
  const group = meaningGroup(two.mentions[0], 0, 1);
  assert.deepEqual([group.cutFor, group.cut.length], [["fum-pooled"], 1]);
  assert.ok(oneGroup(group, question));
  assert.deepEqual(two.mentions[0].options.map((option) => option.id), ["fum-spot", "fum-pooled", "fum-certified"]);
  const one = await check(question, bindings, [], toolsOf());
  assert.deepEqual(one.mentions, [], "\"harbour\" names nothing but the meaning");
  const near = await check(question, bindings, [], toolsOf({ filters: { harbour: ["Harbourside"] } }));
  assert.deepEqual(near.mentions, [], "a filter phrase that only holds the letters is not the word");
  const unrelated = await check(question, bindings, [], toolsOf({ meanings: { FUM: FUM.slice(0, 1) }, filters: { harbour: ["Harbour Desk"] } }));
  assert.deepEqual([unrelated.mentions, unrelated.tools.calls], [[], [["meanings", "FUM"]]], "a name with one meaning is settled before any search");
  const stranger = await check(question, bindings, [], toolsOf({ meanings: { FUM: [{ label: "Other one" }, { label: "Other two" }] } }));
  assert.deepEqual(stranger.mentions, [], "the name's meanings do not hold the bound term: not a qualifier of that name");
  const split = await check("harbour Q1 2025 FUM", [pooled("harbour"), ["Q1 2025", "period"], pooled("FUM")], [], toolsOf({ filters: { harbour: ["Harbour Desk"] } }));
  assert.deepEqual([split.places, split.mentions[0].cut.map((span) => "harbour Q1 2025 FUM".slice(span.start, span.end))], [[["FUM"]], ["harbour"]], "a qualifier written apart is the same qualifier");
  const beside = "Compare FUM and harbour FUM";
  const apartFromBare = await check(beside, [["Compare", "comparison"], spot("FUM"), pooled("harbour FUM")], [open()], toolsOf({ filters: { harbour: ["Harbour Desk"] } }));
  assert.deepEqual([apartFromBare.places, apartFromBare.mentions.map((mention) => mention.said ?? mention.token)], [[["FUM"], ["FUM"]], ["FUM", "harbour FUM"]], "a qualified mention is a mention of its own, apart from a bare one of the same name");
});

test("#707 requirements 7 and 13: a check the limit cuts short, or that fails, ends on the card where the meanings are known, never on a refusal", async () => {
  const question = "What was the harbour FUM for Q1 2025?";
  const bindings = [pooled("harbour FUM"), ["Q1 2025", "period"]];
  const noFilters = await check(question, bindings, [], toolsOf({ filters: { harbour: undefined } }));
  assert.deepEqual(noFilters.tokens, ["FUM"], "the meanings are known and the filter check is not: the card asks");
  const noMeanings = await check(question, bindings, [], toolsOf({ meanings: { FUM: undefined } }));
  assert.deepEqual([noMeanings.mentions, noMeanings.tools.calls], [[], [["meanings", "FUM"]]], "nothing is known to offer: the question goes on as KGE bound it");
});

test("the same bare name written more than once, bound with no qualifier, is one open mention with every place", async () => {
  const question = "Compare Q1 2025 FUM with Q2 2025 FUM";
  const twice = await check(question, [["Compare", "comparison"], ["Q1 2025", "period"], spot("FUM", "explicit"), ["Q2 2025", "period"], spot("FUM", "explicit")], []);
  assert.deepEqual([twice.tokens, twice.places], [["FUM"], [["FUM", "FUM"]]]);
  const once = await check("Q1 2025 FUM", [["Q1 2025", "period"], spot("FUM", "explicit")], []);
  assert.deepEqual([once.mentions, once.tools.calls], [[], []], "once, bound explicitly: KGE's own reading stands");
  const both = await check("FUM and FUM again", [spot("FUM"), spot("FUM")], [open("FUM"), open("FUM", { at: 1 })]);
  assert.deepEqual(both.places, [["FUM", "FUM"]], "two open items of one name are one mention");
});

test("readingOf keeps what the portal reads, places each span where the question holds its words, and is undefined for a reply with no reading", () => {
  const question = "Total 😀 FUM in March";
  const reading = readingOf({ bindings: [{ kind: "term", text: "FUM", start: 8, end: 11, term: "Spot FUM", by: "default_alias", context: "x", options: [{ label: "y" }] }, { kind: "period", text: "March", start: 0, end: 5 }, null, "x"],
    open_items: [{ item: "term", resolution: "default", text: "FUM", start: 8, end: 11, options: [{ label: " Spot  FUM ", id: "a", definition: " d " }, { label: "" }, { id: "no-label" }] },
      { item: "period", resolution: "unanswerable", latest: { label: "June 2025", value: "2025-06" }, reason: " Not released. ", options: [{ label: "June 2025", value: "2025-06" }] },
      { item: "grouping", resolution: "clarify", text: "desk", start: 99, end: 103, options: [{ label: "Desk code", phrases: ["by desk code", "", 3] }] }] }, question);
  assert.deepEqual(reading.bindings, [{ kind: "term", text: "FUM", span: { start: 9, end: 12 }, term: "Spot FUM", by: "default_alias" }, { kind: "period", text: "March", span: undefined }],
    "KGE counts characters, JavaScript UTF-16 units; a place that does not hold its words is no place");
  assert.deepEqual(reading.items[0], { item: "term", resolution: "default", text: "FUM", span: { start: 9, end: 12 }, options: [{ label: "Spot FUM", id: "a", definition: "d" }] });
  assert.deepEqual([reading.items[1].latest, reading.items[1].reason, reading.items[1].options], ["June 2025", "Not released.", [{ label: "June 2025", value: "2025-06" }]]);
  assert.deepEqual([reading.items[2].span, reading.items[2].options], [undefined, [{ label: "Desk code", phrases: ["by desk code"] }]]);
  for (const reply of [undefined, null, {}, { bindings: [] }, { open_items: [] }, { bindings: {}, open_items: [] }, "text"]) assert.equal(readingOf(reply, question), undefined);
  assert.equal(readingOf({ bindings: [], open_items: [] }), undefined, "no question, no reading");
});

test("#707 decisions 3, 8, 17 and 18: the card marks the one \"certified\" option and lists it first, by the named temporary rule; it never takes its marker from the default meaning", () => {
  assert.equal(suggestedOf(optionsOf(FUM)).label, "Certified FUM");
  assert.equal(FUM.find((option) => option.default_alias).label, "Spot FUM", "the default meaning, the one ask binds, is another");
  assert.equal(suggestedOf(optionsOf(FUM.slice(0, 2))), undefined, "no option carries the word: none is suggested");
  assert.equal(suggestedOf(optionsOf([...FUM, { label: "Certified other" }])), undefined, "two carry it: none is suggested");
  assert.equal(suggestedOf(optionsOf([{ label: "Uncertified FUM" }, { label: "Spot FUM" }])), undefined, "the word, whole");
  assert.equal(String(SUGGESTED_UNTIL_709), "/\\bcertified\\b/i");
  const question = "Total FUM in March 2025";
  const mention = { token: "FUM", spans: [{ start: 6, end: 9 }], cut: [], options: optionsOf(FUM) };
  const group = meaningGroup(mention, 0, 1);
  assert.deepEqual([group.id, group.label, group.kind, group.token, group.spans], ["meaning", "Meaning", "meaning", "FUM", [{ start: 6, end: 9 }]]);
  assert.deepEqual(group.options.map((option) => [option.id, option.words, option.suggested]), [["fum-certified", "Certified FUM", true], ["fum-spot", "Spot FUM", undefined], ["fum-pooled", "Pooled FUM", undefined]],
    "the suggested option first, then KGE's order; a pick's words are KGE's label alone");
  assert.ok(group.options.every((option) => !/\(default\)/.test(option.label)), "the group's labels are unmarked: the card marks the suggested one, once");
  assert.equal(group.options[1].label, "Spot FUM — Funds under management at the day's close.");
  const card = meaningCard(group, question);
  assert.equal(card.question, "Which meaning of FUM do you intend? Suggested default: Certified FUM.");
  assert.deepEqual(card.options.map((option) => option.label), [
    "Certified FUM (default) — Funds under management as the finance office froze…",
    "Spot FUM — Funds under management at the day's close.",
    "Pooled FUM — Funds under management in pooled vehicles, at each month's release.",
  ]);
  assert.ok(card.options.every((option) => option.label.length <= 80), "one line each, as on kit 0.14.58");
  assert.deepEqual(card.options.map((option) => option.term), ["Certified FUM", "Spot FUM", "Pooled FUM"]);
  const plain = meaningCard(meaningGroup({ ...mention, options: optionsOf(FUM.slice(0, 2)) }, 0, 1), question);
  assert.equal(plain.question, "Which meaning of FUM do you intend?", "where no option is suggested, none is marked or named");
  assert.ok(plain.options.every((option) => !option.label.includes("(default)")));
  assert.deepEqual([meaningGroup(mention, 1, 2).id, meaningGroup(mention, 1, 2).label], ["meaning-2", "Meaning of FUM"], "two open names: a group each, named");
  const unnamed = meaningGroup({ ...mention, options: optionsOf([{ label: "One", id: "bad id" }, { label: "Two", id: "same" }, { label: "Three", id: "same" }]) }, 0, 1);
  assert.deepEqual(unnamed.options.map((option) => option.id), ["meaning-1", "same", "meaning-2"], "an id the card cannot carry, or a repeated one, is made, and no two are the same");
  const long = meaningCard(meaningGroup({ ...mention, options: optionsOf([{ label: `Certified ${"very ".repeat(20)}long FUM`, definition: "d" }, { label: "Spot FUM" }]) }, 0, 1), question);
  assert.ok(long.options.every((option) => option.label.length <= 80) && long.options[0].label.endsWith("… (default)"), "a long name is cut to the line");
  assert.ok(oneGroup(group, question), "the group holds to the card's shape");
  assert.equal(meaningCard({ ...group, token: undefined }, question).token, "FUM", "with no name given, the words at the mention's place");
});

// #707 acceptance 8: a test fails if the meaning check or the intent check declares any word list other than Cerebro's
// four kinds of request words and the words that locate a mention; outside those lists, the only business word they
// match on is "certified", in the one named temporary rule. Every literal of the three modules is read (comments left
// out): a regular expression may hold words only where it is one of the named constants below; every string literal
// is one of the texts listed, which are KGE's contract words, the card's own texts and what the portal says. A list
// that comes back, as a regular expression, a Set or an array of words, or a word compared in line, is a literal
// this test does not know, and fails it. The words that locate a mention are now none: KGE gives each place.
const sourceOf = (name) => readFileSync(new URL(`../src/cerebro/${name}.js`, import.meta.url), "utf8");
const REQUEST_WORDS = ["OPEN_REQUEST", "DEFINITION", "FIGURE", "OTHER_OUTPUT"];
/** Each request-word list's own text, by its SHA-256's first 16 hex digits: a word added to one is a reviewed change. */
const REQUEST_WORD_LISTS = {"OPEN_REQUEST": "7b3bf29f8b467f56", "SHOW_REQUEST": "436c57c12dee236b", "DEFINITION": "a6332269befe7ae5", "FIGURE": "a871e46cb95815b2", "OTHER_OUTPUT": "d76b52ad419ac7ff"};
const TEXTS = {
  meaning: ["", " ", "   ", "string", "object", "term", "period", "time_mode", "explicit", "meaning- ", " (default)", " — ", "meaning", "Meaning of  ", "Meaning", "  ", " …", " Suggested default:  .", "Which meaning of   do you intend? "],
  intent: ["./periods.js", "", " ", "comparison", "grouping", "string", "What would you like to see?", "definitions", "Show the definitions.", "figures", "Show the figures.", "both", "Show both the definitions and the figures.",
    "^\\\\s* \\\\s+(.+?)[\\\\s?.!…]*$", "is", "What was  ?", "What is the definition of  ?", "time_mode", "term", "explicit", "period", " for  ", "   ", "  ", "      ", "unread"],
  beforeAsk: ["../apps.js", "./intent.js", "./meaning.js", "You may not read the data this question needs.", "There is no data for that period.", "show", "Show", "definitions", "Definitions", "figures", "Figures", "both", "Both",
    "filter", "string", "period", "operator", "There is no data for  .", "  The latest covered period is  .", "", "Period", "  ", " ", "grouping- ", "grouping", "dimension- ", "unread", "access", "refused", "unanswerable", "no_data", "default", "elsewhere", "open", "clear"],
};
function wordLists(name, source) {
  const { regexes, strings } = literalsOf(source);
  const found = [];
  const open = regexes.find((regex) => regex.name === "OPEN_REQUEST");
  for (const regex of regexes) {
    const words = wordsIn(regex.body);
    if (!words.length) continue;
    if (name === "intent" && REQUEST_WORDS.includes(regex.name)) continue;
    if (name === "intent" && regex.name === "SHOW_REQUEST" && open && words.every((word) => wordsIn(open.body).includes(word))) continue;
    if (name === "meaning" && regex.name === "SUGGESTED_UNTIL_709" && regex.body === "\\bcertified\\b") continue;
    found.push(`${name}.js: a regular expression that matches on words: ${regex.name ?? "(unnamed)"} /${regex.body}/`);
  }
  for (const text of new Set(strings)) if (!TEXTS[name].includes(text)) found.push(`${name}.js: a text this check does not know: ${JSON.stringify(text)}`);
  return found;
}

test("#707 acceptance 8: the meaning check, the intent check and the step declare no word list but Cerebro's four kinds of request words; the only business word matched is \"certified\", in the one named rule", () => {
  for (const name of ["meaning", "intent", "beforeAsk"]) assert.deepEqual(wordLists(name, sourceOf(name)), [], name);
  const intent = literalsOf(sourceOf("intent"));
  assert.deepEqual(intent.regexes.filter((regex) => wordsIn(regex.body).length).map((regex) => regex.name).sort(), [...REQUEST_WORDS, "SHOW_REQUEST"].sort(), "decision 10's four kinds, and the one of them that asks to be shown");
  assert.deepEqual(Object.fromEntries(intent.regexes.filter((regex) => regex.name in REQUEST_WORD_LISTS).map((regex) => [regex.name, createHash("sha256").update(regex.body).digest("hex").slice(0, 16)])), REQUEST_WORD_LISTS,
    "the request words are the lists #707 decision 10 kept, word for word");
  const certified = ["meaning", "intent", "beforeAsk"].flatMap((name) => { const { regexes, strings } = literalsOf(sourceOf(name)); return [...regexes.map((regex) => regex.body), ...strings].filter((text) => /certif/i.test(text)).map(() => name); });
  assert.deepEqual(certified, ["meaning"], "\"certified\" is matched once, in meaning.js's SUGGESTED_UNTIL_709");
  assert.doesNotMatch(sourceOf("meaning").replace(/^\/\/.*$|\/\*[\s\S]*?\*\//gm, ""), /^import /m, "the meaning check imports no word list: it imports nothing");
  assert.deepEqual([...sourceOf("intent").matchAll(/^import .* from "(.+)";$/gm)].map((match) => match[1]), ["./periods.js"], "the intent check's one import is the portal's time words, which #707 leaves as they are");
});

test("#707 acceptance 8: the check fails when a word list comes back as a regular expression, a Set or an array of words, or a word compared in line", () => {
  const planted = (line) => wordLists("meaning", `${sourceOf("meaning")}\n${line}\n`);
  assert.equal(planted("const NEGATION = new Set(['not', 'never', 'without']);").length, 3, "a Set of words");
  assert.match(planted("const COMPARISON = /\\b(?:versus|vs\\.?|compare)\\b/i;")[0], /a regular expression that matches on words: COMPARISON/);
  assert.match(planted("const STOP = new Set('a an and are'.split(' '));")[0], /a text this check does not know: "a an and are"/);
  assert.match(planted("const isRetail = (word) => word === \"retail\";")[0], /"retail"/, "a word compared in line");
  assert.match(planted("function negated(text) {\n  return /\\b(?:not|never)\\b/i.test(text);\n}")[0], /a regular expression that matches on words: \(unnamed\)/, "a regular expression after return");
  assert.match(planted("const hedged = (text) => /\\bpartly\\b/.test(text);")[0], /partly/, "a regular expression after an arrow");
  assert.match(planted("const QUALIFIED = (title) => /certified/i.test(title);")[0], /\(unnamed\)|QUALIFIED/, "a second \"certified\" test is not the named rule");
  assert.match(wordLists("intent", `${sourceOf("intent")}\nconst BY = /\\bby\\s+(?:region|segment)\\b/i;\n`)[0], /BY/, "a \"by\" list in the intent check");
  assert.match(wordLists("meaning", sourceOf("meaning").replace("\\bcertified\\b", "\\b(?:certified|frozen)\\b"))[0], /SUGGESTED_UNTIL_709/, "the named rule holds one word");
});
