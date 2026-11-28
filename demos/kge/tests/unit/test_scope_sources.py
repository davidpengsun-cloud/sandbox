"""A product view prefers local citations and falls back to certified sources elsewhere."""
from types import SimpleNamespace

from kge.services.ask.engine.scope import ProductScope


def engine():
    return SimpleNamespace(g=SimpleNamespace(document_metadata=lambda: [
        {"id": "SHARED", "unit": ""},
        {"id": "RETAIL", "unit": "retail"},
        {"id": "CORPORATE", "unit": "corporate"},
    ]))


def test_product_sources_are_narrowed_and_renumbered():
    source = {"cite": ["CORPORATE", "SHARED", "RETAIL"],
              "citations": [{"doc": "CORPORATE", "n": 1}, {"doc": "SHARED", "n": 2}, {"doc": "RETAIL", "n": 3}],
              "references": [{"doc": "CORPORATE"}, {"doc": "SHARED"}],
              "uncertified_context_not_cited": [{"id": "CORPORATE"}, {"id": "RETAIL"}],
              "vector_hits": [{"doc": "CORPORATE"}, {"doc": "RETAIL"}]}
    answer = ProductScope(engine(), ["retail.monthly_revenue"]).documents(source)
    assert answer["cite"] == ["SHARED", "RETAIL"]
    assert answer["citations"] == [{"doc": "SHARED", "n": 1}, {"doc": "RETAIL", "n": 2}]
    assert answer["references"] == [{"doc": "SHARED"}]
    assert answer["uncertified_context_not_cited"] == [{"id": "RETAIL"}]
    assert answer["vector_hits"] == [{"doc": "RETAIL"}]
    assert source["cite"] == ["CORPORATE", "SHARED", "RETAIL"]


def test_unscoped_sources_are_unchanged():
    source = {"cite": ["CORPORATE", "SHARED", "RETAIL"]}
    assert ProductScope(engine(), None).documents(source) is source


def test_definition_without_a_source_in_scope_keeps_its_certified_source():
    answer = ProductScope(engine(), ["retail.monthly_revenue"]).documents(
        {"behaviour": "cite_certified", "cite": ["CORPORATE"]})
    assert answer["behaviour"] == "cite_certified"
    assert answer["cite"] == ["CORPORATE"]
    assert "reason" not in answer


def test_unrelated_shared_citation_cannot_hide_the_requested_terms_outside_definer():
    pack = engine()
    pack.term_names = ["Lumen Mass"]
    pack.ALIASES = {}
    pack.UNIT_WORDS = {}
    pack.g.defining_docs = lambda term: [{"id": "CORPORATE"}] if term == "Lumen Mass" else []
    source = {"question": "What is Lumen Mass?", "behaviour": "cite_certified",
              "cite": ["SHARED", "CORPORATE"],
              "citations": [{"doc": "SHARED", "n": 1}, {"doc": "CORPORATE", "n": 2}],
              "references": [{"doc": "CORPORATE"}],
              "vector_hits": [{"doc": "CORPORATE"}]}

    answer = ProductScope(pack, ["retail.monthly_revenue"]).documents(source)

    assert answer["behaviour"] == "cite_certified"
    assert answer["cite"] == ["SHARED", "CORPORATE"]
    assert answer["citations"] == [{"doc": "SHARED", "n": 1}, {"doc": "CORPORATE", "n": 2}]
    assert answer["references"] == [{"doc": "CORPORATE"}]
    assert answer["vector_hits"] == [{"doc": "CORPORATE"}]
    assert source["cite"] == ["SHARED", "CORPORATE"]
