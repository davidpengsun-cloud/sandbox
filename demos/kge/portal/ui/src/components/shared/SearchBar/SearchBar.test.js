import { afterEach, beforeEach, expect, test, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { Provider } from 'react-redux';
import { configureStore } from '@reduxjs/toolkit';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import searchReducer from '../../../features/search/searchSlice';
import SearchBar from './SearchBar';
import { getSearchResults } from '../../../services/search';
import { getFavouriteDataProducts } from '../../../services/user';

vi.mock('../../../services/search', () => ({ getSearchResults: vi.fn() }));
vi.mock('../../../services/user', () => ({ getFavouriteDataProducts: vi.fn() }));

const response = { data: { searchList: [{ PRODUCTID: 'a' }, { PRODUCTID: 'b' }], domainFilter: ['Sales'], communityFilter: ['Retail'], sourceFilter: ['W'], typeFilter: ['Table'], hierarchy: ['h'] } };

const setup = () => {
  const store = configureStore({ reducer: { search: searchReducer }, preloadedState: { search: { ...searchReducer(undefined, { type: 'x' }), sortOption: 'prdtNameA-Z', filterOptions: [{ value: 'x' }] } } });
  const view = render(
    <Provider store={store}>
      <MemoryRouter>
        <Routes>
          <Route path="/" element={<SearchBar searchBarStyle={['bar', 'input']} iconWidth="2rem" />} />
          <Route path="/search" element={<p>search page</p>} />
        </Routes>
      </MemoryRouter>
    </Provider>,
  );
  return { store, ...view };
};

beforeEach(() => {
  vi.clearAllMocks();
  getSearchResults.mockResolvedValue(response);
  getFavouriteDataProducts.mockResolvedValue({ data: [{ DATA_PRODUCT_ID: 'b' }] });
});
afterEach(() => vi.restoreAllMocks());

test('Enter searches, clears the old filters and opens Explore with the results', async () => {
  const { store } = setup();
  const input = screen.getByPlaceholderText(/Search the portal/);
  fireEvent.keyUp(input, { key: 'Enter', target: { value: 'orders' } });
  expect(await screen.findByText('search page')).toBeInTheDocument();
  expect(getSearchResults).toHaveBeenCalledWith({ searchParam: 'orders' });
  const s = store.getState().search;
  expect(s.searchParam).toBe('orders');
  expect(s.sortOption).toBe('');
  expect(s.filterOptions).toEqual([]);
  expect(s.searchResults).toEqual([{ PRODUCTID: 'a', FAVOURITED: false }, { PRODUCTID: 'b', FAVOURITED: true }]);
  expect(s.typeFilter).toEqual([{ value: 'Table', label: 'Table', parent: 'PRODUCTTYPE' }]);
});

test('typing without Enter does not search', () => {
  setup();
  fireEvent.keyUp(screen.getByPlaceholderText(/Search the portal/), { key: 'o', target: { value: 'o' } });
  expect(getSearchResults).not.toHaveBeenCalled();
});

test('the search icon searches what was typed', async () => {
  const { container, store } = setup();
  fireEvent.keyUp(screen.getByPlaceholderText(/Search the portal/), { key: 's', target: { value: 'sales' } });
  fireEvent.click(container.querySelector('svg'));
  expect(await screen.findByText('search page')).toBeInTheDocument();
  expect(getSearchResults).toHaveBeenCalledWith({ searchParam: 'sales' });
  expect(store.getState().search.hierarchyList).toEqual(['h']);
});

test.each(['enter', 'icon'])('an incomplete response via %s still opens Explore without results', async (how) => {
  vi.spyOn(console, 'log').mockImplementation(() => {});
  getFavouriteDataProducts.mockResolvedValue({});
  const { container, store } = setup();
  if (how === 'enter') fireEvent.keyUp(screen.getByPlaceholderText(/Search the portal/), { key: 'Enter', target: { value: 'x' } });
  else fireEvent.click(container.querySelector('svg'));
  expect(await screen.findByText('search page')).toBeInTheDocument();
  expect(store.getState().search.searchResults).toEqual([]);
});

test.each(['enter', 'icon'])('a failed search via %s is logged and stays put', async (how) => {
  vi.spyOn(console, 'log').mockImplementation(() => {});
  getSearchResults.mockRejectedValue(new Error('down'));
  const { container } = setup();
  if (how === 'enter') fireEvent.keyUp(screen.getByPlaceholderText(/Search the portal/), { key: 'Enter', target: { value: 'x' } });
  else fireEvent.click(container.querySelector('svg'));
  await waitFor(() => expect(console.log).toHaveBeenCalledWith(expect.any(Error)));
  expect(screen.queryByText('search page')).toBeNull();
});
