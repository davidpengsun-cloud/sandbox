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
  a pack join's type, complete condition and inclusive validity window are
    authority together. An outer join requires that exact declaration; semi,
    anti, as-of, positional and natural joins acquire no authority from it.
    A sum or count cannot cross a one-to-many join at the source's grain;
    aggregation at its own grain before joining preserves its figure
    A checked null-keeping exclusion may preserve an optional role with LEFT JOIN to a declared table: its branch
    records the exact declared keys and version conditions in ON. Changing
    those conditions or the join type is refused. Person-authored data reads
    use the same declared join authority.
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

import re
from dataclasses import InitVar, dataclass, field

import sqlglot
from sqlglot import exp
from sqlglot.optimizer.normalize_identifiers import normalize_identifiers
from sqlglot.optimizer.qualify import qualify
from sqlglot.optimizer.scope import Scope, traverse_scope
from sqlglot.dialects.dialect import Dialect
from sqlglot.schema import MappingSchema
from sqlglot.tokens import TokenType

from kge.adapters.deadline import RequestDeadlineExceeded
from kge.adapters.warehouse import predicate_semantics
from kge.ports import AUTHORED
from kge.pipeline.textmatch import backslash_like

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
    conjuncts, since such a predicate joins tables only beside the rest of its condition. Windows names the declared
    SCD2 bounds even on a schema allowlist; checked_cte_joins keeps a checked plan's derived outer joins on a runtime."""
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
    windows: set[tuple[str, str, str]] = field(default_factory=set)
    checked_cte_joins: bool = False
    _join_contracts: InitVar[dict | None] = None
    _governed_sql: InitVar[dict | None] = None

    def __post_init__(self, _join_contracts, _governed_sql):
        """Server policy is rebuilt from the pack, outside the public context contract's fields."""
        self._join_contracts = dict(_join_contracts or {})
        self._governed_sql = dict(_governed_sql) if _governed_sql else None

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


@dataclass
class CheckedAllowlist(DataAllowlist):
    """Additional scope requirements for independently checked relational plans."""
    branches: dict[str, dict] = field(default_factory=dict)
    alignment_keys: set[str] = field(default_factory=set)
    alignments: dict[str, set[str]] = field(default_factory=dict)
    selections: dict[str, set[str]] = field(default_factory=dict)
    projections: dict[str, dict[int, str]] = field(default_factory=dict)
    output_shapes: dict[str, str] = field(default_factory=dict)
    date_series: dict[str, str] = field(default_factory=dict)
    _checked_sql: InitVar[dict | None] = None

    def __post_init__(self, _join_contracts, _governed_sql, _checked_sql):
        """Keep the compiler's query outside the serialized policy for structural adapter rewrites."""
        super().__post_init__(_join_contracts, _governed_sql)
        self._checked_sql = dict(_checked_sql) if _checked_sql else None


def temporal_rule(condition: str, alias: str, policy: dict, sources: dict | None = None) -> bool:
    """A complete declared temporal constraint takes authority; malformed dating fails closed."""
    tree = sqlglot.parse_one(condition, read=AUTHORED)
    for column in tree.find_all(exp.Column):
        if column.table:
            continue
        matches = [(a, name) for a, columns in (sources or {}).items() for name in columns if column.name.casefold() == name.casefold()]
        if len(matches) != 1:
            raise ValueError('unresolved column in declared temporal constraint')
        column.set('table', exp.to_identifier(matches[0][0]))
        column.set('this', exp.to_identifier(matches[0][1]))
    columns = {c.name for c in tree.find_all(exp.Column) if c.table == alias}
    temporal = {policy[k] for k in ('is_current', 'valid_from', 'valid_to')}
    if not columns & temporal:
        return False
    have = {canonical(c, {}) for c in conjuncts(tree)}
    if not windowed(have, alias, policy):
        raise ValueError('incomplete declared temporal constraint')
    current = policy['is_current'] in columns
    dated = bool(columns & {policy['valid_from'], policy['valid_to']})
    if current and dated:
        raise ValueError('conflicting declared temporal constraints')
    declared = next((kind for name, kind in ((sources or {}).get(alias) or {}).items()
                     if name.casefold() == policy['is_current'].casefold()), None)
    if current and declared is not None and not have & current_forms(alias, policy['is_current'], declared):
        raise ValueError('incomplete declared temporal constraint')
    return True


NUMERIC = re.compile(r"INT|DECIMAL|DOUBLE|FLOAT|NUMERIC|REAL", re.I)


def current_forms(alias: str, flag: str, kind: str) -> set[str]:
    """The canonical tests that read a current-version flag as its declared type holds it: a number is current when it
    is 1 (or not 0), text when it is 'Y', and a truth value when it is true. A test written for another type reads only
    part of the versions the flag marks, so a join declaring one is incomplete."""
    cur = f"{alias.replace('.', '__')}.{flag}"
    if NUMERIC.search(kind or ""):
        return {f"{cur} <> 0", f"{cur} = 1", f"1 = {cur}"}
    if re.search(r"CHAR|STRING|TEXT", kind or "", re.I):
        return {f"{cur} = 'Y'", f"'Y' = {cur}"}
    return {cur, f"TRUE = {cur}", f"{cur} = TRUE"}


def period_column(name: str) -> bool:
    return name.lower() in PERIOD_COLUMNS or name.lower().endswith("_on")


def table_name(t: exp.Table, allow: Allowlist) -> str:
    """The complete table path a source reads, as the allowlist names it."""
    return ".".join(part.name for part in t.parts)


def table_path(t: exp.Table) -> tuple[str, ...]:
    return t.meta.get("identifier_parts", tuple(part.name for part in t.parts))


