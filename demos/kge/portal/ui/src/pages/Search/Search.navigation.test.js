import { beforeEach, expect, test, vi } from 'vitest';
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { Provider } from 'react-redux';
import { configureStore } from '@reduxjs/toolkit';
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom';
import userReducer from '../../features/user/userSlice';
import dataProductReducer from '../../features/dataProduct/dataProductSlice';
import searchReducer from '../../features/search/searchSlice';
import Header from '../../components/shared/Header/Header';
import LandingPage from '../LandingPage/LandingPage';
import Search from './Search';
import { filterSearchResults, getSearchData, getSearchResults } from '../../services/search';
import { getConfig, getDataCommunities, getFavouriteDataProducts, getRelatedDataProducts, getViewedDataProducts, saveViewedDataProduct } from '../../services/user';

vi.mock('../../services/search', () => ({ getSearchData: vi.fn(), getSearchResults: vi.fn(), getCommunityProducts: vi.fn(), filterSearchResults: vi.fn() }));
vi.mock('../../services/user', () => ({
  getConfig: vi.fn(), getDataCommunities: vi.fn(), getFavouriteDataProducts: vi.fn(), getRelatedDataProducts: vi.fn(), getViewedDataProducts: vi.fn(),
  saveViewedDataProduct: vi.fn(), saveFavouriteProduct: vi.fn(), removeFavouriteProduct: vi.fn(),
}));
vi.mock('../../services/metadata', () => ({ getFields: vi.fn().mockResolvedValue({ data: [] }), getProduct: vi.fn().mockResolvedValue({ data: {} }) }));
vi.mock('../../components/shell/GlobalBar', () => ({ default: ({ search, actions }) => <header>{search}{actions}</header> }));
vi.mock('../../components/askportal/Chatbot', () => ({ default: () => null }));

const catalog = {
  searchList: [{ PRODUCTID: 'accounts', PRODUCTNAME: 'Accounts', DOMAIN: 'Retail', COMMUNITY: 'Client data' }],
  domainFilter: ['Retail'], communityFilter: ['Client data'], sourceFilter: ['BigQuery'], typeFilter: ['Table'], hierarchy: [],
};
const Where = () => {
  const location = useLocation();
  return <output aria-label="location" data-key={location.key} data-track={String(Boolean(location.state?.track))}>{location.pathname}{location.search}</output>;
};
function mount(origin) {
  const store = configureStore({ reducer: { user: userReducer, dataProduct: dataProductReducer, search: searchReducer } });
  render(<Provider store={store}><MemoryRouter initialEntries={[origin === 'header' ? '/header' : '/home']}>
    <Where />
    <Routes>
      <Route path="/header" element={<Header />} />
      <Route path="/home" element={<LandingPage />} />
      <Route path="/search" element={<Search />} />
    </Routes>
  </MemoryRouter></Provider>);
  return store;
}
const submit = () => {
  const input = screen.getByRole('searchbox', { name: 'Search the portal' });
  fireEvent.change(input, { target: { value: ' accounts ' } });
  fireEvent.keyDown(input, { key: 'Enter' });
};

beforeEach(() => {
  vi.clearAllMocks();
  getFavouriteDataProducts.mockResolvedValue({ data: [{ DATA_PRODUCT_ID: 'accounts' }] });
  getRelatedDataProducts.mockResolvedValue({ data: [] });
  getViewedDataProducts.mockResolvedValue({ data: [] });
  getConfig.mockResolvedValue({ data: {} });
  getDataCommunities.mockResolvedValue({ data: { communityList: [] } });
  getSearchData.mockResolvedValue({ data: ['accounts'] });
  getSearchResults.mockResolvedValue({ data: catalog });
  filterSearchResults.mockResolvedValue({ data: catalog.searchList });
  saveViewedDataProduct.mockResolvedValue({ status: 'Success' });
});

