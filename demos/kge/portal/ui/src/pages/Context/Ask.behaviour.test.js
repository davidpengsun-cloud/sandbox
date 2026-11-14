import React from 'react';
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, expect, test, vi } from 'vitest';
import Ask, { layoutKey, RAIL_FOLDED_KEY, CHAT_SHARE_KEY } from './Ask';
import BehindAnswer from './BehindAnswer';
import { useContextApi } from './contextState';
import { loadPersonHistory, savePersonTurn } from '../../services/askHistory';

const me = { id: 'engineer@example.com', reads: ['leasing'] };
const presets = { default: 'graph', presets: { graph: { arm: 'graph', paths: ['ask'] }, schema: { arm: 'schema' } }, agent_arms: { reference: { arm: 'reference', mode: 'mock', side: 'graph on' } } };
const submit = text => {
  fireEvent.change(screen.getByLabelText('Question'), { target: { value: text } });
  fireEvent.click(screen.getByRole('button', { name: 'Ask', exact: true }));
};
const response = (question, history = []) => ({ question, principal: me.id, behaviour: 'figure', figure: 10, history });
const clipboardDescriptor = Object.getOwnPropertyDescriptor(navigator, 'clipboard');
const scrollDescriptor = Object.getOwnPropertyDescriptor(Element.prototype, 'scrollIntoView');
afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  window.localStorage?.clear();
  window.sessionStorage?.clear();
  if (clipboardDescriptor) Object.defineProperty(navigator, 'clipboard', clipboardDescriptor);
  else delete navigator.clipboard;
  if (scrollDescriptor) Object.defineProperty(Element.prototype, 'scrollIntoView', scrollDescriptor);
  else delete Element.prototype.scrollIntoView;
});

test.each([response('Revenue?'), { error: 'Answer unavailable', status: 503 }])('a native click immediately renders the pending Ask state until the API settles: %j', async answer => {
  let finish;
  const api = vi.fn(() => new Promise(resolve => { finish = resolve; }));
  const { container } = render(<Ask api={api} me={me} presets={presets} tester />);
  fireEvent.change(screen.getByLabelText('Question'), { target: { value: 'Revenue?' } });
  const button = container.querySelector('#ask');
  act(() => {
    button.click();
    expect(button.disabled).toBe(true);
    expect(button.textContent).toBe('Asking…');
    expect(container.querySelector('#thread .exchange .skeleton[aria-busy="true"]')).toHaveTextContent('Working out the answer…');
  });
  expect(api).toHaveBeenCalledTimes(1);
  await act(async () => finish(answer));
  expect(button.disabled).toBe(false);
  expect(button.textContent).toBe('Ask');
  expect(container.querySelector('.skeleton')).toBeNull();
  expect(container.querySelector('#thread .exchange .msg:not(.q)')).toHaveTextContent(answer.error || '10');
});

test('a conversation stops at eight history turns, preserves the question and resumes after restarting', async () => {
  const turns = [];
  const api = vi.fn(async (path, body) => {
    const history = body.history || [];
    if (history.length > 8) return { error: 'history: List should have at most 8 items', status: 422 };
    turns.push({ question: body.question });
    return response(body.question, [...history, turns.at(-1)]);
  });
  const { container } = render(<Ask api={api} me={me} presets={presets} tester />);
  for (let n = 1; n <= 8; n++) {
    submit(`Revenue question ${n}?`);
    await waitFor(() => expect(screen.getByRole('button', { name: 'Ask', exact: true })).not.toBeDisabled());
    expect(api).toHaveBeenCalledTimes(n);
    expect(api.mock.calls[n - 1][1].history || []).toEqual(turns.slice(0, -1));
    expect(container.querySelectorAll('#thread .exchange')).toHaveLength(n);
    expect(container.querySelector('#thread .exchange .answer-card')).toHaveTextContent('10');
  }
  expect(container.querySelector('#conversation')).not.toHaveAttribute('hidden');
  expect(container.querySelector('#conversation-text')).toHaveTextContent('This conversation has reached its eight follow-ups. Start a new conversation to continue.');
  expect(container.querySelector('#new-conversation')).toBeNull();
  expect(screen.getByTestId('rail-new-conversation')).toBeVisible();
  submit('Revenue question 9?');
  expect(api).toHaveBeenCalledTimes(8);
  expect(container.querySelectorAll('#thread .exchange')).toHaveLength(8);
  expect(screen.getByLabelText('Question')).toHaveValue('Revenue question 9?');
  expect(container).not.toHaveTextContent('List should have at most 8 items');
  fireEvent.click(screen.getByTestId('rail-new-conversation'));
  expect(container.querySelector('#conversation')).toHaveAttribute('hidden');
  expect(screen.getByLabelText('Question')).toHaveValue('Revenue question 9?');
  fireEvent.click(screen.getByRole('button', { name: 'Ask', exact: true }));
  await waitFor(() => expect(screen.getByRole('button', { name: 'Ask', exact: true })).not.toBeDisabled());
  expect(api).toHaveBeenCalledTimes(9);
  expect(api).toHaveBeenLastCalledWith('/ask', { question: 'Revenue question 9?', purpose: 'analysis', arm: 'graph' });
});

test('Ask ignores whitespace, prevents duplicate pending requests and lets an example submit a question', async () => {
  let finish;
  const api = vi.fn(() => new Promise(resolve => { finish = resolve; }));
  const { container } = render(<Ask api={api} me={me} presets={presets} golden={{ sets: [{ name: 'golden', version: '1', questions: ['Revenue?'] }] }} tester />);
  submit('  ');
  expect(api).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('button', { name: 'Examples ▾' }));
  fireEvent.click(screen.getByRole('button', { name: 'Revenue?' }));
  expect(screen.getByRole('button', { name: 'Asking…' })).toBeDisabled();
  expect(container.querySelector('.skeleton')).toHaveTextContent('Working out the answer…');
  fireEvent.submit(container.querySelector('form'));
  expect(api).toHaveBeenCalledExactlyOnceWith('/ask', { question: 'Revenue?', purpose: 'analysis', arm: 'graph' });
  await act(async () => finish(response('Revenue?')));
  expect(screen.getByRole('button', { name: 'Ask', exact: true })).not.toBeDisabled();
  expect(container.querySelector('#examples')).not.toBeVisible();
  expect(screen.getByRole('button', { name: 'Examples ▾' })).toHaveAttribute('aria-expanded', 'false');
});

test('each completed answer receives focus without scrolling and pending requests do not focus older answers', async () => {
  let finish;
  const api = vi.fn(() => new Promise(resolve => { finish = resolve; }));
  const { container } = render(<Ask api={api} me={me} presets={presets} tester />);
  const button = screen.getByRole('button', { name: 'Ask', exact: true });
  button.focus();
  submit('Revenue?');
  expect(button).toBeDisabled();
  await act(async () => finish(response('Revenue?')));
  const first = container.querySelector('.answer-card');
  expect(first).toHaveFocus();
  const oldFocus = vi.spyOn(first, 'focus');
  const input = screen.getByLabelText('Question'); input.focus();
  submit('And last month?');
  expect(input).toHaveFocus();
  expect(oldFocus).not.toHaveBeenCalled();
  const focus = vi.spyOn(HTMLElement.prototype, 'focus');
  await act(async () => finish(response('And last month?')));
  expect(container.querySelector('.exchange .answer-card')).toHaveFocus();
  expect(first).not.toHaveFocus();
  expect(focus).toHaveBeenCalledExactlyOnceWith({ preventScroll: true });
  focus.mockRestore(); oldFocus.mockRestore();
});

test.each([false, true])('new answers scroll to their start and respect a reader who scrolled up=%s', async scrolledUp => {
  vi.stubGlobal('innerWidth', 1024);
  const scroll = vi.fn();
  Object.defineProperty(Element.prototype, 'scrollIntoView', { configurable: true, value: scroll });
  let finish;
  const api = vi.fn(() => new Promise(resolve => { finish = resolve; }));
  const { container } = render(<><header className="gb-bar" /><Ask api={api} me={me} presets={presets} /></>);
  Object.defineProperty(container.querySelector('header'), 'offsetHeight', { value: 61 });
  submit('Revenue?');
  expect(scroll).toHaveBeenLastCalledWith({ block: 'nearest', behavior: 'auto' });
  expect(scroll.mock.contexts.at(-1)).toBe(container.querySelector('.exchange'));
  scroll.mockClear();
  if (scrolledUp) {
    const thread = container.querySelector('#thread');
    thread.scrollTop = -100;
    fireEvent.scroll(thread);
  }
  await act(async () => finish(response('Revenue?')));
  const answer = container.querySelector('.answer-card');
  expect(answer).toHaveFocus();
  if (scrolledUp) expect(scroll).not.toHaveBeenCalled();
  else {
    expect(scroll).toHaveBeenCalledExactlyOnceWith({ block: 'start', behavior: 'auto' });
    expect(scroll.mock.contexts[0]).toBe(answer);
    expect(answer.style.scrollMarginTop).toBe('73px');
  }
});

