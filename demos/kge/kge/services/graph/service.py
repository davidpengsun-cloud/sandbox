"""The graph read's logic (cerebro.md §5.1-§5.3): the released graph's terms, tables, columns and documents, the domains,
communities and people the released catalogue names for its products, and the links between them, filtered to the units the person may read before it is paged, each link certified, proposed or inferred
with the reason it exists. It reads no figure: the catalogue's figures, a mapping's or a mention's confidence and a
passage's text are never read into a page.

Tables, columns, declared joins, the pack's join rules and documents come from the per-release catalogue (Graph.catalogue), domains,
communities and people from the released catalogue's products (Graph.catalogue_products, read once per release), and terms, their
mappings and their defining documents from the term reads the graph keeps per release (Graph.q over TERMS), and the
passages' links to terms from Graph.passage_links, read once per release and graph write, so a repeated read sends the
graph store no query and a first one a fixed number, whatever the graph's size."""
from __future__ import annotations

import base64
import binascii
import json
import re
from dataclasses import dataclass
from urllib.parse import quote

from kge.adapters.graphstore import missing_type
from kge.services import identity
from kge.services.graph.models import (Condition, Dimension, ExplainedPath, Explore, GraphPage, Hop, Level, Link,
                                      Measure, Node, TimeRule)

DEFAULT_LIMIT = 200
MAX_LIMIT = 1000
STRENGTH = {"proposed": 0, "inferred": 1, "certified": 2}
STEWARD_ACCEPTED = frozenset({"human", "embedding_steward_accepted"})

TERMS_READ = "MATCH (t:Term) RETURN t.name AS name"
MAPPINGS_READ = "MATCH (c:Col)-[m:MAPS_TO]->(t:Term) RETURN c.id AS col, t.name AS term, m.certified AS certified"
DEFINITIONS_READ = "MATCH (d:Document)-[:DEFINES]->(t:Term) RETURN d.id AS doc, t.name AS term"
PEOPLE = {"product_owner": ("owns", "product owner"), "business_owner": ("owns", "business owner"),
          "steward": ("stewards", "steward")}
SYNCED = "from the catalogue the portal syncs; no steward has curated it yet."
DEEP_LINK = "/cerebro?mode=graph&node={}"
NAMED = 5


class BadCursor(ValueError):
    """A cursor this service did not give."""


@dataclass(frozen=True)
class Visible:
    """What one person may see of the graph, before paging: nodes by id, and links by (source, kind, target)."""
    nodes: dict[str, Node]
    links: tuple[Link, ...]


def node_id(kind: str, identifier: str) -> str:
    return f"{kind}:{identifier}"


def encode_cursor(after: str) -> str:
    return base64.urlsafe_b64encode(json.dumps({"after": after}).encode()).decode().rstrip("=")


def decode_cursor(cursor: str) -> str:
    try:
        held = json.loads(base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)).decode())
    except (binascii.Error, UnicodeDecodeError, ValueError):
        raise BadCursor("The cursor is not one this service gave.") from None
    if not isinstance(held, dict) or not isinstance(held.get("after"), str):
        raise BadCursor("The cursor is not one this service gave.")
    return held["after"]


def mapping_link(column: str, term: str, certified: bool | None) -> Link:
    if certified:
        return Link(source=column, target=term, kind="maps_to", status="certified",
                    why="A steward certified that this column carries the term.")
    return Link(source=column, target=term, kind="maps_to", status="proposed",
                why="The pack maps this column to the term; no steward has certified it yet.")


def mention_link(document: str, term: str, generator: str | None) -> Link:
    if generator in STEWARD_ACCEPTED:
        return Link(source=document, target=term, kind="mentions", status="certified",
                    why="A steward accepted that a passage of this document mentions the term.")
    return Link(source=document, target=term, kind="mentions", status="inferred",
                why="The pipeline found the term in a passage of this document; no steward has reviewed it.")


def words(name: str) -> str:
    """A name as the words it spells, for a name match: case, underscores, hyphens and spacing ignored."""
    return " ".join(re.split(r"[\W_]+", name.casefold())).strip()


def catalogue_links(products, units: set[str], nodes: dict[str, Node]) -> list[Link]:
    """The domain, community and person nodes of the products in the readable units, added to nodes, and their links,
    each proposed, since the catalogue the portal syncs is not curated. A label on a product in a unit the person may
    not read makes no node, so no page says it exists; nor does one on a product whose table the graph does not hold."""
    links: list[Link] = []
    for product in products:
        table = node_id("table", product["id"])
        if product["unit"] not in units or table not in nodes:
            continue
        domain = product["domain"] and node_id("domain", product["domain"])
        if domain:
            nodes[domain] = Node(kind="domain", id=domain, name=product["domain"])
            links.append(Link(source=table, target=domain, kind="in_domain", status="proposed",
                              why=f"The table is placed in this domain {SYNCED}"))
        if product["community"]:
            community = node_id("community", product["community"])
            nodes[community] = Node(kind="community", id=community, name=product["community"])
            if domain:
                links.append(Link(source=domain, target=community, kind="in_community", status="proposed",
                                  why=f"A table of this domain is placed in this community {SYNCED}"))
            else:
                links.append(Link(source=table, target=community, kind="in_community", status="proposed",
                                  why=f"The table is placed in this community {SYNCED}"))
        for label, (kind, role) in PEOPLE.items():
            if product[label]:
                person = node_id("person", product[label])
                nodes[person] = Node(kind="person", id=person, name=product[label])
                links.append(Link(source=person, target=table, kind=kind, status="proposed",
                                  why=f"The table names this person as its {role} {SYNCED}"))
    return links


