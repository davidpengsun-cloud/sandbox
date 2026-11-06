import { afterEach, expect, test, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import { Provider } from 'react-redux';
import { MemoryRouter } from 'react-router-dom';
import App from './App';
import store from './app/store';

afterEach(() => vi.unstubAllGlobals());

test('renders the sign-in page at the root for a person who is not signed in', async () => {
  vi.stubGlobal('fetch', vi.fn((url) => Promise.resolve(url === '/brand'
    ? { ok: true, json: async () => ({ name: 'ALTA: AI-powered Library for Trusted Assets', logo: false }) }
    : { ok: false, status: 401, json: async () => ({}) })));
  render(<Provider store={store}><MemoryRouter><App /></MemoryRouter></Provider>);
  expect(await screen.findByRole('heading', { name: 'Sign in' })).toBeInTheDocument();
  expect(screen.getByRole('button', { name: 'Sign in' })).toBeInTheDocument();
  expect(screen.queryByText(/login/i)).not.toBeInTheDocument();
  expect(screen.queryByText(/preview|export|copy query/i)).not.toBeInTheDocument();
});
