"""The gate every warehouse query passes before it runs, planned or generated.

A query is allowed only if all of these hold:

  one statement, and it reads: SELECT or WITH ... SELECT, nothing that writes
  every table is in the unit's warehouse; no CTE or subquery borrows a table's name,
    and nothing renames columns by position (customer c(a, b, ...))
  every column resolves, scope by scope, to a column of one of those tables, and
    that column is on the allowlist: the certified columns of the question's terms
    and filters, the declared join keys and the period columns (or, for an arm
    with no certified mappings, the warehouse schema); never anything else
  no column is restricted personal data, whatever the allowlist says; nothing
    reads columns it does not name (SELECT *, COLUMNS(...), #n, a whole row)
  two tables meet only on a declared join (JOINS_TABLE), written as one column
    equal to another; a table no declared join connects is refused
  the warehouse accepts it: EXPLAIN plans it without running it (the dry run)

For the reference agent's model SQL (figure=True), one more rule: the query's
one value is a figure, an aggregate (count, sum, avg, min or max, inside round
or not) of one column of a warehouse table that the allowlist gives as a
measure (or of that column signed, CASE WHEN ... THEN -c ELSE c END, as a
measure of a magnitude and its direction is), or count(*) of the rows a WHERE
on such a column selects. A constant,
arithmetic on an aggregate, an aggregate of an expression or of a CTE's column,
a round to anything but a whole number of decimal places (round(x, -3) reports
13,287.67 as 13,000) and GROUP BY are refused: a figure must come from the data,
and name the column it measures. The measures are the certified columns of the question's terms and
filters, or, for an arm with no certified mappings, every column the schema
admits. The rows it adds up are the warehouse's own: one SELECT over its tables,
with no CTE, subquery, VALUES, UNNEST or LATERAL (a source that is not a table
multiplies the rows, and a subquery or CTE narrows them on a key no declared
join links, which the join rule sees only between two tables), and no sample,
random() or uuid() (a different figure on every run). The planner's own SQL,
which is not gated as a figure, may use them.

A context's query (kge/semantics/query.py) is gated on the context's own allowlist,
built from what its pack declared: the columns its measure, filters, row
selectors, declared joins, time policies and period read. It adds four things
to the rules above, and takes none away:

  a declared condition's predicate that is not one column equal to another
    (last_day(a.d) = b.e, a date between two others, a version valid on a
    freeze date) is allowed as written there; it joins the tables it names
    only where the scope applies the whole of the declared join or row
    selector it comes from (last_day(a.d) = b.e is a whole join; a date range
    joins with the keys beside it). A version's window joins nothing: it is a
    condition on a table its keys reach already
  a declared join that carries conditions of its own (a certified
    cross-reference: the certification filter, and the window of the table it
    joins through, current or valid on a date) may be used only with them, in
    the scope that uses it, on the alias it joins through; a query that leaves
    one out, or puts it on another alias of the same table, is refused. The
    window is read by its shape, so one with anything ORed into it is refused
  tables meet by inner joins only: an outer, semi, anti, as-of, positional or
    natural join keeps rows its condition does not match, or keeps only those,
    so a declared join's conditions in its ON would select nothing, or select
    exactly the rows they exist to leave out
  in a session of two units (Warehouses.attached), every table is named with
    its unit, <unit>.<table>, and only the units the session attached and the
    curated table the certified join runs through are there

For a figure, an EXISTS or NOT EXISTS whose every condition is one the context
declared (a row selector, or the semi-join through a certified cross-reference),
or another such EXISTS (a row selector on the far side of the certified join,
inside its semi-join), does not change the rows the figure adds up beyond what
the context says, so it is the one subquery a figure may hold. Restricted columns stay refused in
every query, and so can never be a join key.

The planner's SQL goes through the same gate as the model's. The gate does not
trust where a query came from; it checks what the query does, after resolving
every name the way the warehouse would.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import sqlglot
from sqlglot import exp
from sqlglot.optimizer.normalize_identifiers import normalize_identifiers
from sqlglot.optimizer.qualify import qualify
from sqlglot.optimizer.scope import traverse_scope
from sqlglot.dialects.dialect import Dialect
from sqlglot.schema import MappingSchema
from sqlglot.tokens import TokenType

from kge.adapters.deadline import RequestDeadlineExceeded
from kge.ports import AUTHORED

WRITES = (exp.Insert, exp.Update, exp.Delete, exp.Merge, exp.Create, exp.Drop, exp.Alter, exp.Command,
          exp.TruncateTable, exp.Copy, exp.Pragma, exp.Set, exp.Use)
UNNAMED = (exp.Columns, exp.PositionalColumn)
AGGREGATES = (exp.Count, exp.Sum, exp.Avg, exp.Min, exp.Max)
RANDOM = (exp.Rand, exp.Randn, exp.Uuid, exp.TableSample)
PERIOD_COLUMNS = {"month_start", "as_of", "fiscal_quarter", "calendar_quarter", "calendar_year", "fiscal_year"}


@dataclass
class Allowlist:
    """What a query on unit may touch: columns, per table, the columns it may read; restricted, the (table, column)
    pairs refused in every arm, with what the policy restricting each cites (cites); schema, every column of each table,
    to resolve names; measures, the (table, column) pairs a figure may aggregate or count by. The rest is a context's
    (kge/semantics/query.py) and empty for any other allowlist: attached, a session of two units whose tables are named
    <unit>.<table>; predicates, the canonical predicates that join tables other than as one column equal to another;
    required, by declared join, what a query that uses it must carry; declared, every declared condition, which is what
    an EXISTS in a figure may test; and links, each declared condition that holds one of predicates, as its canonical
    conjuncts, since such a predicate joins tables only beside the rest of its condition."""
    unit: str
    tables: set[str]
    columns: dict[str, set[str]]
    joins: set[frozenset] = field(default_factory=set)
    restricted: set[tuple[str, str]] = field(default_factory=set)
    schema: dict[str, set[str]] = field(default_factory=dict)
    measures: set[tuple[str, str]] = field(default_factory=set)
    attached: bool = False
    predicates: set[str] = field(default_factory=set)
    required: dict[str, dict] = field(default_factory=dict)
    declared: set[str] = field(default_factory=set)
    links: set[frozenset] = field(default_factory=set)
    cites: dict[tuple[str, str], str] = field(default_factory=dict)

    @property
    def declares(self) -> bool:
        """A context's allowlist (kge/semantics/query.py): it holds what a pack declared."""
        return bool(self.attached or self.predicates or self.required or self.declared)

    def as_prompt(self) -> str:
        cols = "\n".join(f"  {t}: {', '.join(sorted(c))}" for t, c in sorted(self.columns.items()) if c)
        js = "\n".join(f"  {a[0]}.{a[1]} = {b[0]}.{b[1]}" for a, b in sorted(tuple(sorted(j)) for j in self.joins))
        return f"Columns you may use (table: columns):\n{cols}\nThe only joins that exist:\n{js}"


