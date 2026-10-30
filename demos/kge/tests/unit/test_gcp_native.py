"""The pilot's cloud path (kge/pipeline/settings.py gcp_native, any BigQuery backend on, GRAPH_STORE=bigquery among
them): the image's modules load no DuckDB, Ladybug or Kuzu package when imported, the warehouses and retrieval open
BigQuery's adapters and the graph BigQuery's store, never the local ones; a question is answered end to end with the
graph in BigQuery (its REST API replayed from the twin's answers, tests/graphtwin.py) and no Ladybug or Kuzu module
loaded, and a configured model that fails stops the step (kge/adapters/model/errors.py NoFallback) where the local stack
falls back to rules, a template or the tagger's rule proposals. The local stack keeps its fallbacks, each tested beside
its cloud twin. Every model, BigQuery client and embedder here is a stand-in."""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from kge.adapters.model import ExternalCallError, ModelSettingError, NoFallback, NotJSON, no_fallback
from kge.estate import draftflow
from kge.pipeline import tagger as tagging
from kge.pipeline.settings import gcp_native
from kge.services.ask.engine import agent as agent_module
from kge.services.ask.engine.combined import Combined

DEMO = Path(__file__).resolve().parents[2]
BACKENDS = ("WAREHOUSE_BACKEND", "RETRIEVAL_BACKEND", "APP_STATE_BACKEND", "GRAPH_STORE")
LOCAL_PACKAGES = ("duckdb", "ladybug", "kuzu")
LOCAL_ADAPTERS = ("kge.adapters.warehouse.duckdb", "kge.adapters.retrieval.graph", "kge.adapters.graphstore.ladybug")

PROBE = """
import json, sys
import kge.app, kge.estate.onboard, kge.estate.draftflow, kge.services.ask, kge.services.onboard.jobs
import kge.pipeline.run, kge.pipeline.objstore, kge.graph.load, kge.graph.release
import kge.estate.generate, kge.estate.from_files, kge.estate.certify, kge.estate.check_estate, kge.estate.load_bigquery
import kge.estate.golden, kge.estate.heldout, kge.estate.golden_wealth, kge.estate.ddl, kge.eval.margin
from kge.adapters import graphstore, retrieval, warehouse
from kge.adapters.retrieval import bigquery as bq_retrieval
from kge.adapters.warehouse import bigquery as bq_warehouse
from kge.pipeline.profile import resolve


class StandIn:
    label = ""

    def __init__(self, *args, **kwargs):
        self.args = args


def stand_in(label):
    return type(label, (StandIn,), {"label": label})


bq_warehouse.BigQueryWarehouses = stand_in("bigquery warehouses")
bq_retrieval.BigQueryRetrieval = stand_in("bigquery retrieval")
if sys.argv[1] == "local":
    from kge.adapters.retrieval import graph as graph_retrieval
    from kge.adapters.warehouse import duckdb as duckdb_warehouse
    duckdb_warehouse.Warehouses = stand_in("duckdb warehouses")
    graph_retrieval.GraphRetrieval = stand_in("graph retrieval")
prof = resolve("meridian")
opened = [warehouse.open_warehouses(prof, ["retail"], [], {}, {}).label, retrieval.open_retrieval(object()).label,
          type(warehouse.estate_of(prof)).__name__, graphstore.store().name]
print(json.dumps({"loaded": sorted(m for m in sys.modules if m.split(".")[0] in ("duckdb", "ladybug", "kuzu")
                                   or m in ("kge.adapters.warehouse.duckdb", "kge.adapters.retrieval.graph",
                                            "kge.adapters.graphstore.ladybug")
                                   or m.startswith("google.cloud")),
                  "opened": opened}))
"""


def probe(env: dict, step: str) -> dict:
    """A fresh interpreter, so that no module another test imported is counted: what the image's modules load, and which
    adapter opens the warehouses and retrieval, each a stand-in, so nothing is built or called. On the cloud path the local
    adapters are not stood in for: opening one would import it, and the probe would list it."""
    clean = {k: v for k, v in os.environ.items() if k not in BACKENDS and k not in ("PROFILE", "PROFILES")}
    out = subprocess.run([sys.executable, "-c", PROBE, step], cwd=DEMO, env={**clean, **env}, capture_output=True,
                         text=True, timeout=120)
    assert out.returncode == 0, out.stderr[-2000:]
    return json.loads(out.stdout.strip().splitlines()[-1])