def name_matches(nodes: dict[str, Node], mapped: set[tuple[str, str]]) -> list[Link]:
    """An inferred link from each column to the term its name spells, where no mapping links the two: a name match
    only, not a verified mapping or join."""
    terms: dict[str, list[str]] = {}
    for node in nodes.values():
        if node.kind == "term":
            terms.setdefault(words(node.name), []).append(node.id)
    links = []
    for node in nodes.values():
        if node.kind != "column":
            continue
        for term in terms.get(words(node.name), ()):
            if (node.id, term) not in mapped:
                links.append(Link(source=node.id, target=term, kind="name_match", status="inferred",
                                  why="The column's name matches the term's name; a name match only, not a verified "
                                      "mapping or join."))
    return links


def visible(engine, who: str) -> Visible:
    """The nodes and links the person may see. A table, its columns and its joins are seen only in a readable unit; a
    document only with a readable unit (one with none is withheld, as the steward's decided list withholds it). A
    link is seen only when both its ends are. A pack join rule is a certified joins link between its tables, seen when
    the person reads both ends; the table a cross-reference joins through is a curated source's (the compiler's S23),
    which no person reads on its own, so it is never a node and the read never names it. A pair a declared key already
    joins keeps the key's link alone, whichever way either runs. Every glossary term is seen, since the glossary is
    shared, so which terms a page holds says nothing of the units the person may not read; only their links to those
    units are absent. A passage-to-term link already made is certified when a steward accepted it and inferred
    otherwise; one still in the steward's queue is proposed; where both reach the same pair, the stronger status is
    kept. A column whose name spells a term it is not mapped to has an inferred name-match link to it. Domains,
    communities and people are seen only as a readable unit's product names them."""
    g = engine.g
    units = set(identity.readable_units(engine, who))
    catalogue = g.catalogue()
    nodes: dict[str, Node] = {}
    links: list[Link] = []
    for unit in sorted(units):
        for table in sorted(catalogue.tables.get(unit, ())):
            tid = node_id("table", f"{unit}.{table}")
            nodes[tid] = Node(kind="table", id=tid, name=table)
            for column in catalogue.columns.get(f"{unit}.{table}", ()):
                cid = node_id("column", f"{unit}.{table}.{column}")
                nodes[cid] = Node(kind="column", id=cid, name=column)
                links.append(Link(source=tid, target=cid, kind="has_column", status="certified",
                                  why="The warehouse schema declares this column on the table."))
        for join in catalogue.joins.get(unit, ()):
            links.append(Link(source=node_id("table", f"{unit}.{join['ft']}"), target=node_id("table", f"{unit}.{join['tt']}"),
                              kind="joins", status="certified",
                              why=f"A declared key joins {join['ft']}.{join['fc']} to {join['tt']}.{join['tc']}."))
    for rule in catalogue.rules:
        links.append(Link(source=node_id("table", rule["ft"]), target=node_id("table", rule["tt"]), kind="joins",
                          status="certified", why=f"The pack's join rule {rule['id']} joins the two tables."))
    for doc in catalogue.documents:
        if doc.get("unit") and doc["unit"] in units:
            did = node_id("document", doc["id"])
            nodes[did] = Node(kind="document", id=did, name=doc.get("title") or doc["id"])

    for r in g.q(MAPPINGS_READ):
        links.append(mapping_link(node_id("column", r["col"]), node_id("term", r["term"]), r["certified"]))
    for r in g.q(DEFINITIONS_READ):
        links.append(Link(source=node_id("document", r["doc"]), target=node_id("term", r["term"]), kind="defines",
                          status="certified", why="The document is a defining source for the term."))
    mentions, waiting = g.passage_links()
    for doc, term, generator in mentions:
        links.append(mention_link(node_id("document", doc), node_id("term", term), generator))
    for doc, term in waiting:
        links.append(Link(source=node_id("document", doc), target=node_id("term", term), kind="mentions",
                          status="proposed",
                          why="An embedding suggested that a passage of this document mentions the term; a steward has "
                              "not decided yet."))
    for r in g.q(TERMS_READ):
        if r["name"]:
            tid = node_id("term", r["name"])
            nodes[tid] = Node(kind="term", id=tid, name=r["name"])
    links.extend(catalogue_links(g.catalogue_products().items, units, nodes))
    links.extend(name_matches(nodes, {(link.source, link.target) for link in links if link.kind == "maps_to"}))

    seen: dict[tuple[str, str, str], Link] = {}
    for link in links:
        if link.source in nodes and link.target in nodes:
            key = (min(link.source, link.target), link.kind, max(link.source, link.target)) if link.kind == "joins" else (
                link.source, link.kind, link.target)
            held = seen.get(key)
            if held is None or STRENGTH[link.status] > STRENGTH[held.status]:
                seen[key] = link
    return Visible(nodes=nodes, links=tuple(seen[k] for k in sorted(seen)))


def neighbourhood(view: Visible, node: str) -> Visible:
    """The node and its neighbours, one link away, with the links between them. A node the person may not see is a
    LookupError, as one that does not exist is."""
    if node not in view.nodes:
        raise LookupError("No such node.")
    near = {node} | {link.target for link in view.links if link.source == node} | {
        link.source for link in view.links if link.target == node}
    return Visible(nodes={i: view.nodes[i] for i in near},
                   links=tuple(link for link in view.links if link.source in near and link.target in near))


def read(engine, who: str, node: str | None = None, cursor: str | None = None, limit: int = DEFAULT_LIMIT) -> GraphPage:
    """One page of the graph the person may read: nodes in id order after the cursor, up to limit, and each link whose
    source is on the page, so every link is on exactly one page. With node, the page is of that node's neighbourhood.
    The cursor names the last node given; it is absent on the last page. No total is given."""
    view = visible(engine, who)
    if node is not None:
        view = neighbourhood(view, node)
    after = decode_cursor(cursor) if cursor else None
    ids = [i for i in sorted(view.nodes) if after is None or i > after]
    page = ids[:limit]
    on_page = set(page)
    more = {"cursor": encode_cursor(page[-1])} if len(ids) > limit else {}
    return GraphPage(nodes=[view.nodes[i] for i in page],
                     links=[link for link in view.links if link.source in on_page], **more)


