import React, { StrictMode } from 'react';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, expect, test, vi } from 'vitest';
import ContextScreens from './ContextScreens';
import { reportUrl, visibleTabs } from './contextState';
import { changeLines } from './Onboard';

const who = (processes = ['querying', 'building', 'drafting', 'improving']) => ({ id: 'engineer@example.com', role: 'engineer', reads: ['leasing'], processes });
const opening = (person = who()) => ({ person, mode: { released: true }, profile: { profile: 'meridian', units: ['leasing'] }, presets: { default: 'graph', presets: { graph: { arm: 'graph', paths: ['ask'] }, schema: { arm: 'schema' } }, agent_arms: { treatment: { arm: 'treatment', side: 'graph on', mode: 'mock' } } }, build: { version: 'test' } });
const reply = (body, status = 200) => ({ ok: status < 400, status, json: async () => body });
let fetch, onboard, answers, evalPolls;
beforeEach(() => {
  onboard = { writable: true, can: { improve: true }, units: [{ unit: 'leasing', label: 'Asset Finance', live: true, items: [] }] };
  answers = [];
  evalPolls = [
    reply({ state: 'running', id: 'eval-1', job: { id: 'eval-1', step: 'eval', state: 'running' } }),
    reply({ graph: { by_kind: { within: { passed: 2, of: 3 }, cross: { passed: 1, of: 1 }, shelf: { passed: 1, of: 2 }, abstain: { passed: 1, of: 1 } } } }),
  ];
  fetch = vi.fn(async (url, init) => {
    if (init?.method === 'POST') {
      if (url.includes('/ask') || url.includes('/answer') || url.includes('/agent')) return reply(answers.shift());
      if (url.endsWith('/eval')) return reply({ job: { id: 'eval-1', step: 'eval', state: 'running' } }, 202);
      return reply({ job: { id: 'new', state: 'running' } }, 202);
    }
    if (url === '/api/context/eval') return evalPolls.length > 1 ? evalPolls.shift() : evalPolls[0];
    const values = {
      '/api/context/onboard': onboard,
      '/api/context/versions': { eval_post: { passed: false, message: 'run kge rollback' } },
      '/api/context/golden': { sets: [{ name: 'golden', version: 'v1', questions: ['A', 'B', 'C', 'D', 'E', 'F'], samples: ['A', 'B', 'C', 'D', 'F'], misses: ['E'] }] },
      '/api/context/ab': { baseline: { score: 2 }, runs: [{ run_id: 'run-1', principal: 'engineer@example.com', repeats: 1, arms: [{ arm: 'treatment', mode: 'mock' }], report: '/ab/run-1/report.html' }] },
      '/api/context/map/Revenue': { contexts: [{ id: 'revenue', description: 'Certified monthly takings.', fact: 'leasing.rental', currency: 'GBP', joins: [] }] },
      '/api/context/steward/queue': [], '/api/context/steward/queue?status=decided': [],
    };
    if (!(url in values)) throw new Error(`Unexpected resource ${url}`);
    return reply(values[url]);
  });
  vi.stubGlobal('fetch', fetch);
});
afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks(); });
const tab = name => fireEvent.click(screen.getByRole('tab', { name, exact: true }));
const ask = async (container, question) => {
  tab('Ask'); fireEvent.change(screen.getByLabelText('Question'), { target: { value: question } });
  fireEvent.click(screen.getByRole('button', { name: 'Ask', exact: true }));
  await waitFor(() => expect(container.querySelector('.msg[data-behaviour]')).toBeTruthy());
};

