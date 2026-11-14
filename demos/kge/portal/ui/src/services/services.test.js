import { beforeEach, expect, test, vi } from 'vitest';
import * as admin from './admin';
import * as dropdown from './dropdown';
import * as login from './login';
import * as metadata from './metadata';
import * as search from './search';
import * as user from './user';
import { clearAskHistory, loadPersonHistory, personStorageKey, savePersonTurn } from './askHistory';
import { getApi, postApi, putApi, deleteApi } from '../api/api';

vi.mock('../api/api', () => ({
  getApi: vi.fn(async (request) => ({ data: { via: 'get', request } })),
  postApi: vi.fn(async (request) => ({ data: { via: 'post', request } })),
  putApi: vi.fn(async (request) => ({ data: { via: 'put', request } })),
  deleteApi: vi.fn(async (request) => ({ data: { via: 'delete', request } })),
}));
beforeEach(() => vi.clearAllMocks());

test('clearing Ask history removes every person history and preserves unrelated local storage', () => {
  window.localStorage.clear();
  try {
    savePersonTurn('alice@example.com', { conversationId: 'alice', question: 'Earlier question?', turns: [{ id: 1, q: 'Earlier question?', a: { figure: 10, sql: 'SELECT 10', unit: 'leasing' } }] });
    savePersonTurn('bob@example.com', { conversationId: 'bob', question: 'Other earlier question?' });
    window.localStorage.setItem('other-feature', 'keep');
    clearAskHistory();
    expect(Object.keys(window.localStorage)).toEqual(['other-feature']);
    expect(window.localStorage.getItem('other-feature')).toBe('keep');
    expect(loadPersonHistory('alice@example.com')).toEqual({ conversations: [], questions: [] });
    expect(loadPersonHistory('bob@example.com')).toEqual({ conversations: [], questions: [] });
  } finally {
    window.localStorage.clear();
  }
});

test('Context history is cleared even when session storage is unavailable', () => {
  const descriptor = Object.getOwnPropertyDescriptor(window, 'sessionStorage');
  window.localStorage.clear();
  try {
    savePersonTurn('alice@example.com', { conversationId: 'alice', question: 'Earlier question?' });
    Object.defineProperty(window, 'sessionStorage', { configurable: true, get() { throw new Error('Storage unavailable'); } });
    expect(() => clearAskHistory()).not.toThrow();
    expect(window.localStorage.getItem(personStorageKey('alice@example.com'))).toBeNull();
  } finally {
    if (descriptor) Object.defineProperty(window, 'sessionStorage', descriptor);
    else delete window.sessionStorage;
    window.localStorage.clear();
  }
});

test('successful sign-in clears every Ask ALTA history before returning', async () => {
  const keys = ['askHistory:retail.account', 'askHistory:domain:Retail Banking', 'askHistory:domain:All data assets'];
  for (const key of keys) window.sessionStorage.setItem(key, JSON.stringify([{ text: 'Previous conversation', isBot: false }]));
  window.sessionStorage.setItem('other-feature', 'keep');
  try {
    const response = await login.login({ email: 'analyst', password: 'pw' });
    expect(response.data.via).toBe('post');
    expect(postApi).toHaveBeenCalledWith({ api: '/session', params: { username: 'analyst', password: 'pw' } });
    for (const key of keys) expect(window.sessionStorage.getItem(key)).toBeNull();
    expect(window.sessionStorage.getItem('other-feature')).toBe('keep');
  } finally {
    window.sessionStorage.clear();
  }
});