@pytest.fixture
def cloud(monkeypatch):
    for name in BACKENDS:
        monkeypatch.setenv(name, "bigquery")
    return monkeypatch


@pytest.fixture
def local(monkeypatch):
    for name in BACKENDS:
        monkeypatch.delenv(name, raising=False)
    return monkeypatch


@pytest.mark.parametrize("name", BACKENDS)
def test_any_bigquery_backend_puts_the_process_on_the_cloud_path(monkeypatch, name):
    for other in BACKENDS:
        monkeypatch.delenv(other, raising=False)
    assert not gcp_native()
    monkeypatch.setenv(name, " BigQuery ")
    assert gcp_native()
    monkeypatch.setenv(name, "duckdb")
    assert not gcp_native()


def test_the_image_modules_import_no_local_store_on_the_cloud_path():
    """Importing the app, the onboarding core, the jobs, the pipeline, the estate's builders, certification, the sync
    and the margin check loads no duckdb, ladybug or kuzu package (nor a google.cloud one) and neither local adapter;
    the warehouses and retrieval open BigQuery's adapters, here the probe's stand-ins for them, and the estate's
    warehouses are BigQuery's (kge/adapters/warehouse/bigquery_estate.py). Running a step on that path is
    tests/unit/test_bigquery_estate.py's, with DuckDB taken away."""
    seen = probe({name: "bigquery" for name in BACKENDS}, "cloud")
    assert seen["loaded"] == []
    assert seen["opened"] == ["bigquery warehouses", "bigquery retrieval", "BigQueryEstate", "bigquery"]


def test_the_local_stack_opens_its_duckdb_warehouses_and_graph_retrieval():
    """The control: with every backend unset the same probe opens the local adapters' stand-ins, and their modules load
    DuckDB."""
    seen = probe({}, "local")
    assert "duckdb" in seen["loaded"] and "kge.adapters.warehouse.duckdb" in seen["loaded"]
    assert seen["opened"] == ["duckdb warehouses", "graph retrieval", "DuckDBEstate", "ladybug"]


ASK = """
import json, os, sys
from kge.pipeline.envfile import load_env
load_env()
if os.environ.get("REPLAY"):
    from pathlib import Path
    from kge.adapters.graphstore import bigquery
    answers = json.loads(Path(os.environ["REPLAY"]).read_text())

    def replay(self, url, body):
        import hashlib
        key = hashlib.sha256(json.dumps([body["query"], body.get("queryParameters") or []], sort_keys=True).encode()).hexdigest()
        if key not in answers:
            raise AssertionError("the replay holds no answer to: " + body["query"][:300])
        return answers[key]
    bigquery.Client._post = replay
from kge.services.ask.engine.core import Engine
from kge.pipeline.profile import resolve
eng = Engine(resolve())
out = {k: v for k, v in eng.answer(sys.argv[1]).items() if k != "run_trace"}
loaded = sorted(m for m in sys.modules if m.split(".")[0] in ("ladybug", "kuzu", "real_ladybug")
                or m == "kge.adapters.graphstore.ladybug")
print(json.dumps({"answer": out, "store": eng.g.profile.graph_path().name, "loaded": loaded}, sort_keys=True, default=str))
"""
QUESTIONS = ("Total retail revenue for June 2025", "What does net interest margin mean?",
             "Total revenue across all units for Q2 2025")


def asked(build, question: str, env: dict, *twin: str) -> dict:
    import tests.scratch as scratch
    script = Path(build) / "ask.py"
    script.write_text(ASK)
    command = [sys.executable, "-m", "tests.graphtwin", *twin, str(script)] if twin else [sys.executable, str(script)]
    clean = {k: v for k, v in os.environ.items() if k not in BACKENDS and k not in ("PROFILE", "PROFILES")}
    out = subprocess.run([*command, question], cwd=DEMO, capture_output=True, text=True, timeout=600,
                         env={**clean, "PROFILES": str(scratch.registry(build)), "PROFILE": "", **env})
    assert out.returncode == 0, out.stderr[-3000:]
    return json.loads(out.stdout.strip().splitlines()[-1])


