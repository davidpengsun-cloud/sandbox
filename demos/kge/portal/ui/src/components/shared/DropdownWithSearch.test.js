import { expect, test, vi } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';
import DropdownWithSearch from './DropdownWithSearch';

const options = [{ value: 'a', label: 'Alpha' }, { value: 'b', label: 'Beta' }];

test('picking options reports them with the parent and shows them chosen', () => {
  const getSelectedOptions = vi.fn();
  render(<DropdownWithSearch options={options} value={[]} parent="DOMAIN" open={true} getSelectedOptions={getSelectedOptions} />);
  fireEvent.click(screen.getByText('Alpha'));
  expect(getSelectedOptions).toHaveBeenCalledWith([options[0]], 'DOMAIN');
  fireEvent.click(screen.getByText('Beta'));
  expect(getSelectedOptions).toHaveBeenLastCalledWith(options, 'DOMAIN');
});

test('a closed menu shows the chosen value only', () => {
  render(<DropdownWithSearch options={options} value={[options[1]]} parent="X" open={false} getSelectedOptions={vi.fn()} />);
  expect(screen.getByText('Beta')).toBeInTheDocument();
  expect(screen.queryByText('Alpha')).toBeNull();
});
