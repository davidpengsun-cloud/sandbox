import React from 'react';
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { expect, test, vi } from 'vitest';
import Review from './Review';
import Quality from './Quality';
import Onboard, { changeLines } from './Onboard';

const resource = data => ({ data, loading: false, refresh: vi.fn(async () => {}) });
const suggestion = (extra = {}) => ({ tag: 'tag-1', family: 'candidate_term', value: 'Revenue', doc: 'doc-1', confidence: .8, ...extra });
const me = { id: 'steward@example.com', reads: ['leasing'] };

for (const decision of ['accept', 'reject']) {
  test.each([
    [{ decision, superseded: 'machine tag', term_created: 'revenue', embedded_now: true, readiness: 'citable', note: 'Recorded safely' }, 'term created: revenue'],
    [{ status: 'pending_second_check', reason: 'another steward must confirm' }, 'second check · another steward must confirm'],
    [{ error: 'Not permitted: decision refused <script>' }, 'Not permitted: decision refused <script>'],
    [{ error: 'The service is unavailable right now. Try again shortly.' }, 'The service is unavailable right now. Try again shortly.'],
  ])(`Review ${decision} records the result or preserves the queue on failure: %j`, async (result, message) => {
    const api = vi.fn(async () => result), queue = resource([suggestion()]), decided = resource([]);
    const { container } = render(<Review api={api} queue={queue} decided={decided} />);
    fireEvent.change(screen.getByLabelText('Definition to record with Revenue'), { target: { value: '  Certified takings.  ' } });
    fireEvent.click(screen.getByRole('button', { name: decision === 'accept' ? 'Accept' : 'Reject', exact: true }));
    await waitFor(() => expect(container.querySelector('#queuemsg')).toHaveTextContent(message));
    expect(api).toHaveBeenCalledWith('/steward/decide', { tag: 'tag-1', decision, ...(decision === 'accept' ? { definition: 'Certified takings.' } : {}) });
    expect(queue.refresh).toHaveBeenCalledTimes(result.error ? 0 : 1);
    expect(decided.refresh).toHaveBeenCalledTimes(result.error ? 0 : 1);
    expect(container.querySelector('script')).toBeNull();
    expect(screen.getByRole('button', { name: 'Accept', exact: true })).not.toBeDisabled();
  });
}

test.each(['accept', 'reject'])('Review shows all remaining families after the last filtered suggestion is decided: %s', async decision => {
  const api = vi.fn(async () => ({ decision, superseded: 'suggestion' }));
  const other = suggestion({ tag: 'tag-2', family: 'privacy', value: 'Personal data' });
  const queue = resource([suggestion(), other]), decided = resource([]);
  const { rerender } = render(<Review api={api} queue={queue} decided={decided} />);
  queue.refresh.mockImplementation(async () => {
    rerender(<Review api={api} queue={{ ...queue, data: [other] }} decided={decided} />);
  });
  fireEvent.change(screen.getByLabelText('Show'), { target: { value: 'candidate_term' } });
  expect(screen.queryByText('Personal data', { exact: true })).toBeNull();
  fireEvent.click(screen.getByRole('button', { name: decision === 'accept' ? 'Accept' : 'Reject', exact: true }));
  await waitFor(() => expect(queue.refresh).toHaveBeenCalledOnce());
  expect(screen.getByLabelText('Show')).toHaveValue('');
  expect(screen.queryByRole('option', { name: /Candidate terms/ })).toBeNull();
  expect(screen.getByText('Personal data', { exact: true })).toBeVisible();
});

test('Review headings count only non-rejected suggestions in each family', () => {
  const queue = resource([suggestion(), suggestion({ tag: 'tag-2', upstream: 'accepted' }),
    suggestion({ tag: 'tag-3', upstream: 'rejected' }), suggestion({ tag: 'tag-4', family: 'privacy', upstream: 'rejected' })]);
  const { container } = render(<Review api={vi.fn()} queue={queue} decided={resource([])} />);
  expect([...container.querySelectorAll('.queue-kind')].map(heading => heading.textContent)).toEqual(['Candidate terms2', 'Personal data in documents0']);
  fireEvent.change(screen.getByLabelText('Show'), { target: { value: 'candidate_term' } });
  expect(container.querySelectorAll('.queue-kind')).toHaveLength(1);
  expect(container.querySelector('.queue-kind .group-count')).toHaveTextContent('2');
  expect(container.querySelectorAll('.settled')).toHaveLength(1);
});