test.each([69, 161])('wide Ask positions answers directly in a %spx thread after layout and keeps following until the reader scrolls up', async height => {
  vi.stubGlobal('innerWidth', 1360);
  const frames = [];
  vi.stubGlobal('requestAnimationFrame', vi.fn(callback => frames.push(callback)));
  const scroll = vi.fn();
  Object.defineProperty(Element.prototype, 'scrollIntoView', { configurable: true, value: scroll });
  let finish;
  const api = vi.fn(() => new Promise(resolve => { finish = resolve; }));
  const { container } = render(<Ask api={api} me={me} presets={presets} />);
  const thread = container.querySelector('#thread');
  vi.spyOn(thread, 'getBoundingClientRect').mockReturnValue({ top: 200, bottom: 200 + height, height });
  submit('Revenue?');
  expect(frames).toHaveLength(1);
  thread.scrollTop = -20;
  vi.spyOn(container.querySelector('.exchange'), 'getBoundingClientRect').mockReturnValue({ top: 220 });
  act(() => frames.shift()());
  expect(thread.scrollTop).toBe(0);
  await act(async () => finish({ ...response('Revenue?'), unit: 'leasing' }));
  const answer = container.querySelector('.answer-card');
  expect(answer).toHaveFocus();
  expect(answer.style.scrollMarginTop).toBe('0px');
  expect(frames).toHaveLength(1);
  thread.scrollTop = -240;
  const answerRect = vi.spyOn(answer, 'getBoundingClientRect').mockImplementation(() => ({ top: 160 - thread.scrollTop }));
  expect(answer.getBoundingClientRect().top).toBeGreaterThan(thread.getBoundingClientRect().bottom);
  act(() => frames.shift()());
  expect(thread.scrollTop).toBe(-40);
  expect(answer.getBoundingClientRect().top).toBe(thread.getBoundingClientRect().top);
  expect(scroll).not.toHaveBeenCalled();
  fireEvent.scroll(thread);
  fireEvent.click(screen.getByRole('link', { name: 'Revenue?' }));
  expect(frames).toHaveLength(1);
  answerRect.mockRestore();
  vi.spyOn(container.querySelector('.answer-card'), 'getBoundingClientRect').mockReturnValue({ top: 200 });
  act(() => frames.shift()());
  thread.scrollTop = -100;
  fireEvent.scroll(thread);
  fireEvent.click(screen.getByRole('link', { name: 'Revenue?' }));
  expect(frames).toHaveLength(0);
});

test('programmatic answer positioning keeps following exchange updates until the reader scrolls up', async () => {
  vi.stubGlobal('innerWidth', 1024);
  let thread;
  const scroll = vi.fn(function () { if (this.classList.contains('answer-card')) thread.scrollTop = -240; });
  Object.defineProperty(Element.prototype, 'scrollIntoView', { configurable: true, value: scroll });
  const api = vi.fn(async () => ({ ...response('Revenue?'), unit: 'leasing' }));
  const { container } = render(<Ask api={api} me={me} presets={presets} />);
  thread = container.querySelector('#thread');
  submit('Revenue?');
  await screen.findByLabelText('Answer');
  await waitFor(() => expect(thread.scrollTop).toBe(-240));
  fireEvent.scroll(thread);
  scroll.mockClear();
  fireEvent.click(screen.getByRole('link', { name: 'Revenue?' }));
  expect(scroll).toHaveBeenCalledExactlyOnceWith({ block: 'start', behavior: 'auto' });
  scroll.mockClear();
  thread.scrollTop = -340;
  fireEvent.scroll(thread);
  fireEvent.click(screen.getByRole('link', { name: 'Revenue?' }));
  expect(scroll).not.toHaveBeenCalled();
});

test.each([
  { unit: 'wealth' },
  { figures: [{ unit: 'leasing' }, { unit: 'wealth' }] },
  { plan: [{ unit: 'wealth' }] },
  { parts: { leasing: 10, wealth: 20 } },
  { citations: [{ n: 1, doc: 'policy', unit: 'wealth' }] },
  { run_trace: { data_reads: [{ unit: 'wealth' }] } },
  { context: { units: ['leasing', 'wealth'] } },
])('the rail drops an entire restored conversation after any turn loses access: %j', async evidence => {
  savePersonTurn(me.id, {
    conversationId: 'mixed', title: 'Mixed conversation', question: 'Earlier question?', units: ['leasing'],
    turns: [
      { id: 2, q: 'Latest readable question?', units: ['leasing'], a: { ...response('Latest readable question?'), unit: 'leasing' } },
      { id: 1, q: 'Earlier question?', units: ['leasing'], a: { ...response('Earlier question?'), ...evidence } },
    ],
  });
  savePersonTurn(me.id, {
    conversationId: 'readable', title: 'Readable conversation', question: 'Readable question?', units: ['leasing'],
    turns: [{ id: 3, q: 'Readable question?', units: ['leasing'], a: { ...response('Readable question?'), unit: 'leasing' } }],
  });
  const api = vi.fn();
  const { rerender, unmount } = render(<Ask api={api} me={{ ...me, reads: ['leasing', 'wealth'] }} presets={presets} />);
  expect(screen.getByRole('link', { name: 'Mixed conversation' })).toBeVisible();
  rerender(<Ask api={api} me={me} presets={presets} />);
  expect(screen.queryByRole('link', { name: 'Mixed conversation' })).toBeNull();
  fireEvent.click(screen.getByRole('link', { name: 'Readable conversation' }));
  expect(screen.getByLabelText('Answer')).toHaveTextContent('10');
  unmount();
  render(<Ask api={api} me={me} presets={presets} />);
  expect(screen.queryByRole('link', { name: 'Mixed conversation' })).toBeNull();
  expect(screen.getByRole('link', { name: 'Readable conversation' })).toBeVisible();
  expect(api).not.toHaveBeenCalled();
});

test.each(['sample', 'past'])('the rail checks %s question units even when the answer has no unit', source => {
  savePersonTurn(me.id, {
    conversationId: 'restricted', title: 'Restricted conversation', question: 'Restricted definition?', units: source === 'past' ? ['wealth'] : [],
    turns: [{ id: 1, q: 'Restricted definition?', units: source === 'past' ? ['wealth'] : [], a: { ...response('Restricted definition?'), behaviour: 'cite_certified', answer: 'Restricted definition' } }],
  });
  const golden = source === 'sample' ? { question_units: { 'Restricted definition?': ['wealth'] } } : {};
  render(<Ask api={vi.fn()} me={me} presets={presets} golden={golden} />);
  expect(screen.queryByRole('link', { name: 'Restricted conversation' })).toBeNull();
  expect(screen.getByText('None yet')).toBeVisible();
});

test('the rail hides legacy turns whose readable units cannot be established', () => {
  savePersonTurn(me.id, {
    conversationId: 'unknown', title: 'Unknown scope', question: 'Old definition?',
    turns: [{ id: 1, q: 'Old definition?', a: { ...response('Old definition?'), behaviour: 'cite_certified', answer: 'Old definition' } }],
  });
  render(<Ask api={vi.fn()} me={me} presets={presets} />);
  expect(screen.queryByRole('link', { name: 'Unknown scope' })).toBeNull();
});

test('a legacy turn cannot borrow readable units from a later identical question', () => {
  savePersonTurn(me.id, {
    conversationId: 'restricted-old', title: 'Old restricted answer', question: 'Revenue?',
    turns: [{ id: 1, q: 'Revenue?', a: response('Revenue?') }],
  });
  savePersonTurn(me.id, {
    conversationId: 'readable-later', title: 'Later retail answer', question: 'Revenue?', units: ['retail'],
    turns: [{ id: 2, q: 'Revenue?', units: ['retail'], a: response('Revenue?') }],
  });
  expect(loadPersonHistory(me.id).questions).toEqual([expect.objectContaining({ question: 'Revenue?', units: ['retail'] })]);
  render(<Ask api={vi.fn()} me={{ ...me, reads: ['retail'] }} presets={presets} />);
  expect(screen.queryByRole('link', { name: 'Old restricted answer' })).toBeNull();
  expect(screen.getByRole('link', { name: 'Later retail answer' })).toBeVisible();
});

test('a legacy turn with no stored units cannot borrow a readable golden sample scope', () => {
  savePersonTurn(me.id, {
    conversationId: 'legacy-retail', title: 'Legacy retail answer', question: 'Retail revenue?',
    turns: [{ id: 1, q: 'Retail revenue?', a: response('Retail revenue?') }],
  });
  const golden = {
    sets: [{ name: 'golden', version: '1', questions: ['Retail revenue?'] }],
    question_units: { 'Retail revenue?': ['retail'] },
  };
  render(<Ask api={vi.fn()} me={{ ...me, reads: ['retail'] }} presets={presets} golden={golden} />);
  fireEvent.click(screen.getByRole('button', { name: 'Examples ▾' }));
  expect(screen.getByRole('button', { name: 'Retail revenue?' })).toBeVisible();
  expect(screen.queryByRole('link', { name: 'Legacy retail answer' })).toBeNull();
  expect(screen.getByText('None yet')).toBeVisible();
  expect(screen.queryByLabelText('Answer')).toBeNull();
});

test('completed turns retain their own scope through storage, restore and a later follow-up', async () => {
  const api = vi.fn(async (_path, body) => response(body.question));
  const { unmount } = render(<Ask api={api} me={me} presets={presets} golden={{ question_units: { 'Leasing definition?': ['leasing'] } }} />);
  submit('Leasing definition?');
  await screen.findByLabelText('Answer');
  expect(loadPersonHistory(me.id).conversations[0].turns[0].units).toEqual(['leasing']);
  unmount();
  const { rerender } = render(<Ask api={api} me={{ ...me, reads: ['leasing', 'retail'] }} presets={presets} golden={{ question_units: { 'Retail definition?': ['retail'] } }} />);
  fireEvent.click(screen.getByRole('link', { name: 'Leasing definition?' }));
  submit('Retail definition?');
  await waitFor(() => expect(loadPersonHistory(me.id).conversations[0].turns).toEqual([
    expect.objectContaining({ q: 'Retail definition?', units: ['retail'] }),
    expect.objectContaining({ q: 'Leasing definition?', units: ['leasing'] }),
  ]));
  rerender(<Ask api={api} me={{ ...me, reads: ['retail'] }} presets={presets} />);
  expect(screen.queryByRole('link', { name: 'Leasing definition?' })).toBeNull();
});

