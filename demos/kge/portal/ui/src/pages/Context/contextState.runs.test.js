import { expect, test, vi } from 'vitest';
import { followQuestionRun } from './contextState';

const initial = () => ({ id: 'a'.repeat(32), state: 'running', cursor: 2, poll_after: 2, expires_at: Date.now() / 1000 + 900 });

test('a question over five minutes reconnects by id, retains progress and never resubmits', async () => {
  const run = initial(), events = [], wait = vi.fn(async () => {});
  const load = vi.fn().mockRejectedValueOnce(new Error('connection lost'))
    .mockResolvedValueOnce({ ...run, cursor: 3, events: [{ event: 'progress', elapsed_ms: 301000 }] })
    .mockResolvedValueOnce({ ...run, state: 'done', cursor: 4, answer: { answer_text: '15' }, events: [{ event: 'answer', answer: { answer_text: '15' } }] });
  expect(await followQuestionRun(run, load, { onEvent: e => events.push(e), wait })).toEqual({ answer_text: '15' });
  expect(load.mock.calls).toEqual([[run.id, 2, expect.any(Function)], [run.id, 2, expect.any(Function)], [run.id, 3, expect.any(Function)]]);
  expect(events).toHaveLength(2);
  expect(events[0].elapsed_ms).toBeGreaterThan(300000);
  expect(wait.mock.calls).toEqual([[2000], [2000], [2000]]);
});

test('an over-limit question returns the server message', async () => {
  const run = { ...initial(), expires_at: 1 };
  const load = vi.fn(async () => ({ ...run, state: 'expired', error: 'This question exceeded its time limit.' }));
  expect(await followQuestionRun(run, load)).toEqual({ error: 'This question exceeded its time limit.' });
  expect(load).toHaveBeenCalledTimes(1);
  expect(load).toHaveBeenCalledWith(run.id, 2, expect.any(Function));
});

test('stopping disconnects the follower without another warehouse status call', async () => {
  const controller = new AbortController(), load = vi.fn();
  const answer = await followQuestionRun(initial(), load, { signal: controller.signal, wait: async () => controller.abort() });
  expect(answer.cancelled).toBe(true);
  expect(load).not.toHaveBeenCalled();
});

test('ownership or revoked access refuses a saved answer', async () => {
  const load = vi.fn(async () => ({ error: 'Question run not found.' }));
  expect(await followQuestionRun(initial(), load, { wait: async () => {} })).toEqual({ error: 'Question run not found.' });
  expect(load).toHaveBeenCalledTimes(1);
});

test('a live poll that fails midway resumes after the last event it delivered, never replaying one', async () => {
  const run = initial(), events = [], wait = vi.fn(async () => {});
  const load = vi.fn()
    .mockImplementationOnce(async (id, after, deliver) => {
      deliver({ event: 'stage', stage: 'execute', state: 'running', cursor: 3 });
      deliver({ event: 'progress', state: 'running', cursor: 4 });
      throw new Error('connection lost');
    })
    .mockImplementationOnce(async (id, after, deliver) => {
      deliver({ event: 'stage', stage: 'execute', state: 'done', cursor: 5 });
      return { ...run, state: 'done', cursor: 6, events: [], answer: { answer_text: '15' } };
    });
  expect(await followQuestionRun(run, load, { onEvent: e => events.push(e), wait })).toEqual({ answer_text: '15' });
  expect(load.mock.calls.map(call => call[1])).toEqual([2, 4]);
  expect(events.map(e => e.cursor)).toEqual([3, 4, 5]);
});
