import { expect, test } from 'vitest';
import { configureStore } from '@reduxjs/toolkit';
import store from './store';
import user, { userDetails, userLogin } from '../features/user/userSlice';
import dataProduct, * as dp from '../features/dataProduct/dataProductSlice';
import search, * as s from '../features/search/searchSlice';

const fresh = () => configureStore({ reducer: { user, dataProduct, search } });

test('the app store starts signed out with empty lists', () => {
  const state = store.getState();
  expect(state.user).toEqual({ isLoggedIn: false, data: {} });
  expect(state.dataProduct.recentDataProducts).toEqual([]);
  expect(state.search.searchResults).toEqual([]);
});

test('user sign-in and details', () => {
  const st = fresh();
  st.dispatch(userLogin());
  expect(st.getState().user.isLoggedIn).toBe(true);
  st.dispatch(userLogin(false));
  expect(st.getState().user.isLoggedIn).toBe(false);
  st.dispatch(userDetails({ userRole: 'admin' }));
  expect(st.getState().user.data).toEqual({ userRole: 'admin' });
});

test('data product lists become labelled options and plain values are stored', () => {
  const st = fresh();
  st.dispatch(dp.recentProductsArray([{ id: 1 }]));
  st.dispatch(dp.recommendedProductsArray([{ id: 2 }]));
  st.dispatch(dp.selectedProduct(7));
  st.dispatch(dp.selectedProductName('Orders'));
  st.dispatch(dp.domainList(['Retail']));
  st.dispatch(dp.platformList(['BigQuery']));
  st.dispatch(dp.applicationNameList(['CRM']));
  st.dispatch(dp.dataProductTypeList(['Table']));
  st.dispatch(dp.fieldsDataList([{ f: 'a' }]));
  st.dispatch(dp.recentProductsError('down'));
  st.dispatch(dp.recommendedProductsError('slow'));
  const d = st.getState().dataProduct;
  expect(d.recentDataProducts).toEqual([{ id: 1 }]);
  expect(d.recommendedDataProducts).toEqual([{ id: 2 }]);
  expect(d.selectedProduct).toBe(7);
  expect(d.selectedProductName).toBe('Orders');
  expect(d.domainList).toEqual([{ value: 'Retail', label: 'Retail', parent: 'Domain' }]);
  expect(d.platformList).toEqual([{ value: 'BigQuery', label: 'BigQuery', parent: 'Platform' }]);
  expect(d.applicationNameList).toEqual([{ value: 'CRM', label: 'CRM', parent: 'ApplicationName' }]);
  expect(d.dataProductTypeList).toEqual([{ value: 'Table', label: 'Table', parent: 'ProductType' }]);
  expect(d.fieldsDataList).toEqual([{ f: 'a' }]);
  expect(d.recentProductsError).toBe('down');
  expect(d.recommendedProductsError).toBe('slow');
});

test('search state keeps results and builds facet options', () => {
  const st = fresh();
  st.dispatch(s.searchResults([{ id: 1 }]));
  st.dispatch(s.searchParam('orders'));
  st.dispatch(s.domainFilter(['Retail']));
  st.dispatch(s.communityFilter(['CRM']));
  st.dispatch(s.sourceFilter(['BQ']));
  st.dispatch(s.typeFilter(['Table']));
  st.dispatch(s.hierarchyList([1]));
  st.dispatch(s.filterOptions([2]));
  st.dispatch(s.groupOption([3]));
  st.dispatch(s.sortOption([4]));
  st.dispatch(s.domainSelected(true));
  const q = st.getState().search;
  expect(q.searchResults).toEqual([{ id: 1 }]);
  expect(q.searchParam).toBe('orders');
  expect(q.domainFilter).toEqual([{ value: 'Retail', label: 'Retail', parent: 'DOMAIN' }]);
  expect(q.communityFilter[0].parent).toBe('COMMUNITY');
  expect(q.sourceFilter[0].parent).toBe('PRODUCTSOURCE');
  expect(q.typeFilter[0].parent).toBe('PRODUCTTYPE');
  expect([q.hierarchyList, q.filterOptions, q.groupOption, q.sortOption, q.domainSelected]).toEqual([[1], [2], [3], [4], true]);
});
