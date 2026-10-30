import { expect, test, vi } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import AdminAccess from './AdminAccess';
import Login from '../Login';

vi.mock('../../components/shared/Header/Header', () => ({ default: () => <header>Header</header> }));
vi.mock('../../components/PublishedStatus/PublilshedStatus', () => ({ default: () => <p>Published status table</p> }));
vi.mock('../../components/RequestQueue/RequestQueue', () => ({ default: () => <p>Request queue</p> }));
vi.mock('../../components/LoginForm', () => ({ default: () => <form aria-label="sign-in form" /> }));

const renderAdmin = () => render(
  <MemoryRouter initialEntries={['/adminaccess']}>
    <Routes>
      <Route path="/adminaccess" element={<AdminAccess />} />
      <Route path="/home" element={<p>Home page</p>} />
    </Routes>
  </MemoryRouter>,
);

test('the admin console opens on Published Status and switches tabs', () => {
  renderAdmin();
  expect(screen.getByRole('heading', { name: 'Data Discovery Portal configuration' })).toBeInTheDocument();
  expect(screen.getByRole('tab', { name: 'Published Status' })).toHaveAttribute('aria-selected', 'true');
  expect(screen.getByText('Published status table')).toBeInTheDocument();

  fireEvent.click(screen.getByRole('tab', { name: 'Access Requests' }));
  expect(screen.getByText('Request queue')).toBeInTheDocument();
  expect(screen.queryByText('Published status table')).toBeNull();

  fireEvent.click(screen.getByRole('tab', { name: 'User Management' }));
  expect(screen.getByText(/User management is read-only here/)).toBeInTheDocument();

  fireEvent.click(screen.getByRole('tab', { name: 'Configuration' }));
  expect(screen.getByText(/Configuration editing isn't available yet/)).toBeInTheDocument();
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
