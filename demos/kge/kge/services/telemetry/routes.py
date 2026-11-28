"""Engineer-only, audited telemetry reads and trusted portal span ingestion."""
from __future__ import annotations

import re
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from kge.adapters.telemetry import SinkUnavailable, sink
from kge.ports import StoreError
from kge.services.http import matrix, served, trusted_portal
from kge.services.telemetry import service
from kge.services.telemetry import Agents, Ingested, ModelUsage, Overview, Sessions, TraceDetail, Traces

router = APIRouter(prefix="/telemetry", tags=["internal: governance"])
engineer = matrix("telemetry")


def call(function, *args):
    try:
        return function(served.PROFILE, *args)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from None
    except (StoreError, OSError) as exc:
        raise HTTPException(503, f"telemetry is unavailable: {type(exc).__name__}") from None


@router.get("/agents", response_model=Agents)
def agents(start: str | None = None, end: str | None = None, who: str = Depends(engineer)):
    """Internal: agents with retained traces in the selected period."""
    return call(service.agents, start, end)


@router.get("/sessions", response_model=Sessions)
def sessions(agent: str | None = None, start: str | None = None, end: str | None = None, who: str = Depends(engineer)):
    """Internal: opaque sessions for an agent and period."""
    return call(service.sessions, agent, start, end)


@router.get("/traces", response_model=Traces)
def traces(agent: str | None = None, session: str | None = None, start: str | None = None, end: str | None = None,
           outcome: str | None = None, day: str | None = None, day_agent: str | None = None,
           limit: int = 50, cursor: str | None = None, who: str = Depends(engineer)):
    """Internal: paged summaries of retained question traces."""
    return call(service.traces, agent, session, start, end, outcome, day, day_agent, limit, cursor)


@router.get("/overview", response_model=Overview)
def overview(start: str | None = None, end: str | None = None, agent: str | None = None,
             session: str | None = None, outcome: str | None = None, who: str = Depends(engineer)):
    """Internal: aggregate question telemetry in the selected period."""
    return call(service.overview, start, end, agent, session, outcome)


@router.get("/traces/{trace_id}", response_model=TraceDetail)
def trace(trace_id: str, start: str | None = None, end: str | None = None, who: str = Depends(engineer)):
    """Internal: one retained trace and its sanitized spans."""
    return call(service.trace, trace_id, start, end)


@router.get("/usage", response_model=ModelUsage)
def usage(start: str | None = None, end: str | None = None, agent: str | None = None, who: str = Depends(engineer)):
    """Internal: daily model calls, usage, known cost and errors."""
    return call(service.usage, start, end, agent)


class TelemetryInputSpan(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    trace_id: str
    parent_id: str | None = None
    name: str
    started_at: str
    ended_at: str
    attributes: dict[str, Any]


class TelemetryInputSpans(BaseModel):
    model_config = ConfigDict(extra="forbid")
    spans: list[TelemetryInputSpan] = Field(min_length=1, max_length=512)


_PORTAL_KEYS = frozenset({"kge.agent", "gen_ai.conversation.id", "http.request.method", "http.route", "http.response.status_code", "error.type", "kge.telemetry.origin", "kge.client"})
_PORTAL_ROUTES = frozenset({"/api/cerebro/stream", "/api/context/playground/call", "/api/ask", "/ask", "/ask-data-product", "/ask-domain", "/api/context/ask", "/api/context/ask/feedback", "/api/context/answer", "/api/context/agent", "/api/context/ask/runs/:id"})


@router.post("/spans", response_model=Ingested)
def ingest(body: TelemetryInputSpans, request: Request):
    """Internal: accept only sanitized portal question spans, authenticated as the portal."""
    trusted_portal(request, "The telemetry sink is only available to the portal.")
    for span in body.spans:
        attrs = span.attributes
        if "kge.client" in attrs and attrs["kge.client"] != served.PROFILE.client:
            raise HTTPException(422, "telemetry client does not match this service")
        if set(attrs) - _PORTAL_KEYS or attrs.get("http.route") not in _PORTAL_ROUTES:
            raise HTTPException(422, "unsupported portal span attributes")
        if span.name != f"{attrs.get('http.request.method')} {attrs.get('http.route')}":
            raise HTTPException(422, "unsupported portal span name")
        error = attrs.get("error.type")
        if error is not None and (not isinstance(error, str) or not re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", error)):
            raise HTTPException(422, "unsupported portal span error")
        status = attrs.get("http.response.status_code")
        if attrs.get("http.request.method") not in ("GET", "POST") or not (
            type(status) is int and 100 <= status <= 599 or status is None and error == "aborted"
        ):
            raise HTTPException(422, "unsupported portal span status")
    try:
        rows = [span.model_dump() for span in body.spans]
        for row in rows:
            row["attributes"]["kge.telemetry.origin"] = "portal"
            attrs = row["attributes"]
            attrs["kge.outcome"] = "failed" if attrs.get("error.type") == "aborted" or attrs.get("http.response.status_code", 0) >= 500 else "ok"
        accepted = sink(served.PROFILE).append(rows)
    except SinkUnavailable as exc:
        raise HTTPException(409, str(exc)) from None
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None
    except (StoreError, OSError) as exc:
        raise HTTPException(503, f"telemetry is unavailable: {type(exc).__name__}") from None
    return {"accepted": accepted}
