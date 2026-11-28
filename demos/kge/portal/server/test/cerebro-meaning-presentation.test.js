import { test } from 'node:test';
import assert from 'node:assert/strict';
import { presentMeaningReply } from '../src/cerebro/meaningPresentation.js';
const selected = { id: 'f', term: 'Certified NNA' };
const other = { id: 'c', term: 'Net New Assets' };
const card = { original: 'NNA for Q1 2025?', spans: [{ start: 0, end: 3 }], options: [selected, other] };
const defs = [{ term: 'Certified NNA', definition: 'Finance frozen assets.' }, { term: 'Net New Assets', definition: 'Current assets.' }];
const text = 'Certified: Certified NNA: USD 42.\nCertified NNA: Finance frozen assets.\nNet New Assets: Current assets. [1]';
const figure = { behaviour: 'figure', figure: 42, sql: 'SELECT 42', history: [{ turn: 'one' }], context: { term: 'Certified NNA' },
  certified_definitions: defs, answer_text: text, display: { headline: { text }, table: { rows: [[42]] } } };
test('selected display removes only exact other definition, preserving immutable raw and evidence', () => {
  const before = structuredClone(figure);
  const shown = presentMeaningReply(figure, card, selected);
  assert.deepEqual(figure, before);
  assert.equal(shown.answer_text, 'Certified: Certified NNA: USD 42.\nCertified NNA: Finance frozen assets.');
  assert.equal(shown.display.headline.text, shown.answer_text);
  assert.deepEqual(shown.certified_definitions, [defs[0]]);
  for (const field of ['figure', 'sql', 'history', 'context']) assert.deepEqual(shown[field], figure[field]);
  assert.deepEqual(shown.display.table, figure.display.table);
});
test('explicit comparison and unreleased decline stay unchanged', () => {
  const comparison = { ...card, original: 'Compare NNA with Net New Assets', spans: [{ start: 8, end: 11 }] };
  assert.equal(presentMeaningReply(figure, comparison, selected), figure);
  assert.equal(presentMeaningReply(figure, { ...card, explicitScopes: true }, selected), figure);
  const combined = { ...figure, context: null };
  assert.equal(presentMeaningReply(combined, { ...card, explicitScopes: true }, selected), combined);
  const decline = { behaviour: 'abstain_with_reason', answer_text: 'Not released yet.', figure: null };
  assert.equal(presentMeaningReply(decline, card, selected), decline);
});
test('unrecognized prose or mismatched context fails safely and never rewrites guesses', () => {
  assert.equal(presentMeaningReply({ ...figure, answer_text: `${text}\nOther wording: Current assets.` }, card, selected), undefined);
  assert.equal(presentMeaningReply({ ...figure, answer_text: `${text}\nNet New Assets: Other explanation.` }, card, selected), undefined);
  assert.equal(presentMeaningReply({ ...figure, certified_definitions: [defs[0]] }, card, selected), undefined);
  assert.equal(presentMeaningReply({ ...figure, context: { term: 'Net New Assets' } }, card, selected), undefined);
});


test('pure definitions use their selected structured term without requiring a figure context', () => {
  const certified = { behaviour: 'glossary_definition', term: 'Certified NNA', context: null,
    definition: 'Finance frozen assets.', certified_definitions: defs,
    answer_text: 'Certified NNA: Finance frozen assets.' };
  const shown = presentMeaningReply(certified, card, selected);
  assert.equal(shown.answer_text, certified.answer_text);
  assert.deepEqual(shown.certified_definitions, [defs[0]]);
  const current = { behaviour: 'cite_certified', context: null, certified_definitions: [defs[1]], answer_text: 'Net New Assets: Current assets.' };
  assert.deepEqual(presentMeaningReply(current, card, other), current);
  assert.equal(presentMeaningReply({ ...current, certified_definitions: defs }, card, other), undefined);
});

