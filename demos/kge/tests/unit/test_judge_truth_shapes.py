"""#565: judge puts an item's expectation on the truth its reference SQL gives on the runtime an answer ran on, whatever
shape that truth takes: a table (named or positional columns), grouped rows, a single figure, decimal and date cells.
Truth that cannot be an expectation is unavailable, a warehouse failure that is not a refusal is the run's and is not
cached, and the kit's sample suite shape is scored on the reference warehouse and a draft elsewhere. No warehouse: a
stand-in whose reference SQL gives each runtime its own result."""
from __future__ import annotations

import datetime as dt
import json
from decimal import Decimal

import pytest

from kge.ports import REFERENCE
from kge.eval import sets
from kge.eval.run_golden import cell, judge, with_truth
from kge.ports import QueryRefused

LIVE = "live"
APPROVED = {"reference_sql_approved_by": "steward@example.com", "reference_sql_approved_on": "2026-11-19"}
SQL = "SELECT segment, SUM(revenue) FROM ${SCHEMA}.monthly_revenue GROUP BY 1"


class Warehouse:
    """Reference SQL run per runtime: the result held for that runtime, or what it raises."""

    versions = {"retail": 1}

    def __init__(self, results: dict, *, active: bool = False, defaults: tuple[str, ...] = (REFERENCE,)):
        self.results = results
        self.active = active
        self.defaults = list(defaults)
        self.calls: list[dict] = []

    def resolve_reference_sql(self, sql, *, unit=None, runtimes=None, runtime_id=None, readable_units=None):
        rt = runtime_id or (runtimes or self.defaults)[0]
        self.calls.append({"sql": sql, "runtime_id": runtime_id, "runtimes": runtimes})
        result = self.results[rt]
        if isinstance(result, Exception):
            raise result
        return {"sql": sql, "runtime": {"id": rt}, **result}

    def runtimes_active(self, requested=None):
        return self.active

    def default_runtime_list(self):
        return list(self.defaults)


def table_item(**extra) -> dict:
    return {"id": "T", "kind": "within", "unit": "retail", "reference_sql": SQL, **APPROVED,
            "expect": {"table": {"columns": ["segment", "revenue"], "rows": [["Mass", 1.0]]}, "measures": ["revenue"]},
            **extra}


def table_answer(rows: list[tuple[str, float]], runtime: str = REFERENCE) -> dict:
    return {"behaviour": "figure", "runtime": {"id": runtime},
            "result": {"rows": [{"dimensions": [{"value": key}], "cells": [{"value": {"value": value}}]}
                                for key, value in rows]}}


def test_a_table_item_takes_its_runtimes_rows_with_its_measures():
    w = Warehouse({REFERENCE: {"columns": ["segment", "revenue"], "rows": [["Mass", 100.0], ["Affluent", 200.0]]}})
    item = table_item()
    truth, why = with_truth(item, {"columns": ["segment", "revenue"], "rows": [["Mass", 100.0], ["Affluent", 200.0]]})
    assert why == "" and truth["expect"]["measures"] == ["revenue"]
    assert truth["expect"]["table"] == {"columns": ["segment", "revenue"], "rows": [["Mass", 100.0], ["Affluent", 200.0]]}
    assert item["expect"]["table"]["rows"] == [["Mass", 1.0]]
    assert judge(item, table_answer([("Mass", 100.0), ("Affluent", 200.0)]), w=w)["status"] == "passed"
    assert judge(item, table_answer([("Mass", 1.0)]), w=w)["status"] == "failed"


def test_a_runtime_that_renames_its_columns_is_read_by_position():
    w = Warehouse({LIVE: {"columns": ["seg_code", "rev_usd"], "rows": [["Mass", 150.0]]}})
    truth, why = with_truth(table_item(), {"columns": ["seg_code", "rev_usd"], "rows": [["Mass", 150.0]]})
    assert why == "" and truth["expect"]["table"]["columns"] == ["segment", "revenue"]
    verdict = judge(table_item(), table_answer([("Mass", 150.0)], LIVE), w=w)
    assert verdict["status"] == "passed" and verdict["runtime"] == LIVE, verdict


def test_a_table_whose_column_count_differs_is_unavailable_not_an_error():
    w = Warehouse({LIVE: {"columns": ["a", "b", "c"], "rows": [["Mass", 1, 150.0]]}})
    truth, why = with_truth(table_item(), {"columns": ["a", "b", "c"], "rows": [["Mass", 1, 150.0]]})
    assert truth is None and "not the item's segment, revenue" in why
    verdict = judge(table_item(), table_answer([("Mass", 150.0)], LIVE), w=w)
    assert verdict["status"] == "unavailable" and verdict["ok"] is None
    assert verdict["why"].startswith(f"expectation not available on {LIVE}: its reference SQL returned columns a, b, c")


