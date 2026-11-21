import React from 'react';
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import ContextScreens from './ContextScreens';
import { NOTICE_MS } from './ReleaseNotice';
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const engineer = { id: 'engineer@example.com', role: 'engineer', reads: ['leasing'], processes: ['querying', 'building', 'drafting', 'improving'], cerebro_roles: ['engineer'] };
const analyst = { id: 'analyst@example.com', role: 'analyst', reads: ['leasing'], processes: ['querying'], cerebro_roles: ['analyst'] };
const profile = { profile: 'meridian', client: 'meridian', units: ['leasing'], release: { id: 'rel-7', as_of: '2026-03-31' }, artifact: '98391f7e3f4a55aa' };
const opening = (person = engineer, extra = {}) => ({ person, mode: { released: true }, profile, presets: { default: 'graph', presets: { graph: { arm: 'graph', paths: ['ask'] } } }, build: { version: 'KGE 0.11.37' }, ...extra });
const identity = { hash: 'abc', graph: 3, chunks: null, warehouses: {}, release: 'rel-7' };
const passed = (at = '2026-11-17T09:00:00Z') => ({ passed: true, compared: true, differences: [], questions: 20, message: 'eval post passed: deterministic results unchanged', identity, at });
const failed = (at = '2026-11-17T09:00:00Z') => ({ passed: false, compared: true, differences: [{ question: ['golden', 'q1'], fields: ['deterministic'] }], message: 'EVAL POST FAILED: the release stays served; run kge rollback', identity, at });
const unfinished = { passed: false, message: 'EVAL POST FAILED: evaluator did not finish; run kge rollback', identity, at: '2026-11-17T09:00:00Z' };

let versions;
const widths = [['desktop', 1280], ['phone', 375]];
const contextCss = () => readFileSync(join(dirname(fileURLToPath(import.meta.url)), 'context.css'), 'utf8');

beforeEach(() => {
  window.localStorage.clear();
  versions = {};
  vi.stubGlobal('fetch', vi.fn(async (url) => {
    const body = {
      '/api/context/onboard': { writable: true, can: {}, units: [] },
      '/api/context/golden': { sets: [] },
      '/api/context/ab': { baseline: { score: 2 }, runs: [] },
      '/api/context/versions': versions,
    }[url];
    if (body === undefined) throw new Error(`Unexpected Context call: ${url}`);
    return { ok: true, status: 200, json: async () => body };
  }));
});
afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  window.localStorage.clear();
});

function atWidth(width) {
  vi.stubGlobal('innerWidth', width);
  vi.stubGlobal('matchMedia', vi.fn((query) => {
    const max = /max-width:\s*(\d+)px/.exec(query);
    return { matches: max ? width <= Number(max[1]) : false, media: query, addEventListener() {}, removeEventListener() {} };
  }));
}

const open = (person = engineer) => render(<ContextScreens bootstrap={opening(person)} />);
const loaded = (container) => waitFor(() => expect(container.querySelector('#about-ab')).toHaveTextContent('Quality compares results with the release baseline.'));
const toast = (container) => container.querySelector('#release-eval-notice');
const redRow = (container) => container.querySelector('#release-eval-banner');
const between = (container) => {
  const bar = container.querySelector('#views-bar');
  return [...bar.parentElement.children].slice(0, [...bar.parentElement.children].indexOf(bar)).filter((node) => !node.hidden);
};