test('server-only original replies follow rewind, count and byte retention of their Ask messages', async () => {
  const { HumanMessage, ToolMessage } = await import('@langchain/core/messages');
  const { turnInput, MAX_MESSAGES, CONVERSATION_MAX_BYTES } = await import('../src/cerebro/agent.js');
  const messages = [], originals = [];
  for (let n = 0; n < 110; n += 1) {
    messages.push(new HumanMessage({ id: `h${n}`, content: `question ${n}` }));
    messages.push(new ToolMessage({ id: `m${n}`, name: 'ask', tool_call_id: `a${n}`, content: '{}' }));
    originals.push({ callId: `a${n}`, reply: { answer_text: `answer ${n}` } });
  }
  const next = new HumanMessage({ id: 'next', content: 'next question' });
  const trimmed = turnInput({ messages, rawMeaningReplies: originals }, next);
  assert.ok(trimmed.rawMeaningReplies.length < originals.length);
  assert.ok(trimmed.messages.length <= MAX_MESSAGES + 1);
  assert.equal(trimmed.rawMeaningReplies.at(-1).callId, 'a109');
  assert.notEqual(trimmed.rawMeaningReplies[0].callId, 'a0');
  const rewound = turnInput({ messages, rawMeaningReplies: originals }, next, 'h2');
  assert.deepEqual(rewound.rawMeaningReplies.map((item) => item.callId), ['a0', 'a1']);
  const oversized = [{ callId: 'a0', reply: { answer_text: 'x'.repeat(CONVERSATION_MAX_BYTES + 1) } }];
  const bySize = turnInput({ messages: messages.slice(0, 4), rawMeaningReplies: oversized }, next);
  assert.deepEqual(bySize.rawMeaningReplies, []);
});


test('a numeric response without selected provenance never passes, even without structured definitions', () => {
  const wrong = { behaviour: 'figure', figure: 2, context: { term: 'Net New Assets' }, answer_text: 'Current: 2.' };
  assert.equal(presentMeaningReply(wrong, card, selected), undefined);
  assert.equal(presentMeaningReply({ ...wrong, context: null }, card, selected), undefined);
  assert.equal(presentMeaningReply({ result: { rows: [{ value: 9 }] }, context: null }, card, selected), undefined);
  const plain = { ...wrong, context: { term: selected.term }, answer_text: 'Certified: 2.' };
  assert.equal(presentMeaningReply(plain, card, selected), plain);
  assert.equal(presentMeaningReply({ ...plain, answer_text: 'Certified: 2.\nNet New Assets: Current assets.' }, card, selected), undefined);
  assert.equal(presentMeaningReply({ ...plain, certified_definitions: [], answer_text: 'Certified: 2.\nNet New Assets: Current assets.' }, card, selected), undefined);
  const decline = { behaviour: 'abstain_with_reason', figure: null, answer_text: 'Not released.' };
  assert.equal(presentMeaningReply(decline, card, selected), decline);
});


test('retained role-filtered evidence uses the existing bounded-result policy', async () => {
  const { boundedMeaningReply, RESULT_MAX_BYTES } = await import('../src/cerebro/agent.js');
  const small = { answer_text: 'Certified: 42.', figure: 42, history: [{ id: 'a' }] };
  assert.deepEqual(boundedMeaningReply(small), small);
  const large = { ...small, result: { rows: Array.from({ length: 3000 }, (_, i) => ({ cells: [{ value: `${i}-${'x'.repeat(600)}` }] })) } };
  const bounded = boundedMeaningReply(large);
  assert.ok(Buffer.byteLength(JSON.stringify(bounded)) <= RESULT_MAX_BYTES);
  assert.ok(bounded.result.rows.length < large.result.rows.length);
  assert.deepEqual(bounded.history, small.history);
  assert.equal(bounded.answer_text, small.answer_text);
  assert.equal(bounded.figure, 42);
  assert.ok(bounded.truncated);
});


test('pure definitions without an array still require the selected structured term', () => {
  const right = { behaviour: 'glossary_definition', term: 'Certified NNA', definition: 'Finance frozen assets.', answer_text: 'Finance frozen assets.' };
  assert.equal(presentMeaningReply(right, card, selected), right);
  assert.equal(presentMeaningReply({ ...right, term: 'Net New Assets' }, card, selected), undefined);
  assert.equal(presentMeaningReply({ ...right, term: null }, card, selected), undefined);
});
