import React from 'react';
import { act, fireEvent, render, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, expect, test, vi } from 'vitest';
import Workspace from './Workspace';

const person = (id, processes) => ({ id, processes, reads: ['leasing'] });
const bootstrap = (who, mode = { released: true }) => ({
  person: who, mode, build: { version: 'test' },
  profile: { profile: 'meridian', client: 'meridian', units: ['leasing'] },
  presets: { presets: { graph: { arm: 'graph' } }, default: 'graph' },
});
const engineer = person('engineer@example.com', ['drafting', 'building', 'querying']);
const business = person('business@example.com', ['accepting', 'querying']);
const unit = { unit: 'leasing', label: 'Asset Finance', items: [] };
const onboard = (can = {}, extra = {}) => ({ writable: true, can, units: [unit], ...extra });

let current;
let fetch;
beforeEach(() => {
  const media = new EventTarget();
  media.matches = false;
  vi.stubGlobal('matchMedia', vi.fn(() => media));
  current = onboard();
  fetch = vi.fn(async (url) => {
    const body = {
      '/api/context/onboard': current,
      '/api/context/steward/queue': [],
      '/api/context/golden': { sets: [] },
      '/api/context/ab': { runs: [] },
      '/api/context/versions': {},
    }[url];
    if (body === undefined) throw new Error(`Unexpected Context call: ${url}`);
    return { ok: true, json: async () => body };
  });
  vi.stubGlobal('fetch', fetch);
});
afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks(); });

async function drawn(root, who) {
  await act(async () => {});
  await waitFor(() => {
    expect(root.querySelector('#onboard')).toHaveAttribute('data-drawn', who.id);
    expect(root.querySelector('#onboard')).not.toHaveAttribute('aria-busy');
  });
}

test('switching from engineer to business keeps the waiting hint and its Open action', async () => {
  current = onboard({ draft: true, create: true });
  const { container, rerender } = render(<Workspace bootstrap={bootstrap(engineer)} />);
  await drawn(container, engineer);
  expect(container.querySelector('#next-step')).toHaveTextContent('Your next step ·Draft Asset Finance.');
  current = onboard({ accept: true });
  rerender(<Workspace bootstrap={bootstrap(business)} />);
  await drawn(container, business);
  const hint = container.querySelector('#next-step');
  expect(hint.hidden).toBe(false);
  expect(hint).toHaveTextContent("Waiting ona knowledge engineer to draft Asset Finance's items.");
  expect(container.querySelector('#ob-status button[data-act="draft"]')).toBeNull();
  expect(hint.querySelector('button')).toHaveAttribute('data-go', 'onboard');
  expect(hint.querySelector('button')).toHaveAttribute('data-unit', 'leasing');
  expect(fetch.mock.calls.every(([url]) => url.startsWith('/api/context/'))).toBe(true);
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
  const { container, rerender } = render(<Workspace bootstrap={bootstrap(business, { released: false, message })} />);
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
  rerender(<Workspace bootstrap={bootstrap(business)} />);
  await drawn(container, business);
  expect(banner.hidden).toBe(true);
  expect(changes.textContent).toBe('');
});

