import { afterEach, beforeEach, expect, test, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { Provider } from 'react-redux';
import { configureStore } from '@reduxjs/toolkit';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import searchReducer from '../../../features/search/searchSlice';
import dataProductReducer from '../../../features/dataProduct/dataProductSlice';
import StyledProductCard from './StyledProductCard';
import { saveFavouriteProduct, removeFavouriteProduct } from '../../../services/user';

vi.mock('../../../services/user', () => ({ saveFavouriteProduct: vi.fn(), removeFavouriteProduct: vi.fn() }));

const product = {
  PRODUCTID: 'retail.customer', PRODUCTNAME: 'customer', PRODUCTDISPLAYNAME: 'Customer', PRODUCTTYPE: 'Table',
  PRODUCTSOURCE: 'Warehouse', APPLICATIONNAME: 'Core ledger', COMMUNITY: 'Retail', DOMAIN: 'Sales', DESCRIPTION: 'Everyone who bought.',
  LAST_REFRESH: '2026-10-01', SUBSCRIBE_COUNT: 1, FAVOURITED: false, BADGE_NAME: 'SILVER',
};

const setup = (card, results = [card]) => {
  const store = configureStore({ reducer: { search: searchReducer, dataProduct: dataProductReducer }, preloadedState: { search: { ...searchReducer(undefined, { type: 'x' }), searchResults: results } } });
  const view = render(
    <Provider store={store}>
      <MemoryRouter initialEntries={['/search']}>
        <Routes>
          <Route path="/search" element={<StyledProductCard cardData={card} />} />
          <Route path="/productDetails/:id" element={<p>details page</p>} />
        </Routes>
      </MemoryRouter>
    </Provider>,
  );
  return { store, ...view };
};

beforeEach(() => vi.clearAllMocks());
afterEach(() => vi.restoreAllMocks());

test('shows the product summary and opens its details', () => {
  const { store } = setup(product);
  expect(screen.getByText('Customer')).toBeInTheDocument();
  expect(screen.getByText('Warehouse')).toBeInTheDocument();
  expect(screen.getByText('Retail')).toBeInTheDocument();
  expect(screen.getByText('Everyone who bought.')).toBeInTheDocument();
  expect(screen.getByText('1 like')).toBeInTheDocument();
  expect(screen.getByText(/Last Refresh/)).toHaveTextContent('2026-10-01');
  fireEvent.click(screen.getByText('Customer'));
  expect(screen.getByText('details page')).toBeInTheDocument();
  expect(store.getState().dataProduct.selectedProduct).toBe('retail.customer');
  expect(store.getState().dataProduct.selectedProductName).toBe('customer');
});

test.each([
  [null, '0 likes'],
  [3, '3 likes'],
])('like count %s reads %s', (count, text) => {
  setup({ ...product, SUBSCRIBE_COUNT: count });
  expect(screen.getByText(text)).toBeInTheDocument();
});

test('a long description is cut short', () => {
  setup({ ...product, DESCRIPTION: 'x'.repeat(250), PRODUCTTYPE: 'Database View', BADGE_NAME: 'GOLD' });
  expect(screen.getByText(`${'x'.repeat(200)}...`)).toBeInTheDocument();
});

test.each(['EMPTY', 'BRONZE'])('badge %s still shows a trust score', (badge) => {
  setup({ ...product, BADGE_NAME: badge });
  expect(screen.getByAltText('Trust Score')).toBeInTheDocument();
});

test('liking a product saves it and counts the like', async () => {
  saveFavouriteProduct.mockResolvedValue({ status: 'Success' });
  const { store, container } = setup(product);
  fireEvent.click(container.querySelector('.box'));
  expect(saveFavouriteProduct).toHaveBeenCalledWith({ productId: 'retail.customer', applicationId: undefined, technicalName: 'customer' });
  await waitFor(() => expect(store.getState().search.searchResults[0]).toMatchObject({ FAVOURITED: true, SUBSCRIBE_COUNT: 2 }));
});

test('unliking a product removes it', async () => {
  removeFavouriteProduct.mockResolvedValue({ status: 'Success' });
  const liked = { ...product, FAVOURITED: true, SUBSCRIBE_COUNT: 2 };
  const { store, container } = setup(liked, [liked, { PRODUCTID: 'other' }]);
  fireEvent.click(container.querySelector('.box'));
  await waitFor(() => expect(store.getState().search.searchResults[0]).toMatchObject({ FAVOURITED: false, SUBSCRIBE_COUNT: 1 }));
  expect(store.getState().search.searchResults[1]).toEqual({ PRODUCTID: 'other' });
});

test.each([
  ['save', false, { status: 'Fail' }],
  ['remove', true, { status: 'Fail' }],
])('a failed %s leaves the results unchanged', async (kind, fav, res) => {
  vi.spyOn(console, 'log').mockImplementation(() => {});
  const fn = kind === 'save' ? saveFavouriteProduct : removeFavouriteProduct;
  fn.mockResolvedValue(res);
  const card = { ...product, FAVOURITED: fav };
  const { store, container } = setup(card);
  fireEvent.click(container.querySelector('.box'));
  await waitFor(() => expect(console.log).toHaveBeenCalled());
  expect(store.getState().search.searchResults[0]).toEqual(card);
});

test.each(['save', 'remove'])('a rejected %s is logged, not thrown', async (kind) => {
  vi.spyOn(console, 'log').mockImplementation(() => {});
  const fn = kind === 'save' ? saveFavouriteProduct : removeFavouriteProduct;
  fn.mockRejectedValue(new Error('down'));
  const { container } = setup({ ...product, FAVOURITED: kind === 'remove' });
  fireEvent.click(container.querySelector('.box'));
  await waitFor(() => expect(console.log).toHaveBeenCalledWith(expect.any(Error)));
});

test('a column result has no footer and does not navigate', () => {
  const { store } = setup({ ...product, TYPE: 'column' });
  expect(screen.queryByText(/Last Refresh/)).toBeNull();
  fireEvent.click(screen.getByText('Customer'));
  fireEvent.click(screen.getByText('Warehouse'));
  expect(screen.queryByText('details page')).toBeNull();
  expect(store.getState().dataProduct.selectedProduct).toBe(0);
});

test('clicking the product type icon or content opens details', () => {
  const { container } = setup(product);
  fireEvent.click(container.querySelector('.productTypeIcon'));
  expect(screen.getByText('details page')).toBeInTheDocument();
});

test('the content area opens details too', () => {
  setup(product);
  fireEvent.click(screen.getByText('Warehouse'));
  expect(screen.getByText('details page')).toBeInTheDocument();
});

test('without card data a spinner shows', () => {
  setup(undefined, []);
  expect(screen.getByRole('progressbar')).toBeInTheDocument();
});
