import { afterEach, beforeEach, expect, test, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import { Provider } from 'react-redux';
import { configureStore } from '@reduxjs/toolkit';
import { MemoryRouter } from 'react-router-dom';
import user from '../../features/user/userSlice';
import Certify from './Certify';
import { configOf, stubPhone, stubShell } from '../../components/shell/testSupport';

function renderCertify() {
  render(
    <Provider store={configureStore({ reducer: { user }, preloadedState: { user: { isLoggedIn: true, data: { userName: 'Cara Creator' } } } })}>
      <MemoryRouter initialEntries={['/certify']}><Certify /></MemoryRouter>
    </Provider>,
  );
}

beforeEach(() => stubPhone(false));
afterEach(() => vi.unstubAllGlobals());

test('the Certify stub says what it is for, who it is for, and lists only certification and sign-off', async () => {
  stubShell();
  renderCertify();
  expect(await screen.findByRole('heading', { name: 'Certify: document-level certification' })).toBeInTheDocument();
  const page = screen.getByRole('main');
  expect(page).toHaveTextContent('content creators, data owners and stewards');
  expect([...page.querySelectorAll('li')].map((li) => li.textContent)).toEqual(['Certify documents', 'Sign off certifications']);
  expect(page).not.toHaveTextContent(/KGE|onboard|draft/i);
  expect(await screen.findByText('[Power App link]')).toBeInTheDocument();
  expect(screen.queryByRole('link', { name: /Open Certify/ })).toBeNull();
});

test('with the Certify setting, the stub links out, marked as external', async () => {
  stubShell({ config: configOf({ certifyUrl: 'https://certify.example.invalid/app' }) });
  renderCertify();
  const link = await screen.findByRole('link', { name: /Open Certify \(opens outside the platform\)/ });
  expect(link).toHaveAttribute('href', 'https://certify.example.invalid/app');
  expect(link).toHaveAttribute('target', '_blank');
  expect(screen.queryByText('[Power App link]')).toBeNull();
});
