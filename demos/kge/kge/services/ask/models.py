"""The ask service's request and response bodies, one set for every transport: the HTTP routes (routes.py) and the MCP
tools (mcp.py) take and give these, so the OpenAPI schema and the tools' schemas are generated from the same models.

A response model declares every field a client relies on. Where the fields beyond those vary with a behaviour or an arm,
the model says so and allows them (extra="allow"): they are returned as the engine gives them, and the route leaves out
any declared field the engine did not set, so a response holds nothing the engine did not give."""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, RootModel, model_validator

from kge.pipeline.settings import ASK_AI_FALLBACKS, ASK_ORDERS
from kge.ports import FEEDBACK_VERDICTS
from kge.services.ask.engine.wording import BEHAVIOURS
from kge.semantics.plan import HistoryEntry, Clarification, Result
from kge.semantics.planhistory import MAX_HISTORY_TURNS, bounds

Number = int | float
Behaviour = Literal[BEHAVIOURS]
AiFallback = Literal[ASK_AI_FALLBACKS]
AskOrder = Literal[ASK_ORDERS]
ANSWER_MODES = ("certified", "ai_generated", "not_sure")
MAX_RECEIPT_CHARS = 8192
MAX_QUESTION_CHARS = 2000
QUESTION_TOO_LONG = f"Question must be at most {MAX_QUESTION_CHARS} characters."

BEHAVIOUR = ("What the answer is. combined: figures planned and answered whole with their documents (/ask). figure: one "
             "figure, or grouped rows. sum_with_verdict, sum_without_verdict, parts_with_reason: parts across units, "
             "added only where they add. cite, cite_certified, glossary_definition: a definition and what it cites. "
             "refuse, blocked_access: a refusal, with its reason. abstain, abstain_route_steward, abstain_with_reason: "
             "no answer, with what is missing. clarify: readable choices, without a figure")


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
    compact: bool = Field(default=False, description="Opt in to answer, figures, definitions, citations and action metadata with the answered query's SQL and sql_certified, without plan internals, traces or duplicate plans: plan and trace stay the engineer's (the role matrix's row 7). History stays intact for follow-up turns.")
    history: list[HistoryEntry] = Field(default_factory=list, max_length=MAX_HISTORY_TURNS)

    @model_validator(mode="after")
    def bounded_history(self):
        bounds(self.question, self.history)
        return self

    products: list[str] | None = Field(default=None, description="Optional product identifiers (<unit>.<table>) supplying default page or Explore context; readable sources outside it remain available.")
    runtimes: list[str] | None = Field(default=None, max_length=16, description="Optional ordered runtime ids to try for this request.")


class Feedback(Body):
    """Whether an answer was what the person meant: the receipt the answer carried, as given, and the verdict. No
    question, history or free text."""
    receipt: str = Field(max_length=MAX_RECEIPT_CHARS, description="The answer's receipt, exactly as the answer gave it")
    verdict: str = Field(max_length=32, description=f"{' or '.join(FEEDBACK_VERDICTS)}")


class FeedbackAccepted(Body):
    """202: the verdict is recorded; nothing is read back."""


class AgentAsk(Body):
    """A question and untrusted history for the reference agent (/agent), on one arm."""
    question: str = Field(max_length=MAX_QUESTION_CHARS)
    purpose: str = Field(default="analysis", max_length=MAX_QUESTION_CHARS)
    arm: str = "treatment"
    products: list[str] | None = None
    history: list[HistoryEntry] = Field(default_factory=list, max_length=MAX_HISTORY_TURNS)
    runtimes: list[str] | None = Field(default=None, max_length=16)

    @model_validator(mode="after")
    def bounded_history(self):
        bounds(self.question, self.history)
        return self


class ContextAsk(Body):
    question: str = Field(max_length=MAX_QUESTION_CHARS)
    purpose: str = Field(default="analysis", max_length=MAX_QUESTION_CHARS)
    arm: str = "treatment"
    products: list[str] | None = None
    plan_vocabulary: bool = False
    compact: bool = Field(default=False, description="Opt in to structured context, definitions, sources and decisions with definition text only in the prompt block, without SQL, traces, duplicate candidates or plan internals.")
    history: list[HistoryEntry] = Field(default_factory=list, max_length=MAX_HISTORY_TURNS)
    runtimes: list[str] | None = Field(default=None, max_length=16)
    open_items: bool = Field(default=False, description="Opt in to bindings and open_items: the question as ask's rules read it, "
                                                         "with the same history, products, purpose and runtimes. Omitted or false "
                                                         "is unchanged.")

    @model_validator(mode="after")
    def bounded_history(self):
        bounds(self.question, self.history)
        return self


