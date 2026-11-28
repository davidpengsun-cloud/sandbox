"""Definitions from certified documents and from the pack's glossary (#39, round 2).

    .venv/bin/python -m pytest tests/integration/test_definitions.py -q

The Wealth terms the pack added are certified in the pack's glossary and through
their contexts. Net new assets also has a certified definition document. A definitional question on
a term no document defines is answered from the pack's glossary, and says so
(glossary_definition): the term's definition and its steward, never the
documents the question happened to retrieve, which are listed as references
and never cited as its definition or as the authority. This holds on every arm
that tells a certified definition from a retrieved document: the graph preset
(the shelf rule), /ask, and the reference agent's treatment (the tier rule), on
rules and on the mock, where the code holds the answer whatever the reply. A
term a document defines is cited by that document, as before, and a question
that resolves no term is answered from what it retrieves, as before (G037 to
G039). The control's arms tell no shelf or tier apart (citation_rule
retrieval): they cite what search returned and say so, as before, since they
are the A/B's graph off and are handed nothing of the graph.

Opens the demo's graph. Stop the API first.
"""
from __future__ import annotations

import json

import pytest
import yaml

from kge.services.ask.engine import runtrace
from kge.services.ask.engine.agent import Agent
from kge.services.ask.engine.combined import Combined
from kge.services.ask.engine.core import PRESETS
from kge.eval.metrics import CLASSES
from kge.adapters.model import LLM
from tests.integration.test_combined import Stub
from tests.scratch import DEMO

WHO = "analyst_enterprise"
NNA = ["What is NNA?", "What is net new assets?", "What is the definition of NNA?"]
AUM = ["What is AUM?", "What is assets under management?", "What is the definition of AUM?"]
PACK = DEMO / "clients" / "meridian" / "pack.yaml"
GLOSSARY = "the pack's glossary"


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def no_network(*a, **k):
        raise AssertionError("the mock made a network call")
    monkeypatch.setattr(LLM, "_post", staticmethod(no_network))


def pack_definition(term_id: str) -> str:
    return next(t["definition"] for t in yaml.safe_load(PACK.read_text())["glossary"] if t["id"] == term_id)


def graph_term(eng, name: str) -> dict:
    return eng.g.q("MATCH (t:Term) WHERE t.name = $t RETURN t.definition AS definition, t.steward AS steward", {"t": name})[0]


def treatment(eng, q: str, llm=None) -> dict:
    agent = Agent(eng, llm=llm)
    return runtrace.run("agent", lambda: agent.ask(q, WHO, arm="treatment"), graph=eng.g)


def asked(eng) -> list[tuple[str, str]]:
    """(term, question) for every glossary term a plain definitional question resolves to that term itself."""
    out = []
    for t in sorted(eng.term_names):
        q = f"What is {t}?"
        if eng.answer(q, WHO)["trace"][0]["term"] == t:
            out.append((t, q))
    return out


def from_the_glossary(a: dict, eng, term: str) -> None:
    """a is term's definition from the pack's glossary, labelled so, citing nothing it retrieved."""
    assert a["behaviour"] == "glossary_definition", (a["question"], a["behaviour"], a.get("cite"))
    held = graph_term(eng, term)
    assert (a["term"], a["definition"], a["steward"]) == (term, held["definition"], held["steward"])
    assert a["definition_from"] == GLOSSARY and f"No document defines {term}" in a["reason"]
    assert a["cite"] == [] and not a.get("citations"), "a retrieved document is cited as the term's definition"
    assert all(r["doc"] not in a["cite"] and "never cited" in r["why"] for r in a["references"])


def test_every_term_no_document_defines_is_answered_from_the_pack_s_glossary(eng):
    undefined = 0
    for t, q in asked(eng):
        a = eng.answer(q, WHO)
        if eng.g.defining_docs(t):
            assert a["behaviour"] != "glossary_definition", q
            continue
        undefined += 1
        from_the_glossary(a, eng, t)
    assert undefined >= 8, "the Wealth terms the pack added are defined by no document"


def test_a_definition_cites_as_the_term_s_definition_only_documents_that_define_it(eng):
    """The tier rule says why it cites each document: a term's certified definition only where a document defines it;
    the treatment cites nothing as the definition of a term no document defines, and nothing as the authority for it."""
    for t, q in asked(eng):
        a = treatment(eng, q)
        defines = {d["id"] for d in eng.g.defining_docs(t)}
        for c in a.get("citations") or []:
            if c["why"].startswith("certified definition of "):
                term = c["why"].removeprefix("certified definition of ")
                assert c["doc"] in {d["id"] for d in eng.g.defining_docs(term)}, (q, c)
        if not defines:
            from_the_glossary(a, eng, t)
            assert a["agent"]["mode"] == "rules"