@dataclass
class DataAllowlist(Allowlist):
    """A person's data-read policy, including whole composite keys between the same aliases in the same scope.

    These extra constraints belong to data reads; context packages keep the shared Allowlist's existing fields.
    """
    composite_joins: dict[str, frozenset] = field(default_factory=dict)


def period_column(name: str) -> bool:
    return name.lower() in PERIOD_COLUMNS or name.lower().endswith("_on")


def table_name(t: exp.Table, allow: Allowlist) -> str:
    """The table a source reads, as the allowlist names it: <unit>.<table> in a session of two units, else its name."""
    return f"{t.db}.{t.name}" if allow.attached and t.db else t.name


def canonical(node: exp.Expression, names: dict[str, str]) -> str:
    """A condition as one string, each column's table named by the table it reads (names: alias -> table), so the same
    condition reads the same however the query aliased its tables; the two sides of an equality in one order."""
    node = node.copy()
    for c in list(node.find_all(exp.Column)):
        if c.table in names:
            c.set("table", exp.to_identifier(names[c.table].replace(".", "__")))
            c.set("db", None)
            c.set("catalog", None)
    if isinstance(node, exp.EQ):
        a, b = sorted((node.this.sql("duckdb"), node.expression.sql("duckdb")))
        return f"{a} = {b}"
    return node.sql("duckdb")


def shown(c: str) -> str:
    """A canonical condition as a reader writes it: <unit>.<table>.<column>."""
    return c.replace("__", ".")


def conjuncts(node: exp.Expression | None) -> list[exp.Expression]:
    """The conditions AND joins in node, each one apart; none for no node."""
    if node is None:
        return []
    node = node.unnest() if isinstance(node, exp.Paren) else node
    if isinstance(node, exp.And):
        return conjuncts(node.this) + conjuncts(node.expression)
    return [node]


def inner(j: exp.Join) -> bool:
    """An inner join (JOIN, INNER JOIN, CROSS JOIN or a comma): one that keeps the rows its condition matches, and no
    others. An outer, semi, anti, as-of, positional or natural join is not one."""
    return not j.args.get("side") and not j.args.get("method") and (j.args.get("kind") or "INNER").upper() in ("INNER", "CROSS")


def join_kind(j: exp.Join) -> str:
    return " ".join(str(j.args[k]).upper() for k in ("method", "side", "kind") if j.args.get(k))


def scope_conditions(scope) -> list[exp.Expression]:
    """Every condition a scope applies, one apart: its WHERE's and each of its inner joins' ON. An outer join's ON
    selects no rows (its condition only pairs them), and an anti join's selects the rows it leaves out, so neither is one."""
    e = scope.expression
    if not isinstance(e, exp.Select):
        return []
    out = conjuncts((e.args.get("where") or exp.Where()).this)
    for j in e.args.get("joins") or []:
        if inner(j):
            out += conjuncts(j.args.get("on"))
    return out