test.each(['click', 'focus', 'answer focus', 'copy'])('an earlier exchange selected by %s restores its details without rewinding the conversation', async action => {
  const questions = ['NNA as reported', 'NNA as reported again', 'NNA restated', 'NNA latest'];
  const answers = questions.map((question, i) => ({ ...response(question, [{ turn_id: `turn-${i}` }]), figure: i + 1,
    sql: `SELECT ${i + 1} FROM flows`, unit: 'leasing', context: { term: 'NNA', context: 'nna' },
  }));
  const api = vi.fn(async (path, body) => path === '/map/NNA' ? { contexts: [{ id: 'nna', description: 'Certified NNA', currency: 'USD' }] } : answers.find(a => a.question === body.question) || response(body.question));
  const writeText = vi.fn(async () => {});
  Object.defineProperty(navigator, 'clipboard', { configurable: true, value: { writeText } });
  const { container } = render(<Ask api={api} me={me} presets={presets} tester />);
  for (const question of questions) {
    submit(question);
    await waitFor(() => expect(container.querySelector('#detail h2')).toHaveTextContent(question));
    await waitFor(() => expect(container.querySelector('#tab-map')).toHaveTextContent('Certified NNA'));
  }
  const exchanges = container.querySelectorAll('#thread .exchange'), earlier = exchanges[1], latest = exchanges[0];
  const current = () => [...exchanges].map(ex => ex.getAttribute('aria-current'));
  expect(current()).toEqual(['true', 'false', 'false', 'false']);
  const calls = api.mock.calls.length;
  if (action === 'click') fireEvent.click(earlier.querySelector('.fig'));
  else {
    const target = action === 'copy' ? earlier.querySelector('.copy') : action === 'answer focus' ? earlier.querySelector('.answer-card') : earlier;
    act(() => target.focus());
  }
  if (action === 'copy') {
    fireEvent.click(earlier.querySelector('.copy'));
    await waitFor(() => expect(writeText).toHaveBeenCalledWith('3.00'));
  }
  expect(current()).toEqual(['false', 'true', 'false', 'false']);
  expect(container.querySelector('#detail h2')).toHaveTextContent('NNA restated');
  fireEvent.click(screen.getByRole('tab', { name: 'Plan' }));
  expect(container.querySelector('#tab-plan')).toBeVisible();
  expect(container.querySelector('#tab-plan pre')).toHaveTextContent('SELECT 3 FROM flows');
  act(() => latest.focus());
  const enter = new KeyboardEvent('keydown', { key: 'Enter', bubbles: true, cancelable: true });
  fireEvent(latest, enter);
  expect(enter.defaultPrevented).toBe(true);
  expect(current()).toEqual(['true', 'false', 'false', 'false']);
  expect(container.querySelector('#detail h2')).toHaveTextContent('NNA latest');
  fireEvent.click(screen.getByRole('tab', { name: 'Plan' }));
  expect(container.querySelector('#tab-plan pre')).toHaveTextContent('SELECT 4 FROM flows');
  await waitFor(() => expect(container.querySelector('#tab-map')).toHaveTextContent('Certified NNA'));
  expect(api).toHaveBeenCalledTimes(calls);
  submit('And April?');
  await waitFor(() => expect(api).toHaveBeenLastCalledWith('/ask', { question: 'And April?', purpose: 'analysis', arm: 'graph', history: answers[3].history }));
  await waitFor(() => expect(container.querySelector('#detail h2')).toHaveTextContent('And April?'));
});

test('follow-ups send only the returned history and starting a new conversation clears it', async () => {
  const history = [{ turn_id: 'one', question: 'Revenue?' }];
  const api = vi.fn().mockResolvedValueOnce(response('Revenue?', history)).mockResolvedValueOnce(response('And last month?', [...history, { turn_id: 'two' }])).mockResolvedValue(response('Fresh?'));
  const { container } = render(<Ask api={api} me={me} presets={presets} tester />);
  submit(' Revenue? ');
  await waitFor(() => expect(container.querySelector('#conversation')).not.toHaveAttribute('hidden'));
  expect(container.querySelector('#conversation')).toHaveTextContent('last question.');
  submit('And last month?');
  await waitFor(() => expect(api).toHaveBeenCalledWith('/ask', { question: 'And last month?', purpose: 'analysis', arm: 'graph', history }));
  await waitFor(() => expect(container.querySelector('#conversation')).toHaveTextContent('last 2 questions.'));
  fireEvent.click(screen.getByRole('button', { name: 'New conversation' }));
  submit('Fresh?');
  await waitFor(() => expect(api).toHaveBeenLastCalledWith('/ask', { question: 'Fresh?', purpose: 'analysis', arm: 'graph' }));
});

test.each(['Purpose', 'Answered by'])('Answer settings %s changes clear conversation scope and remain collapsible', async label => {
  const history = [{ turn_id: 'one' }];
  const api = vi.fn().mockResolvedValueOnce(response('Revenue?', history)).mockResolvedValue(response('Fresh?'));
  const { container } = render(<Ask api={api} me={me} presets={presets} tester />);
  submit('Revenue?');
  await waitFor(() => expect(container.querySelector('#conversation')).not.toHaveAttribute('hidden'));
  fireEvent.click(container.querySelector('#settings summary'));
  expect(container.querySelector('#settings').open).toBe(true);
  fireEvent.change(screen.getByLabelText(label), { target: { value: label === 'Purpose' ? 'supervision' : 'schema' } });
  fireEvent.click(screen.getByLabelText('Rich answers'));
  expect(container.querySelector('#settings-now')).toHaveTextContent('rich answers');
  fireEvent.click(screen.getByLabelText('Rich answers'));
  expect(container.querySelector('#settings-now')).not.toHaveTextContent('rich answers');
  expect(container.querySelector('#conversation')).toHaveAttribute('hidden');
  fireEvent.click(container.querySelector('#settings summary'));
  expect(container.querySelector('#settings').open).toBe(false);
  submit('Fresh?');
  await waitFor(() => expect(api).toHaveBeenLastCalledWith(label === 'Purpose' ? '/ask' : '/answer', { question: 'Fresh?', purpose: label === 'Purpose' ? 'supervision' : 'analysis', arm: label === 'Purpose' ? 'graph' : 'schema' }));
});

test('changing scope while an answer is pending cannot install the old conversation', async () => {
  let finish;
  const api = vi.fn(() => new Promise(resolve => { finish = resolve; }));
  const { container } = render(<Ask api={api} me={me} presets={presets} tester />);
  submit('Revenue?');
  expect(screen.getByTestId('rail-new-conversation')).toBeDisabled();
  fireEvent.change(screen.getByLabelText('Purpose'), { target: { value: 'assistant' } });
  await act(async () => finish(response('Revenue?', [{ turn_id: 'old' }])));
  expect(screen.getByTestId('rail-new-conversation')).not.toBeDisabled();
  expect(container.querySelector('#conversation')).toHaveAttribute('hidden');
  expect(container.querySelector('#settings-now')).toHaveTextContent('assistant');
});

test('a rejected follow-up clears invalid history and previous exchanges remain selectable by keyboard', async () => {
  const api = vi.fn().mockResolvedValueOnce(response('Revenue?', [{ turn_id: 'one' }])).mockResolvedValueOnce({ error: 'History expired', status: 422 });
  const { container } = render(<Ask api={api} me={me} presets={presets} tester />);
  submit('Revenue?');
  await waitFor(() => expect(container.querySelector('.fig')).toBeTruthy());
  submit('And last month?');
  await waitFor(() => expect(screen.getByRole('alert')).toHaveTextContent('History expired'));
  expect(container.querySelector('#conversation')).toHaveAttribute('hidden');
  expect(container.querySelector('#tab-map')).toHaveTextContent('No answer to the last question: History expired');
  const earlier = container.querySelectorAll('.exchange')[1];
  fireEvent.keyDown(earlier, { key: 'Enter' });
  expect(earlier).toHaveAttribute('aria-current', 'true');
  expect(container.querySelector('#detail h2')).toHaveTextContent('Revenue?');
  fireEvent.click(container.querySelectorAll('.exchange')[0]);
  expect(container.querySelector('#tab-map')).toHaveTextContent('History expired');
});

test('clarification resolves only this conversation and sends the selected meaning', async () => {
  const history = [{ turn_id: 'one' }];
  const api = vi.fn().mockResolvedValueOnce({ question: 'Revenue?', principal: me.id, behaviour: 'clarify', history,
    clarification: { spans: [{ turn: 'one', start: 0, end: 7 }], choices: [{ id: 'gross', label: 'Gross revenue' }] },
  }).mockResolvedValue(response('Gross revenue?'));
  render(<Ask api={api} me={me} presets={presets} tester />);
  submit('Revenue?');
  const choice = await screen.findByRole('button', { name: 'Gross revenue' });
  await waitFor(() => expect(choice).not.toBeDisabled());
  fireEvent.click(choice);
  await waitFor(() => expect(api).toHaveBeenLastCalledWith('/ask', { question: 'Gross revenue?', purpose: 'analysis', arm: 'graph', history }));
});

