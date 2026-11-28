import React, { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { ReactFlow, Background, Panel as FlowPanel, useReactFlow, useStore } from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { Button } from "../../../components/shared/Controls";
import { isTyping } from "../cerebroState";
import { collapseColumns, drawnNode, mergePages, readGraphPages } from "./graphData";
import GraphNode, { LOW_DETAIL_ZOOM } from "./GraphNode";
import { ASSET_TYPES, ASSET_TYPE_LABELS, INITIAL_FILTERS, INITIAL_LAYERS, KIND_DETAILS, KIND_LABELS, KIND_ORDER, LAYER_LABELS, NODE_EXTENT, STATUSES, STATUS_LABELS, boundsOf, documentList, fitViewport, layerOn, neighbours, overviewCounts, overviewTiles, pathHighlight, reasons, toggleLayer, visibleLinkIds, visibleNodeIds } from "./graphUx";
import "./graph.css";

/* The layout runs in a worker, never on the main thread (cerebro.md §7.5). Vite bundles the
   worker only from this literal form. */
export const layoutWorker = () => new Worker(new URL("./layout.worker.js", import.meta.url), { type: "module" });

/* At module scope, so xyflow keeps every node mounted across the page's renders. */
export const NODE_TYPES = Object.freeze({ cerebro: GraphNode });

/* Reads the graph's pages, folds the columns into their tables, and has the worker lay it out
   once; the result is kept in the page's state, so a return to Graph mode uses it as it is. `fetcher` and `makeWorker` are the browser's unless a test gives its own. */
function useGraph({ fetcher, makeWorker, kept, keep }) {
  const [graph, setGraph] = useState(kept || { status: "loading" });
  useEffect(() => {
    if (kept) return undefined;
    const abort = new AbortController();
    let worker;
    readGraphPages({ fetcher, signal: abort.signal }).then(({ pages, complete }) => {
      if (abort.signal.aborted) return;
      const view = collapseColumns(mergePages(pages));
      if (!view.nodes.length) return setGraph({ status: "empty" });
      try {
        worker = makeWorker();
      } catch {
        return setGraph({ status: "error", message: "This browser cannot lay out the graph." });
      }
      worker.onmessage = (event) => {
        if (abort.signal.aborted) return;
        if (event.data?.error || !event.data?.positions) setGraph({ status: "error", message: "The graph could not be laid out." });
        else {
          const ready = { status: "ready", view, positions: event.data.positions, complete };
          setGraph(ready);
          keep?.(ready);
        }
        worker.terminate();
      };
      worker.onerror = () => { if (!abort.signal.aborted) setGraph({ status: "error", message: "The graph could not be laid out." }); };
      worker.postMessage({ id: 1, graph: { nodes: view.nodes.map(({ id }) => ({ id })), links: view.links.map(({ source, target }) => ({ source, target })) } });
    }).catch((error) => {
      if (!abort.signal.aborted) setGraph({ status: "error", message: error?.name === "GraphReadError" ? error.message : "The graph could not be read." });
    });
    return () => { abort.abort(); worker?.terminate(); };
    // Read once per visit to the page: a kept graph is not read again on a return to Graph mode.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [fetcher, makeWorker]);
  return graph;
}

const nameOf = (node, id) => node?.name || id;

/* How much room the view leaves around what it fits, as a share of the canvas, and the gap
   between the controls over the canvas and its edge. */
export const FIT_PADDING = 0.06;
const OVERLAY_MARGIN = 12;
/* The largest a node's mark and name are scaled up as the view zooms out (see GraphNode). */
const MAX_LABEL_SCALE = 1 / LOW_DETAIL_ZOOM;
/* The keys for Fit and Center, anywhere on the page in Graph mode but in a field. */
export const FIT_KEY = "f";
export const CENTER_KEY = "c";

/* How far a control over the canvas reaches into it from its top (the filter row) or its foot (the
   open assistant), in pixels; 0 for one beside the canvas, hidden or not drawn. */
function overlap(canvas, element, edge) {
  if (!canvas || !element) return 0;
  const outer = canvas.getBoundingClientRect();
  const inner = element.getBoundingClientRect();
  if (!inner.height || inner.right <= outer.left || inner.left >= outer.right) return 0;
  const reach = edge === "top" ? inner.bottom - outer.top : outer.bottom - inner.top;
  return reach > 0 && reach < outer.height ? reach + OVERLAY_MARGIN : 0;
}
/* The open assistant's card, which CerebroApp floats over the canvas beside this view. */
const dockOf = (canvas) => canvas?.closest(".cerebro-view")?.querySelector(".cerebro-dock-card:not([hidden])") || null;

/* The legend, which is also the layer toggles (§1.5): a node kind's layer, and a link status's,
   each turned on and off by its own entry. Each kind shows its mark and each status the line it is
   drawn with. It folds away to its heading; the body stays mounted, hidden, so the toggle's
   aria-controls always names it. */
function Legend({ kinds, layers, setLayers }) {
  /* Open beside a wide canvas, as the prototype shows it; folded on a phone, where it would cover the graph. */
  const [open, setOpen] = useState(() => !globalThis.matchMedia?.("(max-width: 900px)").matches);
  return <section aria-label="Legend and layers" className="cerebro-legend">
    <Button className="cerebro-legend-toggle" aria-expanded={open} aria-controls="cerebro-legend-body" onClick={() => setOpen(!open)}>Legend<span className="cerebro-chevron" aria-hidden="true" /></Button>
    <div id="cerebro-legend-body" className="cerebro-legend-body" hidden={!open}>
      <ul aria-label="Node kinds">{kinds.map((kind) => <li key={kind}><Button aria-pressed={layerOn(layers, "kinds", kind)} className={`cerebro-layer cerebro-layer-${kind}`} onClick={() => setLayers(toggleLayer(layers, "kinds", kind))}><span className="cerebro-swatch" aria-hidden="true" />{LAYER_LABELS[kind] || KIND_LABELS[kind] || kind}</Button></li>)}</ul>
      <ul aria-label="Link kinds">{STATUSES.map((status) => <li key={status}><Button aria-pressed={layerOn(layers, "statuses", status)} className={`cerebro-layer cerebro-layer-${status}`} onClick={() => setLayers(toggleLayer(layers, "statuses", status))}><svg className={`cerebro-line cerebro-line-${status}`} width="24" height="8" aria-hidden="true"><line x1="0" y1="4" x2="24" y2="4" /></svg>{STATUS_LABELS[status]}</Button></li>)}</ul>
      <p className="cerebro-legend-note">Dashed and dotted links are proposed or inferred: not reviewed, and not verified joins.</p>
    </div>
  </section>;
}

/* The row over the top of the graph: the community, All / Structured / Unstructured with their
   counts, the term-links switch, and a name search. */
const FilterRow = React.forwardRef(function FilterRow({ view, filters, setFilters, termLinks, setTermLinks, counts }, ref) {
  const communities = view.nodes.filter((node) => node.kind === "community");
  const set = (key) => (event) => setFilters({ ...filters, [key]: event.target.value });
  const countOf = { all: null, structured: counts.tables, unstructured: counts.documents };
  return <section ref={ref} aria-label="Filters" className="cerebro-filter-row">
    <label className="cerebro-field"><span className="cerebro-field-name">Community</span><select value={filters.community} onChange={set("community")}><option value="">All communities</option>{communities.map((node) => <option key={node.id} value={node.id}>{node.name}</option>)}</select></label>
    <div role="group" aria-label="Asset type" className="cerebro-segments">
      {ASSET_TYPES.map((type) => <Button key={type} aria-pressed={filters.assets === type} onClick={() => setFilters({ ...filters, assets: type })}>
        {ASSET_TYPE_LABELS[type]}{countOf[type] !== null && <span className="cerebro-count"> {countOf[type]}</span>}
      </Button>)}
    </div>
    <Button role="switch" aria-checked={termLinks} aria-label="Term links" className="cerebro-switch" onClick={() => setTermLinks(!termLinks)}>Term links {termLinks ? "on" : "off"}</Button>
    <label className="cerebro-field cerebro-field-search"><span className="cerebro-field-name">Name</span><input type="search" value={filters.text} onChange={set("text")} placeholder="Find a name" /></label>
  </section>;
});

/* Fits the view to the shown nodes after every change of what is shown, whenever the canvas changes
   size, and when the assistant over its foot is shown or hidden (not as it grows with an answer, so
   a person's zoom is kept as the conversation goes on); and on demand through `fitRef` (Fit). The
   fit leaves room for the controls over the canvas's left edge (`overlayRef`), the filter row over
   its top (`filterRef`) and the open assistant over its foot, at its height when the fit is made. A
   new answer's path is fitted to instead, until the next filter change or Fit. A view restored from
   the page's state is kept until the first such change, unless the answer's path is not the one last
   fitted to (`pathKey`, `fittedPath`): an answer given in another mode is fitted to on the return. */
function AutoFit({ shown, path, pathKey, fittedPath, onPathFitted, positions, restored, fitRef, overlayRef, filterRef }) {
  const flow = useReactFlow();
  const width = useStore((store) => store.width);
  const height = useStore((store) => store.height);
  const minZoom = useStore((store) => store.minZoom);
  const dom = useStore((store) => store.domNode);
  const keep = useRef(restored);
  const first = useRef(true);
  const onPath = useRef(false);
  const seen = useRef({ shown: null, path: null });
  const pathFresh = useRef(false);
  pathFresh.current = pathKey !== fittedPath;
  fitRef.current = (duration = 250, { all = false } = {}) => {
    if (all) onPath.current = false;
    const pathShown = onPath.current ? [...path].filter((id) => shown.has(id)) : [];
    const box = boundsOf(pathShown.length ? pathShown : shown, positions);
    if (!box || !width || !height) return;
    const panel = overlayRef.current;
    const left = panel ? panel.offsetWidth + OVERLAY_MARGIN : 0;
    /* On a narrow canvas (a phone) the controls cannot be left room beside, so they are left room above. */
    const under = panel && width - left < 360 && panel.offsetTop + panel.offsetHeight < height / 2 ? panel.offsetTop + panel.offsetHeight + OVERLAY_MARGIN : 0;
    const top = Math.max(overlap(dom, filterRef.current, "top"), under);
    const bottom = overlap(dom, dockOf(dom), "bottom");
    if (pathShown.length && pathKey !== fittedPath) onPathFitted?.(pathKey);
    flow.setViewport(fitViewport(box, { width, height, left, top, bottom, padding: FIT_PADDING, minZoom, extent: NODE_EXTENT, ...(pathShown.length ? { maxZoom: 1 } : {}) }), { duration });
  };
  useEffect(() => {
    if (!width || !height) return;
    const was = seen.current;
    seen.current = { shown, path };
    if (path !== was.path) onPath.current = path.size > 0;
    else if (shown !== was.shown) onPath.current = false;
    if (keep.current) {
      keep.current = false;
      first.current = false;
      if (!onPath.current || !pathFresh.current) return;
    }
    fitRef.current(first.current ? 0 : 250);
    first.current = false;
  }, [shown, path, width, height, fitRef]);
  /* The assistant floats over the canvas without changing its size, so the dock is watched for the
     card being shown or hidden; the card growing with an answer is not a change. Its first state is
     the baseline. */
  useEffect(() => {
    const view = dom?.closest(".cerebro-view");
    const dock = view?.querySelector(".cerebro-dock");
    if (!dock || typeof ResizeObserver !== "function") return undefined;
    let last = null;
    let timer = null;
    const observer = new ResizeObserver(() => {
      const now = Boolean(dockOf(dom));
      if (last !== null && now !== last) {
        clearTimeout(timer);
        timer = setTimeout(() => fitRef.current?.(), 150);
      }
      last = now;
    });
    observer.observe(dock);
    return () => { observer.disconnect(); clearTimeout(timer); };
  }, [dom, fitRef]);
  return null;
}

/* Which names are drawn at a zoom: zoomed out, only the communities', domains', repositories' and
   documents' (and those of the selected node, the path's and the node under the pointer); then the
   tables' and the linked terms' too; zoomed in, every name. graph.css reads it from the canvas. */
export const labelBand = (zoom) => (zoom < 0.25 ? "few" : zoom < 0.6 ? "some" : "all");

/* Keeps a node's mark and name the same size on screen as the view zooms out, and sets which names
   are drawn, through the canvas's style and one attribute, so no node is drawn again on a zoom. */
function LabelScale() {
  const zoom = useStore((store) => store.transform[2]);
  const dom = useStore((store) => store.domNode);
  useLayoutEffect(() => {
    if (!dom) return;
    dom.style.setProperty("--cerebro-label-scale", String(Math.min(MAX_LABEL_SCALE, Math.max(1, 1 / zoom))));
    dom.dataset.labels = labelBand(zoom);
  }, [dom, zoom]);
  return null;
}

/* Fit, Center and the zoom with its percentage, over the canvas's left edge. F fits and C centres,
   anywhere on the page but in a field. The percentage is plain text in the Zoom group, not a live
   region, so a screen reader does not announce every step of a zoom. */
function ViewTools({ fitRef, selected, positions, shown }) {
  const flow = useReactFlow();
  const zoom = useStore((store) => store.transform[2]);
  const center = () => {
    const at = selected ? positions[selected] : null;
    if (at) return flow.setCenter(at.x + 6, at.y + 6, { zoom: Math.max(zoom, 1), duration: 250 });
    const box = boundsOf(shown, positions);
    if (box) flow.setCenter(box.x + box.width / 2, box.y + box.height / 2, { zoom, duration: 250 });
    return undefined;
  };
  const fit = () => fitRef.current?.(250, { all: true });
  const keys = useRef({});
  keys.current = { [FIT_KEY]: fit, [CENTER_KEY]: center };
  useEffect(() => {
    const onKey = (event) => {
      const run = keys.current[event.key?.toLowerCase()];
      if (!run || event.ctrlKey || event.metaKey || event.altKey || event.defaultPrevented || isTyping(event.target)) return;
      event.preventDefault();
      run();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);
  return <div className="cerebro-tools" role="group" aria-label="View">
    <Button className="cerebro-tool" aria-keyshortcuts="F" onClick={fit}><span className="cerebro-icon cerebro-icon-fit" aria-hidden="true" />Fit<kbd className="cerebro-key" aria-hidden="true">F</kbd></Button>
    <Button className="cerebro-tool" aria-keyshortcuts="C" onClick={center}><span className="cerebro-icon cerebro-icon-center" aria-hidden="true" />{selected ? "Center selection" : "Center"}<kbd className="cerebro-key" aria-hidden="true">C</kbd></Button>
    <div className="cerebro-zoom" role="group" aria-label="Zoom">
      <span className="cerebro-zoom-level">{Math.round(zoom * 100)}%</span>
      <Button aria-label="Zoom out" onClick={() => flow.zoomOut({ duration: 150 })}>−</Button>
      <Button aria-label="Zoom in" onClick={() => flow.zoomIn({ duration: 150 })}>+</Button>
    </div>
  </div>;
}

const StatusChip = ({ status }) => <span className={`cerebro-chip cerebro-chip-${status}`}>{STATUS_LABELS[status] || status}</span>;

/* What the overview shows with nothing selected: what the graph is, its figures and its documents. */
function Overview({ view, select }) {
  const tiles = overviewTiles(overviewCounts(view));
  const docs = documentList(view);
  return <section aria-label="Overview" className="cerebro-overview">
    <p className="cerebro-kind">Graph overview</p>
    <h2 className="cerebro-overview-title">See how your data assets connect</h2>
    <p className="cerebro-muted">Select a node or a link to see what it is and how it connects. You see only what you may read.</p>
    <dl className="cerebro-tiles">{tiles.map(([label, value]) => <div key={label} className="cerebro-tile"><dt>{label}</dt><dd>{value}</dd></div>)}</dl>
    <h3 className="cerebro-kind cerebro-docs-head">Documents ({docs.length})</h3>
    {docs.length ? <ul className="cerebro-docs" aria-label="Documents">{docs.map((doc) => <li key={doc.id}>
      <Button className="cerebro-doc" onClick={() => select(doc.id)}>
        <span className="cerebro-swatch cerebro-doc-mark" aria-hidden="true" />
        <code className="cerebro-doc-id">{doc.short}</code>
        <span className="cerebro-doc-title">{doc.title}</span>
        {doc.domain && <span className="cerebro-doc-domain">{doc.domain}</span>}
      </Button>
    </li>)}</ul> : <p className="cerebro-muted">There is no document in the graph you may read.</p>}
  </section>;
}

/* "Inspect this relationship": why the link exists, once for each link folded into it. */
function Relationship({ view, link, onBack }) {
  const byId = new Map(view.nodes.map((node) => [node.id, node]));
  const why = reasons(link);
  return <section aria-label="Inspect this relationship">
    <p className="cerebro-kind">Relationship</p>
    <h2 className="cerebro-node-name">{nameOf(byId.get(link.source), link.source)} <span className="cerebro-muted">{link.kind.replace(/_/g, " ")}</span> {nameOf(byId.get(link.target), link.target)}</h2>
    <StatusChip status={link.status} />
    <h3 className="cerebro-kind">Why this link exists</h3>
    <ul className="cerebro-why">{why.map((text) => <li key={text}>{text}</li>)}</ul>
    <Button className="cerebro-back" onClick={onBack}>Back</Button>
  </section>;
}

/* The details pane for one node, per kind: what it is, its columns for a table, and its links in
   the kind's sections, each with its status and the reason it exists. */
function NodeDetails({ view, node, actions, inspect }) {
  const details = KIND_DETAILS[node.kind];
  return <section aria-label={`${KIND_LABELS[node.kind] || node.kind} details`}>
    <Button className="cerebro-back" onClick={() => { inspect(null); actions.selectNode(null); }}>Back to overview</Button>
    <p className="cerebro-kind">{KIND_LABELS[node.kind] || node.kind}</p>
    <h2 className="cerebro-node-name">{node.name}</h2>
    <p><code className="cerebro-id">{node.id}</code></p>
    {details?.about && <p className="cerebro-muted">{details.about}</p>}
    <div className="cerebro-actions">
      <Button className="cerebro-primary" onClick={() => actions.askAbout?.(node.id)}>Ask about this</Button>
    </div>
    {node.kind === "table" && node.columns.length > 0 && <><h3 className="cerebro-kind">Columns</h3><ul className="cerebro-columns" aria-label="Columns">{node.columns.map((name) => <li key={name}><code>{name}</code></li>)}</ul></>}
    {neighbours(view, node.id).map((section) => <section key={section.title} aria-label={section.title}>
      <h3 className="cerebro-kind">{section.title}</h3>
      <ul className="cerebro-links">{section.links.map(({ link, other }) => <li key={link.id}>
        <span className="cerebro-link-end">{nameOf(other, link.outgoing ? link.target : link.source)}</span>
        <span className="cerebro-muted"> {link.kind.replace(/_/g, " ")} </span><StatusChip status={link.status} />
        <p className="cerebro-why-line">{reasons(link).join(" ")}</p>
        <Button className="cerebro-inspect" aria-label={`Why this link: ${nameOf(other, link.outgoing ? link.target : link.source)}, ${STATUS_LABELS[link.status] || link.status}`} onClick={() => inspect(link.id)}>Why this link</Button>
      </li>)}</ul>
    </section>)}
  </section>;
}

export default function GraphView({ state, actions, fetcher = globalThis.fetch, makeWorker = layoutWorker }) {
  const graph = useGraph({ fetcher, makeWorker, kept: state.graph, keep: actions.setGraph });
  const [layers, setLayers] = useState(INITIAL_LAYERS);
  const [filters, setFilters] = useState(INITIAL_FILTERS);
  const [termLinks, setTermLinks] = useState(false);
  const fitRef = useRef(null);
  const overlayRef = useRef(null);
  const filterRef = useRef(null);
  const ready = graph.status === "ready";
  const selected = ready ? drawnNode(graph.view, state.node) : null;
  /* The link being inspected, kept with the selection it was opened under, so a new selection
     (a click, or an address) shows that node's details again. */
  const [inspected, setInspected] = useState(null);
  const linkId = inspected && inspected.under === selected ? inspected.id : null;
  const setLinkId = (id) => setInspected(id ? { id, under: selected } : null);
  /* The drawn nodes and links are built once per laid-out graph. A change of selection then
     makes new objects for the old and the new selected node only, so every other memoised node
     keeps its object and is not drawn again (cerebro.md §7.5). A layer, a filter or a new path
     changes only the nodes and links it hides, shows or highlights. */
  const base = useMemo(() => {
    if (graph.status !== "ready") return { nodes: [], edges: [] };
    return {
      nodes: graph.view.nodes.map((node) => ({
        id: node.id,
        type: "cerebro",
        position: graph.positions[node.id] || { x: 0, y: 0 },
        data: { label: node.name, kind: node.kind, columns: node.columns },
        className: `cerebro-node cerebro-node-${node.kind}`,
        selected: false,
        connectable: false,
      })),
      edges: graph.view.links.map((link) => ({
        id: link.id, source: link.source, target: link.target,
        data: { kind: link.kind, status: link.status, members: link.members },
        className: `cerebro-link cerebro-link-${link.status}`,
        ariaLabel: `${link.kind.replace(/_/g, " ")}, ${link.status}`,
      })),
    };
  }, [graph]);
  const shownNodes = useMemo(() => (ready ? visibleNodeIds(graph.view, layers, filters, { termLinks }) : new Set()), [graph, ready, layers, filters, termLinks]);
  const path = useMemo(() => (ready ? pathHighlight(graph.view, state.lastPath) : { nodes: new Set(), links: new Set() }), [graph, ready, state.lastPath]);
  /* Which answer's path this is, kept in the page's state once fitted to, so a return to Graph mode
     knows whether the path is new. */
  const pathKey = useMemo(() => JSON.stringify(state.lastPath ?? null), [state.lastPath]);
  const shownLinks = useMemo(() => (ready ? visibleLinkIds(graph.view, layers, shownNodes, { termLinks, keep: path.links }) : new Set()), [graph, ready, layers, shownNodes, termLinks, path]);
  /* A node none of whose links is shown is "loose": zoomed out, a loose term's name is not drawn (graph.css). */
  const shaped = useMemo(() => {
    const held = new Set();
    for (const edge of base.edges) if (shownLinks.has(edge.id)) { held.add(edge.source); held.add(edge.target); }
    return base.nodes.map((node) => {
      const hidden = !shownNodes.has(node.id);
      const onPath = path.nodes.has(node.id);
      const loose = !hidden && !held.has(node.id);
      if (!hidden && !onPath && !loose) return node;
      return { ...node, hidden, className: `${node.className}${onPath ? " cerebro-on-path" : ""}${loose ? " cerebro-loose" : ""}` };
    });
  }, [base, shownNodes, shownLinks, path]);
  const edges = useMemo(() => base.edges.map((edge) => {
    const hidden = !shownLinks.has(edge.id);
    const onPath = path.links.has(edge.id);
    return hidden || onPath || edge.id === linkId ? { ...edge, hidden, selected: edge.id === linkId, className: onPath ? `${edge.className} cerebro-on-path` : edge.className } : edge;
  }), [base, shownLinks, path, linkId]);
  const nodes = useMemo(() => shaped.map((node) => (node.id === selected ? { ...node, selected: true } : node)), [shaped, selected]);
  const counts = useMemo(() => (ready ? overviewCounts(graph.view) : null), [graph, ready]);
  const chosen = selected ? graph.view.nodes.find((node) => node.id === selected) : null;
  /* A link is inspected from the canvas or from a node's details, where it is listed even when the
     canvas hides it (term links off, or its status's layer off). */
  const link = ready && linkId ? graph.view.links.find((item) => item.id === linkId) : null;
  const kinds = ready ? KIND_ORDER.filter((kind) => graph.view.nodes.some((node) => node.kind === kind)) : [];
  const viewport = state.graphPosition;
  const select = (id) => { setLinkId(null); actions.selectNode(id); };
  /* The controls down the canvas's left edge start under the filter row, however many lines it takes. */
  useLayoutEffect(() => {
    const row = filterRef.current;
    const canvas = row?.closest(".cerebro-canvas");
    if (!row || !canvas || typeof ResizeObserver !== "function") return undefined;
    const place = () => {
      if (getComputedStyle(row).position === "absolute") canvas.style.setProperty("--cerebro-filter-room", `${row.offsetTop + row.offsetHeight + 8}px`);
      else canvas.style.removeProperty("--cerebro-filter-room");
    };
    const observer = new ResizeObserver(place);
    observer.observe(row);
    return () => observer.disconnect();
  }, [ready]);

  return <div className="cerebro-graph">
    <section className="cerebro-canvas" aria-label="Graph" aria-busy={graph.status === "loading"}>
      {ready && <FilterRow ref={filterRef} view={graph.view} filters={filters} setFilters={setFilters} termLinks={termLinks} setTermLinks={setTermLinks} counts={counts} />}
      {ready && !graph.complete && <p className="cerebro-graph-note" role="status">Only part of the graph is shown: it is larger than one view reads.</p>}
      {graph.status === "loading" && <p className="cerebro-muted cerebro-canvas-note" role="status">Reading the graph you may read…</p>}
      {graph.status === "empty" && <p className="cerebro-muted cerebro-canvas-note">There is nothing in the graph you may read yet.</p>}
      {graph.status === "error" && <p className="cerebro-canvas-note" role="alert">{graph.message}</p>}
      {ready && <div className="cerebro-flow">
        <ReactFlow
          nodes={nodes} edges={edges} nodeTypes={NODE_TYPES} onlyRenderVisibleElements
          nodesConnectable={false} nodesDraggable={false} elementsSelectable
          onNodeClick={(_event, node) => select(node.id)}
          onEdgeClick={(_event, edge) => setLinkId(edge.id)}
          onMoveEnd={(_event, position) => actions.setGraphPosition(position)}
          {...(viewport ? { defaultViewport: viewport } : {})}
          minZoom={0.05} maxZoom={4} proOptions={{ hideAttribution: true }}>
          <Background />
          <AutoFit shown={shownNodes} path={path.nodes} pathKey={pathKey} fittedPath={state.fittedPath} onPathFitted={actions.setFittedPath} positions={graph.positions} restored={!!viewport} fitRef={fitRef} overlayRef={overlayRef} filterRef={filterRef} />
          <LabelScale />
          <FlowPanel ref={overlayRef} position="top-left" className="cerebro-overlay">
            <ViewTools fitRef={fitRef} selected={selected} positions={graph.positions} shown={shownNodes} />
            {path.nodes.size > 0 && <p className="cerebro-path-note" role="status">The last answer's path is highlighted: {path.nodes.size} {path.nodes.size === 1 ? "node" : "nodes"} you may read.</p>}
            <Legend kinds={kinds} layers={layers} setLayers={setLayers} />
          </FlowPanel>
        </ReactFlow>
      </div>}
    </section>
    <aside className="cerebro-details" aria-label="Details">
      {link ? <Relationship view={graph.view} link={link} onBack={() => setLinkId(null)} />
        : chosen ? <NodeDetails view={graph.view} node={chosen} actions={actions} inspect={setLinkId} />
        : !ready ? <p className="cerebro-muted">The overview appears once the graph is read.</p>
        : <>
          {state.node && <p className="cerebro-muted cerebro-missing">That node is not in the graph you may read.</p>}
          <Overview view={graph.view} select={select} />
        </>}
    </aside>
  </div>;
}
