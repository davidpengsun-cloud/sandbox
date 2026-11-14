import { afterEach, beforeEach, expect, test, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { Provider } from 'react-redux';
import { configureStore } from '@reduxjs/toolkit';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import searchReducer from '../../../features/search/searchSlice';
import TypeAheadSearchBar from './TypeAheadSearchBar';
import { getSearchData, getSearchResults } from '../../../services/search';
import { getFavouriteDataProducts, saveViewedDataProduct } from '../../../services/user';

vi.mock('../../../services/search', () => ({ getSearchResults: vi.fn(), getSearchData: vi.fn() }));
vi.mock('../../../services/user', () => ({ getFavouriteDataProducts: vi.fn(), saveViewedDataProduct: vi.fn() }));

const response = (list) => ({ data: { searchList: list, domainFilter: ['Sales'], communityFilter: [], sourceFilter: [], typeFilter: [], hierarchy: [] } });

const pick = async (text) => {
  await waitFor(() => expect(document.querySelector(`.ant-select-item-option[title="${text}"]`)).not.toBeNull());
  return document.querySelector(`.ant-select-item-option[title="${text}"]`);
};
const type = (input, value) => {
  fireEvent.change(input, { target: { value } });
  fireEvent.keyUp(input, { key: 'Enter', target: { value } });
};

const setup = () => {
  const store = configureStore({ reducer: { search: searchReducer } });
  const view = render(
    <Provider store={store}>
      <MemoryRouter>
        <Routes>
          <Route path="/" element={<TypeAheadSearchBar searchBarStyle={['bar', 'input']} iconWidth="2rem" />} />
          <Route path="/search" element={<p>search page</p>} />
        </Routes>
      </MemoryRouter>
    </Provider>,
  );
  return { store, input: screen.getByRole('combobox'), icon: view.container.querySelector('[data-testid="SearchOutlinedIcon"], svg.MuiSvgIcon-root'), ...view };
};

beforeEach(() => {
  vi.clearAllMocks();
  getSearchResults.mockResolvedValue(response([{ PRODUCTID: 'a' }]));
  getFavouriteDataProducts.mockResolvedValue({ data: [{ DATA_PRODUCT_ID: 'a' }] });
  getSearchData.mockResolvedValue({ data: [{ value: 'orders' }, { value: 'order_lines' }] });
});
afterEach(() => vi.restoreAllMocks());

test('typing suggests matches from the catalogue', async () => {
  const { input } = setup();
  fireEvent.change(input, { target: { value: 'ord' } });
  expect(getSearchData).toHaveBeenCalledWith('ord');
  expect(await pick('order_lines')).toHaveTextContent('order_lines');
});

test('Enter records a valid search and opens Explore', async () => {
  const { input, store } = setup();
  type(input, 'orders');
  expect(await screen.findByText('search page')).toBeInTheDocument();
  expect(saveViewedDataProduct).toHaveBeenCalledWith({ productId: 0, productName: null, searchWord: 'orders', category: 'Valid', tab: 'SEARCH' });
  expect(store.getState().search.searchResults).toEqual([{ PRODUCTID: 'a', FAVOURITED: true }]);
  expect(store.getState().search.searchParam).toBe('orders');
});

test('Enter with no matches records an invalid search', async () => {
  getSearchResults.mockResolvedValue(response([]));
  const { input } = setup();
  type(input, 'zzz');
  expect(await screen.findByText('search page')).toBeInTheDocument();
  expect(saveViewedDataProduct).toHaveBeenCalledWith(expect.objectContaining({ searchWord: 'zzz', category: 'Invalid' }));
});

test('choosing a suggestion and clicking search searches it', async () => {
  const { input, icon } = setup();
  fireEvent.change(input, { target: { value: 'ord' } });
  fireEvent.click(await pick('order_lines'));
  fireEvent.click(icon);
  expect(await screen.findByText('search page')).toBeInTheDocument();
  expect(getSearchResults).toHaveBeenCalledWith({ searchParam: 'order_lines' });
  expect(saveViewedDataProduct).toHaveBeenCalledWith(expect.objectContaining({ searchWord: 'order_lines', category: 'Valid' }));
});

test('clicking search with no matches records an invalid search', async () => {
  getSearchResults.mockResolvedValue(response([]));
  const { icon } = setup();
  fireEvent.click(icon);
  expect(await screen.findByText('search page')).toBeInTheDocument();
  expect(saveViewedDataProduct).toHaveBeenCalledWith(expect.objectContaining({ category: 'Invalid' }));
});

test('clearing the box clears the suggestions without a lookup', () => {
  const { input } = setup();
  fireEvent.change(input, { target: { value: '' } });
  expect(getSearchData).not.toHaveBeenCalled();
});

test.each(['enter', 'icon'])('an incomplete response via %s still opens Explore', async (how) => {
  vi.spyOn(console, 'log').mockImplementation(() => {});
  getSearchResults.mockResolvedValue({});
  const { input, icon } = setup();
  if (how === 'enter') fireEvent.keyUp(input, { key: 'Enter', target: { value: 'x' } });
  else fireEvent.click(icon);
  expect(await screen.findByText('search page')).toBeInTheDocument();
  expect(saveViewedDataProduct).not.toHaveBeenCalled();
});

test.each(['enter', 'icon'])('a failed search via %s is logged', async (how) => {
  vi.spyOn(console, 'log').mockImplementation(() => {});
  getSearchResults.mockRejectedValue(new Error('down'));
  const { input, icon } = setup();
  if (how === 'enter') fireEvent.keyUp(input, { key: 'Enter', target: { value: 'x' } });
  else fireEvent.click(icon);
  await waitFor(() => expect(console.log).toHaveBeenCalledWith(expect.any(Error)));
  expect(screen.queryByText('search page')).toBeNull();
});

test.each([
  ['an empty', () => getSearchData.mockResolvedValue(undefined)],
  ['a failed', () => getSearchData.mockRejectedValue(new Error('down'))],
])('%s suggestion lookup shows no suggestions', async (_, arrange) => {
  vi.spyOn(console, 'log').mockImplementation(() => {});
  arrange();
  const { input } = setup();
  fireEvent.change(input, { target: { value: 'ord' } });
  await waitFor(() => expect(console.log).toHaveBeenCalled());
  expect(screen.queryByText('orders')).toBeNull();
});
