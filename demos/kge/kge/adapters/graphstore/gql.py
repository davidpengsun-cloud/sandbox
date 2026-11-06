"""The BigQuery form of each Cypher query the demo sends its graph store (cypher.py parses them), over the graph's node
and edge tables and its property graph in the client's graph dataset (bigquery.py names them).

Schema is the graph's tables, read from the compiled model (graph-model.yaml, or the meta-model with a pack's
extensions): each node type a table keyed by its key property, each edge type one table per pair of ends it joins (a rel
group's pairs share its label), with an edge_id key and the ends' keys as src_key and dst_key. Table names carry the
prefix of one load of a model hash, its generation (bigquery.py), so a load never replaces the tables a served graph
reads. groups and batch_sql give a batch of writes (base.py Writer) as set-based statements: one per kind of write and
table, its rows in an ARRAY<STRUCT> parameter (struct_rows).

Translate gives a query its Plan:

  reads    GQL in GRAPH_TABLE: SELECT the returned columns FROM GRAPH_TABLE(<graph> MATCH ... RETURN ...), ordered
           outside again, since a subquery's order is not kept. MATCH, OPTIONAL MATCH, WHERE and EXISTS keep their
           Cypher shape; a WITH becomes LET, ORDER BY and LIMIT statements; RETURN names every column c0, c1, ... and
           the result is mapped back to the names Ladybug gives (an alias, else the expression as written,
           COUNT_STAR() and COUNT(<var>._ID) for counts). Aggregation groups by the returned columns, or, where one is
           an array (BigQuery groups no array), by the keys of the elements they come from. NULL sorts last, as in
           Ladybug. Lists: IN is IN UNNEST, an array property's IS NULL is its length, collect is ARRAY_AGG IGNORE
           NULLS, and an empty collect is NULL again, as Ladybug's is.
  writes   SQL DML on the tables, since a BigQuery property graph is read only: CREATE is INSERT (an edge's ends the
           keys a GRAPH_TABLE match returns), MERGE is MERGE, SET is UPDATE and DELETE is DELETE, each over the keys
           its MATCH finds in GRAPH_TABLE; DETACH DELETE takes the node's edges out of every edge table that touches
           its type first. More than one statement runs as one transaction.
  other    CALL table_info reads INFORMATION_SCHEMA.COLUMNS; the DDL a load sends for a table the compiled model holds
           is nothing to do here (the tables are made from the compiled model's BigQuery DDL).

A query outside the subset, or naming a table the graph does not hold, is refused: Unsupported, or RuntimeError
starting "Binder exception", as Ladybug words a query naming no table, so callers that read an older graph's missing
type the same way keep working."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from kge.adapters.graphstore.cypher import (Create, Ddl, Delete, Item, Match, Merge, Order, Project, Query,
                                            SetProps, TableInfo, Unsupported, aggregated, parse, walk)

STEWARD_WRITES = {"Term", "DEFINES", "CAN_WRITE"}
ACCESS = {"Principal", "CAN_READ", "CAN_WRITE", "MAY_PERFORM", "Process"}
TYPES = {"STRING": "STRING", "INT64": "INT64", "DOUBLE": "FLOAT64", "BOOLEAN": "BOOL", "STRING[]": "ARRAY<STRING>"}
EDGE_ID, SRC, DST = "edge_id", "src_key", "dst_key"


def bq_type(cypher_type: str) -> str:
    """A property's BigQuery type: FLOAT[n] is ARRAY<FLOAT64>, whatever its width."""
    t = str(cypher_type).strip().upper()
    if t.startswith("FLOAT[") and t.endswith("]"):
        return "ARRAY<FLOAT64>"
    if t not in TYPES:
        raise ValueError(f"the property type {cypher_type!r} has no BigQuery type here")
    return TYPES[t]


def quote(name: str) -> str:
    if "`" in name:
        raise ValueError(f"{name!r} is not a name")
    return f"`{name}`"


@dataclass
class NodeTable:
    label: str
    key: str
    props: dict[str, str]

    @property
    def table(self) -> str:
        return self.label


@dataclass
class EdgeTable:
    type: str
    src: str
    dst: str
    props: dict[str, str]

    @property
    def table(self) -> str:
        return f"{self.type}__{self.src}__{self.dst}"


