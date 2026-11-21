import React from 'react';
import { act, fireEvent, render, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, expect, test, vi } from 'vitest';
import ContextScreens from './ContextScreens';

const person = (id, processes) => ({ id, processes, reads: ['leasing'], ...(processes.includes('querying') ? { cerebro_roles: ['business'] } : {}) });
const bootstrap = (who, mode = { released: true }) => ({
  person: who, mode, build: { version: 'test' },
  profile: { profile: 'meridian', client: 'meridian', units: ['leasing'] },
  presets: { presets: { graph: { arm: 'graph' } }, default: 'graph' },
});
const engineer = { ...person('engineer@example.com', ['drafting', 'building', 'querying']), cerebro_roles: ['engineer'] };
const business = person('business@example.com', ['accepting', 'querying']);
const unit = { unit: 'leasing', label: 'Asset Finance', items: [] };
const onboard = (can = {}, extra = {}) => ({ writable: true, can, units: [unit], ...extra });

let current;
let fetch;
let scrollIntoView;
beforeEach(() => {
  scrollIntoView = Object.getOwnPropertyDescriptor(Element.prototype, 'scrollIntoView');
  Object.defineProperty(Element.prototype, 'scrollIntoView', { configurable: true, value: vi.fn() });
  const media = new EventTarget();
  media.matches = false;
  vi.stubGlobal('matchMedia', vi.fn(() => media));
  current = onboard();
  fetch = vi.fn(async (url) => {
    const body = {
      '/api/context/onboard': current,
      '/api/context/golden': { sets: [] },
      '/api/context/ab': { runs: [] },
      '/api/context/versions': {},
    }[url];
    if (body === undefined) throw new Error(`Unexpected Context call: ${url}`);
    return { ok: true, json: async () => body };
  });
  vi.stubGlobal('fetch', fetch);
});
afterEach(() => {
  vi.unstubAllGlobals(); vi.restoreAllMocks();
  if (scrollIntoView) Object.defineProperty(Element.prototype, 'scrollIntoView', scrollIntoView);
  else delete Element.prototype.scrollIntoView;
});

test('the answer context selects the Map term ahead of the first parsed term', async () => {
  const resources = fetch.getMockImplementation();
  const question = 'AUM for household 5001 at 31 Mar 2025';
  const context = { id: 'aum', measure: 'wealth.assets-under-management', fact: 'wealth.AUM_MNTH_END_FACT_1',
    label: 'Assets under management', joins: [], filters: [], row_selectors: [] };
  fetch.mockImplementation(async (url, init) => {
    if (url === '/api/context/ask?background=true') return { ok: true, json: async () => ({
      question, principal: engineer.id, arm: 'graph', behaviour: 'figure', figure: 7641422.24,
      unit: 'wealth', column: 'aum_month_end.aum_usd', context: { term: 'Assets Under Management', context: 'aum' },
      trace: [{ step: 'parse', term: 'Household' }], versions: {}, run_trace: {},
    }) };
    if (url === '/api/context/map/Assets%20Under%20Management') return { ok: true, json: async () => ({ contexts: [context] }) };
    return resources(url, init);
  });
  const opening = bootstrap(engineer);
  opening.presets.presets.graph.paths = ['ask'];
  const { container } = render(<ContextScreens bootstrap={opening} />);
  await drawn(container, engineer);
  fireEvent.click(container.querySelector('#nav-ask'));
  fireEvent.change(container.querySelector('#q'), { target: { value: question } });
  fireEvent.click(container.querySelector('#ask'));
  await waitFor(() => expect(container.querySelector('#tab-map')).toHaveTextContent('Resolves to the context aum'));
  expect(container.querySelector('#tab-map')).toHaveTextContent('wealth.assets-under-management over wealth.AUM_MNTH_END_FACT_1');
  expect(fetch.mock.calls.filter(([url]) => url.includes('/map/')).map(([url]) => url)).toEqual([
    '/api/context/map/Assets%20Under%20Management',
  ]);
});

