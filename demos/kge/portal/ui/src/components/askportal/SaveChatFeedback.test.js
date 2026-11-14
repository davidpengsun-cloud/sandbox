import { expect, test, vi } from 'vitest';
const { post, create } = vi.hoisted(() => {
  const post = vi.fn();
  return { post, create: vi.fn(() => ({ post })) };
});
vi.mock('axios', () => ({ default: { create } }));
import SaveChatFeedback from './SaveChatFeedback';

test('posts the feedback to the given path', async () => {
  post.mockResolvedValue({ status: 201 });
  await expect(SaveChatFeedback({ api: '/feedback-add', params: { feedback_rating: 1 } })).resolves.toEqual({ status: 201 });
  expect(post).toHaveBeenCalledWith('/feedback-add', { feedback_rating: 1 });
});
