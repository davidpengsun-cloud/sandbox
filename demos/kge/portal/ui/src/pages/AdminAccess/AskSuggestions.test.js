import { beforeEach, expect, test, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import AskSuggestions from './AskSuggestions';
import { getAskSuggestions, saveAskSuggestions } from '../../services/admin';

vi.mock('../../services/admin', () => ({ getAskSuggestions: vi.fn(), saveAskSuggestions: vi.fn() }));

const blank = { asset: [], domain: [], changed_by: null, changed_at: null };
const changed = { asset: ['Who owns it?'], domain: ['Which products join?'], changed_by: 'operator@example.com', changed_at: '2026-11-20T12:00:00.000Z' };
const box = (name) => screen.getByRole('textbox', { name });
const save = () => screen.getByRole('button', { name: 'Save suggested questions' });

beforeEach(() => {
  vi.resetAllMocks();
  getAskSuggestions.mockResolvedValue({ data: blank });
  saveAskSuggestions.mockImplementation(async (asset, domain) => ({ data: { ...changed, asset, domain } }));
});

test('a fresh install shows six empty boxes per list, the limits, and no change recorded', async () => {
  render(<AskSuggestions />);
  await waitFor(() => expect(box('Data asset question 1')).toBeEnabled());
  for (let n = 1; n <= 6; n += 1) {
    expect(box(`Data asset question ${n}`)).toHaveValue('');
    expect(box(`Domain question ${n}`)).toHaveAttribute('maxlength', '200');
  }
  expect(screen.queryByRole('textbox', { name: 'Data asset question 7' })).toBeNull();
  expect(screen.getByText(/up to 6 for a data asset and up to 6 for a domain, each of up to 200 characters/)).toBeInTheDocument();
  expect(screen.getByText('No suggested question changes recorded.')).toBeInTheDocument();
});

test('loads the saved lists, saves both lists without blanks, and shows the returned actor and time', async () => {
  getAskSuggestions.mockResolvedValue({ data: changed });
  render(<AskSuggestions />);
  await waitFor(() => expect(box('Data asset question 1')).toHaveValue('Who owns it?'));
  expect(box('Domain question 1')).toHaveValue('Which products join?');
  expect(screen.getByText(/Last changed by operator@example.com/)).toBeInTheDocument();
  fireEvent.change(box('Data asset question 3'), { target: { value: '  How fresh is it?  ' } });
  fireEvent.change(box('Domain question 1'), { target: { value: '' } });
  fireEvent.click(save());
  expect(await screen.findByRole('status')).toHaveTextContent('Suggested questions saved. They apply from the next page load.');
  expect(saveAskSuggestions).toHaveBeenCalledWith(['Who owns it?', 'How fresh is it?'], []);
  expect(box('Data asset question 2')).toHaveValue('How fresh is it?');
  expect(box('Domain question 1')).toHaveValue('');
});

test('a failed load disables the editor; a refused save keeps the draft and shows the reason', async () => {
  getAskSuggestions.mockRejectedValueOnce({ response: { status: 403, data: { message: 'Portal role required.' } } });
  const { unmount } = render(<AskSuggestions />);
  expect(await screen.findByRole('alert')).toHaveTextContent('Portal role required.');
  expect(box('Data asset question 1')).toBeDisabled();
  expect(save()).toBeDisabled();
  unmount();
  saveAskSuggestions.mockRejectedValue({ response: { status: 400, data: { message: 'Suggested questions must be two lists.' } } });
  render(<AskSuggestions />);
  await waitFor(() => expect(box('Data asset question 1')).toBeEnabled());
  fireEvent.change(box('Data asset question 1'), { target: { value: 'Unsaved draft?' } });
  fireEvent.click(save());
  expect(await screen.findByRole('alert')).toHaveTextContent('Suggested questions must be two lists.');
  expect(box('Data asset question 1')).toHaveValue('Unsaved draft?');
  expect(screen.queryByRole('status')).toBeNull();
});

test('questions and actor are rendered as text, not markup', async () => {
  getAskSuggestions.mockResolvedValue({ data: { ...changed, asset: ['<b>Bold?</b>'], changed_by: '<b>operator</b>' } });
  render(<AskSuggestions />);
  await waitFor(() => expect(box('Data asset question 1')).toHaveValue('<b>Bold?</b>'));
  expect(screen.getByText(/Last changed by <b>operator<\/b>/)).toBeInTheDocument();
  expect(document.querySelector('.portal-ask-suggestions b')).toBeNull();
});

test('unstructured failures use a useful fallback message', async () => {
  getAskSuggestions.mockRejectedValue(new Error('offline'));
  render(<AskSuggestions />);
  expect(await screen.findByRole('alert')).toHaveTextContent('Suggested questions could not be loaded or saved. Try again.');
});