async function drawn(root, who) {
  await act(async () => {});
  await waitFor(() => {
    expect(root.querySelector('#onboard')).toHaveAttribute('data-drawn', who.id);
    expect(root.querySelector('#onboard')).not.toHaveAttribute('aria-busy');
  });
}

test('Answer settings takes its arms, default and routes from the API bootstrap presets', async () => {
  const resources = fetch.getMockImplementation();
  fetch.mockImplementation(async (url, init) => ['/api/context/answer', '/api/context/agent'].includes(url)
    ? { ok: true, json: async () => ({ behaviour: 'abstain', reason: 'No matching term', principal: engineer.id }) }
    : resources(url, init));
  const opening = bootstrap(engineer);
  opening.presets = { default: 'future_control', presets: { future_control: { arm: 'future_control', paths: ['answer'] } },
    agent_arms: { future_reference: { arm: 'future_reference', side: 'graph on', mode: 'mock' } } };
  const { container, rerender } = render(<ContextScreens bootstrap={opening} />);
  await drawn(container, engineer);
  fireEvent.click(container.querySelector('#nav-ask'));
  expect(within(container.querySelector('#settings')).queryByLabelText('Answered by')).toBeNull();
  const arms = () => {
    if (!container.querySelector('#ask-settings-panel')) fireEvent.click(container.querySelector('#ask-settings'));
    return within(container.querySelector('#ask-settings-panel')).getByLabelText('Answered by');
  };
  let select = arms();
  expect([...select.options].map(option => option.value)).toEqual(['future_control', 'future_reference']);
  expect(select.value).toBe('future_control');
  fireEvent.change(container.querySelector('#q'), { target: { value: 'Revenue?' } });
  fireEvent.click(container.querySelector('#ask'));
  await waitFor(() => expect(container.querySelector('.answer-card')).toBeTruthy());
  expect(fetch).toHaveBeenCalledWith('/api/context/answer', expect.objectContaining({ body: JSON.stringify({ question: 'Revenue?', purpose: 'analysis', arm: 'future_control' }) }));
  select = arms();
  fireEvent.change(select, { target: { value: 'future_reference' } });
  fireEvent.click(container.querySelector('#ask'));
  await waitFor(() => expect(container.querySelectorAll('.answer-card')).toHaveLength(2));
  expect(fetch).toHaveBeenCalledWith('/api/context/agent', expect.objectContaining({ body: JSON.stringify({ question: 'Revenue?', purpose: 'analysis', arm: 'future_reference' }) }));
  rerender(<ContextScreens bootstrap={{ ...opening, presets: { default: 'new_control', presets: { new_control: { arm: 'new_control', paths: ['answer'] } } } }} />);
  await drawn(container, engineer);
  expect([...arms().options].map(option => option.value)).toEqual(['new_control']);
  expect(arms().value).toBe('new_control');
});

test('the gate A/B report is available in the engineer Quality workspace after switching from an analyst', async () => {
  const resources = fetch.getMockImplementation();
  fetch.mockImplementation(async (url, init) => url === '/api/context/ab'
    ? { ok: true, json: async () => ({ runs: [{ run_id: 'gate-run', principal: 'analyst@example.com', repeats: 1, arms: [], report: '/ab/gate-run/report.html' }] }) }
    : resources(url, init));
  const { container, rerender } = render(<ContextScreens bootstrap={bootstrap(person('analyst@example.com', ['querying']))} />);
  await act(async () => {});
  expect(container.querySelector('#nav-quality')).toBeNull();
  expect(container.querySelector('#ab')).toBeNull();
  rerender(<ContextScreens bootstrap={bootstrap(engineer)} />);
  await drawn(container, engineer);
  fireEvent.click(container.querySelector('#nav-quality'));
  const panel = container.querySelector('#ab');
  await waitFor(() => expect(panel).toHaveTextContent('gate-run'));
  expect(panel).toBeVisible();
  expect(within(panel).getByRole('link', { name: 'Open its report' })).toHaveAttribute('href', '/api/context/ab/gate-run/report.html');
});