@dataclass
class Schema:
    """The graph's node and edge tables, by their loaded names, with each property's Cypher type."""
    nodes: dict[str, NodeTable]
    edges: dict[str, list[EdgeTable]]
    mutable: set[str] = field(default_factory=set)
    """The types written after the load (the pipeline's, and those a steward's decision writes): a read that touches
    none of them reads what the load made, and the store may keep its answer for the process (bigquery.py)."""

    @classmethod
    def build(cls, node_types: list[tuple[str, str, dict]], edge_types: list[tuple[str, list[tuple[str, str]], dict]]) -> "Schema":
        nodes = {name: NodeTable(name, key, dict(props)) for name, key, props in node_types}
        edges = {name: [EdgeTable(name, a, b, dict(props)) for a, b in pairs] for name, pairs, props in edge_types}
        return cls(nodes, edges)

    @classmethod
    def from_meta(cls, model: dict, embedding_dim: int) -> "Schema":
        """From the meta-model with a pack's extensions merged (kge/compiler/emit.py merged): what its Cypher DDL is
        built from."""
        def typed(ps: dict) -> dict:
            return {k: (v if isinstance(v, str) else v["type"]).replace("{embedding_dim}", str(embedding_dim))
                    for k, v in (ps or {}).items()}
        nodes = model["node_types"]
        listed = lambda x: list(x) if isinstance(x, list) else [x]   # noqa: E731
        return cls.build(
            [(t["loaded_as"], t["key"], typed(t.get("properties"))) for t in nodes.values()],
            [(e["loaded_as"], [(nodes[a]["loaded_as"], nodes[b]["loaded_as"]) for a in listed(e["from"]) for b in listed(e["to"])],
              typed(e.get("properties"))) for e in model["edge_types"].values()])

    @classmethod
    def from_graph_model(cls, doc: dict) -> "Schema":
        """From an artifact's graph-model.yaml, which describes the same merged model. Mutable: the types it says the
        pipeline fills, those a steward's decision writes (STEWARD_WRITES), and the access mirror (ACCESS), which a
        re-sync of the identities rewrites, so who may read what is never kept past a change."""
        nodes = doc["node_types"]
        listed = lambda x: list(x) if isinstance(x, list) else [x]   # noqa: E731
        props = lambda t: {p["name"]: p["type"] for p in t.get("properties") or []}   # noqa: E731
        schema = cls.build(
            [(t["loaded_as"], t["key"], props(t)) for t in nodes.values()],
            [(e["loaded_as"], [(nodes[a]["loaded_as"], nodes[b]["loaded_as"]) for a in listed(e["from"]) for b in listed(e["to"])],
              props(e)) for e in doc["edge_types"].values()])
        schema.mutable = {t["loaded_as"] for t in [*nodes.values(), *doc["edge_types"].values()]
                          if t.get("origin") == "pipeline" or t.get("filled_by") == "pipeline"} | STEWARD_WRITES | ACCESS
        return schema

    def to_json(self) -> dict:
        return {"mutable": sorted(self.mutable), "nodes": [[n.label, n.key, n.props] for n in self.nodes.values()],
                "edges": [[name, [[e.src, e.dst] for e in tables], tables[0].props if tables else {}]
                          for name, tables in self.edges.items()]}

    @classmethod
    def from_json(cls, data: dict) -> "Schema":
        schema = cls.build([tuple(n) for n in data["nodes"]], [(name, [tuple(p) for p in pairs], props) for name, pairs, props in data["edges"]])
        schema.mutable = set(data.get("mutable") or [])
        return schema

    def node(self, label: str) -> NodeTable:
        if label not in self.nodes:
            raise RuntimeError(f"Binder exception: Table {label} does not exist.")
        return self.nodes[label]

    def edge(self, type_: str) -> list[EdgeTable]:
        if type_ not in self.edges:
            raise RuntimeError(f"Binder exception: Table {type_} does not exist.")
        return self.edges[type_]

    def edge_props(self, type_: str) -> dict[str, str]:
        tables = self.edge(type_)
        return tables[0].props if tables else {}

    def touching(self, label: str) -> list[tuple[EdgeTable, list[str]]]:
        """Every edge table with label at an end, and which of its end columns hold label's keys."""
        out = []
        for tables in self.edges.values():
            for e in tables:
                ends = [c for c, x in ((SRC, e.src), (DST, e.dst)) if x == label]
                if ends:
                    out.append((e, ends))
        return out


@dataclass
class Namespace:
    """Where a graph's tables are: project, dataset, and the prefix of its model hash."""
    project: str
    dataset: str
    prefix: str

    def table(self, name: str) -> str:
        return quote(f"{self.project}.{self.dataset}.{self.prefix}{name}")

    @property
    def graph(self) -> str:
        return quote(f"{self.project}.{self.dataset}.{self.prefix}graph")

    @property
    def information_schema(self) -> str:
        return quote(f"{self.project}.{self.dataset}") + ".INFORMATION_SCHEMA.COLUMNS"


TEMPLATE = Namespace("${project}", "${dataset}", "${prefix}")
"""The namespace the compiled model's DDL names (kge/compiler/emit.py bigquery_ddl): the store fills in where a graph's
tables go when it makes them."""


@dataclass
class Column:
    alias: str
    name: str
    empty_is_null: bool = False


@dataclass
class Plan:
    """A query's BigQuery form: the SQL, the parameters it names (their BigQuery types), what its columns are called in
    Cypher, and what it is: rows, a write, or nothing to do."""
    kind: str
    sql: str
    params: dict[str, str] = field(default_factory=dict)
    columns: list[Column] = field(default_factory=list)
    cypher: str = ""
    touches: set[str] = field(default_factory=set)
    """Every node label and edge type its patterns name; "*" for an element with neither, which may be any."""


def value_type(v: Any) -> str:
    if isinstance(v, bool):
        return "BOOL"
    if isinstance(v, int):
        return "INT64"
    if isinstance(v, float):
        return "FLOAT64"
    if isinstance(v, (list, tuple)):
        inner = {value_type(x) for x in v if x is not None}
        if inner == {"INT64", "FLOAT64"}:
            inner = {"FLOAT64"}
        if len(inner) > 1:
            raise Unsupported(f"a list parameter of mixed types {sorted(inner)}")
        return f"ARRAY<{inner.pop() if inner else 'STRING'}>"
    return "STRING"


def string(value: str) -> str:
    return "'" + value.replace("\\", "\\\\").replace("'", "\\'").replace("\n", "\\n") + "'"


def ladybug_name(item: Item) -> str:
    """The column name Ladybug gives a RETURN item."""
    if item.alias:
        return item.alias
    e = item.expr
    if e[0] == "call" and e[1] == "count":
        if e[4]:
            return "COUNT_STAR()"
        if len(e[2]) == 1 and e[2][0][0] == "var":
            return f"COUNT({e[2][0][1]}._ID)"
    return item.text


class Translate:
    """Cypher text to its Plan, for one graph's schema and namespace."""

    def __init__(self, schema: Schema, ns: Namespace):
        self.schema = schema
        self.ns = ns

    def __call__(self, text: str, params: dict | None = None) -> Plan:
        """text's Plan, for these parameters; with none given (None), as an inventory reads it: no parameter is asked
        for, and one's type is what the query says of it, else STRING."""
        statement = Statement(self, parse(text), dict(params or {}))
        statement.given = params is not None
        return statement.plan()


