import assert from "node:assert/strict";
import { test } from "node:test";
import { readFileSync, writeFileSync } from "node:fs";
import { cerebroAgent } from "../src/cerebro/agent.js";
import { beforeAsk } from "../src/cerebro/beforeAsk.js";
import { memoryStore, openConversations } from "../src/conversations/index.js";
import { QUESTIONS } from "../src/cerebro/questions.js";
import { kgeStub, standIn } from "./fixtures/cerebro-stand-in.js";
import { expectedOf, verdictsOf } from "./fixtures/suite-oracle.js";

// #707 requirement 16 (#714): the evaluation suite through Cerebro, graded deterministically. Every wording of the
// fixture (the #697-#700 hotfix reviews' wordings, the golden 20 and #707's acceptance wordings) is asked of Cerebro's
// agent against a stand-in KGE that answers with what KGE itself said for it (the fixture's kge.reading and
// kge_tools, recorded from Meridian's build by demos/kge/tests/cerebro_suite.py), with a stand-in model that asks the
// question as written. What Cerebro does before its first ask is the wording's outcome:
//   { outcome: "none" }                  no card: the question goes on to ask as the person wrote it;
//   { outcome: "card", groups: [...] }   one card, with its groups' kinds in order (one group is the one-question card);
//   { outcome: "no_data" }               the period has no data: said at once, no card and no ask;
//   { outcome: "refused" }               KGE's refusal: no card and no ask.
// It must equal the wording's expected outcome, which comes from #707's requirements and not from this run: the
// suite's oracle (fixtures/suite-oracle.js) derives it from what KGE said for the wording, with none of the code under
// test, and the fixture holds it (expected). Each wording also holds a verdict, pass or fail against that expected
// outcome, for the hotfix's recorded outcome and for the outcome now (verdicts), and acceptance 16's "none moves from
// pass to fail" is asserted over them. SUITE_RECORD=1 writes expected (the oracle's), hotfix_reading and verdicts to
// the fixture instead of grading them against it, for a change of the requirements or of KGE's reading; the outcome of
// the run is still graded against the oracle, so recording never makes a failing wording pass.
const FIXTURE = new URL("./fixtures/cerebro-evaluation-suite.json", import.meta.url);
const suite = JSON.parse(readFileSync(FIXTURE, "utf8"));
const RECORD = process.env.SUITE_RECORD === "1";
const FIGURE = { behaviour: "figure", answer_mode: "certified", answer_text: "Certified: it was USD 12.", sql: "SELECT 1", sql_certified: true };

const readingOf = (wording) => ({
  bindings: wording.kge.reading.bindings,
  open_items: wording.kge.reading.open_items.map((item) => ({ ...item, options: suite.kge_options[item.options] })),
});
const unrecorded = [];
const recorded = (table, key, kind) => {
  if (!Object.hasOwn(table, key)) unrecorded.push(`${kind}: ${key}`);
  return table[key] ?? [];
};
/** The step's three reads, answered from the fixture; a read the fixture does not hold is listed, and fails the suite. */
const filtersOf = (query) => ({ value: { hits: recorded(suite.kge_tools.search_filter, query, "search").map((title, at) => ({ kind: "filter", id: `filter:${at}`, title, restricted: false })) } });
const reads = (wording) => ({
  reading: () => ({ value: readingOf(wording) }),
  meanings: (name) => ({ value: { term: name, resolves_to: recorded(suite.kge_tools.map_term, name, "map_term") } }),
  filters: filtersOf,
});
function kgeFor(wording) {
  const tools = reads(wording);
  const stub = kgeStub((name, args) => (name === "ask" ? { value: FIGURE } : name === "map_term" ? tools.meanings(args.term) : { value: {} }),
    { step: (name, args) => (name === "get_context" ? { value: readingOf(wording) } : filtersOf(args.query)) });
  return Object.assign(stub, { tools });
}
const asksAsWritten = () => standIn((messages) => (messages.some((m) => m._getType() === "tool" && m.name === "ask") ? { say: "Done." } : { call: ["ask", { question: messages.findLast((m) => m._getType() === "human").content }] }));
const ONE_QUESTION = { meaning: "meaning", intent: "show", grouping: "grouping", period: "period" };

