import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { Provider } from 'react-redux';
import { configureStore } from '@reduxjs/toolkit';
import userReducer from '../../features/user/userSlice';
import dataProductReducer, { selectedProductName } from '../../features/dataProduct/dataProductSlice';
import searchReducer from '../../features/search/searchSlice';
import Chatbot from './Chatbot';
import { postApi } from '../../api/api';
import SaveChatFeedback from './SaveChatFeedback';

vi.mock('../../api/api', () => ({ postApi: vi.fn() }));
vi.mock('./SaveChatFeedback', () => ({ default: vi.fn() }));

const makeStore = () => configureStore({ reducer: { user: userReducer, dataProduct: dataProductReducer, search: searchReducer } });

const mount = (props = {}, store = makeStore()) => {
  const setShowChatbot = props.setShowChatbot || vi.fn();
  const all = { productId: 'retail.account', showChatbot: true, ...props, setShowChatbot };
  const utils = render(<Provider store={store}><Chatbot {...all} /></Provider>);
  return { ...utils, setShowChatbot, store, rerenderWith: (next) => utils.rerender(<Provider store={store}><Chatbot {...all} {...next} /></Provider>) };
};

const ask = (question) => {
  fireEvent.change(screen.getByLabelText('Your question'), { target: { value: question } });
  fireEvent.click(screen.getByRole('button', { name: 'Send' }));
};

beforeEach(() => {
  vi.clearAllMocks();
  window.sessionStorage.clear();
  window.localStorage.clear();
});

afterEach(() => {
  vi.useRealTimers();
});