// Each service call names the portal endpoint it reads or writes; the table is
// the contract between the page and the portal server.
test.each([
  ['admin.getPortalUsers', () => admin.getPortalUsers(), getApi, { api: '/api/admin/users' }],
  ['admin.getPortalRoleAudit', () => admin.getPortalRoleAudit(), getApi, { api: '/api/admin/users/audit' }],
  ['admin.getPromptDirection', () => admin.getPromptDirection(), getApi, { api: '/api/admin/prompt-direction' }],
  ['admin.savePromptDirection', () => admin.savePromptDirection('Use plain language.'), putApi, { api: '/api/admin/prompt-direction', data: { text: 'Use plain language.' } }],
  ['admin.grantPortalRole', () => admin.grantPortalRole('a/b@example.com', 'publisher'), putApi, { api: '/api/admin/users/a%2Fb%40example.com/roles/publisher' }],
  ['admin.removePortalRole', () => admin.removePortalRole('a/b@example.com', 'publisher'), deleteApi, { api: '/api/admin/users/a%2Fb%40example.com/roles/publisher' }],
  ['admin.getPublishSearchData', () => admin.getPublishSearchData('sales'), getApi, { api: '/getPublishSearchData/sales' }],
  ['admin.getPublishedSearchResult', () => admin.getPublishedSearchResult('sales'), getApi, { api: '/getPublishedSearchResult/sales' }],
  ['admin.getPublishedStatus', () => admin.getPublishedStatus(), getApi, { api: '/getPublishedStatus' }],
  ['admin.updatePublishedStatus', () => admin.updatePublishedStatus({ id: 1 }), postApi, { api: '/updatePublishedStatus', params: { id: 1 } }],
  ['admin.updatePreviewStatus', () => admin.updatePreviewStatus({ id: 2 }), postApi, { api: '/updatePreviewStatus', params: { id: 2 } }],
  ['admin.publishStatusLiveSync', () => admin.publishStatusLiveSync(), getApi, { api: '/publishStatusLiveSync' }],
  ['admin.updateApplicationLiveSync', () => admin.updateApplicationLiveSync(), getApi, { api: '/updateApplicationLiveSync' }],
  ['admin.getDomainList', () => admin.getDomainList(), getApi, { api: '/getDomainList' }],
  ['admin.getFilterResult', () => admin.getFilterResult('retail'), getApi, { api: '/getFilterResult/retail' }],
  ['admin.getUserData', () => admin.getUserData(), getApi, { api: '/getUserData' }],
  ['dropdown.getValues', () => dropdown.getValues('domain'), getApi, { api: '/dropdown-values/domain' }],
  ['login.login', () => login.login({ email: 'analyst', password: 'pw' }), postApi, { api: '/session', params: { username: 'analyst', password: 'pw' } }],
  ['metadata.getFields', () => metadata.getFields('p1'), getApi, { api: '/fields/p1' }],
  ['metadata.getDataQuality', () => metadata.getDataQuality('p1', 'gold'), getApi, { api: '/dataQuality/p1/gold' }],
  ['metadata.getLineage', () => metadata.getLineage('p1'), getApi, { api: '/lineage/p1' }],
  ['metadata.getProduct', () => metadata.getProduct('p1'), getApi, { api: '/product/p1' }],
  ['metadata.submitFeedback', () => metadata.submitFeedback('p1', 4, 'useful'), postApi, { api: '/submit-feedback', params: { productId: 'p1', rating: 4, comment: 'useful' } }],
  ['metadata.getFilteredFields', () => metadata.getFilteredFields({ q: 'x' }), postApi, { api: '/filteredFields', params: { q: 'x' } }],
  ['metadata.getQueryColumns', () => metadata.getQueryColumns('a/b c'), getApi, { api: '/api/products/a%2Fb%20c/fields' }],
  ['metadata.getDataPreview', () => metadata.getDataPreview({ id: 'p1' }), postApi, { api: '/get-data-preview', params: { id: 'p1' } }],
  ['metadata.getDataExport', () => metadata.getDataExport('r9'), getApi, { api: '/get-data-export/r9' }],
  ['metadata.executeQuery', () => metadata.executeQuery({ sql: 'select 1' }), postApi, { api: '/execute-query', params: { sql: 'select 1' } }],
  ['search.getSearchResults', () => search.getSearchResults({ q: 'orders' }), postApi, { api: '/search', params: { q: 'orders' } }],
  ['search.getCommunityProducts', () => search.getCommunityProducts({ c: 1 }), postApi, { api: '/getCommunityProducts', params: { c: 1 } }],
  ['search.sortSearchResults', () => search.sortSearchResults({ s: 1 }), postApi, { api: '/sortSearchResults', params: { s: 1 } }],
  ['search.groupSearchResults', () => search.groupSearchResults({ g: 1 }), postApi, { api: '/groupSearchResults', params: { g: 1 } }],
  ['search.filterSearchResults', () => search.filterSearchResults({ f: 1 }), postApi, { api: '/filterSearchResults', params: { f: 1 } }],
  ['search.getSearchData', () => search.getSearchData('orders'), getApi, { api: '/getSearchData/orders' }],
  ['user.getFavouriteDataProducts', () => user.getFavouriteDataProducts(), getApi, { api: '/getFavourites' }],
  ['user.saveFavouriteProduct', () => user.saveFavouriteProduct({ id: 'p1' }), postApi, { api: '/saveFavouriteProduct', params: { id: 'p1' } }],
  ['user.removeFavouriteProduct', () => user.removeFavouriteProduct({ id: 'p1' }), postApi, { api: '/removeFavouriteProduct', params: { id: 'p1' } }],
  ['user.saveViewedDataProduct', () => user.saveViewedDataProduct({ id: 'p1' }), postApi, { api: '/saveViewedDataProduct', params: { id: 'p1' } }],
  ['user.getRelatedDataProducts', () => user.getRelatedDataProducts(), getApi, { api: '/getRelatedProducts' }],
  ['user.getViewedDataProducts', () => user.getViewedDataProducts(), getApi, { api: '/getMostViewedProducts' }],
  ['user.getDataCommunities', () => user.getDataCommunities(), getApi, { api: '/getDataCommunities' }],
  ['user.getConfig', () => user.getConfig(), getApi, { api: '/getConfig' }],
  ['user.requestAccess', () => user.requestAccess('p1'), postApi, { api: '/api/requests', params: { productId: 'p1', kind: 'access' } }],
  ['user.getMyRequests', () => user.getMyRequests(), getApi, { api: '/api/requests' }],
  ['user.trackMarkeplaceUser', () => user.trackMarkeplaceUser({ u: 1 }), postApi, { api: '/trackMarkeplaceUser', params: { u: 1 } }],
])('%s calls the portal with the right request', async (_name, call, transport, request) => {
  const res = await call();
  expect(transport).toHaveBeenCalledTimes(1);
  expect(transport).toHaveBeenCalledWith(request);
  expect(res.data.request).toEqual(request);
});

