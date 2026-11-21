"""F13 at onboarding: a pack's business terms against each unit's live warehouse version, after S37 and before load.

What is read is term_data's plan, reached through the estate's warehouse seam (EstateWarehouses) and the SQL gate: one
GROUP BY child, parents query for each narrower column and the broader columns one route reaches (term_data.Pair; through
a join, with a UNION ALL of the child rows whose join key is NULL, and, where the route goes on through the reached
table's join to itself, of those whose reached row's parent key is NULL), and one GROUP BY column query for each coded column in no same-row pair (Plan.alone), each
bounded to rows holding a declared code, beside the warehouse's description of each coded table, so the number of
queries is the plan's and never the rows'. A column's kind (term_data.kind) is read from the warehouse's own description of its table:
a code is compared with a number, boolean, date or timestamp column as a literal of that type, and with any
other as text, and every
value read back is put in the same canonical form the up-front check gives the fixture's, so both agree. Each unit is
read at the version warehouse_versions.live_versions names for it (none: its one warehouse). A query the gate refuses,
or a warehouse that cannot be read, is a finding: F13 fails closed. A pack that declares no code sends no query.
"""
from __future__ import annotations

from kge.compiler.pack import Finding, Pack
from kge.estate import term_data
from kge.estate.term_data import canonical
from kge.pipeline import sqlguard
from kge.ports import AUTHORED, EstateWarehouses, WarehouseRefused


def name(text: str) -> str:
    return '"' + text.replace('"', '""') + '"'


def literal(code: str, how: str) -> str | None:
    """A code as a literal of its column's kind; None where it is not one, and so matches no row."""
    if how == "number":
        return term_data.number(code)
    if how == "boolean":
        value = canonical(code, how)
        return value.upper() if value in ("true", "false") else None
    if how in ("date", "timestamp"):
        form = term_data.DATE_TEXT if how == "date" else term_data.TIMESTAMP_TEXT
        return f"{how.upper()} '{canonical(code, how)}'" if form.fullmatch(code) else None
    return "'" + code.replace("'", "''") + "'"


def among(column: str, codes, how: str) -> str | None:
    """The condition that a column holds one of the codes, compared as its kind; None where no code can match."""
    found = sorted({x for c in codes if (x := literal(c, how)) is not None})
    if not found:
        return None
    held = f"CAST({column} AS VARCHAR)" if how == "text" else column
    return f"{held} IN ({', '.join(found)})"


def as_text(column: str) -> str:
    return f"CAST({column} AS VARCHAR)"


def pair_sql(pair: term_data.Pair, wanted: dict[str, frozenset[str]], kinds: dict[str, str]) -> str | None:
    """One GROUP BY child, parents over the pair's rows: on one row, rows holding a declared code in any of its columns;
    through a join, each child row holding one joined, inner, as the SQL gate joins a declared join, to the parent rows
    by the join's equalities (a table joined to itself is read twice, as c and p), and, in the same query, each such child
    row whose join key is NULL, with NULL parents: it has no broader row. Where the route goes on (Route.hop), the child
    row is joined to its own row in the broader table (m) and that row to its parent row (p), and a reached row whose
    parent key is NULL gives NULL parents too. None where no code can match."""
    _, low, x = pair.child.split(".")
    high = pair.parents[0].split(".")[1]
    ys = [c.split(".")[2] for c in pair.parents]
    route = pair.route
    if not route.keys:
        tests = [among(f"{name(low)}.{name(col)}", wanted[q], kinds[q])
                 for col, q in ((x, pair.child), *zip(ys, pair.parents))]
        tests = [t for t in tests if t]
        if not tests:
            return None
        held = ", ".join(as_text(f"{name(low)}.{name(col)}") for col in (x, *ys))
        return f"SELECT {held} FROM {name(low)} WHERE {' OR '.join(tests)} GROUP BY {held}"
    test = among(f"c.{name(x)}", wanted[pair.child], kinds[pair.child])
    if test is None:
        return None
    child = as_text(f"c.{name(x)}")
    parents = ", ".join(as_text(f"p.{name(y)}") for y in ys)
    nulls = ", ".join("CAST(NULL AS VARCHAR)" for _ in ys)
    reached = "p" if route.hop is None else "m"
    on = " AND ".join(f"c.{name(a)} = {reached}.{name(b)}" for a, b in route.keys)
    null = " OR ".join(f"c.{name(a)} IS NULL" for a, _ in route.keys)
    if route.hop is None:
        return (f"SELECT {child}, {parents} FROM {name(low)} AS c JOIN {name(high)} AS p ON {on} "
                f"WHERE {test} GROUP BY {child}, {parents} UNION ALL "
                f"SELECT {child}, {nulls} FROM {name(low)} AS c WHERE {test} AND ({null}) GROUP BY {child}")
    up = " AND ".join(f"m.{name(a)} = p.{name(b)}" for a, b in route.hop_keys)
    top = " OR ".join(f"m.{name(a)} IS NULL" for a, _ in route.hop_keys)
    return (f"SELECT {child}, {parents} FROM {name(low)} AS c JOIN {name(high)} AS m ON {on} "
            f"JOIN {name(high)} AS p ON {up} WHERE {test} GROUP BY {child}, {parents} UNION ALL "
            f"SELECT {child}, {nulls} FROM {name(low)} AS c WHERE {test} AND ({null}) GROUP BY {child} UNION ALL "
            f"SELECT {child}, {nulls} FROM {name(low)} AS c JOIN {name(high)} AS m ON {on} "
            f"WHERE {test} AND ({top}) GROUP BY {child}")


