/* #622 requirement 3: what Cerebro's compact cards read from the non-figure tools' replies, as plain data. The graph
   tools (explore, explore_question: KGE's Explore, { node: { kind, id, name }, summary, link }; read_graph: a page of
   { nodes, links }), search ({ hits: [{ kind, unit, id, title, score, why, restricted }] }) and map_term (KGE's term
   map, compact for anyone but an engineer). Ids, scores and physical names are left out of what a card shows; an
   engineer still has the raw result under the card. A reply is a card only where the server's agent ends the turn at
   it (portal/server/src/cerebro/agent.js, isCard): explore and explore_question with a node, explore_question with
   explained paths, read_graph and search with a node or a hit, map_term with an implementation, a data set, a
   document, a context or explained paths. Any other reply is no card, and the agent speaks instead.
   #638: explore_question and map_term may carry `paths`, KGE's explained paths (kge/services/graph/models.py,
   ExplainedPath), shown as KGE returns them, in KGE's order, with nothing computed. */

import { hasInternalName, toolObject } from "./chatAnswer";

export const GRAPH_TOOLS = ["explore", "explore_question", "read_graph"];
export const SEARCH_TOOL = "search";
export const MAP_TOOL = "map_term";

const toolValue = (message, names) => (names.includes(message?.name) ? toolObject(message, message.name) : null);

const text = (value) => (typeof value === "string" ? value.trim() : "");
const capital = (value) => (value ? value.charAt(0).toUpperCase() + value.slice(1) : "");
/** A unit's name in business words: "retail" reads "Retail", "wealth_mgmt" "Wealth mgmt". */
export const unitWords = (unit) => capital(text(unit).replace(/[_-]+/g, " "));

const KIND_WORDS = { table: "data set", column: "field", product: "data product", metric: "metric", passage: "document passage" };
/** A graph or search kind in business words. */
export const kindWords = (kind) => KIND_WORDS[text(kind)] || text(kind).replace(/[_-]+/g, " ");

// "Name (kind), Other (kind), and others." The names a summary sentence lists, each with its kind.
const NAMED = /([^,]+?) \((term|table|column|document|repository|community|person|domain)\)/g;
function namesIn(sentence) {
  return [...sentence.matchAll(NAMED)].map((m) => ({ name: m[1].replace(/^(?:and\s+)?/, "").trim(), kind: m[2] }));
}

const filled = (value) => (Array.isArray(value) ? value.length > 0 : typeof value === "string" ? value.trim() !== "" : Boolean(value && typeof value === "object"));
const isObject = (value) => Boolean(value && typeof value === "object" && !Array.isArray(value));

/** An explore or explore_question reply naming a node, or carrying explained paths (#638: explore_question's paths
 *  with no node, where no seed matched): { node, summary, links: [{ status, nodes: [{ name, kind }] }], paths? }, or
 *  null. `node` is null where the reply's node has no name; the card then shows the summary. The linked nodes come
 *  from the summary's sentences ("Certified links to …"). `engineer` keeps the engineer's fields of a path. */
export function exploreOf(message, engineer = false) {
  const value = toolValue(message, ["explore", "explore_question"]);
  if (!isObject(value)) return null;
  const paths = pathsOf(value, engineer);
  if (!isObject(value.node) && !paths.length) return null;
  const node = isObject(value.node) && text(value.node.name) ? { id: text(value.node.id), name: text(value.node.name), kind: text(value.node.kind) } : null;
  const summary = text(value.summary);
  const links = [];
  let named = [];
  for (const sentence of summary.split(/(?<=\.)\s+/)) {
    const found = /^(Certified|Proposed|Inferred) links to (.*)$/.exec(sentence.trim());
    if (found) links.push({ status: found[1].toLowerCase(), nodes: namesIn(found[2]) });
    else if (/^The question names /.test(sentence)) named = namesIn(sentence.replace(/^The question names /, ""));
  }
  return { node, named, summary, links, ...(paths.length ? { paths } : {}) };
}

/** The small graph of an explore reply, as MiniGraph draws a path: the node in the middle, a link to each neighbour. */
export function exploreGraph(explore) {
  if (!explore?.node) return null;
  const centre = { id: "centre", label: explore.node.name, type: explore.node.kind };
  const near = explore.links.flatMap((l) => l.nodes).map((n, i) => ({ id: `n${i}`, label: n.name, type: n.kind }));
  return { nodes: [centre, ...near], edges: near.map((n) => ({ from: "centre", to: n.id, type: "link" })) };
}

/** A read_graph page with nodes: its nodes ({ id, name, kind }) and, as MiniGraph draws it, the page's graph; or null. */
export function graphPageOf(message) {
  const value = toolValue(message, ["read_graph"]);
  if (!isObject(value) || !filled(value.nodes) || !Array.isArray(value.nodes)) return null;
  const nodes = value.nodes.filter((n) => n && text(n.id) && text(n.name)).map((n) => ({ id: text(n.id), name: text(n.name), kind: text(n.kind) }));
  const ids = new Set(nodes.map((n) => n.id));
  const edges = (Array.isArray(value.links) ? value.links : []).filter((l) => l && ids.has(l.source) && ids.has(l.target))
    .map((l) => ({ from: l.source, to: l.target, type: l.kind }));
  return { nodes, path: { nodes: nodes.map((n) => ({ id: n.id, label: n.name, type: n.kind })), edges } };
}

/** A search reply's hits as a card shows them: title, kind and unit in business words, and why it matched; null where
 *  it has none. */
