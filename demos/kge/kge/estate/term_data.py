"""F13: a pack's business terms against the rows (meta-model 1.6.0, #449's F3, F4, F7 and F8).

Two things are checked. Every code a term declares (CODED_AS, from its codes) is a value of its column in some row. And
every BROADER edge agrees with the rows, read through the two terms' coded columns: for each column the narrower term is
coded on and each its broader term is coded on, every row whose narrower column holds one of the narrower term's codes
has one of the broader term's codes in the broader column. Several codes a term gives are alternatives (OR), on one
column (F7) and across the columns of one table: where the broader term is coded on several columns of the table the
narrower row reaches, a row holding one of its codes in any of them agrees, so the row is compared once, never once a
column. Each narrower column is compared by itself. On one table, two columns are compared on the same row (F4).
Across two tables, and for two terms coded on the same column (a parent-child table), the rows are compared through
the join terms.row_join gives (for one column, the table joined to itself, its alias the broader row): each narrower row
with every broader row it joins, inner, as the SQL gate joins a declared join, so a narrower row whose join key matches
no row is not compared; one whose join key is NULL has no broader row, and so no broader code. Where both terms are
coded on one column of the table the broader term's columns are on (a member and its parent both coded on a dimension's
key, the member also on a fact column joined to it, or on another column of the dimension), the row reached is the
member's own, so the route goes on through that table's join to itself, and the row compared is the member's parent
row, for every broader column on that table (a NULL key at either step is no broader row). An edge with no such join cannot be compared and
is a finding, never passed over. A NULL broader code means no broader term, so a row with the narrower term's code and a
NULL beside it disagrees with the declared edge. Every row is read, historical versions too (F8).

Values are compared as the column's type reads them (canonical): a number by its value, so 1.50 is 1.5 and 01 is 1, a
boolean as true or false, a date as YYYY-MM-DD and a timestamp as YYYY-MM-DD HH:MM:SS (with its fraction of a second,
and with its zone, as the estate loads one, dropped), each read in every form the estate's loader accepts, and anything
else as its text; the fixture's declared types and the warehouse's own give the
same kinds, so the up-front check and onboarding agree. A code on a column a policy restricts is left to S37 and no row
of it is read, so no restricted value is ever printed.

What is read is planned once (plan) and is the same however the rows are reached: one distinct (narrower value,
broader values) row per narrower column and the broader term's columns that one route reaches (Pair), and one distinct
value per coded column in no same-row pair (Plan.alone),
so a coded column's values come from a same-row pair where it has one. The up-front check reads the fixture's CSVs
(from_csv); onboarding reads the unit's live warehouse with one bounded GROUP BY query for each
(kge/estate/term_warehouse.py). evaluate turns what was read into findings, for the columns that were read alone: a
table that cannot be read, or lacks a column, is F07's and F03's to report.

This module is pure: it opens no warehouse, and reads only the files from_csv is given.
"""
from __future__ import annotations

import csv
import re
from dataclasses import dataclass, field
from decimal import Context, Decimal, DecimalException, localcontext
from pathlib import Path

from kge.compiler import terms
from kge.compiler.pack import Finding

RULE = "F13"
SHOWN = 5
PRECISION = 100
NUMBERS = ("TINYINT", "SMALLINT", "INT2", "INTEGER", "INT", "INT4", "BIGINT", "INT8", "INT64", "HUGEINT", "UTINYINT",
           "USMALLINT", "UINTEGER", "UBIGINT", "DECIMAL", "NUMERIC", "DOUBLE", "FLOAT8", "FLOAT64", "FLOAT", "FLOAT4", "REAL")
BOOLEANS = ("BOOLEAN", "BOOL")
DATES = ("DATE",)
TIMESTAMPS = ("TIMESTAMP", "DATETIME")
DATE_TEXT = re.compile(r"\s*(-?\d{1,6})[-/](\d{1,2})[-/](\d{1,2})(?:[ T].*)?\s*")
TIMESTAMP_TEXT = re.compile(r"\s*(-?\d{1,6})[-/](\d{1,2})[-/](\d{1,2})(?:[ T](\d{1,2}):(\d{2})(?::(\d{2})(?:\.(\d{1,6})\d*)?)?"
                            r"(?:\s*(?:Z|[+-]\d{2}(?::?\d{2})?))?)?\s*")