test('switching from engineer to business keeps the waiting hint and its Open action', async () => {
  current = onboard({ draft: true, create: true });
  const { container, rerender } = render(<ContextScreens bootstrap={bootstrap(engineer)} />);
  await drawn(container, engineer);
  expect(container.querySelector('#next-step')).toHaveTextContent("Waiting ona knowledge engineer to draft Asset Finance's items.");
  current = onboard({ accept: true });
  rerender(<ContextScreens bootstrap={bootstrap(business)} />);
  await drawn(container, business);
  const hint = container.querySelector('#next-step');
  expect(hint.hidden).toBe(false);
  expect(hint).toHaveTextContent("Waiting ona knowledge engineer to draft Asset Finance's items.");
  expect(container.querySelector('#ob-status button[data-act="draft"]')).toBeNull();
  expect(hint.querySelector('button')).toHaveAttribute('data-go', 'onboard');
  expect(hint.querySelector('button')).toHaveAttribute('data-unit', 'leasing');
  expect(fetch.mock.calls.every(([url]) => url.startsWith('/api/context/'))).toBe(true);
});

test.each([
  ['unit action', onboard({ accept: true }, { units: [{ ...unit, items: [{ id: 'rent' }] }] }), '#ob-status .unit-card', 'summary', false],
  ['unit progress', onboard(), '#ob-status .unit-card', 'summary', true],
  ['proposal action', onboard({ accept: true }, { units: [], proposal: { id: 'p1', items: [{ id: 'alias' }] } }), '#ob-proposal-card', 'summary', false],
])('next-step Open reveals and focuses the %s after changing workspaces', async (_, data, selector, focus, reduced) => {
  current = data;
  matchMedia('(prefers-reduced-motion: reduce)').matches = reduced;
  const { container } = render(<ContextScreens bootstrap={bootstrap(business)} />);
  await drawn(container, business);
  fireEvent.click(container.querySelector('#nav-ask'));
  const target = container.querySelector(selector), scroll = vi.fn();
  target.scrollIntoView = scroll;
  const button = container.querySelector('#next-step button');
  fireEvent.click(button);
  expect(container.querySelector('#view-onboard')).toBeVisible();
  expect(scroll).toHaveBeenCalledWith({ block: 'nearest', behavior: reduced ? 'auto' : 'smooth' });
  expect(target.style.scrollMarginTop).toBe('12px');
  expect(target.querySelector(focus) || container.querySelector('#nav-onboard')).toHaveFocus();
  fireEvent.click(button);
  expect(scroll).toHaveBeenCalledTimes(2);
});

test('next-step navigation leaves room for the sticky global bar', async () => {
  current = onboard();
  const { container } = render(<><header className="gb-bar" style={{ position: 'sticky' }} /><ContextScreens bootstrap={bootstrap(business)} /></>);
  await drawn(container, business);
  Object.defineProperty(container.querySelector('header'), 'offsetHeight', { value: 60 });
  fireEvent.click(container.querySelector('#next-step button'));
  expect(container.querySelector('#ob-status .unit-card').style.scrollMarginTop).toBe('72px');
});

