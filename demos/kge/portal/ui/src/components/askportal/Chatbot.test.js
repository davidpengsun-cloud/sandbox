import { expect, test, vi } from 'vitest';
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { Provider } from 'react-redux';
import store from '../../app/store';
import Chatbot from './Chatbot';
import { postApi } from '../../api/api';
import SaveChatFeedback from './SaveChatFeedback';
import { getConfig } from '../../services/user';

vi.mock('../../api/api', () => ({ postApi: vi.fn() }));
vi.mock('../../services/user', () => ({ getConfig: vi.fn().mockResolvedValue({ data: {} }) }));
vi.mock('./SaveChatFeedback', () => ({ default: vi.fn() }));

test.each(['kge', 'portal', 'none'])('%s answers use the shared output and catalogue citation link', async (provider) => {
  window.sessionStorage.clear();
  postApi.mockResolvedValue({ data: { provider: provider === 'kge' ? 'kge' : 'portal', phrased_by: provider === 'none' ? 'catalogue' : provider, answer_text: 'Client Account Master: Region code.', behaviour: 'catalogue', figures: [], citations: [{ n: 1, title: 'Client Account Master: Region code', product: { id: 'retail.accounts' } }] } });
  const { container } = render(<Provider store={store}><Chatbot productId="retail.accounts" showChatbot setShowChatbot={() => {}} /></Provider>);
  fireEvent.click(screen.getByRole('button', { name: 'What does this data asset contain?' }));
  const link = await screen.findByRole('link', { name: 'Client Account Master: Region code' });
  expect(link).toHaveAttribute('href', '/productDetails/retail.accounts');
  expect(link.closest('.chatmsg')).toHaveTextContent('Client Account Master: Region code.');
  expect(container.querySelector('.chatmsg.error')).toBeNull();
});

test('a successful Ask answer shows thumbs and submits the selected thumb with its scope and question', async () => {
  window.sessionStorage.clear();
  postApi.mockResolvedValue({ data: { answer_text: 'Governed answer', behaviour: 'answer', figures: [], citations: [] } });
  SaveChatFeedback.mockResolvedValue({ status: 201 });
  // the closed panel is aria-hidden and inert, so the test opens it as a page would
  render(<Provider store={store}><Chatbot productId="retail.account" showChatbot setShowChatbot={() => {}} /></Provider>);
  fireEvent.click(screen.getByRole('button', { name: 'What does this data asset contain?' }));
  expect(await screen.findByText('Governed answer')).toBeInTheDocument();
  expect(screen.getByRole('button', { name: 'Not helpful' })).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: 'Helpful' }));
  fireEvent.click(await screen.findByRole('button', { name: 'Submit' }));
  await waitFor(() => expect(SaveChatFeedback).toHaveBeenCalledWith({ api: '/feedback-add', params: expect.objectContaining({ productId: 'retail.account', feedback_rating: 1, user_query_seq_id: 1, session_id: expect.any(Number) }) }));
  expect(SaveChatFeedback.mock.calls[0][0].params.session_id).toBeGreaterThan(0);
});

test('feedback is optional across consecutive questions and controls are named', async () => {
  window.sessionStorage.clear();
  postApi.mockReset().mockResolvedValue({ data: { answer_text: 'A governed answer' } });
  const { container, rerender } = render(<Provider store={store}><Chatbot productId="retail.account" showChatbot setShowChatbot={() => {}} /></Provider>);
  const input = screen.getByLabelText('Your question');
  for (const question of ['First question', 'Second question']) {
    expect(input).toBeEnabled();
    fireEvent.change(input, { target: { value: question } });
    fireEvent.keyDown(input, { key: 'Enter' });
    await waitFor(() => expect(postApi).toHaveBeenCalledWith({ api: '/ask-data-product', params: { productId: 'retail.account', question } }));
    await waitFor(() => expect(screen.queryByText('Looking at the data to answer that...')).toBeNull());
  }
  expect(postApi).toHaveBeenCalledTimes(2);
  expect(input).toBeEnabled();
  for (const name of ['Close Ask', 'Start a new chat', 'Send', 'Copy answer']) {
    expect(screen.getAllByRole('button', { name })[0]).toBeEnabled();
  }
  rerender(<Provider store={store}><Chatbot productId="retail.account" showChatbot={false} setShowChatbot={() => {}} /></Provider>);
  expect(container.querySelector('.chatbot')).toHaveAttribute('aria-hidden', 'true');
  expect(container.querySelector('.chatbot')).toHaveAttribute('inert', '');
});