SEED_ORDER = ("term", "domain", "community", "repository", "table", "document", "column", "person")
UNMATCHED = "No node you may read matches the question."


def summarise(view: Visible, centres: list[str]) -> list[str]:
    """The neighbours of the centres, named by the status of the link to them, the centres themselves left out. No count
    and no figure."""
    held = set(centres)
    by_status: dict[str, list[str]] = {}
    for link in view.links:
        ends = {link.source, link.target}
        for centre in held & ends:
            other = (ends - {centre} or {centre}).pop()
            if other in held:
                continue
            named = f"{view.nodes[other].name} ({view.nodes[other].kind})"
            if named not in by_status.setdefault(link.status, []):
                by_status[link.status].append(named)
    parts = []
    for status in ("certified", "proposed", "inferred"):
        names = sorted(by_status.get(status, ()))
        if names:
            more = ", and others" if len(names) > NAMED else ""
            parts.append(f"{status.capitalize()} links to {', '.join(names[:NAMED])}{more}.")
    return parts


def seeds(view: Visible, question: str) -> list[str]:
    """The nodes the person may see whose name the question spells as whole words, case, underscores and hyphens
    ignored: terms first, then domains, communities, repositories, tables, documents, columns and people, a longer name
    before a shorter, at most NAMED of them. A lexical match only; no planner, model or figure is read."""
    asked = f" {words(question)} "
    found = [n for n in view.nodes.values() if words(n.name) and f" {words(n.name)} " in asked]
    found.sort(key=lambda n: (SEED_ORDER.index(n.kind), -len(words(n.name)), n.id))
    return [n.id for n in found[:NAMED]]


def explore(engine, who: str, node: str | None = None, question: str | None = None, engineer: bool = False) -> Explore:
    """A short summary of a node's, or a question's, neighbourhood the person may read, its neighbours named by the
    status of the link to them, and a deep link that opens the graph on the node, or on the node the question matches
    best. A question's neighbourhood is that of the nodes its words name (seeds); with none, the summary says so and
    the link opens the graph on no node. A question whose term resolves to a context also has its whole path explained,
    in order (paths, question_paths), and one sentence of each in the summary; a hierarchy term has its members and
    codes (members_of); a join, join:<id>, its facts (join_hop), its link opening the graph on the table it joins from,
    a node the graph read holds. The question is neither kept nor echoed. No count and
    no figure. Business words for every role, physical names for an engineer (engineer). A node the person may not see
    is a LookupError, as one that does not exist is."""
    if (node is None) == (question is None):
        raise ValueError("Give a node or a question, not both.")
    if node is not None and node.startswith("join:"):
        hop = join_hop(engine, who, node.split(":", 1)[1], engineer)
        centre = Node(kind="join", id=node, name=hop.id)
        source = node_id("table", engine.semantics.declared[hop.id].source)
        return Explore(node=centre, summary=f"{hop.id} is a join: {hop.said}" + (f" {hop.warning}" if hop.warning else ""),
                       link=DEEP_LINK.format(quote(source, safe="")), hop=hop)
    view = visible(engine, who)
    if question is not None:
        paths = question_paths(engine, who, question, engineer)
        explained_ = {"paths": paths} if paths else {}
        found = seeds(view, question)
        if not found:
            if paths:
                return Explore(summary=" ".join(paths_summary(paths)), link=DEEP_LINK.split("&", 1)[0], **explained_)
            return Explore(summary=UNMATCHED, link=DEEP_LINK.split("&", 1)[0])
        named = ", ".join(f"{view.nodes[i].name} ({view.nodes[i].kind})" for i in found)
        near = [neighbourhood(view, i) for i in found]
        links = {(link.source, link.kind, link.target): link for n in near for link in n.links}
        whole = Visible(nodes={k: v for n in near for k, v in n.nodes.items()}, links=tuple(links[k] for k in sorted(links)))
        parts = [f"The question names {named}.", *summarise(whole, found), *paths_summary(paths)]
        return Explore(node=view.nodes[found[0]], summary=" ".join(parts), link=DEEP_LINK.format(quote(found[0], safe="")),
                       **explained_)
    near = neighbourhood(view, node)
    centre = near.nodes[node]
    parts = [f"{centre.name} is a {centre.kind}.", *summarise(near, [node])]
    if len(parts) == 1:
        parts.append("It has no links you may read.")
    members = members_of(engine, who, centre.name, engineer) if centre.kind == "term" else []
    if members:
        parts.append(f"Its hierarchy: {', '.join(level.name + (f' ({level.code})' if level.code else '') for level in members[:NAMED])}"
                     + (", and others." if len(members) > NAMED else "."))
    return Explore(node=centre, summary=" ".join(parts), link=DEEP_LINK.format(quote(node, safe="")),
                   **({"members": members} if members else {}))

DEFAULT_CARDINALITY = "one-to-one"
DEFAULT_TYPE = "inner"
OPTIONS = ("cardinality", "type", "validity_window")
KIND_WORDS = {"key": "key join", "calendar_rollup": "calendar roll-up", "hierarchy": "hierarchy roll-up",
              "cross_reference": "source-system cross-reference"}
AGGREGATION_WORDS = {"sum": "summed", "count": "counted", "count_distinct": "counted once each", "avg": "averaged",
                     "min": "the least taken", "max": "the greatest taken", "median": "the median taken"}
