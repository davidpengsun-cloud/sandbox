import { beforeEach, expect, test, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import FeedbackForm from './FeedbackForm';

const props = { open: true, onCreate: vi.fn(), onCancel: vi.fn(), userQuerySeqId: 3, handleStopClosingChatClick: vi.fn(), confirmLoading: false };
beforeEach(() => vi.clearAllMocks());

test('a thumbs-up can be submitted without a comment and is acknowledged', async () => {
  render(<FeedbackForm {...props} chatFeedback="like" />);
  fireEvent.click(screen.getByRole('button', { name: 'Submit' }));
  await waitFor(() => expect(props.onCreate).toHaveBeenCalledWith(expect.objectContaining({ feedback: undefined }), 'like', 3));
  expect(await screen.findByText('Feedback Captured')).toBeInTheDocument();
});

test('a thumbs-down requires a comment before it is sent', async () => {
  render(<FeedbackForm {...props} chatFeedback="dislike" />);
  fireEvent.click(screen.getByRole('button', { name: 'Submit' }));
  expect(await screen.findByText('Required Input')).toBeInTheDocument();
  expect(props.onCreate).not.toHaveBeenCalled();
  fireEvent.change(screen.getByLabelText('Your Feedback:'), { target: { value: 'Wrong figure' } });
  fireEvent.click(screen.getByRole('button', { name: 'Submit' }));
  await waitFor(() => expect(props.onCreate).toHaveBeenCalledWith(expect.objectContaining({ feedback: 'Wrong figure' }), 'dislike', 3));
  expect(await screen.findByText('Feedback Captured')).toBeInTheDocument();
});

test('cancel closes without sending', () => {
  render(<FeedbackForm {...props} chatFeedback="like" />);
  fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));
  expect(props.onCancel).toHaveBeenCalled();
  expect(props.onCreate).not.toHaveBeenCalled();
});
