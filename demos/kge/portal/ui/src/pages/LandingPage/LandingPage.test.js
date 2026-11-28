import { beforeEach, expect, test, vi } from 'vitest';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { Provider } from 'react-redux';
import { configureStore } from '@reduxjs/toolkit';
import { MemoryRouter, Route, Routes, useLocation, useParams } from 'react-router-dom';
import userReducer from '../../features/user/userSlice';
import searchReducer from '../../features/search/searchSlice';
import dataProductReducer from '../../features/dataProduct/dataProductSlice';
import LandingPage from './LandingPage';
import {
  getConfig, getDataCommunities, getUseCases, getFavouriteDataProducts, getRelatedDataProducts, getViewedDataProducts, saveViewedDataProduct,
} from '../../services/user';
import { getCommunityProducts, getSearchResults } from '../../services/search';
import { exportUseCaseOdcs } from '../../services/exports';

vi.mock('../../services/user', () => ({
  getFavouriteDataProducts: vi.fn(), getRelatedDataProducts: vi.fn(), getViewedDataProducts: vi.fn(),
  getConfig: vi.fn(), getDataCommunities: vi.fn(), getUseCases: vi.fn(), saveViewedDataProduct: vi.fn(),
}));
vi.mock('../../services/search', () => ({ getSearchResults: vi.fn(), getCommunityProducts: vi.fn(), getSearchData: vi.fn() }));
vi.mock('../../services/exports', () => ({ exportUseCaseOdcs: vi.fn(() => Promise.resolve()) }));
vi.mock('../../components/shared/Header/Header', () => ({
  default: () => null,
}));
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
const useCases = {
  useCaseList: [
    {
      id: 'uc-covenant', title: 'Covenant monitoring', objective: 'Credit checks covenants each quarter.', domain: 'Credit', owner: 'Credit risk', url: '',
      kpi: { name: 'Breaches', definition: 'Covenants breached in the quarter.', grain: 'Quarter', unit: 'count' },
      example_questions: ['Which covenants were breached last quarter?', 'Which borrowers are close to a breach?'],
      products: [{ id: 'corp-1', name: 'Corporate loans' }, { id: 'sme-1', name: 'SME loans' }], count: 2,
    },
    {
      id: 'uc-revenue', title: 'Month-end revenue review', objective: 'Finance compares revenue with the plan.', domain: 'Finance', owner: '', url: '', kpi: null,
      example_questions: [], products: [{ id: 'retail-1', name: 'Retail accounts' }, { id: 'sme-1', name: 'SME loans' }], count: 2,
    },
    { id: 'uc-branch', title: '<b>Branch & pack</b>', objective: '<i>Shown as text</i>', domain: '', owner: '', url: '', kpi: null, example_questions: [], products: [{ id: 'retail/1', name: 'Retail one' }], count: 1 },
  ],
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
  getUseCases.mockResolvedValue({ data: useCases });
  getSearchResults.mockResolvedValue(searchResponse);
  getCommunityProducts.mockResolvedValue(searchResponse);
});

test('shows a loading skeleton, then the communities with their product counts', async () => {
  renderPage();
  expect(screen.getByLabelText('Loading ALTA')).toHaveAttribute('aria-busy', 'true');
  expect(await screen.findByRole('heading', { name: 'Browse by community' })).toBeInTheDocument();
  expect(screen.queryByLabelText('Loading ALTA')).toBeNull();
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
  expect(screen.getByRole('heading', { level: 1, name: 'ALTA: AI-powered Library for Trusted Assets' })).toBeInTheDocument();
  expect(screen.queryByText('Governed catalog')).toBeNull();
  expect(await screen.findByText('Nothing saved yet')).toBeInTheDocument();
  expect(screen.queryByRole('heading', { name: 'Browse by community' })).toBeNull();
  expect(screen.queryByRole('heading', { name: 'Most viewed' })).toBeNull();
  expect(screen.getAllByText('—').length).toBeGreaterThan(0);
});

test('the hero shows the portal title and configured description', async () => {
  getConfig.mockResolvedValue({ data: { Application_Title: 'Meridian Discovery', Application_Desc: 'Find Meridian data.', Community_Title: 'Meridian catalog' } });
  renderPage();
  const title = await screen.findByRole('heading', { level: 1, name: 'ALTA: AI-powered Library for Trusted Assets' });
  expect(title.closest('.hero')).toBeInTheDocument();
  expect(screen.queryByText('Meridian Discovery')).toBeNull();
  expect(await screen.findByText('Find Meridian data.')).toBeInTheDocument();
  expect(screen.queryByText('Meridian catalog')).toBeNull();
  expect(document.title).not.toBe('Meridian Discovery');
});

