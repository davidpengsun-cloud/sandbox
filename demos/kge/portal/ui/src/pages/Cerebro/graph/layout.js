import { forceCenter, forceCollide, forceLink, forceManyBody, forceSimulation, forceX, forceY } from "d3-force";

/* The graph's layout (cerebro.md §7.5): d3-force run to a fixed number of ticks, once, from a
   seeded random source, so the same graph is always laid out the same way. It runs in the
   layout worker, never on the page's main thread. Returns each node's position by id. A weak pull
   to the centre, stronger up and down than across, keeps the graph a wide disc that fits the
   canvas. The nodes no link holds (most business terms while term links are few) are packed in
   closer and pull harder, so they fill the gaps around the linked core instead of a ring far out
   that would force the fitted view down to a cloud of dots, and leave the core room for its names. */
export const TICKS = 300;

function seeded(seed) {
  let state = seed >>> 0 || 1;
  return () => {
    state = (state * 1_664_525 + 1_013_904_223) >>> 0;
    return state / 2 ** 32;
  };
}

export function layoutGraph({ nodes = [], links = [] } = {}, { ticks = TICKS, seed = 1 } = {}) {
  const points = nodes.map((node) => ({ id: node.id }));
  const known = new Set(points.map((point) => point.id));
  const springs = links.filter((link) => known.has(link.source) && known.has(link.target)).map((link) => ({ source: link.source, target: link.target }));
  const held = new Set(springs.flatMap((link) => [link.source, link.target]));
  const by = (linked, loose) => (point) => (held.has(point.id) ? linked : loose);
  const simulation = forceSimulation(points)
    .randomSource(seeded(seed))
    .force("link", forceLink(springs).id((point) => point.id).distance(100))
    .force("charge", forceManyBody().strength(by(-220, -30)))
    .force("collide", forceCollide(by(42, 20)))
    .force("x", forceX(0).strength(by(0.05, 0.1)))
    .force("y", forceY(0).strength(by(0.09, 0.16)))
    .force("center", forceCenter(0, 0))
    .stop();
  simulation.tick(ticks);
  return Object.fromEntries(points.map((point) => [point.id, { x: Math.round(point.x), y: Math.round(point.y) }]));
}
