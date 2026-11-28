"""Transport-neutral, privacy-filtered telemetry response contracts."""
from typing import Any

from pydantic import BaseModel


class TelemetryAgent(BaseModel):
    id: str


class Session(BaseModel):
    id: str
    agent: str
    started_at: str
    last_at: str
    trace_count: int


class TraceSummary(BaseModel):
    id: str
    agent: str
    session_id: str
    started_at: str
    duration_ms: float
    outcome: str | None
    turn_index: int | None = None
    session_trace_count: int | None = None
    previous_trace_id: str | None = None
    next_trace_id: str | None = None
    model_calls: int | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    thinking_tokens: int | None = None
    warehouse_reads: int | None = None


class TraceSpan(BaseModel):
    id: str
    parent_id: str | None
    name: str
    started_at: str
    ended_at: str
    duration_ms: float
    attributes: dict[str, Any]


class Trace(TraceSummary):
    spans: list[TraceSpan]


class Usage(BaseModel):
    date: str
    agent: str
    model: str | None
    calls: int | None
    input_tokens: int | None
    output_tokens: int | None
    thinking_tokens: int | None
    cost_usd: float | None
    errors: int | None
    input_tokens_reported: int | None = None
    output_tokens_reported: int | None = None
    thinking_tokens_reported: int | None = None
    cost_usd_reported: int | None = None


class Figures(BaseModel):
    sessions: int | None
    turns: int | None
    ok: int | None
    failed: int | None
    p50_ms: float | None
    p95_ms: float | None
    max_ms: float | None
    model_ms: float | None
    warehouse_ms: float | None
    model_calls: int | None
    input_tokens: int | None
    output_tokens: int | None
    thinking_tokens: int | None
    cost_usd: float | None
    input_tokens_reported: int | None
    output_tokens_reported: int | None
    thinking_tokens_reported: int | None
    cost_usd_reported: int | None
    warehouse_reads: int | None
    no_model_turns: int | None
    no_model_share: float | None
    thinking_share: float | None


class DailyModel(BaseModel):
    model: str | None
    calls: int | None
    errors: int | None
    input_tokens: int | None
    output_tokens: int | None
    thinking_tokens: int | None
    cost_usd: float | None
    input_tokens_reported: int | None
    output_tokens_reported: int | None
    thinking_tokens_reported: int | None
    cost_usd_reported: int | None


class Daily(Figures):
    date: str
    agent: str
    models: list[DailyModel]


class Overview(BaseModel):
    summary: Figures
    daily: list[Daily]


class Agents(BaseModel):
    agents: list[TelemetryAgent]


class Sessions(BaseModel):
    sessions: list[Session]


class Traces(BaseModel):
    traces: list[TraceSummary]
    next_cursor: str | None = None


class TraceDetail(BaseModel):
    trace: Trace


class ModelUsage(BaseModel):
    usage: list[Usage]


class Ingested(BaseModel):
    accepted: int