describe.each(widths)('at %s width', (_, width) => {
  beforeEach(() => atWidth(width));

  test('on a normal day nothing sits between the header and the tabs', async () => {
    const { container, unmount } = open();
    await loaded(container);
    expect(between(container)).toEqual([]);
    expect(container.querySelector('.context-toolbar')).toBeNull();
    expect(container.querySelector('#about-build')).not.toBeVisible();
    unmount();

    versions = { eval_post: passed() };
    window.localStorage.setItem('context-release-eval:engineer@example.com', JSON.stringify([identity, passed().at, passed().message]));
    const again = open();
    await waitFor(() => expect(again.container.querySelector('#about-eval')).toHaveTextContent('passed · results unchanged'));
    expect(between(again.container)).toEqual([]);
    expect(toast(again.container)).toBeNull();
  });

  test('a passed evaluation shows once per release, goes after about ten seconds, and a reload does not bring it back', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    versions = { eval_post: passed() };
    const first = open();
    await waitFor(() => expect(toast(first.container)).toBeTruthy());
    expect(toast(first.container)).toHaveTextContent('Release evaluation passed');
    expect(toast(first.container)).toHaveTextContent('Results unchanged. Details in Quality.');
    expect(toast(first.container)).not.toHaveTextContent('eval post');
    expect(toast(first.container).closest('[role="status"]')).toBeTruthy();
    expect(between(first.container)).toEqual([]);
    await act(async () => {});
    act(() => { vi.advanceTimersByTime(NOTICE_MS - 1000); });
    expect(toast(first.container)).toBeTruthy();
    act(() => { vi.advanceTimersByTime(1500); });
    expect(toast(first.container)).toBeNull();
    first.unmount();

    const reload = open();
    await loaded(reload.container);
    await waitFor(() => expect(reload.container.querySelector('#about-eval')).toBeTruthy());
    expect(toast(reload.container)).toBeNull();
    reload.unmount();

    versions = { eval_post: passed('2026-11-18T09:00:00Z') };
    const next = open();
    await waitFor(() => expect(toast(next.container)).toBeTruthy());
  });

  test('the passed notice closes sooner on ✕ and stays closed after a reload', async () => {
    versions = { eval_post: passed() };
    const first = open();
    await waitFor(() => expect(toast(first.container)).toBeTruthy());
    fireEvent.click(within(toast(first.container)).getByRole('button', { name: 'Close the release evaluation notice' }));
    expect(toast(first.container)).toBeNull();
    first.unmount();
    const reload = open();
    await waitFor(() => expect(reload.container.querySelector('#about-eval')).toBeTruthy());
    expect(toast(reload.container)).toBeNull();
  });

  test('a release without earlier results says so in plain words', async () => {
    versions = { eval_post: { passed: true, compared: false, status: 'not compared', message: 'eval post not compared: no pre-release results to compare', identity, at: 'x' } };
    const { container } = open();
    await waitFor(() => expect(toast(container)).toBeTruthy());
    expect(toast(container)).toHaveTextContent('Release evaluation passed');
    expect(toast(container)).toHaveTextContent('No earlier results to compare.');
    expect(toast(container)).not.toHaveTextContent('Results unchanged');
  });

  test('a failed evaluation stays red until closed, links to Quality, and comes back for the next release', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    versions = { eval_post: failed() };
    const first = open();
    await waitFor(() => expect(redRow(first.container)).toBeTruthy());
    const row = redRow(first.container);
    expect(row).toHaveClass('bad');
    expect(row).toHaveAttribute('role', 'alert');
    expect(row).toHaveTextContent('Release evaluation failed: answers changed after the release.');
    expect(row).not.toHaveTextContent('EVAL POST');
    expect(between(first.container)).toEqual([row]);
    act(() => { vi.advanceTimersByTime(NOTICE_MS * 3); });
    expect(redRow(first.container)).toBeTruthy();
    fireEvent.click(within(row).getByRole('link', { name: 'Open in Quality' }));
    expect(screen.getByRole('tab', { name: 'Quality' })).toHaveAttribute('aria-selected', 'true');
    expect(document.activeElement).toBe(screen.getByRole('tab', { name: 'Quality' }));
    expect(redRow(first.container)).toBeTruthy();
    first.unmount();

    const reload = open();
    await waitFor(() => expect(redRow(reload.container)).toBeTruthy());
    fireEvent.click(within(redRow(reload.container)).getByRole('button', { name: 'Close the release evaluation notice' }));
    expect(redRow(reload.container)).toBeNull();
    reload.unmount();

    const closed = open();
    await waitFor(() => expect(closed.container.querySelector('#about-eval')).toHaveTextContent('failed · answers changed after the release'));
    expect(redRow(closed.container)).toBeNull();
    expect(between(closed.container)).toEqual([]);
    closed.unmount();

    versions = { eval_post: failed('2026-11-18T09:00:00Z') };
    const next = open();
    await waitFor(() => expect(redRow(next.container)).toBeTruthy());
  });

  test('an evaluation that did not finish is a red row too, and a person without Quality gets no link', async () => {
    versions = { eval_post: unfinished };
    const { container } = open(analyst);
    await waitFor(() => expect(redRow(container)).toBeTruthy());
    expect(redRow(container)).toHaveTextContent('Release evaluation did not finish: the release is served without its check.');
    expect(within(redRow(container)).queryByRole('link')).toBeNull();
  });

  test('closing is per person in this browser', async () => {
    versions = { eval_post: failed() };
    const first = open(engineer);
    await waitFor(() => expect(redRow(first.container)).toBeTruthy());
    fireEvent.click(within(redRow(first.container)).getByRole('button', { name: 'Close the release evaluation notice' }));
    first.unmount();
    const other = open(analyst);
    await waitFor(() => expect(redRow(other.container)).toBeTruthy());
  });

  test('a storage error never breaks the page: the notice still shows and closes', async () => {
    vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => { throw new Error('blocked'); });
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => { throw new Error('blocked'); });
    versions = { eval_post: failed() };
    const { container } = open();
    await waitFor(() => expect(redRow(container)).toBeTruthy());
    fireEvent.click(within(redRow(container)).getByRole('button', { name: 'Close the release evaluation notice' }));
    expect(redRow(container)).toBeNull();
    expect(screen.getByRole('tablist', { name: 'Workspaces' })).toBeVisible();
  });

  test('with storage blocked, a closed notice stays closed while the versions poll fails and recovers', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => { throw new Error('blocked'); });
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => { throw new Error('blocked'); });
    versions = { eval_post: passed() };
    const { container } = open();
    await waitFor(() => expect(toast(container)).toBeTruthy());
    fireEvent.click(within(toast(container)).getByRole('button', { name: 'Close the release evaluation notice' }));
    expect(toast(container)).toBeNull();
    versions = { error: 'The service is busy.' };
    await act(async () => { vi.advanceTimersByTime(30000); });
    await waitFor(() => expect(container.querySelector('#about-eval')).toBeNull());
    versions = { eval_post: passed() };
    await act(async () => { vi.advanceTimersByTime(30000); });
    await waitFor(() => expect(container.querySelector('#about-eval')).toBeTruthy());
    expect(toast(container)).toBeNull();

    versions = { eval_post: failed() };
    await act(async () => { vi.advanceTimersByTime(30000); });
    await waitFor(() => expect(redRow(container)).toBeTruthy());
    fireEvent.click(within(redRow(container)).getByRole('button', { name: 'Close the release evaluation notice' }));
    versions = { error: 'The service is busy.' };
    await act(async () => { vi.advanceTimersByTime(30000); });
    await waitFor(() => expect(container.querySelector('#about-eval')).toBeNull());
    versions = { eval_post: failed() };
    await act(async () => { vi.advanceTimersByTime(30000); });
    await waitFor(() => expect(container.querySelector('#about-eval')).toBeTruthy());
    expect(redRow(container)).toBeNull();
  });

  test('clicks under the toast reach the page; only its ✕ takes them', () => {
    const css = contextCss();
    const rule = (selector) => new RegExp(`${selector.replace(/[.]/g, '\\.')}\\s*\\{[^}]*pointer-events:\\s*(\\w+)`).exec(css)?.[1];
    expect(rule('.release-toast-region')).toBe('none');
    expect(rule('.release-toast')).toBe('none');
    expect(rule('.release-toast-region .notice-close')).toBe('auto');
  });

  test('the raw evaluation lives in the Quality tab\'s release gate panel', async () => {
    versions = { eval_post: failed() };
    const { container } = open();
    await waitFor(() => expect(redRow(container)).toBeTruthy());
    expect(redRow(container).querySelector('details, pre')).toBeNull();
    fireEvent.click(screen.getByRole('tab', { name: 'Quality' }));
    const gate = within(screen.getByRole('tabpanel', { name: 'Quality' })).getByRole('heading', { name: 'Release gate' }).parentElement;
    expect(gate).toHaveTextContent('EVAL POST FAILED: the release stays served; run kge rollback');
    fireEvent.click(within(gate).getByText('Automatic release evaluation'));
    expect(JSON.parse([...gate.querySelectorAll('pre')].at(-1).textContent)).toEqual(failed());
  });

  test('the ⓘ at the end of the tab bar opens About this release from the mouse and the keyboard', async () => {
    versions = { eval_post: passed() };
    const { container } = open();
    await loaded(container);
    const tabs = screen.getByRole('tablist', { name: 'Workspaces' });
    const info = screen.getByRole('button', { name: 'About this release' });
    expect(tabs.contains(info)).toBe(false);
    expect(within(tabs).queryByRole('tab', { name: /about/i })).toBeNull();
    expect(container.querySelector('#views-bar').lastElementChild.previousElementSibling).toBe(info.parentElement);
    expect(info.tagName).toBe('BUTTON');
    expect(info.tabIndex).toBe(0);
    expect(info).toHaveAttribute('aria-haspopup', 'dialog');
    expect(info).toHaveAttribute('aria-controls', 'about-build');
    expect(info).toHaveAttribute('aria-expanded', 'false');
    expect(screen.queryByRole('dialog', { name: 'About this release' })).toBeNull();

    info.focus();
    fireEvent.click(info);
    const dialog = screen.getByRole('dialog', { name: 'About this release' });
    expect(info).toHaveAttribute('aria-expanded', 'true');
    expect(dialog).toBeVisible();
    await waitFor(() => expect(document.activeElement).toBe(dialog));
    const rows = Object.fromEntries([...dialog.querySelectorAll('dt')].map((dt) => [dt.textContent, dt.nextElementSibling.textContent]));
    expect(rows).toEqual({
      Release: 'rel-7 · as of 2026-03-31',
      Client: 'meridian · profile meridian',
      Artifact: '98391f7e3f4a',
      Version: 'KGE 0.11.37',
      Evaluation: '✓ passed · results unchanged',
      Baseline: 'Quality compares results with the release baseline.',
    });
    expect(Object.fromEntries([...dialog.querySelectorAll('[data-fact]')].map((f) => [f.dataset.fact, f.textContent])))
      .toEqual({ release: 'rel-7', as_of: '2026-03-31', client: 'meridian', profile: 'meridian', artifact: '98391f7e3f4a' });
    expect(within(dialog).getByText('Artifact')).toBeVisible();

    fireEvent.keyDown(dialog, { key: 'Escape' });
    expect(info).toHaveAttribute('aria-expanded', 'false');
    expect(container.querySelector('#about-build')).not.toBeVisible();
    expect(document.activeElement).toBe(info);

    fireEvent.click(info);
    expect(screen.getByRole('dialog', { name: 'About this release' })).toBeVisible();
    fireEvent.mouseDown(document.body);
    expect(info).toHaveAttribute('aria-expanded', 'false');

    fireEvent.click(info);
    const outside = screen.getByRole('tab', { name: 'Ask' });
    fireEvent.focusOut(screen.getByRole('dialog', { name: 'About this release' }), { relatedTarget: outside });
    expect(info).toHaveAttribute('aria-expanded', 'false');
  });

  test('the not-released banner and the preview label stay above the tabs', async () => {
    const bootstrap = opening(engineer, { mode: { released: false, message: 'Not released yet.' }, profile: { ...profile, preview: { banner: 'Candidate abc, not released' } } });
    const { container } = render(<ContextScreens bootstrap={bootstrap} />);
    await loaded(container);
    expect(between(container).map((node) => node.id)).toEqual(['release-banner', 'preview']);
    expect(container.querySelector('#release-banner')).toHaveTextContent('Not released yet.');
    expect(container.querySelector('#preview')).toHaveTextContent('Candidate abc, not released');
  });
});
