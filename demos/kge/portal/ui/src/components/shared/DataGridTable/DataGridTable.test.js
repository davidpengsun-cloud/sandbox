import { expect, test } from 'vitest';
import { render, screen } from '@testing-library/react';
import DataGridTable from './DataGridTable';
import { readFileSync } from 'node:fs';

const css = readFileSync('src/components/shared/DataGridTable/DataGridTable.css', 'utf8').replace(/\/\*[\s\S]*?\*\//g, '');

test('the grid shows its column headers and rows', async () => {
  render(
    <DataGridTable
      rows={[{ name: 'order_id', type: 'STRING' }, { name: 'amount', type: 'NUMERIC' }]}
      columns={[{ field: 'name', headerName: 'Field Name' }, { field: 'type', headerName: 'Type' }]}
      containerStyle={{ height: '400px' }}
    />,
  );
  expect(await screen.findByText('Field Name')).toBeInTheDocument();
  expect(await screen.findByText('order_id')).toBeInTheDocument();
  expect(screen.getByText('NUMERIC')).toBeInTheDocument();
});

// The layout itself is measured in a browser (tests/e2e/demo_script.cjs, the portal alone): the DOM here has no layout.
test('wrapped cells use the body text line spacing, in this grid only', async () => {
  const rule = css.replace(/\s+/g, ' ').match(/([^{}]+)\{([^{}]*line-height:1\.45[^{}]*)\}/);
  expect(rule).not.toBeNull();
  expect(rule[1].trim()).toBe('.mktplce-grid .ag-cell.ag-cell-wrap-text');
  expect(rule[2]).toMatch(/padding-top:calc\(var\(--ag-grid-size\) \* 2\)/);
  expect(rule[2]).toMatch(/padding-bottom:calc\(var\(--ag-grid-size\) \* 2\)/);
  expect(css).not.toMatch(/(^|[}\s])\.ag-cell-wrap-text\s*\{/);

  const { container } = render(
    <DataGridTable
      rows={[{ description: 'A long description that wraps onto a second line in a narrow column' }]}
      columns={[{ field: 'description', headerName: 'Description' }]}
      containerStyle={{ height: '400px' }}
    />,
  );
  const cell = (await screen.findByText(/A long description/)).closest('.ag-cell');
  expect(cell.classList.contains('ag-cell-wrap-text')).toBe(true);
  expect(container.querySelector('.mktplce-grid').contains(cell)).toBe(true);
});
