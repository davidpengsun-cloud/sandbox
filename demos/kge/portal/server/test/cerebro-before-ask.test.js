import assert from "node:assert/strict";
import { test } from "node:test";
import { RESTATE, SHAPE_RULE, cerebroAgent, savedConversation } from "../src/cerebro/agent.js";
import { CALLS_BEFORE_ASK_MIN, DEFAULT_CALLS_BEFORE_ASK, appSettings } from "../src/apps.js";
import { REFUSED, beforeAsk } from "../src/cerebro/beforeAsk.js";
import { memoryStore, openConversations } from "../src/conversations/index.js";
import { QUESTIONS, QUESTIONS_HEADING, SUGGESTED } from "../src/cerebro/questions.js";
import { INTENT_OPTIONS, INTENT_QUESTION } from "../src/cerebro/intent.js";
import { MONTHS, kgeReading, kgeStub, periodDefault, periodUncovered, standIn, stepReads } from "./fixtures/cerebro-stand-in.js";

// #714 (707.3): the one step before ask, through Cerebro's agent, against the stand-in KGE (#707 requirement 17). Each
// acceptance line of #714 has its test here, named by its number. The readings below are KGE's own for these
// questions, as get_context gave them with open_items on Meridian's build (recorded 2026-11-27, kit 0.14.68), cut to
// the fields the portal reads; the second vocabulary (AUM, "pooled") is invented, with other names, so no rule here
// holds for one pack only.
const store = memoryStore();
const conversations = openConversations({ store });

const NNA = [
  { id: "net-new-assets", label: "Net New Assets", definition: "Money clients bring to the firm less money they take away, in USD, from current received transactions marked eligible for net new assets, over released months, test accounts included.", default_alias: true },
  { id: "branch-sales-nna", label: "Branch Sales NNA", definition: "Eligible received net flows for bank and brokerage accounts in S4100 and the BR branch division, excluding AQ and DT, at each month's financial release.", default_alias: false },
  { id: "certified-nna", label: "Certified NNA", definition: "Finance's frozen month-end net new assets, in USD, from its certified reference table.", default_alias: false },
  { id: "client-directed-nna", label: "Client-Directed NNA", definition: "Finance's net new assets from rows marked as client-directed, over released months, using month-end account and segment versions.", default_alias: false },
];
/** The 0.14.58 card, in the order #707 decisions 3 and 18 set and with KGE's own label for the current meaning (#714
 *  decision 1, B): the suggested default first and marked, then the others in KGE's order. */
export const CARD_QUESTION = "Which meaning of NNA do you intend? Suggested default: Certified NNA.";
export const CARD_LINES = [
  ["certified-nna", "Certified NNA (default) — Finance's frozen month-end net new assets, in USD,…"],
  ["net-new-assets", "Net New Assets — Money clients bring to the firm less money they take away, in…"],
  ["branch-sales-nna", "Branch Sales NNA — Eligible received net flows for bank and brokerage accounts…"],
  ["client-directed-nna", "Client-Directed NNA — Finance's net new assets from rows marked as…"],
];
const SEGMENTS = [
  ["Segment level id1", ["by client segment level 1", "level 1 client segment"]], ["Segment level id2", ["by client segment level 2", "level 2 client segment"]],
  ["Segment level id3", ["by client segment level 3", "level 3 client segment"]], ["Segment level id4", ["by client segment level 4", "by level 4 client segment", "by segment level 4", "level 4 client segment"]],
  ["Segment level id5", ["by client segment level 5", "by parent segment", "by segment level 5", "by sub-segment", "level 5 client segment"]],
  ["Segment level id6", ["by business line", "by client segment", "by client segment level 6", "by segment level 6", "level 6 client segment"]],
  ["Segment level id7", ["by client segment level 7", "level 7 client segment"]], ["Segment level nm4", ["by segment level 4 name"]],
  ["Segment level nm5", ["by segment level 5 name"]], ["Segment level nm6", ["by client segment name", "by segment level 6 name"]],
].map(([label, phrases]) => ({ label, phrases }));
const bare = (text = "NNA", fields = {}) => [text, "term", { term: "Net New Assets", by: "default_alias", time_mode: "current", ...fields }];
const certified = (text) => [text, "term", { term: "Certified NNA", by: "explicit", time_mode: "current" }];
const open = (text = "NNA", fields = {}) => ["term", "default", { text, options: NNA, ...fields }];
const declined = (reason) => ["question", "unanswerable", { reason }];

const READINGS = Object.fromEntries([
  ["What was NNA for June 2025?", [bare(), ["June 2025", "period"]], [open()]],
  ["certified Q2 2025 NNA", [certified("certified"), ["Q2 2025", "period"], certified("NNA")], []],
  ["certified Investor Services NNA for Q2 2024", [certified("certified"), ["Investor Services", "filter", { label: "Segment level id6", value: "S5100" }], certified("NNA"), ["Q2 2024", "period"]], []],
  ["NNA?", [bare()], [open(), periodDefault()]],
  ["certified Q2-2024 NNA", [certified("certified"), ["Q2-2024", "period"], certified("NNA")], []],
  ["current vs. certified Q3 2024 NNA", [["current", "time_mode", { term: "Net New Assets", by: "explicit", value: "current" }], ["vs", "comparison"], certified("certified"), ["Q3 2024", "period"], certified("NNA")], []],
  ["Show me NNA by Finance", [bare("NNA", { time_mode: undefined }), ["by", "unread"], ["Finance", "unread"]], [open(), declined("“by”, “Finance” in the question match no certified definition, so they cannot be applied. No figure is given."), periodDefault()]],
  ["Show me NNA by region", [bare(), ["by region", "grouping", { label: "office.region_code" }]], [open(), periodDefault()]],
  ["What was the retail NNA for Q1 2025 by segment?", [["retail NNA", "term", { term: "Branch Sales NNA", by: "explicit" }], ["Q1 2025", "period"], ["by", "operator"], ["segment", "unread"]],
    [declined("I cannot apply 'What was the retail NNA for Q1 2025 by segment?': no certified measure or declared entity resolves the complete request. No figure is given."), ["grouping", "clarify", { text: "segment", options: SEGMENTS }]]],
  ["What was certified NNA by region?", [certified("certified NNA"), ["by region", "grouping", { label: "office.region_code" }]], [periodDefault()]],
  ["Show me NNA", [bare()], [open(), periodDefault()]],
  ["What was the retail NNA for Q1 2026 by segment?", [["retail NNA", "term", { term: "Branch Sales NNA", by: "explicit" }], ["Q1 2026", "period"], ["by", "operator"], ["segment", "unread"]],
    [declined("I cannot apply the question."), periodUncovered(), ["grouping", "clarify", { text: "segment", options: SEGMENTS }]]],
  ["What was current NNA?", [["current", "time_mode", { value: "current" }], bare()], [open(), periodDefault()]],
  ["What was not certified NNA?", [["not", "unread"], certified("certified NNA")], [declined("“not” in the question matches no certified definition, so it cannot be applied. No figure is given."), periodDefault()]],
  ["What was N N A?", [["N", "unread"], ["N", "name"]], [["meaning", "clarify", { options: [{ label: "N" }, { label: "N" }] }]]],
  ["What was certified NNA for Q1 2025 by segment?", [certified("certified NNA"), ["Q1 2025", "period"], ["by", "unread"], ["segment", "unread"]], [declined("“by”, “segment” match no certified definition."), ["grouping", "clarify", { text: "segment", options: SEGMENTS }]]],
  ["What was certified NNA for June 2025?", [certified("certified NNA"), ["June 2025", "period"]], []],
  ["What was certified NNA?", [certified("certified NNA")], [periodDefault()]],
  ["Show me certified NNA", [certified("certified NNA")], [periodDefault()]],
].map(([question, bindings, items]) => [question, kgeReading(question, bindings, items)]));
const NO_WEALTH = { bindings: [{ kind: "unread", text: "NNA", start: 9, end: 12 }], open_items: [{ item: "access", resolution: "unanswerable", reason: "analyst_retail.meridian@example.com may not read (withheld)." }] };
const FILTERS = { retail: ["division name Retail Branches", "Retail Investor", "Retail Branches"] };
const FIGURE = { behaviour: "figure", answer_mode: "certified", answer_text: "Certified: it was USD 12.", sql: "SELECT 1", sql_certified: true, context: { term: "Certified NNA" } };
const DEFINITION = { behaviour: "glossary_definition", answer_text: "Certified NNA: as declared.", term: "Certified NNA" };

