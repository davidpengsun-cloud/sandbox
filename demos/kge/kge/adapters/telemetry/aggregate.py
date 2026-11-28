"""Provider-independent views of already-authorized, sanitized question spans."""
from __future__ import annotations

from collections import defaultdict
import math
from kge.pipeline.trace_context import instant

def _within(value: str, start: str, end: str) -> bool:
    return instant(start) <= instant(value) < instant(end)


_FIELDS = {"input_tokens": "gen_ai.usage.input_tokens", "output_tokens": "gen_ai.usage.output_tokens",
           "thinking_tokens": "gen_ai.usage.reasoning_tokens", "cost_usd": "kge.model.cost_usd"}


def _numbers(rows):
    result = {}
    for field, key in _FIELDS.items():
        values = [row[key] for row in rows if isinstance(row.get(key), (int, float)) and not isinstance(row.get(key), bool)]
        result[field] = sum(values) if values else None
        result[f"{field}_reported"] = len(values)
    return result


def _percentile(values, fraction):
    values = sorted(values)
    return values[math.ceil(len(values) * fraction) - 1] if values else None


def _attempts(trace):
    return [s for s in trace["spans"] if s["name"] == "kge.model.attempt"]


def _model_rows(traces):
    groups = defaultdict(list)
    for trace in traces:
        for span in _attempts(trace):
            attrs = span.get("attributes", {})
            model = attrs.get("gen_ai.response.model") or attrs.get("gen_ai.request.model")
            groups[model].append(attrs)
    return [{"model": model, "calls": len(rows), "errors": sum(a.get("kge.outcome") == "failed" for a in rows), **_numbers(rows)}
            for model, rows in sorted(groups.items(), key=lambda pair: str(pair[0]))]


def _figures(traces):
    attempts = [s for trace in traces for s in _attempts(trace)]
    warehouses = [s for trace in traces for s in trace["spans"] if s["name"] == "kge.warehouse"]
    durations = [t["duration_ms"] for t in traces]
    reported_outcomes = any(t["outcome"] is not None for t in traces)
    ok = sum(t["outcome"] == "ok" for t in traces)
    no_model = sum(t["outcome"] == "ok" and not any(s["name"] in ("kge.model", "kge.model.attempt", "POST /model/chat") for s in t["spans"]) for t in traces)
    attrs = [s.get("attributes", {}) for s in attempts]
    output_key, thinking_key = _FIELDS["output_tokens"], _FIELDS["thinking_tokens"]
    def reported(row, key):
        return isinstance(row.get(key), (int, float)) and not isinstance(row.get(key), bool)
    comparable = bool(attrs) and all(reported(row, output_key) == reported(row, thinking_key) for row in attrs)
    output = sum(row[output_key] for row in attrs if reported(row, output_key))
    thinking = sum(row[thinking_key] for row in attrs if reported(row, thinking_key))
    return {"sessions": len({(t["agent"], t["session_id"]) for t in traces}), "turns": len(traces),
            "ok": sum(t["outcome"] == "ok" for t in traces) if any(t["outcome"] is not None for t in traces) else None,
            "failed": sum(t["outcome"] == "failed" for t in traces) if any(t["outcome"] is not None for t in traces) else None,
            "p50_ms": _percentile(durations, .5), "p95_ms": _percentile(durations, .95), "max_ms": max(durations) if durations else None,
            "model_ms": sum(s["duration_ms"] for s in attempts), "warehouse_ms": sum(s["duration_ms"] for s in warehouses),
            "model_calls": len(attempts), "warehouse_reads": len(warehouses), "no_model_turns": no_model if reported_outcomes else None,
            "no_model_share": no_model / ok if ok else None, "thinking_share": thinking / output if comparable and output else None,
            **_numbers(attrs)}


