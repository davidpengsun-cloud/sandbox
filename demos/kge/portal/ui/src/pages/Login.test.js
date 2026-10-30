import { afterEach, expect, test, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import Login from './Login';

vi.mock('react-redux', () => ({ useDispatch: () => vi.fn() }));
afterEach(() => vi.unstubAllGlobals());

const renderLogin = () => render(<MemoryRouter><Login /></MemoryRouter>);

test('sign-in shows the initial tile and the name when no logo is configured', async () => {
  renderLogin();
  expect(screen.getByRole('heading', { name: 'Sign in' })).toBeInTheDocument();
  expect(screen.getByText('Data Discovery Portal')).toBeInTheDocument();
  expect(screen.getByText('D')).toHaveAttribute('data-brand', 'tile');
});

test('sign-in shows the configured logo beside the name', async () => {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: true, json: () => Promise.resolve({ name: 'Data Discovery Portal', logo: true, kge_url: null }) }));
  const { container } = renderLogin();
  await vi.waitFor(() => expect(container.querySelector('img[data-brand="logo"]')).toHaveAttribute('src', '/brand/logo'));
  expect(screen.getByText('Data Discovery Portal')).toBeInTheDocument();
  expect(fetch).toHaveBeenCalledWith('/brand', { credentials: 'same-origin' });
});
