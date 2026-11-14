import React, { useState } from 'react';
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { expect, test, vi } from 'vitest';
import BehindAnswer, { GraphPath, Sources, Standards } from './BehindAnswer';
import realW01 from './fixtures/w01_graph_path.json';

const me = { id: 'reader@example.com', reads: ['leasing'] };
const base = { question: 'Revenue?', principal: me.id, arm: 'graph', behaviour: 'figure' };
const props = (extra = {}) => ({ api: vi.fn(async () => ({ contexts: [] })), me, open: true, onToggle: vi.fn(), onCurrency: vi.fn(), ...extra });
const select = name => fireEvent.click(screen.getByRole('tab', { name, exact: true }));

test.each([
  [{ behaviour: 'glossary_definition', term: 'Revenue', steward: 'steward', definition: 'Gross takings', references: [{ doc: 'reference' }] }, ['Defined by', "the pack's glossary", 'steward steward', 'Gross takings', 'Shown, not used']],
  [{ behaviour: 'combined', citations: [{ n: 1, doc: 'policy', version: 2, title: 'Policy', why: 'Certified' }], not_cited: [{ doc: 'unused' }], withheld: [{ doc: 'held', why: 'restricted' }] }, ['Cited', 'policy v2', 'Shown, not used', 'unused', 'Held back', 'restricted']],
  [{ behaviour: 'cite', cited_docs: [{ doc: 'gold', shelf: 'certified' }, { doc: 'silver', shelf: 'silver' }, { doc: 'unknown' }] }, ['certified shelf', 'silver shelf: cited all the same', 'uncertified shelf: cited all the same']],
  [{ behaviour: 'cite', cite: ['legacy'] }, ['legacy']],
  [{ behaviour: 'figure', citations: [{ doc: 'attributed', cited_as: 'attributed', attribution: 'Client policy', why: 'Period matched' }, { doc: 'authority', cited_as: 'authority', tier: 'gold' }, { doc: 'searched', tier: 'bronze' }, { doc: 'ungraded' }], superseded_not_cited: ['old'], references: [{ doc: 'reference' }], not_ai_ready: [{ doc: 'not-ready' }], not_readable: [{ doc: 'unreadable' }] }, ['silver, cited with attribution: Client policy · Period matched', 'gold, the authority', 'found by search, with no tier rule; landed bronze', 'not in force for the period asked', 'not-ready', 'unreadable']],
  [{ cite: ['legacy'], superseded_not_cited: ['old'], uncertified_definition_not_cited: [{ id: 'draft-definition' }], uncertified_context_not_cited: [{ id: 'draft-context' }] }, ['certified', 'superseded', 'uncertified definition', 'draft-context']],
  [{}, ['This answer rests on no documents.']],
])('Sources explains document governance without promoting unused evidence: %j', (answer, text) => {
  const { container } = render(<Sources answer={answer} source={1} />);
  for (const part of text) expect(container).toHaveTextContent(part);
  if (answer.behaviour === 'combined') expect(container.querySelector('[data-n="1"]')).toHaveClass('hl');
});

test('standards render certified mappings and omit an empty disclosure', () => {
  const { container, rerender } = render(<Standards standards={[{ label: 'Revenue', note: 'Certified mapping', relation: 'equivalent', standard_name: 'Example', version: '1', iri: 'urn:example:revenue' }]} />);
  expect(screen.getByText('Industry standards (1)')).toBeVisible();
  expect(container.querySelector('table')).toHaveTextContent('RevenueCertified mappingequivalentExample1urn:example:revenue');
  rerender(<Standards standards={[]} />);
  expect(container).toBeEmptyDOMElement();
});

test('Behind the answer is always shown, with one title and one line of who and how', async () => {
  const { container, rerender } = render(<BehindAnswer {...props()} />);
  expect(container.querySelector('#detail')).toBeVisible();
  expect(container.querySelector('#detail').tagName).toBe('SECTION');
  expect(container.querySelector('#detail summary, details#detail')).toBeNull();
  expect(screen.getByRole('region', { name: 'Behind the answer' })).toBe(container.querySelector('#detail'));
  expect(screen.getByRole('tabpanel', { name: 'Map' })).toHaveTextContent('Ask a question and what lies behind its answer shows here.');
  rerender(<BehindAnswer {...props()} purpose="supervision" answer={{ ...base, context: { term: 'Net New Assets' }, trace: [{ period: 'calendar_quarter 2025-Q1' }] }} />);
  expect(container.querySelectorAll('#detail h2')).toHaveLength(1);
  expect(container.querySelector('#detail h2')).toHaveTextContent('Behind “Revenue?”');
  expect(screen.getByRole('region', { name: 'Behind “Revenue?”' })).toBe(container.querySelector('#detail'));
  expect(container.querySelector('#detail')).not.toHaveTextContent('Behind the answer');
  expect(container.querySelector('#about')).toHaveTextContent('graph arm · as reader@example.com for supervision · reads leasing');
  expect(container.querySelector('#about')).not.toHaveTextContent('Revenue?');
  expect([...container.querySelectorAll('.answer-facts dt')].map(dt => dt.textContent)).toEqual(['Term', 'Period']);
  expect(container.querySelector('.answer-facts')).toHaveTextContent('Net New Assets');
  expect(container.querySelector('.answer-facts')).toHaveTextContent('Q1 2025');
  expect(container.querySelector('.answer-facts')).not.toHaveTextContent('calendar_quarter');
});

