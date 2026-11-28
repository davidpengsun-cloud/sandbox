"""#707's read-only additions are opt-in (decision 12): without the new flag or kind, get_context, POST /context and
search build what they built before, and the new fields have a declared schema.

    .venv/bin/python -m pytest tests/unit/test_open_items_contract.py -q
"""
from __future__ import annotations

from kge.services.ask.models import Binding, ContextAsk, ContextResponse, OpenItem, TermMap
from kge.services.search.models import DEFAULT_KINDS, Hit, Search, Why
from kge.services.search.service import declared_filters, readable_filters, visible

PACKAGE = {"arm": "treatment", "block": "", "sources": [], "units": [], "certified": {}, "in_window": [], "defines": {},
           "meta": {}, "withheld": [], "needed": []}


def test_context_asks_for_no_bindings_unless_the_flag_is_set():
    assert ContextAsk(question="q").open_items is False
    assert ContextAsk(question="q", open_items=True).open_items is True
    assert set(ContextResponse.model_validate(PACKAGE).model_dump()) == set(PACKAGE)


def test_bindings_and_open_items_validate_against_their_schema():
    body = {**PACKAGE,
            "bindings": [{"kind": "term", "text": "abc", "start": 0, "end": 3, "term": "Alpha", "by": "default_alias"},
                         {"kind": "unread", "text": "zz", "start": 4, "end": 6}],
            "open_items": [{"item": "term", "resolution": "default", "text": "abc", "start": 0, "end": 3,
                            "options": [{"label": "Alpha", "id": "alpha", "definition": "A.", "default_alias": True}]},
                           {"item": "period", "resolution": "unanswerable", "grain": "month",
                            "latest": {"value": "2025-06", "label": "June 2025"},
                            "options": [{"value": "2025-06", "label": "June 2025"}]}]}
    dumped = ContextResponse.model_validate(body).model_dump()
    assert dumped["bindings"][1] == {"kind": "unread", "text": "zz", "start": 4, "end": 6}
    assert dumped["open_items"][0]["options"][0] == {"label": "Alpha", "id": "alpha", "definition": "A.", "default_alias": True}
    for bad in ({"item": "card", "resolution": "default", "options": []}, {"item": "term", "resolution": "suggested", "options": []}):
        try:
            OpenItem.model_validate(bad)
        except ValueError:
            continue
        raise AssertionError(bad)
    assert "suggested" not in OpenItem.model_json_schema()["properties"]
    assert set(Binding.model_json_schema()["properties"]["kind"]["enum"]) >= {"term", "period", "filter", "grouping", "unread"}


def test_a_term_map_carries_resolves_to_only_when_given():
    base = {"term": "x", "principal": "p", "compact": True, "definition": "", "steward": "", "data_sets": [], "defined_by": [],
            "contexts": [], "standards": []}
    assert "resolves_to" not in TermMap.model_validate(base).model_dump(exclude_unset=True)


def test_search_without_kinds_never_returns_a_filter_phrase_and_a_hit_keeps_its_old_fields():
    assert "filter" not in DEFAULT_KINDS and set(DEFAULT_KINDS) == {"product", "term", "metric", "column", "document"}
    assert Search(query="x").kinds is None
    hit = Hit(kind="term", unit="u", id="t", title="T", score=1, why=Why(kind="term", text="T"))
    assert set(visible(hit)) == {"kind", "unit", "id", "title", "score", "why", "restricted"}
    phrase = Hit(kind="filter", unit="u", id="filter:1:a", title="A", score=1, why=Why(kind="phrase", text="A"), label="L", value="v")
    assert visible(phrase)["label"] == "L" and "column" not in visible(phrase)


def test_declared_filters_read_any_packs_predicates_and_code_names():
    domain = {"vocabulary": [
        {"kind": "predicate", "params": {"column": "port.CNTR_TYP", "op": "eq", "phrases": ["Reefer", "cold box"], "value": "RF"}},
        {"kind": "predicate", "params": {"column": "port.MOVE", "op": "in", "phrases": ["moves"], "values": ["A", "B"]}},
        {"kind": "code_name_pair", "params": {"code": "port.OPR_CD", "name": "port.OPR_NM",
                                              "members": [{"code": "7", "name": "North Quay Ltd"}]}},
        {"kind": "intent", "params": {"intent": "count", "phrases": ["how many"]}}]}
    rows = declared_filters(domain)
    assert rows[-1]["name_column"] == "port.OPR_NM"
    assert [(r["phrase"], r["column"], r["value"]) for r in rows] == [
        ("Reefer", "port.CNTR_TYP", "RF"), ("cold box", "port.CNTR_TYP", "RF"), ("moves", "port.MOVE", "A, B"),
        ("North Quay Ltd", "port.OPR_CD", "7")]
    assert len({r["id"] for r in rows}) == len(rows) and not any("CNTR" in r["id"] for r in rows)


def test_a_filter_phrase_is_shown_only_for_a_readable_unrestricted_column_in_scope():
    rows = [{"id": "1", "phrase": "Reefer", "column": "port.CNTR.TYP", "value": "RF", "score": 1.0},
            {"id": "2", "phrase": "Captain Ada", "column": "port.CREW.NAME", "value": "Ada", "score": 1.0},
            {"id": "3", "phrase": "Pier 9", "column": "yard.PIER.CD", "value": "9", "score": 1.0}]
    kept = readable_filters(rows, {"port.CREW.NAME"}, ["port"], None)
    assert [r["id"] for r in kept] == ["1"]
    assert readable_filters(rows, set(), ["port", "yard"], {"yard.pier"}) == [rows[2]]
    assert readable_filters(rows, set(), [], None) == []


def test_a_codes_name_is_never_shown_where_its_name_column_is_restricted():
    rows = [{"id": "1", "phrase": "North Quay Ltd", "column": "port.OPR.CD", "value": "7", "name_column": "port.OPR.NM", "score": 1.0}]
    assert readable_filters(rows, {"port.OPR.NM"}, ["port"], None) == []
    assert readable_filters(rows, set(), ["port"], None) == rows


def test_a_dry_run_stops_before_a_model_request_and_a_query_and_nothing_else_changes():
    """get_context's open items run ask dry (dryrun.py): a model request stops it as a query does, before the model
    is sent anything; outside a dry run both hooks do nothing."""
    import pytest

    from kge.services.ask.engine.dryrun import (WouldAskModel, WouldRun, calendar_current, dry, dry_run,
                                                stop_before_model, stop_before_query)
    from kge.services.ask.engine.combined import GlossaryContract
    from kge.services.ask.engine.modelcall import structured_call

    class Model:
        sent = 0

        def structured(self, request):
            Model.sent += 1
            raise AssertionError("a dry run sent the model a request")
    assert not dry() and stop_before_model() is None and stop_before_query() is None and not calendar_current("reference")
    with dry_run(lambda runtime: runtime == "reference"):
        assert dry() and calendar_current("reference") and not calendar_current("live")
        with pytest.raises(WouldRun) as stopped:
            stop_before_query()
        assert not isinstance(stopped.value, WouldAskModel)
        with pytest.raises(WouldAskModel):
            structured_call(Model(), "classify", version="v", instructions="", data={}, question="q",
                            contract=GlossaryContract, legacy_prompt="q")
    assert Model.sent == 0 and not dry()