class Statement:
    def __init__(self, tr: Translate, query: Query, params: dict):
        self.schema, self.ns, self.query, self.values = tr.schema, tr.ns, query, params
        self.labels: dict[str, str] = {}
        self.types: dict[str, str] = {}
        self.lets: set[str] = set()
        self.hints: dict[str, str] = {}
        self.used: set[str] = set()
        self.anon = 0
        self.touches: set[str] = set()
        self.paths: dict[str, str] = {}
        for clause in query.clauses:
            for path in getattr(clause, "paths", None) or ([clause.path] if isinstance(clause, Merge) else []):
                self.learn(path)
        for clause in query.clauses:
            for e in self.expressions(clause):
                for x in walk(e):
                    if x[0] == "exists":
                        for p in x[1].paths:
                            self.learn(p)

    def learn(self, path) -> None:
        for part in path.parts:
            if hasattr(part, "label"):
                if part.var and part.label:
                    self.labels.setdefault(part.var, part.label)
            elif part.var and part.type:
                self.types.setdefault(part.var, part.type)

    @staticmethod
    def expressions(clause) -> list:
        if isinstance(clause, Match):
            return [clause.where] + [v for p in clause.paths for part in p.parts for _, v in part.props]
        if isinstance(clause, Project):
            return [i.expr for i in clause.items] + [o.expr for o in clause.order] + [clause.limit]
        if isinstance(clause, SetProps):
            return [v for _, _, v in clause.items]
        return []

    def plan(self) -> Plan:
        clauses = self.query.clauses
        last = clauses[-1]
        if isinstance(last, Ddl):
            known = last.name in self.schema.nodes or last.name in self.schema.edges
            if not (known and last.if_not_exists):
                raise Unsupported(f"the DDL {self.query.text!r}: a BigQuery graph's tables are made from its compiled model")
            return self.done(Plan("noop", ""))
        if isinstance(last, TableInfo):
            return self.table_info(last)
        if isinstance(last, Project) and last.kind == "RETURN":
            return self.read(clauses[:-1], last)
        if isinstance(last, Create) and not any(isinstance(c, Match) for c in clauses):
            return self.create_nodes(clauses)
        if isinstance(clauses[0], Merge):
            return self.merge(clauses)
        if isinstance(last, Create):
            return self.create_edges(clauses[:-1], last)
        if isinstance(last, SetProps):
            return self.update(clauses[:-1], last)
        if isinstance(last, Delete):
            return self.delete(clauses[:-1], last)
        raise Unsupported(f"the statement {self.query.text!r}")

    def done(self, plan: Plan) -> Plan:
        plan.cypher = self.query.text
        plan.touches = set(self.touches)
        plan.params = {name: self.hints.get(name) or value_type(self.values.get(name)) for name in sorted(self.used)}
        missing = sorted(set(self.used) - set(self.values))
        if missing and getattr(self, "given", True):
            raise Unsupported(f"the parameter {missing[0]} is not given")
        return plan

    def var(self, name: str) -> str:
        return f"v_{name}"

    def fresh(self) -> str:
        self.anon += 1
        return f"_{self.anon}"

    def prop_type(self, var: str, prop: str) -> str | None:
        if var in self.labels:
            return self.schema.node(self.labels[var]).props.get(prop)
        if var in self.types:
            return self.schema.edge_props(self.types[var]).get(prop)
        return None

    def key_of(self, var: str) -> str:
        if var in self.labels:
            return f"{self.var(var)}.{quote(self.schema.node(self.labels[var]).key)}"
        if var in self.types:
            return f"{self.var(var)}.{quote(EDGE_ID)}"
        raise Unsupported(f"{var} has no label to find its key by")

    def param(self, name: str, hint: str | None = None) -> str:
        self.used.add(name)
        if hint and name not in self.hints:
            self.hints[name] = hint
        return f"@{name}"

    def hint_of(self, e) -> str | None:
        if e[0] == "prop":
            t = self.prop_type(e[1], e[2])
            return bq_type(t) if t else None
        if e[0] == "lit" and e[1] is not None:
            return value_type(e[1])
        return None

    def pattern(self, path, where: list) -> str:
        """A path in GQL; each inline property map is moved to where, as conditions."""
        out = []
        for part in path.parts:
            self.touches.add((part.label if hasattr(part, "label") else part.type) or "*")
            if hasattr(part, "label"):
                var = part.var or (self.fresh() if part.props else None)
                if part.var is None and part.props and part.label:
                    self.labels[var] = part.label
                if part.label:
                    self.schema.node(part.label)
                label = f":{quote(part.label)}" if part.label else ""
                out.append(f"({self.var(var) if var else ''}{label})")
                for k, v in part.props:
                    where.append(("cmp", "=", ("prop", var, k), v))
            else:
                var = part.var or (self.fresh() if (part.props or part.hops) else None)
                if part.var is None and var and part.type:
                    self.types[var] = part.type
                if part.type:
                    self.schema.edge(part.type)
                label = f":{quote(part.type)}" if part.type else ""
                body = f"[{self.var(var) if var else ''}{label}]"
                arrow = {"->": f"-{body}->", "<-": f"<-{body}-", "-": f"-{body}-"}[part.direction]
                if part.hops:
                    arrow += "{" + f"{part.hops[0]},{part.hops[1]}" + "}"
                    if path.name:
                        self.paths[path.name] = var
                out.append(arrow)
                for k, v in part.props:
                    where.append(("cmp", "=", ("prop", var, k), v))
        text = "".join(out)
        if any(getattr(p, "hops", None) for p in path.parts):
            if not all(p.shortest for p in path.parts if getattr(p, "hops", None)):
                raise Unsupported("a variable-length path that is not SHORTEST")
            text = "ANY SHORTEST " + text
        return text

    def match(self, m: Match) -> str:
        where: list = []
        patterns = ", ".join(self.pattern(p, where) for p in m.paths)
        if m.where is not None:
            where.append(m.where)
        cond = " AND ".join(f"({self.expr(w)})" for w in where)
        return f"{'OPTIONAL ' if m.optional else ''}MATCH {patterns}" + (f" WHERE {cond}" if cond else "")

    def expr(self, e) -> str:
        kind = e[0]
        if kind == "lit":
            v = e[1]
            if v is None:
                return "NULL"
            if isinstance(v, bool):
                return "TRUE" if v else "FALSE"
            if isinstance(v, (int, float)):
                return repr(v)
            return string(v)
        if kind == "param":
            return self.param(e[1])
        if kind == "prop":
            if e[1] in self.paths:
                raise Unsupported("a property of a path")
            return f"{self.var(e[1])}.{quote(e[2])}"
        if kind == "var":
            if e[1] in self.lets:
                return f"a_{e[1]}"
            raise Unsupported(f"the element {e[1]} as a value")
        if kind == "and" or kind == "or":
            return f"({self.expr(e[1])}) {kind.upper()} ({self.expr(e[2])})"
        if kind == "not":
            return f"NOT ({self.expr(e[1])})"
        if kind == "neg":
            return f"-({self.expr(e[1])})"
        if kind == "cmp":
            left, right = e[2], e[3]
            if right[0] == "param":
                self.param(right[1], self.hint_of(left))
            if left[0] == "param":
                self.param(left[1], self.hint_of(right))
            return f"{self.expr(left)} {e[1]} {self.expr(right)}"
        if kind == "arith":
            return f"({self.expr(e[2])} {e[1]} {self.expr(e[3])})"
        if kind == "isnull":
            inner = e[1]
            if inner[0] == "prop" and (self.prop_type(inner[1], inner[2]) or "").endswith("]"):
                return f"ARRAY_LENGTH({self.expr(inner)}) {'>' if e[2] else '='} 0"
            return f"{self.expr(inner)} IS {'NOT ' if e[2] else ''}NULL"
        if kind == "in":
            return self.in_(e[1], e[2])
        if kind == "list":
            return "[" + ", ".join(self.expr(x) for x in e[1]) + "]"
        if kind == "case":
            whens = " ".join(f"WHEN {self.expr(c)} THEN {self.expr(v)}" for c, v in e[1])
            return f"CASE {whens} ELSE {self.expr(e[2])} END"
        if kind == "cast":
            target = e[2].replace(" ", "").upper()
            if target.startswith("FLOAT[") and e[1][0] == "param":
                return self.param(e[1][1], "ARRAY<FLOAT64>")
            raise Unsupported(f"CAST to {e[2]}")
        if kind == "exists":
            return "EXISTS { " + self.match(e[1]) + " }"
        if kind == "call":
            return self.call(e)
        raise Unsupported(f"the expression {kind}")

    def in_(self, left, right) -> str:
        if right[0] == "param":
            element = self.hint_of(left)
            self.param(right[1], f"ARRAY<{element}>" if element and not element.startswith("ARRAY") else None)
            return f"{self.expr(left)} IN UNNEST({self.expr(right)})"
        if right[0] == "list":
            return f"{self.expr(left)} IN UNNEST({self.expr(right)})"
        if right[0] == "prop":
            t = self.prop_type(right[1], right[2]) or ""
            if not t.endswith("[]"):
                raise Unsupported(f"IN a property that is not a list: {right[2]}")
            if left[0] == "param":
                self.param(left[1], bq_type(t[:-2]))
            return f"{self.expr(left)} IN UNNEST({self.expr(right)})"
        raise Unsupported("IN anything but a parameter, a list or a list property")

    def call(self, e) -> str:
        _, name, args, distinct, star = e
        d = "DISTINCT " if distinct else ""
        if name == "count":
            if star:
                return "COUNT(*)"
            a = args[0]
            inner = self.key_of(a[1]) if a[0] == "var" else self.expr(a)
            return f"COUNT({d}{inner})"
        if name == "collect":
            return f"ARRAY_AGG({d}{self.expr(args[0])} IGNORE NULLS)"
        if name in ("sum", "min", "max", "avg"):
            return f"{name.upper()}({d}{self.expr(args[0])})"
        if name in ("lower", "upper", "coalesce", "left", "abs", "round"):
            return f"{name.upper()}({', '.join(self.expr(a) for a in args)})"
        if name == "size":
            return f"ARRAY_LENGTH({self.expr(args[0])})"
        if name == "label":
            if args[0][0] != "var":
                raise Unsupported("label() of anything but a variable")
            return f"LABELS({self.var(args[0][1])})[OFFSET(0)]"
        if name == "array_cosine_similarity":
            return f"(1 - COSINE_DISTANCE({self.expr(args[0])}, {self.expr(args[1])}))"
        if name == "properties":
            target, prop = args
            if target[0] == "call" and target[1] == "rels" and target[2][0][0] == "var" and target[2][0][1] in self.paths \
                    and prop[0] == "lit":
                return f"ARRAY_AGG({self.var(self.paths[target[2][0][1]])}.{quote(prop[1])})"
            raise Unsupported("properties() of anything but a path's relationships")
        raise Unsupported(f"the function {name}()")

    def limit(self, e) -> str:
        if e[0] == "lit" and isinstance(e[1], int):
            return str(e[1])
        if e[0] == "param":
            return self.param(e[1], "INT64")
        raise Unsupported("a LIMIT that is not a number or a parameter")

    def order_term(self, text: str, desc: bool) -> str:
        return f"{text} {'DESC NULLS FIRST' if desc else 'ASC NULLS LAST'}"

    def body(self, clauses: list) -> tuple[list[str], bool]:
        """The GQL statements of the clauses before a RETURN or a write, and whether a WITH DISTINCT asks the result to
        be distinct."""
        out, distinct = [], False
        for c in clauses:
            if isinstance(c, Match):
                out.append(self.match(c))
            elif isinstance(c, Project) and c.kind == "WITH":
                named = [i for i in c.items if not (i.expr[0] == "var" and i.alias in (None, i.expr[1]))]
                if c.distinct:
                    if named or c.order or c.limit is not None:
                        raise Unsupported("WITH DISTINCT of anything but variables")
                    distinct = True
                for i in named:
                    if aggregated(i.expr):
                        raise Unsupported("an aggregate in WITH")
                    if not i.alias:
                        raise Unsupported("a WITH expression with no alias")
                    out.append(f"LET a_{i.alias} = {self.expr(i.expr)}")
                    self.lets.add(i.alias)
                if c.order:
                    out.append("ORDER BY " + ", ".join(self.order_term(self.expr(o.expr), o.desc) for o in c.order))
                if c.limit is not None:
                    out.append(f"LIMIT {self.limit(c.limit)}")
            else:
                raise Unsupported(f"{type(c).__name__} before the end of a query")
        return out, distinct

    def graph_table(self, statements: list[str], ret: str) -> str:
        return f"GRAPH_TABLE({self.ns.graph} " + " ".join(statements + [ret]) + ")"

    def read(self, clauses: list, ret: Project) -> Plan:
        statements, distinct = self.body(clauses)
        if distinct and not ret.distinct:
            raise Unsupported("WITH DISTINCT followed by a RETURN that is not DISTINCT")
        horizontal = self.paths and any(x[0] == "call" and x[1] == "properties" for i in ret.items for x in walk(i.expr))
        items: list[tuple[str, Item]] = []
        lets: list[str] = []
        for n, item in enumerate(ret.items):
            if item.expr[0] == "var" and item.expr[1] not in self.lets:
                raise Unsupported(f"RETURN of the element {item.expr[1]}")
            text = self.expr(item.expr)
            if horizontal and any(x[0] == "call" and x[1] == "properties" for x in walk(item.expr)):
                lets.append(f"LET h{n} = {text}")
                text = f"h{n}"
            items.append((text, item))
        statements += lets
        agg = any(aggregated(i.expr) for i in ret.items) and not horizontal
        columns = [Column(f"c{n}", ladybug_name(i), empty_is_null=self.empties(i.expr)) for n, (_, i) in enumerate(items)]
        rendered = [t for t, _ in items]
        group: list[str] = []
        if agg:
            plain = [(n, i) for n, (_, i) in enumerate(items) if not aggregated(i.expr)]
            arrays = any(self.is_array(i.expr) for _, i in plain)
            if arrays:
                owners = sorted({x[1] for _, i in plain for x in walk(i.expr) if x[0] == "prop"})
                group = [self.key_of(v) for v in owners]
                for n, _ in plain:
                    rendered[n] = f"ANY_VALUE({rendered[n]})"
            else:
                group = [rendered[n] for n, _ in plain]
        hidden: list[str] = []
        order_terms = []
        for o in ret.order:
            at = self.find(o, ret.items)
            if at is None:
                if agg or ret.distinct:
                    raise Unsupported(f"ORDER BY {o.text}, which the aggregated or DISTINCT RETURN does not return")
                at = f"o{len(hidden)}"
                hidden.append(f"{self.expr(o.expr)} AS {at}")
            order_terms.append(self.order_term(at, o.desc))
        returned = [f"{r} AS c{n}" for n, r in enumerate(rendered)] + hidden
        ret_text = f"RETURN {'DISTINCT ' if ret.distinct else ''}" + ", ".join(returned)
        if group:
            ret_text += " GROUP BY " + ", ".join(group)
        if ret.limit is not None:
            if order_terms:
                ret_text += " ORDER BY " + ", ".join(order_terms)
            ret_text += f" LIMIT {self.limit(ret.limit)}"
        sql = f"SELECT {', '.join(c.alias for c in columns)} FROM {self.graph_table(statements, ret_text)}"
        if order_terms:
            sql += " ORDER BY " + ", ".join(order_terms)
        return self.done(Plan("rows", sql, columns=columns))

    def empties(self, e) -> bool:
        """Whether an empty array in this column is Ladybug's NULL: a collect over nothing, or a vector property."""
        if e[0] == "call" and e[1] == "collect":
            return True
        if e[0] == "prop":
            return (self.prop_type(e[1], e[2]) or "").upper().startswith("FLOAT[")
        return False

    def is_array(self, e) -> bool:
        return e[0] == "prop" and (self.prop_type(e[1], e[2]) or "").endswith("]")

    @staticmethod
    def find(o: Order, items: list[Item]) -> str | None:
        for n, i in enumerate(items):
            if (o.expr[0] == "var" and i.alias == o.expr[1]) or i.text == o.text or i.expr == o.expr:
                return f"c{n}"
        return None

    def source(self, clauses: list, keys: list[tuple[str, str]]) -> str:
        """A GRAPH_TABLE that returns the keys (expression, alias) of what the clauses match."""
        statements, distinct = self.body(clauses)
        ret = f"RETURN {'DISTINCT ' if distinct else ''}" + ", ".join(f"{k} AS {a}" for k, a in keys)
        return self.graph_table(statements, ret)

    def value(self, e, cypher_type: str | None) -> str:
        """A written value: a parameter, a literal or NULL (an array's NULL is the empty array BigQuery keeps)."""
        t = bq_type(cypher_type) if cypher_type else None
        if e[0] == "lit" and e[1] is None and t and t.startswith("ARRAY"):
            return f"CAST([] AS {t})"
        if e[0] == "param":
            return self.param(e[1], t)
        if e[0] in ("lit", "cast"):
            return self.expr(e)
        raise Unsupported("a written value that is not a parameter or a literal")

    def create_nodes(self, clauses: list) -> Plan:
        statements = []
        for c in clauses:
            if not isinstance(c, Create):
                raise Unsupported("CREATE after anything but MATCH")
            for path in c.paths:
                if len(path.parts) != 1:
                    raise Unsupported("CREATE of a path with no MATCH")
                n = path.parts[0]
                table = self.schema.node(n.label)
                for k, _ in n.props:
                    if k not in table.props:
                        raise RuntimeError(f"Binder exception: Cannot find property {k} for {n.label}.")
                cols = ", ".join(quote(k) for k, _ in n.props)
                vals = ", ".join(self.value(v, table.props[k]) for k, v in n.props)
                statements.append(f"INSERT INTO {self.ns.table(table.table)} ({cols}) VALUES ({vals})")
        return self.done(Plan("write", script(statements)))

    def create_edges(self, clauses: list, create: Create) -> Plan:
        statements = []
        for path in create.paths:
            parts = path.parts
            for at in range(1, len(parts), 2):
                rel, a, b = parts[at], parts[at - 1], parts[at + 1]
                if rel.direction == "-" or not rel.type or a.var is None or b.var is None:
                    raise Unsupported("CREATE of anything but a directed edge between matched nodes")
                src, dst = (a, b) if rel.direction == "->" else (b, a)
                sl, dl = self.labels.get(src.var), self.labels.get(dst.var)
                tables = [e for e in self.schema.edge(rel.type) if e.src == sl and e.dst == dl]
                if len(tables) != 1:
                    raise RuntimeError(f"Binder exception: {rel.type} does not join {sl} to {dl}.")
                table = tables[0]
                props = rel.props
                cols = [EDGE_ID, SRC, DST] + [k for k, _ in props]
                vals = ["GENERATE_UUID()", "s", "d"] + [self.value(v, table.props.get(k)) for k, v in props]
                src_ = self.source(clauses, [(self.key_of(src.var), "s"), (self.key_of(dst.var), "d")])
                statements.append(f"INSERT INTO {self.ns.table(table.table)} ({', '.join(quote(c) for c in cols)}) "
                                  f"SELECT {', '.join(vals)} FROM {src_}")
        return self.done(Plan("write", script(statements)))

    def merge(self, clauses: list) -> Plan:
        m = clauses[0]
        if len(m.path.parts) != 1:
            raise Unsupported("MERGE of a path")
        n = m.path.parts[0]
        table = self.schema.node(n.label)
        if [k for k, _ in n.props] != [table.key]:
            raise Unsupported("MERGE on anything but the node's key")
        sets = []
        for c in clauses[1:]:
            if not isinstance(c, SetProps):
                raise Unsupported("MERGE followed by anything but SET")
            sets += c.items
        if any(var != n.var for var, _, _ in sets):
            raise Unsupported("SET of another variable after MERGE")
        key_value = self.value(n.props[0][1], table.props[table.key])
        assigns = [(quote(p), self.value(v, table.props.get(p))) for _, p, v in sets]
        sql = (f"MERGE {self.ns.table(table.table)} AS t USING (SELECT {key_value} AS k) AS s ON t.{quote(table.key)} = s.k"
               + (" WHEN MATCHED THEN UPDATE SET " + ", ".join(f"{c} = {v}" for c, v in assigns) if assigns else "")
               + f" WHEN NOT MATCHED THEN INSERT ({', '.join([quote(table.key)] + [c for c, _ in assigns])}) "
               + f"VALUES ({', '.join(['s.k'] + [v for _, v in assigns])})")
        return self.done(Plan("write", sql))

    def update(self, clauses: list, sets: SetProps) -> Plan:
        owners = {var for var, _, _ in sets.items}
        if len(owners) != 1:
            raise Unsupported("SET on more than one variable")
        var = owners.pop()
        if var not in self.labels:
            raise Unsupported("SET on anything but a labelled node")
        table = self.schema.node(self.labels[var])
        for _, p, _ in sets.items:
            if p not in table.props:
                raise RuntimeError(f"Binder exception: Cannot find property {p} for {table.label}.")
        assigns = ", ".join(f"{quote(p)} = {self.value(v, table.props[p])}" for _, p, v in sets.items)
        src = self.source(clauses, [(self.key_of(var), "k")])
        sql = f"UPDATE {self.ns.table(table.table)} SET {assigns} WHERE {quote(table.key)} IN (SELECT k FROM {src})"
        return self.done(Plan("write", sql))

    def delete(self, clauses: list, d: Delete) -> Plan:
        if len(d.vars) != 1:
            raise Unsupported("DELETE of more than one variable")
        var = d.vars[0]
        if var in self.types:
            if d.detach:
                raise Unsupported("DETACH DELETE of an edge")
            src = self.source(clauses, [(self.key_of(var), "k")])
            ends = self.edge_ends(clauses, var)
            tables = [e for e in self.schema.edge(self.types[var]) if (ends[0] in (None, e.src)) and (ends[1] in (None, e.dst))]
            statements = [f"DELETE FROM {self.ns.table(e.table)} WHERE {quote(EDGE_ID)} IN (SELECT k FROM {src})" for e in tables]
            return self.done(Plan("write", script(statements)))
        if var not in self.labels or not d.detach:
            raise Unsupported("DELETE of a node without DETACH, or of an unlabelled one")
        table = self.schema.node(self.labels[var])
        key_type = bq_type(table.props[table.key])
        src = self.source(clauses, [(self.key_of(var), "k")])
        statements = [f"DELETE FROM {self.ns.table(e.table)} WHERE " + " OR ".join(f"{quote(c)} IN UNNEST(doomed)" for c in cols)
                      for e, cols in self.schema.touching(table.label)]
        statements.append(f"DELETE FROM {self.ns.table(table.table)} WHERE {quote(table.key)} IN UNNEST(doomed)")
        head = f"DECLARE doomed ARRAY<{key_type}> DEFAULT (SELECT ARRAY_AGG(DISTINCT k) FROM {src});"
        return self.done(Plan("write", head + " " + script(statements, force=True)))

    def edge_ends(self, clauses: list, var: str) -> tuple[str | None, str | None]:
        """The labels at the source and destination of the edge var, as the MATCH that binds it says."""
        for c in clauses:
            for path in getattr(c, "paths", []):
                for at in range(1, len(path.parts), 2):
                    rel = path.parts[at]
                    if rel.var == var:
                        a, b = path.parts[at - 1], path.parts[at + 1]
                        la = a.label or self.labels.get(a.var or "")
                        lb = b.label or self.labels.get(b.var or "")
                        return (la, lb) if rel.direction == "->" else (lb, la) if rel.direction == "<-" else (None, None)
        return None, None

    def table_info(self, call: TableInfo) -> Plan:
        table = self.schema.node(call.table)
        if [i.text for i in call.items] != ["name"]:
            raise Unsupported("CALL table_info returning anything but name")
        self.values = {**self.values, "table_info_table": f"{self.ns.prefix}{table.table}"}
        sql = (f"SELECT column_name AS c0 FROM {self.ns.information_schema} WHERE table_name = "
               f"{self.param('table_info_table', 'STRING')} ORDER BY ordinal_position")
        return self.done(Plan("rows", sql, columns=[Column("c0", "name")]))