@pytest.mark.parametrize("q", NNA)
@pytest.mark.parametrize("modeled", [False, True])
def test_nna_cites_its_certified_definition_on_the_graph_arm_on_ask_and_on_the_treatment(eng, q, modeled, monkeypatch):
    model = LLM("mock", "mock") if modeled else None
    if model is not None:
        monkeypatch.setattr(model, "json", lambda *a, **kw: pytest.fail("a definition called numeric interpretation"))
    answers = (eng.answer(q, WHO), Combined(eng, llm=Stub() if modeled else None).ask(q, WHO), treatment(eng, q, model))
    for a in answers:
        assert a["behaviour"] == "cite_certified" and "MD-NET-NEW-ASSETS" in a["cite"]
        assert set(a["cite"]) == set(answers[0]["cite"])
        assert not a.get("definition_citation") and not a.get("sql_run")
    citation = next(c for c in answers[-1]["citations"] if c["doc"] == "MD-NET-NEW-ASSETS")
    assert citation["why"] == "certified definition of Net New Assets"


@pytest.mark.parametrize("q", ["What is AUM?", "What is assets under management?"])
def test_aum_is_its_pack_definition_on_the_graph_arm_on_ask_and_on_the_treatment(eng, q):
    want = pack_definition("assets-under-management")
    for a in (eng.answer(q, WHO), Combined(eng, llm=None).ask(q, WHO), treatment(eng, q)):
        from_the_glossary(a, eng, "Assets Under Management")
        assert a["definition"] == want and a["contexts"] == ["aum"] and "its context aum" in a["reason"]
        assert a["references"], "what the question retrieved is still listed, as a reference"


@pytest.mark.parametrize("combined", [False, True])
def test_glossary_authority_has_the_same_shape_with_or_without_page_context(eng, combined):
    answer = Combined(eng, llm=None).ask if combined else eng.answer
    unscoped = answer("What is AUM?", WHO)
    scoped = answer("What is AUM?", WHO, products=["wealth.F_NNA_FIN"])
    expected = {"kind": "pack_glossary", "source": GLOSSARY, "term": "Assets Under Management", "contexts": ["aum"],
                "client": eng.profile.client, "model_hash": eng.identity["hash"]}
    assert unscoped["definition_citation"] == scoped["definition_citation"] == expected
    assert unscoped["cite"] == scoped["cite"] == []


@pytest.mark.parametrize("q", ["What is AUM?", "What is assets under management?"])
def test_aum_added_by_a_pack_edit_alone_is_its_pack_definition_with_its_context(eng, q):
    """Issue #40's pack-only onboarding test: pack 1.3.0 adds assets under management, which no document defines, so it
    is answered from the pack's glossary, labelled so, with the context it is certified through."""
    want = pack_definition("assets-under-management")
    for a in (eng.answer(q, WHO), Combined(eng, llm=None).ask(q, WHO), treatment(eng, q)):
        from_the_glossary(a, eng, "Assets Under Management")
        assert a["definition"] == want and a["contexts"] == ["aum"] and "its context aum" in a["reason"]


@pytest.mark.parametrize("q", AUM + ["What is client-directed NNA?", "What is a test account?"])
def test_on_the_mock_the_code_holds_the_glossary_s_definition_whatever_the_reply(eng, q, monkeypatch):
    model = LLM("mock", "mock")
    monkeypatch.setattr(model, "json", lambda *a, **kw: pytest.fail("a glossary definition called numeric interpretation"))
    a, r = treatment(eng, q, model), eng.answer(q, WHO, config=PRESETS["graph"])
    assert a["agent"]["mode"] == "mock" and not a.get("attempts") and "history" in a
    from_the_glossary(a, eng, r["term"])
    assert a["definition"] == r["definition"] and a["reason"] == r["reason"]


def test_the_package_says_no_document_defines_the_term_and_lists_no_source_to_cite(eng):
    ctx = Agent(eng, llm=None).context("What is AUM?", WHO, "analysis", "treatment")
    assert not [s for s in ctx["sources"] if s["citable"]], "a retrieved document is offered as a source to cite"
    assert "defined by no document: the definition above is the pack's glossary's" in ctx["block"]
    assert ctx["decided"]["behaviour"] == "glossary_definition"
    figure = Agent(eng, llm=None).context("NNA for June 2025", WHO, "analysis", "treatment")
    assert "defined by no document" not in figure["block"], "only a definitional question is answered from the glossary"


@pytest.mark.parametrize("q", ["What is the month-end freeze?", "What is the month-end release?", "What is a month end freeze?"])
def test_the_month_end_release_and_freeze_are_read_as_their_term(eng, q):
    """Each question uses one of the pack's phrases for the term, whose name no question says whole."""
    from_the_glossary(eng.answer(q, WHO), eng, "Month-End Release / Freeze")


