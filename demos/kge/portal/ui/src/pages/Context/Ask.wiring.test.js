import React from 'react';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, expect, test, vi } from 'vitest';
import Ask from './Ask';
import modes from './fixtures/answer_modes.json';
import { loadPersonHistory } from '../../services/askHistory';

/* A2-3: Context's Ask bar wired to the shared picker (model and mode, no AI fallback) and waiting status,
   and Confirmed / Not what I meant posted to /ask/feedback with the receipt as given. */
const me = { id: 'reader@example.com', reads: ['leasing'] };
const presets = { default: 'graph', presets: { graph: { arm: 'graph', paths: ['ask'] }, schema: { arm: 'schema' } }, agent_arms: { reference: { arm: 'reference', mode: 'mock', side: 'graph on' } } };
const health = { default: 'strict', modes: ['off', 'strict', 'open'], model: 'vertex:gemini-stub' };
const submit = text => {
  fireEvent.change(screen.getByLabelText('Question'), { target: { value: text } });
  fireEvent.click(screen.getByRole('button', { name: 'Ask', exact: true }));
};
const matchMediaDescriptor = Object.getOwnPropertyDescriptor(window, 'matchMedia');
afterEach(() => {
  vi.restoreAllMocks();
  window.localStorage?.clear();
  window.sessionStorage?.clear();
  if (matchMediaDescriptor) Object.defineProperty(window, 'matchMedia', matchMediaDescriptor);
  else delete window.matchMedia;
});

test('the picker names the model and offers no AI-fallback control', () => {
  render(<Ask api={vi.fn()} me={me} presets={presets} health={{ ...health, default: 'open' }} />);
  const trigger = screen.getByRole('button', { name: /Answer settings:/ });
  expect(trigger).toHaveTextContent('vertex:gemini-stub');
  expect(trigger).not.toHaveTextContent(/fallback/i);
  fireEvent.click(trigger);
  expect(screen.queryAllByRole('radio')).toHaveLength(0);
  expect(screen.getByRole('dialog', { name: 'Answer settings' })).not.toHaveTextContent(/fallback/i);
});

// The AI fallback is one deployment switch (owner, 2026-11-16): KGE's /ask refuses an ai_fallback field (422), so
// no request carries one, whatever /health says.
test.each([
  ['/health ask with a fallback default and modes', presets, health, '/ask'],
  ['/health ask with a fallback switch', presets, { ai_fallback: 'on', model: 'vertex:gemini-stub' }, '/ask'],
  ['no /health ask field (a KGE before answer modes)', presets, undefined, '/ask'],
  ['an arm that answers on /answer', { ...presets, default: 'schema' }, health, '/answer'],
])('no AI fallback is sent with %s', async (_, armPresets, askHealth, path) => {
  const api = vi.fn(async (_path, body) => (_path === '/runtimes' ? { runtimes: [] } : { question: body.question, principal: me.id, behaviour: 'figure', figure: 1 }));
  const { container } = render(<Ask api={api} me={me} presets={armPresets} health={askHealth} />);
  fireEvent.click(container.querySelector('#ask-settings'));
  submit('Revenue?');
  await waitFor(() => expect(api).toHaveBeenCalledWith(path, expect.anything()));
  expect(JSON.stringify(api.mock.calls.find(([p]) => p === path)[1])).toBe(JSON.stringify({ question: 'Revenue?', purpose: 'analysis', arm: armPresets.default }));
  for (const [, body] of api.mock.calls) expect(JSON.stringify(body ?? {})).not.toContain('ai_fallback');
});

test('the picker loads runtimes when it opens, and offers Answered by to testers alone', async () => {
  const api = vi.fn(async () => ({ runtimes: [{ id: 'rt', label: 'Certified BigQuery', certified: true }] }));
  const { container, rerender } = render(<Ask api={api} me={me} presets={presets} health={health} />);
  expect(api).not.toHaveBeenCalled();
  fireEvent.click(container.querySelector('#ask-settings'));
  await waitFor(() => expect(api).toHaveBeenCalledWith('/runtimes'));
  expect(await screen.findByText('Certified BigQuery')).toBeVisible();
  expect(screen.queryByLabelText('Answered by')).toBeNull();
  rerender(<Ask api={api} me={me} presets={presets} health={health} tester />);
  expect(within(container.querySelector('#ask-settings-panel')).getByLabelText('Answered by')).toHaveValue('graph');
  expect(container.querySelector('#runtimes-chip')).toBeNull();
  expect(container.querySelector('#settings #arm')).toBeNull();
});