def unset_not_null(schema: dict) -> None:
    """A reply model's schema as its replies hold it: a field left unset is absent, never null, so an optional field
    is its type alone, with no title (the name says it). Keeps get_context's output schema small (#707)."""
    for prop in schema.get("properties", {}).values():
        prop.pop("title", None)
        options = prop.get("anyOf")
        if prop.get("default", ...) is None and isinstance(options, list) and len(options) == 2 and {"type": "null"} in options:
            kept = next(o for o in options if o != {"type": "null"})
            prop.pop("anyOf")
            prop.pop("default")
            prop.update(kept)


class OpenItemOption(BaseModel):
    """An open item's option: a term (id, definition, default_alias), a month (value YYYY-MM), a grouping dimension
    (phrases) or a declared alternative (kind, of: its column's label)."""
    model_config = ConfigDict(extra="forbid", json_schema_extra=unset_not_null)
    label: str
    id: str | None = None
    definition: str | None = None
    default_alias: bool | None = None
    value: str | None = None
    kind: str | None = None
    of: str | None = None
    phrases: list[str] | None = None


class Binding(BaseModel):
    """A span of the question (text, start, end) and what ask's rules bound it to, in business names (#707 req. 3).
    by: explicit; default_alias (an alias naming several readable terms bound its default); implied (no span of its
    own); unsettled (options left)."""
    model_config = ConfigDict(extra="forbid", json_schema_extra=unset_not_null)
    kind: Literal["term", "period", "filter", "grouping", "comparison", "time_mode", "number", "operator", "name", "unread"]
    text: str
    start: int | None
    end: int | None
    term: str | None = None
    context: str | None = None
    by: Literal["explicit", "default_alias", "implied", "unsettled"] | None = None
    time_mode: str | None = None
    label: str | None = None
    value: str | None = None
    operation: str | None = None
    options: list[OpenItemOption] | None = None


class OpenItem(BaseModel):
    """What ask would settle by a default, clarify, or cannot answer, as a fact (#707 req. 4); no card, no suggested
    option. item: term (an alias naming several readable terms), period, grouping (an unread grouping word naming
    declared dimensions), meaning (the rules' clarification), question (a decline) or access (a refusal). resolution:
    default (ask settles it), clarify (the person picks one) or unanswerable. reason: ask's own words for a decline or
    refusal it gives before its first warehouse query or model request, withheld as ask withholds them ("The question
    cannot be read." where ask itself fails with a 500). A question ask hands to its model has no decline item. A
    period's options are the covered months, newest first; latest is the latest one where a month asked for is not
    covered."""
    model_config = ConfigDict(extra="forbid", json_schema_extra=unset_not_null)
    item: Literal["term", "period", "grouping", "meaning", "question", "access"]
    resolution: Literal["default", "clarify", "unanswerable"]
    text: str | None = None
    start: int | None = None
    end: int | None = None
    reason: str | None = None
    grain: str | None = None
    latest: OpenItemOption | None = None
    options: list[OpenItemOption]


class Retrieve(Body):
    query: str = Field(max_length=MAX_QUESTION_CHARS)
    purpose: str = Field(default="assistant", max_length=MAX_QUESTION_CHARS)
    k: int = Field(default=5, ge=1, le=50)
    shelf: str | None = None
    tiers: list[str] | None = None
    include_passages: bool = Field(default=False, strict=True)


class Open(BaseModel):
    model_config = ConfigDict(extra="allow")