test('job polling exposes the completed job only after its redraw settles', async () => {
  const job = { id: 'new-check', step: 'checks', state: 'running' };
  current = onboard({ run: true }, { job });
  const { container, unmount } = render(<Workspace bootstrap={bootstrap(engineer)} />);
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

test('suggested links escape evidence and show similarity; BFF decisions refresh the queue', async () => {
  const unsafe = '<img src=x onerror="window.injected=true">';
  let queue = [
    { tag: 'suggested/high', family: 'suggested_mention', value: `Fiscal Year ${unsafe}`, confidence: .81,
      similarity: .81, definition: `Reporting calendar ${unsafe}`, passage: `April to June ${unsafe}`,
      evidence: `Steward review required ${unsafe}`, doc: 'EM-005', shelf: 'certified', chunk: 'EM-005#0' },
    { tag: 'suggested/lower', family: 'suggested_mention', value: 'Assets Under Management', confidence: .70,
      similarity: .70, definition: 'Managed assets', passage: 'Assets under advice are a different metric.',
      evidence: 'Steward review required', doc: 'MD-ASSETS-UNDER-ADVICE', shelf: 'certified', chunk: 'MD-ASSETS-UNDER-ADVICE#0' },
  ];
  const decisions = [];
  const resources = fetch.getMockImplementation();
  fetch.mockImplementation(async (url, init) => {
    if (url === '/api/context/steward/queue') return { ok: true, json: async () => queue };
    if (url !== '/api/context/steward/decide') return resources(url, init);
    expect(init.method).toBe('POST');
    expect(init.headers['content-type']).toBe('application/json');
    const body = JSON.parse(init.body);
    decisions.push(body);
    queue = queue.filter(row => row.tag !== body.tag);
    return { ok: true, json: async () => ({ decision: body.decision, superseded: body.tag,
      by: body.tag + '/human', mention_created: body.decision === 'accept' }) };
  });
  const errors = [];
  const onError = event => errors.push(event.message);
  window.addEventListener('error', onError);
  try {
    const steward = person('steward@example.com', ['certification']);
    const { container } = render(<Workspace bootstrap={bootstrap(steward)} />);
    await drawn(container, steward);
    const review = container.querySelector('#queue');
    const rows = () => review.querySelectorAll('tr[data-family="suggested_mention"]');
    await waitFor(() => expect(rows()).toHaveLength(2));
    expect(container.querySelector('#view-review').hidden).toBe(false);
    expect(review.querySelector('h3')).toHaveTextContent('Suggested passage-to-term links');
    expect(container.querySelector('#queue-filter option[value="suggested_mention"]')).toHaveTextContent('Suggested passage-to-term links (2)');
    expect(within(review).getByRole('columnheader', { name: 'Similarity' })).toBeVisible();
    expect(rows()[0]).toHaveTextContent(`Possible mention of Fiscal Year ${unsafe}`);
    expect(rows()[0]).toHaveTextContent(`Reporting calendar ${unsafe}`);
    expect(rows()[0]).toHaveTextContent(`April to June ${unsafe}`);
    expect(rows()[0]).toHaveTextContent(`Steward review required ${unsafe}`);
    expect(rows()[0]).toHaveTextContent('81%');
    expect(rows()[1]).toHaveTextContent('70%');
    expect(rows()[0].querySelector('input.def')).toBeNull();
    expect(review.querySelector('img, script')).toBeNull();
    expect(window.injected).toBeUndefined();
    fireEvent.click(within(rows()[0]).getByRole('button', { name: 'Accept', exact: true }));
    await waitFor(() => expect(rows()).toHaveLength(1));
    expect(decisions).toEqual([{ tag: 'suggested/high', decision: 'accept' }]);
    expect(container.querySelector('#queuemsg')).toHaveTextContent(/recorded.*accept/);
    expect(rows()[0]).toHaveTextContent('Assets Under Management');
    fireEvent.click(within(rows()[0]).getByRole('button', { name: 'Reject', exact: true }));
    await waitFor(() => expect(review).toHaveTextContent('Nothing waiting.'));
    expect(decisions).toEqual([
      { tag: 'suggested/high', decision: 'accept' },
      { tag: 'suggested/lower', decision: 'reject' },
    ]);
    expect(container.querySelector('#queuemsg')).toHaveTextContent(/recorded.*reject/);
    expect(fetch.mock.calls.filter(([url]) => url === '/api/context/steward/queue')).toHaveLength(3);
    expect(fetch.mock.calls.every(([url]) => url.startsWith('/api/context/'))).toBe(true);
    expect(errors).toEqual([]);
  } finally {
    window.removeEventListener('error', onError);
  }
});
