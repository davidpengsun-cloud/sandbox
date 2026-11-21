import React from 'react';
import { afterEach, beforeEach, expect, test, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import fixture from './fixtures/steward.json';
import CurateView from './CurateView';

/* The portal's settings as /api/config gives them; a test sets Certify's address or turns it off. */
const settings = vi.hoisted(() => ({ value: null }));
vi.mock('../../../services/config', async (actual) => ({ ...(await actual()), useConfig: () => settings.value }));
const CONFIG = { apps: { certify: true }, certifyUrl: null };

const reply = (body, status = 200) => ({ ok: status < 400, status, json: async () => body });

/* The graph read: three columns, one with a certified term, one proposed, one with none; and a table. */
const GRAPH = { nodes: [{ id: 'table:sales', kind: 'table', name: 'sales' }, { id: 'column:sales.net', kind: 'column', name: 'net' },
  { id: 'column:sales.gross', kind: 'column', name: 'gross' }, { id: 'column:sales.id', kind: 'column', name: 'id' }, { id: 'term:net', kind: 'term', name: 'Net revenue' }],
links: [{ source: 'column:sales.net', target: 'term:net', kind: 'maps_to', status: 'certified', why: '' },
  { source: 'column:sales.gross', target: 'term:net', kind: 'maps_to', status: 'proposed', why: '' },
  { source: 'table:sales', target: 'term:net', kind: 'maps_to', status: 'certified', why: '' }] };
const GAPS = { days: 7, total: 4, gaps: [{ text: 'churn by region', count: 3 }, { text: 'margin last quarter', count: 1 }] };
const candidateOf = (accepted, certified, signed) => ({
  can: { accept: true, certify: true, sign_off: true },
  candidate: { hash: 'c0ffeeabcdefabcd', loaded: true, gate: { passed: true, sets: { finance: 'passed' } }, ...(signed ? { signed_off: { principal: 'sam.steward', at: '2026-11-15T10:00:00Z' } } : {}) },
  units: [{ unit: 'sales', label: 'Sales', items: [{ id: 'i1', ...(accepted ? { accepted_by: 'bo' } : {}), ...(certified ? { certified_by: 'sam' } : {}) }, { id: 'i2', ...(accepted ? { accepted_by: 'bo' } : {}), ...(certified ? { certified_by: 'sam' } : {}) }] }],
});

/* A fake portal: /queue and /queue?status=decided from the fixture; /decide records the body and
   takes the proposal off the queue, adding it to the decided list with who and when; the gaps, the
   graph read and the candidate, whose /accept, /certify and /sign-off move it on. */
function portal({ refuse } = {}) {
  let queue = [...fixture.queue];
  let decided = [...fixture.decided];
  let stage = { accepted: false, certified: false, signed: false };
  const calls = [];
  const fetcher = vi.fn(async (url, init = {}) => {
    calls.push({ url, init });
    if (url.startsWith('/api/cerebro/graph?')) return reply(GRAPH);
    if (url === '/api/cerebro/steward/gaps') return reply(GAPS);
    if (url === '/api/cerebro/steward/candidate') return reply(candidateOf(stage.accepted, stage.certified, stage.signed));
    if (url === '/api/cerebro/steward/accept' && refuse) return reply({ detail: refuse }, 403);
    if (url === '/api/cerebro/steward/accept') { stage = { ...stage, accepted: true }; return reply({ said: 'Accepted 2 items.' }); }
    if (url === '/api/cerebro/steward/certify') { stage = { ...stage, certified: true }; return reply({ said: 'Certified 2 items.' }); }
    if (url === '/api/cerebro/steward/sign-off') { stage = { ...stage, signed: true }; return reply({ job: { id: 'j1' } }); }
    if (url === '/api/cerebro/steward/queue') return reply(queue);
    if (url === '/api/cerebro/steward/queue?status=decided') return reply(decided);
    if (url === '/api/cerebro/steward/decide') {
      const body = JSON.parse(init.body);
      if (refuse) return reply({ detail: refuse }, 403);
      const row = queue.find((item) => item.tag === body.tag);
      queue = queue.filter((item) => item.tag !== body.tag);
      decided = [...decided, { tag: body.tag, term: row.value, doc: row.doc, decision: body.decision, who: 'sam.steward', at: '2026-11-15T09:00:00Z', release: 'f00dfeedcafe0000' }];
      return reply({ decision: body.decision, ...(body.definition ? { term_created: row.value } : {}) });
    }
    return reply({ detail: 'Not found.' }, 404);
  });
  return { fetcher, calls };
}

const actions = () => ({ openNode: vi.fn() });
beforeEach(() => { settings.value = CONFIG; });
afterEach(() => { cleanup(); vi.restoreAllMocks(); });

/* The kind filter's chip for a kind, by its label; All is pressed by default. */
const chip = (within$, name) => within$.getByRole('button', { name: new RegExp(`^${name} \\d+$`) });
const showKind = async (name) => fireEvent.click(chip(within(await screen.findByRole('group', { name: 'Filter by kind' })), name));

test('the page heading, its role note and the four tiles render from the fixture', async () => {
  const { fetcher } = portal();
  render(<CurateView actions={actions()} fetcher={fetcher} />);
  expect(screen.getByRole('heading', { level: 2, name: 'Review and curation' })).toBeInTheDocument();
  expect(screen.getByText(/You review as a steward/)).toBeInTheDocument();
  const tiles = screen.getByRole('group', { name: 'Curate at a glance' });
  await waitFor(() => expect(tiles).toHaveTextContent('4Proposals waiting'));
  await waitFor(() => expect(tiles).toHaveTextContent('33%Columns with a certified term'));
  await waitFor(() => expect(tiles).toHaveTextContent('4Gaps people hit this week'));
  expect(tiles).toHaveTextContent('0Accepted this session');
  expect([...tiles.querySelectorAll('.curate-tile-label')].map((label) => label.textContent)).toEqual(['Proposals waiting', 'Accepted this session', 'Columns with a certified term', 'Gaps people hit this week']);
  expect(['Proposals', 'Decided', 'Candidate to review', 'Gaps people hit'].map((name) => screen.getByRole('region', { name }).querySelector('h3').textContent))
    .toEqual(['Proposals', 'Decided', 'Candidate to review', 'Gaps people hit']);
});

test('proposals list every kind under All, with each row\'s kind; the chips are All and each kind present, with counts', async () => {
  const { fetcher } = portal();
  render(<CurateView actions={actions()} fetcher={fetcher} />);
  const proposals = await screen.findByRole('region', { name: 'Proposals' });
  const filter = await within(proposals).findByRole('group', { name: 'Filter by kind' });
  expect(within(filter).getAllByRole('button').map((button) => button.textContent)).toEqual(['All 4', 'Term tags 1', 'New words 1', 'Documents 1', 'Cross-reference matches 0', 'Other 1']);
  expect(chip(within(filter), 'All')).toHaveAttribute('aria-pressed', 'true');
  expect(within(filter).queryByRole('button', { name: /Filter values/ })).toBeNull();
  const all = within(proposals).getByRole('list', { name: 'All proposals' });
  const rows = within(all).getAllByRole('listitem');
  expect(rows.map((row) => row.dataset.tag)).toEqual(['tag:sm1', 'tag:ct1', 'tag:pv1', 'tag:xr1', 'tag:nw1']);
  expect(rows.map((row) => row.querySelector('.curate-kind').textContent)).toEqual(['Term tag', 'New word', 'Document', 'Cross-reference', 'Other']);
  expect(rows[0]).toHaveTextContent('Net revenue');
  expect(rows[0]).toHaveTextContent('From pricing-policy#3 · certified');
  expect(fetcher).toHaveBeenCalledWith('/api/cerebro/steward/queue', expect.objectContaining({ credentials: 'same-origin' }));
});

test('the kind filter narrows the list to one kind, and All shows every kind again', async () => {
  const { fetcher } = portal();
  render(<CurateView actions={actions()} fetcher={fetcher} />);
  const proposals = await screen.findByRole('region', { name: 'Proposals' });
  await showKind('Documents');
  const filter = within(proposals).getByRole('group', { name: 'Filter by kind' });
  expect(chip(within(filter), 'Documents')).toHaveAttribute('aria-pressed', 'true');
  expect(chip(within(filter), 'All')).toHaveAttribute('aria-pressed', 'false');
  expect(within(proposals).queryByRole('list', { name: 'All proposals' })).toBeNull();
  const documents = within(proposals).getByRole('list', { name: 'Documents' });
  expect(within(documents).getAllByRole('listitem').map((row) => row.dataset.tag)).toEqual(['tag:pv1']);
  expect(documents).toHaveTextContent('Reject needs a second steward');
  await showKind('Cross-reference matches');
  const xref = within(proposals).getByRole('list', { name: 'Cross-reference matches' });
  expect(xref).toHaveTextContent('rejected upstream · a decision here is recorded in the graph only');
  expect(within(proposals).queryByRole('button', { name: 'View in graph' })).toBeNull();
  const settled = within(xref).getByRole('listitem');
  expect(settled).toHaveClass('settled');
  expect(within(settled).getByRole('button', { name: 'Accept' })).not.toBeDisabled();
  expect(within(settled).getByRole('button', { name: 'Accept' })).not.toHaveClass('primary');
  expect(within(settled).getByRole('button', { name: 'Reject' })).not.toBeDisabled();
  await showKind('All');
  expect(within(within(proposals).getByRole('list', { name: 'All proposals' })).getAllByRole('listitem')).toHaveLength(5);
});

test('a suggested link names its score as similarity, other proposals as confidence, as Context\'s review did', async () => {
  const { fetcher } = portal();
  render(<CurateView actions={actions()} fetcher={fetcher} />);
  const proposals = await screen.findByRole('region', { name: 'Proposals' });
  await showKind('Term tags');
  expect(within(proposals).getByRole('list', { name: 'Term tags' })).toHaveTextContent('Proposed · similarity 82%');
  await showKind('New words');
  expect(within(proposals).getByRole('list', { name: 'New words' })).toHaveTextContent('Proposed · confidence 71%');
});

test('Accept sends the decision with a candidate term\'s definition, then the queue, the decided list and the tiles refresh', async () => {
  const { fetcher, calls } = portal();
  render(<CurateView actions={actions()} fetcher={fetcher} />);
  await showKind('New words');
  expect(within(screen.getByRole('list', { name: 'New words' })).getByRole('button', { name: 'Accept' })).toHaveClass('primary');
  fireEvent.change(screen.getByLabelText('Definition to record with Churn'), { target: { value: ' Customers who leave. ' } });
  fireEvent.click(within(screen.getByRole('list', { name: 'New words' })).getByRole('button', { name: 'Accept' }));
  await waitFor(() => expect(within(screen.getByRole('region', { name: 'Proposals' })).getByRole('status')).toHaveTextContent('Accepted. It goes into the next release with your name. Term created: Churn.'));
  const decide = calls.find((call) => call.url === '/api/cerebro/steward/decide');
  expect(decide.init.method).toBe('POST');
  expect(JSON.parse(decide.init.body)).toEqual({ tag: 'tag:ct1', decision: 'accept', definition: 'Customers who leave.' });
  const tiles = screen.getByRole('group', { name: 'Curate at a glance' });
  await waitFor(() => expect(tiles).toHaveTextContent('3Proposals waiting'));
  expect(tiles).toHaveTextContent('1Accepted this session');
  expect(screen.getByText('Nothing waiting in new words.')).toBeInTheDocument();
  expect(chip(within(screen.getByRole('group', { name: 'Filter by kind' })), 'New words')).toHaveAttribute('aria-pressed', 'true');
  await showKind('All');
  expect(within(screen.getByRole('group', { name: 'Filter by kind' })).queryByRole('button', { name: /New words/ })).toBeNull();
});

test('under All, each row\'s Accept and Reject send that row\'s decision, and View in graph opens its node', async () => {
  const act$ = actions();
  const { fetcher, calls } = portal();
  render(<CurateView actions={act$} fetcher={fetcher} />);
  const all = await screen.findByRole('list', { name: 'All proposals' });
  const row = (tag) => within(screen.getByRole('list', { name: 'All proposals' })).getAllByRole('listitem').find((item) => item.dataset.tag === tag);
  fireEvent.click(within(row('tag:ct1')).getByRole('button', { name: 'View in graph' }));
  expect(act$.openNode).toHaveBeenCalledWith('document:retention-note');
  expect(within(all).getAllByRole('button', { name: 'Accept' }).filter((button) => button.classList.contains('primary'))).toHaveLength(4);
  fireEvent.click(within(row('tag:pv1')).getByRole('button', { name: 'Reject' }));
  await waitFor(() => expect(within(screen.getByRole('region', { name: 'Proposals' })).getByRole('status')).toHaveTextContent('Rejected.'));
  await waitFor(() => expect(row('tag:pv1')).toBeUndefined());
  fireEvent.click(within(row('tag:nw1')).getByRole('button', { name: 'Accept' }));
  await waitFor(() => expect(screen.getByRole('group', { name: 'Curate at a glance' })).toHaveTextContent('1Accepted this session'));
  expect(calls.filter((call) => call.url === '/api/cerebro/steward/decide').map((call) => JSON.parse(call.init.body)))
    .toEqual([{ tag: 'tag:pv1', decision: 'reject' }, { tag: 'tag:nw1', decision: 'accept' }]);
});

test('Reject sends the decision alone; the decided list shows who and when', async () => {
  const { fetcher, calls } = portal();
  render(<CurateView actions={actions()} fetcher={fetcher} />);
  await showKind('Term tags');
  fireEvent.click(within(screen.getByRole('list', { name: 'Term tags' })).getByRole('button', { name: 'Reject' }));
  await waitFor(() => expect(within(screen.getByRole('region', { name: 'Proposals' })).getByRole('status')).toHaveTextContent('Rejected.'));
  expect(screen.getByRole('group', { name: 'Curate at a glance' })).toHaveTextContent('0Accepted this session');
  expect(JSON.parse(calls.find((call) => call.url === '/api/cerebro/steward/decide').init.body)).toEqual({ tag: 'tag:sm1', decision: 'reject' });
  const decided = screen.getByRole('region', { name: 'Decided' });
  await waitFor(() => expect(within(decided).getAllByRole('row')).toHaveLength(3));
  const rows = within(decided).getAllByRole('row').map((row) => row.textContent);
  expect(rows[1]).toContain('Gross margin');
  expect(rows[1]).toContain('sam.steward');
  expect(rows[1]).toContain('2026-11-14T10:02:00Z');
  expect(rows[2]).toContain('Net revenue');
  expect(rows[2]).toContain('reject');
  expect(rows[2]).toContain('2026-11-15T09:00:00Z');
});

test('KGE\'s refusal of a decision is shown, and nothing is counted', async () => {
  const { fetcher } = portal({ refuse: 'Not a steward.' });
  render(<CurateView actions={actions()} fetcher={fetcher} />);
  await showKind('Term tags');
  fireEvent.click(within(screen.getByRole('list', { name: 'Term tags' })).getByRole('button', { name: 'Accept' }));
  await waitFor(() => expect(within(screen.getByRole('region', { name: 'Proposals' })).getByRole('alert')).toHaveTextContent('Not a steward.'));
  expect(screen.getByRole('group', { name: 'Curate at a glance' })).toHaveTextContent('0Accepted this session');
});

/* KGE's /steward/decide accepts a cross-reference match its upstream rejected, as Context's review allowed, and its reply
   carries the caveat that the match counts in no figure until the cross-reference certifies it upstream. */
const CAVEAT = 'Recorded in the graph only: figures change when the cross-reference is certified again upstream. The match is rejected upstream: it counts in no figure unless the cross-reference certifies it there.';
test('a match rejected upstream can still be accepted; the request is the decision alone and KGE\'s caveat is shown', async () => {
  const { fetcher: base, calls } = portal();
  const fetcher = vi.fn(async (url, init = {}) => {
    const answer = await base(url, init);
    if (url !== '/api/cerebro/steward/decide') return answer;
    const body = await answer.json();
    return reply({ ...body, note: CAVEAT });
  });
  render(<CurateView actions={actions()} fetcher={fetcher} />);
  await showKind('Cross-reference matches');
  const row = within(screen.getByRole('list', { name: 'Cross-reference matches' })).getByRole('listitem');
  fireEvent.click(within(row).getByRole('button', { name: 'Accept' }));
  const status = within(screen.getByRole('region', { name: 'Proposals' })).getByRole('status');
  await waitFor(() => expect(status).toHaveTextContent(`Accepted. It goes into the next release with your name. ${CAVEAT}`));
  const decide = calls.find((call) => call.url === '/api/cerebro/steward/decide');
  expect(decide.init.method).toBe('POST');
  expect(JSON.parse(decide.init.body)).toEqual({ tag: 'tag:xr1', decision: 'accept' });
});

test('"View in graph" opens Graph mode on the proposal\'s node', async () => {
  const act$ = actions();
  const { fetcher } = portal();
  render(<CurateView actions={act$} fetcher={fetcher} />);
  await showKind('Term tags');
  fireEvent.click(within(screen.getByRole('list', { name: 'Term tags' })).getByRole('button', { name: 'View in graph' }));
  expect(act$.openNode).toHaveBeenCalledWith('document:pricing-policy');
});

test('a queue the portal refuses is shown as an alert, not an empty list', async () => {
  const fetcher = vi.fn(async () => reply({ detail: 'You need the steward role.' }, 403));
  render(<CurateView actions={actions()} fetcher={fetcher} />);
  const proposals = screen.getByRole('region', { name: 'Proposals' });
  await waitFor(() => expect(within(proposals).getByRole('alert')).toHaveTextContent('You need the steward role.'));
  expect(within(screen.getByRole('region', { name: 'Decided' })).getByRole('alert')).toHaveTextContent('You need the steward role.');
});

test('the candidate to review shows its checks; business acceptance, Certify and sign-off go through the portal in turn', async () => {
  const { fetcher, calls } = portal();
  render(<CurateView actions={actions()} fetcher={fetcher} />);
  const region = await screen.findByRole('region', { name: 'Candidate to review' });
  const checks = await within(region).findByRole('list', { name: 'Check results' });
  expect(checks).toHaveTextContent('Release gate Passed');
  expect(checks).toHaveTextContent('finance passed');
  expect(checks).toHaveTextContent('Sign-off Not signed off');
  expect(region.querySelector('.curate-candidate-name')).toHaveTextContent('c0ffeeabcdef');
  expect(region).toHaveTextContent('2 changes');
  expect([...checks.querySelectorAll('.curate-status')].map((status) => [status.textContent, [...status.classList].at(-1)]))
    .toEqual([['Passed', 'ok'], ['passed', 'ok'], ['Not signed off', 'neutral']]);
  expect(within(region).queryByRole('button', { name: 'Certify' })).toBeNull();
  expect(within(region).queryByRole('button', { name: /Return/ })).toBeNull();
  fireEvent.click(within(region).getByRole('button', { name: 'Record business acceptance' }));
  await waitFor(() => expect(within(region).getByRole('status')).toHaveTextContent('Accepted 2 items.'));
  const certify = await within(region).findByRole('button', { name: 'Certify' });
  expect(certify).toHaveClass('primary');
  fireEvent.click(certify);
  await waitFor(() => expect(within(region).getByRole('status')).toHaveTextContent('Certified 2 items.'));
  fireEvent.click(within(region).getByRole('button', { name: 'Sign off' }));
  await waitFor(() => expect(within(region).getByRole('status')).toHaveTextContent('Sign-off started.'));
  await waitFor(() => expect(within(region).getByRole('list', { name: 'Check results' })).toHaveTextContent('Sign-off sam.steward · 2026-11-15T10:00:00Z'));
  expect(within(region).queryByRole('button', { name: 'Sign off' })).toBeNull();
  const posted = calls.filter((call) => call.init.method === 'POST').map((call) => [call.url, JSON.parse(call.init.body)]);
  expect(posted).toEqual([
    ['/api/cerebro/steward/accept', { unit: 'sales', items: 'all' }],
    ['/api/cerebro/steward/certify', { unit: 'sales', items: 'all' }],
    ['/api/cerebro/steward/sign-off', { hash: 'c0ffeeabcdefabcd' }],
  ]);
});

test('KGE\'s refusal of business acceptance is shown in the candidate', async () => {
  const { fetcher } = portal({ refuse: 'Business acceptance needs the business role.' });
  render(<CurateView actions={actions()} fetcher={fetcher} />);
  const region = await screen.findByRole('region', { name: 'Candidate to review' });
  fireEvent.click(await within(region).findByRole('button', { name: 'Record business acceptance' }));
  await waitFor(() => expect(within(region).getByRole('alert')).toHaveTextContent('Business acceptance needs the business role.'));
});

/* Context's accept, certify and sign-off failure cases, moved with the actions (#466): KGE's 403 or 503 detail is shown
   as text, never markup, the request carries its payload, and the button comes back for a retry that sends it again. */
test.each([
  ['accept', 'Record business acceptance', [false, false], { unit: 'sales', items: 'all' }],
  ['certify', 'Certify', [true, false], { unit: 'sales', items: 'all' }],
  ['sign-off', 'Sign off', [true, true], { hash: 'c0ffeeabcdefabcd' }],
].flatMap(([path, name, stage, body]) => [403, 503].map((status) => ({ path, name, stage, body, status }))))('$path surfaces HTTP $status failures and permits a retry', async ({ path, name, stage, body, status }) => {
  const { fetcher: base } = portal();
  const fetcher = vi.fn(async (url, init = {}) => {
    if (init.method === 'POST') return reply({ detail: 'Decision unavailable <script>' }, status);
    if (url === '/api/cerebro/steward/candidate') return reply(candidateOf(...stage, false));
    return base(url, init);
  });
  const { container } = render(<CurateView actions={actions()} fetcher={fetcher} />);
  const region = await screen.findByRole('region', { name: 'Candidate to review' });
  fireEvent.click(await within(region).findByRole('button', { name }));
  await waitFor(() => expect(within(region).getByRole('alert')).toHaveTextContent('Decision unavailable <script>'));
  const posts = () => fetcher.mock.calls.filter(([, init]) => init?.method === 'POST');
  expect(posts().map(([url, init]) => [url, JSON.parse(init.body)])).toEqual([[`/api/cerebro/steward/${path}`, body]]);
  await waitFor(() => expect(within(region).getByRole('button', { name })).not.toBeDisabled());
  fireEvent.click(within(region).getByRole('button', { name }));
  await waitFor(() => expect(posts()).toHaveLength(2));
  expect(posts().map(([url, init]) => [url, JSON.parse(init.body)])).toEqual([[`/api/cerebro/steward/${path}`, body], [`/api/cerebro/steward/${path}`, body]]);
  expect(container.querySelector('script')).toBeNull();
});

test('the gaps people hit list this week\'s unanswered questions with how often they were asked', async () => {
  const { fetcher } = portal();
  render(<CurateView actions={actions()} fetcher={fetcher} />);
  const gaps = await screen.findByRole('list', { name: 'Questions not answered this week' });
  expect(within(gaps).getAllByRole('listitem').map((item) => item.textContent)).toEqual(['churn by region asked 3 times', 'margin last quarter asked once']);
  expect(fetcher).toHaveBeenCalledWith('/api/cerebro/steward/gaps', expect.objectContaining({ credentials: 'same-origin' }));
});

/* Documents items (document tiers and personal data in documents, §6.2) link to Certify; no other kind does. */
test('a documents item opens Certify at PORTAL_CERTIFY_URL in a new tab; other kinds have no Certify link', async () => {
  settings.value = { ...CONFIG, certifyUrl: 'https://certify.example.invalid/app' };
  const { fetcher } = portal();
  render(<CurateView actions={actions()} fetcher={fetcher} />);
  const proposals = await screen.findByRole('region', { name: 'Proposals' });
  await showKind('Term tags');
  expect(within(proposals).queryByRole('link', { name: /Open in Certify/ })).toBeNull();
  await showKind('New words');
  expect(within(proposals).queryByRole('link', { name: /Open in Certify/ })).toBeNull();
  await showKind('Documents');
  const link = within(within(proposals).getByRole('list', { name: 'Documents' })).getByRole('link', { name: /Open in Certify/ });
  expect(link).toHaveAttribute('href', 'https://certify.example.invalid/app');
  expect(link).toHaveAttribute('target', '_blank');
  expect(link).toHaveAttribute('rel', 'noopener noreferrer');
  expect(link).toHaveTextContent('(opens outside the platform)');
});

test('with no Certify address a documents item shows the placeholder, and nothing where Certify is off', async () => {
  const { fetcher } = portal();
  const { unmount } = render(<CurateView actions={actions()} fetcher={fetcher} />);
  await showKind('Documents');
  const list = screen.getByRole('list', { name: 'Documents' });
  expect(list).toHaveTextContent('Certify opens at [Power App link]');
  expect(within(list).queryByRole('link')).toBeNull();
  unmount();
  settings.value = { apps: { certify: false }, certifyUrl: null };
  render(<CurateView actions={actions()} fetcher={portal().fetcher} />);
  await showKind('Documents');
  expect(screen.getByRole('list', { name: 'Documents' })).not.toHaveTextContent('Certify');
});
