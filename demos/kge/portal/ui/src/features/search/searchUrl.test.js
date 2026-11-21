import { expect, test } from 'vitest';
import { buildSearchUrl, parseSearchParams } from './searchUrl';

test('community links retain the domain and filters across round trips', () => {
  const url = buildSearchUrl({ community: 'Accounts', domain: 'Retail', filters: [{ parent: 'COMMUNITY', value: 'Accounts' }, { parent: 'PRODUCTTYPE', value: 'Table' }], tier: 'GOLD', fresh: true });
  expect(parseSearchParams(new URL(url, 'http://local').searchParams)).toMatchObject({ community: 'Accounts', domain: 'Retail', tier: 'GOLD', fresh: true, filters: [{ parent: 'COMMUNITY', value: 'Accounts', label: 'Accounts' }, { parent: 'PRODUCTTYPE', value: 'Table', label: 'Table' }] });
});
test('invalid filters and tiers do not enter Explore state', () => {
  expect(parseSearchParams(new URLSearchParams('q=account&f=UNSAFE:x&f=COMMUNITY:Retail&tier=other'))).toMatchObject({ q: 'account', tier: '', filters: [{ parent: 'COMMUNITY', value: 'Retail', label: 'Retail' }] });
});


test('classification filters survive a shared URL including API/MCP punctuation', () => {
  const filters = [
    { parent: 'PRODUCTTYPE', value: 'API/MCP' },
    { parent: 'ASSETTYPE', value: 'Unstructured' },
    { parent: 'USAGEPURPOSE', value: 'Operational' },
  ];
  const url = buildSearchUrl({ filters });
  expect(parseSearchParams(new URL(url, 'http://local').searchParams).filters).toEqual(filters.map((filter) => ({ ...filter, label: filter.value })));
});

test('a use case link keeps its title, sort and filters across a round trip, and a community takes precedence', () => {
  const url = buildSearchUrl({ useCase: ' Month-end & review ', q: 'ignored', sort: 'name', filters: [{ parent: 'DOMAIN', value: 'Retail' }] });
  const spec = parseSearchParams(new URL(url, 'http://local').searchParams);
  expect(spec).toMatchObject({ useCase: 'Month-end & review', q: '', sort: 'name', filters: [{ parent: 'DOMAIN', value: 'Retail', label: 'Retail' }], baseKey: 'u:Month-end & review' });
  expect(parseSearchParams(new URLSearchParams('community=Accounts&useCase=Review'))).toMatchObject({ community: 'Accounts', useCase: '', baseKey: 'c::Accounts' });
  expect(buildSearchUrl({ community: 'Accounts', useCase: 'Review' })).toBe('/search?community=Accounts');
  expect(parseSearchParams(new URLSearchParams('q=account&useCase=Review'))).toMatchObject({ q: '', useCase: 'Review', baseKey: 'u:Review' });
});