TRUE, FALSE = {"true", "t", "1", "yes", "y"}, {"false", "f", "0", "no", "n"}


def kind(declared: str | None) -> str:
    """number, boolean, date, timestamp or text: how a column's values compare, from its type as a fixture declares it or the
    warehouse describes it, each name the estate accepts for a type (INT8, FLOAT4, BOOL, ...) read as that type."""
    base = re.sub(r"\s+", " ", (declared or "").strip().upper()).split("(", 1)[0].strip()
    return ("number" if base in NUMBERS else "boolean" if base in BOOLEANS else "date" if base in DATES
            else "timestamp" if base in TIMESTAMPS else "text")


def number(text: str) -> str | None:
    """A number written plainly (1.50 is 1.5, 01 is 1, 2.0 is 2), exactly to 100 significant digits, wider than any
    DECIMAL(38) or HUGEINT; None where text is not a finite number or its magnitude is beyond 10 to the 100th either way,
    which no column here holds."""
    with localcontext(Context(prec=PRECISION, Emax=PRECISION, Emin=-PRECISION)):
        try:
            value = Decimal(text.strip())
            if not value.is_finite() or abs(value.adjusted()) > PRECISION:
                return None
            return format(value.normalize(), "f") if value else "0"
        except DecimalException:
            return None


def canonical(value: str | None, how: str) -> str | None:
    """A value as F13 compares it: a number by its value, written plainly; a boolean as true or false; text as it is. A
    value that is not of its kind is kept as written, so it matches only itself."""
    if value is None or how == "text":
        return value
    if how == "boolean":
        word = value.strip().lower()
        return "true" if word in TRUE else "false" if word in FALSE else value
    if how == "date":
        found = DATE_TEXT.fullmatch(value)
        return f"{int(found[1]):04d}-{int(found[2]):02d}-{int(found[3]):02d}" if found else value
    if how == "timestamp":
        found = TIMESTAMP_TEXT.fullmatch(value)
        if not found:
            return value
        fraction = (found[7] or "").ljust(6, "0").rstrip("0")
        return (f"{int(found[1]):04d}-{int(found[2]):02d}-{int(found[3]):02d} {int(found[4] or 0):02d}:"
                f"{int(found[5] or 0):02d}:{int(found[6] or 0):02d}" + (f".{fraction}" if fraction else ""))
    return number(value) or value


@dataclass(frozen=True)
class Route:
    """How a narrower row reaches its broader row: on the same row (no join); else through join, by keys, each (narrower
    row's column, reached row's column); and where hop is given, on from the reached row to its parent row through the
    reached table's join to itself, by hop_keys, each (reached row's column, parent row's column)."""
    join: str | None = None
    keys: tuple[tuple[str, str], ...] = ()
    hop: str | None = None
    hop_keys: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class Pair:
    """A narrower term's coded column (child) and the broader term's coded columns on the one table the route reaches
    (parents), each source.table.column, the parents alternatives; route, how a child row reaches the parent row."""
    child: str
    parents: tuple[str, ...]
    route: Route = Route()

    @property
    def unit(self) -> str:
        return self.child.split(".", 1)[0]

    @property
    def keys(self) -> tuple[tuple[str, str], ...]:
        return self.route.keys

    @property
    def join(self) -> str | None:
        return self.route.join


@dataclass(frozen=True)
class Edge:
    """One BROADER declaration (child under parent, declared at at) compared on one Pair: child_codes, the narrower
    term's codes on its column; parent_codes, the broader term's on each of the pair's parents, in their order."""
    child: str
    parent: str
    at: str
    pair: Pair
    child_codes: frozenset[str]
    parent_codes: tuple[frozenset[str], ...]


@dataclass(frozen=True)
class Unlinked:
    """A BROADER declaration whose coded columns low and high no join lets F13 compare; own, where the row reached is
    the narrower term's own (both terms coded on a column of high's table) and that table has no join to itself to its
    parent row."""
    child: str
    parent: str
    at: str
    low: str
    high: str
    own: bool = False


@dataclass
class Plan:
    codes: list[terms.Code]
    pairs: list[Pair]
    edges: list[Edge]
    alone: list[str]
    wanted: dict[str, frozenset[str]]
    unlinked: list[Unlinked] = field(default_factory=list)

    @property
    def units(self) -> list[str]:
        return list(dict.fromkeys(c.column.split(".", 1)[0] for c in self.codes))

    def of(self, unit: str) -> tuple[list[Pair], list[str]]:
        """The pairs and the single columns read from unit."""
        return [p for p in self.pairs if p.unit == unit], [c for c in self.alone if c.split(".", 1)[0] == unit]