test.each([
  [{ principal: 'previous@example.com', context: { term: 'Revenue' } }, 'Asked as previous@example.com; signed in now as reader@example.com. Ask again to see the map.'],
  [{ behaviour: 'refuse', reason: 'No certified join path', context: { term: 'Revenue' } }, 'No map for a refused answer: No certified join path'],
  [{ behaviour: 'blocked_access', reason: 'Unit unavailable', denied_warehouses: ['private'] }, 'No map for a refused answer: Unit unavailable'],
  [{ reason: 'No matching term' }, 'No matching term'],
  [{}, 'No term resolved.'],
  [{ context: { term: '__population__' } }, 'Loading the resolved map…'],
  [{ error: 'Service unavailable' }, 'No answer to the last question: Service unavailable'],
])('Map never fetches a refused or mismatched answer: %j', async (answer, text) => {
  const settings = props();
  render(<BehindAnswer {...settings} answer={{ ...base, ...answer }} />);
  expect(screen.getByRole('tabpanel', { name: 'Map' })).toHaveTextContent(text);
  expect(settings.api).not.toHaveBeenCalled();
});

const context = { id: 'revenue', label: 'Revenue', description: 'Certified takings', measure: 'revenue', fact: 'leasing.rent', currency: 'GBP',
  filters: [{ id: 'active', label: 'Active rentals', table: 'rental', mode: 'include' }], row_selectors: [{ id: 'latest', table: 'versions' }],
  joins: [{ id: 'used', from: 'rent', to: 'customers' }, { id: 'required', from: 'rent', to: 'calendar', required: true }, { id: 'optional', from: 'rent', to: 'region' }] };

test.each(['as_reported', 'current'])('resolved Map preserves certified joins, scope and %s time semantics', async time_mode => {
  const answer = { ...base, unit: 'leasing', column: 'rent.amount', context: { term: 'Revenue', context: 'revenue', joins: ['used'], time_mode, group_by: 'region', conditions: ['active only'], across: [{ unit: 'retail', join: 'used' }] } };
  const settings = props({ api: vi.fn(async () => ({ contexts: [context], defined_by: [{ id: 'policy', superseded_by: 'new-policy' }],
    implementations: [{ unit: 'leasing', tbl: 'rent', col: 'amount', grain: 'month', currency: 'GBP', calendar: 'fiscal', conversion: 'none', readable: true }, { unit: 'retail', tbl: 'sales', col: 'amount', readable: false }],
    no_join: [{ pair: 'leasing/wealth', asserted_by: 'steward', asserted_on: 'today' }], certified_joins: [{ pair: 'leasing/retail', declared_join: 'used', note: 'certified', asserted_by: 'steward', asserted_on: 'today' }],
    standards: [{ label: 'Revenue', standard_name: 'Example', iri: 'urn:revenue' }],
  })) });
  const { container, rerender } = render(<BehindAnswer {...settings} answer={answer} />);
  await waitFor(() => expect(settings.onCurrency).toHaveBeenCalledWith(answer, { currency: 'GBP', definition: 'Certified takings', source: 'leasing.rent' }));
  const map = container.querySelector('#tab-map');
  for (const text of ['policy (superseded)', 'Active rentals', '(kept only with one)', 'latest', 'used', 'joined', 'required', '1 other certified joins', 'region', 'active only', 'retail, through the certified join', 'yes', 'no', 'No join exists between: leasing/wealth', 'Joined only through a certified join', time_mode === 'current' ? 'each row\'s version that holds now' : "each row's version valid on its month's freeze date"]) expect(map).toHaveTextContent(text);
  rerender(<BehindAnswer {...settings} answer={answer} source={1} />);
  expect(screen.getByRole('tab', { name: 'Sources' })).toHaveAttribute('aria-selected', 'true');
  rerender(<BehindAnswer {...settings} answer={{ ...base, reason: 'No term' }} />);
  rerender(<BehindAnswer {...settings} answer={answer} />);
  await waitFor(() => expect(container.querySelector('#tab-map')).toHaveTextContent('Certified takings'));
  expect(settings.api).toHaveBeenCalledExactlyOnceWith('/map/Revenue');
});