test.each([['abstain', 'Abstained'], ['refuse', 'Refused']])('%s is a neutral notice with its reason', async (behaviour, label) => {
  window.sessionStorage.clear();
  postApi.mockReset().mockResolvedValue({ data: { behaviour, reason: 'No governed recipe covers this.' } });
  const { container } = render(<Provider store={store}><Chatbot productId="retail.account" showChatbot setShowChatbot={() => {}} /></Provider>);
  fireEvent.click(screen.getByRole('button', { name: 'What does this data asset contain?' }));
  expect(await screen.findByRole('note')).toHaveTextContent(label);
  expect(screen.getByRole('note')).toHaveTextContent('No governed recipe covers this.');
  expect(container.querySelector('.error')).toBeNull();
});

test.each([
  ['abstain', 'The requested comparison has no governed query.'],
  ['abstain_with_reason', 'The SQL gate rejected this query.'],
  ['abstain_route_steward', 'No certified mapping covers this term. Ask the data steward to certify it.'],
])('%s preserves the returned explanation without inventing catalogue guidance', async (behaviour, answer_text) => {
  window.sessionStorage.clear();
  postApi.mockReset().mockResolvedValue({ data: { behaviour, answer_text } });
  render(<Provider store={store}><Chatbot productId="retail.account" showChatbot setShowChatbot={() => {}} /></Provider>);
  fireEvent.click(screen.getByRole('button', { name: 'What does this data asset contain?' }));
  const note = await screen.findByRole('note');
  expect(note).toHaveTextContent(answer_text);
  expect(note).not.toHaveTextContent("Try the catalogue's terms");
  expect(note.querySelector('.notice-hint')).toBeNull();
});

test('salary questions are sent without a client keyword filter', async () => {
  window.sessionStorage.clear();
  postApi.mockReset().mockResolvedValue({ data: { answer_text: 'Governed response' } });
  render(<Provider store={store}><Chatbot productId="retail.account" showChatbot setShowChatbot={() => {}} /></Provider>);
  const question = 'What is the average salary by branch?';
  fireEvent.change(screen.getByLabelText('Your question'), { target: { value: question } });
  fireEvent.click(screen.getByRole('button', { name: 'Send' }));
  // Send hides once the input empties: focus stays in the input, not on the page body
  expect(screen.getByLabelText('Your question')).toHaveFocus();
  await screen.findByText('Governed response');
  expect(postApi).toHaveBeenCalledWith({ api: '/ask-data-product', params: { productId: 'retail.account', question } });
  expect(screen.queryByText(/Restricted input/)).toBeNull();
});

test('a reason KGE repeats in its answer text is shown once', async () => {
  window.sessionStorage.clear();
  postApi.mockReset().mockResolvedValue({ data: { behaviour: 'abstain', reason: 'No glossary term recognised in the question.', answer_text: 'No answer: No glossary term recognised in the question. Ask the data steward if it should be answerable.' } });
  render(<Provider store={store}><Chatbot productId="retail.account" showChatbot setShowChatbot={() => {}} /></Provider>);
  fireEvent.click(screen.getByRole('button', { name: 'What does this data asset contain?' }));
  const note = await screen.findByRole('note');
  expect(note.textContent.match(/No glossary term recognised in the question/g)).toHaveLength(1);
  expect(note).toHaveTextContent('Ask the data steward if it should be answerable.');
});

const productPrompts = [
  'What does this data asset contain?',
  'Which columns does this table have?',
  'Who owns this data asset?',
  'Is this data asset certified?',
];
const domainPrompts = [
  'What products are in this domain?',
  'Which columns are available in this domain?',
  'Who owns the products in this domain?',
  'How can I join tables in this domain?',
];