test('an unavailable golden set shows its failure and an empty preset keeps the default answer route', async () => {
  const api = vi.fn(async () => response('Revenue?'));
  const { container } = render(<Ask api={api} me={me} golden={{ error: 'Examples unavailable' }} tester={false} />);
  fireEvent.click(screen.getByRole('button', { name: 'Examples ▾' }));
  expect(screen.getByText('Examples unavailable')).toBeVisible();
  expect(container.querySelector('#settings')).toHaveAttribute('hidden');
  submit('Revenue?');
  await waitFor(() => expect(api).toHaveBeenCalledWith('/answer', { question: 'Revenue?', purpose: 'analysis', arm: '' }));
});

test('suggestions while typing show at most two items, order Sample -> Past -> Glossary, filter to readable units, and support keyboard/click', async () => {
  const golden = {
    sets: [{ name: 'golden', version: '1', questions: ['Leasing revenue for June 2025?', 'Leasing revenue for May 2025?', 'Wealth secret revenue?', 'Shared revenue across leasing and wealth?'] }],
    question_units: {
      'Leasing revenue for June 2025?': ['leasing'],
      'Leasing revenue for May 2025?': ['leasing'],
      'Wealth secret revenue?': ['wealth'],
      'Shared revenue across leasing and wealth?': ['leasing', 'wealth'],
    },
    glossary: [
      { term: 'Leasing Yield', units: ['leasing'] },
      { term: 'Shared Term', units: ['leasing', 'wealth'] },
      { term: 'Private Wealth Fee', units: ['wealth'] },
    ],
    retention_days: 30,
  };
  const api = vi.fn(async (path, body) => ({ ...response(body?.question || 'Q'), unit: 'leasing' }));
  const { container, rerender } = render(<Ask api={api} me={me} presets={presets} golden={golden} tester />);
  const input = screen.getByLabelText('Question');

  fireEvent.change(input, { target: { value: 'Leasing' } });
  const options = container.querySelectorAll('.ask-suggestions [role="option"]');
  expect(options).toHaveLength(2);
  expect(options[0]).toHaveTextContent('Leasing revenue for June 2025?');
  expect(options[0]).toHaveTextContent('Sample question');
  expect(options[1]).toHaveTextContent('Leasing revenue for May 2025?');
  expect(container.querySelector('.composer-ghost')).toHaveTextContent('Leasing revenue for June 2025?');

  fireEvent.keyDown(input, { key: 'ArrowDown' });
  expect(options[1]).toHaveAttribute('aria-selected', 'true');
  fireEvent.keyDown(input, { key: 'ArrowUp' });
  expect(options[0]).toHaveAttribute('aria-selected', 'true');
  fireEvent.keyDown(input, { key: 'Tab' });
  expect(input.value).toBe('Leasing revenue for June 2025?');
  expect(container.querySelectorAll('.ask-suggestions [role="option"]')).toHaveLength(0);

  fireEvent.change(input, { target: { value: 'Private' } });
  expect(container.querySelectorAll('.ask-suggestions [role="option"]')).toHaveLength(0);
  fireEvent.change(input, { target: { value: 'Wealth' } });
  expect(container.querySelectorAll('.ask-suggestions [role="option"]')).toHaveLength(0);

  fireEvent.change(input, { target: { value: 'Shared' } });
  expect(container.querySelectorAll('.ask-suggestions [role="option"]')).toHaveLength(1);
  const sharedOption = container.querySelector('.ask-suggestions [role="option"]');
  expect(sharedOption).toHaveTextContent('What is Shared Term?');
  expect(sharedOption).toHaveTextContent('Glossary term');

  fireEvent.change(input, { target: { value: 'Yield' } });
  const glossaryOpts = container.querySelectorAll('.ask-suggestions [role="option"]');
  expect(glossaryOpts).toHaveLength(1);
  expect(glossaryOpts[0]).toHaveTextContent('What is Leasing Yield?');
  expect(glossaryOpts[0]).toHaveTextContent('Glossary term');
  fireEvent.keyDown(input, { key: 'Escape' });
  expect(container.querySelectorAll('.ask-suggestions [role="option"]')).toHaveLength(0);

  submit('Custom leasing question?');
  await waitFor(() => expect(api).toHaveBeenCalledWith('/ask', { question: 'Custom leasing question?', purpose: 'analysis', arm: 'graph' }));

  fireEvent.change(input, { target: { value: 'Custom' } });
  const pastOpts = container.querySelectorAll('.ask-suggestions [role="option"]');
  expect(pastOpts).toHaveLength(1);
  expect(pastOpts[0]).toHaveTextContent('Custom leasing question?');
  expect(pastOpts[0]).toHaveTextContent('You asked before');
  fireEvent.mouseDown(pastOpts[0]);
  await waitFor(() => expect(api).toHaveBeenCalledTimes(2));

  rerender(<Ask api={api} me={{ id: 'other@example.com', reads: ['leasing'] }} presets={presets} golden={golden} tester />);
  fireEvent.change(screen.getByLabelText('Question'), { target: { value: 'Custom' } });
  expect(container.querySelectorAll('.ask-suggestions [role="option"]')).toHaveLength(0);
});

test('Runtimes chip lists runtimes for everyone, marks certified, sends runtimes in order, and rail switches conversations', async () => {
  const api = vi.fn(async (path, body) => {
    if (path === '/runtimes') {
      return {
        runtimes: [
          { id: 'rt_cert', label: 'Certified BigQuery', certified: true, dialect: 'bigquery', tables: ['leasing.rent'] },
          { id: 'rt_shadow', label: 'Shadow BigQuery', certified: false, dialect: 'bigquery', tables: ['leasing.rent'] },
        ],
      };
    }
    return { ...response(body?.question || 'Q'), unit: 'leasing', path: { nodes: [{ id: 'question', kind: 'question', label: 'Q', state: 'done', time: '0.1 ms' }], edges: [] } };
  });
  const { container } = render(<Ask api={api} me={me} presets={presets} tester={false} />);
  const runtimesChip = container.querySelector('#runtimes-chip');
  expect(runtimesChip).not.toHaveAttribute('hidden');
  fireEvent.click(runtimesChip.querySelector('summary'));
  runtimesChip.open = true;
  fireEvent(runtimesChip, new Event('toggle'));

  await waitFor(() => expect(api).toHaveBeenCalledWith('/runtimes'));
  expect(await screen.findByText('Certified BigQuery')).toBeVisible();
  expect(screen.getByText('Certified')).toBeVisible();

  const checkboxes = container.querySelectorAll('#runtimes-chip input[type="checkbox"]');
  fireEvent.click(checkboxes[1]);
  fireEvent.click(checkboxes[0]);
  expect(container.querySelector('#runtimes-now')).toHaveTextContent('rt_shadow → rt_cert');

  submit('First conv question?');
  await waitFor(() => expect(api).toHaveBeenCalledWith('/ask', {
    question: 'First conv question?',
    purpose: 'analysis',
    arm: 'graph',
    runtimes: ['rt_shadow', 'rt_cert'],
  }));
  await waitFor(() => expect(container.querySelector('.activity-graph-link')).toBeTruthy());

  fireEvent.click(container.querySelector('.activity-graph-link'));
  expect(container.querySelector('#detail')).toBeVisible();
  expect(screen.getByRole('tab', { name: 'Graph' })).toHaveAttribute('aria-selected', 'true');

  fireEvent.click(screen.getByTestId('rail-new-conversation'));
  expect(container.querySelectorAll('#thread .exchange')).toHaveLength(0);
  expect(container.querySelector('#runtimes-now')).toHaveTextContent('certified default');

  const pastLink = container.querySelector('.rail-conv-btn');
  expect(pastLink).toHaveTextContent('First conv question?');
  fireEvent.click(pastLink);
  expect(container.querySelectorAll('#thread .exchange')).toHaveLength(1);
  expect(container.querySelector('#runtimes-now')).toHaveTextContent('rt_shadow → rt_cert');

  submit('Follow-up after restore?');
  await waitFor(() => expect(container.querySelectorAll('#thread .exchange')).toHaveLength(2));
  const ids = [...container.querySelectorAll('#thread .exchange')].map(el => el.getAttribute('data-exchange-id'));
  expect(ids).toEqual([expect.stringMatching(/^\d+$/), '1']);
  expect(ids[0]).not.toBe(ids[1]);
});

const spineEvents = question => [
  { event: 'node_added', node: { id: 'question', kind: 'question', label: question, state: 'running', detail: 'Q' } },
  { event: 'node_added', node: { id: 'terms', kind: 'terms', label: 'Terms & period', state: 'waiting', detail: 'T' } },
  { event: 'edge_added', edge: { from: 'question', to: 'terms', label: '' } },
];
const gNode = (type, name, group, extra = {}) => ({ id: `${type}:${name}`, type, name, label: name, group, used: true, state: 'done', certified: false, badges: [], detail: `${type} ${name}`, ...extra });
const gEdge = (from, to, type, extra = {}) => ({ from, to, type, label: type, planner: false, used: true, state: 'done', ...extra });
const streamingApi = () => {
  const stream = {};
  const api = vi.fn((path, body, opts) => {
    stream.opts = opts;
    return new Promise(resolve => { stream.finish = resolve; });
  });
  return { api, stream };
};

