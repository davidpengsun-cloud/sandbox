import { beforeEach, expect, test, vi } from 'vitest';
import * as admin from './admin';
import * as dropdown from './dropdown';
import * as login from './login';
import * as metadata from './metadata';
import * as search from './search';
import * as user from './user';
import { getApi, postApi } from '../api/api';

vi.mock('../api/api', () => ({
  getApi: vi.fn(async (request) => ({ data: { via: 'get', request } })),
  postApi: vi.fn(async (request) => ({ data: { via: 'post', request } })),
}));
beforeEach(() => vi.clearAllMocks());

// Each service call names the portal endpoint it reads or writes; the table is
// the contract between the page and the portal server.
test.each([
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
  ['metadata.getQueryColumns', () => metadata.getQueryColumns('a/b c'), getApi, { api: '/api/preview/a%2Fb%20c?limit=1' }],
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