def build_allowlist(g, unit: str, certified: list[tuple[str, str]], restricted=(), periods=()) -> Allowlist:
    """certified: (table, column) pairs for the question's terms and filters, from MAPS_TO {certified: true}.
    restricted: (unit, table, column) triples no query may read; never on the list, whatever is passed. periods: (table,
    column) pairs a period binding names (the column a table's period is read on, and the one it rolls up through), on
    the list whatever the column is called, beside those a period column's name gives (period_column)."""
    tables = g.unit_tables(unit)
    edges = g.unit_joins(unit)
    columns: dict[str, set[str]] = {t: set() for t in tables}
    schema = {t: set(g.table_columns(unit, t)) for t in tables}
    joins = set()
    for e in edges:
        joins.add(frozenset({(e["ft"], e["fc"]), (e["tt"], e["tc"])}))
        columns[e["ft"]].add(e["fc"])
        columns[e["tt"]].add(e["tc"])
    for t in tables:
        columns[t] |= {c for c in schema[t] if period_column(c)}
    for t, c in [*certified, *periods]:
        columns.setdefault(t, set()).add(c)
    blocked = {(t, c) for u, t, c in restricted if u == unit}
    for t, c in blocked:
        columns.get(t, set()).discard(c)
    cited = g.domain().get("cites", {}) if hasattr(g, "domain") else {}
    return Allowlist(unit, tables, columns, joins, blocked, schema, {(t, c) for t, c in certified if (t, c) not in blocked},
                     cites={(t, c): cited[f"{unit}.{t}.{c}"] for t, c in blocked if f"{unit}.{t}.{c}" in cited})


def schema_allowlist(g, unit: str, restricted=()) -> Allowlist:
    """For an arm with no certified mappings: every column of the unit's tables, as the
    warehouse schema shows them, and the declared joins. Restricted columns still never."""
    allow = build_allowlist(g, unit, [], restricted)
    for t in allow.tables:
        allow.columns[t] |= {c for c in allow.schema[t] if (t, c) not in allow.restricted}
    allow.measures = {(t, c) for t, cs in allow.columns.items() for c in cs}
    return allow


def warehouse_names(tree: exp.Expression, allow: Allowlist, dialect: str = AUTHORED) -> exp.Expression:
    """The query's names as the warehouse reads them: aliases and CTEs folded as the dialect folds names, and each table and
    column of the warehouse named as the allowlist spells it, its physical name, whatever case the query wrote it in (and
    a column of a CTE or subquery as it names its output, a physical name it selects included). A
    table keeps the name its query reads it by as an alias, so the columns that name it still resolve. A table's name is
    read in any case, as DuckDB reads it; BigQuery reads one only as spelled, so its warehouses route a table to the
    dataset's spelling (kge/adapters/warehouse/bigquery.py route_sql). Both read a column's name in any case. Every
    column of a scope is spelled so, those its ORDER BY, DISTINCT, HAVING and QUALIFY name among them; one there that
    names no table and is a name the scope outputs (an alias an ORDER BY reads, a column its select spelled) takes the
    output's spelling, so it still reads the select's output when names are compared as spelled (resolved)."""
    tree = normalize_identifiers(tree, dialect=dialect)
    if allow is None:
        return tree
    ctes = {c.alias_or_name for c in tree.find_all(exp.CTE)}
    folded: dict[str, list[str]] = {}
    for name in allow.tables:
        folded.setdefault(name.casefold(), []).append(name)
    for table in tree.find_all(exp.Table):
        if not table.db and table.name in ctes:
            continue
        found = folded.get(table_name(table, allow).casefold(), [])
        if len(found) != 1:
            continue
        actual = found[0].rpartition(".")[2]
        alias = table.alias_or_name
        table.set("this", exp.to_identifier(actual))
        if not table.alias and alias != actual:
            table.set("alias", exp.TableAlias(this=exp.to_identifier(alias)))
    for scope in traverse_scope(tree):
        tables = {alias: source for alias, source in scope.sources.items() if isinstance(source, exp.Table)}
        derived = {alias: set(getattr(source.expression, "named_selects", [])) for alias, source in scope.sources.items()
                   if not isinstance(source, exp.Table) and hasattr(source, "expression")}
        resolving = {id(c): c for c in scope.columns}
        for column in resolving.values():
            _spell(column, _names(column, tables, derived, allow))
        outputs = list(scope.expression.named_selects) if isinstance(scope.expression, exp.Query) else []
        for column in scope.walk():
            if isinstance(column, exp.Column) and id(column) not in resolving:
                named = [] if column.table else [name for name in outputs if name.casefold() == column.name.casefold()]
                _spell(column, set(named) if len(set(named)) == 1 else _names(column, tables, derived, allow))
    return tree


