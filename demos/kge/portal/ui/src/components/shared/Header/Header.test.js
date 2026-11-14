import { afterEach, beforeEach, expect, test, vi } from 'vitest';
import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { Provider } from 'react-redux';
import { configureStore } from '@reduxjs/toolkit';
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom';
import userReducer from '../../../features/user/userSlice';
import searchReducer from '../../../features/search/searchSlice';
import dataProductReducer from '../../../features/dataProduct/dataProductSlice';
import Header from './Header';
import { configOf } from '../../shell/testSupport';
import { getSearchData, getSearchResults } from '../../../services/search';
import { getFavouriteDataProducts, saveViewedDataProduct } from '../../../services/user';

vi.mock('../../../services/search', () => ({ getSearchResults: vi.fn(), getSearchData: vi.fn() }));
vi.mock('../../../services/user', () => ({ getFavouriteDataProducts: vi.fn(), saveViewedDataProduct: vi.fn() }));
vi.mock('../../askportal/Chatbot', () => ({
  default: ({ domainMode, scopeLabel, showChatbot, setShowChatbot }) => (
    <aside aria-label="Ask panel" data-open={String(showChatbot)} data-domain={String(Boolean(domainMode))}>
      {scopeLabel}
      <button onClick={() => setShowChatbot(false)}>Close Ask</button>
    </aside>
  ),
}));

const results = {
  data: {
    searchList: [{ PRODUCTID: 'p1', PRODUCTNAME: 'Customer' }, { PRODUCTID: 'p2', PRODUCTNAME: 'Orders' }],
    domainFilter: ['Retail'], communityFilter: ['Fund'], sourceFilter: ['BigQuery'], typeFilter: ['Table'], hierarchy: [],
  },
};

function Explore() {
  const location = useLocation();
  return (
    <>
      <p>Explore page</p>
      <output aria-label="Explore location" data-state={JSON.stringify(location.state)}>{`${location.pathname}${location.search}`}</output>
    </>
  );
}

function expectExploreOpenedAtOnce(url) {
  expect(screen.getByText('Explore page')).toBeInTheDocument();
  const where = screen.getByRole('status', { name: 'Explore location' });
  expect(where.textContent).toBe(url);
  expect(JSON.parse(where.dataset.state)).toEqual({ track: true, submission: expect.stringMatching(/^\d+-\d+$/) });
  expect(getSearchResults).not.toHaveBeenCalled();
  expect(getFavouriteDataProducts).not.toHaveBeenCalled();
  expect(saveViewedDataProduct).not.toHaveBeenCalled();
}

function renderHeader(props = {}, user = { userName: 'Ada Analyst', userRole: 'member' }) {
  const store = configureStore({ reducer: { user: userReducer, search: searchReducer, dataProduct: dataProductReducer }, preloadedState: { user: { isLoggedIn: true, data: user } } });
  render(
    <Provider store={store}>
      <MemoryRouter initialEntries={['/productDetails/p1']}>
        <Routes>
          <Route path="/productDetails/:id" element={<Header {...props} />} />
          <Route path="/home" element={<p>Home page</p>} />
          <Route path="/search" element={<Explore />} />
          <Route path="/adminaccess" element={<p>Admin page</p>} />
          <Route path="/" element={<p>Sign-in page</p>} />
        </Routes>
      </MemoryRouter>
    </Provider>,
  );
  return store;
}

const searchBox = () => screen.getByRole('searchbox', { name: 'Search the portal' });
const openAccount = (name = 'Ada Analyst') => fireEvent.click(screen.getByRole('button', { name: `Account: ${name}` }));

async function typeAndSuggest(value) {
  fireEvent.change(searchBox(), { target: { value } });
  await act(() => vi.advanceTimersByTime(200));
}

beforeEach(() => {
  vi.clearAllMocks();
  getSearchResults.mockResolvedValue(results);
  getFavouriteDataProducts.mockResolvedValue({ data: [{ DATA_PRODUCT_ID: 'p2' }] });
  vi.stubGlobal('fetch', vi.fn((url) => Promise.resolve(url === '/api/config'
    ? { ok: true, json: async () => configOf() }
    : { ok: true })));
});
afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