const conversations = openConversations({ store: memoryStore() });
async function turn(kge, model, sent, thread) {
  const events = [];
  const runTurn = cerebroAgent({ model, kge, conversations, retryAfterMs: 0 });
  for await (const event of runTurn({ thread, ...sent, roles: ["business"], signal: new AbortController().signal, req: { portalIdentity: { principal: "analyst" } } })) events.push(event);
  const values = events.filter(([e]) => e === "values").at(-1)?.[1];
  return { card: values?.__interrupt__?.[0]?.value, said: values.messages.filter((m) => m.type === "ai" && !m.tool_calls?.length).map((m) => m.content) };
}
const asked = (kge) => kge.calls.filter((c) => c.name === "ask").map((c) => c.args.question);
const words = (text) => String(text).toLowerCase().match(/[\p{L}\p{N}]+/gu) ?? [];
/** The framing's own words (intent.js framedQuestion, questions.js completedQuestion): a question's shape, never a qualifier. */
const FRAMING = new Set(words("What was What is the definition of for"));
/** The open request's own words (intent.js OPEN_REQUEST), which the framing takes out with the output's pick. */
const REQUEST = new Set(words("tell me about show give information details on i need want provide some"));

/** One wording through Cerebro: its outcome, and the checks every run must hold. */
async function run(wording) {
  const question = wording.question;
  const kge = kgeFor(wording);
  const model = asksAsWritten();
  const thread = globalThis.crypto.randomUUID();
  const first = await turn(kge, model, { messages: [{ type: "human", content: question }] }, thread);
  const step = await beforeAsk(question, { call: kge.tools, namesPeriod: (text) => NAMES_PERIOD.test(text) });
  if (!first.card) {
    if (step.kind === "refused" || step.kind === "no_data") {
      assert.deepEqual([asked(kge), model.calls, first.said], [[], 0, [step.message]], `${question}: said at once, with no ask and no model call`);
      return { outcome: step.kind };
    }
    assert.deepEqual(asked(kge), [question], `${question}: no card, so the question goes on to ask as written`);
    return { outcome: "none" };
  }
  assert.deepEqual([asked(kge), model.calls], [[], 0], `${question}: nothing is asked, and no model is called, before the card`);
  const groups = first.card.source === QUESTIONS ? first.card.groups.map((group) => group.kind) : [ONE_QUESTION[first.card.source]];
  assert.ok(groups.every(Boolean) && new Set(groups.filter((kind) => kind !== "meaning" && kind !== "grouping")).size === groups.filter((kind) => kind !== "meaning" && kind !== "grouping").length, `${question}: ${JSON.stringify(groups)}`);
  // The first option of every group (figures, of the output's, so every group applies), then Send: at least one ask follows, with no second card before it (#707
  // acceptance 16), and each question sent holds only the person's words, the picks' words and the framing's own
  // (#707 requirement 11).
  const firstOf = (group) => (group.kind === "show" ? group.options.find((option) => option.id === "figures") : group.options[0]);
  const picks = first.card.source === QUESTIONS ? { picks: Object.fromEntries(first.card.groups.map((group) => [group.id, firstOf(group).id])) } : { pick: first.card.options[0].id };
  const picked = first.card.source === QUESTIONS ? first.card.groups.map((group) => firstOf(group).words)
    : [step.groups[0].options.find((option) => option.id === first.card.options[0].id)?.words ?? first.card.options[0].id];
  const second = await turn(kge, model, { resume: picks }, thread);
  const sent = asked(kge);
  assert.ok(sent.length >= 1 && !second.card, `${question}: the picks make at least one ask, and no second card comes before it`);
  assert.equal(model.calls, 0, `${question}: no model call builds the question`);
  const allowed = new Set([...words(question), ...picked.flatMap(words), ...FRAMING]);
  for (const each of sent) {
    const added = words(each).filter((word) => !allowed.has(word));
    assert.deepEqual(added, [], `${question}: "${each}" holds a word that is in neither the person's words nor a pick`);
  }
  // No word of the person's is lost but where a pick stands in its place (a group's places and cuts) or the framing
  // took the open request out (#707 decision 4: shape, never meaning).
  const cardGroups = first.card.source === QUESTIONS ? first.card.groups : step.groups.slice(0, 1);
  const replaced = new Set(cardGroups.flatMap((group) => [...(group.spans ?? []), ...(group.cut ?? [])]).flatMap((span) => words(question.slice(span.start, span.end))));
  const lost = words(question).filter((word) => !sent.some((each) => words(each).includes(word)) && !replaced.has(word) && !(groups.includes("show") && REQUEST.has(word)));
  assert.deepEqual(lost, [], `${question}: a word of the person's is in no question sent (${sent.join(" | ")})`);
  return { outcome: "card", groups };
}
/** agent.js's own time words, as its namesPeriod reads them, for the direct read of the step's kind. */
const NAMES_PERIOD = /\b(?:(?:last|previous|next|this|current|latest)\s+(?:month|quarter|year|week)|today|yesterday|ytd|qtd|(?:january|february|march|april|may|june|july|august|september|october|november|december|jan|feb|mar|apr|jun|jul|aug|sep|oct|nov|dec)\s+\d{4}|(?:q[1-4]|h[12])\s+\d{4}|fy\d{2,4}(?:-q[1-4])?|\d{4}-\d{2}(?:-\d{2})?)\b|\b(?:19|20)\d{2}\b/i;

