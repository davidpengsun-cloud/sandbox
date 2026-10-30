import { beforeEach, expect, test, vi } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';
import DataPreview from './DataPreview';
import DataQuery from './DataQuery';
import { getDataPreview, getDataExport, getQueryColumns, executeQuery } from '../services/metadata';

vi.mock('../services/metadata', () => ({ getDataPreview: vi.fn(), getDataExport: vi.fn(), getQueryColumns: vi.fn(), executeQuery: vi.fn() }));
vi.mock('../services/user', () => ({ saveViewedDataProduct: vi.fn() }));
vi.mock('react-redux', () => ({ useSelector: () => 'Facts' }));
vi.mock('./shared/DataGridTable/DataGridTable', () => ({ default: () => <div>Rows</div> }));
vi.mock('./shared/NoDataFound', () => ({ default: ({ message }) => <p>{message}</p> }));

const data = { PRODUCTID: 'unit.facts', TECHNICALNAME: 'facts', UNIT_ID: 'unit' };
const reason = 'The current release has not landed table(s): facts.';
const refusal = { response: { status: 403, data: { detail: reason } } };
beforeEach(() => vi.resetAllMocks());

test('preview shows the governance refusal', async () => {
  getDataPreview.mockRejectedValue(refusal);
  render(<DataPreview data={data} />);
  expect(await screen.findByText(reason)).toBeInTheDocument();
});

test('export shows its refusal on the page', async () => {
  getDataPreview.mockResolvedValue({ data: [{ id: 1 }] });
  getDataExport.mockRejectedValue(refusal);
  render(<DataPreview data={data} />);
  fireEvent.click(await screen.findByRole('button', { name: 'Export CSV' }));
  expect(await screen.findByRole('alert')).toHaveTextContent(reason);
});

test('query setup shows the governance refusal', async () => {
  getQueryColumns.mockRejectedValue(refusal);
  render(<DataQuery data={data} />);
  expect(await screen.findByText(reason)).toBeInTheDocument();
});

test('query execution shows the governance refusal', async () => {
  getQueryColumns.mockResolvedValue({ data: { columns: ['id'] } });
  executeQuery.mockRejectedValue(refusal);
  render(<DataQuery data={data} />);
  const query = await screen.findByLabelText('SQL query');
  fireEvent.change(query, { target: { value: 'SELECT id FROM facts' } });
  fireEvent.click(screen.getByRole('button', { name: 'Run query' }));
  expect(await screen.findByText(reason)).toBeInTheDocument();
});

test('CSV download uses the server filename including the unit', async () => {
  getDataPreview.mockResolvedValue({ data: [{ id: 1 }] });
  getDataExport.mockResolvedValue({ data: 'id\n1\n', headers: { 'content-disposition': 'attachment; filename="unit-facts.csv"' } });
  URL.createObjectURL = vi.fn(() => 'blob:export');
  URL.revokeObjectURL = vi.fn();
  let downloaded;
  const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(function () { downloaded = this.download; });
  render(<DataPreview data={data} />);
  fireEvent.click(await screen.findByRole('button', { name: 'Export CSV' }));
  await vi.waitFor(() => expect(downloaded).toBe('unit-facts.csv'));
  expect(URL.revokeObjectURL).toHaveBeenCalledWith('blob:export');
  click.mockRestore();
});