def script(statements: list[str], force: bool = False) -> str:
    """One statement as it is; several as one transaction."""
    if len(statements) == 1 and not force:
        return statements[0]
    return "BEGIN TRANSACTION; " + " ".join(s + ";" for s in statements) + " COMMIT TRANSACTION;"


def ddl(schema: Schema, ns: Namespace) -> list[str]:
    """DROP PROPERTY GRAPH IF EXISTS (a table a property graph names may not be replaced under it), CREATE OR REPLACE
    TABLE for every node and edge table of schema, then CREATE OR REPLACE PROPERTY GRAPH over them, in ns: each node table keyed by its key and labelled with its type, each edge table by edge_id, its ends' keys
    referencing their node tables, labelled with its type (a rel group's tables share one label)."""
    out = [f"DROP PROPERTY GRAPH IF EXISTS {ns.graph}"]
    for n in schema.nodes.values():
        cols = ", ".join(f"{quote(p)} {bq_type(t)}" for p, t in n.props.items())
        out.append(f"CREATE OR REPLACE TABLE {ns.table(n.table)} ({cols})")
    for tables in schema.edges.values():
        for e in tables:
            src_t = bq_type(schema.node(e.src).props[schema.node(e.src).key])
            dst_t = bq_type(schema.node(e.dst).props[schema.node(e.dst).key])
            cols = ", ".join([f"{quote(EDGE_ID)} STRING", f"{quote(SRC)} {src_t}", f"{quote(DST)} {dst_t}"]
                             + [f"{quote(p)} {bq_type(t)}" for p, t in e.props.items()])
            out.append(f"CREATE OR REPLACE TABLE {ns.table(e.table)} ({cols})")
    nodes = [f"{ns.table(n.table)} AS {quote(n.table)} KEY ({quote(n.key)}) LABEL {quote(n.label)} "
             f"PROPERTIES ({', '.join(quote(p) for p in n.props)})" for n in schema.nodes.values()]
    edges = []
    for tables in schema.edges.values():
        for e in tables:
            props = ", ".join(quote(p) for p in [EDGE_ID, *e.props])
            edges.append(f"{ns.table(e.table)} AS {quote(e.table)} KEY ({quote(EDGE_ID)}) "
                         f"SOURCE KEY ({quote(SRC)}) REFERENCES {quote(e.src)} ({quote(schema.node(e.src).key)}) "
                         f"DESTINATION KEY ({quote(DST)}) REFERENCES {quote(e.dst)} ({quote(schema.node(e.dst).key)}) "
                         f"LABEL {quote(e.type)} PROPERTIES ({props})")
    out.append(f"CREATE OR REPLACE PROPERTY GRAPH {ns.graph} NODE TABLES ({', '.join(nodes)}) EDGE TABLES ({', '.join(edges)})")
    return out