class Figure(Open):
    """One figure of a combined answer: what it measures and its value, bound to the citation it rests on."""
    id: str
    label: str
    term: str
    kind: str = Field(description="count, sum, avg, comparison or derived")
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
    result: Result | None = None
    clarification: Clarification | None = None
    history: list[HistoryEntry] | None = None
    figure: Number | None = Field(default=None, description="The figure, or the total of a sum")
    unit: str | None = Field(default=None, description="The unit the figure is in")
    column: str | None = Field(default=None, description="The certified column it measures, table.column")
    sql: str | None = Field(default=None, description="The query that gave it, after the gate passed it; kept in compact mode, never on a decline")
    sql_certified: bool | None = Field(default=None, description="Whether the SQL is certified, wherever the answer carries SQL and an answer_mode: true for certified, false for ai_generated or a not_sure that keeps its query, which the card marks \"SQL: not certified (AI-Generated)\"")
    rows: dict[str, Number | None] | None = Field(default=None, description="A grouped figure's rows, by group")
    parts: dict[str, Number | None] | list[Any] | None = Field(
        default=None, description="Across units: each unit's value (a sum's), or each unit's plan (a refusal's)")
    reason: str | None = Field(default=None, description="Why it refused or abstained, or what a sum's verdict rests on")
    definition_citation: dict[str, Any] | None = Field(default=None, description="Certified pack glossary authority and context provenance for a context-backed definition with no defining document")
    certified_definitions: list[dict[str, Any]] | None = Field(default=None, description="Accepted pack definitions quoted verbatim with access redactions for the certified terms the answer explains, with their certifying documents")
    cite: list[str] | None = Field(default=None, description="The documents a definition cites, by id")
    difference: dict[str, Any] | None = Field(default=None, description="A question on how two glossary terms differ: each term's definition, steward, source, declared attributes and the first and latest period its data covers in this release (terms), the attributes on which the two differ (rows) and one summary line (summary), in business words; or, where one side names no term the caller may read, that side (unresolved) and a clarification")
    difference_ids: dict[str, Any] | None = Field(default=None, description="The ids each attribute of a difference answer came from (contexts, facts, filters, row selectors, release driver, version dates): the engineer's, omitted in compact mode")
    citations: list[Citation] | None = Field(default=None, description="The documents the answer cites, numbered")
    answer: Literal["no"] | None = Field(
        default=None, description='The answer to a question that asks whether two units join on a key\'s name: "no", '
                                  "the graph's no-join fact the reason. Absent on every other answer")
    denied_warehouses: list[str] | None = Field(default=None, description="The units the caller may not read that it needs")
    joins_refused: list[str] | None = Field(default=None, description="The unit pairs a no-join fact keeps apart")
    display: dict[str, Any] | None = Field(default=None, description="The page's display block, only where the request asks for it (its display query parameter)")
    reference_sql: str | None = Field(default=None, description="The reference plan's SQL before runtime renaming")
    runtime: dict[str, Any] | None = Field(default=None, description="The runtime that executed the query")
    physical_tables: list[str] | None = Field(default=None, description="The physical tables the runtime read")
    runtime_certified: bool | None = Field(default=None, description="Whether the measure came from its certified runtime")
    certified_in: str | None = Field(default=None, description="The runtime the measure is certified in")
    live_data: bool | None = Field(default=None, description="Whether the answer came from an external live runtime")
    queried_at: str | None = Field(default=None, description="UTC timestamp when an external runtime executed the query")
    path: dict[str, Any] | None = Field(default=None, description="Structured query path (nodes and edges), omitted in compact mode")
    graph_path: dict[str, Any] | None = Field(default=None, description="The context graph's nodes and edges this answer used (graph_path); in compact mode without SQL, plans or traces")
    answer_mode: Literal[ANSWER_MODES] | None = Field(default=None, description="certified (the rules answered, every word certified), ai_generated (the model's best guess, labelled AI-Generated) or not_sure. Absent on an access refusal")
    answered_by: str | None = Field(default=None, description="What planned the answer: rules, or model (the model named in model)")
    answer_id: str | None = Field(default=None, description="The answer's turn id: the same question asked again with the same binding and history has the same id. A decline made before the planner (an access refusal, a question with no glossary term) has an id of its own")
    receipt: str | None = Field(default=None, description="An opaque, encrypted receipt of the answer, declines included, for POST /ask/feedback or the feedback tool, sent back as given")
    ai_fallback: AiFallback | None = Field(default=None, description="The deployment's AI-Generated fallback the answer ran under (ASK_AI_FALLBACK): on or off, off where Ask has no model")
    order: AskOrder | None = Field(default=None, description="The order the answer's planner ran (ASK_ORDER): rules_first or model_first, rules_first where Ask has no model")
    model: str | None = Field(default=None, description="The model the answer used, provider:model, or none for a rules answer")


class AnswerResponse(Answer):
    """POST /answer: an answer on rules (internal: the A/B harness and the control use it). Plan and trace are the
    engineer's (the role matrix's row 7): anyone else's answer is compact, as /ask's is, and keeps the answered query's
    SQL and sql_certified."""
    trace: list[Any] | None = Field(default=None, description="The answer steps, omitted in compact mode")
    run_trace: dict[str, Any] | None = Field(default=None, description="The run diagnostics, omitted in compact mode")


