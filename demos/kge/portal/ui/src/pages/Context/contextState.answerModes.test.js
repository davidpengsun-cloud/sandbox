import { renderHook } from '@testing-library/react';
import { afterEach, expect, test, vi } from 'vitest';
import { answerModeLabel, feedbackBody, stripAnswerLabel, useContextApi } from './contextState';
import replies from './fixtures/answer_modes.json';

afterEach(() => vi.unstubAllGlobals());

test('answerModeLabel names each answer mode, and nothing for a reply without one', () => {
  expect(answerModeLabel(replies.certified)).toBe('Certified');
  expect(answerModeLabel(replies.ai_generated)).toBe('AI-Generated');
  // A decline is unlabelled, whatever its mode: not_sure, and a certified refusal alike.
  expect(answerModeLabel(replies.not_sure)).toBeUndefined();
  expect(answerModeLabel(replies.decline)).toBeUndefined();
  for (const behaviour of ['clarify', 'refuse', 'blocked_access', 'abstain', 'abstain_with_reason', 'abstain_route_steward']) {
    expect(answerModeLabel({ behaviour, answer_mode: 'certified' })).toBeUndefined();
    expect(answerModeLabel({ behaviour, answer_mode: 'ai_generated' })).toBeUndefined();
  }
  expect(answerModeLabel({ behaviour: 'figure', answer_mode: 'not_sure' })).toBeUndefined();
  expect(answerModeLabel({ behaviour: 'figure' })).toBeUndefined();
  expect(answerModeLabel({ behaviour: 'refuse', answer_mode: 'something_new' })).toBeUndefined();
  expect(answerModeLabel(undefined)).toBeUndefined();
});

test('stripAnswerLabel drops the reply\'s own leading label and leaves any other text as it is', () => {
  expect(stripAnswerLabel(replies.certified.answer_text, 'certified')).toBe('Net New Assets by region for Q1 2025: MIDWEST USD 1,062,980.00; PACIFIC USD 1,176,801.46.');
  expect(stripAnswerLabel(replies.ai_generated.answer_text, 'ai_generated')).toMatch(/^The difference between/);
  // A1's exact prefixes, each stripped once; a decline's text carries none and stays whole.
  expect(stripAnswerLabel('Certified: Revenue is USD 25.', 'certified')).toBe('Revenue is USD 25.');
  expect(stripAnswerLabel('AI-Generated: Revenue is USD 25.', 'ai_generated')).toBe('Revenue is USD 25.');
  expect(stripAnswerLabel('Certified: Certified revenue is USD 25.', 'certified')).toBe('Certified revenue is USD 25.');
  expect(stripAnswerLabel(replies.not_sure.answer_text, 'not_sure')).toBe(replies.not_sure.answer_text);
  expect(stripAnswerLabel(replies.decline.answer_text, 'certified')).toBe(replies.decline.answer_text);
  for (const separator of ['. ', ' — ', ' - ', ' · ', ' ', '\n']) expect(stripAnswerLabel(`Certified${separator}`.trimEnd(), 'certified')).toBe('');
  expect(stripAnswerLabel('Certified. Revenue is USD 25.', 'certified')).toBe('Revenue is USD 25.');
  expect(stripAnswerLabel('Not sure: No figure is given.', 'not_sure')).toBe('Not sure: No figure is given.');
  // Pre-A1 text, another mode's label, and a word that only starts with the label stay whole.
  expect(stripAnswerLabel('Certified revenue [1]', undefined)).toBe('Certified revenue [1]');
  expect(stripAnswerLabel('Certified revenue [1]', 'certified')).toBe('Certified revenue [1]');
  expect(stripAnswerLabel('AI-Generated: text', 'certified')).toBe('AI-Generated: text');
  expect(stripAnswerLabel('Certifiedish: text', 'certified')).toBe('Certifiedish: text');
  expect(stripAnswerLabel(undefined, 'certified')).toBe('');
});

test('feedbackBody sends the receipt exactly as given and the verdict, nothing else', () => {
  for (const reply of Object.values(replies).filter(r => r.receipt)) {
    for (const verdict of ['confirmed', 'not_what_i_meant']) {
      const body = feedbackBody(reply, verdict);
      expect(Object.keys(body).sort()).toEqual(['receipt', 'verdict']);
      expect(body).toEqual({ receipt: reply.receipt, verdict });
      expect(JSON.stringify(body)).not.toContain(reply.question);
    }
  }
});

test.each([
  ['an empty 202', { ok: true, status: 202, headers: { get: () => '' }, json: async () => { throw new SyntaxError('Unexpected end of JSON input'); } }, {}],
  ['a 202 whose body is null', { ok: true, status: 202, headers: { get: () => 'application/json' }, json: async () => null }, {}],
  ['a 200 with a body', { ok: true, status: 200, headers: { get: () => 'application/json' }, json: async () => ({ done: true }) }, { done: true }],
])('the Context api accepts %s as a success', async (_, response, expected) => {
  const fetch = vi.fn(async () => response);
  vi.stubGlobal('fetch', fetch);
  const { result } = renderHook(() => useContextApi());
  expect(await result.current('/ask/feedback', feedbackBody(replies.certified, 'confirmed'))).toEqual(expected);
  expect(fetch).toHaveBeenCalledWith('/api/context/ask/feedback', expect.objectContaining({ method: 'POST', body: JSON.stringify({ receipt: replies.certified.receipt, verdict: 'confirmed' }) }));
});

test('a 200 whose body is not JSON is still an outage, not an empty success', async () => {
  vi.stubGlobal('fetch', vi.fn(async () => ({ ok: true, status: 200, headers: { get: () => 'text/html' }, json: async () => { throw new SyntaxError('Unexpected token <'); } })));
  const { result } = renderHook(() => useContextApi());
  expect(await result.current('/ask', { question: 'Revenue' })).toEqual({ error: 'The service is unavailable right now. Try again shortly.', cancelled: false });
});

test('an error reply with no body is still an error, not a success', async () => {
  vi.stubGlobal('fetch', vi.fn(async () => ({ ok: false, status: 400, headers: { get: () => '' }, json: async () => { throw new SyntaxError('Unexpected end of JSON input'); } })));
  const { result } = renderHook(() => useContextApi());
  expect(await result.current('/ask/feedback', feedbackBody(replies.certified, 'confirmed'))).toEqual({ error: 'The service is unavailable right now. Try again shortly.', cancelled: false });
});