describe('scope', () => {
  test('domain mode is labelled by its scope, offers domain prompts and asks across the products in view', async () => {
    postApi.mockResolvedValue({ data: { answer_text: 'Domain answer' } });
    mount({ productId: undefined, domainMode: true, scopeLabel: 'Retail', productIds: ['a', 'b'] });
    expect(screen.getByText(/SCOPED TO Retail · ALL PRODUCTS/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Which category has the most records?' }));
    expect(await screen.findByText('Domain answer')).toBeInTheDocument();
    expect(postApi).toHaveBeenCalledWith({ api: '/ask-domain', params: { productIds: ['a', 'b'], question: 'Which category has the most records?' } });
    expect(JSON.parse(window.sessionStorage.getItem('askHistory:domain:Retail'))).toHaveLength(2);
  });

  test('product mode shows the selected product name as its scope', () => {
    const store = makeStore();
    store.dispatch(selectedProductName('Retail accounts'));
    mount({}, store);
    expect(screen.getByText(/SCOPED TO Retail accounts/)).toBeInTheDocument();
  });

  test('a domain panel without a label says it is scoped to this domain', () => {
    mount({ productId: undefined, domainMode: true, productIds: [] });
    expect(screen.getByText(/SCOPED TO THIS DOMAIN/)).toBeInTheDocument();
  });
});

describe('answers', () => {
  test('figures and citations are listed under the answer, after any notice', async () => {
    postApi.mockResolvedValue({ data: {
      notice: 'Answered from the portal catalogue.',
      answer_text: 'Balances rose.',
      figures: [{ label: 'Total', value: 12, currency: 'USD' }, { label: 'Missing' }, 'Plain figure'],
      citations: [{ n: 1, title: 'Balance sheet' }, { n: 2, doc: 'ledger.md' }, 'Loose citation'],
    } });
    mount();
    ask('How did balances move?');
    expect(await screen.findByText('Balances rose.')).toBeInTheDocument();
    expect(screen.getByText('Answered from the portal catalogue.')).toBeInTheDocument();
    expect(screen.getByText('Figures: Total: 12 USD, Missing: —, Plain figure')).toBeInTheDocument();
    expect(screen.getByText('Citations: [1] Balance sheet, [2] ledger.md, Loose citation')).toBeInTheDocument();
  });

  test('an answer without text says so', async () => {
    postApi.mockResolvedValue({ data: {} });
    mount();
    ask('Anything?');
    expect(await screen.findByText('No answer text was returned.')).toBeInTheDocument();
  });

  test('a server failure shows its reason as an alert', async () => {
    postApi.mockRejectedValue({ response: { data: { detail: 'KGE is unavailable.' } } });
    mount();
    ask('Will this fail?');
    expect(await screen.findByRole('alert')).toHaveTextContent('KGE is unavailable.');
  });

  test('a failure without a reason shows a generic retry message', async () => {
    postApi.mockRejectedValue(new Error('offline'));
    mount();
    ask('Will this fail?');
    expect(await screen.findByRole('alert')).toHaveTextContent('Sorry, that question could not be answered right now. Please try again.');
  });

  test.each([
    ['refuse', 'KGE refused this question and gave no reason.', 'Refused'],
    ['abstain_no_recipe', 'KGE abstained from this question and gave no reason.', 'Abstained'],
  ])('a %s without a reason still explains itself', async (behaviour, text, label) => {
    postApi.mockResolvedValue({ data: { behaviour } });
    mount();
    ask('Why not?');
    const note = await screen.findByRole('note');
    expect(note).toHaveTextContent(label);
    expect(note).toHaveTextContent(text);
  });

  test('an empty question is not sent', () => {
    mount();
    fireEvent.change(screen.getByLabelText('Your question'), { target: { value: '   ' } });
    fireEvent.click(screen.getByRole('button', { name: 'Send' }));
    fireEvent.keyDown(screen.getByLabelText('Your question'), { key: 'Enter', shiftKey: true });
    expect(postApi).not.toHaveBeenCalled();
  });

  test('a second question while one is in flight is ignored', async () => {
    let finish;
    postApi.mockImplementation(() => new Promise((resolve) => { finish = resolve; }));
    mount();
    ask('First');
    ask('Second');
    expect(postApi).toHaveBeenCalledTimes(1);
    await act(async () => finish({ data: { answer_text: 'Only answer' } }));
    expect(screen.getByText('Only answer')).toBeInTheDocument();
  });
});

describe('progress while waiting', () => {
  test('progress messages appear over time and a long wait can be stopped', async () => {
    vi.useFakeTimers();
    postApi.mockImplementation(() => new Promise(() => {}));
    mount();
    ask('Slow question');
    expect(screen.getByText('Looking at the data to answer that...')).toBeInTheDocument();
    await act(async () => { vi.advanceTimersByTime(25000); });
    expect(screen.getByText('Processing your request to generate the results.')).toBeInTheDocument();
    await act(async () => { vi.advanceTimersByTime(5000); });
    expect(screen.queryByText('Processing your request to generate the results.')).toBeNull();
    expect(screen.queryByRole('button', { name: 'Stop generation' })).toBeNull();
    await act(async () => { vi.advanceTimersByTime(90000); });
    fireEvent.click(screen.getByRole('button', { name: 'Stop generation' }));
    expect(screen.getByText('Processing is stopped, Please Try Again')).toBeInTheDocument();
    expect(screen.queryByText('Looking at the data to answer that...')).toBeNull();
  });
});

describe('history', () => {
  test('a saved conversation is restored and a new chat clears it', () => {
    window.sessionStorage.setItem('askHistory:retail.account', JSON.stringify([
      { text: 'Earlier question', isBot: false, type: null },
      { text: 'Earlier answer', isBot: true, type: 'output', chatFeedback: null, userQuerySeqId: 1 },
    ]));
    mount();
    expect(screen.getByText('Earlier question')).toBeInTheDocument();
    expect(screen.getByText('Earlier answer')).toBeInTheDocument();
    expect(screen.queryByLabelText('Suggested questions')).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: 'Start a new chat' }));
    expect(screen.queryByText('Earlier answer')).toBeNull();
    expect(screen.getByLabelText('Suggested questions')).toBeInTheDocument();
    expect(JSON.parse(window.sessionStorage.getItem('askHistory:retail.account'))).toEqual([]);
  });

  test('unreadable saved history starts a fresh chat', () => {
    window.sessionStorage.setItem('askHistory:retail.account', '{not json');
    mount();
    expect(screen.getByLabelText('Suggested questions')).toBeInTheDocument();
  });

  test('changing product swaps to that product\'s history', () => {
    window.sessionStorage.setItem('askHistory:retail.loan', JSON.stringify([{ text: 'Loan question', isBot: false, type: null }]));
    const { rerenderWith } = mount();
    expect(screen.queryByText('Loan question')).toBeNull();
    rerenderWith({ productId: 'retail.loan' });
    expect(screen.getByText('Loan question')).toBeInTheDocument();
    rerenderWith({ productId: 'retail.card' });
    expect(screen.queryByText('Loan question')).toBeNull();
  });

  test('a saved SQL answer shows its text and query, and copies', async () => {
    // copy-to-clipboard falls back to window.prompt where execCommand is missing
    window.prompt = vi.fn(() => null);
    window.sessionStorage.setItem('askHistory:retail.account', JSON.stringify([
      { text: 'Count accounts', isBot: false, type: null },
      { text: { sql: 'select count(*) from accounts', output: 'There are **42** accounts.' }, isBot: true, type: 'sql', chatFeedback: null, userQuerySeqId: 1 },
    ]));
    const { container } = mount();
    expect(screen.getByText('42').tagName).toBe('STRONG');
    fireEvent.click(screen.getByText('Database Query'));
    await waitFor(() => expect(container.querySelector('code')).toHaveTextContent(/select.*count\(\*\).*from.*accounts/));
    fireEvent.click(screen.getByRole('button', { name: 'Copy answer' }));
    expect(await screen.findByRole('button', { name: 'Copied' })).toBeInTheDocument();
  });

  test('a saved table answer renders its rows and downloads them as CSV', () => {
    const createObjectURL = vi.fn(() => 'blob:extract');
    URL.createObjectURL = createObjectURL;
    const clicked = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {});
    window.sessionStorage.setItem('askHistory:retail.account', JSON.stringify([
      { text: 'List branches', isBot: false, type: null },
      { text: JSON.stringify({ table: { columns: [{ column_name: 'branch', data_type: 'text' }, { column_name: 'accounts', data_type: 'digit' }], rows: [{ branch: 'North', accounts: 3 }, { branch: 'South', accounts: 5 }] } }), isBot: true, type: 'table', chatFeedback: null },
    ]));
    const { container } = mount();
    expect(screen.getByText('North')).toBeInTheDocument();
    expect(screen.getByText('South')).toBeInTheDocument();
    fireEvent.click(within(container.querySelector('.with-table')).getAllByRole('button').at(-1));
    expect(createObjectURL).toHaveBeenCalledTimes(1);
    expect(clicked).toHaveBeenCalledTimes(1);
    clicked.mockRestore();
  });
});

