import { afterEach, expect, test, vi } from 'vitest';
import { renderHook, waitFor } from '@testing-library/react';
import { APPS, KNOWLEDGE, appById, appForPath, appsFor, canUse, cerebroRolesOf, contextRolesOf, docsFor, isSetUp, knowledgeFor, singleAppPath } from './apps';
import { DEFAULT_CONFIG, DEFAULT_PRODUCT_NAME, DEFAULT_PRODUCT_SHORT_NAME, forgetConfig, knownConfig, loadConfig, normaliseConfig, shortNameOf, useConfig } from '../../services/config';

afterEach(() => vi.unstubAllGlobals());

test.each([
  ['/', 'knowledge'], ['/knowledge/docs/start-here', 'knowledge'], ['/knowledge', 'knowledge'],
  ['/context', 'context'], ['/Context/x', 'context'], ['/certify', 'certify'], ['/certify/x', 'certify'],
  ['/cerebro', 'cerebro'], ['/Cerebro', 'cerebro'], ['/cerebro/x', 'cerebro'], ['/cerebroish', 'discover'],
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

test.each([
  [{ id: 'a', cerebro_roles: ['business'] }, ['business']],
  [{ id: 'a', cerebro_roles: 'Steward' }, ['steward']],
  [{ id: 'a', cerebro_roles: ['engineer', 'admin', 'engineer'] }, ['engineer', 'admin']],
  [{ id: 'a', cerebro_roles: ['admin'] }, ['admin']],
  [{ id: 'a', cerebro_roles: ['knowledge_engineer', '', 3] }, []],
  [{ id: 'a', roles: ['steward'], processes: ['certification'] }, []],
  [null, []],
])('Cerebro roles of %o are %o, as KGE states them', (identity, roles) => {
  expect(cerebroRolesOf(identity)).toEqual(roles);
});

test('Cerebro opens to Business, Steward and Engineer; an admin-only person and a person with no Cerebro role have no door', () => {
  for (const role of ['business', 'steward', 'engineer']) expect(canUse({ cerebroRoles: [role] }, 'cerebro')).toBe(true);
  expect(canUse({ cerebroRoles: ['admin', 'steward'] }, 'cerebro')).toBe(true);
  expect(canUse({ cerebroRoles: ['admin'] }, 'cerebro')).toBe(false);
  expect(canUse({ cerebroRoles: [] }, 'cerebro')).toBe(false);
  expect(canUse({ contextRoles: ['steward'] }, 'cerebro')).toBe(false);
  expect(canUse(undefined, 'cerebro')).toBe(false);
});

test('Cerebro sits in the registry at /cerebro and carries its name setting, Cerebro by default', () => {
  expect(APPS.map((app) => app.id)).toEqual(['certify', 'context', 'discover', 'cerebro']);
  expect(appById('cerebro', null)).toMatchObject({ id: 'cerebro', label: 'Cerebro', path: '/cerebro' });
  const named = normaliseConfig({ apps: { cerebro: true }, cerebroName: '  Graph ', contextMcpUrl: 'https://kge.example.invalid/mcp' });
  expect(named.cerebroName).toBe('Graph');
  expect(appById('cerebro', named).label).toBe('Graph');
  expect(appsFor(named).map((app) => app.label)).toEqual(['Certify', 'Context', 'Discover', 'Graph']);
  expect(appById('context', named).label).toBe('Context');
  expect(normaliseConfig({ cerebroName: '   ' }).cerebroName).toBe('Cerebro');
});

test('Cerebro is set up only where the server turns it on and KGE is behind the portal, whatever Context is', () => {
  const kge = 'https://kge.example.invalid/mcp';
  expect(isSetUp(normaliseConfig({ apps: { cerebro: true }, kgeJoined: true }), 'cerebro')).toBe(true);
  expect(isSetUp(normaliseConfig({ apps: { cerebro: true, context: false }, kgeJoined: true, contextMcpUrl: null }), 'cerebro')).toBe(true);
  expect(isSetUp(normaliseConfig({ apps: { cerebro: true }, contextMcpUrl: kge }), 'cerebro')).toBe(false);
  expect(isSetUp(normaliseConfig({ apps: { cerebro: true }, kgeJoined: 'yes' }), 'cerebro')).toBe(false);
  expect(isSetUp(normaliseConfig({ apps: { cerebro: false }, kgeJoined: true }), 'cerebro')).toBe(false);
  expect(isSetUp(normaliseConfig({ apps: {}, kgeJoined: true }), 'cerebro')).toBe(false);
  expect(isSetUp(DEFAULT_CONFIG, 'cerebro')).toBe(false);
  const alone = normaliseConfig({ knowledgeHome: false, apps: { certify: false, context: false, discover: false, cerebro: true }, kgeJoined: true, contextMcpUrl: null });
  expect(isSetUp(alone, 'cerebro')).toBe(true);
  expect(singleAppPath(alone)).toBe('/cerebro');
});
