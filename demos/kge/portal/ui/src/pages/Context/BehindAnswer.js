import React, { useEffect, useId, useLayoutEffect, useRef, useState } from "react";
import { TabButtons } from "./ContextTabs";
import { WayForward, armOf, citedAs, periodWords, CopyButton } from "./Answer";
import { declineText, plain, saysWords } from "./contextState";

const GRAPH_COLUMNS = ["words", "vocabulary", "meaning", "rules", "tables"];
const BOTTOM_GROUPS = ["access", "runtime"];
const COLUMN_WIDTH = { words: 96, vocabulary: 144, meaning: 168, rules: 156, tables: 156 };
const BOTTOM_WIDTH = 150;
const NODE_HEIGHT = 48;
const GAP_X = 72;
const WORDS_GAP_X = 52;
const MAX_GAP_X = 150;
const LABEL_CHAR_PX = 6.7;
const GAP_Y = 18;
const PAD = 16;
const LOOP_ROOM = 72;
const LABEL_HEIGHT = 13;
const MAX_SHOWN_NODES = 100;
const TYPE_TITLES = { RowSelector: "Row selector" };
const PLAIN_NAME_TYPES = new Set(["Words", "Term", "Runtime"]);
const LEGEND = [
  ["words", "Words asked"], ["vocabulary", "Vocabulary"], ["meaning", "Meaning"], ["rules", "Rules and entities"],
  ["tables", "Reference tables"], ["certified", "Certified"], ["access", "Access"],
];

const typeTitle = type => TYPE_TITLES[type] || type || "Node";
const nameOf = n => n.name || n.label || n.id;
const columnOf = n => (BOTTOM_GROUPS.includes(n.group) || GRAPH_COLUMNS.includes(n.group) ? n.group : "rules");
const isScd2 = n => (n.badges || []).some(b => b?.kind === "SCD2");

function nodeHeight(n, width) {
  if (n.progress) return NODE_HEIGHT + 36;
  if (n.collapsed) return NODE_HEIGHT + 16;
  if (n.state !== "refused" || !n.reason) return NODE_HEIGHT;
  const perLine = Math.max(12, Math.floor((width - 22) / 6.6));
  return NODE_HEIGHT + Math.min(5, Math.ceil(String(n.reason).length / perLine)) * 15 + 4;
}

/** Collapse whole node types, largest first, until no more than MAX_SHOWN_NODES remain; expanded types stay open. */
function collapseLargeTypes(nodes, edges, expanded) {
  if (nodes.length <= MAX_SHOWN_NODES) return { nodes, edges, groups: [] };
  const counts = new Map();
  nodes.forEach(n => counts.set(n.type, (counts.get(n.type) || 0) + 1));
  const candidates = [...counts].filter(([type, count]) => count > 1 && !expanded.has(type)).sort((x, y) => y[1] - x[1]);
  const folded = new Set();
  let shown = nodes.length;
  for (const [type, count] of candidates) {
    if (shown <= MAX_SHOWN_NODES) break;
    folded.add(type);
    shown -= count - 1;
  }
  if (!folded.size) return { nodes, edges, groups: [] };
  const groupId = type => `group:${type}`;
  const into = new Map();
  const out = [];
  for (const n of nodes) {
    if (!folded.has(n.type)) { out.push(n); continue; }
    const id = groupId(n.type);
    into.set(n.id, id);
    if (!out.some(x => x.id === id)) out.push({ id, type: n.type, group: n.group, collapsed: counts.get(n.type), members: [] });
    out.find(x => x.id === id).members.push(n);
  }
  out.filter(g => g.collapsed).forEach(g => {
    const refused = g.members.filter(m => m.state === "refused"), certified = g.members.filter(m => m.certified);
    g.name = [`${typeTitle(g.type)} ×${g.collapsed}`, refused.length ? `${refused.length} refused` : "", certified.length ? `${certified.length} certified` : ""].filter(Boolean).join(" · ");
    g.state = refused.length ? "refused" : "done";
    g.certified = certified.length > 0;
    g.used = g.members.some(m => m.used !== false);
  });
  const seen = new Set();
  const outEdges = [];
  for (const e of edges) {
    const from = into.get(e.from) || e.from, to = into.get(e.to) || e.to;
    const key = `${from}|${to}|${e.label || e.type}`;
    if (from === to || seen.has(key)) continue;
    seen.add(key);
    outEdges.push({ ...e, from, to });
  }
  return { nodes: out, edges: outEdges, groups: [...folded] };
}

/** Tables in join order: each join chain runs down the column, joins the answer made before those it only read; other tables keep list order. */
function chainJoins(tables, edges) {
  const ids = new Set(tables.map(n => n.id));
  const joins = edges.filter(e => e.type === "JoinRule" && e.from !== e.to && ids.has(e.from) && ids.has(e.to))
    .sort((x, y) => (x.used === false) - (y.used === false));
  const byId = new Map(tables.map(n => [n.id, n]));
  const seen = new Set(), out = [];
  const visit = id => {
    if (seen.has(id)) return;
    seen.add(id);
    out.push(byId.get(id));
    joins.filter(e => e.from === id).forEach(e => visit(e.to));
  };
  tables.forEach(n => visit(n.id));
  return out;
}

