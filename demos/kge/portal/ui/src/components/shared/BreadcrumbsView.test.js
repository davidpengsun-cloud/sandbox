import { beforeEach, expect, test, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { Provider } from 'react-redux';
import { configureStore } from '@reduxjs/toolkit';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import userReducer from '../../features/user/userSlice';
import searchReducer from '../../features/search/searchSlice';
import dataProductReducer from '../../features/dataProduct/dataProductSlice';
import BreadcrumbsView from './BreadcrumbsView';
import { getSearchResults } from '../../services/search';
import { getFavouriteDataProducts } from '../../services/user';

vi.mock('../../services/search', () => ({ getSearchResults: vi.fn() }));
vi.mock('../../services/user', () => ({ getFavouriteDataProducts: vi.fn() }));

const searchResponse = {
  data: { searchList: [{ PRODUCTID: 'p1' }, { PRODUCTID: 'p2' }], domainFilter: ['Retail'], communityFilter: [], sourceFilter: [], typeFilter: [], hierarchy: [] },
};

function renderCrumbs(props, searchResults = []) {
  const store = configureStore({
    reducer: { user: userReducer, search: searchReducer, dataProduct: dataProductReducer },
    preloadedState: { search: { ...searchReducer(undefined, { type: 'init' }), searchResults } },
  });
  render(
    <Provider store={store}>
      <MemoryRouter initialEntries={['/here']}>
        <Routes>
          <Route path="/here" element={<BreadcrumbsView {...props} />} />
          <Route path="/search" element={<p>Explore page</p>} />
          <Route path="/home" element={<p>Home page</p>} />
        </Routes>
      </MemoryRouter>
    </Provider>,
  );
  return store;
}

beforeEach(() => {
  vi.clearAllMocks();
  getSearchResults.mockResolvedValue(searchResponse);
  getFavouriteDataProducts.mockResolvedValue({ data: [{ DATA_PRODUCT_ID: 'p2' }] });
});

test('on Explore the trail is Home then Explore, and Home is a link', () => {
  renderCrumbs({ page: 'search' });
  expect(screen.getByRole('link', { name: 'HOME' })).toHaveAttribute('href', '/home');
  expect(screen.getByText('EXPLORE')).toBeInTheDocument();
  expect(screen.queryByRole('link', { name: 'EXPLORE' })).toBeNull();
});

test('on a product the trail ends with its name, and Explore loads results when there are none', async () => {
  const store = renderCrumbs({ page: 'productDetails', name: 'Customer' });
  expect(screen.getByText('Customer')).toBeInTheDocument();
  fireEvent.click(screen.getByRole('link', { name: 'EXPLORE' }));
  expect(await screen.findByText('Explore page')).toBeInTheDocument();
  expect(getSearchResults).toHaveBeenCalledWith({ searchParam: null });
  await waitFor(() => expect(store.getState().search.searchResults).toHaveLength(2));
  expect(store.getState().search.searchResults.map((r) => r.FAVOURITED)).toEqual([false, true]);
});

test('Explore keeps existing results without searching again', async () => {
  renderCrumbs({ page: 'productDetails', name: 'Customer' }, [{ PRODUCTID: 'kept' }]);
  fireEvent.click(screen.getByRole('link', { name: 'EXPLORE' }));
  expect(await screen.findByText('Explore page')).toBeInTheDocument();
  expect(getSearchResults).not.toHaveBeenCalled();
});

test('an empty or failed answer leaves the results empty', async () => {
  const log = vi.spyOn(console, 'log').mockImplementation(() => {});
  getFavouriteDataProducts.mockResolvedValueOnce({});
  const store = renderCrumbs({ page: 'productDetails', name: 'Customer' });
  fireEvent.click(screen.getByRole('link', { name: 'EXPLORE' }));
  await waitFor(() => expect(log).toHaveBeenCalledWith('Error', expect.anything()));
  expect(store.getState().search.searchResults).toEqual([]);
  log.mockRestore();
});

test('a failed search is caught', async () => {
  const log = vi.spyOn(console, 'log').mockImplementation(() => {});
  getSearchResults.mockRejectedValueOnce(new Error('down'));
  renderCrumbs({ page: 'productDetails', name: 'Customer' });
  fireEvent.click(screen.getByRole('link', { name: 'EXPLORE' }));
  await waitFor(() => expect(log).toHaveBeenCalledWith(expect.any(Error)));
  log.mockRestore();
});

test('an unknown page shows no trail', () => {
  renderCrumbs({ page: 'other' });
  expect(screen.queryByRole('link')).toBeNull();
});