def test_a_grouped_figure_takes_its_runtimes_rows():
    item = {"id": "R", "kind": "figure", "unit": "retail", "reference_sql": SQL, **APPROVED,
            "expect": {"rows": {"Mass": 1.0, "Affluent": 2.0}}}
    w = Warehouse({REFERENCE: {"columns": ["segment", "revenue"], "rows": [["Mass", 100.0], ["Affluent", 200.0]],
                               "figure": None}})
    truth, _ = with_truth(item, {"rows": [["Mass", 100.0], ["Affluent", 200.0]]})
    assert truth["expect"]["rows"] == {"Mass": 100.0, "Affluent": 200.0}
    answer = {"behaviour": "figure", "figure": 300.0, "rows": {"Mass": 100.0, "Affluent": 200.0}}
    assert judge(item, answer, w=w)["status"] == "passed"
    assert judge(item, dict(answer, rows={"Mass": 1.0, "Affluent": 2.0}), w=w)["status"] == "failed"
    assert with_truth(item, {"rows": [["Mass"]]}) == (None, "its reference SQL returned no grouped rows")


def test_decimal_and_date_cells_are_compared_as_numbers_and_iso_text():
    assert cell(Decimal("100.50")) == 100.5 and isinstance(cell(Decimal("1")), float)
    assert cell(dt.date(2025, 6, 30)) == "2025-06-30"
    assert cell(dt.datetime(2025, 6, 30, 12, 0)) == "2025-06-30T12:00:00"
    assert [cell(v) for v in (None, True, 3, "x")] == [None, True, 3, "x"]
    item = table_item(expect={"table": {"columns": ["month", "revenue"], "rows": []}, "measures": ["revenue"]})
    w = Warehouse({REFERENCE: {"columns": ["month", "revenue"], "rows": [(dt.date(2025, 6, 30), Decimal("100.50"))],
                               "figure": Decimal("100.50")}})
    cache: dict = {}
    assert judge(item, table_answer([("2025-06-01", 100.5)]), w=w, cache=cache)["status"] == "passed"
    entry, = cache.values()
    assert entry["rows"] == [["2025-06-30", 100.5]] and entry["figure"] == 100.5
    figure = {"id": "F", "kind": "within", "unit": "retail", "reference_sql": SQL, **APPROVED, "expect": {}}
    assert judge(figure, {"behaviour": "figure", "figure": 100.5}, w=w)["status"] == "passed"


@pytest.mark.parametrize("result", [{"columns": ["v"], "rows": [], "figure": None},
                                    {"columns": ["v"], "rows": [[None]], "figure": None}])
def test_no_rows_or_a_null_figure_is_unavailable(result):
    w = Warehouse({REFERENCE: result})
    figure = {"id": "F", "kind": "within", "unit": "retail", "reference_sql": SQL, **APPROVED,
              "expect": {"figure": 5.0}}
    verdict = judge(figure, {"behaviour": "figure", "figure": 5.0}, w=w)
    assert verdict["status"] == "unavailable" and verdict["ok"] is None
    assert "its reference SQL returned no figure" in verdict["why"]
    if not result["rows"]:
        table = judge(table_item(), table_answer([("Mass", 1.0)]), w=w)
        assert table["status"] == "unavailable" and "returned no rows" in table["why"]


def test_a_warehouse_failure_that_is_not_a_refusal_propagates_and_is_not_cached():
    w = Warehouse({REFERENCE: RuntimeError("warehouse unreachable"), LIVE: QueryRefused("no mapping on live")})
    figure = {"id": "F", "kind": "within", "unit": "retail", "reference_sql": SQL, **APPROVED, "expect": {}}
    cache, refused = {}, []
    for _ in range(2):
        with pytest.raises(RuntimeError, match="warehouse unreachable"):
            judge(figure, {"behaviour": "figure", "figure": 1.0}, w=w, cache=cache, refused=refused)
    assert cache == {} and refused == [] and len(w.calls) == 2
    verdict = judge(figure, {"behaviour": "figure", "figure": 1.0, "runtime": {"id": LIVE}}, w=w, cache=cache,
                    refused=refused)
    assert verdict["status"] == "unavailable" and "no mapping on live" in verdict["why"]
    assert refused == [{"id": "F", "runtime": LIVE, "reason": "no mapping on live"}] and len(cache) == 1


def test_an_approved_refusal_is_scored_on_behaviour_without_running_its_sql():
    w = Warehouse({REFERENCE: RuntimeError("must not run"), LIVE: RuntimeError("must not run")})
    item = {"id": "N", "kind": "refusal", "unit": "retail", "reference_sql": SQL, **APPROVED,
            "expect": {"behaviour": "refuse"}}
    for rt in (REFERENCE, LIVE):
        assert judge(item, {"behaviour": "refuse", "runtime": {"id": rt}}, w=w)["status"] == "passed"
        assert judge(item, {"behaviour": "figure", "figure": 3.0, "runtime": {"id": rt}}, w=w)["status"] == "failed"
    assert w.calls == []