test('the Ask action takes the configured product short name, and the page title the product name', async () => {
  fetch.mockImplementation((url) => Promise.resolve(url === '/api/config'
    ? { ok: true, json: async () => configOf({ productName: 'Meridian Beacon', productShortName: 'Beacon' }) }
    : { ok: true }));
  renderHeader({ askScope: 'Orders' });
  const ask = await screen.findByRole('button', { name: 'Ask Beacon about Orders' });
  expect(ask).toHaveTextContent('Ask Beacon');
  await waitFor(() => expect(document.title).toBe('Data product details · Meridian Beacon'));
});

test('shows the signed-in person and hides Admin from a member', () => {
  renderHeader();
  expect(screen.getByText('Ada Analyst')).toBeInTheDocument();
  expect(screen.getByText('AA')).toBeInTheDocument();
  openAccount();
  expect(screen.getByRole('dialog', { name: 'Account' })).toHaveTextContent('Ada Analyst');
  expect(screen.getByText('MEMBER')).toBeInTheDocument();
  expect(screen.queryByRole('link', { name: 'Admin' })).toBeNull();
  expect(within(screen.getByRole('dialog', { name: 'Account' })).queryByText('Admin')).toBeNull();
});

test('a guest with no profile is shown as a guest member', () => {
  renderHeader({}, null);
  expect(screen.getByText('Guest user')).toBeInTheDocument();
  expect(document.querySelector('.v3-id-dot')).toBeNull();
  openAccount('Guest user');
  expect(screen.getByText('MEMBER')).toBeInTheDocument();
});

test('an Access admin sees the display label while portal roles control the menu', () => {
  renderHeader({}, { userName: 'Access operator', userRole: 'admin', userRoles: ['viewer', 'admin'], userRoleLabel: 'Access admin' });
  openAccount('Access operator');
  expect(screen.getByText('ACCESS ADMIN')).toBeInTheDocument();
  expect(within(screen.getByRole('navigation', { name: 'Discover' })).getByRole('link', { name: 'Admin' })).toHaveAttribute('href', '/adminaccess');
  expect(within(screen.getByRole('dialog', { name: 'Account' })).queryByText('Admin')).toBeNull();
});

test.each(['admin', 'publisher', 'approver'])('a %s sees Admin and it opens the admin page', async (userRole) => {
  renderHeader({}, { userName: 'Bo', userRole });
  openAccount('Bo');
  expect(within(screen.getByRole('dialog', { name: 'Account' })).queryByText('Admin')).toBeNull();
  fireEvent.click(within(screen.getByRole('navigation', { name: 'Discover' })).getByRole('link', { name: 'Admin' }));
  expect(await screen.findByText('Admin page')).toBeInTheDocument();
});

test('the chip shows the primary portal role and any membership grants the admin menu', () => {
  renderHeader({}, { userName: 'Bo', userRole: 'publisher', userRoles: ['approver', 'publisher'] });
  openAccount('Bo');
  expect(screen.getByText('PUBLISHER')).toBeInTheDocument();
  expect(screen.queryByText(/APPROVER/)).toBeNull();
  expect(within(screen.getByRole('navigation', { name: 'Discover' })).getByRole('link', { name: 'Admin' })).toHaveAttribute('href', '/adminaccess');
  expect(within(screen.getByRole('dialog', { name: 'Account' })).queryByText('Admin')).toBeNull();
});

test('a refreshed membership list overrides a stale primary role', () => {
  renderHeader({}, { userName: 'Bo', userRole: 'admin', userRoles: ['viewer'] });
  openAccount('Bo');
  expect(screen.queryByRole('link', { name: 'Admin' })).toBeNull();
  expect(within(screen.getByRole('dialog', { name: 'Account' })).queryByText('Admin')).toBeNull();
});

