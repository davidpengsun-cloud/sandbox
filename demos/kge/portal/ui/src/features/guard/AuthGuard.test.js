import { expect, test, vi } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';
import { Provider } from 'react-redux';
import { configureStore } from '@reduxjs/toolkit';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import user from '../user/userSlice';
import ProtectedRoute from './AuthGuard';

const at = (role, path = '/admin') => {
  const store = configureStore({ reducer: { user }, preloadedState: { user: { isLoggedIn: true, data: { userRole: role } } } });
  return render(
    <Provider store={store}>
      <MemoryRouter initialEntries={[path]}>
        <Routes>
          <Route path="/" element={<p>Sign-in page</p>} />
          <Route path="/home" element={<p>Home page</p>} />
          <Route path="/admin" element={<ProtectedRoute><p>Admin page</p></ProtectedRoute>} />
        </Routes>
      </MemoryRouter>
    </Provider>,
  );
};

test('an admin sees the guarded page', () => {
  at('admin');
  expect(screen.getByText('Admin page')).toBeInTheDocument();
});

test('anyone else is refused and can go back to the start', () => {
  vi.spyOn(console, 'log').mockImplementation(() => {});
  vi.spyOn(console, 'warn').mockImplementation(() => {});
  at('viewer');
  expect(screen.queryByText('Admin page')).toBeNull();
  expect(screen.getByText('No Access!')).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: 'Back to Home' }));
  expect(screen.getByText('Sign-in page')).toBeInTheDocument();
});
