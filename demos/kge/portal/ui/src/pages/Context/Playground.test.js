import React from 'react';
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, expect, test, vi } from 'vitest';
import Playground from './Playground';
import AgentCall from './AgentCall';
import { agentCall, askEquivalent, callTool, graphSliceOf, loadTools, pythonLiteral, replyText, sourcesOf, timeWords } from './playgroundState';

// #623 with stubs only: the portal's Playground route is a fake fetcher that answers as the route does, so the page is
// tested against the contract the server's own tests hold (server/test/playground.test.js).
const presets = { default: 'graph', presets: { graph: { arm: 'graph', paths: ['ask'] } }, agent_arms: {} };
const golden = { sets: [{ name: 'golden', version: 'v1', questions: ['What was NNA in March?'] }] };
const business = { id: 'biz@example.com', reads: ['wealth'], cerebro_roles: ['business'] };
const engineer = { id: 'eng@example.com', reads: ['wealth'], cerebro_roles: ['engineer'] };
const EVERY = [['ask', 'Ask', 'ask', '/mcp/answer'], ['context', 'Context', 'get_context', '/mcp'], ['map', 'Map', 'map_term', '/mcp'], ['search', 'Search', 'search', '/mcp/search'], ['graph', 'Graph', 'explore_question', '/mcp/graph']];
const ENGINEER = [...EVERY, ['retrieve', 'Retrieve', 'retrieve', '/mcp'], ['assist', 'Assist', 'assist', '/mcp/assist']];
const BASE = 'https://kge.example.test';
const VALUES = {
  ask: { question: 'q', behaviour: 'figure', answer_mode: 'certified', answer_text: 'Certified: NNA was 42.', figure: 42, currency: 'USD', sql: 'SELECT SUM(nna) FROM wealth.nna', sql_certified: true, plan: [{ sql: 'SELECT 1' }], run_trace: { stages: [{ stage: 'plan', ms: 5 }] }, citations: [{ n: '1', doc: 'EM-001', title: 'Net new assets', cited_as: 'authority', tier: 'gold' }] },
  get_context: { block: 'Net new assets: money in less money out.', sources: [{ title: 'Glossary' }], defines: { 'Net new assets': {} } },
  map_term: { term: 'NNA', definition: 'Net new assets.', steward: 'Wealth steward', data_sets: [{ unit: 'wealth', unit_label: 'Wealth' }], defined_by: [{ title: 'Glossary' }], contexts: [{ label: 'NNA by month' }] },
  search: { hits: [{ kind: 'term', unit: 'wealth', id: 'term:nna', title: 'Net new assets', score: 0.9, why: 'title' }] },
  explore_question: { summary: 'NNA is read from the wealth flows fact.', node: { kind: 'term', id: 'term:nna', name: 'NNA' }, link: '/graph', paths: [{ term: 'NNA', context: 'NNA by month', fact: 'Flows', measure: { name: 'NNA', aggregation: 'sum', grain: 'month' } }] },
  retrieve: { exact: [{ doc: 'EM-001', title: 'Net new assets', score: 0.8 }], index_returned: 1, post_filter_lost: 0 },
  assist: { answer_text: 'NNA is in the wealth flows.', sources: [{ kind: 'product', id: 'p', title: 'Wealth flows', unit: 'wealth' }], source: 'catalogue', behaviour: 'catalogue' },
};
const ENGINEER_ONLY = ['plan', 'trace', 'run_trace'];

