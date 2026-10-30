import { expect, test, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import RequestQueue from './RequestQueue';
import { getApi, postApi } from '../../api/api';

vi.mock('../../api/api', () => ({ getApi: vi.fn(), postApi: vi.fn() }));

test('an approver sees the open requests and approves one', async () => {
  getApi.mockResolvedValueOnce({ data: [{ id: 7, product_name: 'Retail accounts', unit_id: 'retail', principal_id: 'analyst_retail', kind: 'access', created_at: '2026-10-23T00:00:00Z' }] })
    .mockResolvedValueOnce({ data: [] });
  postApi.mockResolvedValue({ data: { id: '7', status: 'APPROVED' } });
  render(<RequestQueue />);
  expect(await screen.findByText('Retail accounts')).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: 'Approve' }));
  await waitFor(() => expect(postApi).toHaveBeenCalledWith({ api: '/api/admin/requests/7/decision', params: { decision: 'approve' } }));
  expect(await screen.findByText('No open requests.')).toBeInTheDocument();
});

test('Reject is a visible, labelled button that posts the rejection', async () => {
  getApi.mockResolvedValueOnce({ data: [{ id: 9, product_name: 'Wealth holdings', unit_id: 'wealth', principal_id: 'analyst_wealth', kind: 'access', created_at: '2026-10-23T00:00:00Z' }] })
    .mockResolvedValueOnce({ data: [] });
  postApi.mockResolvedValue({ data: { id: '9', status: 'REJECTED' } });
  render(<RequestQueue />);
  const reject = await screen.findByRole('button', { name: 'Reject' });
  // the dark-header ghost style (white text) must not be used on the white panel
  expect(reject.className).toContain('btn-danger');
  expect(reject.className).not.toContain('btn-ghost-light');
  fireEvent.click(reject);
  await waitFor(() => expect(postApi).toHaveBeenCalledWith({ api: '/api/admin/requests/9/decision', params: { decision: 'reject' } }));
  expect(await screen.findByRole('status')).toHaveTextContent('No open requests.');
});

test('a queue row as the server sends it names its product by id', async () => {
  getApi.mockResolvedValueOnce({ data: [{ id: 1, principal_id: 'analyst_retail', product_id: 'retail.account', kind: 'access', status: 'OPEN', created_at: '2026-10-25T17:54:12.473Z', unit_id: 'retail' }] });
  render(<RequestQueue />);
  expect(await screen.findByText('retail.account')).toBeInTheDocument();
});

test('a person without the role is told so', async () => {
  getApi.mockRejectedValueOnce({ response: { status: 403 } });
  render(<RequestQueue />);
  expect(await screen.findByText(/approver or admin portal role/)).toBeInTheDocument();
});