def _names(column: exp.Column, tables: dict, derived: dict, allow: Allowlist) -> set[str]:
    """The names column could be, as its scope's sources spell them: its own table's columns, or every source's when it
    names none (a CTE's or subquery's as it names its outputs)."""
    if column.table:
        source = tables.get(column.table)
        return allow.schema.get(table_name(source, allow), set()) if source is not None else derived.get(column.table, set())
    names = {c for source in tables.values() for c in allow.schema.get(table_name(source, allow), set())}
    return names | {c for outputs in derived.values() for c in outputs}


def _spell(column: exp.Column, names: set[str]) -> None:
    """column named as the one of names it is in any case, where exactly one is."""
    matches = [name for name in names if name.casefold() == column.name.casefold()]
    if len(matches) == 1 and matches[0] != column.name:
        column.set("this", exp.to_identifier(matches[0]))


def check(sql: str, allow: Allowlist, dialect: str = AUTHORED, *, inner_only: bool = False) -> list[str]:
    """The reasons this query may not run, restricted personal data first. Empty means it passes the static checks.
    dialect: the sqlglot dialect the query is written in, which it is read as (DuckDB's, the code's own; or the
    warehouses' own, WarehouseSet.sql_dialect, for SQL a model wrote for them). Names are normalised first, as the
    dialect folds them, a warehouse table's and column's to its physical name (warehouse_names). A source may not rename its columns, since DuckDB binds an alias's column list by position: a
    restricted column could be read under an allowed name, and the resolver would not see the swap.
    inner_only applies person-authored data-read rules: inner joins, conjunction-only ON clauses and complete composite keys."""
    try:
        trees = [t for t in sqlglot.parse(sql, read=dialect) if t is not None]
    except sqlglot.errors.SqlglotError as exc:
        return [f"does not parse: {str(exc).splitlines()[0][:120]}"]
    if len(trees) != 1:
        return [f"one statement only; got {len(trees)}"]
    quoted = quoted_names(sql, allow, dialect)
    if quoted:
        return quoted
    tree = warehouse_names(trees[0], allow, dialect)
    if not isinstance(tree, (exp.Select, exp.Union)) or any(isinstance(n, WRITES) for n in tree.walk()):
        return ["read-only: SELECT or WITH ... SELECT, nothing that writes"]
    problems: list[str] = []
    server_functions = {"current_setting", "current_database", "current_schema", "current_schemas", "current_user",
                        "session_user", "user", "session_user_id", "current_query", "current_catalog", "version",
                        "getenv", "getvariable", "current_role", "current_version", "inet_client_addr", "inet_server_addr"}
    expanding_functions = {"repeat", "space", "pad", "lpad", "rpad", "range", "generate_series", "generate_array",
                           "generate_date_array", "generate_timestamp_array", "array_agg", "list", "listagg",
                           "string_agg", "group_concat", "array_concat_agg", "array_resize", "list_resize",
                           "array_concat", "list_concat", "list_zip", "array_zip", "printf", "format"}
    for function in tree.find_all(exp.Func):
        name = function.name if isinstance(function, exp.Anonymous) else function.sql_name()
        if name.lower() in server_functions:
            problems.append(f"server-information function {name.lower()} is not allowed")
        if name.lower() in expanding_functions:
            problems.append(f"memory-expanding function {name.lower()} is not allowed")
    ctes = {c.alias_or_name for c in tree.find_all(exp.CTE)}
    bare = {t.split(".")[-1].casefold() for t in allow.tables}
    for name in sorted(ctes | {a.alias_or_name for a in tree.find_all(exp.Subquery) if a.alias}):
        if name.casefold() in bare:
            problems.append(f"{name} names a table in the warehouse; a CTE or subquery may not borrow it")
    for t in tree.find_all(exp.Table):
        if t.name in ctes and not (t.db or t.catalog):
            continue
        if allow.attached:
            if t.catalog or not t.db:
                problems.append(f"table {t.sql('duckdb')} is not named <unit>.<table>: in a session of two units every table is")
            elif f"{t.db}.{t.name}" not in allow.tables:
                problems.append(f"table {t.db}.{t.name} is not in this session ({allow.unit})")
        elif t.db or t.catalog:
            problems.append(f"table {t.sql()} is outside this unit's warehouse")
        elif t.name not in allow.tables:
            problems.append(f"table {t.name or t.sql('duckdb')} is not in the {allow.unit} warehouse")
    if allow.declares or inner_only:
        for j in tree.find_all(exp.Join):
            if not inner(j):
                problems.append(f"a {join_kind(j)} join keeps rows its condition does not match, or only those: a context's query "
                                "joins its tables with inner joins only, so the conditions of its declared joins select its rows")
    if inner_only:
        for join in tree.find_all(exp.Join):
            for condition in conjuncts(join.args.get("on")):
                if not (isinstance(condition, exp.EQ) and isinstance(condition.this, exp.Column)
                        and isinstance(condition.expression, exp.Column)):
                    problems.append("JOIN ON must be a conjunction (AND) of column equalities; OR, constants and other expressions are not allowed")
    for renamed in (a for a in tree.find_all(exp.TableAlias) if a.columns):
        problems.append(f"{renamed.name or 'a source'} may not rename its columns by position")
    if any(isinstance(n, UNNAMED) for n in tree.walk()):
        problems.append("COLUMNS(...) and #n are not allowed: name each column")
    if any(not isinstance(s.parent, exp.Count) for s in tree.find_all(exp.Star)):
        problems.append("SELECT * is not allowed: name each column")
    if problems:
        return sorted(set(problems))
    try:
        tree = resolved(tree.copy(), allow, dialect)
    except Exception as exc:  # noqa: BLE001
        return [f"does not resolve against the {allow.unit} warehouse: {str(exc).splitlines()[0][:120]}"]
    for row in tree.find_all(exp.TableColumn):
        problems.append(f"{row.name} is a whole row, not a column: name each column")
    for scope in traverse_scope(tree):
        base = {alias: table_name(src, allow) for alias, src in scope.sources.items() if isinstance(src, exp.Table)}
        outputs = {s.alias_or_name for s in scope.expression.selects} if isinstance(scope.expression, exp.Select) else set()
        for col in scope.find_all(exp.Column):
            if not col.table:
                if col.name in outputs and col.find_ancestor(exp.Order):
                    continue
                problems.append(f"{col.name} is not a column of a table in the warehouse (a whole row, or a name it does not have)")
                continue
            src = _source(scope, col.table)
            if src is None:
                problems.append(f"unknown table or alias {col.table}")
            elif isinstance(src, exp.Table):
                name = table_name(src, allow)
                if (name, col.name) in allow.restricted:
                    cite = allow.cites.get((name, col.name))
                    problems.append(f"column {name}.{col.name} is restricted personal data" + (f" ({cite})" if cite else ""))
                elif col.name not in allow.columns.get(name, set()):
                    problems.append(f"column {name}.{col.name} is not certified for this question")
        joined, used = _join_problems(scope, base, allow, inner_only=inner_only)
        problems += joined + _required_problems(scope, used, allow)
    return sorted(set(problems), key=lambda p: ("restricted personal data" not in p, p))


