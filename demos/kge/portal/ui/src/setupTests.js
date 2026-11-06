import '@testing-library/jest-dom/vitest';
import { cleanup } from '@testing-library/react';
import axios from 'axios';
import { afterEach, expect } from 'vitest';
import { forgetBrand } from './services/brand';
import { forgetConfig } from './services/config';

// No test may reach a server: a request that escapes the mocks of src/api or
// src/services is refused here and fails the test that made it.
const unmocked = [];
axios.defaults.adapter = (config) => {
  unmocked.push(`${config.method?.toUpperCase()} ${config.url}`);
  return Promise.reject(new Error(`unmocked request in a test: ${config.method} ${config.url}`));
};

// The global bar reads /api/config once: tests get every flag on unless they stub fetch themselves.
const TEST_CONFIG = { knowledgeHome: true, apps: { certify: true, context: true, discover: true }, certifyUrl: null, productName: 'Meridian ALTA', contextMcpUrl: 'https://kge.example.invalid/mcp' };

// The shell reads /brand once for its logo and product links; tests get the
// unconfigured answer unless they stub fetch themselves.
globalThis.fetch = (url, init) => {
  if (url === '/api/config') return Promise.resolve(new Response(JSON.stringify(TEST_CONFIG), { status: 200, headers: { 'Content-Type': 'application/json' } }));
  if (url === '/brand') return Promise.resolve(new Response(JSON.stringify({ name: 'ALTA: AI-powered Library for Trusted Assets', logo: false, kge_url: null }), { status: 200, headers: { 'Content-Type': 'application/json' } }));
  unmocked.push(`${(init?.method || 'GET').toUpperCase()} ${url}`);
  return Promise.reject(new Error(`unmocked fetch in a test: ${url}`));
};

afterEach(() => {
  cleanup();
  forgetBrand();
  forgetConfig();
  delete document.documentElement.dataset.theme;
  const escaped = unmocked.splice(0);
  expect(escaped, 'requests that reached the network adapter').toEqual([]);
});
