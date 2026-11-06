import assert from 'node:assert/strict';
import { createServer } from 'node:http';
import test from 'node:test';
import { kgeClient, relay } from '../src/kge.js';

test('the data relay aborts a stalled response body within its request timeout', async (context) => {
  const upstream = createServer((_req, res) => { res.writeHead(200, { 'content-type': 'text/csv' }); res.write('id\n'); });
  await new Promise((resolve) => upstream.listen(0, '127.0.0.1', resolve));
  context.after(() => { upstream.closeAllConnections(); upstream.close(); });
  const timeout = AbortSignal.timeout.bind(AbortSignal);
  const seen = [];
  context.mock.method(AbortSignal, 'timeout', (ms) => { seen.push(ms); return timeout(200); });
  const client = kgeClient(`http://127.0.0.1:${upstream.address().port}`);
  const response = await client.request({ headers: {} }, '/data/export', { method: 'POST', body: '{}' });
  const res = { removeHeader() { return this; }, status() { return this; }, type() { return this; }, append() { return this; }, send() { assert.fail('a partial export was sent'); } };
  await assert.rejects(relay(response, res), (error) => ['AbortError', 'TimeoutError'].includes(error.name));
  assert.deepEqual(seen, [30000]);
});
