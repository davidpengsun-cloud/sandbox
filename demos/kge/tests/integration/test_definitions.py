"""A definition asked of a term no document defines (#39, round 2).

    .venv/bin/python -m pytest tests/integration/test_definitions.py -q

The Wealth terms the pack added (net new assets and the rest) are certified in
the pack's glossary, and net new assets and its client-directed variant through
their contexts too; no document defines any of them. A definitional question on
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

import pytest
import yaml

from kge.services.ask.engine import runtrace
from kge.services.ask.engine.agent import Agent
from kge.services.ask.engine.combined import Combined
from kge.services.ask.engine.core import PRESETS
from kge.eval.metrics import CLASSES
from kge.adapters.model import LLM
from tests.scratch import DEMO

WHO = "analyst_enterprise"
NNA = ["What is NNA?", "What is net new assets?", "What is the definition of NNA?"]
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
def test_nna_is_its_pack_definition_on_the_graph_arm_on_ask_and_on_the_treatment(eng, q):
    want = pack_definition("net-new-assets")
    for a in (eng.answer(q, WHO), Combined(eng, llm=None).ask(q, WHO), treatment(eng, q)):
        from_the_glossary(a, eng, "Net New Assets")
        assert a["definition"] == want and a["contexts"] == ["nna"] and "its context nna" in a["reason"]
        assert a["references"], "what the question retrieved is still listed, as a reference"


@pytest.mark.parametrize("q", ["What is AUM?", "What is assets under management?"])
def test_aum_added_by_a_pack_edit_alone_is_its_pack_definition_with_its_context(eng, q):
    """Issue #40's pack-only onboarding test: pack 1.3.0 adds assets under management, which no document defines, so it
    is answered as net new assets is, from the pack's glossary, labelled so, with the context it is certified through."""
    want = pack_definition("assets-under-management")
    for a in (eng.answer(q, WHO), Combined(eng, llm=None).ask(q, WHO), treatment(eng, q)):
        from_the_glossary(a, eng, "Assets Under Management")
        assert a["definition"] == want and a["contexts"] == ["aum"] and "its context aum" in a["reason"]


@pytest.mark.parametrize("q", NNA + ["What is client-directed NNA?", "What is a test account?"])
def test_on_the_mock_the_code_holds_the_glossary_s_definition_whatever_the_reply(eng, q):
    a, r = treatment(eng, q, LLM("mock", "mock")), eng.answer(q, WHO, config=PRESETS["graph"])
    assert a["agent"]["mode"] == "mock" and a["attempts"] == []
    from_the_glossary(a, eng, r["term"])
    assert a["definition"] == r["definition"] and a["answer_text"] == a["reason"]


def test_the_package_says_no_document_defines_the_term_and_lists_no_source_to_cite(eng):
    ctx = Agent(eng, llm=None).context("What is NNA?", WHO, "analysis", "treatment")
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
