import { afterEach, expect, test, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import { Provider } from 'react-redux';
import { configureStore } from '@reduxjs/toolkit';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import user from '../features/user/userSlice';
import { RequiredAuth } from './SecureRoute';

const json = (ok, body) => ({ ok, json: async () => body });
afterEach(() => vi.unstubAllGlobals());

function renderGuarded(store) {
  return render(
    <Provider store={store}>
      <MemoryRouter initialEntries={['/home']}>
        <Routes>
          <Route path="/" element={<p>Sign-in page</p>} />
          <Route element={<RequiredAuth />}>
            <Route path="/home" element={<p>Home page</p>} />
          </Route>
        </Routes>
      </MemoryRouter>
    </Provider>,
  );
}

test('a signed-in person sees the page and their portal role is stored', async () => {
  const fetch = vi.fn((url) => Promise.resolve(url === '/whoami' ? json(true, { id: 'analyst_retail' }) : json(true, { role: 'approver' })));
  vi.stubGlobal('fetch', fetch);
  const store = configureStore({ reducer: { user } });
  renderGuarded(store);
  expect(screen.getByText('Loading portal…')).toBeInTheDocument();
  expect(await screen.findByText('Home page')).toBeInTheDocument();
  expect(fetch).toHaveBeenCalledWith('/whoami', { credentials: 'include' });
  expect(fetch).toHaveBeenCalledWith('/api/role', { credentials: 'include' });
  expect(store.getState().user).toEqual({ isLoggedIn: true, data: { userId: 'analyst_retail', userName: 'analyst_retail', userRole: 'approver' } });
});

test('a role lookup that fails leaves the person a viewer', async () => {
  vi.stubGlobal('fetch', vi.fn((url) => Promise.resolve(url === '/whoami' ? json(true, { id: 'a' }) : json(false, {}))));
  const store = configureStore({ reducer: { user } });
  renderGuarded(store);
  expect(await screen.findByText('Home page')).toBeInTheDocument();
  expect(store.getState().user.data.userRole).toBe('viewer');
});

test('a signed-out person is sent to sign in', async () => {
  vi.stubGlobal('fetch', vi.fn(() => Promise.resolve(json(false, {}))));
  renderGuarded(configureStore({ reducer: { user } }));
  expect(await screen.findByText('Sign-in page')).toBeInTheDocument();
  expect(screen.queryByText('Home page')).toBeNull();
});
