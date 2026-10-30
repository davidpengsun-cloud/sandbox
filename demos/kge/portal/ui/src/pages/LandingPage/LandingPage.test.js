import { beforeEach, expect, test, vi } from 'vitest';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { Provider } from 'react-redux';
import { configureStore } from '@reduxjs/toolkit';
import { MemoryRouter, Route, Routes, useLocation, useParams } from 'react-router-dom';
import userReducer from '../../features/user/userSlice';
import searchReducer from '../../features/search/searchSlice';
import dataProductReducer from '../../features/dataProduct/dataProductSlice';
import LandingPage from './LandingPage';
import {
  getConfig, getDataCommunities, getFavouriteDataProducts, getRelatedDataProducts, getViewedDataProducts, saveViewedDataProduct,
} from '../../services/user';
import { getCommunityProducts, getSearchResults } from '../../services/search';

vi.mock('../../services/user', () => ({
  getFavouriteDataProducts: vi.fn(), getRelatedDataProducts: vi.fn(), getViewedDataProducts: vi.fn(),
  getConfig: vi.fn(), getDataCommunities: vi.fn(), saveViewedDataProduct: vi.fn(),
}));
vi.mock('../../services/search', () => ({ getSearchResults: vi.fn(), getCommunityProducts: vi.fn(), getSearchData: vi.fn() }));
vi.mock('../../components/shared/Header/Header', () => ({ default: () => null }));
vi.mock('../../components/shared/V3ProductCard/V3ProductCard', () => ({
  default: ({ product }) => <article aria-label={product.PRODUCTDISPLAYNAME}>{product.PRODUCTTYPE} · {product.COMMUNITY}</article>,
  titleCaseName: (s) => String(s || ''),
}));

const communities = {
  communityList: [
    { id: 'retail', kind: 'community', name: 'Retail', val: 'retail', domain: 'Retail Banking', desc: 'Retail Banking', count: 2, children: [{ kind: 'product', name: 'Customer' }, { kind: 'product', name: 'Orders' }] },
    { id: 'fund', kind: 'community', name: 'Fund', val: 'fund', domain: 'Investments', count: 1, children: [{ kind: 'product', name: 'Holdings' }] },
    { id: 'risk', kind: 'community', name: 'Risk', val: 'risk', domain: 'Risk', count: 0, children: [] },
  ],
  totalCount: 3,
};
const searchResponse = {
  data: { searchList: [{ PRODUCTID: 'p1' }], domainFilter: ['Retail'], communityFilter: [], sourceFilter: [], typeFilter: [], hierarchy: [] },
};

function DetailsPage() {
  return <p>Details of {useParams().id}</p>;
}

function ExplorePage() {
  return <><p>Explore page</p><output data-testid="explore-search">{useLocation().search}</output></>;
}

function renderPage() {
  const store = configureStore({ reducer: { user: userReducer, search: searchReducer, dataProduct: dataProductReducer } });
  render(
    <Provider store={store}>
      <MemoryRouter initialEntries={['/home']}>
        <Routes>
          <Route path="/home" element={<LandingPage />} />
          <Route path="/search" element={<ExplorePage />} />
          <Route path="/productDetails/:id" element={<DetailsPage />} />
        </Routes>
      </MemoryRouter>
    </Provider>,
  );
  return store;
}

beforeEach(() => {
  vi.clearAllMocks();
  getFavouriteDataProducts.mockResolvedValue({ data: [] });
  getRelatedDataProducts.mockResolvedValue({ data: [] });
  getViewedDataProducts.mockResolvedValue({ data: [] });
  getConfig.mockResolvedValue({ data: {} });
  getDataCommunities.mockResolvedValue({ data: communities });
  getSearchResults.mockResolvedValue(searchResponse);
  getCommunityProducts.mockResolvedValue(searchResponse);
});

