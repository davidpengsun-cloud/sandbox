import { expect, test } from 'vitest';
import fixture from './fixtures/graph_page.json';
import { collapseColumns, mergePages } from './graphData';
import { INITIAL_FILTERS, INITIAL_LAYERS, NODE_EXTENT, boundsOf, fitViewport, communityMembers, documentList, overviewCounts, overviewTiles, neighbours, pathHighlight, reasons, toggleLayer, visibleLinkIds, visibleNodeIds } from './graphUx';

const view = () => collapseColumns(mergePages(fixture.pages));
const ids = (set) => [...set].sort();
const linkOf = (v, kind, status) => v.links.find((link) => link.kind === kind && link.status === status);

/* A small catalogue: one community, a domain in it, a table and a document in the domain, and a
   table outside it. */
const catalogue = () => collapseColumns({
  nodes: [
    { kind: 'community', id: 'community:retail', name: 'Retail' },
    { kind: 'domain', id: 'domain:sales', name: 'Sales' },
    { kind: 'table', id: 'table:a', name: 'a' },
    { kind: 'document', id: 'document:d', name: 'd' },
    { kind: 'table', id: 'table:b', name: 'b' },
  ],
  links: [
    { source: 'domain:sales', target: 'community:retail', kind: 'in_community', status: 'proposed', why: 'w1' },
    { source: 'table:a', target: 'domain:sales', kind: 'in_domain', status: 'proposed', why: 'w2' },
    { source: 'document:d', target: 'domain:sales', kind: 'in_domain', status: 'inferred', why: 'w3' },
  ],
});

test('every link keeps its status, so proposed and inferred links are drawn apart from certified ones', () => {
  const v = view();
  expect(new Set(v.links.map((link) => link.status))).toEqual(new Set(['certified', 'proposed', 'inferred']));
  // A column's certified and proposed mappings to one term stay two links, not one.
  const toTerm = v.links.filter((link) => link.source === 'table:retail.orders' && link.target === 'term:net_revenue');
  expect(toTerm.map((link) => link.status).sort()).toEqual(['certified', 'proposed']);
});

test('the legend\'s layers hide a node kind, or a link status, and the links of a hidden node', () => {
  const v = view();
  const all = visibleNodeIds(v, INITIAL_LAYERS, INITIAL_FILTERS);
  expect(all.size).toBe(v.nodes.length);
  expect(visibleLinkIds(v, INITIAL_LAYERS, all).size).toBe(v.links.length);
  const noTables = toggleLayer(INITIAL_LAYERS, 'kinds', 'table');
  const shown = visibleNodeIds(v, noTables, INITIAL_FILTERS);
  expect(ids(shown)).toEqual(['document:pricing-policy', 'domain:sales', 'term:net_revenue']);
  expect([...visibleLinkIds(v, noTables, shown)].map((id) => v.links.find((link) => link.id === id).kind)).toEqual(['mentions']);
  const noProposed = toggleLayer(INITIAL_LAYERS, 'statuses', 'proposed');
  const links = [...visibleLinkIds(v, noProposed, all)].map((id) => v.links.find((link) => link.id === id).status);
  expect(links).not.toContain('proposed');
  expect(links).toContain('certified');
  expect(toggleLayer(noProposed, 'statuses', 'proposed').statuses.proposed).toBe(true);
});

test('the filters: a name or a column\'s name, and a community through its domains', () => {
  const v = view();
  expect(ids(visibleNodeIds(v, INITIAL_LAYERS, { ...INITIAL_FILTERS, text: 'NET_AMOUNT' }))).toEqual(['table:retail.orders']);
  expect(ids(visibleNodeIds(v, INITIAL_LAYERS, { ...INITIAL_FILTERS, text: 'pricing' }))).toEqual(['document:pricing-policy']);
  const c = catalogue();
  expect(communityMembers(c, '')).toBeNull();
  expect(ids(visibleNodeIds(c, INITIAL_LAYERS, { ...INITIAL_FILTERS, community: 'community:retail' }))).toEqual(['community:retail', 'document:d', 'domain:sales', 'table:a']);
});

