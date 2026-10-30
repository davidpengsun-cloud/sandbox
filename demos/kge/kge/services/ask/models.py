"""The ask service's request and response bodies, one set for every transport: the HTTP routes (routes.py) and the MCP
tools (mcp.py) take and give these, so the OpenAPI schema and the tools' schemas are generated from the same models.

A response model declares every field a client relies on. Where the fields beyond those vary with a behaviour or an arm,
the model says so and allows them (extra="allow"): they are returned as the engine gives them, and the route leaves out
any declared field the engine did not set, so a response holds nothing the engine did not give."""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from kge.services.ask.engine.wording import BEHAVIOURS

Number = int | float
Behaviour = Literal[BEHAVIOURS]
MAX_QUESTION_CHARS = 2000
QUESTION_TOO_LONG = f"Question must be at most {MAX_QUESTION_CHARS} characters."

BEHAVIOUR = ("What the answer is. combined: figures planned and answered whole with their documents (/ask). figure: one "
             "figure, or grouped rows. sum_with_verdict, sum_without_verdict, parts_with_reason: parts across units, "
             "added only where they add. cite, cite_certified, glossary_definition: a definition and what it cites. "
             "refuse, blocked_access: a refusal, with its reason. abstain, abstain_route_steward, abstain_with_reason: "
             "no answer, with what is missing")