class AskResponse(Answer):
    """POST /ask: the graph arm answered whole, figures with the documents that back them. A question of one customer
    group and measures over it is planned and cited whole (combined: true, with figures, citations, not_cited and
    withheld); any other gets /answer's answer, with combined: false. A compact answer keeps the answered query's SQL
    (sql, and each figure's) and sql_certified; plan and trace stay the engineer's (the role matrix's row 7)."""
    trace: list[Any] | None = Field(default=None, description="The answer steps, omitted in compact mode")
    run_trace: dict[str, Any] | None = Field(default=None, description="The run diagnostics, omitted in compact mode")
    products: list[str] | None = Field(default=None, description="Product identifiers (<unit>.<table>) supplying the question’s page or Explore context, when provided.")
    combined: bool = Field(description="Whether /ask planned and answered it whole, figures with their documents")
    figures: list[Figure] | None = Field(default=None, description="A combined answer's figures, each bound to its citation")
    not_cited: list[NotCited] | None = Field(default=None, description="A combined answer's documents shown and not used")
    withheld: list[Withheld] | None = Field(default=None, description="A combined answer's documents the privacy gate held back")
    unit_label: str | None = None
    period: str | None = Field(default=None, description="A combined answer's period, in words")

    def model_dump(self, *args, **kwargs) -> dict[str, Any]:
        kwargs.setdefault("exclude_unset", True)
        return super().model_dump(*args, **kwargs)


class QuestionRunResponse(Body):
    """A durable question, readable only by its signed-in owner."""
    id: str
    state: Literal["running", "done", "failed", "expired"]
    expires_at: float
    poll_after: float
    events: list[dict[str, Any]]
    cursor: int
    answer: AskResponse | None = None
    error: str | None = None
    reason: Literal["warehouse_time_limit", "run_time_limit", "warehouse_failure", "stopped", "access_changed"] | None = Field(
        default=None, description="What ended a failed or expired run beside its error: the warehouse's time limit, the run's "
        "time limit (QUESTION_RUN_TIMEOUT), another failure the warehouse job reported, its owner's stop "
        "(DELETE /ask/runs/{run_id}, or the stop_question_run tool), or the person's access or the served release changing "
        "since it started; absent when something else ended it")

    def model_dump(self, *args, **kwargs) -> dict[str, Any]:
        kwargs.setdefault("exclude_unset", True)
        return super().model_dump(*args, **kwargs)


class AskRun(Body):
    """The ask tool's reply to a background question its answer was not ready for within QUESTION_START_WAIT: the run,
    as GET /ask/runs/{run_id} gives it, to follow with the question_run tool."""
    run: QuestionRunResponse


class AskToolResponse(RootModel[AskResponse | AskRun]):
    """The ask tool's reply: the answer (AskResponse), or, for a background question not answered within
    QUESTION_START_WAIT, {"run": ...} (AskRun). Each is given as its own model gives it, its unset fields left out."""
    model_config = ConfigDict(json_schema_extra={"type": "object"})

    def model_dump(self, *args, **kwargs) -> Any:
        kwargs.setdefault("exclude_unset", True)
        return super().model_dump(*args, **kwargs)


class AgentResponse(Open):
    """POST /agent (internal: the reference agent, for the A/B harness): its answer on one arm, in the shape of /answer's,
    with what ran it (agent). Its fields beyond these vary with the arm and the behaviour."""
    question: str
    principal: str
    purpose: str
    arm: str
    behaviour: Behaviour
    trace: list[Any] | None = Field(default=None, description="The agent's steps, omitted in compact mode")
    run_trace: dict[str, Any] | None = Field(default=None, description="The run diagnostics, omitted in compact mode")
    answer_text: str | None = None
    phrased_by: str | None = None
    agent: dict[str, Any] | None = Field(default=None, description="What ran the agent: rules, mock or model, and its steps")
    history: list[HistoryEntry] | None = None
    result: Result | None = None
    clarification: Clarification | None = None
    path: dict[str, Any] | None = Field(default=None, description="Structured query path (nodes and edges)")
    graph_path: dict[str, Any] | None = Field(default=None, description="The context graph's nodes and edges this answer used")


