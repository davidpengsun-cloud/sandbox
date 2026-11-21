import { beforeEach, describe, expect, test, vi } from 'vitest';
import { act, fireEvent, render, screen } from '@testing-library/react';
import { Provider } from 'react-redux';
import { configureStore } from '@reduxjs/toolkit';
import userReducer from '../../features/user/userSlice';
import dataProductReducer from '../../features/dataProduct/dataProductSlice';
import searchReducer from '../../features/search/searchSlice';
import Chatbot, { historyOf } from './Chatbot';
import { postApi } from '../../api/api';

vi.mock('../../api/api', () => ({ postApi: vi.fn() }));
vi.mock('../../services/user', () => ({ getConfig: vi.fn().mockResolvedValue({ data: {} }) }));
vi.mock('./SaveChatFeedback', () => ({ default: vi.fn() }));

// #613: each Ask sends the history KGE returned on the previous answer in its scope, unchanged. The stub plays KGE:
// it appends a turn to the history it was sent and resolves a follow-up only when that history is there.
const turn = (id, question) => ({ turn_id: id, question, plan: { measure: 'synthetic_sales' }, clarification: null, binding: { release: 'r1' } });

function stubKge() {
  let n = 0;
  postApi.mockImplementation(async ({ params }) => {
    n += 1;
    const history = params.history || [];
    if (/^and by month/i.test(params.question)) {
      if (!history.length) return { data: { behaviour: 'abstain', reason: 'Which measure do you mean?', history: [] } };
      return { data: { behaviour: 'figure', answer_text: `Monthly ${history.at(-1).plan.measure}: 10, 12, 14`, history: [...history, turn(`t${n}`, params.question)] } };
    }
    return { data: { behaviour: 'figure', answer_text: `Answer to ${params.question}`, history: [...history, turn(`t${n}`, params.question)] } };
  });
}

const makeStore = () => configureStore({ reducer: { user: userReducer, dataProduct: dataProductReducer, search: searchReducer } });

const mount = (props = {}) => {
  const store = makeStore();
  const all = { productId: 'synthetic.sales', showChatbot: true, setShowChatbot: vi.fn(), ...props };
  const utils = render(<Provider store={store}><Chatbot {...all} /></Provider>);
  return { rerenderWith: (next) => utils.rerender(<Provider store={store}><Chatbot {...all} {...next} /></Provider>) };
};

const ask = async (question, answer) => {
  fireEvent.change(screen.getByLabelText('Your question'), { target: { value: question } });
  fireEvent.click(screen.getByRole('button', { name: 'Send' }));
  await screen.findByText(answer);
};
const sent = (n) => postApi.mock.calls[n][0].params;

beforeEach(() => {
  vi.clearAllMocks();
  window.sessionStorage.clear();
  window.localStorage.clear();
});

describe('conversation history (#613)', () => {
  test('the second question sends the first answer\'s history unchanged and the stubbed KGE resolves the follow-up', async () => {
    stubKge();
    mount();
    await ask('Total synthetic sales?', 'Answer to Total synthetic sales?');
    expect(sent(0)).toEqual({ productId: 'synthetic.sales', question: 'Total synthetic sales?' });
    const first = (await postApi.mock.results[0].value).data.history;
    await ask('And by month?', 'Monthly synthetic_sales: 10, 12, 14');
    expect(sent(1)).toEqual({ productId: 'synthetic.sales', question: 'And by month?', history: first });
    const second = (await postApi.mock.results[1].value).data.history;
    await ask('What about last year?', 'Answer to What about last year?');
    expect(sent(2).history).toEqual(second);
    expect(sent(2).history).toHaveLength(2);
  });

  test('the domain scope sends its history to /ask-domain', async () => {
    stubKge();
    mount({ productId: undefined, domainMode: true, scopeLabel: 'Synthetic', productIds: ['a', 'b'] });
    await ask('Total synthetic sales?', 'Answer to Total synthetic sales?');
    await ask('And by month?', 'Monthly synthetic_sales: 10, 12, 14');
    expect(postApi.mock.calls[1][0]).toEqual({ api: '/ask-domain', params: { productIds: ['a', 'b'], domain: 'Synthetic', question: 'And by month?', history: [turn('t1', 'Total synthetic sales?')] } });
  });

  test('a new chat clears the history', async () => {
    stubKge();
    mount();
    await ask('Total synthetic sales?', 'Answer to Total synthetic sales?');
    fireEvent.click(screen.getByRole('button', { name: 'Start a new chat' }));
    await ask('And by month?', 'Which measure do you mean?');
    expect(sent(1)).toEqual({ productId: 'synthetic.sales', question: 'And by month?' });
  });

  test('history is kept per scope: another asset starts without it and returning resumes it', async () => {
    stubKge();
    const { rerenderWith } = mount();
    await ask('Total synthetic sales?', 'Answer to Total synthetic sales?');
    rerenderWith({ productId: 'synthetic.returns' });
    await ask('And by month?', 'Which measure do you mean?');
    expect(sent(1)).toEqual({ productId: 'synthetic.returns', question: 'And by month?' });
    rerenderWith({ productId: 'synthetic.sales' });
    await ask('And by month?', 'Monthly synthetic_sales: 10, 12, 14');
    expect(sent(2)).toEqual({ productId: 'synthetic.sales', question: 'And by month?', history: [turn('t1', 'Total synthetic sales?')] });
  });

  test('an error keeps the history, a rejected history (422) starts afresh', async () => {
    const history = [turn('t1', 'Total synthetic sales?')];
    postApi
      .mockResolvedValueOnce({ data: { answer_text: 'First', history } })
      .mockRejectedValueOnce({ response: { status: 503, data: { detail: 'Context is busy.' } } })
      .mockRejectedValueOnce({ response: { status: 422, data: { detail: 'Context returned 422' } } })
      .mockResolvedValue({ data: { answer_text: 'Fresh' } });
    mount();
    await ask('Total synthetic sales?', 'First');
    await ask('And by month?', 'Context is busy.');
    await ask('And by month?', 'Context returned 422');
    await ask('And by month?', 'Fresh');
    expect(sent(1).history).toEqual(history);
    expect(sent(2).history).toEqual(history);
    expect(sent(3)).not.toHaveProperty('history');
  });
});