// The hotfix's recorded outcomes (#697-#700, review rounds r4 to r9), read as what kit 0.14.58's hotfix did before its
// first ask, on the two things it could ask: the meaning card and the output (intent) card. Each record is "file:
// outcome" in the review's own words; where rounds differ, the latest round that says a thing stands.
//   "card[1] card[1] INTENT", "clear clear -"       the phrase's check, the whole question's check, the intent check
//   "CARD(1) | CARD(1) | intent=true"               the same three; NO-AMBIG is clear, INVALID the check's refusal
//   "answered[...]", "card:meaning", "card:intent"  the agent's turn: answered with no card, or its first card;
//   "FAIL:meaning_unverified[]"                     the agent's turn ended in the meaning check's refusal
//   "... head[card:5] dev[...]"                     the meaning check at the hotfix's head over the question's phrases
//   "card", "none", "both-card", "both-none", "LOSES-CARD"   the intent check at the hotfix's head
function hotfixOf(records) {
  const said = { meaning: undefined, intent: undefined };
  const rounds = { meaning: -1, intent: -1 };
  const say = (thing, value, round) => { if (value !== undefined && round >= rounds[thing]) { said[thing] = value; rounds[thing] = round; } };
  const check = (token) => (/^card/i.test(token) ? "card" : /^(?:clear|no-ambig)/i.test(token) ? "none" : /^(?:invalid|error)/i.test(token) ? "refusal" : undefined);
  for (const record of records) {
    const [, file, outcome] = record.match(/^(\S+): (.*)$/s) ?? [];
    const round = Number(file?.match(/^r(\d+)/)?.[1]);
    assert.ok(file && Number.isInteger(round), `a hotfix record names its review file: ${record}`);
    const head = outcome.match(/head\[([^\]]*)\]/)?.[1];
    if (head !== undefined) {
      const kinds = new Set(head.split(",").map((part) => check(part.trim())));
      say("meaning", kinds.size === 1 ? [...kinds][0] : "mixed", round);
    } else if (outcome.includes(" | ")) {
      const parts = outcome.split(" | ");
      say("meaning", check(parts[1]), round);
      say("intent", /intent=true/.test(parts[2]) ? "card" : "none", round);
    } else if (/^(?:card\[\d+\]|clear|INVALID|ERROR)\S* \S+ (?:INTENT|-)$/.test(outcome)) {
      const parts = outcome.split(" ");
      say("meaning", check(parts[1]), round);
      say("intent", parts[2] === "INTENT" ? "card" : "none", round);
    } else if (/^FAIL:meaning_unverified/.test(outcome)) say("meaning", "refusal", round);
    else if (/^answered\[/.test(outcome)) {
      say("meaning", "none", round);
      say("intent", "none", round);
    } else if (outcome === "card:meaning") {
      say("meaning", "card", round);
      say("intent", "none", round);
    } else if (outcome === "card:intent") say("intent", "card", round);
    else if (["card", "both-card"].includes(outcome)) say("intent", "card", round);
    else if (["none", "both-none", "LOSES-CARD"].includes(outcome)) say("intent", "none", round);
    else assert.fail(`a hotfix record this suite cannot read: ${record}`);
  }
  return said;
}
// #707 acceptance 16 ("none moves from pass to fail against the hotfix's recorded outcome") has one known exception
// (independent review of 12d907dd, finding 3; coordinator, 2026-11-28, on #714). The suite finds it, it is not told
// it: its verdicts are hotfix pass, now fail (suite-oracle.js verdictsOf: a turned qualifier, no card, and ask gives a
// figure). It is named here with its reason and marked in the fixture's hotfix_reading; a wording that moves from
// pass to fail and is not named here fails the suite, and so does a name here that no longer moves: when #709 gives
// the wording its card, the mark goes.
const KNOWN_EXCEPTIONS = {
  "What was the still to be certified Q2 2025 NNA?": "The hotfix's card guarded this wording; now KGE reports no open item for it, no card is shown, and ask answers with a Certified NNA figure, "
    + "though the person hedged the qualifier (\"still to be certified\"). The portal cannot catch a hedge without a word list, which #707 requirement 8 forbids. "
    + "#709 requirement 3 fixes it: a qualifier KGE does not recognise counts as bare, and so gets the card.",
};
const EXCEPTION = "known exception to acceptance 16";
/** How the hotfix's recorded outcome reads against the expected one: "no record", "agrees", or what differs, as
 *  "<what the hotfix did> → <what is expected>" for the meaning and for the output. A known exception to acceptance
 *  16 is marked as one, with what differs and the reason. */
