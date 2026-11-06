import React from 'react';
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, expect, test, vi } from 'vitest';
import ContextApp from './ContextApp';
import Workspace from './Workspace';
import { mountContext } from './controller';

vi.mock('../../components/shell/ShellPage', () => ({ default: ({ children }) => <div data-testid="shell">{children}</div> }));
vi.mock('./controller', () => ({ mountContext: vi.fn(() => vi.fn()) }));
const bootstrap = { person: { id: 'reader@example.com' }, profile: {}, mode: {}, presets: {}, build: {} };
const response = (body, status = 200) => ({ ok: status === 200, status, json: async () => body });

afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks(); vi.clearAllMocks(); });

test('Context opens from the combined BFF response and mounts all existing workspaces', async () => {
  const fetch = vi.fn(async () => response(bootstrap));
  vi.stubGlobal('fetch', fetch);
  const { container, unmount } = render(<ContextApp />);
  expect(screen.getByRole('status')).toHaveTextContent('Opening Context');
  await waitFor(() => expect(mountContext).toHaveBeenCalledTimes(1));
  expect(fetch).toHaveBeenCalledWith('/api/context/bootstrap', expect.objectContaining({ signal: expect.any(AbortSignal) }));
  for (const view of ['docs', 'ask', 'review', 'onboard', 'quality']) expect(container.querySelector(`#view-${view}`)).toBeTruthy();
  expect(container.querySelector('iframe.gb-frame')).toBeNull();
  expect(container.querySelector('#nav-tools, #view-tools, #sql-check, #check-sql')).toBeNull();
  expect(container.querySelector('#role-docs')).toHaveAttribute('title', 'Your role guides');
  expect(container.querySelector('#ask').tagName).toBe('BUTTON');
  expect(container.querySelector('#assistant').tagName).toBe('SECTION');
  expect(screen.getByRole('tablist', { name: 'Workspaces' })).toBeInTheDocument();
  const cleanup = mountContext.mock.results[0].value;
  unmount();
  expect(cleanup).toHaveBeenCalledOnce();
});

test.each([403, 404, 502])('a BFF %s shows its own plain reason and retries the opening call', async (status) => {
  const fetch = vi.fn().mockResolvedValueOnce(response({ detail: 'The service gave its reason.' }, status)).mockResolvedValueOnce(response(bootstrap));
  vi.stubGlobal('fetch', fetch);
  render(<ContextApp />);
  expect(await screen.findByRole('alert')).toHaveTextContent('The service gave its reason.');
  fireEvent.click(screen.getByRole('button', { name: 'Retry' }));
  await waitFor(() => expect(mountContext).toHaveBeenCalled());
  expect(fetch).toHaveBeenCalledTimes(2);
});

test('a failed connection offers the availability message, without transport details', async () => {
  vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new TypeError('private network detail')));
  render(<ContextApp />);
  expect(await screen.findByRole('alert')).toHaveTextContent('Context is unavailable right now. Try again shortly.');
});

test('an expired sign-in forgets the portal session before returning to sign-in', async () => {
  const assign = vi.spyOn(window.location, 'assign').mockImplementation(() => {});
  const fetch = vi.fn().mockResolvedValueOnce(response({}, 401)).mockResolvedValueOnce(response({}));
  vi.stubGlobal('fetch', fetch);
  render(<ContextApp />);
  await waitFor(() => expect(assign).toHaveBeenCalledWith('/'));
  expect(fetch).toHaveBeenNthCalledWith(2, '/session', expect.objectContaining({ method: 'DELETE' }));
});

test('leaving Context aborts an unfinished opening call without showing an error', async () => {
  let reject;
  const pending = new Promise((_, fail) => { reject = fail; });
  const fetch = vi.fn(() => pending);
  vi.stubGlobal('fetch', fetch);
  const { unmount } = render(<ContextApp />);
  const signal = fetch.mock.calls[0][1].signal;
  unmount();
  expect(signal.aborted).toBe(true);
  await act(async () => reject(new DOMException('aborted', 'AbortError')));
});

test('the workspace teardown is called when the portal removes it', () => {
  const { unmount } = render(<Workspace bootstrap={bootstrap} />);
  const cleanup = mountContext.mock.results[0].value;
  unmount();
  expect(cleanup).toHaveBeenCalledOnce();
});
