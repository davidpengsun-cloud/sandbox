import { beforeEach, expect, test, vi } from 'vitest';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { Provider } from 'react-redux';
import { configureStore } from '@reduxjs/toolkit';
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom';
import userReducer from '../../features/user/userSlice';
import dataProductReducer from '../../features/dataProduct/dataProductSlice';
import searchReducer from '../../features/search/searchSlice';
import Search from './Search';
import { filterSearchResults, getSearchResults, getCommunityProducts } from '../../services/search';
import { getFavouriteDataProducts, removeFavouriteProduct, saveFavouriteProduct } from '../../services/user';
import { getFields, getProduct } from '../../services/metadata';

vi.mock('../../services/search', () => ({ getSearchResults: vi.fn(), getCommunityProducts: vi.fn(), filterSearchResults: vi.fn() }));
vi.mock('../../services/user', () => ({ getFavouriteDataProducts: vi.fn(), saveFavouriteProduct: vi.fn(), removeFavouriteProduct: vi.fn(), saveViewedDataProduct: vi.fn() }));
vi.mock('../../services/metadata', () => ({ getFields: vi.fn(), getProduct: vi.fn() }));
vi.mock('../../components/shared/Header/Header', () => ({
  default: ({ toggleChatbot, askScope, askOpen }) => <button onClick={toggleChatbot} aria-expanded={askOpen}>{toggleChatbot ? `Ask about ${askScope}` : 'Ask'}</button>,
}));
vi.mock('../../components/askportal/Chatbot', () => ({
  default: ({ domainMode, scopeLabel, productIds, showChatbot }) => <aside aria-label="Ask panel" data-open={String(showChatbot)} data-domain={String(Boolean(domainMode))}>{scopeLabel}: {productIds.join(',')}</aside>,
}));

const product = (id, extra = {}) => ({ PRODUCTID: id, PRODUCTNAME: id.toUpperCase(), COMMUNITY: 'Retail', DOMAIN: 'Accounts', PRODUCTTYPE: 'Table', PRODUCTSOURCE: 'BigQuery', SUBSCRIBE_COUNT: 1, ...extra });
const catalog = {
  searchList: [product('alpha', { BADGE_NAME: 'GOLD', SUBSCRIBE_COUNT: 5 }), product('bravo', { BADGE_NAME: 'SILVER', SUBSCRIBE_COUNT: 2 })],
  domainFilter: ['Accounts', 'Loans'], communityFilter: ['Retail'], sourceFilter: ['BigQuery'], typeFilter: ['Table'], hierarchy: [],
};
const Where = () => <output aria-label="location">{useLocation().pathname + useLocation().search}</output>;
function mount(path = '/search') {
  const store = configureStore({ reducer: { user: userReducer, dataProduct: dataProductReducer, search: searchReducer } });
  render(<Provider store={store}><MemoryRouter initialEntries={[path]}>
    <Routes><Route path="*" element={<><Search /><Where /></>} /></Routes>
  </MemoryRouter></Provider>);
  return store;
}
const rows = () => within(screen.getByRole('region', { name: 'Results' })).getAllByRole('row').slice(1);
beforeEach(() => {
  vi.clearAllMocks();
  getSearchResults.mockResolvedValue({ data: catalog });
  getCommunityProducts.mockResolvedValue({ data: catalog });
  getFavouriteDataProducts.mockResolvedValue({ data: [{ DATA_PRODUCT_ID: 'bravo' }] });
  filterSearchResults.mockResolvedValue({ data: [product('alpha', { BADGE_NAME: 'GOLD' })] });
  getProduct.mockResolvedValue({ data: { COUNTOFRECORDS: 20, COUNTOFCOLUMNS: 1, SCHEMANAME: 'retail' } });
  getFields.mockResolvedValue({ data: [{ 'Field Name': 'account_id', 'Data Type': 'TEXT' }] });
  saveFavouriteProduct.mockResolvedValue({ status: 'Success' });
  removeFavouriteProduct.mockResolvedValue({ status: 'Success' });
});

