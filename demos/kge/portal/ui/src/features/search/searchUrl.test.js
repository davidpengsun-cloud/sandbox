import { expect, test } from 'vitest';
import { buildSearchUrl, parseSearchParams } from './searchUrl';

test('community links retain the domain and filters across round trips', () => {
  const url = buildSearchUrl({ community: 'Accounts', domain: 'Retail', filters: [{ parent: 'COMMUNITY', value: 'Accounts' }, { parent: 'PRODUCTTYPE', value: 'Table' }], tier: 'GOLD', fresh: true });
  expect(parseSearchParams(new URL(url, 'http://local').searchParams)).toMatchObject({ community: 'Accounts', domain: 'Retail', tier: 'GOLD', fresh: true, filters: [{ parent: 'COMMUNITY', value: 'Accounts', label: 'Accounts' }, { parent: 'PRODUCTTYPE', value: 'Table', label: 'Table' }] });
});
test('invalid filters and tiers do not enter Explore state', () => {
  expect(parseSearchParams(new URLSearchParams('q=account&f=UNSAFE:x&f=COMMUNITY:Retail&tier=other'))).toMatchObject({ q: 'account', tier: '', filters: [{ parent: 'COMMUNITY', value: 'Retail', label: 'Retail' }] });
});
