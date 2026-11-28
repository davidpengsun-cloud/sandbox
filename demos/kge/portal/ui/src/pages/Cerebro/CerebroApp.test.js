import React from 'react';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { afterEach, beforeEach, expect, test, vi } from 'vitest';
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { Provider } from 'react-redux';
import { configureStore } from '@reduxjs/toolkit';
import { MemoryRouter, useLocation } from 'react-router-dom';
import CerebroApp, { VIEWS } from './CerebroApp';
import { useChatSession } from './chat/chatSession';

vi.mock('../../components/shell/ShellPage', () => ({ default: ({ app, title, fill, children }) => <div data-testid="shell" data-app={app} data-title={title} data-fill={String(fill)}>{children}</div> }));
// The graph view reads the graph; its own tests cover it, so the shell's tests draw a stand-in.
vi.mock('./graph/GraphView', () => ({ default: () => <div data-testid="graph-view" /> }));
vi.mock('./curate/CurateView', () => ({ default: () => <section aria-label="Proposals" /> }));
// No name setting served: the app carries its default name.
vi.mock('../../services/config', async (actual) => ({ ...(await actual()), useConfig: () => null }));

const read = (name) => readFileSync(fileURLToPath(new URL(name, import.meta.url)), 'utf8');

function Where() {
  const { pathname, search } = useLocation();
  return <output data-testid="where">{pathname + search}</output>;
}

function renderAt(path, roles = ['engineer'], views) {
  const store = configureStore({ reducer: { user: () => ({ data: { cerebroRoles: roles } }) } });
  return render(<Provider store={store}><MemoryRouter initialEntries={[path]}><CerebroApp views={views} /><Where /></MemoryRouter></Provider>);
}

const where = () => screen.getByTestId('where').textContent;
const tab = (name) => screen.getByRole('tab', { name });
const press = (target = window, init = {}) => act(() => { fireEvent.keyDown(target, { key: '`', ...init }); });

beforeEach(() => { vi.stubGlobal('fetch', vi.fn(() => Promise.resolve({ ok: false, json: async () => null }))); });
afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });

test('the address opens its mode and node, in the shell, under the portal bar', () => {
  renderAt('/cerebro?mode=chat&node=table%3Aorders');
  expect(screen.getByTestId('shell')).toHaveAttribute('data-app', 'cerebro');
  expect(screen.getByTestId('shell')).toHaveAttribute('data-fill', 'true');
  expect(screen.getByTestId('shell')).toHaveAttribute('data-title', 'Cerebro');
  expect(tab('Chat')).toHaveAttribute('aria-selected', 'true');
  expect(tab('Graph')).toHaveAttribute('aria-selected', 'false');
  expect(screen.getByRole('tabpanel')).toHaveAttribute('aria-labelledby', 'cerebro-tab-chat');
  expect(where()).toBe('/cerebro?mode=chat&node=table%3Aorders');
});

test('the app is named Cerebro by default', async () => {
  const { DEFAULT_CEREBRO_NAME, normaliseConfig } = await vi.importActual('../../services/config');
  expect(DEFAULT_CEREBRO_NAME).toBe('Cerebro');
  expect(normaliseConfig({}).cerebroName).toBe('Cerebro');
  renderAt('/cerebro');
  expect(screen.getByTestId('shell')).toHaveAttribute('data-title', 'Cerebro');
});

test('under the portal bar, one row holds the mode tabs and then the switch hint, and the view names its mode', () => {
  renderAt('/cerebro?mode=graph', ['steward']);
  const tablist = screen.getByRole('tablist', { name: 'Modes' });
  const row = tablist.parentElement;
  expect(row).toHaveClass('cerebro-bar');
  expect([...row.children].map((el) => el.className)).toEqual(['cerebro-modes', 'cerebro-hint']);
  expect(row.querySelector('.cerebro-hint').textContent).toBe('Press ` to switch mode');
  expect(screen.getAllByRole('tab').map((t) => t.textContent)).toEqual(['Chat', 'Graph', 'Curate']);
  expect(screen.getByRole('tabpanel')).toHaveClass('cerebro-view', 'cerebro-view-graph');
  expect(row.nextElementSibling).toBe(screen.getByRole('tabpanel'));
  fireEvent.click(tab('Chat'));
  expect(screen.getByRole('tabpanel')).toHaveClass('cerebro-view', 'cerebro-view-chat');
  expect(screen.getByRole('tabpanel')).not.toHaveClass('cerebro-view-graph');
});

/* jsdom lays nothing out, so the height rules are read from the stylesheet: the shell fills the window (ShellPage's
   `fill`), the app and its view take what is left under the mode row, and Graph mode's view never scrolls the page. */
