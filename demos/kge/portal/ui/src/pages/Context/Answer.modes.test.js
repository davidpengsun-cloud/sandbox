import React from 'react';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, expect, test, vi } from 'vitest';
import Answer from './Answer';
import replies from './fixtures/answer_modes.json';

const me = { id: 'reader@example.com', reads: ['leasing'] };
const LABEL = { certified: 'Certified', ai_generated: 'AI-Generated' };
const OUTCOMES = ['certified', 'ai_generated', 'not_sure', 'decline'];
const times = (text, word) => text.split(word).length - 1;
const clipboardDescriptor = Object.getOwnPropertyDescriptor(navigator, 'clipboard');
afterEach(() => {
  vi.restoreAllMocks();
  if (clipboardDescriptor) Object.defineProperty(navigator, 'clipboard', clipboardDescriptor);
  else delete navigator.clipboard;
});

test.each(['certified', 'ai_generated'])('the %s answer shows its label once, as the badge, and its text without the label', key => {
  const reply = replies[key], label = LABEL[reply.answer_mode];
  const { container } = render(<Answer answer={reply} me={me} />);
  const card = screen.getByLabelText('Answer');
  const badges = container.querySelectorAll('[data-answer-mode]');
  expect(badges).toHaveLength(1);
  expect(badges[0]).toHaveTextContent(label);
  expect(badges[0]).toHaveAttribute('data-answer-mode', reply.answer_mode);
  expect(times(card.textContent, label)).toBe(1);
  expect(card.textContent).not.toContain(`${label}:`);
});

test.each([['not_sure', 'Cannot answer'], ['decline', 'Refused']])('the %s decline is unlabelled: its one badge says what happened', (key, said) => {
  const reply = replies[key];
  const { container } = render(<Answer answer={reply} me={me} />);
  const card = screen.getByLabelText('Answer');
  expect(container.querySelector('[data-answer-mode]')).toBeNull();
  expect(container.querySelector('.row .badge')).toHaveTextContent(said);
  for (const label of Object.values(LABEL)) expect(card.textContent).not.toContain(label);
  expect(card.textContent).not.toContain('Not sure');
});

test.each(OUTCOMES)('the %s reply shows no AI fallback: it is a deployment setting, not shown per answer', key => {
  render(<Answer answer={{ ...replies[key], ai_fallback: 'on' }} me={me} />);
  expect(screen.getByLabelText('Answer').textContent).not.toMatch(/AI fallback|ai_fallback/i);
});

test.each([
  ['an agent reply', { ...replies.ai_generated, agent: { arm: 'reference', mode: 'mock' } }, 'AI-Generated', 'The difference between'],
  ['an answer with sources', { ...replies.certified, behaviour: 'combined', rows: undefined, figures: [], citations: [], guard: { passed: true }, answer_text: 'Certified: Revenue is USD 25 [1].' }, 'Certified', 'Revenue is USD 25'],
  ['a certified definition', { ...replies.certified, behaviour: 'cite_certified', rows: undefined, cite: ['EM-001'], answer_text: 'Certified: Net New Assets means inflows less outflows.' }, 'Certified', 'Net New Assets means inflows less outflows.'],
])('%s shows answer_text without its leading label, and copies it without the label', async (_, reply, label, body) => {
  const writeText = vi.fn(async () => {});
  Object.defineProperty(navigator, 'clipboard', { configurable: true, value: { writeText } });
  const { container } = render(<Answer answer={reply} me={me} />);
  expect(container.querySelector('.ans').textContent.startsWith(body)).toBe(true);
  expect(times(screen.getByLabelText('Answer').textContent, label)).toBe(1);
  fireEvent.click(screen.getByRole('button', { name: 'Copy answer' }));
  await waitFor(() => expect(writeText).toHaveBeenCalledTimes(1));
  expect(writeText.mock.calls[0][0].startsWith(body)).toBe(true);
  expect(writeText.mock.calls[0][0]).not.toContain(`${label}:`);
});