/** Deterministic left-to-right layout: one column per group, stacked in list order; a bottom row holds access under vocabulary and meaning, and the runtime under the tables. */
function layoutGraph(nodes, edges, labels) {
  const byGroup = Object.fromEntries([...GRAPH_COLUMNS, ...BOTTOM_GROUPS].map(c => [c, []]));
  nodes.forEach(n => byGroup[columnOf(n)].push(n));
  byGroup.tables = chainJoins(byGroup.tables, edges);
  const present = GRAPH_COLUMNS.filter(c => byGroup[c].length);
  const columnIndex = Object.fromEntries(nodes.map(n => [n.id, present.indexOf(columnOf(n))]));
  const widest = present.map(() => 0);
  edges.forEach(e => {
    const from = columnIndex[e.from], to = columnIndex[e.to];
    if (from < 0 || to < 0 || Math.abs(from - to) !== 1) return;
    widest[Math.min(from, to)] = Math.max(widest[Math.min(from, to)], labels.get(e).text.length * LABEL_CHAR_PX);
  });
  const columnX = {};
  let x = PAD;
  present.forEach((c, i) => {
    columnX[c] = x;
    const least = c === "words" ? WORDS_GAP_X : GAP_X;
    x += COLUMN_WIDTH[c] + Math.min(MAX_GAP_X, Math.max(least, Math.ceil(widest[i]) + 16));
  });
  const mainRight = present.length ? columnX[present.at(-1)] + COLUMN_WIDTH[present.at(-1)] : PAD;
  const pos = {};
  let mainBottom = PAD;
  for (const c of present) {
    let y = PAD;
    byGroup[c].forEach((n, index) => {
      const h = nodeHeight(n, COLUMN_WIDTH[c]);
      pos[n.id] = { x: columnX[c], y, w: COLUMN_WIDTH[c], h, column: c, band: "main", index, last: index === byGroup[c].length - 1 };
      y += h + GAP_Y;
    });
    mainBottom = Math.max(mainBottom, y - GAP_Y);
  }
  const slotX = present.map(c => columnX[c]);
  const firstSlot = group => {
    const wanted = group === "access" ? ["vocabulary", "meaning", "rules", "tables"] : ["tables", "rules", "meaning", "vocabulary"];
    const hit = wanted.find(c => present.includes(c));
    return hit ? present.indexOf(hit) : 0;
  };
  const bottomTop = present.length ? mainBottom + GAP_Y * 2 : PAD;
  let bottomEnd = 0, nextSlot = 0, right = mainRight;
  for (const group of BOTTOM_GROUPS) {
    let slot = Math.max(firstSlot(group), nextSlot);
    byGroup[group].forEach((n, index) => {
      const bx = slot < slotX.length ? slotX[slot] : right + GAP_X;
      const h = nodeHeight(n, BOTTOM_WIDTH);
      pos[n.id] = { x: bx, y: bottomTop, w: BOTTOM_WIDTH, h, column: `slot-${slot}`, band: "bottom", index, last: true };
      right = Math.max(right, bx + BOTTOM_WIDTH);
      bottomEnd = Math.max(bottomEnd, bottomTop + h);
      slot += 1;
    });
    nextSlot = slot;
  }
  const order = [...present.flatMap(c => byGroup[c]), ...BOTTOM_GROUPS.flatMap(g => byGroup[g])];
  return { pos, order, width: right + LOOP_ROOM + PAD, height: Math.max(mainBottom, bottomEnd) + PAD };
}

/** Each edge's label text: repeated labels into or out of one node are drawn once, on the middle edge, counted. */
function edgeLabels(edges) {
  const byTarget = new Map(), bySource = new Map();
  edges.forEach(e => {
    const label = e.label || e.type || "";
    const t = `${e.to}|${label}`, s = `${e.from}|${label}`;
    byTarget.set(t, [...(byTarget.get(t) || []), e]);
    bySource.set(s, [...(bySource.get(s) || []), e]);
  });
  return new Map(edges.map(e => {
    const label = e.label || e.type || "";
    const fanIn = byTarget.get(`${e.to}|${label}`), fanOut = bySource.get(`${e.from}|${label}`);
    const bundle = fanIn.length >= 2 ? fanIn : fanOut.length >= 2 ? fanOut : null;
    const text = !label ? "" : bundle ? (bundle[Math.floor(bundle.length / 2)] === e ? `${label} ×${bundle.length}` : "") : label;
    return [e, { text, bundle }];
  }));
}

const cubic = (t, p0, p1, p2, p3) => { const u = 1 - t; return u * u * u * p0 + 3 * u * u * t * p1 + 3 * u * t * t * p2 + t * t * t * p3; };
const overlaps = (r, q) => r.x1 < q.x2 && q.x1 < r.x2 && r.y1 < q.y2 && q.y1 < r.y2;

/** A label's box: horizontal text sits on its baseline, turned text runs down from its centre. */
function labelBox(text, lx, ly, anchor, turn) {
  const w = text.length * LABEL_CHAR_PX;
  if (turn) return { x1: lx - LABEL_HEIGHT / 2 - 1, x2: lx + LABEL_HEIGHT / 2 + 1, y1: ly - w / 2 - 2, y2: ly + w / 2 + 2 };
  const left = anchor === "end" ? lx - w : anchor === "start" ? lx : lx - w / 2;
  return { x1: left - 2, x2: left + w + 2, y1: ly - LABEL_HEIGHT + 2, y2: ly + 3 };
}