test('the Map lists all five API no-join facts and the certified Retail-Wealth join', async () => {
  const pairs = ['retail-sme', 'retail-corporate', 'sme-corporate', 'sme-wealth', 'corporate-wealth'];
  const settings = props({ api: vi.fn(async () => ({
    no_join: pairs.map(pair => ({ pair, asserted_by: 'Data Architecture Board', asserted_on: '2026-10-10' })),
    certified_joins: [{ pair: 'retail-wealth', declared_join: 'client_xref', note: 'certified client cross-reference', asserted_by: 'Data Architecture Board', asserted_on: '2026-10-10' }],
  })) });
  const { container } = render(<BehindAnswer {...settings} answer={{ ...base, context: { term: 'Revenue' } }} />);
  const map = container.querySelector('#tab-map');
  await waitFor(() => expect(map).toHaveTextContent(`No join exists between: ${pairs.join(', ')}`));
  expect(map).toHaveTextContent('Joined only through a certified join: retail-wealth, by client_xref (certified client cross-reference; asserted Data Architecture Board, 2026-10-10)');
  expect(settings.api).toHaveBeenCalledExactlyOnceWith('/map/Revenue');
});

test.each([
  [{ error: 'Not permitted: map unavailable' }, 'Not permitted: map unavailable'],
  [{ contexts: [{ ...context, currency: undefined }], implementations: [{ unit: 'leasing', tbl: 'rent', col: 'amount', currency: 'USD' }] }, 'This answer did not run (Clarify): it made no join and reached no other unit.'],
  [{ contexts: [] }, "no document; the pack's glossary defines it"],
])('Map handles refusal, an unrun context and glossary-only definitions: %j', async (map, text) => {
  const answer = { ...base, behaviour: map.contexts?.length ? 'clarify' : 'glossary_definition', unit: 'leasing', column: 'rent.amount', context: { term: 'Revenue' } };
  const settings = props({ api: vi.fn(async () => map) });
  const { container } = render(<BehindAnswer {...settings} answer={answer} />);
  select('Map');
  await waitFor(() => expect(container.querySelector('#tab-map')).toHaveTextContent(text));
  if (map.contexts?.length) expect(settings.onCurrency).toHaveBeenCalledWith(answer, expect.objectContaining({ currency: 'USD' }));
});

test('a late map cannot overwrite the next selected answer', async () => {
  let finish;
  const answer = { ...base, context: { term: 'Revenue' } };
  const settings = props({ api: vi.fn(() => new Promise(resolve => { finish = resolve; })) });
  const { container, rerender } = render(<BehindAnswer {...settings} answer={answer} />);
  expect(container.querySelector('#tab-map')).toHaveTextContent('Loading the resolved map…');
  rerender(<BehindAnswer {...settings} answer={{ ...base, behaviour: 'blocked_access', reason: 'Refused' }} />);
  await act(async () => finish({ contexts: [context] }));
  expect(container.querySelector('#tab-map')).toHaveTextContent('No map for a refused answer');
  expect(container.querySelector('#tab-map')).not.toHaveTextContent('Certified takings');
  expect(settings.onCurrency).not.toHaveBeenCalled();
});

test.each([
  [{ sql: 'SELECT 1 FROM leasing', unit: 'leasing', sql_source: 'rules', guard: { passed: true, checks: ['Allowlist checked'] } }, ['SQL by rules', 'gate passed', 'Allowlist checked', 'SELECT 1']],
  [{ plan: [{ unit: 'leasing', status: 'refused', reason: 'No certified join', notes: ['Not executed'] }], guard: { passed: false, problems: ['No join'] } }, ['gate failed', 'No join', 'Not executed', 'No certified join']],
  [{ parts: [{ unit: 'leasing', status: 'ok', sql: 'SELECT 0' }] }, ['SELECT 0']],
  [{ figures: [{ unit: 'leasing', sql: 'SELECT 1' }, { unit: 'retail', sql: 'SELECT 2' }, { unit: 'wealth' }], attempts: [{ unit: 'leasing', passed: true }] }, ['gate passed', 'gate failed', 'SELECT 2']],
  [{}, ['No warehouse query was run.']],
])('Plan exposes checked SQL or the reason none ran: %j', (answer, texts) => {
  const { container } = render(<BehindAnswer {...props()} answer={{ ...base, ...answer }} />);
  select('Plan');
  for (const text of texts) expect(container.querySelector('#tab-plan')).toHaveTextContent(text);
});

test.each([
  [{}, ['No timed stages were recorded for this answer.']],
  [{ stages: [{ stage: 'parse', ms: 0 }, { stage: 'gate' }] }, ['No model was called for this answer.', "the parse stage's result", '0 ms']],
  [{ stages: [{ stage: 'parse', llm_calls: 1, tokens_in: 10, tokens_out: 5, ms: 1 }, { stage: 'gate', ms: 2 }] }, ['1 model calls; 10 tokens in, 5 out', '0 model calls; 0 tokens in, 0 out']],
])('Trace describes actual calls and timings: %j', (run_trace, texts) => {
  const { container } = render(<BehindAnswer {...props()} answer={{ ...base, run_trace, trace: ['checked'], versions: { release: 'r' }, retrieval: { count: 0 } }} />);
  select('Trace');
  for (const text of texts) expect(container.querySelector('#tab-trace')).toHaveTextContent(text);
  expect(within(container.querySelector('#tab-trace')).getByText('Raw JSON')).toBeVisible();
  expect(container.querySelector('#tab-trace pre')).toHaveTextContent('"release": "r"');
});