def quoted_names(sql: str, allow: Allowlist, dialect: str) -> list[str]:
    """Where the dialect quotes strings with double quotes (BigQuery's), a double-quoted string that names a table or
    column of the warehouse is a name written in another dialect: read as a string, it would compare or add up a constant
    and give a wrong figure with no error, so it is refused. None in DuckDB's, where double quotes make a name."""
    if '"' not in Dialect.get_or_raise(dialect).tokenizer_class.QUOTES:
        return []
    names = {n.lower() for t, cs in allow.schema.items() for n in (*t.split("."), *cs)}
    try:
        tokens = sqlglot.tokenize(sql, read=dialect)
    except sqlglot.errors.SqlglotError:
        return []
    return sorted({f'"{t.text}" is a string in this dialect, not a name: quote a table or column with backticks'
                   for t in tokens if t.token_type == TokenType.STRING and sql[t.start] == '"' and t.text.lower() in names})


def resolved(tree: exp.Expression, allow: Allowlist, dialect: str = AUTHORED) -> exp.Expression:
    """Every name qualified the way the warehouse would read it, the query read as dialect, its names already the
    warehouse's own (warehouse_names): compared as spelled, never folded again. Raises if a name does not resolve."""
    tables: dict = {}
    for t, cs in allow.schema.items():
        unit, _, name = t.rpartition(".") if allow.attached else ("", "", t)
        (tables.setdefault(unit, {}) if allow.attached else tables)[name] = {c: "VARCHAR" for c in cs}
    spelled = Dialect.get_or_raise(f"{dialect}, normalization_strategy=case_sensitive")
    schema = MappingSchema(tables, dialect=spelled, normalize=False)
    return qualify(tree, schema=schema, dialect=spelled, validate_qualify_columns=True, quote_identifiers=False, identify=False)


