/* What Graph mode shows of the drawn graph (cerebro.md §7.1, §7.4, §7.5): the layers the legend
   turns on and off, the filters, the last answer's path, and a node's links with the reason each
   exists. Pure functions over the view `collapseColumns` returns, so the drawing stays memoised. */

export const STATUSES = Object.freeze(["certified", "proposed", "inferred"]);
export const STATUS_LABELS = Object.freeze({ certified: "Certified", proposed: "Proposed, not reviewed", inferred: "Inferred, not verified" });

/* The node kinds as the legend names its layers, in the order it lists them. */
export const KIND_ORDER = Object.freeze(["community", "domain", "repository", "table", "document", "term", "person"]);
export const KIND_LABELS = Object.freeze({ term: "Term", table: "Table", column: "Column", document: "Document", repository: "Repository", community: "Community", person: "Person", domain: "Domain" });
export const LAYER_LABELS = Object.freeze({ community: "Communities", domain: "Domains", repository: "Repositories", table: "Structured assets", document: "Documents", term: "Business terms", person: "People" });

/* The details pane per kind: the sections a node's links are grouped into, by the kind at the
   link's other end; links to any other kind are listed under "Other links". */
export const KIND_DETAILS = Object.freeze({
  table: { about: "A structured asset: a table, with its columns folded into it.", sections: [["Business terms", ["term"]], ["Domains and communities", ["domain", "community"]], ["Tables it joins", ["table"]], ["People and accountability", ["person"]]] },
  document: { about: "A document, an unstructured asset.", sections: [["Business terms", ["term"]], ["Domains and communities", ["domain", "community"]], ["Repository", ["repository"]]] },
  term: { about: "A business term.", sections: [["Mapped tables and columns", ["table"]], ["Documents that mention it", ["document"]], ["Related terms", ["term"]]] },
  domain: { about: "A domain, as the catalogue places assets in it.", sections: [["Assets in this domain", ["table", "document"]], ["Community", ["community"]]] },
  community: { about: "A community, as the catalogue groups domains and assets.", sections: [["Domains", ["domain"]], ["Assets", ["table", "document"]], ["Repositories", ["repository"]]] },
  repository: { about: "A document repository.", sections: [["Documents it contains", ["document"]], ["Community", ["community"]]] },
  person: { about: "A person the catalogue names on an asset.", sections: [["Assets", ["table", "document"]]] },
});

export const INITIAL_LAYERS = Object.freeze({ kinds: Object.freeze({}), statuses: Object.freeze({}) });
export const INITIAL_FILTERS = Object.freeze({ community: "", text: "", assets: "all" });

/* The All / Structured / Unstructured switch: structured is the tables, unstructured the documents
   and the repositories that hold them. Each narrows the graph to its own assets and the nodes they
   link to (their domains, communities and people, and their terms while term links are shown),
   never the other side's assets. */
export const ASSET_TYPES = Object.freeze(["all", "structured", "unstructured"]);
export const ASSET_TYPE_LABELS = Object.freeze({ all: "All", structured: "Structured", unstructured: "Unstructured" });
const ASSET_KINDS = Object.freeze({ structured: Object.freeze(["table"]), unstructured: Object.freeze(["document", "repository"]) });

/* A layer is shown unless it is turned off. */
export const layerOn = (layers, group, key) => layers?.[group]?.[key] !== false;
export const toggleLayer = (layers, group, key) => ({ ...layers, [group]: { ...layers[group], [key]: !layerOn(layers, group, key) } });

const nodeIndex = (view) => new Map(view.nodes.map((node) => [node.id, node]));

/* A node's community, derived from the links since a node carries none: the community itself, a
   node linked to it, and a node linked to a domain that is linked to it. */
export function communityMembers(view, communityId) {
  if (!communityId) return null;
  const byId = nodeIndex(view);
  const members = new Set([communityId]);
  const domains = new Set();
  for (const link of view.links) {
    const other = link.source === communityId ? link.target : link.target === communityId ? link.source : null;
    if (!other) continue;
    members.add(other);
    if (byId.get(other)?.kind === "domain") domains.add(other);
  }
  for (const link of view.links) {
    if (domains.has(link.source)) members.add(link.target);
    if (domains.has(link.target)) members.add(link.source);
  }
  return members;
}

/* The nodes Structured or Unstructured keeps: that side's assets and the nodes they link to, but
   for the other side's assets, and for the terms while term links are hidden; null for All. */
export function assetScope(view, assets, { termLinks = true } = {}) {
  const own = ASSET_KINDS[assets];
  if (!own) return null;
  const other = new Set(Object.values(ASSET_KINDS).flat().filter((kind) => !own.includes(kind)));
  const byId = nodeIndex(view);
  const scope = new Set(view.nodes.filter((node) => own.includes(node.kind)).map((node) => node.id));
  const assetIds = new Set(scope);
  for (const link of view.links) {
    for (const [from, to] of [[link.source, link.target], [link.target, link.source]]) {
      const node = byId.get(to);
      if (assetIds.has(from) && node && !other.has(node.kind) && (termLinks || node.kind !== "term")) scope.add(to);
    }
  }
  return scope;
}

