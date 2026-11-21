import { expect, test } from 'vitest';
import fixture from './fixtures/steward.json';
import { ALL, KINDS, byKind, plainPassage, candidateChecks, certifiedShare, checkTone, decideBody, filterChips, kindOf, nodeOf, outcome, reviewGroups, shownRows } from './curateData';

test('proposals are grouped by the kind KGE gives each item, in Curate order, and none is dropped', () => {
  expect(KINDS.map((kind) => kind.label)).toEqual(['Term tags', 'New words', 'Filter values', 'Documents', 'Business terms', 'Cross-reference matches']);
  expect(['term_tags', 'documents', 'cross_references', 'unknown', undefined].map((kind) => kindOf({ kind, family: 'suggested_mention' })))
    .toEqual(['term_tags', 'documents', 'cross_references', 'other', 'other']);
  const kinds = byKind(fixture.queue);
  expect(kinds.flatMap((kind) => kind.rows.map((row) => row.tag)).sort()).toEqual(fixture.queue.map((row) => row.tag).sort());
  expect(kinds.at(-1)).toMatchObject({ id: 'other', waiting: 1 });
  expect(kinds.find((kind) => kind.id === 'cross_references')).toMatchObject({ waiting: 0 });
  expect(byKind([]).map((kind) => kind.id)).not.toContain('other');
});

test('a proposal opens on the node KGE names and on nothing else; a candidate term accepted carries its definition', () => {
  expect(nodeOf({ doc: 'pricing-policy' })).toBeNull();
  expect(nodeOf(fixture.queue.find((row) => row.tag === 'tag:sm1'))).toBe('document:pricing-policy');
  expect(nodeOf({ node: 'term:churn', doc: 'x' })).toBe('term:churn');
  expect(nodeOf({})).toBeNull();
  expect(nodeOf(fixture.queue.find((row) => row.tag === 'tag:xr1'))).toBeNull();
  expect(decideBody({ tag: 't', family: 'candidate_term' }, 'accept', ' Customers who leave. ')).toEqual({ tag: 't', decision: 'accept', definition: 'Customers who leave.' });
  expect(decideBody({ tag: 't', family: 'candidate_term' }, 'reject', 'x')).toEqual({ tag: 't', decision: 'reject' });
  expect(outcome({ status: 'pending_second_check', reason: 'likely personal' }, 'reject')).toBe('Waiting on a second steward · likely personal.');
  expect(outcome({ term_created: 'Churn' }, 'accept')).toContain('Term created: Churn.');
});

test('certifiedShare counts columns with a certified maps_to link over all columns, ignoring tables and proposed links', () => {
  const nodes = [{ id: 'c1', kind: 'column' }, { id: 'c2', kind: 'column' }, { id: 't', kind: 'table' }];
  const links = [{ source: 'c1', kind: 'maps_to', status: 'certified' }, { source: 'c1', kind: 'maps_to', status: 'certified' },
    { source: 'c2', kind: 'maps_to', status: 'proposed' }, { source: 't', kind: 'maps_to', status: 'certified' }];
  expect(certifiedShare({ nodes, links })).toBe(0.5);
  expect(certifiedShare({ nodes: [{ id: 't', kind: 'table' }], links: [] })).toBeNull();
});

test('candidateChecks and reviewGroups read KGE onboarding status', () => {
  expect(candidateChecks({})).toEqual([]);
  expect(candidateChecks({ candidate: { hash: 'h' } })).toEqual([{ name: 'Release gate', result: 'Not checked' }, { name: 'Sign-off', result: 'Not signed off' }]);
  const groups = reviewGroups({ units: [{ unit: 'u', items: [{ accepted_by: 'a' }, {}] }, { unit: 'empty', items: [] }], proposal: { id: 'p1', items: [{ accepted_by: 'a', certified_by: 's' }] } });
  expect(groups.map((group) => [group.label, group.items, group.accepted, group.certified, group.body])).toEqual([
    ['u', 2, 1, 0, { unit: 'u', items: 'all' }], ['Improvement proposal', 1, 1, 1, { proposal: 'p1', items: 'all' }]]);
});

test('the kind filter offers each kind with a proposal and the chosen one; All shows every kind\'s rows in order', () => {
  const kinds = byKind(fixture.queue);
  expect(filterChips(kinds, ALL).map((kind) => kind.id)).toEqual(['term_tags', 'new_words', 'documents', 'cross_references', 'other']);
  expect(filterChips(kinds, 'filter_values').map((kind) => kind.id)).toContain('filter_values');
  expect(filterChips(byKind([]), ALL)).toEqual([]);
  expect(shownRows(kinds, ALL).map(({ row, kind }) => [row.tag, kind.one])).toEqual([
    ['tag:sm1', 'Term tag'], ['tag:ct1', 'New word'], ['tag:pv1', 'Document'], ['tag:xr1', 'Cross-reference'], ['tag:nw1', 'Other']]);
  expect(shownRows(kinds, 'documents').map(({ row }) => row.tag)).toEqual(['tag:pv1']);
  expect(shownRows(kinds, 'filter_values')).toEqual([]);
});

test('a check result\'s chip tone: passed ok, failed bad, a recorded sign-off ok, anything else neutral', () => {
  expect(['Passed', 'passed', 'Failed', 'failed', 'Not checked', 'skipped'].map((result) => checkTone({ name: 'x', result }))).toEqual(['ok', 'ok', 'bad', 'bad', 'neutral', 'neutral']);
  expect(checkTone({ name: 'Sign-off', result: 'Not signed off' })).toBe('neutral');
  expect(checkTone({ name: 'Sign-off', result: 'sam.steward · 2026-11-15T10:00:00Z' })).toBe('ok');
  expect(checkTone(undefined)).toBe('neutral');
});

test('a passage reads as plain words: Markdown headings, emphasis and code ticks are taken out', () => {
  expect(plainPassage('A customer is active when `txn_count` is **one**.\n\n## Certified implementations')).toBe('A customer is active when txn_count is one. Certified implementations');
  expect(plainPassage('It is withdrawn. ## Certified implementations')).toBe('It is withdrawn. Certified implementations');
  expect(plainPassage('Issue #12 stays')).toBe('Issue #12 stays');
  expect(plainPassage(undefined)).toBe('');
});