def test_the_kit_sample_suite_is_scored_on_the_reference_warehouse_and_a_draft_elsewhere(tmp_path):
    suite = tmp_path / "format_sample.json"
    suite.write_text(json.dumps({
        "suite_id": "format_sample",
        "suite_version": "1.0",
        "title": "Invented revenue format sample",
        "defaults": {"unit": "retail", "tolerance": {"abs": 0.01, "rel": 0}},
        "templates": [{
            "id_prefix": "REVENUE",
            "kind": "within",
            "question_template": "Total retail revenue for {month} 2025",
            "reference_sql": "SELECT SUM(total_revenue_usd) FROM ${SCHEMA}.monthly_revenue WHERE month_start = DATE '{start}'",
            "matrix": [{"month": "April", "start": "2025-04-01"}, {"month": "May", "start": "2025-05-01"}],
        }],
    }))
    items = sets.read(suite)["items"]
    assert len(items) == 2
    item = items[0]
    assert sets.reference_sql(item) and not sets.approved(item) and "figure" not in item["expect"]
    w = Warehouse({REFERENCE: {"columns": ["total"], "rows": [[120.0]], "figure": 120.0},
                   LIVE: {"columns": ["total"], "rows": [[999.0]], "figure": 999.0}})
    wrong = judge(item, {"behaviour": "figure", "figure": 99.0, "runtime": {"id": REFERENCE}}, w=w)
    assert wrong["status"] == "failed" and wrong["ok"] is False and wrong["runtime"] == REFERENCE, wrong
    right = judge(item, {"behaviour": "figure", "figure": 120.0, "runtime": {"id": REFERENCE}}, w=w)
    assert right["status"] == "passed", right
    assert {c["runtime_id"] for c in w.calls} == {REFERENCE}
    elsewhere = judge(item, {"behaviour": "figure", "figure": 999.0, "runtime": {"id": LIVE}}, w=w)
    assert elsewhere["status"] == "draft" and elsewhere["ok"] is None and elsewhere["runtime"] == LIVE
    assert {c["runtime_id"] for c in w.calls} == {REFERENCE}


def test_an_answer_that_ran_no_query_is_judged_on_its_behaviour_never_a_draft():
    w = Warehouse({LIVE: {"columns": ["v"], "rows": [[7.0]], "figure": 7.0},
                   REFERENCE: {"columns": ["v"], "rows": [[3.0]], "figure": 3.0}},
                  active=True, defaults=(LIVE, REFERENCE))
    stored = {"id": "D", "kind": "within", "unit": "retail", "expect": {"figure": 3.0}}
    declined = judge(stored, {"behaviour": "abstain"}, w=w)
    assert declined["status"] == "failed" and declined["ok"] is False, declined
    figure = {"id": "F", "kind": "within", "unit": "retail", "reference_sql": SQL, **APPROVED, "expect": {}}
    verdict = judge(figure, {"behaviour": "abstain"}, w=w)
    assert verdict["status"] == "failed" and verdict["runtime"] == LIVE, verdict
    verdict = judge(figure, {"behaviour": "figure", "figure": 3.0}, w=w, runtimes=[REFERENCE, LIVE])
    assert verdict["status"] == "passed" and verdict["runtime"] == REFERENCE, verdict


def test_parts_and_ratios_are_not_read_from_a_query_and_group_keys_match_the_items():
    w = Warehouse({LIVE: {"columns": ["month", "v"], "rows": [["2024-06-30", 5.0], ["2024-07-31", 6.0]], "figure": None}},
                  active=True, defaults=(LIVE,))
    parts = {"id": "P", "kind": "cross", "unit": "retail", "reference_sql": SQL, **APPROVED,
             "expect": {"behaviour": "figure", "parts": {"retail": 1.0}}}
    assert judge(parts, {"behaviour": "figure", "runtime": {"id": LIVE}}, w=w)["status"] == "unavailable"
    ratio = {"id": "R", "kind": "within", "unit": "retail", "reference_sql": SQL, **APPROVED,
             "expect": {"figure": 1.0, "ratio": 0.5}}
    assert judge(ratio, {"behaviour": "figure", "figure": 1.0, "runtime": {"id": LIVE}}, w=w)["status"] == "unavailable"
    grouped = {"id": "G", "kind": "within", "unit": "retail", "reference_sql": SQL, **APPROVED,
               "expect": {"rows": {"2024-06": 1.0, "2024-07": 2.0}}}
    right = {"behaviour": "figure", "rows": {"2024-06": 5.0, "2024-07": 6.0}, "figure": 11.0, "runtime": {"id": LIVE}}
    assert judge(grouped, right, w=w)["status"] == "passed"