/* The ids of the nodes shown: their kind's layer is on and they pass every filter. */
export function visibleNodeIds(view, layers, filters, { termLinks = true } = {}) {
  const members = communityMembers(view, filters?.community);
  const scope = assetScope(view, filters?.assets, { termLinks });
  const text = (filters?.text || "").trim().toLowerCase();
  const shown = new Set();
  for (const node of view.nodes) {
    if (!layerOn(layers, "kinds", node.kind)) continue;
    if (scope && !scope.has(node.id)) continue;
    if (members && !members.has(node.id)) continue;
    if (text && !`${node.name} ${(node.columns || []).join(" ")}`.toLowerCase().includes(text)) continue;
    shown.add(node.id);
  }
  return shown;
}

/* The ids of the links shown: their status's layer is on and both ends are shown. With term links
   off, a link to or from a business term is hidden unless it is on the last answer's path (`keep`). */
export function visibleLinkIds(view, layers, shownNodes, { termLinks = true, keep } = {}) {
  const terms = termLinks ? null : new Set(view.nodes.filter((node) => node.kind === "term").map((node) => node.id));
  return new Set(view.links.filter((link) => layerOn(layers, "statuses", link.status) && shownNodes.has(link.source) && shownNodes.has(link.target)
    && (!terms || keep?.has(link.id) || !(terms.has(link.source) || terms.has(link.target)))).map((link) => link.id));
}

/* The overview's figures, all from the graph the person may read: no figure is made up. Columns
   are counted from the tables they are folded into; links by each original link, before folding. */
export function overviewCounts(view) {
  const count = (kind) => view.nodes.filter((node) => node.kind === kind).length;
  const members = (kinds) => view.links.reduce((sum, link) => sum + (kinds.includes(link.kind) ? (link.members?.length || 1) : 0), 0);
  return {
    tables: count("table"),
    columns: view.nodes.reduce((sum, node) => sum + (node.columns?.length || 0), 0),
    documents: count("document"),
    repositories: count("repository"),
    terms: count("term"),
    nameMatches: members(["name_match"]),
    termTags: members(["maps_to", "mentions", "defines"]),
  };
}

/* The overview's six tiles. The graph names no repository today, so where it holds none the tile
   counts business terms instead of showing a figure the data cannot give. */
export function overviewTiles(counts) {
  return [
    ["Structured assets", counts.tables],
    ["Columns", counts.columns],
    ["Documents", counts.documents],
    counts.repositories ? ["Repositories", counts.repositories] : ["Business terms", counts.terms],
    ["Name-match links", counts.nameMatches],
    ["Term tags", counts.termTags],
  ];
}

/* The documents the person may read, for the overview's list: a short name from the id, the
   title, and the domain (else the community) a link places it in, when one does. */
export function documentList(view) {
  const byId = nodeIndex(view);
  const placed = new Map();
  for (const link of view.links) {
    for (const [doc, other] of [[link.source, link.target], [link.target, link.source]]) {
      const node = byId.get(other);
      if (byId.get(doc)?.kind !== "document" || !node || (node.kind !== "domain" && node.kind !== "community")) continue;
      if (!placed.has(doc) || (node.kind === "domain" && placed.get(doc).kind !== "domain")) placed.set(doc, node);
    }
  }
  return view.nodes.filter((node) => node.kind === "document")
    .map((node) => ({ id: node.id, short: node.id.replace(/^document:/, ""), title: node.name || node.id, domain: placed.get(node.id)?.name || "" }))
    .sort((a, b) => a.short.localeCompare(b.short));
}

/* The box around the given nodes' centres, for fitting the view to them and centring it on them. A
   node's position is its top-left corner and its mark about `mark` across, so its centre is half
   that in from it. What a node draws around its centre is screen room, not graph room (NODE_EXTENT). */
export function boundsOf(ids, positions, { mark = 12 } = {}) {
  let box = null;
  for (const id of ids) {
    const at = positions?.[id];
    if (!at) continue;
    box = box ? { x0: Math.min(box.x0, at.x), y0: Math.min(box.y0, at.y), x1: Math.max(box.x1, at.x), y1: Math.max(box.y1, at.y) } : { x0: at.x, y0: at.y, x1: at.x, y1: at.y };
  }
  if (!box) return null;
  return { x: box.x0 + mark / 2, y: box.y0 + mark / 2, width: box.x1 - box.x0, height: box.y1 - box.y0 };
}

/* What a node draws around its centre, in screen pixels: half the largest mark on each side, and its
   name to the right. A mark and its name keep their size on screen as the view zooms out (GraphNode),
   so at a zoom of 1 or less this is the room on screen whatever the zoom; zoomed in past 1 they grow
   with the view. A name is given the room of an average one. */
export const NODE_EXTENT = Object.freeze({ left: 10, right: 170, up: 10, down: 10 });
const NO_EXTENT = Object.freeze({ left: 0, right: 0, up: 0, down: 0 });

