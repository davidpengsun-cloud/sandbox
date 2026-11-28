import { expect, test, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { Provider } from 'react-redux';
import { configureStore } from '@reduxjs/toolkit';
import searchReducer from '../../features/search/searchSlice';
import userReducer from '../../features/user/userSlice';
import MultiSelectMenu from './MultiSelectMenu';

vi.mock('../../services/search', () => ({ filterSearchResults: vi.fn() }));

const sales = { value: 'Sales', label: 'Sales', parent: 'DOMAIN' };
const stock = { value: 'Stock', label: 'Stock', parent: 'DOMAIN' };
const retail = { value: 'Retail', label: 'Retail', parent: 'APPLICATIONNAME' };

const setup = (preselected = []) => {
  const store = configureStore({
    reducer: { search: searchReducer, user: userReducer },
    preloadedState: { search: { ...searchReducer(undefined, { type: 'x' }), filterOptions: preselected } },
  });
  const data = [
    { key: 'DOMAIN', menu: 'Domain', expanded: false, submenu: [sales, stock], selectedSubmenu: [] },
    { key: 'APPLICATIONNAME', menu: 'Community', expanded: false, submenu: [retail], selectedSubmenu: [] },
  ];
  render(<Provider store={store}><MultiSelectMenu data={data} open={true} /></Provider>);
  return { store };
};

test('lists the filter groups closed, and opening one shows its options', () => {
  setup();
  expect(screen.getByText('Domain')).toBeInTheDocument();
  expect(screen.getByText('Community')).toBeInTheDocument();
  expect(screen.queryByText('Sales')).toBeNull();
  fireEvent.click(screen.getByText('Domain'));
  expect(screen.getByText('Sales')).toBeInTheDocument();
  expect(screen.getByText('Stock')).toBeInTheDocument();
});

test('opening another group closes the first', async () => {
  setup();
  fireEvent.click(screen.getByText('Domain'));
  fireEvent.click(screen.getByText('Community'));
  expect(screen.getByText('Retail')).toBeInTheDocument();
  await waitFor(() => expect(screen.queryByText('Sales')).toBeNull());
});

test('choosing an option stores it as a filter', () => {
  const { store } = setup();
  fireEvent.click(screen.getByText('Domain'));
  fireEvent.click(screen.getByText('Stock'));
  expect(store.getState().search.filterOptions).toEqual([stock]);
});

test('filters already applied show as chosen', () => {
  setup([sales]);
  fireEvent.click(screen.getByText('Domain'));
  expect(screen.getByText('Sales').closest('.select__multi-value')).not.toBeNull();
});
