"""Period failures preserve the rest of the request and the established full-month reading."""
import re
from types import SimpleNamespace

import pytest

from kge.services.ask.engine.question import Period, parse


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
