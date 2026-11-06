import { beforeEach, expect, test, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { Provider } from 'react-redux';
import { configureStore } from '@reduxjs/toolkit';
import dataProductReducer from '../../features/dataProduct/dataProductSlice';
import TextFilter from './TextFilter';
import { getFilteredFields } from '../../services/metadata';

vi.mock('../../services/metadata', () => ({ getFilteredFields: vi.fn() }));

const setup = () => {
  const store = configureStore({ reducer: { dataProduct: dataProductReducer } });
  const view = render(<Provider store={store}><TextFilter field="NAME" productId="retail.orders" /></Provider>);
  fireEvent.click(view.container.querySelector('svg'));
  return { store, ...view };
};

beforeEach(() => {
  vi.clearAllMocks();
  getFilteredFields.mockResolvedValue({ data: [{ NAME: 'order_id' }] });
});

test('typing a value and pressing Enter filters the fields', async () => {
  const { store } = setup();
  const input = screen.getByPlaceholderText('Search');
  fireEvent.keyUp(input, { key: 'd', target: { value: 'ord' } });
  fireEvent.keyUp(input, { key: 'Enter', target: { value: 'ord' } });
  expect(getFilteredFields).toHaveBeenCalledWith({ field: 'NAME', value: 'ord', productId: 'retail.orders' });
  await waitFor(() => expect(store.getState().dataProduct.fieldsDataList).toEqual([{ NAME: 'order_id' }]));
  await waitFor(() => expect(screen.queryByPlaceholderText('Search')).toBeNull());
});

test('choosing an action filters with it', async () => {
  const { store } = setup();
  fireEvent.keyUp(screen.getByPlaceholderText('Search'), { key: 'd', target: { value: 'ord' } });
  fireEvent.click(document.querySelectorAll('svg')[1]);
  fireEvent.click(await screen.findByText('Contains'));
  expect(getFilteredFields).toHaveBeenCalledWith({ field: 'NAME', value: 'ord', action: 'Contains', productId: 'retail.orders' });
  await waitFor(() => expect(store.getState().dataProduct.fieldsDataList).toEqual([{ NAME: 'order_id' }]));
});

test('an action with nothing typed does not filter', async () => {
  setup();
  fireEvent.click(document.querySelectorAll('svg')[1]);
  fireEvent.click(await screen.findByText('Equals'));
  expect(getFilteredFields).not.toHaveBeenCalled();
});

test('closing the action list closes the filter', async () => {
  setup();
  fireEvent.click(document.querySelectorAll('svg')[1]);
  await screen.findByText('Equals');
  fireEvent.keyDown(screen.getByText('Equals'), { key: 'Escape' });
  await waitFor(() => expect(screen.queryByPlaceholderText('Search')).toBeNull());
});
