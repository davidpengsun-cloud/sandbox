import { afterEach, beforeEach, expect, test, vi } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';
import { Provider } from 'react-redux';
import { configureStore } from '@reduxjs/toolkit';
import dataProductReducer from '../../features/dataProduct/dataProductSlice';
import UserDetails from './UserDetails';
import { getUserData } from '../../services/admin';

vi.mock('../../services/admin', () => ({ getUserData: vi.fn() }));

const setup = () => {
  const store = configureStore({ reducer: { dataProduct: dataProductReducer } });
  return render(<Provider store={store}><UserDetails /></Provider>);
};

beforeEach(() => vi.clearAllMocks());
afterEach(() => vi.restoreAllMocks());

test('lists users with their roles', async () => {
  getUserData.mockResolvedValue({ data: [{ userName: 'Ana', userId: 'u1', role: 'User' }, { userName: 'Ben', userId: 'u2', role: 'Admin' }] });
  setup();
  expect(screen.getByText('User Role')).toBeInTheDocument();
  expect(await screen.findByText('Ana')).toBeInTheDocument();
  expect(screen.getByText('Ben')).toBeInTheDocument();
  expect(screen.getByText('u2')).toBeInTheDocument();
});

// One user only: every row's radio group shares one name, so with two rows the
// browser unchecks across rows (a product defect, noted in the coverage report).
test('a role can be changed', async () => {
  vi.spyOn(console, 'log').mockImplementation(() => {});
  getUserData.mockResolvedValue({ data: [{ userName: 'Ana', userId: 'u1', role: 'User' }] });
  setup();
  await screen.findByText('Ana');
  expect(screen.getByLabelText('User')).toBeChecked();
  fireEvent.click(screen.getByLabelText('Super Admin'));
  expect(screen.getByLabelText('Super Admin')).toBeChecked();
  expect(screen.getByLabelText('User')).not.toBeChecked();
});

test.each([
  ['no data', () => getUserData.mockResolvedValue({})],
  ['an empty list', () => getUserData.mockResolvedValue({ data: [] })],
  ['a failure', () => getUserData.mockRejectedValue(new Error('down'))],
])('%s says no records were found', async (_, arrange) => {
  arrange();
  setup();
  expect(screen.getByText('Loading...')).toBeInTheDocument();
  expect(await screen.findByText('No records found')).toBeInTheDocument();
});