class ContextResponse(Open):
    """The context package the reference agent's prompt carries on one arm: the block of text itself and what it was
    built from. The fields beyond these vary with the arm, and are returned as the workflow gives them."""
    arm: str = Field(description="control (graph off) or treatment (graph on)")
    block: str = Field(description="The text the agent's prompt carries; compact mode keeps only its definitions")
    sources: list[Any]
    units: list[Any] = Field(description="The units the principal may read")
    certified: dict[str, Any]
    in_window: list[Any]
    defines: dict[str, Any]
    meta: dict[str, Any]
    group: Any = None
    named: list[Any] | None = Field(default=None, description="Documents also named in the prompt block, omitted in compact mode")
    withheld: list[Any]
    needed: list[Any]
    plan: Any = None
    decided: Any = None
    plan_vocabulary: dict[str, Any] | None = None
    bindings: list[Binding] | None = Field(default=None, description="With open_items=true only")
    open_items: list[OpenItem] | None = Field(default=None, description="With open_items=true only")

    def model_dump(self, *args, **kwargs) -> dict[str, Any]:
        kwargs.setdefault("exclude_unset", True)
        return super().model_dump(*args, **kwargs)


class RetrieveResponse(Open):
    """POST /retrieve: vector search over the chunks the caller may read for a purpose. exact: the hits, each a chunk of
    a document with its score; what the index returned and what the access and purpose filter took out."""
    exact: list[dict[str, Any]]
    index_returned: int | None = None
    post_filter_lost: int | None = None
    truncated: bool | None = None


class TermMeaning(BaseModel):
    """A readable term a name resolves to; default_alias: the pack's alias binds it by default (#707 req. 2)."""
    model_config = ConfigDict(extra="forbid", json_schema_extra=unset_not_null)
    id: str
    label: str
    definition: str
    default_alias: bool


class TermMap(Open):
    """GET /map/{term}: a term's definition and steward, the data sets that hold it, the documents that define it, its
    contexts and the industry standards it maps to, as the caller may read them. An engineer's map is full (compact
    false): its certified columns (each marked readable or not), its contexts as the graph declares them, the no-join
    facts and certified joins among their units and its uncertified mentions. Anyone else's is compact (the role
    matrix's row 7): business names alone, no physical table or column, join condition, no-join fact or certified join.
    Both carry the term's explained paths (#628), whose join hops the compact map names in business words."""
    term: str
    principal: str
    compact: bool = Field(description="Whether the map is the compact one, in business names: every caller's but an engineer's")
    definition: str = Field(description="The term's definition in the pack's glossary, empty where it has none")
    steward: str = Field(description="The term's steward, by role, empty where it names none")
    data_sets: list[dict[str, Any]] = Field(description="The data sets that hold the term in the units the caller may read: "
                                                        "unit, unit_label, domain, community, business_owner, currency, "
                                                        "certified_by, certified_on, each where known; no table or column name")
    defined_by: list[Any]
    contexts: list[dict[str, Any]] = Field(description="The term's contexts in the units the caller may read; compact: label, "
                                                       "measure, currency, unit, unit_label, filters and row_selectors by label")
    paths: list[dict[str, Any]] = Field(default_factory=list, description="The term's whole paths in order, each of its contexts "
                                                                     "explained as explore_question explains it (#628): each "
                                                                     "context's fact, measure, join hops with cardinality, type "
                                                                     "and window, dimensions, conditions and time rules; business "
                                                                     "words in the compact map, physical names in the full; only "
                                                                     "units the caller may read; no figure")
    standards: list[dict[str, Any]]
    implementations: list[dict[str, Any]] | None = Field(default=None, description="The certified columns; the full map's")
    no_join: list[dict[str, Any]] | None = Field(default=None, description="The no-join facts among the units; the full map's")
    certified_joins: list[dict[str, Any]] | None = Field(default=None, description="The certified joins among the units; the full map's")
    uncertified_mentions: list[Any] | None = Field(default=None, description="Uncertified documents that mention the term; the full map's")
    resolves_to: list[TermMeaning] | None = Field(default=None, description="Only for a name that is no term's own: the readable "
                                                                           "terms it resolves to")

    def model_dump(self, *args, **kwargs) -> dict[str, Any]:
        kwargs.setdefault("exclude_unset", True)
        return super().model_dump(*args, **kwargs)


class Problems(BaseModel):
    """A 400: a run config or an arm that cannot run here, each problem in plain English."""
    error: str
    problems: list[str]
    config: dict[str, Any] | None = None


class RuntimeInfo(Open):
    """One runtime the caller may use, and the reference tables it holds."""
    id: str
    label: str
    kind: str
    dialect: str
    available: bool = True
    certified: bool = False
    tables: list[str] = Field(default_factory=list)


class RuntimesResponse(Open):
    """GET /runtimes: the runtimes the caller may use, and which reference tables each holds."""
    runtimes: list[RuntimeInfo]
