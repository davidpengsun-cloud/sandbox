"""Product identifiers normalize once; page context never blocks a readable answer."""
from types import SimpleNamespace

from kge.services.ask.engine.scope import ProductScope
from kge.services.ask.models import Ask, AskResponse


def engine():
    return SimpleNamespace(UNIT_ORDER=["retail", "sme", "wealth"],
                           g=SimpleNamespace(readable_units=lambda principal: {"retail", "sme"}))


def test_products_are_an_optional_public_contract():
    assert Ask(question="Revenue?").products is None
    assert Ask(question="Revenue?", products=[]).products == []
    assert "products" in AskResponse.model_json_schema()["properties"]


def test_scope_normalizes_aliases_whitespace_case_and_duplicates():
    scope = ProductScope(engine(), [" Retail.Account ", "retail-account", "", "   ", "sme.loan"])
    assert scope.products == ["retail.account", "sme.loan"]
    assert scope.units == ["retail", "sme"]
    assert scope.missing(["retail.account", "retail.monthly_revenue"]) == ["retail.monthly_revenue"]


def test_empty_and_omitted_product_lists_preserve_metadata_without_blocking():
    omitted = ProductScope(engine(), None)
    empty = ProductScope(engine(), [])
    assert omitted.check("analyst", ["sme"]) is None
    assert empty.check("analyst", []) is None
    assert omitted.metadata() == {}
    assert empty.metadata() == {"products": []}


def test_readable_unit_outside_view_can_answer():
    scope = ProductScope(engine(), ["retail.account"])
    assert scope.check("analyst", ["sme"]) is None
    assert scope.metadata() == {"products": ["retail.account"]}


def test_unknown_unit_context_does_not_claim_access_denial_or_stop_answer():
    scope = ProductScope(engine(), ["missing.account"])
    assert scope.check("analyst", []) is None
    assert scope.metadata() == {"products": ["missing.account"]}


def test_denied_units_share_the_known_unit_rule_for_scoped_and_unscoped_callers():
    assert ProductScope(engine(), ["missing.account", "wealth.holdings"]).denied_units("analyst", []) == ["wealth"]
    assert ProductScope(engine(), None).denied_units("analyst", ["missing", "wealth"]) == ["wealth"]