class Collection:
    """One client's sanitized spans, normalized identically for each telemetry backend."""

    def __init__(self, spans):
        unique = {}
        conflicts = set()
        for span in spans:
            key = (span["trace_id"], span["id"])
            if key in unique and unique[key] != span:
                conflicts.add(span["trace_id"])
            else:
                unique[key] = span
        self._spans = [span for (trace_id, _), span in unique.items() if trace_id not in conflicts]
        by_trace = defaultdict(list)
        for span in self._spans:
            by_trace[span["trace_id"]].append(span)
        self._traces = {}
        for trace_id, pieces in by_trace.items():
            labeled = [s for s in pieces if s.get("attributes", {}).get("kge.agent") and s.get("attributes", {}).get("gen_ai.conversation.id")]
            if not labeled:
                continue
            portal = [s for s in labeled if s["attributes"].get("kge.telemetry.origin") == "portal"]
            source = portal or labeled
            agents = {s["attributes"]["kge.agent"] for s in source}
            sessions = {s["attributes"]["gen_ai.conversation.id"] for s in source}
            if len(agents) != 1 or len(sessions) != 1:
                continue
            ordered = sorted(pieces, key=lambda s: (s["started_at"], s["id"]))
            first = min(instant(s["started_at"]) for s in pieces)
            last = max(instant(s["ended_at"]) for s in pieces)
            roots = [s for s in pieces if s.get("parent_id") is None]
            outcome = None
            if len(roots) == 1:
                attrs = roots[0].get("attributes", {})
                explicit, status = attrs.get("kge.outcome"), attrs.get("http.response.status_code")
                if explicit in ("ok", "failed"):
                    outcome = explicit
                elif type(status) is int and 100 <= status <= 599:
                    outcome = "failed" if status >= 500 else "ok"
            self._traces[trace_id] = {"id": trace_id, "agent": next(iter(agents)), "session_id": next(iter(sessions)),
                                      "started_at": first.isoformat(), "duration_ms": max(0, (last - first).total_seconds() * 1000),
                                      "outcome": outcome, "spans": [{k: v for k, v in s.items() if k != "trace_id"} for s in ordered]}

    def agents(self, start, end):
        return [{"id": agent} for agent in sorted({t["agent"] for t in self._traces.values() if _within(t["started_at"], start, end)})]

    def sessions(self, agent, start, end):
        groups = defaultdict(list)
        for trace in self._traces.values():
            if (not agent or trace["agent"] == agent) and _within(trace["started_at"], start, end):
                groups[(trace["agent"], trace["session_id"])].append(trace)
        return [{"id": session, "agent": kind, "started_at": min(t["started_at"] for t in rows),
                 "last_at": max(t["started_at"] for t in rows), "trace_count": len(rows)}
                for (kind, session), rows in sorted(groups.items())]

    def _window(self, start, end, agent=None, session=None):
        return [t for t in self._traces.values() if _within(t["started_at"], start, end)
                and (not agent or t["agent"] == agent) and (not session or t["session_id"] == session)]

    def traces(self, agent, session, start, end):
        return [{k: v for k, v in t.items() if k != "spans"} for t in sorted(self._window(start, end, agent, session),
                key=lambda t: (t["started_at"], t["id"]), reverse=True)]

    def page(self, agent, session, start, end, outcome=None, day=None, day_agent=None, limit=50, after=None):
        window = self._window(start, end, agent, session)
        sessions = defaultdict(list)
        for t in window:
            sessions[(t["agent"], t["session_id"])].append(t)
        position = {}
        for group in sessions.values():
            ordered = sorted(group, key=lambda t: (t["started_at"], t["id"]))
            for i, trace in enumerate(ordered):
                position[trace["id"]] = (i + 1, len(ordered), ordered[i - 1]["id"] if i else None,
                                         ordered[i + 1]["id"] if i + 1 < len(ordered) else None)
        rows = []
        for t in sorted(window, key=lambda t: (t["started_at"], t["id"]), reverse=True):
            if outcome and t["outcome"] != outcome or day and t["started_at"][:10] != day or day_agent and t["agent"] != day_agent:
                continue
            if after and (t["started_at"], t["id"]) >= tuple(after):
                continue
            index, count, previous, following = position[t["id"]]
            attempts = _attempts(t)
            nums = _numbers([s.get("attributes", {}) for s in attempts])
            rows.append({**{k: v for k, v in t.items() if k != "spans"}, "turn_index": index, "session_trace_count": count,
                         "previous_trace_id": previous, "next_trace_id": following, "model_calls": len(attempts),
                         "warehouse_reads": sum(s["name"] == "kge.warehouse" for s in t["spans"]),
                         **{field: nums[field] for field in ("input_tokens", "output_tokens", "thinking_tokens")}})
        return rows[:limit], len(rows) > limit

    def overview(self, start, end, agent=None, session=None, outcome=None):
        traces = [t for t in self._window(start, end, agent, session) if not outcome or t["outcome"] == outcome]
        groups = defaultdict(list)
        for trace in traces:
            groups[(trace["started_at"][:10], trace["agent"])].append(trace)
        return {"summary": _figures(traces), "daily": [{"date": day, "agent": who, **_figures(rows), "models": _model_rows(rows)}
                for (day, who), rows in sorted(groups.items())]}

    def detail(self, trace_id, start, end):
        trace = self._traces.get(trace_id)
        if trace is None:
            return None
        rows, _ = self.page(None, None, start, end, limit=len(self._traces) + 1)
        summary = next((row for row in rows if row["id"] == trace_id), None)
        return {**trace, **(summary or {})}

    def trace(self, trace_id, start=None, end=None):
        return self._traces.get(trace_id)

    def usage(self, start, end, agent):
        groups = defaultdict(list)
        for trace in self._traces.values():
            if agent and trace["agent"] != agent:
                continue
            for span in _attempts(trace):
                if not _within(span["started_at"], start, end):
                    continue
                attrs = span.get("attributes", {})
                model = attrs.get("gen_ai.response.model") or attrs.get("gen_ai.request.model")
                groups[(span["started_at"][:10], trace["agent"], model)].append(attrs)
        return [{"date": day, "agent": who, "model": model, "calls": len(rows),
                 "errors": sum(row.get("kge.outcome") == "failed" for row in rows), **_numbers(rows)}
                for (day, who, model), rows in sorted(groups.items(), key=lambda item: tuple(str(x) for x in item[0]))]