TERM_DEFINITIONS = "MATCH (t:Term) RETURN t.name AS name, t.id AS id, t.definition AS definition"
BROADER_READ = "MATCH (a:Term)-[:BROADER]->(b:Term) RETURN a.name AS narrower, b.name AS broader"
IS_A_READ = "MATCH (a:Term)-[:IS_A]->(b:Term) RETURN a.name AS member, b.name AS of"
CODED_AS_READ = "MATCH (t:Term)-[c:CODED_AS]->(x:Col) RETURN t.name AS term, x.id AS col, c.code AS code"
CODE_OPS = ("eq", "in")
DERIVED = ("derived_measure", "count_metric")
AMOUNT = r"\$?\d[\d,.]*\s*(?:k|m|bn|thousand|million|billion)?"
SIZE = (rf"\s+(?:of|worth)\s+(?:(?:at least|at most|more than|less than|over|under|above|below)\s+{AMOUNT}"
        rf"|{AMOUNT}\s+(?:or more|or less|and over|and above|and under|or above|or below)\b)")


def sized(engine, context, question: str) -> bool:
    """Whether the question states a size threshold on the context's rows, as Ask's ledger reads one
    (kge/semantics/coverage.py, magnitude_filters): the name of the entity the fact table holds, then only "of" or
    "worth", then a comparison with a number. Any other comparison is a threshold on an output, not on rows. The entity
    is named by its label and that label's plural; another name the pack's vocabulary gives it is not read, so such a
    threshold goes unsaid rather than one being invented."""
    names = {spelled(e["label"]).casefold() for e in engine.semantics.entities.values() if e["table"] == context.fact}
    names = sorted({form for n in names for form in (n, n + "s", n + "es", *([n[:-1] + "ies"] if n.endswith("y") else []))},
                   key=len, reverse=True)
    return bool(names) and re.search(rf"\b(?:{'|'.join(map(re.escape, names))}){SIZE}", question, re.I) is not None


def held_rows(g, cypher: str) -> list[dict]:
    """A term read's rows, or none on a graph loaded before its node or edge type existed (as Graph.read_vocabulary
    reads a missing type)."""
    try:
        return g.q(cypher)
    except Exception as exc:
        if not missing_type(exc):
            raise
        return []


def spelled(name: str) -> str:
    """A pack label as words: underscores as spaces."""
    return name.replace("_", " ").strip()


@dataclass(frozen=True)
class Words:
    """How a path names what it holds: an engineer sees physical names, anyone else business words alone (a table by
    the entity it represents, a column by the business name the pack gives it, a unit by its label), and none names a
    unit the person may not read."""
    engine: object
    engineer: bool
    units: frozenset

    def unit(self, unit: str) -> str:
        return (getattr(self.engine, "UNIT_LABEL", None) or {}).get(unit, unit)

    def table(self, table: str) -> str:
        if self.engineer:
            return table
        sem = self.engine.semantics
        bound = {v for c in sem.contexts.values() for v in (c.bindings or {}).values()}
        labels = [e["label"] for i, e in sem.entities.items() if e["table"] == table and i not in bound]
        labels = labels or [e["label"] for e in sem.entities.values() if e["table"] == table]
        return spelled(labels[0]) if labels else f"{self.unit(table.split('.', 1)[0])} data"

    def column(self, column: str) -> str | None:
        """A column, unit.table.column: its physical name for an engineer, else its business name, or None."""
        if self.engineer:
            return column
        business = getattr(self.engine, "BUSINESS", None) or {}
        return business.get(column.split(".", 1)[1]) if "." in column else None

    def readable(self, table: str) -> bool:
        return table.split(".", 1)[0] in self.units


def window_words(words: Words, window: dict, target: str) -> str:
    if words.engineer:
        return f"{window['date']} BETWEEN {window['effective']} AND {window['expiry']}"
    date = words.column(window["date"]) or f"a date of the {words.table(window['date'].rsplit('.', 1)[0])}"
    return f"the {words.table(target)} version in force on {date}"


def hop_of(words: Words, j, order: int | None = None, use: str | None = None, unit: str | None = None) -> Hop:
    """A declared join's facts: its kind, its ends, and its cardinality, type and validity window from the pack's join
    options, else what SQL generation assumes (kge/semantics/query.py, Join and Plan.add_join): one-to-one, an inner
    join (a left join only where an exclusion must keep fact rows with no match), and no window. A one-to-many hop
    carries a plain warning. A required hop (use always) is never left-joined for an exclusion. A cross-reference reads
    from the side in the context's unit, as Ask's planner orients it; its condition, which names the curated table it
    joins through, is an engineer's only where that table's unit is readable too. A join to a table under an alias of
    its own (a self-join) names its far side by that alias."""
    options = dict(j.options or {})
    declared = [k for k in OPTIONS if k in options]
    cardinality = options.get("cardinality", DEFAULT_CARDINALITY)
    typed = "type" in options or "validity_window" in options
    join_type = options.get("type", DEFAULT_TYPE) if typed else DEFAULT_TYPE
    window = options.get("validity_window")
    near, far = (j.target, j.source) if j.through and unit and j.source.split(".", 1)[0] != unit else (j.source, j.target)
    source, target = words.table(near), words.table(far)
    if j.alias and far == j.target:
        target = f"{far} AS {j.alias}" if words.engineer else spelled(j.alias)
    kind = KIND_WORDS.get(j.kind, spelled(j.kind))
    said = f"{source} to {target}, a {kind}: {cardinality}, {'an' if join_type == 'inner' else 'a'} {join_type} join"
    said += f", valid on {window_words(words, window, far)}" if window else ", no validity window"
    if j.through:
        said += ", through certified matches only"
    assumed = [k for k in OPTIONS if k not in declared]
    if assumed:
        said += (f" ({', '.join(k.replace('_', ' ') for k in assumed)} not declared: as SQL generation assumes"
                 + (", which joins left only where an exclusion must keep rows with no match"
                    if not typed and use != "always" else "") + ")")
    warning = (f"One-to-many: a {source} row can match several {target} rows, so a figure summed across this hop can "
               "count a row more than once.") if cardinality == "one-to-many" else None
    extra = {"condition": j.condition} if words.engineer and (not j.through or words.readable(j.through)) else {}
    return Hop(id=j.id, kind=j.kind, source=source, target=target, cardinality=cardinality, type=join_type,
               declared=declared, said=said + ".", **({"window": window_words(words, window, far)} if window else {}),
               **({"order": order} if order is not None else {}), **({"use": use} if use else {}),
               **({"warning": warning} if warning else {}), **extra)


