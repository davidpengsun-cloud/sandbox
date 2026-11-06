"""The closed search contract shared by HTTP and MCP."""
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

Kind = Literal["product", "term", "metric", "column", "document"]
Query = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)]
Limit = Annotated[int, Field(ge=1, le=100)]


class Body(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Search(Body):
    query: Query
    kinds: list[Kind] | None = None
    limit: Limit = 20


class Why(Body):
    kind: Literal["term", "synonym", "passage", "name", "description"]
    text: str
    term: str | None = None
    chunk: str | None = None
    similarity: float | None = None
    via: list[str] = Field(default_factory=list)


class Hit(Body):
    kind: Kind
    unit: str
    id: str
    title: str
    score: float = Field(ge=0, le=1)
    why: Why
    restricted: bool = False


class SearchResponse(Body):
    hits: list[Hit]