/** The stand-in KGE: the readings above, map_term's meanings of "NNA", search's filter phrases, and ask. */
function kgeOf({ readings = READINGS, filters = FILTERS, ask = () => ({ value: FIGURE }), meanings = { NNA }, golden } = {}) {
  return kgeStub((name, args) => {
    if (name === "ask") return ask(args.question);
    if (name === "map_term") return { value: { term: args.term, resolves_to: meanings[args.term] ?? [] } };
    return { value: {} };
  }, { golden, step: stepReads((question) => readings[question], (query) => filters[query.toLowerCase()] ?? []) });
}
const asksAsWritten = () => standIn((messages) => (messages.some((m) => m._getType() === "tool" && m.name === "ask") ? { say: "Done." } : { call: ["ask", { question: messages.findLast((m) => m._getType() === "human").content }] }));
async function turn(kge, { question, resume, thread = globalThis.crypto.randomUUID(), roles = ["business"], model = asksAsWritten(), settings, principal = "person-1" } = {}) {
  const events = [];
  const runTurn = cerebroAgent({ model, kge, conversations, retryAfterMs: 0, settings });
  const sent = resume === undefined ? { messages: [{ type: "human", content: question }] } : { resume };
  for await (const event of runTurn({ thread, ...sent, roles, signal: new AbortController().signal, req: { portalIdentity: { principal } } })) events.push(event);
  const values = events.filter(([e]) => e === "values").at(-1)?.[1];
  const outcome = events.filter(([e, d]) => e === "custom" && d.kind === "outcome").at(-1)?.[1];
  return { values, outcome, card: values?.__interrupt__?.[0]?.value, thread, model, said: values?.messages.filter((m) => m.type === "ai" && !m.tool_calls?.length).map((m) => m.content) ?? [] };
}
const asked = (kge) => kge.calls.filter((c) => c.name === "ask").map((c) => c.args.question);
const groupsOf = (card) => (card?.source === QUESTIONS ? card.groups.map((g) => g.label) : card ? [card.source] : []);
const optionsOf = (card, label) => card.groups.find((g) => g.label === label).options;

test("#714 acceptance 7, 10: \"What was NNA for June 2025?\" shows one card whose options and labels are the 0.14.58 fixture's, the certified option first and marked; a pick sends at once", async () => {
  const kge = kgeOf();
  const first = await turn(kge, { question: "What was NNA for June 2025?" });
  assert.equal(first.model.calls, 0, "no model call fills the items");
  assert.deepEqual(asked(kge), [], "no ask before the card");
  assert.deepEqual(first.card, { kind: "hitl", tool_call_id: first.card.tool_call_id, source: "meaning", question: CARD_QUESTION, options: CARD_LINES.map(([id, label]) => ({ id, label })), other: true });
  assert.equal(first.card.options.filter((o) => o.label.includes("(default)")).length, 1, "one marker, on the certified option");
  assert.ok(first.card.options.every((o) => o.label.length <= 80), "one line each");
  assert.deepEqual(kge.reads.map((r) => r.name), ["get_context"], "one KGE call: the reading itself gives the meanings");
  const sent = await turn(kge, { thread: first.thread, resume: { pick: "certified-nna" } });
  assert.deepEqual(asked(kge), ["What was Certified NNA for June 2025?"], "the pick's words, KGE's own label, in the mention's place");
  assert.equal(sent.model.calls, 0);
  assert.equal(sent.outcome.outcome, "answered");
  assert.deepEqual(sent.said, [`Selected definition (Certified NNA): ${NNA[2].definition}`]);
});

test("#714 acceptance 7: \"certified Q2 2025 NNA\" and \"certified Investor Services NNA …\" are answered with no card; \"NNA?\" and \"certified Q2-2024 NNA\" never end in a refusal", async () => {
  for (const question of ["certified Q2 2025 NNA", "certified Investor Services NNA for Q2 2024", "certified Q2-2024 NNA"]) {
    const kge = kgeOf();
    const done = await turn(kge, { question });
    assert.equal(done.card, undefined, `${question}: no card`);
    assert.deepEqual(asked(kge), [question], `${question}: asked as written`);
    assert.equal(done.outcome.outcome, "answered");
    assert.deepEqual(kge.reads.map((r) => r.name), ["get_context"], "the bindings settle it: no other call");
  }
  const kge = kgeOf();
  const bareOne = await turn(kge, { question: "NNA?" });
  assert.deepEqual(groupsOf(bareOne.card), ["Meaning", "Period"], "\"NNA?\": the card, never a refusal");
  assert.equal(bareOne.outcome.outcome, "paused");
  assert.deepEqual(asked(kge), []);
});