def monitoring_overview(rows, filtered=False):
    trace_fields = ("sessions", "turns", "ok", "failed", "p50_ms", "p95_ms", "max_ms", "model_ms", "warehouse_ms", "warehouse_reads", "no_model_turns")
    model_fields = ("model_calls", "input_tokens", "output_tokens", "thinking_tokens", "cost_usd", "input_tokens_reported", "output_tokens_reported", "thinking_tokens_reported", "cost_usd_reported")
    def aggregate(values):
        out = dict.fromkeys(trace_fields)
        for key in model_fields:
            source = "calls" if key == "model_calls" else key
            parts = [row.get(source) for row in values if row.get(source) is not None]
            out[key] = sum(parts) if parts and not filtered else None
        out["no_model_share"] = None
        calls = out["model_calls"]
        output, thinking = out["output_tokens"], out["thinking_tokens"]
        out["thinking_share"] = (thinking / output if calls and output and thinking is not None
                                  and out["output_tokens_reported"] == calls and out["thinking_tokens_reported"] == calls else None)
        return out
    groups = defaultdict(list)
    for row in rows:
        groups[(row["date"], row["agent"])].append(row)
    return {"summary": aggregate(rows), "daily": [{"date": day, "agent": agent, **aggregate(items),
             "models": [{key: value for key, value in item.items() if key not in ("date", "agent")} for item in items]}
             for (day, agent), items in sorted(groups.items())]}