test('catalog defaults to a table with a selected preview and the scoped facet counts', async () => {
  mount();
  await waitFor(() => expect(rows()).toHaveLength(2));
  expect(getSearchResults).toHaveBeenCalledWith({ searchParam: null });
  expect(screen.getByRole('button', { name: 'Table' })).toHaveAttribute('aria-pressed', 'true');
  expect(rows()[0]).toHaveAttribute('aria-selected', 'true');
  expect(within(screen.getByRole('complementary', { name: 'Preview' })).getByText('Alpha')).toBeInTheDocument();
  await waitFor(() => expect(getFields).toHaveBeenCalledWith('alpha'));
  expect(await screen.findByText('account_id')).toBeInTheDocument();
  expect(within(screen.getByRole('complementary', { name: 'Filters' })).getByText('Retail').parentElement).toHaveTextContent('2');
});

test.each([
  ['one community', [product('a'), product('b')], '', 'Retail', 'a,b'],
  ['one domain across communities', [product('a'), product('b', { COMMUNITY: 'Wealth' })], '', 'Accounts', 'a,b'],
  ['mixed results under a search', [product('a'), product('b', { COMMUNITY: 'Wealth', DOMAIN: 'Loans' })], '?q=balances', 'balances', 'a,b'],
  ['mixed results without a search', [product('a'), product('b', { COMMUNITY: 'Wealth', DOMAIN: 'Loans' })], '', 'Current results', 'a,b'],
  ['no results under a search', [], '?q=nothing', 'nothing', ''],
  ['no results without a search', [], '', 'Current results', ''],
])('Explore always offers Ask, scoped to %s', async (_, list, query, label, ids) => {
  getSearchResults.mockResolvedValue({ data: { ...catalog, searchList: list } });
  mount(`/search${query}`);
  const panel = screen.getByRole('complementary', { name: 'Ask panel' });
  await waitFor(() => expect(panel.textContent).toBe(`${label}: ${ids}`));
  expect(panel).toHaveAttribute('data-domain', 'true');
  const ask = screen.getByRole('button', { name: `Ask about ${label}` });
  expect(ask).toHaveAttribute('aria-expanded', 'false');
  fireEvent.click(ask);
  expect(ask).toHaveAttribute('aria-expanded', 'true');
  expect(panel).toHaveAttribute('data-open', 'true');
});

test('facet groups start expanded and can be collapsed with their available-choice count', async () => {
  mount();
  await waitFor(() => expect(rows()).toHaveLength(2));
  const filters = within(screen.getByRole('complementary', { name: 'Filters' }));
  const community = filters.getByRole('button', { name: 'Community, 1 available choice, collapse filters' });
  expect(community).toHaveAttribute('aria-expanded', 'true');
  expect(community).toHaveTextContent('1');
  expect(filters.getByRole('checkbox', { name: /Retail/ })).toBeInTheDocument();

  fireEvent.click(community);
  expect(community).toHaveAttribute('aria-expanded', 'false');
  expect(community).toHaveTextContent('+');
  expect(filters.queryByRole('checkbox', { name: /Retail/ })).toBeNull();

  fireEvent.click(community);
  expect(community).toHaveAttribute('aria-expanded', 'true');
  expect(filters.getByRole('checkbox', { name: /Retail/ })).toBeInTheDocument();
});

test('URL community and domain survive filtering and clearing; facets use COMMUNITY', async () => {
  const store = mount('/search?community=Retail&domain=Accounts');
  await waitFor(() => expect(rows()).toHaveLength(2));
  expect(getCommunityProducts).toHaveBeenCalledWith({ searchParam: 'Retail', domain: 'Accounts', isDomain: false });
  const facet = within(screen.getByRole('complementary', { name: 'Filters' }));
  fireEvent.click(facet.getByRole('checkbox', { name: /Retail/ }));
  await waitFor(() => expect(filterSearchResults).toHaveBeenCalledWith(expect.objectContaining({
    browseScope: { community: 'Retail', domain: 'Accounts' },
    filterParams: [{ parent: 'COMMUNITY', value: 'Retail', label: 'Retail' }],
  })));
  expect(screen.getByRole('status', { name: 'location' })).toHaveTextContent('community=Retail&domain=Accounts');
  fireEvent.click(screen.getAllByRole('button', { name: 'Clear all' })[0]);
  await waitFor(() => expect(rows()).toHaveLength(2));
  expect(store.getState().search.browseScope).toEqual({ community: 'Retail', domain: 'Accounts' });
  expect(getSearchResults).not.toHaveBeenCalled();
});

