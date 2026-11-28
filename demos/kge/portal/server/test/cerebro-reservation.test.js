import assert from 'node:assert/strict';
import test from 'node:test';
import { kgeTools } from '../src/cerebro/tools.js';
import { kgeClient } from '../src/kge.js';

const personBearer = crypto.randomUUID();
const questionId = crypto.randomUUID();
const conversationId = crypto.randomUUID();
const correlation = crypto.randomUUID();
const request = () => ({ headers: {}, modelQuestionId: questionId, modelConversationId: conversationId, modelQuestionToken: correlation });
const reply = () => Response.json({ jsonrpc: '2.0', id: 1, result: { structuredContent: { answer: 'ok' } } });

test('Ask and each background poll reserve a unique invocation before dispatch; stop does not', async () => {
  const reservations = []; const calls = [];
  const kge = { sessionBearer: async () => personBearer, modelReserve: async () => { const id = crypto.randomUUID(); reservations.push(id); return id; },
    request: async (_req, _path, options) => { calls.push(options); return reply(); } };
  const tools = kgeTools({ kge, req: request(), allowed: new Set(['ask']) });
  await tools('ask', { question: 'NNA?' });
  await tools.questionRun(crypto.randomUUID().replaceAll("-", ""));
  await tools.questionRun(crypto.randomUUID().replaceAll("-", ""));
  await tools.stopRun(crypto.randomUUID().replaceAll("-", ""));
  assert.equal(reservations.length, 3); assert.equal(new Set(reservations).size, 3);
  assert.deepEqual(calls.map((call) => call.headers['x-kge-invocation-id']), [...reservations, undefined]);
  for (const call of calls) { assert.equal(call.headers['x-kge-question-token'], correlation); assert.equal(call.headers['x-kge-conversation-id'], conversationId); assert.equal(call.trusted, false); }
});

test('a reservation failure blocks dispatch; a dropped request retains its reservation and a retry uses a new one', async () => {
  let failReserve = true; let failRequest = true; const ids = []; const seen = [];
  const kge = { sessionBearer: async () => personBearer, modelReserve: async () => { if (failReserve) throw new Error('offline'); const id = crypto.randomUUID(); ids.push(id); return id; },
    request: async (_req, _path, options) => { seen.push(options.headers['x-kge-invocation-id']); if (failRequest) throw new Error('disconnected'); return reply(); } };
  const tools = kgeTools({ kge, req: request(), allowed: new Set(['ask']) });
  assert.equal((await tools('ask', { question: 'NNA?' })).failure, 'kge_unreachable'); assert.equal(seen.length, 0);
  failReserve = false; assert.equal((await tools('ask', { question: 'NNA?' })).failure, 'kge_unreachable');
  failRequest = false; assert.ok((await tools('ask', { question: 'NNA?' })).value);
  assert.deepEqual(seen, ids); assert.notEqual(ids[0], ids[1]);
});

test('reservation authenticates trusted portal and person and validates the returned invocation UUID', async () => {
  const original = globalThis.fetch; const sent = []; let valid = true;
  globalThis.fetch = async (url, options) => { sent.push({ url: String(url), options }); return String(url).endsWith('/session/bearer') ? Response.json({ access_token: personBearer }) : Response.json({ invocation_id: valid ? crypto.randomUUID() : 'bad' }); };
  try {
    const kge = kgeClient('http://example.test', { headers: async () => ({ 'x-caller-token': 'trusted' }) }, 's'.repeat(32));
    const req = { ...request(), headers: { cookie: 'session=present' } };
    assert.match(await kge.modelReserve(req), /^[0-9a-f-]{36}$/);
    const wire = sent.at(-1); assert.ok(wire.url.endsWith('/model/usage/reserve')); assert.equal(wire.options.headers['x-kge-question-token'], correlation);
    assert.equal(wire.options.headers['x-kge-authorization'], `Bearer ${personBearer}`); assert.equal(wire.options.headers['x-caller-token'], 'trusted'); assert.equal(wire.options.headers.cookie, undefined);
    valid = false; await assert.rejects(kge.modelReserve(req), /usage is unavailable/);
  } finally { globalThis.fetch = original; }
});