test('Review disables decisions while pending, filters families and highlights the answer source', async () => {
  let finish;
  const api = vi.fn(() => new Promise(resolve => { finish = resolve; }));
  const queue = resource([suggestion({ family: 'suggested_mention', similarity: .9, passage: 'A certified passage', assessment: 'needs_review', upstream: 'accepted', unit: 'leasing', second_check_required_to_reject: true }),
    suggestion({ tag: 'tag-2', family: 'privacy', doc: 'doc-2', upstream: 'rejected', reject_requested_by: 'first steward', units: ['leasing'] })]);
  const { container, rerender } = render(<Review api={api} queue={queue} decided={resource([])} />);
  fireEvent.change(screen.getByLabelText('Show'), { target: { value: 'privacy' } });
  expect(screen.queryByText('A certified passage', { exact: false })).toBeNull();
  expect(screen.getByText('rejected upstream · no action needed')).toBeVisible();
  rerender(<Review api={api} queue={queue} decided={resource([])} highlightDoc="doc-1" />);
  await waitFor(() => expect(screen.getByLabelText('Show')).toHaveValue(''));
  expect(container.querySelector('[data-doc="doc-1"]')).toHaveClass('hl');
  fireEvent.click(within(container.querySelector('[data-doc="doc-1"]')).getByRole('button', { name: 'Accept' }));
  expect(screen.getAllByRole('button').every(b => b.disabled)).toBe(true);
  expect(api).toHaveBeenCalledWith('/steward/decide', { tag: 'tag-1', decision: 'accept' });
  await act(async () => finish({ decision: 'accept', superseded: 'suggestion' }));
  expect(screen.getAllByRole('button').every(b => !b.disabled)).toBe(true);
});

test.each([
  [{ data: null, loading: true }, 'Loading.', 'Loading past decisions…'],
  [resource([]), 'Nothing waiting.', 'No readable suggested-link decisions yet.'],
  [resource({ error: 'Queue unavailable <script>' }), 'Queue unavailable <script>', 'Queue unavailable <script>'],
])('Review has explicit loading, empty and failed resources: %j', (state, pending, past) => {
  const { container } = render(<Review api={vi.fn()} queue={state} decided={state} />);
  expect(container.querySelector('#queue')).toHaveTextContent(pending);
  expect(container.querySelector('#decided')).toHaveTextContent(past);
  expect(container.querySelector('script')).toBeNull();
});

test('Review history groups missing release identities and gives honest missing audit fields', () => {
  render(<Review api={vi.fn()} queue={resource([])} decided={resource([{ doc: 'D1', term: 'Revenue', decision: 'accept' }, { doc: 'D2', term: 'Revenue', decision: 'reject', role: 'steward' }])} />);
  expect(screen.getAllByText('Release not recorded')).toHaveLength(1);
  expect(screen.getByText('not recorded · steward')).toBeInTheDocument();
  expect(screen.getByText('D1 ·')).toBeInTheDocument();
});

test.each([
  [{ error: 'Not permitted: cannot evaluate' }, 'Not permitted: cannot evaluate'],
  [{ error: 'The service is unavailable right now.' }, 'The service is unavailable right now.'],
  [{ graph: {} }, 'Done.'],
])('Quality runs without an impersonated principal and recovers from failures: %j', async (result, message) => {
  let finish;
  const api = vi.fn(() => new Promise(resolve => { finish = resolve; }));
  const { container } = render(<Quality api={api} me={me} units={['leasing', 'retail']} />);
  expect(screen.getByText('No A/B results yet.')).toBeVisible();
  expect(screen.getByText('No release baseline yet.')).toBeVisible();
  expect(screen.getByText('Not checked')).toBeVisible();
  fireEvent.click(screen.getByRole('button', { name: 'Run the golden set' }));
  expect(screen.getByRole('button', { name: 'Running…' })).toBeDisabled();
  expect(screen.getByRole('progressbar')).toBeVisible();
  expect(api).toHaveBeenCalledExactlyOnceWith('/eval', null);
  await act(async () => finish(result));
  expect(container.querySelector('#evalnote')).toHaveTextContent(message);
  expect(screen.getByRole('button', { name: 'Run the golden set' })).not.toBeDisabled();
  expect(container.querySelector('#evalprogress')).not.toBeVisible();
  if (result.error) expect(container.querySelector('#evalout')).toBeEmptyDOMElement();
  else {
    expect(container.querySelector('#evalout')).toHaveTextContent('reads leasing');
    expect(container.querySelector('#evalout')).toHaveTextContent('refusal counts as a miss');
    expect(within(container.querySelector('#evalout')).getAllByText('0/0')).toHaveLength(4);
  }
});