test.each(OUTCOMES)('the %s reply offers Confirmed and Not what I meant in place of Helpful?, sent once with its receipt', key => {
  const reply = replies[key], onFeedback = vi.fn();
  const { rerender } = render(<Answer answer={reply} me={me} onFeedback={onFeedback} />);
  expect(screen.queryByRole('button', { name: 'Helpful?' })).toBeNull();
  const confirmed = screen.getByRole('button', { name: 'Confirmed' }), wrong = screen.getByRole('button', { name: 'Not what I meant' });
  for (const button of [confirmed, wrong]) {
    expect(button).toHaveAttribute('aria-pressed', 'false');
    expect(button).toBeEnabled();
  }
  fireEvent.click(wrong);
  expect(onFeedback).toHaveBeenCalledTimes(1);
  expect(onFeedback).toHaveBeenCalledWith('not_what_i_meant', { receipt: reply.receipt, verdict: 'not_what_i_meant' });
  expect(wrong).toHaveAttribute('aria-pressed', 'true');
  expect(confirmed).toHaveAttribute('aria-pressed', 'false');
  expect(confirmed).toBeDisabled();
  expect(wrong).toBeDisabled();
  fireEvent.click(confirmed);
  expect(onFeedback).toHaveBeenCalledTimes(1);
  // A verdict Ask already recorded on the exchange holds across a re-render.
  rerender(<Answer answer={reply} me={me} onFeedback={onFeedback} feedback="confirmed" />);
  expect(screen.getByRole('button', { name: 'Confirmed' })).toHaveAttribute('aria-pressed', 'true');
  expect(screen.getByRole('button', { name: 'Not what I meant' })).toBeDisabled();
});

test('a failed send shows the error as small text and lets the person send again', () => {
  const onFeedback = vi.fn();
  const { container, rerender } = render(<Answer answer={replies.certified} me={me} onFeedback={onFeedback} />);
  fireEvent.click(screen.getByRole('button', { name: 'Confirmed' }));
  rerender(<Answer answer={replies.certified} me={me} onFeedback={onFeedback} feedbackError="Feedback could not be sent." />);
  expect(container.querySelector('p.small[role="alert"]')).toHaveTextContent('Feedback could not be sent.');
  expect(screen.getByRole('button', { name: 'Confirmed' })).toBeEnabled();
  expect(screen.getByRole('button', { name: 'Confirmed' })).toHaveAttribute('aria-pressed', 'false');
});

test('feedback cannot be sent where the page offers no handler', () => {
  render(<Answer answer={replies.not_sure} me={me} />);
  expect(screen.getByRole('button', { name: 'Confirmed' })).toBeDisabled();
  expect(screen.getByRole('button', { name: 'Not what I meant' })).toBeDisabled();
});

test('an access refusal carries a receipt but no mode: its behaviour label stays and feedback is offered', () => {
  const onFeedback = vi.fn();
  const refusal = { question: 'Revenue for retail', principal: me.id, arm: 'graph', behaviour: 'refuse', reason: 'You may not read retail.', denied_warehouses: ['retail'], receipt: 'stub-receipt-refusal.EEEE' };
  const { container } = render(<Answer answer={refusal} me={me} onFeedback={onFeedback} />);
  expect(container.querySelector('.badge.bad')).toHaveTextContent('Refused');
  expect(container.querySelector('[data-answer-mode]')).toBeNull();
  expect(screen.getByLabelText('Answer')).not.toHaveTextContent('AI fallback');
  fireEvent.click(screen.getByRole('button', { name: 'Confirmed' }));
  expect(onFeedback).toHaveBeenCalledWith('confirmed', { receipt: 'stub-receipt-refusal.EEEE', verdict: 'confirmed' });
});

test('a pre-A1 reply renders as today: behaviour label, no fallback badge, Helpful? and no feedback buttons', () => {
  const { container } = render(<Answer answer={{ question: 'Revenue', principal: me.id, arm: 'graph', behaviour: 'combined', answer_text: 'Certified revenue [1]', agent: { arm: 'reference', mode: 'mock' } }} me={me} onFeedback={vi.fn()} />);
  expect(container.querySelector('.badge')).toHaveTextContent('Answer with sources');
  expect(container.querySelector('.ans')).toHaveTextContent('Certified revenue [1]');
  expect(screen.getByLabelText('Answer')).not.toHaveTextContent('AI fallback');
  expect(screen.getByRole('button', { name: 'Helpful?' })).toBeInTheDocument();
  expect(screen.queryByRole('button', { name: 'Confirmed' })).toBeNull();
});
