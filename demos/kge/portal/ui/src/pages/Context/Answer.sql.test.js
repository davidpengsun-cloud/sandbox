// #624: the answer card's second part is the SQL behind the answer, for every role who may ask (KGE's compact answer now
// carries it): readable, formatted as Behind the answer formats it, and copyable; an AI-Generated query is headed
// "SQL: not certified (AI-Generated)". Synthetic replies in KGE's shapes.
import React from 'react';
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, expect, test, vi } from 'vitest';
import Answer, { formatSql, sqlHeading, sqlOf } from './Answer';
import Ask from './Ask';
import replies from './fixtures/answer_modes.json';

const business = { id: 'reader@example.com', reads: ['leasing'], cerebro_roles: ['business'] };
const SQL = 'SELECT SUM(amount) FROM flows WHERE month = \'2025-03\'';
const clipboardDescriptor = Object.getOwnPropertyDescriptor(navigator, 'clipboard');
afterEach(() => {
  vi.restoreAllMocks();
  if (clipboardDescriptor) Object.defineProperty(navigator, 'clipboard', clipboardDescriptor);
  else delete navigator.clipboard;
});

test('a business user\'s certified answer shows its SQL part, headed SQL, readable and copyable', async () => {
  const writeText = vi.fn(async () => {});
  Object.defineProperty(navigator, 'clipboard', { configurable: true, value: { writeText } });
  render(<Answer answer={{ ...replies.certified, sql: SQL, sql_certified: true }} me={business} />);
  const part = within(screen.getByLabelText('Answer')).getByRole('region', { name: 'SQL' });
  expect(part.querySelector('pre').textContent).toBe('SELECT SUM(amount)\nFROM flows\nWHERE month = \'2025-03\'');
  await act(async () => fireEvent.click(within(part).getByRole('button', { name: 'Copy the SQL' })));
  expect(writeText).toHaveBeenCalledWith(SQL);
  await waitFor(() => expect(within(part).getByRole('button', { name: 'Copy the SQL' })).toHaveTextContent('Copied'));
});

test('an AI-Generated answer\'s SQL is headed "SQL: not certified (AI-Generated)", with its figure on the same card', () => {
  render(<Answer answer={{ ...replies.ai_generated, sql: SQL, sql_certified: false }} me={business} />);
  const card = screen.getByLabelText('Answer');
  expect(within(card).getByRole('region', { name: 'SQL: not certified (AI-Generated)' })).toHaveTextContent('FROM flows');
  expect(card.querySelector('.fig')).toHaveTextContent('12,500.00');
  // An answer is delivered, never held for a confirm.
  expect(within(card).queryByRole('button', { name: /^(Confirm|Decline)$/ })).toBeNull();
});

test('the heading and the SQL shown: one part per card, a combined answer\'s figures\' distinct queries', () => {
  expect(sqlHeading({ sql_certified: false })).toBe('SQL: not certified (AI-Generated)');
  expect(sqlHeading({ answer_mode: 'ai_generated' })).toBe('SQL: not certified (AI-Generated)');
  expect(sqlHeading({ answer_mode: 'ai_generated', sql_certified: true })).toBe('SQL');
  expect(sqlHeading({ answer_mode: 'certified', sql_certified: true })).toBe('SQL');
  expect(sqlOf({ sql: SQL, figures: [{ sql: 'SELECT 2' }] })).toEqual([SQL]);
  expect(sqlOf({ behaviour: 'combined', figures: [{ sql: 'SELECT 1' }, { sql: 'SELECT 2' }, { sql: 'SELECT 1' }, {}] })).toEqual(['SELECT 1', 'SELECT 2']);
  expect(sqlOf({ behaviour: 'refuse' })).toEqual([]);
  expect(formatSql('SELECT a FROM t LEFT JOIN u ON x GROUP BY a')).toBe('SELECT a\nFROM t\nLEFT JOIN u ON x\nGROUP BY a');
  render(<Answer answer={{ ...replies.certified, behaviour: 'combined', answer_text: 'Two figures.', figures: [{ label: 'A', value: 1, sql: 'SELECT 1' }, { label: 'B', value: 2, sql: 'SELECT 2' }] }} me={business} />);
  const parts = screen.getAllByRole('region', { name: 'SQL' });
  expect(parts).toHaveLength(1);
  expect(within(parts[0]).getAllByRole('button', { name: /^Copy query [12]$/ })).toHaveLength(2);
});

test('a refusal shows no SQL part', () => {
  render(<Answer answer={replies.decline} me={business} />);
  expect(screen.queryByRole('region', { name: /^SQL/ })).toBeNull();
});

test('Context\'s Ask shows a business user the SQL of KGE\'s compact answer on the answer card', async () => {
  const reply = { ...replies.certified, question: 'NNA for March 2025', principal: business.id, history: [], sql: SQL, sql_certified: true };
  const api = vi.fn(async () => reply);
  const presets = { default: 'graph', presets: { graph: { arm: 'graph', paths: ['ask'] } } };
  render(<Ask api={api} me={business} presets={presets} />);
  fireEvent.change(screen.getByLabelText('Question'), { target: { value: 'NNA for March 2025' } });
  fireEvent.click(screen.getByRole('button', { name: 'Ask', exact: true }));
  const card = await screen.findByLabelText('Answer');
  expect(within(card).getByRole('region', { name: 'SQL' })).toHaveTextContent('FROM flows');
});
