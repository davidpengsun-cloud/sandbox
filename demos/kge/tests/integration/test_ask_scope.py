"""An unrelated question cannot turn nearby documents into a definition (#145)."""
import pytest

from kge.adapters.model import LLM, NotJSON
from kge.services.ask.engine.agent import Agent
from kge.services.ask.engine.combined import Combined
from kge.services.ask.engine.core import PRESETS
from kge.services.ask.engine.question import OUTSIDE_PACK, grounded, parse
from tests.integration.test_answer_text import client as client, mcp_wire
from tests.integration.test_combined import Stub


OFF_TOPIC = (
    "What is the weather in Paris today?",
    "What is the distance to the moon?",
    "Explain how to bake sourdough bread.",
    "What is the weather like for retail staff today?",
)
COVERED = "What is the definition of an active customer?"
PARAPHRASE = ("Of the engaged Retail shoppers in June 2025, how many lodged a grievance that month, "
              "and what were their mean balances and fees?")
COHORT_TERMS = ["Active Customer", "Complaint", "Average Balance", "Fee Income"]
POLICIES = {
    "What makes a column certified?": "POL-METRIC-CERT",
    "Where is client health information recorded?": "STD-SUITABILITY",
    "May restricted personal data be embedded for the assistant?": "POL-DATA-CLASS",
}


def abstained(answer, reason=OUTSIDE_PACK):
    assert answer["behaviour"] == "abstain", answer
    assert answer["reason"] == reason
    assert not answer.get("cite") and not answer.get("citations")
    assert not answer.get("sql") and not answer.get("sql_run")


def test_grounded_model_paraphrase_keeps_the_combined_figures(eng):
    """A question with no literal term match retains the model's cohort when its content belongs to the pack."""
    req = parse(PARAPHRASE, eng.term_names, eng)
    assert req.term is None and not req.terms_found
    assert grounded(PARAPHRASE, [{"excerpt": " ".join(eng.term_names)}], eng)
    model = Stub(terms=COHORT_TERMS)
    answer = Combined(eng, llm=model).ask(PARAPHRASE, "analyst_retail")
    assert answer["behaviour"] == "combined"
    assert {f["id"]: f["value"] for f in answer["figures"]} == {"c1": 176, "c2": 3, "m1": 6400.62, "m2": 42.64}
    assert answer["trace"][0]["resolved_by"] == "rules+model"
    assert answer["guard"]["passed"]


@pytest.mark.parametrize("question", OFF_TOPIC)
def test_ungrounded_model_cohort_is_ignored_before_any_warehouse_query(eng, monkeypatch, question):
    monkeypatch.setattr(eng.w, "row", lambda *a, **kw: pytest.fail("ungrounded cohort queried a warehouse"))
    answer = Combined(eng, llm=Stub(terms=COHORT_TERMS)).ask(question, "analyst_retail")
    abstained(answer)
    assert answer["trace"][0]["terms"] == []


def test_a_covered_definition_naming_a_unit_still_answers(eng):
    answer = Combined(eng, llm=None).ask("What is the definition of an active customer in Retail?", "analyst_retail")
    assert answer["behaviour"] == "cite_certified"
    assert "MD-ACTIVE-CUSTOMER" in answer["cite"]


