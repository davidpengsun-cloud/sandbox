"""Actual planner inputs, unchanged selection/validation, release-scoped warnings and safe numeric telemetry."""
import json
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from kge.adapters import tracing
from kge.adapters.model import model_for
from kge.adapters.model.providers import Chat
from kge.eval import metrics, report
from kge.pipeline import trace
from kge.ports import ModelReply
from kge.services.ask.engine import planmodel, runtrace
from tests.tracing_setup import tracing_to
from tests.unit.test_ask_ai_mode import member_vocabulary, proposal
from tests.unit.test_ask_model_corrections import CLEARED, PRO, GOOD, UNDECLARED, reply


class RecordedModel:
    """A model-port stand-in records the real request and the provider's usual usage event."""
    name = "offline"

    def __init__(self, *replies):
        self.replies = list(replies)
        self.requests = []

    def structured(self, request):
        self.requests.append(request)
        trace.llm_call(self.name, self.name, 3000, 20, 1, detail={"call": request.call, "attempt": request.attempt})
        value = self.replies.pop(0)
        return ModelReply(data=value, text=json.dumps(value))


@pytest.mark.parametrize("path", ["legacy", "ai_generated"])
def test_calls_and_corrections_measure_the_emitted_vocabulary(path, monkeypatch):
    monkeypatch.setattr(planmodel, "ask_vocabulary_warn_tokens", lambda: 100000)
    vocab = member_vocabulary()
    model = RecordedModel({}, proposal() if path == "ai_generated" else {"kind": "abstain", "spans": [], "missing": ["None"]})
    with runtrace.start("ask") as held:
        if path == "legacy":
            planmodel.read(model, "Metric by category", vocab)
        else:
            first = planmodel.read_on(model, "Metric by category", vocab)
            assert first.errors
            planmodel.read_on(model, "Metric by category", vocab,
                              corrections=(planmodel.correction(first, first.problems, vocab),))
    assert len(model.requests) == len(held.calls) == 2
    for request, call in zip(model.requests, held.calls):
        public = json.loads(request.content.split("## Vocabulary\n", 1)[1].split("\n\n## ", 1)[0])
        assert call["vocabulary_entries"] == len(public["names"])
        assert call["vocabulary_tokens_estimated"] == (len(planmodel.encoded(public)) + 3) // 4
        assert call["vocabulary_warn_tokens"] == 100000
        assert call["vocabulary_exceeded"] is False
        assert (call["tokens_in"], call["tokens_out"]) == (3000, 20)
    assert held.calls[0]["vocabulary_entries"] == (24 if path == "legacy" else 19)


def test_the_all_extension_preserves_both_existing_selections():
    vocab = member_vocabulary()
    full = planmodel.payload("Metric by month", vocab)
    assert planmodel.select_vocabulary("Metric by month", full["obligations"], full["vocabulary"]) is full["vocabulary"]
    selected = planmodel.on_payload("Metric by month", vocab)["vocabulary"]
    assert len(full["vocabulary"]["names"]) == 24
    assert len(selected["names"]) == 15
    assert "value:u.events.category:CHI" not in {n["id"] for n in selected["names"]}


def test_an_extension_never_replaces_the_vocabulary_used_for_binding_or_corrections(monkeypatch):
    vocab = member_vocabulary()
    original = planmodel.bind_ids
    seen = []

    def fewer(question, obligations, permitted):
        assert question == "Metric by month" and obligations
        return {**permitted, "names": [n for n in permitted["names"] if n["id"] != "u.events.entity_id"]}

    def binding(raw, permitted):
        assert permitted is vocab
        assert "u.events.entity_id" in permitted.entries
        seen.append(permitted)
        return original(raw, permitted)

    monkeypatch.setattr(planmodel, "select_vocabulary", fewer)
    monkeypatch.setattr(planmodel, "bind_ids", binding)
    model = RecordedModel({}, proposal())
    first = planmodel.read_on(model, "Metric by month", vocab)
    correction = planmodel.correction(first, first.problems, vocab)
    planmodel.read_on(model, "Metric by month", vocab, corrections=(correction,))
    assert len(seen) == 2
    for request in model.requests:
        public = json.loads(request.content.split("## Vocabulary\n", 1)[1].split("\n\n## ", 1)[0])
        assert "u.events.entity_id" not in {n["id"] for n in public["names"]}
    assert model.requests[1].turns, "the failed contract is still corrected through the model port"


def test_extension_keeps_the_sent_id_contract_and_full_permission_checks_separate(monkeypatch):
    from kge.semantics.plancheck import CheckedPlan, check_plan
    from tests.plan_fixtures import model as plan

    vocab = member_vocabulary()

    def without_context(question, obligations, permitted):
        return {**permitted, "names": [n for n in permitted["names"] if n["id"] != "ctx"]}

    monkeypatch.setattr(planmodel, "select_vocabulary", without_context)
    written = RecordedModel(GOOD)
    reading = planmodel.read_on(written, "Metric", vocab)
    assert reading.reply is None and reading.errors
    assert any("context" in error["field"] for error in reading.errors)
    assert "ctx" not in reading.ids
    assert isinstance(check_plan(plan(), vocab, "Metric", words=False), CheckedPlan)
    assert "ctx" in vocab.entries, "selection must not mutate the permitted vocabulary"


