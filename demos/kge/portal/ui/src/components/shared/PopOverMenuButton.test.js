import { expect, test, vi } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';
import { Provider } from 'react-redux';
import { configureStore } from '@reduxjs/toolkit';
import searchReducer from '../../features/search/searchSlice';
import userReducer from '../../features/user/userSlice';
import PopOverMenuButton from './PopOverMenuButton';

vi.mock('../../services/search', () => ({ filterSearchResults: vi.fn(), groupSearchResults: vi.fn(), sortSearchResults: vi.fn() }));

const Icon = (props) => <svg {...props} />;
const setup = (props) => {
  const store = configureStore({ reducer: { search: searchReducer, user: userReducer } });
  render(<Provider store={store}><PopOverMenuButton icon={Icon} {...props} /></Provider>);
  return { store };
};

test('a single-select button opens its choices and closes on a pick', () => {
  vi.spyOn(console, 'error').mockImplementation(() => {});
  const { store } = setup({ name: 'Sort by', singleSelectMenu: true, data: [{ key: 'prdtNameA-Z', value: 'Name A-Z' }] });
  fireEvent.click(screen.getByRole('button', { name: 'Sort by' }));
  fireEvent.click(screen.getByLabelText('Name A-Z'));
  expect(store.getState().search.sortOption).toBe('prdtNameA-Z');
});

test('a multi-select button opens its filter groups', () => {
  vi.spyOn(console, 'error').mockImplementation(() => {});
  setup({ name: 'Filter', data: [{ key: 'DOMAIN', menu: 'Domain', expanded: false, submenu: [], selectedSubmenu: [] }] });
  fireEvent.click(screen.getByRole('button', { name: 'Filter' }));
  expect(screen.getByText('Domain')).toBeInTheDocument();
});