@dataclass
class Rows:
    """What was read: values, each column's distinct canonical values among its declared codes; pairs, each Pair's
    distinct (child value, its parents' values, each or None) where the child value is a declared code of the child
    column; read,
    the columns read; kinds, each column's kind."""
    values: dict[str, set[str]] = field(default_factory=dict)
    pairs: dict[Pair, set[tuple[str, tuple[str | None, ...]]]] = field(default_factory=dict)
    read: set[str] = field(default_factory=set)
    kinds: dict[str, str] = field(default_factory=dict)

    def wanted(self, the_plan: Plan, column: str) -> set[str]:
        """The column's declared codes as its kind compares them."""
        how = self.kinds.get(column, "text")
        return {canonical(c, how) for c in the_plan.wanted.get(column, ())}


def coded(column: str) -> bool:
    """Whether a column is written source.table.column."""
    return isinstance(column, str) and column.count(".") == 2 and all(column.split("."))


def restricted(data: dict) -> set[str]:
    """The columns policies.restricted_attributes names, as written; malformed entries give nothing."""
    policies = data.get("policies") if isinstance(data, dict) else None
    entries = policies.get("restricted_attributes") if isinstance(policies, dict) else None
    out = set()
    for entry in entries if isinstance(entries, list) else []:
        columns = entry.get("columns") if isinstance(entry, dict) else None
        if isinstance(columns, list):
            out.update(c for c in columns if isinstance(c, str))
    return out


def plan(data: dict) -> Plan:
    """What F13 reads for a pack's terms. A code on a column not written source.table.column, or on a restricted column,
    is S01's and S37's and is not read; an edge between terms either of which has no code reads nothing."""
    blocked = restricted(data)
    codes = [c for c in terms.codes(data) if coded(c.column) and c.column not in blocked]
    held: dict[str, dict[str, set[str]]] = {}
    for c in codes:
        held.setdefault(c.term, {}).setdefault(c.column, set()).add(c.code)
    pairs: dict[Pair, None] = {}
    edges, unlinked = [], []
    for b in terms.broader(data):
        own = held.get(b.child, {})
        shared = {terms.table_of(c) for c in own if c in held.get(b.parent, {})}
        for low, mine in own.items():
            reached: dict[tuple[str, Route], list[str]] = {}
            for high in held.get(b.parent, {}):
                sharing = terms.table_of(high) in shared
                route = link(data, low, high, sharing)
                if route is None:
                    unlinked.append(Unlinked(b.child, b.parent, b.at, low, high,
                                             sharing and low != high and link(data, low, high, False) is not None))
                    continue
                reached.setdefault((terms.table_of(high), route), []).append(high)
            for (_, route), highs in reached.items():
                pair = Pair(low, tuple(highs), route)
                pairs.setdefault(pair)
                edges.append(Edge(b.child, b.parent, b.at, pair, frozenset(mine),
                                  tuple(frozenset(held[b.parent][h]) for h in highs)))
    same = {c for p in pairs if not p.keys for c in (p.child, *p.parents)}
    wanted: dict[str, set[str]] = {}
    for c in codes:
        wanted.setdefault(c.column, set()).add(c.code)
    alone = [c for c in wanted if c not in same]
    return Plan(codes, list(pairs), edges, alone, {c: frozenset(v) for c, v in wanted.items()}, unlinked)


def link(data: dict, low: str, high: str, own: bool) -> Route | None:
    """How a row holding the narrower term's code on low reaches the row whose high the broader term's code should be
    in: two columns of one table on the same row; otherwise, across two tables of one source or on one column, through
    the join terms.row_join gives. Where own (both terms are coded on one column of high's table), the row reached is
    the narrower term's own: on one table the route is that table's join to itself to the parent row, and across two
    tables it goes on through that join after the join between them. None where there is no such route."""
    a, b = terms.table_of(low), terms.table_of(high)
    if a.split(".", 1)[0] != b.split(".", 1)[0]:
        return None
    if a == b and low != high and not own:
        return Route()
    found = terms.row_join(data, a, b)
    if found is None:
        return None
    name, keys = found
    route = Route(name, columns(keys))
    if not own or a == b:
        return route
    up = terms.row_join(data, b, b)
    return None if up is None else Route(name, route.keys, up[0], columns(up[1]))