test('spine events drive the live activity line while the Graph tab says the path is being built, and Stop aborts the running query', async () => {
  const { api, stream } = streamingApi();
  const { container } = render(<Ask api={api} me={me} presets={presets} tester />);
  submit('Streaming question?');

  expect(stream.opts).toBeTruthy();
  expect(screen.getByRole('button', { name: 'Stop' })).toBeVisible();
  act(() => { spineEvents('Streaming question?').forEach(stream.opts.onEvent); });

  expect(screen.getByTestId('live-activity-line')).toHaveTextContent('Working out the answer… · Streaming question?');
  expect(screen.getByRole('tab', { name: 'Graph' })).toHaveAttribute('aria-selected', 'true');
  expect(container.querySelector('[data-node-id="question"]')).toBeNull();
  expect(container.querySelector('#tab-graph .graph-building')).toHaveAttribute('aria-busy', 'true');
  expect(container.querySelector('#tab-graph')).toHaveTextContent('Building the graph path…');

  act(() => {
    stream.opts.onEvent({ event: 'state_changed', id: 'question', state: 'done', time: '0.1 ms' });
    stream.opts.onEvent({ event: 'state_changed', id: 'terms', state: 'running', node: { id: 'terms', kind: 'terms', label: 'revenue · 2025-06', state: 'running', detail: 'T' } });
  });
  expect(screen.getByTestId('live-activity-line')).toHaveTextContent('Working out the answer… · revenue · 2025-06');
  expect(container.querySelector('#tab-graph')).toHaveTextContent('Building the graph path…');

  fireEvent.click(screen.getByRole('button', { name: 'Stop' }));
  expect(stream.opts.signal.aborted).toBe(true);
  expect(screen.queryByRole('button', { name: 'Stop' })).toBeNull();
  expect(container.querySelector('.answer-card')).toHaveTextContent('Stopped.');
  fireEvent.click(screen.getByRole('tab', { name: 'Graph' }));
  expect(container.querySelector('#tab-graph')).toHaveTextContent('No graph path was recorded for this answer.');

  await act(async () => stream.finish({ error: 'Stopped.', cancelled: true }));
});

test('graph events arrive after the spine, just before the answer, and fill the Graph tab without moving the reader', async () => {
  const { api, stream } = streamingApi();
  const { container } = render(<Ask api={api} me={me} presets={presets} tester />);
  submit('Revenue for June?');
  act(() => {
    spineEvents('Revenue for June?').forEach(stream.opts.onEvent);
    stream.opts.onEvent({ event: 'state_changed', id: 'question', state: 'done', time: '0.1 ms' });
    stream.opts.onEvent({ event: 'state_changed', id: 'terms', state: 'done', time: '0.2 ms' });
  });
  expect(container.querySelector('#tab-graph')).toHaveTextContent('Building the graph path…');

  const words = gNode('Words', 'revenue', 'words'), term = gNode('Term', 'Revenue', 'vocabulary'), ctx = gNode('Context', 'revenue', 'meaning');
  const matches = gEdge(words.id, term.id, 'matches', { planner: true }), resolves = gEdge(term.id, ctx.id, 'RESOLVES_TO');
  act(() => {
    stream.opts.onEvent({ event: 'graph_summary', unused: 4 });
  });
  expect(container.querySelector('#tab-graph')).toHaveTextContent('Building the graph path…');
  act(() => {
    stream.opts.onEvent({ event: 'graph_node_added', node: words });
    stream.opts.onEvent({ event: 'graph_node_added', node: term });
    stream.opts.onEvent({ event: 'graph_edge_added', edge: matches });
    stream.opts.onEvent({ event: 'graph_edge_added', edge: matches });
    stream.opts.onEvent({ event: 'graph_node_added', node: { ...term, certified: true } });
    stream.opts.onEvent({ event: 'graph_node_added', node: ctx });
    stream.opts.onEvent({ event: 'graph_edge_added', edge: resolves });
  });
  const graph = container.querySelector('#tab-graph');
  expect(screen.getByRole('tab', { name: 'Graph' })).toHaveAttribute('aria-selected', 'true');
  expect(graph.querySelectorAll('[data-node-id]')).toHaveLength(3);
  expect(graph.querySelector('[data-node-id="Term:Revenue"]')).toHaveClass('certified');
  expect(graph.querySelectorAll('.gedge')).toHaveLength(2);
  expect(graph.querySelector('[data-edge="Words:revenue->Term:Revenue"]')).toHaveAttribute('data-planner', 'true');
  expect(graph.querySelector('#path-detail')).toHaveTextContent('Selected: Context revenue');
  expect(screen.getByRole('checkbox', { name: 'Also show what was read but not used (4)' })).toBeEnabled();

  const graph_path = { nodes: [words, { ...term, certified: true }, ctx], edges: [matches, resolves], unused: 0 };
  await act(async () => stream.finish({ ...response('Revenue for June?'), graph_path }));
  expect(screen.getByRole('tab', { name: 'Graph' })).toHaveAttribute('aria-selected', 'true');
  expect(container.querySelectorAll('#tab-graph [data-node-id]')).toHaveLength(3);
  expect(container.querySelector('#tab-graph')).toHaveTextContent('The path this answer read: 3 nodes');
});

test('the tab the reader chose while the answer streamed is kept when it lands', async () => {
  const { api, stream } = streamingApi();
  const { container } = render(<Ask api={api} me={me} presets={presets} tester />);
  submit('Revenue for June?');
  act(() => { spineEvents('Revenue for June?').forEach(stream.opts.onEvent); });
  fireEvent.click(screen.getByRole('tab', { name: 'Trace' }));
  act(() => { stream.opts.onEvent({ event: 'state_changed', id: 'question', state: 'done', time: '0.1 ms' }); });
  expect(screen.getByRole('tab', { name: 'Trace' })).toHaveAttribute('aria-selected', 'true');
  await act(async () => stream.finish({ ...response('Revenue for June?'), graph_path: { nodes: [gNode('Words', 'revenue', 'words')], edges: [], unused: 0 } }));
  expect(screen.getByRole('tab', { name: 'Trace' })).toHaveAttribute('aria-selected', 'true');
});

const zeroBox = { left: 0, top: 0, right: 0, bottom: 0, width: 0, height: 0, x: 0, y: 0 };
const workspaceWide = width => vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockImplementation(function box() {
  return this.classList?.contains('ask-workspace') ? { ...zeroBox, right: width, width, height: 600, bottom: 600 } : zeroBox;
});

test('the conversation rail folds to a strip and back, and the choice is remembered', () => {
  const api = vi.fn(async () => ({}));
  const { container, unmount } = render(<Ask api={api} me={me} presets={presets} tester />);
  const fold = screen.getByRole('button', { name: 'Hide conversations' });
  expect(fold).toHaveAttribute('aria-expanded', 'true');
  expect(fold).toHaveAttribute('aria-controls', 'ask-rail-body');
  expect(container.querySelector('#ask-rail-body')).toBeTruthy();
  expect(screen.queryByTestId('rail-new-conversation-icon')).toBeNull();
  fireEvent.click(fold);
  const unfold = screen.getByRole('button', { name: 'Show conversations' });
  expect(unfold).toHaveAttribute('aria-expanded', 'false');
  expect(container.querySelector('.ask-shell')).toHaveClass('rail-folded');
  expect(container.querySelector('.ask-rail')).toHaveClass('folded');
  expect(screen.getByTestId('rail-new-conversation-icon')).toHaveAccessibleName('New conversation');
  expect(window.localStorage.getItem(layoutKey(RAIL_FOLDED_KEY, me.id))).toBe('1');
  unmount();
  const again = render(<Ask api={api} me={me} presets={presets} tester />);
  expect(screen.getByRole('button', { name: 'Show conversations' })).toHaveAttribute('aria-expanded', 'false');
  fireEvent.click(screen.getByRole('button', { name: 'Show conversations' }));
  expect(again.container.querySelector('.ask-shell')).not.toHaveClass('rail-folded');
  expect(window.localStorage.getItem(layoutKey(RAIL_FOLDED_KEY, me.id))).toBe('0');
});

test('the rail and the splitter work when the browser refuses storage', () => {
  const storage = Object.getOwnPropertyDescriptor(window, 'localStorage');
  Object.defineProperty(window, 'localStorage', { configurable: true, get() { throw new Error('storage blocked'); } });
  try {
    workspaceWide(1012);
    render(<Ask api={vi.fn(async () => ({}))} me={me} presets={presets} tester />);
    fireEvent.click(screen.getByRole('button', { name: 'Hide conversations' }));
    expect(screen.getByRole('button', { name: 'Show conversations' })).toBeVisible();
    const splitter = screen.getByRole('separator');
    fireEvent.keyDown(splitter, { key: 'ArrowRight' });
    expect(splitter).toHaveAttribute('aria-valuenow', '59');
  } finally {
    if (storage) Object.defineProperty(window, 'localStorage', storage);
    else delete window.localStorage;
  }
});