test('available screens use real tabs with one keyboard stop and Docs links to the developer page', async () => {
  const { container } = render(<ContextScreens bootstrap={opening()} />);
  await waitFor(() => expect(container.querySelector('#onboard')).toHaveAttribute('data-drawn', 'engineer@example.com'));
  const tabs = screen.getByRole('tablist', { name: 'Workspaces' });
  const chosen = within(tabs).getByRole('tab', { selected: true }); chosen.focus();
  fireEvent.keyDown(chosen, { key: 'Home' });
  expect(document.activeElement).toHaveTextContent('Ask');
  expect(within(tabs).getAllByRole('tab').filter(t => t.tabIndex === 0)).toHaveLength(1);
  fireEvent.keyDown(document.activeElement, { key: 'ArrowRight' });
  expect(document.activeElement).toHaveTextContent('Docs');
  expect(screen.getByRole('link', { name: 'Developer page' })).toHaveAttribute('href', '/api/context/');
  expect(screen.getByTitle('Your role guides')).toHaveAttribute('src', '/api/context/onboarding-guide');
  fireEvent.keyDown(document.activeElement, { key: 'End' }); expect(document.activeElement).toHaveTextContent('Quality');
  fireEvent.keyDown(document.activeElement, { key: 'ArrowRight' }); expect(document.activeElement).toHaveTextContent('Ask');
  fireEvent.keyDown(document.activeElement, { key: 'ArrowLeft' }); expect(document.activeElement).toHaveTextContent('Quality');
});

test('quality scores with the signed-in identity, shows A/B, baseline and the release gate', async () => {
  onboard.candidate = { gate: { passed: true, run_id: 'checked' } };
  const { container } = render(<ContextScreens bootstrap={opening()} />);
  tab('Quality');
  const button = screen.getByRole('button', { name: 'Run the golden set' });
  fireEvent.click(button);
  await waitFor(() => expect(button).toBeDisabled());
  await waitFor(() => expect(container.querySelector('#evalout')).toHaveTextContent('2/3'));
  expect(button).not.toBeDisabled();
  expect(container.querySelector('#evalout')).toHaveTextContent('Scored as engineer@example.com (reads every unit)');
  expect(fetch).toHaveBeenCalledWith('/api/context/eval', expect.objectContaining({ method: 'POST' }));
  expect(fetch.mock.calls.filter(([url, init]) => url === '/api/context/eval' && !init?.method).length).toBeGreaterThanOrEqual(2);
  expect(container.querySelector('#ab a')).toHaveAttribute('href', '/api/context/ab/run-1/report.html');
  expect(within(screen.getByRole('tabpanel', { name: 'Quality' })).getByText('Passed · checked')).toBeVisible();
  expect(screen.getByText('Release baseline')).toBeVisible();
  expect(container.querySelector('#release-eval-banner')).toHaveTextContent('run kge rollback');
});

test.each([404, 503])('quality evaluation surfaces HTTP %s errors when polling GET /eval', async status => {
  evalPolls = [
    reply({ state: 'running', id: 'eval-err', job: { id: 'eval-err', step: 'eval', state: 'running' } }),
    reply({ detail: `Eval unavailable ${status}` }, status),
  ];
  const { container } = render(<ContextScreens bootstrap={opening()} />);
  tab('Quality');
  const button = screen.getByRole('button', { name: 'Run the golden set' });
  fireEvent.click(button);
  await waitFor(() => expect(container.querySelector('#evalnote')).toHaveTextContent(`Eval unavailable ${status}`));
  expect(button).not.toBeDisabled();
});

test('Improve Run and proposal decisions preserve their payloads', async () => {
  const { container } = render(<ContextScreens bootstrap={opening()} />);
  const improve = await screen.findByRole('button', { name: 'Improve: Run' });
  fireEvent.click(improve);
  await waitFor(() => expect(fetch).toHaveBeenCalledWith('/api/context/onboard/run', expect.objectContaining({ body: JSON.stringify({ step: 'improve' }) })));
  await waitFor(() => expect(container.querySelector('#ob-msg')).toHaveTextContent('Improvement job started.'));
});