def keep_identifier_parts(tree: exp.Expression, sql: str, dialect: str) -> None:
    """Keep separately quoted identifiers where the parser expands a BigQuery quoted path."""
    tokens = sqlglot.tokenize(sql, read=dialect)
    ends = {token.end: i for i, token in enumerate(tokens)}
    for table in tree.find_all(exp.Table):
        if not table.meta.get("quoted_table"):
            continue
        end = ends.get(table.this.meta.get("end"))
        if end is None:
            continue
        start = end
        while start >= 2 and tokens[start - 1].token_type == TokenType.DOT:
            start -= 2
        if start != end:
            table.meta["identifier_parts"] = tuple(token.text for token in tokens[start:end + 1:2])


def canonical(node: exp.Expression, names: dict[str, str]) -> str:
    """A condition as one string, each column's table named by the table it reads (names: alias -> table), so the same
    condition reads the same however the query aliased its tables; the two sides of an equality in one order."""
    node = node.copy()
    for like in list(node.find_all(exp.Like)):
        if not isinstance(like.parent, exp.Escape) and isinstance(like.expression, exp.Literal) and like.expression.is_string:
            explicit = exp.Escape(this=like.copy(), expression=exp.Literal.string(''))
            if like is node:
                node = explicit
            else:
                like.replace(explicit)
    for escaped in list(node.find_all(exp.Escape)):
        try:
            normalized = backslash_like(escaped)
        except ValueError:
            normalized = None
        if normalized is not None:
            normalized = exp.Escape(this=normalized, expression=exp.Literal.string('\\'))
            if escaped is node:
                node = normalized
            else:
                escaped.replace(normalized)
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


def declared_join_type(join: exp.Join) -> str | None:
    """Only ordinary keyed inner and outer joins have a pack declaration."""
    if join.method or join.kind not in ('', 'INNER', 'OUTER'):
        return None
    return (join.side or 'inner').lower()


def join_contracts(allow):
    return [*allow._join_contracts.values(),
            *(need['join_contract'] for need in allow.required.values() if 'join_contract' in need)]


def contract_candidate(join, allow):
    """A physical target may proceed to the resolved, exact declaration check."""
    return isinstance(join.this, exp.Table) and any(
        contract['target'] == table_name(join.this, allow) and contract['type'] == declared_join_type(join)
        for contract in join_contracts(allow))


def matching_join_contracts(scope, join, prior, allow):
    """Resolve the exact declaration once for admission and cardinality analysis alike."""
    names = reach_of(scope, allow)
    target = join.this.alias_or_name
    possible = [contract for contract in join_contracts(allow) if
                (contract['target'] == names.get(target) and any(names.get(alias) == contract['source'] for alias in prior))
                or (contract['source'] == names.get(target) and any(names.get(alias) == contract['target'] for alias in prior))]
    on = conjuncts(join.args.get('on'))
    applied = scope_conditions(scope) if inner(join) else on
    actual = {scope_canonical(c, scope, allow) for c in applied}
    on_set = {scope_canonical(c, scope, allow) for c in on}
    matches = []
    for contract in possible:
        if (contract['source'] != contract['target'] and names.get(target) == contract['source']
                and contract['type'] != 'inner'):
            continue
        expected = contract['condition']
        preserved = (not contract.get('typed', True) and isinstance(allow, CheckedAllowlist)
                     and declared_join_type(join) == 'left' and any(target in need.get('left_joins', {})
                     for need in allow.branches.values()))
        permitted = expected | contract.get('policy_conditions', frozenset())
        if ((contract['type'] != declared_join_type(join) and not preserved) or not on_set
                or not on_set <= permitted or not expected <= actual):
            continue
        if contract['source'] == contract['target']:
            source_aliases = {alias for alias in prior if names.get(alias) == contract['source']}
            if len(source_aliases) != 1 or not contract.get('role_condition'):
                continue
            roles = {next(iter(source_aliases)): 'join_source', target: 'join_target'}
            if contract['role_condition'] <= {canonical(c, roles) for c in applied}:
                matches.append(contract)
            continue
        required_tables = {c.table.replace('__', '.') for condition in expected
                           for c in sqlglot.parse_one(condition, read=AUTHORED).find_all(exp.Column)}
        bound = {table: {alias for alias, physical in names.items() if physical == table and alias in prior | {target}}
                 for table in required_tables}
        if not all(len(aliases) == 1 for aliases in bound.values()):
            continue
        aliases = {next(iter(value)) for value in bound.values()}
        if all(c.table in aliases for condition in applied if scope_canonical(condition, scope, allow) in expected
               for c in condition.find_all(exp.Column)):
            matches.append(contract)
    authorities = {}
    for contract in matches:
        authority = (contract['source'], contract['target'], contract['type'], contract['condition'],
                     contract.get('role_condition', frozenset()))
        cardinality = contract.get('cardinality', 'one-to-one')
        if authorities.setdefault(authority, cardinality) != cardinality:
            return possible, []
    return possible, matches


def declared_join_problems(scope, allow):
    """A join to a declared table states its declared conditions in its own ON, so a comma join, which has no ON,
    matches no declaration and is refused (65a88434). An outer join's ON holds every declared condition; an inner
    join's ON holds at least one and may leave the rest to WHERE. No ON holds a condition the declaration does not
    permit."""
    if not isinstance(scope.expression, exp.Select):
        return []
    names = reach_of(scope, allow)
    first = scope.expression.args.get('from_')
    prior = ({first.this.alias_or_name} if first is not None else set()) | (set(names) - set(scope.sources))
    physical = any(isinstance(source, exp.Table) for _, source in scope.selected_sources.values())
    problems = []
    for join in scope.expression.args.get('joins') or []:
        target = join.this.alias_or_name
        possible, matches = matching_join_contracts(scope, join, prior, allow)
        derived = [scope.sources[alias] for alias in prior | {target} if isinstance(scope.sources.get(alias), Scope)]
        if derived and (physical or not isinstance(allow, CheckedAllowlist)) and (
                not matches or any(grouped_source(source, allow) is None for source in derived)):
            problems.append('derived join requires a declared condition and retained pre-aggregation grain')
        if not possible and contract_candidate(join, allow):
            problems.append('declared join requires a physical source or its retained pre-aggregation grain')
        if possible and not matches:
            problems.append(f"join to {names[target]} must use exactly a declared type, condition and validity window")
        prior.add(target)
    return problems