function hotfixReading(wording, expected) {
  const reading = hotfixDiffers(wording, expected);
  const reason = KNOWN_EXCEPTIONS[wording.question];
  return reason ? `${EXCEPTION}: ${reading.replace(/^differs: /, "")}. ${reason}` : reading;
}
function hotfixDiffers(wording, expected) {
  if (!wording.hotfix.length) return "no record";
  const hotfix = hotfixOf(wording.hotfix);
  const now = (kind) => (expected.outcome === "card" ? (expected.groups.includes(kind) ? "card" : "none") : expected.outcome === "none" ? "none" : expected.outcome);
  const differs = [["meaning", "meaning"], ["intent", "show"]].filter(([thing]) => hotfix[thing] !== undefined)
    .filter(([thing, kind]) => hotfix[thing] !== now(kind)).map(([thing, kind]) => `${thing} ${hotfix[thing]} → ${now(kind)}`);
  return differs.length ? `differs: ${differs.join("; ")}` : "agrees";
}
const tally = (values) => Object.fromEntries([...values.reduce((counts, value) => counts.set(value, (counts.get(value) ?? 0) + 1), new Map())].sort(([a], [b]) => a.localeCompare(b)));
const shown = (outcome) => (outcome.outcome === "card" ? `card: ${outcome.groups.join(", ")}` : outcome.outcome);

const outcomes = new Map();
const oracle = new Map(suite.wordings.map((wording) => [wording.question, expectedOf(wording, suite)]));
const verdictOf = (wording) => verdictsOf(wording, suite, oracle.get(wording.question).expected, outcomes.get(wording.question) ?? oracle.get(wording.question).expected, wording.hotfix.length ? hotfixOf(wording.hotfix) : undefined);
const kept = ({ guarded, hotfix, now }) => ({ hotfix, now, ...(guarded ? { guarded: true } : {}) });
function writeFixture() {
  const line = (value) => JSON.stringify(value);
  const head = Object.entries(suite).filter(([key]) => key !== "wordings").map(([key, value]) => `${line(key)}:${line(value)}`);
  writeFileSync(FIXTURE, `{\n${head.join(",\n")},\n"wordings":[\n${suite.wordings.map(line).join(",\n")}\n]\n}\n`);
}

test("#707 acceptance 16: every wording's expected outcome is the requirements' (the oracle's), and its outcome through Cerebro equals it; no run shows more than one card before its first ask; no ask holds a word of neither the person nor a pick", async () => {
  assert.equal(suite.wordings.length, 2472);
  if (!RECORD) assert.deepEqual(suite.wordings.filter((wording) => JSON.stringify(wording.expected) !== JSON.stringify(oracle.get(wording.question).expected)).map((wording) => wording.question), [],
    "each wording's expected outcome in the fixture is the one the requirements give (fixtures/suite-oracle.js); SUITE_RECORD=1 writes it");
  const moved = [];
  for (const wording of suite.wordings) {
    const outcome = await run(wording);
    outcomes.set(wording.question, outcome);
    const { expected, why } = oracle.get(wording.question);
    if (JSON.stringify(outcome) !== JSON.stringify(expected)) moved.push(`${wording.question}: ${shown(outcome)}, expected ${shown(expected)} (${why.join("; ")})`);
  }
  assert.deepEqual(unrecorded, [], "every read the step makes is in the fixture (rebuild it: demos/kge/tests/cerebro_suite.py)");
  assert.deepEqual(moved, [], `${moved.length} wordings do not do what the requirements expect`);
});

