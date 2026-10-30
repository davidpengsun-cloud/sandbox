import { expect, test } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import DataCommunity from './DataCommunity';

const setup = (content) => render(
  <MemoryRouter>
    <Routes>
      <Route path="/" element={<DataCommunity content={content} />} />
      <Route path="/search" element={<p>search page</p>} />
    </Routes>
  </MemoryRouter>,
);

test('lists communities with their domains and opens search from a domain', () => {
  setup([{ name: 'Retail', expanded: true, description: 'r'.repeat(60), domain: [{ name: 'Sales', description: 'Orders and returns' }, { name: 'Stock', description: 's'.repeat(55) }] }]);
  expect(screen.getByText('Retail')).toBeInTheDocument();
  expect(screen.getByText(`${'r'.repeat(50)}...`)).toBeInTheDocument();
  expect(screen.getByText('Orders and returns')).toBeInTheDocument();
  expect(screen.getByText(`${'s'.repeat(50)}...`)).toBeInTheDocument();
  fireEvent.click(screen.getByText('Sales'));
  expect(screen.getByText('search page')).toBeInTheDocument();
});

test('a short description shows in full', () => {
  setup([{ name: 'Finance', expanded: false, description: 'Ledgers', domain: [] }]);
  expect(screen.getByText('Ledgers')).toBeInTheDocument();
});

test('no communities renders nothing', () => {
  const { container } = setup([]);
  expect(container).toBeEmptyDOMElement();
});
