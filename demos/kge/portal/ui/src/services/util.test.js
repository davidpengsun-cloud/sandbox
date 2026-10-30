import { expect, test } from 'vitest';
import { sortArray, sortArrayBasedOnDates } from './util';

const rows = [{ name: 'beta', n: 2 }, { name: 'Alpha', n: 3 }, { name: 'gamma', n: 1 }];

test('sortArray orders strings case-insensitively, ascending by default', () => {
  expect([...rows].sort(sortArray('name')).map((r) => r.name)).toEqual(['Alpha', 'beta', 'gamma']);
});

test('sortArray orders numbers descending on request', () => {
  expect([...rows].sort(sortArray('n', 'desc')).map((r) => r.n)).toEqual([3, 2, 1]);
});

test('sortArray leaves rows without the key in place', () => {
  const mixed = [{ name: 'b' }, { other: 1 }, { name: 'a' }];
  expect(sortArray('name')(mixed[0], mixed[1])).toBe(0);
  expect(sortArray('name')(mixed[0], mixed[0])).toBe(0);
});

test('sortArrayBasedOnDates puts the newest first for asc and the oldest first for desc', () => {
  const dated = [{ d: '2024-01-02' }, { d: '2025-06-01' }, { d: '2023-03-04' }];
  expect([...dated].sort(sortArrayBasedOnDates('d', 'asc')).map((r) => r.d)).toEqual(['2025-06-01', '2024-01-02', '2023-03-04']);
  expect([...dated].sort(sortArrayBasedOnDates('d', 'desc')).map((r) => r.d)).toEqual(['2023-03-04', '2024-01-02', '2025-06-01']);
  expect(sortArrayBasedOnDates('d', 'other')(dated[0], dated[1])).toBeUndefined();
});
