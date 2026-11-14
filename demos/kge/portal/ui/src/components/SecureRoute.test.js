import { afterEach, expect, test, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { Provider } from 'react-redux';
import { configureStore } from '@reduxjs/toolkit';
import { Link, MemoryRouter, Route, Routes } from 'react-router-dom';
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
            <Route path="/home" element={<><p>Home page</p><Link to="/adminaccess">Open admin</Link><Link to="/home?sort=name">Sort home</Link></>} />
            <Route path="/adminaccess" element={<p>Admin page</p>} />
          </Route>
        </Routes>
      </MemoryRouter>
    </Provider>,
  );
}

test('a signed-in person uses the shared username and display name, with all portal roles stored', async () => {
  const fetch = vi.fn((url) => Promise.resolve(url === '/whoami' ? json(true, { id: 'analyst@example.com', name: 'Ada Analyst', roles: ['analyst_retail'] }) : json(true, { role: 'approver', roles: ['publisher', 'approver'] })));
  vi.stubGlobal('fetch', fetch);
  const store = configureStore({ reducer: { user } });
  renderGuarded(store);
  expect(screen.getByText('Loading ALTA…')).toBeInTheDocument();
  expect(await screen.findByText('Home page')).toBeInTheDocument();
  expect(fetch).toHaveBeenCalledWith('/whoami', { credentials: 'include' });
  expect(fetch).toHaveBeenCalledWith('/api/role', { credentials: 'include' });
  expect(store.getState().user).toEqual({ isLoggedIn: true, data: { userId: 'analyst@example.com', userName: 'Ada Analyst', userRole: 'approver', userRoles: ['publisher', 'approver'], contextRoles: ['analyst_retail'] } });
});

test('navigation reloads the live display name and roles after a grant is removed', async () => {
  let current = { role: 'admin', roles: ['admin', 'publisher'] };
  let name = 'Ada Analyst';
  vi.stubGlobal('fetch', vi.fn((url) => Promise.resolve(url === '/whoami' ? json(true, { id: 'ada@example.com', name }) : json(true, current))));
  const store = configureStore({ reducer: { user } });
  renderGuarded(store);
  await screen.findByText('Home page');
  current = { role: 'publisher', roles: ['publisher'] };
  name = 'Ada';
  fireEvent.click(screen.getByRole('link', { name: 'Open admin' }));
  await waitFor(() => expect(store.getState().user.data.userRoles).toEqual(['publisher']));
  expect(store.getState().user.data.userName).toBe('Ada');
  expect(screen.getByText('Admin page')).toBeInTheDocument();
});

test('a role lookup that fails leaves the person a viewer', async () => {
  vi.stubGlobal('fetch', vi.fn((url) => Promise.resolve(url === '/whoami' ? json(true, { id: 'a' }) : json(false, {}))));
  const store = configureStore({ reducer: { user } });
  renderGuarded(store);
  expect(await screen.findByText('Home page')).toBeInTheDocument();
  expect(store.getState().user.data.userRole).toBe('viewer');
  expect(store.getState().user.data.userRoles).toEqual(['viewer']);
  expect(store.getState().user.data.userName).toBe('a');
});

test('a signed-out person is sent to sign in', async () => {
  vi.stubGlobal('fetch', vi.fn(() => Promise.resolve(json(false, {}))));
  renderGuarded(configureStore({ reducer: { user } }));
  expect(await screen.findByText('Sign-in page')).toBeInTheDocument();
  expect(screen.queryByText('Home page')).toBeNull();
});

test.each(['Open admin', 'Sort home'])('a session that expires after sign-in is sent back to sign in on %s', async (navigation) => {
  let signedIn = true;
  vi.stubGlobal('fetch', vi.fn((url) => Promise.resolve(url === '/whoami'
    ? json(signedIn, { id: 'analyst@example.com' }) : json(true, { role: 'viewer' }))));
  renderGuarded(configureStore({ reducer: { user } }));
  await screen.findByText('Home page');
  signedIn = false;
  fireEvent.click(screen.getByRole('link', { name: navigation }));
  expect(await screen.findByText('Sign-in page')).toBeInTheDocument();
  expect(screen.queryByText('Admin page')).toBeNull();
  expect(screen.queryByText('Home page')).toBeNull();
});

test('an Access admin keeps the KGE display label and the portal role IDs', async () => {
  vi.stubGlobal('fetch', vi.fn((url) => Promise.resolve(url === '/whoami'
    ? json(true, { id: 'access_admin.sample@example.com', name: 'Access operator', role: 'Access admin', roles: ['access_admin'], admin: true })
    : json(true, { role: 'admin', roles: ['viewer', 'admin'] }))));
  const store = configureStore({ reducer: { user } });
  renderGuarded(store);
  await screen.findByText('Home page');
  expect(store.getState().user.data.userRoleLabel).toBe('Access admin');
  expect(store.getState().user.data.userRole).toBe('admin');
  expect(store.getState().user.data.userRoles).toEqual(['viewer', 'admin']);
});

test('where a signed-out view is given, it shows in place instead of a redirect', async () => {
  vi.stubGlobal('fetch', vi.fn(() => Promise.resolve(json(false, {}))));
  render(
    <Provider store={configureStore({ reducer: { user } })}>
      <MemoryRouter initialEntries={['/']}>
        <Routes>
          <Route path="/" element={<RequiredAuth signedOut={<p>Sign-in form</p>} />}>
            <Route index element={<p>Root page</p>} />
          </Route>
        </Routes>
      </MemoryRouter>
    </Provider>,
  );
  expect(await screen.findByText('Sign-in form')).toBeInTheDocument();
  expect(screen.queryByText('Root page')).toBeNull();
});

test('a navigation to the same path, as after sign-in at the root, reads who is signed in again', async () => {
  let who = null;
  const fetch = vi.fn((url) => Promise.resolve(url === '/whoami' ? (who ? json(true, who) : json(false, {})) : json(true, { role: 'viewer', roles: ['viewer'] })));
  vi.stubGlobal('fetch', fetch);
  const store = configureStore({ reducer: { user } });
  render(
    <Provider store={store}>
      <MemoryRouter initialEntries={['/']}>
        <Routes>
          <Route path="/" element={<RequiredAuth signedOut={<Link to="/">Signed out: retry</Link>} />}>
            <Route index element={<p>Root page</p>} />
          </Route>
        </Routes>
      </MemoryRouter>
    </Provider>,
  );
  const retry = await screen.findByRole('link', { name: 'Signed out: retry' });
  who = { id: 'sam@example.com', name: 'Sam', roles: ['steward'] };
  fireEvent.click(retry);
  expect(await screen.findByText('Root page')).toBeInTheDocument();
  expect(store.getState().user.data.contextRoles).toEqual(['steward']);
});