def grouped_source(source, allow):
    """The physical grain retained by a single-table pre-aggregation, and its projected key names.

    Only grouping columns preserve join authority. Aggregate outputs never become join keys,
    and unions, windows, nested joins and transformed grouping keys acquire no such authority.
    """
    if not isinstance(source, Scope) or not isinstance(source.expression, exp.Select):
        return None
    query = source.expression
    if len(source.selected_sources) != 1 or query.args.get('joins') or query.args.get('qualify'):
        return None
    alias, (_, table) = next(iter(source.selected_sources.items()))
    group = query.args.get('group')
    if not isinstance(table, exp.Table) or group is None or query.find(exp.Window):
        return None
    grouping = set()
    for key in group.expressions:
        if isinstance(key, exp.Literal) and key.is_int:
            index = int(key.this) - 1
            if not 0 <= index < len(query.selects):
                return None
            key = query.selects[index].unalias()
        if not isinstance(key, exp.Column) or key.table != alias:
            return None
        grouping.add(key.name)
    keys = {item.alias_or_name: item.unalias().name for item in query.selects
            if isinstance(item.unalias(), exp.Column) and item.unalias().table == alias
            and item.unalias().name in grouping}
    if set(keys.values()) != grouping or not keys or not any(item.find(exp.Sum, exp.Count) for item in query.selects):
        return None
    return table_name(table, allow), keys


def scope_canonical(node, scope, allow):
    """Canonical authority follows only the group keys a pre-aggregation retained."""
    node = node.copy()
    for column in node.find_all(exp.Column):
        grouped = grouped_source(_source(scope, column.table), allow)
        if grouped and column.name in grouped[1]:
            column.set('this', exp.to_identifier(grouped[1][column.name]))
    return canonical(node, reach_of(scope, allow))


def cardinality_problems(scope, allow):
    """A raw sum or count cannot cross a declared fan-out, even through an aggregate alias."""
    if (not isinstance(scope.expression, exp.Select)
            or not any(c.get('cardinality') == 'one-to-many' for c in join_contracts(allow))):
        return []
    repeated, inherited, multiplied = fanout_state(scope, allow)
    own_columns = {id(column) for column in scope.columns}
    problems = []
    for aggregate in scope.find_all(exp.AggFunc, exp.Anonymous):
        if isinstance(aggregate, exp.Anonymous) and aggregate.name.upper() == 'TO_JSON':
            continue
        if isinstance(aggregate, (exp.Min, exp.Max)):
            continue
        if isinstance(aggregate, exp.Count) and isinstance(aggregate.this, exp.Distinct):
            continue
        if (isinstance(aggregate, exp.Count) and isinstance(aggregate.parent, exp.EQ)
                and aggregate.parent.this is aggregate and isinstance(aggregate.parent.expression, exp.Literal)
                and aggregate.parent.expression.this == '0'):
            continue
        columns = [column for column in aggregate.find_all(exp.Column) if id(column) in own_columns]
        if ((multiplied and not columns)
                or any(column.table in repeated or column.name in inherited.get(column.table, set()) for column in columns)):
            problems.append('one-to-many join repeats a sum or count; aggregate at the measure grain before joining')
    return problems


def fanout_state(scope, allow):
    """Follow multiplied rows and values through derived scopes, so a wrapper cannot launder a fan-out."""
    inherited = {}
    multiplied = False
    for alias, (_, source) in scope.selected_sources.items():
        if not isinstance(source, Scope) or source is scope:
            continue
        inherited[alias], rows = fanout_outputs(source, allow)
        multiplied |= rows

    repeated = set()
    names = reach_of(scope, allow)
    applied = {scope_canonical(c, scope, allow) for c in scope_conditions(scope)}
    external = set(names) - set(scope.sources)
    for contract in join_contracts(allow):
        if contract.get('cardinality') == 'one-to-many' and contract['condition'] <= applied:
            sources = {alias for alias, table in names.items() if table == contract['source']}
            targets = {alias for alias, table in names.items() if table == contract['target']}
            if contract['source'] == contract['target']:
                for source in sources & external:
                    for target in targets - external:
                        roles = {source: 'join_source', target: 'join_target'}
                        have = {canonical(c, roles) for c in scope_conditions(scope)}
                        if contract.get('role_condition') and contract['role_condition'] <= have:
                            repeated.add(source)
            elif sources and targets and (sources | targets) & external:
                repeated.update(sources)
    first = scope.expression.args.get('from_')
    prior = ({first.this.alias_or_name} if first is not None else set()) | external
    for join in scope.expression.args.get('joins') or []:
        target = join.this.alias_or_name
        _, matches = matching_join_contracts(scope, join, prior, allow)
        if any(contract.get('cardinality') == 'one-to-one' for contract in matches):
            prior.add(target)
            continue
        for contract in matches:
            if contract.get('cardinality') == 'one-to-many' and names.get(target) == contract['target']:
                repeated.update(prior)
            elif contract.get('cardinality') == 'one-to-many' and names.get(target) == contract['source']:
                repeated.add(target)
        prior.add(target)
    return repeated, inherited, multiplied or bool(repeated)