/* The zoom at which `span` graph units, and `extra` pixels that keep their screen size up to a zoom
   of 1 and grow with it past 1, just fill `room` pixels. */
function zoomToFill(span, extra, room) {
  if (room >= span + extra) return room / Math.max(span + extra, 1e-9);
  return span > 0 ? (room - extra) / span : 0;
}

/* The drawn node a path node names, if the person may read it: KGE names the graph read's node on the
   path node itself (`graph_id`, contextpath.py's READ_NODE), so the page keeps no mapping of its own; a
   column's table when columns are collapsed; null for a node with no read counterpart. */
function pathNode(byId, tableOf, node) {
  const id = node && node.graph_id;
  if (typeof id !== "string") return null;
  const drawn = tableOf?.get(id) || id;
  return byId.has(drawn) ? drawn : null;
}

/* The last answer's path on the whole graph (§7.4): the drawn nodes and links it used. A node the
   person may not read is not in the drawn graph, so it is skipped, as is any step it took; steps
   marked unused are skipped, as Context does by default. A path step lights a drawn link only when
   both its ends are drawn and the link is of the kind KGE names for the step (`graph_kind`, e.g. a
   JoinRule step is a `joins` link), never any other link between the same two nodes. */
export function pathHighlight(view, lastPath) {
  const nodes = new Set();
  const links = new Set();
  if (!view || !lastPath) return { nodes, links };
  const byId = nodeIndex(view);
  const ends = new Map();
  for (const node of lastPath.nodes || []) {
    if (!node || typeof node.id !== "string") continue;
    const drawn = pathNode(byId, view.tableOf, node);
    if (drawn) ends.set(node.id, drawn);
    if (drawn && node.used !== false) nodes.add(drawn);
  }
  const byEnds = new Map();
  for (const link of view.links) {
    for (const key of [`${link.source}\u0000${link.target}\u0000${link.kind}`, `${link.target}\u0000${link.source}\u0000${link.kind}`]) {
      if (!byEnds.has(key)) byEnds.set(key, []);
      byEnds.get(key).push(link.id);
    }
  }
  for (const edge of lastPath.edges || []) {
    if (!edge || edge.used === false || typeof edge.graph_kind !== "string") continue;
    const from = ends.get(edge.from);
    const to = ends.get(edge.to);
    if (!from || !to || from === to) continue;
    for (const id of byEnds.get(`${from}\u0000${to}\u0000${edge.graph_kind}`) || []) links.add(id);
  }
  return { nodes, links };
}

/* A node's links, each with the node at its other end and every reason it exists, grouped into the
   node's kind's sections. */
export function neighbours(view, id) {
  const byId = nodeIndex(view);
  const node = byId.get(id);
  if (!node) return [];
  const sections = (KIND_DETAILS[node.kind]?.sections || []).map(([title, kinds]) => ({ title, kinds, links: [] }));
  const other = { title: "Other links", kinds: [], links: [] };
  for (const link of view.links) {
    const end = link.source === id ? link.target : link.target === id ? link.source : null;
    if (!end) continue;
    const entry = { link, other: byId.get(end), outgoing: link.source === id };
    (sections.find((section) => section.kinds.includes(entry.other?.kind)) || other).links.push(entry);
  }
  return [...sections, other].filter((section) => section.links.length);
}

/* The distinct reasons a drawn link exists: one per original link folded into it. */
export const reasons = (link) => [...new Set((link?.members || []).map((member) => member.why).filter(Boolean))];

/* The viewport that fits a box of node centres into the canvas, with room for what each node draws
   around its centre (`extent`, screen pixels: NODE_EXTENT for the graph), leaving `left` pixels free
   for the controls over the canvas's left edge, `top` for the filter row over its top and `bottom`
   for the open assistant over its foot, each when the canvas is large enough to spare them. A fit
   never zooms past `maxZoom`, so one node alone is not blown up. */
export function fitViewport(box, { width, height, left = 0, top = 0, bottom = 0, padding = 0.12, minZoom = 0.05, maxZoom = 1.5, extent = NO_EXTENT }) {
  const room = width - left >= 360 ? left : 0;
  const [above, below] = height - top - bottom >= 200 ? [top, bottom] : height - top >= 200 ? [top, 0] : [0, 0];
  const tall = height - above - below;
  const across = (width - room) * (1 - 2 * padding);
  const down = tall * (1 - 2 * padding);
  const fill = Math.min(zoomToFill(box.width, extent.left + extent.right, across), zoomToFill(box.height, extent.up + extent.down, down));
  const zoom = Math.min(maxZoom, Math.max(minZoom, fill));
  const grow = Math.max(zoom, 1);
  return {
    x: room + (width - room) / 2 - (box.x + box.width / 2) * zoom - ((extent.right - extent.left) * grow) / 2,
    y: above + tall / 2 - (box.y + box.height / 2) * zoom - ((extent.down - extent.up) * grow) / 2,
    zoom,
  };
}
