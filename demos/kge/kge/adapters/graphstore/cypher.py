"""The Cypher the demo's code sends a graph store, parsed: the subset its queries use, and nothing more, so the BigQuery
store (bigquery.py) can give each one its BigQuery form (gql.py). A construct outside the subset is refused here,
naming it and the query, never guessed at.

The subset: MATCH and OPTIONAL MATCH over paths of node and relationship patterns (a label or type, an inline property
map, a direction or none, a variable-length SHORTEST hop range on a named path), WHERE, WITH (variables and named
expressions, DISTINCT, ORDER BY, LIMIT), RETURN (DISTINCT, aliases, ORDER BY, LIMIT), CREATE, MERGE, SET, DELETE and
DETACH DELETE, CALL table_info, and the DDL a load sends (CREATE NODE or REL TABLE, IF NOT EXISTS). Expressions:
literals, $parameters, lists, property access, AND, OR, NOT, comparisons, IN, IS [NOT] NULL, arithmetic, CASE WHEN,
CAST, EXISTS { MATCH ... } and function calls, count(*) among them."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any


class Unsupported(ValueError):
    """A query, or a construct in one, outside the subset the BigQuery store translates."""


TOKEN = re.compile(r"""
    (?P<ws>\s+)
  | (?P<string>'(?:[^'\\]|\\.)*')
  | (?P<number>\d+\.\d+(?![.\d])|\d+)
  | (?P<param>\$[A-Za-z_]\w*)
  | (?P<quoted>`[^`]+`)
  | (?P<name>[A-Za-z_]\w*)
  | (?P<op>->|<-|<>|<=|>=|\.\.|[-()\[\]{},.:*=<>+/%|])
""", re.X)
KEYWORDS = {"MATCH", "OPTIONAL", "WHERE", "WITH", "RETURN", "ORDER", "BY", "LIMIT", "SKIP", "CREATE", "MERGE", "SET",
            "DELETE", "DETACH", "CALL", "AS", "DISTINCT", "AND", "OR", "XOR", "NOT", "IN", "IS", "NULL", "TRUE", "FALSE",
            "CASE", "WHEN", "THEN", "ELSE", "END", "EXISTS", "ASC", "DESC", "ASCENDING", "DESCENDING", "SHORTEST",
            "UNWIND", "STARTS", "ENDS", "CONTAINS", "CAST", "UNION"}
CLAUSES = {"MATCH", "OPTIONAL", "WHERE", "WITH", "RETURN", "ORDER", "LIMIT", "SKIP", "CREATE", "MERGE", "SET", "DELETE",
           "DETACH", "CALL", "UNWIND", "UNION"}


@dataclass
class Tok:
    kind: str
    text: str
    start: int

    @property
    def upper(self) -> str:
        return self.text.upper() if self.kind == "name" else self.text


def tokens(text: str) -> list[Tok]:
    out, at = [], 0
    while at < len(text):
        m = TOKEN.match(text, at)
        if not m:
            raise Unsupported(f"cannot read {text[at:at + 20]!r} at {at}")
        if m.lastgroup != "ws":
            out.append(Tok(m.lastgroup, m.group(), at))
        at = m.end()
    out.append(Tok("end", "", len(text)))
    return out


@dataclass
class Node:
    var: str | None
    label: str | None
    props: list[tuple[str, Any]] = field(default_factory=list)


@dataclass
class Rel:
    var: str | None
    type: str | None
    direction: str
    props: list[tuple[str, Any]] = field(default_factory=list)
    hops: tuple[int, int] | None = None
    shortest: bool = False


@dataclass
class Path:
    name: str | None
    parts: list


@dataclass
class Match:
    optional: bool
    paths: list[Path]
    where: Any = None


@dataclass
class Item:
    expr: Any
    alias: str | None
    text: str


@dataclass
class Order:
    expr: Any
    desc: bool
    text: str


@dataclass
class Project:
    """WITH or RETURN."""
    kind: str
    distinct: bool
    items: list[Item]
    order: list[Order] = field(default_factory=list)
    limit: Any = None


@dataclass
class Create:
    paths: list[Path]


@dataclass
class Merge:
    path: Path


@dataclass
class SetProps:
    items: list[tuple[str, str, Any]]


@dataclass
class Delete:
    detach: bool
    vars: list[str]


@dataclass
class TableInfo:
    table: str
    items: list[Item]


@dataclass
class Ddl:
    kind: str
    name: str
    if_not_exists: bool


@dataclass
class Query:
    text: str
    clauses: list


class Parser:
    def __init__(self, text: str):
        self.text = text
        self.toks = tokens(text)
        self.i = 0

    def peek(self, n: int = 0) -> Tok:
        return self.toks[min(self.i + n, len(self.toks) - 1)]

    def next(self) -> Tok:
        t = self.toks[self.i]
        self.i += 1
        return t

    def at(self, *words: str) -> bool:
        return all(self.peek(n).upper == w for n, w in enumerate(words))

    def accept(self, *words: str) -> bool:
        if self.at(*words):
            self.i += len(words)
            return True
        return False

    def expect(self, word: str) -> Tok:
        t = self.next()
        if t.upper != word:
            raise Unsupported(f"expected {word} at {t.start}, found {t.text!r}")
        return t

    def name(self) -> str:
        t = self.next()
        if t.kind == "quoted":
            return t.text[1:-1]
        if t.kind != "name":
            raise Unsupported(f"expected a name at {t.start}, found {t.text!r}")
        return t.text

    def query(self) -> Query:
        clauses = []
        while self.peek().kind != "end":
            clauses.append(self.clause())
        if not clauses:
            raise Unsupported("an empty query")
        return Query(self.text, clauses)

    def clause(self):
        if self.at("CREATE", "NODE") or self.at("CREATE", "REL"):
            return self.ddl()
        if self.accept("OPTIONAL", "MATCH"):
            return self.match(True)
        if self.accept("MATCH"):
            return self.match(False)
        if self.at("WITH") or self.at("RETURN"):
            return self.project()
        if self.accept("CREATE"):
            return Create(self.paths())
        if self.accept("MERGE"):
            return Merge(self.path())
        if self.accept("SET"):
            return self.sets()
        if self.accept("DETACH", "DELETE"):
            return Delete(True, self.var_list())
        if self.accept("DELETE"):
            return Delete(False, self.var_list())
        if self.accept("CALL"):
            return self.call()
        raise Unsupported(f"the clause {self.peek().text!r} at {self.peek().start}")

    def ddl(self) -> Ddl:
        self.expect("CREATE")
        kind = self.name().upper()
        self.expect("TABLE")
        exists = self.accept("IF", "NOT", "EXISTS")
        name = self.name()
        self.i = len(self.toks) - 1
        return Ddl(kind, name, exists)

    def call(self) -> TableInfo:
        proc = self.name().lower()
        if proc != "table_info":
            raise Unsupported(f"CALL {proc}")
        self.expect("(")
        t = self.next()
        if t.kind != "string":
            raise Unsupported("CALL table_info takes the table's name as a literal")
        self.expect(")")
        ret = self.project()
        if ret.kind != "RETURN":
            raise Unsupported("CALL table_info without RETURN")
        return TableInfo(literal(t.text), ret.items)

    def var_list(self) -> list[str]:
        out = [self.name()]
        while self.accept(","):
            out.append(self.name())
        return out

    def sets(self) -> SetProps:
        items = []
        while True:
            var = self.name()
            self.expect(".")
            prop = self.name()
            self.expect("=")
            items.append((var, prop, self.expr()))
            if not self.accept(","):
                return SetProps(items)

    def match(self, optional: bool) -> Match:
        paths = self.paths()
        where = self.expr() if self.accept("WHERE") else None
        return Match(optional, paths, where)

    def paths(self) -> list[Path]:
        out = [self.path()]
        while self.accept(","):
            out.append(self.path())
        return out

    def path(self) -> Path:
        name = None
        if self.peek().kind == "name" and self.peek(1).text == "=":
            name = self.name()
            self.next()
        parts = [self.node()]
        while self.peek().text in ("-", "<-"):
            parts.append(self.rel())
            parts.append(self.node())
        return Path(name, parts)

    def node(self) -> Node:
        self.expect("(")
        var = label = None
        if self.peek().kind in ("name", "quoted"):
            var = self.name()
        if self.accept(":"):
            label = self.name()
        props = self.props() if self.peek().text == "{" else []
        self.expect(")")
        return Node(var, label, props)

    def rel(self) -> Rel:
        left = self.next().text
        var = type_ = None
        hops, shortest, props = None, False, []
        if self.accept("["):
            if self.peek().kind in ("name", "quoted") and self.peek().upper not in ("SHORTEST",):
                var = self.name()
            if self.accept(":"):
                type_ = self.name()
            if self.accept("*"):
                shortest = self.accept("SHORTEST")
                lo = int(self.next().text)
                self.expect("..")
                hops = (lo, int(self.next().text))
            if self.peek().text == "{":
                props = self.props()
            self.expect("]")
        right = self.next().text
        if left == "<-" and right == "-":
            direction = "<-"
        elif left == "-" and right == "->":
            direction = "->"
        elif left == "-" and right == "-":
            direction = "-"
        else:
            raise Unsupported(f"the relationship arrow {left}...{right}")
        return Rel(var, type_, direction, props, hops, shortest)

    def props(self) -> list[tuple[str, Any]]:
        self.expect("{")
        out = []
        if not self.accept("}"):
            while True:
                key = self.name()
                self.expect(":")
                out.append((key, self.expr()))
                if self.accept("}"):
                    break
                self.expect(",")
        return out

    def project(self) -> Project:
        kind = self.next().upper
        distinct = self.accept("DISTINCT")
        items = []
        while True:
            if self.accept("*"):
                raise Unsupported(f"{kind} *")
            start = self.peek().start
            e = self.expr()
            text = self.text[start:self.peek().start].strip()
            alias = self.name() if self.accept("AS") else None
            items.append(Item(e, alias, text))
            if not self.accept(","):
                break
        order, limit = [], None
        if self.accept("ORDER", "BY"):
            while True:
                start = self.peek().start
                e = self.expr()
                text = self.text[start:self.peek().start].strip()
                desc = False
                if self.peek().upper in ("DESC", "DESCENDING"):
                    self.next()
                    desc = True
                elif self.peek().upper in ("ASC", "ASCENDING"):
                    self.next()
                order.append(Order(e, desc, text))
                if not self.accept(","):
                    break
        if self.at("SKIP"):
            raise Unsupported("SKIP")
        if self.accept("LIMIT"):
            limit = self.expr()
        if kind == "WITH" and self.at("WHERE"):
            raise Unsupported("WITH ... WHERE")
        return Project(kind, distinct, items, order, limit)

    def expr(self):
        return self.or_()

    def or_(self):
        e = self.and_()
        while self.accept("OR"):
            e = ("or", e, self.and_())
        if self.at("XOR"):
            raise Unsupported("XOR")
        return e

    def and_(self):
        e = self.not_()
        while self.accept("AND"):
            e = ("and", e, self.not_())
        return e

    def not_(self):
        if self.accept("NOT"):
            return ("not", self.not_())
        return self.compare()

    def compare(self):
        e = self.additive()
        while True:
            t = self.peek()
            if t.text in ("=", "<>", "<", ">", "<=", ">="):
                self.next()
                e = ("cmp", t.text, e, self.additive())
            elif self.accept("IN"):
                e = ("in", e, self.additive())
            elif self.accept("IS", "NOT", "NULL"):
                e = ("isnull", e, True)
            elif self.accept("IS", "NULL"):
                e = ("isnull", e, False)
            elif t.upper in ("STARTS", "ENDS", "CONTAINS"):
                raise Unsupported(t.upper)
            else:
                return e

    def additive(self):
        e = self.multiplicative()
        while self.peek().text in ("+", "-"):
            op = self.next().text
            e = ("arith", op, e, self.multiplicative())
        return e

    def multiplicative(self):
        e = self.unary()
        while self.peek().text in ("*", "/", "%"):
            op = self.next().text
            e = ("arith", op, e, self.unary())
        return e

    def unary(self):
        if self.accept("-"):
            return ("neg", self.unary())
        return self.postfix()

    def postfix(self):
        e = self.atom()
        while self.peek().text == "." and self.peek(1).kind in ("name", "quoted"):
            self.next()
            prop = self.name()
            if e[0] != "var":
                raise Unsupported("property access on anything but a variable")
            e = ("prop", e[1], prop)
        return e

    def atom(self):
        t = self.peek()
        if t.kind == "string":
            self.next()
            return ("lit", literal(t.text))
        if t.kind == "number":
            self.next()
            return ("lit", float(t.text) if "." in t.text else int(t.text))
        if t.kind == "param":
            self.next()
            return ("param", t.text[1:])
        if t.text == "(":
            self.next()
            e = self.expr()
            self.expect(")")
            return e
        if t.text == "[":
            self.next()
            items = []
            if not self.accept("]"):
                while True:
                    items.append(self.expr())
                    if self.accept("]"):
                        break
                    self.expect(",")
            return ("list", items)
        if t.text == "{":
            raise Unsupported("a map literal")
        if t.upper in ("TRUE", "FALSE"):
            self.next()
            return ("lit", t.upper == "TRUE")
        if t.upper == "NULL":
            self.next()
            return ("lit", None)
        if t.upper == "CASE":
            return self.case()
        if t.upper == "CAST":
            self.next()
            self.expect("(")
            e = self.expr()
            self.expect("AS")
            start = self.peek().start
            depth = 0
            while depth or self.peek().text != ")":
                depth += {"[": 1, "]": -1}.get(self.next().text, 0)
            target = self.text[start:self.peek().start].strip()
            self.expect(")")
            return ("cast", e, target)
        if t.upper == "EXISTS":
            self.next()
            self.expect("{")
            self.expect("MATCH")
            m = self.match(False)
            self.expect("}")
            return ("exists", m)
        if t.kind in ("name", "quoted"):
            name = self.name()
            if self.peek().text == "(":
                return self.function(name)
            if name.upper() in KEYWORDS:
                raise Unsupported(f"the keyword {name} in an expression")
            return ("var", name)
        raise Unsupported(f"{t.text!r} at {t.start}")

    def case(self):
        self.expect("CASE")
        if not self.at("WHEN"):
            raise Unsupported("CASE <expr> WHEN (the simple form)")
        whens = []
        while self.accept("WHEN"):
            cond = self.expr()
            self.expect("THEN")
            whens.append((cond, self.expr()))
        other = self.expr() if self.accept("ELSE") else ("lit", None)
        self.expect("END")
        return ("case", whens, other)

    def function(self, name: str):
        self.expect("(")
        fname = name.lower()
        if self.accept("*"):
            self.expect(")")
            return ("call", fname, [], False, True)
        distinct = self.accept("DISTINCT")
        args = []
        if not self.accept(")"):
            while True:
                args.append(self.expr())
                if self.accept(")"):
                    break
                self.expect(",")
        return ("call", fname, args, distinct, False)


def literal(text: str) -> str:
    """A Cypher string literal's value."""
    body = text[1:-1]
    return re.sub(r"\\(.)", lambda m: {"n": "\n", "t": "\t"}.get(m.group(1), m.group(1)), body)


def parse(text: str) -> Query:
    """The query, parsed; Unsupported, naming the construct and the query, for anything outside the subset."""
    try:
        return Parser(text).query()
    except Unsupported as exc:
        raise Unsupported(f"{exc}, in the query: {' '.join(text.split())}") from None


AGGREGATES = {"count", "collect", "sum", "min", "max", "avg"}


def walk(e):
    """Every sub-expression of e, e first, EXISTS bodies included."""
    if not isinstance(e, tuple):
        return
    yield e
    kind = e[0]
    if kind in ("and", "or"):
        yield from walk(e[1])
        yield from walk(e[2])
    elif kind == "not" or kind == "neg":
        yield from walk(e[1])
    elif kind in ("cmp", "arith"):
        yield from walk(e[2])
        yield from walk(e[3])
    elif kind == "in":
        yield from walk(e[1])
        yield from walk(e[2])
    elif kind == "isnull":
        yield from walk(e[1])
    elif kind == "list":
        for x in e[1]:
            yield from walk(x)
    elif kind == "call":
        for x in e[2]:
            yield from walk(x)
    elif kind == "case":
        for c, v in e[1]:
            yield from walk(c)
            yield from walk(v)
        yield from walk(e[2])
    elif kind == "cast":
        yield from walk(e[1])
    elif kind == "exists":
        m = e[1]
        for p in m.paths:
            for part in p.parts:
                for _, v in part.props:
                    yield from walk(v)
        yield from walk(m.where)


def aggregated(e) -> bool:
    return any(x[0] == "call" and x[1] in AGGREGATES for x in walk(e))
