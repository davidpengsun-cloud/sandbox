import { expect, test, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import DataQuery from './DataQuery';
import { executeQuery, getQueryColumns } from '../services/metadata';

vi.mock('../services/metadata', () => ({ executeQuery: vi.fn(), getQueryColumns: vi.fn() }));
vi.mock('./shared/DataGridTable/DataGridTable', () => ({ default: () => <div>Results</div> }));

test('the query starts with the real permitted columns and resets when the product changes', async () => {
  getQueryColumns.mockResolvedValueOnce({ data: { columns: ['account_id', 'status_code'] } })
    .mockResolvedValueOnce({ data: { columns: ['advisor_id'] } });
  executeQuery.mockResolvedValue({ data: { columns: ['account_id'], rows: [{ account_id: 1 }] } });
  const { rerender } = render(<DataQuery data={{ PRODUCTID: 'retail.account', TECHNICALNAME: 'account', UNIT_ID: 'retail' }} />);
  await waitFor(() => expect(screen.getByLabelText('SQL query').value).toBe('SELECT account.account_id, account.status_code FROM account LIMIT 20'));
  expect(getQueryColumns).toHaveBeenCalledWith('retail.account');
  fireEvent.click(screen.getByRole('button', { name: 'Run query' }));
  await waitFor(() => expect(executeQuery).toHaveBeenCalledWith({ unit: 'retail', sql: 'SELECT account.account_id, account.status_code FROM account LIMIT 20' }));
  rerender(<DataQuery data={{ PRODUCTID: 'wealth.advisor', TECHNICALNAME: 'advisor', UNIT_ID: 'wealth' }} />);
  await waitFor(() => expect(screen.getByLabelText('SQL query').value).toBe('SELECT advisor.advisor_id FROM advisor LIMIT 20'));
});

test('no invented column is offered when the schema cannot be read', async () => {
  getQueryColumns.mockRejectedValue(new Error('unavailable'));
  render(<DataQuery data={{ PRODUCTID: 'retail.account', TECHNICALNAME: 'account', UNIT_ID: 'retail' }} />);
  expect(await screen.findByText('Available columns could not be loaded.')).toBeInTheDocument();
  expect(screen.getByRole('button', { name: 'Run query' })).toBeDisabled();
});
