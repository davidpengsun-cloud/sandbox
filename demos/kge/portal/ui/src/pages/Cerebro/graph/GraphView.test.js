import React from 'react';
import { afterEach, expect, test, vi } from 'vitest';
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import fixture from './fixtures/graph_page.json';
import GraphView, { labelBand } from './GraphView';

// The layout must never run on the main thread: GraphView does not import it, and this guards
// against a future import calling it here. The build's own worker chunk is checked by `vite build`.
vi.mock('./layout', () => ({ layoutGraph: () => { throw new Error('layout ran on the main thread'); } }));

const reply = (body, status = 200) => ({ ok: status < 400, status, json: async () => body });
const pagesFetcher = () => vi.fn().mockResolvedValueOnce(reply(fixture.pages[0])).mockResolvedValueOnce(reply(fixture.pages[1]));

function fakeWorker(answer) {
  const worker = { posted: [], terminate: vi.fn(), postMessage(message) {
    worker.posted.push(message);
    queueMicrotask(() => worker.onmessage({ data: answer(message) }));
  } };
  return worker;
}
const place = (message) => ({ id: message.id, positions: Object.fromEntries(message.graph.nodes.map(({ id }, i) => [id, { x: 100 * (i + 1), y: 7 }])) });

const actions = () => ({ selectNode: vi.fn(), setGraphPosition: vi.fn() });
const state = (extra = {}) => ({ mode: 'graph', node: null, graphPosition: null, ...extra });

afterEach(() => { cleanup(); vi.restoreAllMocks(); });

test('the graph is laid out by the worker, never on the main thread, with columns folded into tables', async () => {
  const worker = fakeWorker(place);
  const { container } = render(<GraphView state={state()} actions={actions()} fetcher={pagesFetcher()} makeWorker={() => worker} />);
  await waitFor(() => expect(container.querySelector('[data-id="table:retail.orders"]')).not.toBeNull());
  expect(worker.posted).toHaveLength(1);
  expect(worker.posted[0].graph.nodes.map((n) => n.id)).toEqual(['table:retail.orders', 'table:retail.customers', 'term:net_revenue', 'document:pricing-policy', 'domain:sales']);
  expect(container.querySelector('[data-id="column:retail.orders.net_amount"]')).toBeNull();
  expect(container.querySelector('[data-id="table:retail.orders"]').style.transform).toContain('translate(100px,7px)');
  expect(container.querySelector('[data-id="domain:sales"]').style.transform).toContain('translate(500px,7px)');
  expect(worker.terminate).toHaveBeenCalled();
});

test('a node clicked is selected through the shared state; an address naming a column shows its table', async () => {
  const act$ = actions();
  const { container } = render(<GraphView state={state({ node: 'column:retail.orders.net_amount' })} actions={act$} fetcher={pagesFetcher()} makeWorker={() => fakeWorker(place)} />);
  await waitFor(() => expect(screen.getByRole('heading', { name: 'orders' })).toBeInTheDocument());
  expect(screen.getByRole('list', { name: 'Columns' })).toHaveTextContent('net_amount');
  expect(container.querySelector('[data-id="table:retail.orders"]').classList.contains('selected')).toBe(true);
  fireEvent.click(container.querySelector('[data-id="term:net_revenue"]'));
  expect(act$.selectNode).toHaveBeenCalledWith('term:net_revenue');
  fireEvent.click(screen.getByRole('button', { name: 'Back to overview' }));
  expect(act$.selectNode).toHaveBeenLastCalledWith(null);
});

test('a refused read, a failed layout and no worker each say so, and nothing is laid out here', async () => {
  render(<GraphView state={state()} actions={actions()} fetcher={async () => reply({}, 403)} makeWorker={() => fakeWorker(place)} />);
  expect(await screen.findByRole('alert')).toHaveTextContent('You may not read the graph.');
  cleanup();
  render(<GraphView state={state()} actions={actions()} fetcher={pagesFetcher()} makeWorker={() => fakeWorker(() => ({ error: 'boom' }))} />);
  expect(await screen.findByRole('alert')).toHaveTextContent('The graph could not be laid out.');
  cleanup();
  render(<GraphView state={state()} actions={actions()} fetcher={pagesFetcher()} makeWorker={() => { throw new ReferenceError('Worker is not defined'); }} />);
  expect(await screen.findByRole('alert')).toHaveTextContent('This browser cannot lay out the graph.');
});