test('the splitter resizes the chat and the panel by keyboard and pointer, within its limits, and resets', () => {
  workspaceWide(1012);
  const { container } = render(<Ask api={vi.fn(async () => ({}))} me={me} presets={presets} tester />);
  const splitter = screen.getByRole('separator', { name: 'Resize the chat and Behind the answer' });
  const workspace = container.querySelector('.ask-workspace');
  expect(splitter).toHaveAttribute('aria-orientation', 'vertical');
  expect(splitter).toHaveAttribute('tabindex', '0');
  expect(splitter.previousElementSibling).toHaveClass('ask-main-col');
  expect(splitter.nextElementSibling).toHaveClass('ask-behind-col');
  expect([splitter.getAttribute('aria-valuenow'), splitter.getAttribute('aria-valuemin'), splitter.getAttribute('aria-valuemax')]).toEqual(['57', '37', '65']);
  expect(workspace.style.getPropertyValue('--ask-chat')).toBe('57fr');
  expect(workspace.style.getPropertyValue('--ask-behind')).toBe('43fr');
  const key = name => fireEvent.keyDown(splitter, { key: name });
  key('ArrowRight');
  expect(splitter).toHaveAttribute('aria-valuenow', '59');
  expect(window.localStorage.getItem(layoutKey(CHAT_SHARE_KEY, me.id))).toBe('59');
  key('ArrowLeft'); key('ArrowLeft');
  expect(splitter).toHaveAttribute('aria-valuenow', '55');
  key('End'); key('ArrowRight');
  expect(splitter).toHaveAttribute('aria-valuenow', '65');
  key('Home'); key('ArrowLeft');
  expect(splitter).toHaveAttribute('aria-valuenow', '37');
  expect(workspace.style.getPropertyValue('--ask-chat')).toBe('37fr');
  key('Enter');
  expect(splitter).toHaveAttribute('aria-valuenow', '57');
  key('Home');
  fireEvent.doubleClick(splitter);
  expect(splitter).toHaveAttribute('aria-valuenow', '57');
  fireEvent.pointerDown(splitter, { button: 0, pointerId: 1, clientX: 600 });
  fireEvent.pointerMove(splitter, { pointerId: 1, clientX: 506 });
  expect(splitter).toHaveAttribute('aria-valuenow', '50');
  expect(splitter).toHaveClass('dragging');
  fireEvent.pointerMove(splitter, { pointerId: 1, clientX: 5 });
  expect(splitter).toHaveAttribute('aria-valuenow', '37');
  fireEvent.pointerUp(splitter, { pointerId: 1, clientX: 506 });
  expect(splitter).toHaveAttribute('aria-valuenow', '50');
  expect(splitter).not.toHaveClass('dragging');
  expect(window.localStorage.getItem(layoutKey(CHAT_SHARE_KEY, me.id))).toBe('50');
  fireEvent.pointerMove(splitter, { pointerId: 1, clientX: 900 });
  expect(splitter).toHaveAttribute('aria-valuenow', '50');
});

test('a remembered split outside the limits is clamped when the page opens', () => {
  window.localStorage.setItem(layoutKey(CHAT_SHARE_KEY, me.id), '99');
  workspaceWide(1012);
  render(<Ask api={vi.fn(async () => ({}))} me={me} presets={presets} tester />);
  expect(screen.getByRole('separator')).toHaveAttribute('aria-valuenow', '65');
});


test('layout preferences belong to the viewer and reload when the viewer changes', () => {
  workspaceWide(1012);
  const api = vi.fn(async () => ({}));
  const { rerender } = render(<Ask api={api} me={me} presets={presets} tester />);
  fireEvent.click(screen.getByRole('button', { name: 'Hide conversations' }));
  fireEvent.keyDown(screen.getByRole('separator'), { key: 'ArrowRight' });
  const viewerB = { ...me, id: 'viewer-b@example.com' };
  rerender(<Ask api={api} me={viewerB} presets={presets} tester />);
  expect(screen.getByRole('button', { name: 'Hide conversations' })).toBeVisible();
  expect(screen.getByRole('separator')).toHaveAttribute('aria-valuenow', '57');
  fireEvent.keyDown(screen.getByRole('separator'), { key: 'ArrowLeft' });
  expect(window.localStorage.getItem(layoutKey(CHAT_SHARE_KEY, viewerB.id))).toBe('55');
  rerender(<Ask api={api} me={me} presets={presets} tester />);
  expect(screen.getByRole('button', { name: 'Show conversations' })).toBeVisible();
  expect(screen.getByRole('separator')).toHaveAttribute('aria-valuenow', '59');
});

function ndjsonFeed() {
  const queued = [], waiting = [];
  const take = value => waiting.length ? waiting.shift()(value) : queued.push(value);
  return {
    reader: { read: () => queued.length ? Promise.resolve(queued.shift()) : new Promise(resolve => waiting.push(resolve)) },
    send: value => take({ done: false, value: new TextEncoder().encode(value) }),
    end: () => take({ done: true }),
  };
}

function StreamAsk() {
  const api = useContextApi();
  return <Ask api={api} me={me} presets={presets} tester />;
}

function setupNdjson(feed) {
  const map = { contexts: [{ id: 'revenue', label: 'Revenue', description: 'Certified revenue', measure: 'revenue', fact: 'rent' }], defined_by: [] };
  vi.stubGlobal('fetch', vi.fn(async url => url.includes('/map/')
    ? { ok: true, headers: { get: () => 'application/json' }, json: async () => map }
    : { ok: true, headers: { get: () => 'application/x-ndjson' }, body: { getReader: () => feed.reader } }));
}

const sendEvent = async (feed, event) => {
  await act(async () => { feed.send(`${JSON.stringify(event)}\n`); await Promise.resolve(); });
};

test('NDJSON fills Graph, Plan and Map before the answer, preserves the selected node, and shows warehouse progress', async () => {
  const feed = ndjsonFeed();
  setupNdjson(feed);
  const { container } = render(<StreamAsk />);
  submit('Revenue?');
  const words = gNode('Words', 'revenue', 'words');
  const runtime = gNode('Runtime', 'reference', 'runtime', { sql: 'SELECT 1 FROM rent' });
  const edge = gEdge(words.id, runtime.id, 'READS');
  await sendEvent(feed, { event: 'node_added', node: words });
  await sendEvent(feed, { event: 'node_added', node: runtime });
  await sendEvent(feed, { event: 'edge_added', edge });
  await sendEvent(feed, { event: 'state_changed', id: runtime.id, state: 'done', node: { ...runtime, certified: true } });
  expect(container.querySelector(`[data-node-id="${runtime.id}"]`)).toHaveClass('certified');
  fireEvent.click(container.querySelector(`[data-node-id="${words.id}"]`));
  await sendEvent(feed, { event: 'plan', plan: { unit: 'leasing', sql_source: 'query-plan-v1', guard: { passed: true, checks: ['Allowlist checked'] }, sql: 'SELECT 1 FROM rent' } });
  fireEvent.click(screen.getByRole('tab', { name: 'Plan' }));
  expect(container.querySelector('#tab-plan')).toHaveTextContent('SELECT 1 FROM rent');
  expect(container.querySelector('#tab-plan')).toHaveTextContent('Allowlist checked');
  await sendEvent(feed, { event: 'context', context: { id: 'revenue', label: 'Revenue', term: 'Revenue', context: 'revenue', fact: 'leasing.rent', measure: 'revenue', time_mode: 'current' } });
  fireEvent.click(screen.getByRole('tab', { name: 'Map' }));
  await waitFor(() => expect(container.querySelector('#tab-map')).toHaveTextContent('Certified revenue'));
  await sendEvent(feed, { event: 'progress', stage: 'warehouse', state: 'queued', elapsed_ms: 0, bytes_processed: null, statement: 1 });
  expect(container.querySelector('.warehouse-progress')).toHaveTextContent('queued · 0 ms · bytes unavailable');
  await sendEvent(feed, { event: 'progress', stage: 'warehouse', state: 'running', elapsed_ms: 3100, bytes_processed: 2500000, statement: 1 });
  expect(container.querySelector('.warehouse-progress')).toHaveTextContent('Statement 1 · running · 3.1 s · 2.5 MB');
  const live = container.querySelector('.warehouse-progress [role="status"]');
  expect(live).toHaveTextContent('Warehouse statement 1 running.');
  await sendEvent(feed, { event: 'progress', stage: 'warehouse', state: 'running', elapsed_ms: 6200, bytes_processed: 3000000, statement: 1 });
  expect(live).toHaveTextContent('Warehouse statement 1 running.');
  fireEvent.click(screen.getByRole('tab', { name: 'Graph' }));
  expect(container.querySelector(`[data-node-id="${runtime.id}"]`)).toHaveTextContent('6.2 s · 3 MB');
  const selectedNode = container.querySelector(`[data-node-id="${words.id}"]`);
  await sendEvent(feed, { event: 'progress', stage: 'warehouse', state: 'done', elapsed_ms: 6300, bytes_processed: 3000000, statement: 1 });
  expect(live).toHaveTextContent('Warehouse statement 1 done.');
  await sendEvent(feed, { event: 'graph_summary', unused: 1 });
  expect(container.querySelector(`[data-node-id="${runtime.id}"]`)).toHaveTextContent('Statement 1 · done');
  const answer = { ...response('Revenue?'), context: { id: 'revenue', label: 'Revenue', term: 'Revenue', context: 'revenue', fact: 'leasing.rent', measure: 'revenue', time_mode: 'current' }, unit: 'leasing', sql_source: 'query-plan-v1', guard: { passed: true, checks: ['Allowlist checked'] }, sql: 'SELECT 1 FROM rent', graph_path: { nodes: [words, { ...runtime, certified: true }], edges: [edge], unused: 1 } };
  const line = `${JSON.stringify({ event: 'answer', answer })}\n`;
  await act(async () => { feed.send(line.slice(0, 15)); await Promise.resolve(); });
  expect(container.querySelector('.answer-card')).toBeNull();
  await act(async () => { feed.send(line.slice(15)); feed.end(); await Promise.resolve(); });
  await waitFor(() => expect(container.querySelector('.answer-card')).toHaveTextContent('10'));
  expect(container.querySelector(`[data-node-id="${words.id}"]`)).toBe(selectedNode);
  expect(selectedNode).toHaveAttribute('aria-pressed', 'true');
  expect(container.querySelector('.warehouse-progress')).toBeNull();
  expect(fetch.mock.calls.filter(([url]) => url.includes('/map/'))).toHaveLength(1);
  const streamedGraph = container.querySelector('#tab-graph').textContent;
  const streamedPlan = container.querySelector('#tab-plan').textContent;
  const streamedMap = container.querySelector('#tab-map').textContent;
  fireEvent.click(screen.getByRole('tab', { name: 'Map' }));
  const direct = render(<BehindAnswer api={vi.fn(async () => ({ contexts: [{ id: 'revenue', label: 'Revenue', description: 'Certified revenue', measure: 'revenue', fact: 'rent' }], defined_by: [] }))} answer={answer} me={me} open onToggle={() => {}} onCurrency={() => {}} />);
  await waitFor(() => expect(direct.container.querySelector('#tab-map')).toHaveTextContent('Certified revenue'));
  expect(direct.container.querySelector('#tab-plan').textContent).toBe(streamedPlan);
  expect(direct.container.querySelector('#tab-map').textContent).toBe(streamedMap);
  fireEvent.click(within(direct.container).getByRole('tab', { name: 'Graph' }));
  fireEvent.click(direct.container.querySelector(`[data-node-id="${words.id}"]`));
  expect(direct.container.querySelector('#tab-graph').textContent).toBe(streamedGraph);
});

