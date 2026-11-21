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

from kge.services import identity
from kge.services.graph.models import Explore, GraphPage, Link, Node

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


def explore(engine, who: str, node: str | None = None, question: str | None = None) -> Explore:
    """A short summary of a node's, or a question's, neighbourhood the person may read, its neighbours named by the
    status of the link to them, and a deep link that opens the graph on the node, or on the node the question matches
    best. A question's neighbourhood is that of the nodes its words name (seeds); with none, the summary says so and
    the link opens the graph on no node. The question is neither kept nor echoed. No count and no figure. A node the
    person may not see is a LookupError, as one that does not exist is."""
    if (node is None) == (question is None):
        raise ValueError("Give a node or a question, not both.")
    view = visible(engine, who)
    if question is not None:
        found = seeds(view, question)
        if not found:
            return Explore(summary=UNMATCHED, link=DEEP_LINK.split("&", 1)[0])
        named = ", ".join(f"{view.nodes[i].name} ({view.nodes[i].kind})" for i in found)
        near = [neighbourhood(view, i) for i in found]
        links = {(link.source, link.kind, link.target): link for n in near for link in n.links}
        whole = Visible(nodes={k: v for n in near for k, v in n.nodes.items()}, links=tuple(links[k] for k in sorted(links)))
        parts = [f"The question names {named}.", *summarise(whole, found)]
        return Explore(node=view.nodes[found[0]], summary=" ".join(parts), link=DEEP_LINK.format(quote(found[0], safe="")))
    near = neighbourhood(view, node)
    centre = near.nodes[node]
    parts = [f"{centre.name} is a {centre.kind}.", *summarise(near, [node])]
    if len(parts) == 1:
        parts.append("It has no links you may read.")
    return Explore(node=centre, summary=" ".join(parts), link=DEEP_LINK.format(quote(node, safe="")))