function fakePortal(person, { delays = {}, fail = {} } = {}) {
  const list = person.cerebro_roles.includes('engineer') ? ENGINEER : EVERY;
  const sent = [];
  const fetcher = vi.fn(async (url, init = {}) => {
    const reply = (status, body) => ({ ok: status < 400, status, json: async () => body });
    if (url === '/api/context/playground/tools') return reply(200, { base: BASE, settings: list.map(([setting, label, tool, mount]) => ({ setting, label, tool, mount })) });
    if (url === '/api/context/playground/call') {
      const { setting, question } = JSON.parse(init.body);
      const [, , tool, mount] = list.find(([name]) => name === setting);
      const args = { ask: { question, display: true }, get_context: { question, compact: true }, map_term: { term: question }, search: { query: question }, explore_question: { question }, retrieve: { query: question }, assist: { question } }[tool];
      const body = JSON.stringify({ jsonrpc: '2.0', id: sent.length + 1, method: 'tools/call', params: { name: tool, arguments: args } });
      sent.push({ setting, tool, body });
      if (delays[setting]) await new Promise(resolve => setTimeout(resolve, delays[setting]));
      let value = VALUES[tool];
      if (!person.cerebro_roles.includes('engineer')) value = Object.fromEntries(Object.entries(value).filter(([k]) => !ENGINEER_ONLY.includes(k)));
      const request = { method: 'POST', url: `${BASE}${mount}`, headers: { 'content-type': 'application/json', accept: 'application/json, text/event-stream', authorization: 'Bearer <access_token>' }, body };
      const raw = JSON.stringify({ jsonrpc: '2.0', id: sent.length, result: { structuredContent: value, isError: false } });
      if (fail[setting]) return reply(200, { setting, tool, request, reply: '', error: fail[setting], ms: 9 });
      return reply(200, { setting, tool, request, reply: raw, value, ms: { ask: 1500, assist: 900 }[tool] || 120 });
    }
    return reply(404, { detail: 'Not found.' });
  });
  return { fetcher, sent };
}
const api = vi.fn(async () => ({}));
function open(person, options) {
  const portal = fakePortal(person, options);
  const view = render(<Playground api={api} me={person} presets={presets} golden={golden} fetcher={portal.fetcher} />);
  return { ...portal, ...view };
}
const choose = async (label) => { const radio = await screen.findByRole('radio', { name: label }); fireEvent.click(radio); return radio; };
const send = (text) => {
  fireEvent.change(screen.getByLabelText('Your question'), { target: { value: text } });
  fireEvent.click(document.querySelector('#pg-send'));
};
const tabsOf = () => within(screen.getByRole('tablist', { name: 'Behind the reply' })).getAllByRole('tab').map(t => t.textContent);
afterEach(() => { vi.restoreAllMocks(); window.localStorage?.clear(); });

test('the switch holds Ask, Context, Map, Search and Graph and Try all for a business user; an engineer adds Retrieve and Assist', async () => {
  open(business);
  await screen.findByRole('radio', { name: 'Graph' });
  expect(within(screen.getByRole('radiogroup', { name: 'Tool' })).getAllByRole('radio').map(r => r.textContent)).toEqual(['Ask', 'Context', 'Map', 'Search', 'Graph', 'Try all']);
  expect(screen.getByRole('radio', { name: 'Ask' })).toHaveAttribute('aria-checked', 'true');
  expect(document.querySelector('#assistant')).not.toBeNull();
  document.body.innerHTML = '';
  open(engineer);
  await screen.findByRole('radio', { name: 'Assist' });
  expect(within(screen.getByRole('radiogroup', { name: 'Tool' })).getAllByRole('radio').map(r => r.textContent)).toEqual(['Ask', 'Context', 'Map', 'Search', 'Graph', 'Retrieve', 'Assist', 'Try all']);
});

test('the switch moves by the arrow keys, with one keyboard stop', async () => {
  open(business);
  const ask = await screen.findByRole('radio', { name: 'Ask' });
  await screen.findByRole('radio', { name: 'Graph' });
  ask.focus();
  fireEvent.keyDown(ask, { key: 'ArrowRight' });
  expect(screen.getByRole('radio', { name: 'Context' })).toHaveAttribute('aria-checked', 'true');
  expect(document.activeElement).toHaveTextContent('Context');
  fireEvent.keyDown(document.activeElement, { key: 'End' });
  expect(screen.getByRole('radio', { name: 'Try all' })).toHaveAttribute('aria-checked', 'true');
  fireEvent.keyDown(document.activeElement, { key: 'ArrowRight' });
  expect(screen.getByRole('radio', { name: 'Ask' })).toHaveAttribute('aria-checked', 'true');
  fireEvent.keyDown(document.activeElement, { key: 'x' });
  expect(within(screen.getByRole('radiogroup', { name: 'Tool' })).getAllByRole('radio').filter(r => r.tabIndex === 0)).toHaveLength(1);
});

