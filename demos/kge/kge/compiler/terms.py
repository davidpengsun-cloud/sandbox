"""Business terms, codes and hierarchy in a pack (meta-model 1.6.0, the owner's addendum on #449): what a glossary term's
is_a, broader and codes say, read as plain records.

  is_a     the glossary ids of the classes a term is a member of
  broader  the glossary ids of the terms it sits under, narrower to broader; the direction gives the order of levels
  codes    {column, code} pairs: how the data identifies the term, column as <source>.<table>.<column> in the lock and
           code as quoted text; several codes, on one column or several, are alternatives (OR)

The readers are pure and tolerate a malformed pack: anything not of the shape S01 accepts (a list of non-blank term
ids; a list of mappings each with a non-blank column and a non-blank text code) is skipped, never raised on, since the
up-front checks read a pack before S01 has passed it. Whether a reference resolves, whether broader terms form a cycle
or give a term two parents in one classification, and whether a code is in its column's rows are later checks' to say
(S37 and F13); the compiler (emit) writes the IS_A, BROADER and CODED_AS edges from these records.

F13 compares a term's BROADER edge with the rows through the two terms' coded columns: on one table the same row, and
across two tables, and for two terms coded on one column (a parent-child table), through the join row_join gives, the
first hierarchy or key join declared between those tables directly (no through table; for one column, from the table to
itself under an alias, the alias naming the broader, parent row), read as the column equalities its condition holds
(equalities). The rest of a condition,
such as a current-version flag, and a validity window are not read, so every row is compared, historical versions too.
"""
from __future__ import annotations

from dataclasses import dataclass

import sqlglot
from sqlglot import exp

TERM_LINKS = ("is_a", "broader")
FIELDS = (*TERM_LINKS, "codes")


@dataclass(frozen=True)
class IsA:
    """A term (member, by glossary id) is one of a class (of, by glossary id), declared at at."""
    member: str
    of: str
    at: str


@dataclass(frozen=True)
class Broader:
    """A term (child, by glossary id) sits under a broader one (parent, by glossary id), declared at at."""
    child: str
    parent: str
    at: str


@dataclass(frozen=True)
class Code:
    """A term (by glossary id, and its name) is identified by code on column, declared at at (the code's place)."""
    term: str
    name: str
    column: str
    code: str
    at: str


def text(value) -> bool:
    """Whether value is what S01 calls a text: a string that is not blank."""
    return isinstance(value, str) and value.strip() != ""


def glossary(data: dict) -> list[tuple[int, dict]]:
    """The glossary's entries that are mappings, with their places."""
    value = data.get("glossary") if isinstance(data, dict) else None
    return [(i, g) for i, g in enumerate(value) if isinstance(g, dict)] if isinstance(value, list) else []


def term_id(g: dict) -> str | None:
    return g["id"] if text(g.get("id")) else None


def refs(g: dict, field: str) -> list[tuple[str, str]]:
    """The term ids a glossary entry's is_a or broader lists, each with its place's suffix (is_a[0], broader[1]). A
    field that is not a list, and an item that is not a text, give nothing."""
    value = g.get(field)
    return [(f"{field}[{k}]", x) for k, x in enumerate(value) if text(x)] if isinstance(value, list) else []


def is_a(data: dict) -> list[IsA]:
    """Every is_a a term declares, in glossary order and then list order."""
    return [IsA(member, ref, f"glossary[{i}].{suffix}") for i, g in glossary(data) if (member := term_id(g))
            for suffix, ref in refs(g, "is_a")]


def broader(data: dict) -> list[Broader]:
    """Every broader a term declares, narrower to broader, in glossary order and then list order."""
    return [Broader(child, ref, f"glossary[{i}].{suffix}") for i, g in glossary(data) if (child := term_id(g))
            for suffix, ref in refs(g, "broader")]


def codes(data: dict) -> list[Code]:
    """Every code a term declares, in glossary order and then list order, the code as declared."""
    out = []
    for i, g in glossary(data):
        term, value = term_id(g), g.get("codes")
        if term is None or not isinstance(value, list):
            continue
        name = g["name"] if text(g.get("name")) else term
        out += [Code(term, name, c["column"], c["code"], f"glossary[{i}].codes[{k}].code") for k, c in enumerate(value)
                if isinstance(c, dict) and text(c.get("column")) and text(c.get("code"))]
    return out


ROW_JOINS = ("hierarchy", "key")


def table_of(column: str) -> str:
    """source.table of a column written source.table.column."""
    return column.rsplit(".", 1)[0]


def equalities(join: dict) -> list[tuple[str, str]]:
    """The column equalities a join's condition holds between its two sides, each (from's column, to's column), both
    written source.table.column as from and to name the tables. A table joined to itself names its to side by the join's
    alias (a parent row of the same table). A conjunct that is not one column equal to another, one on each side, is
    left out; a join through a third table, a join to itself without an alias, or a condition that cannot be read gives
    none."""
    sides = [join.get("from"), join.get("to")]
    condition = join.get("condition")
    alias = join.get("alias") if text(join.get("alias")) else None
    if not all(text(x) and x.count(".") == 1 for x in sides) or join.get("through") or not text(condition) \
            or (sides[0] == sides[1] and alias is None):
        return []
    known: dict[str, int] = {}
    for k, table in enumerate(sides):
        if k == 1 and sides[0] == sides[1]:
            break
        for name in (table, table.split(".", 1)[1]):
            known.setdefault(name.casefold(), k)
    if alias is not None:
        known[alias.casefold()] = 1
    try:
        tree = sqlglot.parse_one(condition, read="duckdb")
    except (sqlglot.errors.ParseError, sqlglot.errors.TokenError, RecursionError):
        return []
    out = []
    try:
        parts = conjuncts(tree)
    except RecursionError:
        return []
    for part in parts:
        if not isinstance(part, exp.EQ) or not all(isinstance(x, exp.Column) for x in (part.left, part.right)):
            continue
        ends = [(known.get(".".join(x for x in (c.db, c.table) if x).casefold()), c.name) for c in (part.left, part.right)]
        if {side for side, _ in ends} != {0, 1}:
            continue
        ends.sort(key=lambda end: end[0])
        out.append((f"{sides[0]}.{ends[0][1]}", f"{sides[1]}.{ends[1][1]}"))
    return list(dict.fromkeys(out))


def conjuncts(node) -> list:
    """The conditions an AND joins, however it nests or is parenthesised."""
    node = node.unnest()
    return [x for side in (node.left, node.right) for x in conjuncts(side)] if isinstance(node, exp.And) else [node]


def row_join(data: dict, low: str, high: str) -> tuple[str, list[tuple[str, str]]] | None:
    """The join F13 compares a narrower term's rows on table low (source.table) with its broader term's on table high
    through: the first hierarchy or key join in joins between the two, either way round, or, where low is high, from
    the table to itself under an alias, that holds a column equality (equalities), as its id and its equalities each
    (low's column, high's column), the narrower row's on the from side. None where there is none."""
    value = data.get("joins") if isinstance(data, dict) else None
    for k, j in enumerate(value if isinstance(value, list) else []):
        if not isinstance(j, dict) or j.get("kind") not in ROW_JOINS or not (text(j.get("from")) and text(j.get("to"))) \
                or {j["from"], j["to"]} != {low, high}:
            continue
        keys = equalities(j)
        if keys:
            name = j["id"] if text(j.get("id")) else f"joins[{k}]"
            return name, keys if j["from"] == low else [(b, a) for a, b in keys]
    return None
