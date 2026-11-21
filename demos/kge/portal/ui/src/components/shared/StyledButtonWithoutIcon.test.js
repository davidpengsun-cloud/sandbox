import { expect, test, vi } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';
import StyledButtonWithoutIcon from './StyledButtonWithoutIcon';

test('the button shows its name and reports a click', () => {
  const handleClick = vi.fn();
  render(<StyledButtonWithoutIcon name="Apply" handleClick={handleClick} />);
  const button = screen.getByRole('button', { name: 'Apply' });
  fireEvent.mouseEnter(button);
  fireEvent.mouseLeave(button);
  fireEvent.click(button);
  expect(handleClick).toHaveBeenCalledTimes(1);
});

test('a disabled button is disabled', () => {
  render(<StyledButtonWithoutIcon name="Apply" disabled />);
  expect(screen.getByRole('button', { name: 'Apply' })).toBeDisabled();
});
