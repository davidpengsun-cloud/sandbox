import assert from 'node:assert/strict';
import { chmod, mkdtemp, rm, symlink, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { test } from 'node:test';
import { openModelServiceBearer } from '../src/modelServiceAuth.js';
import { kgeClient } from '../src/kge.js';

test('service bearer is loaded lazily only from an owner-only regular file, with no secret in errors', async () => {
  const dir = await mkdtemp(join(tmpdir(), 'model-service-')); const file = join(dir, 'bearer'); const link = join(dir, 'link'); const token = crypto.randomUUID();
  try {
    assert.equal(openModelServiceBearer({}), undefined);
    assert.throws(() => openModelServiceBearer({ PORTAL_MODEL_SERVICE_BEARER_FILE: 'relative' }), /absolute path/);
    const provider = openModelServiceBearer({ PORTAL_MODEL_SERVICE_BEARER_FILE: file });
    await assert.rejects(provider(), /unavailable or invalid/);
    await writeFile(file, `${token}\n`, { mode: 0o600 });
    assert.equal(await provider(), token);
    await chmod(file, 0o644);
    await assert.rejects(provider(), (error) => { assert.doesNotMatch(error.message, new RegExp(token)); return /unavailable or invalid/.test(error.message); });
    await chmod(file, 0o600); await symlink(file, link);
    await assert.rejects(openModelServiceBearer({ PORTAL_MODEL_SERVICE_BEARER_FILE: link })(), /unavailable or invalid/);
    await writeFile(file, 'invalid bearer with whitespace');
    await assert.rejects(provider(), /unavailable or invalid/);
    await writeFile(file, 'a'.repeat(8193));
    await assert.rejects(provider(), /unavailable or invalid/);
  } finally { await rm(dir, { recursive: true, force: true }); }
});

test('an explicit service context uses its configured bearer and cannot change identity, purpose, or question', async () => {
  const serviceBearer = crypto.randomUUID(); const correlation = crypto.randomUUID(); const requests = []; let providerCalls = 0;
  const prev = globalThis.fetch;
  globalThis.fetch = async (url, options) => {
    const body = JSON.parse(options.body); requests.push({ path: new URL(url).pathname, options, body });
    if (new URL(url).pathname === '/model/question') return Response.json({ question_id: body.question_id, correlation_token: correlation });
    return new Response('event: final\ndata: {"finish":"STOP"}\n\n', { headers: { 'content-type': 'text/event-stream' } });
  };
  try {
    const kge = kgeClient('http://example.test', { headers: async () => ({ 'x-caller-token': 'portal' }) }, 's'.repeat(32), {},
      { modelServiceBearer: async () => { providerCalls += 1; return serviceBearer; } });
    const held = await kge.modelService('agent', { expectedModel: 'approved-model' });
    assert.ok(Object.isFrozen(held.req));
    const request = { purpose: 'agent', expected_model: 'approved-model', question_id: held.req.modelQuestionId, conversation_id: held.req.modelConversationId, messages: [] };
    await held.kge.modelChat({ headers: { cookie: 'attacker' }, portalIdentity: { principal: 'attacker' } }, request);
    assert.deepEqual(requests.map((r) => r.path), ['/model/question', '/model/chat']);
    for (const item of requests) {
      assert.equal(item.options.headers['x-kge-authorization'], `Bearer ${serviceBearer}`);
      assert.equal(item.options.headers['x-caller-token'], 'portal');
      assert.equal(item.options.headers.cookie, undefined);
      assert.equal(item.body.principal, undefined);
    }
    assert.equal(requests[1].options.headers['x-kge-question-token'], correlation);
    await assert.rejects(held.kge.modelChat({}, { ...request, question_id: crypto.randomUUID() }), /does not match/);
    await assert.rejects(held.kge.modelChat({}, { ...request, purpose: 'catalogue' }), /does not match/);
    assert.equal(providerCalls, 2);
  } finally { globalThis.fetch = prev; }
});

test('a failed person session cannot fall back to the service identity, and unregistered services are refused', async () => {
  let providerCalls = 0; const prev = globalThis.fetch;
  globalThis.fetch = async () => new Response('{}', { status: 401, headers: { 'content-type': 'application/json' } });
  try {
    const kge = kgeClient('http://example.test', undefined, '', {}, { modelServiceBearer: async () => { providerCalls += 1; return crypto.randomUUID(); } });
    await assert.rejects(kge.modelQuestion({ headers: { cookie: 'invalid' }, modelConversationId: crypto.randomUUID() }, crypto.randomUUID(), 'agent'), { status: 401 });
    assert.equal(providerCalls, 0, 'a person error does not consult the service provider');
    globalThis.fetch = async () => new Response('{}', { status: 403, headers: { 'content-type': 'application/json' } });
    await assert.rejects(kge.modelService('agent'), { status: 403 });
    assert.equal(providerCalls, 1, 'KGE decides whether this service principal is registered');
  } finally { globalThis.fetch = prev; }
});