test('NDJSON associates each warehouse statement with the runtime recorded before it', async () => {
  const feed = ndjsonFeed();
  setupNdjson(feed);
  const { container } = render(<StreamAsk />);
  submit('Revenue?');
  const first = gNode('Runtime', 'first', 'runtime', { sql: 'SELECT 1' });
  const second = gNode('Runtime', 'second', 'runtime', { sql: 'SELECT 2' });
  await sendEvent(feed, { event: 'node_added', node: first });
  await sendEvent(feed, { event: 'progress', stage: 'warehouse', state: 'done', elapsed_ms: 1200, bytes_processed: 1000, statement: 1 });
  await sendEvent(feed, { event: 'node_added', node: second });
  const firstNode = container.querySelector(`[data-node-id="${first.id}"]`);
  const secondNode = container.querySelector(`[data-node-id="${second.id}"]`);
  expect(firstNode).toHaveTextContent('Statement 1 · done · 1.2 s · 1 KB');
  expect(secondNode.querySelector('.gnode-progress')).toBeNull();
  await sendEvent(feed, { event: 'progress', stage: 'warehouse', state: 'running', elapsed_ms: 3100, bytes_processed: 2000, statement: 2 });
  expect(firstNode).toHaveTextContent('Statement 1 · done · 1.2 s · 1 KB');
  expect(secondNode).toHaveTextContent('Statement 2 · running · 3.1 s · 2 KB');
  await sendEvent(feed, { event: 'progress', stage: 'warehouse', state: 'done', elapsed_ms: 1300, bytes_processed: 1000, statement: 1 });
  expect(firstNode).toHaveTextContent('Statement 1 · done · 1.3 s');
  expect(secondNode).toHaveTextContent('Statement 2 · running');
  await sendEvent(feed, { event: 'progress', stage: 'warehouse', state: 'done', elapsed_ms: 3300, bytes_processed: 3000, statement: 2 });
  expect(firstNode).toHaveTextContent('Statement 1 · done');
  expect(secondNode).toHaveTextContent('Statement 2 · done · 3.3 s · 3 KB');
  fireEvent.click(screen.getByRole('button', { name: 'Stop' }));
  await act(async () => { feed.end(); await Promise.resolve(); });
});

test('NDJSON updates the same runtime when consecutive statements use it', async () => {
  const feed = ndjsonFeed();
  setupNdjson(feed);
  const { container } = render(<StreamAsk />);
  submit('Revenue?');
  const runtime = gNode('Runtime', 'reference', 'runtime', { sql: 'SELECT 1' });
  await sendEvent(feed, { event: 'node_added', node: runtime });
  await sendEvent(feed, { event: 'progress', stage: 'warehouse', state: 'done', elapsed_ms: 100, bytes_processed: null, statement: 1 });
  await sendEvent(feed, { event: 'state_changed', id: runtime.id, state: 'done', node: { ...runtime, sql: 'SELECT 2' } });
  await sendEvent(feed, { event: 'progress', stage: 'warehouse', state: 'running', elapsed_ms: 200, bytes_processed: null, statement: 2 });
  expect(container.querySelectorAll('[data-node-id="Runtime:reference"]')).toHaveLength(1);
  expect(container.querySelector('[data-node-id="Runtime:reference"]')).toHaveTextContent('Statement 2 · running · 200 ms');
  fireEvent.click(screen.getByRole('button', { name: 'Stop' }));
  await act(async () => { feed.end(); await Promise.resolve(); });
});

test.each(['error', 'cancel'])('NDJSON keeps a refusal edge and rules plan when the stream stops: %s', async ending => {
  const feed = ndjsonFeed();
  setupNdjson(feed);
  const { container } = render(<StreamAsk />);
  submit('Revenue?');
  const model = gNode('Plan', 'model', 'meaning');
  const check = gNode('Check', 'model', 'rules', { state: 'refused', reason: 'Missing filter' });
  const edge = gEdge(model.id, check.id, 'CHECKS', { state: 'refused', reason: 'Missing filter' });
  await sendEvent(feed, { event: 'node_added', node: model });
  await sendEvent(feed, { event: 'node_added', node: check });
  await sendEvent(feed, { event: 'edge_added', edge });
  expect(container.querySelector('.gedge.refused')).toBeInTheDocument();
  await sendEvent(feed, { event: 'plan', plan: { unit: 'leasing', sql_source: 'query-plan-v1', guard: { passed: true, checks: [] }, sql: 'SELECT 1 FROM rent' } });
  await sendEvent(feed, { event: 'context', context: { term: 'Revenue', context: 'revenue' } });
  await waitFor(() => expect(container.querySelector('#tab-map')).toHaveTextContent('Certified revenue'));
  if (ending === 'cancel') fireEvent.click(screen.getByRole('button', { name: 'Stop' }));
  else await sendEvent(feed, { event: 'error', error: 'Warehouse unavailable' });
  await waitFor(() => expect(container.querySelector('.warehouse-progress')).toHaveTextContent('Stopped'));
  expect(container.querySelector('#tab-plan')).toHaveTextContent('SELECT 1 FROM rent');
  expect(container.querySelector('#tab-map')).toHaveTextContent('Certified revenue');
  expect(container.querySelector('.gedge.refused')).toBeInTheDocument();
  await act(async () => { feed.end(); await Promise.resolve(); });
});

test('a reloaded Context resumes its own pending run by id without posting another question', async () => {
  const id = 'b'.repeat(32), question = 'Revenue after reconnect?';
  window.sessionStorage.setItem(layoutKey('kge.ask.pendingRun', me.id), JSON.stringify({ id, question }));
  const api = vi.fn(async () => response(question));
  const { container } = render(<Ask api={api} me={me} presets={presets} tester />);
  await waitFor(() => expect(container.querySelector('.answer-card')).toHaveTextContent('10'));
  expect(api).toHaveBeenCalledTimes(1);
  expect(api).toHaveBeenCalledWith(`/ask/runs/${id}`, undefined, expect.objectContaining({ signal: expect.any(AbortSignal) }));
  expect(window.sessionStorage.getItem(layoutKey('kge.ask.pendingRun', me.id))).toBe('');
});

test.each(['localStorage', 'sessionStorage'])('Ask completes when the browser blocks %s', async storage => {
  vi.spyOn(window, storage, 'get').mockImplementation(() => { throw new DOMException('Storage blocked', 'SecurityError'); });
  const api = vi.fn(async (_path, body, options) => {
    options?.onEvent?.({ event: 'run', id: 'c'.repeat(32) });
    return response(body.question);
  });
  const { container } = render(<Ask api={api} me={me} presets={presets} tester />);
  submit('Revenue with blocked storage?');
  await waitFor(() => expect(container.querySelector('.answer-card')).toHaveTextContent('10'));
  expect(screen.getByRole('button', { name: 'Ask', exact: true })).not.toBeDisabled();
  expect(api).toHaveBeenCalledExactlyOnceWith('/ask', expect.objectContaining({ question: 'Revenue with blocked storage?' }),
    expect.objectContaining({ onEvent: expect.any(Function), signal: expect.any(AbortSignal) }));
});

test('a failed reconnect preserves the run id for the next reload', async () => {
  const id = 'd'.repeat(32), question = 'Revenue after an interrupted reconnect?';
  const key = layoutKey('kge.ask.pendingRun', me.id);
  window.sessionStorage.setItem(key, JSON.stringify({ id, question }));
  const unavailable = vi.fn(async () => ({ error: 'Temporarily unavailable', status: 503 }));
  const first = render(<Ask api={unavailable} me={me} presets={presets} tester />);
  await waitFor(() => expect(first.container).toHaveTextContent('Temporarily unavailable'));
  expect(JSON.parse(window.sessionStorage.getItem(key)).id).toBe(id);
  first.unmount();
  const recovered = vi.fn(async () => response(question));
  const second = render(<Ask api={recovered} me={me} presets={presets} tester />);
  await waitFor(() => expect(second.container.querySelector('.answer-card')).toHaveTextContent('10'));
  expect(recovered).toHaveBeenCalledExactlyOnceWith(`/ask/runs/${id}`, undefined,
    expect.objectContaining({ signal: expect.any(AbortSignal) }));
  expect(window.sessionStorage.getItem(key)).toBe('');
});

