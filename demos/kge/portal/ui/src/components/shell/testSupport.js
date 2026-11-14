import { vi } from 'vitest';

/* Shared by the global bar's tests: an install's /api/config answer, a signed-in person's
   /whoami, and a fetch stub that answers the portal's shell calls and refuses anything else. */

export const ALL_ON = { certify: true, context: true, discover: true };

export const configOf = (overrides = {}) => ({
  knowledgeHome: true,
  apps: { ...ALL_ON, ...(overrides.apps || {}) },
  certifyUrl: null,
  productName: 'Meridian ALTA',
  contextMcpUrl: 'https://kge.example.invalid/mcp',
  ...Object.fromEntries(Object.entries(overrides).filter(([key]) => key !== 'apps')),
});

export const STEWARD = { id: 'steward@example.com', name: 'Sam Steward', role: 'steward', roles: ['steward'], processes: ['onboarding'], reads: [] };
export const READER = { id: 'reader@example.com', name: 'Rae Reader', roles: [], reads: [] };

const json = (ok, body) => ({ ok, status: ok ? 200 : 401, json: async () => body });

export function stubShell({ config = configOf(), who = STEWARD, role = { role: 'viewer', roles: ['viewer'] } } = {}) {
  const state = { who };
  const fetch = vi.fn((url, init = {}) => {
    if (url === '/api/config') return Promise.resolve(json(true, config));
    if (url === '/brand') return Promise.resolve(json(true, { name: 'ALTA: AI-powered Library for Trusted Assets', logo: false, kge_url: null }));
    if (url === '/whoami') return Promise.resolve(state.who ? json(true, state.who) : json(false, {}));
    if (url === '/api/role') return Promise.resolve(state.who ? json(true, role) : json(false, {}));
    if (url === '/session' && init.method === 'DELETE') {
      state.who = null;
      return Promise.resolve(json(true, {}));
    }
    return Promise.reject(new Error(`unexpected fetch in a shell test: ${url}`));
  });
  vi.stubGlobal('fetch', fetch);
  return { fetch, state };
}

export function stubPhone(matches) {
  vi.stubGlobal('matchMedia', vi.fn((query) => ({
    matches,
    media: query,
    addEventListener: () => {},
    removeEventListener: () => {},
  })));
}