test('an empty graph says so; leaving the page stops the read', async () => {
  render(<GraphView state={state()} actions={actions()} fetcher={async () => reply({ nodes: [], links: [] })} makeWorker={() => fakeWorker(place)} />);
  expect(await screen.findByText('There is nothing in the graph you may read yet.')).toBeInTheDocument();
  cleanup();
  let seen;
  const { unmount } = render(<GraphView state={state()} actions={actions()} fetcher={(_url, { signal }) => { seen = signal; return new Promise(() => {}); }} makeWorker={() => fakeWorker(place)} />);
  expect(screen.getByRole('status')).toHaveTextContent('Reading the graph you may read');
  act(() => unmount());
  expect(seen.aborted).toBe(true);
});

test('a laid-out graph is kept in the page state and reused, not read again', async () => {
  const act$ = { ...actions(), setGraph: vi.fn() };
  const fetcher = pagesFetcher();
  const { container, unmount } = render(<GraphView state={state()} actions={act$} fetcher={fetcher} makeWorker={() => fakeWorker(place)} />);
  await waitFor(() => expect(act$.setGraph).toHaveBeenCalledTimes(1));
  const kept = act$.setGraph.mock.calls[0][0];
  expect(kept).toMatchObject({ status: 'ready', complete: true });
  unmount();
  const again = vi.fn();
  const view = render(<GraphView state={state({ graph: kept })} actions={act$} fetcher={again} makeWorker={() => { throw new Error('laid out again'); }} />);
  expect(view.container.querySelector('[data-id="table:retail.orders"]')).not.toBeNull();
  expect(again).not.toHaveBeenCalled();
  expect(container).toBeTruthy();
});

test('a worker that answers after the page is left changes nothing', async () => {
  let worker;
  const act$ = { ...actions(), setGraph: vi.fn() };
  const { unmount } = render(<GraphView state={state()} actions={act$} fetcher={pagesFetcher()} makeWorker={() => { worker = { postMessage: vi.fn(), terminate: vi.fn() }; return worker; }} />);
  await waitFor(() => expect(worker?.postMessage).toHaveBeenCalled());
  unmount();
  expect(worker.terminate).toHaveBeenCalled();
  worker.onmessage({ data: place(worker.postMessage.mock.calls[0][0]) });
  worker.onerror?.();
  expect(act$.setGraph).not.toHaveBeenCalled();
});

test('the graph takes the free width and the overview a fixed column, with no column for the assistant', async () => {
  const { readFileSync } = await import('node:fs');
  const css = readFileSync(`${process.cwd()}/src/pages/Cerebro/graph/graph.css`, 'utf8');
  expect(css).toMatch(/\.cerebro-view > \.cerebro-graph \{[^}]*grid-template-columns:minmax\(0, 1fr\) var\(--cerebro-overview-width\);/);
});

test('the graph\'s styles use the portal tokens only', async () => {
  const { readFileSync } = await import('node:fs');
  const { fileURLToPath } = await import('node:url');
  const read = (name) => readFileSync(fileURLToPath(new URL(name, import.meta.url)), 'utf8');
  const css = read('./graph.css') + read('./GraphView.js');
  expect(css).not.toMatch(/#[0-9a-fA-F]{3,8}\b/);
  expect(css).not.toMatch(/--v3-[\w-]+\s*:/);
  expect(css).not.toMatch(/border-radius:\s*\d/);
});

const uxActions = () => ({ ...actions(), askAbout: vi.fn() });
const ready = (container) => waitFor(() => expect(container.querySelector('[data-id="table:retail.orders"]')).not.toBeNull());

test('the legend turns a layer off and on, and the filters narrow the graph', async () => {
  const { container } = render(<GraphView state={state()} actions={uxActions()} fetcher={pagesFetcher()} makeWorker={() => fakeWorker(place)} />);
  await ready(container);
  const tables = screen.getByRole('button', { name: 'Structured assets' });
  expect(tables).toHaveAttribute('aria-pressed', 'true');
  fireEvent.click(tables);
  expect(tables).toHaveAttribute('aria-pressed', 'false');
  expect(container.querySelector('[data-id="table:retail.orders"]')).toBeNull();
  expect(container.querySelector('[data-id="term:net_revenue"]')).not.toBeNull();
  fireEvent.click(tables);
  await ready(container);
  for (const name of ['Certified', 'Proposed, not reviewed', 'Inferred, not verified']) expect(screen.getByRole('button', { name })).toHaveAttribute('aria-pressed', 'true');
  fireEvent.change(screen.getByRole('searchbox', { name: 'Name' }), { target: { value: 'pricing' } });
  expect(container.querySelector('[data-id="table:retail.orders"]')).toBeNull();
  expect(container.querySelector('[data-id="document:pricing-policy"]')).not.toBeNull();
  fireEvent.change(screen.getByRole('searchbox', { name: 'Name' }), { target: { value: '' } });
  expect(screen.getByRole('group', { name: 'Asset type' })).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: 'Unstructured 1' }));
  expect(screen.getByRole('button', { name: 'Unstructured 1' })).toHaveAttribute('aria-pressed', 'true');
  expect(screen.getByRole('button', { name: 'All' })).toHaveAttribute('aria-pressed', 'false');
  expect(container.querySelector('[data-id="document:pricing-policy"]')).not.toBeNull();
  expect(container.querySelector('[data-id="table:retail.orders"]')).toBeNull();
  // Unstructured narrows to the document and what it links to: the term it mentions, not the tables' domain.
  expect(container.querySelector('[data-id="domain:sales"]')).toBeNull();
  fireEvent.click(screen.getByRole('button', { name: 'Structured 2' }));
  expect(container.querySelector('[data-id="table:retail.orders"]')).not.toBeNull();
  expect(container.querySelector('[data-id="document:pricing-policy"]')).toBeNull();
  expect(container.querySelector('[data-id="domain:sales"]')).not.toBeNull();
  expect(screen.getByRole('combobox', { name: 'Community' })).toHaveTextContent('All communities');
});

