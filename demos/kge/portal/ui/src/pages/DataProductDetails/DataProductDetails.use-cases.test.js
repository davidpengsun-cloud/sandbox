import { beforeEach, expect, test, vi } from 'vitest';
import { fireEvent, render, screen, within } from '@testing-library/react';
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom';
import DataProductDetails from './DataProductDetails';
import V3Card from '../../components/shared/V3ProductCard/V3Card';
import { getProduct } from '../../services/metadata';
import { getConfig, getMyRequests, getUseCases } from '../../services/user';
import { exportAssetOdps, exportUseCaseOdcs } from '../../services/exports';
import { showToast } from '../../components/shared/V3Toast/V3Toast';
vi.mock('../../services/metadata', () => ({ getProduct: vi.fn(), submitFeedback: vi.fn() }));
vi.mock('../../services/exports', () => ({ exportAssetOdps: vi.fn(() => Promise.resolve()), exportUseCaseOdcs: vi.fn(() => Promise.resolve()) }));
vi.mock('../../services/user', () => ({ getConfig: vi.fn(), getMyRequests: vi.fn(), requestAccess: vi.fn(), removeFavouriteProduct: vi.fn(), saveFavouriteProduct: vi.fn(), getFavouriteDataProducts: vi.fn(), saveViewedDataProduct: vi.fn(), getUseCases: vi.fn() }));
vi.mock('../../components/shared/V3Toast/V3Toast', () => ({ showToast: vi.fn() }));
vi.mock('react-redux', () => ({ useDispatch: () => vi.fn(), useSelector: () => [] }));
vi.mock('../../components/shared/Header/Header', () => ({ default: () => null }));
vi.mock('../../components/askportal/Chatbot', () => ({
  default: ({ showChatbot, productId }) => <aside aria-label="Ask panel" data-open={String(Boolean(showChatbot))}>{productId}</aside>,
}));
vi.mock('../../components/DataFields', () => ({ default: () => null }));
vi.mock('../../components/shared/V3ProductCard/V3Card', async (importOriginal) => {
  const actual = await importOriginal();
  return { ...actual, default: vi.fn(actual.default) };
});
vi.mock('../../components/DataLineage', () => ({ default: () => null }));
vi.mock('../../components/DataControls/DataControls', () => ({ default: () => null }));

const declared = Array.from({ length: 5 }, (_, i) => ({
  title: `Use case ${i + 1}`,
  description: `What use case ${i + 1} is for.\nIts second line.`,
  ...(i === 0 ? { url: 'https://example.com/one' } : i === 1 ? { url: 'javascript:alert(1)' } : {}),
}));
declared[4] = { title: '<b>Fifth</b>', description: '<img src=x onerror="alert(1)">' };
const product = (useCases) => ({ PRODUCTID: 'retail.account', PRODUCTNAME: 'Retail accounts', TECHNICALNAME: 'account', HIERARCHY: 'Portal->Retail->Accounts', BADGES: [], PRODUCT_SCORE: 0, COUNTOFCOLUMNS: 2, USE_CASES: useCases });
function SearchPage() {
  return <output aria-label="Search">{new URLSearchParams(useLocation().search).get('useCase')}</output>;
}
const mount = () => render(<MemoryRouter initialEntries={['/product/retail.account']}><Routes><Route path="/product/:id" element={<DataProductDetails />} /><Route path="/search" element={<SearchPage />} /></Routes></MemoryRouter>);
const ucCards = () => [...document.querySelectorAll('.mp-grid > .mp-card-wrap > .mp-card')];
const tabNames = () => within(screen.getByRole('tablist', { name: 'Data asset sections' })).getAllByRole('tab').map((tab) => tab.firstChild.textContent);

beforeEach(() => {
  vi.clearAllMocks();
  getConfig.mockResolvedValue({ data: {} });
  getMyRequests.mockResolvedValue({ data: [] });
  getUseCases.mockResolvedValue({ data: { useCaseList: [] } });
});

