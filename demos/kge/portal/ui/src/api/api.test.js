import { expect, test, vi } from 'vitest';
const { post, remove } = vi.hoisted(() => ({ post: vi.fn(), remove: vi.fn() }));
import { postApi, deleteApi } from './api';
vi.mock('axios', () => ({ default: { create: () => ({ post, delete: remove }) } }));

test('a session 401 preserves the current location and rejects for the form', async () => {
  const error = { response: { status: 401, data: { detail: 'wrong username or password' } } };
  post.mockRejectedValue(error);
  window.history.replaceState({}, '', '/signin?next=home');
  const location = window.location.href;
  await expect(postApi({ api: '/session' })).rejects.toBe(error);
  expect(window.location.href).toBe(location);
});

test('removing a role sends an authenticated DELETE without an identity body and returns the audit', async () => {
  const response = { data: { by: 'operator@example.com', before: ['publisher'], after: [] } };
  remove.mockResolvedValue(response);
  expect(await deleteApi({ api: '/api/admin/users/ada%40example.com/roles/publisher' })).toBe(response);
  expect(remove).toHaveBeenCalledWith('/api/admin/users/ada%40example.com/roles/publisher');
});

test('a role removal refusal is passed to the admin screen', async () => {
  const error = { response: { status: 403, data: { message: 'Portal role required.' } } };
  remove.mockRejectedValue(error);
  await expect(deleteApi({ api: '/api/admin/users/ada/roles/publisher' })).rejects.toBe(error);
});