test("#714 acceptance 9: \"current vs. certified Q3 2024 NNA\" shows no Meaning group; \"Show me NNA by Finance\" Meaning, Show and Period; \"Show me NNA by region\" Meaning and Period, no Show", async () => {
  const versus = await turn(kgeOf(), { question: "current vs. certified Q3 2024 NNA" });
  assert.deepEqual([versus.card.source, versus.card.question, versus.card.options], ["intent", INTENT_QUESTION, INTENT_OPTIONS], "a comparison KGE bound, with no output named: the output alone is asked");
  const finance = await turn(kgeOf(), { question: "Show me NNA by Finance" });
  assert.deepEqual(groupsOf(finance.card), ["Meaning", "Show", "Period"], "KGE read no grouping in \"by Finance\", so the output is open; its decline does not stop the card");
  const region = await turn(kgeOf(), { question: "Show me NNA by region" });
  assert.deepEqual(groupsOf(region.card), ["Meaning", "Period"], "KGE bound \"by region\" as a grouping, so the output is not open");
  assert.equal(region.card.question, QUESTIONS_HEADING);
});

test("#714 acceptance 9: the output's pick asks the comparison in the person's own words, one ask per requested output", async () => {
  const kge = kgeOf({ ask: (question) => ({ value: /definition/.test(question) ? DEFINITION : FIGURE }) });
  const first = await turn(kge, { question: "current vs. certified Q3 2024 NNA" });
  await turn(kge, { thread: first.thread, resume: { pick: "both" } });
  assert.deepEqual(asked(kge), ["What is the definition of current vs. certified Q3 2024 NNA?", "current vs. certified Q3 2024 NNA"]);
});

test("#714 acceptance 12, 15: \"What was the retail NNA for Q1 2025 by segment?\" shows one card, a Meaning group as the 0.14.58 fixture and a Segment group with every dimension KGE reports; both picks make one ask", async () => {
  const kge = kgeOf();
  const first = await turn(kge, { question: "What was the retail NNA for Q1 2025 by segment?" });
  assert.deepEqual(groupsOf(first.card), ["Meaning", "Segment"]);
  assert.deepEqual(optionsOf(first.card, "Meaning").map((o) => o.id), CARD_LINES.map(([id]) => id), "the fixture's options, the suggested default first (decision 18)");
  assert.equal(optionsOf(first.card, "Meaning")[0].label.endsWith(SUGGESTED), true);
  assert.deepEqual(optionsOf(first.card, "Meaning").map((o) => o.words), ["Certified NNA", "Net New Assets", "Branch Sales NNA", "Client-Directed NNA"]);
  assert.deepEqual(optionsOf(first.card, "Segment").map((o) => o.label), SEGMENTS.map((s) => s.label), "all ten, no cap (decision 20)");
  assert.deepEqual(kge.reads.map((r) => [r.name, r.args.query ?? r.args.question]), [["get_context", "What was the retail NNA for Q1 2025 by segment?"], ["search", "retail"]]);
  assert.deepEqual(kge.calls.map((c) => [c.name, c.args.term]), [["map_term", "NNA"]], "requirement 15: \"retail\" also names filter values, so the meaning is open");
  const missing = await turn(kge, { thread: first.thread, resume: { picks: { meaning: "certified-nna" } } });
  assert.deepEqual(asked(kge), [], "a group with no pick is no answer: no ask");
  assert.equal(missing.outcome.outcome, "failed");
  const again = await turn(kge, { question: "What was the retail NNA for Q1 2025 by segment?" });
  const sent = await turn(kge, { thread: again.thread, resume: { picks: { meaning: "certified-nna", grouping: "dimension-6" } } });
  assert.deepEqual(asked(kge), ["What was the retail Certified NNA for Q1 2025 by client segment?"],
    "one ask; the person's own qualifier stays (decision 4), and the grouping pick sends a phrase ask reads, with the person's own word");
  assert.equal(sent.outcome.outcome, "answered");
  const bound = kgeOf();
  const third = await turn(bound, { question: "What was the retail NNA for Q1 2025 by segment?" });
  await turn(bound, { thread: third.thread, resume: { picks: { meaning: "branch-sales-nna", grouping: "dimension-5" } } });
  assert.deepEqual(asked(bound), ["What was the Branch Sales NNA for Q1 2025 by client segment level 5?"], "the meaning KGE bound the phrase to takes the qualifier's place: its name says it");
});

test("#714 acceptance 12: \"both\" sends one definitions ask and one figures ask; a resume with a group missing, a pick not offered or a pick for a card the server did not save is not accepted", async () => {
  const kge = kgeOf({ ask: (question) => ({ value: /definition/.test(question) ? DEFINITION : FIGURE }) });
  const first = await turn(kge, { question: "Show me NNA" });
  await turn(kge, { thread: first.thread, resume: { picks: { meaning: "certified-nna", show: "both", period: "2025-05" } } });
  assert.deepEqual(asked(kge), ["What is the definition of Certified NNA?", "What was Certified NNA for May 2025?"], "each complete; the period applies to the figures alone");
  for (const picks of [{ meaning: "certified-nna", show: "figures" }, { meaning: "made-up", show: "figures", period: "2025-05" }, { meaning: "certified-nna", show: "figures", period: "2025-05", segment: "x" }]) {
    const other = kgeOf();
    const card = await turn(other, { question: "Show me NNA" });
    const refused = await turn(other, { thread: card.thread, resume: { picks } });
    assert.deepEqual(asked(other), [], JSON.stringify(picks));
    assert.equal(refused.outcome.outcome, "failed");
  }
  const one = kgeOf();
  const single = await turn(one, { question: "What was NNA for June 2025?" });
  const off = await turn(one, { thread: single.thread, resume: { pick: "segment-1" } });
  assert.deepEqual([asked(one), off.outcome.outcome], [[], "failed"], "a pick the one-question card did not offer");
});

test("#714 acceptance 12: a \"Something else\" answer that leaves an item open gets a request to restate, no second card and no ask; one that leaves nothing open is asked as it is", async () => {
  for (const [question, other] of [["Show me NNA", "Show me NNA by region"], ["What was NNA for June 2025?", "What was certified NNA?"], ["What was NNA for June 2025?", "anything at all"], ["Show me NNA", "What was not certified NNA?"]]) {
    const kge = kgeOf({ readings: { ...READINGS, "anything at all": undefined } });
    const first = await turn(kge, { question });
    const answered = await turn(kge, { thread: first.thread, resume: { other } });
    assert.equal(answered.card, undefined, `${other}: no second card`);
    assert.deepEqual(asked(kge), other === "anything at all" ? [other] : [], `${other}: no ask where an item is open`);
    if (other !== "anything at all") assert.deepEqual(answered.said, [RESTATE]);
    assert.equal(answered.model.calls, 0);
  }
  const kge = kgeOf();
  const first = await turn(kge, { question: "Show me NNA" });
  const whole = await turn(kge, { thread: first.thread, resume: { other: "What was certified NNA for June 2025?" } });
  assert.deepEqual(asked(kge), ["What was certified NNA for June 2025?"], "read back through the same tools: nothing open, so it is asked as the person wrote it");
  assert.equal(whole.outcome.outcome, "answered");
  const named = kgeOf();
  const one = await turn(named, { question: "What was NNA for June 2025?" });
  await turn(named, { thread: one.thread, resume: { other: "branch" } });
  assert.deepEqual(asked(named), ["What was Branch Sales NNA for June 2025?"], "words that name one option of a one-question card are that pick");
  const saved = await savedConversation(conversations, "person-1", one.thread);
  assert.deepEqual(JSON.parse(saved.messages.find((m) => m.type === "tool" && m.name === "choose").content), { other: "branch" });
});

