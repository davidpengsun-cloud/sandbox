/* Cerebro's graph data (cerebro.md §5.1, §7.5): the pages of KGE's graph read, behind the
   portal's server, merged into one graph and then drawn with columns collapsed into their table.
   The read returns no figures, and none is made here. */

/* The portal server's route to KGE's GET /graph, with the person's own credentials. */
export const GRAPH_URL = "/api/cerebro/graph";
/* The server's largest page, so the whole readable graph comes in as few requests as it can. */
export const PAGE_LIMIT = 1000;
/* A bound on the pages read, so a cursor that never ends cannot hold the page forever. */
export const MAX_PAGES = 50;

export class GraphReadError extends Error {
  constructor(message, status) {
    super(message);
    this.name = "GraphReadError";
    this.status = status;
  }
}

/* Every page of the graph the person may read, in order, following the cursor until it is
   absent. `fetcher` is the browser's fetch unless a test gives its own. */
export async function readGraphPages({ fetcher = globalThis.fetch, signal, url = GRAPH_URL, limit = PAGE_LIMIT, maxPages = MAX_PAGES } = {}) {
  const pages = [];
  let cursor = null;
  do {
    const params = new URLSearchParams({ limit: String(limit) });
    if (cursor) params.set("cursor", cursor);
    const response = await fetcher(`${url}?${params}`, { signal, credentials: "same-origin", headers: { accept: "application/json" } });
    if (!response.ok) throw new GraphReadError(response.status === 403 ? "You may not read the graph." : "The graph could not be read.", response.status);
    const page = await response.json();
    pages.push(page);
    cursor = typeof page?.cursor === "string" && page.cursor ? page.cursor : null;
  } while (cursor && pages.length < maxPages);
  return { pages, complete: !cursor };
}

const linkKey = (link) => `${link.source}\u0000${link.target}\u0000${link.kind}\u0000${link.status}`;
const sameLink = (link) => `${linkKey(link)}\u0000${link.why}`;

/* The pages as one graph: each node once by id, each link once (a second reason for the same link is kept). A page carries the links whose
   source is on it, so a link may name a node that arrives on a later page; it is kept here and
   dropped only by `collapseColumns` if its end never arrives. */
export function mergePages(pages) {
  const nodes = new Map();
  const links = new Map();
  for (const page of pages || []) {
    for (const node of page?.nodes || []) if (node?.id && !nodes.has(node.id)) nodes.set(node.id, node);
    for (const link of page?.links || []) if (link?.source && link?.target && !links.has(sameLink(link))) links.set(sameLink(link), link);
  }
  return { nodes: [...nodes.values()], links: [...links.values()] };
}

/* The graph as drawn: each column folded into its table, as the `has_column` link from the table
   names it (never from the id's text). A link that touched a column now touches its table; the
   links this makes from a table to itself are dropped, as are links whose ends never arrived.
   Links that become the same (ends, kind and status) are drawn once, keeping every original link
   in `members` so each reason it exists can still be shown. Each table lists its columns. */
export function collapseColumns({ nodes, links }) {
  const byId = new Map(nodes.map((node) => [node.id, node]));
  const tableOf = new Map();
  for (const link of links) {
    if (link.kind !== "has_column") continue;
    const table = byId.get(link.source);
    const column = byId.get(link.target);
    if (table?.kind === "table" && column?.kind === "column") tableOf.set(column.id, table.id);
  }
  const columns = new Map();
  for (const [column, table] of tableOf) {
    if (!columns.has(table)) columns.set(table, []);
    columns.get(table).push(byId.get(column).name);
  }
  const drawn = nodes.filter((node) => !tableOf.has(node.id)).map((node) => (node.kind === "table" ? { ...node, columns: columns.get(node.id) || [] } : node));
  const present = new Set(drawn.map((node) => node.id));
  const edges = new Map();
  for (const link of links) {
    if (link.kind === "has_column" && tableOf.get(link.target) === link.source) continue;
    const source = tableOf.get(link.source) || link.source;
    const target = tableOf.get(link.target) || link.target;
    if (source === target || !present.has(source) || !present.has(target)) continue;
    const key = linkKey({ source, target, kind: link.kind, status: link.status });
    if (!edges.has(key)) edges.set(key, { id: key, source, target, kind: link.kind, status: link.status, members: [] });
    edges.get(key).members.push(link);
  }
  return { nodes: drawn, links: [...edges.values()], tableOf };
}

/* The drawn node an address names: a column's table, since columns are folded into it. */
export function drawnNode(view, id) {
  if (!id) return null;
  const target = view?.tableOf?.get(id) || id;
  return view?.nodes?.some((node) => node.id === target) ? target : null;
}