test('Behind the answer shows the complete run trace as text', () => {
  const run_trace = { id: 'run-1', profile: 'meridian', graph: { artifact: 'served-hash' },
    config: { arm: 'graph' }, stages: [{ stage: 'gate', ms: 2 }], retrieved: [{ doc: '<img src=x onerror=alert(1)>' }] };
  const { container } = render(<BehindAnswer {...props()} answer={{ ...base, run_trace }} />);
  select('Trace');
  const trace = screen.getByRole('tabpanel', { name: 'Trace' });
  expect(within(trace).getByRole('table')).toHaveTextContent('gate');
  expect(within(trace).getByRole('table')).toHaveTextContent('2 ms');
  fireEvent.click(within(trace).getByText('Raw JSON', { selector: 'summary' }));
  const raw = trace.querySelector('pre');
  expect(raw).toBeVisible();
  expect(JSON.parse(raw.textContent).run_trace).toEqual(run_trace);
  expect(container.querySelector('img')).toBeNull();
});

const node = (type, id, group, extra = {}) => ({ id: `${type}:${id}`, type, name: id, label: id, group, used: true, state: 'done', certified: false, badges: [], detail: `${type} ${id}`, ...extra });
const edge = (from, to, type, extra = {}) => ({ from, to, type, label: type, planner: false, used: true, state: 'done', ...extra });
const w01 = () => structuredClone(realW01);
const graphTab = container => container.querySelector('#tab-graph');
const used = xs => xs.filter(x => x.used !== false);
const firstOf = type => realW01.nodes.find(n => n.type === type && n.used !== false).id;
const nodeEl = (tab, id) => tab.querySelector(`[data-node-id="${CSS.escape(id)}"]`);
const labelEl = (tab, e) => tab.querySelector(`[data-edge-label="${CSS.escape(`${e.from}->${e.to}`)}"]`);

test('the real W01 graph path: 21 nodes used, 3 read but not used, 3 joins, 3 SCD2 tables', () => {
  expect(used(realW01.nodes)).toHaveLength(21);
  expect(realW01.unused).toBe(3);
  expect(used(realW01.nodes).some(n => n.id === 'Entity:wealth_branch_division')).toBe(true);
  expect(realW01.edges.some(e => e.from === 'Term:division' && e.to === 'Entity:wealth_branch_division' && e.planner)).toBe(true);
  expect(used(realW01.edges).filter(e => e.type === 'JoinRule')).toHaveLength(3);
  expect(used(realW01.nodes).filter(n => (n.badges || []).some(b => b.kind === 'SCD2'))).toHaveLength(3);
});

test('Graph tab draws the W01 context-graph path by group, with the Context selected', () => {
  const { container } = render(<BehindAnswer {...props()} answer={{ ...base, path: { nodes: [{ id: 'query', kind: 'query', label: 'spine', state: 'done' }], edges: [] }, graph_path: w01() }} />);
  select('Graph');
  const tab = graphTab(container);
  expect(tab).toHaveTextContent('The path this answer read: 21 nodes');
  expect(tab.querySelectorAll('[data-node-id]')).toHaveLength(21);
  expect(tab.querySelector('[data-node-id="query"]')).toBeNull();
  expect(tab.querySelectorAll('[data-used="false"]')).toHaveLength(0);
  expect(tab.querySelectorAll('.gedge[data-type="JoinRule"]')).toHaveLength(3);
  expect(tab.querySelectorAll('.graph-stage .gnode-scd2')).toHaveLength(3);
  expect(tab.querySelectorAll('.gedge')).toHaveLength(used(realW01.edges).length);
  const x = id => parseFloat(nodeEl(tab, id).style.left);
  const y = id => parseFloat(nodeEl(tab, id).style.top);
  const order = ['Words', 'Acronym', 'Context', 'Filter', 'Table'].map(type => x(firstOf(type)));
  expect(order).toEqual([...order].sort((p, q) => p - q));
  expect(new Set(order).size).toBe(5);
  expect(x(firstOf('Principal'))).toBe(x(firstOf('Acronym')));
  expect(x(firstOf('Unit'))).toBe(x(firstOf('Context')));
  expect(x(firstOf('Runtime'))).toBe(x(firstOf('Table')));
  const lowestTable = Math.max(...used(realW01.nodes).filter(n => n.type === 'Table').map(n => y(n.id)));
  for (const type of ['Principal', 'Unit', 'Runtime']) expect(y(firstOf(type))).toBeGreaterThan(lowestTable);
  for (const e of used(realW01.edges).filter(e => e.type === 'JoinRule')) expect(labelEl(tab, e)).toHaveTextContent(e.label);
  const planner = used(realW01.edges).find(e => e.planner);
  expect(tab.querySelector(`[data-edge="${CSS.escape(`${planner.from}->${planner.to}`)}"]`)).toHaveAttribute('data-planner', 'true');
  expect(tab.querySelector('[role="region"]')).toHaveAttribute('aria-label', expect.stringContaining('Context graph path'));
  expect(tab.querySelector('.graph-legend')).toHaveTextContent('SCD2versioned table');
  const context = realW01.nodes.find(n => n.type === 'Context');
  expect(nodeEl(tab, context.id)).toHaveAttribute('aria-pressed', 'true');
  expect(tab.querySelector('#path-detail')).toHaveTextContent(`Selected: Context ${context.name}`);
  expect(tab.querySelector('#path-detail pre').textContent).toBe(context.detail);
  expect(tab.querySelector('.graph-note')).toHaveTextContent("Solid edges are stored graph edges; dashed edges are the planner matching the question's words. Select a node, or an underlined edge label, to see its detail.");
});