export function searchOf(message) {
  const value = toolValue(message, [SEARCH_TOOL]);
  if (!isObject(value) || !filled(value.hits) || !Array.isArray(value.hits)) return null;
  return value.hits.filter((h) => h && text(h.title)).map((h) => ({
    title: text(h.title),
    kind: kindWords(h.kind),
    unit: unitWords(h.unit),
    why: text(typeof h.why === "string" ? h.why : h.why?.text),
    restricted: h.restricted === true,
  }));
}

const nameOf = (item) => (typeof item === "string" ? text(item) : text(item?.name || item?.title || item?.label || item?.unit_label));
const listOf = (value) => (Array.isArray(value) ? value : []);
const named = (items, engineer) => [...new Set(listOf(items).map(nameOf).filter((n) => n && (engineer || !hasInternalName(n))))];

/** A map_term reply in its compact form: the term, its data sets and documents in business names, its steward and its
 *  contexts, and its explained paths (#638) where it carries them. A data set with no business name is named by its
 *  unit; physical columns, joins and no-join facts are never part of it (the engineer's raw view has them). Null where
 *  the map holds no implementation, data set, document, context or path. */
const MAP_PARTS = ["implementations", "data_sets", "defined_by", "contexts", "paths"];
export function termMapOf(message, engineer = false) {
  const value = toolValue(message, [MAP_TOOL]);
  if (!isObject(value) || !MAP_PARTS.some((part) => filled(value[part]))) return null;
  const sets = value.data_sets ?? value.datasets ?? value.products;
  const dataSets = sets !== undefined ? named(sets, engineer)
    : [...new Set(listOf(value.implementations).map((i) => unitWords(i?.unit)).filter(Boolean))];
  const documents = named(value.documents ?? value.defined_by, engineer);
  const steward = typeof value.steward === "string" ? text(value.steward) : nameOf(value.steward);
  const contexts = named(value.contexts, engineer);
  const paths = pathsOf(value, engineer);
  return { term: text(value.term), dataSets, documents, steward, contexts, ...(paths.length ? { paths } : {}) };
}

/* #638: an explained path as KGE returns it (kge/services/graph/models.py, ExplainedPath): the term, its context, fact
   and measure, each join hop in KGE's order, the dimensions, conditions and time rules it reaches, and its warnings.
   Nothing is computed, reordered or inferred: a field KGE left out stays out. KGE sends physical names only in an
   engineer's reply; a measure's expression, a condition's predicate and a hop's declared options and condition are the
   engineer's, and so are the ids (a context's, a hop's, the hop a dimension is reached by): for anyone else they are
   left out whatever arrives. */
const words = (value) => (typeof value === "string" ? value.trim() : typeof value === "number" ? String(value) : "");
const kept = (entries) => Object.fromEntries(entries.filter(([, value]) => value !== "" && value !== undefined && value !== null && !(Array.isArray(value) && !value.length)));

/** A reply's explained paths, each in KGE's own shape and order, with the engineer's fields only for an engineer. */
export function pathsOf(value, engineer = false) {
  return listOf(value?.paths).filter(isObject).map((p) => {
    const m = isObject(p.measure) ? p.measure : {};
    return kept([
      ["term", words(p.term)], ["basis", words(p.basis)], ["definition", words(p.definition)], ["context", words(p.context)],
      ["context_id", engineer ? words(p.context_id) : ""], ["part_of", words(p.part_of)], ["unit", words(p.unit)], ["fact", words(p.fact)],
      ["measure", isObject(p.measure) ? kept([
        ["name", words(m.name)], ["aggregation", words(m.aggregation)], ["grain", words(m.grain)], ["currency", words(m.currency)],
        ["snapshot", typeof m.snapshot === "boolean" ? m.snapshot : undefined], ["expression", engineer ? words(m.expression) : ""],
      ]) : undefined],
      ["hops", listOf(p.hops).filter(isObject).map((h) => kept([
        ["id", engineer ? words(h.id) : ""], ["kind", words(h.kind)], ["source", words(h.source)], ["target", words(h.target)],
        ["cardinality", words(h.cardinality)], ["type", words(h.type)], ["window", words(h.window)], ["said", words(h.said)],
        ["order", words(h.order)], ["use", words(h.use)], ["warning", words(h.warning)],
        ["declared", engineer ? listOf(h.declared).map(words).filter(Boolean) : []], ["condition", engineer ? words(h.condition) : ""],
      ]))],
      ["dimensions", listOf(p.dimensions).filter(isObject).map((d) => kept([
        ["kind", words(d.kind)], ["via", engineer ? words(d.via) : ""], ["name", words(d.name)], ["said", words(d.said)],
        ["levels", listOf(d.levels).filter(isObject).map((l) => kept([
          ["name", words(l.name)], ["code", words(l.code)], ["column", words(l.column)], ["broader", words(l.broader)], ["of", words(l.of)],
        ]))],
      ]))],
      ["conditions", listOf(p.conditions).filter(isObject).map((c) => kept([
        ["kind", words(c.kind)], ["id", engineer ? words(c.id) : ""], ["label", words(c.label)], ["on", words(c.on)], ["predicate", engineer ? words(c.predicate) : ""],
      ]))],
      ["time_rules", listOf(p.time_rules).filter(isObject).map((t) => kept([["kind", words(t.kind)], ["on", words(t.on)], ["said", words(t.said)]]))],
      ["warnings", listOf(p.warnings).map(words).filter(Boolean)],
    ]);
  });
}

/** The words a non-figure card shows, for #602's rule: agent text that repeats them is not shown again. */
export function toolCardTexts(message) {
  const explore = exploreOf(message);
  if (explore) return [explore.summary];
  return [];
}