def encode(name: str, bq: str, value: Any) -> dict:
    """A named query parameter of BigQuery type bq, as the REST API takes it; None is a typed NULL."""
    if bq.startswith("ARRAY<"):
        inner = bq[6:-1]
        ptype = {"type": "ARRAY", "arrayType": {"type": inner}}
        items = [] if value is None else list(value)
        return {"name": name, "parameterType": ptype,
                "parameterValue": {"arrayValues": [{"value": scalar(inner, x)} for x in items]}}
    return {"name": name, "parameterType": {"type": bq},
            "parameterValue": {} if value is None else {"value": scalar(bq, value)}}


def scalar(bq: str, value: Any) -> str:
    if bq == "BOOL":
        return "true" if value else "false"
    if bq == "FLOAT64":
        return repr(float(value))
    if bq == "INT64":
        return str(int(value))
    if not isinstance(value, str):
        raise Unsupported(f"the value {value!r} for a STRING parameter")
    return value


def decode(field: dict, value: Any) -> Any:
    """One cell of a REST result, by its schema field: INT64, FLOAT64 and BOOL come back as text."""
    if field.get("mode") == "REPEATED":
        inner = {**field, "mode": "NULLABLE"}
        return [decode(inner, x.get("v") if isinstance(x, dict) else x) for x in (value or [])]
    if value is None:
        return None
    t = field.get("type", "STRING").upper()
    if t in ("INTEGER", "INT64"):
        return int(value)
    if t in ("FLOAT", "FLOAT64", "NUMERIC", "BIGNUMERIC"):
        return float(value)
    if t in ("BOOLEAN", "BOOL"):
        return value in (True, "true", "TRUE", "1")
    if t in ("RECORD", "STRUCT"):
        fields = field.get("fields") or []
        return {f["name"]: decode(f, c.get("v")) for f, c in zip(fields, value.get("f", []))}
    if t == "JSON":
        return json.loads(value)
    return value