test('the view fills the height under the mode row, and Graph mode does not scroll', () => {
  const css = read('./cerebro.css');
  const rule = (selector) => {
    const at = css.indexOf(`\n${selector} {`);
    expect(at, selector).toBeGreaterThanOrEqual(0);
    return css.slice(at, css.indexOf('}', at));
  };
  expect(rule('.cerebro-app')).toMatch(/flex:1;.*min-height:0;.*flex-direction:column;.*overflow:hidden/s);
  expect(rule('.cerebro-bar')).toMatch(/flex:none/);
  expect(rule('.cerebro-view')).toMatch(/flex:1;.*min-height:0;/s);
  expect(rule('.cerebro-view-graph')).toMatch(/overflow:hidden/);
  expect(css).toMatch(/\.cerebro-modes \[role="tab"\]\[aria-selected="true"\] \{ background:var\(--v3-accent-soft\); border-color:var\(--v3-accent-line\)/);
});

test('a bare /cerebro opens Graph and writes the mode into the address', () => {
  renderAt('/cerebro');
  expect(tab('Graph')).toHaveAttribute('aria-selected', 'true');
  expect(where()).toBe('/cerebro?mode=graph');
});

test.each(['/cerebro', '/cerebro?mode=graph', '/cerebro?mode=curate'])('a business-only user opens Chat at %s and cannot switch modes', (url) => {
  const Probe = ({ actions }) => <button onClick={() => actions.openNode('node-1')}>open node</button>;
  renderAt(url, ['business'], { chat: Probe, graph: Probe, curate: Probe });
  expect(where()).toBe('/cerebro?mode=chat');
  expect(screen.getAllByRole('tab').map((item) => item.textContent)).toEqual(['Chat']);
  expect(screen.queryByText(/switch mode/)).toBeNull();
  expect(screen.getByRole('tabpanel')).toHaveAttribute('aria-labelledby', 'cerebro-tab-chat');
  press();
  fireEvent.click(screen.getByRole('button', { name: 'open node' }));
  expect(where()).toBe('/cerebro?mode=chat');
});

test('a business-only redirect preserves the open conversation id', () => {
  const id = '1b4e28ba-2fa1-11d2-883f-0016d3cca427';
  renderAt(`/cerebro?mode=graph&node=term%3Aone&chat=${id}`, ['business', 'admin']);
  expect(where()).toBe(`/cerebro?mode=chat&node=term%3Aone&chat=${id}`);
  expect(screen.getAllByRole('tab').map((item) => item.textContent)).toEqual(['Chat']);
});

test('roles loading does not flash Graph or discard a later engineer deep link', () => {
  const store = configureStore({ reducer: { user: (state = { data: {} }, action) => action.type === 'roles' ? { data: { cerebroRoles: action.roles } } : state } });
  render(<Provider store={store}><MemoryRouter initialEntries={['/cerebro?mode=graph&node=x']}><CerebroApp /><Where /></MemoryRouter></Provider>);
  expect(screen.queryByTestId('graph-view')).toBeNull();
  expect(where()).toBe('/cerebro?mode=graph&node=x');
  act(() => store.dispatch({ type: 'roles', roles: ['business', 'engineer'] }));
  expect(screen.getByTestId('graph-view')).toBeInTheDocument();
  expect(where()).toBe('/cerebro?mode=graph&node=x');
});

test('Business with Steward retains all modes', () => {
  renderAt('/cerebro?mode=curate', ['business', 'steward']);
  expect(screen.getAllByRole('tab').map((item) => item.textContent)).toEqual(['Chat', 'Graph', 'Curate']);
  expect(where()).toBe('/cerebro?mode=curate');
});

test('choosing a mode writes it to the address and keeps the node', () => {
  renderAt('/cerebro?mode=graph&node=t1');
  fireEvent.click(tab('Chat'));
  expect(where()).toBe('/cerebro?mode=chat&node=t1');
  expect(tab('Chat')).toHaveAttribute('aria-selected', 'true');
  const before = window.history.length;
  fireEvent.click(tab('Chat'));
  expect(where()).toBe('/cerebro?mode=chat&node=t1');
  expect(window.history.length).toBe(before);
  fireEvent.keyDown(tab('Chat'), { key: 'ArrowRight' });
  expect(where()).toBe('/cerebro?mode=graph&node=t1');
  expect(tab('Graph')).toHaveFocus();
});

test('without the steward role Curate is not shown, the key skips it, and its address opens Graph', () => {
  renderAt('/cerebro?mode=curate&node=x', ['business', 'engineer']);
  expect(screen.queryByRole('tab', { name: 'Curate' })).toBeNull();
  expect(screen.getAllByRole('tab').map((t) => t.textContent)).toEqual(['Chat', 'Graph']);
  expect(where()).toBe('/cerebro?mode=graph&node=x');
  press();
  expect(where()).toBe('/cerebro?mode=chat&node=x');
  press();
  expect(where()).toBe('/cerebro?mode=graph&node=x');
});

test('an admin alone has no Curate: its address opens Graph', () => {
  for (const roles of [['admin']]) {
    renderAt('/cerebro?mode=curate', roles);
    expect(screen.queryByRole('tab', { name: 'Curate' })).toBeNull();
    expect(screen.queryByRole('region', { name: 'Proposals' })).toBeNull();
    expect(where()).toBe('/cerebro?mode=graph');
    press();
    expect(where()).toBe('/cerebro?mode=chat');
    cleanup();
  }
});

test('a steward has Curate, and the key cycles through all three modes', () => {
  renderAt('/cerebro?mode=chat', ['steward']);
  expect(screen.getAllByRole('tab').map((t) => t.textContent)).toEqual(['Chat', 'Graph', 'Curate']);
  press();
  expect(where()).toBe('/cerebro?mode=graph');
  press();
  expect(where()).toBe('/cerebro?mode=curate');
  expect(screen.getByRole('region', { name: 'Proposals' })).toBeInTheDocument();
  press();
  expect(where()).toBe('/cerebro?mode=chat');
});

test('the key typed into a field, or with a modifier, is text and not the switch', () => {
  const Typing = () => <input aria-label="Ask" />;
  renderAt('/cerebro?mode=chat', ['business'], { ...VIEWS, chat: Typing });
  press(screen.getByRole('textbox', { name: 'Ask' }));
  press(window, { ctrlKey: true });
  expect(where()).toBe('/cerebro?mode=chat');
});

test('the key listener goes when the app does', () => {
  const remove = vi.spyOn(window, 'removeEventListener');
  const { unmount } = renderAt('/cerebro');
  unmount();
  expect(remove).toHaveBeenCalledWith('keydown', expect.any(Function));
});

test('the conversation, the last path and the graph position are kept across modes, and not put in the address', () => {
  const seen = [];
  const Probe = ({ state, actions }) => {
    seen.push(state);
    return <>
      <button type="button" onClick={() => { actions.addTurn({ role: 'user', text: 'what is net revenue?' }); actions.setLastPath({ nodes: ['t1'] }); actions.setGraphPosition({ x: 3, y: 4, zoom: 2 }); }}>fill</button>
      <button type="button" onClick={() => actions.openNode('t1')}>open</button>
      <button type="button" onClick={() => actions.askAbout('t2')}>ask</button>
      <button type="button" onClick={() => actions.selectNode(null)}>clear</button>
    </>;
  };
  renderAt('/cerebro?mode=chat', ['business', 'engineer'], { chat: Probe, graph: Probe, curate: Probe });
  fireEvent.click(screen.getByRole('button', { name: 'fill' }));
  fireEvent.click(screen.getByRole('button', { name: 'open' }));
  expect(where()).toBe('/cerebro?mode=graph&node=t1');
  expect(seen.at(-1)).toMatchObject({ mode: 'graph', node: 't1', conversation: [{ role: 'user', text: 'what is net revenue?' }], lastPath: { nodes: ['t1'] }, graphPosition: { x: 3, y: 4, zoom: 2 }, roles: ['business', 'engineer'] });
  fireEvent.click(screen.getByRole('button', { name: 'ask' }));
  expect(where()).toBe('/cerebro?mode=chat&node=t2');
  expect(seen.at(-1).conversation).toHaveLength(1);
  fireEvent.click(screen.getByRole('button', { name: 'clear' }));
  expect(where()).toBe('/cerebro?mode=chat');
  expect(where()).not.toMatch(/revenue/);
});

test('Cerebro\'s shell uses the portal tokens only: no hex colour, no new token', () => {
  const own = read('./cerebro.css') + read('./CerebroApp.js') + read('./cerebroState.js');
  expect(own).not.toMatch(/#[0-9a-fA-F]{3,8}\b/);
  expect(own).not.toMatch(/--v3-[\w-]+\s*:/);
  expect(own).not.toMatch(/\b(rgba?|hsla?)\(/);
  expect(read('./cerebro.css')).not.toMatch(/font(-family)?:[^;}]*(serif|sans-serif|monospace|"[^"]+"|'[^']+')/);
  expect(own).toMatch(/var\(--v3-surface\)/);
});

test('the page reads KGE\'s ask health once from /api/cerebro/health for the picker, unless it was handed one', async () => {
  const ask = { model: { available: true } };
  fetch.mockImplementation(() => Promise.resolve({ ok: true, json: async () => ask }));
  const seen = [];
  const Probe = () => { seen.push(useChatSession()?.health); return null; };
  renderAt('/cerebro?mode=chat', ['business'], { chat: Probe, graph: Probe, curate: Probe });
  await waitFor(() => expect(seen.at(-1)).toEqual(ask));
  expect(fetch.mock.calls.filter(([url]) => url === '/api/cerebro/health')).toEqual([['/api/cerebro/health', { credentials: 'same-origin' }]]);
  cleanup();
  fetch.mockClear();
  const store = configureStore({ reducer: { user: () => ({ data: { cerebroRoles: ['business'] } }) } });
  render(<Provider store={store}><MemoryRouter initialEntries={['/cerebro?mode=chat']}><CerebroApp views={{ chat: Probe, graph: Probe, curate: Probe }} chat={{ health: null, questions: [] }} /></MemoryRouter></Provider>);
  // Issue 625: the sessions rail reads the person's conversations; nothing else is read.
  expect(fetch.mock.calls.map(([url]) => url).filter((url) => !url.startsWith('/api/cerebro/conversations?'))).toEqual([]);
});

test('#651 B7: a failed KGE health read still gives the chip the chat model the portal names; a body without one gives none', async () => {
  fetch.mockImplementation(() => Promise.resolve({ ok: false, status: 502, json: async () => ({ detail: 'KGE is unavailable.', model: 'model-a' }) }));
  const seen = [];
  const Probe = () => { seen.push(useChatSession()?.health); return null; };
  renderAt('/cerebro?mode=chat', ['business'], { chat: Probe, graph: Probe, curate: Probe });
  await waitFor(() => expect(seen.at(-1)).toEqual({ model: 'model-a' }));
  cleanup();
  fetch.mockImplementation(() => Promise.resolve({ ok: false, status: 404, json: async () => ({ detail: 'Not set up.' }) }));
  const after = [];
  const Again = () => { after.push(useChatSession()?.health); return null; };
  renderAt('/cerebro?mode=chat', ['business'], { chat: Again, graph: Again, curate: Again });
  await waitFor(() => expect(fetch.mock.calls.some(([url]) => url === '/api/cerebro/health')).toBe(true));
  await new Promise((done) => setTimeout(done, 0));
  expect(after.at(-1) ?? null).toBeNull();
});

test('the page reads the golden questions to try once from /api/cerebro/questions, and none when they cannot be read', async () => {
  const golden = ['Golden question 1?', 'Golden question 2?'];
  fetch.mockImplementation((url) => Promise.resolve(url === '/api/cerebro/questions' ? { ok: true, json: async () => ({ questions: [...golden, ' ', 7] }) } : { ok: true, json: async () => null }));
  const seen = [];
  const Probe = () => { seen.push(useChatSession()?.questions); return null; };
  renderAt('/cerebro?mode=chat', ['business'], { chat: Probe, graph: Probe, curate: Probe });
  await waitFor(() => expect(seen.at(-1)).toEqual(golden));
  expect(fetch.mock.calls.filter(([url]) => url === '/api/cerebro/questions')).toEqual([['/api/cerebro/questions', { credentials: 'same-origin' }]]);
  cleanup();
  seen.length = 0;
  fetch.mockImplementation(() => Promise.resolve({ ok: false, json: async () => ({ detail: 'Sign in.' }) }));
  renderAt('/cerebro?mode=chat', ['business'], { chat: Probe, graph: Probe, curate: Probe });
  await waitFor(() => expect(fetch).toHaveBeenCalledWith('/api/cerebro/questions', { credentials: 'same-origin' }));
  expect(seen.at(-1)).toEqual([]);
});

test('Ask about this holds its node for the next question in Chat alone: a send or a mode change clears it', () => {
  const seen = [];
  const Probe = ({ state, actions }) => {
    seen.push(state);
    return <>
      <button type="button" onClick={() => actions.askAbout('table:retail.orders')}>ask</button>
      <button type="button" onClick={() => actions.asked()}>sent</button>
      <button type="button" onClick={() => actions.setMode('graph')}>graph</button>
      <button type="button" onClick={() => actions.setMode('chat')}>chat</button>
    </>;
  };
  renderAt('/cerebro?mode=graph&node=table:retail.orders', ['engineer'], { chat: Probe, graph: Probe, curate: Probe });
  fireEvent.click(screen.getByRole('button', { name: 'chat' }));
  expect(seen.at(-1)).toMatchObject({ mode: 'chat', node: 'table:retail.orders', asking: null });
  fireEvent.click(screen.getByRole('button', { name: 'ask' }));
  expect(seen.at(-1)).toMatchObject({ mode: 'chat', asking: 'table:retail.orders' });
  fireEvent.click(screen.getByRole('button', { name: 'sent' }));
  expect(seen.at(-1).asking).toBeNull();
  fireEvent.click(screen.getByRole('button', { name: 'ask' }));
  fireEvent.click(screen.getByRole('button', { name: 'graph' }));
  expect(seen.at(-1)).toMatchObject({ mode: 'graph', asking: null });
});