test('opening responses render and clear escaped release and candidate changes without page globals', async () => {
  current = onboard({ accept: true }, { changes: {
    inputs: { ignored: ['clients/c/<img src=x>'], suggestion: 'inputs:\n  catalogue: <script>bad()</script>', upload: { sha256: 'a'.repeat(64) } },
    estate: { rebuilt: true, why: 'reader-changed', unread_documents: [
      { path: 'docs/certified/<img src=x>.unknown', evidence: "content not read: unknown isn't supported by <reader>" },
    ] },
    people: { reloaded: true, added: ['<img src=x>'] }, rechecked: true,
  }, change_lines: null });
  const message = 'nothing released for <img src=x onerror=alert(1)>: release first';
  const { container, rerender } = render(<ContextScreens bootstrap={bootstrap(business, { released: false, message })} />);
  await drawn(container, business);
  const banner = container.querySelector('#release-banner');
  expect(banner.hidden).toBe(false);
  expect(banner.textContent).toBe(message);
  const changes = container.querySelector('#ob-changes');
  expect(changes).toHaveTextContent('Ignored: clients/c/<img src=x>');
  expect(changes).toHaveTextContent('Upload aaaaaaaaaaaa');
  expect(changes).toHaveTextContent('Checked again: the last check did not pass');
  expect(changes).not.toHaveTextContent('People reloaded:');
  expect(changes).toHaveTextContent('Estate rebuilt: the document reader changed');
  expect(changes).toHaveTextContent("docs/certified/<img src=x>.unknown: content not read: unknown isn't supported by <reader>");
  expect(changes.querySelector('pre').textContent).toContain('\n  catalogue: <script>');
  expect(container.querySelector('#release-banner img, #ob-changes img, #ob-changes script')).toBeNull();
  current = onboard({ accept: true }, { changes: null });
  rerender(<ContextScreens bootstrap={bootstrap(business)} />);
  await drawn(container, business);
  expect(container.querySelector('#release-banner').hidden).toBe(true);
  expect(container.querySelector('#ob-changes').textContent).toBe('');
});

test('job polling exposes the completed job only after its redraw settles', async () => {
  const job = { id: 'new-check', step: 'checks', state: 'running' };
  current = onboard({ run: true }, { job });
  const { container, unmount } = render(<ContextScreens bootstrap={bootstrap(engineer)} />);
  await drawn(container, engineer);
  expect(container.querySelector('#ob-job')).toHaveAttribute('data-job-id', job.id);
  expect(container.querySelector('#ob-job')).toHaveAttribute('data-state', 'running');
  let finish;
  const pending = new Promise(resolve => { finish = resolve; });
  fetch.mockImplementationOnce(() => pending);
  await waitFor(() => expect(container.querySelector('#onboard')).toHaveAttribute('aria-busy', 'true'), { timeout: 3000 });
  expect(container.querySelector('#ob-job')).toHaveAttribute('data-state', 'running');
  await act(async () => finish({ ok: true, json: async () => onboard({ run: true }, { job: { ...job, state: 'done' } }) }));
  await drawn(container, engineer);
  expect(container.querySelector('#ob-job')).toHaveAttribute('data-state', 'done');
  expect(container.querySelector('#ob-job')).toHaveTextContent('Checks passed');
  unmount();
});

test.each([
  ['reader', ['querying'], ['ask', 'docs'], false],
  ['steward', ['querying', 'certification'], ['ask', 'docs', 'onboard'], false],
  ['engineer', ['querying', 'drafting', 'building'], ['ask', 'docs', 'onboard', 'quality'], true],
  ['business', ['querying', 'accepting'], ['ask', 'docs', 'onboard'], false],
])('%s sees only their tabs and collapsed tester settings', async (id, processes, views, tester) => {
  const who = person(`${id}@example.com`, processes);
  const { container } = render(<ContextScreens bootstrap={bootstrap(who)} />);
  await waitFor(() => expect(container.querySelector('.context-app')).toHaveAttribute('data-person', who.id));
  const tabs = [...container.querySelectorAll('#views button')].filter(b => !b.hidden);
  expect(tabs.map(b => b.dataset.view)).toEqual(views);
  if (!processes.includes('querying')) return;
  const settings = container.querySelector('#settings');
  expect(settings.hidden).toBe(!tester);
  expect(container.querySelector('#settings').open).toBe(false);
  if (tester) {
    fireEvent.click(container.querySelector('#nav-ask'));
    fireEvent.click(container.querySelector('#settings summary'));
    expect(container.querySelector('#settings').open).toBe(true);
    expect(within(container.querySelector('#settings')).getByLabelText('Purpose')).toBeVisible();
    expect(within(container.querySelector('#settings')).getByLabelText('Rich answers')).toBeVisible();
  }
  fireEvent.click(container.querySelector('#nav-ask'));
  fireEvent.click(container.querySelector('#ask-settings'));
  const picker = container.querySelector('#ask-settings-panel');
  expect(within(picker).getByRole('group', { name: 'Runtimes' })).toBeVisible();
  if (tester) expect(within(picker).getByLabelText('Answered by')).toBeVisible();
  else expect(within(picker).queryByLabelText('Answered by')).toBeNull();
  expect(container.textContent).not.toContain('Local demo. Synthetic estate');
  expect(container.textContent).not.toContain('make ab');
  expect(container.querySelectorAll('input[type="file"], #ob-add, .rows-form')).toHaveLength(0);
  expect(fetch.mock.calls.some(([url]) => url.includes('/steward/queue'))).toBe(false);
});