def fanout_outputs(scope, allow):
    """Set operations and projections retain the provenance of repeated values."""
    if isinstance(scope.expression, exp.SetOperation):
        names = scope.expression.named_selects
        outputs, multiplied = set(), False
        for branch in scope.set_operation_scopes:
            risky, rows = fanout_outputs(branch, allow)
            outputs.update(names[index] for index, name in enumerate(branch.expression.named_selects)
                           if name in risky and index < len(names))
            multiplied |= rows
        return outputs, multiplied
    repeated, inherited, multiplied = fanout_state(scope, allow)
    grouped = scope.expression.args.get('group') is not None
    collapsed = not grouped and any(not aggregate.find_ancestor(exp.Window) for aggregate in scope.find_all(exp.AggFunc))
    if scope.expression.args.get('distinct'):
        projected = [item.unalias() for item in scope.expression.selects]
        for alias in tuple(repeated):
            grain = grouped_source(scope.sources.get(alias), allow)
            retained = {item.name for item in projected if isinstance(item, exp.Column) and item.table == alias}
            if (grain and set(grain[1]) <= retained and all(isinstance(item, exp.Literal)
                    or (isinstance(item, exp.Column) and item.table == alias) for item in projected)):
                repeated.remove(alias)
                multiplied = False
    own_columns = {id(column) for column in scope.columns}
    outputs = set()
    for item in scope.expression.selects:
        columns = [column for column in unaggregated_columns(item, scalar=grouped) if id(column) in own_columns]
        if ((multiplied and not collapsed and not columns)
                or any(c.table in repeated or c.name in inherited.get(c.table, set()) for c in columns)):
            outputs.add(item.alias_or_name)
    return outputs, multiplied and not collapsed