def as_figure(sql: str, allow: Allowlist, dialect: str = AUTHORED) -> tuple[list[str], list[str]]:
    """(why this query's value is not a figure, the table.column it measures). Only for a query
    check() has passed, read as the same dialect: one value, an aggregate of a measure column, or count(*) by one."""
    tree = resolved(warehouse_names(sqlglot.parse_one(sql, read=dialect), allow, dialect), allow, dialect)
    if not isinstance(tree, exp.Select):
        return ["a figure is one SELECT, not a set of them"], []
    if len(tree.selects) != 1:
        return [f"a figure is one value; this query returns {len(tree.selects)}"], []
    if tree.args.get("group"):
        return ["a figure is one total over the rows asked for: no GROUP BY"], []
    value = tree.selects[0].unalias()
    agg = value.this if isinstance(value, exp.Round) and whole(value.args.get("decimals")) else value
    if not isinstance(agg, AGGREGATES):
        return [f"a figure is an aggregate (count, sum, avg, min or max, inside round to 0 or more places or not) of one column, "
                f"with nothing added to it; {value.sql('duckdb')} is not"], []
    sources = traverse_scope(tree)[-1].sources
    arg = agg.this
    if isinstance(arg, exp.Distinct) and len(arg.expressions) == 1:
        arg = arg.expressions[0]
    arg = signed(arg) or arg
    if isinstance(arg, exp.Star) and isinstance(agg, exp.Count):
        where = tree.args.get("where")
        by = [(table_name(sources[c.table], allow), c.name) for c in (where.find_all(exp.Column) if where else [])
              if c.find_ancestor(exp.Select) is tree and isinstance(sources.get(c.table), exp.Table)]
        by = [tc for tc in by if tc in allow.measures and tc[1] not in PERIOD_COLUMNS]
        if not by:
            return ["count(*) counts rows: a figure counts the rows a WHERE on a measure, flag or filter column selects, "
                    "or counts that column, such as count(table.column)"], []
        rows = rows_problems(tree, allow)
        return (rows, []) if rows else ([], list(dict.fromkeys(f"{t}.{c}" for t, c in by)))
    if not isinstance(arg, exp.Column):
        return [f"a figure aggregates one column, not {arg.sql('duckdb')}"], []
    src = sources.get(arg.table)
    if not isinstance(src, exp.Table):
        return [f"{arg.sql('duckdb')} is not a column of a table in the warehouse: aggregate the table's own column"], []
    name = table_name(src, allow)
    if (name, arg.name) not in allow.measures:
        return [f"{name}.{arg.name} is not a measure for this question: aggregate the column the question's measure is on"], []
    rows = rows_problems(tree, allow)
    return (rows, []) if rows else ([], [f"{name}.{arg.name}"])


def signed(node: exp.Expression) -> exp.Column | None:
    """The column a signed amount reads: CASE WHEN <condition> THEN -c ELSE c END, every branch the one column c or its
    negative (a magnitude and the direction it moves in, as a measure signs it). None for anything else."""
    if not isinstance(node, exp.Case) or node.args.get("this") is not None:
        return None
    branches = [i.args.get("true") for i in node.args.get("ifs") or []] + [node.args.get("default")]
    columns = [b.this if isinstance(b, exp.Neg) else b for b in (x.unnest() if isinstance(x, exp.Paren) else x for x in branches if x is not None)]
    if len(columns) != len(branches) or not all(isinstance(c, exp.Column) for c in columns):
        return None
    return columns[0] if len({(c.table, c.name) for c in columns}) == 1 else None


def declared_exists(scope, allow: Allowlist) -> bool:
    """A subquery a figure may hold: an EXISTS or NOT EXISTS that selects a constant from tables, every condition of
    which is one the context declared (a row selector, or the semi-join through a certified cross-reference), or another
    such EXISTS, which is a scope of its own and checked as one (a row selector on the far side of a certified join,
    inside its semi-join)."""
    e = scope.expression
    if not (allow.declared and isinstance(e, exp.Select) and isinstance(e.parent, exp.Exists)):
        return False
    if any(e.args.get(k) for k in ("group", "having", "qualify", "with", "distinct")) or not all(isinstance(x.unalias(), exp.Literal) for x in e.selects):
        return False
    if not scope.sources or not all(isinstance(src, exp.Table) for src in scope.sources.values()):
        return False
    if not all(inner(j) for j in e.args.get("joins") or []):
        return False
    names = reach_of(scope, allow)
    nested = lambda c: isinstance(c, exp.Exists) or (isinstance(c, exp.Not) and isinstance(c.this, exp.Exists))  # noqa: E731
    return all(nested(c) or canonical(c, names) in allow.declared for c in scope_conditions(scope))


def rows_problems(tree: exp.Select, allow: Allowlist | None = None) -> list[str]:
    """Why a figure's rows are not the warehouse's own rows, every one the WHERE selects: empty when they are."""
    problems = []
    nested = [s for s in traverse_scope(tree)[:-1] if not (allow and declared_exists(s, allow))]
    if (nested or tree.find(exp.Values, exp.Unnest, exp.Lateral)
            or any(not isinstance(t.this, exp.Identifier) for t in tree.find_all(exp.Table))):
        problems.append("a figure adds up the rows of the warehouse's own tables, in one SELECT: no CTE, subquery, VALUES, UNNEST "
                        "or LATERAL, which multiply the rows or narrow them on a key no declared join links")
    if tree.find(*RANDOM) or any(f.name.lower() == "setseed" for f in tree.find_all(exp.Anonymous)):
        problems.append("a figure adds up every row asked for, the same on every run: no sample, random() or uuid()")
    return problems


def whole(decimals) -> bool:
    """round's precision leaves the figure as the data gave it: none, or a whole number of places written as one."""
    return decimals is None or (isinstance(decimals, exp.Literal) and not decimals.is_string and str(decimals.this).isdigit())


