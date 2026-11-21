"""The graph read's contract, shared by its route and its MCP tool (cerebro.md §5.1). It carries no figures: no record
count, size, score or value from the data, on any node or link."""
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

NodeKind = Literal["term", "table", "column", "document", "repository", "community", "person", "domain"]
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


class Explore(Body):
    """A node, a short summary of its neighbourhood with no figures, and a deep link that opens the graph on it. For a
    question, node is the one it names best, absent when it names none, and the link then opens the graph on no node."""
    node: Node | None = None
    summary: str
    link: str
