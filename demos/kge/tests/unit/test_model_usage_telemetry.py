from types import SimpleNamespace

import pytest

from kge.adapters.model.accounting import Attempt
from kge.adapters.modelusage import attempt_cost
from kge.pipeline import trace


class Ledger:
    def __init__(self, failure=False):
        self.failure = failure
        self.finished = []

    def start_attempt(self, question, attempt, invocation):
        return True

    def finish_attempt(self, question, attempt, usage):
        if self.failure:
            raise RuntimeError("write failed")
        self.finished.append((question, attempt, usage))


def model():
    return SimpleNamespace(provider="vertex-gemini", model="gemini-3.1-pro", input_price=1.0, output_price=2.0, stage="agent")


def payload():
    return {"modelVersion": "gemini-3.1-pro", "usageMetadata": {"promptTokenCount": 100, "candidatesTokenCount": 20,
            "thoughtsTokenCount": 5, "cachedContentTokenCount": 0, "totalTokenCount": 125}}


def capture(monkeypatch):
    spans = []
    monkeypatch.setattr(trace, "finished", lambda name, start, end, attrs, **kw: spans.append((name, attrs)))
    return spans


def test_attempt_span_uses_persisted_usage_and_ledger_cost_once(monkeypatch):
    spans, book = capture(monkeypatch), Ledger()
    attempt = Attempt(model(), book, "question", "invocation")
    attempt.finish(payload=payload(), outcome="ok")
    attempt.finish(payload=payload(), outcome="ok")
    assert len(book.finished) == len(spans) == 1
    assert spans[0][0] == "kge.model.attempt"
    attrs = spans[0][1]
    assert attrs["gen_ai.usage.input_tokens"] == 100
    assert attrs["gen_ai.usage.output_tokens"] == 25
    assert attrs["gen_ai.usage.reasoning_tokens"] == 5
    assert attrs["kge.model.cost_usd"] == pytest.approx(0.00015)
    assert attrs["kge.model.cost_usd"] == attempt_cost(book.finished[0][2])


def test_unknown_usage_stays_unknown_in_attempt_span(monkeypatch):
    spans, book = capture(monkeypatch), Ledger()
    Attempt(model(), book, "question", "invocation").finish(payload={"modelVersion": "other"}, outcome="ok")
    assert spans[0][1]["gen_ai.usage.reasoning_tokens"] is None
    assert spans[0][1]["kge.model.cost_usd"] is None


def test_failed_ledger_write_does_not_report_usage_as_persisted(monkeypatch):
    spans = capture(monkeypatch)
    with pytest.raises(RuntimeError, match="write failed"):
        Attempt(model(), Ledger(failure=True), "question", "invocation").finish(payload=payload(), outcome="ok")
    attrs = spans[0][1]
    for name in ("gen_ai.usage.input_tokens", "gen_ai.usage.output_tokens", "gen_ai.usage.reasoning_tokens", "kge.model.cost_usd"):
        assert attrs[name] is None


def traced_question(monkeypatch, metadata):
    from opentelemetry import context, trace as otel
    from opentelemetry.trace import NonRecordingSpan, SpanContext, TraceFlags
    span = NonRecordingSpan(SpanContext(trace_id=0x1234, span_id=0x5678, is_remote=False, trace_flags=TraceFlags(1)))
    token = context.attach(otel.set_span_in_context(span))
    monkeypatch.setattr(trace, "telemetry_current", lambda: metadata, raising=False)
    return token


def test_no_ledger_question_counts_each_real_provider_retry(monkeypatch):
    from opentelemetry import context
    from kge.adapters.model import errors, providers
    from kge.adapters.model.accounting import model_scope

    spans = capture(monkeypatch)
    calls = []
    def post(*args):
        calls.append(1)
        if len(calls) == 1:
            raise errors.ExternalCallError("transient", transient=True)
        return payload()
    monkeypatch.setattr(errors, "_post_json", post)
    token = traced_question(monkeypatch, {"kge.client": "meridian", "kge.agent": "context"})
    selected = SimpleNamespace(provider="vertex-gemini", model="gemini-3.1-pro", prices={"in": 1.0, "out": 2.0})
    try:
        with model_scope(selected, "plan"):
            assert providers.post("https://example.test", {}, {}) == payload()
    finally:
        context.detach(token)
    assert len(calls) == len(spans) == 2
    assert [s[1]["kge.outcome"] for s in spans] == ["failed", "ok"]
    assert spans[0][1]["kge.model.cost_usd"] is None
    assert spans[1][1]["kge.model.cost_usd"] == pytest.approx(0.00015)


def test_unregistered_traced_job_and_token_preflight_emit_no_attempt(monkeypatch):
    from opentelemetry import context
    from kge.adapters.model.accounting import model_scope, start_attempt, without_inference

    selected = SimpleNamespace(provider="vertex-gemini", model="gemini-3.1-pro", prices={})
    token = traced_question(monkeypatch, {})
    try:
        with model_scope(selected, "plan"):
            assert start_attempt() is None
        monkeypatch.setattr(trace, "telemetry_current", lambda: {"kge.client": "meridian"}, raising=False)
        with model_scope(selected, "plan"), without_inference():
            assert start_attempt() is None
    finally:
        context.detach(token)


def test_no_ledger_malformed_provider_usage_never_breaks_the_answer(monkeypatch):
    from opentelemetry import context
    from kge.adapters.model import errors, providers
    from kge.adapters.model.accounting import model_scope

    spans = capture(monkeypatch)
    reply = payload()
    reply["usageMetadata"]["cachedContentTokenCount"] = 101
    monkeypatch.setattr(errors, "_post_json", lambda *_args: reply)
    token = traced_question(monkeypatch, {"kge.client": "meridian", "kge.agent": "context"})
    selected = SimpleNamespace(provider="vertex-gemini", model="gemini-3.1-pro", prices={"in": 1.0, "out": 2.0})
    try:
        with model_scope(selected, "plan"):
            assert providers.post("https://example.test", {}, {}) == reply
    finally:
        context.detach(token)
    assert len(spans) == 1
    assert spans[0][1]["gen_ai.usage.input_tokens"] is None
    assert spans[0][1]["kge.model.cost_usd"] is None