def _source(scope, alias: str):
    """What an alias names, looking outward from this scope: a warehouse table, a CTE or
    subquery scope, or None when nothing in reach declares it."""
    while scope is not None:
        if alias in scope.sources:
            return scope.sources[alias]
        scope = scope.parent
    return None


def reach_of(scope, allow: Allowlist) -> dict[str, str]:
    """The aliases a condition in scope may name, and the table each reads: this scope's tables, then an enclosing
    scope's (a correlated reference)."""
    reach = {}
    s = scope
    while s is not None:
        for alias, src in s.sources.items():
            if isinstance(src, exp.Table):
                reach.setdefault(alias, table_name(src, allow))
        s = s.parent
    return reach


def _join_problems(scope, base: dict[str, str], allow: Allowlist, *, inner_only: bool = False) -> tuple[list[str], dict[frozenset, set[str]]]:
    """Two tables meet only on a declared join, one column equal to another, and every table
    in a scope is reached that way: no condition that hides a join, and no cross product.
    A context's allowlist also declares the predicates of its joins, row selectors and
    windows that are not one column equal to another; one of those is allowed as written, and
    joins the tables it names only where the scope applies, among those tables, every other
    conjunct of the join or row selector it comes from (a window joins nothing). Returns the
    problems and the declared column pairs this scope joined on, each with the aliases that
    joined on it."""
    problems, linked, used = [], set(), {}
    reach = reach_of(scope, allow)
    own = {id(c) for c in scope.find_all(exp.Column)}
    applied = scope_conditions(scope)
    have = [(canonical(c, reach), {x.table for x in c.find_all(exp.Column) if x.table in reach}) for c in applied]
    top = {id(c) for c in applied}
    applied_pairs = {}
    for pred in applied:
        if isinstance(pred, exp.EQ) and isinstance(pred.this, exp.Column) and isinstance(pred.expression, exp.Column):
            left, right = pred.this, pred.expression
            if left.table in reach and right.table in reach and left.table != right.table:
                aliases = frozenset({left.table, right.table})
                pair = frozenset({(reach[left.table], left.name), (reach[right.table], right.name)})
                applied_pairs.setdefault(aliases, set()).add(pair)

    def whole(pred: exp.Expression, c: str, tables: set[str]) -> bool:
        """pred, a condition the scope applies, is one conjunct of a declared condition the scope applies whole, among
        the tables pred names."""
        return id(pred) in top and any(c in link and all(any(k == h and t <= tables for h, t in have) for k in link)
                                       for link in allow.links)

    for pred in scope.find_all(exp.Predicate):
        tables = {c.table for c in pred.find_all(exp.Column) if id(c) in own and c.table in reach}
        if len(tables) < 2:
            continue
        left, right = pred.args.get("this"), pred.args.get("expression")
        if isinstance(pred, exp.EQ) and isinstance(left, exp.Column) and isinstance(right, exp.Column) and len(tables) == 2:
            a, b = (reach[left.table], left.name), (reach[right.table], right.name)
            if frozenset({a, b}) in allow.joins:
                if inner_only and id(pred) not in top:
                    continue
                pair = frozenset({a, b})
                composites = allow.composite_joins if isinstance(allow, DataAllowlist) else {}
                groups = [(name, pairs) for name, pairs in composites.items() if pair in pairs]
                present = applied_pairs.get(frozenset({left.table, right.table}), set())
                if inner_only and groups and not any(pairs <= present for _, pairs in groups):
                    for name, pairs in sorted(groups):
                        missing = ", ".join(" = ".join(f"{t}.{c}" for t, c in sorted(p))
                                            for p in sorted(pairs - present, key=lambda p: sorted(p)))
                        reason = f"the declared join {name} requires all its column pairs together; missing: {missing}"
                        if reason not in problems:
                            problems.append(reason)
                    continue
                linked.add(frozenset({left.table, right.table}))
                used.setdefault(frozenset({a, b}), set()).update((left.table, right.table))
                continue
            problems.append(f"join {a[0]}.{a[1]} = {b[0]}.{b[1]} is not a declared join")
        elif (c := canonical(pred, reach)) in allow.predicates:
            if whole(pred, c, tables):
                linked |= {frozenset({x, y}) for x in tables for y in tables if x < y}
        else:
            problems.append(f"join condition {pred.sql('duckdb')} is not a declared join: write one column equal to another")
    aliases = sorted(base)
    first = scope.expression.args.get("from_") if isinstance(scope.expression, exp.Select) else None
    first = first.this.alias_or_name if first is not None and isinstance(first.this, exp.Table) else None
    reached = {first if first in base else aliases[0]} if aliases else set()
    while True:
        more = {x for pair in linked if pair & reached for x in pair} - reached
        if not more:
            break
        reached |= more
    for alias in aliases:
        if alias not in reached:
            problems.append(f"table {base[alias]} is not joined to the others by a declared join")
    return problems, used