test('the home-band search submits the term to Explore', async () => {
  renderPage();
  const box = await screen.findByRole('searchbox', { name: 'Search the portal' });
  fireEvent.change(box, { target: { value: ' orders ' } });
  fireEvent.keyDown(box, { key: 'Enter' });
  expect(await screen.findByText('Explore page')).toBeInTheDocument();
  expect(getSearchResults).not.toHaveBeenCalled();
  expect(getFavouriteDataProducts).toHaveBeenCalledTimes(1);
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
  expect(getSearchResults).not.toHaveBeenCalled();
  expect(getFavouriteDataProducts).toHaveBeenCalledTimes(1);
  expect(new URLSearchParams(screen.getByTestId('explore-search').textContent).get('q')).toBeNull();
});

test('Explore all from Most viewed searches everything, even when the answer is empty', async () => {
  getViewedDataProducts.mockResolvedValue({ data: [{ PRODUCTID: 'v1', PRODUCTNAME: 'Viewed' }] });
  getSearchResults.mockResolvedValue({});
  const store = renderPage();
  fireEvent.click(await screen.findByText('Explore all →'));
  expect(await screen.findByText('Explore page')).toBeInTheDocument();
  expect(store.getState().search.searchResults).toEqual([]);
});

test('a search opens Explore immediately without making a request on the home page', async () => {
  getSearchResults.mockRejectedValue(new Error('down'));
  renderPage();
  fireEvent.click(await screen.findByText('See all →'));
  expect(await screen.findByText('Explore page')).toBeInTheDocument();
  expect(getSearchResults).not.toHaveBeenCalled();
  expect(getFavouriteDataProducts).toHaveBeenCalledTimes(1);
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
  for (const f of [getFavouriteDataProducts, getRelatedDataProducts, getViewedDataProducts, getConfig, getDataCommunities, getUseCases]) {
    f.mockRejectedValue(new Error('down'));
  }
  renderPage();
  expect(await screen.findByText('Nothing saved yet')).toBeInTheDocument();
  expect(screen.getByRole('heading', { level: 1, name: 'ALTA: AI-powered Library for Trusted Assets' })).toBeInTheDocument();
  log.mockRestore();
});


test.each([undefined, ''])('Discover uses the default subtitle when config description is %s', async (description) => {
  getConfig.mockResolvedValue({ data: { Application_Desc: description } });
  renderPage();
  expect(await screen.findByText('Search curated data assets, see how fresh and trustworthy each one is, and request access from the people who own it.')).toBeInTheDocument();
});

const useCaseSection = () => screen.getByRole('region', { name: 'Browse by use case' });
const useCaseCards = () => [...useCaseSection().querySelectorAll('.domain')];
const communityCard = () => screen.getByRole('button', { name: /^Community\s*Retail/ });
const openButton = (title) => screen.getByRole('button', { name: `Show assets for use case ${title}` });

test('the use case section sits directly above the communities, one community-style card per use case in the order given', async () => {
  renderPage();
  const heading = await screen.findByRole('heading', { level: 2, name: 'Browse by use case' });
  const section = heading.closest('section');
  expect(section.nextElementSibling.querySelector('h2')).toHaveTextContent('Browse by community');
  expect(section.querySelector(':scope > .domains')).not.toBeNull();
  expect(communityCard().parentElement).toHaveClass('domains');
  const cards = useCaseCards();
  expect(cards.map((card) => card.getAttribute('aria-label'))).toEqual(['Covenant monitoring', 'Month-end revenue review', '<b>Branch & pack</b>']);
  expect(section.querySelector('.mp-card')).toBeNull();
  expect(getUseCases).toHaveBeenCalledTimes(1);
});

test('a use case card has the community card\'s parts, classes and order: eyebrow, title with →, Export as ODCS on the domain line, the count with Explore, and the bar', async () => {
  renderPage();
  await screen.findByRole('heading', { name: 'Browse by use case' });
  const [card] = useCaseCards();
  const community = communityCard();
  expect(card).toHaveClass('domain');
  expect(card).toHaveAttribute('role', 'group');
  const parts = (root) => [...root.querySelectorAll('.domain-eyebrow, b, .domain-desc, .domain-foot, .meter')].map((part) => part.classList[0] || part.tagName);
  expect(parts(card)).toEqual(parts(community));
  expect(card.querySelector('.domain-eyebrow')).toHaveTextContent(/^Use case$/);
  expect(card.querySelector('b')).toHaveTextContent('Covenant monitoring→');
  expect(card.querySelector('b > .go')).toHaveAttribute('aria-hidden', 'true');
  const exportLine = card.querySelector('.domain-desc');
  expect(exportLine.tagName).toBe('BUTTON');
  expect(exportLine).toHaveTextContent(/^⤓ Export as ODCS$/);
  expect(exportLine).toHaveAccessibleName('Export as ODCS: Covenant monitoring');
  expect(card.querySelector('.domain-foot .domain-num')).toHaveTextContent(/^2$/);
  expect(openButton('Covenant monitoring')).toHaveAccessibleDescription('2 ASSETS');
  expect(card.querySelector('.domain-foot .domain-unit')).toHaveTextContent(/^ASSETS$/);
  const explore = within(card.querySelector('.domain-foot')).getByRole('button', { name: 'Explore use case Covenant monitoring' });
  expect(explore).toHaveTextContent(/^Explore$/);
  expect(explore.previousElementSibling).toHaveClass('domain-unit');
  expect(card.lastElementChild).toHaveClass('meter');
  expect(card.querySelector('.meter > i')).toHaveStyle({ width: '100%' });
  expect(useCaseCards()[2].querySelector('.meter > i')).toHaveStyle({ width: '50%' });
  expect(useCaseCards()[2].querySelector('.domain-unit')).toHaveTextContent(/^ASSET$/);
});

