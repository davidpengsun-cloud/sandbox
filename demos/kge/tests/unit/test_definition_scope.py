"""Definition scope uses readable pack terms or content words shared with citable passages."""
from types import SimpleNamespace

import pytest

from kge.services.ask.engine.core import Engine
from kge.services.ask.engine.question import content_words, grounded, OUTSIDE_PACK, Request


def domain(terms, aliases=None, documents=(), units=("workshop",)):
    graph = SimpleNamespace(
        readable_units=lambda principal: set(units),
        defining_docs=lambda term: [{"id": doc} for doc, _ in documents],
        certification=lambda docs: {doc: {"unit": unit} for doc, unit in documents},
    )
    return SimpleNamespace(term_names=terms, ALIASES=aliases or {}, UNIT_WORDS={}, UNIT_LABEL={u: u for u in units}, g=graph)


@pytest.mark.parametrize("question", ["What is Lumen Mass?", "Define glow weight", "What is glow weight?"])
def test_a_loaded_term_and_its_pack_alias_resolve_without_a_builtin_vocabulary(question):
    pack = domain(["Lumen Mass"], {"glow weight": "Lumen Mass"})
    assert Engine.definition_in_scope(pack, question, "reader")


@pytest.mark.parametrize("question", ["What is the weather in Paris today?", "What is the distance to the moon?",
                                      "Explain how to bake sourdough bread."])
def test_an_unmatched_question_needs_passage_grounding(question):
    pack = domain(["Lumen Mass"], {"glow weight": "Lumen Mass"})
    assert not Engine.definition_in_scope(pack, question, "reader")


def test_the_same_words_depend_on_the_loaded_pack():
    question = "What is Lumen Mass?"
    assert Engine.definition_in_scope(domain(["Lumen Mass"]), question, "reader")
    assert not Engine.definition_in_scope(domain(["Quill Yield"]), question, "reader")


@pytest.mark.parametrize("unit,allowed", [("workshop", True), ("", True), ("vault", False)])
def test_a_document_defined_term_needs_a_readable_or_shared_definition(unit, allowed):
    pack = domain(["Lumen Mass"], documents=[("D-LUMEN", unit)])
    assert Engine.definition_in_scope(pack, "What is Lumen Mass?", "reader") is allowed


def test_a_substring_of_a_word_is_not_a_term():
    assert not Engine.definition_in_scope(domain(["Mint"]), "What is mintage?", "reader")


def test_an_alias_to_a_term_absent_from_the_loaded_glossary_is_not_a_definition():
    pack = domain(["Quill Yield"], {"glow weight": "Lumen Mass"})
    assert not Engine.definition_in_scope(pack, "What is glow weight?", "reader")


@pytest.mark.parametrize("question", [
    "What is it today?", "Where was it in Q2 2025?", "May it be in Mar 2025?",
    "Explain the latest month with rows.", "What was it during FY26-Q1?", "Where was it on Monday?",
])
def test_question_and_period_words_cannot_ground_a_passage(question):
    assert not grounded(question, [{"excerpt": "It was today in March 2025, during the latest month."}])


@pytest.mark.parametrize("period", ["weeks", "months", "years"])
def test_plural_period_words_cannot_ground_a_passage(period):
    assert not grounded(f"What were the last {period}?", [{"excerpt": f"The latest {period}."}])


def test_grounding_reuses_singular_tokens_without_matching_substrings_or_titles():
    assert grounded("Where are quills recorded?", [{"excerpt": "A quill belongs in the register."}])
    assert not grounded("What is mint?", [{"excerpt": "Mintage is recorded."}])
    assert not grounded("Where are quills?", [{"title": "Quills", "excerpt": "An unrelated passage."}])
    assert not grounded("What is it?", [{"excerpt": "What is it?"}])
    assert content_words("What were yields in Sept 2025?") == {"yield"}


@pytest.mark.parametrize("name", ["atelier", "Quill Workshops", "workshop"])
def test_loaded_unit_ids_and_label_words_cannot_ground_a_question(name):
    pack = domain(["Lumen Mass"])
    pack.UNIT_LABEL = {"atelier": "Quill Workshops"}
    question = f"What is the weather for {name} today?"
    passage = [{"excerpt": f"The {name} records lumen mass."}]
    assert grounded(question, passage)
    assert not grounded(question, passage, pack)
    assert grounded(f"What is lumen mass for {name}?", passage, pack)


def test_calendar_content_still_grounds_a_question_after_unit_labels_are_removed():
    pack = domain(["Lumen Mass"])
    pack.UNIT_LABEL = {"atelier": "Quill Workshops"}
    assert grounded("Explain which fiscal calendar Quill Workshops uses",
                    [{"excerpt": "The fiscal calendar begins in April."}], pack)


@pytest.mark.parametrize("excerpt,behaviour", [
    ("The quill register records all permitted transfers.", "cite"),
    ("The ledger records the current month.", "abstain"),
    ("", "abstain"),
])
def test_an_unmatched_question_can_answer_only_from_a_grounded_retrieved_passage(excerpt, behaviour):
    pack = domain(["Lumen Mass"])
    pack.definition_in_scope = lambda question, principal: Engine.definition_in_scope(pack, question, principal)
    pack.g.retrieve = lambda *args, **kwargs: {"exact": [{"doc": "D-QUILL", "title": "Register", "excerpt": excerpt, "shelf": "certified"}]}
    answer = Engine.definition_answer(pack, Request(question="Where are quills registered?"), "reader", "analysis",
                                      SimpleNamespace(citation_rule="none"))
    assert answer["behaviour"] == behaviour
    if behaviour == "abstain":
        assert answer["reason"] == OUTSIDE_PACK
        assert not answer.get("cite")


@pytest.mark.parametrize("rule", ["shelves", "tiers"])
def test_unreadable_term_definers_are_removed_before_document_only_answering(rule):
    pack = domain(["Lumen Mass"], documents=[("D-PRIVATE", "vault")])
    pack.definition_in_scope = lambda question, principal: Engine.definition_in_scope(pack, question, principal)
    calls = []

    def cite(req, principal, purpose, ground):
        calls.append((req, ground))
        return {"behaviour": "abstain", "reason": OUTSIDE_PACK}

    pack.cite = pack.cite_tiers = cite
    original = Request(question="What is Lumen Mass?", term="Lumen Mass", terms_found=["Lumen Mass"])
    Engine.definition_answer(pack, original, "reader", "analysis", SimpleNamespace(citation_rule=rule))
    [(req, needs_grounding)] = calls
    assert needs_grounding and req.term is None and req.terms_found == []
    assert original.term == "Lumen Mass"
