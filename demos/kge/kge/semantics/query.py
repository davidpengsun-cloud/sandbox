"""A context's query, assembled from the graph's query-semantics layer (meta-model 1.1).

A term that resolves to a context (Term -RESOLVES_TO-> Context) is answered
from what the context declares, never from the question's words:

  measure        the measure the context specialises (SPECIALIZES), its
                 expression over the fact table (USES_FACT)
  joins          the declared joins it requires, always, in its order; and those
                 it reaches a dimension, an entity or another unit through, only
                 when the question asks for one. A table that keeps its history
                 (a scd2 policy), the fact among them, is read as its version
                 current now, or, as reported, the version valid on its month's
                 freeze date (the release driver's)
  filters        each predicate it requires (REQUIRES_FILTER), in its order
  row selectors  each one it selects rows with (SELECTS_ROWS_WITH), as NOT
                 EXISTS: a fact row that a row of the selector's table matches
                 is left out (include: kept only with one, EXISTS). One on
                 another unit's table selects that unit's side of a certified
                 join, applied only across it, inside its semi-join
  period         the window asked, through the calendar rollup: on the release
                 driver's month-end column, a month at a time; with no release
                 driver, on the fact's own period column. A period the question
                 says that the parser could not place (a year, a quarter or a
                 fiscal year in another form) is no figure, saying the forms it
                 reads, never every month's
  release rule   a month asked that its release driver has not released, or
                 holds no row for, is abstain_with_reason, saying when it is
                 released; the latest month is the latest released one
  group by       a column of the fact, or of a dimension the declared joins
                 reach: a group_by alias, or "by <acronym>" of a column the
                 acronym spells (the fact's own, where it spells one there)
  entity id      an entity's key equal to the id the question names after one of
                 its aliases; a count of an entity counts its distinct keys, the
                 whole key (a tuple for a key of two columns), as the fact carries
                 it where a declared join from the fact equates it, else on the
                 entity's table its declared joins reach; a count of nothing the
                 pack declares an entity is no figure, never count(*) of the rows
  the question's conditions
                 a value or a condition the question names (the pack's
                 vocabulary, kge/semantics/vocabulary.py), on the column of that name: on
                 the fact, or on the first table of the unit its declared joins
                 reach that has it, in the unit of the column its vocabulary
                 names; on none, no figure
  every term     a certified term the question names besides the context's own
                 (and its variants') is applied (by the words of a table or
                 column it applies, or of their business names), or there is no
                 figure: a
                 dimension it groups by, a value's table, an entity it names or
                 counts; one it applies no condition on (a row it leaves out, a
                 dimension it is not grouped by) would make its figure answer
                 another question; so would a code the question writes in
                 capitals that nothing the parser read accounts for (a region's
                 or a segment's code no vocabulary gives)
  across units   another unit the question names is reached only through a
                 certified join (CERTIFIED_JOIN) whose declared join the context
                 reaches: a semi-join (EXISTS) through the curated table, with
                 the join's own conditions and that table's window, so each fact
                 row counts once. A pair of units with a no-join fact is
                 refused. The units are then read in one session that attaches
                 them (Warehouses.attached), opened only once the access rule has
                 passed on every one of them. A question that asks for restricted
                 personal data by name (a column a restricted-attribute policy
                 governs) is refused across units before any query, under that
                 policy, whatever the certified join would count

The query goes through the gate (kge/pipeline/sqlguard.py) on the context's own
allowlist: the columns these declare, their joins and predicates, and the
conditions a certified join requires. Everything is read from the graph, once,
and the code names no client. A query is read in one of two time modes (MODES):
current or as reported; restated is current.
"""
from __future__ import annotations

import datetime as dt
import json
import re
from dataclasses import dataclass, field

import sqlglot
from sqlglot import exp

from kge.pipeline.sqlguard import Allowlist, DataAllowlist, canonical, conjuncts

MODES = ("current", "as_reported")
AS_READ = {"current": "current: each row's version that holds now", "as_reported": "as reported: each row's version valid on "
                                                                                    "its month's freeze date"}
NUMERIC = re.compile(r"INT|DECIMAL|DOUBLE|FLOAT|NUMERIC|REAL", re.I)
PERIODISH = r"(?:19|20)\d{2}|fy ?\d{2,4}|q[1-4]|quarters?|years?|months?"
CODE = re.compile(r"(?<![\w-])[A-Z]{2,}(?![\w-])")


@dataclass(frozen=True)
class Join:
    """A declared join, as a context renders it: its condition joins source to target (target named alias where it is a
    table joined to itself), through a curated table for a cross-reference."""
    id: str
    kind: str
    condition: str
    alias: str | None
    through: str | None
    source: str
    target: str
    required: bool


@dataclass(frozen=True)
class Rule:
    """A filter (a standing condition on its table's rows) or a row selector (mode exclude: a fact row its table's rows
    match is left out)."""
    id: str
    label: str
    predicate: str
    table: str
    mode: str = ""


@dataclass(frozen=True, init=False)
class Context:
    """A context of the query-semantics layer. Its grain affects point-in-time planning but is not part of the context
    record carried in the agent package, so it is kept outside the dataclass fields and existing packages stay
    byte-identical."""
    id: str
    label: str
    description: str
    term: str
    measure: str
    expression: str
    aggregation: str
    fact: str
    values: tuple[str, ...]
    filters: tuple[Rule, ...]
    selectors: tuple[Rule, ...]
    joins: tuple[Join, ...]

    def __init__(self, id: str, label: str, description: str, term: str, measure: str,
                 expression: str, aggregation: str, grain: str, fact: str,
                 values: tuple[str, ...], filters: tuple[Rule, ...], selectors: tuple[Rule, ...],
                 joins: tuple[Join, ...]):
        for name, value in (("id", id), ("label", label), ("description", description),
                            ("term", term), ("measure", measure), ("expression", expression),
                            ("aggregation", aggregation), ("fact", fact), ("values", values),
                            ("filters", filters), ("selectors", selectors), ("joins", joins),
                            ("_grain", grain)):
            object.__setattr__(self, name, value)

    @property
    def grain(self) -> str:
        return self._grain

    @property
    def unit(self) -> str:
        return self.fact.split(".", 1)[0]