test.each([[[]], [undefined]])('Quality accurately labels a scorer with no readable units: %j', async reads => {
  render(<Quality api={vi.fn(async () => ({ graph: {} }))} me={{ id: me.id, reads }} units={['leasing']} ab={{ error: 'History unavailable' }} onboard={{ candidate: { gate: { passed: false, run_id: 'failed-check' } }, eval_pre: { report: 'Margin fell' } }} />);
  expect(screen.getByRole('alert')).toHaveTextContent('History unavailable');
  expect(screen.getByText('Failed · failed-check')).toBeVisible();
  expect(screen.getByText('Margin fell')).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: 'Run the golden set' }));
  await waitFor(() => expect(screen.getByText(/Scored as/)).toHaveTextContent('reads no unit'));
});

test('Quality keeps plural repeat counts and refuses external report links', () => {
  const { container } = render(<Quality api={vi.fn()} me={me} units={[]} ab={{ runs: [{ run_id: 'r', principal: me.id, repeats: 2, arms: [{ arm: 'graph', mode: 'rules' }], report: 'https://outside.invalid/' }], baseline: { score: 1 } }} />);
  expect(screen.getByText(/Latest run/)).toHaveTextContent('2 repeats: graph (rules)');
  expect(container.querySelector('#ab a')).toBeNull();
  expect(screen.getByText('Release baseline')).toBeVisible();
});

test.each([
  [{ data: null, loading: true }, 'Loading units…'],
  [resource({ writable: false, why: 'Read only', units: [] }), 'No units yet.'],
  [resource({ error: 'Status unavailable' }), 'Status unavailable'],
])('Onboard keeps empty, loading and failed status explicit: %j', (state, text) => {
  const { container } = render(<Onboard api={vi.fn()} resource={state} me={me} />);
  expect(container.querySelector('#onboard')).toHaveTextContent(text);
  expect(screen.getByText('No candidate yet.')).toBeVisible();
  expect(screen.getByText('No releases yet.')).toBeVisible();
  expect(container.querySelector('[data-act]')).toBeNull();
});

test.each([
  ['checks', 'running', 'Checks running'], ['checks', 'done', 'Checks passed · 2 of 2 steps'],
  ['sign-off', 'done', 'Signed off by steward'], ['release', 'done', 'Released: the candidate is live'],
  ['improve', 'done', 'Proposal drafted · 2 of 2 steps'], ['rollback', 'done', 'Rollback done'],
  ['checks', 'failed', 'Checks failed: Gate refused'],
])('Onboard reports %s jobs in %s state', (step, state, text) => {
  const data = { writable: true, units: [], job: { id: 'job', step, state, by: 'steward', message: state === 'failed' ? 'Gate refused' : '', started_at: '2026-11-01T12:00:00Z', finished_at: '2026-11-01T12:01:05Z', hash: 'candidate-hash', report: '/ab/run-1/report.html', progress: [{ n: 2, of: 2, name: 'Gate', state: 'done' }], gate: { lines: ['Held-out passed'] } } };
  const { container } = render(<Onboard api={vi.fn()} resource={resource(data)} me={me} />);
  expect(container.querySelector('#ob-job')).toHaveTextContent(text);
  expect(screen.getByRole('list', { name: 'Job steps' })).toHaveTextContent('2 of 2 · Gate · done');
  expect(container.querySelector('#ob-gate')).toHaveTextContent('Held-out passed');
  expect(screen.getByRole('link', { name: 'report' })).toHaveAttribute('href', '/api/context/ab/run-1/report.html');
  if (state !== 'running') expect(container.querySelector('#ob-job')).toHaveTextContent('took 1 min 5 s');
});