test('the card is lean: no objective, KPI, domain or owner, and every value is text', async () => {
  renderPage();
  await screen.findByRole('heading', { name: 'Browse by use case' });
  const [card] = useCaseCards();
  for (const text of ['Credit checks covenants each quarter.', 'Breaches', 'Credit', 'Credit risk']) {
    expect(within(card).queryByText(text)).toBeNull();
  }
  expect([...useCaseSection().querySelectorAll('i, u, s, img')].every((node) => node.tagName === 'I' && node.parentElement.classList.contains('meter'))).toBe(true);
  expect(useCaseSection().querySelectorAll('b')).toHaveLength(3);
  expect(useCaseCards()[2].querySelector('b')).toHaveTextContent('<b>Branch & pack</b>');
});

test('no button sits inside another, and the card\'s three controls are each keyboard-reachable buttons', async () => {
  renderPage();
  await screen.findByRole('heading', { name: 'Browse by use case' });
  for (const card of useCaseCards()) {
    const buttons = within(card).getAllByRole('button');
    expect(buttons.map((button) => button.getAttribute('aria-label').replace(/ (Covenant monitoring|Month-end revenue review|<b>Branch & pack<\/b>)$/, ''))).toEqual(['Show assets for use case', 'Export as ODCS:', 'Explore use case']);
    expect(buttons.every((button) => button.tagName === 'BUTTON' && button.getAttribute('type') === 'button' && !button.hasAttribute('tabindex'))).toBe(true);
    expect(buttons.every((button) => !button.parentElement.closest('button'))).toBe(true);
  }
});

test('the card\'s styles are the community card\'s, with the main button stretched over it and the two controls above it', () => {
  const css = readFileSync(join(process.cwd(), 'src/assets/styles/v3-home.css'), 'utf8').replace(/\s+/g, ' ');
  const rule = (selector) => css.match(new RegExp(`(?:^|\\} )${selector.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}\\{([^}]*)\\}`))?.[1] || '';
  expect(rule('.v3-home .uc-domain-open::after')).toMatch(/position:absolute; inset:0/);
  expect(rule('.v3-home .uc-domain-export,.v3-home .uc-domain-explore')).toMatch(/position:relative; z-index:1/);
  expect(rule('.v3-home .uc-domain-explore')).toMatch(/margin-left:auto/);
  expect(rule('.v3-home .uc-domain-export,.v3-home .uc-domain-explore')).toMatch(/min-height:24px; padding:4px 8px; margin:-4px -8px/);
  expect(rule('.v3-home .uc-domain-open::after')).toMatch(/border-radius:var\(--v3-r-md\)/);
  expect(rule('.v3-home .uc-domain-open:focus-visible::after')).toMatch(/outline:2px solid var\(--v3-focus\)/);
  expect(rule('.v3-home .uc-domain-export:focus-visible,.v3-home .uc-domain-explore:focus-visible')).toMatch(/outline:2px solid var\(--v3-focus\)/);
});

test.each([
  ['no use case', { data: { useCaseList: [] } }],
  ['an empty answer', { data: {} }],
  ['no answer', undefined],
  ['only entries without readable products or a title', { data: { useCaseList: [{ id: 'uc-x', title: 'Nothing visible', count: 0, products: [] }, { id: 'uc-y', title: '', count: 1, products: [{ id: 'x' }] }, { count: 2 }] } }],
])('the use case section is hidden when there is %s', async (_label, answer) => {
  getUseCases.mockResolvedValue(answer);
  renderPage();
  expect(await screen.findByRole('heading', { name: 'Browse by community' })).toBeInTheDocument();
  expect(screen.queryByRole('heading', { name: 'Browse by use case' })).toBeNull();
});

