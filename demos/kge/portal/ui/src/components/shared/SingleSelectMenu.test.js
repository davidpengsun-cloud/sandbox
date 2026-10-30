import { expect, test, vi } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';
import { Provider } from 'react-redux';
import { configureStore } from '@reduxjs/toolkit';
import searchReducer from '../../features/search/searchSlice';
import SingleSelectMenu from './SingleSelectMenu';

vi.mock('../../services/search', () => ({ groupSearchResults: vi.fn(), sortSearchResults: vi.fn() }));

const sorts = [
  { key: 'prdtNameA-Z', value: 'Name A-Z' },
  { key: 'prdtNameZ-A', value: 'Name Z-A' },
  { key: 'publishDateNew-Old', value: 'Newest' },
  { key: 'publishDateOld-New', value: 'Oldest' },
];
const groups = [
  { key: 'COMMUNITY', value: 'Community' },
  { key: 'DOMAIN', value: 'Domain' },
  { key: 'PRODUCTTYPE', value: 'Type' },
];

const setup = (action, data) => {
  const store = configureStore({ reducer: { search: searchReducer } });
  const close = vi.fn();
  render(<Provider store={store}><SingleSelectMenu action={action} data={data} popupState={{ close }} /></Provider>);
  return { store, close };
};

test.each(sorts.map((s) => [s.value, s.key]))('sorting by %s stores the choice and closes', (label, key) => {
  const { store, close } = setup('Sort by', sorts);
  fireEvent.click(screen.getByLabelText(label));
  expect(store.getState().search.sortOption).toBe(key);
  expect(close).toHaveBeenCalledTimes(1);
});

test.each(groups.map((g) => [g.value, g.key]))('grouping by %s stores the choice and closes', (label, key) => {
  const { store, close } = setup('Group by', groups);
  fireEvent.click(screen.getByLabelText(label));
  expect(store.getState().search.groupOption).toBe(key);
  expect(close).toHaveBeenCalledTimes(1);
});

test('an unrelated action changes nothing', () => {
  const { store, close } = setup('Other', groups);
  fireEvent.click(screen.getByLabelText('Domain'));
  expect(store.getState().search.groupOption).toEqual([]);
  expect(close).not.toHaveBeenCalled();
});