test('per-person Ask history isolates past questions and conversations by principal and honours retentionDays', () => {
  window.localStorage.clear();
  try {
    expect(personStorageKey('Alice@Example.com')).toBe('contextAsk:person:alice@example.com');
    const now = 1_700_000_000_000;
    const saved = savePersonTurn(
      'Alice@Example.com',
      {
        conversationId: 'conv-1',
        title: 'NNA for Q1',
        turns: [{ id: 1, q: 'NNA for Q1', purpose: 'analysis', arm: 'graph', a: { figure: 100 } }],
        runtimes: ['rt_a'],
        question: 'NNA for Q1',
        units: ['wealth'],
      },
      { retentionDays: 30, now },
    );
    expect(saved.questions).toEqual([{ question: 'NNA for Q1', units: ['wealth'], askedAt: now }]);
    expect(saved.conversations).toHaveLength(1);
    expect(saved.conversations[0].runtimes).toEqual(['rt_a']);

    // Other person sees nothing
    expect(loadPersonHistory('bob@example.com', { retentionDays: 30, now })).toEqual({ conversations: [], questions: [] });

    // Expired after 31 days
    const future = now + 31 * 24 * 60 * 60 * 1000;
    expect(loadPersonHistory('alice@example.com', { retentionDays: 30, now: future })).toEqual({ conversations: [], questions: [] });

    // Zero retention clears storage
    expect(savePersonTurn('alice@example.com', { question: 'New' }, { retentionDays: 0, now })).toEqual({ conversations: [], questions: [] });
    expect(loadPersonHistory('alice@example.com', { retentionDays: 0, now })).toEqual({ conversations: [], questions: [] });
  } finally {
    window.localStorage.clear();
  }
});