@pytest.mark.parametrize("path", ["none", "mock", "configured", "configured_cloud", "misresolved_cloud"])
@pytest.mark.parametrize("transport", ["http", "mcp"])
@pytest.mark.parametrize("question", [*OFF_TOPIC, COVERED, *POLICIES])
def test_ask_scope_on_each_path_and_transport(client, monkeypatch, path, transport, question):
    import kge.app as appmod
    import kge.pipeline.settings as settings_module
    monkeypatch.setenv("MODEL_PROVIDER", "mock" if path == "mock" else "none")
    model = Stub(terms=["Active Customer"]) if path == "misresolved_cloud" else Stub() if path.startswith("configured") else "auto"
    combined = Combined(appmod.engine(), llm=model)
    monkeypatch.setattr(appmod, "_combined", combined)
    monkeypatch.setattr(settings_module, "gcp_native", lambda: path.endswith("_cloud"))
    if question in OFF_TOPIC or question in POLICIES:
        monkeypatch.setattr(combined.e, "answer", lambda *a, **kw: pytest.fail("unmatched question delegated to rules"))
        monkeypatch.setattr(combined.e.w, "row", lambda *a, **kw: pytest.fail("unmatched question queried a warehouse"))
        monkeypatch.setattr(combined.e.w, "run", lambda *a, **kw: pytest.fail("unmatched question queried a warehouse"))
    if transport == "http":
        response = client.post("/ask", json={"question": question}, headers=client.as_("analyst_retail"))
        assert response.status_code == 200, response.text
        answer = response.json()
    else:
        response = mcp_wire(client, "/mcp/answer", "analyst_retail", "ask", {"question": question})
        assert not response.get("isError"), response
        answer = response["structuredContent"]
    if question in OFF_TOPIC:
        abstained(answer)
        assert "outside what this pack covers" in answer["answer_text"]
    else:
        assert answer["behaviour"] == "cite_certified"
        assert POLICIES.get(question, "MD-ACTIVE-CUSTOMER") in answer["cite"]
    if isinstance(model, Stub):
        assert len(model.prompts) == 1
        assert model.prompts[0].startswith("Glossary terms")


GROUNDED_NOT_DEFINITIONAL = "Can we embed restricted personal data for the AI assistant?"


def test_no_model_agent_keeps_the_frozen_reason_and_combined_explains_scope(eng):
    """The frozen agent keeps its reason; combined distinguishes grounding from parsing."""
    for question in [OFF_TOPIC[2], GROUNDED_NOT_DEFINITIONAL]:
        abstained(Agent(eng, llm=None).ask(question, "analyst_retail"), "No glossary term recognised in the question.")
        reason = "No glossary term recognised in the question." if question == GROUNDED_NOT_DEFINITIONAL else OUTSIDE_PACK
        abstained(Combined(eng, llm=None).ask(question, "analyst_retail"), reason)


@pytest.mark.parametrize("path", ["none", "configured"])
@pytest.mark.parametrize("transport", ["http", "mcp"])
@pytest.mark.parametrize("question,in_scope", [(GROUNDED_NOT_DEFINITIONAL, True), (PARAPHRASE, True), (OFF_TOPIC[2], False)])
def test_unmatched_non_definitional_question_explains_its_scope(client, monkeypatch, path, transport, question, in_scope):
    """Grounding admits a definition from documents only: an unmatched question of another shape cites nothing."""
    import kge.app as appmod
    combined = Combined(appmod.engine(), llm=Stub() if path == "configured" else "auto")
    monkeypatch.setattr(appmod, "_combined", combined)
    req = parse(question, combined.e.term_names, combined.e)
    assert req.term is None and not req.terms_found and not req.definitional
    hits = combined.g.retrieve(question, "analyst_retail", "analysis", k=3)["exact"]
    vocabulary = [{"excerpt": " ".join([*combined.e.term_names, *combined.e.ALIASES])}]
    assert (grounded(question, vocabulary, combined.e) or grounded(question, hits, combined.e)) == in_scope
    monkeypatch.setattr(combined.e, "answer", lambda *a, **kw: pytest.fail("unmatched question delegated to rules"))
    monkeypatch.setattr(combined.e.w, "row", lambda *a, **kw: pytest.fail("unmatched question queried a warehouse"))
    if transport == "http":
        response = client.post("/ask", json={"question": question}, headers=client.as_("analyst_retail"))
        assert response.status_code == 200, response.text
        answer = response.json()
    else:
        response = mcp_wire(client, "/mcp/answer", "analyst_retail", "ask", {"question": question})
        assert not response.get("isError"), response
        answer = response["structuredContent"]
    abstained(answer, "No glossary term recognised in the question." if in_scope else OUTSIDE_PACK)
    assert ("outside what this pack covers" in answer["answer_text"]) is not in_scope


