"""The closed search contract shared by HTTP and MCP."""
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

Kind = Literal["product", "term", "metric", "column", "document", "filter"]
DEFAULT_KINDS = ("product", "term", "metric", "column", "document")
"""The kinds a search without kinds returns: a filter phrase only when kinds names it (#707 decision 12)."""
Query = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)]
Limit = Annotated[int, Field(ge=1, le=100)]


class Body(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Search(Body):
    query: Query
    kinds: list[Kind] | None = None
    limit: Limit = 20


class Why(Body):
    kind: Literal["term", "synonym", "passage", "name", "description", "phrase"]
    text: str
    term: str | None = None
    chunk: str | None = None
    similarity: float | None = None
    via: list[str] = Field(default_factory=list)


class Hit(Body):
    """A ranked hit. A filter hit (kind filter, only when kinds names it) adds label (its column's business label),
    value (where the phrase states one) and, for an engineer alone, column."""
    kind: Kind
    unit: str
    id: str
    title: str
    score: float = Field(ge=0, le=1)
    why: Why
    restricted: bool = False
    label: str | None = None
    value: str | None = None
    column: str | None = None


class SearchResponse(Body):
    hits: list[Hit]

    def model_dump(self, *args, **kwargs):
        """As set: a filter phrase's own fields only on a filter hit, so an MCP result is POST /search's."""
        kwargs.setdefault("exclude_unset", True)
        return super().model_dump(*args, **kwargs)