def parents(context) -> dict:
    """Each table the context's own joins reach, and the join that reaches it from the fact, as the planner reaches
    one (kge/semantics/query.py, Plan.reach): no alias, no cross-reference."""
    held: dict = {context.fact: None}
    for j in context.joins:
        if not j.through and j.source in held and j.target not in held and not j.alias:
            held[j.target] = j
    return held


def needed(context, req, engine) -> set[str]:
    """The joins of the context that this question's dimensions and conditions need: those reaching each table it groups
    by (a band by its column), filters on, names an entity on or counts, as the context's bindings name it, and a
    cross-reference to a unit it names."""
    bind = context.bindings or {}
    bands = {r["params"]["id"]: r["params"]["column"] for r in getattr(engine, "VOCABULARY", ()) or ()
             if r["kind"] == "band_options"}
    columns = [bind.get(bands.get(c, c), bands.get(c, c)) for c in (*req.group_by, *[f for f in req.filtered if f])]
    entities = engine.semantics.entities
    tables = {c.rsplit(".", 1)[0] for c in columns if c.count(".") >= 2}
    for named in (req.entity[0] if req.entity else None, req.counted):
        entity = named and bind.get(named, named)
        if entity in entities:
            tables.add(entities[entity]["table"])
        elif entity and entity.count(".") >= 2:
            tables.add(entity.rsplit(".", 1)[0])
    reach = parents(context)
    out: set[str] = set()
    for table in tables:
        while reach.get(table) is not None:
            out.add(reach[table].id)
            table = reach[table].source
    for j in context.joins:
        if j.through and {j.source.split(".", 1)[0], j.target.split(".", 1)[0]} & set(req.units):
            out.add(j.id)
    return out


def coded(engine) -> dict[str, list[dict]]:
    """Every code a term declares (CODED_AS), by the table of its column."""
    out: dict[str, list[dict]] = {}
    for r in held_rows(engine.g, CODED_AS_READ):
        out.setdefault(r["col"].rsplit(".", 1)[0], []).append(r)
    return out


def levels_on(words: Words, engine, table: str, codes: dict, broader: dict) -> list[Level]:
    """A hierarchy table's levels and members: each term coded on one of its columns, with its code and broader term;
    each value phrase the pack's vocabulary decodes on one of its columns, with the code; and each group-by phrase that
    names one of its columns, in that order, each once."""
    out: list[Level] = []
    for r in sorted(codes.get(table, ()), key=lambda r: (r["col"], r["term"], r["code"] or "")):
        out.append(Level(name=r["term"], **({"code": r["code"]} if r["code"] else {}), **column_of(words, r["col"]),
                         **({"broader": broader[r["term"]]} if r["term"] in broader else {})))
    for row in getattr(engine, "VOCABULARY", ()) or ():
        p = row["params"]
        if row["kind"] != "predicate" or p.get("op") not in CODE_OPS or str(p.get("column", "")).rsplit(".", 1)[0] != table:
            continue
        value = shown_value(p.get("value") if p["op"] == "eq" else p.get("values") or ())
        phrases = p.get("phrases") or ([p["phrase"]] if p.get("phrase") else [])
        if phrases:
            out.append(Level(name=phrases[-1], **({"code": value} if value else {}), **column_of(words, p["column"])))
    for phrase, column in sorted((getattr(engine, "GROUP_BY", None) or {}).items()):
        if column.rsplit(".", 1)[0] == table:
            out.append(Level(name=phrase, **column_of(words, column)))
    seen, kept = set(), []
    for level in out:
        key = (level.name, level.code, level.column)
        if key not in seen:
            seen.add(key)
            kept.append(level)
    return kept


def column_of(words: Words, column: str) -> dict:
    named = words.column(column)
    return {"column": named} if named else {}


def dimensions_of(words: Words, engine, context, hops, codes, broader) -> list[Dimension]:
    """The dimensions the path reaches, by hop in order: a calendar roll-up with the fact's period column and calendar,
    a hierarchy with its levels and codes, and a source-system cross-reference."""
    out: list[Dimension] = []
    period = (getattr(engine, "PERIODS", None) or {}).get(context.fact) or {}
    joins = {j.id: j for j in context.joins}
    for hop in hops:
        j = joins[hop.id]
        if j.kind == "calendar_rollup":
            calendar = spelled(period.get("calendar") or "calendar")
            column = words.column(f"{context.fact}.{period['column']}") if period.get("column") else None
            said = f"Dates roll up to {hop.target} on the {calendar}"
            said += f", from the {column} ({spelled(period.get('kind') or 'date')})" if column else ""
            out.append(Dimension(kind="calendar", via=j.id, name=hop.target, said=said + ".", levels=[]))
        elif j.kind == "hierarchy":
            levels = levels_on(words, engine, j.target, codes, broader)
            said = f"{hop.source} rolls up to {hop.target}" + (
                f", by {', '.join(level.name for level in levels[:NAMED])}" if levels else "")
            out.append(Dimension(kind="hierarchy", via=j.id, name=hop.target, said=said + ".", levels=levels))
        elif j.kind == "cross_reference" or j.through:
            out.append(Dimension(kind="cross_reference", via=j.id, name=hop.target,
                                 said=f"{hop.source} is matched to {hop.target} across source systems, through "
                                      "certified matches only.", levels=[]))
    return out


def conditions_of(words: Words, context) -> list[Condition]:
    """The context's standing filters and its row selectors, in order, each on a table the person may read."""
    out = [Condition(kind="filter", id=f.id, label=f.label, on=words.table(f.table),
                     **({"predicate": f.predicate} if words.engineer else {})) for f in context.filters if words.readable(f.table)]
    out += [Condition(kind="inclusion" if s.mode == "include" else "exclusion", id=s.id, label=s.label, on=words.table(s.table),
                      **({"predicate": s.predicate} if words.engineer else {})) for s in context.selectors if words.readable(s.table)]
    return out