@dataclass
class Asked:
    """What a question asks of a context, as the parser read it (kge/services/ask/engine/question.py, parse)."""
    period: object = None
    time_mode: str = "current"
    latest: bool = False
    group_by: list[str] = field(default_factory=list)
    entity: tuple[str, str] | None = None
    count: str | None = None
    units: list[str] = field(default_factory=list)
    agg: str = "sum"
    restricted: list[str] = field(default_factory=list)
    filters: list[tuple[str, str, object]] = field(default_factory=list)
    terms: list[str] = field(default_factory=list)
    filtered: list[str | None] = field(default_factory=list)
    question: str = ""

    @classmethod
    def of(cls, req) -> Asked:
        return cls(period=req.period, time_mode=req.time_mode, latest=req.latest, group_by=list(req.group_by), entity=req.entity,
                   count=req.counted if req.agg == "count" else None, units=list(req.units), agg=req.agg,
                   restricted=list(req.restricted), filters=list(req.filters), terms=list(req.terms_found),
                   filtered=list(req.filtered), question=req.question)


def bare(table: str) -> str:
    return table.split(".", 1)[1]


def holds(column: str, kind: str) -> str:
    """A flag column read as true, as its type holds a flag: a boolean as itself, a number as any but 0 (SAGE's CURR_IN, 1
    for the current version; DW_BRKRG_MNTH_FRM_TODAY_CT, 0 for the open month), as DuckDB reads a number for a condition,
    and text as Y (SAGE's indicators), so every warehouse reads the condition alike, BigQuery's too, which takes no number
    or text for a condition."""
    if NUMERIC.search(kind or ""):
        return f"{column} <> 0"
    if re.search(r"CHAR|STRING|TEXT", kind or "", re.I):
        return f"{column} = 'Y'"
    return column


def month_end(y: int, m: int) -> dt.date:
    return dt.date(y + (m == 12), m % 12 + 1, 1) - dt.timedelta(days=1)


def month_ends(lo: dt.date, hi: dt.date) -> list[dt.date]:
    """The last day of each month from lo's to hi's."""
    out, y, m = [], lo.year, lo.month
    while (y, m) <= (hi.year, hi.month):
        out.append(month_end(y, m))
        y, m = y + (m == 12), m % 12 + 1
    return out


def month_words(d: dt.date) -> str:
    return d.strftime("%B %Y")