/** Each label takes the first of its candidate spots that covers no node and no label already placed. */
function placeLabels(routes, pos) {
  const taken = Object.values(pos).map(p => ({ x1: p.x, x2: p.x + p.w, y1: p.y, y2: p.y + p.h }));
  return routes.map(route => {
    if (!route.text) return route;
    const spots = route.spots.map(([lx, ly, anchor, turn]) => ({ lx, ly, anchor, turn, box: labelBox(route.text, lx, ly, anchor, turn) }));
    const free = spots.find(spot => !taken.some(box => overlaps(spot.box, box))) || spots[0];
    taken.push(free.box);
    return { ...route, lx: free.lx, ly: free.ly, anchor: free.anchor, turn: free.turn };
  });
}

/** Where each edge runs, and the spots its label may sit, best first. */
function routeEdges(edges, pos, labels) {
  let loops = 0;
  const OFFSETS = [0, -9, 9, -18, 18, -27, 27];
  return placeLabels(edges.map(e => {
    const a = pos[e.from], b = pos[e.to];
    const { text, bundle } = labels.get(e);
    const ay = a.y + NODE_HEIGHT / 2, by = b.y + NODE_HEIGHT / 2;
    let d;
    const spots = [];
    const turned = (x0, y1, y2, outward) => {
      for (const t of [0.5, 0.3, 0.7, 0.15, 0.85]) for (const dx of outward) spots.push([x0 + dx, y1 + (y2 - y1) * t, "middle", 90]);
    };
    if (e.from === e.to) {
      const x0 = a.x + a.w;
      d = `M ${x0} ${a.y + 12} C ${x0 + 34} ${a.y - 6}, ${x0 + 34} ${a.y + a.h + 6}, ${x0 + 3} ${a.y + a.h - 12}`;
      turned(x0 + 30, a.y + a.h / 2, a.y + a.h / 2, [0, 12, 24]);
    } else if (a.x === b.x) {
      const adjacent = a.band === b.band ? Math.abs(a.index - b.index) === 1 : (b.band === "bottom" ? a.last : b.last);
      const cx = a.x + a.w / 2;
      if (adjacent) {
        const down = b.y > a.y;
        const y1 = down ? a.y + a.h : a.y, y2 = down ? b.y - 3 : b.y + b.h + 3;
        d = `M ${cx} ${y1} L ${cx} ${y2}`;
        const mid = (y1 + y2) / 2 + 4;
        spots.push([cx - 6, mid, "end", 0], [cx + 6, mid, "start", 0]);
      } else {
        loops += 1;
        const x0 = a.x + a.w, bulge = a.band === b.band ? 22 + (loops % 3) * 12 : 30;
        d = `M ${x0} ${ay} C ${x0 + bulge} ${ay}, ${x0 + bulge} ${by}, ${x0 + 3} ${by}`;
        turned(x0 + bulge * 0.75 + 4, ay, by, [0, 12, 24, 36]);
      }
    } else {
      const forward = b.x > a.x;
      const x1 = forward ? a.x + a.w : a.x, x2 = forward ? b.x - 3 : b.x + b.w + 3;
      const mx = (x1 + x2) / 2;
      d = `M ${x1} ${ay} C ${mx} ${ay}, ${mx} ${by}, ${x2} ${by}`;
      const bundleY = bundle && bundle.map(o => (pos[o.from].y + pos[o.to].y + NODE_HEIGHT) / 2).reduce((sum, m) => sum + m, 0) / bundle.length;
      for (const t of [0.5, 0.35, 0.65, 0.2, 0.8]) {
        const px = cubic(t, x1, mx, mx, x2), py = bundle && t === 0.5 ? bundleY : cubic(t, ay, ay, by, by);
        for (const dy of OFFSETS) spots.push([px, py - 4 + dy, "middle", 0]);
      }
    }
    return { edge: e, d, text, spots, members: bundle || [e] };
  }), pos);
}

function GraphNode({ node: n, place, selected, onSelect, onExpand }) {
  const refused = n.state === "refused";
  const classes = ["gnode", isScd2(n) ? "has-scd2" : "", n.collapsed ? "g-collapsed" : `g-${columnOf(n)}`, n.certified ? "certified" : "", refused ? "refused" : "", n.used === false ? "unused" : "", n.carried ? "carried" : "", selected ? "selected" : ""];
  return (
    <button
      type="button"
      className={classes.filter(Boolean).join(" ")}
      style={{ left: place.x, top: place.y, width: place.w, height: place.h }}
      data-node-id={n.id}
      data-type={n.type}
      data-group={n.group}
      data-state={n.state || "done"}
      data-used={n.used === false ? "false" : "true"}
      data-carried={n.carried ? "true" : undefined}
      title={[nameOf(n), n.label && n.label !== nameOf(n) ? n.label : ""].filter(Boolean).join(" · ")}
      aria-pressed={n.collapsed ? undefined : selected}
      aria-expanded={n.collapsed ? false : undefined}
      onClick={() => (n.collapsed ? onExpand(n.type) : onSelect(n.id))}
    >
      <span className="gnode-type">{n.collapsed ? "Group · select to expand" : `${n.used === false ? "Not used · " : ""}${typeTitle(n.type)}${n.carried ? " · earlier turn" : ""}${n.certified ? " · certified" : ""}`}</span>
      {isScd2(n) && <span className="gnode-scd2">SCD2</span>}
      <span className={`gnode-name${PLAIN_NAME_TYPES.has(n.type) || n.collapsed ? "" : " mono"}`}>{nameOf(n)}</span>
      {refused && n.reason && <span className="gnode-reason">{n.reason}</span>}
      {n.progress && <span className="gnode-progress">{progressText(n.progress)}</span>}
    </button>
  );
}