def column_sql(column: str, wanted: dict[str, frozenset[str]], kinds: dict[str, str]) -> str | None:
    """One GROUP BY column over the rows holding a declared code in it; None where no code can match."""
    _, table, col = column.split(".")
    test = among(f"{name(table)}.{name(col)}", wanted[column], kinds[column])
    if test is None:
        return None
    value = as_text(f"{name(table)}.{name(col)}")
    return f"SELECT {value} FROM {name(table)} WHERE {test} GROUP BY {value}"


def allowlist(unit: str, pairs: list[term_data.Pair], alone: list[str]) -> sqlguard.Allowlist:
    """What the unit's F13 queries may read: the coded columns and the join keys, and those joins."""
    columns: dict[str, set[str]] = {}
    joins = set()
    for column in [c for p in pairs for c in (p.child, *p.parents)] + alone:
        _, table, col = column.split(".")
        columns.setdefault(table, set()).add(col)
    for p in pairs:
        low, high = p.child.split(".")[1], p.parents[0].split(".")[1]
        for start, end, keys in ((low, high, p.route.keys), (high, high, p.route.hop_keys)):
            for a, b in keys:
                columns.setdefault(start, set()).add(a)
                columns.setdefault(end, set()).add(b)
            if keys:
                joins.add(frozenset({(start, a) for a, _ in keys} | {(end, b) for _, b in keys}))
                joins.update(frozenset({(start, a), (end, b)}) for a, b in keys)
    return sqlguard.Allowlist(unit, set(columns), columns, joins, set(), {t: set(c) for t, c in columns.items()})


def kinds_of(session, columns: list[str]) -> dict[str, str]:
    """Each column's kind, from the warehouse's description of its table (WarehouseRefused where it cannot be read)."""
    described: dict[str, dict[str, str]] = {}
    held = {t.casefold(): t for t in session.tables()}
    out = {}
    for column in columns:
        _, table, col = column.split(".")
        if table.casefold() not in held:
            raise WarehouseRefused(f"it holds no table {table}")
        if table not in described:
            described[table] = {c["name"].casefold(): c["type"]
                                for c in session.describe(held[table.casefold()])["columns"]}
        out[column] = term_data.kind(described[table].get(col.casefold()))
    return out


def queries(the_plan: term_data.Plan, unit: str, kinds: dict[str, str]) -> list[tuple[str | None, object]]:
    """The unit's queries in the order they run, each with what it reads (a Pair, or a column); None for one with
    nothing to match."""
    pairs, alone = the_plan.of(unit)
    return ([(pair_sql(p, the_plan.wanted, kinds), p) for p in pairs]
            + [(column_sql(c, the_plan.wanted, kinds), c) for c in alone])


def read_unit(estate: EstateWarehouses, the_plan: term_data.Plan, unit: str, version: int | None,
              rows: term_data.Rows) -> list[str]:
    """Run the unit's queries, through the gate, into rows; the reasons any could not run (empty when all did)."""
    pairs, alone = the_plan.of(unit)
    allow = allowlist(unit, pairs, alone)
    try:
        session = estate.open(unit, version)
    except (WarehouseRefused, OSError, RuntimeError, ValueError) as exc:
        return [f"its warehouse{'' if version is None else f' version {version}'} cannot be opened: {exc}"]
    problems = []
    try:
        try:
            kinds = kinds_of(session, [c for p in pairs for c in (p.child, *p.parents)] + alone)
        except Exception as exc:
            return [f"its tables cannot be described: {str(exc).splitlines()[0][:160] if str(exc) else type(exc).__name__}"]
        rows.kinds.update(kinds)
        for sql, what in queries(the_plan, unit, kinds):
            got = []
            if sql is not None:
                checked = sqlguard.gate(session, sql, allow, dialect=AUTHORED)
                if not checked["passed"]:
                    problems.append(f"the SQL gate refused its query: {'; '.join(checked['problems'])}")
                    continue
                try:
                    got = session.execute(sql).fetchall()
                except (WarehouseRefused, RuntimeError, ValueError) as exc:
                    problems.append(f"its query did not run: {str(exc).splitlines()[0][:160]}")
                    continue
            if isinstance(what, term_data.Pair):
                read = [(canonical(r[0], kinds[what.child]),
                         tuple(canonical(v, kinds[c]) for v, c in zip(r[1:], what.parents))) for r in got]
                mine = rows.wanted(the_plan, what.child)
                rows.pairs.setdefault(what, set()).update((x, ps) for x, ps in read if x in mine)
                rows.values.setdefault(what.child, set()).update(x for x, _ in read if x in mine)
                if not what.keys:
                    for k, c in enumerate(what.parents):
                        theirs = rows.wanted(the_plan, c)
                        rows.values.setdefault(c, set()).update(ps[k] for _, ps in read if ps[k] in theirs)
                    rows.read.update((what.child, *what.parents))
            else:
                rows.values.setdefault(what, set()).update(canonical(r[0], kinds[what]) for r in got)
                rows.read.add(what)
    finally:
        session.close()
    return problems


def check(pack: Pack, estate: EstateWarehouses, versions: dict[str, int]) -> list[Finding]:
    """F13 over the live warehouses: every finding, a unit that could not be read among them. versions: each unit's live
    version (warehouse_versions.live_versions)."""
    the_plan = term_data.plan(pack.data)
    if not the_plan.codes:
        return []
    rows, out = term_data.Rows(), []
    for unit in the_plan.units:
        for problem in read_unit(estate, the_plan, unit, versions.get(unit), rows):
            out.append(Finding(term_data.RULE, f"{unit}:rows", f"{unit}'s declared codes and broader terms are not "
                                                               f"checked: {problem}"))
    return out + term_data.evaluate(the_plan, rows, pack.at)
