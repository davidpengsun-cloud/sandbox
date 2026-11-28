import { act, renderHook } from '@testing-library/react';
import { afterEach, expect, test, vi } from 'vitest';
import { safeConversationId, useContextApi } from './contextState';
afterEach(() => vi.unstubAllGlobals());
const json = body => ({ ok: true, status: 200, headers: { get: () => 'application/json' }, json: async () => body });
test('question requests and resumed runs send the existing safe session id, never arbitrary input', async () => {
  const fetcher = vi.fn(async () => json({ answer_text: 'ok' }));
  vi.stubGlobal('fetch', fetcher);
  const { result } = renderHook(() => useContextApi());
  const id = `conv-${'1'.repeat(13)}`;
  await act(async () => { await result.current('/ask?display=1', { question: 'first' }, { conversationId: id }); });
  await act(async () => { await result.current('/ask', { question: 'follow-up' }, { conversationId: id }); });
  await act(async () => { await result.current('/answer', { question: 'other' }, { conversationId: 'not a safe id' }); });
  await act(async () => { await result.current('/telemetry/agents', undefined, { conversationId: id }); });
  await act(async () => { await result.current(`/ask/runs/${'a'.repeat(32)}`, undefined, { conversationId: id }); });
  expect(fetcher.mock.calls[0][1].headers['x-kge-conversation-id']).toBe(id);
  expect(fetcher.mock.calls[1][1].headers['x-kge-conversation-id']).toBe(id);
  expect(fetcher.mock.calls[2][1].headers['x-kge-conversation-id']).toBeUndefined();
  expect(fetcher.mock.calls[3][1].headers?.['x-kge-conversation-id']).toBeUndefined();
  expect(fetcher.mock.calls[4][1].headers['x-kge-conversation-id']).toBe(id);
});
test('only UUID and bounded existing conv ids can enter the telemetry header', () => {
  expect(safeConversationId('a86b72fb-7c09-4e53-83cd-8ef5f26f5048')).toBe('a86b72fb-7c09-4e53-83cd-8ef5f26f5048');
  expect(safeConversationId('conv-123')).toBe('conv-123');
  expect(safeConversationId(`conv-${'1'.repeat(21)}`)).toBeNull();
  expect(safeConversationId('conv-123\r\nx-name: value')).toBeNull();
  expect(safeConversationId('a person@example.com')).toBeNull();
  expect(safeConversationId({})).toBeNull();
});
