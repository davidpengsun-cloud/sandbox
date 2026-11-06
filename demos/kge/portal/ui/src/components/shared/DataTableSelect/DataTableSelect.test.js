import { expect, test, vi } from 'vitest';
import { fireEvent, render, screen, within } from '@testing-library/react';
import DataTableSelect from './DataTableSelect';

test('the publish table shows each row with publish and preview checkboxes', () => {
  const handleSelection = vi.fn();
  const handlePreviewSelection = vi.fn();
  const data = [{ TECHNICAL_NAME: 'retail.orders', PUBLISHED_STATUS: true, DATA_PREVIEW: false }];
  render(<DataTableSelect page="PublishedStatus" tableHeader={['TECHNICAL_NAME', 'PUBLISHED_STATUS', 'DATA_PREVIEW']} data={data} message="" handleSelection={handleSelection} handlePreviewSelection={handlePreviewSelection} />);
  expect(screen.getByText('TECHNICAL_NAME')).toBeInTheDocument();
  expect(screen.getByText('retail.orders')).toBeInTheDocument();
  const [publish, preview] = screen.getAllByRole('checkbox');
  expect(publish).toBeChecked();
  expect(preview).not.toBeChecked();
  fireEvent.click(publish);
  expect(handleSelection).toHaveBeenCalledWith(data, 'retail.orders', 'N');
  fireEvent.click(preview);
  expect(handlePreviewSelection).toHaveBeenCalledWith(data, 'retail.orders', 'Y');
});

test('unpublished rows publish as Y and previewed rows unpreview as N', () => {
  const handleSelection = vi.fn();
  const handlePreviewSelection = vi.fn();
  const data = [{ TECHNICAL_NAME: 't', PUBLISHED_STATUS: false, DATA_PREVIEW: true }];
  render(<DataTableSelect page="PublishedStatus" tableHeader={['a']} data={data} handleSelection={handleSelection} handlePreviewSelection={handlePreviewSelection} />);
  const [publish, preview] = screen.getAllByRole('checkbox');
  fireEvent.click(publish);
  fireEvent.click(preview);
  expect(handleSelection).toHaveBeenCalledWith(data, 't', 'Y');
  expect(handlePreviewSelection).toHaveBeenCalledWith(data, 't', 'N');
});

test('the user table offers roles and reports a change for that user', () => {
  const handleRoleChange = vi.fn();
  const data = [{ User_Id: 'u1', Name: 'Ana', Role: 'User' }];
  render(<DataTableSelect page="UserDetails" tableHeader={['User_Id', 'Name', 'Role']} data={data} handleRoleChange={handleRoleChange} />);
  expect(screen.getByText('Ana')).toBeInTheDocument();
  expect(screen.getByLabelText('User')).toBeChecked();
  fireEvent.click(screen.getByLabelText('Admin'));
  expect(handleRoleChange).toHaveBeenCalledWith(data, 'u1', 'Admin');
});

test('an unknown page renders rows without cells', () => {
  render(<DataTableSelect page="Other" tableHeader={['x']} data={[{ x: 'hidden' }]} />);
  expect(screen.queryByText('hidden')).toBeNull();
});

test('with no rows the message shows', () => {
  render(<DataTableSelect page="PublishedStatus" tableHeader={['x']} data={[]} message="No products" />);
  expect(screen.getByText('No products')).toBeInTheDocument();
});
