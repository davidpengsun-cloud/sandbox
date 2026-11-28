import assert from 'node:assert/strict';
import { test } from 'node:test';
import { cerebroAgent, pendingRun, savedConversation } from '../src/cerebro/agent.js';
import { backgroundUsage, followRun } from '../src/cerebro/runs.js';
import { safeUsage, unknownUsage } from '../src/cerebro/usage.js';
import { openConversations } from '../src/conversations/index.js';
import { kgeStub, standIn } from './fixtures/cerebro-stand-in.js';

const runId = 'feedc0de'.repeat(4);
const slow = 'Revenue by branch for every day of 2025';
const figure = (q) => ({ question: q, behaviour: 'figure', figure: 42, answer_text: 'Certified: 42.', answer_mode: 'certified', receipt: 'r' });
const report = { calls: 2, input_tokens: 50, output_tokens: 10, thinking_tokens: null, cost_usd: null, complete: true };

function stub() {
  const calls = [];
  const kge = kgeStub((name, args) => {
    if (name === 'ask') return { value: args.question === slow ? { run: { id: runId, state: 'running', poll_after: 2 } } : figure(args.question) };
    if (name === 'question_run') return { value: { id: runId, state: 'done', answer: figure(slow) } };
    return { value: {} };
  });
  kge.modelUsage = async (req, options) => { calls.push({ id: req.modelQuestionId, finish: options.finish }); return { ...report, complete: options.finish, hidden: 'never-display' }; };
  return { kge, calls };
}
const issuer = async (_req, id) => `opaque-${id}`;
const asks = () => standIn((messages) => ({ call: ['ask', { question: messages.findLast((m) => m._getType() === 'human').content }] }));
async function turn(agent, thread, question, id) {
  const req = { portalIdentity: { principal: 'reader' } }; const events = [];
  for await (const event of agent({ thread, messages: [{ type: 'human', content: question, id }], roles: ['business'], req })) events.push(event);
  return { events, req };
}

test('numeric report strips unknown fields and never presents incomplete or invalid usage as zero', () => {
  assert.deepEqual(safeUsage({ ...report, identity: 'secret' }), report);
  assert.deepEqual(safeUsage({ ...report, complete: false }), unknownUsage());
  assert.deepEqual(safeUsage({ ...report, cost_usd: -1 }), unknownUsage());
  assert.deepEqual(safeUsage({ ...report, input_tokens: undefined }), unknownUsage());
});

test('a terminal foreground answer finishes the question and exposes only sanitized numeric usage', async () => {
  const { kge, calls } = stub(); const conversations = openConversations(); const thread = crypto.randomUUID();
  const agent = cerebroAgent({ model: asks(), kge, issueQuestion: issuer, conversations, retryAfterMs: 0 });
  const { req } = await turn(agent, thread, 'Revenue for June 2025', 'h-1');
  assert.deepEqual(calls, [{ id: req.modelQuestionId, finish: true }]);
  const saved = await savedConversation(conversations, 'reader', thread);
  assert.deepEqual(saved.replies['h-1'].usage, report);
  assert.doesNotMatch(JSON.stringify(saved), /opaque-|never-display|questionIds|questionRuns/);
});

test('background completion keeps the original question identity after a newer turn and finishes only when settled', async () => {
  const { kge, calls } = stub(); const conversations = openConversations(); const thread = crypto.randomUUID();
  const agent = cerebroAgent({ model: asks(), kge, issueQuestion: issuer, conversations, retryAfterMs: 0 });
  const first = await turn(agent, thread, slow, 'h-slow');
  assert.equal(calls.at(-1).finish, false, 'pending run stays open');
  const pending = (await savedConversation(conversations, 'reader', thread)).messages.find((m) => m.type === 'tool' && m.name === 'ask' && pendingRun(JSON.parse(m.content)));
  assert.ok(pending);
  const second = await turn(agent, thread, 'Revenue for June 2025', 'h-fast');
  assert.notEqual(first.req.modelQuestionId, second.req.modelQuestionId);
  const req = { portalIdentity: { principal: 'reader' } };
  const result = await followRun({ conversations, kge, req, roles: ['business'], id: thread, messageId: pending.id, issueQuestion: issuer });
  assert.equal(result.state, 'ended'); assert.equal(req.modelQuestionId, first.req.modelQuestionId);
  const usage = await backgroundUsage({ conversations, kge, req, id: thread, messageId: pending.id, result });
  assert.deepEqual(usage, report); assert.deepEqual(calls.at(-1), { id: first.req.modelQuestionId, finish: true });
  const saved = await savedConversation(conversations, 'reader', thread);
  assert.deepEqual(saved.replies['h-slow'].usage, report); assert.deepEqual(saved.replies['h-fast'].usage, report);
  assert.doesNotMatch(JSON.stringify(saved), /opaque-|never-display|questionIds|questionRuns/);
});

