import React from 'react';
import { fireEvent, render, screen } from '@testing-library/react';
import { afterEach, expect, test, vi } from 'vitest';
import { Provider } from 'react-redux';
import { configureStore } from '@reduxjs/toolkit';
import { MemoryRouter } from 'react-router-dom';
import user from '../../features/user/userSlice';
import ContextApp from './ContextApp';
import { configOf } from '../../components/shell/testSupport';

afterEach(() => { vi.unstubAllGlobals(); vi.clearAllMocks(); window.localStorage.removeItem('v3-theme'); });

test('Context and the bar share the live document theme without reloading or remounting Context', async () => {
  window.localStorage.removeItem('v3-theme');
  const bootstrap = { person: { id: 'reader@example.com', processes: ['querying'], reads: [] }, profile: {}, mode: {}, presets: {}, build: {} };
  const fetch = vi.fn(async (url) => {
    const body = {
      '/api/config': configOf(), '/brand': { logo: false }, '/api/context/bootstrap': bootstrap,
      '/api/context/versions': {}, '/api/context/golden': { sets: [] }, '/api/context/ab': { runs: [] },
    }[url];
    if (!body) throw new Error(`Unexpected call: ${url}`);
    return { ok: true, json: async () => body };
  });
  vi.stubGlobal('fetch', fetch);
  const store = configureStore({ reducer: { user } });
  const { container } = render(<Provider store={store}><MemoryRouter initialEntries={['/context']}><ContextApp /></MemoryRouter></Provider>);
  await screen.findByRole('heading', { name: 'Ask a governed question' });
  const workspace = container.querySelector('.context-app');
  const bar = screen.getByRole('banner');
  expect(container.querySelectorAll('button[aria-label^="Theme:"]')).toHaveLength(1);
  for (const [before, after, chosen] of [['System', 'Light', 'light'], ['Light', 'Dark', 'dark'], ['Dark', 'System', undefined]]) {
    fireEvent.click(screen.getByRole('button', { name: `Theme: ${before}` }));
    expect(screen.getByRole('button', { name: `Theme: ${after}` })).toBeInTheDocument();
    expect(document.documentElement.dataset.theme).toBe(chosen);
    expect(document.documentElement).toContainElement(bar);
    expect(document.documentElement).toContainElement(workspace);
    expect(workspace.closest('[data-theme]')).toBe(chosen ? document.documentElement : null);
    expect(container.querySelector('.context-app')).toBe(workspace);
  }
  expect(fetch.mock.calls.filter(([url]) => url === '/api/context/bootstrap')).toHaveLength(1);
});
