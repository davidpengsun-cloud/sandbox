import React from 'react';
import { render, screen, within } from '@testing-library/react';
import { expect, test } from 'vitest';
import Answer from './Answer';

const cell = value => ({ value: value === null ? { kind: 'null' } : { kind: 'decimal', value: String(value) }, citations: [], null_reasons: [] });
const result = {
  columns: [{ id: 'Region', kind: 'dimension' }, { id: 'Amount', kind: 'measure', currency: 'USD' }, { id: 'Share', kind: 'derived' }],
  rows: [
    { panel: null, end: 'top', rank: 1, dimensions: [{ kind: 'text', value: '<North>' }], cells: [cell(30), cell(.6)] },
    { panel: null, end: 'bottom', rank: 1, dimensions: [{ kind: 'text', value: 'South' }], cells: [cell(5), cell(.1)] },
    { panel: null, total: true, dimensions: [{ kind: 'null' }], cells: [cell(50), cell(null)] },
  ],
};

test('ranked answers show both ends, ranks, and the full-scope total above legacy grouped rows', () => {
  const { container } = render(<Answer answer={{ behaviour: 'figure', figure: null, rows: { North: 30, South: 5 }, result }} />);
  expect(screen.getByRole('columnheader', { name: 'End' })).toBeVisible();
  expect(screen.getByRole('columnheader', { name: 'Rank' })).toBeVisible();
  const rows = container.querySelectorAll('.result tbody tr');
  expect(rows).toHaveLength(3);
  expect(rows[0]).toHaveTextContent('top1<North>USD 30');
  expect(rows[1]).toHaveTextContent('bottom1SouthUSD 5');
  expect(rows[2]).toHaveTextContent('TotalUSD 50');
  expect(within(rows[2]).getAllByRole('cell').at(-1)).toBeEmptyDOMElement();
  expect(container.querySelector('North')).toBeNull();
});

test('an unranked total is labelled and a null dimension remains a separate detail row', () => {
  const { container } = render(<Answer answer={{ behaviour: 'figure', figure: null, rows: {}, result: {
    ...result, rows: [
      { panel: null, dimensions: [{ kind: 'null' }], cells: [cell(50), cell(1)] },
      result.rows[2],
    ],
  } }} />);
  expect(screen.queryByRole('columnheader', { name: 'End' })).toBeNull();
  const rows = container.querySelectorAll('.result tbody tr');
  expect(rows).toHaveLength(2);
  expect(rows[0]).toHaveTextContent('none');
  expect(rows[1]).toHaveTextContent('Total');
});

test('rich display preserves ranked rows and totals without adding a percentage total', () => {
  const display = { table: {
    total_rows: [2],
    columns: [{ key: 'end', label: 'End' }, { key: 'rank', label: 'Rank', type: 'number' }, { key: 'region', label: 'Region' }, { key: 'amount', label: 'Amount', type: 'number' }, { key: 'share', label: 'Share', type: 'number' }],
    rows: [['top', 1, 'North', 30, .6], ['bottom', 1, 'South', 5, .1], [null, null, 'Total', 50, null]],
  } };
  const { container } = render(<Answer answer={{ behaviour: 'figure', result, display }} />);
  const rows = container.querySelectorAll('.rich-table tbody tr');
  expect(rows).toHaveLength(3);
  expect(rows[0]).toHaveTextContent('top');
  expect(rows[1]).toHaveTextContent('bottom');
  expect(rows[2]).toHaveTextContent('Total');
  expect(within(rows[2]).getAllByRole('cell').at(-1)).toBeEmptyDOMElement();
});


