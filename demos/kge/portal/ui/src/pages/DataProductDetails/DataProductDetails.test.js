import { beforeEach, expect, test, vi } from 'vitest';
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import DataProductDetails from './DataProductDetails';
import { getProduct } from '../../services/metadata';
import { getConfig, getMyRequests, requestAccess } from '../../services/user';
import { showToast } from '../../components/shared/V3Toast/V3Toast';
vi.mock('../../services/metadata', () => ({ getProduct: vi.fn(), submitFeedback: vi.fn() }));
vi.mock('../../services/user', () => ({ getConfig: vi.fn(), getMyRequests: vi.fn(), requestAccess: vi.fn(), removeFavouriteProduct: vi.fn(), saveFavouriteProduct: vi.fn(), getFavouriteDataProducts: vi.fn() }));
vi.mock('../../components/shared/V3Toast/V3Toast', () => ({ showToast: vi.fn() }));
vi.mock('react-redux', () => ({ useDispatch: () => vi.fn(), useSelector: () => [] }));
vi.mock('../../components/shared/Header/Header', () => ({ default: ({ toggleChatbot, askScope, askOpen }) => toggleChatbot ? <button onClick={toggleChatbot} aria-expanded={askOpen}>Ask about {askScope}</button> : null }));
vi.mock('../../components/askportal/Chatbot', () => ({ default: () => null }));
vi.mock('../../components/DataOverview/DataOverviewV3', () => ({ default: () => null }));
vi.mock('../../components/DataOverview/DataOverview', () => ({ default: () => null }));
vi.mock('../../components/DataFields', () => ({ default: () => null }));
vi.mock('../../components/DataLineage', () => ({ default: () => null }));
vi.mock('../../components/DataControls/DataControls', () => ({ default: () => null }));
vi.mock('../../components/DataPreview', () => ({ default: () => null }));
vi.mock('../../components/DataQuery', () => ({ default: () => null }));
vi.mock('../../components/UseCases/UseCases', () => ({ default: () => null }));
const product = { PRODUCTID: 'retail.account', PRODUCTNAME: 'Retail accounts', TECHNICALNAME: 'account', HIERARCHY: 'Portal->Retail->Accounts', BADGES: ['GOLD', 'CERTIFIED'], PRODUCT_SCORE: 0, DQRUNDATE: '2026-10-20' };
const mount = () => render(<MemoryRouter initialEntries={['/product/retail.account']}><Routes><Route path="/product/:id" element={<DataProductDetails />} /></Routes></MemoryRouter>);
beforeEach(() => {
  vi.clearAllMocks();
  getProduct.mockResolvedValue({ data: product });
  getConfig.mockResolvedValue({ data: { ask: true } });
  getMyRequests.mockResolvedValue({ data: [] });
  requestAccess.mockResolvedValue({ data: {} });
});

test('pending product uses a skeleton without a loading heading or visible text', () => {
  getProduct.mockReturnValue(new Promise(() => {}));
  const { container } = mount();
  expect(container.querySelector('[aria-busy="true"]')).toHaveAttribute('aria-label', 'Loading data asset');
  expect(container.querySelectorAll('.tape-cell')).toHaveLength(5);
  expect(screen.queryByRole('heading')).toBeNull();
  expect(screen.queryByText(/Loading/)).toBeNull();
});

test.each([[0, 'NO DQ RUN RECORDED'], [16.7, '16.7%']])('quality score %s is honest about assessment', async (score, text) => {
  getProduct.mockResolvedValue({ data: { ...product, PRODUCT_SCORE: score } });
  mount();
  expect(await screen.findByText(text)).toBeInTheDocument();
  expect(screen.queryByText('0%')).toBeNull();
  if (!score) expect(screen.queryByText(/LAST DQ/)).toBeNull();
  expect(screen.getByText('CERTIFIED')).toHaveClass('chip-aqua', 'chip-ok');
  expect(screen.getByText('CERTIFIED')).not.toHaveClass('chip-rasp');
});

