import assert from 'node:assert/strict';
import { test } from 'node:test';
import { AIMessage, HumanMessage, SystemMessage, ToolMessage } from '@langchain/core/messages';
import { GatewayChat, gatewayEvents, gatewayMessages, REPLAY, SIGNATURES } from '../src/cerebro/gatewayModel.js';
import { kgeClient } from '../src/kge.js';
import { busy, toPage } from '../src/cerebro/agent.js';

const frame = (event, data) => `event: ${event}\ndata: ${JSON.stringify(data)}\n\n`;
const stream = (text, cuts = []) => new Response(new ReadableStream({ start(controller) {
  const bytes = new TextEncoder().encode(text); let at = 0;
  for (const end of cuts) { controller.enqueue(bytes.slice(at, end)); at = end; }
  controller.enqueue(bytes.slice(at)); controller.close();
} }), { headers: { 'content-type': 'text/event-stream' } });
const end = frame('final', { finish: 'STOP', model: 'served', tokens_in: 3, tokens_out: 4, reduced: 0 });

test('gateway delivers ordered text and parallel calls, keeps private opaque replay on each call, and round-trips it', async () => {
  const sent = [];
  const privateToken = crypto.randomUUID();
  const replay = { claude_continuation: privateToken, reduction: { drop: 2, omit: [{ turn: 3, message: 1, part: 0, call_id: 'old' }] } };
  const kge = { modelChat: async (req, body) => { sent.push({ req, body }); return stream(
    frame('part', { kind: 'text', text: 'Checking.' }) +
    frame('part', { kind: 'call', call_id: 'a', name: 'ask', value: { question: 'NNA' }, signature: 'secret-a', metadata: replay }) +
    frame('part', { kind: 'call', call_id: 'b', name: 'search', value: { query: 'NNA' }, signature: 'secret-b' }) + end, [5, 18, 37]); } };
  const req = { portalIdentity: { principal: 'alice' }, modelQuestionId: '123e4567-e89b-42d3-a456-abcdefabcdef', modelConversationId: '123e4567-e89b-42d3-a456-abcdefabcdef' };
  const model = new GatewayChat({ kge, req }).bindTools([{ type: 'function', function: { name: 'ask', parameters: {} } }]);
  const first = await model.invoke([new SystemMessage('Be accurate.'), new HumanMessage('NNA?')]);
  assert.equal(first.content, 'Checking.'); assert.deepEqual(first.tool_calls.map((c) => c.id), ['a', 'b']);
  assert.deepEqual(first.additional_kwargs[SIGNATURES], { a: 'secret-a', b: 'secret-b' });
  assert.deepEqual(first.additional_kwargs[REPLAY], { a: replay });
  assert.doesNotMatch(JSON.stringify(toPage(first, true)), /secret-a|reduction/);
  assert.equal(JSON.stringify(toPage(first, true)).includes(privateToken), false);
  await model.invoke([new SystemMessage('Be accurate.'), new HumanMessage('NNA?'), first,
    new ToolMessage({ name: 'ask', tool_call_id: 'a', content: '{"x":1}' }), new ToolMessage({ name: 'search', tool_call_id: 'b', content: '{}' })]);
  const second = sent[1].body; assert.equal(second.instructions, 'Be accurate.');
  assert.equal(second.messages[1].parts[1].signature, 'secret-a');
  assert.deepEqual(second.messages[1].parts[1].metadata, replay);
  assert.deepEqual(second.messages.slice(-2).map((m) => m.parts[0].name), ['ask', 'search']);
  assert.equal(sent[0].req, req); assert.deepEqual(sent[0].body.tools.map((t) => t.name), ['ask']);
});