def test_a_question_is_answered_end_to_end_with_the_graph_in_bigquery_and_no_ladybug_loaded(tmp_path):
    """The graph store is BigQuery (GRAPH_STORE=bigquery) in a fresh interpreter whose BigQuery REST API replays what the
    twin answered the same questions with: each answer is the local stack's, the graph opened is graph.bigquery, and no
    Ladybug or Kuzu module, nor Ladybug's adapter, is loaded. The warehouses stay the local ones here, as only the graph's
    path is under test; the import probe above holds the rest of the cloud path to none."""
    import tests.scratch as scratch
    import yaml
    from kge.adapters.graphstore.bigquery import Graph, prefix_of
    from kge.adapters.graphstore.gql import Schema
    build = scratch.scratch_build(tmp_path / "build")
    prof = scratch.profile_of(build)
    lb = prof.graph_path()
    artifact = prof.artifacts / prof.client / prof.model_hash()
    os.environ.setdefault("BIGQUERY_PROJECT", "twin-project")
    graph = Graph(lb.with_name("graph.bigquery"))
    graph.ns.prefix = prefix_of(prof.model_hash(), "00000000")
    graph.write(Schema.from_graph_model(yaml.safe_load((artifact / "graph-model.yaml").read_text())))
    answers = tmp_path / "answers.json"
    cloud = {"GRAPH_STORE": "bigquery", "BIGQUERY_PROJECT": "twin-project"}
    for question in QUESTIONS:
        local = asked(build, question, {})
        assert local["store"] == "graph.ladybug"
        asked(build, question, {**cloud, "GRAPHTWIN_ANSWERS": str(answers)}, str(lb), "-")
        replayed = asked(build, question, {**cloud, "REPLAY": str(answers)})
        assert replayed["store"] == "graph.bigquery" and replayed["loaded"] == []
        assert replayed["answer"] == local["answer"], question


class Failing:
    """A model whose every call fails, as a provider that is down does; its message carries a secret."""
    name = "test:failing"
    provider = "vertex-gemini"
    model = "failing"

    def __init__(self, exc: BaseException | None = None):
        self.exc = exc or RuntimeError("token sk-secret-value refused")

    def json(self, prompt, max_tokens=None):
        raise self.exc

    def complete(self, prompt, max_tokens=None):
        raise self.exc


def test_no_fallback_names_the_type_never_the_text_and_keeps_an_external_calls_line(cloud):
    with pytest.raises(NoFallback) as caught:
        no_fallback("a step", RuntimeError("token sk-secret-value refused"))
    assert "RuntimeError" in str(caught.value) and "sk-secret" not in str(caught.value)
    assert isinstance(caught.value, ExternalCallError)
    with pytest.raises(NoFallback, match=r"Vertex AI error \(HTTP 429\): quota"):
        no_fallback("a step", ExternalCallError("Vertex AI error (HTTP 429): quota"))
    with pytest.raises(NoFallback, match="retries exhausted"):
        no_fallback("a step", "retries exhausted after 3 replies")


def test_no_fallback_returns_on_the_local_stack(local):
    assert no_fallback("a step", RuntimeError("down")) is None


def test_the_model_tagger_stops_on_a_failed_call_in_the_cloud(cloud):
    tagger = tagging.ModelTagger.configured(["revenue"], Failing())
    with pytest.raises(NoFallback, match="the tagger"):
        tagger.propose("Revenue rose in June.", "certified", "policy")


def test_the_model_tagger_stops_on_a_reply_that_is_not_json_in_the_cloud(cloud):
    class Prose(Failing):
        def complete(self, prompt, max_tokens=None):
            return "no JSON here"
    with pytest.raises(NoFallback, match="the tagger"):
        tagging.ModelTagger.configured(["revenue"], Prose()).propose("Revenue rose in June.", "certified", "policy")


def test_the_model_tagger_retries_a_transient_error_or_truncated_reply_in_the_cloud(cloud):
    replies = iter([ExternalCallError("Vertex AI unreachable: read timed out", transient=True), '{"mentions": [',
                    '{"mentions": [{"term": "revenue", "confidence": 0.9, "evidence": "Revenue rose"}]}'])

    class Flaky(Failing):
        def complete(self, prompt, max_tokens=None):
            item = next(replies)
            if isinstance(item, Exception):
                raise item
            return item

    proposals = tagging.ModelTagger.configured(["revenue"], Flaky()).propose("Revenue rose in June.", "certified", "policy")
    assert any(p.family == "business_meaning" and p.value == "revenue" and p.generator == "auto" for p in proposals)