test('Ask is scoped to the product whatever the config says, and request access becomes an announced chip with a toast', async () => {
  getConfig.mockResolvedValue({ data: { ask: false } });
  let finish;
  requestAccess.mockImplementation(() => new Promise(resolve => { finish = resolve; }));
  mount();
  const request = await screen.findByRole('button', { name: 'Request access' });
  expect(request).toHaveAttribute('title', "Record a request for this product's data with its data steward");
  expect(screen.queryByRole('button', { name: 'Ask Portal' })).toBeNull();
  const ask = screen.getByRole('button', { name: 'Ask about Retail accounts' });
  fireEvent.click(ask);
  expect(ask).toHaveAttribute('aria-expanded', 'true');
  request.focus();
  expect(request).toHaveFocus();
  fireEvent.click(request);
  expect(screen.getByRole('button', { name: 'Requesting…' })).toBeDisabled();
  expect(requestAccess).toHaveBeenCalledWith('retail.account');
  await act(async () => finish({ data: {} }));
  expect(screen.getByRole('status')).toHaveTextContent('ACCESS REQUESTED');
  // the button is gone: focus moves to its chip, not to the page body
  expect(screen.getByRole('status')).toHaveFocus();
  expect(screen.getByRole('status')).toHaveAttribute('tabindex', '-1');
  expect(showToast).toHaveBeenCalledWith('Request recorded — the data steward will review it');
});

test.each(['OPEN', 'APPROVED'])('existing %s request has a status chip instead of a button', async status => {
  getMyRequests.mockResolvedValue({ data: [{ product_id: product.PRODUCTID, kind: 'access', status }] });
  mount();
  await screen.findByText(status === 'OPEN' ? 'ACCESS REQUESTED' : 'ACCESS APPROVED');
  expect(screen.queryByRole('button', { name: 'Request access' })).toBeNull();
});

test('latest rejected request shows a declined chip and can record a new request', async () => {
  getMyRequests.mockResolvedValue({ data: [
    { product_id: 'other.product', kind: 'access', status: 'OPEN' },
    { product_id: product.PRODUCTID, kind: 'subscription', status: 'OPEN' },
    { product_id: product.PRODUCTID, kind: 'access', status: 'REJECTED' },
    { product_id: product.PRODUCTID, kind: 'access', status: 'APPROVED' },
  ] });
  let finish;
  requestAccess.mockImplementation(() => new Promise(resolve => { finish = resolve; }));
  mount();
  expect(await screen.findByText('REQUEST DECLINED')).toHaveClass('chip-rasp');
  const retry = screen.getByRole('button', { name: 'Request again' });
  expect(retry).toHaveAttribute('title', "Record a request for this product's data with its data steward");
  fireEvent.click(retry);
  expect(screen.getByRole('button', { name: 'Requesting…' })).toBeDisabled();
  expect(requestAccess).toHaveBeenCalledExactlyOnceWith(product.PRODUCTID);
  await act(async () => finish({ data: {} }));
  expect(screen.getByRole('status')).toHaveTextContent('ACCESS REQUESTED');
  expect(screen.getByRole('status')).toHaveFocus();
  expect(screen.queryByText('REQUEST DECLINED')).toBeNull();
  expect(screen.queryByRole('button', { name: 'Request again' })).toBeNull();
  expect(showToast).toHaveBeenCalledWith('Request recorded — the data steward will review it');
});

test('failed retry preserves the declined chip and restores Request again', async () => {
  getMyRequests.mockResolvedValue({ data: [{ product_id: product.PRODUCTID, kind: 'access', status: 'REJECTED' }] });
  requestAccess.mockRejectedValue({ response: { data: { detail: 'Please try again later.' } } });
  mount();
  fireEvent.click(await screen.findByRole('button', { name: 'Request again' }));
  await waitFor(() => expect(showToast).toHaveBeenCalledWith('Please try again later.'));
  expect(screen.getByRole('button', { name: 'Request again' })).toBeEnabled();
  expect(screen.getByRole('status')).toHaveTextContent('REQUEST DECLINED');
});

test('a slow request-list response cannot erase a successful access request', async () => {
  let finish;
  getMyRequests.mockImplementation(() => new Promise(resolve => { finish = resolve; }));
  mount();
  fireEvent.click(await screen.findByRole('button', { name: 'Request access' }));
  await screen.findByText('ACCESS REQUESTED');
  await act(async () => finish({ data: [] }));
  expect(screen.getByRole('status')).toHaveTextContent('ACCESS REQUESTED');
});

test.each(['rejection', 'empty'])('%s product response shows an accessible missing state', async kind => {
  if (kind === 'rejection') getProduct.mockRejectedValue(new Error('missing'));
  else getProduct.mockResolvedValue({ data: null });
  mount();
  expect(await screen.findByRole('status')).toHaveTextContent('could not be found');
  expect(screen.getByRole('link', { name: 'Back to home' })).toHaveAttribute('href', '/home');
});