test.each([['Retrieve', 'retrieve', '1 passage'], ['Assist', 'assist', 'in the wealth flows']])('an engineer\'s %s runs its tool from the switch and renders its reply', async (label, tool, said) => {
  const { sent } = open(engineer);
  await choose(label);
  send('Net new assets');
  const panel = await screen.findByRole('region', { name: `Behind “Net new assets” · ${label}` });
  expect(sent.map(s => s.tool)).toEqual([tool]);
  expect(within(panel).getByRole('tabpanel', { name: 'Reply' })).toHaveTextContent(said);
});

test('Try all sends every call before any reply lands', async () => {
  const { sent } = open(business, { delays: { ask: 40, context: 40, map: 40, search: 40, graph: 40 } });
  await choose('Try all');
  send('NNA');
  await waitFor(() => expect(sent).toHaveLength(5));
  expect(document.querySelectorAll('.pg-column[aria-disabled="true"]')).toHaveLength(5);
  await waitFor(() => expect(document.querySelectorAll('.pg-column:not([aria-disabled])')).toHaveLength(5));
});

test('moving through the switch keeps today\'s Ask and every tool\'s runs', async () => {
  const ask = vi.fn(async (_path, body) => ({ question: body.question, principal: business.id, arm: 'graph', behaviour: 'figure', figure: 1, currency: 'USD', history: [] }));
  const portal = fakePortal(business);
  render(<Playground api={ask} me={business} presets={presets} golden={golden} fetcher={portal.fetcher} />);
  await screen.findByRole('radio', { name: 'Graph' });
  fireEvent.change(screen.getByLabelText('Question'), { target: { value: 'NNA for March?' } });
  fireEvent.click(screen.getByRole('button', { name: 'Ask', exact: true }));
  await screen.findByLabelText('Answer');
  await choose('Search');
  expect(document.querySelector('.pg-ask')).not.toBeVisible();
  send('branch');
  await screen.findByRole('region', { name: 'Behind “branch” · Search' });
  await choose('Try all');
  expect(document.querySelector('[data-setting="search"]')).toBeInTheDocument();
  await choose('Ask');
  expect(document.querySelector('.pg-ask')).toBeVisible();
  expect(screen.getByLabelText('Answer')).toHaveTextContent('1');
  expect(document.querySelector('#thread')).toHaveTextContent('NNA for March?');
  await choose('Context');
  expect(document.querySelector('.pg-run')).toHaveTextContent('branch');
  fireEvent.click(document.querySelector('[data-setting="search"]'));
  const ids = [...document.querySelectorAll('[id]')].map(el => el.id);
  expect(ids.filter((id, i) => ids.indexOf(id) !== i)).toEqual([]);
  for (const tab of within(screen.getByRole('tablist', { name: 'Behind the reply' })).getAllByRole('tab')) {
    expect(document.getElementById(tab.getAttribute('aria-controls'))).toHaveAttribute('aria-labelledby', tab.id);
  }
  expect(ask).toHaveBeenCalledTimes(1);
});

test('a restored turn\'s equivalent says its history is not kept', () => {
  render(<AgentCall request={{ method: 'POST', url: '/mcp/answer', headers: { accept: 'application/json' }, body: '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"ask","arguments":{"question":"q"}}}' }} base={BASE} equivalent historyUnknown />);
  expect(screen.getByText(/follow-up history it was asked with is not kept/)).toBeInTheDocument();
  document.body.innerHTML = '';
  render(<AgentCall />);
  expect(screen.getByText('No request was made.')).toBeInTheDocument();
});

test.each([['Context', 'get_context', 'money in less money out'], ['Map', 'map_term', 'Steward: Wealth steward'], ['Search', 'search', '1 hit'], ['Graph', 'explore_question', 'wealth flows fact']])('%s runs its tool from the switch and renders its reply', async (label, tool, said) => {
  const { sent } = open(business);
  await choose(label);
  send('Net new assets');
  const panel = await screen.findByRole('region', { name: /Behind “Net new assets” · / });
  expect(sent.map(s => s.tool)).toEqual([tool]);
  expect(within(panel).getByRole('tabpanel', { name: 'Reply' })).toHaveTextContent(said);
  expect(within(panel).getByText('Time')).toBeInTheDocument();
  expect(document.querySelector('.pg-column')).toHaveTextContent(said);
});