test.each([['accept', ['accepting'], {}], ['certify', ['certification'], { accepted_by: 'business' }]])('proposal %s keeps proposal identity and all items', async (action, processes, item) => {
  onboard = { writable: true, can: { [action]: true }, units: [], proposal: { id: 'proposal-1', items: [{ id: 'alias', state: 'draft', ...item }] } };
  const { container } = render(<ContextScreens bootstrap={opening(who(processes))} />);
  const button = await waitFor(() => { const button = container.querySelector(`#ob-proposal [data-act="${action}"]`); expect(button).toBeTruthy(); return button; });
  tab('Onboard'); fireEvent.click(button);
  await waitFor(() => expect(fetch).toHaveBeenCalledWith(`/api/context/onboard/${action}`, expect.objectContaining({ body: JSON.stringify({ proposal: 'proposal-1', items: 'all' }) })));
});

test.each([['graph', '/api/context/ask?display=1&background=true'], ['schema', '/api/context/answer?display=1'], ['treatment', '/api/context/agent']])('Ask routes %s with collapsed rich settings and safe provenance', async (arm, endpoint) => {
  answers = [{ question: 'Revenue', principal: who().id, arm, behaviour: 'figure', figure: 0, context: { context: 'revenue', term: 'Revenue' }, trace: [], sql: 'SELECT 0', run_trace: { stages: [{ stage: 'gate', ms: 1 }] } }];
  const { container } = render(<ContextScreens bootstrap={opening()} />); tab('Ask');
  const settings = container.querySelector('#settings'); expect(settings.open).toBe(false);
  fireEvent.click(settings.querySelector('summary'));
  fireEvent.change(screen.getByLabelText('Answered by'), { target: { value: arm } });
  fireEvent.click(screen.getByLabelText('Rich answers'));
  await ask(container, 'Revenue');
  await waitFor(() => expect(container.querySelector('.cur')).toHaveTextContent('GBP'));
  expect(fetch).toHaveBeenCalledWith(endpoint, expect.objectContaining({ body: JSON.stringify({ question: 'Revenue', purpose: 'analysis', arm }) }));
  expect(container.querySelector('.fig')).toHaveTextContent('0');
  expect(container.querySelector('.answer-card')).toHaveTextContent('Certified monthly takings.');
  expect(container.querySelector('#detail')).toBeVisible();
  expect(container.querySelector('#detail > summary')).toBeNull();
  fireEvent.click(screen.getByRole('tab', { name: 'Plan', exact: true }));
  expect(container.querySelector('#tab-plan')).toHaveTextContent('SELECT 0');
  const clipboard = { writeText: vi.fn(async () => {}) }; Object.defineProperty(navigator, 'clipboard', { configurable: true, value: clipboard });
  fireEvent.click(screen.getByRole('button', { name: 'Copy the query' }));
  await waitFor(() => expect(clipboard.writeText).toHaveBeenCalledWith('SELECT 0'));
});

test('a failure clears the selected answer details and its previous map', async () => {
  let fail = false;
  const resources = fetch.getMockImplementation();
  fetch.mockImplementation((url, init) => url === '/api/context/ask?background=true' && fail ? Promise.resolve(reply({ detail: 'Refused <script>' }, 403)) : resources(url, init));
  answers = [{ question: 'Revenue', principal: who().id, behaviour: 'figure', figure: 10, context: { term: 'Revenue' } }];
  const { container } = render(<ContextScreens bootstrap={opening()} />);
  await ask(container, 'Revenue'); await waitFor(() => expect(container.querySelector('.cur')).toHaveTextContent('GBP'));
  fail = true; fireEvent.change(screen.getByLabelText('Question'), { target: { value: 'Private figure' } }); fireEvent.click(screen.getByRole('button', { name: 'Ask', exact: true }));
  await waitFor(() => expect(container.querySelector('#tab-map')).toHaveTextContent('No answer to the last question: Not permitted: Refused <script>'));
  expect(container.querySelector('#detail')).not.toHaveTextContent('Certified monthly takings');
  expect(container.querySelector('script')).toBeNull();
});

