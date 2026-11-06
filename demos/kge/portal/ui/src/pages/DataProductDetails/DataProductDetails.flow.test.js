import { beforeEach, expect, test, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { Provider } from 'react-redux';
import { configureStore } from '@reduxjs/toolkit';
import { MemoryRouter, Route, Routes, useNavigate } from 'react-router-dom';
import userReducer from '../../features/user/userSlice';
import dataProductReducer from '../../features/dataProduct/dataProductSlice';
import searchReducer from '../../features/search/searchSlice';
import DataProductDetails from './DataProductDetails';
import { getProduct, submitFeedback } from '../../services/metadata';
import { getConfig, getFavouriteDataProducts, getMyRequests, removeFavouriteProduct, saveFavouriteProduct } from '../../services/user';
import { getCommunityProducts } from '../../services/search';
import { showToast } from '../../components/shared/V3Toast/V3Toast';

vi.mock('../../services/metadata', () => ({ getProduct: vi.fn(), submitFeedback: vi.fn() }));
vi.mock('../../services/user', () => ({ getConfig: vi.fn(), getMyRequests: vi.fn(), requestAccess: vi.fn(), removeFavouriteProduct: vi.fn(), saveFavouriteProduct: vi.fn(), getFavouriteDataProducts: vi.fn() }));
vi.mock('../../services/search', () => ({ getSearchResults: vi.fn(), getCommunityProducts: vi.fn() }));
vi.mock('../../components/shared/V3Toast/V3Toast', () => ({ showToast: vi.fn() }));
vi.mock('../../components/shared/Header/Header', () => ({ default: () => null }));
vi.mock('../../components/askportal/Chatbot', () => ({ default: ({ productId }) => <aside aria-label="Ask panel">{productId}</aside> }));
const { section } = vi.hoisted(() => ({ section: (name) => ({ default: () => <section aria-label={name} /> }) }));
vi.mock('../../components/DataOverview/DataOverviewV3', () => section('Overview section'));
vi.mock('../../components/DataOverview/DataOverview', () => section('Old overview section'));
vi.mock('../../components/DataFields', () => section('Fields section'));
vi.mock('../../components/DataLineage', () => section('Lineage section'));
vi.mock('../../components/DataControls/DataControls', () => section('Controls section'));
vi.mock('../../components/DataPreview', () => section('Preview section'));
vi.mock('../../components/DataQuery', () => section('Query section'));
vi.mock('../../components/UseCases/UseCases', () => section('Use cases section'));

const product = {
  PRODUCTID: 'retail.account', PRODUCTNAME: 'Retail accounts', TECHNICALNAME: 'account', SCHEMANAME: 'retail',
  APPLICATION_IDENTIFIER: 'app-1', HIERARCHY: 'Portal->Retail->Accounts', BADGE_NAME: 'silver',
  COUNTOFCOLUMNS: 10, PARENT_DATASET_COUNT_OF_COLUMNS: 20, COUNTOFRECORDS: '1,200', SIZE: '2 MB', DATA_FORMAT: 'table',
  PRODUCTTYPE: 'Table', DATA_PRODUCT_SOURCE: 'BigQuery', DATA_DOMAIN_NAME: 'Accounts', AVGRATING: 4,
};

const mount = (searchResults = []) => {
  const store = configureStore({
    reducer: { user: userReducer, dataProduct: dataProductReducer, search: searchReducer },
    preloadedState: { search: { ...searchReducer(undefined, { type: 'init' }), searchResults } },
  });
  render(
    <Provider store={store}>
      <MemoryRouter initialEntries={['/productDetails/retail.account']}>
        <Routes>
          <Route path="/productDetails/:id" element={<DataProductDetails />} />
          <Route path="/search" element={<p>Explore page</p>} />
          <Route path="/home" element={<p>Home page</p>} />
        </Routes>
      </MemoryRouter>
    </Provider>,
  );
  return store;
};

beforeEach(() => {
  vi.clearAllMocks();
  getProduct.mockResolvedValue({ data: product });
  getConfig.mockResolvedValue({ data: { ask: true, preview: true, executeQuery: true } });
  getMyRequests.mockResolvedValue({ data: [] });
  saveFavouriteProduct.mockResolvedValue({ status: 'Success' });
  removeFavouriteProduct.mockResolvedValue({ status: 'Success' });
  submitFeedback.mockResolvedValue({ data: {} });
});

test('the product header shows its identity, tier and figures, and records the selection', async () => {
  const store = mount();
  expect(await screen.findByRole('heading', { name: /Retail accounts/ })).toBeInTheDocument();
  expect(screen.getByText('Table · BigQuery · Accounts')).toBeInTheDocument();
  expect(screen.getByAltText('SILVER tier')).toHaveAttribute('title', 'Silver — owned and documented');
  expect(screen.getByRole('button', { name: 'Copy fully qualified name' }).previousSibling).toHaveTextContent('retail.account');
  expect(screen.getByText('10 / 20')).toBeInTheDocument();
  expect(screen.getByText('1,200')).toBeInTheDocument();
  expect(screen.getByText('2 MB · TABLE')).toBeInTheDocument();
  expect(screen.getByText('RATING · 4 / 5')).toBeInTheDocument();
  expect(screen.getByText('0')).toBeInTheDocument();
  expect(screen.getByRole('complementary', { name: 'Ask panel' })).toHaveTextContent('retail.account');
  expect(store.getState().dataProduct.selectedProduct).toBe('retail.account');
  expect(store.getState().dataProduct.selectedProductName).toBe('account');
});

test('extra badges show as chips and a product without a tier says so', async () => {
  getProduct.mockResolvedValue({ data: { ...product, BADGE_NAME: undefined, BADGES: ['pii', 'certified'], PARENT_DATASET_COUNT_OF_COLUMNS: null, VIEWTIMES: 7, SCHEMANAME: '' } });
  mount();
  expect(await screen.findByText('PII')).toHaveClass('chip-rasp');
  expect(screen.getByText('CERTIFIED')).toHaveClass('chip-aqua', 'chip-ok');
  expect(screen.getByAltText('No quality tier')).toHaveAttribute('title', 'No quality tier recorded');
  expect(screen.getByText('7')).toBeInTheDocument();
  expect(screen.getByText('SCHEMA · —')).toBeInTheDocument();
  expect(screen.getByRole('tab', { name: /^Fields/ })).toHaveTextContent('Fields10');
});

test.each([['GOLD', 'Gold — owned, documented and monitored'], ['BRONZE', 'Bronze — catalogued only']])('a %s product shows its tier medal', async (badge, title) => {
  getProduct.mockResolvedValue({ data: { ...product, BADGE_NAME: badge } });
  mount();
  expect(await screen.findByAltText(`${badge} tier`)).toHaveAttribute('title', title);
});

test('each tab shows its section; preview and query appear only when configured', async () => {
  mount();
  await screen.findByRole('region', { name: 'Overview section' });
  for (const [tab, name] of [['Fields', 'Fields section'], ['Field Data Quality', 'Controls section'], ['Lineage', 'Lineage section'], ['Data preview', 'Preview section'], ['Run a query', 'Query section'], ['Overview', 'Overview section']]) {
    fireEvent.click(screen.getByRole('tab', { name: new RegExp(`^${tab}`) }));
    expect(screen.getByRole('tab', { name: new RegExp(`^${tab}`) })).toHaveAttribute('aria-selected', 'true');
    expect(screen.getByRole('region', { name })).toBeInTheDocument();
  }
});

test.each([
  ['a config without data', () => getConfig.mockResolvedValue({})],
  ['a config failure', () => getConfig.mockRejectedValue(new Error('down'))],
  ['a config with Ask off', () => getConfig.mockResolvedValue({ data: { ask: false } })],
])('%s leaves only the core tabs and keeps Ask on the product', async (_, arrange) => {
  vi.spyOn(console, 'log').mockImplementation(() => {});
  arrange();
  mount();
  await screen.findByRole('heading', { name: /Retail accounts/ });
  expect(screen.getAllByRole('tab').map((tab) => tab.textContent.replace(/\d+$/, ''))).toEqual(['Overview', 'Fields', 'Field Data Quality', 'Lineage']);
  expect(screen.getByRole('complementary', { name: 'Ask panel' })).toHaveTextContent('retail.account');
});

test('saving and removing the product updates the button, the results and confirms with a toast', async () => {
  const store = mount([{ PRODUCTID: 'retail.account', SUBSCRIBE_COUNT: 2 }, { PRODUCTID: 'other', SUBSCRIBE_COUNT: 1 }]);
  const save = await screen.findByRole('button', { name: 'Save to my products' });
  expect(save).toHaveAttribute('aria-pressed', 'false');
  fireEvent.click(save);
  expect(saveFavouriteProduct).toHaveBeenCalledWith({ productId: 'retail.account', applicationId: 'app-1', technicalName: 'account' });
  await waitFor(() => expect(save).toHaveAttribute('aria-pressed', 'true'));
  expect(showToast).toHaveBeenCalledWith('Saved to favourites');
  expect(store.getState().search.searchResults).toEqual([{ PRODUCTID: 'retail.account', SUBSCRIBE_COUNT: 3, FAVOURITED: true }, { PRODUCTID: 'other', SUBSCRIBE_COUNT: 1 }]);

  fireEvent.click(save);
  expect(removeFavouriteProduct).toHaveBeenCalledWith({ productId: 'retail.account', applicationId: 'app-1', technicalName: 'account' });
  await waitFor(() => expect(save).toHaveAttribute('aria-pressed', 'false'));
  expect(showToast).toHaveBeenCalledWith('Removed from favourites');
  expect(store.getState().search.searchResults[0]).toMatchObject({ SUBSCRIBE_COUNT: 2, FAVOURITED: false });
});

test.each([
  ['save', { ISFAVOURITE: null }, saveFavouriteProduct, 'false'],
  ['remove', { ISFAVOURITE: 'Y' }, removeFavouriteProduct, 'true'],
])('a refused %s leaves the product as it was', async (_, extra, call, pressed) => {
  vi.spyOn(console, 'log').mockImplementation(() => {});
  getProduct.mockResolvedValue({ data: { ...product, ...extra } });
  call.mockResolvedValue({ status: 'Fail', message: 'no' });
  mount();
  const save = await screen.findByRole('button', { name: 'Save to my products' });
  fireEvent.click(save);
  await waitFor(() => expect(call).toHaveBeenCalled());
  expect(save).toHaveAttribute('aria-pressed', pressed);
  expect(showToast).not.toHaveBeenCalled();
});

test('a breadcrumb opens Explore for that part of the hierarchy', async () => {
  getCommunityProducts.mockResolvedValue({ data: { searchList: [{ PRODUCTID: 'retail.account' }, { PRODUCTID: 'retail.loan' }], domainFilter: ['Accounts'], communityFilter: ['Retail'], sourceFilter: [], typeFilter: [], hierarchy: ['Retail'] } });
  getFavouriteDataProducts.mockResolvedValue({ data: [{ DATA_PRODUCT_ID: 'retail.loan' }] });
  const store = mount();
  await screen.findByRole('heading', { name: /Retail accounts/ });
  const crumbs = screen.getByRole('navigation', { name: 'Breadcrumb' });
  expect(crumbs).toHaveTextContent('HOME/Portal/Retail/Retail accounts');
  fireEvent.click(screen.getByRole('link', { name: 'Retail' }));
  expect(await screen.findByText('Explore page')).toBeInTheDocument();
  expect(getCommunityProducts).toHaveBeenCalledWith({ searchParam: 'Retail' });
  await waitFor(() => expect(store.getState().search.searchResults).toEqual([{ PRODUCTID: 'retail.account', FAVOURITED: false }, { PRODUCTID: 'retail.loan', FAVOURITED: true }]));
  expect(store.getState().search.searchParam).toBe('Retail');
  expect(store.getState().search.domainFilter).toEqual([{ value: 'Accounts', label: 'Accounts', parent: 'DOMAIN' }]);
});

test('a breadcrumb search that returns nothing leaves the results alone', async () => {
  vi.spyOn(console, 'log').mockImplementation(() => {});
  getCommunityProducts.mockResolvedValue({});
  getFavouriteDataProducts.mockResolvedValue({ data: [] });
  const store = mount([{ PRODUCTID: 'kept' }]);
  await screen.findByRole('heading', { name: /Retail accounts/ });
  fireEvent.click(screen.getByRole('link', { name: 'Retail' }));
  await screen.findByText('Explore page');
  await waitFor(() => expect(getCommunityProducts).toHaveBeenCalled());
  expect(store.getState().search.searchResults).toEqual([{ PRODUCTID: 'kept' }]);
});

test('the full name can be copied', async () => {
  const writeText = vi.fn();
  Object.defineProperty(navigator, 'clipboard', { value: { writeText }, configurable: true });
  mount();
  fireEvent.click(await screen.findByRole('button', { name: 'Copy fully qualified name' }));
  expect(writeText).toHaveBeenCalledWith('retail.account');
  expect(showToast).toHaveBeenCalledWith('Copied to clipboard');
});

test('copying without a clipboard fails quietly', async () => {
  Object.defineProperty(navigator, 'clipboard', { value: undefined, configurable: true });
  mount();
  fireEvent.click(await screen.findByRole('button', { name: 'Copy fully qualified name' }));
  expect(showToast).not.toHaveBeenCalled();
});

test('product feedback is sent with its rating and comment', async () => {
  mount();
  await screen.findByRole('heading', { name: /Retail accounts/ });
  fireEvent.change(screen.getByRole('combobox', { name: 'Rating' }), { target: { value: '3' } });
  fireEvent.change(screen.getByRole('textbox', { name: 'Feedback comment' }), { target: { value: 'Useful columns' } });
  fireEvent.click(screen.getByRole('button', { name: 'Send feedback' }));
  expect(submitFeedback).toHaveBeenCalledWith('retail.account', 3, 'Useful columns');
  await waitFor(() => expect(showToast).toHaveBeenCalledWith('Feedback saved'));
  expect(screen.getByRole('textbox', { name: 'Feedback comment' })).toHaveValue('');
});

test('feedback that cannot be saved says so and keeps the comment', async () => {
  submitFeedback.mockRejectedValue(new Error('down'));
  mount();
  await screen.findByRole('heading', { name: /Retail accounts/ });
  fireEvent.change(screen.getByRole('textbox', { name: 'Feedback comment' }), { target: { value: 'Keep me' } });
  fireEvent.click(screen.getByRole('button', { name: 'Send feedback' }));
  await waitFor(() => expect(showToast).toHaveBeenCalledWith('Feedback could not be saved'));
  expect(screen.getByRole('textbox', { name: 'Feedback comment' })).toHaveValue('Keep me');
});

test.each([undefined, false])('table queries are hidden when executeQuery is %s, with preview still available', async (executeQuery) => {
  getConfig.mockResolvedValue({ data: { preview: true, executeQuery } });
  mount();
  await screen.findByRole('tab', { name: 'Data preview' });
  expect(screen.queryByRole('tab', { name: 'Run a query' })).toBeNull();
  expect(screen.queryByRole('region', { name: 'Query section' })).toBeNull();
});


test('the asset page header displays Connector for API/MCP', async () => {
  getProduct.mockResolvedValue({ data: { ...product, PRODUCTTYPE: 'API/MCP' } });
  mount();
  expect(await screen.findByText('Connector · BigQuery · Accounts')).toBeInTheDocument();
  expect(screen.queryByText(/API\/MCP/)).toBeNull();
});


test.each(['Documents', 'API/MCP'])('a %s asset strip shows its cadence and no stand-in records, storage, columns, schema or Lineage tab', async (type) => {
  getProduct.mockResolvedValue({ data: { ...product, PRODUCTTYPE: type, REFRESH_CADENCE: 'Weekly', LAST_REFRESH: '2026-10-20' } });
  mount();
  expect(await screen.findByText('Refresh cadence')).toBeInTheDocument();
  expect(screen.getByText('Weekly')).toBeInTheDocument();
  for (const text of ['Last refresh', '2026-10-20', 'Source records (before read filters)', '1,200', '2 MB · TABLE', 'Columns', '10 / 20', 'SCHEMA · retail']) expect(screen.queryByText(text)).toBeNull();
  expect(screen.queryByRole('tab', { name: 'Lineage' })).toBeNull();
  expect(screen.getByRole('tab', { name: 'Overview' })).toBeInTheDocument();
});


test('a Documents asset without a cadence shows a dash in its place', async () => {
  getProduct.mockResolvedValue({ data: { ...product, PRODUCTTYPE: 'Documents', LAST_REFRESH: '2026-10-20' } });
  mount();
  expect(await screen.findByText('Refresh cadence')).toBeInTheDocument();
  expect(screen.getByText('Refresh cadence').nextSibling).toHaveTextContent('—');
  expect(screen.queryByText('2026-10-20')).toBeNull();
});


test.each(['Table', 'View'])('a %s asset keeps its strip and its Lineage tab', async (type) => {
  getProduct.mockResolvedValue({ data: { ...product, PRODUCTTYPE: type, REFRESH_CADENCE: 'Weekly', LAST_REFRESH: '2026-10-20' } });
  mount();
  expect(await screen.findByText('Last refresh')).toBeInTheDocument();
  for (const text of ['2026-10-20', 'Source records (before read filters)', '1,200', '2 MB · TABLE', 'Columns', '10 / 20', 'SCHEMA · retail']) expect(screen.getByText(text)).toBeInTheDocument();
  expect(screen.queryByText('Refresh cadence')).toBeNull();
  expect(screen.queryByText('Weekly')).toBeNull();
  fireEvent.click(screen.getByRole('tab', { name: 'Lineage' }));
  expect(await screen.findByRole('region', { name: 'Lineage section' })).toBeInTheDocument();
});


test.each(['Documents', 'API/MCP'])('moving from a Table on its Lineage tab to a %s asset opens the overview', async (type) => {
  const store = configureStore({ reducer: { user: userReducer, dataProduct: dataProductReducer, search: searchReducer } });
  const Go = () => { const navigate = useNavigate(); return <button onClick={() => navigate('/productDetails/retail.policies')}>Next asset</button>; };
  getProduct.mockImplementation((id) => Promise.resolve({ data: id === 'retail.account' ? product : { ...product, PRODUCTID: id, PRODUCTNAME: 'Policies', PRODUCTTYPE: type } }));
  render(
    <Provider store={store}>
      <MemoryRouter initialEntries={['/productDetails/retail.account']}>
        <Routes><Route path="/productDetails/:id" element={<><DataProductDetails /><Go /></>} /></Routes>
      </MemoryRouter>
    </Provider>,
  );
  fireEvent.click(await screen.findByRole('tab', { name: 'Lineage' }));
  expect(await screen.findByRole('region', { name: 'Lineage section' })).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: 'Next asset' }));
  expect(await screen.findByRole('heading', { name: /Policies/ })).toBeInTheDocument();
  expect(screen.queryByRole('tab', { name: 'Lineage' })).toBeNull();
  expect(screen.getByRole('tab', { name: 'Overview' })).toHaveAttribute('aria-selected', 'true');
  expect(screen.getByRole('region', { name: 'Overview section' })).toBeInTheDocument();
  expect(screen.queryByRole('region', { name: 'Lineage section' })).toBeNull();
});