test.each([['Confirmed', 'confirmed'], ['Not what I meant', 'not_what_i_meant']])('%s posts the verdict with the receipt as given and is recorded on the exchange', async (text, verdict) => {
  const reply = modes.ai_generated;
  const api = vi.fn(async path => (path === '/ask/feedback' ? {} : reply));
  render(<Ask api={api} me={me} presets={presets} />);
  submit(reply.question);
  const button = await screen.findByRole('button', { name: text });
  await act(async () => fireEvent.click(button));
  expect(api).toHaveBeenCalledWith('/ask/feedback', { receipt: reply.receipt, verdict });
  expect(api.mock.calls.filter(([path]) => path === '/ask/feedback')).toHaveLength(1);
  expect(screen.getByRole('button', { name: text })).toHaveAttribute('aria-pressed', 'true');
  expect(screen.getByRole('button', { name: text })).toBeDisabled();
  expect(screen.queryByRole('alert')).toBeNull();
});

test('a conversation restored from the rail shows its sent verdict and does not send it again, also after a reload and a follow-up', async () => {
  const reply = modes.ai_generated;
  const api = vi.fn(async path => (path === '/ask/feedback' ? {} : reply));
  const sent = () => api.mock.calls.filter(([path]) => path === '/ask/feedback');
  // The rail lists conversations whose units the person reads.
  const golden = { question_units: { [reply.question]: ['leasing'], 'A follow-up question': ['leasing'] } };
  const first = render(<Ask api={api} me={me} presets={presets} golden={golden} />);
  submit(reply.question);
  const confirm = await screen.findByRole('button', { name: 'Confirmed' });
  await act(async () => fireEvent.click(confirm));
  expect(sent()).toHaveLength(1);
  const restoreAndCheck = async () => {
    fireEvent.click(document.querySelector('.rail-conv-btn'));
    await waitFor(() => expect(document.querySelector('.answer-card')).toBeTruthy());
    for (const name of ['Confirmed', 'Not what I meant']) {
      const button = screen.getByRole('button', { name });
      expect(button).toBeDisabled();
      await act(async () => fireEvent.click(button));
    }
    expect(screen.getByRole('button', { name: 'Confirmed' })).toHaveAttribute('aria-pressed', 'true');
    expect(sent()).toHaveLength(1);
  };
  // Same session: a new conversation, then back to the past one.
  fireEvent.click(screen.getByTestId('rail-new-conversation'));
  expect(document.querySelector('.answer-card')).toBeNull();
  await restoreAndCheck();
  // A follow-up in the restored conversation saves its turns again; the verdict stays with its turn.
  submit('A follow-up question');
  await waitFor(() => expect(document.querySelectorAll('#thread .answer-card')).toHaveLength(2));
  first.unmount();
  // A reload: the verdict comes back from the saved history alone.
  render(<Ask api={api} me={me} presets={presets} golden={golden} />);
  await act(async () => fireEvent.click(document.querySelector('.rail-conv-btn')));
  await waitFor(() => expect(document.querySelectorAll('#thread .answer-card')).toHaveLength(2));
  // The thread shows the newest turn first; the verdict belongs to the first question, shown last.
  const cards = document.querySelectorAll('#thread .exchange');
  const card = cards[cards.length - 1];
  expect(within(card).getByRole('button', { name: 'Not what I meant' })).toHaveAttribute('aria-pressed', 'false');
  expect(within(cards[0]).getByRole('button', { name: 'Confirmed' })).not.toBeDisabled();
  const confirmed = within(card).getByRole('button', { name: 'Confirmed' });
  expect(confirmed).toBeDisabled();
  expect(confirmed).toHaveAttribute('aria-pressed', 'true');
  await act(async () => fireEvent.click(confirmed));
  expect(sent()).toHaveLength(1);
});

test('a restored conversation whose turn shares an id with the one on screen does not inherit its verdict', async () => {
  const reply = modes.ai_generated;
  const past = modes.certified;
  window.localStorage.setItem('contextAsk:person:reader@example.com', JSON.stringify({ questions: [], conversations: [
    { id: 'conv-past', title: past.question, updatedAt: Date.now(), turns: [{ id: 1, q: past.question, units: ['leasing'], a: past }] },
  ] }));
  const api = vi.fn(async path => (path === '/ask/feedback' ? {} : reply));
  render(<Ask api={api} me={me} presets={presets} golden={{ question_units: { [reply.question]: ['leasing'] } }} />);
  submit(reply.question);
  const confirm = await screen.findByRole('button', { name: 'Confirmed' });
  expect(document.querySelector('#thread .exchange').getAttribute('data-exchange-id')).toBe('1');
  await act(async () => fireEvent.click(confirm));
  const link = [...document.querySelectorAll('.rail-conv-btn')].find(el => el.textContent === past.question);
  await act(async () => fireEvent.click(link));
  const restored = screen.getByRole('button', { name: 'Confirmed' });
  expect(restored).toHaveAttribute('aria-pressed', 'false');
  expect(restored).not.toBeDisabled();
  await act(async () => fireEvent.click(restored));
  expect(api).toHaveBeenLastCalledWith('/ask/feedback', { receipt: past.receipt, verdict: 'confirmed' });
});

