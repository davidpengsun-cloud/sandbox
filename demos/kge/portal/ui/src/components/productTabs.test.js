import { afterEach, beforeEach, expect, test, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import DataFields from './DataFields';
import DataControls from './DataControls/DataControls';
import DataLineage from './DataLineage';
import { getDataQuality, getFields, getLineage } from '../services/metadata';
import { saveViewedDataProduct } from '../services/user';

vi.mock('../services/metadata', () => ({ getFields: vi.fn(), getDataQuality: vi.fn(), getLineage: vi.fn() }));
vi.mock('../services/user', () => ({ saveViewedDataProduct: vi.fn() }));
vi.mock('react-redux', () => ({ useSelector: (select) => select({ dataProduct: { selectedProduct: 'retail.orders' } }) }));
vi.mock('./shared/DataGridTable/DataGridTable', () => ({
  default: ({ rows, columns }) => (
    <table>
      <thead><tr>{columns.map((c) => <th key={c.field}>{c.headerName}{c.sortable ? ' (sortable)' : ''}</th>)}</tr></thead>
      <tbody>{rows.map((r, i) => <tr key={i}>{Object.values(r).map((v, j) => <td key={j}>{v}</td>)}</tr>)}</tbody>
    </table>
  ),
}));
vi.mock('reactflow', () => ({
  default: ({ nodes, edges, children }) => (
    <div aria-label="lineage graph">
      {nodes.map((n) => <span key={n.id}>{n.data.label}</span>)}
      {edges.map((e) => <i key={e.id} data-testid="edge" data-arrow={e.markerEnd?.type} />)}
      {children}
    </div>
  ),
  Controls: () => null,
  Background: () => null,
  MarkerType: { ArrowClosed: 'arrowclosed' },
}));

const product = { PRODUCTID: 'retail.orders', TECHNICALNAME: 'orders' };
beforeEach(() => vi.resetAllMocks());

test('Fields lists each field with sortable name and score columns and records the visit', async () => {
  getFields.mockResolvedValue({ data: [
    { 'Field Name': 'order_id', Description: 'Order key', 'Data Quality Score': 99 },
    { 'Field Name': 'amount', Description: 'Value', 'Data Quality Score': 80 },
  ] });
  render(<DataFields data={product} />);
  expect(screen.getByRole('status')).toHaveTextContent('Loading…');
  expect(await screen.findByRole('heading', { name: '2 fields' })).toBeInTheDocument();
  expect(screen.getByText('order_id')).toBeInTheDocument();
  expect(screen.getByRole('columnheader', { name: 'Field Name (sortable)' })).toBeInTheDocument();
  expect(screen.getByRole('columnheader', { name: 'Description' })).toBeInTheDocument();
  expect(getFields).toHaveBeenCalledWith('retail.orders');
  expect(saveViewedDataProduct).toHaveBeenCalledWith({ productId: 'retail.orders', productName: 'orders', searchWord: null, category: 'Details', tab: 'DETAILS-FIELDS' });
});

test('Fields says "1 field" for a single field', async () => {
  getFields.mockResolvedValue({ data: [{ 'Field Name': 'only' }] });
  render(<DataFields data={product} />);
  expect(await screen.findByRole('heading', { name: '1 field' })).toBeInTheDocument();
});

test.each([
  ['an empty list', () => getFields.mockResolvedValue({ data: [] })],
  ['no body', () => getFields.mockResolvedValue({})],
  ['a failure', () => getFields.mockRejectedValue(new Error('down'))],
])('Fields shows "No records found" for %s', async (_name, arrange) => {
  arrange();
  render(<DataFields data={product} />);
  expect(await screen.findByText('No records found')).toBeInTheDocument();
});

test('Data quality loads all rules, then reloads for the chosen filter', async () => {
  getDataQuality.mockResolvedValue({ data: [{ 'Data Element Name': 'amount', 'Data Quality Rule Name': 'not null', 'Failing %': 0 }] });
  render(<DataControls data={product} />);
  expect(await screen.findByText('not null')).toBeInTheDocument();
  expect(getDataQuality).toHaveBeenCalledWith('retail.orders', 'all');
  expect(screen.getByRole('button', { name: 'All' })).toHaveAttribute('aria-pressed', 'true');
  expect(saveViewedDataProduct).toHaveBeenCalledWith(expect.objectContaining({ tab: 'DETAILS-DATA_QUALITY' }));

  getDataQuality.mockResolvedValue({ data: [] });
  fireEvent.click(screen.getByRole('button', { name: 'Anomaly checks' }));
  expect(await screen.findByText('No records found')).toBeInTheDocument();
  expect(getDataQuality).toHaveBeenLastCalledWith('retail.orders', 'adaptive');
  expect(screen.getByRole('button', { name: 'Anomaly checks' })).toHaveAttribute('aria-pressed', 'true');
});

test.each([
  ['no body', () => getDataQuality.mockResolvedValue(null)],
  ['a failure', () => getDataQuality.mockRejectedValue(new Error('down'))],
])('Data quality shows "No records found" for %s', async (_name, arrange) => {
  arrange();
  render(<DataControls data={product} />);
  expect(await screen.findByText('No records found')).toBeInTheDocument();
});

test('Lineage draws the graph it is given with arrowed edges', async () => {
  getLineage.mockResolvedValue({ data: {
    nodes: [{ id: 'crm', data: { label: 'CRM' } }, { id: 'orders', data: { label: 'Orders' } }],
    edges: [{ id: 'e1', source: 'crm', target: 'orders' }],
  } });
  render(<DataLineage data={product} />);
  expect(await screen.findByRole('heading', { name: 'Lineage' })).toBeInTheDocument();
  expect(screen.getByText('CRM')).toBeInTheDocument();
  expect(screen.getByText('Orders')).toBeInTheDocument();
  expect(screen.getByTestId('edge')).toHaveAttribute('data-arrow', 'arrowclosed');
  expect(getLineage).toHaveBeenCalledWith('retail.orders');
  expect(saveViewedDataProduct).toHaveBeenCalledWith(expect.objectContaining({ tab: 'DETAILS-LINEAGE' }));
});

test.each([
  ['no graph', () => getLineage.mockResolvedValue({ data: {} })],
  ['an empty graph', () => getLineage.mockResolvedValue({ data: { nodes: [], edges: [] } })],
  ['a failure', () => getLineage.mockRejectedValue(new Error('down'))],
])('Lineage shows "No records found" for %s', async (_name, arrange) => {
  arrange();
  render(<DataLineage data={product} />);
  expect(await screen.findByText('No records found')).toBeInTheDocument();
});