test('period subtotal labels preserve the period and display count and arithmetic totals', () => {
  const shaped = {
    columns: [{ id: 'Period', kind: 'dimension' }, { id: 'Region', kind: 'dimension' },
      { id: 'Count', kind: 'count' }, { id: 'Difference', kind: 'derived', currency: 'USD' }, { id: 'Share', kind: 'derived' }],
    rows: [
      { panel: null, total: true, dimensions: [{ kind: 'date', value: '2025-04-01' }, { kind: 'null' }], cells: [cell(7), cell(-3), cell(.4)] },
      { panel: null, total: true, dimensions: [{ kind: 'null' }, { kind: 'null' }], cells: [cell(9), cell(2), cell(1)] },
    ],
  };
  const { container } = render(<Answer answer={{ behaviour: 'figure', result: shaped }} />);
  const rows = container.querySelectorAll('.result tbody tr');
  expect(rows[0]).toHaveTextContent('2025-04-01Total7USD -3');
  expect(rows[1]).toHaveTextContent('Total9USD 2');
  expect(within(rows[1]).getAllByRole('cell').at(-1)).toHaveTextContent('1');
});

const labelled = {
  columns: [
    { id: 'month', kind: 'dimension', label: 'Month' },
    { id: 'net', kind: 'measure', label: 'Net', currency: 'USD', display_order: 3 },
    { id: 'i', kind: 'measure', label: 'Incoming', currency: 'USD', display_order: 1 },
    { id: 'o', kind: 'measure', label: 'Outgoing', currency: 'USD', display_order: 2 },
    { id: 'n', kind: 'count', label: 'Groups', display_order: 5 },
    { id: 'r', kind: 'derived', label: 'Ratio', format: 'ratio', display_order: 4 },
  ],
  rows: [{ dimensions: [{ kind: 'text', value: '2025-04' }], cells: [cell(75), cell(125), cell(-50), cell(8), cell(2.5)] }],
};

test('named outputs follow question order without moving their values or citations', () => {
  const { container } = render(<Answer answer={{ behaviour: 'combined', result: labelled }} />);
  expect(screen.getAllByRole('columnheader').map(c => c.textContent)).toEqual(['Month', 'Incoming (USD)', 'Outgoing (USD)', 'Net (USD)', 'Ratio', 'Groups']);
  const values = within(container.querySelector('.result tbody tr')).getAllByRole('cell');
  expect(values.map(c => c.textContent)).toEqual(['2025-04', 'USD 125.00', 'USD -50.00', 'USD 75.00', '2.50 : 1', '8']);
});

test.each([false, true])('comparison formats keep money and percent on detail and total rows, rich=%s', rich => {
  const shaped = {
    columns: [{ id: 'segment', label: 'Segment', kind: 'dimension' },
      { id: 'base', label: 'This period', kind: 'comparison', currency: 'USD' },
      { id: 'other', label: 'Prior period', kind: 'comparison', currency: 'USD' },
      { id: 'df', label: 'Difference', kind: 'derived', currency: 'USD' },
      { id: 'pc', label: '% change', kind: 'derived', format: 'percent' }],
    rows: [
      { dimensions: [{ kind: 'text', value: 'Group A' }], cells: [cell(25), cell(20), cell(5), cell(.25)] },
      { total: true, dimensions: [{ kind: 'null' }], cells: [cell(75), cell(50), cell(25), cell(.5)] },
    ],
  };
  const display = rich ? { table: {
    columns: shaped.columns.map(c => ({ ...c, key: c.id, type: c.kind === 'dimension' ? 'text' : 'number' })),
    rows: [['Group A', 25, 20, 5, .25], ['Total', 75, 50, 25, .5]], total_rows: [1],
  } } : undefined;
  const { container } = render(<Answer answer={{ behaviour: 'combined', result: shaped, display }} />);
  const rows = container.querySelectorAll('tbody tr');
  const cells = row => [...row.querySelectorAll('td')].map(td => td.textContent);
  expect(cells(rows[0]).slice(-2)).toEqual(['USD 5.00', '25.00%']);
  expect(cells(rows[1]).slice(-2)).toEqual(['USD 25.00', '50.00%']);
});
