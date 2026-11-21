import { afterEach, expect, test, vi } from 'vitest';
import { act, render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import Login from './Login';

vi.mock('react-redux', () => ({ useDispatch: () => vi.fn() }));
afterEach(() => vi.unstubAllGlobals());

const renderLogin = () => render(<MemoryRouter><Login /></MemoryRouter>);

const configure = (config, logo = false) => {
  vi.stubGlobal('fetch', vi.fn((url) => Promise.resolve({ ok: true, json: async () => url === '/brand'
    ? { name: 'ALTA: AI-powered Library for Trusted Assets', logo, kge_url: null }
    : config })));
};
const all = { knowledgeHome: true, apps: { certify: true, context: true, discover: true }, productName: 'Meridian ALTA' };

test('sign-in shows the Knowledge product name and its initial tile', async () => {
  configure(all);
  renderLogin();
  expect(screen.getByRole('heading', { name: 'Sign in' })).toBeInTheDocument();
  expect(await screen.findByText('Meridian ALTA')).toBeInTheDocument();
  expect(screen.getByText('M')).toHaveAttribute('data-brand', 'tile');
  expect(screen.queryByText('ALTA: AI-powered Library for Trusted Assets')).not.toBeInTheDocument();
});

test('sign-in shows the configured logo beside the Knowledge name', async () => {
  configure({ ...all, productName: 'Example Knowledge' }, true);
  const { container } = renderLogin();
  expect(await screen.findByText('Example Knowledge')).toBeInTheDocument();
  await vi.waitFor(() => expect(container.querySelector('img[data-brand="logo"]')).toHaveAttribute('src', '/brand/logo'));
  expect(fetch).toHaveBeenCalledWith('/brand', { credentials: 'same-origin' });
});

for (const app of ['certify', 'context', 'discover']) test(`sign-in names ${app} when it runs alone`, async () => {
  configure({ ...all, knowledgeHome: false, apps: { certify: false, context: false, discover: false, [app]: true } });
  renderLogin();
  expect(await screen.findByText(app.charAt(0).toUpperCase() + app.slice(1))).toBeInTheDocument();
  expect(screen.queryByText('Meridian ALTA')).not.toBeInTheDocument();
  expect(screen.queryByText('ALTA: AI-powered Library for Trusted Assets')).not.toBeInTheDocument();
});

test.each(['loaded', 'failed'])('settings %s after loading never duplicates the sign-in heading in the eyebrow', async (outcome) => {
  let finish;
  const pending = new Promise((resolve) => { finish = resolve; });
  vi.stubGlobal('fetch', vi.fn((url) => url === '/brand'
    ? Promise.resolve({ ok: true, json: async () => ({ logo: false }) }) : pending));
  const { container } = renderLogin();
  expect(screen.getByRole('heading', { name: 'Sign in' })).toBeInTheDocument();
  expect(container.querySelector('.signin-eyebrow')).toBeNull();
  await act(async () => finish({ ok: outcome === 'loaded', json: async () => all }));
  if (outcome === 'loaded') expect(await screen.findByText('Meridian ALTA')).toBeInTheDocument();
  else await vi.waitFor(() => expect(fetch).toHaveBeenCalledWith('/api/config', { credentials: 'same-origin' }));
  expect(container.querySelector('.signin-eyebrow')?.textContent).not.toBe('Sign in');
  expect(container).not.toHaveTextContent('[Client name]');
});