class Scripted(Failing):
    """A model that answers each call with the next of replies (an exception is raised), counting the calls."""

    def __init__(self, *replies):
        super().__init__()
        self.replies, self.calls = list(replies), 0

    def complete(self, prompt, max_tokens=None):
        self.calls += 1
        item = self.replies[min(self.calls, len(self.replies)) - 1]
        if isinstance(item, BaseException):
            raise item
        return item


def tag(model) -> list:
    return tagging.ModelTagger.configured(["revenue"], model).propose("Revenue rose in June.", "certified", "policy")


@pytest.mark.parametrize("exc", [ExternalCallError("Vertex AI error (HTTP 503): unavailable", transient=True),
                                 ExternalCallError("Vertex AI error (HTTP 429): quota", transient=True),
                                 ExternalCallError("Vertex AI unreachable: timed out", transient=True), TimeoutError("read")])
def test_the_model_tagger_asks_a_transient_failure_again_three_times_at_most(local, exc):
    model = Scripted(exc)
    proposals = tag(model)
    assert model.calls == tagging.TAGGER_RETRIES + 1 == 3
    assert any(p.value == "__model_error__" for p in proposals)


@pytest.mark.parametrize("exc", [ExternalCallError("Vertex AI error (HTTP 401): bad key"),
                                 ExternalCallError("Vertex AI error (HTTP 400): bad request"),
                                 ModelSettingError("MODEL_LOW_PROVIDER names no provider"), RuntimeError("token sk-secret-value")])
def test_the_model_tagger_never_asks_a_refusal_or_a_setting_again(cloud, exc):
    model = Scripted(exc)
    with pytest.raises(NoFallback, match="the tagger"):
        tag(model)
    assert model.calls == 1


def test_the_model_tagger_asks_a_reply_that_is_not_json_again_three_times_at_most(local):
    model = Scripted("no JSON here")
    proposals = tag(model)
    assert model.calls == 3 and any(p.value == "__model_error__" and "not JSON" in p.evidence for p in proposals)


def test_the_model_tagger_takes_a_trailing_comma_out_of_a_reply_that_does_not_parse(local):
    model = Scripted('{"mentions": [{"term": "revenue", "confidence": 0.9, "evidence": "Revenue rose",},], "candidates": [],}')
    proposals = tag(model)
    assert model.calls == 1
    assert [p.evidence for p in proposals if p.value == "revenue" and p.generator == "auto"] == ["Revenue rose"]


def test_the_model_tagger_leaves_a_valid_reply_s_strings_as_they_are(local):
    model = Scripted('{"mentions": [{"term": "revenue", "confidence": 0.9, "evidence": "items [a, ] and {b, }"}], "candidates": []}')
    proposals = tag(model)
    assert model.calls == 1
    assert [p.evidence for p in proposals if p.value == "revenue" and p.generator == "auto"] == ["items [a, ] and {b, }"]


@pytest.mark.parametrize("code, again", [(429, True), (500, True), (503, True), (400, False), (401, False), (403, False),
                                         (404, False)])
def test_post_json_says_which_failures_may_pass_if_made_again(monkeypatch, code, again):
    import io
    import urllib.error
    import urllib.request
    from kge.adapters.model import post_json, transient

    def refuse(req, timeout=None):
        raise urllib.error.HTTPError(req.full_url, code, "no", {}, io.BytesIO(b'{"error": {"message": "no"}}'))
    monkeypatch.setattr(urllib.request, "urlopen", refuse)
    with pytest.raises(ExternalCallError) as caught:
        post_json("https://aiplatform.googleapis.com/v1/x", {}, {})
    assert caught.value.transient is again and transient(caught.value) is again

    def unreachable(req, timeout=None):
        raise urllib.error.URLError("timed out")
    monkeypatch.setattr(urllib.request, "urlopen", unreachable)
    with pytest.raises(ExternalCallError) as caught:
        post_json("https://aiplatform.googleapis.com/v1/x", {}, {})
    assert transient(caught.value) and not transient(NoFallback("stopped", transient=True))


