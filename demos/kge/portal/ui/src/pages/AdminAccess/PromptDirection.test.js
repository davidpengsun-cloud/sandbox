import { beforeEach, expect, test, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import PromptDirection from './PromptDirection';
import { getPromptDirection, savePromptDirection } from '../../services/admin';

vi.mock('../../services/admin', () => ({ getPromptDirection: vi.fn(), savePromptDirection: vi.fn() }));

const blank = { text: '', changed_by: null, changed_at: null };
const changed = { text: 'Explain field meanings.', changed_by: 'operator@example.com', changed_at: '2026-11-12T12:00:00.000Z' };

beforeEach(() => {
  vi.resetAllMocks();
  getPromptDirection.mockResolvedValue({ data: blank });
  savePromptDirection.mockImplementation(async (text) => ({ data: { ...changed, text } }));
});

test('a fresh install has an empty textbox with no default and explains the fixed limits', async () => {
  render(<PromptDirection />);
  const text = await screen.findByRole('textbox', { name: 'Prompt direction' });
  await waitFor(() => expect(text).toBeEnabled());
  expect(text).toHaveValue('');
  expect(text).toHaveAttribute('maxlength', '2000');
  expect(screen.getByText(/fixed rules, readable catalogue and figure limits always apply/)).toBeInTheDocument();
  expect(screen.getByText('No prompt direction changes recorded.')).toBeInTheDocument();
  expect(screen.getByRole('button', { name: 'Save prompt direction' })).toBeEnabled();
});

test('loads the saved direction and the last change, then saves edits and shows the returned actor and time', async () => {
  getPromptDirection.mockResolvedValue({ data: changed });
  const saved = { text: 'Use plain language.', changed_by: 'other-operator@example.com', changed_at: '2026-11-12T12:01:00.000Z' };
  savePromptDirection.mockResolvedValue({ data: saved });
  render(<PromptDirection />);
  const text = screen.getByRole('textbox', { name: 'Prompt direction' });
  await waitFor(() => expect(text).toHaveValue(changed.text));
  expect(screen.getByText(/Last changed by operator@example.com/)).toBeInTheDocument();
  expect(screen.getByText(changed.changed_at)).toHaveAttribute('dateTime', changed.changed_at);
  fireEvent.change(text, { target: { value: saved.text } });
  fireEvent.click(screen.getByRole('button', { name: 'Save prompt direction' }));
  expect(await screen.findByRole('status')).toHaveTextContent('Prompt direction saved. It applies from the next question.');
  expect(savePromptDirection).toHaveBeenCalledTimes(1);
  expect(savePromptDirection).toHaveBeenCalledWith(saved.text);
  expect(screen.getByText(/Last changed by other-operator@example.com/)).toBeInTheDocument();
  expect(screen.getByText(saved.changed_at)).toHaveAttribute('dateTime', saved.changed_at);
});

test('clearing the textbox saves empty text and retains who and when it was cleared', async () => {
  getPromptDirection.mockResolvedValue({ data: changed });
  render(<PromptDirection />);
  const text = screen.getByRole('textbox', { name: 'Prompt direction' });
  await waitFor(() => expect(text).toHaveValue(changed.text));
  fireEvent.change(text, { target: { value: '' } });
  fireEvent.click(screen.getByRole('button', { name: 'Save prompt direction' }));
  await screen.findByRole('status');
  expect(savePromptDirection).toHaveBeenCalledTimes(1);
  expect(savePromptDirection).toHaveBeenCalledWith('');
  expect(text).toHaveValue('');
  expect(screen.getByText(/Last changed by operator@example.com/)).toBeInTheDocument();
  expect(screen.getByText(changed.changed_at)).toHaveAttribute('dateTime', changed.changed_at);
});

test('a failed load shows the refusal and prevents saving an apparent empty default', async () => {
  getPromptDirection.mockRejectedValue({ response: { status: 403, data: { message: 'Portal role required.' } } });
  render(<PromptDirection />);
  expect(await screen.findByRole('alert')).toHaveTextContent('Portal role required.');
  expect(screen.getByRole('textbox', { name: 'Prompt direction' })).toBeDisabled();
  expect(screen.getByRole('button', { name: 'Save prompt direction' })).toBeDisabled();
  expect(screen.queryByText('No prompt direction changes recorded.')).toBeNull();
  expect(savePromptDirection).not.toHaveBeenCalled();
});

test('a failed save preserves the draft and the last successful change without claiming success', async () => {
  getPromptDirection.mockResolvedValue({ data: changed });
  savePromptDirection.mockRejectedValue({ response: { status: 400, data: { message: 'Prompt direction must be text of up to 2000 characters.' } } });
  render(<PromptDirection />);
  const text = screen.getByRole('textbox', { name: 'Prompt direction' });
  await waitFor(() => expect(text).toHaveValue(changed.text));
  fireEvent.change(text, { target: { value: 'Unsaved draft.' } });
  fireEvent.click(screen.getByRole('button', { name: 'Save prompt direction' }));
  expect(await screen.findByRole('alert')).toHaveTextContent('2000 characters');
  expect(text).toHaveValue('Unsaved draft.');
  expect(screen.queryByRole('status')).toBeNull();
  expect(screen.getByText(changed.changed_at)).toHaveAttribute('dateTime', changed.changed_at);
  expect(screen.getByRole('button', { name: 'Save prompt direction' })).toBeEnabled();
});

test('loading and saving disable edits until the request finishes', async () => {
  let loaded, saved;
  getPromptDirection.mockImplementation(() => new Promise((resolve) => { loaded = resolve; }));
  savePromptDirection.mockImplementation(() => new Promise((resolve) => { saved = resolve; }));
  render(<PromptDirection />);
  const text = screen.getByRole('textbox', { name: 'Prompt direction' });
  const button = screen.getByRole('button', { name: 'Save prompt direction' });
  expect(text).toBeDisabled();
  expect(button).toBeDisabled();
  loaded({ data: blank });
  await waitFor(() => expect(text).toBeEnabled());
  fireEvent.change(text, { target: { value: changed.text } });
  fireEvent.click(button);
  expect(text).toBeDisabled();
  expect(button).toBeDisabled();
  saved({ data: changed });
  await screen.findByRole('status');
  expect(text).toBeEnabled();
  expect(button).toBeEnabled();
});

test('the limit is shown and an oversized draft cannot be submitted', async () => {
  render(<PromptDirection />);
  const text = screen.getByRole('textbox', { name: 'Prompt direction' });
  await waitFor(() => expect(text).toBeEnabled());
  fireEvent.change(text, { target: { value: 'x'.repeat(2000) } });
  expect(screen.getByText('2000 / 2000 characters')).toBeInTheDocument();
  expect(screen.getByRole('button', { name: 'Save prompt direction' })).toBeEnabled();
  fireEvent.change(text, { target: { value: 'x'.repeat(2001) } });
  expect(screen.getByRole('button', { name: 'Save prompt direction' })).toBeDisabled();
  fireEvent.submit(text.closest('form'));
  expect(savePromptDirection).not.toHaveBeenCalled();
});

test('direction and actor are rendered as text rather than markup', async () => {
  getPromptDirection.mockResolvedValue({ data: { ...changed, text: '<b>Use plain language.</b>', changed_by: '<b>operator</b>' } });
  render(<PromptDirection />);
  await waitFor(() => expect(screen.getByRole('textbox', { name: 'Prompt direction' })).toHaveValue('<b>Use plain language.</b>'));
  expect(screen.getByText(/Last changed by <b>operator<\/b>/)).toBeInTheDocument();
  expect(document.querySelector('.portal-prompt-direction b')).toBeNull();
});

test('unstructured failures use a useful fallback message', async () => {
  getPromptDirection.mockRejectedValue(new Error('offline'));
  render(<PromptDirection />);
  expect(await screen.findByRole('alert')).toHaveTextContent('Prompt direction could not be loaded or saved. Try again.');
});
