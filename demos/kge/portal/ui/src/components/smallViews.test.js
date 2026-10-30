import { expect, test, vi } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';
import { Provider } from 'react-redux';
import { configureStore } from '@reduxjs/toolkit';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import dataProduct from '../features/dataProduct/dataProductSlice';
import Loading from './Loading';
import NoDataFound from './shared/NoDataFound';
import HoverPopup from './shared/HoverPopup/HoverPopup';
import DataCard from './shared/DataCard/DataCard';
import theme from '../theme';

vi.mock('./shared/DataCommunity/DataCommunityTree', () => ({ default: ({ content }) => <p>Community tree of {content.length}</p> }));

test('Loading tells the person the portal is loading', () => {
  render(<Loading />);
  expect(screen.getByText('Loading portal…')).toBeInTheDocument();
});

test('NoDataFound shows its message as a status', () => {
  render(<NoDataFound message="No products match" />);
  expect(screen.getByRole('status')).toHaveTextContent('No products match');
});

test('HoverPopup labels each product fact', () => {
  render(<HoverPopup data={{ COUNTOFCOLUMNS: '' }} />);
  for (const label of ['Publish Date', 'Data Owner', 'Source records (before read filters)', '# of Columns']) {
    expect(screen.getByText(label)).toBeInTheDocument();
  }
});

test('the theme carries the portal palette', () => {
  expect(theme.palette.error.main).toBe('#A13030');
  expect(theme.typography.fontFamily).toContain('EB Garamond');
});

function renderCard(props) {
  const store = configureStore({ reducer: { dataProduct } });
  render(
    <Provider store={store}>
      <MemoryRouter initialEntries={['/']}>
        <Routes>
          <Route path="/" element={<DataCard {...props} />} />
          <Route path="/productDetails/:id" element={<p>Details page</p>} />
        </Routes>
      </MemoryRouter>
    </Provider>,
  );
  return store;
}

test('DataCard lists products with like counts and opens one on click', () => {
  const store = renderCard({
    title: 'Most viewed',
    type: 'list',
    data: [
      { DATA_PRODUCT_ID: 'p1', PRODUCT_NAME: 'Orders', TECHNICAL_NAME: 'orders_t', SUBSCRIBERS: null },
      { DATA_PRODUCT_ID: 'p2', PRODUCT_NAME: 'Customers', TECHNICAL_NAME: 'cust_t', SUBSCRIBERS: 1 },
      { DATA_PRODUCT_ID: 'p3', PRODUCT_NAME: 'Stores', TECHNICAL_NAME: 'stores_t', SUBSCRIBERS: 5 },
    ],
  });
  expect(screen.getByRole('heading', { name: 'Most viewed' })).toBeInTheDocument();
  expect(screen.getByText('0 likes')).toBeInTheDocument();
  expect(screen.getByText('1 like')).toBeInTheDocument();
  expect(screen.getByText('5 likes')).toBeInTheDocument();
  fireEvent.click(screen.getByText('Customers'));
  expect(screen.getByText('Details page')).toBeInTheDocument();
  expect(store.getState().dataProduct.selectedProduct).toBe('p2');
  expect(store.getState().dataProduct.selectedProductName).toBe('cust_t');
});

test('DataCard with no products shows only its title', () => {
  renderCard({ title: 'Recent', type: 'list', data: [] });
  expect(screen.getByRole('heading', { name: 'Recent' })).toBeInTheDocument();
  expect(screen.queryByText(/likes?$/)).toBeNull();
});

test('a community DataCard shows the community tree', () => {
  renderCard({ title: 'Communities', type: 'community', data: [1, 2] });
  expect(screen.getByText('Community tree of 2')).toBeInTheDocument();
});