test('Graph tab hides what was read but not used until asked, then marks it with a dashed card and "Not used"', () => {
  const { container } = render(<BehindAnswer {...props()} answer={{ ...base, graph_path: w01() }} />);
  select('Graph');
  const tab = graphTab(container);
  const toggle = within(tab).getByRole('checkbox', { name: 'Also show what was read but not used (3)' });
  expect(toggle).not.toBeChecked();
  fireEvent.click(toggle);
  const unusedNodes = realW01.nodes.filter(n => n.used === false);
  expect(tab.querySelectorAll('[data-node-id][data-used="false"]')).toHaveLength(unusedNodes.length);
  const unused = nodeEl(tab, unusedNodes[0].id);
  expect(unused).toHaveClass('unused');
  expect(unused.querySelector('.gnode-type')).toHaveTextContent(/^Not used · /);
  expect(tab.querySelectorAll('.gedge[data-used="false"]').length).toBeGreaterThan(0);
  expect(tab.querySelector('.graph-legend')).toHaveTextContent('read but not used');
  expect(tab).toHaveTextContent('The path this answer read: 21 nodes');
  fireEvent.click(unused);
  expect(tab.querySelector('#path-detail')).toHaveTextContent('read, not used');
  fireEvent.click(toggle);
  expect(nodeEl(tab, unusedNodes[0].id)).toBeNull();
  expect(tab.querySelector('#path-detail')).toHaveTextContent('Selected: Context');
});

test('Graph tab selects a node by pointer or keyboard and shows its detail as text', () => {
  const graph = w01();
  const runtime = graph.nodes.find(n => n.type === 'Runtime');
  runtime.detail = 'Runtime reference\n<img src=x onerror=alert(1)>';
  const { container } = render(<BehindAnswer {...props()} answer={{ ...base, graph_path: graph }} />);
  select('Graph');
  const tab = graphTab(container);
  const button = nodeEl(tab, runtime.id);
  expect(button.tagName).toBe('BUTTON');
  fireEvent.click(button);
  expect(button).toHaveAttribute('aria-pressed', 'true');
  expect(tab.querySelector('#path-detail')).toHaveTextContent(`Selected: Runtime ${runtime.name}`);
  expect(tab.querySelector('#path-detail pre').textContent).toBe('Runtime reference\n<img src=x onerror=alert(1)>');
  expect(container.querySelector('img')).toBeNull();
  const scd2 = used(graph.nodes).find(n => n.type === 'Table' && n.badges.length);
  const table = nodeEl(tab, scd2.id);
  table.focus();
  expect(document.activeElement).toBe(table);
  fireEvent.click(table);
  expect(tab.querySelector('#path-detail')).toHaveTextContent(`Selected: Table ${scd2.name}`);
  expect(tab.querySelector('#path-detail')).toHaveTextContent('SCD2');
});

test('an edge with a detail is selected through its label, by pointer or keyboard', () => {
  const { container } = render(<BehindAnswer {...props()} answer={{ ...base, graph_path: w01() }} />);
  select('Graph');
  const tab = graphTab(container);
  const join = used(realW01.edges).find(e => e.type === 'JoinRule' && e.detail);
  const label = labelEl(tab, join);
  expect(label).toHaveAttribute('role', 'button');
  expect(label).toHaveAttribute('tabindex', '0');
  fireEvent.click(label);
  expect(label).toHaveAttribute('aria-pressed', 'true');
  expect(tab.querySelector('#path-detail')).toHaveTextContent(`Selected: JoinRule ${join.label}`);
  expect(tab.querySelector('#path-detail pre').textContent).toBe(join.detail);
  expect(tab.querySelectorAll('[data-node-id][aria-pressed="true"]')).toHaveLength(0);
  const computed = used(realW01.edges).find(e => e.type === 'COMPUTED_FROM' && e.detail);
  fireEvent.keyDown(labelEl(tab, computed), { key: 'Enter' });
  expect(tab.querySelector('#path-detail pre').textContent).toBe(computed.detail);
  const plain = used(realW01.edges).find(e => !e.detail && e.type === 'RESOLVES_TO');
  expect(labelEl(tab, plain)).not.toHaveAttribute('role');
});