const listed = {
  useCaseList: [
    { id: 'uc-other', title: 'Not this asset\'s', objective: 'Listed for another asset.', domain: '', owner: '', url: '', kpi: null, example_questions: [], products: [{ id: 'x', name: 'X' }], count: 1 },
    {
      id: 'uc-use-case-2', title: 'Use case 2', objective: 'Portal-wide entry.', domain: 'Retail', owner: 'Retail desk', url: '',
      kpi: { name: 'Active accounts', definition: '', grain: 'Month', unit: '' }, example_questions: ['How many accounts were active last month?'],
      products: [{ id: 'retail.account', name: 'Retail accounts' }, { id: 'sme.account', name: 'SME accounts' }], count: 2,
    },
  ],
};

const openTab = async () => {
  fireEvent.click(await screen.findByRole('tab', { name: /^Use cases/ }));
  return within(await screen.findByRole('heading', { level: 3, name: 'Use cases' }).then((h) => h.closest('.card')));
};

test('the Use cases tab comes directly after Overview and shows a product card per use case the asset references, in its order', async () => {
  getProduct.mockResolvedValue({ data: product(declared) });
  getUseCases.mockResolvedValue({ data: listed });
  const { container } = mount();
  const tab = await screen.findByRole('tab', { name: /^Use cases/ });
  expect(tabNames().slice(0, 3)).toEqual(['Overview', 'Use cases', 'Fields']);
  expect(within(tab).getByText('5')).toHaveClass('count');
  fireEvent.click(tab);
  expect(tab).toHaveAttribute('aria-selected', 'true');
  expect(await screen.findByRole('button', { name: 'Explore use case Use case 1' })).toBeInTheDocument();
  const cards = ucCards();
  expect(cards).toHaveLength(5);
  expect(V3Card).toHaveBeenCalled();
  expect(new Set(V3Card.mock.calls.map(([props]) => props.title))).toEqual(new Set(['Use case 1', 'Use case 2', 'Use case 3', 'Use case 4', '<b>Fifth</b>']));
  expect(cards.map((card) => within(card).getByRole('heading', { level: 4 }).textContent)).toEqual(['Use case 1', 'Use case 2', 'Use case 3', 'Use case 4', '<b>Fifth</b>']);
  expect(cards.every((card) => card.classList.contains('mp-card--n') && card.getAttribute('role') === 'button')).toBe(true);
  expect(cards[1].querySelector('.mp-card__kind')).toHaveTextContent('Use case · Retail');
  expect(within(cards[1]).getByText('Retail desk')).toHaveClass('mp-card__meta');
  expect(within(cards[1]).getByText('Active accounts')).toHaveClass('mp-chip');
  expect(cards[1].querySelector('.mp-card__foot > .mp-card__meta')).toHaveTextContent('2 assets');
  expect(cards[0].querySelector('.mp-card__foot > .mp-card__meta')).toHaveTextContent('1 asset');
  expect(within(cards[4]).getByText('<img src=x onerror="alert(1)">')).toBeInTheDocument();
  expect(screen.queryByText('Not this asset\'s')).toBeNull();
  expect(screen.queryByText(/View details/)).toBeNull();
  expect(container.querySelector('.panel img, .panel b')).toBeNull();
  expect(screen.queryByRole('heading', { name: 'About this data asset' })).toBeNull();
  expect(getUseCases).toHaveBeenCalledTimes(1);
});

test('a use case the portal does not list falls back to the asset\'s own entry, linked to this asset', async () => {
  getProduct.mockResolvedValue({ data: product(declared.slice(0, 1)) });
  mount();
  await openTab();
  const card = await screen.findByRole('button', { name: 'Explore use case Use case 1' });
  expect(card.querySelector('.mp-card__desc')).toHaveTextContent('What use case 1 is for.');
  expect(card.querySelector('.mp-card__foot > .mp-card__meta')).toHaveTextContent(/^1 asset$/);
  fireEvent.click(card);
  const dialog = screen.getByRole('dialog', { name: 'Use case 1' });
  expect(within(within(dialog).getByRole('list', { name: 'Linked data products' })).getAllByRole('listitem').map((item) => item.querySelector('.uc-product-name').textContent)).toEqual(['Retail accounts']);
  expect(screen.queryByRole('status', { name: 'Search' })).toBeNull();
});

