import { expect, test } from 'vitest';
import { layoutGraph } from './layout';

const graph = { nodes: ['a', 'b', 'c', 'd'].map((id) => ({ id })), links: [{ source: 'a', target: 'b' }, { source: 'b', target: 'c' }, { source: 'a', target: 'missing' }] };

test('the layout places every node, the same way each time, and ignores a link to no node', () => {
  const first = layoutGraph(graph);
  expect(Object.keys(first)).toEqual(['a', 'b', 'c', 'd']);
  for (const point of Object.values(first)) { expect(Number.isFinite(point.x)).toBe(true); expect(Number.isFinite(point.y)).toBe(true); }
  expect(layoutGraph(graph)).toEqual(first);
  const spread = new Set(Object.values(first).map((p) => `${p.x},${p.y}`));
  expect(spread.size).toBe(4);
});

test('an empty graph lays out to nothing', () => {
  expect(layoutGraph({ nodes: [], links: [] })).toEqual({});
});

test('nodes no link holds stay in a compact, wide disc around the linked ones, not a ring far out', () => {
  const linked = Array.from({ length: 40 }, (_, i) => ({ id: `t${i}` }));
  const loose = Array.from({ length: 110 }, (_, i) => ({ id: `term${i}` }));
  const links = linked.slice(1).map((node, i) => ({ source: linked[Math.floor(i / 3)].id, target: node.id }));
  const at = Object.values(layoutGraph({ nodes: [...linked, ...loose], links }));
  const xs = at.map((p) => p.x);
  const ys = at.map((p) => p.y);
  const width = Math.max(...xs) - Math.min(...xs);
  const height = Math.max(...ys) - Math.min(...ys);
  expect(width).toBeLessThan(2200);
  expect(width).toBeGreaterThan(height);
});
