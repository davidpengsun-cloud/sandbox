import { beforeEach, expect, test, vi } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import LoginForm from './LoginForm';
import { login } from '../services/login';

const TYPED = ['invented', 'sign-in', 'text'].join('-');
vi.mock('../services/login', () => ({ login: vi.fn() }));
vi.mock('react-redux', () => ({ useDispatch: () => vi.fn() }));
beforeEach(() => vi.clearAllMocks());

test.each([
  [401, 'wrong username or password'],
  [429, 'too many sign-in attempts: try again in 30 s'],
  [502, 'KGE is unavailable.'],
  [null, 'The portal could not be reached. Check your connection and try again.'],
])('sign-in reports %s without losing labelled fields', async (status, reason) => {
  login.mockRejectedValue(status ? { response: { status, data: { detail: reason } } } : new Error('offline'));
  render(<MemoryRouter><LoginForm /></MemoryRouter>);
  fireEvent.change(screen.getByLabelText('Username'), { target: { value: 'analyst_retail' } });
  fireEvent.change(screen.getByLabelText('Password'), { target: { value: TYPED } });
  fireEvent.click(screen.getByRole('button', { name: 'Sign in' }));
  expect(await screen.findByRole('alert')).toHaveTextContent(reason);
  expect(login).toHaveBeenCalledWith({ email: 'analyst_retail', password: TYPED });
  expect(screen.getByRole('button', { name: 'Sign in' })).toBeEnabled();
});