test('examples spread over passing samples and keep all known misses a click away', async () => {
  const { container } = render(<ContextScreens bootstrap={opening(who(['querying']))} />);
  await waitFor(() => expect(container.querySelectorAll('#chips [data-q]')).toHaveLength(4));
  expect(container.querySelector('#examples')).not.toBeVisible();
  fireEvent.click(screen.getByRole('button', { name: 'Examples ▾' }));
  fireEvent.click(screen.getByRole('button', { name: 'All 6 sample questions' }));
  expect(container.querySelectorAll('#chips [data-q]')).toHaveLength(6);
  expect(container.querySelector('#chips .miss')).toHaveTextContent('E');
  expect(container.querySelector('#chips .note')).toHaveTextContent('Known miss: the graph arm');
  fireEvent.click(screen.getByRole('button', { name: 'Fewer sample questions' }));
  expect(container.querySelectorAll('#chips [data-q]')).toHaveLength(4);
});

test('unmounting aborts Context resources and late results cannot restore the previous person', async () => {
  const { container, rerender, unmount } = render(<ContextScreens bootstrap={opening(who(['querying', 'certification']))} />);
  await waitFor(() => expect(fetch).toHaveBeenCalledWith('/api/context/steward/queue', expect.anything()));
  const signal = fetch.mock.calls.find(([url]) => url.endsWith('/steward/queue'))[1].signal;
  rerender(<ContextScreens bootstrap={opening(who(['querying']))} />);
  expect(signal.aborted).toBe(true); expect(container.querySelector('#steward')).toBeNull();
  unmount(); expect(fetch.mock.calls.at(-1)[1].signal.aborted).toBe(true);
});

test('admin flags grant evaluation while role-like names grant no extra permissions', () => {
  expect(visibleTabs({ processes: ['querying'], role: 'Admin' })).toEqual(['ask', 'docs']);
  expect(visibleTabs({ admin: true })).toEqual(['docs', 'quality']);
  expect(visibleTabs({ roles: ['admin'] })).toEqual(['docs', 'quality']);
  expect(visibleTabs({ writes_terms: true })).toEqual(['docs', 'onboard']);
  expect(visibleTabs(null)).toEqual(['docs']);
  expect(reportUrl('https://outside.example/report')).toBeUndefined();
  expect(reportUrl('/ab/run-1/report.html')).toBe('/api/context/ab/run-1/report.html');
});

test('candidate change text preserves schema keys, escaped descriptions and estate reasons', () => {
  const key = { columns: ['a'], ref_table: 'old', ref_columns: ['id'] };
  const record = { lock: { changes: [{ source: 'u', table: 't', change: 'key-changed', was: { primary_key: ['id'], foreign_keys: [key] }, now: { primary_key: ['id', 'other'], foreign_keys: [{ ...key, ref_table: '<img>' }] } }, { source: 'u', table: 't', column: 'x', change: 'column-described', was: 'old', now: '<img>' }] }, estate: { rebuilt: true, why: 'fixture-changed' }, reloaded: true };
  expect(changeLines(record)).toEqual(['Primary key of u.t changed from (id) to (id, other)', 'Foreign key of u.t (a) referencing old (id) removed', 'Foreign key of u.t (a) referencing <img> (id) added', 'Description of column u.t.x changed from old to <img>', 'Estate rebuilt: the fixture files changed', 'Reloaded the same model']);
  expect(changeLines(null)).toEqual([]);
});

test('StrictMode replay starts fresh requests after cancelling the first mount', async () => {
  const { container, unmount } = render(<StrictMode><ContextScreens bootstrap={opening(who(['querying']))} /></StrictMode>);
  await waitFor(() => expect(container.querySelectorAll('#chips [data-q]')).toHaveLength(4));
  const goldenCalls = fetch.mock.calls.filter(([url]) => url === '/api/context/golden');
  expect(goldenCalls[0][1].signal.aborted).toBe(true);
  expect(goldenCalls.at(-1)[1].signal.aborted).toBe(false);
  unmount(); expect(goldenCalls.at(-1)[1].signal.aborted).toBe(true);
});