describe('feedback', () => {
  const answerOnce = async () => {
    postApi.mockResolvedValue({ data: { answer_text: 'Governed answer' } });
    ask('Question one');
    await screen.findByText('Governed answer');
  };

  test('a thumbs-down needs a comment, then submits a rating of 0', async () => {
    SaveChatFeedback.mockResolvedValue({ status: 201 });
    mount();
    await answerOnce();
    fireEvent.click(screen.getByRole('button', { name: 'Not helpful' }));
    const dialog = await screen.findByRole('dialog');
    fireEvent.click(within(dialog).getByRole('button', { name: 'Submit' }));
    expect(await within(dialog).findByText('Required Input')).toBeInTheDocument();
    expect(SaveChatFeedback).not.toHaveBeenCalled();
    fireEvent.change(within(dialog).getByLabelText('Your Feedback:'), { target: { value: 'Wrong unit' } });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Submit' }));
    await waitFor(() => expect(SaveChatFeedback).toHaveBeenCalledWith({ api: '/feedback-add', params: expect.objectContaining({ productId: 'retail.account', feedback_rating: 0, feedback_comment: 'Wrong unit' }) }));
    await waitFor(() => expect(screen.getByRole('button', { name: 'Not helpful' })).toHaveAttribute('aria-pressed', 'true'));
    fireEvent.click(screen.getByRole('button', { name: 'Not helpful' }));
    expect(screen.getByRole('button', { name: 'Not helpful' })).toHaveAttribute('aria-pressed', 'false');
  });

  test('a failed save tells the user and keeps the dialog open', async () => {
    SaveChatFeedback.mockRejectedValue(new Error('down'));
    mount();
    await answerOnce();
    fireEvent.click(screen.getByRole('button', { name: 'Helpful' }));
    fireEvent.click(await screen.findByRole('button', { name: 'Submit' }));
    expect(await screen.findByText('Feedback was not saved')).toBeInTheDocument();
  });

  test('cancelling the dialog clears the thumb', async () => {
    mount();
    await answerOnce();
    fireEvent.click(screen.getByRole('button', { name: 'Helpful' }));
    expect(screen.getByRole('button', { name: 'Helpful' })).toHaveAttribute('aria-pressed', 'true');
    fireEvent.click(await screen.findByRole('button', { name: 'Cancel' }));
    expect(screen.getByRole('button', { name: 'Helpful' })).toHaveAttribute('aria-pressed', 'false');
    expect(SaveChatFeedback).not.toHaveBeenCalled();
  });

  test('domain feedback carries the domain and its products', async () => {
    SaveChatFeedback.mockResolvedValue({ status: 201 });
    postApi.mockResolvedValue({ data: { answer_text: 'Domain answer' } });
    mount({ productId: undefined, domainMode: true, scopeLabel: 'Retail', productIds: ['a'] });
    ask('Across retail?');
    await screen.findByText('Domain answer');
    fireEvent.click(screen.getByRole('button', { name: 'Helpful' }));
    fireEvent.click(await screen.findByRole('button', { name: 'Submit' }));
    await waitFor(() => expect(SaveChatFeedback).toHaveBeenCalledWith({ api: '/feedback-add', params: expect.objectContaining({ domain: 'Retail', productIds: ['a'], feedback_rating: 1 }) }));
  });
});