def rows(data: dict, columns: list[Column]) -> tuple[list[str], list[list]]:
    """A result's rows, decoded, under the names Cypher gives its columns."""
    fields = data.get("schema", {}).get("fields", [])
    at = {f["name"]: n for n, f in enumerate(fields)}
    names = [c.name for c in columns]
    out = []
    for r in data.get("rows", []):
        cells = r.get("f", [])
        row = []
        for c in columns:
            n = at[c.alias]
            v = decode(fields[n], cells[n].get("v") if n < len(cells) else None)
            if c.empty_is_null and v == []:
                v = None
            row.append(v)
        out.append(row)
    return names, out



@dataclass
class Group:
    """Writes of one kind on one table sent as one statement: kind and label as Op's, the ends of an edge, the struct
    fields of each row of the parameter param (the key _key, an edge's ends _src and _dst, then its properties), and
    for rows (an artifact's file) the file."""
    kind: str
    label: str
    src: str | None
    dst: str | None
    param: str
    fields: list[tuple[str, str]]
    rows: list[list]
    path: Any = None
    data: Any = None


def groups(schema: Schema, ops: list) -> list[Group]:
    """ops (kge/adapters/graphstore/base.py Op), gathered as the statements they make: by phase, then each kind and
    table (and set of properties) in the order first written. Two upserts or updates of one node are one row, the later
    property winning; a node deleted is deleted once."""
    out: dict[tuple, Group] = {}
    seen: dict[tuple, int] = {}
    rows_at: dict[tuple, dict] = {}
    for n, op in enumerate(ops):
        if op.kind in ("upsert", "update"):
            at = (op.kind, op.label, op.key)
            if at in rows_at:
                rows_at[at].update(op.props)
                continue
            rows_at[at] = dict(op.props)
            seen.setdefault(at, n)
    order = []
    for n, op in enumerate(ops):
        if op.kind in ("upsert", "update"):
            at = (op.kind, op.label, op.key)
            if seen.get(at) != n:
                continue
            props = rows_at[at]
            table = schema.node(op.label)
            fields = [("_key", bq_type(table.props[table.key]))] + [(p, bq_type(table.props[p])) for p in props]
            gkey = (op.phase, op.kind, op.label, None, None, tuple(props))
            row = [op.key, *props.values()]
        elif op.kind == "create_node":
            table = schema.node(op.label)
            fields = [(p, bq_type(table.props[p])) for p in op.props]
            gkey = (op.phase, op.kind, op.label, None, None, tuple(op.props))
            row = list(op.props.values())
        elif op.kind == "delete_node":
            table = schema.node(op.label)
            fields = [("_key", bq_type(table.props[table.key]))]
            gkey = (op.phase, op.kind, op.label, None, None, ())
            row = [op.key]
        elif op.kind == "delete_edges":
            node = schema.node(op.src)
            fields = [("_key", bq_type(node.props[node.key]))]
            gkey = (op.phase, op.kind, op.label, op.src, op.dst, ())
            row = [op.key]
        elif op.kind == "create_edge":
            edge = edge_table(schema, op.label, op.src, op.dst)
            s, d = schema.node(op.src), schema.node(op.dst)
            fields = [("_src", bq_type(s.props[s.key])), ("_dst", bq_type(d.props[d.key]))] + \
                     [(p, bq_type(edge.props[p])) for p in op.props]
            gkey = (op.phase, op.kind, op.label, op.src, op.dst, tuple(op.props))
            row = [op.key, op.dst_key, *op.props.values()]
        elif op.kind == "rows":
            gkey = (op.phase, op.kind, op.label, op.src, op.dst, n)
            fields, row = [], None
        else:
            raise Unsupported(f"the write {op.kind}")
        if gkey not in out:
            out[gkey] = Group(op.kind, op.label, op.src, op.dst, "", fields, [], getattr(op, "path", None), getattr(op, "data", None))
            order.append(gkey)
        if row is not None and not (op.kind in ("delete_node", "delete_edges") and row in out[gkey].rows):
            out[gkey].rows.append(row)
    ranked = sorted(order, key=lambda g: (g[0], order.index(g)))
    result = []
    for n, g in enumerate(ranked):
        group = out[g]
        group.param = f"w{n}"
        result.append(group)
    return result


