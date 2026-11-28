import assert from "node:assert/strict";
import { test } from "node:test";
import { readFileSync } from "node:fs";
import { cerebroAgent } from "../src/cerebro/agent.js";
import { openConversations } from "../src/conversations/index.js";
import { kgeStub, standIn } from "./fixtures/cerebro-stand-in.js";

// #697, #700, #707 requirement 8 (#714): every showcase question of the example pack (the golden 20, the fixture KGE's
// unit lane holds equal to the pack: tests/unit/test_golden20_questions.py) through Cerebro's one step before ask,
// offline. KGE's reading of each is the evaluation suite's (recorded from the example's build). With the pack's golden
// list served, a golden question is exempt: asked as written, with no card and no read of KGE's reading, decided once
// at the step. With none served (an install whose pack declares none), no question is exempt, and each is still
// answered with no card, because KGE reads the meaning every one of them names: the cards kit 0.14.58 showed for S07,
// S08, S12 and S24 (a segment name between "certified" and the term, two meanings on one mention, a later bare
// mention) are gone, which is what #707 was raised for.
const { items, questions } = JSON.parse(readFileSync(new URL("./fixtures/cerebro-showcase-questions.json", import.meta.url), "utf8"));
const suite = JSON.parse(readFileSync(new URL("./fixtures/cerebro-evaluation-suite.json", import.meta.url), "utf8"));
const wordings = new Map(suite.wordings.map((wording) => [wording.question, wording]));
const FIGURE = { behaviour: "figure", answer_mode: "certified", answer_text: "Certified: 42.", figure: 42, sql: "SELECT 42" };

/** The example KGE: the suite's reading of a question it holds, as written; with golden, GET /golden serves the pack's
 *  golden questions, as KGE does for the golden 20. */
function exampleKge({ golden } = {}) {
  const reading = (question) => {
    const held = wordings.get(question)?.kge.reading;
    return held && { bindings: held.bindings, open_items: held.open_items.map((item) => ({ ...item, options: suite.kge_options[item.options] })) };
  };
  return kgeStub((name, args) => (name === "ask" ? { value: FIGURE } : name === "map_term" ? { value: { term: args.term, resolves_to: suite.kge_tools.map_term[args.term] ?? [] } } : { value: {} }),
    { step: (name, args) => (name === "get_context" ? { value: reading(args.question) ?? { bindings: [], open_items: [] } } : { value: { hits: (suite.kge_tools.search_filter[args.query] ?? []).map((title) => ({ kind: "filter", title, restricted: false })) } }),
      ...(golden ? { golden: { sets: [{ set: "sage", questions, golden20: [] }] } } : {}) });
}
const asksAsWritten = (question) => standIn((messages, n) => (n === 1 ? { call: ["ask", { question }] } : { say: "unused" }));
async function asked(kge, question) {
  const model = asksAsWritten(question);
  const run = cerebroAgent({ model, kge, conversations: openConversations(), retryAfterMs: 0 });
  const events = [];
  for await (const event of run({ thread: globalThis.crypto.randomUUID(), req: { portalIdentity: { principal: "analyst" } }, roles: ["business"],
    messages: [{ type: "human", content: question }] })) events.push(event);
  const values = events.filter(([name]) => name === "values").at(-1)?.[1];
  const outcome = events.filter(([name]) => name === "custom").map(([, data]) => data).filter((data) => data.kind === "outcome").at(-1);
  return { values, outcome, model, card: values.__interrupt__?.[0]?.value, asks: kge.calls.filter((c) => c.name === "ask").map((c) => c.args.question) };
}

test("with no golden list served, every showcase question is answered as written from KGE's own reading, with no card and never a refusal", async () => {
  assert.equal(questions.length, 20);
  for (const [at, question] of questions.entries()) {
    const kge = exampleKge();
    const { card, outcome, asks, model } = await asked(kge, question);
    const label = `${items[at]}: ${question}`;
    assert.ok(wordings.has(question), `the suite holds KGE's reading of ${label}`);
    assert.deepEqual(kge.gets.map((get) => get.path), ["/golden"], "KGE serves no showcase: GET /golden is a 404 here");
    assert.equal(card, undefined, `no card: ${label}`);
    assert.equal(outcome.outcome, "answered", `${label}: ${JSON.stringify(outcome.error)}`);
    assert.deepEqual(asks, [question], label);
    assert.deepEqual([kge.reads.map((read) => read.name), model.calls], [["get_context"], 1], `one read of KGE's reading settles it, with no other call: ${label}`);
  }
});

// Owner, 2026-11-27: a question that is exactly one of the pack's golden questions (case, punctuation and spacing
// folded) is asked as written; a rewording follows the rule.
test("#707 acceptance 8: with the pack's golden list served, every golden question is asked as written with no card and no step, however its case and spacing", async () => {
  for (const [variant, form] of [["as written", (q) => q], ["lower case", (q) => q.toLowerCase()], ["spacing and punctuation", (q) => ` ${q.replace(/[.?]$/, "")}  `]]) {
    for (const [at, written] of questions.entries()) {
      const question = form(written);
      const label = `${items[at]} ${variant}: ${question}`;
      const kge = exampleKge({ golden: true });
      const { card, outcome, asks } = await asked(kge, question);
      assert.equal(card, undefined, label);
      assert.equal(outcome.outcome, "answered", `${label}: ${JSON.stringify(outcome.error)}`);
      assert.deepEqual(asks, [question], label);
      assert.deepEqual(kge.reads, [], `the exemption reads nothing: ${label}`);
      assert.equal(kge.calls.some((c) => c.name === "search" || c.name === "map_term"), false, label);
    }
  }
});

test("with the golden list served, a question that is not golden follows the rule: an open meaning shows the card, a period with no data is said", async () => {
  const kge = exampleKge({ golden: true });
  const bare = await asked(kge, "What was Q3 2024 NNA by business line?");
  assert.equal(bare.card?.source, "meaning", "a bare name after a period still asks for its meaning before any answer");
  assert.deepEqual(bare.card.options.map((option) => option.id), ["certified-nna", "net-new-assets", "branch-sales-nna", "client-directed-nna"]);
  assert.deepEqual([bare.asks, bare.model.calls], [[], 0]);
  const late = exampleKge({ golden: true });
  const reworded = await asked(late, "Show me certified Investor Services NNA by sub-segment and quarter for Q1 through Q3 2025.");
  assert.deepEqual([reworded.card, reworded.asks], [undefined, []], "a rewording of S07 past the latest covered month: no card and no ask");
  assert.match(reworded.values.messages.at(-1).content, /not released yet.*latest released month is June 2025/);
});
