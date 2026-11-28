"""OpenTelemetry traces for each question (#443), on Meridian's demo, with spans held in memory by the SDK's own exporter.

One /ask question is one trace, continuing the caller's traceparent (the portal's): an HTTP span named by its route
template, the question's span, a span per stage spell that agrees with run_trace.stages, and under them a span for each
model call (the mock's) and each warehouse job. A background run's passes, started with no start wait and advanced by
polls, share the trace its start opened. No span holds the question's text, its SQL, a row value, a document's text, the
person's email or a prompt: each is planted and looked for in every exported span. With tracing off, kge's SDK module is
never imported, the OpenTelemetry API's no-op tracer records nothing, and no tracer provider, exporter or flush thread
starts, so nothing is exported (another library may still import the SDK itself).

Opens the graph directly: stop the API first.
"""
from __future__ import annotations

import json
import time

import pytest
from fastapi.testclient import TestClient
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

import kge.adapters.tracing as tracing
from tests.scratch import profile_of, scratch_build
from tests.tracing_setup import tracing_to

WHO = "knowledge_engineer.meridian@example.com"
FIGURE = "Total retail revenue for June 2025"
PLANT = "Zyxplantq"
CALLER = "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01"


@pytest.fixture
def exported():
    return InMemorySpanExporter()


def serving(tmp_path_factory, exported, provider, batch=False):
    import kge.app as appmod
    build = scratch_build(tmp_path_factory.mktemp(f"tracing_{provider}") / "build")
    with pytest.MonkeyPatch.context() as mp, tracing_to(exported, batch=batch):
        mp.setenv("MODEL_PROVIDER", provider)
        for tier in ("HIGH", "MEDIUM", "LOW"):
            mp.setenv(f"MODEL_{tier}_PROVIDER", provider)
        mp.setenv("QUESTION_START_WAIT", "0")
        for name, value in (("PROFILE", profile_of(build)), ("_engine", None), ("_combined", None), ("_agent", None),
                            ("_ids", None)):
            mp.setattr(appmod, name, value)
        with TestClient(appmod.app) as c:
            from kge.services.identity.service import token_for
            prof = profile_of(build)
            c.as_ = lambda who: {"authorization": f"Bearer {token_for(prof.run_dir(), who)}"}
            yield c
        appmod._engine.close() if appmod._engine else None


@pytest.fixture
def client(tmp_path_factory, exported):
    """Rules (MODEL_PROVIDER=none): a figure planned, gated and run on the warehouse."""
    yield from serving(tmp_path_factory, exported, "none")


@pytest.fixture
def modelled(tmp_path_factory, exported):
    """The offline mock model, so the question's prompts and model calls exist."""
    yield from serving(tmp_path_factory, exported, "mock")


@pytest.fixture
def batched(tmp_path_factory, exported):
    """Rules, with spans batched as the OTLP and Cloud Trace exporters batch them (a five-second schedule)."""
    yield from serving(tmp_path_factory, exported, "none", batch=True)


def spans_of(exported, trace_id=None):
    held = exported.get_finished_spans()
    return [s for s in held if trace_id is None or s.context.trace_id == trace_id]