for (const origin of ['header', 'landing']) {
  test.each(['Enter', 'picked suggestion', 'keyboard suggestion'])(`${origin}: %s opens results with loading placeholders before its one request resolves, then records history once`, async (mode) => {
    let finish;
    getSearchResults.mockImplementation(() => new Promise((resolve) => { finish = resolve; }));
    const store = mount(origin);
    const input = screen.getByRole('searchbox', { name: 'Search the portal' });
    fireEvent.change(input, { target: { value: mode === 'Enter' ? ' accounts ' : 'acc' } });
    if (mode !== 'Enter') {
      const option = await screen.findByRole('option', { name: 'accounts' });
      if (mode === 'picked suggestion') fireEvent.mouseDown(option);
      else {
        fireEvent.keyDown(input, { key: 'ArrowDown' });
        fireEvent.keyDown(input, { key: 'Enter' });
      }
    } else fireEvent.keyDown(input, { key: 'Enter' });
    expect(screen.getByRole('status', { name: 'location' })).toHaveTextContent('/search?q=accounts');
    expect(screen.getByRole('heading', { name: 'Results for accounts' })).toBeInTheDocument();
    const results = screen.getByRole('region', { name: 'Results' });
    expect(results.querySelectorAll('tbody tr[aria-hidden="true"]')).toHaveLength(8);
    expect(getSearchResults).toHaveBeenCalledExactlyOnceWith({ searchParam: 'accounts' });
    expect(getFavouriteDataProducts).toHaveBeenCalledTimes(origin === 'landing' ? 2 : 1);
    expect(saveViewedDataProduct).not.toHaveBeenCalled();
    await act(async () => finish({ data: catalog }));
    await screen.findByRole('button', { name: 'Open Accounts' });
    expect(saveViewedDataProduct).toHaveBeenCalledExactlyOnceWith({ productId: 0, productName: null, searchWord: 'accounts', category: 'Valid', tab: 'SEARCH' });
    expect(store.getState().search.searchResults[0].FAVOURITED).toBe(true);
    expect(store.getState().search.domainFilter).toEqual([{ value: 'Retail', label: 'Retail', parent: 'DOMAIN' }]);
    expect(screen.getByRole('status', { name: 'location' })).toHaveAttribute('data-track', 'false');
    fireEvent.change(screen.getByRole('combobox', { name: 'Sort results' }), { target: { value: 'prdtNameA-Z' } });
    const filters = within(screen.getByRole('complementary', { name: 'Filters' }));
    fireEvent.click(filters.getByRole('checkbox', { name: /Client data/ }));
    await waitFor(() => expect(filterSearchResults).toHaveBeenCalledTimes(1));
    await screen.findByRole('button', { name: 'Open Accounts' });
    expect(getSearchResults).toHaveBeenCalledTimes(1);
    expect(saveViewedDataProduct).toHaveBeenCalledTimes(1);
  });

  test(`${origin}: an empty search result records Invalid exactly once`, async () => {
    getSearchResults.mockResolvedValue({ data: { ...catalog, searchList: [] } });
    mount(origin);
    submit();
    await screen.findByText('No data assets match');
    expect(getSearchResults).toHaveBeenCalledTimes(1);
    expect(saveViewedDataProduct).toHaveBeenCalledExactlyOnceWith({ productId: 0, productName: null, searchWord: 'accounts', category: 'Invalid', tab: 'SEARCH' });
  });

  test(`${origin}: a rejected search shows the results page error and a retry records the successful search once`, async () => {
    getSearchResults.mockRejectedValueOnce(new Error('offline'));
    mount(origin);
    submit();
    expect(await screen.findByRole('alert')).toHaveTextContent("Results didn't load");
    expect(screen.getByRole('status', { name: 'location' })).toHaveTextContent('/search?q=accounts');
    expect(getSearchResults).toHaveBeenCalledTimes(1);
    expect(saveViewedDataProduct).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: 'Try again' }));
    await screen.findByRole('button', { name: 'Open Accounts' });
    expect(getSearchResults).toHaveBeenCalledTimes(2);
    expect(saveViewedDataProduct).toHaveBeenCalledTimes(1);
  });

  test(`${origin}: a blank query still records the search once`, async () => {
    mount(origin);
    fireEvent.keyDown(screen.getByRole('searchbox', { name: 'Search the portal' }), { key: 'Enter' });
    await screen.findByRole('button', { name: 'Open Accounts' });
    expect(getSearchResults).toHaveBeenCalledExactlyOnceWith({ searchParam: null });
    expect(saveViewedDataProduct).toHaveBeenCalledExactlyOnceWith({ productId: 0, productName: null, searchWord: null, category: 'Valid', tab: 'SEARCH' });
  });

  test(`${origin}: leaving a pending search for Home discards its late result and history`, async () => {
    let finish;
    getSearchResults.mockImplementationOnce(() => new Promise((resolve) => { finish = resolve; }));
    const store = mount(origin);
    submit();
    expect(getSearchResults).toHaveBeenCalledExactlyOnceWith({ searchParam: 'accounts' });
    expect(screen.getByRole('status', { name: 'location' })).toHaveTextContent('/search?q=accounts');
    const searchState = store.getState().search;
    fireEvent.click(screen.getByRole('button', { name: 'Workspace' }));
    expect(screen.getByRole('status', { name: 'location' }).textContent).toBe('/home');
    await act(async () => finish({ data: catalog }));
    expect(screen.getByRole('status', { name: 'location' }).textContent).toBe('/home');
    expect(saveViewedDataProduct).not.toHaveBeenCalled();
    expect(store.getState().search).toEqual(searchState);
    expect(store.getState().search.searchResults).toEqual([]);
    expect(screen.queryByRole('region', { name: 'Results' })).toBeNull();
  });
}

