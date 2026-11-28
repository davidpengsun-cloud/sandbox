"""The generic reading rules of #707 (kge/semantics/readingrules.py) on two packs: a synthetic one with invented names
(tests/fixtures/reading_rules/harbour.json) and Meridian's own declarations, read from its pack. A rule that names a
client, a term, a dimension or a value works on one of them only, and fails here.

    .venv/bin/python -m pytest tests/unit/test_reading_rules.py -q
"""
from __future__ import annotations

import datetime
import json
from pathlib import Path

import pytest
import yaml

from kge.compiler.vocabulary import alias_entries
from kge.pipeline.settings import ROOT
from kge.semantics.readingrules import column_label, filter_phrases, labelled_year_window, grouping_options, joined, meanings, spaced, word_in

HARBOUR = json.loads((ROOT / "tests" / "fixtures" / "reading_rules" / "harbour.json").read_text())
MERIDIAN = ROOT / "clients" / "meridian" / "pack.yaml"


@pytest.fixture(scope="module")
def meridian() -> dict:
    return yaml.safe_load(MERIDIAN.read_text())


def meridian_names(pack: dict) -> dict[str, list[str]]:
    """Each glossary term's name with its aliases, as the compiler expands them (concept wordings included)."""
    names = {term["id"]: term["name"] for term in pack["glossary"]}
    aliases: dict[str, list[str]] = {name: [] for name in names.values()}
    for alias in alias_entries(pack["terminology"]):
        if "term" in alias and alias["term"] in names:
            aliases[names[alias["term"]]].append(alias["phrase"])
    return aliases


def meridian_groupings(pack: dict) -> dict[str, str]:
    return {alias["phrase"]: alias["group_by"] for alias in alias_entries(pack["terminology"]) if "group_by" in alias}


@pytest.mark.parametrize("case", HARBOUR["meanings"], ids=lambda case: case["phrase"])
def test_a_name_resolves_to_the_term_its_alias_names_and_every_term_ending_in_it(case):
    found = meanings(case["phrase"], HARBOUR["terms"])
    assert [[m["term"], m["default_alias"]] for m in found] == case["expected"]


def test_meridians_nna_resolves_to_its_four_terms_with_the_alias_default_first(meridian):
    found = meanings("NNA", meridian_names(meridian))
    assert [m["term"] for m in found] == ["Net New Assets", "Branch Sales NNA", "Certified NNA", "Client-Directed NNA"]
    assert [m["default_alias"] for m in found] == [True, False, False, False]
    assert [m["term"] for m in meanings("retail NNA", meridian_names(meridian))] == ["Branch Sales NNA"]
    assert meanings("net new assets", meridian_names(meridian)) == [{"term": "Net New Assets", "default_alias": False, "named": True}]


def test_a_term_ending_in_the_name_is_a_meaning_and_one_merely_holding_it_is_not():
    names = {"Alpha Beta": [], "Beta": ["b"], "Beta Gamma": [], "Delta Beta Share": []}
    assert [(m["term"], m["named"], m["default_alias"]) for m in meanings("BETA", names)] == [("Beta", True, False), ("Alpha Beta", False, False)]
    assert [(m["term"], m["default_alias"]) for m in meanings("B", names)] == [("Beta", True)]


@pytest.mark.parametrize("case", HARBOUR["grouping"]["cases"], ids=lambda case: case["words"])
def test_an_unread_grouping_word_names_the_declared_dimensions_it_matches(case):
    found = grouping_options(case["words"], HARBOUR["grouping"]["phrases"], HARBOUR["grouping"]["labels"], case["operator"])
    assert sorted(option["dimension"] for option in found) == case["expected"]
    for option in found:
        assert option["label"] == HARBOUR["grouping"]["labels"][option["dimension"]]
        assert option["phrases"] == sorted(p for p, d in HARBOUR["grouping"]["phrases"].items() if d == option["dimension"])


def test_a_grouping_word_is_matched_on_declared_phrases_and_names_never_on_descriptions():
    grouping = HARBOUR["grouping"]
    assert grouping_options("two", grouping["phrases"], grouping["labels"], "by") == []
    named = grouping_options("area", grouping["phrases"], grouping["labels"], "by", names={"port.QUAY_ZN": "Quay area"})
    assert [o["dimension"] for o in named] == ["port.QUAY_ZN"] and named[0]["label"] == "Quay zone"


def test_a_column_label_is_its_business_name_else_its_description_else_its_name():
    business, descriptions = {"port.A": "Berth"}, {"port.A": "berth code", "port.B": "Crane id"}
    assert [column_label(c, business, descriptions) for c in ("port.A", "port.B", "port.C_CD")] == ["Berth", "Crane id", "C_CD"]


