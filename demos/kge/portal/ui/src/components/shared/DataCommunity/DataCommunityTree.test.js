import { afterEach, beforeEach, expect, test, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { Provider } from 'react-redux';
import { configureStore } from '@reduxjs/toolkit';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import searchReducer from '../../../features/search/searchSlice';
import dataProductReducer from '../../../features/dataProduct/dataProductSlice';
import DataCommunityTree from './DataCommunityTree';
import { getCommunityProducts } from '../../../services/search';
import { getDataCommunities, getFavouriteDataProducts, saveViewedDataProduct } from '../../../services/user';

vi.mock('../../../services/search', () => ({ getCommunityProducts: vi.fn() }));
vi.mock('../../../services/user', () => ({ getDataCommunities: vi.fn(), getFavouriteDataProducts: vi.fn(), saveViewedDataProduct: vi.fn() }));

const tree = {
  communityList: [{
    id: 'c1', name: 'Retail', val: 'Retail', count: 2,
    children: [{ id: 'd1', kind: 'domain', name: 'Sales', val: 'Retail>Sales', count: 2, children: [{ id: 'p1', kind: 'product', name: 'Orders', val: 'retail.orders', productId: 'retail.orders' }] }],
  }],
  treeExpandList: ['c1', 'd1'],
  totalCount: 2,
};

const none = [];
const setup = (props = {}) => {
  const store = configureStore({ reducer: { search: searchReducer, dataProduct: dataProductReducer } });
  const handlers = { getAllNodes: vi.fn(), getTotalProductCount: vi.fn(), getSelectedNodes: vi.fn() };
  const ui = (p) => (
    <Provider store={store}>
      <MemoryRouter>
        <Routes>
          <Route path="/" element={<DataCommunityTree selectedNodes={none} expanded={false} {...handlers} {...p} />} />
          <Route path="/search" element={<p>search page</p>} />
          <Route path="/productDetails/:id" element={<p>details page</p>} />
        </Routes>
      </MemoryRouter>
    </Provider>
  );
  const view = render(ui(props));
  return { ...view, store, handlers, rerender: (p) => view.rerender(ui(p)) };
};

beforeEach(() => {
  vi.clearAllMocks();
  getDataCommunities.mockResolvedValue({ data: tree });
  getFavouriteDataProducts.mockResolvedValue({ data: [{ DATA_PRODUCT_ID: 'retail.orders' }] });
});
afterEach(() => vi.restoreAllMocks());

test('loads the community tree and reports its nodes and count', async () => {
  const { handlers } = setup();
  expect(await screen.findByText('Retail')).toBeInTheDocument();
  expect(handlers.getAllNodes).toHaveBeenCalledWith(['c1', 'd1']);
  expect(handlers.getTotalProductCount).toHaveBeenCalledWith(2);
});

test('expanding a node reports the selection', async () => {
  const { handlers, container } = setup();
  await screen.findByText('Retail');
  fireEvent.click(container.querySelector('.MuiTreeItem-iconContainer'));
  expect(handlers.getSelectedNodes).toHaveBeenCalledWith(['c1']);
  expect(await screen.findByText('Sales')).toBeInTheDocument();
});

test('expand-all toggles every node open, and the full selection keeps them open', async () => {
  const { rerender } = setup();
  await screen.findByText('Retail');
  rerender({ expanded: true });
  expect(await screen.findByText('Orders')).toBeInTheDocument();
  rerender({ expanded: true, selectedNodes: ['c1', 'd1'] });
  expect(screen.getByText('Orders')).toBeInTheDocument();
});

test('a community click searches it and opens Explore', async () => {
  getCommunityProducts.mockResolvedValue({ data: { searchList: [{ PRODUCTID: 'retail.orders' }, { PRODUCTID: 'x' }], domainFilter: ['Sales'], communityFilter: ['Retail'], sourceFilter: ['W'], typeFilter: ['Table'], hierarchy: ['h'] } });
  const { store } = setup();
  fireEvent.click(await screen.findByText('Retail'));
  expect(await screen.findByText('search page')).toBeInTheDocument();
  expect(getCommunityProducts).toHaveBeenCalledWith({ searchParam: 'Retail', isDomain: false });
  expect(saveViewedDataProduct).toHaveBeenCalledWith(expect.objectContaining({ searchWord: 'Retail', category: 'Valid' }));
  const s = store.getState().search;
  expect(s.searchParam).toBe('Retail');
  expect(s.domainSelected).toBe(false);
  expect(s.searchResults).toEqual([{ PRODUCTID: 'retail.orders', FAVOURITED: true }, { PRODUCTID: 'x', FAVOURITED: false }]);
  expect(s.domainFilter).toEqual([{ value: 'Sales', label: 'Sales', parent: 'DOMAIN' }]);
  expect(s.hierarchyList).toEqual(['h']);
});

test('a domain click with no results records an invalid search', async () => {
  getCommunityProducts.mockResolvedValue({ data: { searchList: [], domainFilter: [], communityFilter: [], sourceFilter: [], typeFilter: [], hierarchy: [] } });
  const { store, rerender } = setup();
  await screen.findByText('Retail');
  rerender({ expanded: true });
  fireEvent.click(await screen.findByText('Sales'));
  expect(await screen.findByText('search page')).toBeInTheDocument();
  expect(getCommunityProducts).toHaveBeenCalledWith({ searchParam: 'Retail>Sales', isDomain: true });
  expect(saveViewedDataProduct).toHaveBeenCalledWith(expect.objectContaining({ category: 'Invalid' }));
  expect(store.getState().search.searchParam).toBe('Sales');
  expect(store.getState().search.domainSelected).toBe(true);
});

test('a product click opens its details', async () => {
  const { store, rerender } = setup();
  await screen.findByText('Retail');
  rerender({ expanded: true });
  fireEvent.click(await screen.findByText('Orders'));
  expect(screen.getByText('details page')).toBeInTheDocument();
  expect(store.getState().dataProduct.selectedProduct).toBe('retail.orders');
  expect(store.getState().dataProduct.selectedProductName).toBe('Orders');
});

test('an incomplete search response still opens Explore', async () => {
  vi.spyOn(console, 'log').mockImplementation(() => {});
  getCommunityProducts.mockResolvedValue({});
  setup();
  fireEvent.click(await screen.findByText('Retail'));
  expect(await screen.findByText('search page')).toBeInTheDocument();
  expect(saveViewedDataProduct).not.toHaveBeenCalled();
});

test('a failed search is logged and stays put', async () => {
  vi.spyOn(console, 'log').mockImplementation(() => {});
  getCommunityProducts.mockRejectedValue(new Error('down'));
  setup();
  fireEvent.click(await screen.findByText('Retail'));
  await waitFor(() => expect(console.log).toHaveBeenCalledWith(expect.any(Error)));
  expect(screen.queryByText('search page')).toBeNull();
});

test.each([
  ['an empty response', () => getDataCommunities.mockResolvedValue(undefined)],
  ['a failure', () => getDataCommunities.mockRejectedValue(new Error('down'))],
])('the tree stays empty on %s', async (_, arrange) => {
  vi.spyOn(console, 'log').mockImplementation(() => {});
  arrange();
  const { handlers } = setup();
  await waitFor(() => expect(console.log).toHaveBeenCalled());
  expect(screen.queryByText('Retail')).toBeNull();
  expect(handlers.getAllNodes).not.toHaveBeenCalled();
});