test("#714 acceptance 12, 13: \"What was certified NNA by region?\" is answered with no card; \"Show me NNA\" shows Meaning, Show and Period, the Period group the latest three covered periods", async () => {
  const kge = kgeOf();
  const plain = await turn(kge, { question: "What was certified NNA by region?" });
  assert.equal(plain.card, undefined, "a period ask settles by a default opens no card by itself");
  assert.deepEqual(asked(kge), ["What was certified NNA by region?"]);
  const show = await turn(kgeOf(), { question: "Show me NNA" });
  assert.deepEqual(groupsOf(show.card), ["Meaning", "Show", "Period"]);
  assert.deepEqual(optionsOf(show.card, "Period").map((o) => [o.id, o.label]), MONTHS.slice(0, 3).map((m) => [m.value, m.label]));
  assert.deepEqual(optionsOf(show.card, "Show").map((o) => o.id), ["definitions", "figures", "both"]);
  const certifiedOnly = await turn(kgeOf(), { question: "Show me certified NNA" });
  assert.deepEqual(groupsOf(certifiedOnly.card), ["Show", "Period"], "the period joins a card the output already opens");
});

test("#714 acceptance 13: with the limit set low, the items left open when it is spent appear on the card; the limit is the operators' setting", async () => {
  const question = "What was the retail NNA for Q1 2025 by segment?";
  const calls = async (limit) => {
    const kge = kgeOf();
    const done = await turn(kge, { question, settings: { cerebroCallsBeforeAsk: limit } });
    return { groups: groupsOf(done.card), made: kge.reads.length + kge.calls.length };
  };
  assert.deepEqual(await calls(3), { groups: ["Meaning", "Segment"], made: 3 }, "the reading, the name's meanings, the qualifier's filter phrases");
  assert.deepEqual(await calls(2), { groups: ["Meaning", "Segment"], made: 2 }, "the filter check is cut short: the meaning stays open, the fail-safe outcome");
  assert.deepEqual(await calls(1), { groups: ["Meaning", "Segment"], made: 1 }, "below the setting's lowest value, which startup refuses: the name's meanings are not read, so it is asked with the one meaning KGE bound it to");
  const unset = kgeOf();
  await turn(unset, { question });
  assert.equal(unset.reads.length + unset.calls.length, 3);
  assert.equal(DEFAULT_CALLS_BEFORE_ASK, 6);
  const spent = [];
  const counted = await beforeAsk(question, { limit: 0, call: { reading: async () => { spent.push(1); return { value: READINGS[question] }; } } });
  assert.deepEqual([counted, spent], [{ kind: "unread", calls: 0 }, []], "no call is made past the limit");
});

test("#714 acceptance 13: at the setting's lowest value the \"retail NNA … by segment\" card holds both the Meaning group and the Segment group", async () => {
  const lowest = appSettings({ PORTAL_CEREBRO_CALLS_BEFORE_ASK: String(CALLS_BEFORE_ASK_MIN) });
  assert.equal(lowest.cerebroCallsBeforeAsk, 2);
  const kge = kgeOf();
  const done = await turn(kge, { question: "What was the retail NNA for Q1 2025 by segment?", settings: { cerebroCallsBeforeAsk: lowest.cerebroCallsBeforeAsk } });
  assert.deepEqual(groupsOf(done.card), ["Meaning", "Segment"], "the open meaning goes on the card, with KGE's grouping item");
  assert.deepEqual(optionsOf(done.card, "Meaning").map((o) => o.words), ["Certified NNA", "Net New Assets", "Branch Sales NNA", "Client-Directed NNA"]);
  assert.deepEqual([...kge.reads, ...kge.calls].map((c) => c.name).sort(), ["get_context", "map_term"], "the read, then the name's meanings: both always run");
  assert.deepEqual(asked(kge), [], "no ask before the card");
});