class Plan:
    """One context's query for one question: what it reads, how, and the gate's allowlist for it. A plan that cannot be
    answered carries its decision instead (refuse or abstain, with its reason). The period comes last: query(months),
    once the release rule has said which months the question may have (release_query, released)."""

    def __init__(self, sem: Semantics, context: Context, asked: Asked):
        self.sem, self.context, self.asked = sem, context, asked
        self.unit = context.unit
        self.units = [self.unit]
        self.across: list[dict] = []
        self.decision: dict | None = None
        self.notes: list[str] = []
        self.path: list[str] = []
        self.aliases: dict[str, str] = {}
        self.joins: list[str] = []
        self.where: list[str] = []
        self.group: str | None = None
        self.group_column: str | None = None
        self.select = ""
        self.column = ""
        self.release: dict | None = None
        self.period_on: str | None = None
        self.allow: Allowlist | None = None
        self.fact = ""
        self.counted: tuple[str, str] | None = None
        self.keys: list[dict] = []
        self.decided: dict | None = None
        self.applied: list[tuple[str, str]] = []

    @property
    def attached(self) -> bool:
        return len(self.units) > 1

    def ref(self, table: str, alias: str) -> str:
        """A table as the FROM or JOIN clause names it: with its unit in a session of two units."""
        if self.attached:
            return f"{table} AS {alias}"
        return bare(table) if alias == bare(table) else f"{bare(table)} AS {alias}"

    def name(self, table: str, alias: str | None = None, taken: dict[str, str] | None = None) -> str:
        """An alias for table: its join alias or its name, or <unit>_<name> if another table holds that already."""
        taken = self.aliases if taken is None else taken
        a = alias or bare(table)
        if taken.get(a, table) != table:
            a = table.replace(".", "_")
        taken[a] = table
        return a

    def render(self, sql: str, names: dict[str, str], unit: str | None = None) -> str:
        """sql with each column named by the alias its table has here (names: <unit>.<table> or a join alias -> alias)."""
        unit = unit or self.unit
        node = sqlglot.parse_one(sql, read="duckdb")
        for c in list(node.find_all(exp.Column)):
            if not c.table:
                continue
            key = f"{c.db}.{c.table}" if c.db else c.table if c.table in names else f"{unit}.{c.table}"
            if key in names:
                c.set("table", exp.to_identifier(names[key]))
                c.set("db", None)
        return node.sql("duckdb")

    def outer(self) -> dict[str, str]:
        """The outer query's tables by table id, and by alias, for render."""
        return {**{t: a for a, t in self.aliases.items()}, **{a: a for a in self.aliases}}

    def version(self, alias: str, table: str) -> str | None:
        """The version of a table that keeps its history the time mode reads: the current one, or the one valid on its
        month's freeze date. None for a table that keeps none."""
        p = self.sem.scd2.get(table)
        if p is None:
            return None
        if self.asked.time_mode != "as_reported":
            return holds(f"{alias}.{p['is_current']}", self.sem.tables.get(table, {}).get(p["is_current"], ""))
        freeze = f"{self.release['alias']}.{self.release['freeze_date']}"
        after = ">=" if p.get("valid_to_inclusive", True) else ">"
        return f"{alias}.{p['valid_from']} <= {freeze} AND ({alias}.{p['valid_to']} IS NULL OR {alias}.{p['valid_to']} {after} {freeze})"

    def reach(self, table: str) -> str | None:
        """The alias table has in the outer query once the declared joins that reach it from the fact are rendered;
        None if the context's joins (those within its unit) do not reach it. Each join follows from the fact or a join
        before it (the compiler's S25)."""
        have = {t: a for a, t in self.aliases.items()}
        if table in have:
            return have[table]
        parent: dict[str, Join | None] = {self.context.fact: None}
        for j in self.context.joins:
            if not j.through and j.source in parent and j.target not in parent and not j.alias:
                parent[j.target] = j
        if table not in parent:
            return None
        chain, t = [], table
        while parent[t] is not None:
            chain.append(parent[t])
            t = parent[t].source
        for j in reversed(chain):
            if j.target not in have:
                self.add_join(j)
                have = {t: a for a, t in self.aliases.items()}
        return have[table]

    def add_join(self, j: Join) -> str:
        alias = self.name(j.target, j.alias)
        on = self.render(j.condition, {**self.outer(), j.alias or j.target: alias})
        version = self.version(alias, j.target)
        self.joins.append(f"JOIN {self.ref(j.target, alias)} ON {on}" + (f" AND {version}" if version else ""))
        self.path.append(j.id)
        return alias

    def semi_join(self, j: Join, near: str, far: str, entity: str | None) -> str:
        """EXISTS (...) through the curated table j joins through: a fact row whose near table has a match on the far
        side counts once. The join's conditions as declared, the curated table's window, and an id the question names
        on the far side."""
        near_alias = self.reach(near)
        inner: dict[str, str] = dict(self.aliases)
        t_alias = self.name(j.through, taken=inner)
        f_alias = self.name(far, taken=inner)
        names = {**self.outer(), near: near_alias, j.through: t_alias, far: f_alias}
        node = sqlglot.parse_one(self.render(j.condition, names), read="duckdb")
        on, where = [], []
        for c in conjuncts(node):
            tables = {col.table for col in c.find_all(exp.Column)}
            (on if tables == {t_alias, f_alias} else where).append(c.sql("duckdb"))
        for alias, table in ((t_alias, j.through), (f_alias, far)):
            v = self.version(alias, table)
            if v:
                where.append(v)
        if entity:
            where.append(entity.replace("{alias}", f_alias))
        for s in self.across_selectors(far):
            s_alias = self.name(s.table, taken=inner)
            test = "NOT EXISTS" if s.mode == "exclude" else "EXISTS"
            where.append(f"{test} (SELECT 1 FROM {self.ref(s.table, s_alias)} WHERE "
                         f"{self.render(s.predicate, {s.table: s_alias, far: f_alias}, unit=far.split('.')[0])})")
        self.path.append(j.id)
        key = lambda t: (next((e["key"] for e in self.sem.entities.values() if e["table"] == t), None)  # noqa: E731
                         or [self.sem.d.UNIT_KEY.get(t.split(".")[0], "")])[0]
        self.keys.append({"unit": far.split(".")[0], "join": j.id, "far": f"{f_alias}.{key(far)}", "near": f"{near_alias}.{key(near)}",
                          "through": t_alias})
        return (f"EXISTS (SELECT 1 FROM {self.ref(j.through, t_alias)} JOIN {self.ref(far, f_alias)} ON {' AND '.join(on)} "
                f"WHERE {' AND '.join(where)})")

    def reachable(self) -> list[str]:
        """The fact and the tables of its unit the context's declared joins reach from it, in the order they reach them."""
        out = [self.context.fact]
        for j in self.context.joins:
            if not j.through and j.source in out and j.target not in out and not j.alias:
                out.append(j.target)
        return out

    def key(self, e: dict) -> list[str] | None:
        """An entity's whole key as the query reads it, column by column: the fact's own columns where the fact is the
        entity's table, or a declared join from the fact equates every key column with one of the fact's (the key as the
        fact carries it); else the key on the entity's table, reached through the declared joins; None if they reach it
        not."""
        fact, table = self.context.fact, e["table"]
        if table == fact:
            return [f"{self.fact}.{k}" for k in e["key"]]
        for j in self.context.joins:
            if j.through or j.alias or (j.source, j.target) != (fact, table):
                continue
            carried = {}
            for c in conjuncts(sqlglot.parse_one(j.condition, read="duckdb")):
                if isinstance(c, exp.EQ) and isinstance(c.this, exp.Column) and isinstance(c.expression, exp.Column):
                    ends = {c.this.table: c.this.name, c.expression.table: c.expression.name}
                    if set(ends) == {bare(fact), bare(table)}:
                        carried[ends[bare(table)]] = ends[bare(fact)]
            if all(k in carried for k in e["key"]):
                return [f"{self.fact}.{carried[k]}" for k in e["key"]]
        alias = self.reach(table)
        return [f"{alias}.{k}" for k in e["key"]] if alias else None

    @staticmethod
    def condition(alias: str, column: str, op: str, value) -> str:
        """A condition the question names, as the planner writes one (kge/services/ask/engine/core.py, Engine.plan_unit)."""
        if op == "IS NULL":
            return f"{alias}.{column} IS NULL"
        if op == "= TRUE":
            return f"{alias}.{column}"
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return f"{alias}.{column} {op} {value}"
        return f"{alias}.{column} {op} '" + str(value).replace("'", "''") + "'"

    def across_selectors(self, far: str) -> list[Rule]:
        """The context's row selectors on the tables of far's unit, another unit's: applied only across, to its side."""
        return [s for s in self.context.selectors if s.table.split(".")[0] == far.split(".")[0]]

    def selector(self, rule: Rule, names: dict[str, str]) -> str:
        """The pack's row selector, shared by context answers and governed data reads."""
        test = "NOT EXISTS" if rule.mode == "exclude" else "EXISTS"
        alias = bare(rule.table)
        return (f"{test} (SELECT 1 FROM {self.ref(rule.table, alias)} WHERE "
                f"{self.render(rule.predicate, {**names, rule.table: alias}, unit=rule.table.split('.')[0])})")

    def equals(self, column: str, value: str) -> str:
        """column = value, the value as the column's type reads it: a number as written, else a quoted string."""
        kind = self.sem.types.get(column, "")
        unit, table, name = column.split(".")
        literal = value if NUMERIC.search(kind) and re.fullmatch(r"\d+", value) else "'" + value.replace("'", "''") + "'"
        return f"{{alias}}.{name} = {literal}"

    def parts(self, months: list[dt.date] | None) -> dict:
        """The query's parts, its period the month-ends given (None for every month the filters keep): what it selects,
        its FROM, its JOINs, its conditions and what it groups by. A point-in-time measure of an onboarded unit reads
        the latest snapshot in the period asked, not a sum over every snapshot in it, its date taken over the same FROM,
        joins and conditions as the outer query, so a filter on a joined table chooses the snapshot too; the pack's own
        contexts keep their planning."""
        where = list(self.where)
        if months and self.period_on:
            lo, hi = min(months), max(months)
            where.append(f"{self.period_on} = DATE '{lo.isoformat()}'" if lo == hi
                         else f"{self.period_on} BETWEEN DATE '{lo.isoformat()}' AND DATE '{hi.isoformat()}'")
        elif (self.asked.period is not None and self.period_on
              and self.context.unit in (getattr(self.sem.d, "ONBOARDED", None) or ())
              and self.context.grain in {"as_of", "quarter_end"}):
            lo, hi = self.sem.dates(self.asked.period)
            window = f"{self.period_on} BETWEEN DATE '{lo.isoformat()}' AND DATE '{hi.isoformat()}'"
            inner = [window, *self.where]
            latest = (f"SELECT max({self.period_on}) FROM {self.ref(self.context.fact, self.fact)}" +
                      "".join(f" {j}" for j in self.joins) +
                      " WHERE " + " AND ".join(f"({w})" if " OR " in w else w for w in inner))
            where.append(f"{window} AND {self.period_on} = ({latest})")
        return {"select": self.select, "from": self.ref(self.context.fact, self.fact), "joins": list(self.joins),
                "where": [f"({w})" if " OR " in w else w for w in where], "group": self.group}

    def query(self, months: list[dt.date] | None) -> str:
        """The query, its period the month-ends given (None for every month the filters keep)."""
        q = self.parts(months)
        head = f"SELECT {q['group']}, {q['select']}" if q["group"] else f"SELECT {q['select']}"
        sql = f"{head} FROM {q['from']}" + "".join(f" {j}" for j in q["joins"])
        sql += (" WHERE " + " AND ".join(q["where"])) if q["where"] else ""
        if q["group"]:
            sql += f" GROUP BY {q['group']} ORDER BY {q['group']}"
        return sql

    def release_query(self) -> str | None:
        """The release driver's rows for the months asked, or for the latest released one; None with no release driver,
        or no period and no latest asked."""
        r = self.release
        if r is None or (self.asked.period is None and not self.asked.latest):
            return None
        a, p = r["alias"], f"{r['alias']}.{r['period']}"
        released = holds(f"{a}.{r['released']}", self.sem.tables.get(r["table"], {}).get(r["released"], ""))
        cols = f"{p}, {released}, {a}.{r['release_date']}"
        src = self.ref(r["table"], a)
        if self.asked.latest:
            return f"SELECT {cols} FROM {src} WHERE {released} ORDER BY {p} DESC LIMIT 1"
        lo, hi = self.window()
        return f"SELECT {cols} FROM {src} WHERE {p} BETWEEN DATE '{lo.isoformat()}' AND DATE '{hi.isoformat()}' ORDER BY {p}"

    def window(self) -> tuple[dt.date, dt.date]:
        """The first and last month-end the period asks for."""
        ends = month_ends(*self.sem.dates(self.asked.period))
        return ends[0], ends[-1]

    def released(self, rows: list[tuple]) -> tuple[list[dt.date] | None, str | None]:
        """(the month-ends to read, None) once the release rule allows them, or (None, why not): a month the driver holds
        no row for is not booked, and one it has not released is not released yet."""
        if self.asked.latest:
            if not rows:
                return None, "no month has been released yet"
            self.notes.append(f"the latest month read as {month_words(rows[0][0])}, the latest released")
            return [rows[0][0]], None
        held = {r[0]: r for r in rows}
        wanted = month_ends(*self.window())
        missing = [m for m in wanted if m not in held]
        unreleased = [held[m] for m in wanted if m in held and not held[m][1]]
        if not missing and not unreleased:
            return wanted, None
        said = [f"{month_words(r[0])} is not yet released: its figures are released on {r[2].isoformat()}" for r in unreleased]
        if missing:
            said.append(f"{', '.join(month_words(m) for m in missing)} {'is' if len(missing) == 1 else 'are'} not booked")
        return None, "; ".join(said) + f" ({self.release['table']}). Only released months are reported."

    def about(self) -> dict:
        """The plan, for the answer's trace and the agent's package: ids, never golden questions."""
        return {"context": self.context.id, "term": self.context.term, "fact": self.context.fact, "measure": self.context.measure,
                "time_mode": self.asked.time_mode, "filters": [f.id for f in self.context.filters],
                "row_selectors": [s.id for s in self.context.selectors if s.table.split(".")[0] in self.units], "joins": list(self.path),
                "group_by": self.group_column, "entity": list(self.asked.entity) if self.asked.entity else None,
                "count": self.asked.count if self.asked.agg == "count" else None,
                "conditions": [f"{c} {op}" + ("" if v is None else f" {v}") for c, op, v in self.asked.filters],
                "units": list(self.units), "across": [{k: a[k] for k in ("unit", "join")} for a in self.across]}


