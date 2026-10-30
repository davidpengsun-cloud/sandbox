import { expect, test } from 'vitest';
import { render, screen } from '@testing-library/react';
import { Provider } from 'react-redux';
import { MemoryRouter } from 'react-router-dom';
import App from './App';
import store from './app/store';

test('renders the sign-in page', () => {
  render(<Provider store={store}><MemoryRouter><App /></MemoryRouter></Provider>);
  expect(screen.getByRole('heading', { name: 'Sign in' })).toBeInTheDocument();
  expect(screen.getByRole('button', { name: 'Sign in' })).toBeInTheDocument();
  expect(screen.queryByText(/login/i)).not.toBeInTheDocument();
  expect(screen.queryByText(/preview|export|copy query/i)).not.toBeInTheDocument();
});