def unaggregated_columns(node, window=False, scalar=False):
    """Only an aggregate in this SELECT collapses its inputs; windows and scalar subqueries do not protect siblings."""
    scalar = scalar or isinstance(node, (exp.Subquery, exp.Select))
    if isinstance(node, exp.Column):
        return [node]
    window = window or isinstance(node, exp.Window)
    if isinstance(node, exp.AggFunc) and not window and not scalar:
        return []
    return [column for child in node.iter_expressions() for column in unaggregated_columns(child, window, scalar)]


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
    folded: dict[tuple[str, ...], list[str]] = {}
    for name in allow.tables:
        folded.setdefault(tuple(part.casefold() for part in name.split(".")), []).append(name)
    for table in tree.find_all(exp.Table):
        if not table.db and table.name in ctes:
            continue
        found = folded.get(tuple(part.casefold() for part in table_path(table)), [])
        if len(found) != 1:
            continue
        named = normalize_identifiers(exp.to_table(found[0]), dialect=dialect)
        if any(part.name != spelled.name for part, spelled in zip(table.parts[:-1], named.parts[:-1])):
            continue
        actual = found[0].rpartition(".")[2]
        alias = table.alias_or_name
        for part, spelled in zip(table.parts, found[0].split(".")):
            part.set("this", spelled)
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
        for join in (scope.expression.args.get("joins") or []) if isinstance(scope.expression, exp.Select) else []:
            for ident in join.args.get("using") or []:
                if isinstance(ident, exp.Identifier):
                    candidates = _names(exp.column(ident.name), tables, derived, allow)
                    matches = [name for name in candidates if name.casefold() == ident.name.casefold()]
                    if len(set(matches)) == 1:
                        ident.set("this", matches[0])
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
    inner_only applies person-authored data-read rules: exact declared join contracts, conjunction-only ON clauses
    elsewhere and complete composite keys."""
    try:
        trees = [t for t in sqlglot.parse(sql, read=dialect) if t is not None]
    except sqlglot.errors.SqlglotError as exc:
        return [f"does not parse: {str(exc).splitlines()[0][:120]}"]
    if len(trees) != 1:
        return [f"one statement only; got {len(trees)}"]
    quoted = quoted_names(sql, allow, dialect)
    if quoted:
        return quoted
    keep_identifier_parts(trees[0], sql, dialect)
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
    checked_series = {id(node) for node in tree.find_all(exp.Unnest) if isinstance(allow, CheckedAllowlist) and not inner_only
                      and node.find_ancestor(exp.CTE) is not None and node.find_ancestor(exp.CTE).alias_or_name in allow.date_series}
    for function in tree.find_all(exp.Func):
        name = function.name if isinstance(function, exp.Anonymous) else function.sql_name()
        if name.lower() in server_functions:
            problems.append(f"server-information function {name.lower()} is not allowed")
        if name.lower() in expanding_functions and id(function.find_ancestor(exp.Unnest)) not in checked_series:
            problems.append(f"memory-expanding function {name.lower()} is not allowed")
    ctes = {c.alias_or_name for c in tree.find_all(exp.CTE)}
    bare = {t.split(".")[-1].casefold() for t in allow.tables}
    paths = {tuple(t.split(".")) for t in allow.tables}
    for name in sorted(ctes | {a.alias_or_name for a in tree.find_all(exp.Subquery) if a.alias}):
        if name.casefold() in bare:
            problems.append(f"{name} names a table in the warehouse; a CTE or subquery may not borrow it")
    for t in tree.find_all(exp.Table):
        if t.name in ctes and not (t.db or t.catalog):
            continue
        if allow.attached:
            if t.catalog or not t.db:
                problems.append(f"table {t.sql('duckdb')} is not named <unit>.<table>: in a session of two units every table is")
            elif table_path(t) not in paths:
                problems.append(f"table {t.db}.{t.name} is not in this session ({allow.unit})")
        elif (t.db or t.catalog) and table_path(t) not in paths:
            problems.append(f"table {t.sql(dialect)} is outside this unit's warehouse")
        elif table_path(t) not in paths:
            problems.append(f"table {t.name or t.sql('duckdb')} is not in the {allow.unit} warehouse")
    for j in tree.find_all(exp.Join):
        if allow._governed_sql:
            break
        checked_series_join = (isinstance(allow, CheckedAllowlist) and isinstance(j.this, exp.Unnest)
                               and j.find_ancestor(exp.CTE) is not None
                               and j.find_ancestor(exp.CTE).alias_or_name in allow.date_series and not inner_only)
        checked_left = (isinstance(allow, CheckedAllowlist) and isinstance(j.this, exp.Table)
                        and j.side == 'LEFT' and not j.method and j.kind in ('', 'OUTER') and not inner_only
                        and any(j.this.alias_or_name in need.get('left_joins', {})
                                and table_name(j.this, allow) == need['tables'].get(j.this.alias_or_name)
                                for need in allow.branches.values()))
        if not inner(j) and not contract_candidate(j, allow) and not checked_left and not checked_series_join and not ((isinstance(allow, CheckedAllowlist) or allow.checked_cte_joins)
                                                    and isinstance(j.this, exp.Table) and j.this.name in ctes and not inner_only):
            problems.append(f"a {join_kind(j)} join requires an exact pack declaration; otherwise a context's query "
                            "joins its tables with inner joins only, so the conditions of its declared joins select its rows")
    if inner_only and not allow._governed_sql:
        for join in tree.find_all(exp.Join):
            if contract_candidate(join, allow):
                continue
            for condition in conjuncts(join.args.get("on")):
                if not (isinstance(condition, exp.EQ) and isinstance(condition.this, exp.Column)
                        and isinstance(condition.expression, exp.Column)):
                    problems.append("JOIN ON must be a conjunction (AND) of column equalities; OR, constants and other expressions are not allowed")
    for renamed in (a for a in tree.find_all(exp.TableAlias) if a.columns):
        if id(renamed.parent) not in checked_series:
            problems.append(f"{renamed.name or 'a source'} may not rename its columns by position")
    if any(isinstance(n, UNNAMED) for n in tree.walk()):
        problems.append("COLUMNS(...) and #n are not allowed: name each column")
    derived_stars = {
        id(star) for scope in traverse_scope(tree) for star in scope.find_all(exp.Star)
        if (isinstance(star.parent, exp.Column) and isinstance(scope.sources.get(star.parent.table), Scope))
        or (isinstance(star.parent, exp.Select) and scope.references
            and all(isinstance(scope.sources.get(alias), Scope) for alias, _ in scope.references))
    }
    if any(not isinstance(s.parent, exp.Count) and id(s) not in derived_stars for s in tree.find_all(exp.Star)):
        problems.append("SELECT * is not allowed: name each column")
    if problems:
        return sorted(set(problems))
    try:
        tree = resolved(tree.copy(), allow, dialect)
    except Exception as exc:  # noqa: BLE001
        return [f"does not resolve against the {allow.unit} warehouse: {str(exc).splitlines()[0][:120]}"]
    if allow._governed_sql:
        proof = allow._governed_sql
        expected = sqlglot.parse_one(proof['sql'], read=proof['dialect'])
        expected = resolved(warehouse_names(expected, allow, proof['dialect']), allow, proof['dialect'])
        if tree.sql(AUTHORED) != expected.sql(AUTHORED):
            return ['governed query changed its validated join type, condition, validity window, policy or selector']
    for row in tree.find_all(exp.TableColumn):
        problems.append(f"{row.name} is a whole row, not a column: name each column")
    if isinstance(allow, CheckedAllowlist) and not allow._governed_sql:
        problems.extend(checked_scopes(tree, allow))
        if allow._checked_sql:
            expected = sqlglot.parse_one(allow._checked_sql['sql'], read=allow._checked_sql['dialect'])
            if checked_conditions(tree) != checked_conditions(expected):
                problems.append('query predicates differ from the checked pack operators, columns or values')
            if checked_row_bounds(tree) != checked_row_bounds(expected):
                problems.append('query row limit, offset or distinct differs from the checked plan')
    for scope in traverse_scope(tree):
        if not allow._governed_sql:
            problems.extend(declared_join_problems(scope, allow))
            problems.extend(cardinality_problems(scope, allow))
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
        if not allow._governed_sql:
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
    tree = predicate_semantics(tree, dialect)
    tables: dict = {}
    depth = max((len(t.split(".")) for t in allow.schema), default=1)
    for t, cs in allow.schema.items():
        parts = t.split(".")
        nested = tables
        for part in [""] * (depth - len(parts)) + parts[:-1]:
            nested = nested.setdefault(part, {})
        nested[parts[-1]] = {c: "VARCHAR" for c in cs}
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
            elif grouped := grouped_source(src, allow):
                reach.setdefault(alias, grouped[0])
        s = s.parent
    return reach


def physical_key(scope, column, allow):
    grouped = grouped_source(_source(scope, column.table), allow)
    return grouped[1].get(column.name, '') if grouped else column.name


def _join_problems(scope, base: dict[str, str], allow: Allowlist, *, inner_only: bool = False) -> tuple[list[str], dict[frozenset, set[str]]]:
    """Two tables meet only on a declared join, one column equal to another, and every table
    in a scope is reached that way: no condition that hides a join, and no cross product.
    A context's allowlist also declares the predicates of its joins, row selectors and
    windows that are not one column equal to another; one of those is allowed as written, and
    joins the tables it names only where the scope applies, among those tables, every other
    conjunct of the join or row selector it comes from (a window joins nothing). A declared
    SCD2 BETWEEN on a schema allowlist must be a top-level AND-conjunct, and its window's
    table needs a top-level declared equality key in this scope. Returns the
    problems and the declared column pairs this scope joined on, each with the aliases that
    joined on it."""
    problems, linked, used = [], set(), {}
    reach = reach_of(scope, allow)
    own = {id(c) for c in scope.find_all(exp.Column)}
    applied = scope_conditions(scope)
    if isinstance(scope.expression, exp.Select):
        applied += [c for j in scope.expression.args.get("joins") or [] if not inner(j) for c in conjuncts(j.args.get("on"))]
    have = [(scope_canonical(c, scope, allow), {x.table for x in c.find_all(exp.Column) if x.table in reach}) for c in applied]
    top = {id(c) for c in applied}
    applied_pairs = {}
    for pred in applied:
        if isinstance(pred, exp.EQ) and isinstance(pred.this, exp.Column) and isinstance(pred.expression, exp.Column):
            left, right = pred.this, pred.expression
            if left.table in reach and right.table in reach and left.table != right.table:
                aliases = frozenset({left.table, right.table})
                pair = frozenset({(reach[left.table], physical_key(scope, left, allow)),
                                  (reach[right.table], physical_key(scope, right, allow))})
                applied_pairs.setdefault(aliases, set()).add(pair)

    def whole(pred: exp.Expression, c: str, tables: set[str]) -> bool:
        """pred, a condition the scope applies, is one conjunct of a declared condition the scope applies whole, among
        the tables pred names."""
        return id(pred) in top and any(c in link and all(any(k == h and t <= tables for h, t in have) for k in link)
                                       for link in allow.links)

    def window_pair(pred: exp.Expression) -> frozenset:
        if (isinstance(pred, exp.Between) and isinstance(pred.this, exp.Column)
                and isinstance(pred.args.get("low"), exp.Column) and isinstance(pred.args.get("high"), exp.Column)
                and pred.args["low"].table == pred.args["high"].table
                and pred.this.table != pred.args["low"].table
                and (reach.get(pred.args["low"].table), pred.args["low"].name, pred.args["high"].name) in allow.windows):
            return frozenset({pred.this.table, pred.args["low"].table})
        return frozenset()

    keyed = {alias for aliases, pairs in applied_pairs.items() if pairs & allow.joins for alias in aliases}

    for pred in scope.find_all(exp.Predicate):
        tables = {c.table for c in pred.find_all(exp.Column) if id(c) in own and c.table in reach}
        if len(tables) < 2:
            continue
        left, right = pred.args.get("this"), pred.args.get("expression")
        if isinstance(pred, exp.EQ) and isinstance(left, exp.Column) and isinstance(right, exp.Column) and len(tables) == 2:
            a, b = (reach[left.table], physical_key(scope, left, allow)), (reach[right.table], physical_key(scope, right, allow))
            if frozenset({a, b}) in allow.joins:
                if id(pred) not in top:
                    if not inner_only:
                        problems.append(f"join {a[0]}.{a[1]} = {b[0]}.{b[1]} must be an AND-conjunct of the ON or WHERE, not under OR or NOT")
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
        elif (c := scope_canonical(pred, scope, allow)) in allow.predicates:
            if whole(pred, c, tables):
                linked |= {frozenset({x, y}) for x in tables for y in tables if x < y}
        elif not inner_only and id(pred) in top and window_pair(pred) and pred.args["low"].table in keyed:
            pass
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
    if {cur, f"TRUE = {cur}", f"{cur} = TRUE", f"{cur} <> 0", f"{cur} = 1", f"1 = {cur}",
            f"{cur} = 'Y'", f"'Y' = {cur}"} & have:
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
        if x.find(exp.Connector, exp.Not, exp.Predicate, exp.Subquery, exp.Select, exp.Boolean) or any(not c.table or c.table == t for c in x.find_all(exp.Column)):
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
        if isinstance(b, exp.Is):
            a, b = b, a
        if not (isinstance(a, exp.Is) and col(a.this, vt) and isinstance(a.expression, exp.Null)):
            return False
        forward = (exp.GTE,) if w.get('valid_to_inclusive') is True else (exp.GT,) if w.get('valid_to_inclusive') is False else (exp.GTE, exp.GT)
        reverse = (exp.LTE,) if w.get('valid_to_inclusive') is True else (exp.LT,) if w.get('valid_to_inclusive') is False else (exp.LTE, exp.LT)
        return ((isinstance(b, forward) and col(b.this, vt) and b.expression.sql("duckdb") == x)
                or (isinstance(b, reverse) and col(b.expression, vt) and b.this.sql("duckdb") == x))

    inclusive = w.get('valid_to_inclusive', True)
    if inclusive and any(isinstance(n, exp.Between) and col(n.args.get('low'), vf) and col(n.args.get('high'), vt)
                         and value(n.this) is not None for n in nodes):
        return True
    return any(x is not None and any(upper(n, x) for n in nodes) for x in map(lower, nodes))


def _required_problems(scope, used: dict[frozenset, set[str]], allow: Allowlist) -> list[str]:
    """A declared join that carries conditions of its own, used in this scope (one of its column pairs joined here),
    is used with them, here, on each alias of the table it joins through that one of its pairs joined: each condition
    it declares on one table (a certification filter), and that table's window, its current version (is_current) or
    the version valid on a date (valid_from and valid_to). Another alias of the same table carries none of them for it."""
    problems = []
    names = reach_of(scope, allow)
    conditions = scope_conditions(scope)
    if isinstance(allow, CheckedAllowlist):
        conditions += [c for join in scope.expression.args.get('joins') or [] if join.side == 'LEFT'
                       for c in conjuncts(join.args.get('on'))]
    for jid, need in sorted(allow.required.items()):
        if 'through' not in need:
            continue
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


def physical_scope_name(scope):
    expression = scope.expression
    parent = expression.parent
    if isinstance(parent, exp.CTE):
        return parent.alias_or_name
    path = []
    while parent is not None and not isinstance(parent, exp.CTE):
        path.append(expression.arg_key + (str(expression.index) if expression.index is not None else ''))
        expression, parent = parent, parent.parent
    return (parent.alias_or_name if parent is not None else '') + '/' + '/'.join(reversed(path))


def projection_signature(expression, scope=None):
    """Bind an output's name and expression across lossless warehouse encodings."""
    node = expression.copy()
    series_aliases = {series.alias: 'checked_series' + str(index)
                     for index, series in enumerate(scope.expression.find_all(exp.Unnest))} if scope is not None else {}
    for alias in node.find_all(exp.Alias):
        if '/' in alias.alias:
            alias.set('alias', exp.to_identifier(alias.alias.replace('/', '__'), quoted=True))
    for column in node.find_all(exp.Column):
        if column.table in series_aliases:
            column.set('table', exp.to_identifier(series_aliases[column.table]))
        if '/' in column.name and scope is not None and isinstance(_source(scope, column.table), Scope):
            column.set('this', exp.to_identifier(column.name.replace('/', '__'), quoted=True))
    for count in node.find_all(exp.Count):
        if not isinstance(count.this, exp.Distinct):
            continue
        for encoded in list(count.this.find_all(exp.JSONFormat)):
            row = encoded.this
            if isinstance(row, exp.Struct) and all(isinstance(field, exp.PropertyEQ) and field.this.name == 'k' + str(i)
                                                   for i, field in enumerate(row.expressions)):
                encoded.replace(exp.Tuple(expressions=[field.expression.copy() for field in row.expressions]))
    return canonical(node, {})


