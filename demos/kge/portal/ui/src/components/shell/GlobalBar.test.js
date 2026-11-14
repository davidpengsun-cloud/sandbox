import { afterEach, beforeEach, expect, test, vi } from 'vitest';
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { Provider } from 'react-redux';
import { configureStore } from '@reduxjs/toolkit';
import { MemoryRouter } from 'react-router-dom';
import user from '../../features/user/userSlice';
import GlobalBar from './GlobalBar';
import { loadConfig } from '../../services/config';
import { configOf, stubPhone, stubShell } from './testSupport';

const PERSON = { userId: 'sam.steward@example.com', userName: 'Sam Steward', userRole: 'admin', userRoles: ['admin'], contextRoles: ['steward'] };

function renderBar({ app, path = '/', person = PERSON, contextPerson } = {}) {
  const store = configureStore({ reducer: { user }, preloadedState: { user: { isLoggedIn: true, data: person } } });
  return render(
    <Provider store={store}>
      <MemoryRouter initialEntries={[path]}>
        <GlobalBar app={app} contextPerson={contextPerson} />
      </MemoryRouter>
    </Provider>,
  );
}

const switcherButton = (current) => screen.findByRole('button', { name: `Switch app, current: ${current}` });
const appsPanel = () => screen.getByRole('navigation', { name: 'Apps' });
const rows = () => [...appsPanel().querySelectorAll('li > *')];

beforeEach(() => stubPhone(false));
afterEach(() => vi.unstubAllGlobals());

test('the bar holds, left to right: the product name to home, the current app, the switcher, then theme and account', async () => {
  stubShell();
  renderBar({ app: 'knowledge' });
  const bar = screen.getByRole('banner');
  await switcherButton('ALTA home');
  const shown = (el) => [...el.childNodes].filter((node) => node.nodeType === 3 || node.getAttribute('aria-hidden') !== 'true').map((node) => node.textContent).join('').trim();
  const controls = [...bar.querySelectorAll('a, button')].map((el) => el.getAttribute('aria-label') || shown(el));
  expect(controls).toEqual(['Meridian ALTA', 'ALTA home', 'Switch app, current: ALTA home', 'Theme: System', 'Account: Sam Steward']);
  expect(within(bar).getByRole('link', { name: 'Meridian ALTA' })).toHaveAttribute('href', '/');
});

test("the fallback brand link shows the install's name", async () => {
  stubShell({ config: configOf({ productName: 'Meridian ALTA' }) });
  renderBar({ app: 'discover' });
  const brand = await screen.findByRole('link', { name: 'Meridian ALTA' });
  expect(brand).toHaveAttribute('href', '/');
  expect(brand).toHaveTextContent('Meridian ALTA');
});

test('the home entry and Discover take the configured product short name', async () => {
  stubShell({ config: configOf({ productName: 'Meridian Beacon', productShortName: 'Beacon' }) });
  renderBar({ app: 'discover' });
  const button = await switcherButton('Beacon');
  fireEvent.click(button);
  expect(rows().map((row) => row.querySelector('.gb-label').textContent)).toEqual(['Beacon home', 'Certify', 'Context', 'Discover']);
  expect(screen.getByRole('banner')).not.toHaveTextContent(/ALTA/);
});

test("the switcher lists the ALTA home and the apps in the doors' order, with the current one marked", async () => {
  stubShell({ config: configOf({ certifyUrl: 'https://certify.example.invalid/app' }) });
  renderBar({ app: 'context' });
  const button = await switcherButton('Context');
  expect(button).toHaveTextContent('Context');
  expect(button).toHaveAttribute('aria-expanded', 'false');
  fireEvent.click(button);
  expect(button).toHaveAttribute('aria-expanded', 'true');
  expect(rows().map((row) => row.querySelector('.gb-label').textContent)).toEqual(['ALTA home', 'Certify', 'Context', 'Discover']);
  expect(within(appsPanel()).getByRole('link', { name: 'Context' })).toHaveAttribute('aria-current', 'page');
  expect(appsPanel().querySelectorAll('[aria-current]')).toHaveLength(1);
  expect(within(appsPanel()).getByRole('link', { name: 'ALTA home' })).toHaveAttribute('href', '/');
  expect(within(appsPanel()).getByRole('link', { name: 'Discover' })).toHaveAttribute('href', '/home');
});