test("Discover's navigation, search and Ask share the global bar and go to Discover's home", async () => {
  renderHeader();
  const own = screen.getByRole('navigation', { name: 'Discover' });
  const bar = screen.getByRole('banner');
  expect(bar).toContainElement(own);
  expect(bar).toContainElement(searchBox());
  expect(bar).toContainElement(screen.getByRole('button', { name: 'Ask ALTA' }));
  expect(await screen.findByRole('link', { name: 'Meridian ALTA' })).toHaveAttribute('href', '/');
  fireEvent.click(screen.getByRole('link', { name: 'ALTA' }));
  expect(screen.getByText('Home page')).toBeInTheDocument();
});

test('the home header leaves its search to the hero, and the app switcher marks Discover', async () => {
  renderHeader({ type: 'home' });
  expect(screen.queryByRole('searchbox', { name: 'Search the portal' })).toBeNull();
  fireEvent.click(await screen.findByRole('button', { name: 'Switch app, current: ALTA' }));
  expect(within(screen.getByRole('navigation', { name: 'Apps' })).getByRole('link', { name: 'Discover' })).toHaveAttribute('aria-current', 'page');
});

test('Sign out ends the session and returns to sign-in', async () => {
  renderHeader();
  openAccount();
  fireEvent.click(screen.getByRole('button', { name: 'Sign out' }));
  expect(await screen.findByText('Sign-in page')).toBeInTheDocument();
  expect(fetch).toHaveBeenCalledWith('/session', { method: 'DELETE', credentials: 'include' });
});

test('a page that provides an Ask handler gets the Ask button scoped to it, and the bar opens no panel of its own', () => {
  const toggleChatbot = vi.fn();
  renderHeader({ toggleChatbot, askScope: 'Customer', askOpen: false });
  const ask = screen.getByRole('button', { name: 'Ask ALTA about Customer' });
  expect(ask).toHaveTextContent('Ask ALTA');
  expect(ask).toHaveAttribute('aria-expanded', 'false');
  fireEvent.click(ask);
  expect(toggleChatbot).toHaveBeenCalledTimes(1);
  expect(screen.queryByRole('complementary', { name: 'Ask panel' })).toBeNull();
});

test.each([['home'], ['other'], ['DPD'], [undefined]])('the %s header always shows Ask, and without a page handler it opens a panel over every data asset', (type) => {
  renderHeader({ type });
  const ask = screen.getByRole('button', { name: 'Ask ALTA' });
  expect(ask).toHaveAttribute('aria-expanded', 'false');
  expect(screen.queryByRole('complementary', { name: 'Ask panel' })).toBeNull();
  fireEvent.click(ask);
  expect(ask).toHaveAttribute('aria-expanded', 'true');
  const panel = screen.getByRole('complementary', { name: 'Ask panel' });
  expect(panel).toHaveAttribute('data-open', 'true');
  expect(panel).toHaveAttribute('data-domain', 'true');
  expect(panel).toHaveTextContent('All data assets');
  fireEvent.click(screen.getByRole('button', { name: 'Close Ask' }));
  expect(ask).toHaveAttribute('aria-expanded', 'false');
  expect(panel).toHaveAttribute('data-open', 'false');
  fireEvent.click(ask);
  expect(panel).toHaveAttribute('data-open', 'true');
});

test('Enter opens Explore for the trimmed term at once, leaving the search to the results page', () => {
  const store = renderHeader();
  fireEvent.change(searchBox(), { target: { value: '  customer  ' } });
  fireEvent.keyDown(searchBox(), { key: 'Enter' });
  expectExploreOpenedAtOnce('/search?q=customer');
  const state = store.getState().search;
  expect(state.searchParam).toBe('customer');
  expect(state.searchResults).toEqual([]);
});

test('Enter in an empty box opens the unfiltered results page at once', () => {
  getSearchResults.mockResolvedValue({ data: { ...results.data, searchList: [] } });
  const store = renderHeader();
  fireEvent.keyDown(searchBox(), { key: 'Enter' });
  expectExploreOpenedAtOnce('/search');
  expect(store.getState().search.searchParam).toBeNull();
});

test('a search answered without data still opens Explore', async () => {
  getFavouriteDataProducts.mockResolvedValue({});
  const store = renderHeader();
  fireEvent.keyDown(searchBox(), { key: 'Enter' });
  expect(await screen.findByText('Explore page')).toBeInTheDocument();
  expect(store.getState().search.searchResults).toEqual([]);
});