def test_warnings_are_once_per_client_release_even_with_concurrent_calls(monkeypatch, caplog):
    monkeypatch.setattr(runtrace, "_vocabulary_warned", set())
    measurement = {"vocabulary_entries": 10, "vocabulary_tokens_estimated": 101,
                   "vocabulary_warn_tokens": 100, "vocabulary_exceeded": True}

    def call(client, release, exceeded=True):
        graph = SimpleNamespace(profile=SimpleNamespace(client=client), identity={"release": release, "hash": "same"})
        with runtrace.start("ask", graph) as held, trace.prompting("v1", "hash", vocabulary={**measurement, "vocabulary_exceeded": exceeded}):
            trace.llm_call("offline", "offline", 120, 3, 1)
        return held.calls[0]

    with caplog.at_level("WARNING", logger=runtrace.__name__):
        assert call("first", "r1", False)["vocabulary_exceeded"] is False
        assert not caplog.records
        with ThreadPoolExecutor(max_workers=4) as workers:
            calls = list(workers.map(lambda _: call("first", "r1"), range(12)))
        assert all(c["vocabulary_exceeded"] for c in calls)
        assert len(caplog.records) == 1
        call("first", "r2")
        call("second", "r1")
    assert len(caplog.records) == 3
    assert all("101 estimated tokens (10 entries)" in r.message for r in caplog.records)


def test_existing_sanitized_export_keeps_only_numeric_vocabulary_measurements():
    exporter = InMemorySpanExporter()
    measurement = {"vocabulary_entries": 24, "vocabulary_tokens_estimated": 2000,
                   "vocabulary_warn_tokens": 50000, "vocabulary_exceeded": False}
    with tracing_to(exporter), runtrace.start("ask"):
        with trace.prompting("v1", "safe-hash", vocabulary=measurement):
            trace.llm_call("offline", "offline", 3000, 20, 1)
    attributes = dict(next(s for s in exporter.get_finished_spans() if s.name == "kge.model").attributes)
    assert all(attributes[f"kge.{k}"] == v for k, v in measurement.items())
    assert attributes["gen_ai.usage.input_tokens"] == 3000
    assert "prompt_hash" not in attributes
    assert tracing.safe({"kge.vocabulary_entries": "secret", "kge.vocabulary_exceeded": "secret"}) == {}


@pytest.mark.parametrize("path", ["legacy", "ai_generated"])
def test_real_provider_accounting_exports_each_actual_attempt_with_its_vocabulary(monkeypatch, path):
    for name in CLEARED:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("MODEL_PROVIDER", "none")
    monkeypatch.setenv("MODEL_HIGH_PROVIDER", "vertex-gemini")
    monkeypatch.setenv("MODEL_HIGH_NAME", PRO)
    monkeypatch.setenv("ANSWER_MODEL_TIER", "high")
    monkeypatch.setattr(Chat, "token", lambda self: ("offline", "sample-project"))
    vocab = member_vocabulary()
    responses = [reply(UNDECLARED), reply(GOOD)] if path == "ai_generated" else [reply({}), reply({"kind": "abstain", "spans": [], "missing": ["None"]})]
    for response in responses:
        response["usageMetadata"]["toolUsePromptTokenCount"] = 0
    bodies = []

    def transport(url, headers, body, timeout, service):
        bodies.append(body)
        return responses.pop(0)

    monkeypatch.setattr("kge.adapters.model.errors._post_json", transport)
    model = model_for("answer")
    exporter = InMemorySpanExporter()
    with tracing_to(exporter), trace.telemetry_scope("sample", "context_ask", "conv-1"), runtrace.start("ask") as held:
        if path == "legacy":
            planmodel.read(model, "Metric by category", vocab)
        else:
            first = planmodel.read_on(model, "Metric by category", vocab)
            assert first.errors
            planmodel.read_on(model, "Metric by category", vocab,
                              corrections=(planmodel.correction(first, first.problems, vocab),))
    attempts = [s for s in exporter.get_finished_spans() if s.name == "kge.model.attempt"]
    assert len(bodies) == len(held.calls) == len(attempts) == 2
    for body, call, attempt in zip(bodies, held.calls, attempts):
        text = body["contents"][0]["parts"][0]["text"]
        public = json.loads(text.split("## Vocabulary\n", 1)[1].split("\n\n## ", 1)[0])
        expected = {"vocabulary_entries": len(public["names"]),
                    "vocabulary_tokens_estimated": (len(planmodel.encoded(public)) + 3) // 4}
        for name, value in expected.items():
            assert call[name] == attempt.attributes[f"kge.{name}"] == value
        assert call["tokens_in"] == attempt.attributes["gen_ai.usage.input_tokens"] == 900
        assert call["tokens_thinking"] == attempt.attributes["gen_ai.usage.reasoning_tokens"] == 300


def test_unknown_release_identity_cannot_fail_a_model_call():
    for identity in ({"release": object()}, object(), None):
        graph = SimpleNamespace(profile=SimpleNamespace(client="sample"), identity=identity)
        with runtrace.start("ask", graph), trace.prompting("v1", "hash", vocabulary={"vocabulary_exceeded": True}):
            trace.llm_call("offline", "offline", 120, 3, 1)


def test_report_takes_maxima_and_warning_across_questions_calls_and_repeats():
    small = {"vocabulary_entries": 24, "vocabulary_tokens_estimated": 2000,
             "vocabulary_warn_tokens": 3000, "vocabulary_exceeded": False}
    large = {"vocabulary_entries": 30, "vocabulary_tokens_estimated": 4000,
             "vocabulary_warn_tokens": 3000, "vocabulary_exceeded": True}
    assert metrics.vocabulary_summary([]) is None
    assert metrics.vocabulary_summary([{"tokens_in": 100}]) is None
    records = [{"repeat": i, "measures": {"vocabulary": metrics.vocabulary_summary(calls)}}
               for i, calls in enumerate(([small], [small, large]), 1)]
    assert report.vocabulary_totals(records) == {"entries_max": 30, "tokens_estimated_max": 4000,
                                                  "thresholds": [3000], "exceeded": True}