test('Onboard improvement comparisons keep held-out regressions and item evidence visible', () => {
  const data = { writable: true, can: {}, candidate: { hash: 'h', released: true, loaded: false, signed_off: { principal: 'steward', at: 'today' } }, proposal: {
    id: 'p', base_version: 'one', next_version: 'two', report: '/ab/run-1/report.html',
    ab: { learnable: { sets: { golden: { before: 1, after: 2, of: 3 } } }, heldout: { note: 'judge only', sets: { unseen: { before: 3, after: 2, of: 3, fell: true } } } },
    items: [
      { id: 'q', kind: 'question', detail: { question: 'Revenue?', figure: 0, sql: 'SELECT 0' }, made_by: 'model', model: 'offline', accepted_by: 'business', accepted_name: 'Business', certified_by: 'steward', certified_name: 'Steward' },
      { id: 'm', kind: 'mapping', detail: { relation: 'equivalent', iri: 'urn:example:revenue', standard: 'Example', standard_version: '1' }, made_by: 'rules' },
      { id: 'p', kind: 'policy', detail: { column: 'private', family: 'restricted', relation: 'matches', iri: 'urn:example:restricted' } },
      { id: 'a', kind: 'alias', change: { kind: 'added' }, detail: { phrase: 'takings', term_name: 'Revenue', term: 'revenue', evidence_column: 'amount', evidence: 'checked', also_answers: ['Monthly takings?'] } },
    ],
  } };
  const { container } = render(<Onboard api={vi.fn()} resource={resource(data)} me={me} />);
  const proposal = container.querySelector('#ob-proposal');
  for (const text of ['pack one → two', 'held-out fell', 'golden', 'unseen', 'drafted by the model offline', 'drafted by rules', 'accepted by Business', 'certified by Steward', 'SELECT 0', 'urn:example:revenue', 'restrict', 'missing alias', 'would also answer: Monthly takings?']) expect(proposal).toHaveTextContent(text);
  expect(screen.getByRole('link', { name: 'candidate A/B report' })).toHaveAttribute('href', '/api/context/ab/run-1/report.html');
});

test('Onboard reference figures keep develop number formatting', () => {
  const items = [1062980, 10.5, 3].map((figure, i) => ({ id: `reference-${i}`, kind: 'question',
    detail: { question: 'Revenue?', figure, sql: `SELECT ${figure}` },
  }));
  const data = { writable: true, can: {}, units: [{ unit: 'leasing', items }] };
  const { container } = render(<Onboard api={vi.fn()} resource={resource(data)} me={me} />);
  expect([...container.querySelectorAll('.item p b')].map(figure => figure.textContent)).toEqual(['1,062,980', '10.5', '3']);
});

test.each([
  ['table-added', undefined, undefined, 'Table u.t added'], ['table-removed', undefined, undefined, 'Table u.t removed'],
  ['column-added', undefined, { type: 'INTEGER' }, 'Column u.t.x added (INTEGER)'],
  ['column-removed', 'TEXT', undefined, 'Column u.t.x removed (was TEXT)'],
  ['column-retyped', 'TEXT', 'INTEGER', 'Column u.t.x retyped from TEXT to INTEGER'],
  ['column-moved', 1, 2, 'Column u.t.x moved from position 1 to 2'],
  ['unknown', null, null, 'Change to u.t: unknown'],
])('schema change descriptions preserve %s evidence', (change, was, now, text) => {
  expect(changeLines({ lock: { changes: [{ source: 'u', table: 't', column: 'x', change, was, now }] } })).toEqual([text]);
});