test('shows a loading skeleton, then the communities with their product counts', async () => {
  renderPage();
  expect(screen.getByLabelText('Loading portal')).toHaveAttribute('aria-busy', 'true');
  expect(await screen.findByRole('heading', { name: 'Browse by community' })).toBeInTheDocument();
  expect(screen.queryByLabelText('Loading portal')).toBeNull();
  const retail = screen.getByRole('button', { name: /^Community\s*Retail/ });
  expect(within(retail).getByText('Retail Banking')).toBeInTheDocument();
  expect(within(retail).getByText('2')).toBeInTheDocument();
  expect(within(retail).getByText('ASSETS')).toBeInTheDocument();
  const fund = screen.getByRole('button', { name: /^Community\s*Fund/ });
  expect(within(fund).getByText('1')).toBeInTheDocument();
  expect(within(fund).getByText('ASSET')).toBeInTheDocument();
  // The hero search is the only search box; community cards are listed once below it.
  expect(screen.getAllByRole('searchbox', { name: 'Search the portal' })).toHaveLength(1);
  expect(screen.getByRole('searchbox', { name: 'Search the portal' }).closest('.hero-search')).toBeInTheDocument();
  expect(screen.getByRole('button', { name: 'Filter by domain Retail Banking' })).toBeInTheDocument();
  expect(screen.getByRole('group', { name: 'Filter by domain' }).parentElement).toHaveClass('hero');
  expect(screen.queryByRole('button', { name: 'RETAIL' })).toBeNull();
  expect(screen.getAllByText('3').length).toBeGreaterThan(0);
  expect(screen.getByText('ACROSS 3 COMMUNITIES')).toBeInTheDocument();
});

test('the default title, description and empty saved list', async () => {
  getDataCommunities.mockResolvedValue({ data: {} });
  renderPage();
  expect(screen.getByRole('heading', { level: 1, name: 'Data Discovery Portal' })).toBeInTheDocument();
  expect(screen.getByText('Governed catalog')).toBeInTheDocument();
  expect(await screen.findByText('Nothing saved yet')).toBeInTheDocument();
  expect(screen.queryByRole('heading', { name: 'Browse by community' })).toBeNull();
  expect(screen.queryByRole('heading', { name: 'Most viewed' })).toBeNull();
  expect(screen.getAllByText('—').length).toBeGreaterThan(0);
});

test('the hero shows the portal title and configured description', async () => {
  getConfig.mockResolvedValue({ data: { Application_Title: 'Meridian Discovery', Application_Desc: 'Find Meridian data.', Community_Title: 'Meridian catalog' } });
  renderPage();
  const title = await screen.findByRole('heading', { level: 1, name: 'Data Discovery Portal' });
  expect(title.closest('.hero')).toBeInTheDocument();
  expect(screen.queryByText('Meridian Discovery')).toBeNull();
  expect(await screen.findByText('Find Meridian data.')).toBeInTheDocument();
  expect(screen.getByText('Meridian catalog')).toBeInTheDocument();
  expect(document.title).not.toBe('Meridian Discovery');
});

test('the home-band search submits the term to Explore', async () => {
  renderPage();
  const box = await screen.findByRole('searchbox', { name: 'Search the portal' });
  fireEvent.change(box, { target: { value: ' orders ' } });
  fireEvent.keyDown(box, { key: 'Enter' });
  expect(await screen.findByText('Explore page')).toBeInTheDocument();
  expect(getSearchResults).toHaveBeenCalledWith({ searchParam: 'orders' });
  expect(new URLSearchParams(screen.getByTestId('explore-search').textContent).get('q')).toBe('orders');
});

test('a domain quick filter opens Explore with that domain selected', async () => {
  renderPage();
  fireEvent.click(await screen.findByRole('button', { name: 'Filter by domain Retail Banking' }));
  expect(await screen.findByText('Explore page')).toBeInTheDocument();
  expect(new URLSearchParams(screen.getByTestId('explore-search').textContent).getAll('f')).toEqual(['DOMAIN:Retail Banking']);
});

test('a domain quick filter combines with the typed search term', async () => {
  renderPage();
  const box = await screen.findByRole('searchbox', { name: 'Search the portal' });
  fireEvent.change(box, { target: { value: ' balance ' } });
  fireEvent.click(await screen.findByRole('button', { name: 'Filter by domain Retail Banking' }));
  expect(await screen.findByText('Explore page')).toBeInTheDocument();
  const params = new URLSearchParams(screen.getByTestId('explore-search').textContent);
  expect(params.get('q')).toBe('balance');
  expect(params.getAll('f')).toEqual(['DOMAIN:Retail Banking']);
});