test('a search that would fail still opens the results page at once, where the failure shows', () => {
  getSearchResults.mockRejectedValue(new Error('down'));
  renderHeader();
  fireEvent.change(searchBox(), { target: { value: 'customer' } });
  fireEvent.keyDown(searchBox(), { key: 'Enter' });
  expectExploreOpenedAtOnce('/search?q=customer');
});

test('typing shows suggestions; arrows move the highlight and Enter opens Explore for it at once', async () => {
  vi.useFakeTimers();
  getSearchData.mockResolvedValue({ data: ['customer', { value: 'customer_orders' }, { label: 'customer_tier' }] });
  renderHeader();
  await typeAndSuggest('cust');
  expect(getSearchData).toHaveBeenCalledWith('cust');
  const options = screen.getAllByRole('option');
  expect(options.map((o) => o.textContent)).toEqual(['customer', 'customer_orders', 'customer_tier']);
  fireEvent.keyDown(searchBox(), { key: 'ArrowDown' });
  fireEvent.keyDown(searchBox(), { key: 'ArrowDown' });
  expect(screen.getAllByRole('option')[1]).toHaveAttribute('aria-selected', 'true');
  fireEvent.keyDown(searchBox(), { key: 'ArrowUp' });
  fireEvent.keyDown(searchBox(), { key: 'ArrowUp' });
  expect(screen.getAllByRole('option')[2]).toHaveAttribute('aria-selected', 'true');
  fireEvent.keyDown(searchBox(), { key: 'Enter' });
  expectExploreOpenedAtOnce('/search?q=customer_tier');
  expect(screen.queryByRole('listbox')).toBeNull();
});

test('clicking a suggestion opens Explore for it at once; hovering highlights it', async () => {
  vi.useFakeTimers();
  getSearchData.mockResolvedValue({ data: ['orders'] });
  renderHeader();
  await typeAndSuggest('ord');
  const option = screen.getByRole('option', { name: 'orders' });
  fireEvent.mouseEnter(option);
  expect(option).toHaveAttribute('aria-selected', 'true');
  fireEvent.mouseDown(option);
  expectExploreOpenedAtOnce('/search?q=orders');
});

test('Escape and a click elsewhere close the suggestions; focus reopens them', async () => {
  vi.useFakeTimers();
  getSearchData.mockResolvedValue({ data: ['orders'] });
  renderHeader();
  await typeAndSuggest('ord');
  fireEvent.keyDown(searchBox(), { key: 'Escape' });
  expect(screen.queryByRole('listbox')).toBeNull();
  fireEvent.focus(searchBox());
  expect(screen.getByRole('listbox')).toBeInTheDocument();
  fireEvent.mouseDown(document.body);
  expect(screen.queryByRole('listbox')).toBeNull();
});

test('no suggestions for an empty box, a non-list answer or a failure', async () => {
  vi.useFakeTimers();
  renderHeader();
  fireEvent.change(searchBox(), { target: { value: 'a' } });
  await typeAndSuggest('');
  expect(getSearchData).not.toHaveBeenCalled();
  getSearchData.mockResolvedValueOnce({ data: { not: 'a list' } });
  await typeAndSuggest('x');
  expect(screen.queryByRole('listbox')).toBeNull();
  getSearchData.mockRejectedValueOnce(new Error('down'));
  await typeAndSuggest('xy');
  expect(screen.queryByRole('listbox')).toBeNull();
  getSearchData.mockResolvedValueOnce({ data: [] });
  await typeAndSuggest('xyz');
  expect(screen.queryByRole('listbox')).toBeNull();
});

test('typing quickly asks for suggestions once', async () => {
  vi.useFakeTimers();
  getSearchData.mockResolvedValue({ data: [] });
  renderHeader();
  fireEvent.change(searchBox(), { target: { value: 'c' } });
  fireEvent.change(searchBox(), { target: { value: 'cu' } });
  await act(() => vi.advanceTimersByTime(200));
  expect(getSearchData).toHaveBeenCalledTimes(1);
  expect(getSearchData).toHaveBeenCalledWith('cu');
});