test('Try all sends one question to every tool a business user may use, side by side, each with its time', async () => {
  const { sent } = open(business, { delays: { graph: 30 } });
  await choose('Try all');
  send('NNA');
  await waitFor(() => expect(document.querySelectorAll('.pg-column:not([aria-disabled])')).toHaveLength(5));
  expect(sent.map(s => s.tool).sort()).toEqual(['ask', 'explore_question', 'get_context', 'map_term', 'search']);
  const columns = [...document.querySelectorAll('.pg-columns.all .pg-column')];
  expect(columns.map(c => c.dataset.setting)).toEqual(['ask', 'context', 'map', 'search', 'graph']);
  expect(columns.map(c => c.querySelector('.pg-time').textContent)).toEqual(['1.5 s', '120 ms', '120 ms', '120 ms', '120 ms']);
  expect(screen.queryByRole('region', { name: /Behind “NNA”/ })).toBeNull();
  fireEvent.click(columns[3]);
  expect(screen.getByRole('region', { name: 'Behind “NNA” · Search' })).toBeInTheDocument();
  expect(columns[3]).toHaveAttribute('aria-pressed', 'true');
});

test('Try all for an engineer runs seven tools, retrieve and assist among them', async () => {
  const { sent } = open(engineer);
  await choose('Try all');
  send('NNA');
  await waitFor(() => expect(document.querySelectorAll('.pg-column:not([aria-disabled])')).toHaveLength(7));
  expect(sent.map(s => s.tool).sort()).toEqual(['ask', 'assist', 'explore_question', 'get_context', 'map_term', 'retrieve', 'search']);
});

test('a column still running shows it is running and cannot be picked; a failure shows its error and its time', async () => {
  open(business, { delays: { map: 60 }, fail: { search: 'The knowledge graph refused the search call (500).' } });
  await choose('Try all');
  send('NNA');
  await waitFor(() => expect(document.querySelector('[data-setting="search"]')).not.toHaveAttribute('aria-disabled'));
  expect(document.querySelector('[data-setting="map"]')).toHaveAttribute('aria-disabled', 'true');
  fireEvent.click(document.querySelector('[data-setting="map"]'));
  expect(screen.queryByRole('region', { name: /· Map/ })).toBeNull();
  expect(document.querySelector('[data-setting="map"]')).toHaveTextContent('running…');
  expect(document.querySelector('#pg-send')).toHaveTextContent('Running…');
  const failed = document.querySelector('[data-setting="search"]');
  expect(failed).toHaveClass('failed');
  expect(failed).toHaveTextContent('refused the search call');
  expect(failed.querySelector('.pg-time')).toHaveTextContent('9 ms');
  fireEvent.click(failed);
  expect(within(screen.getByRole('tabpanel', { name: 'Reply' })).getByRole('alert')).toHaveTextContent('refused the search call');
  await waitFor(() => expect(document.querySelector('[data-setting="map"]')).not.toHaveAttribute('aria-disabled'));
  expect(screen.getByRole('status')).toHaveTextContent('Map replied in 120 ms.');
});

test('the right panel shows for a business user with the reply, sources, SQL, graph and agent call, and no plan or trace', async () => {
  open(business);
  await choose('Try all');
  send('NNA');
  await waitFor(() => expect(document.querySelector('[data-setting="ask"]')).not.toHaveAttribute('aria-disabled'));
  fireEvent.click(document.querySelector('[data-setting="ask"]'));
  expect(tabsOf()).toEqual(['Reply', 'Sources', 'SQL', 'Graph', 'Agent call']);
  expect(screen.getByRole('tabpanel', { name: 'Reply' })).toHaveTextContent('42');
  fireEvent.click(screen.getByRole('tab', { name: 'SQL' }));
  expect(screen.getByRole('tabpanel', { name: 'SQL' })).toHaveTextContent('SELECT SUM(nna)');
  fireEvent.click(screen.getByRole('tab', { name: 'Sources' }));
  expect(screen.getByRole('tabpanel', { name: 'Sources' })).toHaveTextContent('Net new assets');
  expect(within(screen.getByRole('region', { name: 'Behind “NNA” · Ask' })).getByText('1.5 s')).toBeInTheDocument();
  fireEvent.click(document.querySelector('[data-setting="graph"]'));
  expect(tabsOf()).toEqual(['Reply', 'Sources', 'Graph', 'Agent call']);
  fireEvent.click(screen.getByRole('tab', { name: 'Graph' }));
  expect(screen.getByRole('tabpanel', { name: 'Graph' })).toHaveTextContent('Flows');
});

