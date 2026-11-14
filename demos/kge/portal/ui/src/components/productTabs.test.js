import { afterEach, beforeEach, expect, test, vi } from 'vitest';
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
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
vi.mock('@xyflow/react', () => ({
  ReactFlow: ({ nodes, edges, children }) => (
    <div aria-label="lineage graph">
      {nodes.map((n) => <span key={n.id}>{n.data.label}</span>)}
      {edges.map((e) => <i key={e.id} data-testid="edge" data-arrow={e.markerEnd?.type} data-source={e.source} data-target={e.target} data-selected={String(!!e.selected)} />)}
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
    { 'Field Name': 'order_id', Description: 'Order key', Classification: 'Internal', PII: '', 'Data Quality Score': 99 },
    { 'Field Name': 'amount', Description: 'Value', Classification: 'Restricted', PII: 'Yes', 'Data Quality Score': 80 },
    { 'Field Name': 'tax_id', Description: 'Identifier', Classification: 'Highly Confidential', PII: '', 'Data Quality Score': 95 },
    { 'Field Name': 'notes', Description: 'Free text', Classification: 'unknown', 'Data Quality Score': 75 },
  ] });
  render(<DataFields data={product} />);
  expect(screen.getByRole('status')).toHaveTextContent('Loading…');
  expect(await screen.findByRole('heading', { name: '4 fields' })).toBeInTheDocument();
  expect(screen.getByText('order_id')).toBeInTheDocument();
  expect(screen.getByRole('columnheader', { name: 'Field Name (sortable)' })).toBeInTheDocument();
  expect(screen.getByRole('columnheader', { name: 'Description' })).toBeInTheDocument();
  expect(screen.queryByRole('columnheader', { name: 'Classification' })).toBeNull();
  expect(screen.getByRole('columnheader', { name: 'PII' })).toBeInTheDocument();
  expect(screen.getAllByText('Yes')).toHaveLength(1);
  expect(screen.queryByText('Internal')).toBeNull();
  expect(screen.queryByText('Highly Confidential')).toBeNull();
  expect(screen.queryByText('Restricted')).toBeNull();
  expect(screen.queryByText('unknown')).toBeNull();
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
  getDataQuality.mockResolvedValue({ data: [{ 'Data Element Name': 'amount', 'Data Quality Rule Name': 'not null', 'Rule description': 'Every amount is present.', 'Failing %': 0 }, { 'Data Element Name': 'id', 'Data Quality Rule Name': 'unique', 'Rule description': '', 'Failing %': 0 }] });
  render(<DataControls data={product} />);
  expect(await screen.findByText('not null')).toBeInTheDocument();
  expect(screen.getByText('Every amount is present.')).toBeInTheDocument();
  const header = screen.getByRole('columnheader', { name: 'Rule description' });
  expect(header.previousElementSibling).toHaveTextContent('Data Quality Rule Name');
  expect(screen.getByText('unique').nextElementSibling).toBeEmptyDOMElement();
  expect(getDataQuality).toHaveBeenCalledWith('retail.orders', 'all');
  expect(screen.getByRole('button', { name: 'All' })).toHaveAttribute('aria-pressed', 'true');
  expect(saveViewedDataProduct).toHaveBeenCalledWith(expect.objectContaining({ tab: 'DETAILS-DATA_QUALITY' }));

  getDataQuality.mockResolvedValue({ data: [] });
  fireEvent.click(screen.getByRole('button', { name: 'Anomaly checks' }));
  expect(await screen.findByText('No records found')).toBeInTheDocument();
  expect(getDataQuality).toHaveBeenLastCalledWith('retail.orders', 'adaptive');
  expect(screen.getByRole('button', { name: 'Anomaly checks' })).toHaveAttribute('aria-pressed', 'true');
});

