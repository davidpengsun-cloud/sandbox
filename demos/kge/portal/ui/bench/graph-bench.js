/* The page the graph benchmark drives (cerebro.md §7.5): Cerebro's GraphView, drawn as the
   portal draws it, on a synthetic graph of 1,000 nodes and 2,500 links, laid out by the real
   worker. Not part of the portal's build; scripts/graph-benchmark.mjs serves and measures it. */
import { createElement as h, useState } from "react";
import { createRoot } from "react-dom/client";
import GraphView, { layoutWorker } from "../src/pages/Cerebro/graph/GraphView";

const NODES = 1000;
const LINKS = 2500;
const KINDS = ["term", "table", "document", "domain", "community", "person"];

/* No column, so none is folded away; each link once and never to itself, so all 2,500 are drawn. */
export function syntheticGraph() {
  const nodes = Array.from({ length: NODES }, (_, i) => ({ id: `n${i}`, kind: KINDS[i % KINDS.length], name: `node ${i}` }));
  const seen = new Set();
  const links = [];
  for (let i = 0; links.length < LINKS; i += 1) {
    const source = i % NODES;
    const target = (source * 7 + 1 + Math.floor(i / NODES) * 131) % NODES;
    const key = `${source}>${target}`;
    if (source === target || seen.has(key)) continue;
    seen.add(key);
    links.push({ source: `n${source}`, target: `n${target}`, kind: "related_to", status: "certified", why: "synthetic" });
  }
  return { nodes, links };
}

const bench = (window.__bench = { start: performance.now() });
const graph = syntheticGraph();
const fetcher = async () => ({ ok: true, status: 200, json: async () => graph });
const makeWorker = () => {
  const worker = layoutWorker();
  const post = worker.postMessage.bind(worker);
  worker.postMessage = (message) => {
    bench.layoutStart = performance.now();
    bench.sent = { nodes: message.graph.nodes.length, links: message.graph.links.length };
    post(message);
  };
  worker.addEventListener("message", () => { bench.layoutMs = performance.now() - bench.layoutStart; });
  return worker;
};

function Bench() {
  const [state, setState] = useState({ mode: "graph", node: null, graphPosition: null, graph: null });
  const actions = {
    selectNode: (node) => setState((s) => ({ ...s, node })),
    setGraphPosition: (graphPosition) => setState((s) => ({ ...s, graphPosition })),
    setGraph: (kept) => { bench.ready = performance.now(); setState((s) => ({ ...s, graph: kept })); },
  };
  return h(GraphView, { state, actions, fetcher, makeWorker });
}

createRoot(document.getElementById("root")).render(h(Bench));