test('two inline titles that share an id each show their own card, not the portal-wide entry of the first', async () => {
  getProduct.mockResolvedValue({ data: product([{ title: 'A/B', description: 'First.' }, { title: 'a b', description: 'Second.' }]) });
  getUseCases.mockResolvedValue({ data: { useCaseList: [{ id: 'uc-a-b', title: 'A/B', objective: 'First.', domain: '', owner: '', url: '', kpi: null, example_questions: [], products: [{ id: 'retail.account', name: 'Retail accounts' }], count: 1 }] } });
  mount();
  await openTab();
  await screen.findByRole('button', { name: 'Explore use case A/B' });
  const cards = ucCards();
  expect(cards.map((card) => within(card).getByRole('heading', { level: 4 }).textContent)).toEqual(['A/B', 'a b']);
  expect(within(cards[1]).getByText('Second.')).toBeInTheDocument();
});

test('two inline titles that share an id each take the portal-wide card of their own title', async () => {
  getProduct.mockResolvedValue({ data: product([{ title: 'A/B', description: 'First.' }, { title: 'a b', description: 'Second.' }]) });
  const card = (title, objective, products) => ({ id: 'uc-a-b', title, objective, domain: '', owner: '', url: '', kpi: null, example_questions: [], products, count: products.length });
  getUseCases.mockResolvedValue({ data: { useCaseList: [
    card('A/B', 'First.', [{ id: 'retail.account', name: 'Retail accounts' }]),
    card('a b', 'Second.', [{ id: 'retail.account', name: 'Retail accounts' }, { id: 'sme.loan', name: 'SME loans' }]),
  ] } });
  mount();
  await openTab();
  await screen.findByRole('button', { name: 'Explore use case A/B' });
  const cards = ucCards();
  expect(cards.map((entry) => within(entry).getByRole('heading', { level: 4 }).textContent)).toEqual(['A/B', 'a b']);
  expect(cards[0].querySelector('.mp-card__foot > .mp-card__meta')).toHaveTextContent(/^1 asset$/);
  expect(cards[1].querySelector('.mp-card__foot > .mp-card__meta')).toHaveTextContent(/^2 assets$/);
});

test('when the use case list cannot be read the tab still shows the asset\'s own entries', async () => {
  getProduct.mockResolvedValue({ data: product([{ id: 'uc-a', title: 'Resolved A', objective: 'From the asset.' }]) });
  getUseCases.mockRejectedValue(new Error('down'));
  mount();
  const card = await openTab();
  expect(await card.findByText('From the asset.')).toBeInTheDocument();
});

test('the tab waits for the use case list before showing cards', async () => {
  getProduct.mockResolvedValue({ data: product(declared.slice(0, 1)) });
  let answer;
  getUseCases.mockReturnValue(new Promise((resolve) => { answer = resolve; }));
  mount();
  const card = await openTab();
  expect(card.getByText('Loading use cases…')).toHaveAttribute('aria-busy', 'true');
  answer({ data: listed });
  expect(await screen.findByRole('button', { name: 'Explore use case Use case 1' })).toBeInTheDocument();
});

test.each([
  ['a click', (card) => fireEvent.click(card)],
  ['Enter', (card) => fireEvent.keyDown(card, { key: 'Enter' })],
])('from the tab, %s on a use case opens its detail modal, not the search; Escape returns focus to the card', async (_how, press) => {
  getProduct.mockResolvedValue({ data: product(declared.slice(0, 2)) });
  getUseCases.mockResolvedValue({ data: listed });
  mount();
  await openTab();
  const card = await screen.findByRole('button', { name: 'Explore use case Use case 2' });
  card.focus();
  press(card);
  const dialog = screen.getByRole('dialog', { name: 'Use case 2' });
  expect(dialog).toHaveFocus();
  expect(within(dialog).getByText('Portal-wide entry.')).toBeInTheDocument();
  expect(within(dialog).getByText('Active accounts')).toBeInTheDocument();
  expect(within(within(dialog).getByRole('list', { name: 'Example questions' })).getAllByRole('listitem').map((item) => item.textContent)).toEqual(['How many accounts were active last month?']);
  expect(within(within(dialog).getByRole('list', { name: 'Linked data products' })).getAllByRole('listitem').map((item) => item.querySelector('.uc-product-name').textContent)).toEqual(['Retail accounts', 'SME accounts']);
  expect(screen.queryByRole('status', { name: 'Search' })).toBeNull();
  fireEvent.keyDown(dialog, { key: 'Escape' });
  expect(screen.queryByRole('dialog')).toBeNull();
  expect(card).toHaveFocus();
});

