import { expect, test, vi } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';
import FeaturedDataProducts from './FeaturedDataProducts';

vi.mock('./shared/StyledProductCard/StyledProductCard', () => ({
  default: ({ cardData, selectItem }) => <button onClick={() => selectItem(cardData)}>{cardData.name}</button>,
}));
vi.mock('./shared/Pagination/PaginationButton', () => ({
  default: ({ data, itemsPerPage, onClickChange }) => (
    <nav aria-label="pages">
      {Array.from({ length: Math.ceil(data.length / itemsPerPage) }, (_, i) => (
        <button key={i} onClick={() => onClickChange(i + 1)}>{`Page ${i + 1}`}</button>
      ))}
    </nav>
  ),
}));

// More than 12 products: the component resets to page 1 for 12 or fewer.
const products = Array.from({ length: 13 }, (_, i) => ({ name: `Product ${i + 1}` }));

test('featured products show one page at a time and page on request', async () => {
  const selectItem = vi.fn();
  render(<FeaturedDataProducts data={products} itemsPerPage={4} selectItem={selectItem} />);
  expect(await screen.findByRole('button', { name: 'Product 1' })).toBeInTheDocument();
  expect(screen.getByRole('button', { name: 'Product 2' })).toBeInTheDocument();
  expect(screen.queryByRole('button', { name: 'Product 5' })).toBeNull();

  fireEvent.click(screen.getByRole('button', { name: 'Page 4' }));
  expect(await screen.findByRole('button', { name: 'Product 13' })).toBeInTheDocument();
  expect(screen.queryByRole('button', { name: 'Product 1' })).toBeNull();

  fireEvent.click(screen.getByRole('button', { name: 'Product 13' }));
  expect(selectItem).toHaveBeenCalledWith({ name: 'Product 13' });
});

test('a short list needs no pages', async () => {
  render(<FeaturedDataProducts data={products.slice(0, 2)} itemsPerPage={4} selectItem={() => {}} />);
  expect(await screen.findByRole('button', { name: 'Product 2' })).toBeInTheDocument();
  expect(screen.queryByRole('navigation', { name: 'pages' })).toBeNull();
});