test('Onboard remains read-only on files even with every authoring permission', async () => {
  current = onboard({ create: true, draft: true, run: true }, { units: [
    unit, { ...unit, unit: 'second', live: true, needs: 'rows', items: [{ id: 'x', state: 'draft' }] },
  ] });
  const { container } = render(<ContextScreens bootstrap={bootstrap(engineer)} />);
  await drawn(container, engineer);
  expect(container.querySelectorAll('input[type="file"], input[type="text"]:not(#q), textarea, [data-act="draft"], [data-act="rows"], [data-act="update"], #ob-create')).toHaveLength(0);
  expect(container.querySelector('#onboard')).toHaveTextContent('kge onboard');
  expect(container.querySelector('#onboard')).toHaveTextContent('land-and-draft, build-and-gate and improve');
  expect(fetch.mock.calls.some(([url]) => /\/onboard\/(source|rows|tables|draft)$/.test(url))).toBe(false);
});

test('Onboard keeps release-audit history and escapes readable decisions', async () => {
  const unsafe = '<img src=x onerror=alert(1)>';
  current = onboard({}, { versions: [
    { release: `release-one${unsafe}`, released_at: '2026-11-01T12:00:00Z', audit: {
      [`unit${unsafe}`]: { [`item${unsafe}`]: {
        accepted: { by: `business${unsafe}`, as: 'business', on: '2026-10-30' },
        certified: { by: `steward${unsafe}`, as: 'steward', on: '2026-10-31' },
      } }, private: { secret_item: { certified: { by: 'private_person', as: 'private_role', on: '2026-10-31' } } },
    } },
    { release: 'release-two', released_at: '2026-11-02T12:00:00Z', audit: {} },
  ] });
  const steward = { ...person('steward@example.com', ['querying', 'certification']), reads: [`unit${unsafe}`] };
  const { container } = render(<ContextScreens bootstrap={bootstrap(steward)} />);
  await drawn(container, steward);
  const decided = container.querySelector('#ob-versions');
  expect(container.querySelector('#decided')).toBeNull();
  expect(decided.querySelectorAll('.release-decisions')).toHaveLength(2);
  expect(decided).toHaveTextContent(`release-one${unsafe}`);
  expect(decided).toHaveTextContent(`business${unsafe} · business`);
  expect(decided).toHaveTextContent('accepted');
  expect(decided).toHaveTextContent('certified');
  expect(decided).toHaveTextContent('2026-10-31');
  expect(decided).toHaveTextContent('No readable item decisions recorded for this release.');
  expect(decided.querySelector('img, script')).toBeNull();
  expect(decided).not.toHaveTextContent('secret_item');
  expect(decided).not.toHaveTextContent('private_person');
  expect(fetch.mock.calls.filter(([url]) => url === '/api/context/versions')).toHaveLength(1);
  expect(fetch.mock.calls.filter(([url]) => url === '/api/context/onboard')).toHaveLength(1);
});