test('a superseded submission cannot store, navigate or record history after the newer search completes', async () => {
  let finish;
  const current = { ...catalog, searchList: [{ ...catalog.searchList[0], PRODUCTID: 'current-accounts', PRODUCTNAME: 'Current Accounts' }] };
  getSearchResults.mockImplementationOnce(() => new Promise((resolve) => { finish = resolve; }))
    .mockResolvedValueOnce({ data: current });
  const store = mount('header');
  submit();
  submit();
  await screen.findByRole('button', { name: 'Open Current Accounts' });
  expect(getSearchResults).toHaveBeenCalledTimes(2);
  expect(saveViewedDataProduct).toHaveBeenCalledTimes(1);
  const searchState = store.getState().search;
  const location = screen.getByRole('status', { name: 'location' });
  /* the router commits location changes in a transition, so the newer search's
     one replace (dropping track) lands just after its results render */
  await waitFor(() => expect(location).toHaveAttribute('data-track', 'false'));
  const locationKey = location.getAttribute('data-key');
  expect(saveViewedDataProduct).toHaveBeenCalledTimes(1);
  await act(async () => finish({ data: catalog }));
  expect(location.textContent).toBe('/search?q=accounts');
  expect(location).toHaveAttribute('data-key', locationKey);
  expect(location).toHaveAttribute('data-track', 'false');
  expect(store.getState().search).toEqual(searchState);
  expect(saveViewedDataProduct).toHaveBeenCalledTimes(1);
  fireEvent.change(screen.getByRole('combobox', { name: 'Sort results' }), { target: { value: 'prdtNameA-Z' } });
  await screen.findByRole('button', { name: 'Open Current Accounts' });
  await waitFor(() => expect(store.getState().search.sortOption).toBe('prdtNameA-Z'));
  expect(getSearchResults).toHaveBeenCalledTimes(2);
  expect(store.getState().search.searchResults.map((product) => product.PRODUCTID)).toEqual(['current-accounts']);
  expect(saveViewedDataProduct).toHaveBeenCalledTimes(1);
});

test('submitting the query already shown searches and records it once per submission', async () => {
  const store = configureStore({ reducer: { user: userReducer, dataProduct: dataProductReducer, search: searchReducer } });
  render(<Provider store={store}><MemoryRouter initialEntries={['/search?q=accounts']}>
    <Where />
    <Routes>
      <Route path="/search" element={<Search />} />
    </Routes>
  </MemoryRouter></Provider>);
  await screen.findByRole('button', { name: 'Open Accounts' });
  expect(getSearchResults).toHaveBeenCalledTimes(1);
  expect(saveViewedDataProduct).not.toHaveBeenCalled();
  for (const round of [1, 2]) {
    submit();
    await waitFor(() => expect(saveViewedDataProduct).toHaveBeenCalledTimes(round));
    await waitFor(() => expect(screen.getByRole('status', { name: 'location' })).toHaveAttribute('data-track', 'false'));
    await screen.findByRole('button', { name: 'Open Accounts' });
    expect(screen.getByRole('status', { name: 'location' })).toHaveTextContent('/search?q=accounts');
    expect(getSearchResults).toHaveBeenCalledTimes(1 + round);
    expect(getSearchResults).toHaveBeenLastCalledWith({ searchParam: 'accounts' });
    expect(saveViewedDataProduct).toHaveBeenLastCalledWith({ productId: 0, productName: null, searchWord: 'accounts', category: 'Valid', tab: 'SEARCH' });
  }
  await act(async () => { await new Promise((resolve) => setTimeout(resolve, 50)); });
  expect(getSearchResults).toHaveBeenCalledTimes(3);
  expect(saveViewedDataProduct).toHaveBeenCalledTimes(2);
});
