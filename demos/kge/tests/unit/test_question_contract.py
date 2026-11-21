"""Period failures preserve the rest of the request and the established full-month reading."""
import re
from types import SimpleNamespace

import pytest

from kge.services.ask.engine.question import Period, parse, definition_request


@pytest.mark.parametrize("aliases", [True, False])
@pytest.mark.parametrize("period", ["June", "FY26 Q1", "31 June 2025"])
def test_invalid_period_keeps_filters_and_aggregate(aliases, period):
    domain = SimpleNamespace(
        ALIASES={}, UNIT_WORDS={},
        INTENTS={"count": re.compile("how many")},
        FILTERS=[(re.compile("open"), "resolved_on", "IS NULL", None, None)],
        FILTER_COLUMNS=["retail.complaint.resolved_on"],
    )
    req = parse(f"How many complaints open in {period}", [], domain, aliases=aliases)
    assert req.special == "invalid_period" and req.problem and req.period is None
    assert req.filters == [("resolved_on", "IS NULL", None)]
    assert req.filtered == ["retail.complaint.resolved_on"]
    assert req.agg == "count"


@pytest.mark.parametrize("text,expected", [
    ("30 June 2025", Period("month", "2025-06-01")),
    ("at 30 June 2025", Period("as_of", "2025-06-30")),
    ("as at 30 June 2025", Period("as_of", "2025-06-30")),
    ("to 30 June 2025", Period("as_of", "2025-06-30")),
    ("30 Jun 2025", Period("as_of", "2025-06-30")),
    ("30th June 2025", Period("as_of", "2025-06-30")),
    ("30/06/2025", Period("as_of", "2025-06-30")),
    ("June 30, 2025", Period("as_of", "2025-06-30")),
])
def test_full_month_compatibility_does_not_change_explicit_day_forms(text, expected):
    req = parse(f"Revenue, {text}", [], SimpleNamespace(ALIASES={}, UNIT_WORDS={}))
    assert req.period == expected and req.problem is None


def test_bare_full_month_does_not_hide_an_impossible_day():
    req = parse("Revenue, 31 June 2025", [], SimpleNamespace(ALIASES={}, UNIT_WORDS={}))
    assert req.period is None and req.problem == "That date does not exist: 31 june 2025."


@pytest.mark.parametrize("held", ["GBP", "USD"])
def test_currency_case_obeys_the_domains_holdings(held):
    domain = SimpleNamespace(ALIASES={}, UNIT_WORDS={}, CURRENCIES=[held], CONVERSIONS=[])
    for code in ("GBP", "gbp"):
        req = parse(f"Revenue in {code} for Q2 2025", ["Revenue"], domain)
        assert req.special == (None if held == "GBP" else "no_conversion_standard")


@pytest.mark.parametrize("tense", ["is", "was", "are", "were"])
@pytest.mark.parametrize("period", ["June 2025", "2025", "Q2 2025"])
def test_measure_with_period_is_a_figure_whatever_its_tense(tense, period):
    domain = SimpleNamespace(ALIASES={}, UNIT_WORDS={}, INTENTS={"definitional": re.compile(r"what (?:is|are)", re.I)})
    req = parse(f"What {tense} the total revenue in {period}?", ["Revenue"], domain)
    assert req.term == "Revenue" and req.period is not None and req.problem is None
    assert not req.definitional


@pytest.mark.parametrize("question", [
    "What is the definition of revenue in June 2025?",
    "What is revenue defined as in June 2025?",
    "What is the meaning of revenue in June 2025?",
    "What is the revenue version in June 2025?",
    "What is revenue?",
    "What is Revenue? in 2025",
])
def test_explicit_definition_keeps_its_intent_with_a_period(question):
    domain = SimpleNamespace(ALIASES={}, UNIT_WORDS={}, INTENTS={"definitional": re.compile("what is", re.I)})
    assert parse(question, ["Revenue"], domain).definitional


@pytest.mark.parametrize("question", [
    "Explain which revenue was recorded in June 2025",
    "Is it acceptable to sum revenue in June 2025?",
    "Explain which revenue sources are available",
    "Is it acceptable to sum revenue?",
    "Explain which transactions occurred in June",
])
def test_explanatory_openings_do_not_replace_a_numeric_or_invalid_period_request(question):
    domain = SimpleNamespace(ALIASES={}, UNIT_WORDS={})
    assert not definition_request(parse(question, ["Revenue"], domain)).definitional