test('a feedback refusal shows as small text and the person can send again', async () => {
  const reply = modes.certified;
  let fail = true;
  const api = vi.fn(async path => (path === '/ask/feedback' ? (fail ? { error: 'Not permitted: no receipt', status: 403 } : {}) : reply));
  render(<Ask api={api} me={me} presets={presets} />);
  submit(reply.question);
  await waitFor(() => expect(document.querySelector('.answer-card')).toBeTruthy());
  await act(async () => fireEvent.click(screen.getByRole('button', { name: 'Confirmed' })));
  const alert = screen.getByRole('alert');
  expect(alert).toHaveClass('small');
  expect(alert).toHaveTextContent('Not permitted: no receipt');
  expect(screen.getByRole('button', { name: 'Confirmed' })).not.toBeDisabled();
  fail = false;
  await act(async () => fireEvent.click(screen.getByRole('button', { name: 'Confirmed' })));
  expect(api.mock.calls.filter(([path]) => path === '/ask/feedback')).toHaveLength(2);
  expect(screen.queryByRole('alert')).toBeNull();
  expect(screen.getByRole('button', { name: 'Confirmed' })).toHaveAttribute('aria-pressed', 'true');
});

test('the waiting state is the shared spinner and one live region; the step shows outside it', () => {
  const api = vi.fn(() => new Promise(() => {}));
  const { container } = render(<Ask api={api} me={me} presets={presets} />);
  submit('Revenue?');
  const line = screen.getByTestId('live-activity-line');
  expect(line).toHaveClass('waiting-status', 'is-animated');
  expect(line.querySelector('.waiting-status-spinner')).toHaveAttribute('aria-hidden', 'true');
  const regions = [...container.querySelectorAll('#thread [role="status"]')];
  expect(regions).toHaveLength(1);
  expect(regions[0].textContent).toBe('Working out the answer…');
  expect(line.closest('[aria-live]')).toHaveAttribute('aria-live', 'off');
});

test('with reduced motion the waiting state does not animate', () => {
  Object.defineProperty(window, 'matchMedia', { configurable: true, writable: true, value: query => ({ matches: query.includes('prefers-reduced-motion'), media: query, addEventListener() {}, removeEventListener() {} }) });
  render(<Ask api={vi.fn(() => new Promise(() => {}))} me={me} presets={presets} />);
  submit('Revenue?');
  const line = screen.getByTestId('live-activity-line');
  expect(line).not.toHaveClass('is-animated');
  expect(within(line).getByRole('status')).toHaveTextContent('Working out the answer…');
});

test('the Ask bar keeps its one-line input: no textarea', () => {
  const source = readFileSync(join(process.cwd(), 'src/pages/Context/Ask.js'), 'utf8');
  expect(source).not.toMatch(/<textarea/i);
});

test('a verdict and the next answer settling together keep the verdict in the saved history', async () => {
  const first = { ...modes.ai_generated, unit: 'leasing' };
  const second = { ...first, question: 'Second question?' };
  let finishFeedback, finishSecond;
  const api = vi.fn((path, body) => {
    if (path === '/ask/feedback') return new Promise(resolve => { finishFeedback = resolve; });
    if (body.question === second.question) return new Promise(resolve => { finishSecond = resolve; });
    return Promise.resolve(first);
  });
  render(<Ask api={api} me={me} presets={presets} />);
  submit(first.question);
  const button = await screen.findByRole('button', { name: 'Confirmed' });
  await act(async () => fireEvent.click(button));
  submit(second.question);
  await act(async () => { finishFeedback({}); finishSecond(second); });
  await waitFor(() => expect(screen.getByRole('button', { name: 'Ask', exact: true })).not.toBeDisabled());
  const turns = loadPersonHistory(me.id).conversations[0].turns;
  expect(turns.map(t => t.q).sort()).toEqual([first.question, second.question].sort());
  expect(turns.find(t => t.q === first.question).feedback).toBe('confirmed');
});

test('a verdict queued before a new question is asked is kept, with the prior answer, when that question settles', async () => {
  const first = { ...modes.ai_generated, unit: 'leasing' };
  const second = { ...first, question: 'Second question?' };
  let finishFeedback, finishSecond;
  const api = vi.fn((path, body) => {
    if (path === '/ask/feedback') return new Promise(resolve => { finishFeedback = resolve; });
    if (body.question === second.question) return new Promise(resolve => { finishSecond = resolve; });
    return Promise.resolve(first);
  });
  render(<Ask api={api} me={me} presets={presets} />);
  submit(first.question);
  const button = await screen.findByRole('button', { name: 'Confirmed' });
  await act(async () => fireEvent.click(button));
  submit(second.question);
  await act(async () => { finishFeedback({}); });
  await act(async () => { finishSecond(second); });
  await waitFor(() => expect(screen.getByRole('button', { name: 'Ask', exact: true })).not.toBeDisabled());
  const turns = loadPersonHistory(me.id).conversations[0].turns;
  const prior = turns.find(t => t.q === first.question);
  expect(turns).toHaveLength(2);
  expect(prior.feedback).toBe('confirmed');
  expect(prior.a.question).toBe(first.question);
});
