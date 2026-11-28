"""Cloud Trace and Monitoring reads for one trusted client.

Only spans and metric series bearing the client's exact marker enter the common collection. Cloud responses are
untrusted: unknown attributes, malformed ids, cross-client items and free-form text never reach the reader's caller.
"""
from __future__ import annotations

import math
import re
import uuid
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from urllib.parse import quote

from kge.adapters.deadline import ExternalCallError, request_remaining

_TRACE = "https://cloudtrace.googleapis.com/v1/projects"
_MONITORING = "https://monitoring.googleapis.com/v3/projects"
METRIC_TYPE = "custom.googleapis.com/kge/model/usage"
_AGENTS = frozenset({"kge_http", "kge_mcp", "context_ask", "context_playground", "discover_ask", "cerebro"})
_NUMERIC = frozenset({"gen_ai.usage.input_tokens", "gen_ai.usage.output_tokens", "gen_ai.usage.reasoning_tokens",
                      "kge.duration_ms", "kge.chat.input_budget", "kge.chat.output_budget", "kge.chat.reductions", "kge.model.cost_usd"})
_TEXT = frozenset({"gen_ai.request.model", "gen_ai.response.model", "kge.outcome", "kge.stage", "kge.call",
                   "kge.chat.finish_reason", "kge.chat.thinking_level"})
_FIELDS = ("input_tokens", "output_tokens", "thinking_tokens", "cost_usd")
_KINDS = frozenset({"calls", "errors", *_FIELDS, *(f"{field}_reported" for field in _FIELDS)})
_TOKEN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:/-]{0,127}\Z")
_CLIENT = re.compile(r"[a-z0-9][a-z0-9-]{0,62}\Z")
_HEX = re.compile(r"[0-9a-f]{32}\Z")
_NAME = re.compile(r"kge(?:\.[a-z_]+)+|[A-Z]{3,7}(?: /[A-Za-z0-9_/{}:.-]{0,120})?")
_MAX_PAGES = 100


