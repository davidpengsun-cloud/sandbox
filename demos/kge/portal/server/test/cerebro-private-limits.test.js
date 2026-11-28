import assert from 'node:assert/strict';
import { test } from 'node:test';
import { emptyCheckpoint } from '@langchain/langgraph';
import { AIMessage, HumanMessage, ToolMessage } from '@langchain/core/messages';
import { CONVERSATION_MAX_BYTES, toPage, turnInput } from '../src/cerebro/agent.js';
import { REPLAY } from '../src/cerebro/gatewayModel.js';
import { ConversationSaver, memoryStore } from '../src/conversations/index.js';

const token = 'private-ciphertext-' + 'x'.repeat(390_000);
const exchange = (index) => [new HumanMessage({ content: `Question ${index}`, id: `h-${index}` }),
  new AIMessage({ content: 'Checking.', tool_calls: [{ id: `c-${index}`, name: 'ask', args: { question: 'NNA' } }], additional_kwargs: { [REPLAY]: { [`c-${index}`]: { claude_continuation: token } } } }),
  new ToolMessage({ content: '{}', name: 'ask', tool_call_id: `c-${index}` })];

test('history size includes hidden continuations and trims whole old exchanges without changing the latest question', () => {
  const saved = Array.from({ length: 24 }, (_, index) => exchange(index)).flat();
  const input = turnInput({ messages: saved, trimmed: 0 }, new HumanMessage('Most recent question'));
  assert.ok(input.trimmed > 0, 'private metadata must count toward the 8 MiB limit');
  assert.equal(input.messages.at(-1).content, 'Most recent question');
  assert.doesNotMatch(JSON.stringify(input.messages.map((m) => toPage(m, true))), /private-ciphertext/);
});

test('persisted checkpoint refuses over-limit private continuations without leaking the ciphertext', async () => {
  const store = memoryStore(); let refusal;
  const saver = new ConversationSaver(store, 'reader', { onRefused: (error) => { refusal = error; } });
  const thread = crypto.randomUUID(); const config = { configurable: { thread_id: thread, checkpoint_ns: '' } };
  const checkpoint = { ...emptyCheckpoint(), id: crypto.randomUUID(), channel_values: { messages: Array.from({ length: 96 }, (_, index) => exchange(index)).flat() }, channel_versions: { messages: 1 } };
  await saver.put(config, checkpoint, { source: 'loop', step: 1, parents: {} });
  await assert.rejects(saver.flush(), (error) => { assert.match(error.message, /too large to save/); assert.doesNotMatch(error.message, /private-ciphertext/); return true; });
  assert.ok(refusal); assert.ok(CONVERSATION_MAX_BYTES > 0);
});
