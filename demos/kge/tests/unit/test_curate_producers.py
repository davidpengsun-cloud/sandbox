"""The producers of the Curate queue families (cerebro.md §6.2, §6.3), each a pure step: the tagger's document tier, an
onboarding draft's business terms and filter values, similar-name inference's column names, and who proposed an item."""
import pytest
import yaml

from kge.graph.load import DRAFT_GENERATOR, draft_records, drafted
from kge.pipeline.suggestions import NAMED, named
from kge.pipeline.tagger import tier_proposal
from kge.services.ask.engine.graphview import OFF_PASSAGE, PIPELINE_ROLE, proposed_by


@pytest.mark.parametrize("shelf,ai_ready,tier,expected", [
    ("certified", True, None, "silver"),
    ("uncertified", True, "", "bronze"),
    ("", True, "platinum", "bronze"),
    ("certified", True, "gold", None),
    ("certified", False, None, None),
])
def test_the_tagger_proposes_a_tier_only_for_an_ai_ready_document_without_one(shelf, ai_ready, tier, expected):
    proposal = tier_proposal(shelf, ai_ready, tier)
    if expected is None:
        assert proposal is None
    else:
        assert (proposal.family, proposal.value) == ("document_tier", expected)
        assert proposal.value != "gold"


class Store:
    """A pack store's listing and reads, from a dict of names to yaml."""

    def __init__(self, files):
        self.files = files

    def list(self, prefix):
        return [n for n in self.files if n.startswith(prefix + "/")]

    def read(self, name):
        return yaml.safe_dump(self.files[name])


class Writer:
    def __init__(self):
        self.nodes = []

    def create_node(self, label, props):
        self.nodes.append((label, props))


def draft(by, items):
    return {"drafted": {"by": by, "on": "2026-11-15"}, "items": items}


TERM = {"id": "basket_size", "kind": "term", "title": "Basket Size",
        "pack": {"glossary": [{"name": "Basket Size"}], "filters": [{"id": "f1", "predicate": "t.channel = 'online'"}, {"label": "no id"}]}}


def test_each_unit_s_draft_is_read_and_other_files_are_ignored():
    store = Store({"units/retail/draft.yaml": draft("a", []), "units/retail/update/draft.yaml": draft("b", []),
                   "units/retail/pack.yaml": {"items": []}, "units/corporate/draft.yaml": {"no": "items"}})
    records = draft_records(store)
    assert list(records) == ["retail"] and records["retail"]["drafted"]["by"] == "a"


def test_a_draft_s_uncertified_term_is_a_business_term_and_its_filters_filter_values_on_no_passage():
    w = Writer()
    items = [TERM, {**TERM, "id": "done", "certified_as": "done"}, {"id": "x", "kind": "table"}, "not an item",
             {"kind": "term", "title": "no id"}, {"id": "odd", "kind": "term", "pack": {"glossary": ["not a dict"]}}]
    assert drafted(w, {"retail": draft("engineer", items)}) == 3
    tags = {p["id"]: p for label, p in w.nodes if label == "Tag"}
    assert set(tags) == {"retail/draft/basket_size/business_term", "retail/draft/basket_size/filter/f1", "retail/draft/odd/business_term"}
    assert tags.pop("retail/draft/odd/business_term")["value"] == "odd"
    term, flt = tags["retail/draft/basket_size/business_term"], tags["retail/draft/basket_size/filter/f1"]
    assert (term["family"], term["value"], flt["family"], flt["value"]) == ("business_term", "Basket Size", "filter_value", "t.channel = 'online'")
    assert {t["generator"] for t in tags.values()} == {DRAFT_GENERATOR} and {t["model"] for t in tags.values()} == {"engineer"}
    assert all(label == "Tag" for label, _ in w.nodes)


@pytest.mark.parametrize("column,term", [("merchant_category", "merchant category"), ("Merchant__Category ", "merchant category"),
                                         ("amount", "amount")])
def test_a_column_name_is_spelled_as_a_term(column, term):
    assert named(column) == term


@pytest.mark.parametrize("tag,who", [
    ({"generator": "draft", "model": "engineer"}, "engineer"),
    ({"generator": "certification", "model": "fuzzy"}, "certification"),
    ({"generator": NAMED, "model": "similar-name-v1"}, PIPELINE_ROLE),
    ({"generator": "rule"}, PIPELINE_ROLE),
    ({}, PIPELINE_ROLE),
])
def test_each_item_says_who_proposed_it(tag, who):
    assert proposed_by(tag) == who


def test_the_off_passage_generators_are_the_draft_s_and_similar_name_inference_s():
    assert set(OFF_PASSAGE) == {DRAFT_GENERATOR, NAMED}