/* A path in KGE's real graph_path shape ({ nodes, edges, unused }, contextpath.py): each node carries the
   read's id (graph_id) or null, each edge the read's link kind (graph_kind) or null. */
const node = (id, type, graph_id, extra = {}) => ({ id, type, name: id.split(':')[1], label: '', group: 'tables', used: true, state: 'done', certified: false, badges: [], detail: '', graph_id, ...extra });
const edge = (from, to, type, graph_kind, extra = {}) => ({ from, to, type, label: type, planner: false, used: true, state: 'done', graph_kind, ...extra });

test('the last answer\'s path is highlighted by the read ids KGE names; a node the person may not read is skipped', () => {
  const v = view();
  const lastPath = {
    nodes: [
      node('Table:retail.orders', 'Table', 'table:retail.orders'),
      node('Table:retail.customers', 'Table', 'table:retail.customers'),
      node('Term:net-revenue', 'Term', 'term:net_revenue'),
      node('Term:hidden-term', 'Term', 'term:hidden'),
      node('Words:revenue', 'Words', null),
      node('Table:withheld-1', 'Table', null, { name: 'withheld' }),
    ],
    edges: [
      edge('Table:retail.orders', 'Table:retail.customers', 'JoinRule', 'joins'),
      edge('Words:revenue', 'Term:net-revenue', 'matches', null),
      edge('Term:net-revenue', 'Table:retail.orders', 'USES_FACT', null),
      edge('Table:retail.orders', 'Table:withheld-1', 'JoinRule', 'joins'),
    ],
    unused: 0,
  };
  const path = pathHighlight(v, lastPath);
  expect(ids(path.nodes)).toEqual(['table:retail.customers', 'table:retail.orders', 'term:net_revenue']);
  // Only the JoinRule step lights a link, the joins link; the maps_to links between orders and net_revenue are
  // not lit by a step of another kind.
  expect([...path.links].map((id) => v.links.find((link) => link.id === id).kind)).toEqual(['joins']);
  expect(path.nodes.has('term:hidden')).toBe(false);
  expect(pathHighlight(v, null).nodes.size).toBe(0);
  expect(pathHighlight(null, lastPath).links.size).toBe(0);
});

test('a path from before graph_id, or an unused step, lights nothing; the page keeps no id mapping of its own', () => {
  const v = view();
  const old = { nodes: [{ id: 'Table:retail.orders', type: 'Table' }, { id: 'table:retail.orders', type: 'Table' }], edges: [], unused: 0 };
  expect(pathHighlight(v, old).nodes.size).toBe(0);
  const unused = {
    nodes: [node('Table:retail.orders', 'Table', 'table:retail.orders'), node('Table:retail.customers', 'Table', 'table:retail.customers', { used: false })],
    edges: [edge('Table:retail.orders', 'Table:retail.customers', 'JoinRule', 'joins', { used: false })],
    unused: 1,
  };
  const path = pathHighlight(v, unused);
  expect(ids(path.nodes)).toEqual(['table:retail.orders']);
  expect(path.links.size).toBe(0);
});

test('a node\'s links are grouped in its kind\'s sections, each with every reason it exists', () => {
  const v = view();
  const sections = neighbours(v, 'table:retail.orders');
  expect(sections.map((section) => section.title)).toEqual(['Business terms', 'Domains and communities', 'Tables it joins']);
  const term = sections[0].links.find(({ link }) => link.status === 'certified');
  expect(term.other.name).toBe('Net revenue');
  expect(reasons(term.link)).toEqual(['A steward certified this mapping.']);
  expect(neighbours(v, 'nowhere')).toEqual([]);
  expect(neighbours(catalogue(), 'table:a').map((section) => section.title)).toEqual(['Domains and communities']);
  expect(reasons(linkOf(v, 'mentions', 'inferred'))).toHaveLength(1);
  expect(reasons(null)).toEqual([]);
});

