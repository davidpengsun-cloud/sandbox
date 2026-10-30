"""SAGE keeps its physical names, and every Wealth name is a business name too (#176, pack 1.5.0).

Every table and column sage.json names is in the generated Wealth warehouse, the pack's lock and the compiled graph's
harvested layer under exactly the name sage.json gives it, case kept; Wealth's fifteen Meridian tables are there under
the physical names kge/estate/names.py gives them, and no other Wealth table is: none is named account, flow, month_end,
office, ref_source_system or any other business name. Each business name resolves in the graph's curated layer: each
Wealth table represents an entity, and each column's business name is an alias of the physical column, as names.py
says, and an answer names its measure column by its business name while its SQL reads the physical one; a source
system named by Meridian's code (BROKERAGE) is a condition on SAGE's id for it. Runs on the
build (make graph pipeline)."""
from __future__ import annotations

import json

from kge.estate import names, sage
from kge.services.ask.engine.core import PRESETS
from tests.scratch import BUILD

LOCK = BUILD.parent / "clients" / "meridian" / "pack.lock"


def physical() -> dict[str, list[str]]:
    """Every Wealth table's physical name and its columns, in order: SAGE's from sage.json, the rest from names.py."""
    out = {sage.TABLES[t]: [f["name"] for f in sage.fields(t)] for t in sage.TABLES}
    out.update({p: list(cols.values()) for p, cols in names.MERIDIAN.values()})
    return out


def test_the_warehouse_holds_every_wealth_table_and_column_under_its_physical_name():
    from kge.estate.golden import warehouses
    con = warehouses(BUILD).open("wealth")
    try:
        held = {t: [c["name"] for c in con.describe(t)["columns"]] for t in con.tables()}
    finally:
        con.close()
    assert held == physical()
    business = set(names.tables()) | {b for b, _ in names.columns()}
    assert not business & set(held)


def test_the_lock_holds_them_as_the_warehouse_names_them():
    lock = json.loads(LOCK.read_text())
    tables = {t["name"]: [c["name"] for c in sorted(t["columns"], key=lambda c: c["ordinal"])] for t in lock["sources"]["wealth"]["tables"]}
    assert tables == physical()


def test_the_harvested_layer_holds_them_and_each_business_name_resolves(eng):
    q = eng.g.q
    tables = {r["t"]: set() for r in q("MATCH (t:Tbl {unit: 'wealth'}) RETURN t.name AS t")}
    for r in q("MATCH (t:Tbl {unit: 'wealth'})-[:HAS_COLUMN]->(c:Col) RETURN t.name AS t, c.name AS c"):
        tables[r["t"]].add(r["c"])
    assert tables == {t: set(cols) for t, cols in physical().items()}
    represented = {r["t"] for r in q("MATCH (t:Tbl {unit: 'wealth'})-[:REPRESENTS]->(e:Entity) RETURN t.name AS t")}
    assert represented == set(physical())
    aliases = {r["phrase"]: r["col"] for r in q("MATCH (a:Alias)-[:ALIAS_OF]->(c:Col) WHERE a.kind = 'column' RETURN a.phrase AS phrase, "
                                               "c.id AS col")}
    assert aliases == {f"{bt}.{bc}": f"wealth.{pt}.{pc}" for (bt, bc), (pt, pc) in names.columns().items()}


def test_a_business_worded_question_runs_on_physical_names_and_answers_in_business_names(eng):
    a = eng.answer("NNA for June 2025", "analyst_enterprise", config=PRESETS["graph"])
    assert a["behaviour"] == "figure" and a["column"] == "flow.amount_usd"
    assert "TRAN_FACT_1.TRAN_AM" in a["sql"] and "amount_usd" not in a["sql"]
    r = eng.answer("Wealth revenue FY26-Q1 for households with a GROWTH risk profile", "analyst_enterprise", config=PRESETS["graph"])
    assert r["column"] == "quarterly_fee_revenue.revenue_usd"
    assert r["join_path"] == ["quarterly_fee_revenue.household_id -> household.household_id"]
    assert "QTR_FEE_REV_FACT_1" in r["sql"] and "HSHLD_DIM_1.RISK_PRFL_CD" in r["sql"]
    b = eng.answer("NNA from BROKERAGE in FY26-Q1", "analyst_enterprise", config=PRESETS["graph"])
    assert b["behaviour"] == "figure" and "TRAN_FACT_1.ACCT_SRCE_SYS_ID = 100" in b["sql"]
    bank = eng.answer("NNA in FY26-Q1 from clients who also bank with Retail", "analyst_enterprise", config=PRESETS["graph"])
    assert "ACCT_SRCE_SYS_ID = 200" not in bank.get("sql", "")
