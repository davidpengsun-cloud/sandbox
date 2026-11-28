import React from 'react';
import { afterEach, expect, test, vi } from 'vitest';
import { cleanup, render, waitFor } from '@testing-library/react';
import fixture from './fixtures/graph_page.json';
import GraphView, { NODE_TYPES } from './GraphView';
import { drawCounter, LOW_DETAIL_ZOOM } from './GraphNode';

// The drawing budget of cerebro.md §7.5: memoised nodes, less detail zoomed out, handles that
// are anchors only. On-screen-only drawing is held in GraphBudgetProps.test.js.

const reply = (body) => ({ ok: true, status: 200, json: async () => body });
const pagesFetcher = () => vi.fn().mockResolvedValueOnce(reply(fixture.pages[0])).mockResolvedValueOnce(reply(fixture.pages[1]));
function fakeWorker() {
  const worker = { terminate: vi.fn(), postMessage(message) {
    const positions = Object.fromEntries(message.graph.nodes.map(({ id }, i) => [id, { x: 100 * (i + 1), y: 7 }]));
    queueMicrotask(() => worker.onmessage({ data: { id: message.id, positions } }));
  } };
  return worker;
}
const actions = () => ({ selectNode: vi.fn(), setGraphPosition: vi.fn(), setGraph: vi.fn() });
const state = (extra = {}) => ({ mode: 'graph', node: null, graphPosition: null, ...extra });

afterEach(() => { cleanup(); drawCounter.onDraw = null; });

async function laidOut() {
  const act$ = actions();
  const view = render(<GraphView state={state()} actions={act$} fetcher={pagesFetcher()} makeWorker={fakeWorker} />);
  await waitFor(() => expect(act$.setGraph).toHaveBeenCalledTimes(1));
  view.unmount();
  return act$.setGraph.mock.calls[0][0];
}

test('a change of selection draws again only the node left and the node chosen', async () => {
  const kept = await laidOut();
  const draws = [];
  drawCounter.onDraw = (id) => draws.push(id);
  const act$ = actions();
  const view = render(<GraphView state={state({ graph: kept, node: 'term:net_revenue' })} actions={act$} fetcher={vi.fn()} makeWorker={fakeWorker} />);
  await waitFor(() => expect(draws).toContain('domain:sales'));
  draws.length = 0;
  view.rerender(<GraphView state={state({ graph: kept, node: 'domain:sales' })} actions={act$} fetcher={vi.fn()} makeWorker={fakeWorker} />);
  await waitFor(() => expect(view.container.querySelector('[data-id="domain:sales"]').classList.contains('selected')).toBe(true));
  expect(new Set(draws)).toEqual(new Set(['term:net_revenue', 'domain:sales']));
  expect(NODE_TYPES.cerebro).toBeTruthy();
});

test('zoomed out, a node is a mark without its name; zoomed in, its name is drawn', async () => {
  const kept = await laidOut();
  const far = render(<GraphView state={state({ graph: kept, graphPosition: { x: 0, y: 0, zoom: LOW_DETAIL_ZOOM / 2 } })} actions={actions()} fetcher={vi.fn()} makeWorker={fakeWorker} />);
  const node = far.container.querySelector('[data-id="table:retail.orders"]');
  expect(node.querySelector('.cerebro-node-label')).toBeNull();
  expect(node.querySelector('.cerebro-node-mark').getAttribute('aria-label')).toBe('orders');
  far.unmount();
  const near = render(<GraphView state={state({ graph: kept, graphPosition: { x: 0, y: 0, zoom: 1 } })} actions={actions()} fetcher={vi.fn()} makeWorker={fakeWorker} />);
  expect(near.container.querySelector('[data-id="table:retail.orders"] .cerebro-node-label')).toHaveTextContent('orders');
  expect(near.container.querySelector('.cerebro-node-mark')).toBeNull();
});

test('a node offers no connection: its handles are hidden anchors, never connectable', async () => {
  const kept = await laidOut();
  const { container } = render(<GraphView state={state({ graph: kept })} actions={actions()} fetcher={vi.fn()} makeWorker={fakeWorker} />);
  const handles = [...container.querySelectorAll('.react-flow__handle')];
  expect(handles).toHaveLength(kept.view.nodes.length * 2);
  for (const handle of handles) {
    expect(handle.classList.contains('cerebro-handle')).toBe(true);
    expect(handle.classList.contains('connectable')).toBe(false);
    expect(handle.classList.contains('connectableend')).toBe(false);
    expect(handle.classList.contains('connectablestart')).toBe(false);
  }
  const { readFileSync } = await import('node:fs');
  const css = readFileSync(`${process.cwd()}/src/pages/Cerebro/graph/graph.css`, 'utf8');
  expect(css).toMatch(/\.react-flow__handle\.cerebro-handle\s*\{[^}]*visibility:hidden/);
});