test('All / Structured / Unstructured: each narrows to its own assets and what they link to, never the other side\'s', () => {
  const v = view();
  expect(ids(visibleNodeIds(v, INITIAL_LAYERS, { ...INITIAL_FILTERS, assets: 'structured' }))).toEqual(['domain:sales', 'table:retail.customers', 'table:retail.orders', 'term:net_revenue']);
  // The document mentions the term; the domain holds only a table, so it goes.
  expect(ids(visibleNodeIds(v, INITIAL_LAYERS, { ...INITIAL_FILTERS, assets: 'unstructured' }))).toEqual(['document:pricing-policy', 'term:net_revenue']);
  expect(visibleNodeIds(v, INITIAL_LAYERS, { ...INITIAL_FILTERS, assets: 'all' }).size).toBe(v.nodes.length);
  // A document in a domain beside a table keeps the domain, but not the table.
  const c = catalogue();
  expect(ids(visibleNodeIds(c, INITIAL_LAYERS, { ...INITIAL_FILTERS, assets: 'unstructured' }))).toEqual(['document:d', 'domain:sales']);
  expect(ids(visibleNodeIds(c, INITIAL_LAYERS, { ...INITIAL_FILTERS, assets: 'structured' }))).toEqual(['domain:sales', 'table:a', 'table:b']);
  // While term links are hidden, a term is not reached through one.
  expect(ids(visibleNodeIds(v, INITIAL_LAYERS, { ...INITIAL_FILTERS, assets: 'unstructured' }, { termLinks: false }))).toEqual(['document:pricing-policy']);
  expect(ids(visibleNodeIds(v, INITIAL_LAYERS, { ...INITIAL_FILTERS, assets: 'structured' }, { termLinks: false }))).toEqual(['domain:sales', 'table:retail.customers', 'table:retail.orders']);
});

test('term links off hides every link to or from a term, but those on the last answer\'s path', () => {
  const v = view();
  const all = visibleNodeIds(v, INITIAL_LAYERS, INITIAL_FILTERS);
  const kinds = (set) => [...set].map((id) => v.links.find((link) => link.id === id).kind).sort();
  expect(kinds(visibleLinkIds(v, INITIAL_LAYERS, all, { termLinks: false }))).toEqual(['in_domain', 'joins']);
  const keep = new Set([linkOf(v, 'mentions', 'inferred').id]);
  expect(kinds(visibleLinkIds(v, INITIAL_LAYERS, all, { termLinks: false, keep }))).toEqual(['in_domain', 'joins', 'mentions']);
  expect(visibleLinkIds(v, INITIAL_LAYERS, all, { termLinks: true }).size).toBe(v.links.length);
});

test('the overview\'s figures come from the graph read, and a tile the data cannot give is replaced', () => {
  const counts = overviewCounts(view());
  expect(counts).toEqual({ tables: 2, columns: 2, documents: 1, repositories: 0, terms: 1, nameMatches: 0, termTags: 3 });
  expect(overviewTiles(counts).map(([label]) => label)).toEqual(['Structured assets', 'Columns', 'Documents', 'Business terms', 'Name-match links', 'Term tags']);
  expect(overviewTiles({ ...counts, repositories: 4 })[3]).toEqual(['Repositories', 4]);
  const named = collapseColumns({
    nodes: [{ kind: 'column', id: 'column:t.c', name: 'c' }, { kind: 'table', id: 'table:t', name: 't' }, { kind: 'term', id: 'term:c', name: 'C' }],
    links: [{ source: 'table:t', target: 'column:t.c', kind: 'has_column', status: 'certified', why: 'x' }, { source: 'column:t.c', target: 'term:c', kind: 'name_match', status: 'inferred', why: 'y' }],
  });
  expect(overviewCounts(named)).toMatchObject({ columns: 1, nameMatches: 1, termTags: 0 });
});