function progressText(progress) {
  const ms = Math.max(0, progress.elapsed_ms || 0);
  const elapsed = ms < 1000 ? `${ms} ms` : `${(ms / 1000).toFixed(1)} s`;
  const bytes = progress.bytes_processed;
  const units = ["B", "KB", "MB", "GB", "TB", "PB"];
  const power = bytes > 0 ? Math.min(units.length - 1, Math.floor(Math.log(bytes) / Math.log(1000))) : 0;
  const size = bytes == null ? "bytes unavailable" : `${Number((bytes / 1000 ** power).toFixed(power ? 1 : 0))} ${units[power]}`;
  return `Statement ${progress.statement} · ${progress.state} · ${elapsed} · ${size}`;
}

const edgeKey = e => `${e.from}|${e.to}|${e.type}|${e.label}`;
const NOTE = "Solid edges are stored graph edges; dashed edges are the planner matching the question's words. Select a node, or an underlined edge label, to see its detail.";

function SelectedDetail({ selected, nodeName }) {
  if (selected.kind === "edges") {
    const { label, members } = selected;
    const only = members.length === 1 ? members[0] : null;
    return (
      <section className="path-detail graph-detail" id="path-detail" aria-label="Selected edge detail" aria-live="polite">
        <div className="row">
          <h3 className="sect">Selected: {only ? `${only.type === only.label ? "Edge" : only.type} ${only.label || only.type}` : `${label}, ${members.length} edges`}</h3>
          {members.some(e => e.state === "refused") && <span className="badge bad">refused</span>}
          {members.some(e => e.planner) && <span className="badge">planner match</span>}
          {members.every(e => e.used === false) && <span className="badge">read, not used</span>}
        </div>
        {only && <p>{nodeName(only.from)} → {nodeName(only.to)}</p>}
        <pre className="graph-detail-text">{only ? only.detail || only.reason : members.map(e => `${nodeName(e.from)} → ${nodeName(e.to)}${e.detail || e.reason ? `\n  ${String(e.detail || e.reason).replace(/\n/g, "\n  ")}` : ""}`).join("\n")}</pre>
      </section>
    );
  }
  return (
    <section className="path-detail graph-detail" id="path-detail" aria-label="Selected node detail" aria-live="polite">
      <div className="row">
        <h3 className="sect">Selected: {typeTitle(selected.type)} {nameOf(selected)}{selected.label && selected.label !== nameOf(selected) ? ` · ${selected.label}` : ""}</h3>
        {selected.state === "refused" && <span className="badge bad">refused</span>}
        {selected.certified && <span className="badge ok">certified</span>}
        {selected.used === false && <span className="badge">read, not used</span>}
        {selected.carried && <span className="badge">from an earlier turn</span>}
        {isScd2(selected) && <span className="badge warn">SCD2</span>}
      </div>
      {selected.reason && <p className="path-reason">{selected.reason}</p>}
      {selected.detail && <pre className="graph-detail-text">{selected.detail}</pre>}
      {selected.type === "Runtime" && selected.sql && <div className="codeblock"><CopyButton label="Copy the query" value={selected.sql} /><pre>{selected.sql}</pre></div>}
    </section>
  );
}