test("#707 acceptance 16: no wording that passed under the hotfix fails now, but for the named exceptions; each hotfix outcome is read as a pass or a fail against the expected one", () => {
  const verdicts = suite.wordings.map(verdictOf);
  const readings = suite.wordings.map((wording) => hotfixReading(wording, oracle.get(wording.question).expected));
  if (RECORD) {
    suite.wordings.forEach((wording, at) => { wording.expected = oracle.get(wording.question).expected; wording.hotfix_reading = readings[at]; wording.verdicts = kept(verdicts[at]); });
    writeFixture();
  }
  assert.deepEqual(readings, suite.wordings.map((wording) => wording.hotfix_reading), "each wording's hotfix_reading is this reading of its records");
  assert.deepEqual(verdicts.map(kept), suite.wordings.map((wording) => wording.verdicts), "each wording's verdicts are the ones this run gives");
  // The assertion acceptance 16 asks for: pass under the hotfix, fail now, is the named exceptions and nothing else.
  const lost = suite.wordings.filter((wording, at) => verdicts[at].hotfix === "pass" && verdicts[at].now === "fail");
  assert.deepEqual(lost.map((wording) => wording.question), Object.keys(KNOWN_EXCEPTIONS), "the wordings that move from pass to fail are the named exceptions");
  const failing = suite.wordings.filter((wording, at) => verdicts[at].now === "fail");
  assert.deepEqual(failing.map((wording) => wording.question), Object.keys(KNOWN_EXCEPTIONS), "and no other wording fails now, whatever the hotfix did with it");
  assert.deepEqual(tally(verdicts.map((verdict) => `hotfix ${verdict.hotfix}, now ${verdict.now}`)), VERDICTS);
  // A turned qualifier ("not certified", "never certified"): no card, and ask gives no figure (decision 1 on #714),
  // which tests/integration/test_open_items.py holds against the build with and without a model.
  const guarded = suite.wordings.filter((wording, at) => verdicts[at].guarded);
  assert.equal(guarded.length, GUARDED.wordings);
  assert.deepEqual(guarded.filter((wording) => wording.ask_terms !== null).map((wording) => wording.question), Object.keys(KNOWN_EXCEPTIONS), "ask gives a figure for a turned qualifier in the named exceptions alone");
  assert.equal(guarded.filter((wording) => outcomes.get(wording.question)?.outcome === "none" && wording.ask_terms === null).length, GUARDED.noCardNoFigure);
  assert.deepEqual(tally(suite.wordings.map((wording) => shown(wording.expected))), EXPECTED_OUTCOMES);
  assert.deepEqual(tally(readings.map((reading) => (reading.startsWith(EXCEPTION) ? EXCEPTION : reading))), HOTFIX_READINGS);
  const marked = suite.wordings.filter((wording) => wording.hotfix_reading.startsWith(EXCEPTION));
  assert.deepEqual(marked.map((wording) => wording.question), Object.keys(KNOWN_EXCEPTIONS), "the one wording that moves from pass to fail, named and marked");
  for (const wording of marked) {
    assert.deepEqual(wording.expected, { outcome: "none" }, `${wording.question}: no card, and it goes on to ask`);
    assert.deepEqual(wording.verdicts, { hotfix: "pass", now: "fail", guarded: true });
    assert.equal(wording.hotfix_reading, `${EXCEPTION}: meaning card → none. ${KNOWN_EXCEPTIONS[wording.question]}`, "the hotfix showed the meaning card; the mark gives the reason and where it is fixed");
  }
});