test('Certify opens outside the platform, marked as external, from the Certify setting', async () => {
  stubShell({ config: configOf({ certifyUrl: 'https://certify.example.invalid/app' }) });
  renderBar({ app: 'discover' });
  fireEvent.click(await switcherButton('ALTA'));
  const certify = within(appsPanel()).getByRole('link', { name: 'Certify (opens outside the platform)' });
  expect(certify).toHaveAttribute('href', 'https://certify.example.invalid/app');
  expect(certify).toHaveAttribute('target', '_blank');
  expect(certify).toHaveAttribute('rel', 'noopener noreferrer');
  expect(certify.querySelector('.gb-out')).not.toBeNull();
});

test('until the Certify setting is set, the switcher shows the placeholder, not a link', async () => {
  stubShell();
  renderBar({ app: 'discover' });
  fireEvent.click(await switcherButton('ALTA'));
  expect(within(appsPanel()).queryByRole('link', { name: /Certify/ })).toBeNull();
  expect(appsPanel()).toHaveTextContent('Certify[Power App link]');
});

const COMBOS = [true, false].flatMap((certify) => [true, false].flatMap((context) => [true, false].map((discover) => ({ certify, context, discover }))));

test.each(COMBOS)('with the ALTA home on and apps %o, an app switched off shows disabled as not set up here', async (apps) => {
  stubShell({ config: configOf({ apps, certifyUrl: 'https://certify.example.invalid/' }) });
  renderBar({ app: 'knowledge' });
  fireEvent.click(await switcherButton('ALTA home'));
  expect(within(appsPanel()).getByRole('link', { name: 'ALTA home' })).toHaveAttribute('aria-current', 'page');
  for (const [id, label] of [['certify', 'Certify'], ['context', 'Context'], ['discover', 'Discover']]) {
    const row = rows().find((el) => el.querySelector('.gb-label').textContent === label);
    if (apps[id]) {
      expect(row.tagName).toBe('A');
      expect(row).not.toHaveAttribute('aria-disabled');
      expect(row).not.toHaveTextContent('not set up here');
    } else {
      expect(row.tagName).toBe('SPAN');
      expect(row).toHaveAttribute('aria-disabled', 'true');
      expect(row).toHaveTextContent(`${label}not set up here`);
    }
  }
  expect(rows()).toHaveLength(4);
});

test('nothing is hidden by role: a person with no roles at all sees every app in the switcher', async () => {
  stubShell();
  renderBar({ app: 'knowledge', person: { userName: 'Rae Reader', userRoles: ['viewer'], contextRoles: [] } });
  fireEvent.click(await switcherButton('ALTA home'));
  expect(within(appsPanel()).getByRole('link', { name: 'Context' })).toBeInTheDocument();
  expect(within(appsPanel()).getByRole('link', { name: 'Discover' })).toBeInTheDocument();
});

test('the switcher opens with focus inside, closes on Escape with focus back on its button, and on a click outside', async () => {
  stubShell();
  renderBar({ app: 'knowledge' });
  const button = await switcherButton('ALTA home');
  fireEvent.click(button);
  expect(appsPanel()).toHaveFocus();
  expect(button).toHaveAttribute('aria-controls', appsPanel().id);
  for (const link of within(appsPanel()).getAllByRole('link')) expect(link.tabIndex).toBe(0);
  fireEvent.keyDown(document, { key: 'Escape' });
  expect(screen.queryByRole('navigation', { name: 'Apps' })).toBeNull();
  expect(button).toHaveFocus();
  fireEvent.click(button);
  fireEvent.mouseDown(appsPanel());
  expect(appsPanel()).toBeInTheDocument();
  fireEvent.mouseDown(document.body);
  expect(screen.queryByRole('navigation', { name: 'Apps' })).toBeNull();
});

