import { expect, test, vi } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';
import PaginationButton from './PaginationButton';

test('shows the page position and moves through pages', () => {
  const onClickChange = vi.fn();
  render(<PaginationButton data={Array.from({ length: 25 })} itemsPerPage={10} onClickChange={onClickChange} />);
  expect(screen.getByText('1/3')).toBeInTheDocument();
  fireEvent.click(screen.getByLabelText('Go to next page'));
  expect(onClickChange).toHaveBeenLastCalledWith(2);
  expect(screen.getByText('2/3')).toBeInTheDocument();
  fireEvent.click(screen.getByLabelText('Go to last page'));
  expect(onClickChange).toHaveBeenLastCalledWith(3);
  fireEvent.click(screen.getByLabelText('Go to first page'));
  expect(onClickChange).toHaveBeenLastCalledWith(1);
});

test('many pages hide the page numbers behind an empty ellipsis', () => {
  render(<PaginationButton data={Array.from({ length: 200 })} itemsPerPage={10} onClickChange={vi.fn()} />);
  expect(screen.getByText('1/20')).toBeInTheDocument();
  expect(screen.queryByText('…')).toBeNull();
});
