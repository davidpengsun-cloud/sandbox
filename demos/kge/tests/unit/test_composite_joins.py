from types import SimpleNamespace

import pytest

from kge.pipeline.sqlguard import Allowlist, check
from kge.semantics.query import Join, Semantics
from kge.services.data.engine.governance import declared_allowlist


@pytest.fixture
def allow():
    sem = Semantics.__new__(Semantics)
    sem.d = SimpleNamespace(RESTRICTED_COLUMNS=[], PERIODS={}, GROUP_BY={}, ACRONYMS={})
    schema = {"flow": {"account_id", "source_system"}, "account": {"account_id", "source_system", "household_id"},
              "household": {"household_id"}}
    sem.tables = {f"wealth.{t}": dict.fromkeys(cols, "VARCHAR") for t, cols in schema.items()}
    sem.scd2, sem.entities, sem.cites = {}, {}, {}
    sem.declared = {"flow_account": Join("flow_account", "key",
                    "flow.account_id = account.account_id AND flow.source_system = account.source_system",
                    None, None, "wealth.flow", "wealth.account", True)}
    result = Allowlist("wealth", set(schema), schema, schema=schema)
    result.joins.add(frozenset({("account", "household_id"), ("household", "household_id")}))
    return declared_allowlist(SimpleNamespace(semantics=sem), "wealth", result)


@pytest.mark.parametrize("join", [
    "JOIN account a ON f.account_id = a.account_id AND f.source_system = a.source_system",
    "JOIN account a ON (a.source_system = f.source_system) AND (a.account_id = f.account_id)",
    "JOIN account a USING (account_id, source_system)",
    ", account a WHERE f.account_id = a.account_id AND f.source_system = a.source_system",
])
def test_full_composite_passes(allow, join):
    assert check(f"SELECT f.account_id FROM flow f {join}", allow, inner_only=True) == []


@pytest.mark.parametrize("join, missing", [
    ("JOIN account a ON f.account_id = a.account_id", "account.source_system = flow.source_system"),
    ("JOIN account a USING (account_id)", "account.source_system = flow.source_system"),
    ("JOIN account a ON f.source_system = a.source_system", "account.account_id = flow.account_id"),
    ("JOIN account a USING (source_system)", "account.account_id = flow.account_id"),
    ("JOIN account a ON f.account_id = a.account_id JOIN account b ON f.source_system = b.source_system",
     "account.source_system = flow.source_system"),
])
def test_partial_composite_names_missing_pairs(allow, join, missing):
    problems = check(f"SELECT f.account_id FROM flow f {join}", allow, inner_only=True)
    assert any("flow_account requires all its column pairs together" in p and missing in p for p in problems)


def test_catalogue_single_column_join_unchanged(allow):
    assert check("SELECT a.account_id FROM account a JOIN household h USING (household_id)", allow, inner_only=True) == []


@pytest.mark.parametrize("sql, reason", [
    ("SELECT f.account_id FROM flow f WHERE EXISTS (SELECT 1 FROM account a2 "
     "WHERE a2.account_id = f.account_id AND a2.household_id = 5001)", "requires all its column pairs"),
    ("SELECT f.account_id FROM flow f JOIN account a ON f.account_id = a.account_id AND 1=1", "conjunction"),
    ("SELECT f.account_id FROM flow f, account a WHERE f.account_id = a.account_id OR "
     "f.source_system = a.source_system", "not joined"),
])
def test_person_authored_join_rules_do_not_change_planner_checks(allow, sql, reason):
    assert check(sql, allow) == []
    assert any(reason in problem for problem in check(sql, allow, inner_only=True))


def test_catalogue_without_declared_composites_is_unchanged(allow):
    allow.composite_joins.clear()
    assert check("SELECT f.account_id FROM flow f JOIN account a USING (account_id)", allow, inner_only=True) == []


def test_single_column_declared_join_passes():
    sem = Semantics.__new__(Semantics)
    sem.d = SimpleNamespace(RESTRICTED_COLUMNS=[], PERIODS={}, GROUP_BY={}, ACRONYMS={})
    schema = {"account": {"household_id"}, "household": {"household_id"}}
    sem.tables = {f"wealth.{t}": dict.fromkeys(cols, "VARCHAR") for t, cols in schema.items()}
    sem.scd2, sem.entities, sem.cites = {}, {}, {}
    sem.declared = {"account_household": Join("account_household", "key", "account.household_id = household.household_id",
                                            None, None, "wealth.account", "wealth.household", True)}
    allow = declared_allowlist(SimpleNamespace(semantics=sem), "wealth", Allowlist("wealth", set(schema), schema, schema=schema))
    assert not allow.composite_joins
    assert check("SELECT a.household_id FROM account a JOIN household h USING (household_id)", allow, inner_only=True) == []
