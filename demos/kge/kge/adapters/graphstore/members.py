"""Release vocabulary for coded business members and their classification hierarchy."""
from __future__ import annotations

from dataclasses import dataclass

from kge.adapters.graphstore import missing_type

CODED = 'MATCH (t:Term)-[c:CODED_AS]->(x:Col) RETURN t.name AS term, x.id AS col, c.code AS code'
BROADER = 'MATCH (t:Term)-[:BROADER]->(p:Term) RETURN t.name AS member, p.name AS parent'
CLASSES = 'MATCH (t:Term)-[:IS_A]->(c:Term) RETURN t.name AS member, c.name AS parent'
ALIASES = 'MATCH (a:Alias)-[:ALIAS_OF]->(t:Term) RETURN a.phrase AS phrase, a.ordinal AS n, t.name AS name'


def related(links, names):
    """Follow the links from their members, adding each parent once."""
    seen = set(names)
    pending = list(seen)
    while pending:
        for parent in links.get(pending.pop(), ()):
            if parent not in seen:
                seen.add(parent)
                pending.append(parent)
    return seen


@dataclass(frozen=True)
class MemberTerms:
    release: object
    names: frozenset[str] = frozenset()
    codes: tuple[tuple[str, str, str], ...] = ()
    aliases: tuple[tuple[str, str], ...] = ()
    attached: tuple[tuple[str, str], ...] = ()

    def columns(self, term):
        """Columns of a coded member, a group whose codes come from its children, or its class."""
        return tuple(sorted({column for name, column in self.attached if term == name}))


def members(read, release=None) -> MemberTerms:
    """Keep structured names apart from ordinary terms. Only proven codes give a visible binding."""
    def read_optional(query):
        try:
            return read(query)
        except Exception as exc:
            if not missing_type(exc):
                raise
            return ()

    coded = tuple(sorted((r['term'], r['col'], r['code']) for r in read_optional(CODED)))
    direct = {name for name, _, _ in coded}
    broader = {}
    for row in read_optional(BROADER):
        broader.setdefault(row['member'], []).append(row['parent'])
    classes = {}
    for row in read_optional(CLASSES):
        classes.setdefault(row['member'], []).append(row['parent'])
    names = related(classes, related(broader, direct | broader.keys()) | classes.keys())
    aliases = tuple(sorted({(r['phrase'], r['name']) for r in read_optional(ALIASES) if r['name'] in names})) if names else ()

    effective, attached = set(coded), set()
    for name, column, code in coded:
        groups = related(broader, (name,)) - direct
        attached.update((term, column) for term in related(classes, {name, *groups}))
        effective.update((group, column, code) for group in groups)
    return MemberTerms(release, frozenset(names), tuple(sorted(effective)), aliases, tuple(sorted(attached)))