test('the Claude token boundary accepts 400000 characters and rejects any longer token privately', async () => {
  const base = crypto.randomUUID();
  for (const size of [400_000, 400_001]) {
    const token = base.repeat(Math.ceil(size / base.length)).slice(0, size);
    const kge = { modelChat: async () => stream(frame('part', { kind: 'call', call_id: 'a', name: 'ask', value: {}, metadata: { claude_continuation: token, reduction: { drop: 0, omit: [] } } }) + end) };
    const model = new GatewayChat({ kge, req: { modelQuestionId: crypto.randomUUID(), modelConversationId: crypto.randomUUID() } });
    if (size === 400_000) assert.equal((await model.invoke([new HumanMessage('NNA')])).additional_kwargs[REPLAY].a.claude_continuation, token);
    else await assert.rejects(model.invoke([new HumanMessage('NNA')]), (error) => !error.message.includes(token) && /safe size/.test(error.message));
  }
});

test('tool calls are not emitted when the stream fails or ends without a successful final event', async () => {
  for (const suffix of ['', frame('error', { code: 'model_refused', message: 'Unavailable.' }), frame('final', { finish: 'MAX_TOKENS' })]) {
    const kge = { modelChat: async () => stream(frame('part', { kind: 'call', call_id: 'a', name: 'ask', value: { question: 'NNA' } }) + suffix) };
    const model = new GatewayChat({ kge, req: { modelQuestionId: '123e4567-e89b-42d3-a456-abcdefabcdef', modelConversationId: '123e4567-e89b-42d3-a456-abcdefabcdef' } }); const chunks = [];
    await assert.rejects(async () => { for await (const chunk of await model.stream([new HumanMessage('NNA')])) chunks.push(chunk); });
    assert.equal(chunks.some((c) => c.tool_calls?.length || c.tool_call_chunks?.length), false);
  }
});

test('quota errors are not retried as upstream model congestion', async () => {
  const response = new Response(JSON.stringify({ code: 'quota_exceeded', detail: 'Quota reached.' }), { status: 429, headers: { 'content-type': 'application/json' } });
  let error; try { for await (const _ of gatewayEvents(response)) {} } catch (e) { error = e; }
  assert.equal(error.failure, 'model_quota'); assert.equal(busy(error), false);
});

test('gateway identity forwards only trusted portal and current person, never browser auth or cookies', async () => {
  const requests = [];
  const token = 'p'.repeat(32);
  const personBearer = crypto.randomUUID();
  const kge = kgeClient('http://example.test', { headers: async () => ({ 'x-caller-token': 'service' }) }, token);
  const previousFetch = globalThis.fetch;
  globalThis.fetch = async (url, options) => { requests.push({ url: String(url), options }); return String(url).endsWith('/session/bearer')
    ? Response.json({ access_token: personBearer }) : stream(end); };
  try {
    await kge.modelChat({ headers: { cookie: 'session=alice', authorization: 'browser-secret' }, modelQuestionToken: 'opaque-token' }, { purpose: 'agent' });
    assert.equal(requests[1].options.headers['x-kge-authorization'], `Bearer ${personBearer}`);
    assert.equal(requests[1].options.headers['x-caller-token'], 'service');
    assert.equal(requests[1].options.headers['x-kge-question-token'], 'opaque-token');
    assert.equal(requests[1].options.headers['x-portal-sync-token'], token);
    assert.equal(requests[1].options.headers.cookie, undefined);
    assert.equal(requests[1].options.headers.authorization, undefined);
  } finally { globalThis.fetch = previousFetch; }
});

test('assistant replay preserves the matching tool name and rejects unidentified tool results', () => {
  assert.throws(() => gatewayMessages([new HumanMessage('hello'), new ToolMessage({ tool_call_id: 'missing', content: '{}' })]));
});

test('continuation metadata is bounded and errors never disclose its ciphertext', async () => {
  const secret = crypto.randomUUID().repeat(12_000);
  const kge = { modelChat: async () => stream(frame('part', { kind: 'call', call_id: 'a', name: 'ask', value: {}, metadata: { claude_continuation: secret } }) + end) };
  const model = new GatewayChat({ kge, req: { modelQuestionId: crypto.randomUUID(), modelConversationId: crypto.randomUUID() } });
  await assert.rejects(model.invoke([new HumanMessage('NNA')]), (error) => {
    assert.match(error.message, /continuation exceeds/); assert.equal(error.message.includes(secret), false); return true;
  });
});
