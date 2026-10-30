"""Currency spelling belongs to a generic ISO vocabulary, never to a client's terms."""
from types import SimpleNamespace

import pytest

from kge.compiler.checks import ISO_4217
from kge.services.ask.engine.currencies import NAMES
from kge.services.ask.engine.question import currency_asked, requested_currencies


@pytest.mark.parametrize("code,names", NAMES.items())
def test_iso_names_read_as_their_codes(code, names):
    assert code in ISO_4217
    for name in names.split("|"):
        assert requested_currencies(f"Lumen mass in {name}") == [code]
        assert requested_currencies(f"Lumen mass in {name.upper()}s") == [code]


@pytest.mark.parametrize("text", ["in all units", "in the group", "in any market"])
def test_ordinary_words_are_not_currency_codes(text):
    assert requested_currencies(f"Lumen mass {text}") == []


@pytest.mark.parametrize("text", ["in eur", "in Euros", "in euro", ", EUR", "in ＥＵＲ"])
def test_held_and_unheld_currency_spellings_are_equivalent(text):
    domain = SimpleNamespace(CURRENCIES=["USD"], CONVERSIONS=[])
    assert currency_asked(f"Lumen mass {text}", domain) == "EUR"
    domain.CURRENCIES.append("EUR")
    assert currency_asked(f"Lumen mass {text}", domain) is None


@pytest.mark.parametrize("code", sorted(ISO_4217))
def test_every_iso_code_reads_uppercase_and_unambiguous_lowercase(code):
    assert requested_currencies(f"Lumen mass in {code}") == [code]
    ambiguous = {"ALL", "BOB", "COP", "CUP", "GEL", "MAD", "MOP", "PEN", "PHP", "RON", "RUB", "SOS", "TOP", "TRY"}
    assert requested_currencies(f"Lumen mass in {code.lower()}") == ([] if code in ambiguous else [code])


@pytest.mark.parametrize("text", [
    "in all?", "In all,", ", all.", "in top 5 products", ", try retail only", "in php", "in cup",
    "in all units", ", all units", "In sum,", "in real terms", "try again", "a pen", "a cup",
])
def test_round3_ordinary_currency_homographs_are_not_requests(text):
    assert requested_currencies(f"Lumen mass {text}") == []


@pytest.mark.parametrize("text,code", [
    ("in €", "EUR"), ("(€)", "EUR"), ("EUR revenue", "EUR"), ("converted to EUR", "EUR"),
    ("to EUR", "EUR"), ("in pounds", "GBP"), ("in francs", "CHF"), ("in E U R", "EUR"),
    ("in eur", "EUR"), ("in Euros", "EUR"), ("in euro", "EUR"), (", GBP", "GBP"),
    ("in sterling", "GBP"), ("in yen", "JPY"), ("in swiss francs", "CHF"), ("in ＥＵＲ", "EUR"),
    ("in Eur.", "EUR"), ("in USD", "USD"), ("in US dollars", "USD"), ("£", "GBP"), ("¥", "JPY"),
    ("in php currency", "PHP"), ("in php pesos", "PHP"), ("in Egyptian pounds", "EGP"),
    ("in Burundi francs", "BIF"),
])
def test_round3_currency_requests_resolve_and_check_holdings(text, code):
    assert requested_currencies(f"Lumen mass {text}") == [code]
    domain = SimpleNamespace(CURRENCIES=["XXX"], CONVERSIONS=[])
    assert currency_asked(f"Lumen mass {text}", domain) == code
    domain.CURRENCIES = [code]
    assert currency_asked(f"Lumen mass {text}", domain) is None