def columns(keys) -> tuple[tuple[str, str], ...]:
    """A join's equalities (terms.row_join) as the column names alone."""
    return tuple((x.rsplit(".", 1)[1], y.rsplit(".", 1)[1]) for x, y in keys)


def table_rows(path: Path) -> tuple[dict[str, int], list[list[str | None]]] | None:
    """A fixture CSV's header (each column's name, case folded, to its place) and rows, an empty field None, as the
    estate loads it (kge/estate/from_files.py load_rows); None where the file cannot be read, which F07 reports."""
    try:
        with path.open(newline="") as stream:
            rows = list(csv.reader(stream, strict=True))
    except (OSError, UnicodeDecodeError, csv.Error):
        return None
    if not rows:
        return None
    header = {name.casefold(): k for k, name in enumerate(rows[0])}
    return header, [[v if v != "" else None for v in r] for r in rows[1:] if len(r) == len(rows[0])]


def types_of(house: dict) -> dict[str, dict[str, str]]:
    """A fixture warehouse's declared types: by table, each column's name, case folded, to its type."""
    tables = house.get("tables") if isinstance(house, dict) else None
    out: dict[str, dict[str, str]] = {}
    for t in tables if isinstance(tables, list) else []:
        if isinstance(t, dict) and isinstance(t.get("name"), str) and isinstance(t.get("columns"), list):
            out[t["name"]] = {c["name"].casefold(): str(c.get("type")) for c in t["columns"]
                              if isinstance(c, dict) and isinstance(c.get("name"), str)}
    return out


def from_csv(the_plan: Plan, unit: str, folder: Path, types: dict[str, dict[str, str]], rows: Rows) -> None:
    """Read unit's part of the plan from its fixture CSVs (folder/<table>.csv), each value as its declared type (types,
    types_of) compares it, into rows. Only a table the fixture's schema declares (a key of types) is read. A table that
    cannot be read, or that lacks a column the plan reads, is left out, and so is what it would have shown: F07 and F03
    report it."""
    tables: dict[str, tuple[dict[str, int], list] | None] = {}

    def table(name: str):
        if name not in tables:
            declared = {t.casefold(): t for t in types}.get(name.casefold())
            tables[name] = table_rows(folder / f"{declared}.csv") if declared else None
        return tables[name]

    def column(name: str, col: str):
        """(place, kind) of a column, or None where its table cannot be read or lacks it."""
        got = table(name)
        k = got[0].get(col.casefold()) if got else None
        declared = {t.casefold(): c for t, c in types.items()}.get(name.casefold(), {})
        return None if k is None else (k, kind(declared.get(col.casefold())))

    pairs, alone = the_plan.of(unit)
    for qualified in alone:
        _, name, col = qualified.split(".")
        found = column(name, col)
        if found is None:
            continue
        k, how = found
        rows.kinds[qualified] = how
        want = rows.wanted(the_plan, qualified)
        rows.values.setdefault(qualified, set()).update(v for r in table(name)[1] if (v := canonical(r[k], how)) in want)
        rows.read.add(qualified)
    for pair in pairs:
        _, low, x = pair.child.split(".")
        high = pair.parents[0].split(".")[1]
        route = pair.route
        cx = column(low, x)
        ps = [column(high, c.split(".")[2]) for c in pair.parents]
        ck = [column(low, a) for a, _ in route.keys]
        mk = [column(high, b) for _, b in route.keys]
        hk = [column(high, a) for a, _ in route.hop_keys]
        pk = [column(high, b) for _, b in route.hop_keys]
        if cx is None or None in ps or None in ck or None in mk or None in hk or None in pk:
            continue
        rows.kinds[pair.child] = cx[1]
        rows.kinds.update((c, found[1]) for c, found in zip(pair.parents, ps))
        mine = rows.wanted(the_plan, pair.child)
        theirs = [rows.wanted(the_plan, c) for c in pair.parents]
        seen = rows.pairs.setdefault(pair, set())

        def values(r, places=ps):
            return tuple(canonical(r[k], how) for k, how in places)
        none = (None,) * len(pair.parents)
        if not route.keys:
            for r in table(low)[1]:
                child, parents = canonical(r[cx[0]], cx[1]), values(r)
                if child in mine:
                    seen.add((child, parents))
                    rows.values.setdefault(pair.child, set()).add(child)
                for c, value, want in zip(pair.parents, parents, theirs):
                    if value in want:
                        rows.values.setdefault(c, set()).add(value)
            rows.read.update((pair.child, *pair.parents))
            continue
        top: dict[tuple, list] = {}
        if route.hop_keys:
            for r in table(high)[1]:
                key = values(r, pk)
                if None not in key:
                    top.setdefault(key, []).append(values(r))
        index: dict[tuple, list] = {}
        for r in table(high)[1]:
            key = values(r, mk)
            if None in key:
                continue
            if not route.hop_keys:
                index.setdefault(key, []).append(values(r))
                continue
            up = values(r, hk)
            index.setdefault(key, []).extend([none] if None in up else top.get(up, []))
        for r in table(low)[1]:
            child = canonical(r[cx[0]], cx[1])
            if child not in mine:
                continue
            key = values(r, ck)
            for parents in index.get(key, []) if None not in key else [none]:
                seen.add((child, parents))