def identity_ordering(shape):
    """Each unqualified column of a select's own ORDER BY that names an identity output (aligned.d0 AS d0, the only
    output of that name) read as that output's column. Inside an ORDER BY expression BigQuery reads such a name as the
    select alias and leaves it unqualified, DuckDB as the source column aligned.d0; for an identity output the two are
    the same value, so the compiler's shape (resolved as DuckDB reads it), the runtime's and the checked side's agree on
    either dialect. A name that is an alias of any other output keeps its reading, so an ORDER BY moved to it differs."""
    if not isinstance(shape, exp.Select) or not shape.args.get('order'):
        return
    names = [output.alias_or_name for output in shape.expressions]
    identities = {output.alias: output.this for output in shape.expressions
                  if isinstance(output, exp.Alias) and isinstance(output.this, exp.Column) and output.this.table
                  and output.this.name == output.alias and names.count(output.alias) == 1}
    for column in list(shape.args['order'].find_all(exp.Column)):
        if not column.table and column.name in identities and column.find_ancestor(exp.Select) is shape:
            column.replace(identities[column.name].copy())


def checked_output_shape(scope):
    """Bind projections to their scope; an explicit null bucket preserves time ordering, BigQuery's spelling of a
    null-safe comparison reads as the comparison it is, and an ORDER BY name that is an identity output reads as that
    output's column on either dialect (identity_ordering)."""
    shape = scope.expression.copy()
    shape.set('with_', None)
    identity_ordering(shape)
    for node in list(shape.find_all(exp.Paren, exp.Or, exp.Not)):
        if node.parent is not None or node is shape:
            replaced = null_safe_pair(node)
            if replaced is not node:
                node.replace(replaced)
    for alias in shape.find_all(exp.Alias):
        if '/' in alias.alias:
            alias.set('alias', exp.to_identifier(alias.alias.replace('/', '__'), quoted=True))
    for column in shape.find_all(exp.Column):
        if '/' in column.name and isinstance(scope.sources.get(column.table), Scope):
            column.set('this', exp.to_identifier(column.name.replace('/', '__'), quoted=True))
    for index, series in enumerate(shape.find_all(exp.Unnest)):
        original = series.alias
        normalized = 'checked_series' + str(index)
        for column in shape.find_all(exp.Column):
            if column.table == original:
                column.set('table', exp.to_identifier(normalized))
        if series.args.get('alias'):
            series.args['alias'].set('this', exp.to_identifier(normalized))
    for window in shape.find_all(exp.Window):
        if window.args.get('spec') and not isinstance(window.this, exp.RowNumber):
            ordered = window.args.get('order', exp.Order()).expressions
            if len(ordered) == 2 and not ordered[0].args.get('desc'):
                null_bucket = exp.Case(ifs=[exp.If(this=exp.Is(this=ordered[1].this.copy(), expression=exp.Null()),
                    true=exp.Literal.number(1))], default=exp.Literal.number(0))
                if canonical(ordered[0].this, {}) == canonical(null_bucket, {}):
                    for order in ordered:
                        order.set('nulls_first', False)
    return canonical(shape, {})