test('an engineer\'s right panel for Ask adds the plan and the trace', async () => {
  open(engineer);
  await choose('Try all');
  send('NNA');
  await waitFor(() => expect(document.querySelector('[data-setting="ask"]')).not.toHaveAttribute('aria-disabled'));
  fireEvent.click(document.querySelector('[data-setting="ask"]'));
  expect(tabsOf()).toEqual(['Reply', 'Sources', 'SQL', 'Graph', 'Plan', 'Trace', 'Agent call']);
});

test('As an agent would call it shows the MCP request exactly as sent and the raw reply, each copyable, with a curl and a Python example', async () => {
  const writeText = vi.fn(async () => {});
  Object.defineProperty(navigator, 'clipboard', { configurable: true, value: { writeText } });
  const { sent } = open(business);
  await choose('Search');
  send('branch');
  await screen.findByRole('region', { name: /Behind “branch”/ });
  fireEvent.click(screen.getByRole('tab', { name: 'Agent call' }));
  const panel = screen.getByRole('tabpanel', { name: 'Agent call' });
  expect(within(panel).getByRole('heading', { name: 'As an agent would call it' })).toBeInTheDocument();
  const request = within(panel).getByText('Request').nextElementSibling.querySelector('pre').textContent;
  expect(request.startsWith(`POST ${BASE}/mcp/search\n`)).toBe(true);
  expect(request).toContain('authorization: Bearer <access_token>');
  expect(request.slice(request.indexOf('\n\n') + 2)).toBe(sent[0].body);
  expect(within(panel).getByText('Raw reply').nextElementSibling.querySelector('pre').textContent).toMatch(/^\{"jsonrpc":"2.0","id":1,"result":/);
  fireEvent.click(within(panel).getByRole('button', { name: 'Copy the curl call' }));
  await waitFor(() => expect(writeText).toHaveBeenCalled());
  const curl = writeText.mock.calls.at(-1)[0];
  expect(curl).toContain(`'${BASE}/mcp/search'`);
  expect(curl).toContain(`-d '${sent[0].body}'`);
  expect(within(panel).getByText(/streamablehttp_client/)).toBeInTheDocument();
  expect(within(panel).getByText('Token').nextElementSibling).toHaveTextContent(`${BASE}/session/token`);
  for (const name of ['Copy the request', 'Copy the raw reply', 'Copy the token step', 'Copy the Python example']) expect(within(panel).getByRole('button', { name })).toBeInTheDocument();
});

test('the tools list failing leaves Ask alone in the switch, with the reason', async () => {
  const fetcher = vi.fn(async () => ({ ok: false, status: 403, json: async () => ({ detail: "The Playground's tools need a business, steward or engineer role; Ask works as before." }) }));
  render(<Playground api={api} me={business} presets={presets} golden={golden} fetcher={fetcher} />);
  expect(await screen.findByText(/tools need a business, steward or engineer role/)).toBeInTheDocument();
  expect(within(screen.getByRole('radiogroup', { name: 'Tool' })).getAllByRole('radio').map(r => r.textContent)).toEqual(['Ask']);
});

test('Context\'s Ask in the Playground keeps its own composer, and its panel shows the agent\'s equivalent MCP ask, labelled so', async () => {
  const ask = vi.fn(async (_path, body) => ({ question: body.question, principal: business.id, arm: 'graph', behaviour: 'figure', figure: 1, currency: 'USD', history: [{ turn_id: 't1' }] }));
  const portal = fakePortal(business);
  render(<Playground api={ask} me={business} presets={presets} golden={golden} fetcher={portal.fetcher} />);
  await screen.findByRole('radio', { name: 'Graph' });
  fireEvent.change(screen.getByLabelText('Question'), { target: { value: 'NNA for March?' } });
  fireEvent.click(screen.getByRole('button', { name: 'Ask', exact: true }));
  await screen.findByLabelText('Answer');
  expect(portal.sent).toEqual([]);
  fireEvent.click(screen.getByRole('tab', { name: 'Agent call' }));
  const panel = screen.getByRole('tabpanel', { name: 'Agent call' });
  expect(panel).toHaveTextContent("The agent's equivalent: this page asked over HTTP");
  expect(panel).toHaveTextContent(`POST ${BASE}/mcp/answer`);
  expect(panel).toHaveTextContent('"question": "NNA for March?"');
  expect(within(panel).queryByText('Raw reply')).toBeNull();
  fireEvent.change(screen.getByLabelText('Question'), { target: { value: 'And April?' } });
  fireEvent.click(screen.getByRole('button', { name: 'Ask', exact: true }));
  await waitFor(() => expect(ask).toHaveBeenCalledTimes(2));
  await waitFor(() => expect(screen.getAllByLabelText('Answer')).toHaveLength(2));
  fireEvent.click(screen.getByRole('tab', { name: 'Agent call' }));
  expect(screen.getByRole('tabpanel', { name: 'Agent call' })).toHaveTextContent('"turn_id": "t1"');
});

test('the helpers: time, reply text, sources and graph slice per tool, and the agent example', async () => {
  expect([timeWords(0), timeWords(12.4), timeWords(1500)]).toEqual(['', '12 ms', '1.5 s']);
  expect(replyText('ask', VALUES.ask)).toBe('NNA was 42.');
  expect(replyText('assist', VALUES.assist)).toBe('NNA is in the wealth flows.');
  expect(replyText('retrieve', VALUES.retrieve)).toBe('1 passage');
  expect(replyText('retrieve', { exact: [] })).toBe('No passages.');
  expect(replyText('search', { hits: [] })).toBe('No hits.');
  expect(replyText('other', { text: 'plain' })).toBe('plain');
  expect(replyText('ask', null)).toBe('');
  expect(sourcesOf('map_term', VALUES.map_term).map(s => s.title)).toEqual(['Glossary', 'Wealth']);
  expect(sourcesOf('assist', VALUES.assist)[0]).toEqual({ title: 'Wealth flows', kind: 'product', unit: 'wealth', why: '' });
  expect(sourcesOf('retrieve', VALUES.retrieve)[0].kind).toBe('passage');
  expect(sourcesOf('explore_question', VALUES.explore_question)).toEqual([]);
  expect(sourcesOf('search', null)).toEqual([]);
  expect(graphSliceOf('explore_question', VALUES.explore_question).map(n => n.name)).toEqual(['NNA', 'NNA', 'NNA by month', 'Flows', 'NNA']);
  expect(graphSliceOf('map_term', VALUES.map_term)).toEqual([{ kind: 'term', name: 'NNA' }, { kind: 'context', name: 'NNA by month' }]);
  expect(graphSliceOf('get_context', VALUES.get_context)).toEqual([{ kind: 'term', name: 'Net new assets' }]);
  expect(graphSliceOf('ask', { graph_path: { nodes: [{ type: 'Term', label: 'NNA' }] } })).toEqual([{ kind: 'Term', name: 'NNA' }]);
  expect(graphSliceOf('search', VALUES.search)).toEqual([]);
  expect(graphSliceOf('search', null)).toEqual([]);
  expect(pythonLiteral({ a: true, b: false, c: null, d: [1, 'x'] })).toBe('{"a": True, "b": False, "c": None, "d": [1, "x"]}');
  const call = agentCall(askEquivalent("It's NNA?", []), BASE);
  expect(call.url).toBe(`${BASE}/mcp/answer`);
  expect(call.curl).toContain(`'{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"ask","arguments":{"question":"It'\\''s NNA?","display":true}}}'`);
  expect(call.token).toContain(`'${BASE}/session/token'`);
  expect(call.python).toContain('session.call_tool("ask", {"question": "It\'s NNA?", "display": True})');
  expect(agentCall(askEquivalent('q'), '').url).toBe('<kge>/mcp/answer');
  expect(JSON.parse(askEquivalent('q', [{ turn_id: 't' }]).body).params.arguments.history).toEqual([{ turn_id: 't' }]);
});

test('callTool and loadTools report what the portal could not do', async () => {
  const refused = vi.fn(async () => ({ ok: false, status: 400, json: async () => ({ detail: 'Send a question.' }) }));
  expect(await callTool('search', '', { fetcher: refused })).toMatchObject({ setting: 'search', error: 'Send a question.' });
  const broken = vi.fn(async () => ({ ok: false, status: 502, json: async () => { throw new Error('not json'); } }));
  expect((await callTool('search', 'q', { fetcher: broken })).error).toBe('The search call could not be made (502).');
  expect(await loadTools(broken)).toEqual({ error: "The Playground's tools are unavailable right now.", settings: [] });
  const down = vi.fn(async () => { throw new Error('offline'); });
  expect((await callTool('search', 'q', { fetcher: down })).error).toMatch(/could not be reached/);
  const abort = new AbortController(); abort.abort();
  expect(await callTool('search', 'q', { fetcher: down, signal: abort.signal })).toEqual({ setting: 'search', cancelled: true });
  const odd = vi.fn(async () => ({ ok: true, status: 200, json: async () => ({ base: 7, settings: 'x' }) }));
  expect(await loadTools(odd)).toEqual({ base: '', settings: [] });
  await act(async () => {});
});

test("callTool sends a call KGE turned away as busy (429) again, the same request, and shows the call that answered", async () => {
  const shown = { setting: 'search', tool: 'search', request: { body: '{"id":1}' } };
  const replies = [
    { ...shown, reply: '{}', error: '429: Too many requests in flight for this person; wait for one to finish and try again.', ms: 3 },
    { ...shown, reply: '', error: 'The knowledge graph refused the search call (429).', ms: 4 },
    { ...shown, reply: '{"hits":[]}', value: { hits: [] }, ms: 9 },
  ];
  const fetcher = vi.fn(async () => ({ ok: true, status: 200, json: async () => replies.shift() }));
  const result = await callTool('search', 'q', { fetcher, waits: [0, 0, 0] });
  expect(fetcher).toHaveBeenCalledTimes(3);
  expect(new Set(fetcher.mock.calls.map(([, init]) => init.body))).toEqual(new Set([JSON.stringify({ setting: 'search', question: 'q' })]));
  expect(result).toMatchObject({ value: { hits: [] }, ms: 9 });
  expect(result.error).toBeUndefined();
});

test("callTool stops after its waits and shows KGE's busy reply; another error is never sent again; a stop during a wait cancels", async () => {
  const busy = vi.fn(async () => ({ ok: true, status: 200, json: async () => ({ setting: 'ask', tool: 'ask', request: {}, error: '429: Too many requests in flight for this person; wait for one to finish and try again.', ms: 2 }) }));
  expect((await callTool('ask', 'q', { fetcher: busy, waits: [0, 0] })).error).toMatch(/^429: Too many requests/);
  expect(busy).toHaveBeenCalledTimes(3);
  const failed = vi.fn(async () => ({ ok: true, status: 200, json: async () => ({ setting: 'ask', tool: 'ask', request: {}, error: '503: The request ran out of time.', ms: 2 }) }));
  await callTool('ask', 'q', { fetcher: failed, waits: [0, 0] });
  expect(failed).toHaveBeenCalledTimes(1);
  const abort = new AbortController();
  const stopped = vi.fn(async () => { setTimeout(() => abort.abort(), 0); return { ok: true, status: 200, json: async () => ({ setting: 'ask', tool: 'ask', request: {}, error: '429: busy', ms: 2 }) }; });
  expect(await callTool('ask', 'q', { fetcher: stopped, signal: abort.signal, waits: [60000] })).toEqual({ setting: 'ask', cancelled: true });
  expect(stopped).toHaveBeenCalledTimes(1);
});
