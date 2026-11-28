import React from 'react';
import { afterAll, afterEach, beforeAll, expect, test, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import fixture from './fixtures/graph_page.json';
import { NODE_EXTENT, boundsOf, fitViewport } from './graphUx';

// The view fits to what is shown (#539): after every filter change, when the canvas changes size,
// and on Fit. xyflow's own setViewport is kept and watched.
const fits = [];
vi.mock('@xyflow/react', async (original) => {
  const real = await original();
  return {
    ...real,
    useReactFlow: () => {
      const flow = real.useReactFlow();
      return { ...flow, setViewport: (viewport, options) => { fits.push({ viewport, options }); return flow.setViewport(viewport, options); } };
    },
  };
});
const { default: GraphView, FIT_PADDING } = await import('./GraphView');

// The page measures no layout here, so the canvas is given a size: xyflow reads it from offsetWidth and offsetHeight.
let size = { width: 1000, height: 600 };
const kept = {};
beforeAll(() => {
  for (const [prop, key] of [['offsetWidth', 'width'], ['offsetHeight', 'height']]) {
    kept[prop] = Object.getOwnPropertyDescriptor(HTMLElement.prototype, prop);
    Object.defineProperty(HTMLElement.prototype, prop, { configurable: true, get() { return size[key]; } });
  }
});
afterAll(() => { for (const [prop, descriptor] of Object.entries(kept)) Object.defineProperty(HTMLElement.prototype, prop, descriptor); });
afterEach(() => { cleanup(); fits.length = 0; size = { width: 1000, height: 600 }; });

const reply = (body) => ({ ok: true, status: 200, json: async () => body });
const pagesFetcher = () => vi.fn().mockResolvedValueOnce(reply(fixture.pages[0])).mockResolvedValueOnce(reply(fixture.pages[1]));
function fakeWorker() {
  const worker = { terminate: vi.fn(), postMessage(message) {
    const positions = Object.fromEntries(message.graph.nodes.map(({ id }, i) => [id, { x: 100 * (i + 1), y: 10 * i }]));
    queueMicrotask(() => worker.onmessage({ data: { id: message.id, positions } }));
  } };
  return worker;
}
const actions = () => ({ selectNode: vi.fn(), setGraphPosition: vi.fn(), setGraph: vi.fn() });
const state = (extra = {}) => ({ mode: 'graph', node: null, graphPosition: null, ...extra });
const ids = ['table:retail.orders', 'table:retail.customers', 'term:net_revenue', 'document:pricing-policy', 'domain:sales'];
const positions = Object.fromEntries(ids.map((id, i) => [id, { x: 100 * (i + 1), y: 10 * i }]));
// Every element measures as the whole canvas here, so the controls leave no room to spare beside them.
const fitted = (shown, canvas = size) => fitViewport(boundsOf(shown, positions), { ...canvas, left: canvas.width + 12, padding: FIT_PADDING, minZoom: 0.05, extent: NODE_EXTENT });
const pause = (ms) => new Promise((resolve) => { setTimeout(resolve, ms); });

test('the view fits to the shown nodes once laid out, and again after every filter change', async () => {
  render(<GraphView state={state()} actions={actions()} fetcher={pagesFetcher()} makeWorker={fakeWorker} />);
  await waitFor(() => expect(fits).toHaveLength(1));
  expect(fits[0].viewport).toEqual(fitted(ids));
  expect(fits[0].options.duration).toBe(0);

  // Term links on: the same nodes, fitted again; then Unstructured reaches the document's term through its link.
  fireEvent.click(screen.getByRole('switch', { name: 'Term links' }));
  await waitFor(() => expect(fits).toHaveLength(2));
  expect(fits[1].viewport).toEqual(fitted(ids));

  fireEvent.click(screen.getByRole('button', { name: 'Unstructured 1' }));
  await waitFor(() => expect(fits).toHaveLength(3));
  expect(fits[2].viewport).toEqual(fitted(['term:net_revenue', 'document:pricing-policy']));
  expect(fits[2].viewport).not.toEqual(fits[0].viewport);

  fireEvent.change(screen.getByRole('searchbox', { name: 'Name' }), { target: { value: 'pricing' } });
  await waitFor(() => expect(fits).toHaveLength(4));
  expect(fits[3].viewport).toEqual(fitted(['document:pricing-policy']));

  fireEvent.change(screen.getByRole('searchbox', { name: 'Name' }), { target: { value: '' } });
  await waitFor(() => expect(fits).toHaveLength(5));
  fireEvent.click(screen.getByRole('button', { name: 'Documents' }));
  await waitFor(() => expect(fits).toHaveLength(6));
  expect(fits[5].viewport).toEqual(fitted(['term:net_revenue']));
});

test('Fit fits again on demand, and a change of the canvas size fits again', async () => {
  render(<GraphView state={state()} actions={actions()} fetcher={pagesFetcher()} makeWorker={fakeWorker} />);
  await waitFor(() => expect(fits).toHaveLength(1));
  fireEvent.click(screen.getByRole('button', { name: 'Fit' }));
  expect(fits).toHaveLength(2);
  expect(fits[1].viewport).toEqual(fits[0].viewport);
  size = { width: 1400, height: 600 };
  window.dispatchEvent(new Event('resize'));
  await waitFor(() => expect(fits).toHaveLength(3));
  expect(fits[2].viewport).toEqual(fitted(ids, { width: 1400, height: 600 }));
});

test('a view restored from the page state is kept until the first change, then fits', async () => {
  const act$ = actions();
  render(<GraphView state={state()} actions={act$} fetcher={pagesFetcher()} makeWorker={fakeWorker} />);
  await waitFor(() => expect(act$.setGraph).toHaveBeenCalled());
  cleanup();
  fits.length = 0;
  const graph = act$.setGraph.mock.calls[0][0];
  render(<GraphView state={state({ graph, graphPosition: { x: 5, y: 6, zoom: 0.8 } })} actions={actions()} fetcher={vi.fn()} makeWorker={fakeWorker} />);
  expect(within(screen.getByRole('group', { name: 'Zoom' })).getByText('80%')).toBeInTheDocument();
  expect(fits).toHaveLength(0);
  fireEvent.click(screen.getByRole('button', { name: 'Structured 2' }));
  await waitFor(() => expect(fits).toHaveLength(1));
});

test('F fits and C centres from anywhere on the page but a field; the buttons show their keys', async () => {
  render(<GraphView state={state()} actions={actions()} fetcher={pagesFetcher()} makeWorker={fakeWorker} />);
  await waitFor(() => expect(fits).toHaveLength(1));
  expect(screen.getByRole('button', { name: 'Fit' })).toHaveAttribute('aria-keyshortcuts', 'F');
  expect(screen.getByRole('button', { name: 'Center' })).toHaveAttribute('aria-keyshortcuts', 'C');
  fireEvent.keyDown(window, { key: 'f' });
  expect(fits).toHaveLength(2);
  fireEvent.keyDown(screen.getByRole('searchbox', { name: 'Name' }), { key: 'f' });
  fireEvent.keyDown(window, { key: 'f', ctrlKey: true });
  expect(fits).toHaveLength(2);
});

test('a new answer\'s path is fitted to, until Fit fits every shown node again', async () => {
  const graphActions = actions();
  const { rerender } = render(<GraphView state={state()} actions={graphActions} fetcher={pagesFetcher()} makeWorker={fakeWorker} />);
  await waitFor(() => expect(fits).toHaveLength(1));
  const graph = graphActions.setGraph.mock.calls[0][0];
  const lastPath = { nodes: [{ id: 'a', graph_id: 'table:retail.orders' }, { id: 'b', graph_id: 'term:net_revenue' }], edges: [] };
  rerender(<GraphView state={state({ graph, lastPath })} actions={graphActions} fetcher={vi.fn()} makeWorker={fakeWorker} />);
  await waitFor(() => expect(fits).toHaveLength(2));
  expect(fits[1].viewport).toEqual(fitViewport(boundsOf(['table:retail.orders', 'term:net_revenue'], positions), { ...size, left: size.width + 12, padding: FIT_PADDING, minZoom: 0.05, maxZoom: 1, extent: NODE_EXTENT }));
  fireEvent.click(screen.getByRole('button', { name: 'Fit' }));
  expect(fits[2].viewport).toEqual(fitted(ids));
});

/* An answer given in Chat mode is fitted to on the return to Graph, though the view was kept; the same path, already
   fitted to, keeps the view. */
test('a kept view is fitted to a path new since the view was last fitted, and kept for the path already fitted to', async () => {
  const first = actions();
  render(<GraphView state={state()} actions={first} fetcher={pagesFetcher()} makeWorker={fakeWorker} />);
  await waitFor(() => expect(first.setGraph).toHaveBeenCalled());
  cleanup();
  fits.length = 0;
  const graph = first.setGraph.mock.calls[0][0];
  const lastPath = { nodes: [{ id: 'a', graph_id: 'table:retail.orders' }, { id: 'b', graph_id: 'term:net_revenue' }], edges: [] };
  const key = JSON.stringify(lastPath);
  const kept = { x: 5, y: 6, zoom: 0.8 };
  const back = { ...actions(), setFittedPath: vi.fn() };
  render(<GraphView state={state({ graph, lastPath, graphPosition: kept, fittedPath: null })} actions={back} fetcher={vi.fn()} makeWorker={fakeWorker} />);
  await waitFor(() => expect(fits).toHaveLength(1));
  expect(fits[0].viewport).toEqual(fitViewport(boundsOf(['table:retail.orders', 'term:net_revenue'], positions), { ...size, left: size.width + 12, padding: FIT_PADDING, minZoom: 0.05, maxZoom: 1, extent: NODE_EXTENT }));
  expect(back.setFittedPath).toHaveBeenCalledWith(key);
  cleanup();
  fits.length = 0;
  render(<GraphView state={state({ graph, lastPath, graphPosition: kept, fittedPath: key })} actions={{ ...actions(), setFittedPath: vi.fn() }} fetcher={vi.fn()} makeWorker={fakeWorker} />);
  await pause(50);
  expect(fits).toHaveLength(0);
  expect(within(screen.getByRole('group', { name: 'Zoom' })).getByText('80%')).toBeInTheDocument();
});

/* The assistant floats over the canvas's foot (CerebroApp draws it beside the view): the fit leaves its height free
   while it is open, and fits again when it is hidden or shown, though the canvas keeps its size. */
test('the fit leaves the open assistant\'s height free, keeps the view as it grows, and fits again when it is hidden', async () => {
  const observers = [];
  const RealObserver = globalThis.ResizeObserver;
  globalThis.ResizeObserver = class {
    constructor(callback) { this.callback = callback; observers.push(this); }
    observe() { queueMicrotask(() => this.callback([])); }
    unobserve() {}
    disconnect() {}
  };
  const rect = (top, bottom, left, right) => ({ top, bottom, left, right, x: left, y: top, width: right - left, height: bottom - top });
  const realRect = Element.prototype.getBoundingClientRect;
  let cardTop = 400;
  Element.prototype.getBoundingClientRect = function box() {
    if (this.classList.contains('react-flow')) return rect(0, 600, 0, 1000);
    if (this.classList.contains('cerebro-dock-card') && !this.hidden) return rect(cardTop, 584, 300, 800);
    return rect(0, 0, 0, 0);
  };
  try {
    const { container } = render(<div className="cerebro-view">
      <GraphView state={state()} actions={actions()} fetcher={pagesFetcher()} makeWorker={fakeWorker} />
      <aside className="cerebro-dock"><section className="cerebro-dock-card">Assistant</section></aside>
    </div>);
    await waitFor(() => expect(fits).toHaveLength(1));
    // The card reaches 200 px up from the canvas's foot, and 12 more are kept between them.
    expect(fits[0].viewport).toEqual(fitViewport(boundsOf(ids, positions), { ...size, left: size.width + 12, bottom: 212, padding: FIT_PADDING, minZoom: 0.05, extent: NODE_EXTENT }));
    await pause(0);
    // An answer makes the card 100 px taller: the person's view is kept.
    cardTop = 300;
    for (const observer of observers) observer.callback([]);
    await pause(250);
    expect(fits).toHaveLength(1);
    container.querySelector('.cerebro-dock-card').hidden = true;
    for (const observer of observers) observer.callback([]);
    await waitFor(() => expect(fits).toHaveLength(2));
    expect(fits[1].viewport).toEqual(fitted(ids));
  } finally {
    globalThis.ResizeObserver = RealObserver;
    Element.prototype.getBoundingClientRect = realRect;
  }
});