def dated_by_join(context, table: str, policy: dict) -> bool:
    """Whether the join that reaches table already dates it, as Plan.version reads one (kge/pipeline/sqlguard.py,
    temporal_rule): its condition or validity window names the table's own history columns, so SQL generation adds no
    version predicate of its own. A join under an alias dates that alias's read alone, never the table's own."""
    columns = {policy.get(k) for k in ("is_current", "valid_from", "valid_to")} - {None}
    bare = table.rsplit(".", 1)[-1]
    return any(j.target == table and not j.through and not j.alias and any(re.search(rf"\b{re.escape(j.alias or bare)}\.{re.escape(c)}\b",
                                                                          j.predicate, re.I) for c in columns)
               for j in context.joins)


def time_rules_of(words: Words, engine, context, tables: list[str]) -> list[TimeRule]:
    """The time rules on the path's tables, in path order, as SQL generation applies them (kge/semantics/query.py,
    Plan.version and the release driver): a table that keeps its history (scd2) is read as its version current now, or
    as reported as the version valid on the month's freeze date, unless the context reads it on a date of its own
    (version_dates, said once, as its own rule) or the join that reaches it already dates it; only the release driver a
    required calendar roll-up reaches reports released months alone."""
    sem = engine.semantics
    out: list[TimeRule] = []
    driver = next((j.target for j in context.joins if j.required and j.kind == "calendar_rollup" and j.target in sem.releases),
                  None)
    dated = context.version_dates or {}
    for table in tables:
        policy = sem.scd2.get(table)
        if policy and table not in dated and not dated_by_join(context, table, policy):
            said = (f"The {words.table(table)} keeps its history: it is read as the version current now, or, as reported, "
                    "the version valid on the month's freeze date")
            if words.engineer:
                said += (f" ({policy.get('valid_from')} to {policy.get('valid_to')}, current where "
                         f"{policy.get('is_current')})")
            out.append(TimeRule(kind="scd2", on=words.table(table), said=said + "."))
        release = sem.releases.get(table) if table == driver else None
        if release:
            said = ("Only released months are reported, and the latest month is the latest released; a month as reported "
                    "is read as it stood when it was frozen")
            if words.engineer:
                said += (f" ({release.get('period')} released by {release.get('released')}, frozen on "
                         f"{release.get('freeze_date')}, released on {release.get('release_date')})")
            out.append(TimeRule(kind="release_driver", on=words.table(table), said=said + "."))
    for table, date in sorted((context.version_dates or {}).items()):
        if words.readable(table):
            at = words.column(date) or f"a date of the {words.table(date.rsplit('.', 1)[0])}"
            out.append(TimeRule(kind="version_date", on=words.table(table),
                                said=f"The {words.table(table)} is read as the version valid on {at}."))
    return out


def measure_of(words: Words, engine, context) -> Measure:
    declared = engine.semantics.measure_declarations.get(context.measure) or {}
    extra = {"expression": context.expression} if words.engineer and context.expression else {}
    return Measure(name=context.term, aggregation=declared.get("aggregation") or context.aggregation,
                   grain=declared.get("grain") or context.grain,
                   **({"currency": declared["currency"]} if declared.get("currency") else {}),
                   **({"snapshot": bool(declared["snapshot"])} if declared.get("snapshot") is not None else {}), **extra)


def basis_of(engine, term: str, question: str) -> str:
    """The words of the question that name the term, as the question writes them: the longest of its name and the
    phrases that name it (Alias); the term's name where none is in the question."""
    phrases = [term, *(p for p, t in (getattr(engine, "ALIASES", None) or {}).items() if t == term)]
    found = []
    for phrase in phrases:
        spelled_out = words(phrase).split()
        if spelled_out:
            match = re.search(r"\b" + r"[\W_]+".join(map(re.escape, spelled_out)) + r"s?\b", question, re.I)
            if match:
                found.append(match.group(0))
    return max(found, key=lambda m: (len(words(m)), m)) if found else term


OP_WORDS = {"=": "is", "eq": "is", "<>": "is not", "ne": "is not", "IN": "is one of", "in": "is one of",
            "NOT IN": "is none of", "not_in": "is none of", ">": "is over", "gt": "is over", "<": "is under", "lt": "is under",
            ">=": "is at least", "ge": "is at least", "<=": "is at most", "le": "is at most", "IS NULL": "is empty",
            "IS NOT NULL": "is not empty", "= TRUE": "is true", "CONTAINS": "contains", "STARTS WITH": "starts with"}


def shown_value(value) -> str:
    """A stated value as words: a list's items joined, none for no value."""
    if value is None:
        return ""
    if isinstance(value, (list, tuple, set, frozenset)):
        return ", ".join(str(v) for v in value)
    return str(value)


def sql_value(value) -> str:
    """A stated value as SQL text: text quoted, a list in brackets."""
    if isinstance(value, (list, tuple, set, frozenset)):
        return "(" + ", ".join(sql_value(v) for v in value) + ")"
    if isinstance(value, str):
        return "'" + value.replace("'", "''") + "'"
    return str(value)


def asked_phrase(engine, question: str, column: str, value) -> str | None:
    """The longest phrase of the question the pack's vocabulary decodes to value on column (a predicate's phrase)."""
    asked = f" {words(question)} "
    found = []
    for row in getattr(engine, "VOCABULARY", ()) or ():
        p = row["params"]
        if row["kind"] != "predicate" or p.get("column") != column:
            continue
        if value is not None and str(p.get("value")) != str(value) and str(value) not in {str(v) for v in p.get("values") or ()}:
            continue
        found += [phrase for phrase in (p.get("phrases") or [p.get("phrase")]) if phrase and f" {words(phrase)} " in asked]
    return max(found, key=lambda phrase: (len(phrase), phrase)) if found else None