def listed(values) -> str:
    shown = sorted(values, key=lambda v: (v is None, v or ""))
    words = ["NULL" if v is None else v for v in shown[:SHOWN]]
    return ", ".join(words) + (f" and {len(shown) - SHOWN} more" if len(shown) > SHOWN else "")


def evaluate(the_plan: Plan, rows: Rows, at) -> list[Finding]:
    """The findings for what was read, for the columns rows read and the edges no join lets F13 compare; at turns a pack
    place into a finding's path."""
    out = []
    for c in the_plan.codes:
        if c.column in rows.read and canonical(c.code, rows.kinds.get(c.column, "text")) not in rows.values.get(c.column, ()):
            out.append(Finding(RULE, at(c.at), f"{c.column} holds no row with {c.code}: {c.term}'s code is a value of "
                                               "its column"))
    for u in the_plan.unlinked:
        if u.own:
            out.append(Finding(RULE, at(u.at), f"{u.child} is under {u.parent}, coded on {u.low} and {u.high}, and both "
                                               f"are coded on a column of {terms.table_of(u.high)}, so the row reached is "
                                               f"{u.child}'s own, and no hierarchy or key join from "
                                               f"{terms.table_of(u.high)} to itself, under an alias, leads on to its "
                                               "parent row: the rows cannot be compared"))
            continue
        out.append(Finding(RULE, at(u.at), f"{u.child} is under {u.parent}, coded on {u.low} and {u.high}, and no "
                                           "hierarchy or key join declared between those tables (from a table to itself, "
                                           "under an alias) equates their columns: the rows cannot be compared"))
    for e in the_plan.edges:
        if e.pair not in rows.pairs:
            continue
        mine = {canonical(c, rows.kinds.get(e.pair.child, "text")) for c in e.child_codes}
        theirs = [{canonical(c, rows.kinds.get(col, "text")) for c in codes}
                  for col, codes in zip(e.pair.parents, e.parent_codes)]
        wrong = {ps for x, ps in rows.pairs[e.pair] if x in mine and not any(v in t for v, t in zip(ps, theirs))}
        if not wrong:
            continue
        route = e.pair.route
        through = (" on the same row" if route.join is None else f" through {route.join}"
                   + (f" and {route.hop}" if route.hop else ""))
        null = " (a NULL is no broader term)" if any(all(v is None for v in ps) for ps in wrong) else ""
        if len(e.pair.parents) == 1:
            held, shown = e.pair.parents[0], listed(ps[0] for ps in wrong)
            expected = listed(e.parent_codes[0])
        else:
            held = " / ".join(e.pair.parents)
            shown = listed(" / ".join("NULL" if v is None else v for v in ps) for ps in wrong)
            expected = " or ".join(f"{col} {listed(codes)}" for col, codes in zip(e.pair.parents, e.parent_codes))
        out.append(Finding(RULE, at(e.at), f"{e.child} is under {e.parent}, but rows with {e.pair.child} "
                                           f"{listed(e.child_codes)} have {held} {shown}{null}{through}, "
                                           f"not {expected}: a broader term agrees with the rows"))
    return out