test("#707 acceptance 7, 10 and 12, 15: the card for an open meaning holds the 0.14.58 fixture's options, KGE's own labels, the marked option first and the others in the fixture's order", async () => {
  const recorded = suite.card_0_14_58;
  const name = (label) => label.split(" — ")[0];
  const cardOf = async (question) => (await turn(kgeFor(suite.wordings.find((wording) => wording.question === question)), asksAsWritten(), { messages: [{ type: "human", content: question }] }, globalThis.crypto.randomUUID())).card;
  const card = await cardOf("What was NNA for June 2025?");
  const shownNow = { question: card.question, options: card.options };
  if (RECORD) {
    recorded.expected = shownNow;
    writeFixture();
  }
  assert.deepEqual(shownNow, recorded.expected, "the card as the fixture holds it");
  assert.equal(card.question, recorded.question, "the question, with its suggested default, is 0.14.58's");
  const marked = recorded.options.find((option) => /\(default\)/.test(option.label));
  assert.deepEqual(card.options.map((option) => option.id), [marked.id, ...recorded.options.filter((option) => option !== marked).map((option) => option.id)], "decisions 3 and 18: the marked option first, the others in the fixture's order");
  for (const option of card.options) {
    const was = recorded.options.find((each) => each.id === option.id);
    // #714 decision 1 (B): the portal adds no "(current)" word; the label is KGE's.
    assert.equal(name(option.label), name(was.label).replace(" (current)", ""), option.id);
    assert.ok(option.label.length <= 80 && option.label.startsWith(was.label.replace(" (current)", "").replace(/…$/, "").slice(0, 40)), `${option.label}: one line, the definition KGE gives`);
  }
  assert.equal(card.options.filter((option) => /\(default\)/.test(option.label)).length, 1);
  const several = await cardOf("What was the retail NNA for Q1 2025 by segment?");
  assert.deepEqual(several.groups.map((group) => group.kind), ["meaning", "grouping"]);
  assert.deepEqual(several.groups[0].options.map((option) => [option.id, option.words]), card.options.map((option) => [option.id, name(option.label).replace(" (default)", "")]), "the Meaning group's options are the same card's");
  const reported = suite.kge_options[suite.wordings.find((wording) => wording.question === "What was the retail NNA for Q1 2025 by segment?").kge.reading.open_items.find((item) => item.item === "grouping").options];
  assert.deepEqual(several.groups[1].options.map((option) => option.label), reported.map((option) => option.label), "decision 20: every grouping dimension KGE's grouping item reports");
  assert.equal(reported.length, 10);
});

// The suite's outcomes, its verdicts and the reading of the hotfix's, pinned: a change shows here as a moved count, and
// in the fixture wording by wording. VERDICTS is acceptance 16's table: where the hotfix passed, the wording passes
// now, but for the named exception; where the hotfix failed #707's requirements (a card for a meaning the person
// named, a card or silence before a period with no data, a missing card), it passes now.
const VERDICTS = {
  "hotfix fail, now pass": 820,
  "hotfix no record, now pass": 294,
  "hotfix pass, now fail": 1,
  "hotfix pass, now pass": 1357,
};
const GUARDED = { wordings: 194, noCardNoFigure: 157 };
const EXPECTED_OUTCOMES = {
  "card: grouping": 2,
  "card: meaning": 115,
  "card: meaning, grouping": 1,
  "card: meaning, period": 267,
  "card: meaning, period, grouping": 3,
  "card: meaning, show": 56,
  "card: meaning, show, period": 343,
  "card: meaning, show, period, grouping": 1,
  "card: show": 45,
  "card: show, period": 60,
  "no_data": 379,
  "none": 1200,
};
const HOTFIX_READINGS = {
  "agrees": 1254,
  "differs: intent card → no_data": 56,
  "differs: intent card → none": 58,
  "differs: intent none → card": 39,
  "differs: intent none → no_data": 46,
  "differs: meaning card → no_data": 9,
  "differs: meaning card → no_data; intent card → no_data": 23,
  "differs: meaning card → no_data; intent none → no_data": 69,
  "differs: meaning card → none": 437,
  "differs: meaning card → none; intent card → none": 32,
  "differs: meaning mixed → no_data; intent none → no_data": 1,
  "differs: meaning none → card": 31,
  "differs: meaning none → card; intent card → none": 8,
  "differs: meaning none → no_data": 24,
  "differs: meaning none → no_data; intent card → no_data": 7,
  "differs: meaning none → no_data; intent none → no_data": 83,
  "known exception to acceptance 16": 1,
  "no record": 294,
};
