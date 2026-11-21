import { expect, test } from 'vitest';
import { render, screen } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import Protected from './Protected';

const at = (isLoggedIn) => render(
  <MemoryRouter initialEntries={['/private']}>
    <Routes>
      <Route path="/" element={<p>Sign-in page</p>} />
      <Route path="/private" element={<Protected isLoggedIn={isLoggedIn}><p>Private page</p></Protected>} />
    </Routes>
  </MemoryRouter>,
);

test('shows its children when signed in', () => {
  at(true);
  expect(screen.getByText('Private page')).toBeInTheDocument();
});

test('sends a signed-out person to sign in', () => {
  at(false);
  expect(screen.getByText('Sign-in page')).toBeInTheDocument();
});