test('a held background answer streams its stages to the Trace tab and its running step to the activity line and the Graph, then follows its live poll to the answer', async () => {
  const start = ndjsonFeed(), poll = ndjsonFeed();
  const id = 'c'.repeat(32);
  const asked = [];
  vi.stubGlobal('fetch', vi.fn(async (url, init) => {
    asked.push({ url, accept: init?.headers?.accept });
    if (url.includes('/map/')) return { ok: true, headers: { get: () => 'application/json' }, json: async () => ({ contexts: [], defined_by: [] }) };
    return { ok: true, headers: { get: () => 'application/x-ndjson' }, body: { getReader: () => (url.includes('/ask/runs/') ? poll : start).reader } };
  }));
  const { container } = render(<StreamAsk />);
  submit('Revenue?');
  const runtime = gNode('Runtime', 'reference', 'runtime');
  await sendEvent(start, { event: 'run', id, state: 'running', expires_at: Date.now() / 1000 + 900, poll_after: 0.05, cursor: 0 });
  await sendEvent(start, { event: 'stage', stage: 'understand', state: 'running', cursor: 1 });
  expect(screen.getByTestId('live-activity-line')).toHaveTextContent('Working out the answer… · understanding the question');
  await sendEvent(start, { event: 'stage', stage: 'understand', state: 'done', ms: 1.5, llm_calls: 0, tokens_in: 0, tokens_out: 0, cursor: 2 });
  await sendEvent(start, { event: 'stage', stage: 'execute', state: 'running', cursor: 3 });
  await sendEvent(start, { event: 'node_added', node: { ...runtime, state: 'running' }, cursor: 4 });
  expect(screen.getByTestId('live-activity-line')).toHaveTextContent('Working out the answer… · running the query');
  expect(container.querySelector(`[data-node-id="${runtime.id}"]`)).toHaveAttribute('data-state', 'running');
  fireEvent.click(screen.getByRole('tab', { name: 'Trace' }));
  const rows = () => [...container.querySelectorAll('#tab-trace tbody tr')].map(tr => [...tr.querySelectorAll('td')].map(td => td.textContent));
  expect(rows().map(r => [r[1], r.at(-1)])).toEqual([['understand', '1.5 ms'], ['execute', 'running…']]);
  expect(container.querySelector('.answer-card')).toBeNull();
  await act(async () => { start.end(); await Promise.resolve(); });
  await waitFor(() => expect(asked.some(a => a.url.includes(`/ask/runs/${id}?after=4`))).toBe(true));
  expect(asked.find(a => a.url.includes('/ask/runs/')).accept).toContain('application/x-ndjson');
  await sendEvent(poll, { event: 'run', id, state: 'running', expires_at: Date.now() / 1000 + 900, poll_after: 0.05, cursor: 4 });
  await sendEvent(poll, { event: 'stage', stage: 'execute', state: 'done', ms: 4200, llm_calls: 0, tokens_in: 0, tokens_out: 0, cursor: 5 });
  await sendEvent(poll, { event: 'state_changed', id: runtime.id, state: 'done', node: runtime, cursor: 6 });
  expect(rows().map(r => [r[1], r.at(-1)])).toEqual([['understand', '1.5 ms'], ['execute', '4200 ms']]);
  fireEvent.click(screen.getByRole('tab', { name: 'Graph' }));
  expect(container.querySelector(`[data-node-id="${runtime.id}"]`)).toHaveAttribute('data-state', 'done');
  const answer = { ...response('Revenue?'), graph_path: { nodes: [runtime], edges: [], unused: 0 },
    run_trace: { stages: [{ stage: 'understand', ms: 1.5, llm_calls: 0, tokens_in: 0, tokens_out: 0 }, { stage: 'execute', ms: 4200, llm_calls: 0, tokens_in: 0, tokens_out: 0 }] } };
  await act(async () => { poll.send(`${JSON.stringify({ event: 'answer', answer, cursor: 7 })}\n`); poll.end(); await Promise.resolve(); });
  await waitFor(() => expect(container.querySelector('.answer-card')).toHaveTextContent('10'));
  expect(asked.filter(a => a.url.includes('/ask/runs/'))).toHaveLength(1);
});

test('a reloaded Context replays its run once, in order, then follows the run live', async () => {
  const id = 'e'.repeat(32), question = 'Revenue after reload?';
  window.sessionStorage.setItem(layoutKey('kge.ask.pendingRun', me.id), JSON.stringify({ id, question }));
  const poll = ndjsonFeed();
  const runtime = gNode('Runtime', 'reference', 'runtime');
  const asked = [];
  vi.stubGlobal('fetch', vi.fn(async (url, init) => {
    asked.push({ url, accept: init?.headers?.accept });
    if (url.includes('/map/')) return { ok: true, headers: { get: () => 'application/json' }, json: async () => ({ contexts: [], defined_by: [] }) };
    if (url === `/api/context/ask/runs/${id}`) return { ok: true, headers: { get: () => 'application/json' }, json: async () => ({
      id, state: 'running', expires_at: Date.now() / 1000 + 900, poll_after: 0.05, cursor: 3, events: [
        { event: 'stage', stage: 'plan', state: 'running' },
        { event: 'node_added', node: { ...runtime, state: 'running' } },
        { event: 'stage', stage: 'plan', state: 'done', ms: 12, llm_calls: 0, tokens_in: 0, tokens_out: 0 }] }) };
    return { ok: true, headers: { get: () => 'application/x-ndjson' }, body: { getReader: () => poll.reader } };
  }));
  const { container } = render(<StreamAsk />);
  await waitFor(() => expect(asked.some(a => a.url.includes(`/ask/runs/${id}?after=3`))).toBe(true));
  expect(asked.filter(a => a.url.startsWith('/api/context/ask?'))).toHaveLength(0);
  fireEvent.click(screen.getByRole('tab', { name: 'Trace' }));
  const rows = () => [...container.querySelectorAll('#tab-trace tbody tr')].map(tr => [...tr.querySelectorAll('td')].map(td => td.textContent));
  expect(rows().map(r => [r[1], r.at(-1)])).toEqual([['plan', '12 ms']]);
  fireEvent.click(screen.getByRole('tab', { name: 'Graph' }));
  expect(container.querySelectorAll(`[data-node-id="${runtime.id}"]`)).toHaveLength(1);
  await sendEvent(poll, { event: 'run', id, state: 'running', expires_at: Date.now() / 1000 + 900, poll_after: 0.05, cursor: 3 });
  await sendEvent(poll, { event: 'state_changed', id: runtime.id, state: 'done', node: runtime, cursor: 4 });
  expect(container.querySelector(`[data-node-id="${runtime.id}"]`)).toHaveAttribute('data-state', 'done');
  await act(async () => { poll.send(`${JSON.stringify({ event: 'answer', answer: response(question), cursor: 5 })}\n`); poll.end(); await Promise.resolve(); });
  await waitFor(() => expect(container.querySelector('.answer-card')).toHaveTextContent('10'));
});

test('a reloaded Context asks its run for live events and follows a GET that advances the run as it streams', async () => {
  const id = 'f'.repeat(32), question = 'Revenue after a reload that advances?';
  window.sessionStorage.setItem(layoutKey('kge.ask.pendingRun', me.id), JSON.stringify({ id, question }));
  const feed = ndjsonFeed(), asked = [];
  vi.stubGlobal('fetch', vi.fn(async (url, init) => {
    asked.push({ url, accept: init?.headers?.accept });
    if (url.includes('/map/')) return { ok: true, headers: { get: () => 'application/json' }, json: async () => ({ contexts: [], defined_by: [] }) };
    return { ok: true, headers: { get: () => 'application/x-ndjson' }, body: { getReader: () => feed.reader } };
  }));
  const { container } = render(<StreamAsk />);
  await waitFor(() => expect(asked.some(a => a.url === `/api/context/ask/runs/${id}`)).toBe(true));
  expect(asked.find(a => a.url === `/api/context/ask/runs/${id}`).accept).toContain('application/x-ndjson');
  await sendEvent(feed, { event: 'run', id, state: 'running', expires_at: Date.now() / 1000 + 900, poll_after: 0.05, cursor: 0 });
  await sendEvent(feed, { event: 'stage', stage: 'bind', state: 'running', cursor: 1 });
  expect(screen.getByTestId('live-activity-line')).toHaveTextContent('Working out the answer… · binding the figures');
  await act(async () => { feed.send(`${JSON.stringify({ event: 'answer', answer: response(question), cursor: 2 })}\n`); feed.end(); await Promise.resolve(); });
  await waitFor(() => expect(container.querySelector('.answer-card')).toHaveTextContent('10'));
  expect(asked.filter(a => a.url.includes('/ask/runs/'))).toHaveLength(1);
});

test('an edge_changed event updates the edge already drawn in the Graph tab, never adding a second', async () => {
  const feed = ndjsonFeed();
  setupNdjson(feed);
  const { container } = render(<StreamAsk />);
  submit('Revenue?');
  const words = gNode('Words', 'revenue', 'words');
  const context = gNode('Context', 'revenue', 'meaning');
  const edge = gEdge(words.id, context.id, 'matches', { used: false });
  await sendEvent(feed, { event: 'node_added', node: words });
  await sendEvent(feed, { event: 'node_added', node: context });
  await sendEvent(feed, { event: 'edge_added', edge });
  const drawn = () => container.querySelectorAll(`#tab-graph .gedge[data-edge="${words.id}->${context.id}"]`);
  fireEvent.click(screen.getByLabelText(/Also show what was read but not used/));
  expect(drawn()).toHaveLength(1);
  expect(drawn()[0]).toHaveAttribute('data-used', 'false');
  await sendEvent(feed, { event: 'edge_changed', edge: { ...edge, used: true } });
  expect(drawn()).toHaveLength(1);
  expect(drawn()[0]).toHaveAttribute('data-used', 'true');
  await act(async () => { feed.send(`${JSON.stringify({ event: 'answer', answer: { ...response('Revenue?'), graph_path: { nodes: [words, context], edges: [{ ...edge, used: true }], unused: 0 } } })}\n`); feed.end(); await Promise.resolve(); });
  await waitFor(() => expect(container.querySelector('.answer-card')).toHaveTextContent('10'));
});