def test_meridians_segment_names_several_declared_dimensions_and_sub_segment_one(meridian):
    phrases = meridian_groupings(meridian)
    several = grouping_options("segment", phrases, {}, "by")
    assert len(several) > 1
    assert all(any(word_in("segment", p) for p in option["phrases"]) for option in several)
    declared = phrases["by sub-segment"]
    for spelling in ("sub segment", "subsegment", "Sub-Segment", "sub-segment"):
        assert [option["dimension"] for option in grouping_options(spelling, phrases, {}, "by")] == [declared], spelling


def test_hyphens_spaces_and_joined_forms_compare_alike():
    assert spaced("Sub-Segment") == spaced("sub  segment") == "sub segment"
    assert joined("sub-segment") == joined("Sub Segment") == joined("subsegment") == "subsegment"
    assert word_in("segment", "by sub-segment") and word_in("sub segment", "by sub-segment")
    assert not word_in("seg", "by sub-segment")


@pytest.mark.parametrize("case", HARBOUR["filters"]["cases"], ids=lambda case: case["query"])
def test_a_query_names_the_declared_filter_phrases_it_matches(case):
    found = filter_phrases(case["query"], HARBOUR["filters"]["rows"])
    assert [[row["id"], row["score"]] for row in found] == case["expected"]


def test_meridians_retail_investor_is_a_declared_filter_phrase(meridian):
    rows = [{"id": str(n), "phrase": phrase, "column": predicate["column"], "value": predicate.get("value")}
            for n, predicate in enumerate(meridian["terminology"]["predicates"]) for phrase in predicate.get("phrases", ())]
    found = [row for row in filter_phrases("Retail Investor", rows) if row["score"] == 1]
    assert [row["value"] for row in found] == ["S4100"]


@pytest.mark.parametrize("case", HARBOUR["fiscal_years"], ids=lambda case: f"{case['value']}@{case['start_month']}")
def test_a_fiscal_year_is_the_twelve_months_from_the_packs_start(case):
    first, last = labelled_year_window(case["value"], case["start_month"])
    assert [first.isoformat(), last.isoformat()] == case["window"]


def test_meridians_fiscal_year_follows_its_april_calendar(meridian):
    start = next(c["year_start_month"] for c in meridian["calendars"] if c["quarter_label"].startswith("FY"))
    assert labelled_year_window("FY25", start) == (datetime.date(2024, 4, 1), datetime.date(2025, 3, 31))


def test_the_context_window_reads_a_fiscal_year_from_the_domain_and_every_other_period_as_asked_window():
    from types import SimpleNamespace

    from kge.services.ask.engine.agent import context_window
    from kge.services.ask.engine.question import Period, Request, asked_window
    domain = SimpleNamespace(FISCAL_START=7)
    assert context_window(Request(question="", period=Period("fiscal_year", "FY25")), domain) == ("2024-07-01", "2025-06-30")
    assert context_window(Request(question="", period=Period("fiscal_year", "2025")), domain) == ("2024-07-01", "2025-06-30")
    for period in (Period("fiscal_quarter", "FY26-Q1"), Period("calendar_quarter", "2025-Q2"), Period("month", "2025-06-01"),
                   Period("year", "2024"), None):
        req = Request(question="", period=period)
        assert context_window(req, domain) == asked_window(req, domain)


@pytest.mark.parametrize("module", ["kge/semantics/readingrules.py", "kge/services/ask/engine/openitems.py"])
def test_no_rule_names_a_client_term_dimension_or_value(module):
    source = (Path(__file__).resolve().parents[2] / module).read_text().casefold()
    for word in ("nna", "segment", "meridian", "wealth", "retail", "region", "fiscal"):
        assert word not in source, word


@pytest.mark.parametrize("case", HARBOUR["fiscal_years"], ids=lambda case: f"context-{case['value']}@{case['start_month']}")
def test_the_context_path_reads_a_year_label_from_the_packs_start_and_never_fails(case):
    """get_context's own handling of a year label (decision 19), on the synthetic pack's starts: a note with its twelve
    months where ask's period clause has none for it, and an invalid period, never an error, for a label with no
    window."""
    from types import SimpleNamespace

    from kge.services.ask.engine.agent import unplaced_year, year_clause
    from kge.services.ask.engine.question import Period, Request
    domain = SimpleNamespace(FISCAL_START=case["start_month"])
    period = Period("fiscal_year", case["value"])
    assert year_clause(period, domain) == (None, [], f"{case['value']} read as {case['window'][0]} to {case['window'][1]}")
    assert unplaced_year(Request(question="", period=period), domain) is None
    for degenerate, placed in (("FY0000", False), ("FY0001", case["start_month"] == 1)):
        reason = unplaced_year(Request(question="", period=Period("fiscal_year", degenerate)), domain)
        assert reason == (None if placed else f"Please give a valid period: {degenerate}."), degenerate