export function GraphPath({ answer: a }) {
  const graph = a?.graph_path;
  const earlier = Array.isArray(a?.history) && a.history.length > 1 ? a.history.slice(0, -1).map(turn => turn?.question).filter(q => typeof q === "string") : [];
  const carriedOver = n => n.type === "Words" && earlier.length > 0 && !saysWords(a.question, nameOf(n)) && earlier.some(q => saysWords(q, nameOf(n)));
  const allNodes = (graph?.nodes || []).filter(n => n?.id).map(n => a.progressByNode?.[n.id] ? { ...n, progress: a.progressByNode[n.id] } : n).map(n => carriedOver(n) ? { ...n, carried: true } : n);
  const carriedCount = allNodes.filter(n => n.carried && (n.used !== false)).length;
  const allEdges = (graph?.edges || []).filter(e => e?.from && e?.to);
  const unusedCount = Number.isFinite(graph?.unused) ? graph.unused : allNodes.filter(n => n.used === false).length + allEdges.filter(e => e.used === false).length;
  const [showUnused, setShowUnused] = useState(false);
  const [picked, setPicked] = useState(null);
  const [expanded, setExpanded] = useState(() => new Set());
  const previous = useRef(a);
  const markerId = useId().replace(/:/g, "");
  useEffect(() => {
    if (a?.question !== previous.current?.question || !(a?.streaming || a?.stopped || previous.current?.streaming)) { setPicked(null); setExpanded(new Set()); }
    if (a?.question !== previous.current?.question) setShowUnused(false);
    previous.current = a;
  }, [a]);
  if (!allNodes.length) {
    return a?.streaming
      ? <p className="graph-building" role="status" aria-busy="true">Building the graph path…</p>
      : <p>{graph?.error || "No graph path was recorded for this answer."}</p>;
  }

  const visibleNodes = showUnused ? allNodes : allNodes.filter(n => n.used !== false);
  const visibleIds = new Set(visibleNodes.map(n => n.id));
  const visibleEdges = allEdges.filter(e => visibleIds.has(e.from) && visibleIds.has(e.to) && (showUnused || e.used !== false));
  const folded = collapseLargeTypes(visibleNodes, visibleEdges, expanded);
  const labels = edgeLabels(folded.edges);
  const layout = layoutGraph(folded.nodes, folded.edges, labels);
  const routes = routeEdges(folded.edges, layout.pos, labels);
  const usedCount = allNodes.filter(n => n.used !== false).length;
  const shownIds = new Set(folded.nodes.map(n => n.id));
  const selectable = layout.order.filter(n => !n.collapsed);
  const fallback = selectable.find(n => n.state === "refused") || selectable.find(n => n.type === "Context") || selectable[0];
  const pickedRoute = picked?.edge && routes.find(r => r.text && edgeKey(r.edge) === picked.edge);
  const selected = pickedRoute ? { kind: "edges", label: pickedRoute.text, members: pickedRoute.members }
    : (picked?.node && shownIds.has(picked.node) && visibleNodes.find(n => n.id === picked.node)) || fallback;
  const byId = new Map([...allNodes, ...folded.nodes].map(n => [n.id, n]));
  const nodeName = id => {
    const n = byId.get(id);
    return !n ? id : n.collapsed ? `${nameOf(n)} (grouped)` : `${typeTitle(n.type)} ${nameOf(n)}`;
  };
  const expand = type => setExpanded(old => new Set([...old, type]));
  const collapse = type => setExpanded(old => new Set([...old].filter(t => t !== type)));
  const reopened = [...expanded].filter(type => visibleNodes.filter(n => n.type === type).length > 1 && visibleNodes.length > MAX_SHOWN_NODES);
  const arrow = kind => `url(#${markerId}-${kind})`;
  const pickEdge = route => setPicked({ edge: edgeKey(route.edge) });

  return (
    <div className="path-view graph-view">
      <div className="graph-toolbar">
        <div className="graph-chips">
          <span className="badge">The path this answer read: {usedCount} {usedCount === 1 ? "node" : "nodes"}</span>
          {folded.groups.length > 0 && <span className="badge">Over {MAX_SHOWN_NODES} nodes: {folded.groups.map(typeTitle).join(", ")} grouped; select a group to expand it</span>}
          {reopened.map(type => <button key={type} type="button" className="graph-collapse" onClick={() => collapse(type)}>Group the {typeTitle(type)} nodes again</button>)}
        </div>
        <label className="switch graph-unused">
          <input type="checkbox" checked={showUnused} disabled={!unusedCount} onChange={e => setShowUnused(e.target.checked)} />
          Also show what was read but not used ({unusedCount})
        </label>
      </div>
      <div className="graph-scroll" role="region" aria-label="Context graph path: the nodes and edges this answer read" tabIndex={0}>
        <div className="graph-stage" style={{ width: layout.width, height: layout.height }}>
          <svg className="graph-edges" width={layout.width} height={layout.height} aria-hidden="true">
            <defs>
              {["read", "planner", "refused", "unused"].map(kind => (
                <marker key={kind} id={`${markerId}-${kind}`} className={`gedge-arrow ${kind}`} viewBox="0 0 6 6" refX="5" refY="3" markerWidth="6" markerHeight="6" orient="auto">
                  <path d="M0 0 L6 3 L0 6 Z" />
                </marker>
              ))}
            </defs>
            {routes.map(({ edge: e, d }, i) => {
              const kind = e.state === "refused" ? "refused" : e.planner ? "planner" : "read";
              return (
                <g key={`${edgeKey(e)}|${i}`} className={`gedge ${kind}${e.used === false ? " unused" : ""}`} data-edge={`${e.from}->${e.to}`} data-type={e.type} data-planner={e.planner ? "true" : "false"} data-used={e.used === false ? "false" : "true"}>
                  <path d={d} markerEnd={arrow(e.used === false && kind === "read" ? "unused" : kind)} />
                </g>
              );
            })}
          </svg>
          {layout.order.map(n => (
            <GraphNode key={n.id} node={n} place={layout.pos[n.id]} selected={selected?.kind !== "edges" && selected?.id === n.id} onSelect={id => setPicked({ node: id })} onExpand={expand} />
          ))}
          <svg className="graph-labels" width={layout.width} height={layout.height}>
            {routes.filter(r => r.text).map((route, i) => {
              const { edge: e, text, lx, ly, anchor, turn, members } = route;
              const selectable = members.some(m => m.detail || m.reason);
              const isSelected = selected?.kind === "edges" && edgeKey(e) === picked?.edge;
              const classes = [e.used === false ? "unused" : "", selectable ? "selectable" : "", isSelected ? "selected" : ""].filter(Boolean).join(" ") || undefined;
              const label = <text x={lx} y={ly} textAnchor={anchor} transform={turn ? `rotate(${turn} ${lx} ${ly})` : undefined}>{text}</text>;
              return selectable ? (
                <g key={`${edgeKey(e)}|${i}`} className={classes} data-edge-label={`${e.from}->${e.to}`} role="button" tabIndex={0}
                  aria-pressed={isSelected} aria-label={`Edge ${text}: ${nodeName(e.from)} to ${nodeName(e.to)}${members.length > 1 ? ` and ${members.length - 1} more` : ""}`}
                  onClick={() => pickEdge(route)} onKeyDown={ev => { if (ev.key === "Enter" || ev.key === " ") { ev.preventDefault(); pickEdge(route); } }}>
                  {label}
                </g>
              ) : (
                <g key={`${edgeKey(e)}|${i}`} className={classes} data-edge-label={`${e.from}->${e.to}`} aria-hidden="true">{label}</g>
              );
            })}
          </svg>
        </div>
      </div>
      <div className="graph-legend" aria-label="Legend">
        {LEGEND.map(([key, text]) => <span key={key} className="graph-legend-item"><span className={`graph-swatch g-${key}`} aria-hidden="true" />{text}</span>)}
        <span className="graph-legend-item"><span className="gnode-scd2">SCD2</span>versioned table</span>
        <span className="graph-legend-item"><svg width="28" height="8" aria-hidden="true"><path className="graph-legend-dash" d="M0 4 L28 4" /></svg>dashed: the planner matching the question's words</span>
        {carriedCount > 0 && <span className="graph-legend-item graph-legend-carried"><span className="graph-swatch g-words carried" aria-hidden="true" />words carried over from an earlier turn</span>}
        {showUnused && <span className="graph-legend-item"><svg width="28" height="8" aria-hidden="true"><path className="graph-legend-dot" d="M0 4 L28 4" /></svg>dotted, or a dashed card marked “not used”: read but not used</span>}
      </div>
      {selected && <SelectedDetail selected={selected} nodeName={nodeName} />}
      <p className="small muted graph-note">{NOTE}</p>
    </div>
  );
}