test('every control in the bar is a native button or link with an accessible name', async () => {
  stubShell();
  renderBar({ app: 'context' });
  await switcherButton('Context');
  for (const el of screen.getByRole('banner').querySelectorAll('a, button')) {
    expect(el.getAttribute('aria-label') || el.textContent.trim()).not.toBe('');
    expect(el.tabIndex).toBe(0);
  }
});

test.each(['knowledge', 'certify', 'discover'].flatMap((app) =>
  [false, true].flatMap((phone) => [false, true].map((knowledgeHome) => ({ app, phone, knowledgeHome }))),
))('the bar has no Docs entry for %o', async ({ app, phone, knowledgeHome }) => {
  stubPhone(phone);
  stubShell({ config: configOf({ knowledgeHome }) });
  renderBar({ app });
  await screen.findByRole('button', { name: 'Account: Sam Steward' });
  const bar = screen.getByRole('banner');
  if (knowledgeHome) {
    fireEvent.click(await screen.findByRole('button', { name: phone ? 'Menu' : /Switch app/ }));
    expect(screen.getByRole('navigation', { name: phone ? 'Menu' : 'Apps' })).toBeInTheDocument();
  } else {
    await within(bar).findByText({ knowledge: 'ALTA home', context: 'Context', certify: 'Certify', discover: 'ALTA' }[app]);
    expect(within(bar).queryByRole('button', { name: 'Menu' })).toBeNull();
  }
  expect(bar).not.toHaveTextContent(/docs/i);
  expect(within(bar).queryByRole('button', { name: /docs/i })).toBeNull();
  expect(bar.querySelector('a[href*="docs"], a[href*="onboarding-guide"]')).toBeNull();
});

test.each([false, true].flatMap((phone) => [false, true].map((knowledgeHome) => ({ phone, knowledgeHome }))))(
  'Context opens its own Docs from the global bar for %o', async ({ phone, knowledgeHome }) => {
    stubPhone(phone);
    stubShell({ config: configOf({ knowledgeHome }) });
    renderBar({ app: 'context' });
    const docs = await within(screen.getByRole('banner')).findByRole('link', { name: 'Context docs (opens in a new tab)' });
    expect(docs).toHaveAttribute('href', '/api/context/onboarding-guide');
    expect(docs).toHaveAttribute('target', '_blank');
    expect(docs).toHaveAttribute('rel', 'noopener');
    expect(docs.tabIndex).toBe(0);
  },
);

test('Context Docs is absent when the Context service is not configured', async () => {
  stubShell({ config: configOf({ apps: { context: false } }) });
  renderBar({ app: 'context' });
  await screen.findByRole('button', { name: 'Account: Sam Steward' });
  expect(within(screen.getByRole('banner')).queryByRole('link', { name: /Context docs/ })).toBeNull();
});

test('with the ALTA home off, the bar links the brand and current app without a switcher', async () => {
  stubShell({ config: configOf({ knowledgeHome: false, apps: { certify: false, context: false, discover: true } }) });
  await loadConfig();
  renderBar({ path: '/Search' });
  const bar = screen.getByRole('banner');
  expect(await within(bar).findByText('ALTA')).toBeInTheDocument();
  expect(within(bar).getByRole('link', { name: 'Meridian ALTA' })).toHaveAttribute('href', '/');
  expect(within(bar).getByRole('link', { name: 'ALTA' })).toHaveAttribute('href', '/home');
  expect(within(bar).queryByRole('button', { name: /Switch app/ })).toBeNull();
});