test('an empty suggestion value opens the unfiltered results page at once, and closed arrows do nothing', async () => {
  vi.useFakeTimers();
  getSearchData.mockResolvedValue({ data: [{}] });
  renderHeader();
  fireEvent.keyDown(searchBox(), { key: 'ArrowDown' });
  fireEvent.keyDown(searchBox(), { key: 'ArrowUp' });
  fireEvent.change(searchBox(), { target: { value: 'x' } });
  await typeAndSuggest('x');
  fireEvent.keyDown(searchBox(), { key: 'ArrowDown' });
  fireEvent.keyDown(searchBox(), { key: 'Enter' });
  expectExploreOpenedAtOnce('/search');
});

test('Ctrl-K or Cmd-K focuses the search box', () => {
  renderHeader();
  fireEvent.keyDown(document, { key: 'k', ctrlKey: true });
  expect(searchBox()).toHaveFocus();
  searchBox().blur();
  fireEvent.keyDown(document, { key: 'K', metaKey: true });
  expect(searchBox()).toHaveFocus();
});

test('the account menu closes on Escape with focus back on the chip, and on a click outside', () => {
  renderHeader();
  const trigger = screen.getByRole('button', { name: 'Account: Ada Analyst' });
  expect(trigger).toHaveAttribute('aria-expanded', 'false');
  fireEvent.click(trigger);
  expect(trigger).toHaveAttribute('aria-expanded', 'true');
  expect(screen.getByRole('dialog', { name: 'Account' })).toHaveFocus();
  expect(document.querySelector('.v3-id-dot')).not.toBeNull();
  fireEvent.keyDown(document, { key: 'Escape' });
  expect(screen.queryByRole('dialog')).toBeNull();
  expect(trigger).toHaveFocus();
  fireEvent.click(trigger);
  fireEvent.mouseDown(screen.getByRole('dialog', { name: 'Account' }));
  expect(screen.getByRole('dialog', { name: 'Account' })).toBeInTheDocument();
  fireEvent.mouseDown(document.body);
  expect(screen.queryByRole('dialog')).toBeNull();
  fireEvent.click(trigger);
  fireEvent.click(trigger);
  expect(screen.queryByRole('dialog')).toBeNull();
});

test('the brand shows the initial tile, and the bar no longer links to KGE\'s own page', async () => {
  vi.unstubAllGlobals();
  renderHeader();
  expect(screen.getByText('A')).toHaveAttribute('data-brand', 'tile');
  expect(await screen.findByRole('link', { name: 'Meridian ALTA' })).toBeInTheDocument();
  expect(screen.queryByRole('link', { name: 'Ask ALTA' })).toBeNull();
  expect(screen.getByRole('button', { name: 'Ask ALTA' })).toBeInTheDocument();
});

test('a configured logo shows in the bar, and a KGE address in /brand adds no link to it', async () => {
  fetch.mockImplementation((url) => Promise.resolve(url === '/brand'
    ? { ok: true, json: () => Promise.resolve({ name: 'ALTA: AI-powered Library for Trusted Assets', logo: true, kge_url: 'https://kge.example.invalid/' }) }
    : { ok: true }));
  renderHeader({ toggleChatbot: vi.fn() });
  await waitFor(() => expect(document.querySelector('img[data-brand="logo"]')).toHaveAttribute('src', '/brand/logo'));
  expect(document.querySelector('img[data-brand="logo"]').closest('.gb-home').querySelector('.gb-name')).toBeNull();
  expect(document.querySelector('a[href="https://kge.example.invalid/"]')).toBeNull();
  expect(screen.getByRole('button', { name: 'Ask ALTA' })).toBeInTheDocument();
});