export function Sources({ answer: a, source }) {
  let cited = [], unused = [], held = [], defined = [];
  if (a.behaviour === "glossary_definition") {
    defined = [{ doc: a.term, title: "the pack's glossary", why: a.steward && `steward ${a.steward}`, excerpt: a.definition }];
    unused = a.references || [];
  } else if (a.behaviour === "combined") {
    cited = a.citations || []; unused = a.not_cited || []; held = a.withheld || [];
  } else if (a.behaviour === "cite") {
    cited = (a.cited_docs || (a.cite || []).map(doc => ({ doc }))).map(d => ({ ...d, why: d.shelf === "certified" ? "certified shelf" : `${d.shelf || "uncertified"} shelf: cited all the same, as this arm cannot tell` }));
  } else if (a.citations) {
    cited = a.citations.map(c => ({ ...c, why: citedAs(c) + (c.cited_as && c.why ? ` · ${c.why}` : "") }));
    unused = (a.superseded_not_cited || []).map(doc => ({ doc, why: "not in force for the period asked" })).concat(a.references || []);
    held = (a.not_ai_ready || []).concat(a.not_readable || []);
  } else {
    cited = (a.cite || []).map(doc => ({ doc, why: "certified" }));
    unused = (a.superseded_not_cited || []).map(doc => ({ doc, why: "superseded" })).concat((a.uncertified_definition_not_cited || []).map(d => ({ ...d, doc: d.id, why: "uncertified definition" })), (a.uncertified_context_not_cited || []).map(d => ({ ...d, doc: d.id, why: "uncertified" })));
  }
  if (![defined, cited, unused, held].some(rows => rows.length)) return <p>This answer rests on no documents. A combined or definitional answer lists what it cites, what it did not use, and what the privacy gate held back.</p>;
  return <>{[["Defined by", defined], ["Cited", cited], ["Shown, not used", unused], ["Held back", held]].filter(([, rows]) => rows.length).map(([title, rows]) => <section key={title}><h3 className="sect">{title}</h3><ul className="cites">{rows.map((row, i) => <li key={i} data-n={row.n} className={String(row.n) === String(source) ? "hl" : ""}>{row.n && <span>[{row.n}] </span>}<code>{row.doc}{row.version ? ` v${row.version}` : ""}</code> {row.title}{row.why && ` · ${row.why}`}{row.excerpt && <p>{row.excerpt}</p>}</li>)}</ul></section>)}</>;
}
export function Standards({ standards }) {
  return standards?.length > 0 ? <details className="standards"><summary>Industry standards ({standards.length})</summary><div className="scroll"><table><thead><tr><th>Concept</th><th>Relation</th><th>Standard</th><th>IRI</th></tr></thead><tbody>{standards.map((s, i) => <tr key={i}><td>{s.label}<p>{s.note}</p></td><td>{s.relation}</td><td>{s.standard_name}<p>{s.version}</p></td><td title={s.iri}><code>{s.iri}</code></td></tr>)}</tbody></table></div></details> : null;
}
function ContextMap({ context: c, used, ran, pending }) {
  const named = (xs) => (xs || []).map(x => <p key={x.id}>{x.label || x.id} <code>{x.table}</code>{x.mode === "include" && " (kept only with one)"}</p>);
  const made = (j) => ran && used.joins?.includes(j.id);
  const join = (j) => <p key={j.id}><code>{j.id}</code> {j.from} to {j.to} {made(j) && <span className="badge ok">joined</span>}</p>;
  const rest = (c.joins || []).filter(j => !made(j) && !j.required);
  return <><p>Resolves to the context <code>{c.id}</code>, {c.label}: {c.description}</p><table className="kv"><tbody>
    <tr><th>Measure</th><td><code>{c.measure}</code> over <code>{c.fact}</code>{c.currency && `, in ${c.currency}`}</td></tr>
    {c.filters?.length > 0 && <tr><th>Filters</th><td>{named(c.filters)}</td></tr>}{c.row_selectors?.length > 0 && <tr><th>Row selectors</th><td>{named(c.row_selectors)}</td></tr>}
    <tr><th>Joins</th><td>{(c.joins || []).filter(j => made(j) || j.required).map(join)}{rest.length > 0 && <details className="more-joins"><summary>{rest.length} other certified joins, made when a question reaches through them</summary>{rest.map(join)}</details>}</td></tr>
    {used.time_mode && <tr><th>Time mode</th><td>{used.time_mode === "as_reported" ? "as reported: each row's version valid on its month's freeze date" : "current: each row's version that holds now"}</td></tr>}
    {used.group_by && <tr><th>Grouped by</th><td>{used.group_by}</td></tr>}{used.conditions?.length > 0 && <tr><th>Conditions</th><td>{used.conditions.join("; ")}</td></tr>}
    {ran && used.across?.length > 0 && <tr><th>Across</th><td>{used.across.map((x, i) => <p key={i}>{x.unit}, through the certified join <code>{x.join}</code></p>)}</td></tr>}
  </tbody></table>{!ran && !pending && <p>This answer did not run ({plain(used.behaviour)}): it made no join and reached no other unit.</p>}</>;
}
function MapView({ a, me, map }) {
  if (a.principal !== me.id) return <p>Asked as {a.principal}; signed in now as {me.id}. Ask again to see the map.</p>;
  if (["refuse", "blocked_access"].includes(a.behaviour)) return <><p>No map for a refused answer: {a.reason}</p><WayForward answer={a} me={me} /></>;
  if (!map) return <p>{a.context?.term || a.trace?.[0]?.term ? "Loading the resolved map…" : declineText(a.reason, a.question) || "No term resolved."}</p>;
  if (map.error) return <p>{map.error}</p>;
  const ctx = (map.contexts || []).find(c => c.id === a.context?.context) || map.contexts?.[0];
  return <><p><b>{a.context?.term || a.trace?.[0]?.term}</b> · defined by {(map.defined_by || []).map(d => `${d.id}${d.superseded_by ? " (superseded)" : ""}`).join(", ") || (ctx ? "no document; certified through its context" : a.behaviour === "glossary_definition" ? "no document; the pack's glossary defines it" : "no certified definition")}</p>
    {ctx && <ContextMap context={ctx} used={{ ...a.context, behaviour: a.behaviour }} ran={a.behaviour === "figure"} pending={a.streaming || a.stopped} />}
    {map.implementations?.length > 0 && <div className="scroll"><table><thead><tr>{["Unit", "Column", "Grain", "Currency", "Calendar", "Conversion", "Read"].map(label => <th key={label}>{label}</th>)}</tr></thead><tbody>{map.implementations.map((row, i) => <tr key={i}><td>{row.unit}</td><td><code>{row.tbl}.{row.col}</code></td><td>{row.grain}</td><td>{row.currency}</td><td>{row.calendar}</td><td>{row.conversion}</td><td>{row.readable ? "yes" : "no"}</td></tr>)}</tbody></table></div>}
    {map.no_join?.length > 0 && <p>No join exists between: {map.no_join.map(j => j.pair).join(", ")} (asserted {map.no_join[0].asserted_by}, {map.no_join[0].asserted_on})</p>}
    {(map.certified_joins || []).map((j, i) => <p key={i}>Joined only through a certified join: {j.pair}, by {j.declared_join} ({j.note}; asserted {j.asserted_by}, {j.asserted_on})</p>)}<Standards standards={map.standards} /></>;
}
function Plan({ answer: a }) {
  const rows = a.sql ? [{ unit: a.unit, status: a.sql_source && `SQL by ${a.sql_source}`, sql: a.sql }] : Array.isArray(a.plan || a.parts) ? (a.plan || a.parts) : (a.figures || []).filter(f => f.sql).map(f => ({ ...f, status: `gate ${(a.attempts || []).filter(t => t.unit === f.unit).at(-1)?.passed ? "passed" : "failed"}` }));
  return <>{a.guard && <p><span className={`badge ${a.guard.passed ? "ok" : "bad"}`}>gate {a.guard.passed ? "passed" : "failed"}</span>{(a.guard.passed ? a.guard.checks || [] : a.guard.problems || []).join(" · ")}</p>}{rows?.length ? rows.map((p, i) => <section key={i}><p>{p.unit} · {p.status}{p.notes?.length ? ` · ${p.notes.join("; ")}` : ""}</p>{p.sql ? <div className="codeblock"><CopyButton label="Copy the query" value={p.sql} /><pre>{String(p.sql).replace(/\s+(FROM|WHERE|GROUP BY|ORDER BY|HAVING|LIMIT|UNION ALL|(?:LEFT |RIGHT |INNER |FULL |CROSS )?JOIN)\s/g, "\n$1 ")}</pre></div> : <p>{p.reason}</p>}</section>) : <p>No warehouse query was run.</p>}</>;
}
function Trace({ answer: a }) {
  const stages = a.run_trace?.stages || [], modelled = stages.some(s => s.llm_calls || s.tokens_in || s.tokens_out);
  return <>{stages.length ? <><div className="scroll"><table><thead><tr><th>Step</th><th>What ran</th><th>Input</th>{modelled && <th>Output</th>}<th>Time</th></tr></thead><tbody>{stages.map((s, i) => <tr key={i}><td>{i + 1}</td><td>{s.stage}</td><td>{i ? `the ${stages[i - 1].stage} stage's result` : `the question: ${a.question}`}</td>{modelled && <td>{s.llm_calls || 0} model calls; {s.tokens_in || 0} tokens in, {s.tokens_out || 0} out</td>}<td>{s.state === "running" ? <span className="stage-running">running…</span> : s.ms == null ? "" : `${s.ms} ms`}</td></tr>)}</tbody></table></div>{!modelled && !a.streaming && <p>No model was called for this answer.</p>}</> : <p>{a.streaming ? "The stages appear here as the answer is worked out." : "No timed stages were recorded for this answer."}</p>}
    <details><summary>Raw JSON</summary><pre>{JSON.stringify({ run_trace: a.run_trace, trace: a.trace, versions: a.versions, retrieval: a.retrieval }, null, 2)}</pre></details></>;
}
export default function BehindAnswer({ api, answer: a, purpose, me, agentArms, source, requestedTab, onCurrency }) {
  const [tab, setTab] = useState("map"), [resolved, setResolved] = useState(null), cache = useRef(new WeakMap());
  const lastAnswer = useRef(null);
  useEffect(() => {
    const before = lastAnswer.current;
    lastAnswer.current = a;
    const sameQuestionStreamed = Boolean(before?.streaming && a && before.question === a.question);
    if (a?.streaming) {
      if (!sameQuestionStreamed) setTab("graph");
    } else if (!sameQuestionStreamed) {
      const defaultTab = a?.citations?.length || ["combined", "cite_certified", "cite", "glossary_definition"].includes(a?.behaviour) ? "sources" : "map";
      setTab(t => (t === "graph" && requestedTab?.tab === "graph") ? "graph" : defaultTab);
    }
  }, [a, requestedTab]);
  const term = a?.context?.term || a?.trace?.[0]?.term;
  const mapKey = a?._mapKey || a;
  const canMap = a && !a.error && a.principal === me.id && !["refuse", "blocked_access"].includes(a.behaviour) && term && term !== "__population__";
  useEffect(() => {
    let active = true;
    if (!canMap) return () => { active = false; };
    const key = `${me.id}\0${term}`;
    let entries = cache.current.get(mapKey);
    if (!entries) { entries = new Map(); cache.current.set(mapKey, entries); }
    if (!entries.has(key)) entries.set(key, api(`/map/${encodeURIComponent(term)}`));
    entries.get(key).then(map => { if (active) setResolved({ mapKey, key, map }); });
    return () => { active = false; };
  }, [api, canMap, mapKey, me.id, term]);
  const map = canMap && resolved?.mapKey === mapKey && resolved.key === `${me.id}\0${term}` ? resolved.map : null;
  useEffect(() => {
    if (!map || !a || a.streaming || a.stopped) return;
    const ctx = (map.contexts || []).find(c => c.id === a.context?.context) || map.contexts?.[0];
    const currency = ctx?.currency || (map.implementations || []).find(i => i.unit === a.unit && `${i.tbl}.${i.col}` === a.column)?.currency;
    onCurrency(a, { currency, definition: ctx?.description || "", source: ctx?.fact || "" });
  }, [a, map, onCurrency]);
  useEffect(() => { if (source != null) setTab("sources"); }, [source, a]);
  const lastRequestedSeq = useRef(0);
  useLayoutEffect(() => {
    if (requestedTab?.seq && requestedTab.seq !== lastRequestedSeq.current) {
      lastRequestedSeq.current = requestedTab.seq;
      if (requestedTab.tab) setTab(requestedTab.tab);
    }
  }, [requestedTab]);
  const valid = a && !a.error;
  return <section id="detail" className="fold" aria-labelledby="detail-title">
    <h2 id="detail-title">{valid ? `Behind “${a.question}”` : "Behind the answer"}</h2>{valid && <p id="about" className="muted">{[armOf(a, agentArms) && `${armOf(a, agentArms)} arm${a.agent ? ` (${a.agent.mode})` : ""}`, `as ${a.principal}${purpose ? ` for ${purpose}` : ""}`, `reads ${me.reads?.join(", ") || "no unit"}`].filter(Boolean).join(" · ")}</p>}
    {valid && <dl className="answer-facts"><div><dt>Term</dt><dd>{a.context?.term || a.trace?.[0]?.term || a.term || "No term resolved"}</dd></div><div><dt>Period</dt><dd>{periodWords(a) || "Not specified"}</dd></div></dl>}
    {(a?.progress || a?.stopped) && <p className="small muted warehouse-progress"><span>{a.progress && progressText(a.progress)}{a.progress && a.stopped ? " · " : ""}{a.stopped && "Stopped"}</span><span className="vh" role="status">{a.stopped ? "Question stopped." : `Warehouse statement ${a.progress.statement} ${a.progress.state}.`}</span></p>}
    <TabButtons className="tabs" label="Behind the answer" names={["sources", "map", "plan", "graph", "trace"]} value={tab} onChange={setTab} prefix="tabbtn-" />
    {["sources", "map", "plan", "graph", "trace"].map(name => <div key={name} id={`tab-${name}`} role="tabpanel" aria-labelledby={`tabbtn-${name}`} tabIndex={0} hidden={tab !== name}>
      {!valid ? <p>{a?.error ? `No answer to the last question: ${a.error}` : "Ask a question and what lies behind its answer shows here."}</p> : name === "sources" ? <Sources answer={a} source={source} /> : name === "map" ? <MapView a={a} me={me} map={map} /> : name === "plan" ? <Plan answer={a} /> : name === "graph" ? <GraphPath answer={a} /> : <Trace answer={a} />}
    </div>)}
  </section>;
}