test('a counted label selects every edge it stands for', () => {
  const graph_path = {
    nodes: [node('Runtime', 'reference', 'runtime'), ...['a', 'b', 'c'].map(t => node('Table', `t_${t}`, 'tables'))],
    edges: ['a', 'b', 'c'].map(t => edge('Runtime:reference', `Table:t_${t}`, 'IMPLEMENTS', { label: 'HOLDS · IMPLEMENTS', detail: `RtTable: rt_${t}` })),
    unused: 0,
  };
  const { container } = render(<BehindAnswer {...props()} answer={{ ...base, graph_path }} />);
  select('Graph');
  const tab = graphTab(container);
  const label = tab.querySelector('.graph-labels [role="button"]');
  expect(label).toHaveTextContent('HOLDS · IMPLEMENTS ×3');
  fireEvent.click(label);
  expect(tab.querySelector('#path-detail')).toHaveTextContent('Selected: HOLDS · IMPLEMENTS ×3, 3 edges');
  for (const t of ['a', 'b', 'c']) expect(tab.querySelector('#path-detail pre')).toHaveTextContent(`Runtime reference → Table t_${t} RtTable: rt_${t}`);
});

test('while an answer streams with no graph yet, the Graph tab says the path is being built', () => {
  const path = { nodes: [{ id: 'question', kind: 'question', label: 'Q', state: 'running' }], edges: [] };
  const { container, rerender } = render(<BehindAnswer {...props()} answer={{ question: 'Revenue?', principal: me.id, arm: 'graph', path, streaming: true }} />);
  const building = graphTab(container).querySelector('.graph-building');
  expect(screen.getByRole('tab', { name: 'Graph' })).toHaveAttribute('aria-selected', 'true');
  expect(building).toHaveTextContent('Building the graph path…');
  expect(building).toHaveAttribute('aria-busy', 'true');
  expect(graphTab(container)).not.toHaveTextContent('No graph path was recorded');
  select('Sources');
  rerender(<BehindAnswer {...props()} answer={{ question: 'Revenue?', principal: me.id, arm: 'graph', path: { ...path }, streaming: true }} />);
  expect(screen.getByRole('tab', { name: 'Sources' })).toHaveAttribute('aria-selected', 'true');
  rerender(<BehindAnswer {...props()} answer={{ ...base, path, graph_path: w01() }} />);
  expect(screen.getByRole('tab', { name: 'Sources' })).toHaveAttribute('aria-selected', 'true');
  select('Graph');
  rerender(<BehindAnswer {...props()} answer={{ ...base, path, graph_path: w01(), streaming: true }} />);
  rerender(<BehindAnswer {...props()} answer={{ ...base, path, graph_path: w01() }} />);
  expect(screen.getByRole('tab', { name: 'Graph' })).toHaveAttribute('aria-selected', 'true');
  expect(graphTab(container).querySelectorAll('[data-node-id]')).toHaveLength(21);
});

test('a refused path shows the refused node in red with its reason, and nothing after it', () => {
  const graph_path = {
    nodes: [node('Words', 'NNA', 'words'), node('Acronym', 'NNA', 'vocabulary'), node('Term', 'Net New Assets', 'vocabulary'),
      node('Context', 'nna', 'meaning'), node('Principal', 'reader@example.com', 'access'),
      node('Unit', 'wealth', 'access', { state: 'refused', reason: 'reader@example.com may not read wealth.', detail: 'Access check: refused' })],
    edges: [edge('Words:NNA', 'Acronym:NNA', 'matches', { planner: true }), edge('Acronym:NNA', 'Term:Net New Assets', 'ABBREVIATED_AS'),
      edge('Term:Net New Assets', 'Context:nna', 'RESOLVES_TO'), edge('Principal:reader@example.com', 'Unit:wealth', 'CAN_READ', { state: 'refused' })],
    unused: 0,
  };
  const { container } = render(<BehindAnswer {...props()} answer={{ ...base, behaviour: 'refuse', reason: 'reader@example.com may not read wealth.', graph_path }} />);
  select('Graph');
  const tab = graphTab(container);
  const unit = tab.querySelector('[data-node-id="Unit:wealth"]');
  expect(unit).toHaveAttribute('data-state', 'refused');
  expect(unit).toHaveClass('refused');
  expect(unit).toHaveTextContent('reader@example.com may not read wealth.');
  expect(tab.querySelector('.gedge.refused')).toHaveAttribute('data-edge', 'Principal:reader@example.com->Unit:wealth');
  expect(tab.querySelectorAll('[data-node-id]')).toHaveLength(6);
  expect(tab.querySelector('[data-type="Table"], [data-type="Runtime"]')).toBeNull();
  expect(within(tab).getByRole('checkbox')).toBeDisabled();
  expect(unit).toHaveAttribute('aria-pressed', 'true');
  expect(tab.querySelector('[data-node-id="Context:nna"]')).toHaveAttribute('aria-pressed', 'false');
  expect(tab.querySelector('#path-detail')).toHaveTextContent('Selected: Unit wealth');
  expect(tab.querySelector('#path-detail')).toHaveTextContent('refused');
  expect(tab.querySelector('#path-detail .path-reason')).toHaveTextContent('reader@example.com may not read wealth.');
});

