import { afterEach, beforeEach, expect, test, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { Provider } from 'react-redux';
import { configureStore } from '@reduxjs/toolkit';
import { MemoryRouter, useLocation } from 'react-router-dom';
import user from '../../features/user/userSlice';
import search from '../../features/search/searchSlice';
import dataProduct from '../../features/dataProduct/dataProductSlice';
import App from '../../App';
import { login } from '../../services/login';
import { configOf, READER, stubPhone, stubShell } from './testSupport';

vi.mock('../../services/login', () => ({ login: vi.fn() }));
vi.mock('../knowledgePages', () => ({
  KnowledgeHome: () => <main><h1>ALTA home page</h1></main>,
  DocsAtAGlance: () => <h1>At a glance page</h1>,
  DocsStartHere: () => <h1>Start here page</h1>,
}));
const { page } = vi.hoisted(() => ({ page: async (text) => {
  const { default: Header } = await import('../shared/Header/Header');
  return { default: () => <><Header type="home" /><h1>{text}</h1></> };
} }));
vi.mock('../../pages/LandingPage/LandingPage', () => page('Discover landing'));
vi.mock('../../pages/Search/Search', () => page('Explore page'));
vi.mock('../../pages/AdminAccess/AdminAccess', () => page('Admin page'));
vi.mock('../../pages/DataProductDetails/DataProductDetails', () => page('Product page'));

function Where() {
  return <output data-testid="where">{useLocation().pathname}</output>;
}

function renderApp(path = '/') {
  const store = configureStore({ reducer: { user, search, dataProduct } });
  render(
    <Provider store={store}>
      <MemoryRouter initialEntries={[path]}>
        <App />
        <Where />
      </MemoryRouter>
    </Provider>,
  );
  return store;
}

const where = () => screen.getByTestId('where').textContent;
const openSwitcher = async (current) => fireEvent.click(await screen.findByRole('button', { name: `Switch app, current: ${current}` }));

beforeEach(() => {
  vi.clearAllMocks();
  stubPhone(false);
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

test('signed out, the root is the sign-in page; signing in lands on the ALTA home', async () => {
  const shell = stubShell({ who: null });
  login.mockImplementation(async () => {
    shell.state.who = { id: 'sam@example.com', name: 'Sam', roles: ['steward'] };
    return { data: { id: 'sam@example.com', name: 'Sam' } };
  });
  renderApp('/');
  expect(await screen.findByRole('heading', { name: 'Sign in' })).toBeInTheDocument();
  fireEvent.change(screen.getByLabelText('Username'), { target: { value: 'sam' } });
  fireEvent.change(screen.getByLabelText('Password'), { target: { value: 'x' } });
  fireEvent.click(screen.getByRole('button', { name: 'Sign in' }));
  expect(await screen.findByRole('heading', { name: 'ALTA home page' })).toBeInTheDocument();
  expect(where()).toBe('/');
  expect(screen.getByRole('banner')).toBeInTheDocument();
});

test('signing out from the account menu returns to the sign-in page at the root', async () => {
  stubShell();
  renderApp('/');
  await screen.findByRole('heading', { name: 'ALTA home page' });
  fireEvent.click(screen.getByRole('button', { name: 'Account: Sam Steward' }));
  const account = screen.getByRole('dialog', { name: 'Account' });
  expect(account).toHaveTextContent('Signed in as');
  expect(account).toHaveTextContent('Sam Steward');
  fireEvent.click(within(account).getByRole('button', { name: 'Sign out' }));
  expect(await screen.findByRole('heading', { name: 'Sign in' })).toBeInTheDocument();
});

test.each([
  ['/', 'ALTA home page', 'ALTA home'],
  ['/knowledge/docs/at-a-glance', 'ALTA home page', 'ALTA home'],
  ['/knowledge/docs/start-here', 'ALTA home page', 'ALTA home'],
  ['/home', 'Discover landing', 'ALTA'],
  ['/Search', 'Explore page', 'ALTA'],
  ['/productDetails/p1', 'Product page', 'ALTA'],
  ['/adminaccess', 'Admin page', 'ALTA'],
  ['/certify', 'Certify: document-level certification', 'Certify'],
])('with every flag on, %s opens under the one global bar with the current app marked', async (path, heading, current) => {
  stubShell();
  renderApp(path);
  expect(await screen.findByRole('heading', { name: heading })).toBeInTheDocument();
  expect(screen.getAllByRole('banner')).toHaveLength(1);
  expect(document.querySelectorAll('.v3-topbar')).toHaveLength(1);
  expect(screen.getByRole('link', { name: 'Meridian ALTA' })).toHaveAttribute('href', '/');
  await openSwitcher(current);
  const apps = screen.getByRole('navigation', { name: 'Apps' });
  const marked = apps.querySelectorAll('[aria-current="page"]');
  expect(marked).toHaveLength(1);
  expect(marked[0]).toHaveTextContent(current === 'ALTA' ? 'Discover' : current);
});

test('Context is a portal workspace below the global bar', async () => {
  stubShell();
  renderApp('/context');
  await screen.findByRole('main', { name: 'Context' });
  expect(document.querySelector('iframe.gb-frame')).toBeNull();
  expect(screen.getAllByRole('banner')).toHaveLength(1);
});

test('from any app, one click in the switcher reaches the ALTA home and every other app', async () => {
  stubShell();
  renderApp('/home');
  await screen.findByRole('heading', { name: 'Discover landing' });
  await openSwitcher('ALTA');
  fireEvent.click(screen.getByRole('link', { name: 'Context' }));
  expect(await screen.findByRole('main', { name: 'Context' })).toBeInTheDocument();
  await openSwitcher('Context');
  fireEvent.click(screen.getByRole('link', { name: 'ALTA home' }));
  expect(await screen.findByRole('heading', { name: 'ALTA home page' })).toBeInTheDocument();
  await openSwitcher('ALTA home');
  fireEvent.click(within(screen.getByRole('navigation', { name: 'Apps' })).getByRole('link', { name: 'Discover' }));
  expect(await screen.findByRole('heading', { name: 'Discover landing' })).toBeInTheDocument();
  fireEvent.click(screen.getByRole('link', { name: 'Meridian ALTA' }));
  expect(await screen.findByRole('heading', { name: 'ALTA home page' })).toBeInTheDocument();
});

test('a person with no Context role sees the shared no-permission view, from the switcher and on the route', async () => {
  stubShell({ who: READER });
  renderApp('/home');
  await screen.findByRole('heading', { name: 'Discover landing' });
  await openSwitcher('ALTA');
  const context = screen.getByRole('link', { name: 'Context' });
  fireEvent.click(context);
  expect(await screen.findByRole('heading', { name: "You don't have access to Context" })).toBeInTheDocument();
  expect(screen.getByText(/To request access, ask your Access admin for a role in Context/)).toBeInTheDocument();
  expect(screen.queryByRole('main', { name: 'Context' })).toBeNull();
  expect(screen.getByRole('link', { name: 'Back to Meridian ALTA' })).toHaveAttribute('href', '/');
  expect(document.body.textContent).not.toMatch(/get access/i);
});

test.each([
  ['context', '/context', 'Context is not set up here'],
  ['discover', '/home', 'Discover is not set up here'],
  ['discover', '/productDetails/p1', 'Discover is not set up here'],
  ['certify', '/certify', 'Certify is not set up here'],
])('with %s off, %s shows the shared not-set-up view under the bar', async (app, path, heading) => {
  stubShell({ config: configOf({ apps: { [app]: false } }) });
  renderApp(path);
  expect(await screen.findByRole('heading', { name: heading })).toBeInTheDocument();
  expect(screen.getByRole('banner')).toBeInTheDocument();
  expect(screen.queryByRole('main', { name: 'Context' })).toBeNull();
});

test('without KGE configured (no Context MCP address), Context is not set up here', async () => {
  stubShell({ config: configOf({ contextMcpUrl: null }) });
  renderApp('/context');
  expect(await screen.findByRole('heading', { name: 'Context is not set up here' })).toBeInTheDocument();
});

test.each([
  ['certify', '/certify', 'Certify: document-level certification', 'Certify'],
  ['context', '/context', null, 'Context'],
  ['discover', '/home', 'Discover landing', 'ALTA'],
])('with the ALTA home off and only %s on, a signed-in person lands straight in it', async (app, path, heading, label) => {
  stubShell({ config: configOf({ knowledgeHome: false, apps: { certify: false, context: false, discover: false, [app]: true } }) });
  renderApp('/');
  if (heading) expect(await screen.findByRole('heading', { name: heading })).toBeInTheDocument();
  else expect(await screen.findByRole('main', { name: 'Context' })).toBeInTheDocument();
  expect(where()).toBe(path);
  const bar = screen.getByRole('banner');
  expect(within(bar).getByText(label)).toBeInTheDocument();
  expect(within(bar).getByRole('link', { name: 'Meridian ALTA' })).toHaveAttribute('href', '/');
  expect(within(bar).getByRole('link', { name: label })).toHaveAttribute('href', path);
  expect(within(bar).queryByRole('button', { name: /Switch app/ })).toBeNull();
  expect(screen.queryByText('ALTA home page')).toBeNull();
});

test('with the ALTA home off, its pages are not served: the Docs pages fall back to the single app', async () => {
  stubShell({ config: configOf({ knowledgeHome: false, apps: { certify: false, context: false, discover: true } }) });
  renderApp('/knowledge/docs/at-a-glance');
  expect(await screen.findByRole('heading', { name: 'Discover landing' })).toBeInTheDocument();
  expect(where()).toBe('/home');
});

test('an answer naming several apps with the ALTA home off (the server refuses this) opens the first on', async () => {
  stubShell({ config: configOf({ knowledgeHome: false }) });
  renderApp('/');
  expect(await screen.findByRole('heading', { name: 'Certify: document-level certification' })).toBeInTheDocument();
});

test('an answer naming no app at all says so rather than looping', async () => {
  stubShell({ config: configOf({ knowledgeHome: false, apps: { certify: false, context: false, discover: false } }) });
  renderApp('/');
  expect(await screen.findByRole('heading', { name: 'ALTA home is not set up here' })).toBeInTheDocument();
});

test('on a phone the switcher folds into one menu', async () => {
  stubPhone(true);
  stubShell();
  renderApp('/home');
  await screen.findByRole('heading', { name: 'Discover landing' });
  expect(screen.queryByRole('button', { name: /Switch app/ })).toBeNull();
  expect(screen.queryByRole('button', { name: 'Docs' })).toBeNull();
  fireEvent.click(screen.getByRole('button', { name: 'Menu' }));
  const menu = screen.getByRole('navigation', { name: 'Menu' });
  expect(within(menu).getByText('Apps')).toBeInTheDocument();
  expect(within(menu).getByRole('link', { name: 'Discover' })).toHaveAttribute('aria-current', 'page');
  expect(menu).not.toHaveTextContent(/docs/i);
  fireEvent.click(within(menu).getByRole('link', { name: 'ALTA home' }));
  expect(await screen.findByRole('heading', { name: 'ALTA home page' })).toBeInTheDocument();
  expect(screen.queryByRole('navigation', { name: 'Menu' })).toBeNull();
});

test('on a phone with Discover alone, there is no menu at all', async () => {
  stubPhone(true);
  stubShell({ config: configOf({ knowledgeHome: false, apps: { certify: false, context: false, discover: true } }) });
  renderApp('/home');
  await screen.findByRole('heading', { name: 'Discover landing' });
  expect(screen.queryByRole('button', { name: 'Menu' })).toBeNull();
});

test('on a phone with Context alone, there is no global menu', async () => {
  stubPhone(true);
  stubShell({ config: configOf({ knowledgeHome: false, apps: { certify: false, context: true, discover: false } }) });
  renderApp('/context');
  await screen.findByRole('main', { name: 'Context' });
  expect(screen.queryByRole('button', { name: 'Menu' })).toBeNull();
});

test('nothing the shell shows names KGE', async () => {
  stubShell({ who: READER });
  for (const path of ['/', '/certify', '/context', '/home']) {
    const { unmount } = render(
      <Provider store={configureStore({ reducer: { user, search, dataProduct } })}>
        <MemoryRouter initialEntries={[path]}><App /></MemoryRouter>
      </Provider>,
    );
    await waitFor(() => expect(screen.getByRole('banner')).toBeInTheDocument());
    await openSwitcher(path === '/' ? 'ALTA home' : path === '/certify' ? 'Certify' : path === '/context' ? 'Context' : 'ALTA');
    expect(document.body.textContent).not.toMatch(/KGE/);
    unmount();
  }
});

test.each(['/', '/knowledge/docs/start-here', '/certify', '/context'])('%s has exactly one main landmark under the bar', async (path) => {
  stubShell();
  renderApp(path);
  await screen.findByRole('button', { name: /Switch app/ });
  expect(screen.getAllByRole('main')).toHaveLength(1);
  expect(screen.getByRole('banner').closest('main')).toBeNull();
});

test.each([
  ['/', 'ALTA home'],
  ['/knowledge/docs/at-a-glance', 'ALTA home'],
  ['/knowledge/docs/start-here', 'ALTA home'],
  ['/certify', 'Certify'], ['/context', 'Context'],
  ['/home', 'Discover'], ['/Search', 'Explore'],
  ['/productDetails/p1', 'Data product details'], ['/adminaccess', 'Admin access'],
])('the shell sets the page title at %s', async (path, title) => {
  stubShell({ config: configOf({ productName: 'Meridian ALTA' }) });
  renderApp(path);
  await waitFor(() => expect(document.title).toBe(`${title} · Meridian ALTA`));
});

test.each([
  [configOf(), READER, 'No permission'],
  [configOf({ apps: { context: false } }), READER, 'Not set up'],
])('status pages have their own title', async (config, who, title) => {
  stubShell({ config, who });
  renderApp('/context');
  await waitFor(() => expect(document.title).toBe(`${title} · Meridian ALTA`));
});

test('unavailable settings block the page until Retry succeeds', async () => {
  const { fetch } = stubShell();
  const original = fetch.getMockImplementation();
  let failed = true;
  fetch.mockImplementation((url, init) => url === '/api/config' && failed
    ? Promise.resolve({ ok: false }) : original(url, init));
  renderApp('/');
  await screen.findByRole('heading', { name: 'Settings unavailable' });
  expect(screen.queryByRole('banner')).toBeNull();
  expect(screen.queryByText('ALTA home page')).toBeNull();
  failed = false;
  fireEvent.click(screen.getByRole('button', { name: 'Retry' }));
  expect(await screen.findByRole('heading', { name: 'ALTA home page' })).toBeInTheDocument();
});

test('Context alone without its service still reaches its not-set-up page', async () => {
  stubShell({ config: configOf({ knowledgeHome: false, apps: { certify: false, context: true, discover: false }, contextMcpUrl: null }) });
  renderApp('/');
  expect(await screen.findByRole('heading', { name: 'Context is not set up here' })).toBeInTheDocument();
  expect(where()).toBe('/context');
});

test.each([
  '/docs', '/docs/', '/docs/at-a-glance', '/docs/start-here', '/docs/oauth2-redirect',
  '/knowledge/docs', '/knowledge/docs/at-a-glance', '/knowledge/docs/start-here',
])('the portal Docs route %s redirects to the ALTA home', async (path) => {
  stubShell();
  renderApp(path);
  expect(await screen.findByRole('heading', { name: 'ALTA home page' })).toBeInTheDocument();
  expect(where()).toBe('/');
  expect(screen.queryByRole('heading', { name: /At a glance page|Start here page/ })).toBeNull();
});