def asked_conditions(words_: Words, engine, context, req, question: str) -> list[Condition]:
    """The conditions the question states, each on the column the pack's vocabulary names for its words, where that
    column is in the context's unit: by the question's own phrase and the code it decodes to, and for an engineer the
    physical column and condition."""
    out = []
    for i, (_, op, value) in enumerate(req.filters if req is not None else ()):
        qualified = req.filtered[i] if i < len(req.filtered) and req.filtered[i] else None
        bound = (context.bindings or {}).get(qualified, qualified) if qualified else None
        if bound is None or bound.split(".", 1)[0] != context.unit or not words_.readable(bound):
            continue
        shown = shown_value(value)
        phrase = asked_phrase(engine, question, qualified, value)
        named = words_.column(bound)
        label = (f"{phrase} ({shown})" if phrase and shown else phrase
                 or f"{named or 'its value'} {OP_WORDS.get(op, op.replace('_', ' '))}" + (f" {shown}" if shown else ""))
        out.append(Condition(kind="asked", id=f"asked:{bound if words_.engineer else words(label)}", label=label,
                             on=words_.table(bound.rsplit(".", 1)[0]),
                             **({"predicate": f"{bound} {op}" + (f" {sql_value(value)}" if value is not None else "")}
                                if words_.engineer else {})))
    amounts = [v for v in context.values if v.rsplit(".", 1)[0] == context.fact]
    if question and sized(engine, context, question) and context.aggregation != "count" and len(amounts) == 1:
        out.append(Condition(kind="asked", id=f"asked:{amounts[0] if words_.engineer else 'size threshold'}",
                             on=words_.table(context.fact),
                             label=f"a size threshold the question states, on each row's {words_.column(amounts[0]) or 'amount'}, "
                                   "whatever its sign",
                             **({"predicate": f"abs({amounts[0]}) compared with the stated size"} if words_.engineer else {})))
    return out


def explained(engine, words_: Words, context, req, question: str, definitions: dict, codes: dict,
              broader: dict, part_of: str | None = None, basis: str | None = None) -> ExplainedPath:
    """One context's whole path, in order, for the question req parses."""
    used = needed(context, req, engine) if req is not None else set()
    hops = []
    for n, j in enumerate((j for j in context.joins if words_.readable(j.source) and words_.readable(j.target)), 1):
        use = "always" if j.required else "question" if j.id in used else "available"
        hops.append(hop_of(words_, j, n, use, context.unit))
    tables = list(dict.fromkeys([context.fact, *(j.target for j in context.joins
                                                 if words_.readable(j.target) and words_.readable(j.source))]))
    measure = measure_of(words_, engine, context)
    named = basis or (basis_of(engine, context.term, question) if question else context.term)
    return ExplainedPath(term=context.term, basis=named, definition=definitions.get(context.term) or "",
                         context=context.label or context.id, context_id=context.id, **({"part_of": part_of} if part_of else {}),
                         unit=words_.unit(context.unit), fact=words_.table(context.fact),
                         measure=measure, hops=hops, dimensions=dimensions_of(words_, engine, context, hops, codes, broader),
                         conditions=[*conditions_of(words_, context), *asked_conditions(words_, engine, context, req, question)],
                         time_rules=time_rules_of(words_, engine, context, tables),
                         warnings=[h.warning for h in hops if h.warning and h.use in ("always", "question")])


def term_reads(engine) -> tuple[dict, dict, dict, dict]:
    """The terms' definitions, their names by glossary id, each term's broader term and the codes by table: term reads
    the graph keeps per release (Graph.q over TERMS), so a repeated explore sends the graph store no query."""
    g = engine.g
    rows = held_rows(g, TERM_DEFINITIONS)
    definitions = {r["name"]: r["definition"] for r in rows}
    names = {r["id"]: r["name"] for r in rows if r.get("id")}
    broader = {r["narrower"]: r["broader"] for r in held_rows(g, BROADER_READ)}
    return definitions, names, broader, coded(engine)


def operands(engine, term: str, names: dict) -> list[tuple[str, str]]:
    """A derived measure's or a count metric's contexts, for a term that resolves to none of its own: each with what
    it is of the term (numerator of a ratio, a count metric's context), as the pack's vocabulary declares it."""
    out = []
    for row in getattr(engine, "VOCABULARY", ()) or ():
        p = row["params"]
        if row["kind"] not in DERIVED or names.get(p.get("term"), p.get("term")) != term:
            continue
        label = spelled(p.get("label") or term)
        if row["kind"] == "derived_measure":
            kind = spelled((p.get("formula") or {}).get("kind") or "derived measure")
            roles = p.get("contexts") or {}
            ordered = [r for r in ("numerator", "denominator") if r in roles] + sorted(r for r in roles if r not in ("numerator", "denominator"))
            out += [(roles[role], f"the {role} of {label}, a {kind}") for role in ordered]
        elif p.get("context"):
            out.append((p["context"], f"the population {label} counts"))
    return out


def term_contexts(engine, term: str, names: dict, units: frozenset) -> list[tuple]:
    """The contexts a term resolves to in the units the person may read, each with what it is of the term: its own
    contexts, or for a derived measure or a count metric with none of its own, its operands' (part says which)."""
    sem = engine.semantics
    found = [(c, None) for c in sem.contexts.values() if c.term == term]
    if not found:
        found = [(sem.contexts[cid], part) for cid, part in operands(engine, term, names) if cid in sem.contexts]
    return [(c, part) for c, part in found if c.unit in units]