// A reply that settles after its chat was cleared or swapped for another scope's belongs to no chat on screen.
describe('a reply that lands after its chat has gone', () => {
  const deferred = () => {
    let resolve;
    const promise = new Promise((r) => { resolve = r; });
    return { promise, resolve };
  };
  const send = (question) => {
    fireEvent.change(screen.getByLabelText('Your question'), { target: { value: question } });
    fireEvent.click(screen.getByRole('button', { name: 'Send' }));
  };
  const bHistory = [turn('b1', 'Total synthetic returns?')];
  const aHistory = [turn('a1', 'Total synthetic sales?')];
  const saveB = () => window.sessionStorage.setItem('askHistory:synthetic.returns', JSON.stringify([
    { text: 'Total synthetic returns?', isBot: false, type: null },
    { text: 'Answer B', isBot: true, type: 'output', chatFeedback: null, userQuerySeqId: 1, history: bHistory },
  ]));

  test('does not join another scope\'s saved chat, and that chat keeps sending its own history', async () => {
    saveB();
    const slow = deferred();
    postApi.mockReturnValueOnce(slow.promise).mockResolvedValue({ data: { answer_text: 'Answer B2', history: [...bHistory, turn('b2', 'And by month?')] } });
    const { rerenderWith } = mount();
    send('Total synthetic sales?');
    rerenderWith({ productId: 'synthetic.returns' });
    expect(screen.getByText('Answer B')).toBeInTheDocument();
    await act(async () => slow.resolve({ data: { answer_text: 'Answer A', history: aHistory } }));
    expect(screen.queryByText('Answer A')).toBeNull();
    expect(screen.queryByText('Looking at the data to answer that...')).toBeNull();
    const savedB = JSON.parse(window.sessionStorage.getItem('askHistory:synthetic.returns'));
    expect(savedB.map((m) => m.text)).toEqual(['Total synthetic returns?', 'Answer B']);
    await ask('And by month?', 'Answer B2');
    expect(sent(1)).toEqual({ productId: 'synthetic.returns', question: 'And by month?', history: bHistory });
    // Nor was it saved to the scope it was asked in, which never showed it.
    expect(JSON.parse(window.sessionStorage.getItem('askHistory:synthetic.sales')).map((m) => m.text)).toEqual(['Total synthetic sales?']);
  });

  test('a new chat while a question is pending drops its reply without crashing', async () => {
    const slow = deferred();
    postApi.mockReturnValueOnce(slow.promise).mockResolvedValue({ data: { answer_text: 'Fresh answer' } });
    mount();
    send('Total synthetic sales?');
    fireEvent.click(screen.getByRole('button', { name: 'Start a new chat' }));
    await act(async () => slow.resolve({ data: { answer_text: 'Late answer', history: aHistory } }));
    expect(screen.queryByText('Late answer')).toBeNull();
    expect(screen.getByLabelText('Suggested questions')).toBeInTheDocument();
    expect(JSON.parse(window.sessionStorage.getItem('askHistory:synthetic.sales'))).toEqual([]);
    await ask('And by month?', 'Fresh answer');
    expect(sent(1)).toEqual({ productId: 'synthetic.sales', question: 'And by month?' });
  });

  test('switching to a scope with no saved chat while a question is pending drops its reply without crashing', async () => {
    const slow = deferred();
    postApi.mockReturnValueOnce(slow.promise);
    const { rerenderWith } = mount();
    send('Total synthetic sales?');
    rerenderWith({ productId: 'synthetic.returns' });
    await act(async () => slow.resolve({ data: { answer_text: 'Late answer', history: aHistory } }));
    expect(screen.queryByText('Late answer')).toBeNull();
    expect(screen.getByLabelText('Suggested questions')).toBeInTheDocument();
    expect(JSON.parse(window.sessionStorage.getItem('askHistory:synthetic.returns'))).toEqual([]);
  });

  test('a copied answer whose question is not text copies without crashing', async () => {
    window.sessionStorage.setItem('askHistory:synthetic.sales', JSON.stringify([
      { text: 'Orphan answer', isBot: true, type: 'output', chatFeedback: null, userQuerySeqId: 1 },
    ]));
    mount();
    expect(screen.getByText('Orphan answer')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Copy answer' })).toBeInTheDocument();
  });
});

describe('historyOf', () => {
  const eight = Array.from({ length: 8 }, (_, i) => turn(`t${i}`, `q${i}`));
  test('sends the latest settled history unchanged, below KGE\'s maximum turns', () => {
    const latest = eight.slice(0, 7);
    expect(historyOf([{ isBot: true }, { history: eight.slice(0, 1) }, { isBot: false }, { history: latest }, { type: 'error' }])).toBe(latest);
  });
  test('sends none at KGE\'s maximum turns, for a reply without history, or before any answer', () => {
    expect(historyOf([{ history: eight }])).toBeUndefined();
    expect(historyOf([{ history: eight.slice(0, 2) }, { history: undefined }])).toBeUndefined();
    expect(historyOf([{ history: [] }])).toBeUndefined();
    expect(historyOf([{ isBot: true }])).toBeUndefined();
  });
});