def checked_conditions(tree):
    """Every checked scope retains exactly its predicates, regardless of operator spelling: BigQuery's spelling of a
    null-safe comparison, which rewrite_sql writes, reads as the comparison the checked statement names."""
    out = {}
    for scope in traverse_scope(tree):
        conditions = scope_conditions(scope)
        for kind in ('having', 'qualify'):
            if clause := scope.expression.args.get(kind):
                conditions.extend(conjuncts(clause.this))
        out[physical_scope_name(scope)] = frozenset(canonical(null_safe_read(node), {}) for node in conditions)
    return out


def null_safe_read(node: exp.Expression) -> exp.Expression:
    """A copy of a condition with each of BigQuery's null-safe spellings in it read as null_safe_pair reads it."""
    node = node.copy()
    top = null_safe_pair(node)
    if top is not node:
        return top
    for inner in list(node.find_all(exp.Paren, exp.Or, exp.Not)):
        if inner is not node and inner.parent is not None:
            replaced = null_safe_pair(inner)
            if replaced is not inner:
                inner.replace(replaced)
    return node


def checked_row_bounds(tree):
    """Every checked scope's LIMIT, OFFSET and DISTINCT, as written: a checked plan keeps the rows it was checked for."""
    out = {}
    for scope in traverse_scope(tree):
        args = scope.expression.args
        out[physical_scope_name(scope)] = tuple(
            canonical(args[kind], {}) if isinstance(args.get(kind), exp.Expression) else args.get(kind)
            for kind in ('limit', 'offset', 'distinct'))
    return out