def test_the_model_tagger_keeps_the_rules_proposals_on_the_local_stack(local):
    proposals = tagging.ModelTagger.configured(["revenue"], Failing()).propose("Revenue rose in June.", "certified", "policy")
    assert any(p.value == "__model_error__" and "RuntimeError" in p.evidence for p in proposals)


def test_tagger_provider_model_with_no_model_is_refused_in_the_cloud(cloud):
    cloud.setenv("TAGGER_PROVIDER", "model")
    cloud.setenv("MODEL_PROVIDER", "none")
    for name in ("MODEL_LOW_PROVIDER", "MODEL_LOW_NAME", "TAGGER_MODEL_TIER"):
        cloud.delenv(name, raising=False)
    with pytest.raises(ModelSettingError, match="does not fall back to rules"):
        tagging.make_tagger(["revenue"])


@pytest.mark.parametrize("tier", ["default", "low"])
def test_tagger_provider_model_with_only_the_mock_is_refused_in_the_cloud(cloud, tier):
    """The mock answers only the reference agent's prompts, so it never tags: on the cloud path it is refused as no
    model is, never a quiet RuleTagger (Grok on #97); the local stack keeps the rules."""
    cloud.setenv("TAGGER_PROVIDER", "model")
    for name in ("MODEL_LOW_PROVIDER", "MODEL_LOW_NAME", "TAGGER_MODEL_TIER"):
        cloud.delenv(name, raising=False)
    cloud.setenv("MODEL_PROVIDER", "mock" if tier == "default" else "none")
    if tier == "low":
        cloud.setenv("MODEL_LOW_PROVIDER", "mock")
    with pytest.raises(ModelSettingError, match="the mock never tags"):
        tagging.make_tagger(["revenue"])


def test_tagger_provider_model_with_only_the_mock_tags_by_rules_on_the_local_stack(local):
    local.setenv("TAGGER_PROVIDER", "model")
    local.setenv("MODEL_PROVIDER", "mock")
    for name in ("MODEL_LOW_PROVIDER", "MODEL_LOW_NAME", "TAGGER_MODEL_TIER"):
        local.delenv(name, raising=False)
    assert type(tagging.make_tagger(["revenue"])) is tagging.RuleTagger


def test_tagger_provider_model_with_no_model_tags_by_rules_on_the_local_stack(local):
    local.setenv("TAGGER_PROVIDER", "model")
    local.setenv("MODEL_PROVIDER", "none")
    for name in ("MODEL_LOW_PROVIDER", "MODEL_LOW_NAME", "TAGGER_MODEL_TIER"):
        local.delenv(name, raising=False)
    assert type(tagging.make_tagger(["revenue"])) is tagging.RuleTagger


def test_explicit_rules_stay_a_choice_in_the_cloud(cloud):
    cloud.setenv("TAGGER_PROVIDER", "rules")
    assert type(tagging.make_tagger(["revenue"])) is tagging.RuleTagger


def draft(monkeypatch, model):
    monkeypatch.setattr(draftflow, "model_for", lambda agent, step=None: model)
    files = {"semantic.md": b"# Unit\n\n## fact\n\n| column | type | description | key |\n|---|---|---|---|\n"
                            b"| id | BIGINT | Identifier | primary |\n"}
    return draftflow.run(files, "unit", "semantic.md", [], checkpointer=draftflow.InMemorySaver())


def test_onboarding_drafting_stops_on_a_model_error_in_the_cloud(cloud):
    with pytest.raises(NoFallback, match="onboarding's read step: model error: RuntimeError"):
        draft(cloud, Failing())


def test_onboarding_drafting_stops_when_its_retries_are_exhausted_in_the_cloud(cloud):
    class Invalid(Failing):
        def json(self, prompt, max_tokens=None):
            return {"tables": "not a list"}
    with pytest.raises(NoFallback, match="retries exhausted"):
        draft(cloud, Invalid())


def test_onboarding_drafting_falls_back_on_the_local_stack(local):
    result = draft(local, Failing())
    read = next(o for o in result["outcomes"] if o["node"] == "read")
    assert read["made_by"] == "rules" and read["reason"] == "model error: RuntimeError"


def test_onboarding_drafting_with_no_model_configured_takes_the_rules_in_the_cloud(cloud):
    """No model is not a fallback: nothing was asked, and the history says so."""
    result = draft(cloud, None)
    assert all(o["made_by"] == "rules" for o in result["outcomes"])


class Engine:
    term_names = ["revenue", "deposits"]


