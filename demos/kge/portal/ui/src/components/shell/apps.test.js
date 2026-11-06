import { afterEach, expect, test, vi } from 'vitest';
import { renderHook, waitFor } from '@testing-library/react';
import { KNOWLEDGE, appById, appForPath, canUse, contextRolesOf, docsFor, isSetUp, knowledgeFor, singleAppPath } from './apps';
import { DEFAULT_CONFIG, DEFAULT_PRODUCT_NAME, DEFAULT_PRODUCT_SHORT_NAME, forgetConfig, knownConfig, loadConfig, normaliseConfig, shortNameOf, useConfig } from '../../services/config';

afterEach(() => vi.unstubAllGlobals());

test.each([
  ['/', 'knowledge'], ['/knowledge/docs/start-here', 'knowledge'], ['/knowledge', 'knowledge'],
  ['/context', 'context'], ['/Context/x', 'context'], ['/certify', 'certify'], ['/certify/x', 'certify'],
  ['/home', 'discover'], ['/Search', 'discover'], ['/productDetails/p1', 'discover'], ['/adminaccess', 'discover'],
])('%s belongs to %s', (path, app) => {
  expect(appForPath(path)).toBe(app);
});

test('appForPath defaults to the root', () => {
  expect(appForPath()).toBe('knowledge');
});

test.each([
  [{ id: 'a', roles: ['steward'] }, ['steward']],
  [{ id: 'a', roles: 'steward' }, ['steward']],
  [{ id: 'a', roles: [], admin: true, role: 'Access admin' }, ['Access admin']],
  [{ id: 'a', role: 'analyst', processes: [], reads: [] }, ['analyst']],
  [{ id: 'a', principal: 'a', reads: [], role: 'admin' }, []],
  [{ id: 'a', roles: [''] }, []],
  [null, []],
])('Context roles of %o are %o', (identity, roles) => {
  expect(contextRolesOf(identity)).toEqual(roles);
});

test('only Context asks for a role; Discover and Certify follow today\'s rules', () => {
  expect(canUse({ contextRoles: [] }, 'context')).toBe(false);
  expect(canUse(undefined, 'context')).toBe(false);
  expect(canUse({ contextRoles: ['steward'] }, 'context')).toBe(true);
  expect(canUse({}, 'discover')).toBe(true);
  expect(canUse({}, 'certify')).toBe(true);
});

test('isSetUp reads the flags, and Context also needs KGE', () => {
  const config = normaliseConfig({ apps: { certify: false }, contextMcpUrl: 'https://kge.example.invalid/mcp' });
  expect(isSetUp(null, 'discover')).toBe(false);
  expect(isSetUp(config, 'knowledge')).toBe(true);
  expect(isSetUp(config, 'certify')).toBe(false);
  expect(isSetUp(config, 'context')).toBe(true);
  expect(isSetUp(normaliseConfig({ ...config, contextMcpUrl: null }), 'context')).toBe(false);
});

test('singleAppPath picks the app that is on, or none', () => {
  expect(singleAppPath({ apps: { certify: false, context: true, discover: false } })).toBe('/context');
  expect(singleAppPath({ apps: { certify: false, context: false, discover: false } })).toBeNull();
  expect(singleAppPath(null)).toBeNull();
});

test('docsFor never offers KGE\'s API reference', () => {
  for (const knowledgeHome of [true, false]) {
    for (const app of ['knowledge', 'certify', 'context', 'discover']) {
      const hrefs = docsFor({ ...DEFAULT_CONFIG, knowledgeHome, contextMcpUrl: 'x' }, app).flatMap((g) => g.items.map((i) => i.to || i.href));
      expect(hrefs.some((href) => /^\/(docs|redoc)/.test(href))).toBe(false);
    }
  }
  expect(docsFor(null, 'knowledge')).toEqual([]);
});

test.each([
  () => Promise.reject(new Error('down')),
  async () => ({ ok: false }),
  async () => ({ ok: true, json: async () => ({}) }),
  async () => ({ ok: true, json: async () => { throw new Error('bad JSON'); } }),
])('failed or malformed config is not cached as permissive defaults', async (fetch) => {
  forgetConfig();
  vi.stubGlobal('fetch', vi.fn(fetch));
  await expect(loadConfig()).rejects.toThrow();
  expect(knownConfig()).toBeNull();
});

test('the config is read once and shared', async () => {
  forgetConfig();
  const fetch = vi.fn().mockResolvedValue({ ok: true, json: async () => ({ knowledgeHome: false, apps: { certify: false, context: false, discover: true } }) });
  vi.stubGlobal('fetch', fetch);
  const [a, b] = await Promise.all([loadConfig(), loadConfig()]);
  expect(a).toBe(b);
  expect(knownConfig()).toBe(a);
  const { result } = renderHook(() => useConfig());
  expect(result.current).toBe(a);
  expect(fetch).toHaveBeenCalledTimes(1);
  expect(fetch).toHaveBeenCalledWith('/api/config', { credentials: 'same-origin' });
});

test('useConfig is null until the answer is in', async () => {
  forgetConfig();
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: true, json: async () => ({ ...DEFAULT_CONFIG, certifyUrl: 'https://c.example.invalid/' }) }));
  const { result } = renderHook(() => useConfig());
  expect(result.current).toBeNull();
  await waitFor(() => expect(result.current?.certifyUrl).toBe('https://c.example.invalid/'));
});

test('the product short name comes from the served settings, trimmed, and is ALTA otherwise', () => {
  expect(DEFAULT_PRODUCT_SHORT_NAME).toBe('ALTA');
  expect(DEFAULT_PRODUCT_NAME).toBe('Client ALTA');
  expect(DEFAULT_CONFIG.productShortName).toBe('ALTA');
  for (const value of [undefined, '', '   ', 7]) {
    expect(normaliseConfig({ productShortName: value }).productShortName).toBe('ALTA');
    expect(shortNameOf({ productShortName: value })).toBe('ALTA');
  }
  expect(shortNameOf(null)).toBe('ALTA');
  expect(normaliseConfig({ productShortName: '  Beacon  ' }).productShortName).toBe('Beacon');
  const beacon = normaliseConfig({ knowledgeHome: true, productShortName: 'Beacon', contextMcpUrl: 'x' });
  expect(KNOWLEDGE.label).toBe('ALTA home');
  expect(knowledgeFor(beacon).label).toBe('Beacon home');
  expect(appById('knowledge', beacon).label).toBe('Beacon home');
  expect(appById('knowledge').label).toBe('ALTA home');
  expect(docsFor(beacon, 'knowledge').map((group) => group.title)).toEqual(['Beacon docs']);
  expect(docsFor(beacon, 'context').map((group) => group.title)).toEqual(['Context docs', 'Beacon docs']);
  expect(docsFor({ ...DEFAULT_CONFIG, knowledgeHome: true }, 'knowledge').map((group) => group.title)).toEqual(['ALTA docs']);
});