test('evaluation controls and open Answer settings follow identity changes', async () => {
  const reader = person('reader@example.com', ['querying']);
  const { container, rerender } = render(<ContextScreens bootstrap={bootstrap(engineer)} />);
  await drawn(container, engineer);
  const settings = container.querySelector('#settings');
  expect(container.querySelector('#settings').open).toBe(false);
  fireEvent.click(container.querySelector('#nav-ask'));
  fireEvent.click(container.querySelector('#settings summary'));
  expect(container.querySelector('#settings').open).toBe(true);
  for (const who of [reader, engineer, { ...engineer, processes: ['querying'] }]) {
    rerender(<ContextScreens bootstrap={bootstrap(who)} />);
    await act(async () => {});
    const allowed = who.processes.includes('building');
    await waitFor(() => {
      expect(container.querySelector('.context-app')).toHaveAttribute('data-person', who.id);
      expect(!!container.querySelector('#runeval')).toBe(allowed);
      expect(!!container.querySelector('#nav-quality')).toBe(allowed);
      expect(container.querySelector('#settings').hidden).toBe(!allowed);
      expect(container.querySelector('#settings').open).toBe(false);
    });
    if (allowed) {
      fireEvent.click(container.querySelector('#nav-ask'));
      fireEvent.click(container.querySelector('#settings summary'));
      expect(container.querySelector('#settings').open).toBe(true);
    }
  }
  expect(fetch.mock.calls.some(([url]) => url.endsWith('/eval'))).toBe(false);
});

test('Onboard history keeps each version, release identity, current marker and held-out score together', async () => {
  current = onboard({}, { versions: [
    { release: 'release-before', version: '1.6.0', model_hash: 'hash-before', scores: { heldout: { passed: 12, total: 19 } } },
    { release: 'release-current', version: '1.7.0', model_hash: 'hash-current', current: true, scores: { heldout: { passed: 13, of: 19 } } },
    { release: 'release-unsafe', version: '<img src=x onerror=alert(1)>', model_hash: 'hash-unsafe' },
  ] });
  const { container } = render(<ContextScreens bootstrap={bootstrap(engineer)} />);
  await drawn(container, engineer);
  const history = container.querySelector('#ob-versions');
  expect(within(history).getByRole('heading', { name: 'History · releases, sign-offs and rollbacks' })).toBeVisible();
  const rows = within(history.querySelector('table')).getAllByRole('row');
  for (const [version, release, score, isCurrent] of [
    ['1.6.0', 'release-before', '12/19', false], ['1.7.0', 'release-current', '13/19', true],
  ]) {
    const row = rows.find(row => within(row).queryByText(version, { exact: true }));
    expect(row).toHaveTextContent(release);
    expect(within(row).getAllByRole('cell')[4]).toHaveTextContent(score);
    expect(within(row).queryByText('current', { exact: true }) !== null).toBe(isCurrent);
  }
  expect(history).toHaveTextContent('<img src=x onerror=alert(1)>');
  expect(history.querySelector('img')).toBeNull();
});

test('read-only Onboard shows units, job steps and result, candidate checks, gate and release history', async () => {
  const unsafe = '<script>bad()</script>';
  current = onboard({ run: true }, {
    serving: 'live-hash',
    candidate: { hash: 'candidate-hash', loaded: true, gate: { passed: false, run_id: `run${unsafe}` }, signed_off: { principal: `steward${unsafe}`, at: '2026-11-01T12:00:00Z' } },
    job: { id: 'checks-1', step: 'checks', state: 'failed', by: `engineer${unsafe}`, started_at: '2026-11-01T10:00:00Z', finished_at: '2026-11-01T10:01:00Z', message: `Gate failed${unsafe}`, progress: [{ n: 1, of: 2, name: `Load${unsafe}`, state: 'done' }], gate: { lines: [`Baseline comparison failed${unsafe}`] } },
    changes: { reloaded: true },
    units: [{ ...unit, status: 'Awaiting checks', history: [{ step: 'sign-off', by: `steward${unsafe}`, at: '2026-11-01T12:00:00Z', version: 1 }] }],
    versions: [{ release: 'release-2', model_hash: 'hash-2', version: '1.2.0', released_at: '2026-11-02T13:14:15Z', signed_off_by: `steward${unsafe}`, rollback_of: `hash-1${unsafe}`, note: 'Rollback' }],
  });
  const { container } = render(<ContextScreens bootstrap={bootstrap(engineer)} />);
  await drawn(container, engineer);
  const panel = container.querySelector('#onboard');
  expect(panel).toHaveTextContent('Units and status');
  expect(panel).toHaveTextContent('Asset Finance');
  expect(panel).toHaveTextContent('Awaiting checks');
  expect(panel).toHaveTextContent(`1 of 2 · Load${unsafe} · done`);
  expect(panel).toHaveTextContent(`Gate failed${unsafe}`);
  expect(panel).toHaveTextContent(`Baseline comparison failed${unsafe}`);
  expect(panel).toHaveTextContent('candidate-hash');
  expect(panel).toHaveTextContent('live-hash');
  expect(panel).toHaveTextContent('Checks and release gateFailed');
  expect(panel).toHaveTextContent('Reloaded the same model');
  expect(panel).toHaveTextContent('release-2');
  expect(panel).toHaveTextContent('2026-11-02T13:14:15Z');
  expect(panel).toHaveTextContent(`Rolled back from hash-1${unsafe}`);
  expect(panel.querySelector('script')).toBeNull();
});