def edge_table(schema: Schema, type_: str, src: str, dst: str) -> EdgeTable:
    tables = [e for e in schema.edge(type_) if e.src == src and e.dst == dst]
    if len(tables) != 1:
        raise RuntimeError(f"Binder exception: {type_} does not join {src} to {dst}.")
    return tables[0]


def batch_sql(schema: Schema, ns: Namespace, group: Group) -> list[str]:
    """The statements one group of writes makes, over its rows in UNNEST(@<param>)."""
    rows = f"UNNEST(@{group.param})"
    if group.kind == "delete_edges":
        tables = [e for e in schema.edge(group.label) if e.src == group.src and (group.dst is None or e.dst == group.dst)]
        return [f"DELETE FROM {ns.table(e.table)} WHERE {quote(SRC)} IN (SELECT _key FROM {rows})" for e in tables]
    if group.kind == "delete_node":
        node = schema.node(group.label)
        out = [f"DELETE FROM {ns.table(e.table)} WHERE " + " OR ".join(f"{quote(c)} IN (SELECT _key FROM {rows})" for c in cols)
               for e, cols in schema.touching(node.label)]
        return out + [f"DELETE FROM {ns.table(node.table)} WHERE {quote(node.key)} IN (SELECT _key FROM {rows})"]
    if group.kind == "create_node":
        cols = [quote(f) for f, _ in group.fields]
        return [f"INSERT INTO {ns.table(schema.node(group.label).table)} ({', '.join(cols)}) "
                f"SELECT {', '.join(f'r.{c}' for c in cols)} FROM {rows} AS r"]
    if group.kind in ("upsert", "update"):
        node = schema.node(group.label)
        props = [quote(f) for f, _ in group.fields[1:]]
        sql = (f"MERGE {ns.table(node.table)} AS t USING (SELECT * FROM {rows}) AS s ON t.{quote(node.key)} = s._key"
               + (" WHEN MATCHED THEN UPDATE SET " + ", ".join(f"{c} = s.{c}" for c in props) if props else ""))
        if group.kind == "upsert":
            sql += (f" WHEN NOT MATCHED THEN INSERT ({', '.join([quote(node.key), *props])}) "
                    f"VALUES ({', '.join(['s._key', *(f's.{c}' for c in props)])})")
        return [sql]
    if group.kind == "create_edge":
        edge = edge_table(schema, group.label, group.src, group.dst)
        s, d = schema.node(group.src), schema.node(group.dst)
        props = [quote(f) for f, _ in group.fields[2:]]
        return [f"INSERT INTO {ns.table(edge.table)} ({', '.join([quote(EDGE_ID), quote(SRC), quote(DST), *props])}) "
                f"SELECT {', '.join(['GENERATE_UUID()', 'r._src', 'r._dst', *(f'r.{c}' for c in props)])} FROM {rows} AS r "
                f"WHERE r._src IN (SELECT {quote(s.key)} FROM {ns.table(s.table)}) "
                f"AND r._dst IN (SELECT {quote(d.key)} FROM {ns.table(d.table)})"]
    if group.kind == "rows":
        if group.label in schema.nodes:
            cols = [quote(f) for f, _ in group.fields]
            return [f"INSERT INTO {ns.table(schema.nodes[group.label].table)} ({', '.join(cols)}) "
                    f"SELECT {', '.join(f'r.{c}' for c in cols)} FROM {rows} AS r"]
        edge = edge_table(schema, group.label, group.src, group.dst)
        props = [quote(f) for f, _ in group.fields[2:]]
        return [f"INSERT INTO {ns.table(edge.table)} ({', '.join([quote(EDGE_ID), quote(SRC), quote(DST), *props])}) "
                f"SELECT {', '.join(['GENERATE_UUID()', 'r._src', 'r._dst', *(f'r.{c}' for c in props)])} FROM {rows} AS r"]
    raise Unsupported(f"the write {group.kind}")


def struct_rows(group: Group) -> dict:
    """The group's rows as its ARRAY<STRUCT<...>> query parameter, each field typed; None a NULL, and an array's NULL
    the empty array BigQuery keeps."""
    def one(t: str, v: Any) -> dict:
        if t.startswith("ARRAY<"):
            inner = t[6:-1]
            return {"arrayValues": [{"value": scalar(inner, x)} for x in (v or [])]}
        return {} if v is None else {"value": scalar(t, v)}

    def kind(t: str) -> dict:
        return {"type": "ARRAY", "arrayType": {"type": t[6:-1]}} if t.startswith("ARRAY<") else {"type": t}
    return {"name": group.param,
            "parameterType": {"type": "ARRAY", "arrayType": {"type": "STRUCT", "structTypes": [
                {"name": f, "type": kind(t)} for f, t in group.fields]}},
            "parameterValue": {"arrayValues": [{"structValues": {f: one(t, v) for (f, t), v in zip(group.fields, r)}}
                                               for r in group.rows]}}