test('tier, freshness and sort are URL state; sorting does not need a server sort', async () => {
  const recent = new Date().toISOString().slice(0, 10);
  getSearchResults.mockResolvedValue({ data: { ...catalog, searchList: [
    product('zulu', { BADGE_NAME: 'GOLD', LAST_REFRESH: recent }),
    product('alpha', { BADGE_NAME: 'SILVER', LAST_REFRESH: '2020-01-01' }),
  ] } });
  mount('/search?q=retail');
  await waitFor(() => expect(rows()).toHaveLength(2));
  fireEvent.change(screen.getByRole('combobox', { name: 'Sort results' }), { target: { value: 'prdtNameZ-A' } });
  await waitFor(() => expect(rows()[0]).toHaveTextContent('Zulu'));
  expect(screen.getByRole('status', { name: 'location' })).toHaveTextContent('sort=prdtNameZ-A');
  const facets = within(screen.getByRole('complementary', { name: 'Filters' }));
  fireEvent.click(facets.getByRole('checkbox', { name: /GOLD/ }));
  await waitFor(() => expect(rows()).toHaveLength(1));
  fireEvent.click(facets.getByRole('radio', { name: /Loaded in 7 days/ }));
  expect(screen.getByRole('status', { name: 'location' })).toHaveTextContent('tier=GOLD&fresh=1');
  expect(getSearchResults).toHaveBeenCalledTimes(1);
});

test('keyboard selection and double click open the selected data asset', async () => {
  mount();
  await waitFor(() => expect(rows()).toHaveLength(2));
  fireEvent.keyDown(rows()[0], { key: 'ArrowDown' });
  expect(rows()[1]).toHaveAttribute('aria-selected', 'true');
  fireEvent.keyDown(rows()[1], { key: 'Enter' });
  expect(screen.getByRole('status', { name: 'location' })).toHaveTextContent('/productDetails/bravo');
});

test('cards allow saving; pagination shows 24 and loads more', async () => {
  getSearchResults.mockResolvedValue({ data: { ...catalog, searchList: Array.from({ length: 30 }, (_, i) => product(`p${i}`)) } });
  mount();
  await waitFor(() => expect(rows()).toHaveLength(24));
  expect(screen.getByText('Showing 24 of 30')).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: 'Load 6 more' }));
  expect(rows()).toHaveLength(30);
  fireEvent.click(screen.getByRole('button', { name: 'Cards' }));
  expect(screen.getAllByRole('button', { name: /^Open P/ })).toHaveLength(30);
  fireEvent.click(screen.getByRole('button', { name: 'Favourite P0' }));
  await waitFor(() => expect(saveFavouriteProduct).toHaveBeenCalledWith(expect.objectContaining({ productId: 'p0' })));
});

test('an API error is distinct from empty results and can retry', async () => {
  getSearchResults.mockRejectedValueOnce(new Error('offline'));
  mount();
  expect(await screen.findByRole('alert')).toHaveTextContent("Results didn't load");
  fireEvent.click(screen.getByRole('button', { name: 'Try again' }));
  await waitFor(() => expect(rows()).toHaveLength(2));
});

test('mobile filter drawer stays inert when closed and locks scrolling when open', async () => {
  mount();
  await waitFor(() => expect(rows()).toHaveLength(2));
  const drawer = document.querySelector('.cx-drawer');
  expect(drawer).toHaveAttribute('inert', '');
  fireEvent.click(screen.getByRole('button', { name: 'Filters' }));
  expect(drawer).not.toHaveAttribute('inert');
  expect(document.body.style.overflow).toBe('hidden');
  fireEvent.click(within(drawer).getByRole('button', { name: 'Close filters' }));
  expect(drawer).toHaveAttribute('aria-hidden', 'true');
  expect(document.body.style.overflow).toBe('');
});