test.each(['knowledge', 'context', 'certify', 'discover'])('the %s account carries sign-out and no app-specific Admin action, even for an admin', async (app) => {
  stubShell();
  renderBar({ app });
  await screen.findByRole('button', { name: /Switch app/ });
  fireEvent.click(screen.getByRole('button', { name: 'Account: Sam Steward' }));
  const account = screen.getByRole('dialog', { name: 'Account' });
  expect(account).toHaveTextContent('Signed in as');
  expect(within(account).getByText('Sam Steward')).toBeInTheDocument();
  expect(account).toHaveTextContent('ADMIN');
  expect(within(account).getByRole('button', { name: 'Sign out' })).toBeInTheDocument();
  expect(within(account).queryByRole('button', { name: 'Admin' })).toBeNull();
});

test('the app menu follows the bar width as the Ask panel opens and closes', async () => {
  let resize;
  vi.stubGlobal('ResizeObserver', class {
    constructor(callback) { resize = callback; }
    observe() {}
    disconnect() {}
  });
  stubShell();
  renderBar({ app: 'discover' });
  fireEvent.click(await switcherButton('ALTA'));
  expect(screen.getByRole('navigation', { name: 'Apps' })).toBeInTheDocument();
  act(() => resize([{ contentRect: { width: 500 } }]));
  expect(screen.getByRole('button', { name: 'Menu' })).toBeInTheDocument();
  expect(screen.getByRole('button', { name: 'Menu' })).toHaveFocus();
  expect(screen.queryByRole('button', { name: /Switch app/ })).toBeNull();
  fireEvent.click(screen.getByRole('button', { name: 'Menu' }));
  expect(screen.getByRole('navigation', { name: 'Menu' })).toBeInTheDocument();
  act(() => resize([{ contentRect: { width: 900 } }]));
  expect(await switcherButton('ALTA')).toHaveAttribute('aria-expanded', 'false');
  expect(await switcherButton('ALTA')).toHaveFocus();
  expect(screen.queryByRole('navigation', { name: 'Apps' })).toBeNull();
  expect(screen.queryByRole('button', { name: 'Menu' })).toBeNull();
  act(() => resize([{ contentRect: { width: 500 } }]));
  expect(screen.getByRole('button', { name: 'Menu' })).toHaveAttribute('aria-expanded', 'false');
  expect(screen.queryByRole('navigation', { name: 'Menu' })).toBeNull();
  const theme = screen.getByRole('button', { name: /Theme:/ });
  theme.focus();
  act(() => resize([{ contentRect: { width: 900 } }]));
  expect(theme).toHaveFocus();
});

test('the phone menu holds the switcher and closes on Escape', async () => {
  stubPhone(true);
  stubShell({ config: configOf({ certifyUrl: 'https://certify.example.invalid/' }) });
  renderBar({ app: 'context' });
  const menuButton = await screen.findByRole('button', { name: 'Menu' });
  expect(screen.queryByRole('button', { name: 'Docs' })).toBeNull();
  expect(screen.queryByRole('button', { name: /Switch app/ })).toBeNull();
  fireEvent.click(menuButton);
  const menu = screen.getByRole('navigation', { name: 'Menu' });
  expect([...menu.querySelectorAll('.gb-group-title')].map((el) => el.textContent)).toEqual(['Apps']);
  expect(within(menu).getByRole('link', { name: 'Context' })).toHaveAttribute('aria-current', 'page');
  expect(within(menu).getByRole('link', { name: 'Certify (opens outside the platform)' })).toBeInTheDocument();
  fireEvent.keyDown(document, { key: 'Escape' });
  expect(screen.queryByRole('navigation', { name: 'Menu' })).toBeNull();
  expect(menuButton).toHaveFocus();
});

test('without matchMedia the bar takes the desktop layout', async () => {
  vi.stubGlobal('matchMedia', undefined);
  stubShell();
  renderBar({ app: 'knowledge' });
  expect(await switcherButton('ALTA home')).toBeInTheDocument();
});

test('the current app follows the path when no app is named', async () => {
  stubShell();
  renderBar({ path: '/CERTIFY' });
  expect(await switcherButton('Certify')).toBeInTheDocument();
});