def question_paths(engine, who: str, question: str, engineer: bool) -> list[ExplainedPath]:
    """The question's paths: for each term it names that resolves to a context (its main term first), each such context
    in a unit the person may read, in a unit the question names where it names one, as Ask's planner matches them
    (planrules.matched_contexts); for a derived measure or a count metric with no context of its own, its operands'
    contexts. Graph only: the question is read by the rules parser, with no model call and no warehouse query. None
    where the engine carries no query-semantics layer."""
    from kge.services.ask import parse
    if getattr(engine, "semantics", None) is None:
        return []
    units = frozenset(identity.readable_units(engine, who))
    req = parse(question, list(engine.term_names), engine)
    terms = list(dict.fromkeys([t for t in (req.term, *req.terms_found) if t]))
    definitions, names, broader, codes = term_reads(engine)
    words_ = Words(engine, engineer, units)
    out: list[ExplainedPath] = []
    seen: set[tuple[str, str | None]] = set()
    for term in terms:
        found = term_contexts(engine, term, names, units)
        if req.units:
            matched = [(c, part) for c, part in found if c.unit in req.units] or [
                (c, part) for c, part in found if any(s.table.split(".")[0] in req.units for s in c.selectors)
                and any(j.through and {j.source.split(".")[0], j.target.split(".")[0]} & set(req.units) for j in c.joins)]
            found = matched or found
        basis = basis_of(engine, term, question)
        for c, part in found:
            if (c.id, part) in seen:
                continue
            seen.add((c.id, part))
            out.append(explained(engine, words_, c, req, question, definitions, codes, broader, part,
                                 basis if part else None))
    return out


def term_paths(engine, who: str, term: str, engineer: bool) -> list[ExplainedPath]:
    """A term's paths: each context the term itself resolves to in a unit the person may read (for a derived measure or
    a count metric, its operands'), explained as question_paths explains it, every hop available but those always
    joined, with the term's own name as its basis. Unlike a question, it explains no other term the term's words
    contain. The per-term view map_term gives (#628). Graph only; none where the engine has no query-semantics layer."""
    if getattr(engine, "semantics", None) is None:
        return []
    units = frozenset(identity.readable_units(engine, who))
    definitions, names, broader, codes = term_reads(engine)
    words_ = Words(engine, engineer, units)
    return [explained(engine, words_, c, None, "", definitions, codes, broader, part, term if part else None)
            for c, part in term_contexts(engine, term, names, units)]


def paths_summary(paths: list[ExplainedPath]) -> list[str]:
    """A sentence for each of the first NAMED paths, then a word that there are others; each in full in paths."""
    return [*(path_summary(p) for p in paths[:NAMED]), *(["Other paths are in paths."] if len(paths) > NAMED else [])]


def path_summary(path: ExplainedPath) -> str:
    """One plain sentence of a path, in order, no figure."""
    m = path.measure
    said = f"{path.term} ({path.context}{', ' + path.part_of if path.part_of else ''}): {path.fact} rows "
    said += f"{AGGREGATION_WORDS.get(m.aggregation, m.aggregation)} by {m.grain}"
    said += f" in {m.currency}" if m.currency else ""
    used = [h for h in path.hops if h.use in ("always", "question")]
    if used:
        said += "; joined in order " + ", then ".join(f"{h.source} to {h.target} ({h.cardinality}, {h.type})" for h in used)
    for kind, label in (("filter", "filters"), ("exclusion", "exclusions"), ("inclusion", "only where"),
                        ("asked", "as asked")):
        held = [c.label for c in path.conditions if c.kind == kind]
        if held:
            said += f"; {label}: {', '.join(held)}"
    return said + "." + (" " + " ".join(path.warnings) if path.warnings else "")


def members_of(engine, who: str, term: str, engineer: bool) -> list[Level]:
    """A hierarchy term's levels: itself, with every term it sits under (BROADER) and every class it is one of (IS_A);
    its members (IS_A, each one of it); its narrower terms (BROADER, each under it); the terms it sits under; and the
    classes it is one of. Each with the codes that identify it (CODED_AS) on columns of units the person may read; a
    code on another unit's column is left out, the term kept. Empty for a term with no hierarchy, or on a graph loaded
    before these types existed."""
    g = engine.g
    words_ = Words(engine, engineer, frozenset(identity.readable_units(engine, who)))
    edges = held_rows(g, BROADER_READ)
    classes_of = held_rows(g, IS_A_READ)
    narrower = sorted(r["narrower"] for r in edges if r["broader"] == term)
    upward = sorted(r["broader"] for r in edges if r["narrower"] == term)
    members = sorted(r["member"] for r in classes_of if r["of"] == term)
    classes = sorted(r["of"] for r in classes_of if r["member"] == term)
    codes: dict[str, list[dict]] = {}
    for r in held_rows(g, CODED_AS_READ):
        if words_.readable(r["col"].rsplit(".", 1)[0]):
            codes.setdefault(r["term"], []).append(r)
    if not (narrower or upward or members or classes or codes.get(term)):
        return []
    held = ([(term, ", ".join(upward), ", ".join(classes))] + [(m, "", term) for m in members] +
            [(n, term, "") for n in narrower] + [(u, "", "") for u in upward] + [(c, "", "") for c in classes])
    out: list[Level] = []
    for name, broader, of in held:
        for r in sorted(codes.get(name, ()), key=lambda r: (r["col"], r["code"] or "")) or [None]:
            level = Level(name=name, **({"code": r["code"]} if r and r["code"] else {}),
                          **(column_of(words_, r["col"]) if r else {}), **({"broader": broader} if broader else {}),
                          **({"of": of} if of else {}))
            if level not in out:
                out.append(level)
    return out


def join_hop(engine, who: str, join: str, engineer: bool) -> Hop:
    """A declared join's facts, as a path's hop gives them; a join with an end in a unit the person may not read, or
    none of that id, is a LookupError, worded as for a node that does not exist."""
    sem = getattr(engine, "semantics", None)
    j = sem.declared.get(join) if sem is not None else None
    words_ = Words(engine, engineer, frozenset(identity.readable_units(engine, who)))
    if j is None or not (words_.readable(j.source) and words_.readable(j.target)):
        raise LookupError("No such node.")
    return hop_of(words_, j)
