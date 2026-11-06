import { expect, test } from 'vitest';
import { render, screen } from '@testing-library/react';
import DataGridTable from './DataGridTable';

test('the grid shows its column headers and rows', async () => {
  render(
    <DataGridTable
      rows={[{ name: 'order_id', type: 'STRING' }, { name: 'amount', type: 'NUMERIC' }]}
      columns={[{ field: 'name', headerName: 'Field Name' }, { field: 'type', headerName: 'Type' }]}
      containerStyle={{ height: '400px' }}
    />,
  );
  expect(await screen.findByText('Field Name')).toBeInTheDocument();
  expect(await screen.findByText('order_id')).toBeInTheDocument();
  expect(screen.getByText('NUMERIC')).toBeInTheDocument();
});