test.each([
  ['accept', {}, '/accept', { unit: 'leasing', items: 'all' }],
  ['certify', { accepted_by: 'business' }, '/certify', { unit: 'leasing', items: 'all' }],
  ['run', { accepted_by: 'business', certified_by: 'steward' }, '/run', { step: 'checks' }],
  ['sign_off', { accepted_by: 'business', certified_by: 'steward' }, '/run', { step: 'sign-off' }],
  ['release', { accepted_by: 'business', certified_by: 'steward' }, '/run', { step: 'release' }],
  ['rollback', {}, '/rollback', {}],
].flatMap(([action, item, path, body]) => [403, 503].map(status => ({ action, item, path, body, status }))))('$action surfaces HTTP $status failures and permits a retry', async ({ action, item, path, body, status }) => {
  onboard = { writable: true, can: { [action]: true }, units: [{ unit: 'leasing', items: [{ id: 'one', ...item }],
    history: ['sign_off', 'release'].includes(action) ? [{ step: 'onboard', detail: { passed: true } }] : [] }],
    candidate: { hash: 'candidate', ...(action === 'release' ? { signed_off: { principal: 'steward' } } : {}) } };
  const resources = fetch.getMockImplementation();
  fetch.mockImplementation((url, init) => init?.method === 'POST' ? Promise.resolve(reply({ detail: 'Decision unavailable <script>' }, status)) : resources(url, init));
  const { container } = render(<ContextScreens bootstrap={opening(who(['building', 'certification', 'accepting']))} />);
  const button = await waitFor(() => { const b = container.querySelector(action === 'rollback' ? '#ob-rollback' : `[data-act="${action}"]`); expect(b).toBeTruthy(); return b; });
  fireEvent.click(button);
  await waitFor(() => expect(container.querySelector('#ob-msg')).toHaveTextContent(`${status === 403 ? 'Not permitted: ' : ''}Decision unavailable <script>`));
  expect(fetch).toHaveBeenCalledWith('/api/context/onboard' + path, expect.objectContaining({ method: 'POST', body: JSON.stringify(body) }));
  await waitFor(() => expect(button).not.toBeDisabled());
  fireEvent.click(button);
  await waitFor(() => expect(fetch.mock.calls.filter(([, init]) => init?.method === 'POST')).toHaveLength(2));
  expect(container.querySelector('script')).toBeNull();
});

test.each([409, 403, 503])('checks waiting on rows keep HTTP %s in its proper tone and plain words', async status => {
  onboard = { writable: true, can: { run: true }, units: [{ unit: 'leasing', label: 'Asset Finance', items: [{ id: 'rent', accepted_by: 'business', certified_by: 'steward' }] }] };
  const resources = fetch.getMockImplementation();
  const detail = 'unit leasing has no rows: onboarding stops at the drafted pack. The checks need its rows: CSVs beside the DDL (one <table>.csv per table), or invented rows from the DDL (ROWS=invented), labelled invented';
  fetch.mockImplementation((url, init) => init?.method === 'POST' ? Promise.resolve(reply({ detail }, status)) : resources(url, init));
  const { container } = render(<ContextScreens bootstrap={opening()} />);
  fireEvent.click(await screen.findByRole('button', { name: 'Run checks' }));
  const message = container.querySelector('#ob-msg');
  await waitFor(() => expect(message).toHaveTextContent('CSVs beside the DDL'));
  expect(message.textContent.startsWith(status === 409 ? 'needs attention' : 'refused')).toBe(true);
  expect(message.querySelector('.badge')).toHaveClass(status === 409 ? 'warn' : 'bad');
  expect(message).toHaveTextContent('invented rows');
  expect(message).toHaveTextContent('<table>.csv');
  expect(message).not.toHaveTextContent('ROWS=');
  expect(message.querySelector('table')).toBeNull();
  expect(fetch).toHaveBeenCalledWith('/api/context/onboard/run', expect.objectContaining({ body: JSON.stringify({ step: 'checks' }) }));
});