test('most viewed, related and saved products are listed', async () => {
  getViewedDataProducts.mockResolvedValue({ data: [{ DATA_PRODUCT_ID: 'v1', PRODUCT_NAME: 'Viewed One', DATA_PRODUCT_TYPE: 'Table', BUSINESS_APPLICATION_NAME: 'Retail' }] });
  getRelatedDataProducts.mockResolvedValue({ data: { searchList: [{ PRODUCTID: 'r1', PRODUCTDISPLAYNAME: 'Related One', PRODUCTTYPE: 'View' }] } });
  getFavouriteDataProducts.mockResolvedValue({ data: [
    { PRODUCT_ID: 's1', PRODUCT_DISPLAY_NAME: 'Saved Gold', BADGE_NAME: 'gold', DOMAIN: 'Retail' },
    { TECHNICALNAME: 'saved_plain' },
  ] });
  renderPage();
  expect(await screen.findByRole('heading', { name: 'Most viewed' })).toBeInTheDocument();
  expect(screen.getByRole('article', { name: 'Viewed One' })).toHaveTextContent('Table · Retail');
  expect(screen.getByRole('heading', { name: 'Related to you' })).toBeInTheDocument();
  expect(screen.getByRole('article', { name: 'Related One' })).toHaveTextContent('View');
  expect(screen.getByAltText('gold tier')).toBeInTheDocument();
  expect(screen.getByText('saved_plain')).toBeInTheDocument();
  expect(screen.queryByText('Nothing saved yet')).toBeNull();
  // a saved product without an id does not navigate
  fireEvent.click(screen.getByText('saved_plain'));
  expect(screen.queryByText(/Details of/)).toBeNull();
  fireEvent.click(screen.getByText('Saved Gold'));
  expect(await screen.findByText('Details of s1')).toBeInTheDocument();
});

test('See all searches everything', async () => {
  renderPage();
  fireEvent.click(await screen.findByText('See all →'));
  expect(await screen.findByText('Explore page')).toBeInTheDocument();
  expect(getSearchResults).toHaveBeenCalledWith({ searchParam: null });
});

test('Explore all from Most viewed searches everything, even when the answer is empty', async () => {
  getViewedDataProducts.mockResolvedValue({ data: [{ PRODUCTID: 'v1', PRODUCTNAME: 'Viewed' }] });
  getSearchResults.mockResolvedValue({});
  const store = renderPage();
  fireEvent.click(await screen.findByText('Explore all →'));
  expect(await screen.findByText('Explore page')).toBeInTheDocument();
  expect(store.getState().search.searchResults).toEqual([]);
});

test('a failed search stays on the home page', async () => {
  getSearchResults.mockRejectedValue(new Error('down'));
  renderPage();
  fireEvent.click(await screen.findByText('See all →'));
  await waitFor(() => expect(getSearchResults).toHaveBeenCalled());
  expect(screen.queryByText('Explore page')).toBeNull();
});

test('opening a community searches its community within its domain and records the view', async () => {
  const store = renderPage();
  fireEvent.click(await screen.findByRole('button', { name: /^Community\s*Retail/ }));
  expect(await screen.findByText('Explore page')).toBeInTheDocument();
  expect(getCommunityProducts).toHaveBeenCalledWith({ searchParam: 'retail', isDomain: false, domain: 'Retail Banking' });
  expect(saveViewedDataProduct).toHaveBeenCalledWith(expect.objectContaining({ searchWord: 'retail', tab: 'SEARCH' }));
  expect(store.getState().search.searchParam).toBe('retail');
  expect(store.getState().search.domainSelected).toBe(false);
});

test('opening a community card searches its value', async () => {
  const store = renderPage();
  fireEvent.click(await screen.findByRole('button', { name: /^Community\s*Fund/ }));
  expect(await screen.findByText('Explore page')).toBeInTheDocument();
  expect(getCommunityProducts).toHaveBeenCalledWith({ searchParam: 'fund', isDomain: false, domain: 'Investments' });
  expect(store.getState().search.searchParam).toBe('fund');
});

test('a community answer without data still opens Explore without recording a view', async () => {
  getCommunityProducts.mockResolvedValue({});
  renderPage();
  fireEvent.click(await screen.findByRole('button', { name: /^Community\s*Risk/ }));
  expect(await screen.findByText('Explore page')).toBeInTheDocument();
  expect(saveViewedDataProduct).not.toHaveBeenCalled();
});

test('a failed community search stays on the home page', async () => {
  getCommunityProducts.mockRejectedValue(new Error('down'));
  renderPage();
  fireEvent.click(await screen.findByRole('button', { name: /^Community\s*Risk/ }));
  await waitFor(() => expect(getCommunityProducts).toHaveBeenCalled());
  expect(screen.queryByText('Explore page')).toBeNull();
});

test('the page still renders when every feed fails', async () => {
  const log = vi.spyOn(console, 'log').mockImplementation(() => {});
  for (const f of [getFavouriteDataProducts, getRelatedDataProducts, getViewedDataProducts, getConfig, getDataCommunities]) {
    f.mockRejectedValue(new Error('down'));
  }
  renderPage();
  expect(await screen.findByText('Nothing saved yet')).toBeInTheDocument();
  expect(screen.getByRole('heading', { level: 1, name: 'Data Discovery Portal' })).toBeInTheDocument();
  log.mockRestore();
});
