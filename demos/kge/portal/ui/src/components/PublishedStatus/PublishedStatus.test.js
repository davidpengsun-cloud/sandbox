import { beforeEach, expect, test, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import PublishedStatus from './PublilshedStatus';
import * as admin from '../../services/admin';
vi.mock('../../services/admin', () => ({ getPublishedStatus: vi.fn(), updatePublishedStatus: vi.fn(), publishStatusLiveSync: vi.fn(), updateApplicationLiveSync: vi.fn(), getDomainList: vi.fn(), getFilterResult: vi.fn(), getPublishSearchData: vi.fn(), getPublishedSearchResult: vi.fn() }));
vi.mock('react-redux', () => ({ useSelector: () => ({ userId: 'admin' }) }));
beforeEach(() => {
  vi.clearAllMocks();
  admin.getPublishedStatus.mockResolvedValue({ data: [{ TECHNICAL_NAME: 'retail.account', PRODUCTNAME: 'Retail accounts', PUBLISHED_STATUS: true, DOMAIN_NAME: 'Retail', COMMUNITY_NAME: 'Banking' }] });
  admin.getDomainList.mockResolvedValue({ data: [] });
  admin.getPublishSearchData.mockResolvedValue({ data: [] });
});

test('labelled filters and publish checkbox update the table and announce the result', async () => {
  admin.updatePublishedStatus.mockResolvedValue({ data: [{ PUBLISHED_STATUS: false }] });
  render(<PublishedStatus />);
  expect(await screen.findByRole('table', { name: 'Published status' })).toHaveTextContent('Retail accounts');
  expect(screen.getByLabelText('Data asset').tagName).toBe('SELECT');
  expect(screen.getByLabelText('Domain').tagName).toBe('SELECT');
  const checkbox = screen.getByRole('checkbox', { name: 'Publish Retail accounts (Retail)' });
  checkbox.focus();
  expect(checkbox).toHaveFocus();
  fireEvent.click(checkbox);
  await waitFor(() => expect(admin.updatePublishedStatus).toHaveBeenCalledWith({ technicalName: 'retail.account', publishedStatus: 'N', approver: 'admin' }));
  await waitFor(() => expect(screen.getByRole('status')).toHaveTextContent('Retail accounts is now unpublished.'));
  expect(checkbox).not.toBeChecked();
});

test('live sync waits for both services and announces completion', async () => {
  admin.publishStatusLiveSync.mockResolvedValue({ data: true });
  let finish;
  admin.updateApplicationLiveSync.mockImplementation(() => new Promise(resolve => { finish = resolve; }));
  render(<PublishedStatus />);
  fireEvent.click(screen.getByRole('button', { name: 'Live sync' }));
  await waitFor(() => expect(admin.updateApplicationLiveSync).toHaveBeenCalledOnce());
  expect(screen.getByRole('status')).not.toHaveTextContent('Live sync completed.');
  expect(screen.getByRole('button', { name: 'Syncing…' })).toBeDisabled();
  finish({ data: true });
  await waitFor(() => expect(screen.getByRole('status')).toHaveTextContent('Live sync completed.'));
});

test('products of the same name have checkboxes told apart by their domain', async () => {
  admin.getPublishedStatus.mockResolvedValue({ data: [
    { TECHNICAL_NAME: 'retail.account', PRODUCTNAME: 'Account', PUBLISHED_STATUS: true, DOMAIN_NAME: 'Retail Banking', COMMUNITY_NAME: 'Core Banking Ledger' },
    { TECHNICAL_NAME: 'wealth.account', PRODUCTNAME: 'Account', PUBLISHED_STATUS: true, DOMAIN_NAME: 'Wealth Management', COMMUNITY_NAME: 'Wealth Client Platform' },
  ] });
  render(<PublishedStatus />);
  expect(await screen.findByRole('checkbox', { name: 'Publish Account (Retail Banking)' })).toBeInTheDocument();
  expect(screen.getByRole('checkbox', { name: 'Publish Account (Wealth Management)' })).toBeInTheDocument();
});
