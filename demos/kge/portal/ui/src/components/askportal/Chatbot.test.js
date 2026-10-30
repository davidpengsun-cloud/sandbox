import { expect, test, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { Provider } from 'react-redux';
import store from '../../app/store';
import Chatbot from './Chatbot';
import { postApi } from '../../api/api';
import SaveChatFeedback from './SaveChatFeedback';

vi.mock('../../api/api', () => ({ postApi: vi.fn() }));
vi.mock('./SaveChatFeedback', () => ({ default: vi.fn() }));

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
