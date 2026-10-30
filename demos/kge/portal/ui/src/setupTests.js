import '@testing-library/jest-dom/vitest';
import { cleanup } from '@testing-library/react';
import axios from 'axios';
import { afterEach, expect } from 'vitest';
import { forgetBrand } from './services/brand';

// No test may reach a server: a request that escapes the mocks of src/api or
// src/services is refused here and fails the test that made it.
const unmocked = [];
axios.defaults.adapter = (config) => {
  unmocked.push(`${config.method?.toUpperCase()} ${config.url}`);
  return Promise.reject(new Error(`unmocked request in a test: ${config.method} ${config.url}`));
};

// The shell reads /brand once for its logo and product links; tests get the
// unconfigured answer unless they stub fetch themselves.
globalThis.fetch = (url, init) => {
  if (url === '/brand') return Promise.resolve(new Response(JSON.stringify({ name: 'Data Discovery Portal', logo: false, kge_url: null }), { status: 200, headers: { 'Content-Type': 'application/json' } }));
  unmocked.push(`${(init?.method || 'GET').toUpperCase()} ${url}`);
  return Promise.reject(new Error(`unmocked fetch in a test: ${url}`));
};

afterEach(() => {
  cleanup();
  forgetBrand();
  delete document.documentElement.dataset.theme;
  const escaped = unmocked.splice(0);
  expect(escaped, 'requests that reached the network adapter').toEqual([]);
});