class Semantics:
    """The graph's query-semantics layer, read once: the contexts, the time policies, the entities and the certified
    joins. domain: the Engine, or anything that carries the client's domain as it does (UNIT_ORDER, RESTRICTED_COLUMNS,
    GROUP_BY, ACRONYMS, CURATED, FISCAL_START)."""

    def __init__(self, g, domain):
        self.g, self.d = g, domain
        q = g.q
        self.contexts: dict[str, Context] = {}
        rows = q("MATCH (t:Term)-[:RESOLVES_TO]->(c:Context)-[:SPECIALIZES]->(m:Measure), (c)-[:USES_FACT]->(f:Tbl) "
                 "RETURN c.id AS id, c.label AS label, c.description AS description, t.name AS term, m.id AS measure, "
                 "m.expression AS expression, m.aggregation AS aggregation, m.grain AS grain, f.id AS fact ORDER BY c.id")
        rules = q("MATCH (c:Context)-[r:REQUIRES_FILTER]->(x:Filter)-[:TARGETS]->(t:Tbl) RETURN c.id AS ctx, x.id AS id, x.label AS label, "
                  "x.predicate AS predicate, t.id AS tbl, r.ordinal AS n ORDER BY ctx, n")
        selectors = q("MATCH (c:Context)-[r:SELECTS_ROWS_WITH]->(x:RowSelector)-[:TARGETS]->(t:Tbl) RETURN c.id AS ctx, x.id AS id, "
                      "x.label AS label, x.predicate AS predicate, x.mode AS mode, t.id AS tbl, r.ordinal AS n ORDER BY ctx, n")
        joins = q("MATCH (c:Context)-[r:REQUIRES_JOIN]->(j:JoinRule), (j)-[:JOIN_FROM]->(a:Tbl), (j)-[:JOIN_TO]->(b:Tbl) "
                  "RETURN c.id AS ctx, j.id AS id, j.kind AS kind, j.condition AS condition, j.alias AS alias, j.through AS through, "
                  "a.id AS source, b.id AS target, r.required AS required, r.ordinal AS n ORDER BY ctx, n")
        values = q("MATCH (m:Measure)-[r:COMPUTED_FROM]->(x:Col) WHERE r.role = 'value' RETURN m.id AS measure, x.id AS col ORDER BY measure, col")
        for r in rows:
            self.contexts[r["id"]] = Context(
                id=r["id"], label=r["label"] or "", description=r["description"] or "", term=r["term"], measure=r["measure"],
                expression=r["expression"], aggregation=r["aggregation"] or "sum", grain=r["grain"] or "day", fact=r["fact"],
                values=tuple(v["col"] for v in values if v["measure"] == r["measure"]),
                filters=tuple(Rule(x["id"], x["label"] or "", x["predicate"], x["tbl"]) for x in rules if x["ctx"] == r["id"]),
                selectors=tuple(Rule(x["id"], x["label"] or "", x["predicate"], x["tbl"], x["mode"] or "exclude")
                                for x in selectors if x["ctx"] == r["id"]),
                joins=tuple(Join(j["id"], j["kind"], j["condition"], j["alias"] or None, j["through"] or None, j["source"], j["target"],
                                 bool(j["required"])) for j in joins if j["ctx"] == r["id"]))
        self.scd2: dict[str, dict] = {}
        self.releases: dict[str, dict] = {}
        for p in q("MATCH (p:Policy) WHERE p.kind IN ['scd2', 'release_driver'] RETURN p.id AS id, p.kind AS kind, p.params AS params "
                   "ORDER BY p.id"):
            params = json.loads(p["params"])
            (self.scd2 if p["kind"] == "scd2" else self.releases)[params["table"]] = {"id": p["id"], **params}
        self.entities: dict[str, dict] = {}
        for e in q("MATCH (t:Tbl)-[:REPRESENTS]->(e:Entity)-[i:IDENTIFIED_BY]->(c:Col) RETURN e.id AS id, e.label AS label, "
                   "t.id AS tbl, c.name AS col, i.ordinal AS n ORDER BY e.id, n"):
            self.entities.setdefault(e["id"], {"label": e["label"], "table": e["tbl"], "key": []})["key"].append(e["col"])
        self.types = {r["id"]: r["t"] for r in q("MATCH (c:Col) RETURN c.id AS id, c.data_type AS t")}
        self.certified = [{"between": (r["a"], r["b"]), "asserted_by": r["asserted_by"], "asserted_on": r["asserted_on"], "note": r["note"],
                           "declared_join": r["j"]}
                          for r in q("MATCH (x:Unit)-[c:CERTIFIED_JOIN]->(y:Unit) RETURN x.id AS a, y.id AS b, c.asserted_by AS asserted_by, "
                                     "c.asserted_on AS asserted_on, c.note AS note, c.declared_join AS j ORDER BY a, b")]
        self.declared: dict[str, Join] = {r["id"]: Join(r["id"], r["kind"], r["condition"], r["alias"] or None, r["through"] or None,
                                                        r["source"], r["target"], False)
                                          for r in q("MATCH (j:JoinRule)-[:JOIN_FROM]->(a:Tbl), (j)-[:JOIN_TO]->(b:Tbl) RETURN j.id AS id, "
                                                     "j.kind AS kind, j.condition AS condition, j.alias AS alias, j.through AS through, "
                                                     "a.id AS source, b.id AS target ORDER BY j.id")}
        self.tables: dict[str, dict[str, str]] = {}
        for r in q("MATCH (t:Tbl)-[:HAS_COLUMN]->(c:Col) RETURN t.id AS tbl, c.name AS col, c.data_type AS dtype ORDER BY tbl, col"):
            self.tables.setdefault(r["tbl"], {})[r["col"]] = r["dtype"]
        self.cites = {r["col"]: json.loads(r["params"]).get("cite", r["policy"])
                      for r in q("MATCH (p:Policy)-[:GOVERNS]->(c:Col) WHERE p.kind = 'restricted_attribute' RETURN c.id AS col, "
                                 "p.id AS policy, p.params AS params ORDER BY col")}

    def contexts_of(self, term: str | None) -> list[Context]:
        return [c for c in self.contexts.values() if c.term == term]

    def certified_join(self, a: str, b: str) -> dict | None:
        """The certified join fact between two units, either way round, with the declared join it names."""
        return next((c for c in self.certified if set(c["between"]) == {a, b}), None)

    def restricted(self, columns: list[str], units: list[str]) -> dict:
        """The decision on a question across units that asks for restricted personal data by name: refused, citing the
        policy that governs each column, before any query; a certified join answers no such question, whatever it counts."""
        cites = list(dict.fromkeys(self.cites[c] for c in columns if c in self.cites))
        return {"behaviour": "refuse", "restricted": list(columns),
                "reason": f"Restricted personal data ({', '.join(columns)}). Not retrievable by the assistant under any purpose"
                          + (f" ({', '.join(cites)})" if cites else "") + f", across {' and '.join(units)} through the certified "
                          "join or otherwise. No warehouse was queried."}

    def dates(self, period) -> tuple[dt.date, dt.date]:
        from kge.services.ask.engine.core import asked_window, Request
        lo, hi = asked_window(Request(question="", period=period), self.d)
        return dt.date.fromisoformat(lo), dt.date.fromisoformat(hi)

    def plan(self, context: Context, asked: Asked) -> Plan:
        """The context's query for what the question asks: its parts, and the gate's allowlist; or its decision.
        Every certified term the question names is applied, or there is no figure: its own and its variants', and a term
        whose words name a table or column of what the query applies. A condition the question states applies on the
        column of that name, on the fact or the first table of its unit the declared joins reach (a restricted column
        is none, and on none there is no figure), and never on another unit's column its vocabulary names."""
        p = Plan(self, context, asked)
        e = self.entities.get(asked.entity[0]) if asked.entity else None
        named = [*asked.units, *([e["table"].split(".")[0]] if e else [])]
        others = [u for u in dict.fromkeys(named) if u != context.unit]
        for u in others:
            cj = self.certified_join(context.unit, u)
            j = next((x for x in context.joins if cj and x.id == cj["declared_join"]), None)
            if j is None:
                from kge.services.ask.engine.core import pair
                fact = self.g.no_join(context.unit, u)
                if fact:
                    p.decision = {"behaviour": "refuse", "joins_refused": [pair(context.unit, u, self.d)], "fact": fact,
                                  "reason": f"No join path between {context.unit} and {u}. {fact['note']}"}
                else:
                    p.decision = {"behaviour": "abstain", "reason": f"{context.label} reaches {u} through no declared join."}
                return p
            p.units.append(u)
            p.across.append({"unit": u, "join": j.id, "fact": cj, "declared": j})
        if p.attached and asked.restricted:
            p.decision = self.restricted(asked.restricted, p.units)
            return p
        fact = p.fact = p.name(context.fact)
        rollup = next((j for j in context.joins if j.required and j.kind == "calendar_rollup" and j.target in self.releases), None)
        if rollup is not None:
            r = self.releases[rollup.target]
            p.release = {**r, "alias": bare(rollup.target)}
        elif asked.time_mode == "as_reported" or asked.latest:
            p.decision = {"behaviour": "abstain", "reason": f"{context.label} has no release driver, so it has no freeze date to read "
                                                            "a month as reported at, and no released month to call the latest."}
            return p
        for j in context.joins:
            if j.required and not j.through:
                a = p.add_join(j)
                if rollup is not None and j.id == rollup.id:
                    p.release["alias"] = a
        v = p.version(fact, context.fact)
        if v:
            p.where.append(v)
        names = p.outer()
        p.where += [p.render(f.predicate, names) for f in context.filters]
        for s in context.selectors:
            if s.table.split(".")[0] != context.unit:
                continue
            p.where.append(p.selector(s, names))
        if p.release is not None:
            p.period_on = f"{p.release['alias']}.{p.release['period']}"
        else:
            b = self.d.PERIODS.get(context.fact)
            p.period_on = f"{fact}.{b['column']}" if b else None
        if asked.period is not None and p.period_on is None:
            p.notes.append("no period column; period ignored")
        if asked.period is None and not asked.latest:
            from kge.services.ask.engine.core import MONTH
            said = list(dict.fromkeys(m.group(0) for m in re.finditer(rf"\b(?:{PERIODISH}|{MONTH})\b", asked.question, re.I)))
            if said:
                p.decision = {"behaviour": "abstain_with_reason", "reason": self.unplaced(said)}
                return p
            p.notes.append("no period asked: every month the filters keep")
        if asked.time_mode == "as_reported":
            p.notes.append(AS_READ["as_reported"] + f" ({p.release['table']}.{p.release['freeze_date']})")
        entity = None
        if asked.entity:
            e = self.entities.get(asked.entity[0])
            if e is None or len(e["key"]) != 1:
                p.decision = {"behaviour": "abstain", "reason": f"{asked.entity[0]} is not an entity with one key column the question "
                                                                f"can name by id."}
                return p
            cond = p.equals(f"{e['table']}.{e['key'][0]}", asked.entity[1])
            far = next((a for a in p.across if e["table"] in (a["declared"].source, a["declared"].target)
                        and e["table"].split(".")[0] == a["unit"]), None)
            if far is not None:
                entity = (cond, far)
            else:
                alias = p.reach(e["table"])
                if alias is None:
                    p.decision = {"behaviour": "abstain", "reason": f"{context.label} reaches no {e['label']} through its declared joins."}
                    return p
                p.where.append(cond.replace("{alias}", alias))
            p.applied += [(e["table"], k) for k in e["key"]]
        restricted = {".".join(r) for r in self.d.RESTRICTED_COLUMNS}
        for i, (column, op, value) in enumerate(asked.filters):
            named = asked.filtered[i] if i < len(asked.filtered) else None
            unit = named.split(".")[0] if named else None
            owner = next((t for t in p.reachable() if column in self.tables.get(t, {}) and f"{t}.{column}" not in restricted
                          and unit in (None, t.split(".")[0])), None)
            if owner is None:
                said = f"{column} {op}" + ("" if value is None else f" {value}")
                p.decision = {"behaviour": "abstain", "reason": f"{context.label} reaches no {unit + ' ' if unit else ''}column {column} through "
                                                                f"its declared joins, so the question's condition ({said}) cannot be applied: "
                                                                "no figure is given."}
                return p
            p.where.append(p.condition(fact if owner == context.fact else p.reach(owner), column, op, value))
            p.applied.append((owner, column))
        for a in p.across:
            j = a["declared"]
            near, far = (j.source, j.target) if j.source.split(".")[0] == context.unit else (j.target, j.source)
            p.where.append(p.semi_join(j, near, far, entity[0] if entity and entity[1] is a else None))
            sides = p.across_selectors(far)
            p.notes.append(f"{a['unit']} only through the certified join {j.id} ({a['fact']['note']}, asserted by "
                           f"{a['fact']['asserted_by']} on {a['fact']['asserted_on']}): a row counts once, through certified "
                           f"matches, {AS_READ[asked.time_mode].split(':')[0]}"
                           + "".join(f"; on {a['unit']}'s side, {x.id}: {x.label}" for x in sides))
        for col in sorted(asked.group_by, key=lambda c: c.rsplit(".", 1)[0] != context.fact):
            unit, table, column = col.split(".")
            if f"{unit}.{table}" != context.fact and unit != context.unit:
                continue
            alias = fact if f"{unit}.{table}" == context.fact else p.reach(f"{unit}.{table}")
            if alias is not None:
                p.group, p.group_column = f"{alias}.{column}", f"{table}.{column}"
                p.applied.append((f"{unit}.{table}", column))
                break
        if asked.group_by and p.group is None:
            p.decision = {"behaviour": "abstain", "reason": f"{context.label} reaches none of {', '.join(asked.group_by)} through its "
                                                            "declared joins: no such grouping is certified."}
            return p
        expr = p.render(context.expression, {context.fact: fact, bare(context.fact): fact})
        p.column = ", ".join(".".join(v.split(".")[1:]) for v in context.values)
        if asked.agg == "count":
            e = self.entities.get(asked.count or "")
            keys = p.key(e) if e else None
            if keys is None:
                p.decision = {"behaviour": "abstain", "reason": (f"{context.label} reaches no {e['label']} through its declared joins, so "
                                                                 "it counts none." if e else "The question counts nothing the pack "
                                                                 f"declares an entity, and {context.label} is a figure, not a count of "
                                                                 "its rows: no figure is given.")}
                return p
            p.select = f"count(DISTINCT {keys[0]})" if len(keys) == 1 else f"count(DISTINCT ({', '.join(keys)}))"
            p.column = ", ".join(f"{bare(e['table'])}.{k}" for k in e["key"])
            p.applied += [(e["table"], k) for k in e["key"]]
        else:
            p.select = f"round({expr}, 2)" if context.aggregation in ("sum", "avg") else expr
        business = getattr(self.d, "BUSINESS", None) or {}
        names = [set(n.lower().split("_")) for t, c in p.applied
                 for n in (bare(t), c, *business.get(f"{bare(t)}.{c}", "").split("."))]
        others = [t for t in asked.terms if t != context.term and not any(c.measure == context.measure for c in self.contexts_of(t))]
        unapplied = [t for t in others if not any(set(re.findall(r"[a-z0-9]+", t.lower())) <= n for n in names)] + self.unread(asked)
        if unapplied:
            said = " and ".join(", ".join(unapplied).rsplit(", ", 1))
            p.decision = {"behaviour": "abstain", "reason": f"The question names {said}, which {context.label} applies no condition on: "
                                                            "its figure would answer another question, so none is given."}
            return p
        p.allow = self.allowlist(p)
        return p

    def unplaced(self, said: list[str]) -> str:
        """Why a period the question says and the parser could not place gives no figure: the forms it reads (kge/services/ask/engine/question.py
        parse), a fiscal quarter as the client's fiscal year labels it, and the client's phrase for the latest month.
        A period said and not placed is a period asked and not read, never no period at all, and a month without its
        year (June) is one too."""
        from kge.services.ask.engine.core import fiscal_from_calendar
        latest = [f"the {x}" for x, to in (getattr(self.d, "NAMED_PERIODS", None) or {}).items() if to == "latest"]
        forms = ["a month (June 2025)", "a date (31 Mar 2025)", "a calendar quarter (Q2 2025)",
                 f"a fiscal quarter ({fiscal_from_calendar('2025-Q2', self.d.FISCAL_START)})", "a year (in 2025)", *latest]
        return (f"The question's period ({', '.join(said)}) is not one the parser can place, so no figure is given rather than "
                f"every month's. It reads {', '.join(forms[:-1])} or {forms[-1]}.")

    def unread(self, asked: Asked) -> list[str]:
        """The codes the question writes in capitals that nothing the parser read accounts for: no phrase of the client's
        (an alias, a unit's, a context's or an acronym), no term it names, no value or phrase of a condition it applies and
        no word a question is put in. Such a code (a region's, a segment's) is a condition the context does not apply."""
        from kge.services.ask.engine.core import ABBREVIATED, COMMON, MONTHS
        said = [m.group(0) for rx, *_ in (getattr(self.d, "FILTERS", None) or ()) if (m := rx.search(asked.question))]
        phrases = [*(k for kind in ("ALIASES", "UNIT_WORDS", "TIME_MODES", "NAMED_PERIODS", "GROUP_BY", "ENTITY_WORDS", "ACRONYMS")
                     for k in (getattr(self.d, kind, None) or {})), *asked.terms, *(str(v) for _, _, v in asked.filters if v is not None),
                   *said]
        read = {w for x in phrases for w in re.findall(r"[a-z0-9]+", x.lower())} | COMMON | set(MONTHS) | set(ABBREVIATED)
        return [c for c in dict.fromkeys(CODE.findall(asked.question)) if c.lower() not in read]

    def linked(self, first: str, second: str, restricted: list[str] = ()) -> Plan | None:
        """first's entities a certified join links to second's, counted once each: the plan of a question that asks which
        of one unit's customers are also the other's. None with no certified join between them. restricted: the
        restricted columns the question asks for by name; any, and the plan's decision is to refuse it."""
        cj = self.certified_join(first, second)
        j = self.declared.get(cj["declared_join"]) if cj else None
        if j is None:
            return None
        near, far = (j.source, j.target) if j.source.split(".")[0] == first else (j.target, j.source)
        e = next((e for e in self.entities.values() if e["table"] == near and len(e["key"]) == 1), None)
        key = e["key"][0] if e else self.d.UNIT_KEY[first]
        context = Context(id=f"{j.id}:{first}", label=j.id, description=cj["note"], term="", measure="", expression="",
                          aggregation="count", grain="day", fact=near, values=(f"{near}.{key}",), filters=(), selectors=(), joins=(j,))
        p = Plan(self, context, Asked(units=[first, second], agg="count", restricted=list(restricted)))
        p.units.append(second)
        p.across.append({"unit": second, "join": j.id, "fact": cj, "declared": j})
        if restricted:
            p.decision = self.restricted(list(restricted), p.units)
            return p
        alias = p.fact = p.name(near)
        p.where.append(p.semi_join(j, near, far, None))
        p.select, p.column = f"count(DISTINCT {alias}.{key})", f"{bare(near)}.{key}"
        label = lambda t: next((x["label"] for x in self.entities.values() if x["table"] == t), bare(t))  # noqa: E731
        p.counted = (label(near), label(far))
        p.notes.append(f"through the certified join {j.id} ({cj['note']}, asserted by {cj['asserted_by']} on {cj['asserted_on']}): "
                       f"certified matches current now, each {label(near)} counted once")
        p.allow = self.allowlist(p)
        return p

    def allowlist(self, p: Plan, *, data_read: bool = False) -> Allowlist:
        """The context's allowlist for the gate: every table of the units the plan reads, and the curated tables between
        them; the columns the context declares (its measure, filters, row selectors, joins, the time policies of the
        tables they name, its release driver and period column, the dimensions and entity keys its joins reach), and
        those of the question's own conditions the plan applies; its joins' column pairs, its other predicates, and what
        a certified join requires. Data reads also admit declared self-joins and require complete composite keys."""
        ctx, attached = p.context, p.attached
        units = set(p.units)
        tables = {t for t in self.tables if t.split(".")[0] in units}
        if attached:
            tables |= {j.through for a in p.across for j in [a["declared"]] if j.through}
        key = (lambda t: t) if attached else bare
        cols: dict[str, set[str]] = {key(t): set() for t in tables}
        aliases = {j.alias: j.target for j in ctx.joins if j.alias}

        def parsed(sql: str, unit: str) -> exp.Expression | None:
            """sql with each column's table named as the gate names it; None if it names a table the session does not hold
            (a cross-reference, for a query of one unit)."""
            node = sqlglot.parse_one(sql, read="duckdb")
            for c in list(node.find_all(exp.Column)):
                if not c.table:
                    continue
                t = f"{c.db}.{c.table}" if c.db else aliases.get(c.table) or f"{unit}.{c.table}"
                if t not in tables:
                    return None
                cols[key(t)].add(c.name)
                c.set("table", exp.to_identifier(key(t).replace(".", "__")))
                c.set("db", None)
            return node

        unit_of = lambda t: t.split(".")[0]  # noqa: E731
        pieces = [(j.condition, j, unit_of(j.source)) for j in ctx.joins] + [(s.predicate, None, unit_of(s.table)) for s in ctx.selectors] \
            + [(f.predicate, None, unit_of(f.table)) for f in ctx.filters]
        named = {ctx.fact} | {j.source for j in ctx.joins} | {j.target for j in ctx.joins} | {j.through for j in ctx.joins if j.through} \
            | {s.table for s in ctx.selectors} | {f.table for f in ctx.filters}
        joins, predicates, declared, required, links = set(), set(), set(), {}, set()
        composite_joins = {}
        rollup = p.release
        windows = []
        for t in sorted(named & set(self.scd2)):
            s = self.scd2[t]
            n = key(t).replace(".", "__")
            windows.append(holds(f"{n}.{s['is_current']}", self.tables.get(t, {}).get(s["is_current"], "")))
            if rollup:
                r = f"{key(rollup['table']).replace('.', '__')}.{rollup['freeze_date']}"
                after = ">=" if s.get("valid_to_inclusive", True) else ">"
                windows.append(f"{n}.{s['valid_from']} <= {r} AND ({n}.{s['valid_to']} IS NULL OR {n}.{s['valid_to']} {after} {r})")
            for c in ("valid_from", "valid_to", "is_current"):
                cols.setdefault(key(t), set()).add(s[c])
        for sql, j, unit in pieces + [(w, "window", None) for w in windows]:
            window = j == "window"
            if window:
                node, j = sqlglot.parse_one(sql, read="duckdb"), None
            else:
                node = parsed(sql, unit)
            if node is None:
                continue
            pairs, other = set(), False
            for c in conjuncts(node):
                declared.add(canonical(c, {}))
            for pred in node.find_all(exp.Predicate):
                cs = list(pred.find_all(exp.Column))
                if len({c.table for c in cs}) < 2 and not (data_read and j is not None and j.alias and j.source == j.target):
                    continue
                if isinstance(pred, exp.EQ) and isinstance(pred.this, exp.Column) and isinstance(pred.expression, exp.Column):
                    ends = frozenset((c.table.replace("__", "."), c.name) for c in (pred.this, pred.expression))
                    joins.add(ends)
                    pairs.add(ends)
                else:
                    predicates.add(canonical(pred, {}))
                    other = True
            if data_read and j is not None and not j.through and len(pairs) > 1:
                composite_joins[j.id] = frozenset(pairs)
            if other and not window:
                links.add(frozenset(canonical(c, {}) for c in conjuncts(node)))
            if j is not None and j.through:
                one = {canonical(c, {}): c.sql("duckdb").replace("__", ".") for c in conjuncts(node)
                       if len({x.table for x in c.find_all(exp.Column)}) == 1}
                w = self.scd2.get(j.through)
                required[j.id] = {"pairs": pairs, "conjuncts": one, "through": key(j.through),
                                  "window": {k: w[k] for k in ("is_current", "valid_from", "valid_to")} if w else None}
        if rollup:
            cols.setdefault(key(rollup["table"]), set()).update(rollup[k] for k in ("period", "release_date", "freeze_date", "released"))
        b = self.d.PERIODS.get(ctx.fact)
        if b:
            cols[key(ctx.fact)].add(b["column"])
        for col in [*getattr(self.d, "GROUP_BY", {}).values(), *(c for cs in getattr(self.d, "ACRONYMS", {}).values() for c in cs)]:
            t, c = col.rsplit(".", 1)
            if t in named and t in tables:
                cols[key(t)].add(c)
        for e in self.entities.values():
            if e["table"] in named and e["table"] in tables:
                cols[key(e["table"])].update(e["key"])
        for v in ctx.values:
            t, c = v.rsplit(".", 1)
            cols.setdefault(key(t), set()).add(c)
        if ctx.expression and ctx.fact in tables:
            cols.setdefault(key(ctx.fact), set()).update(c.name for c in sqlglot.parse_one(ctx.expression, read="duckdb").find_all(exp.Column))
        for t, c in p.applied:
            if t in tables:
                cols.setdefault(key(t), set()).add(c)
        restricted = {(key(f"{u}.{t}"), c) for u, t, c in self.d.RESTRICTED_COLUMNS if f"{u}.{t}" in tables}
        for t, c in restricted:
            cols.get(t, set()).discard(c)
        joins = {j for j in joins if not any((t, c) in restricted for t, c in j)}
        schema = {key(t): set(self.tables.get(t, {})) for t in tables}
        measures = {(key(v.rsplit(".", 1)[0]), v.rsplit(".", 1)[1]) for v in ctx.values}
        measures |= {(key(f.table), c.name) for f in ctx.filters for c in sqlglot.parse_one(f.predicate, read="duckdb").find_all(exp.Column)
                     if f.table == ctx.fact}
        label = " and ".join(p.units) if attached else p.unit
        cites = {(key(f"{u}.{t}"), c): self.cites[f"{u}.{t}.{c}"] for u, t, c in self.d.RESTRICTED_COLUMNS
                 if f"{u}.{t}" in tables and f"{u}.{t}.{c}" in self.cites}
        allow_type = DataAllowlist if data_read else Allowlist
        extra = {"composite_joins": composite_joins} if data_read else {}
        return allow_type(label, {key(t) for t in tables}, cols, joins, restricted, schema, measures - restricted, attached=attached,
                          predicates=predicates, required=required, declared=declared, links=links, cites=cites, **extra)
