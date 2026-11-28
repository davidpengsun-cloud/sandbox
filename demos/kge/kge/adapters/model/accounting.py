"""Per-physical-inference accounting; provider transport calls this seam before making each request."""
from __future__ import annotations

import contextlib
import math
import re
import threading
import time
import uuid
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any

from kge.adapters.modelusage import attempt_cost, current_usage


@dataclass(frozen=True)
class _Model:
    provider: str
    model: str
    input_price: float | None
    output_price: float | None
    stage: str = "unknown"


_active: ContextVar[_Model | None] = ContextVar("kge_usage_model", default=None)
_suppressed: ContextVar[bool] = ContextVar("kge_usage_suppressed", default=False)


def _price(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return value if math.isfinite(value) and value >= 0 else None


def _count(value):
    return value if isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= 2**53 - 1 else None


@contextlib.contextmanager
def model_scope(model, stage="unknown"):
    """Keep the server-selected model and prices across the call's worker and transport retries."""
    prices = model.prices or {}
    stage = stage if stage in {"agent", "catalogue", "complete", "classify", "plan", "wording"} else "unknown"
    value = _Model(model.provider, model.model, _price(prices.get("in")), _price(prices.get("out")), stage)
    token = _active.set(value)
    try:
        yield
    finally:
        _active.reset(token)


@contextlib.contextmanager
def without_inference():
    """Token-counting preflights are not inference attempts and cannot complete an invocation marker."""
    token = _suppressed.set(True)
    try:
        yield
    finally:
        _suppressed.reset(token)


def _usage(model: _Model, payload: Any, outcome: str) -> dict:
    """Only approved numeric fields leave a provider reply; unknown pricing and token counts remain unknown."""
    result = {"outcome": outcome, "input_tokens": None, "output_tokens": None, "thinking_tokens": None,
              "input_price": model.input_price, "output_price": model.output_price, "cache_mode": "unknown",
              "cache_read_tokens": None, "cache_write_tokens": None, "cache_read_price": None, "cache_write_price": None}
    if model.provider == "mock":
        usage = payload.get("usage") if isinstance(payload, dict) else None
        usage = usage if isinstance(usage, dict) else {}
        result.update(input_tokens=_count(usage.get("input_tokens")), output_tokens=_count(usage.get("output_tokens")),
                      thinking_tokens=0, input_price=0, output_price=0, cache_mode="none")
        return result
    if not isinstance(payload, dict):
        return result
    served = payload.get("modelVersion") if model.provider in ("vertex-gemini", "gemini") else payload.get("model")
    if not isinstance(served, str) or served != model.model:
        result["input_price"] = result["output_price"] = None
    if model.provider in ("vertex-gemini", "gemini"):
        usage = payload.get("usageMetadata") or {}
        if not isinstance(usage, dict):
            return result
        inp, out, reasoning, tool, total = (_count(usage.get(name)) for name in
            ("promptTokenCount", "candidatesTokenCount", "thoughtsTokenCount", "toolUsePromptTokenCount", "totalTokenCount"))
        if None not in (inp, out, total):
            remainder = total - inp - out
            if (remainder < 0 or (reasoning is not None and reasoning > remainder)
                    or (tool is not None and tool > remainder)
                    or (reasoning is not None and tool is not None and reasoning + tool != remainder)):
                inp = out = reasoning = tool = None
            elif reasoning is None and tool is not None:
                reasoning = remainder - tool if remainder >= tool else None
            elif tool is None and reasoning is not None:
                tool = remainder - reasoning if remainder >= reasoning else None
            elif reasoning is None and tool is None and remainder == 0:
                reasoning = tool = 0
        result.update(input_tokens=inp + tool if inp is not None and tool is not None else None,
                      output_tokens=out + reasoning if out is not None and reasoning is not None else None,
                      thinking_tokens=reasoning)
        if "cachedContentTokenCount" in usage:
            cached = _count(usage.get("cachedContentTokenCount"))
            result.update(cache_mode="separate", cache_read_tokens=cached, cache_write_tokens=0)
    elif model.provider in ("anthropic", "vertex-claude"):
        usage = payload.get("usage") or {}
        if not isinstance(usage, dict):
            return result
        inp, out = _count(usage.get("input_tokens")), _count(usage.get("output_tokens"))
        read, write = _count(usage.get("cache_read_input_tokens")), _count(usage.get("cache_creation_input_tokens"))
        result.update(input_tokens=inp + read + write if None not in (inp, read, write) else None,
                      output_tokens=out, thinking_tokens=None)
        if read is not None and write is not None:
            result.update(cache_mode="separate", cache_read_tokens=read, cache_write_tokens=write)
    else:
        usage = payload.get("usage") or {}
        if not isinstance(usage, dict):
            return result
        result.update(input_tokens=_count(usage.get("prompt_tokens")), output_tokens=_count(usage.get("completion_tokens")))
        reasoning = usage.get("completion_tokens_details") or {}
        if isinstance(reasoning, dict):
            result["thinking_tokens"] = _count(reasoning.get("reasoning_tokens"))
        details = usage.get("prompt_tokens_details") or {}
        if isinstance(details, dict) and "cached_tokens" in details:
            result.update(cache_mode="separate", cache_read_tokens=_count(details.get("cached_tokens")), cache_write_tokens=0)
    return result


class Attempt:
    """One actual provider inference attempt, created before the request and finished exactly once."""

    def __init__(self, model: _Model, ledger, question_id: str, invocation_id: str):
        self._model, self._ledger, self._question = model, ledger, question_id
        self._attempt = str(uuid.uuid4())
        self._finished = False
        self._lock = threading.Lock()
        if ledger is not None and not ledger.start_attempt(question_id, self._attempt, invocation_id):
            raise RuntimeError("model accounting could not reserve a new attempt")
        from opentelemetry import trace as otel_trace
        self._parent = otel_trace.get_current_span()
        self._started = time.time_ns()
        self._started_monotonic = time.perf_counter_ns()

    def finish(self, *, payload=None, outcome="failed"):
        with self._lock:
            if self._finished:
                return
            usage = _usage(self._model, payload, outcome)
            usable = self._ledger is None
            try:
                if self._ledger is not None:
                    self._ledger.finish_attempt(self._question, self._attempt, usage)
                    usable = True
            finally:
                from kge.pipeline import trace
                try:
                    cost = attempt_cost(usage) if usable else None
                except (ValueError, ArithmeticError):
                    usable, cost = False, None
                served = (payload.get("modelVersion") if self._model.provider in ("vertex-gemini", "gemini")
                          else payload.get("model")) if isinstance(payload, dict) else None
                if not isinstance(served, str) or not re.fullmatch(r"[A-Za-z0-9._:/-]{1,200}", served):
                    served = None
                elapsed = max(0, time.perf_counter_ns() - self._started_monotonic)
                end = self._started + elapsed
                trace.finished("kge.model.attempt", self._started, end,
                    {"gen_ai.request.model": self._model.model, "gen_ai.response.model": served,
                     "gen_ai.usage.input_tokens": usage["input_tokens"] if usable else None,
                     "gen_ai.usage.output_tokens": usage["output_tokens"] if usable else None,
                     "gen_ai.usage.reasoning_tokens": usage["thinking_tokens"] if usable else None,
                     "kge.model.cost_usd": cost,
                     "kge.stage": self._model.stage, "kge.duration_ms": elapsed / 1e6,
                     "kge.outcome": "ok" if outcome == "ok" else "failed"},
                    parent=self._parent, failure=None if outcome == "ok" else "failed")
                self._finished = True


def start_attempt() -> Attempt | None:
    """Reserve before an actual inference request; refuse an unattributed active scope before network I/O."""
    scope, model = current_usage(), _active.get()
    if _suppressed.get():
        return None
    if scope is None:
        from opentelemetry import trace as otel_trace
        from kge.pipeline import trace
        context = otel_trace.get_current_span().get_span_context()
        question = getattr(trace, "telemetry_current", None)
        if model is None or not context.is_valid or question is None or not question():
            return None
        return Attempt(model, None, None, None)
    ledger, question_id, invocation_id = scope
    if model is None or invocation_id is None:
        raise RuntimeError("model usage lacks a trusted invocation")
    return Attempt(model, ledger, question_id, invocation_id)