test('schema changes preserve reordered foreign keys and caller-supplied descriptions', () => {
  const a = { columns: ['a'], ref_table: 'one', ref_columns: ['id'] }, b = { columns: ['b'], ref_table: 'two', ref_columns: ['id'] };
  expect(changeLines({ lock: { changes: [{ source: 'u', table: 't', change: 'key-changed', was: { foreign_keys: [a, b] }, now: { foreign_keys: [b, a] } }] } })[0]).toContain('Foreign keys of u.t reordered');
  expect(changeLines({ inputs: { ignored: ['duplicate'] }, estate: { rebuilt: false, why: 'unchanged' } }, ['Authored description'])).toEqual(['Authored description', 'Estate not rebuilt: the fixture files are unchanged']);
});

test('Onboard preserves sub-graph progress, update evidence, requests and document provenance', () => {
  const data = { writable: true, can: { run: true }, units: [
    { unit: 'leasing', label: 'Asset finance', version: 2, status: 'Ready (ROWS=small)',
      items: [{ id: 'draft', state: 'draft', made_by: 'model' }], golden: { client: 2, drafted: 1 },
      progress: { physical: { items: 3, accepted: 2, certified: 1 } },
      update: { version: 2, changes: { changed: ['measure'], added: ['alias'], removed: ['old'], details: { measure: ['Updated amount'], alias: ['New synonym'] } } },
      requests: [{ ask: 'Review amount (ROWS=small)', source: 'steward' }],
      history: [{ step: 'accept', by: 'business', at: 'today', version: 1, detail: { passed: true } }, { step: 'draft', by: 'engineer', at: 'yesterday', version: 1 }],
      documents: [{ title: 'M365 policy', path: 'm365', tier: 'gold' }, { title: 'Graded policy', path: 'graded' }, { title: 'Unused policy', path: 'other' }],
    },
    { unit: 'retail', items: [], update: { version: 3, changes: {} } },
  ], requests: [{ question_id: 'Q1', category: 'mapping', summary: 'Need mapping', proposer: 'engineer' }], notes: [{ id: 'N1', diagnosis: 'alias', why: 'Need alias' }],
  eval_pre: { passed: true, report: 'Margin passed' }, eval_post: { message: 'Release evaluated' } };
  const { container } = render(<Onboard api={vi.fn()} resource={resource(data)} me={me} />);
  for (const text of ['What changed in v2', 'changed · measure: Updated amount', 'added · alias: New synonym', 'removed · old:', 'Nothing to review: only rows changed.',
    'Review amount steward', '2 from client · 1 drafted', 'M365 policy · certified by M365 · gold tier', 'Graded policy · graded here', 'Unused policy · not used',
    'No history yet.', 'No reference documents supplied.', 'request to the platform team', 'Need mapping', 'note to the steward', 'Need alias', 'Margin passed', 'Release evaluated']) expect(container.querySelector('#onboard')).toHaveTextContent(text);
  expect(container.querySelector('#onboard')).not.toHaveTextContent('ROWS=');
  const progress = container.querySelector('table.progress');
  expect(progress).toHaveTextContent('Physical321');
  expect(progress.querySelectorAll('tbody tr')).toHaveLength(6);
  expect(container.querySelector('[data-act="run"]')).toBeTruthy();
});

test('Onboard renders direct comparison tables and does not offer unavailable historical packs', () => {
  const data = { writable: true, can: { release: true }, units: [], proposal: { id: 'p', items: [], heldout_fell: true,
    version: { from: 'one', to: 'two' }, learnable_table: [{ set: 'golden', before_passed: 1, after_passed: 2, of: 3 }],
    heldout_table: [{ set: 'unseen', before_passed: 3, after_passed: 2, of: 3 }] },
    versions: [{ current: true, model_hash: 'current', scores: { learnable: { passed: 2, total: 3 } } }, { model_hash: 'lost', kept_pack: false }, {}] };
  const { container } = render(<Onboard api={vi.fn()} resource={resource(data)} me={me} />);
  expect(container.querySelector('#ob-proposal')).toHaveTextContent('held-out (judge only, never read by the job)');
  expect(container.querySelector('#ob-versions')).toHaveTextContent('2/3');
  expect(container.querySelector('#ob-versions')).toHaveTextContent('no kept pack');
  expect(container.querySelector('#ob-versions')).toHaveTextContent('Unnamed release');
  expect(container.querySelector('[data-release-hash="lost"]')).toBeNull();
});
