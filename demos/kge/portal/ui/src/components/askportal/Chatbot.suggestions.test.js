// Discover's Ask, issue #590: the suggested questions an admin saves replace the standard ones on a data asset and on
// a domain, an empty list keeps the standard ones, and PORTAL_ASK_GUIDE=off (the served settings' askGuide: false)
// hides the Guidelines section.
import { afterEach, beforeEach, expect, test, vi } from 'vitest';
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { Provider } from 'react-redux';
import store from '../../app/store';
import Chatbot from './Chatbot';
import { postApi } from '../../api/api';

vi.mock('../../api/api', () => ({ postApi: vi.fn() }));
vi.mock('../../services/user', () => ({ getConfig: vi.fn().mockResolvedValue({ data: {} }) }));
vi.mock('./SaveChatFeedback', () => ({ default: vi.fn() }));

const STANDARD_ASSET = ['What does this data asset contain?', 'Which columns does this table have?', 'Who owns this data asset?', 'Is this data asset certified?'];
const STANDARD_DOMAIN = ['What products are in this domain?', 'Which columns are available in this domain?', 'Who owns the products in this domain?', 'How can I join tables in this domain?'];
const CONFIG = { knowledgeHome: true, apps: { certify: true, context: true, discover: true }, contextMcpUrl: 'https://kge.example.invalid/mcp' };
const json = (body, status = 200) => Promise.resolve(new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } }));

let original;
let answers;
beforeEach(() => {
  window.sessionStorage.clear();
  original = globalThis.fetch;
  answers = { suggestions: () => json({ asset: [], domain: [] }), config: () => json(CONFIG) };
  globalThis.fetch = vi.fn((url, init) => {
    if (url === '/api/ask-suggestions') return answers.suggestions();
    if (url === '/api/config') return answers.config();
    return original(url, init);
  });
});
afterEach(() => { globalThis.fetch = original; });

const prompts = () => within(screen.getByRole('listitem', { name: 'Suggested questions' })).getAllByRole('button').map((button) => button.textContent);
const renderAsk = (props) => render(<Provider store={store}><Chatbot {...props} showChatbot setShowChatbot={() => {}} /></Provider>);

test.each([
  [{ productId: 'retail.monthly_revenue' }, ['Who signs off this asset?', 'How fresh is it?']],
  [{ domainMode: true, scopeLabel: 'Retail Banking', productIds: ['retail.monthly_revenue'] }, ['Which assets join on customer?']],
])('the admin\'s saved questions replace the standard ones for scope %o', async (props, expected) => {
  answers.suggestions = () => json({ asset: ['Who signs off this asset?', 'How fresh is it?'], domain: ['Which assets join on customer?'] });
  renderAsk(props);
  await waitFor(() => expect(prompts()).toEqual(expected));
  expect(globalThis.fetch).toHaveBeenCalledWith('/api/ask-suggestions', { credentials: 'same-origin' });
});

test('a saved question asks itself when clicked', async () => {
  answers.suggestions = () => json({ asset: ['Who signs off this asset?'], domain: [] });
  postApi.mockResolvedValue({ data: { answer_text: 'The steward signs it off.', behaviour: 'answer', figures: [], citations: [] } });
  renderAsk({ productId: 'retail.monthly_revenue' });
  await waitFor(() => expect(prompts()).toEqual(['Who signs off this asset?']));
  fireEvent.click(screen.getByRole('button', { name: 'Who signs off this asset?' }));
  expect(await screen.findByText('The steward signs it off.')).toBeInTheDocument();
  expect(postApi.mock.calls.at(-1)[0].params.question).toBe('Who signs off this asset?');
});

test.each([
  ['empty lists', () => json({ asset: [], domain: [] })],
  ['a refusal', () => json({ message: 'Portal role required.' }, 403)],
  ['an unreachable portal', () => Promise.reject(new Error('offline'))],
])('%s keep today\'s four questions on an asset and a domain', async (_name, answer) => {
  answers.suggestions = answer;
  renderAsk({ productId: 'retail.monthly_revenue' });
  await act(async () => {});
  expect(prompts()).toEqual(STANDARD_ASSET);
});

test('a domain with only an asset list saved keeps its own standard questions', async () => {
  answers.suggestions = () => json({ asset: ['Only for assets?'], domain: [] });
  renderAsk({ domainMode: true, scopeLabel: 'All data assets' });
  await act(async () => {});
  expect(prompts()).toEqual(STANDARD_DOMAIN);
});

test.each([
  [{ askGuide: false }, false],
  [{ askGuide: true }, true],
  [{}, true],
])('served settings %o show the Guidelines section: %s', async (flag, shown) => {
  answers.config = () => json({ ...CONFIG, ...flag });
  renderAsk({ productId: 'retail.monthly_revenue' });
  await waitFor(() => expect(globalThis.fetch).toHaveBeenCalledWith('/api/config', { credentials: 'same-origin' }));
  await act(async () => {});
  if (shown) {
    expect(screen.getByText('Guidelines')).toBeInTheDocument();
    expect(screen.getByText(/Open Guidelines for help/)).toBeInTheDocument();
  } else {
    expect(screen.queryByText('Guidelines')).not.toBeInTheDocument();
    expect(screen.queryByText(/Open Guidelines/)).not.toBeInTheDocument();
    expect(screen.getByText('Ask about the products you can read.')).toBeInTheDocument();
  }
  expect(prompts()).toEqual(STANDARD_ASSET);
});

test('a failed read is not kept: the next Ask opened reads the admin\'s lists again', async () => {
  answers.suggestions = () => json({ message: 'Busy.' }, 503);
  const first = renderAsk({ productId: 'retail.monthly_revenue' });
  await waitFor(() => expect(prompts()).toEqual(STANDARD_ASSET));
  first.unmount();
  answers.suggestions = () => json({ asset: ['Now readable?'], domain: [] });
  renderAsk({ productId: 'retail.monthly_revenue' });
  await waitFor(() => expect(prompts()).toEqual(['Now readable?']));
  expect(globalThis.fetch.mock.calls.filter(([url]) => url === '/api/ask-suggestions')).toHaveLength(2);
});

test('the list waits for the admin\'s lists, so the standard questions never show first and swap', async () => {
  let release;
  answers.suggestions = () => new Promise((resolve) => { release = () => resolve(new Response(JSON.stringify({ asset: ['Admin question?'], domain: [] }), { status: 200, headers: { 'Content-Type': 'application/json' } })); });
  renderAsk({ productId: 'retail.monthly_revenue' });
  await act(async () => {});
  expect(screen.queryByRole('listitem', { name: 'Suggested questions' })).toBeNull();
  await act(async () => release());
  await waitFor(() => expect(prompts()).toEqual(['Admin question?']));
});
