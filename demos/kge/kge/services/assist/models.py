"""Assist's shared HTTP and MCP contract."""
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class Stages(BaseModel):
    model_config = ConfigDict(extra="forbid")
    resolution: str | None = None
    context: str | None = None
    sql_source: str | None = None
    citation_rule: str | None = None
    renderer: str | None = None
    profile: str | None = None


class Assist(BaseModel):
    model_config = ConfigDict(extra="forbid")
    question: str = Field(max_length=2000)
    purpose: str = Field(default="analysis", max_length=2000)
    arm: str = "graph"
    stages: Stages | None = None
    products: list[str] | None = None


class AssistSource(BaseModel):
    kind: Literal["catalogue", "search", "ask"]
    id: str
    title: str
    unit: str = ""
    why: str


class AssistResponse(BaseModel):
    model_config = ConfigDict(extra="allow")
    question: str
    principal: str
    behaviour: str
    answer_text: str
    source: Literal["catalogue", "search", "ask"]
    sources: list[AssistSource]
    assistant_available: bool
    notice: str = ""
    figure: int | float | None = None
    figures: list[Any] | None = None
    term: str | None = None
    citations: list[dict[str, Any]] | None = None
    cite: list[str] | None = None
    notes: list[Any] | None = None
    joins_refused: list[Any] | None = None
    missing_parts: list[Any] | None = None
    reason: str | None = None
    denied_warehouses: list[str] | None = None
    products: list[str] | None = None