test.each([
  [{ productId: 'retail.monthly_revenue' }, productPrompts],
  [{ domainMode: true, scopeLabel: 'All data assets' }, domainPrompts],
  [{ domainMode: true, scopeLabel: 'Retail Banking', productIds: ['retail.monthly_revenue'] }, domainPrompts],
  [{ domainMode: true, scopeLabel: 'credit', productIds: ['corporate.credit_exposure'] }, domainPrompts],
  [{ scopeLabel: 'All data assets' }, domainPrompts],
])('starter chips are pinned for scope %o', (props, expected) => {
  window.sessionStorage.clear();
  render(<Provider store={store}><Chatbot {...props} showChatbot setShowChatbot={() => {}} /></Provider>);
  const prompts = screen.getByRole('listitem', { name: 'Suggested questions' });
  expect(within(prompts).getAllByRole('button').map((button) => button.textContent)).toEqual(expected);
});

test.each([
  { productId: 'retail.monthly_revenue', showChatbot: false },
  { productId: 'retail.monthly_revenue', showChatbot: true },
  { scopeLabel: 'All data assets', showChatbot: false },
  { scopeLabel: 'All data assets', showChatbot: true },
])('Guidelines do not load settings on mount: %o', async (props) => {
  window.sessionStorage.clear();
  getConfig.mockClear();
  render(<Provider store={store}><Chatbot {...props} setShowChatbot={() => {}} /></Provider>);
  await act(async () => {});
  expect(getConfig).not.toHaveBeenCalled();
});

test.each([
  { preview: false, executeQuery: false },
  { preview: true, executeQuery: false },
  { preview: false, executeQuery: true },
  { preview: true, executeQuery: true },
])('Guidelines mention only enabled table features: %o', async (flags) => {
  window.sessionStorage.clear();
  let resolveConfig;
  getConfig.mockReset().mockReturnValueOnce(new Promise((resolve) => { resolveConfig = resolve; }));
  const { rerender } = render(<Provider store={store}><Chatbot productId="retail.monthly_revenue" showChatbot setShowChatbot={() => {}} /></Provider>);
  expect(getConfig).not.toHaveBeenCalled();
  fireEvent.click(screen.getByText('Guidelines'));
  expect(getConfig).toHaveBeenCalledTimes(1);
  const guidelines = document.querySelector('.guideline-list');
  expect(guidelines).not.toHaveTextContent(/Data preview|Export CSV|Run a query/);
  fireEvent.click(screen.getByText('Guidelines'));
  fireEvent.click(screen.getByText('Guidelines'));
  expect(getConfig).toHaveBeenCalledTimes(1);
  await act(async () => resolveConfig({ data: flags }));
  expect(guidelines.textContent.includes('Data preview')).toBe(flags.preview);
  expect(guidelines.textContent.includes('Export CSV')).toBe(flags.preview);
  expect(guidelines.textContent.includes('Run a query')).toBe(flags.executeQuery);
  expect(guidelines).toHaveTextContent('Read the returned answer and citations together');
  fireEvent.click(screen.getByText('Guidelines'));
  fireEvent.click(screen.getByText('Guidelines'));
  fireEvent.click(screen.getByRole('button', { name: 'Start a new chat' }));
  rerender(<Provider store={store}><Chatbot productId="retail.account" showChatbot setShowChatbot={() => {}} /></Provider>);
  expect(getConfig).toHaveBeenCalledTimes(1);
});

test('Guidelines keep table features hidden when settings cannot be loaded', async () => {
  window.sessionStorage.clear();
  getConfig.mockReset().mockRejectedValueOnce(new Error('Settings unavailable'));
  render(<Provider store={store}><Chatbot productId="retail.monthly_revenue" showChatbot setShowChatbot={() => {}} /></Provider>);
  expect(getConfig).not.toHaveBeenCalled();
  fireEvent.click(screen.getByText('Guidelines'));
  await act(async () => {});
  expect(document.querySelector('.guideline-list')).not.toHaveTextContent(/Data preview|Export CSV|Run a query/);
  fireEvent.click(screen.getByText('Guidelines'));
  fireEvent.click(screen.getByText('Guidelines'));
  await act(async () => {});
  expect(getConfig).toHaveBeenCalledTimes(1);
  expect(document.querySelector('.guideline-list')).not.toHaveTextContent(/Data preview|Export CSV|Run a query/);
});