def test_a_term_a_document_defines_is_cited_by_it_as_before(eng):
    q = "What is the definition of an active customer?"
    a = eng.answer(q, WHO)
    assert a["behaviour"] == "cite_certified" and "MD-ACTIVE-CUSTOMER" in a["cite"]
    t = treatment(eng, q)
    assert t["behaviour"] == "cite_certified"
    assert {"doc": "MD-ACTIVE-CUSTOMER", "why": "certified definition of Active Customer"}.items() <= \
        next(c for c in t["citations"] if c["doc"] == "MD-ACTIVE-CUSTOMER").items()


def test_a_question_that_resolves_no_term_is_answered_from_what_it_retrieves_as_before(eng):
    a = eng.answer("What makes a column certified?", WHO)
    assert a["behaviour"] == "cite_certified" and "POL-METRIC-CERT" in a["cite"]


@pytest.mark.parametrize("arm", ["schema", "catalog"])
def test_the_control_s_arms_cite_what_search_returned_and_say_so(eng, arm):
    a = eng.answer("What is NNA?", WHO, arm_name=arm)
    assert a["behaviour"] == "cite" and a["reason"] == "no shelf distinction in this arm" and a["cite"]
    c = Agent(eng, llm=None).ask("What is NNA?", WHO, arm="control")
    assert c["behaviour"] == "cite" and "definition" not in c


def test_a_definition_from_the_glossary_is_an_answer():
    assert CLASSES["glossary_definition"] == "answer"


@pytest.mark.parametrize("question", [
    "Explain which fiscal calendar Wealth Management uses",
    "Is it acceptable to embed restricted personal data for the assistant?",
])
@pytest.mark.parametrize("modeled", [False, True])
def test_heldout_definition_paraphrases_cite_on_agent_and_public_ask(eng, monkeypatch, question, modeled):
    monkeypatch.setattr(eng.w, "cursor", lambda *a, **kw: pytest.fail("a definition queried the warehouse"))
    agent_model = LLM("mock", "mock") if modeled else None
    if agent_model is not None:
        monkeypatch.setattr(agent_model, "json", lambda *a, **kw: pytest.fail("a definition called numeric interpretation"))
    answers = [eng.answer(question, WHO), Combined(eng, llm=Stub() if modeled else None).ask(question, WHO),
               treatment(eng, question, agent_model)]
    for answer in answers:
        assert answer["behaviour"] == "cite_certified" and answer["cite"]
        assert set(answer["cite"]) == set(answers[0]["cite"])
        assert all(c["shelf"] == "certified" for c in eng.g.certification(answer["cite"]).values())
        assert answer["history"] == [] and not answer.get("figure") and not answer.get("sql_run")


def test_manifest_distinguishes_approved_control_guards_from_definition_moves():
    rows = [json.loads(line) for line in (DEMO / "tests/fixtures/agent_query_plan_v1_manifest.jsonl").read_text().splitlines()]
    approved = [r for r in rows if r.get("approval") == "owner_step7b_control_guard_bugfixes"]
    assert len(approved) == 76
    assert all(":control:" in r["key"] and not r["additional_outcome_move"] and '"This fixes bugs, so I am good"' in r["decision"]
               for r in approved)
    exceptions = {r["key"] for r in rows if r["additional_outcome_move"]}
    assert exceptions == {"golden:G034:control:malformed_json", "heldout:H011:control:malformed_json",
                          "heldout:H012:control:careful", "heldout:H013:control:careful"}
    fixed = [r for r in rows if r.get("resolution") == "step7b_definition_path_restored"]
    assert len(fixed) == 4
    assert all(":treatment:" in r["key"] and r["after_step7"]["behaviour"] == "cite_certified" for r in fixed)


@pytest.mark.parametrize("question,principal,products,behaviour", [
    ("Explain which fiscal calendar Wealth Management uses", "analyst_retail", ["wealth.TRAN_FACT_1"], "refuse"),
    ("Is it acceptable to embed restricted personal data for the assistant?", "pipeline_tagger", None, "refuse"),
    ("Explain which lunar orbit a spacecraft uses", WHO, None, "abstain"),
    ("Is it acceptable to bake sourdough bread?", WHO, None, "abstain"),
])
def test_definition_paraphrases_keep_access_and_grounding_guards(eng, monkeypatch, question, principal, products, behaviour):
    monkeypatch.setattr(eng.w, "cursor", lambda *a, **kw: pytest.fail("a definition queried the warehouse"))
    for caller in (Combined(eng, llm=None), Agent(eng, llm=None)):
        answer = caller.ask(question, principal, products=products)
        assert answer["behaviour"] == behaviour and not answer.get("cite") and not answer.get("citations")