test('a successful onboarding response waiting on rows also needs attention', async () => {
  onboard = { writable: true, can: { accept: true }, units: [{ unit: 'leasing', items: [{ id: 'rent' }] }] };
  const resources = fetch.getMockImplementation();
  fetch.mockImplementation((url, init) => init?.method === 'POST' ? Promise.resolve(reply({ said: 'The next check needs its rows (ROWS=invented).' })) : resources(url, init));
  const { container } = render(<ContextScreens bootstrap={opening(who(['accepting']))} />);
  fireEvent.click(await screen.findByRole('button', { name: 'Accept all' }));
  const message = container.querySelector('#ob-msg');
  await waitFor(() => expect(message).toHaveTextContent('needs attentionThe next check needs its rows.'));
  expect(message.querySelector('.badge')).toHaveClass('warn');
});

test.each([
  [{ processes: ['querying'] }, ['Ask', 'Docs']],
  [{ processes: ['querying', 'certification', 'building'] }, ['Ask', 'Docs', 'Review', 'Onboard', 'Quality']],
  [{ processes: ['certification'] }, ['Docs', 'Review', 'Onboard']],
  [{}, ['Docs']],
  [{ processes: ['accepting'] }, ['Docs', 'Onboard']],
  [{ processes: ['improving'] }, ['Docs', 'Onboard']],
  [{ writes_terms: true }, ['Docs', 'Onboard']],
  [{ admin: true }, ['Docs', 'Quality']],
  [{ roles: ['admin'] }, ['Docs', 'Quality']],
  [{ role: 'admin' }, ['Docs']],
])('tabs and resource reads follow capabilities: %j', async (capabilities, names) => {
  render(<ContextScreens bootstrap={opening({ id: 'person@example.com', ...capabilities })} />);
  await waitFor(() => expect(fetch).toHaveBeenCalledWith('/api/context/versions', expect.anything()));
  expect(within(screen.getByRole('tablist', { name: 'Workspaces' })).getAllByRole('tab').map(b => b.textContent)).toEqual(names);
  expect(fetch.mock.calls.some(([url]) => url === '/api/context/onboard')).toBe(names.includes('Onboard'));
  expect(fetch.mock.calls.some(([url]) => url.includes('/steward/queue'))).toBe(names.includes('Review'));
});

test.each(['refuse', 'blocked_access'])('%s answers never request a map and explain readable alternatives', async behaviour => {
  answers = [{ question: 'Private revenue', principal: who().id, behaviour, reason: 'Cannot read private', denied_warehouses: ['private'], context: { term: 'Revenue' } }];
  const { container } = render(<ContextScreens bootstrap={opening(who(['querying']))} />);
  await ask(container, 'Private revenue');
  expect(container.querySelector('.answer-card')).toHaveTextContent('Ask about leasing instead');
  expect(container.querySelector('#detail')).toBeVisible();
  expect(container.querySelector('#tab-map')).toHaveTextContent('No map for a refused answer');
  expect(container.querySelector('#detail')).toBeVisible();
  expect(container.querySelector('#detail > summary')).toBeNull();
  expect(fetch.mock.calls.some(([url]) => url.includes('/map/'))).toBe(false);
});

test.each([
  [undefined, 'Ask a governed question', 'Certified definitions and figures, answered only from data you may read.'],
  [{ title: 'Ask Meridian', subtitle: 'Answers from certified figures.' }, 'Ask Meridian', 'Answers from certified figures.'],
])('the Ask header shows the portal settings the opening screen carries: %j', async (ask, title, subtitle) => {
  const { container } = render(<ContextScreens bootstrap={{ ...opening(), ...(ask ? { ask } : {}) }} />);
  tab('Ask');
  expect(within(container.querySelector('.ask-title-row')).getByRole('heading', { level: 1 })).toHaveTextContent(title);
  expect(container.querySelector('.ask-subtitle').textContent).toBe(subtitle);
});