test.each([false, true])('a model plan refusal shows lexical matches and its red check edge, with a rules replacement: %s', replacement => {
  const reason = 'The model plan omitted a named filter. <img src=x onerror=alert(1)>';
  const check = node('Check', 'model/plan', 'rules', { state: 'refused', reason, detail: `Plan check: abstain\n${reason}` });
  const refusal = edge('Plan:model', check.id, 'CHECKS', { state: 'refused', planner: true, reason });
  const matches = [edge('Words:certified NNA', 'Context:nna_certified', 'matches', { planner: true }),
    edge('Words:certified NNA', 'Measure:wealth.certified-nna', 'matches', { planner: true }),
    edge('Words:March 2025', 'Calendar:calendar', 'matches', { planner: true })];
  const graph_path = {
    nodes: [node('Words', 'certified NNA', 'words'), node('Words', 'March 2025', 'words'),
      node('Context', 'nna_certified', 'meaning'), node('Measure', 'wealth.certified-nna', 'meaning'),
      node('Calendar', 'calendar', 'vocabulary'), node('Plan', 'model', 'meaning'), check,
      ...(replacement ? [node('Runtime', 'reference', 'runtime')] : [])],
    edges: [...matches, refusal], unused: 0,
  };
  const { container } = render(<BehindAnswer {...props()} answer={{ ...base, behaviour: replacement ? 'figure' : 'abstain', graph_path }} />);
  select('Graph');
  const tab = graphTab(container);
  expect(nodeEl(tab, check.id)).toHaveClass('refused');
  expect(nodeEl(tab, check.id)).toHaveTextContent(reason);
  expect(tab.querySelector('.gedge.refused')).toHaveAttribute('data-edge', 'Plan:model->Check:model/plan');
  for (const match of matches) expect(tab.querySelector(`[data-edge="${CSS.escape(`${match.from}->${match.to}`)}"]`)).toHaveAttribute('data-planner', 'true');
  expect(Boolean(tab.querySelector('[data-type="Runtime"]'))).toBe(replacement);
  expect(tab.querySelector('#path-detail .path-reason')).toHaveTextContent(reason);
  expect(tab.querySelector('img')).toBeNull();
  fireEvent.click(labelEl(tab, refusal));
  expect(tab.querySelector('#path-detail .badge.bad')).toHaveTextContent('refused');
  expect(tab.querySelector('#path-detail pre').textContent).toBe(reason);
  expect(tab.querySelector('img')).toBeNull();
  fireEvent.keyDown(labelEl(tab, refusal), { key: 'Enter' });
  expect(tab.querySelector('#path-detail pre').textContent).toBe(reason);
});

test('above 100 nodes, the largest type folds into one counted group that expands on click', () => {
  const graph_path = {
    nodes: [node('Context', 'big', 'meaning'), ...Array.from({ length: 120 }, (_, i) => node('Table', `t${i}`, 'tables'))],
    edges: Array.from({ length: 120 }, (_, i) => edge('Context:big', `Table:t${i}`, 'USES_FACT')),
    unused: 0,
  };
  const { container } = render(<BehindAnswer {...props()} answer={{ ...base, graph_path }} />);
  select('Graph');
  const tab = graphTab(container);
  expect(tab).toHaveTextContent('The path this answer read: 121 nodes');
  expect(tab.querySelectorAll('[data-node-id]')).toHaveLength(2);
  const group = tab.querySelector('[data-node-id="group:Table"]');
  expect(group).toHaveTextContent('Table ×120');
  expect(group).toHaveAttribute('aria-expanded', 'false');
  expect(tab.querySelectorAll('.gedge')).toHaveLength(1);
  fireEvent.click(group);
  expect(tab.querySelectorAll('[data-node-id]')).toHaveLength(121);
  fireEvent.click(within(tab).getByRole('button', { name: 'Group the Table nodes again' }));
  expect(tab.querySelectorAll('[data-node-id]')).toHaveLength(2);
});