class Body(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Stages(Body):
    """A run-config override: each stage given replaces the preset's; values are checked by RunConfig.problems. The
    profile is no stage: one given is a 400 (RunConfig.with_stages), as the process sets it."""
    resolution: str | None = None
    context: str | None = None
    sql_source: str | None = None
    citation_rule: str | None = None
    renderer: str | None = None
    profile: str | None = None


class Ask(Body):
    """A question for /ask or /answer. Never a principal: the caller is who asks (a 422 for one in the body)."""
    question: str = Field(max_length=MAX_QUESTION_CHARS)
    purpose: str = Field(default="analysis", max_length=MAX_QUESTION_CHARS)
    arm: str = "graph"
    stages: Stages | None = None


class AgentAsk(Body):
    """A question for the reference agent (/agent) or its context package (/context), on one arm."""
    question: str = Field(max_length=MAX_QUESTION_CHARS)
    purpose: str = Field(default="analysis", max_length=MAX_QUESTION_CHARS)
    arm: str = "treatment"


class Retrieve(Body):
    query: str = Field(max_length=MAX_QUESTION_CHARS)
    purpose: str = Field(default="assistant", max_length=MAX_QUESTION_CHARS)
    k: int = Field(default=5, ge=1, le=50)
    shelf: str | None = None
    tiers: list[str] | None = None


class Open(BaseModel):
    model_config = ConfigDict(extra="allow")


class Figure(Open):
    """One figure of a combined answer: what it measures and its value, bound to the citation it rests on."""
    id: str
    label: str
    term: str
    kind: str = Field(description="count, sum or avg")
    column: str = Field(description="The certified column, table.column")
    currency: str = Field(description="Its currency, empty where it counts")
    value: Number | None
    cite: int | None = Field(default=None, description="The n of the citation it is bound to")


class Citation(Open):
    """A document the answer cites: its marker n, as [n] in answer_text, and why it is cited. The tier rule adds its tier,
    how it is cited, its owner, version and time in force."""
    n: int = Field(description="Its marker: [n] in answer_text")
    doc: str
    title: str = ""
    why: str = ""


class NotCited(Open):
    """A document shown and not used: superseded or uncertified, and why."""
    doc: str
    why: str


class Withheld(Open):
    """A document held back by the privacy gate: named, never quoted."""
    doc: str


class Answer(Open):
    """An answer, as /answer gives it: who asked what on which arm, how it behaved, its readable text, and what it read
    and ran. Its fields beyond these vary with the behaviour (a context's plan, a definition's references, the parts of
    a sum), and are returned as the engine gives them."""
    question: str
    principal: str
    purpose: str
    arm: str = Field(description="The run config the answer ran on, as its label")
    behaviour: Behaviour = Field(description=BEHAVIOUR)
    answer_text: str = Field(description="The answer in plain English, for every behaviour: a figure's value, currency, term "
                                         "or column, period and [n] markers; a refusal's what and why and whom to ask, by "
                                         "role; an abstention's what is missing. Every number in it is one the answer holds "
                                         "and every [n] one of its citations")
    phrased_by: str = Field(description="Who worded answer_text: template, or model:<provider:model> where a model's wording "
                                        "passed the number and citation checks; template (<why>) where it did not")
    trace: list[Any] = Field(description="The steps the answer took, each a line")
    versions: dict[str, Any] = Field(description="The parser, planner, models and graph the answer ran on")
    run_trace: dict[str, Any] = Field(description="The run's trace: its config, stages, reads and model calls")
    figure: Number | None = Field(default=None, description="The figure, or the total of a sum")
    unit: str | None = Field(default=None, description="The unit the figure is in")
    column: str | None = Field(default=None, description="The certified column it measures, table.column")
    sql: str | None = Field(default=None, description="The query that gave it, after the gate passed it")
    rows: dict[str, Number | None] | None = Field(default=None, description="A grouped figure's rows, by group")
    parts: dict[str, Number | None] | list[Any] | None = Field(
        default=None, description="Across units: each unit's value (a sum's), or each unit's plan (a refusal's)")
    reason: str | None = Field(default=None, description="Why it refused or abstained, or what a sum's verdict rests on")
    cite: list[str] | None = Field(default=None, description="The documents a definition cites, by id")
    citations: list[Citation] | None = Field(default=None, description="The documents the answer cites, numbered")
    answer: Literal["no"] | None = Field(
        default=None, description='The answer to a question that asks whether two units join on a key\'s name: "no", '
                                  "the graph's no-join fact the reason. Absent on every other answer")
    denied_warehouses: list[str] | None = Field(default=None, description="The units the caller may not read that it needs")
    joins_refused: list[str] | None = Field(default=None, description="The unit pairs a no-join fact keeps apart")
    display: dict[str, Any] | None = Field(default=None, description="The page's display block, only where the request asks for it (its display query parameter)")


class AnswerResponse(Answer):
    """POST /answer: an answer on rules (internal: the A/B harness and the control use it)."""


class AskResponse(Answer):
    """POST /ask: the graph arm answered whole, figures with the documents that back them. A question of one customer
    group and measures over it is planned and cited whole (combined: true, with figures, citations, not_cited and
    withheld); any other gets /answer's answer, with combined: false."""
    combined: bool = Field(description="Whether /ask planned and answered it whole, figures with their documents")
    figures: list[Figure] | None = Field(default=None, description="A combined answer's figures, each bound to its citation")
    not_cited: list[NotCited] | None = Field(default=None, description="A combined answer's documents shown and not used")
    withheld: list[Withheld] | None = Field(default=None, description="A combined answer's documents the privacy gate held back")
    unit_label: str | None = None
    period: str | None = Field(default=None, description="A combined answer's period, in words")

    def model_dump(self, *args, **kwargs) -> dict[str, Any]:
        kwargs.setdefault("exclude_unset", True)
        return super().model_dump(*args, **kwargs)


class AgentResponse(Open):
    """POST /agent (internal: the reference agent, for the A/B harness): its answer on one arm, in the shape of /answer's,
    with what ran it (agent). Its fields beyond these vary with the arm and the behaviour."""
    question: str
    principal: str
    purpose: str
    arm: str
    behaviour: str
    trace: list[Any]
    run_trace: dict[str, Any]
    answer_text: str | None = None
    phrased_by: str | None = None
    agent: dict[str, Any] | None = Field(default=None, description="What ran the agent: rules, mock or model, and its steps")


class ContextResponse(Open):
    """The context package the reference agent's prompt carries on one arm: the block of text itself and what it was
    built from. The fields beyond these vary with the arm, and are returned as the workflow gives them."""
    arm: str = Field(description="control (graph off) or treatment (graph on)")
    block: str = Field(description="The text the agent's prompt carries")
    sources: list[Any]
    units: list[Any] = Field(description="The units the principal may read")
    certified: dict[str, Any]
    in_window: list[Any]
    defines: dict[str, Any]
    meta: dict[str, Any]
    group: Any = None
    named: list[Any]
    withheld: list[Any]
    needed: list[Any]
    plan: Any = None
    decided: Any = None

    def model_dump(self, *args, **kwargs) -> dict[str, Any]:
        kwargs.setdefault("exclude_unset", True)
        return super().model_dump(*args, **kwargs)


class RetrieveResponse(Open):
    """POST /retrieve: vector search over the chunks the caller may read for a purpose. exact: the hits, each a chunk of
    a document with its score; what the index returned and what the access and purpose filter took out."""
    exact: list[dict[str, Any]]
    index_returned: int
    post_filter_lost: int


class TermMap(Open):
    """GET /map/{term}: a term's certified columns (each marked readable or not), the documents that define it, its
    contexts, the no-join facts and certified joins among their units, and the industry standards it maps to."""
    term: str
    principal: str
    implementations: list[dict[str, Any]]
    defined_by: list[Any]
    contexts: list[dict[str, Any]]
    no_join: list[dict[str, Any]]
    certified_joins: list[dict[str, Any]]
    uncertified_mentions: list[Any]
    standards: list[dict[str, Any]]


class Problems(BaseModel):
    """A 400: a run config or an arm that cannot run here, each problem in plain English."""
    error: str
    problems: list[str]
    config: dict[str, Any] | None = None