// Review of PR #728, finding 6 (#707 requirement 13: when the limit is spent, the remaining open items go on the
// card). Two names, each with a qualifier that also names a filter value, in an invented vocabulary beside the first.
test("#714 acceptance 13: at the lowest limit, with two open names, the name the spent limit left unchecked goes on the card with the one meaning KGE bound it to; a call that fails within the limit puts nothing on the card", async () => {
  const AUM = [{ id: "aum-spot", label: "Spot AUM", definition: "Assets under management at the day's close.", default_alias: true }, { id: "aum-pooled", label: "Pooled AUM", definition: "Assets under management in pooled vehicles." }];
  const question = "What were retail NNA and harbour AUM in March 2025?";
  const readings = { [question]: kgeReading(question, [["retail NNA", "term", { term: "Branch Sales NNA", by: "explicit" }], ["harbour AUM", "term", { term: "Pooled AUM", by: "explicit" }], ["March 2025", "period"]], []) };
  const kgeFor = (meanings = { NNA, AUM }) => kgeOf({ readings, filters: { retail: FILTERS.retail, harbour: ["Harbour Desk"] }, meanings });
  const at = async (limit, kge = kgeFor()) => {
    const done = await turn(kge, { question, settings: { cerebroCallsBeforeAsk: limit } });
    return { done, kge, groups: (done.card?.groups ?? []).map((group) => [group.label, group.options.map((option) => option.words)]), made: [...kge.reads, ...kge.calls].map((c) => `${c.name}:${c.args.term ?? c.args.query ?? ""}`).sort() };
  };
  const all = ["Certified NNA", "Net New Assets", "Branch Sales NNA", "Client-Directed NNA"];
  const whole = await at(DEFAULT_CALLS_BEFORE_ASK);
  assert.deepEqual(whole.groups, [["Meaning of retail NNA", all], ["Meaning of harbour AUM", ["Spot AUM", "Pooled AUM"]]], "within the limit, each name with every meaning KGE lists");
  assert.deepEqual(whole.made, ["get_context:", "map_term:AUM", "map_term:NNA", "search:harbour", "search:retail"]);
  const lowest = await at(CALLS_BEFORE_ASK_MIN);
  assert.deepEqual(lowest.made, ["get_context:", "map_term:NNA"], "the limit is kept: the read and the first name's meanings");
  assert.deepEqual(lowest.groups, [["Meaning of retail NNA", all], ["Meaning of harbour AUM", ["Pooled AUM"]]],
    "the second name is not dropped: it is asked with the option it has, the meaning KGE bound it to, beside \"Something else\"");
  assert.deepEqual(asked(lowest.kge), [], "no ask before the card");
  const [first, second] = lowest.done.card.groups;
  const sent = await turn(lowest.kge, { thread: lowest.done.thread, resume: { picks: { [first.id]: "certified-nna", [second.id]: second.options[0].id } } });
  assert.deepEqual(asked(lowest.kge), ["What were retail Certified NNA and Pooled AUM in March 2025?"], "confirming the bound meaning puts its name in the phrase's place; another meaning's pick keeps the person's qualifier");
  assert.equal(sent.model.calls, 0);
  const three = await at(3);
  assert.deepEqual([three.made, three.groups.map(([label, options]) => [label, options.length])], [["get_context:", "map_term:NNA", "search:retail"], [["Meaning of retail NNA", 4], ["Meaning of harbour AUM", 1]]], "one call more checks the first name's qualifier; the second name is still asked");
  const four = await at(4);
  assert.deepEqual(four.groups.map(([label, options]) => [label, options.length]), [["Meaning of retail NNA", 4], ["Meaning of harbour AUM", 2]], "its meanings read, the qualifier's check cut short: open, with every meaning");
  // The unchecked name written with a second qualifier, bound to another term: each is asked, neither is dropped.
  const twice = "What were retail NNA, harbour AUM and vault AUM in March 2025?";
  const both = kgeOf({ readings: { [twice]: kgeReading(twice, [["retail NNA", "term", { term: "Branch Sales NNA", by: "explicit" }], ["harbour AUM", "term", { term: "Pooled AUM", by: "explicit" }], ["vault AUM", "term", { term: "Spot AUM", by: "explicit" }], ["March 2025", "period"]], []) },
    filters: { retail: FILTERS.retail, harbour: ["Harbour Desk"], vault: ["Vault Desk"] }, meanings: { NNA, AUM } });
  const threeNames = await turn(both, { question: twice, settings: { cerebroCallsBeforeAsk: CALLS_BEFORE_ASK_MIN } });
  assert.deepEqual(threeNames.card.groups.map((group) => [group.label, group.options.map((option) => option.words)]),
    [["Meaning of retail NNA", all], ["Meaning of harbour AUM", ["Pooled AUM"]], ["Meaning of vault AUM", ["Spot AUM"]]], "the same unread name, written twice: both go on the card");
  assert.equal(both.reads.length + both.calls.length, CALLS_BEFORE_ASK_MIN);
  // One unchecked name alone is the one-question card, with its one option.
  const lone = "What was harbour AUM in March 2025?";
  const alone = kgeOf({ readings: { [lone]: kgeReading(lone, [["harbour AUM", "term", { term: "Pooled AUM", by: "explicit" }], ["March 2025", "period"]], []) }, filters: {}, meanings: { AUM } });
  const one = await turn(alone, { question: lone, settings: { cerebroCallsBeforeAsk: 1 } });
  assert.deepEqual([one.card.source, one.card.question, one.card.options.map((option) => option.label), one.card.other], ["meaning", "Which meaning of AUM do you intend?", ["Pooled AUM"], true]);
  await turn(alone, { thread: one.thread, resume: { pick: one.card.options[0].id } });
  assert.deepEqual(asked(alone), ["What was Pooled AUM in March 2025?"]);
  // A failed call is not a spent limit (#710's fail-safe rule): the name stays as KGE bound it, and nothing is asked of it.
  const failing = kgeStub((name, args) => (name === "ask" ? { value: FIGURE } : name === "map_term" && args.term === "AUM" ? { error: "KGE could not answer." } : name === "map_term" ? { value: { term: args.term, resolves_to: NNA } } : { value: {} }),
    { step: stepReads((asked) => readings[asked], (query) => ({ retail: FILTERS.retail, harbour: ["Harbour Desk"] })[query.toLowerCase()] ?? []) });
  const failed = await at(DEFAULT_CALLS_BEFORE_ASK, failing);
  assert.deepEqual([failed.done.card.source, failed.done.card.question], ["meaning", CARD_QUESTION], "the first name's card alone");
});

test("#714 acceptance 14: a period with no data is said at once, naming the latest covered period, with no card and no ask; a person who may not read the unit gets KGE's refusal and no card", async () => {
  const kge = kgeOf();
  const late = await turn(kge, { question: "What was the retail NNA for Q1 2026 by segment?" });
  assert.equal(late.card, undefined);
  assert.deepEqual(late.said, ["There is no data for Q1 2026. The latest covered period is June 2025."]);
  assert.deepEqual([asked(kge), kge.calls.length, late.model.calls], [[], 0, 0], "no ask, no other call, no model call: the order is refusal, coverage, then the card");
  for (const question of ["What was NNA for June 2025?", "Show me NNA", "What was the retail NNA for Q1 2026 by segment?"]) {
    const closed = kgeOf({ readings: { [question]: NO_WEALTH }, meanings: {}, filters: {} });
    const refused = await turn(closed, { question, roles: ["business"] });
    assert.equal(refused.card, undefined, "no card");
    assert.deepEqual(refused.said, ["analyst_retail.meridian@example.com may not read (withheld)."], "KGE's refusal, in KGE's words");
    assert.doesNotMatch(JSON.stringify(refused.values.messages), /Certified|Branch Sales|Net New Assets|Segment level/, "the refusal stands alone and reveals nothing");
    assert.deepEqual([asked(closed), closed.calls.length, closed.reads.length], [[], 0, 1]);
  }
  const wordless = await beforeAsk("q", { call: { reading: async () => ({ value: { bindings: [], open_items: [{ item: "access", resolution: "unanswerable" }] } }) } });
  assert.equal(wordless.message, REFUSED);
  const reasoned = kgeOf({ readings: { "Show me NNA for September": kgeReading("Show me NNA for September", [bare()], [open(), periodUncovered({ reason: "Please give a valid period: september." })]) } });
  assert.deepEqual((await turn(reasoned, { question: "Show me NNA for September" })).said, ["Please give a valid period: september. The latest covered period is June 2025."], "ask's own reason where it gives one");
});