test('the use case section is hidden when its feed fails, and the communities still show', async () => {
  const log = vi.spyOn(console, 'log').mockImplementation(() => {});
  getUseCases.mockRejectedValue(new Error('down'));
  renderPage();
  expect(await screen.findByRole('heading', { name: 'Browse by community' })).toBeInTheDocument();
  expect(screen.queryByRole('heading', { name: 'Browse by use case' })).toBeNull();
  log.mockRestore();
});

test('the page waits for the use cases before leaving its loading state', async () => {
  let answer;
  getUseCases.mockReturnValue(new Promise((resolve) => { answer = resolve; }));
  renderPage();
  await waitFor(() => expect(getDataCommunities).toHaveBeenCalled());
  await Promise.resolve();
  expect(screen.getByLabelText('Loading ALTA')).toBeInTheDocument();
  answer({ data: useCases });
  expect(await screen.findByRole('heading', { name: 'Browse by use case' })).toBeInTheDocument();
});

test('a click on the card opens Discover\'s search filtered to the use case, with no dialog and no download', async () => {
  renderPage();
  await screen.findByRole('heading', { name: 'Browse by use case' });
  fireEvent.click(openButton('<b>Branch & pack</b>'));
  expect(screen.queryByRole('dialog')).toBeNull();
  expect(await screen.findByText('Explore page')).toBeInTheDocument();
  expect(new URLSearchParams(screen.getByTestId('explore-search').textContent).get('useCase')).toBe('<b>Branch & pack</b>');
  expect(exportUseCaseOdcs).not.toHaveBeenCalled();
});

test('"Export as ODCS" downloads the use case\'s contract and does not open search or the modal', async () => {
  renderPage();
  await screen.findByRole('heading', { name: 'Browse by use case' });
  fireEvent.click(screen.getByRole('button', { name: 'Export as ODCS: Covenant monitoring' }));
  expect(exportUseCaseOdcs).toHaveBeenCalledTimes(1);
  expect(exportUseCaseOdcs.mock.calls[0][0]).toMatchObject({ id: 'uc-covenant', title: 'Covenant monitoring' });
  expect(screen.queryByText('Explore page')).toBeNull();
  expect(screen.queryByRole('dialog')).toBeNull();
});

test('"Explore" opens the use case\'s detail modal, not the search; Close returns focus to Explore', async () => {
  renderPage();
  await screen.findByRole('heading', { name: 'Browse by use case' });
  const explore = screen.getByRole('button', { name: 'Explore use case Covenant monitoring' });
  explore.focus();
  fireEvent.click(explore);
  const dialog = screen.getByRole('dialog', { name: 'Covenant monitoring' });
  expect(dialog).toHaveFocus();
  expect(within(dialog).getByText('Credit checks covenants each quarter.')).toBeInTheDocument();
  expect(within(dialog).getByText('Credit risk')).toBeInTheDocument();
  expect(within(dialog).getByText('Breaches')).toBeInTheDocument();
  expect(within(within(dialog).getByRole('list', { name: 'Example questions' })).getAllByRole('listitem').map((item) => item.textContent)).toEqual(useCases.useCaseList[0].example_questions);
  expect(within(within(dialog).getByRole('list', { name: 'Linked data products' })).getAllByRole('listitem').map((item) => item.querySelector('.uc-product-name').textContent)).toEqual(['Corporate loans', 'SME loans']);
  expect(screen.queryByText('Explore page')).toBeNull();
  expect(exportUseCaseOdcs).not.toHaveBeenCalled();
  fireEvent.click(within(dialog).getByRole('button', { name: 'Close' }));
  expect(screen.queryByRole('dialog')).toBeNull();
  expect(explore).toHaveFocus();
});

test('the modal\'s "Show assets in Discover" opens the same filtered search, and its "Export as ODCS" downloads the contract', async () => {
  renderPage();
  await screen.findByRole('heading', { name: 'Browse by use case' });
  fireEvent.click(screen.getByRole('button', { name: 'Explore use case Month-end revenue review' }));
  const dialog = screen.getByRole('dialog', { name: 'Month-end revenue review' });
  fireEvent.click(within(dialog).getByRole('button', { name: 'Export as ODCS' }));
  expect(exportUseCaseOdcs.mock.calls[0][0]).toMatchObject({ id: 'uc-revenue', title: 'Month-end revenue review' });
  expect(screen.getByRole('dialog')).toBeInTheDocument();
  fireEvent.click(within(dialog).getByRole('link', { name: 'Show assets in Discover' }));
  expect(await screen.findByText('Explore page')).toBeInTheDocument();
  expect(new URLSearchParams(screen.getByTestId('explore-search').textContent).get('useCase')).toBe('Month-end revenue review');
  expect(screen.queryByRole('dialog')).toBeNull();
});
