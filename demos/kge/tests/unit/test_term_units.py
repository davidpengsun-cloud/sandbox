"""The access check reads every unit a question's term would be read in, on a stub engine with no graph."""
from types import SimpleNamespace

import pytest

from kge.services.ask.engine import planrules
from kge.services.ask.engine.core import Engine, term_units
from kge.services.ask.engine.question import Request
from kge.services.ask.engine.scope import ProductScope


def context(unit):
    return SimpleNamespace(unit=unit, fact=f"{unit}.fact", term="Measure", selectors=(), joins=())


def stub_engine(monkeypatch, context_units, mapping_units):
    engine = Engine.__new__(Engine)
    engine.UNIT_ORDER = ["a", "b", "c"]
    contexts = [context(unit) for unit in context_units]
    engine._semantics = SimpleNamespace(contexts_of=lambda term: list(contexts) if term == "Measure" else [])
    engine.g = SimpleNamespace(readable_units=lambda principal: {"a", "b"})
    runtime = SimpleNamespace(mapping_declarations=[{"term": "Measure", "unit": unit} for unit in mapping_units])
    monkeypatch.setattr(planrules, "snapshot", lambda *args, **kwargs: runtime)
    monkeypatch.setattr(planrules, "cohort_terms", lambda *args: [])
    return engine


def units_for(engine, units):
    req = Request("Total measure?", term="Measure", terms_found=["Measure"], units=list(units))
    return term_units(engine, req, ProductScope(engine, None), "person")


@pytest.mark.parametrize("mapping_units", [[], ["c"], ["a"]])
def test_a_term_read_only_in_a_third_unit_is_checked_when_two_units_are_asked(monkeypatch, mapping_units):
    """#505 review: two requested units and a term whose only context is in a third; planrules.read reads it there."""
    engine = stub_engine(monkeypatch, ["c"], mapping_units)
    assert units_for(engine, ["a", "b"]) == ["c"]


def test_a_term_in_the_requested_units_is_read_there_alone(monkeypatch):
    engine = stub_engine(monkeypatch, ["a", "c"], ["a", "c"])
    assert units_for(engine, ["a", "b"]) == ["a"]
    assert units_for(engine, ["a"]) == ["a"]


def test_one_requested_unit_with_the_term_mapped_there_is_read_there(monkeypatch):
    engine = stub_engine(monkeypatch, ["c"], ["a", "c"])
    assert units_for(engine, ["a"]) == ["a"]


@pytest.mark.parametrize("mapping_units", [[], ["c"]])
def test_one_requested_unit_without_the_term_still_checks_where_it_is_read(monkeypatch, mapping_units):
    engine = stub_engine(monkeypatch, ["c"], mapping_units)
    assert units_for(engine, ["a"]) == ["c"]