test('the modal\'s "Show assets in Discover" opens the search filtered to the use case', async () => {
  getProduct.mockResolvedValue({ data: product(declared.slice(0, 2)) });
  getUseCases.mockResolvedValue({ data: listed });
  mount();
  await openTab();
  fireEvent.click(await screen.findByRole('button', { name: 'Explore use case Use case 2' }));
  fireEvent.click(within(screen.getByRole('dialog')).getByRole('link', { name: 'Show assets in Discover' }));
  expect(await screen.findByRole('status', { name: 'Search' })).toHaveTextContent('Use case 2');
  expect(screen.queryByRole('dialog')).toBeNull();
});

test('the Overview has no use case box, and the Use cases tab still lists every use case of the asset', async () => {
  getProduct.mockResolvedValue({ data: product(declared) });
  mount();
  await screen.findByRole('heading', { level: 3, name: 'About this data asset' });
  expect(screen.queryByRole('heading', { level: 3, name: 'Use cases' })).toBeNull();
  expect(screen.queryByRole('button', { name: /All \d+ use cases/ })).toBeNull();
  fireEvent.click(screen.getByRole('tab', { name: /^Use cases/ }));
  await screen.findByRole('button', { name: 'Explore use case Use case 1' });
  expect(ucCards()).toHaveLength(5);
});

test.each([[undefined], [[]], ['not a list']])('an asset without use cases (%o) has no Use cases tab', async (useCases) => {
  getProduct.mockResolvedValue({ data: product(useCases) });
  mount();
  await screen.findByRole('heading', { level: 3, name: 'About this data asset' });
  expect(screen.queryByRole('tab', { name: /^Use cases/ })).toBeNull();
  expect(tabNames()[0]).toBe('Overview');
  expect(screen.queryByText('No use cases recorded yet')).toBeNull();
});

test('the asset page offers "Export as ODPS", which downloads the asset\'s ODPS document', async () => {
  getProduct.mockResolvedValue({ data: product(declared.slice(0, 1)) });
  mount();
  fireEvent.click(await screen.findByRole('button', { name: 'Export as ODPS' }));
  expect(exportAssetOdps).toHaveBeenCalledWith('retail.account');
  expect(showToast).not.toHaveBeenCalled();
  exportAssetOdps.mockRejectedValueOnce(new Error('404'));
  fireEvent.click(screen.getByRole('button', { name: 'Export as ODPS' }));
  await vi.waitFor(() => expect(showToast).toHaveBeenCalledWith('The ODPS export could not be downloaded'));
});

test('each card on the Use cases tab offers "Export as ODCS"; it downloads the contract, and only the card itself opens the modal', async () => {
  getProduct.mockResolvedValue({ data: product(declared.slice(0, 2)) });
  getUseCases.mockResolvedValue({ data: listed });
  mount();
  await openTab();
  const card = await screen.findByRole('button', { name: 'Explore use case Use case 2' });
  expect(ucCards().map((each) => within(each.parentElement).getByRole('button', { name: /^Export as ODCS: / }).getAttribute('aria-label'))).toEqual(['Export as ODCS: Use case 1', 'Export as ODCS: Use case 2']);
  fireEvent.click(within(card.parentElement).getByRole('button', { name: 'Export as ODCS: Use case 2' }));
  expect(exportUseCaseOdcs).toHaveBeenCalledTimes(1);
  expect(exportUseCaseOdcs.mock.calls[0][0]).toMatchObject({ id: 'uc-use-case-2', title: 'Use case 2' });
  expect(screen.queryByRole('dialog')).toBeNull();
  fireEvent.click(card);
  expect(screen.getByRole('dialog', { name: 'Use case 2' })).toBeInTheDocument();
  expect(screen.queryByRole('status', { name: 'Search' })).toBeNull();
  expect(exportUseCaseOdcs).toHaveBeenCalledTimes(1);
});
