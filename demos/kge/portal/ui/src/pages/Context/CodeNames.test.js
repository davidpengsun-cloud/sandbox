import React from 'react';
import { render, screen } from '@testing-library/react';
import { expect, test } from 'vitest';
import Answer from './Answer';

const cell = value => ({ value: { kind: 'decimal', value: String(value) }, citations: [], null_reasons: [] });
const named = (code, value, name) => ({ panel: null, dimensions: [{ kind: 'text', value: code }], ...(name ? { dimension_labels: [name] } : {}), cells: [cell(value)] });
const columns = [{ id: 'segment', kind: 'dimension', label: 'Segment' }, { id: 'nna', kind: 'measure', currency: 'USD' }];

test('the grouped card shows a declared name, the code where two names match, and an unnamed value as it is', () => {
  const rows = [named('S4100', 10, 'Affluent'), named('S4200', 20, 'Affluent'), named('P1', 30, 'Private Clients'), named('US', 40)];
  const { container } = render(<Answer answer={{ behaviour: 'figure', currency: 'USD', rows: { S4100: 10, S4200: 20, P1: 30, US: 40 }, group_by: 'segment', result: { columns, rows } }} />);
  const shown = [...container.querySelectorAll('table.parts tbody tr td:first-child')].map(td => td.textContent);
  expect(shown).toEqual(['Affluent (S4100)', 'Affluent (S4200)', 'Private Clients', 'US']);
});

test('the general card shows names and keeps the total', () => {
  const rows = [named('P1', 30, 'Private Clients'), named('US', 40), { panel: null, total: true, dimensions: [{ kind: 'null' }], cells: [cell(70)] }];
  const { container } = render(<Answer answer={{ behaviour: 'figure', figure: null, rows: { P1: 30, US: 40 }, result: { columns, rows } }} />);
  const shown = [...container.querySelectorAll('.result tbody tr td:first-child')].map(td => td.textContent);
  expect(shown).toEqual(['Private Clients', 'US', 'Total']);
  expect(container.textContent).not.toMatch(/P1/);
});

test('a choice button shows the label it is given', () => {
  render(<Answer answer={{ behaviour: 'clarify', reason: 'Which segment?', question: 'NNA for Affluent', clarification: { choices: [{ id: 'value:unit.segment.segment_code:S4100', label: 'Affluent (S4100)' }, { id: 'value:unit.segment.segment_name:Affluent', label: 'Affluent' }], spans: [] } }} />);
  expect(screen.getByRole('button', { name: 'Affluent (S4100)' })).toBeInTheDocument();
});
