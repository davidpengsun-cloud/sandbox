"""Model period values are untrusted, canonical dates before any planner sees them."""
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from kge.services.ask.engine.model_period import read, validate
from kge.services.ask.engine.question import Period


@pytest.mark.parametrize("value,expected", [
    ({"kind": "day", "value": "2024-02-29"}, Period("as_of", "2024-02-29")),
    ({"kind": "month", "value": "2025-06"}, Period("month", "2025-06-01")),
    ({"kind": "month", "value": "2025-06-01"}, Period("month", "2025-06-01")),
    ({"kind": "quarter", "value": "2025-Q2"}, Period("calendar_quarter", "2025-Q2")),
    ({"kind": "year", "value": "2025"}, Period("year", "2025")),
])
def test_valid_model_values_use_existing_period_types(value, expected):
    assert read(value) == (expected, None)


@pytest.mark.parametrize("value", [
    None, [], "2025-06", 2025, True, {}, {"kind": "month"}, {"kind": [], "value": "2025"},
    {"kind": "day", "value": "2025-02-29"}, {"kind": "day", "value": "2025-02-31"},
    {"kind": "day", "value": "0000-01-01"}, {"kind": "day", "value": "20250630"},
    {"kind": "day", "value": "9999-12-31"},
    {"kind": "month", "value": "9999-12"}, {"kind": "month", "value": "2025-06-30"},
    {"kind": "quarter", "value": "0000-Q1"}, {"kind": "quarter", "value": "2025-Q0"},
    {"kind": "quarter", "value": "9999-Q4"}, {"kind": "year", "value": "9999"},
    {"kind": "year", "value": "0000"}, {"kind": "year", "value": 2025},
    {"kind": "day", "value": "2025-06-30' OR 1=1 --"},
    {"kind": "month", "value": "2025-06", "start": "2025-01-01"},
    {"kind": "range", "start": "2025-01-01", "end": "2025-06-30"},
])
def test_invalid_and_unsupported_values_have_a_reason(value):
    period, reason = read(value)
    assert period is None and reason


@pytest.mark.parametrize("kind,probes", [("month", 4), ("snapshot", 8)])
def test_validation_deduplicates_by_unit_and_table_except_snapshots(monkeypatch, kind, probes):
    """Monthly count/sum share probes; snapshot count/avg retain their separate clauses."""
    monkeypatch.setattr("kge.services.ask.engine.model_period.build_allowlist", lambda *a: object())
    warehouse = SimpleNamespace(gate=Mock(return_value={"passed": True}),
                                row=Mock(side_effect=lambda unit, sql, dialect:
                                         (10, 1) if sql.startswith("SELECT count") else ("2025-01-01", "2025-12-01")))
    engine = SimpleNamespace(PERIODS={f"{u}.activity": {"kind": kind, "column": "period", "calendar": "standard"}
                                     for u in ("north", "south")},
                             CALENDARS={"standard": {"year_start_month": 1}}, RESTRICTED_COLUMNS=[], g=object(), w=warehouse,
                             period_clause=Mock(return_value=("period = DATE '2025-06-01'", [], None)))
    targets = [(u, "activity", agg) for u in ("north", "south") for agg in ("count", "avg", "count")]
    assert validate(engine, Period("month", "2025-06-01"), targets, object()) is None
    assert warehouse.gate.call_count == warehouse.row.call_count == probes
    assert engine.period_clause.call_count == probes // 2


def test_snapshot_aggregation_refusal_is_checked_before_probes(monkeypatch):
    monkeypatch.setattr("kge.services.ask.engine.model_period.build_allowlist", lambda *a: object())
    engine = SimpleNamespace(PERIODS={"north.activity": {"kind": "snapshot", "column": "period"}},
                             RESTRICTED_COLUMNS=[], g=object(), w=SimpleNamespace(gate=Mock(), row=Mock()),
                             period_clause=Mock(side_effect=[("period = DATE '2025-06-01'", [], None),
                                                            (None, [], "average cannot apply that period")]))
    reason = validate(engine, Period("year", "2025"), [("north", "activity", "sum"), ("north", "activity", "avg")], object())
    assert reason == "average cannot apply that period"
    engine.w.gate.assert_not_called()
    engine.w.row.assert_not_called()


def test_readme_states_the_model_period_eligibility_boundary():
    from kge.pipeline.settings import ROOT

    paragraph = (ROOT / "README.md").read_text().split("On the model resolution path,", 1)[1].split("\n\n", 1)[0]
    for phrase in ("only when the parser saw a period-like token", "placed no part", "Ranges and comparisons",
                   "impossible dates", "partial readings", "no period-like token", "asks for a definition",
                   "just one such expression", "anywhere in the question", "and/or/then", "without a model call",
                   "every four-digit year", "a bare year accepts only a year", "a named grain must be preserved"):
        assert phrase in paragraph
