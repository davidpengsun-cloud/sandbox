import React from 'react';
import { afterEach, expect, test, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import fixture from './fixtures/graph_page.json';

// What GraphView asks of xyflow for the drawing budget (cerebro.md §7.5): only on-screen
// elements drawn, and node types that keep their identity so no node is mounted again.
const seen = [];
vi.mock('@xyflow/react', async (original) => ({
  ...(await original()),
  ReactFlow: (props) => { seen.push(props); return null; },
}));
const { default: GraphView, NODE_TYPES } = await import('./GraphView');

afterEach(() => { cleanup(); seen.length = 0; });

test('only on-screen elements are drawn, with the same node types on every render', async () => {
  const reply = (body) => ({ ok: true, status: 200, json: async () => body });
  const fetcher = vi.fn().mockResolvedValueOnce(reply(fixture.pages[0])).mockResolvedValueOnce(reply(fixture.pages[1]));
  const worker = { terminate: vi.fn(), postMessage(message) {
    queueMicrotask(() => worker.onmessage({ data: { positions: Object.fromEntries(message.graph.nodes.map(({ id }) => [id, { x: 0, y: 0 }])) } }));
  } };
  const actions = { selectNode: vi.fn(), setGraphPosition: vi.fn() };
  const view = render(<GraphView state={{ node: null }} actions={actions} fetcher={fetcher} makeWorker={() => worker} />);
  await waitFor(() => expect(seen.length).toBeGreaterThan(0));
  view.rerender(<GraphView state={{ node: 'domain:sales' }} actions={actions} fetcher={fetcher} makeWorker={() => worker} />);
  expect(seen.length).toBeGreaterThan(1);
  for (const props of seen) {
    expect(props.onlyRenderVisibleElements).toBe(true);
    expect(props.nodeTypes).toBe(NODE_TYPES);
  }
  const [first, last] = [seen[0], seen.at(-1)];
  expect(last.edges).toBe(first.edges);
  expect(last.nodes.filter((node, i) => node !== first.nodes[i]).map((node) => node.id)).toEqual(['domain:sales']);
  expect(last.nodes.every((node) => node.type === 'cerebro' && node.connectable === false)).toBe(true);
});

test('term links are drawn off at first, but for the last answer\'s path, and the switch draws them', async () => {
  const reply = (body) => ({ ok: true, status: 200, json: async () => body });
  const fetcher = vi.fn().mockResolvedValueOnce(reply(fixture.pages[0])).mockResolvedValueOnce(reply(fixture.pages[1]));
  const worker = { terminate: vi.fn(), postMessage(message) {
    queueMicrotask(() => worker.onmessage({ data: { positions: Object.fromEntries(message.graph.nodes.map(({ id }) => [id, { x: 0, y: 0 }])) } }));
  } };
  const lastPath = { nodes: [{ id: 'T', graph_id: 'table:retail.orders' }, { id: 'N', graph_id: 'term:net_revenue' }], edges: [{ from: 'T', to: 'N', graph_kind: 'maps_to' }] };
  render(<GraphView state={{ node: null, lastPath }} actions={{ selectNode: vi.fn(), setGraphPosition: vi.fn() }} fetcher={fetcher} makeWorker={() => worker} />);
  await waitFor(() => expect(seen.length).toBeGreaterThan(0));
  const drawn = () => seen.at(-1).edges.filter((edge) => !edge.hidden).map((edge) => edge.ariaLabel).sort();
  expect(drawn()).toEqual(['in domain, proposed', 'joins, certified', 'maps to, certified', 'maps to, proposed']);
  const toggle = screen.getByRole('switch', { name: 'Term links' });
  expect(toggle).toHaveTextContent('Term links off');
  fireEvent.click(toggle);
  expect(toggle).toHaveAttribute('aria-checked', 'true');
  expect(drawn()).toEqual(['in domain, proposed', 'joins, certified', 'maps to, certified', 'maps to, proposed', 'mentions, inferred']);
});