def strings(value):
    """Every string an answer holds, at any depth."""
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from strings(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            yield from strings(item)
    elif value is not None and not isinstance(value, bool):
        yield str(value)


def exported_text(spans) -> str:
    """Everything an exporter would send of the spans: names, attributes, status, events, links and resource."""
    return json.dumps([json.loads(span.to_json()) for span in spans], sort_keys=True)


def test_one_question_is_one_trace_whose_stage_spans_match_its_run_trace(client, exported):
    exported.clear()
    response = client.post("/ask", json={"question": FIGURE}, headers={**client.as_(WHO), "traceparent": CALLER})
    assert response.status_code == 200
    answer = response.json()
    caller = int(CALLER.split("-")[1], 16)
    spans = spans_of(exported)
    assert spans and {s.context.trace_id for s in spans} == {caller}
    by_id = {s.context.span_id: s for s in spans}
    http = [s for s in spans if s.name == "POST /ask"]
    assert len(http) == 1 and http[0].parent.span_id == int(CALLER.split("-")[2], 16)
    assert http[0].attributes["http.response.status_code"] == 200 and http[0].attributes["http.route"] == "/ask"
    question = [s for s in spans if s.name == "kge.ask"]
    assert len(question) == 1 and question[0].parent.span_id == http[0].context.span_id
    stages = [s for s in spans if s.name.startswith("kge.stage.")]
    assert all(s.parent.span_id == question[0].context.span_id for s in stages)
    traced = answer["run_trace"]["stages"]
    assert list(dict.fromkeys(s.attributes["kge.stage"] for s in sorted(stages, key=lambda s: s.start_time))) == [
        s["stage"] for s in traced]
    for stage in traced:
        spells = [s for s in stages if s.attributes["kge.stage"] == stage["stage"]]
        assert sum(s.end_time - s.start_time for s in spells) / 1e6 == pytest.approx(stage["ms"], abs=0.01 * len(spells) + 0.01)
    assert [s for s in spans if s.name == "kge.model"] == []
    jobs = [s for s in spans if s.name == "kge.warehouse"]
    assert jobs and all(by_id[s.parent.span_id] in stages + question for s in jobs)
    assert {s.attributes["db.system.name"] for s in jobs} == {"duckdb"}
    assert any(s.attributes.get("kge.stage") == "execute" for s in jobs)
    assert answer["behaviour"] == "figure"


def test_each_model_call_is_a_child_span_of_its_stage(modelled, exported, monkeypatch):
    """Under A1's default rules_first the rules answer the figure certified with no model call and no model span (#464
    requirement 1); under ASK_ORDER=model_first, A1's documented order (requirement 2), the model plans first."""
    exported.clear()
    certified = modelled.post("/ask", json={"question": FIGURE}, headers=modelled.as_(WHO)).json()
    assert (certified["answer_mode"], certified["answered_by"]) == ("certified", "rules")
    assert certified["run_trace"]["llm_calls"] == [] and [s for s in spans_of(exported) if s.name == "kge.model"] == []
    monkeypatch.setenv("ASK_ORDER", "model_first")
    exported.clear()
    answer = modelled.post("/ask", json={"question": FIGURE}, headers=modelled.as_(WHO)).json()
    spans = spans_of(exported)
    by_id = {s.context.span_id: s for s in spans}
    models = sorted((s for s in spans if s.name == "kge.model"), key=lambda s: s.start_time)
    calls = answer["run_trace"]["llm_calls"]
    assert len(models) == len(calls) > 0
    for span, call in zip(models, calls):
        assert span.attributes["gen_ai.request.model"] == call["model"]
        assert span.attributes["gen_ai.usage.input_tokens"] == call["tokens_in"]
        assert span.attributes["gen_ai.usage.output_tokens"] == call["tokens_out"]
        assert span.attributes["kge.stage"] == call["stage"]
        assert by_id[span.parent.span_id].name == f"kge.stage.{call['stage']}"


def test_a_questions_spans_are_exported_before_its_response_completes(batched, exported):
    """Cloud Run gives an instance no CPU once its response is sent: the batch is flushed first, so the question's spans
    have reached the exporter by the time the caller holds the answer, well inside the batch's own five seconds."""
    exported.clear()
    started = time.monotonic()
    response = batched.post("/ask", json={"question": FIGURE}, headers={**batched.as_(WHO), "traceparent": CALLER})
    names = {s.name for s in spans_of(exported)}
    assert response.status_code == 200 and time.monotonic() - started < 5
    assert {"POST /ask", "kge.ask", "kge.warehouse"} <= names
    assert any(n.startswith("kge.stage.") for n in names)


def test_assists_routing_call_is_a_model_span_of_its_request(modelled, exported):
    """/assist routes the question with a model before any question's run trace opens: that call is a model span of
    the request, with no question text, and run_trace is as before."""
    exported.clear()
    question = f"Who owns the Monthly Revenue product {PLANT}?"
    response = modelled.post("/assist", json={"question": question}, headers={**modelled.as_(WHO), "traceparent": CALLER})
    assert response.status_code == 200, response.text
    spans = spans_of(exported, int(CALLER.split("-")[1], 16))
    http = next(s for s in spans if s.name == "POST /assist")
    routed = [s for s in spans if s.name == "kge.model" and s.parent.span_id == http.context.span_id]
    assert routed
    assert routed[0].attributes["gen_ai.request.model"] and routed[0].attributes["kge.outcome"] == "ok"
    assert "kge.stage" not in routed[0].attributes and "kge.run_id" not in routed[0].attributes
    text = exported_text(spans)
    assert PLANT not in text and WHO not in text


def test_a_background_runs_passes_share_one_trace(client, exported):
    exported.clear()
    started = client.post("/ask?background=true", json={"question": FIGURE}, headers=client.as_(WHO))
    assert started.status_code in (200, 202)
    run = started.json()
    for _ in range(400):
        if run["state"] != "running":
            break
        time.sleep(0.02)
        run = client.get(f"/ask/runs/{run['id']}", headers=client.as_(WHO)).json()
    assert run["state"] == "done", run.get("error")
    passes = [s for s in spans_of(exported) if s.name == "kge.question_run.pass"]
    assert len(passes) >= 2
    assert len({s.context.trace_id for s in passes}) == 1
    assert {s.attributes["kge.question_run"] for s in passes} == {run["id"]}
    trace_id = passes[0].context.trace_id
    names = {s.name for s in spans_of(exported, trace_id)}
    assert {"kge.question_run", "kge.ask", "kge.warehouse"} <= names
    polls = [s for s in spans_of(exported) if s.name == "GET /ask/runs/{run_id}"]
    assert polls and all(s.context.trace_id != trace_id for s in polls)
    requests = {s.context.span_id for s in spans_of(exported) if s.name in ("POST /ask", "GET /ask/runs/{run_id}")}
    assert all(len(s.links) == 1 and s.links[0].context.span_id in requests for s in passes)
    assert any(s.links[0].context.trace_id != trace_id for s in passes)


def test_live_ndjson_questions_keep_their_spans_in_the_request_and_run_traces(client, exported):
    from tests.integration.test_ask_live_trace import NDJSON, background, lines
    exported.clear()
    direct = lines(client.post("/ask", json={"question": FIGURE}, headers={**client.as_(WHO), **NDJSON, "traceparent": CALLER}).text)
    assert direct[-1]["event"] == "answer"
    spans = spans_of(exported, int(CALLER.split("-")[1], 16))
    http = next(s for s in spans if s.name == "POST /ask")
    question = next(s for s in spans if s.name == "kge.ask")
    assert question.parent.span_id == http.context.span_id
    assert any(s.name == "kge.warehouse" for s in spans)
    exported.clear()
    events = background(client, FIGURE, client.as_(WHO))
    assert events[-1]["event"] == "answer"
    passes = [s for s in spans_of(exported) if s.name == "kge.question_run.pass"]
    assert passes and len({s.context.trace_id for s in passes}) == 1
    assert {"kge.question_run", "kge.ask"} <= {s.name for s in spans_of(exported, passes[0].context.trace_id)}


@pytest.mark.parametrize("serving_as", ["client", "modelled"])
def test_no_client_content_reaches_a_span(serving_as, request, exported):
    """On rules (a figure, its SQL and rows) and on the mock model (its prompts): nothing planted, nothing the answers
    or prompts hold, reaches a span."""
    from kge.adapters.model.mock import ChatMock
    served = request.getfixturevalue(serving_as)
    prompts = []
    generate = ChatMock._generate
    exported.clear()
    answers = []
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(ChatMock, "_generate", lambda self, messages, *a, **kw: (
            prompts.extend(str(m.content) for m in messages), generate(self, messages, *a, **kw))[1])
        for path, question in (("/ask", FIGURE), ("/ask", f"{FIGURE} for {PLANT}"), ("/answer", f"{FIGURE} {PLANT}"),
                               ("/ask?background=true", f"{FIGURE} {PLANT}"),
                               ("/ask", f"What is the definition of an active customer {PLANT}?"),
                               ("/ask", "What is the definition of an active customer?")):
            answers.append(served.post(path, json={"question": question}, headers=served.as_(WHO)).json())
        served.get(f"/map/{PLANT}?q={PLANT}", headers=served.as_(WHO))
    if serving_as == "modelled":
        assert prompts
    else:
        assert any(a.get("behaviour") == "figure" for a in answers)
    spans = spans_of(exported)
    text = exported_text(spans)
    assert PLANT not in text and WHO not in text and "@example.com" not in text
    assert "GET /map/{term:path}" in {s.name for s in spans}
    planted = set()
    for answer in answers:
        for key in ("answer_text", "sql", "figures", "citations", "rows", "plan", "context", "display", "result"):
            planted.update(s for s in strings(answer.get(key)) if len(s) >= 12)
    planted.update(line for prompt in prompts for line in prompt.splitlines() if len(line) >= 24)
    assert any("SELECT" in s for s in planted) if serving_as == "client" else prompts
    leaked = sorted(s for s in planted if s in text)
    assert not leaked
    for span in spans:
        assert set(span.attributes) <= tracing.SAFE_KEYS
        assert span.status.description is None and not span.events


def test_with_tracing_off_nothing_loads_and_nothing_is_exported(tmp_path):
    import subprocess
    import sys
    code = ("import sys\n"
            "import kge.adapters.tracing as tracing\n"
            "from opentelemetry import trace as api\n"
            "from kge.services.ask.engine import runtrace\n"
            "tracing.check_settings()\n"
            "with runtrace.start('ask') as trace:\n"
            "    trace.stage('understand')\n"
            "    runtrace.llm_call('m', None, 1, 1, 1.0)\n"
            "    recording = trace.span.is_recording()\n"
            "import threading\n"
            "print(type(api.get_tracer_provider()).__name__, recording, 'kge.adapters.tracing.otel' in sys.modules,\n"
            "      bool(tracing._set_up), any(m.startswith('opentelemetry.exporter') for m in sys.modules),\n"
            "      any('Span' in th.name or th.name == 'trace-flush' for th in threading.enumerate()))\n")
    env = {k: v for k, v in __import__("os").environ.items() if k != "TRACING"}
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=env, check=True)
    assert out.stdout.split() == ["ProxyTracerProvider", "False", "False", "False", "False", "False"]
    assert out.stderr == ""