class Config:
    def uses_model(self, stage):
        return True


def combined(model):
    c = Combined.__new__(Combined)
    c.llm, c.e = model, Engine()
    c.template = lambda r: "the template's answer"
    return c


ANSWER = {"unit_label": "Retail", "period": "June", "figures": [], "citations": [], "not_cited": [], "withheld": []}


def test_the_combined_engine_stops_on_a_failed_call_in_the_cloud(cloud):
    with pytest.raises(NoFallback, match="reading the question"):
        combined(Failing()).understand("revenue in June", [])
    with pytest.raises(NoFallback, match="phrasing the answer"):
        combined(Failing()).phrase(dict(ANSWER), Config())


def test_the_combined_engine_falls_back_on_the_local_stack(local):
    assert combined(Failing()).understand("revenue in June", []) == ([], None)
    assert combined(Failing()).phrase(dict(ANSWER), Config()) == ("the template's answer", "template (model call failed)")


def agent(model):
    a = agent_module.Agent.__new__(agent_module.Agent)
    a.llm = model
    return a


def test_the_agent_stops_on_a_failed_call_in_the_cloud(cloud):
    with pytest.raises(NoFallback, match="the agent's sql step: the model call failed \\(RuntimeError\\)"):
        agent(Failing()).call("sql", "prompt")


def test_the_agent_abstains_on_a_failed_call_on_the_local_stack(local):
    assert agent(Failing()).call("sql", "prompt") == (None, "the model call failed (RuntimeError)")


class Prose(Failing):
    """A model that answers in prose, as a Gemini 3 reply cut short by its thinking budget does: no JSON object."""

    def __init__(self):
        super().__init__(NotJSON("model reply held no JSON object"))


class Allow:
    def as_prompt(self):
        return "Allowed: retail.sales.amount"


class Warehouses:
    query_dialect = "DuckDB"


class GenerateEngine(Engine):
    UNIT_LABEL, UNIT_KEY, w = {"retail": "Retail"}, {"retail": "customer_id"}, Warehouses()


def generate(model):
    c = combined(model)
    c.e = GenerateEngine()
    return c.generate("revenue in June", "retail", None, [], Allow())


NOT_JSON_STOP = "the model's reply was not JSON, and the pilot on BigQuery does not fall back to rules"


def test_a_reply_that_is_not_json_stops_every_route_in_the_cloud(cloud):
    """Every model step on every route stops the same way on a reply that is not JSON (Grok on #97): the agent's sql and
    phrase steps, and the combined engine's understand, generate and phrase steps, each named."""
    for task in ("sql", "phrase"):
        with pytest.raises(NoFallback, match=f"the agent's {task} step: {NOT_JSON_STOP}"):
            agent(Prose()).call(task, "prompt")
    with pytest.raises(NoFallback, match=f"writing the query: {NOT_JSON_STOP}"):
        generate(Prose())
    with pytest.raises(NoFallback, match="reading the question: the model call failed \\(NotJSON\\)"):
        combined(Prose()).understand("revenue in June", [])
    with pytest.raises(NoFallback, match="phrasing the answer: the model call failed \\(NotJSON\\)"):
        combined(Prose()).phrase(dict(ANSWER), Config())


def test_a_reply_that_is_not_json_abstains_or_falls_back_on_the_local_stack(local):
    for task in ("sql", "phrase"):
        raw, why = agent(Prose()).call(task, "prompt")
        assert raw is None and why.startswith("the model's reply was not one JSON object")
    sql, guard, attempts = generate(Prose())
    assert sql is None and attempts[0]["problems"][0].startswith("model reply was not JSON")
    assert combined(Prose()).understand("revenue in June", []) == ([], None)
    assert combined(Prose()).phrase(dict(ANSWER), Config()) == ("the template's answer", "template (model call failed)")


def test_the_vertex_embedder_is_never_swapped_for_the_hashing_one_in_the_cloud(cloud):
    from kge.adapters.model import VertexEmbedder, make_embedder
    cloud.setenv("EMBEDDING_PROVIDER", "vertex")
    cloud.setenv("VERTEX_PROJECT", "your-project-id")
    cloud.delenv("EMBEDDING_DIM", raising=False)
    embedder = make_embedder()
    assert type(embedder) is VertexEmbedder and embedder.dim == 768