test('a late answer for an earlier asset never replaces the current one, and the old asset is not shown while the next loads', async () => {
  const store = configureStore({ reducer: { user: userReducer, dataProduct: dataProductReducer, search: searchReducer } });
  const pending = {};
  const Go = () => { const navigate = useNavigate(); return <>{['b', 'c'].map((id) => <button key={id} onClick={() => navigate(`/productDetails/retail.${id}`)}>Go {id}</button>)}</>; };
  getProduct.mockImplementation((id) => id === 'retail.account' ? Promise.resolve({ data: product }) : new Promise((resolve) => { pending[id] = resolve; }));
  const asset = (id, name) => ({ data: { ...product, PRODUCTID: id, PRODUCTNAME: name } });
  render(
    <Provider store={store}>
      <MemoryRouter initialEntries={['/productDetails/retail.account']}>
        <Routes><Route path="/productDetails/:id" element={<><DataProductDetails /><Go /></>} /></Routes>
      </MemoryRouter>
    </Provider>,
  );
  expect(await screen.findByRole('heading', { name: /Retail accounts/ })).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: 'Go b' }));
  await waitFor(() => expect(screen.queryByRole('heading', { name: /Retail accounts/ })).toBeNull());
  fireEvent.click(screen.getByRole('button', { name: 'Go c' }));
  await waitFor(() => expect(pending['retail.c']).toBeDefined());
  pending['retail.c'](asset('retail.c', 'Asset C'));
  expect(await screen.findByRole('heading', { name: /Asset C/ })).toBeInTheDocument();
  pending['retail.b'](asset('retail.b', 'Asset B'));
  await new Promise((resolve) => setTimeout(resolve, 20));
  expect(screen.getByRole('heading', { name: /Asset C/ })).toBeInTheDocument();
  expect(screen.queryByRole('heading', { name: /Asset B/ })).toBeNull();
  expect(store.getState().dataProduct.selectedProduct).toBe('retail.c');
});