test("#714 P3, P4: a question item does not stop the card and opens none; a meaning item is ask's own clarification and goes to ask", async () => {
  const kge = kgeOf();
  const negated = await turn(kge, { question: "What was not certified NNA?" });
  assert.equal(negated.card, undefined, "KGE reports no open meaning: nothing is asked, and ask declines in its own words");
  assert.deepEqual(asked(kge), ["What was not certified NNA?"]);
  const spelled = kgeOf({ ask: () => ({ value: { behaviour: "clarify", reason: "Which N?", clarification: { choices: [{ id: "a", label: "N one" }, { id: "b", label: "N two" }] } } }) });
  const clarified = await turn(spelled, { question: "What was N N A?" });
  assert.deepEqual(asked(spelled), ["What was N N A?"], "no card before ask");
  assert.deepEqual([clarified.card.source, clarified.card.question], ["clarify", "Which N?"], "ask clarifies as today, on its own card");
});

test("#714: a time mode the person wrote before the name settles it; a grouping word alone is a one-question card that sends a phrase ask reads", async () => {
  const kge = kgeOf();
  const current = await turn(kge, { question: "What was current NNA?" });
  assert.equal(current.card, undefined, "\"current NNA\" stays current: the mode is the bound meaning's own");
  assert.deepEqual(asked(kge), ["What was current NNA?"]);
  const grouped = kgeOf();
  const first = await turn(grouped, { question: "What was certified NNA for Q1 2025 by segment?" });
  assert.deepEqual([first.card.source, first.card.question, first.card.options.length], ["grouping", "Which segment do you mean?", 10]);
  await turn(grouped, { thread: first.thread, resume: { pick: "dimension-10" } });
  assert.deepEqual(asked(grouped), ["What was certified NNA for Q1 2025 by client segment name?"]);
});

test("#714: each role that may ask gets the step; a failed or missing reading leaves the turn as it was; another person cannot answer the card", async () => {
  for (const roles of [["business"], ["steward"], ["engineer"]]) {
    const done = await turn(kgeOf(), { question: "What was NNA for June 2025?", roles });
    assert.equal(done.card?.source, "meaning", roles.join());
  }
  for (const reading of [() => ({ status: 503 }), () => ({ value: {} }), () => ({ error: "400: no such argument" }), () => { throw new Error("down"); }]) {
    const kge = kgeStub((name, args) => (name === "ask" ? { value: FIGURE } : { value: {} }), { step: reading });
    const done = await turn(kge, { question: "What was NNA for June 2025?" });
    assert.equal(done.card, undefined);
    assert.deepEqual(asked(kge), ["What was NNA for June 2025?"], "the turn goes on with no step");
  }
  const kge = kgeOf();
  const first = await turn(kge, { question: "What was NNA for June 2025?" });
  await assert.rejects(turn(kge, { thread: first.thread, resume: { pick: "certified-nna" }, principal: "person-2" }), { status: 404 });
  assert.deepEqual(asked(kge), []);
});

// Review of PR #728, finding 7: the step never fails the turn, and a fault in it is never unseen.
test("#714 (review, finding 7): a step that throws leaves the turn as it was and is logged with the error's type; the person's Stop is not logged; a KGE that answers with no reading is no error", async (context) => {
  const logged = [];
  context.mock.method(console, "error", (line) => logged.push(line));
  const question = "What was NNA for June 2025?";
  const fault = async () => { throw new TypeError("Cannot read properties of undefined (reading 'toLowerCase')"); };
  const broken = kgeOf();
  const model = asksAsWritten();
  const events = [];
  const runTurn = cerebroAgent({ model, kge: broken, conversations, retryAfterMs: 0, openQuestions: fault });
  for await (const event of runTurn({ thread: globalThis.crypto.randomUUID(), messages: [{ type: "human", content: question }], roles: ["business"], signal: new AbortController().signal, req: { portalIdentity: { principal: "person-1" } } })) events.push(event);
  assert.equal(events.filter(([e]) => e === "values").at(-1)[1].__interrupt__, undefined, "no card");
  assert.deepEqual(asked(broken), [question], "the turn goes on with no step");
  assert.deepEqual(logged, ["Cerebro step before ask not read: TypeError: Cannot read properties of undefined (reading 'toLowerCase')"], "one line, naming the step, the error's type and its message");
  logged.length = 0;
  for (const reading of [() => ({ status: 503 }), () => ({ value: {} }), () => ({ error: "400: no such argument" }), () => { throw new Error("down"); }]) await turn(kgeStub((name) => (name === "ask" ? { value: FIGURE } : { value: {} }), { step: reading }), { question });
  assert.deepEqual(logged, [], "a read KGE fails or does not hold is the tool's own result (tools.js), not a fault of the step");
  const stop = new AbortController();
  const stopped = cerebroAgent({ model: asksAsWritten(), kge: kgeOf(), conversations, retryAfterMs: 0, openQuestions: async () => { stop.abort(); throw new Error("stopped"); } });
  await assert.rejects((async () => { for await (const event of stopped({ thread: globalThis.crypto.randomUUID(), messages: [{ type: "human", content: question }], roles: ["business"], signal: stop.signal, req: { portalIdentity: { principal: "person-1" } } })) void event; })());
  assert.deepEqual(logged.filter((line) => line.startsWith("Cerebro step before ask")), [], "a Stop is the person's, not a fault");
});

// Review of PR #728, finding 5 (#707 requirement 8: a golden question is asked as written; requirement 13: a
// "Something else" answer is read back through the same tools). The golden question here has an open meaning in
// KGE's reading, so without the exemption its read-back would ask the person to restate.
test("#714 (review, finding 5): a \"Something else\" answer that is a golden question is asked as written, with no read of KGE and no request to restate; the same answer where it is not golden is read back", async () => {
  const golden = { sets: [{ set: "sage", questions: ["What was NNA for June 2025?"], golden20: [] }] };
  const may = "What was NNA for May 2025?";
  const readings = { ...READINGS, [may]: kgeReading(may, [bare(), ["May 2025", "period"]], [open()]) };
  for (const [first, card] of [["Show me NNA", QUESTIONS], [may, "meaning"]]) {
    for (const other of ["What was NNA for June 2025?", "  what was NNA for june 2025  "]) {
      const kge = kgeOf({ golden, readings });
      const shown = await turn(kge, { question: first });
      assert.equal(shown.card?.source, card, first);
      const reads = kge.reads.length;
      const sent = await turn(kge, { thread: shown.thread, resume: { other } });
      assert.equal(kge.reads.length, reads, `${first} / ${other}: no step read for a golden question`);
      assert.deepEqual([asked(kge), sent.card, sent.said.includes(RESTATE), sent.model.calls], [[other.trim()], undefined, false, 0], "asked as written: no card, no request to restate, no model call");
    }
    // The same answer where the pack serves no such golden question: read back, its meaning is open, so restate.
    const plain = kgeOf({ readings });
    const shown = await turn(plain, { question: first });
    const reads = plain.reads.length;
    const back = await turn(plain, { thread: shown.thread, resume: { other: "What was NNA for June 2025?" } });
    assert.ok(plain.reads.length > reads, "not golden: it is read back through KGE");
    assert.deepEqual([asked(plain), back.said.at(-1)], [[], RESTATE], "and, a meaning still open, the person is asked to restate, with no ask");
  }
});

