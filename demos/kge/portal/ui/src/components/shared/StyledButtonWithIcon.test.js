import { expect, test, vi } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';
import StyledButtonWithIcon from './StyledButtonWithIcon';

test('the button shows its icon and name and opens its popup', () => {
  const open = vi.fn();
  const Icon = (props) => <svg data-testid="icon" {...props} />;
  render(<StyledButtonWithIcon name="Filter" icon={Icon} popupState={{ popupId: 'p', variant: 'popover', isOpen: false, open, close: vi.fn(), toggle: vi.fn(), setAnchorEl: vi.fn(), anchorEl: null, onMouseLeave: vi.fn(), _openEventType: null }} />);
  expect(screen.getByTestId('icon')).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: 'Filter' }));
  expect(open).toHaveBeenCalled();
});