def _when(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        if len(value) != 10:
            raise ValueError("telemetry time must have a timezone")
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _time(value: str) -> str:
    return _when(value).isoformat().replace("+00:00", "Z")


def _session(value: object) -> bool:
    try:
        return isinstance(value, str) and (bool(re.fullmatch(r"conv-[0-9]{1,20}", value)) or str(uuid.UUID(value)) == value)
    except (ValueError, AttributeError):
        return False


def _span_id(value: object) -> str | None:
    try:
        number = int(str(value), 10)
        return f"{number:016x}" if 0 < number < 2**64 else None
    except (ValueError, TypeError):
        return None


def _labels(raw: object, client: str) -> dict | None:
    if not isinstance(raw, dict) or raw.get("kge.client") != client:
        return None
    agent, session = raw.get("kge.agent"), raw.get("gen_ai.conversation.id")
    if not isinstance(agent, str) or agent not in _AGENTS or not _session(session):
        return None
    attrs = {"kge.client": client, "kge.agent": agent, "gen_ai.conversation.id": session}
    origin = raw.get("kge.telemetry.origin")
    if isinstance(origin, str) and origin in {"portal", "kge"}:
        attrs["kge.telemetry.origin"] = origin
    for key in _NUMERIC:
        value = raw.get(key)
        try:
            number = float(value)
            if math.isfinite(number) and number >= 0:
                attrs[key] = int(number) if number.is_integer() else number
        except (TypeError, ValueError):
            pass
    for key in _TEXT:
        value = raw.get(key)
        if isinstance(value, str) and _TOKEN.fullmatch(value):
            attrs[key] = value
    status = raw.get("http.response.status_code")
    status = int(status) if isinstance(status, (str, int)) and not isinstance(status, bool) and str(status).isdigit() else None
    status = status if status is not None and 100 <= status <= 599 else None
    if status is not None:
        attrs["http.response.status_code"] = status
    if origin == "portal":
        attrs.pop("kge.outcome", None)
        if raw.get("error.type") == "aborted":
            attrs["error.type"] = "aborted"
            attrs["kge.outcome"] = "failed"
        elif status is not None:
            attrs["kge.outcome"] = "failed" if status >= 500 else "ok"
    return attrs


def _spans(trace: object, client: str) -> list[dict]:
    if not isinstance(trace, dict) or not _HEX.fullmatch(str(trace.get("traceId", ""))):
        return []
    result = []
    for raw in trace.get("spans", []):
        if not isinstance(raw, dict):
            continue
        attrs, span_id = _labels(raw.get("labels"), client), _span_id(raw.get("spanId"))
        if attrs is None or span_id is None:
            continue
        try:
            started, ended = _time(raw["startTime"]), _time(raw["endTime"])
            duration = (_when(ended) - _when(started)).total_seconds() * 1000
            if duration < 0:
                continue
        except (KeyError, ValueError, TypeError, OverflowError):
            continue
        name = raw.get("name", "span")
        name = name if isinstance(name, str) and _NAME.fullmatch(name) else "span"
        result.append({"id": span_id, "trace_id": trace["traceId"], "parent_id": _span_id(raw.get("parentSpanId")),
                       "name": name, "started_at": started, "ended_at": ended, "duration_ms": duration,
                       "attributes": attrs})
    return result


class Cloud:
    """One client's Cloud Trace and Monitoring reader, authenticated as the runtime service account."""

    def __init__(self, client: str, *, session=None, project: str | None = None, collection=None, monitoring_verified: bool = False):
        if not isinstance(client, str) or not _CLIENT.fullmatch(client):
            raise ValueError("invalid telemetry client")
        if session is None:
            from kge.adapters.tracing.otel import cloud_session
            session, found = cloud_session()
            project = project or found
        if not isinstance(project, str) or not _TOKEN.fullmatch(project):
            raise ValueError("cloud telemetry requires a project")
        if collection is None:
            from kge.adapters.telemetry.aggregate import Collection
            collection = Collection
        self.client, self.project, self.session, self.collection = client, project, session, collection
        self.monitoring_verified = monitoring_verified

    def _get(self, url: str, params: dict) -> dict:
        remaining = request_remaining()
        try:
            response = self.session.get(url, params=params, timeout=min(10, remaining) if remaining is not None else 10)
            response.raise_for_status()
            data = response.json()
            if not isinstance(data, dict):
                raise ValueError("bad response")
            return data
        except Exception as exc:
            raise ExternalCallError("Cloud telemetry is unavailable.") from exc

    def _pages(self, url: str, params: dict, key: str):
        token = None
        seen = set()
        for _ in range(_MAX_PAGES):
            data = self._get(url, {**params, **({"pageToken": token} if token else {})})
            rows = data.get(key, [])
            if not isinstance(rows, list):
                raise ExternalCallError("Cloud telemetry returned an invalid response.")
            yield from rows
            token = data.get("nextPageToken")
            if not token:
                return
            if not isinstance(token, str) or token in seen:
                raise ExternalCallError("Cloud telemetry returned an invalid page.")
            seen.add(token)
        raise ExternalCallError("Cloud telemetry returned too many pages; use a shorter period.")

    def _all(self, start: str, end: str):
        if _when(start) >= _when(end):
            raise ValueError("telemetry start must precede end")
        url = f"{_TRACE}/{quote(self.project, safe='')}/traces"
        params = {"startTime": _time(start), "endTime": _time(end), "view": "COMPLETE", "pageSize": 100,
                  "filter": f'+kge.client:{self.client}'}
        unique: dict[tuple[str, str], dict] = {}
        for trace in self._pages(url, params, "traces"):
            for span in _spans(trace, self.client):
                key = (span["trace_id"], span["id"])
                if key in unique and unique[key] != span:
                    raise ExternalCallError("Cloud telemetry returned conflicting duplicate spans.")
                unique[key] = span
        return self.collection(unique.values())

    def agents(self, start: str, end: str):
        return self._all(start, end).agents(start, end)

    def sessions(self, agent: str, start: str, end: str):
        return self._all(start, end).sessions(agent, start, end)

    def traces(self, agent: str, session: str, start: str, end: str):
        return self._all(start, end).traces(agent, session, start, end)

    def page(self, agent, session, start, end, outcome=None, day=None, day_agent=None, limit=50, after=None):
        return self._all(start, end).page(agent, session, start, end, outcome, day, day_agent, limit, after)

    def overview(self, start, end, agent=None, session=None, outcome=None):
        if not self.monitoring_verified:
            return self._all(start, end).overview(start, end, agent, session, outcome)
        from kge.adapters.telemetry.aggregate import monitoring_overview
        return monitoring_overview([] if session or outcome else self._monitoring_usage(start, end, agent), bool(session or outcome))

    def trace(self, trace_id: str, start=None, end=None):
        if not isinstance(trace_id, str) or not _HEX.fullmatch(trace_id):
            return None
        url = f"{_TRACE}/{quote(self.project, safe='')}/traces/{trace_id}"
        data = self._get(url, {})
        unique: dict[str, dict] = {}
        for span in _spans(data, self.client):
            if span["id"] in unique and unique[span["id"]] != span:
                raise ExternalCallError("Cloud telemetry returned conflicting duplicate spans.")
            unique[span["id"]] = span
        found = self.collection(unique.values()).trace(trace_id)
        if found is None or not start or not end:
            return found
        enriched = self._all(start, end).detail(trace_id, start, end)
        return {**found, **{k: v for k, v in (enriched or {}).items() if k != "spans"}}

    def usage(self, start: str, end: str, agent: str | None):
        if not self.monitoring_verified:
            return self._all(start, end).usage(start, end, agent)
        rows = self._monitoring_usage(start, end, agent)
        return rows if rows else self._all(start, end).usage(start, end, agent)

    def _monitoring_usage(self, start: str, end: str, agent: str | None):
        if _when(start) >= _when(end):
            raise ValueError("telemetry start must precede end")
        url = f"{_MONITORING}/{quote(self.project, safe='')}/timeSeries"
        params = {"filter": f'metric.type = "{METRIC_TYPE}" AND metric.labels.client = "{self.client}"',
                  "interval.startTime": _time(start), "interval.endTime": _time(end), "view": "FULL",
                  "aggregation.alignmentPeriod": "86400s", "aggregation.perSeriesAligner": "ALIGN_SUM", "pageSize": 100}
        groups: dict[tuple[str, str, str], dict[str, float]] = defaultdict(dict)
        for series in self._pages(url, params, "timeSeries"):
            if not isinstance(series, dict):
                continue
            metric = series.get("metric", {})
            if not isinstance(metric, dict) or metric.get("type") != METRIC_TYPE:
                continue
            labels = metric.get("labels", {})
            if not isinstance(labels, dict) or labels.get("client") != self.client:
                continue
            who, model, kind = labels.get("agent"), labels.get("model"), labels.get("kind")
            if who not in _AGENTS or (agent and who != agent) or not isinstance(model, str) or not _TOKEN.fullmatch(model) or kind not in _KINDS:
                continue
            for point in series.get("points", []):
                try:
                    finish = _when(point["interval"]["endTime"])
                    beginning = _when(point["interval"]["startTime"])
                    if beginning >= finish or finish <= _when(start) or beginning >= _when(end):
                        continue
                    number = point["value"].get("doubleValue", point["value"].get("int64Value"))
                    number = float(number)
                    if not math.isfinite(number) or number < 0:
                        continue
                    date = (finish - timedelta(microseconds=1)).date().isoformat()
                    group = groups[(date, who, model)]
                    group[kind] = group.get(kind, 0) + number
                except (KeyError, TypeError, ValueError, OverflowError):
                    continue
        result = []
        for (day, who, model), values in sorted(groups.items()):
            calls, errors = values.get("calls"), values.get("errors")
            row = {"date": day, "agent": who, "model": model, "calls": int(calls) if calls is not None and calls.is_integer() else None,
                   "errors": int(errors) if errors is not None and errors.is_integer() else None}
            for field in _FIELDS:
                value = values.get(field)
                reported = values.get(f"{field}_reported", 0.0 if calls is not None else None)
                row[f"{field}_reported"] = int(reported) if reported is not None and reported.is_integer() else None
                valid = value is not None and (field == "cost_usd" or value.is_integer())
                row[field] = (float(value) if field == "cost_usd" else int(value)) if reported and valid else None
            result.append(row)
        return result