test('focus may move inside a popover but leaving its wrapper closes it', async () => {
  stubShell();
  renderBar({ app: 'knowledge' });
  const button = await switcherButton('ALTA home');
  fireEvent.click(button);
  const panel = appsPanel();
  const link = within(panel).getByRole('link', { name: 'Discover' });
  fireEvent.focusOut(panel, { relatedTarget: link });
  expect(appsPanel()).toBeInTheDocument();
  fireEvent.focusOut(link, { relatedTarget: screen.getByRole('button', { name: 'Theme: System' }) });
  expect(screen.queryByRole('navigation', { name: 'Apps' })).toBeNull();
  expect(button).toHaveAttribute('aria-expanded', 'false');
});

test('unconfigured Context is disabled in the switcher', async () => {
  stubShell({ config: configOf({ contextMcpUrl: null }) });
  renderBar({ app: 'knowledge' });
  fireEvent.click(await switcherButton('ALTA home'));
  expect(within(appsPanel()).queryByRole('link', { name: 'Context' })).toBeNull();
  expect(rows().find((row) => row.textContent.includes('Context'))).toHaveAttribute('aria-disabled', 'true');
});

test('a Safari mouse focusout with no related target keeps the switcher usable', async () => {
  stubShell();
  renderBar({ app: 'context' });
  const button = await switcherButton('Context');
  fireEvent.click(button);
  const panel = appsPanel();
  const link = within(panel).getByRole('link', { name: 'Discover' });
  fireEvent.mouseDown(link);
  fireEvent.focusOut(panel, { relatedTarget: null });
  expect(button).toHaveAttribute('aria-expanded', 'true');
  expect(link).toBeInTheDocument();
  fireEvent.click(link);
  expect(button).toHaveAttribute('aria-expanded', 'false');
});


test.each([
  [{ role: 'knowledge_engineer', reads: ['retail'], processes: ['querying', 'building'], writes_terms: true }, ['knowledge_engineer', 'retail', 'ask questions, building, write terms and mappings']],
  [{}, ['none', 'none', 'nothing']],
])('the shared account preserves Context role, reads and permitted actions', async (contextPerson, expected) => {
  stubShell();
  renderBar({ app: 'context', contextPerson });
  fireEvent.click(await screen.findByRole('button', { name: 'Account: Sam Steward' }));
  const account = screen.getByRole('dialog', { name: 'Account' });
  expect([...account.querySelectorAll('.gb-context-rights dd')].map((node) => node.textContent)).toEqual(expected);
});

test('arrow keys wrap through actionable switcher items; Home and End reach the ends', async () => {
  stubShell({ config: configOf({ certifyUrl: 'https://example.com/certify' }) });
  renderBar({ app: 'context' });
  const trigger = await switcherButton('Context');
  fireEvent.click(trigger);
  const panel = appsPanel();
  const links = within(panel).getAllByRole('link');
  for (const link of links) {
    fireEvent.keyDown(document.activeElement, { key: 'ArrowDown' });
    expect(link).toHaveFocus();
  }
  fireEvent.keyDown(document.activeElement, { key: 'ArrowDown' });
  expect(links[0]).toHaveFocus();
  fireEvent.keyDown(document.activeElement, { key: 'ArrowUp' });
  expect(links.at(-1)).toHaveFocus();
  fireEvent.keyDown(document.activeElement, { key: 'Home' });
  expect(links[0]).toHaveFocus();
  fireEvent.keyDown(document.activeElement, { key: 'End' });
  expect(links.at(-1)).toHaveFocus();
  fireEvent.keyDown(document.activeElement, { key: 'Escape' });
  expect(trigger).toHaveFocus();
  expect(screen.queryByRole('navigation', { name: 'Apps' })).toBeNull();
});

