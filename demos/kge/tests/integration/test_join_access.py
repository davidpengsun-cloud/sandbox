"""The join-time access rule: admin gets the combined result, steward is blocked
before any SQL runs against a warehouse it cannot read, and steward still
succeeds within the warehouses it can read.

    .venv/bin/python -m pytest tests -q

Opens the graph directly, so stop the API first; if the graph is locked the
tests ask the running API instead (DEMO_API, default http://127.0.0.1:8000) and
the in-process SQL spy test is skipped.
"""
from __future__ import annotations

import os

import pytest

from scripts.access_demo import ALLOWED_Q, BANK_Q, BLOCKED_Q, Client
from tests.scratch import profile_of


@pytest.fixture(scope="module")
def client():
    """The demo's own profile, its graph or its tokens."""
    c = Client(profile_of(), os.getenv("DEMO_API"))
    yield c


def test_access_mirror(client):
    if client.engine is None:
        pytest.skip("needs the graph in-process")
    g = client.engine.g
    assert g.readable_units("admin") == {"retail", "sme", "corporate", "wealth"}
    assert g.readable_units("steward") == {"retail", "wealth"}


def test_admin_gets_the_combined_sum(client):
    a = client.ask(BLOCKED_Q, "admin")
    assert a["behaviour"] == "sum_with_verdict"
    assert set(a["parts"]) == {"retail", "sme"} and all(v is not None for v in a["parts"].values())
    assert a["figure"] == pytest.approx(sum(a["parts"].values()), abs=0.01)
    assert "denied_warehouses" not in a


def test_steward_is_blocked_on_the_same_question(client):
    a = client.ask(BLOCKED_Q, "steward")
    assert a["behaviour"] == "blocked_access"
    assert a["denied_warehouses"] == ["sme"]
    assert a["allowed_warehouses"] == ["retail"]
    assert "sme" in a["reason"] and "steward" in a["reason"]
    assert "figure" not in a and "parts" not in a and "plan" not in a
    assert a["sql_run"] == []


def test_steward_blocked_across_the_bank_lists_every_denied_warehouse(client):
    admin = client.ask(BANK_Q, "admin")
    assert set(admin["parts"]) == {"retail", "sme", "corporate", "wealth"}
    a = client.ask(BANK_Q, "steward")
    assert a["behaviour"] == "blocked_access"
    assert a["denied_warehouses"] == ["sme", "corporate"]


def test_steward_succeeds_within_its_warehouses(client):
    """Retail and Wealth differ in grain and calendar, so the parts are the answer, the same as admin gets."""
    a = client.ask(ALLOWED_Q, "steward")
    assert a["behaviour"] == "parts_with_reason"
    assert set(a["parts"]) == {"retail", "wealth"} and all(v is not None for v in a["parts"].values())
    assert a["parts"] == client.ask(ALLOWED_Q, "admin")["parts"]
    single = client.ask("Retail revenue for June 2025", "steward")
    assert single["behaviour"] == "figure" and single["unit"] == "retail"


def test_steward_single_warehouse_denial_is_still_a_refusal(client):
    a = client.ask("SME revenue for June 2025", "steward")
    assert a["behaviour"] == "refuse"
    assert a["denied_warehouses"] == ["sme"]


def test_no_sql_reaches_any_warehouse_when_blocked(client, monkeypatch):
    if client.engine is None:
        pytest.skip("SQL spy needs the engine in-process")
    ran: list[str] = []
    real = client.engine.w.run
    monkeypatch.setattr(client.engine.w, "run", lambda unit, sql, *rest: ran.append(unit) or real(unit, sql, *rest))
    client.ask(BLOCKED_Q, "steward")
    assert ran == []
    client.ask(BLOCKED_Q, "admin")
    assert sorted(ran) == ["retail", "sme"]


def test_existing_principals_unchanged_where_allowed(client):
    a = client.ask(BLOCKED_Q, "analyst_enterprise")
    assert a["behaviour"] == "sum_with_verdict"
    r = client.ask(BLOCKED_Q, "analyst_retail")
    assert r["behaviour"] == "blocked_access" and r["denied_warehouses"] == ["sme"]
