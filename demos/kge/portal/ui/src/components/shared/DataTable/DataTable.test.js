import { expect, test } from 'vitest';
import { render, screen } from '@testing-library/react';
import DataTable from './DataTable';

test('shows the header and each row', () => {
  render(<DataTable tableHeader={['Field Name', 'Description']} data={[{ name: 'order_id', desc: 'Order key' }, { name: 'total', desc: 'Order value' }]} />);
  expect(screen.getByText('Field Name')).toBeInTheDocument();
  expect(screen.getByText('order_id')).toBeInTheDocument();
  expect(screen.getByText('Order value')).toBeInTheDocument();
});

test('without rows it says it is loading', () => {
  render(<DataTable tableHeader={['Field Name']} data={[]} />);
  expect(screen.getByText('Loading...')).toBeInTheDocument();
});