test('the last answer\'s path is highlighted, without the nodes the person may not read', async () => {
  const lastPath = { nodes: [{ id: 'Table:retail.orders', graph_id: 'table:retail.orders' }, { id: 'Term:hidden', graph_id: 'term:hidden' }, { id: 'Term:net-revenue', graph_id: 'term:net_revenue' }], edges: [], unused: 0 };
  const { container } = render(<GraphView state={state({ lastPath })} actions={uxActions()} fetcher={pagesFetcher()} makeWorker={() => fakeWorker(place)} />);
  await ready(container);
  expect(container.querySelector('[data-id="table:retail.orders"]').classList.contains('cerebro-on-path')).toBe(true);
  expect(container.querySelector('[data-id="document:pricing-policy"]').classList.contains('cerebro-on-path')).toBe(false);
  expect(screen.getByText(/path is highlighted: 2 nodes you may read/)).toBeInTheDocument();
});

test('the details pane per kind shows each link\'s status and why, and "Ask about this" goes to the agent', async () => {
  const act$ = uxActions();
  const { container } = render(<GraphView state={state({ node: 'term:net_revenue' })} actions={act$} fetcher={pagesFetcher()} makeWorker={() => fakeWorker(place)} />);
  await ready(container);
  const pane = screen.getByRole('region', { name: 'Term details' });
  expect(pane).toHaveTextContent('A business term.');
  expect(screen.getByRole('region', { name: 'Mapped tables and columns' })).toHaveTextContent('Suggested from the column\'s name; not yet reviewed.');
  expect(screen.getByRole('region', { name: 'Documents that mention it' })).toHaveTextContent('Inferred, not verified');
  fireEvent.click(screen.getByRole('button', { name: 'Ask about this' }));
  expect(act$.askAbout).toHaveBeenCalledWith('term:net_revenue');
  fireEvent.click(screen.getAllByRole('button', { name: /^Why this link: orders/ })[0]);
  const why = screen.getByRole('region', { name: 'Inspect this relationship' });
  expect(why).toHaveTextContent('Why this link exists');
  fireEvent.click(screen.getByRole('button', { name: 'Back' }));
  expect(screen.getByRole('region', { name: 'Term details' })).toBeInTheDocument();
});