test.each([false, true])('switcher arrows skip disabled apps and missing external links, phone=%s', async (phone) => {
  stubPhone(phone);
  stubShell({ config: configOf({ apps: { context: false }, certifyUrl: null }) });
  renderBar({ app: 'knowledge' });
  fireEvent.click(await screen.findByRole('button', { name: phone ? 'Menu' : /Switch app/ }));
  const panel = screen.getByRole('navigation', { name: phone ? 'Menu' : 'Apps' });
  const links = within(panel).getAllByRole('link');
  fireEvent.keyDown(panel, { key: 'ArrowDown' });
  expect(links[0]).toHaveTextContent('ALTA home');
  expect(links[0]).toHaveFocus();
  fireEvent.keyDown(document.activeElement, { key: 'ArrowDown' });
  expect(links[1]).toHaveTextContent('Discover');
  expect(links[1]).toHaveFocus();
  fireEvent.keyDown(document.activeElement, { key: 'ArrowUp' });
  expect(links[0]).toHaveFocus();
});


test.each([['knowledge', '/', 'ALTA home'], ['discover', '/home', 'ALTA'], ['context', '/context', 'Context'], ['certify', '/certify', 'Certify']])(
  '%s shows its home link after the brand and before the switcher', async (app, path, label) => {
    stubShell();
    renderBar({ app, path });
    await switcherButton(label);
    const bar = screen.getByRole('banner');
    const controls = [...bar.querySelectorAll('a, button')];
    expect(controls[0]).toHaveAccessibleName('Meridian ALTA');
    expect(controls[0]).toHaveAttribute('href', '/');
    expect(controls[1]).toHaveAccessibleName(label);
    expect(controls[1]).toHaveAttribute('href', path);
    expect(controls[1]).toHaveAttribute('aria-current', 'page');
    if (app === 'context') {
      expect(controls[2]).toHaveAccessibleName('Context docs (opens in a new tab)');
      expect(controls[2]).toHaveAttribute('href', '/api/context/onboarding-guide');
    }
    expect(controls[app === 'context' ? 3 : 2]).toHaveAccessibleName(`Switch app, current: ${label}`);
  },
);

test('sign-out clears all Ask ALTA scopes and Context history before another person signs in', async () => {
  window.sessionStorage.clear();
  window.localStorage.clear();
  const keys = ['askHistory:retail.monthly_revenue', 'askHistory:retail.account',
    'askHistory:domain:Retail Banking', 'askHistory:domain:credit', 'askHistory:domain:All data assets'];
  for (const key of keys) window.sessionStorage.setItem(key, JSON.stringify([{ text: 'Previous conversation', isBot: false }]));
  window.sessionStorage.setItem('other-feature', 'keep');
  const personKeys = ['contextAsk:person:sam@example.com', 'contextAsk:person:earlier@example.com'];
  for (const key of personKeys) window.localStorage.setItem(key, JSON.stringify({ conversations: [{ id: 'previous', title: 'Previous answer', turns: [{ a: { figure: 10, sql: 'SELECT 10' } }] }], questions: [{ question: 'Previous question' }] }));
  window.localStorage.setItem('other-feature', 'keep');
  const { fetch } = stubShell();
  const first = renderBar({ app: 'discover' });
  fireEvent.click(await screen.findByRole('button', { name: 'Account: Sam Steward' }));
  fireEvent.click(screen.getByRole('button', { name: 'Sign out' }));
  await waitFor(() => {
    expect(fetch).toHaveBeenCalledWith('/session', { method: 'DELETE', credentials: 'include' });
    for (const key of keys) expect(window.sessionStorage.getItem(key)).toBeNull();
    for (const key of personKeys) expect(window.localStorage.getItem(key)).toBeNull();
  });
  first.unmount();
  renderBar({ app: 'discover', person: { userName: 'Rae Reader' } });
  await screen.findByRole('button', { name: 'Account: Rae Reader' });
  for (const key of keys) expect(window.sessionStorage.getItem(key)).toBeNull();
  expect(Object.keys(window.localStorage).filter(key => key.startsWith('contextAsk:person:'))).toEqual([]);
  expect(window.sessionStorage.getItem('other-feature')).toBe('keep');
  expect(window.localStorage.getItem('other-feature')).toBe('keep');
  window.sessionStorage.clear();
  window.localStorage.clear();
});