@pytest.mark.parametrize("arm", list(PRESETS))
@pytest.mark.parametrize("question", OFF_TOPIC)
def test_rule_questions_without_grounding_abstain(eng, monkeypatch, arm, question):
    monkeypatch.setattr(eng.w, "row", lambda *a, **kw: pytest.fail("unmatched question queried a warehouse"))
    answer = eng.answer(question, "analyst_retail", arm_name=arm)
    if question == OFF_TOPIC[2]:
        assert answer["behaviour"] == "abstain"
        assert not answer.get("cite") and not answer.get("sql")
    else:
        abstained(answer)


@pytest.mark.parametrize("arm", list(PRESETS))
def test_a_question_about_a_declared_join_fact_keeps_its_answer(eng, arm):
    question = "Can retail customers be joined to corporate contacts on customer_id?"
    answer = eng.answer(question, "analyst_enterprise", arm_name=arm)
    assert answer["behaviour"] == ("cite_certified" if arm == "graph" else "cite")


@pytest.mark.parametrize("path", ["none", "mock", "configured"])
@pytest.mark.parametrize("question", OFF_TOPIC)
def test_agent_abstains_before_a_model_can_invent_an_ungrounded_definition(eng, monkeypatch, path, question):
    agent = Agent(eng, llm=None if path == "none" else LLM("mock", "mock") if path == "mock" else Stub())
    monkeypatch.setattr(agent, "plan", lambda *a, **kw: pytest.fail("ungrounded question requested a model answer"))
    monkeypatch.setattr(eng.w, "row", lambda *a, **kw: pytest.fail("unmatched question queried a warehouse"))
    answer = agent.ask(question, "analyst_retail")
    abstained(answer, "No glossary term recognised in the question." if path == "none" and question == OFF_TOPIC[2] else OUTSIDE_PACK)


@pytest.mark.parametrize("transport", ["http", "mcp"])
def test_failed_model_resolution_still_obeys_no_fallback(client, monkeypatch, transport):
    import kge.app as appmod
    import kge.pipeline.settings as settings_module
    model = Stub()
    combined = Combined(appmod.engine(), llm=model)
    monkeypatch.setattr(appmod, "_combined", combined)
    monkeypatch.setattr(settings_module, "gcp_native", lambda: True)

    def failed(*args, **kwargs):
        raise NotJSON("model reply held no JSON object")

    monkeypatch.setattr(model, "json", failed)
    monkeypatch.setattr(combined.e, "answer", lambda *a, **kw: pytest.fail("failed resolution delegated to rules"))
    if transport == "http":
        response = client.post("/ask", json={"question": OFF_TOPIC[0]}, headers=client.as_("analyst_retail"))
        assert response.status_code == 503, response.text
        assert "reading the question" in response.json()["detail"]
    else:
        response = mcp_wire(client, "/mcp/answer", "analyst_retail", "ask", {"question": OFF_TOPIC[0]})
        assert response["isError"]
        assert "503:" in response["content"][0]["text"]
        assert "reading the question" in response["content"][0]["text"]


@pytest.mark.parametrize("rule", ["shelves", "tiers"])
def test_grounding_in_a_passage_not_selected_for_citation_cannot_admit_an_answer(eng, monkeypatch, rule):
    from kge.services.ask.engine.question import parse

    question = "What is quill registration?"
    req = parse(question, eng.term_names, eng)
    cite = eng.cite if rule == "shelves" else eng.cite_tiers
    baseline = cite(req, "analyst_retail", "analysis")
    selected = set(baseline["cite"])
    original = eng.g.retrieve
    seen_unselected = []

    def retrieve(*args, **kwargs):
        result = original(*args, **kwargs)
        hits = []
        for hit in result["exact"]:
            chosen = hit["doc"] in selected
            if not chosen:
                seen_unselected.append(hit["doc"])
            hits.append({**hit, "excerpt": "The handbook contains procedures." if chosen else "Quill registration is recorded here."})
        return {**result, "exact": hits}

    monkeypatch.setattr(eng.g, "retrieve", retrieve)
    abstained(eng.definition_answer(req, "analyst_retail", "analysis", PRESETS["graph"].with_stages(citation_rule=rule)))
    assert seen_unselected, "the probe must contain a grounded retrieval hit outside the citations"