test('finishing one of two concurrent background answers does not close the shared question', async () => {
  const { kge, calls } = stub(); const conversations = openConversations(); const thread = crypto.randomUUID();
  const model = standIn(() => ({ calls: [['ask', { question: slow }], ['ask', { question: slow }]] }));
  const agent = cerebroAgent({ model, kge, issueQuestion: issuer, conversations, retryAfterMs: 0 });
  const first = await turn(agent, thread, 'What was revenue last year?', 'h-two');
  const pending = (await savedConversation(conversations, 'reader', thread)).messages.filter((m) => m.type === 'tool' && m.name === 'ask' && pendingRun(JSON.parse(m.content)));
  assert.equal(pending.length, 2);
  const req1 = { portalIdentity: { principal: 'reader' } };
  const done1 = await followRun({ conversations, kge, req: req1, roles: ['business'], id: thread, messageId: pending[0].id, issueQuestion: issuer });
  const usage1 = await backgroundUsage({ conversations, kge, req: req1, id: thread, messageId: pending[0].id, result: done1 });
  assert.deepEqual(usage1, unknownUsage());
  assert.deepEqual(calls.at(-1), { id: first.req.modelQuestionId, finish: false });
  const req2 = { portalIdentity: { principal: 'reader' } };
  const done2 = await followRun({ conversations, kge, req: req2, roles: ['business'], id: thread, messageId: pending[1].id, issueQuestion: issuer });
  assert.deepEqual(await backgroundUsage({ conversations, kge, req: req2, id: thread, messageId: pending[1].id, result: done2 }), report);
  assert.deepEqual(calls.at(-1), { id: first.req.modelQuestionId, finish: true });
});

test('accepting a non-pausing offer starts a new logical question without reopening the finished ledger', async () => {
  const calls = []; const issued = [];
  const term = (name) => ({ term: name, definition: `${name} defined`, period_range: { first: '2024-01-01', latest: '2025-06-30', released_months: true } });
  const difference = { behaviour: 'cite_certified', answer_mode: 'certified', answer_text: 'Certified: the meanings differ.',
    difference: { terms: [term('current NNA'), term('certified NNA')], rows: [], summary: 'They differ.' } };
  let firstAsk = true;
  const kge = kgeStub((name, args) => {
    if (name !== 'ask') return { value: {} };
    if (firstAsk) { firstAsk = false; return { value: difference }; }
    return { value: figure(args.question) };
  });
  kge.modelUsage = async (req, { finish }) => { calls.push({ id: req.modelQuestionId, finish }); return { ...report, complete: finish }; };
  const issueQuestion = async (_req, id) => { issued.push(id); return `opaque-${id}`; };
  const model = standIn(() => ({ call: ['ask', { question: 'What is the difference between current NNA and certified NNA?' }] }));
  const conversations = openConversations(); const thread = crypto.randomUUID();
  const agent = cerebroAgent({ model, kge, issueQuestion, conversations, retryAfterMs: 0 });
  const first = await turn(agent, thread, 'What is the difference between current NNA and certified NNA? Compare definitions.', 'h-offer');
  assert.equal(first.events.some(([, value]) => value?.__interrupt__), false, 'the offer ends the answered turn');
  const req = { portalIdentity: { principal: 'reader' } }; const events = [];
  for await (const event of agent({ thread, resume: { pick: 'latest_month' }, roles: ['business'], req })) events.push(event);
  assert.equal(issued.length, 2);
  assert.notEqual(issued[0], issued[1]);
  assert.deepEqual(calls.map(({ id, finish }) => ({ id, finish })), [{ id: issued[0], finish: true }, { id: issued[1], finish: true }]);
  assert.ok(kge.calls.some((call) => call.name === 'ask' && /Compare current NNA vs\. certified NNA/.test(call.args.question)));
});
