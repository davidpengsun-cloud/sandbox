import { expect, test } from 'vitest';
import { apartOf, armRows, countsOf } from './Quality';

const result = {
  graph: { by_kind: { within: { passed: 1, of: 1 } }, apart: [{ id: 'S07', status: 'unavailable', why: 'expectation not available on live: no mapping' }] },
  schema: { by_kind: { within: { passed: 0, of: 1 } } },
  refused: [{ id: 'S07', runtime: 'live', reason: 'no mapping' }],
  by_runtime: { live: { by_kind: {} } },
  heldout: { graph: { within: { passed: 1, of: 1 } } },
  catalogue: { graph: { abstain: { passed: 1, of: 1 } } },
  runtimes: ['live', 'reference'],
  approved: { sets: [{ name: 'sage_v1', version: 'v4', items: 2 }], graph: { by_kind: { within: { passed: 2, of: 2 } }, apart: [] } },
};

test('the golden set table shows the arms and the approved items, never the report\'s other keys', () => {
  expect(armRows(result).map(([arm]) => arm)).toEqual(['graph', 'schema', 'graph, approved reference SQL']);
});

test('items reported apart are listed with their arm and why, apart from the passes and failures', () => {
  expect(apartOf(result)).toEqual([{ id: 'S07', status: 'unavailable', why: 'expectation not available on live: no mapping', arm: 'graph' }]);
  expect(apartOf(null)).toEqual([]);
});

test('an arm counts its kinds under by_kind, as the API job writes them, or directly, as make eval writes them', () => {
  expect(countsOf({ by_kind: { within: { passed: 3, of: 4 } } }, 'within')).toEqual({ passed: 3, of: 4 });
  expect(countsOf({ within: { passed: 2, of: 5 }, apart: [] }, 'within')).toEqual({ passed: 2, of: 5 });
  expect(countsOf({}, 'cross')).toEqual({ passed: 0, of: 0 });
});
