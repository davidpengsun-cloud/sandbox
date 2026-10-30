import { expect, test, vi } from 'vitest';
const { post } = vi.hoisted(() => ({ post: vi.fn() }));
import { postApi } from './api';
vi.mock('axios', () => ({ default: { create: () => ({ post }) } }));

test('a session 401 preserves the current location and rejects for the form', async () => {
  const error = { response: { status: 401, data: { detail: 'wrong username or password' } } };
  post.mockRejectedValue(error);
  window.history.replaceState({}, '', '/signin?next=home');
  const location = window.location.href;
  await expect(postApi({ api: '/session' })).rejects.toBe(error);
  expect(window.location.href).toBe(location);
});
