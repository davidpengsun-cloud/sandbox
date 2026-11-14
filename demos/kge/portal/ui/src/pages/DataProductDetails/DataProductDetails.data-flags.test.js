import { beforeEach, expect, test, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { Provider } from 'react-redux';
import { configureStore } from '@reduxjs/toolkit';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import userReducer from '../../features/user/userSlice';
import dataProductReducer from '../../features/dataProduct/dataProductSlice';
import searchReducer from '../../features/search/searchSlice';
import DataProductDetails from './DataProductDetails';
import { getApi, postApi } from '../../api/api';
import { getConfig, getFavouriteDataProducts, getMyRequests, removeFavouriteProduct, saveFavouriteProduct } from '../../services/user';
import { getCommunityProducts } from '../../services/search';
import { showToast } from '../../components/shared/V3Toast/V3Toast';

vi.mock('../../api/api', () => ({ getApi: vi.fn(), postApi: vi.fn() }));
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
vi.mock('../../components/DataControls/DataControls', () => ({ default: ({ config }) => <section aria-label="Controls section" data-filters={String(config?.dqFilters)} /> }));
vi.mock('../../components/shared/DataGridTable/DataGridTable', () => ({ default: () => <div>Query results</div> }));

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
  vi.resetAllMocks();
  getMyRequests.mockResolvedValue({ data: [] });
  getApi.mockImplementation(async ({ api }) => {
    if (api === '/product/retail.account') return { data: product };
    if (api === '/api/products/retail.account/fields') return { data: [{ name: 'account_id' }, { name: 'status_code' }] };
    throw new Error(`Unexpected request: ${api}`);
  });
  postApi.mockResolvedValue({ data: { columns: ['account_id'], rows: [{ account_id: 1 }] } });
});

test('both table flags off mount neither data tab and send zero preview or query requests', async () => {
  getConfig.mockResolvedValue({ data: { preview: false, executeQuery: false } });
  mount();
  await screen.findByRole('heading', { name: /Retail accounts/ });
  for (const tab of screen.getAllByRole('tab')) fireEvent.click(tab);
  expect(screen.queryByRole('tab', { name: 'Data preview' })).toBeNull();
  expect(screen.queryByRole('tab', { name: 'Run a query' })).toBeNull();
  expect(screen.queryByLabelText('SQL query')).toBeNull();
  expect(getApi.mock.calls.map(([request]) => request.api)).toEqual(['/product/retail.account']);
  expect(postApi).not.toHaveBeenCalled();
});

test('Run Query with Preview off loads catalogue fields and executes without a preview request', async () => {
  getConfig.mockResolvedValue({ data: { preview: false, executeQuery: true } });
  mount();
  fireEvent.click(await screen.findByRole('tab', { name: 'Run a query' }));
  await waitFor(() => expect(screen.getByLabelText('SQL query')).toHaveValue('SELECT account.account_id, account.status_code FROM account LIMIT 20'));
  fireEvent.click(screen.getByRole('button', { name: 'Run query' }));
  await screen.findByText('Query results');
  expect(getApi.mock.calls.map(([request]) => request.api)).toEqual(['/product/retail.account', '/api/products/retail.account/fields']);
  expect(postApi).toHaveBeenCalledExactlyOnceWith({ api: '/execute-query', params: { unit: 'retail', sql: 'SELECT account.account_id, account.status_code FROM account LIMIT 20' } });
  expect(screen.queryByRole('tab', { name: 'Data preview' })).toBeNull();
});


test.each([false, true, undefined])('the table page passes dqFilters=%s from getConfig to Data quality', async (dqFilters) => {
  getConfig.mockResolvedValue({ data: { dqFilters } });
  mount();
  fireEvent.click(await screen.findByRole('tab', { name: 'Field Data Quality' }));
  await waitFor(() => expect(screen.getByRole('region', { name: 'Controls section' })).toHaveAttribute('data-filters', String(dqFilters)));
});
