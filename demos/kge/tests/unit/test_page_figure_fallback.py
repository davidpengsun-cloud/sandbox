"""Default page contexts can try other readable sources without bypassing refusals."""
from types import SimpleNamespace

import pytest

from kge.services.ask.engine.core import Engine, PRESETS
from kge.services.ask.engine import planpublic, planrules
from kge.services.ask.engine.question import Request
from kge.services.ask.engine.scope import ProductScope
from kge.services.ask.engine.wording import outside_sources


def context_engine(monkeypatch, first, readable=("page", "other")):
    engine = Engine.__new__(Engine)
    engine.UNIT_ORDER = ["page", "other", "hidden"]
    contexts = {unit: SimpleNamespace(unit=unit, fact=f"{unit}.fact", term="Measure") for unit in engine.UNIT_ORDER}
    engine.g = SimpleNamespace(readable_units=lambda principal: set(readable))
    runtime = SimpleNamespace(contexts=contexts, mapping_declarations=[])
    monkeypatch.setattr(planrules, "snapshot", lambda *args: runtime)
    monkeypatch.setattr(planrules, "cohort_terms", lambda *args: [])
    tried = []

    def answer(engine, req, *args, **kwargs):
        tried.extend(req.units)
        return first if req.units == ["page"] else {"behaviour": "figure", "unit": "other", "figure": 42}

    monkeypatch.setattr(planpublic, "selected_answer", answer)
    return engine, tried


def scoped_answer(engine, products, units=()):
    req = Request("Total?", term="Measure", terms_found=["Measure"], units=list(units))
    return planpublic.answer(engine, req, "person", "analysis", PRESETS["graph"],
                             ProductScope(engine, products), {"trace": []})


@pytest.mark.parametrize("first", [{"behaviour": "abstain"}, {"behaviour": "abstain_with_reason"},
                                   {"behaviour": "figure", "figure": None}])
def test_context_falls_back_after_preferred_source_cannot_answer(monkeypatch, first):
    engine, tried = context_engine(monkeypatch, first)
    result = scoped_answer(engine, ["page.fact"])
    assert result == {"behaviour": "figure", "unit": "other", "figure": 42}
    assert tried == ["page", "other"]


@pytest.mark.parametrize("first", [{"behaviour": "refuse"}, {"behaviour": "figure", "figure": 0},
                                   {"behaviour": "figure", "figure": None, "rows": {"group": 0}},
                                   {"behaviour": "clarify"},
                                   {"behaviour": "abstain", "guard": {"failed_at": "static"}}])
def test_refusal_or_answer_keeps_preferred_context(monkeypatch, first):
    engine, tried = context_engine(monkeypatch, first)
    result = scoped_answer(engine, ["page.fact"])
    assert result == first
    assert tried == ["page"]


def test_no_preference_uses_readable_contexts(monkeypatch):
    engine, tried = context_engine(monkeypatch, {"behaviour": "abstain"}, readable=("other",))
    result = scoped_answer(engine, [])
    assert result["unit"] == "other"
    assert tried == ["other"]


def test_explicit_unit_does_not_fall_back(monkeypatch):
    engine, tried = context_engine(monkeypatch, {"behaviour": "abstain"})
    result = scoped_answer(engine, ["page.fact"], ["page"])
    assert result["behaviour"] == "abstain"
    assert tried == ["page"]


@pytest.mark.parametrize("parts,plan,expected", [
    ({"page": None, "second": None}, [{"status": "ok"}, {"status": "ok"}], True),
    ({"page": 0, "second": None}, [{"status": "ok"}, {"status": "ok"}], False),
    ({"page": None, "second": 3}, [{"status": "ok"}, {"status": "ok"}], False),
    ({"page": None, "second": None}, [{"status": "refused"}, {"status": "ok"}], False),
    ({}, [], False),
])
def test_incomplete_parts_fall_back_only_without_any_usable_or_refused_part(parts, plan, expected):
    assert Engine.needs_scope_fallback({"behaviour": "parts_with_reason", "parts": parts, "plan": plan}) is expected


def test_sql_policy_rejection_is_not_a_missing_page_answer():
    assert not Engine.needs_scope_fallback({"behaviour": "abstain", "guard": {"passed": False, "failed_at": "static"}})


@pytest.mark.parametrize("rows,expected", [({}, True), ({"group": None}, True), ({"group": 0}, False),
                                           ({"group": None, "other": 3}, False)])
def test_grouped_figures_fall_back_only_when_no_group_has_a_value(rows, expected):
    assert Engine.needs_scope_fallback({"behaviour": "figure", "figure": None, "rows": rows}) is expected


def test_cohort_provenance_uses_only_final_physical_mapping_and_its_unit():
    answer = {"behaviour": "combined", "unit": "sme", "products": ["retail.monthly_revenue"],
              "figures": [{"column": "monthly_client_revenue.revenue_usd", "value": 42}],
              "trace": [{"step": "map", "columns": [{"column": "monthly_revenue.revenue_usd"}]},
                        {"step": "map", "columns": [{"column": "monthly_client_revenue.revenue_usd"}]}]}
    assert outside_sources(answer) == "From sme.monthly_client_revenue, the certified source for these figures."
    assert len(answer["trace"]) == 2


def test_context_provenance_excludes_failed_preferred_context():
    answer = {"behaviour": "figure", "products": ["page.fact"], "context": {"fact": "other.fact"},
              "trace": [{"step": "context", "fact": "page.failed"}, {"step": "context", "fact": "other.fact"}]}
    assert outside_sources(answer) == "From other.fact, the certified source for these figures."
    assert len(answer["trace"]) == 2


def test_part_provenance_excludes_unsuccessful_attempts():
    answer = {"behaviour": "parts_with_reason", "products": ["page.fact"],
              "parts": {"other": 0, "page": None},
              "plan": [{"status": "ok", "unit": "other", "column": "fact.value", "value": 0},
                       {"status": "abstain", "unit": "page", "column": "failed.value"}],
              "trace": [{"step": "context", "fact": "page.failed"}]}
    assert outside_sources(answer) == "From other.fact, the certified source for these figures."


def test_cohort_provenance_keeps_physical_tables_when_business_aliases_are_shown():
    answer = {"behaviour": "combined", "unit": "wealth", "products": ["wealth.fee_summary"],
              "figures": [{"column": "suitability_review.review_id", "value": 42}],
              "trace": [{"step": "map", "columns": [{"column": "SUIT_RVW_FACT_1.RVW_ID"}]}]}
    assert outside_sources(answer) == "From wealth.SUIT_RVW_FACT_1, the certified source for these figures."


@pytest.mark.parametrize("rows", [[], [{"cells": [{"value": {"kind": "null"}}]}],
    [{"cells": [{"value": {"kind": "integer", "value": 0}}]}]])
def test_a_governed_result_stays_on_its_source_even_without_an_observed_value(rows):
    assert not Engine.needs_scope_fallback({"behaviour": "combined", "figures": [],
        "result": {"columns": [{"id": "m"}], "rows": rows}})


@pytest.mark.parametrize("behaviour", ["figure", "combined"])
def test_checked_result_provenance_uses_only_final_physical_sources(behaviour):
    answer = {"behaviour": behaviour, "products": ["page.fact"], "trace": [
        {"step": "sources", "tables": ["page.failed"]}, {"step": "sources", "tables": ["other.fact"]}]}
    assert outside_sources(answer) == "From other.fact, the certified source for these figures."
