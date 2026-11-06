import { expect, test, vi } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import AdminAccess from './AdminAccess';
import Login from '../Login';
import { Provider } from 'react-redux';
import { configureStore } from '@reduxjs/toolkit';
import user from '../../features/user/userSlice';

vi.mock('../../components/shared/Header/Header', () => ({ default: () => <header>Header</header> }));
vi.mock('../../components/PublishedStatus/PublilshedStatus', () => ({ default: () => <p>Published status table</p> }));
vi.mock('../../components/RequestQueue/RequestQueue', () => ({ default: () => <p>Request queue</p> }));
vi.mock('../../components/LoginForm', () => ({ default: () => <form aria-label="sign-in form" /> }));
vi.mock('./UserManagement', () => ({ default: () => <p>Portal role management</p> }));

const renderAdmin = (roles = ['admin']) => render(
  <Provider store={configureStore({ reducer: { user }, preloadedState: { user: { data: { userRoles: roles } } } })}>
  <MemoryRouter initialEntries={['/adminaccess']}>
    <Routes>
      <Route path="/adminaccess" element={<AdminAccess />} />
      <Route path="/home" element={<p>Home page</p>} />
    </Routes>
  </MemoryRouter>
  </Provider>
);

test('the admin console opens on Published Status and switches tabs', () => {
  renderAdmin();
  expect(screen.getByRole('heading', { name: 'ALTA: AI-powered Library for Trusted Assets configuration' })).toBeInTheDocument();
  expect(screen.getByRole('tab', { name: 'Published Status' })).toHaveAttribute('aria-selected', 'true');
  expect(screen.getByText('Published status table')).toBeInTheDocument();

  fireEvent.click(screen.getByRole('tab', { name: 'Access Requests' }));
  expect(screen.getByText('Request queue')).toBeInTheDocument();
  expect(screen.queryByText('Published status table')).toBeNull();

  fireEvent.click(screen.getByRole('tab', { name: 'User Management' }));
  expect(screen.getByText('Portal role management')).toBeInTheDocument();

  fireEvent.click(screen.getByRole('tab', { name: 'Configuration' }));
  expect(screen.getByText(/Configuration editing isn't available yet/)).toBeInTheDocument();
});

test.each(['viewer', 'publisher', 'approver'])('%s cannot open portal user management', (role) => {
  renderAdmin([role]);
  expect(screen.queryByRole('tab', { name: 'User Management' })).toBeNull();
  expect(screen.queryByText('Portal role management')).toBeNull();
});

test.each(['HOME', 'Back to home'])('the admin console goes home from "%s"', (name) => {
  renderAdmin();
  fireEvent.click(screen.getByRole('button', { name }));
  expect(screen.getByText('Home page')).toBeInTheDocument();
});

test('the sign-in page titles itself and holds the form', () => {
  render(<Login />);
  expect(screen.getByRole('heading', { name: 'Sign in' })).toBeInTheDocument();
  expect(screen.getByRole('form', { name: 'sign-in form' })).toBeInTheDocument();
});