test('the documents list: a short name from the id, the title, and the domain a link places it in', () => {
  expect(documentList(view())).toEqual([{ id: 'document:pricing-policy', short: 'pricing-policy', title: 'Pricing policy', domain: '' }]);
  expect(documentList(catalogue())).toEqual([{ id: 'document:d', short: 'd', title: 'd', domain: 'Sales' }]);
});

test('the box the view fits: the centres of every given node', () => {
  const positions = { a: { x: 10, y: 20 }, b: { x: -30, y: 70 }, c: { x: 500, y: 500 } };
  expect(boundsOf(['a', 'b'], positions, { mark: 10 })).toEqual({ x: -25, y: 25, width: 40, height: 50 });
  expect(boundsOf(['missing'], positions)).toBeNull();
  expect(boundsOf([], positions)).toBeNull();
});

test('the fitted viewport centres the box beside the controls, and never zooms a lone node past the cap', () => {
  const box = { x: 0, y: 0, width: 1000, height: 500 };
  // 1200 x 600 with 200 px of controls on the left and no padding: the box fills the 1000 px beside them.
  expect(fitViewport(box, { width: 1200, height: 600, left: 200, padding: 0 })).toEqual({ x: 200, y: 50, zoom: 1 });
  // A narrow canvas (a phone) spares no room for the controls.
  expect(fitViewport(box, { width: 500, height: 600, left: 200, padding: 0 })).toEqual({ x: 0, y: 175, zoom: 0.5 });
  expect(fitViewport({ x: 10, y: 10, width: 12, height: 12 }, { width: 800, height: 600, padding: 0.1 }).zoom).toBe(1.5);
  expect(fitViewport({ x: 0, y: 0, width: 1e6, height: 1e6 }, { width: 800, height: 600, minZoom: 0.05 }).zoom).toBe(0.05);
});

test('the fitted viewport leaves screen room for each node\'s mark and name, which keep their size on screen', () => {
  const extent = { left: 10, right: 170, up: 10, down: 10 };
  // Zoomed out, the 180 px of mark and name are the same on screen at any zoom: 500 px are left for the 1000 units.
  expect(fitViewport({ x: 0, y: 0, width: 1000, height: 100 }, { width: 680, height: 600, padding: 0, extent })).toEqual({ x: 10, y: 275, zoom: 0.5 });
  // Zoomed in past 1, they grow with the view: the box and the room together just fill the canvas's width.
  const near = fitViewport({ x: 0, y: 0, width: 100, height: 0 }, { width: 680, height: 600, padding: 0, maxZoom: 5, extent });
  expect(near.zoom).toBeCloseTo(680 / 280);
  expect(near.x - extent.left * near.zoom).toBeCloseTo(0);
  expect(near.x + (100 + extent.right) * near.zoom).toBeCloseTo(680);
  // The graph's own: a name's room to the right of the mark, the largest mark's half around its centre.
  expect(NODE_EXTENT.right).toBeGreaterThan(NODE_EXTENT.left);
  expect(NODE_EXTENT.up).toBe(NODE_EXTENT.down);
});

test('the fitted viewport leaves the filter row\'s height at the top and the open assistant\'s at the foot', () => {
  const box = { x: 0, y: 0, width: 400, height: 400 };
  // 600 px tall, 100 kept at the top and 100 at the foot: the box fills the 400 px between them.
  expect(fitViewport(box, { width: 1200, height: 600, top: 100, bottom: 100, padding: 0 })).toEqual({ x: 400, y: 100, zoom: 1 });
  // Too short to spare both: the foot is given up first, then the top.
  expect(fitViewport(box, { width: 1200, height: 300, top: 50, bottom: 100, padding: 0, maxZoom: 5 }).zoom).toBe(0.625);
  const short = fitViewport(box, { width: 1200, height: 220, top: 50, bottom: 100, padding: 0, maxZoom: 5 });
  expect(short.zoom).toBeCloseTo(0.55);
  expect(short.y).toBeCloseTo(0);
});
