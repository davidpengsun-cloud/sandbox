import { afterEach, beforeEach, expect, test, vi } from 'vitest';
import showToast from './V3Toast';

beforeEach(() => vi.useFakeTimers());
afterEach(() => { vi.useRealTimers(); document.body.innerHTML = ''; });

test('a toast announces its message politely and leaves after its duration', () => {
  showToast('Saved to favourites');
  const wrap = document.querySelector('[aria-live="polite"]');
  expect(wrap).toHaveTextContent('Saved to favourites');
  vi.advanceTimersByTime(2600);
  expect(wrap.querySelector('.v3-toast--out')).not.toBeNull();
  vi.advanceTimersByTime(320);
  expect(wrap).not.toHaveTextContent('Saved to favourites');
});

test('toasts stack in one region and honour a custom duration', () => {
  showToast('First', { duration: 1000 });
  showToast('Second', { duration: 5000 });
  const regions = document.querySelectorAll('[aria-live="polite"]');
  expect(regions).toHaveLength(1);
  vi.advanceTimersByTime(1320);
  expect(regions[0]).not.toHaveTextContent('First');
  expect(regions[0]).toHaveTextContent('Second');
});

test('an empty message shows nothing', () => {
  showToast('');
  expect(document.querySelector('.v3-toast')).toBeNull();
});
