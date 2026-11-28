"""Provider-independent question telemetry and validated query ranges."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
import base64
import json

from kge.adapters.telemetry import reader
from kge.pipeline.trace_context import instant
from kge.pipeline.trace_context import AGENTS, HEX_TRACE, SESSION


def period(start: str | None, end: str | None) -> tuple[str, str]:
    """UTC [start, end), defaulting to the last seven days; future end boundaries are allowed."""
    now = datetime.now(timezone.utc)
    try:
        last = instant(end) if end else now
        first = instant(start) if start else last - timedelta(days=7)
    except (ValueError, TypeError, OverflowError):
        raise ValueError("the telemetry period must use ISO dates or timestamps") from None
    if first >= last or last - first > timedelta(days=366):
        raise ValueError("the telemetry period must be increasing and at most 366 days")
    return first.isoformat(), last.isoformat()


def valid_agent(agent: str | None) -> None:
    if agent is not None and agent not in AGENTS:
        raise ValueError("unknown telemetry agent")


def agents(profile, start=None, end=None):
    first, last = period(start, end)
    return {"agents": reader(profile).agents(first, last)}


def sessions(profile, agent=None, start=None, end=None):
    valid_agent(agent)
    first, last = period(start, end)
    return {"sessions": reader(profile).sessions(agent, first, last)}


def _filters(agent, session, outcome):
    valid_agent(agent)
    if session is not None and not SESSION.fullmatch(session):
        raise ValueError("invalid session ID")
    if outcome is not None and outcome not in ("ok", "failed"):
        raise ValueError("invalid telemetry outcome")


def traces(profile, agent=None, session=None, start=None, end=None, outcome=None, day=None, day_agent=None, limit=50, cursor=None):
    _filters(agent, session, outcome)
    valid_agent(day_agent)
    if type(limit) is not int or not 1 <= limit <= 100:
        raise ValueError("page size must be between 1 and 100")
    if day is not None:
        try:
            if date.fromisoformat(day).isoformat() != day:
                raise ValueError()
        except (ValueError, TypeError):
            raise ValueError("invalid telemetry day") from None
    saved = None
    if cursor is not None:
        try:
            if not isinstance(cursor, str) or len(cursor) > 2048:
                raise ValueError()
            saved = json.loads(base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)))
            if not isinstance(saved, dict) or set(saved) != {"start", "end", "agent", "session", "outcome", "day", "day_agent", "limit", "after"}:
                raise ValueError()
            if not isinstance(saved["after"], list) or len(saved["after"]) != 2 or not HEX_TRACE.fullmatch(saved["after"][1]):
                raise ValueError()
            instant(saved["after"][0])
        except (ValueError, TypeError, KeyError, OverflowError):
            raise ValueError("invalid telemetry cursor") from None
    first, last = period(start or (saved and saved["start"]), end or (saved and saved["end"]))
    query = {"start": first, "end": last, "agent": agent, "session": session, "outcome": outcome,
             "day": day, "day_agent": day_agent, "limit": limit}
    if saved and any(saved[key] != value for key, value in query.items()):
        raise ValueError("telemetry cursor does not match the filters")
    rows, more = reader(profile).page(agent, session, first, last, outcome, day, day_agent, limit, saved["after"] if saved else None)
    token = None
    if more:
        query["after"] = [rows[-1]["started_at"], rows[-1]["id"]]
        token = base64.urlsafe_b64encode(json.dumps(query, separators=(",", ":")).encode()).decode().rstrip("=")
    return {"traces": rows, "next_cursor": token}


def overview(profile, start=None, end=None, agent=None, session=None, outcome=None):
    _filters(agent, session, outcome)
    first, last = period(start, end)
    return reader(profile).overview(first, last, agent, session, outcome)


def trace(profile, trace_id, start=None, end=None):
    if not HEX_TRACE.fullmatch(trace_id):
        raise ValueError("invalid trace ID")
    if start is None and end is None:
        found = reader(profile).trace(trace_id)
    else:
        first, last = period(start, end)
        found = reader(profile).trace(trace_id, first, last)
    if found is None:
        raise LookupError("trace not found")
    return {"trace": found}


def usage(profile, start=None, end=None, agent=None):
    valid_agent(agent)
    first, last = period(start, end)
    return {"usage": reader(profile).usage(first, last, agent)}
