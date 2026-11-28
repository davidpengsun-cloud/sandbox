import React from "react";

/* The graph of the last answer, small (the owner's prototype, Chat mode: a card at the foot of the sessions rail): the
   nodes and links the answer used, from its `graph_path` ({ nodes: [{ id, label, name, type, used }], edges: [{ from,
   to, type, used }] }, KGE's contextpath.py), drawn on an oval in plain SVG. A non-figure card (#622) draws its own
   small graph with it: `label` and `caption` name it. #651 (B9): it is read-only; it has no "Open graph" button, and
   it no longer sits in the rail. */

const WIDTH = 280;
const HEIGHT = 160;
const MAX_NODES = 12;

const nameOf = (node) => String(node.label || node.name || node.id);

/** The used nodes and the links between them, at most MAX_NODES, on a ring. */
export function miniLayout(path) {
  const all = Array.isArray(path?.nodes) ? path.nodes : [];
  const nodes = all.filter((n) => n && n.id != null && n.used !== false).slice(0, MAX_NODES);
  const at = new Map();
  const rx = WIDTH / 2 - 56;
  const ry = HEIGHT / 2 - 28;
  nodes.forEach((node, i) => {
    const angle = (2 * Math.PI * i) / Math.max(nodes.length, 1) - Math.PI / 2;
    const one = nodes.length === 1;
    at.set(String(node.id), { x: WIDTH / 2 + (one ? 0 : rx * Math.cos(angle)), y: HEIGHT / 2 + (one ? 0 : ry * Math.sin(angle)), name: nameOf(node), type: node.type });
  });
  const edges = (Array.isArray(path?.edges) ? path.edges : [])
    .filter((e) => e && e.used !== false && at.has(String(e.from)) && at.has(String(e.to)))
    .map((e) => ({ from: at.get(String(e.from)), to: at.get(String(e.to)), key: `${e.from}\u0000${e.to}\u0000${e.type || ""}` }));
  return { nodes: [...at.entries()].map(([id, p]) => ({ id, ...p })), edges, more: Math.max(all.filter((n) => n && n.used !== false).length - nodes.length, 0) };
}

export default function MiniGraph({ path, label = "The last answer's graph", caption }) {
  const { nodes, edges, more } = miniLayout(path);
  return (
    <figure className="cerebro-mini" aria-label={label}>
      <div className="cerebro-mini-head">
        <span className="cerebro-mini-title">Graph</span>
      </div>
      {nodes.length ? (
        <svg viewBox={`0 0 ${WIDTH} ${HEIGHT}`} role="img" aria-label={`Path of ${nodes.length} node${nodes.length === 1 ? "" : "s"}: ${nodes.map((n) => n.name).join(", ")}`}>
          {edges.map((e) => <line key={e.key} x1={e.from.x} y1={e.from.y} x2={e.to.x} y2={e.to.y} className="cerebro-mini-link" />)}
          {nodes.map((n) => (
            <g key={n.id} className="cerebro-mini-node" data-type={n.type}>
              {n.type === "term"
                ? <rect x={n.x - 5} y={n.y - 5} width={10} height={10} transform={`rotate(45 ${n.x} ${n.y})`} />
                : <circle cx={n.x} cy={n.y} r={5} />}
              <text x={n.x} y={n.y < HEIGHT / 2 ? n.y - 10 : n.y + 18} textAnchor="middle">{n.name.length > 18 ? `${n.name.slice(0, 17)}…` : n.name}</text>
            </g>
          ))}
        </svg>
      ) : null}
      <figcaption className="cerebro-muted">
        {caption ?? (nodes.length ? "The path the last answer read." : "No answer with a path yet.")}
        {more ? ` And ${more} more in the graph.` : null}
      </figcaption>
    </figure>
  );
}