def windowed(have: set[str], through: str, w: dict) -> bool:
    """Whether the conditions have (canonical) read one version of the table through: its current one (is_current), or
    the one valid on a date, valid_from <= X and (valid_to IS NULL OR valid_to >= X), X the same both times. Each is
    read by its shape, not its text: one comparison, and one OR of exactly those two, X a value (a date, a cast, a
    column of another table) with no condition in it and no column of through. Anything looser, such as OR TRUE in
    either half, reads every version, and counts a match once per version."""
    t = through.replace(".", "__")
    cur, vf, vt = (f"{t}.{w[k]}" for k in ("is_current", "valid_from", "valid_to"))
    if {cur, f"TRUE = {cur}"} & have:
        return True
    nodes = []
    for c in have:
        try:
            nodes.append(sqlglot.parse_one(c, read="duckdb").unnest())
        except sqlglot.errors.ParseError:
            continue
    col = lambda n, name: isinstance(n, exp.Column) and f"{n.table}.{n.name}" == name  # noqa: E731

    def value(x: exp.Expression) -> str | None:
        """x as written, if it is one value: no condition, subquery or truth value in it, and no column of through."""
        if x.find(exp.Connector, exp.Not, exp.Predicate, exp.Subquery, exp.Select, exp.Boolean) or any(c.table == t for c in x.find_all(exp.Column)):
            return None
        return x.sql("duckdb")

    def lower(n: exp.Expression) -> str | None:
        if isinstance(n, exp.LTE) and col(n.this, vf):
            return value(n.expression)
        if isinstance(n, exp.GTE) and col(n.expression, vf):
            return value(n.this)
        return None

    def upper(n: exp.Expression, x: str) -> bool:
        if not isinstance(n, exp.Or):
            return False
        a, b = n.this.unnest(), n.expression.unnest()
        if not (isinstance(a, exp.Is) and col(a.this, vt) and isinstance(a.expression, exp.Null)):
            return False
        return ((isinstance(b, (exp.GTE, exp.GT)) and col(b.this, vt) and b.expression.sql("duckdb") == x)
                or (isinstance(b, (exp.LTE, exp.LT)) and col(b.expression, vt) and b.this.sql("duckdb") == x))

    return any(x is not None and any(upper(n, x) for n in nodes) for x in map(lower, nodes))


def _required_problems(scope, used: dict[frozenset, set[str]], allow: Allowlist) -> list[str]:
    """A declared join that carries conditions of its own, used in this scope (one of its column pairs joined here),
    is used with them, here, on each alias of the table it joins through that one of its pairs joined: each condition
    it declares on one table (a certification filter), and that table's window, its current version (is_current) or
    the version valid on a date (valid_from and valid_to). Another alias of the same table carries none of them for it."""
    problems = []
    names = reach_of(scope, allow)
    conditions = scope_conditions(scope)
    for jid, need in sorted(allow.required.items()):
        through = need["through"]
        aliases = sorted({a for pair in need["pairs"] & set(used) for a in used[pair] if names.get(a) == through})
        for alias in aliases:
            have = {canonical(c, names) for c in conditions
                    if all(x.table == alias for x in c.find_all(exp.Column) if names.get(x.table) == through)}
            on = "" if alias == through.split(".")[-1] else f" on {alias}, the alias it joins through"
            for c, said in need["conjuncts"].items():
                if c not in have:
                    problems.append(f"the declared join {jid} is used without its condition {said}{on}, which it requires")
            w = need.get("window")
            if w and not windowed(have, through, w):
                problems.append(f"the declared join {jid} reads {through}" + (f" as {alias}" if on else "") + " without its window: the "
                                f"version current now ({w['is_current']}), or the one valid on a date ({w['valid_from']} and "
                                f"{w['valid_to']})")
    return problems


def dry_run(con, sql: str) -> str | None:
    """Plan the query without running it. None when the warehouse accepts it."""
    try:
        con.execute("EXPLAIN " + sql).fetchall()
        return None
    except RequestDeadlineExceeded:
        raise
    except Exception as exc:  # noqa: BLE001
        return f"dry run failed: {str(exc).splitlines()[0][:160]}"


def gate(con, sql: str, allow: Allowlist, figure: bool = False, dialect: str = AUTHORED) -> dict:
    """failed_at says which kind of check stopped it: a rule ("static") or the warehouse ("dry run").
    figure: the query's value is a figure (the reference agent's model SQL); measured names its column. dialect: the
    SQL's, as check reads it; con is a session that reads the same (WarehouseSet.cursor and attached, given it)."""
    problems, failed_at, measured = check(sql, allow, dialect), "static", []
    if not problems and figure:
        problems, measured = as_figure(sql, allow, dialect)
    if not problems:
        err = dry_run(con, sql)
        problems, failed_at = ([err], "dry run") if err else ([], None)
    return {"passed": not problems, "problems": problems, "failed_at": failed_at if problems else None,
            "checks": ["read-only", "tables", "columns", "joins"] + (["figure"] if figure else []) + ["dry run"],
            **({"measured": measured} if figure and not problems else {})}