test.each([
  ['checks', ['building'], 'run', { hash: 'h', loaded: true }, { items: [{ id: 'one', accepted_by: 'business', certified_by: 'steward' }] }, '/onboard/run', { step: 'checks' }],
  ['release', ['building'], 'release', { hash: 'h', loaded: true, signed_off: { principal: 'steward' } }, { items: [{ id: 'one', accepted_by: 'business', certified_by: 'steward' }], history: [{ step: 'onboard', detail: { passed: true } }] }, '/onboard/run', { step: 'release' }],
])('%s keeps its endpoint, role permission and identity-free payload', async (_label, processes, action, candidate, unitExtra, path, body) => {
  const who = person('person@example.com', processes);
  current = onboard({ [action]: true }, { candidate, units: [{ ...unit, ...unitExtra }] });
  const resources = fetch.getMockImplementation();
  fetch.mockImplementation(async (url, init) => init?.method === 'POST' ? { ok: true, json: async () => ({}) } : resources(url, init));
  const { container, rerender } = render(<ContextScreens bootstrap={bootstrap(who)} />);
  await drawn(container, who);
  const button = container.querySelector(`[data-act="${action}"]`);
  expect(button).toBeTruthy();
  fireEvent.click(button);
  await waitFor(() => expect(fetch).toHaveBeenCalledWith('/api/context' + path, expect.objectContaining({ method: 'POST', body: JSON.stringify(body) })));
  current = onboard({}, { candidate, units: [{ ...unit, ...unitExtra }] });
  rerender(<ContextScreens bootstrap={bootstrap(who, { released: true, revision: 2 })} />);
  await drawn(container, who);
  await waitFor(() => expect(container.querySelector(`[data-act="${action}"]`)).toBeNull());
});

test('rollback and release again keep their payloads and forbidden decisions remain refused', async () => {
  const steward = person('steward@example.com', ['certification']);
  current = onboard({ rollback: true, release: true }, { versions: [
    { model_hash: 'current', current: true }, { model_hash: 'previous', kept_pack: true },
  ] });
  const resources = fetch.getMockImplementation();
  fetch.mockImplementation(async (url, init) => init?.method === 'POST'
    ? { ok: false, status: 403, json: async () => ({ detail: 'Only a steward may decide.' }) } : resources(url, init));
  const { container } = render(<ContextScreens bootstrap={bootstrap(steward)} />);
  await drawn(container, steward);
  fireEvent.click(container.querySelector('#ob-rollback'));
  await waitFor(() => expect(fetch).toHaveBeenCalledWith('/api/context/onboard/rollback', expect.objectContaining({ method: 'POST', body: '{}' })));
  await waitFor(() => expect(container.querySelector('#ob-msg')).toHaveTextContent('Not permitted: Only a steward may decide.'));
  fireEvent.click(container.querySelector('[data-release-hash="previous"]'));
  await waitFor(() => expect(fetch).toHaveBeenCalledWith('/api/context/onboard/run', expect.objectContaining({ body: JSON.stringify({ step: 'release', hash: 'previous' }) })));
});