test('Data quality lists every rule of a column and leaves a blank fail % blank, never 0', async () => {
  getDataQuality.mockResolvedValue({ data: [
    { 'Data Element Name': 'amount', 'Data Quality Rule Name': 'not null', 'Rule description': '', 'Data Quality Score': 99, 'Failing %': 1 },
    { 'Data Element Name': 'amount', 'Data Quality Rule Name': 'in range', 'Rule description': '', 'Data Quality Score': '', 'Failing %': '' },
  ] });
  render(<DataControls data={product} />);
  expect(await screen.findByText('not null')).toBeInTheDocument();
  expect(screen.getAllByText('amount')).toHaveLength(2);
  const blank = screen.getByText('in range').closest('tr').querySelectorAll('td');
  expect(blank[3]).toBeEmptyDOMElement();
  expect(blank[4]).toBeEmptyDOMElement();
  expect(screen.queryByText('0')).toBeNull();
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
  expect(screen.getByTestId('edge')).toHaveAttribute('data-source', 'crm');
  expect(screen.getByTestId('edge')).toHaveAttribute('data-target', 'orders');
  expect(screen.getByTestId('edge')).toHaveAttribute('data-selected', 'false');
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

test.each([undefined, true])('Data quality filters %s open on All and keep all four choices', async (dqFilters) => {
  getDataQuality.mockResolvedValue({ data: [{ 'Data Element Name': 'amount', 'Data Quality Rule Name': 'not null' }] });
  render(<DataControls data={product} config={{ dqFilters }} />);
  await screen.findByText('not null');
  expect(getDataQuality).toHaveBeenCalledExactlyOnceWith('retail.orders', 'all');
  const group = screen.getByRole('group', { name: 'Filter data quality rules' });
  expect(group.querySelectorAll('button')).toHaveLength(4);
  expect(screen.getByRole('button', { name: 'All' })).toHaveAttribute('aria-pressed', 'true');
  for (const [label, value] of [['Custom data checks', 'custom'], ['No rules', 'na'], ['All', 'all']]) {
    fireEvent.click(screen.getByRole('button', { name: label }));
    await waitFor(() => expect(getDataQuality).toHaveBeenLastCalledWith('retail.orders', value));
  }
});

test('Data quality filters off hide every filter and show the complete All response', async () => {
  getDataQuality.mockResolvedValue({ data: [
    { 'Data Element Name': 'amount', 'Data Quality Rule Name': 'not null' },
    { 'Data Element Name': 'unruled', 'Data Quality Rule Name': '' },
  ] });
  render(<DataControls data={product} config={{ dqFilters: false }} />);
  await screen.findByText('not null');
  expect(screen.getByText('unruled')).toBeInTheDocument();
  expect(screen.getByRole('columnheader', { name: 'Data Element Name (sortable)' })).toBeInTheDocument();
  expect(screen.queryByRole('group', { name: 'Filter data quality rules' })).toBeNull();
  expect(screen.queryByRole('button')).toBeNull();
  expect(getDataQuality).toHaveBeenCalledExactlyOnceWith('retail.orders', 'all');
});

test('a filter response still in flight cannot replace All when configuration hides the filters', async () => {
  let finish;
  getDataQuality.mockResolvedValue({ data: [{ 'Data Element Name': 'all rows' }] });
  const { rerender } = render(<DataControls data={product} config={{ dqFilters: true }} />);
  await screen.findByText('all rows');
  getDataQuality.mockImplementationOnce(() => new Promise((resolve) => { finish = resolve; }));
  fireEvent.click(screen.getByRole('button', { name: 'Custom data checks' }));
  rerender(<DataControls data={product} config={{ dqFilters: false }} />);
  await waitFor(() => expect(getDataQuality).toHaveBeenLastCalledWith('retail.orders', 'all'));
  await act(async () => finish({ data: [{ 'Data Element Name': 'filtered rows' }] }));
  await waitFor(() => expect(screen.getByText('all rows')).toBeInTheDocument());
  expect(screen.queryByText('filtered rows')).toBeNull();
});
