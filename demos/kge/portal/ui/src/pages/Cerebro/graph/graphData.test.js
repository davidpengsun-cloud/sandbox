import { expect, test, vi } from 'vitest';
import fixture from './fixtures/graph_page.json';
import { GRAPH_URL, MAX_PAGES, PAGE_LIMIT, collapseColumns, drawnNode, mergePages, readGraphPages } from './graphData';

const reply = (body, status = 200) => ({ ok: status < 400, status, json: async () => body });

test('every page is read, following the cursor until it is absent, at the largest page size', async () => {
  const fetcher = vi.fn().mockResolvedValueOnce(reply(fixture.pages[0])).mockResolvedValueOnce(reply(fixture.pages[1]));
  const signal = new AbortController().signal;
  const { pages, complete } = await readGraphPages({ fetcher, signal });
  expect(pages).toHaveLength(2);
  expect(complete).toBe(true);
  expect(fetcher.mock.calls.map(([url]) => url)).toEqual([`${GRAPH_URL}?limit=${PAGE_LIMIT}`, `${GRAPH_URL}?limit=${PAGE_LIMIT}&cursor=page-2`]);
  expect(fetcher.mock.calls[0][1]).toMatchObject({ signal, credentials: 'same-origin' });
});

test('a cursor that never ends stops at the page bound and says the graph is partial', async () => {
  const fetcher = vi.fn().mockImplementation(async () => reply({ nodes: [], links: [], cursor: 'again' }));
  const { pages, complete } = await readGraphPages({ fetcher });
  expect(pages).toHaveLength(MAX_PAGES);
  expect(complete).toBe(false);
});

test('a refused read is an error the page can word', async () => {
  await expect(readGraphPages({ fetcher: async () => reply({}, 403) })).rejects.toMatchObject({ name: 'GraphReadError', status: 403, message: 'You may not read the graph.' });
  await expect(readGraphPages({ fetcher: async () => reply({}, 502) })).rejects.toMatchObject({ status: 502, message: 'The graph could not be read.' });
});

test('pages merge into one graph: each node once, each link once, links to later pages kept', () => {
  const graph = mergePages([...fixture.pages, fixture.pages[1]]);
  expect(graph.nodes.map((n) => n.id)).toEqual(['table:retail.orders', 'column:retail.orders.net_amount', 'column:retail.orders.customer_id', 'table:retail.customers', 'term:net_revenue', 'document:pricing-policy', 'domain:sales']);
  expect(graph.links).toHaveLength(8);
  expect(graph.links.some((l) => l.target === 'term:net_revenue' && l.status === 'certified')).toBe(true);
});

test('columns fold into their table by the has_column link; their links move to the table', () => {
  const view = collapseColumns(mergePages(fixture.pages));
  expect(view.nodes.map((n) => n.id)).toEqual(['table:retail.orders', 'table:retail.customers', 'term:net_revenue', 'document:pricing-policy', 'domain:sales']);
  expect(view.nodes[0].columns).toEqual(['net_amount', 'customer_id']);
  expect(view.links.some((l) => l.kind === 'has_column')).toBe(false);
  const maps = view.links.filter((l) => l.kind === 'maps_to');
  expect(maps.map((l) => [l.source, l.target, l.status])).toEqual([['table:retail.orders', 'term:net_revenue', 'certified'], ['table:retail.orders', 'term:net_revenue', 'proposed']]);
  expect(maps[0].members[0].why).toBe('A steward certified this mapping.');
  // A link whose end never arrived is not drawn.
  expect(view.links.some((l) => l.target === 'term:hidden')).toBe(false);
});

test('links folded into the same table edge are drawn once, keeping every reason', () => {
  const pages = [{ nodes: [{ kind: 'table', id: 't', name: 't' }, { kind: 'column', id: 'c1', name: 'a' }, { kind: 'column', id: 'c2', name: 'b' }, { kind: 'term', id: 'x', name: 'x' }],
    links: [{ source: 't', target: 'c1', kind: 'has_column', status: 'certified', why: '' }, { source: 't', target: 'c2', kind: 'has_column', status: 'certified', why: '' },
      { source: 'c1', target: 'x', kind: 'maps_to', status: 'certified', why: 'one' }, { source: 'c2', target: 'x', kind: 'maps_to', status: 'certified', why: 'two' },
      { source: 'c1', target: 'c2', kind: 'same_name', status: 'inferred', why: 'a link inside one table' }] }];
  const view = collapseColumns(mergePages(pages));
  expect(view.links).toHaveLength(1);
  expect(view.links[0].members.map((m) => m.why)).toEqual(['one', 'two']);
});

test('a column is never taken for one by its id alone, and an address naming a column opens its table', () => {
  const view = collapseColumns(mergePages(fixture.pages));
  expect(drawnNode(view, 'column:retail.orders.net_amount')).toBe('table:retail.orders');
  expect(drawnNode(view, 'term:net_revenue')).toBe('term:net_revenue');
  expect(drawnNode(view, 'term:nowhere')).toBeNull();
  const lone = collapseColumns({ nodes: [{ kind: 'column', id: 'column:a.b.c', name: 'c' }], links: [] });
  expect(lone.nodes.map((n) => n.id)).toEqual(['column:a.b.c']);
});

test('the drawn graph carries no figure from the data', () => {
  const view = collapseColumns(mergePages(fixture.pages));
  const numbers = JSON.stringify(view.nodes.concat(view.links)).match(/:\s*-?\d/g);
  expect(numbers).toBeNull();
});