describe('panel', () => {
  test('Escape and the close button close an open panel', () => {
    const { setShowChatbot } = mount();
    fireEvent.keyDown(document, { key: 'Escape' });
    expect(setShowChatbot).toHaveBeenCalledWith(false);
    setShowChatbot.mockClear();
    fireEvent.click(screen.getByRole('button', { name: 'Close Ask' }));
    expect(setShowChatbot).toHaveBeenCalledWith(false);
  });

  test('Escape does nothing while the panel is closed', () => {
    const { setShowChatbot } = mount({ showChatbot: false });
    fireEvent.keyDown(document, { key: 'Escape' });
    expect(setShowChatbot).not.toHaveBeenCalled();
  });

  test('opening moves focus into the question box and closing returns it to the opener', () => {
    const opener = document.createElement('button');
    document.body.appendChild(opener);
    opener.focus();
    const { rerenderWith } = mount({ showChatbot: false });
    rerenderWith({ showChatbot: true });
    expect(screen.getByLabelText('Your question')).toHaveFocus();
    rerenderWith({ showChatbot: false });
    expect(opener).toHaveFocus();
    opener.remove();
  });

  test('dragging the edge resizes the panel and remembers the width; double-click resets it', () => {
    const { container } = mount();
    const panel = container.querySelector('.chatbot');
    const handle = container.querySelector('.chatbot-resizer');
    fireEvent.mouseDown(handle);
    fireEvent.mouseMove(document, { clientX: window.innerWidth - 500 });
    fireEvent.mouseUp(document);
    expect(panel.style.width).not.toBe('');
    expect(Number(window.localStorage.getItem('askPortalPanelWidth'))).toBeGreaterThanOrEqual(360);
    fireEvent.doubleClick(handle);
    expect(panel.style.width).toBe('');
    expect(window.localStorage.getItem('askPortalPanelWidth')).toBeNull();
  });

  test('a remembered width is applied when the panel mounts', () => {
    window.localStorage.setItem('askPortalPanelWidth', '480');
    const { container } = mount();
    expect(container.querySelector('.chatbot').style.width).toBe('480px');
  });
});