test("#714 requirement 11: the prompt carries the shape rule, and a model's tool list holds no meaning check of its own", async () => {
  const kge = kgeOf();
  const done = await turn(kge, { question: "certified Q2 2025 NNA" });
  const prompt = done.model.read[0][0].content;
  assert.ok(prompt.includes(SHAPE_RULE));
  assert.match(SHAPE_RULE, /shape for ask, never its meaning: every qualifier you send .* comes from the person's words or their pick/);
  assert.doesNotMatch(prompt, /discover_meanings/);
});

test("#714: an invented vocabulary reads the same way: a default alias is open, a qualifier that also names a filter keeps the card, an own name is settled", async () => {
  const AUM = [{ id: "aum-spot", label: "Spot AUM", definition: "Assets under management at the day's close.", default_alias: true }, { id: "aum-pooled", label: "Pooled AUM", definition: "Assets under management in pooled vehicles." }];
  const readings = {
    "Total AUM in March 2025": kgeReading("Total AUM in March 2025", [["Total", "operator"], ["AUM", "term", { term: "Spot AUM", by: "default_alias" }], ["March 2025", "period"]], [["term", "default", { text: "AUM", options: AUM }]]),
    "harbour AUM in March 2025": kgeReading("harbour AUM in March 2025", [["harbour AUM", "term", { term: "Pooled AUM", by: "explicit" }], ["March 2025", "period"]], []),
    "Pooled AUM in March 2025": kgeReading("Pooled AUM in March 2025", [["Pooled AUM", "term", { term: "Pooled AUM", by: "explicit" }], ["March 2025", "period"]], []),
    "vault AUM in March 2025": kgeReading("vault AUM in March 2025", [["vault AUM", "term", { term: "Pooled AUM", by: "explicit" }], ["March 2025", "period"]], []),
  };
  const kge = () => kgeOf({ readings, filters: { harbour: ["Harbour Desk"] }, meanings: { AUM } });
  const bareOne = await turn(kge(), { question: "Total AUM in March 2025" });
  assert.equal(bareOne.card.question, "Which meaning of AUM do you intend?", "no option carries the temporary rule's word, so none is suggested or marked");
  assert.deepEqual(bareOne.card.options.map((o) => o.label), ["Spot AUM — Assets under management at the day's close.", "Pooled AUM — Assets under management in pooled vehicles."]);
  const two = kge();
  const double = await turn(two, { question: "harbour AUM in March 2025" });
  assert.equal(double.card.source, "meaning", "\"harbour\" also names a filter value");
  await turn(two, { thread: double.thread, resume: { pick: "aum-spot" } });
  assert.deepEqual(asked(two), ["harbour Spot AUM in March 2025"], "another meaning's pick keeps the person's qualifier");
  const same = kge();
  await turn(same, { thread: (await turn(same, { question: "harbour AUM in March 2025" })).thread, resume: { pick: "aum-pooled" } });
  assert.deepEqual(asked(same), ["Pooled AUM in March 2025"]);
  for (const question of ["Pooled AUM in March 2025", "vault AUM in March 2025"]) {
    const settled = kge();
    assert.equal((await turn(settled, { question })).card, undefined, question);
    assert.deepEqual(asked(settled), [question]);
  }
});

test("#714 (review): a grouping word after another grouping operator takes the operator's place with a whole phrase; two mentions of one name stay two groups", async () => {
  const per = "Tell me about NNA per segment";
  const each = "What was certified NNA in each segment for Q1 2025?";
  const mixed = "Compare NNA and retail NNA for June 2025";
  const readings = {
    [per]: kgeReading(per, [bare(), ["per", "operator"], ["segment", "unread"]], [open(), periodDefault(), ["grouping", "clarify", { text: "segment", options: SEGMENTS }]]),
    [each]: kgeReading(each, [certified("certified NNA"), ["in", "operator"], ["each", "operator"], ["segment", "unread"], ["Q1 2025", "period"]], [["grouping", "clarify", { text: "segment", options: SEGMENTS }]]),
    [mixed]: kgeReading(mixed, [["Compare", "comparison"], bare(), ["retail NNA", "term", { term: "Branch Sales NNA", by: "explicit" }], ["June 2025", "period"]], [open()]),
  };
  const one = kgeOf({ readings });
  const first = await turn(one, { question: per });
  assert.deepEqual(groupsOf(first.card), ["Meaning", "Show", "Period", "Segment"]);
  await turn(one, { thread: first.thread, resume: { picks: { meaning: "certified-nna", show: "figures", period: "2025-06", grouping: "dimension-6" } } });
  assert.deepEqual(asked(one), ["What was Certified NNA by client segment for June 2025?"], "never \"per by …\"");
  const two = kgeOf({ readings });
  const second = await turn(two, { question: each });
  await turn(two, { thread: second.thread, resume: { pick: "dimension-10" } });
  assert.deepEqual(asked(two), ["What was certified NNA by client segment name for Q1 2025?"]);
  const three = kgeOf({ readings, ask: () => ({ value: FIGURE }) });
  const third = await turn(three, { question: mixed });
  assert.deepEqual(third.card.groups.map((group) => [group.id, group.label]), [["meaning", "Meaning of NNA"], ["meaning-2", "Meaning of retail NNA"], ["show", "Show"]]);
  await turn(three, { thread: third.thread, resume: { picks: { meaning: "certified-nna", "meaning-2": "branch-sales-nna", show: "figures" } } });
  assert.deepEqual(asked(three), ["Compare Certified NNA and Branch Sales NNA for June 2025"]);
});

test("#714 (review): a refusal comes before coverage; a read-back KGE cannot read is asked as written; more than six open groups keep the first six; the card is kept with the group its pick is checked against", async () => {
  const both = await beforeAsk("q", { call: { reading: async () => ({ value: { bindings: [], open_items: [periodUncovered()[2] && { item: "period", resolution: "unanswerable", latest: MONTHS[0], options: MONTHS }, { item: "access", resolution: "unanswerable", reason: "No." }, { item: "term", resolution: "default", text: "q", start: 0, end: 1, options: NNA }] } }) } });
  assert.deepEqual([both.kind, both.message], ["refused", "No."], "#707 requirement 14: refusal, then coverage, then the card");
  let reads = 0;
  const flaky = kgeStub((name) => (name === "ask" ? { value: FIGURE } : { value: {} }), { step: (name, args) => ((reads += 1) === 1 ? { value: READINGS["What was NNA for June 2025?"] } : { status: 503 }) });
  const first = await turn(flaky, { question: "What was NNA for June 2025?" });
  const again = await turn(flaky, { thread: first.thread, resume: { other: "What was the frozen NNA for June 2025?" } });
  assert.deepEqual([asked(flaky), again.card, again.said], [["What was the frozen NNA for June 2025?"], undefined, []], "the fail-safe rule: the turn goes on as it would with no step");
  const many = "A1 A2 A3 A4 A5 A6 A7 in June 2025";
  const names = many.split(" ").slice(0, 7);
  const crowded = kgeOf({ readings: { [many]: kgeReading(many, names.map((name) => [name, "term", { term: `${name} one`, by: "default_alias" }]), names.map((name) => ["term", "default", { text: name, options: [{ label: `${name} one` }, { label: `${name} two` }] }])) } });
  const card = await turn(crowded, { question: many });
  assert.deepEqual(card.card.groups.map((group) => group.label), names.slice(0, 6).map((name) => `Meaning of ${name}`));
  const kge = kgeOf();
  const paused = await turn(kge, { question: "What was NNA for June 2025?" });
  const { conversationKey, ownerOf } = await import("../src/conversations/index.js");
  const saved = await store.read(conversationKey(ownerOf("person-1"), paused.thread));
  assert.match(saved.body.toString(), /"group"/, "the card is kept with the group its pick is checked against");
});

// #714 (independent review of 12d907dd, finding 1): a question whose request words read as definitions or another
// output is not one the step fills. The readings are KGE's own for these questions, as the review recorded them on
// Meridian's build: a bare name, a default period, and ask's decline as a question item.
const CATALOGUE = "Ask answers governed figures and definitions. Use the product page or Search in the catalogue.";
const unreadWord = (word) => `“${word}” in the question matches no certified definition, so it cannot be applied. No figure is given.`;
const ELSEWHERE = Object.fromEntries([
  ["Who is the steward of NNA?", [["steward", "unread"], bare()], [open(), declined(CATALOGUE), periodDefault()]],
  ["Which tables hold NNA?", [["tables", "unread"], bare()], [open(), declined(CATALOGUE), periodDefault()]],
  ["Tell me about NNA definitions", [bare(), ["definitions", "unread"]], [open(), declined(unreadWord("definitions")), periodDefault()]],
  ["Show me the definition of NNA by region", [["definition", "unread"], bare(), ["by region", "grouping", { label: "office.region_code" }]], [open(), declined(unreadWord("definition")), periodDefault()]],
  ["Where does NNA come from?", [bare(), ["come", "unread"]], [open(), declined(unreadWord("come")), periodDefault()]],
  ["Show me NNA broken down by source system", [bare(), ["by source system", "grouping", { label: "source_system" }]], [open(), periodDefault()]],
  ["What was the NNA total in the steward's table?", [bare(), ["steward", "unread"], ["table", "unread"]], [open(), declined(unreadWord("steward")), periodDefault()]],
].map(([question, bindings, items]) => [question, kgeReading(question, bindings, items)]));

test("#714 (review, finding 1): a question for definitions or another output gets no Period group and no card: the model chooses its tool, as before the step, and nothing ends at ask's decline after a card", async () => {
  const stepOf = (question, options = {}) => beforeAsk(question, { call: { reading: async () => ({ value: ELSEWHERE[question] }), meanings: async () => ({ value: { resolves_to: NNA } }), filters: async () => ({ value: { hits: [] } }) }, ...options });
  for (const question of ["Who is the steward of NNA?", "Which tables hold NNA?", "Tell me about NNA definitions", "Show me the definition of NNA by region"]) {
    const kge = kgeOf({ readings: ELSEWHERE });
    const done = await turn(kge, { question });
    assert.equal(done.card, undefined, `${question}: no card, so no Period group`);
    assert.equal(done.model.calls, 1, `${question}: the model reads the question and chooses its tool`);
    assert.deepEqual(asked(kge), [question], `${question}: what the model sends is the person's own question, with no period and no pick added`);
    assert.deepEqual(kge.reads.map((r) => r.name), ["get_context"], `${question}: one read, and no call for the meanings of a card that is not shown`);
    const step = await stepOf(question);
    assert.deepEqual([step.kind, step.calls, step.groups], ["elsewhere", 1, undefined], question);
  }
  const other = standIn((messages) => (messages.some((m) => m._getType() === "tool") ? { say: "Done." } : { call: ["search", { query: "NNA steward" }] }));
  const kge = kgeOf({ readings: ELSEWHERE });
  const searched = await turn(kge, { question: "Who is the steward of NNA?", model: other });
  assert.deepEqual([searched.card, asked(kge), kge.calls.map((c) => c.name)], [undefined, [], ["search"]], "the model's own tool answers it: no card and no ask");
  // No request word reads this one, and KGE's decline has the shape of any unread word's ("by Finance", acceptance 9),
  // so its card stays: the portal cannot tell it without a word list.
  assert.deepEqual(groupsOf((await turn(kgeOf({ readings: ELSEWHERE }), { question: "Where does NNA come from?" })).card), ["Meaning", "Period"]);
  // The card is the fail-safe side: a request word in a place KGE bound is KGE's reading, and a question that also
  // asks for a figure in the request words keeps the step.
  assert.deepEqual(groupsOf((await turn(kgeOf({ readings: ELSEWHERE }), { question: "Show me NNA broken down by source system" })).card), ["Meaning", "Period"], "\"source\" is in the grouping KGE bound");
  assert.deepEqual(groupsOf((await turn(kgeOf({ readings: ELSEWHERE }), { question: "What was the NNA total in the steward's table?" })).card), ["Meaning", "Period"], "a figure is asked for");
  // A "Something else" answer read back is not read this way: what is open is still open, and is restated.
  assert.equal((await stepOf("Who is the steward of NNA?", { cardNeeded: true })).kind, "open");
  const first = await turn(kgeOf(), { question: "What was NNA for June 2025?" });
  const kgeBack = kgeOf({ readings: { ...READINGS, ...ELSEWHERE } });
  const back = await turn(kgeBack, { thread: first.thread, resume: { other: "Who is the steward of NNA?" } });
  assert.deepEqual([back.said.at(-1), asked(kgeBack), back.card], [RESTATE, [], undefined]);
});