test.each([false, true])('the logo title setting %s controls the visible name while the home link remains accessible', async (logoTitle) => {
  fetch.mockImplementation((url) => Promise.resolve(url === '/brand'
    ? { ok: true, json: async () => ({ name: 'ALTA', logo: true }) }
    : url === '/api/config' ? { ok: true, json: async () => configOf({ logoTitle }) } : { ok: true }));
  renderHeader();
  const link = await screen.findByRole('link', { name: 'Meridian ALTA' });
  await waitFor(() => expect(link.querySelector('img')).toHaveAttribute('src', '/brand/logo'));
  expect(link).toHaveAttribute('href', '/');
  if (logoTitle) expect(link.querySelector('.gb-name')).toHaveTextContent('Meridian ALTA');
  else expect(link.querySelector('.gb-name')).toBeNull();
});

test('a brand answer that fails or is malformed keeps the fallback', async () => {
  fetch.mockImplementation((url) => Promise.resolve(url === '/brand' ? { ok: false } : { ok: true }));
  renderHeader();
  await waitFor(() => expect(fetch).toHaveBeenCalledWith('/brand', expect.anything()));
  expect(screen.getByText('A')).toHaveAttribute('data-brand', 'tile');
});

test('the theme button cycles System, Light, Dark and back, and remembers the choice', () => {
  window.localStorage.removeItem('v3-theme');
  renderHeader();
  const button = () => screen.getByRole('button', { name: /^Theme: / });
  expect(button()).toHaveAccessibleName('Theme: System');
  expect(button()).toHaveAttribute('title', 'Theme: System');
  fireEvent.click(button());
  expect(button()).toHaveAccessibleName('Theme: Light');
  expect(document.documentElement.dataset.theme).toBe('light');
  expect(window.localStorage.getItem('v3-theme')).toBe('light');
  fireEvent.click(button());
  expect(button()).toHaveAccessibleName('Theme: Dark');
  expect(document.documentElement.dataset.theme).toBe('dark');
  expect(window.localStorage.getItem('v3-theme')).toBe('dark');
  fireEvent.click(button());
  expect(button()).toHaveAccessibleName('Theme: System');
  expect(document.documentElement.dataset.theme).toBeUndefined();
  expect(window.localStorage.getItem('v3-theme')).toBeNull();
});

test('a stored theme is read back, and a blocked storage still switches the page', () => {
  window.localStorage.setItem('v3-theme', 'dark');
  renderHeader();
  expect(screen.getByRole('button', { name: 'Theme: Dark' })).toBeInTheDocument();
  window.localStorage.removeItem('v3-theme');
  const blocked = () => { throw new Error('blocked'); };
  const spies = ['getItem', 'setItem', 'removeItem'].map((method) => vi.spyOn(window.localStorage, method).mockImplementation(blocked));
  try {
    fireEvent.click(screen.getByRole('button', { name: 'Theme: Dark' }));
    expect(screen.getByRole('button', { name: 'Theme: System' })).toBeInTheDocument();
    expect(document.documentElement.dataset.theme).toBeUndefined();
    fireEvent.click(screen.getByRole('button', { name: 'Theme: System' }));
    expect(document.documentElement.dataset.theme).toBe('light');
    cleanup();
    renderHeader();
    expect(screen.getByRole('button', { name: 'Theme: System' })).toBeInTheDocument();
    expect(spies[0]).toHaveBeenCalled();
  } finally {
    spies.forEach((spy) => spy.mockRestore());
  }
});


test('Discover has a single header row containing the controls in order', async () => {
  renderHeader({}, { userName: 'Bo', userRole: 'admin' });
  const brand = await screen.findByRole('link', { name: 'Meridian ALTA' });
  expect(brand).toHaveAccessibleName('Meridian ALTA');
  const bar = screen.getByRole('banner');
  expect(document.querySelectorAll('.v3-topbar')).toHaveLength(1);
  expect(document.querySelector('.v3-appbar')).toBeNull();
  expect([...bar.querySelectorAll('a, input, button')].map((el) => el === brand ? el.getAttribute('aria-label') : el.getAttribute('aria-label') || el.textContent.trim())).toEqual([
    'Meridian ALTA', 'ALTA', 'Admin', 'Search the portal', 'Ask ALTA',
    'Switch app, current: ALTA', 'Theme: System', 'Account: Bo',
  ]);
  expect(searchBox().closest('.v3-search').parentElement).toBe(bar);
});
