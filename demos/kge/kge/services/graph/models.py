"""The graph read's contract, shared by its route and its MCP tool (cerebro.md §5.1). It carries no figures: no record
count, size, score or value from the data, on any node or link."""
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, SerializerFunctionWrapHandler, model_serializer

NodeKind = Literal["term", "table", "column", "document", "repository", "community", "person", "domain", "join"]
LinkStatus = Literal["certified", "proposed", "inferred"]


class Body(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Node(Body):
    """A node, its id unique across kinds (<kind>:<identifier>) since a link's ends name no kind."""
    kind: NodeKind
    id: str
    name: str


class Link(Body):
    """A link between two nodes of the same page or of others, with exactly one status and the reason it exists."""
    source: str
    target: str
    kind: str
    status: LinkStatus
    why: str


class GraphPage(Body):
    """A page of nodes, and the links whose source is one of them. cursor names the next page, absent on the last."""
    nodes: list[Node]
    links: list[Link]
    cursor: str | None = None


class ExploreQuestion(Body):
    """POST /graph/explore's body: the question, sent in a body so that no URL, and no access log, carries it."""
    question: Annotated[str, Field(min_length=1, max_length=2000)]


class Sparse(Body):
    """A reply part whose optional fields are absent where they do not apply, on every transport alike: the route's
    JSON, the tool's structured content and its text (cerebro.md §5.3)."""

    @model_serializer(mode="wrap")
    def _sparse(self, handler: SerializerFunctionWrapHandler):
        held = handler(self)
        return {k: v for k, v in held.items() if v is not None or k not in self.optional} if isinstance(held, dict) else held

    @property
    def optional(self) -> frozenset[str]:
        return frozenset(name for name, field in type(self).model_fields.items() if not field.is_required() and field.default is None)


class Hop(Sparse):
    """One join hop of a figure's path, as the graph declares it: its kind, the tables it joins (business words, physical
    names for an engineer), and its cardinality, type and validity window, from the pack's join options where declared
    (declared names which) and otherwise what SQL generation assumes. order and use are a context's: always (a join the
    context always renders), question (a reach this question's dimensions or conditions need) or available (a reach
    rendered only when a question groups or filters by what it reaches); a join explored on its own has neither."""
    id: str
    kind: str
    source: str
    target: str
    cardinality: str
    type: str
    window: str | None = None
    declared: list[str]
    said: str
    order: int | None = None
    use: Literal["always", "question", "available"] | None = None
    condition: str | None = None
    warning: str | None = None


class Level(Sparse):
    """A level or member of a hierarchy: a term or a phrase, the code that identifies it in the data where one is
    declared, the column it is read on (business words, the physical column for an engineer), the terms it sits under
    (BROADER) and the classes it is one of (IS_A), each where it has them."""
    name: str
    code: str | None = None
    column: str | None = None
    broader: str | None = None
    of: str | None = None


class Dimension(Sparse):
    """A dimension a path reaches: a calendar roll-up, a hierarchy (its levels and codes) or a source-system
    cross-reference, by the hop that reaches it."""
    kind: Literal["calendar", "hierarchy", "cross_reference"]
    via: str
    name: str
    said: str
    levels: list[Level] = Field(default_factory=list)


class Condition(Sparse):
    """A standing filter on a table's rows, or a row selector: an exclusion leaves out a fact row a row of its table
    matches, an inclusion keeps only those one matches; or a condition the question states (asked), on the column the
    pack's vocabulary names for its words. predicate is an engineer's."""
    kind: Literal["filter", "exclusion", "inclusion", "asked"]
    id: str
    label: str
    on: str
    predicate: str | None = None


class TimeRule(Sparse):
    """A time rule on a table of the path: its versions (scd2), the months it releases (release_driver), or the date a
    context reads a table's version at (version_date)."""
    kind: Literal["scd2", "release_driver", "version_date"]
    on: str
    said: str


class Measure(Sparse):
    """How a figure is aggregated: the measure's term, its aggregation and grain, and its currency and whether it is a
    snapshot where declared; its expression an engineer's."""
    name: str
    aggregation: str
    grain: str
    currency: str | None = None
    snapshot: bool | None = None
    expression: str | None = None


class ExplainedPath(Sparse):
    """A question's whole path for one context its term resolves to, in order, from what the graph holds: the term and
    the words that name it (basis), its definition, the context, fact table and measure (for an operand of a derived
    measure or a count metric the question names, part_of says which and how), each join hop in the context's
    order, the dimensions reached, the filters and exclusions, the time rules and a plain warning per one-to-many hop.
    No figure: figures come only from Ask."""
    term: str
    basis: str
    definition: str
    context: str
    context_id: str
    part_of: str | None = None
    unit: str
    fact: str
    measure: Measure
    hops: list[Hop]
    dimensions: list[Dimension]
    conditions: list[Condition]
    time_rules: list[TimeRule]
    warnings: list[str]


class Explore(Sparse):
    """A node, a short summary of its neighbourhood with no figures, and a deep link that opens the graph on it. For a
    question, node is the one it names best, absent when it names none, and the link then opens the graph on no node.
    For a question whose term resolves to a context the person may read, paths explains each such path in order; for a
    hierarchy term, members lists its members, narrower and broader terms and codes; for a join (join:<id>), hop gives
    its facts. Each is absent where it does not apply."""
    node: Node | None = None
    summary: str
    link: str
    paths: list[ExplainedPath] | None = None
    members: list[Level] | None = None
    hop: Hop | None = None