test('every shown node carries its name as a label beside its mark; a hidden node draws none', async () => {
  const { container } = render(<GraphView state={state()} actions={uxActions()} fetcher={pagesFetcher()} makeWorker={() => fakeWorker(place)} />);
  await ready(container);
  const labels = () => Object.fromEntries([...container.querySelectorAll('.react-flow__node')].map((node) => [node.dataset.id, node.querySelector('.cerebro-node-label')?.textContent]));
  expect(labels()).toEqual({ 'table:retail.orders': 'orders', 'table:retail.customers': 'customers', 'term:net_revenue': 'Net revenue', 'document:pricing-policy': 'Pricing policy', 'domain:sales': 'Sales' });
  expect(container.querySelector('[data-id="domain:sales"]').classList.contains('cerebro-node-domain')).toBe(true);
  expect(container.querySelector('[data-id="domain:sales"] .cerebro-node-dot')).not.toBeNull();
  // A term none of whose links is shown is loose: zoomed out, its name is not drawn (graph.css).
  const loose = (id) => container.querySelector(`[data-id="${id}"]`).classList.contains('cerebro-loose');
  expect(loose('term:net_revenue')).toBe(true);
  expect(loose('table:retail.orders')).toBe(false);
  fireEvent.click(screen.getByRole('switch', { name: 'Term links' }));
  expect(loose('term:net_revenue')).toBe(false);
  fireEvent.click(screen.getByRole('button', { name: 'Unstructured 1' }));
  expect(Object.keys(labels()).sort()).toEqual(['document:pricing-policy', 'term:net_revenue']);
});

test('with nothing selected the panel shows the overview\'s counts and documents; a selection shows its details, with a way back', async () => {
  const act$ = uxActions();
  const { container, rerender } = render(<GraphView state={state()} actions={act$} fetcher={pagesFetcher()} makeWorker={() => fakeWorker(place)} />);
  await ready(container);
  const overview = screen.getByRole('region', { name: 'Overview' });
  const tiles = Object.fromEntries([...overview.querySelectorAll('.cerebro-tile')].map((tile) => [tile.querySelector('dt').textContent, tile.querySelector('dd').textContent]));
  // Two tables with two columns, one document, no repository (so the terms are counted), one name match, and
  // three term tags: two column mappings and one mention.
  expect(tiles).toEqual({ 'Structured assets': '2', Columns: '2', Documents: '1', 'Business terms': '1', 'Name-match links': '0', 'Term tags': '3' });
  const docs = screen.getByRole('list', { name: 'Documents' });
  expect(docs).toHaveTextContent('pricing-policy');
  expect(docs).toHaveTextContent('Pricing policy');
  expect(screen.getByRole('heading', { name: 'Documents (1)' })).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: /pricing-policy/ }));
  expect(act$.selectNode).toHaveBeenCalledWith('document:pricing-policy');
  rerender(<GraphView state={state({ node: 'document:pricing-policy' })} actions={act$} fetcher={pagesFetcher()} makeWorker={() => fakeWorker(place)} />);
  expect(screen.queryByRole('region', { name: 'Overview' })).toBeNull();
  expect(screen.getByRole('region', { name: 'Document details' })).toHaveTextContent('Pricing policy');
  fireEvent.click(screen.getByRole('button', { name: 'Back to overview' }));
  expect(act$.selectNode).toHaveBeenLastCalledWith(null);
});

test('Fit, Center and the zoom control, which shows the zoom as a percentage', async () => {
  const { container } = render(<GraphView state={state({ graphPosition: { x: 0, y: 0, zoom: 0.45 } })} actions={uxActions()} fetcher={pagesFetcher()} makeWorker={() => fakeWorker(place)} />);
  await ready(container);
  // The percentage is plain text in the Zoom group, not a live region announced at every step.
  const zoomGroup = screen.getByRole('group', { name: 'Zoom' });
  await waitFor(() => expect(zoomGroup.querySelector('.cerebro-zoom-level')).toHaveTextContent(/^45%$/));
  expect(zoomGroup.querySelector('output, [aria-live], [role="status"]')).toBeNull();
  // At 45%, every name is drawn but people's and loose terms' (graph.css); those are drawn zoomed in.
  expect(container.querySelector('.react-flow').dataset.labels).toBe('some');
  expect([labelBand(0.2), labelBand(0.45), labelBand(1)]).toEqual(['few', 'some', 'all']);
  for (const name of ['Fit', 'Center', 'Zoom out', 'Zoom in']) expect(screen.getByRole('button', { name })).toBeInTheDocument();
  const legend = screen.getByRole('button', { name: 'Legend' });
  expect(legend).toHaveAttribute('aria-expanded', 'true');
  fireEvent.click(legend);
  expect(legend).toHaveAttribute('aria-expanded', 'false');
  expect(screen.queryByRole('button', { name: 'Structured assets' })).toBeNull();
  // Folded, the body is hidden, not removed, so the toggle's aria-controls still names it.
  const body = document.getElementById(legend.getAttribute('aria-controls'));
  expect(body).not.toBeNull();
  expect(body.hidden).toBe(true);
});