def checked_scopes(tree, allow):
    """Each physical branch keeps its own predicates; derived joins use complete output keys."""
    problems, seen, selections = [], set(), set()
    for scope in traverse_scope(tree):
        name = physical_scope_name(scope)
        expressions = scope.expression.expressions
        if name in allow.projections and {i: projection_signature(expression, scope) for i, expression in enumerate(expressions)} != allow.projections[name]:
            problems.append('checked scope has different expressions from its declared measures')
        selections.add(name)
        if name in allow.date_series:
            generated = [canonical(expression, {}) for node in scope.expression.args.get('joins') or []
                         if isinstance(node.this, exp.Unnest) for expression in node.this.expressions]
            if generated != [allow.date_series[name]]:
                problems.append('date spine differs from the checked plan')
        if name in allow.output_shapes:
            if checked_output_shape(scope) != allow.output_shapes[name]:
                problems.append('output scope differs from the checked plan')
        have = {canonical(c, {}) for c in scope_conditions(scope)}
        if allow.selections.get(name, set()) - have:
            problems.append('checked selection lacks required predicates')
        base = {a: table_name(s, allow) for a, s in scope.sources.items() if isinstance(s, exp.Table)}
        if base:
            cte = scope.expression.find_ancestor(exp.CTE)
            group = cte.alias_or_name if cte is not None else ''
            candidates = {key: need for key, need in allow.branches.items() if need['scope'] == group and need['tables'] == base}
            if not candidates:
                problems.append('unchecked physical base scope')
                continue
            left_joins = {join.this.alias_or_name: frozenset(canonical(c, {}) for c in conjuncts(join.args.get('on')))
                          for join in scope.expression.args.get('joins') or [] if join.side == 'LEFT' and join.this.alias_or_name in base}
            typed_aliases = {alias for need in candidates.values() for alias in need.get('typed_joins', {})}
            typed_joins = {join.this.alias_or_name: {'type': declared_join_type(join),
                           'condition': frozenset(canonical(c, {}) for c in conjuncts(join.args.get('on')))}
                           for join in scope.expression.args.get('joins') or [] if join.this.alias_or_name in typed_aliases}
            matched = {key for key, need in candidates.items() if need['predicates'] <= have
                       and need.get('left_joins', {}) == left_joins and need.get('typed_joins', {}) == typed_joins}
            seen.update(matched)
            if not matched:
                for need in candidates.values():
                    if need.get('typed_joins', {}) != typed_joins:
                        problems.append('base scope ' + name + ' has different declared join types or conditions from its checked plan')
                    if need.get('left_joins', {}) != left_joins:
                        problems.append('base scope ' + name + ' has different null-preserving joins from its checked plan')
                    for predicate in need['predicates'] - have:
                        problems.append('base scope ' + name + ' lacks required predicate ' + predicate)
        expression = scope.expression
        if not isinstance(expression, exp.Select) or base:
            continue
        first = expression.args.get('from_')
        reached = {first.this.alias_or_name} if first is not None else set()
        for join in expression.args.get('joins') or []:
            if isinstance(join.this, exp.Unnest) and name in allow.date_series:
                continue
            alias = join.this.alias_or_name
            pairs = conjuncts(join.args.get('on'))
            keys = set()
            for pair in pairs:
                pair = null_safe_pair(pair)
                if not isinstance(pair, (exp.EQ, exp.NullSafeEQ)) or not isinstance(pair.this, exp.Column) or not isinstance(pair.expression, exp.Column):
                    continue
                a, b = pair.this, pair.expression
                if a.name == b.name and {a.table, b.table} & reached and alias in (a.table, b.table):
                    keys.add(a.name)
            expected = allow.alignments.get(alias, allow.alignment_keys)
            if not expected <= keys or len(pairs) != len(expected):
                problems.append('aggregate alignment requires every checked dimension key')
            reached.add(alias)
    if set(allow.selections) - selections:
        problems.append('missing checked selection scope')
    if set(allow.projections) - selections:
        problems.append('missing checked measure scope')
    if set(allow.output_shapes) - selections:
        problems.append('missing checked output scope')
    if set(allow.date_series) - selections:
        problems.append('missing checked date spine')
    if seen != set(allow.branches):
        problems.append('missing checked base scope')
    return problems


def null_safe_pair(node: exp.Expression) -> exp.Expression:
    """BigQuery's spelling of a null-safe equality, (COALESCE(a = b, FALSE) OR (a IS NULL AND b IS NULL)), TRUE or
    FALSE and never NULL, read as the NullSafeEQ it is (rewrite_sql writes it), and NOT of it as the NullSafeNEQ; any
    other node as it is."""
    if isinstance(node, exp.Not):
        same = null_safe_pair(node.this)
        return exp.NullSafeNEQ(this=same.this.copy(), expression=same.expression.copy()) if isinstance(same, exp.NullSafeEQ) else node
    inner = node.unnest() if isinstance(node, exp.Paren) else node
    if not isinstance(inner, exp.Or) or not isinstance(inner.this, exp.Coalesce):
        return node
    known = inner.this
    if not isinstance(known.this, exp.EQ) or [e.sql() for e in known.expressions] != ['FALSE']:
        return node
    equal, nulls = known.this, inner.expression.unnest() if isinstance(inner.expression, exp.Paren) else inner.expression
    if not isinstance(nulls, exp.And) or not all(isinstance(n, exp.Is) and isinstance(n.expression, exp.Null) for n in (nulls.this, nulls.expression)):
        return node
    if (nulls.this.this, nulls.expression.this) != (equal.this, equal.expression):
        return node
    return exp.NullSafeEQ(this=equal.this.copy(), expression=equal.expression.copy())


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