test('a folded group carries its members\' refused and certified state, and an edge into it names the group', () => {
  const tables = Array.from({ length: 120 }, (_, i) => node('Table', `t${i}`, 'tables', i === 7 ? { state: 'refused', reason: 'not readable' } : i < 2 ? { certified: true } : {}));
  const graph_path = {
    nodes: [node('Context', 'big', 'meaning'), ...tables],
    edges: tables.map(t => edge('Context:big', t.id, 'USES_FACT', { detail: `Fact ${t.name}` })),
    unused: 0,
  };
  const { container } = render(<BehindAnswer {...props()} answer={{ ...base, graph_path }} />);
  select('Graph');
  const tab = graphTab(container);
  const group = tab.querySelector('[data-node-id="group:Table"]');
  expect(group).toHaveTextContent('Table ×120 · 1 refused · 2 certified');
  expect(group).toHaveAttribute('data-state', 'refused');
  expect(group).toHaveClass('refused');
  expect(group).toHaveClass('certified');
  expect(tab.querySelector('#path-detail')).toHaveTextContent('Selected: Context big');
  fireEvent.click(tab.querySelector('.graph-labels [role="button"]'));
  const detail = tab.querySelector('#path-detail');
  expect(detail).toHaveTextContent('Context big → Table ×120 · 1 refused · 2 certified (grouped)');
  expect(detail).not.toHaveTextContent('group:Table');
});

test('the unused count is counted from the graph only when the answer does not carry it', () => {
  const graph_path = { nodes: [node('Context', 'c', 'meaning'), node('Filter', 'f', 'rules', { used: false })], edges: [edge('Context:c', 'Filter:f', 'REQUIRES_FILTER', { used: false })] };
  const { container, rerender } = render(<BehindAnswer {...props()} answer={{ ...base, graph_path }} />);
  select('Graph');
  expect(within(graphTab(container)).getByRole('checkbox')).toHaveAccessibleName('Also show what was read but not used (2)');
  rerender(<BehindAnswer {...props()} answer={{ ...base, graph_path: { ...graph_path, unused: 1 } }} />);
  select('Graph');
  expect(within(graphTab(container)).getByRole('checkbox')).toHaveAccessibleName('Also show what was read but not used (1)');
});

test.each([[{}], [{ graph_path: { nodes: [], edges: [], unused: 0 } }]])('Graph tab says so when no graph path was recorded, never drawing the spine: %j', extra => {
  const path = { nodes: [{ id: 'question', kind: 'question', label: 'Q', state: 'done' }], edges: [] };
  const { container } = render(<BehindAnswer {...props()} answer={{ ...base, path, ...extra }} />);
  select('Graph');
  expect(graphTab(container)).toHaveTextContent('No graph path was recorded for this answer.');
  expect(graphTab(container).querySelector('[data-node-id]')).toBeNull();
});

test('a graph path the service could not build says so', () => {
  const { container } = render(<GraphPath answer={{ question: 'q', graph_path: { nodes: [], edges: [], unused: 0, error: 'The graph path could not be built for this answer.' } }} />);
  expect(container).toHaveTextContent('The graph path could not be built for this answer.');
  expect(container).not.toHaveTextContent('No graph path was recorded');
});


test('runtime detail renders the recorded SQL with the existing copy control', async () => {
  const graph = w01();
  const runtime = graph.nodes.find(n => n.type === 'Runtime');
  runtime.sql = 'SELECT safe_amount FROM readable_table';
  const writeText = vi.fn(async () => {});
  const descriptor = Object.getOwnPropertyDescriptor(navigator, 'clipboard');
  Object.defineProperty(navigator, 'clipboard', { configurable: true, value: { writeText } });
  try {
    const { container } = render(<BehindAnswer {...props()} answer={{ ...base, graph_path: graph }} />);
    select('Graph');
    const tab = graphTab(container);
    fireEvent.click(nodeEl(tab, runtime.id));
    expect(tab.querySelector('#path-detail .codeblock pre')).toHaveTextContent(runtime.sql);
    fireEvent.click(within(tab.querySelector('#path-detail')).getByRole('button', { name: 'Copy the query' }));
    await waitFor(() => expect(writeText).toHaveBeenCalledWith(runtime.sql));
  } finally {
    if (descriptor) Object.defineProperty(navigator, 'clipboard', descriptor);
    else delete navigator.clipboard;
  }
});

test.each([false, true])('runtime certification reflects the recorded pack value: %s', certified => {
  const graph = w01();
  const runtime = graph.nodes.find(n => n.type === 'Runtime');
  runtime.certified = certified;
  const { container } = render(<BehindAnswer {...props()} answer={{ ...base, graph_path: graph }} />);
  select('Graph');
  const tab = graphTab(container);
  fireEvent.click(nodeEl(tab, runtime.id));
  expect(Boolean(tab.querySelector('#path-detail .badge.ok'))).toBe(certified);
});
