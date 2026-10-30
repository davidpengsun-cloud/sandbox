import { afterEach, beforeEach, expect, test, vi } from 'vitest';
const { get, post, put } = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn(), put: vi.fn() }));
vi.mock('axios', () => ({ default: { create: () => ({ get, post, put }) } }));
import { getApi, postApi, putApi } from './api';
import { clearKnowledge } from '../components/KnowledgeStatus';

let seen;
const listen = (event) => seen.push(event.detail);
beforeEach(() => {
  vi.resetAllMocks();
  vi.spyOn(console, 'error').mockImplementation(() => {});
  seen = [];
  window.addEventListener('portal-knowledge', listen);
  window.history.replaceState({}, '', '/home');
});
afterEach(() => window.removeEventListener('portal-knowledge', listen));

test('getApi sends the query params and publishes the knowledge notice it returns', async () => {
  get.mockResolvedValue({ headers: { 'x-portal-notice': 'Using the portal catalogue.' }, data: [] });
  const res = await getApi({ api: '/search', params: { q: 'orders' } });
  expect(get).toHaveBeenCalledWith('/search', { params: { q: 'orders' } });
  expect(res.data).toEqual([]);
  expect(seen.at(-1)).toEqual({ notice: 'Using the portal catalogue.', hits: [] });
});

test('getApi without params sends none', async () => {
  get.mockResolvedValue({ headers: {}, data: {} });
  await getApi({ api: '/getConfig' });
  expect(get).toHaveBeenCalledWith('/getConfig', { params: null });
});

test('postApi sends its body; signing in clears earlier knowledge first', async () => {
  post.mockResolvedValue({ headers: {}, data: { hits: [{ id: 'h' }] } });
  await postApi({ api: '/session', params: { username: 'u', password: 'p' } });
  expect(post).toHaveBeenCalledWith('/session', { username: 'u', password: 'p' });
  expect(seen[0]).toEqual({ notice: '', hits: [] });
  expect(seen.at(-1).hits).toEqual([{ id: 'h' }]);
});

test('putApi sends the body with the language header', async () => {
  put.mockResolvedValue({ headers: {}, data: {} });
  await putApi({ api: '/thing', data: { a: 1 } });
  expect(put).toHaveBeenCalledWith('/thing', { a: 1 }, { headers: { 'Custom-Language': 'en' } });
});

test('a response that lands after the session changed is not published', async () => {
  let resolve;
  get.mockReturnValue(new Promise((r) => { resolve = r; }));
  const pending = getApi({ api: '/search' });
  clearKnowledge();
  seen = [];
  resolve({ headers: { 'x-portal-notice': 'stale' }, data: {} });
  await pending;
  expect(seen).toEqual([]);
});

test.each([
  ['getApi', () => getApi({ api: '/getConfig' }), get],
  ['postApi', () => postApi({ api: '/search' }), post],
  ['putApi', () => putApi({ api: '/thing' }), put],
])('%s: a 401 outside sign-in sends the person back to the start', async (_name, call, transport) => {
  const error = { response: { status: 401 } };
  transport.mockRejectedValue(error);
  await expect(call()).rejects.toBe(error);
  expect(window.location.pathname).toBe('/');
});

test('any other failure is logged and rethrown without leaving the page', async () => {
  const error = { response: { status: 500 } };
  get.mockRejectedValue(error);
  await expect(getApi({ api: '/getConfig' })).rejects.toBe(error);
  expect(window.location.pathname).toBe('/home');
  expect(console.error).toHaveBeenCalledWith(error);
});
