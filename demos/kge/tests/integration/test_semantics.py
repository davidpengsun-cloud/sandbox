"""A term that resolves to a context is answered from the graph's query-semantics layer (#39 item 7d).

    .venv/bin/python -m pytest tests/integration/test_semantics.py -q

kge/semantics/query.py assembles a context's query from what Meridian's pack 1.1.0
declares: the measure and its fact table; the declared joins, each read in the
question's time mode (current, or as reported at the month's freeze date); the
filters, and the row selectors as NOT EXISTS; the period through the calendar
rollup; the release rule; a dimension to group by and an entity's id. Each
figure here is put beside an oracle: the query written by hand, as the owner's
examples write it, on the warehouses themselves. One test per join kind
(calendar rollup, key, hierarchy, cross-reference), per filter and row
selector, per time mode and for the release rule.

Only the graph context takes this path, and only for a term a context answers:
every other term, and every other arm, plans as before (make parity holds the
183). The parser reads the phrases the context path needs from the pack's
Alias rows, and a date may abbreviate its month.

Opens the demo's graph and reads its warehouses: stop the API first.
"""
from __future__ import annotations

import datetime as dt
from types import SimpleNamespace

import duckdb
import pytest

from kge.services.ask.engine.core import PRESETS, Period, parse
from kge.semantics.query import Asked, Semantics, month_ends
from kge.eval import sets
from tests.scratch import BUILD

W = BUILD / "warehouses"
CURRENT = "f.CURR_IN = 1"
REPORTED = "f.DW_EFF_DT <= m.FIN_MTRC_RLSE_DT AND (f.DW_EXPR_DT IS NULL OR f.DW_EXPR_DT >= m.FIN_MTRC_RLSE_DT)"
FY26_Q1 = "m.CLDR_EOM_DT BETWEEN DATE '2025-04-30' AND DATE '2025-06-30'"
SIGNED = "CASE WHEN f.DW_TRAN_DIRCTN_CD = 'O' THEN -f.TRAN_AM ELSE f.TRAN_AM END"
DECODED = "CASE f.ACCT_SRCE_SYS_ID WHEN 100 THEN 'BROKERAGE' WHEN 200 THEN 'BANK' END"


def oracle(period: str, *, time: str = CURRENT, joins: str = "", where: str = "", eligible: bool = True, test_accounts: bool = False,
           exclusions: bool = False, select: str = f"round(sum({SIGNED}), 2)", group: str | None = None, attach: bool = False):
    """Net new assets as the owner's examples write them, on SAGE's tables and Meridian's under their physical names: eligible
    flows in released months, test accounts and accounts excluded on the day left out; with the flags, one rule at a time
    put back. Grouped, a {group: value} dict."""
    w = "wealth." if attach else ""
    sql = (f"SELECT {group + ', ' if group else ''}{select} FROM {w}TRAN_FACT_1 f JOIN {w}MONTH_DIM_1 m ON m.CLDR_EOM_DT = last_day(f.DW_TRAN_DT) "
           f"{joins} WHERE {time} AND m.DW_BRKRG_MNTH_FRM_TODAY_CT <> 0 AND {period}" + (" AND f.NNA_IN = 'Y'" if eligible else "")
           + ("" if test_accounts else f" AND NOT EXISTS (SELECT 1 FROM {w}ACCT_DIM2_1 t WHERE t.DW_ACCT_ID = f.DW_ACCT_ID AND "
                                       "t.SRCE_SYS_ID = f.ACCT_SRCE_SYS_ID AND t.TEST_ACCT_IN = 'Y')")
           + ("" if exclusions else f" AND NOT EXISTS (SELECT 1 FROM {w}NNA_EXCL_1 x WHERE x.DW_ACCT_ID = f.DW_ACCT_ID AND "
                                    f"x.SRCE_SYS_CD = {DECODED} AND f.DW_TRAN_DT BETWEEN x.EXCL_EFF_DT AND "
                                    "coalesce(x.EXCL_EXPR_DT, DATE '9999-12-31'))")
           + (f" {where}" if where else "") + (" GROUP BY 1 ORDER BY 1" if group else ""))
    if attach:
        con = duckdb.connect(":memory:")
        for u in ("retail", "wealth", "xref"):
            con.execute(f"ATTACH '{W / u}.duckdb' AS {u} (READ_ONLY)")
    else:
        con = duckdb.connect(str(W / "wealth.duckdb"), read_only=True)
    try:
        rows = con.execute(sql).fetchall()
    finally:
        con.close()
    return {str(k): float(v) for k, v in rows} if group else float(rows[0][0])


def ask(eng, q: str, who: str = "analyst_enterprise", arm: str = "graph") -> dict:
    return eng.answer(q, who, config=PRESETS[arm])


ACCOUNT = "JOIN ACCT_DIM2_1 a ON a.DW_ACCT_ID = f.DW_ACCT_ID AND a.SRCE_SYS_ID = f.ACCT_SRCE_SYS_ID AND a.CURR_IN = 1"
HOUSEHOLD = f"{ACCOUNT} JOIN HSHLD_DIM_1 h ON h.HSHLD_DK = a.HSHLD_DK"


def test_a_term_that_resolves_to_a_context_is_answered_from_it(eng):
    a = ask(eng, "NNA for June 2025")
    assert a["behaviour"] == "figure" and a["figure"] == oracle("m.CLDR_EOM_DT = DATE '2025-06-30'")
    assert (a["context"]["context"], a["context"]["term"], a["context"]["fact"]) == ("nna", "Net New Assets", "wealth.TRAN_FACT_1")
    assert a["context"]["filters"] == ["eligible", "released"] and a["context"]["row_selectors"] == ["not_test_account", "not_excluded"]
    assert a["unit"] == "wealth" and a["column"] == "flow.amount_usd" and a["guard"]["passed"]
    assert [t["step"] for t in a["trace"]][:2] == ["parse", "context"]


def test_a_variant_resolves_to_its_own_context(eng):
    """Client-Directed NNA resolves to nna_client_directed: the eligible and released filters and the client-directed one."""
    a = ask(eng, "Client-directed NNA for June 2025")
    assert a["context"]["context"] == "nna_client_directed" and a["context"]["filters"] == ["eligible", "released", "client_directed"]
    directed = "AND EXISTS (SELECT 1 FROM F_NNA_FIN n WHERE n.DW_TRAN_ID = f.DW_TRAN_ID AND n.CLIENT_DIRECTED_NNA_IN = 'Y')"
    assert a["figure"] == oracle("m.CLDR_EOM_DT = DATE '2025-06-30'", where=directed)
    assert a["figure"] < ask(eng, "NNA for June 2025")["figure"]


def test_the_control_and_the_other_presets_keep_today_s_path(eng):
    """No context on the schema or catalog arm, nor for a term no context answers."""
    for arm in ("schema", "catalog"):
        assert "context" not in ask(eng, "NNA for June 2025", arm=arm)
    assert ask(eng, "NNA for June 2025", arm="catalog")["behaviour"] == "abstain"
    r = ask(eng, "Retail revenue for June 2025")
    assert r["behaviour"] == "figure" and "context" not in r and not any(t["step"] == "context" for t in r["trace"])


def test_join_kind_calendar_rollup(eng):
    """flow_month_end, required: each flow joins its month's end, which carries its release and freeze."""
    a = ask(eng, "NNA for FY26-Q1")
    assert a["join_path"] == ["flow_month_end"] and "LAST_DAY(TRAN_FACT_1.DW_TRAN_DT) = MONTH_DIM_1.CLDR_EOM_DT" in a["sql"]
    assert a["figure"] == oracle(FY26_Q1) and a["months"] == ["2025-04-30", "2025-05-31", "2025-06-30"]


def test_join_kind_key_with_a_composite_key_and_an_entity_id(eng):
    """flow_account joins on the account number and its source system (one alone counts twice), account_household on its
    key; household 5001 is an id the question names after the entity's phrase."""
    a = ask(eng, "NNA for household 5001 in FY26-Q1")
    assert a["join_path"] == ["flow_month_end", "flow_account", "account_household"]
    assert ("TRAN_FACT_1.DW_ACCT_ID = ACCT_DIM2_1.DW_ACCT_ID AND TRAN_FACT_1.ACCT_SRCE_SYS_ID = ACCT_DIM2_1.SRCE_SYS_ID AND "
            "ACCT_DIM2_1.CURR_IN <> 0") in a["sql"]
    assert a["figure"] == oracle(FY26_Q1, joins=HOUSEHOLD, where="AND h.HSHLD_DK = 5001") and a["figure"]
    assert a["context"]["entity"] == ["wealth_household", "5001"]


@pytest.mark.parametrize("phrase, table, column", [("by division", "d", "DIVSN_CD"), ("by region", "r", "RGN_CD")])
def test_join_kind_hierarchy(eng, phrase, table, column):
    """advisor_office, office_region and region_division roll offices up to regions and divisions, each read at its current
    version: the closed office row before the window would count its flows twice."""
    a = ask(eng, f"NNA in FY26-Q1 {phrase}")
    joins = (f"{HOUSEHOLD} JOIN ADV_DIM_1 v ON v.ADV_ID = h.PRIM_ADV_ID JOIN BRCH_DIM_1 o ON o.BRCH_CD = v.BRCH_CD AND o.CURR_IN = 1 "
             "JOIN RGN_DIM_1 r ON r.RGN_CD = o.RGN_CD AND r.CURR_IN JOIN DIVSN_DIM_1 d ON d.DIVSN_CD = r.DIVSN_CD AND d.CURR_IN")
    assert a["behaviour"] == "figure" and a["figure"] is None and a["group_by"] == f"{dict(d='DIVSN_DIM_1', r='RGN_DIM_1')[table]}.{column}"
    assert a["rows"] == oracle(FY26_Q1, joins=joins, group=f"{table}.{column}") and len(a["rows"]) > 1
    assert {"advisor_office", "office_region"} <= set(a["join_path"]) and "BRCH_DIM_1.CURR_IN <> 0" in a["sql"]


def test_join_kind_cross_reference(eng):
    """client_xref: Retail customers to Wealth households, through the certified matches current now, as a semi-join, so
    a household with two matched members counts once; the two units read in one session that attaches them. A client who
    banks with Retail holds an open Retail account: the context's row selector on Retail's side (banks_with_retail), which
    applies only across the certified join, to its far side. XB3's figure, as the owner's SQL counts it."""
    a = ask(eng, "NNA in FY26-Q1 from clients who also bank with Retail")
    assert a["behaviour"] == "figure" and a["warehouses_involved"] == ["retail", "wealth"] and "client_xref" in a["join_path"]
    linked = ("AND a.HSHLD_DK IN (SELECT x.household_id FROM xref.client_xref x JOIN retail.customer c ON c.customer_id = "
              "x.retail_customer_id WHERE x.certification_status = 'CERTIFIED' AND x.is_current{})")
    banks = " AND EXISTS (SELECT 1 FROM retail.account ra WHERE ra.customer_id = c.customer_id AND ra.status_code = 'OPEN')"
    joins = ACCOUNT.replace("ACCT_DIM2_1 a", "wealth.ACCT_DIM2_1 a")
    assert a["figure"] == oracle(FY26_Q1, joins=joins, where=linked.format(banks), attach=True) == GW["XB3"]["expect"]["figure"]
    assert 0 < a["figure"] < oracle(FY26_Q1, joins=joins, where=linked.format(""), attach=True) < ask(eng, "NNA for FY26-Q1")["figure"]
    assert "FROM wealth.TRAN_FACT_1 AS TRAN_FACT_1" in a["sql"] and "EXISTS (SELECT 1 FROM xref.client_xref AS client_xref" in a["sql"]
    assert "client_xref.certification_status = 'CERTIFIED'" in a["sql"] and "client_xref.is_current" in a["sql"]
    assert "EXISTS (SELECT 1 FROM retail.account AS account WHERE" in a["sql"] and "account.status_code = 'OPEN'" in a["sql"]
    assert a["context"]["row_selectors"] == ["not_test_account", "not_excluded", "banks_with_retail"]
    assert any("banks_with_retail" in n for n in a["notes"])


def test_a_row_selector_on_another_unit_applies_only_across(eng):
    """banks_with_retail is on Retail's table: a question within Wealth reads no Retail table, and its query is as before."""
    a = ask(eng, "NNA for FY26-Q1")
    assert a["context"]["row_selectors"] == ["not_test_account", "not_excluded"] and "retail" not in a["sql"] and "OPEN" not in a["sql"]


def test_filter_eligible(eng):
    """A flow the eligibility rules leave out counts towards no figure, whatever the question says; so does an internal
    transfer from the same client's Retail account, whose flow type is not new money: the context's filter, not the
    question's text, leaves it out."""
    a = ask(eng, "NNA for June 2025")
    assert "TRAN_FACT_1.NNA_IN = 'Y'" in a["sql"] and a["figure"] != oracle("m.CLDR_EOM_DT = DATE '2025-06-30'", eligible=False)
    con = duckdb.connect(str(W / "wealth.duckdb"), read_only=True)
    try:
        internal = con.execute("SELECT count(*) FROM TRAN_FACT_1 WHERE DW_TRAN_TYPE_CD = 'INT_TRF' AND NNA_IN = 'N'").fetchone()[0]
    finally:
        con.close()
    assert internal > 0


def test_filter_released(eng):
    a = ask(eng, "NNA for June 2025")
    assert "MONTH_DIM_1.DW_BRKRG_MNTH_FRM_TODAY_CT <> 0" in a["sql"]


def test_row_selector_not_test_account(eng):
    """A test account's round deposits are left out (NOT EXISTS over the account), unasked."""
    a = ask(eng, "NNA for June 2025")
    period = "m.CLDR_EOM_DT = DATE '2025-06-30'"
    assert "NOT EXISTS (SELECT 1 FROM ACCT_DIM2_1 WHERE" in a["sql"] and "ACCT_DIM2_1.TEST_ACCT_IN = 'Y'" in a["sql"]
    assert a["figure"] == oracle(period) != oracle(period, test_accounts=True)


def test_row_selector_not_excluded(eng):
    """An account excluded on the flow's date is left out, effective-dated, and wins over the eligible flag it conflicts
    with: FY25-Q4 holds flows of accounts excluded then."""
    a = ask(eng, "NNA for FY25-Q4")
    period = "m.CLDR_EOM_DT BETWEEN DATE '2025-01-31' AND DATE '2025-03-31'"
    assert "NOT EXISTS (SELECT 1 FROM NNA_EXCL_1 WHERE" in a["sql"]
    assert a["figure"] == oracle(period) != oracle(period, exclusions=True)


def test_time_mode_as_reported_reads_each_version_valid_on_the_freeze_date(eng):
    a = ask(eng, "NNA for March 2025 as reported")
    assert a["context"]["time_mode"] == "as_reported" and "MONTH_DIM_1.FIN_MTRC_RLSE_DT" in a["sql"]
    assert a["figure"] == oracle("m.CLDR_EOM_DT = DATE '2025-03-31'", time=REPORTED)
    assert any("as reported" in n for n in a["notes"])


def test_time_mode_restated_is_the_current_version(eng):
    """March 2025 was restated after its freeze, so the month as reported and as restated differ."""
    a, b = ask(eng, "NNA for March 2025 restated"), ask(eng, "NNA for March 2025 as reported")
    assert a["context"]["time_mode"] == "current" and "TRAN_FACT_1.CURR_IN <> 0" in a["sql"]
    assert a["figure"] == oracle("m.CLDR_EOM_DT = DATE '2025-03-31'") != b["figure"]


def test_release_rule_a_month_not_yet_released_is_abstain_with_reason(eng):
    con = duckdb.connect(str(W / "wealth.duckdb"), read_only=True)
    try:
        released, on = con.execute("SELECT DW_BRKRG_MNTH_FRM_TODAY_CT <> 0, FIN_MTRC_RLSE_DT FROM MONTH_DIM_1 "
                                   "WHERE CLDR_EOM_DT = DATE '2025-07-31'").fetchone()
    finally:
        con.close()
    assert not released
    a = ask(eng, "NNA for July 2025")
    assert a["behaviour"] == "abstain_with_reason" and "figure" not in a
    assert a["reason"].startswith(f"July 2025 is not yet released: its figures are released on {on.isoformat()}")
    assert len(a["sql_run"]) == 1 and "DW_BRKRG_MNTH_FRM_TODAY_CT" in a["sql_run"][0], "only the release driver was read"


def test_release_rule_the_latest_month_is_the_latest_released(eng):
    a = ask(eng, "NNA for the latest month")
    assert a["behaviour"] == "figure" and a["months"] == ["2025-06-30"]
    assert a["figure"] == oracle("m.CLDR_EOM_DT = DATE '2025-06-30'")
    assert any("June 2025, the latest released" in n for n in a["notes"])


def test_release_rule_a_period_with_months_not_booked_or_released_abstains(eng):
    a = ask(eng, "NNA for FY26-Q2")
    assert a["behaviour"] == "abstain_with_reason"
    assert "July 2025 is not yet released" in a["reason"] and "August 2025, September 2025 are not booked" in a["reason"]


def test_group_by_an_acronym_the_column_spells(eng):
    """SRC_SYS_CD spells the source system: "by SRC_SYS_CD" groups by the fact's own column, SAGE's id (100 and 200);
    NNA_ELIG_FLG is the eligible flag the context applies anyway."""
    a = ask(eng, "Sum of NNA_ELIG_FLG flows by SRC_SYS_CD in FY26-Q1")
    assert a["group_by"] == "TRAN_FACT_1.ACCT_SRCE_SYS_ID" and a["rows"] == oracle(FY26_Q1, group="f.ACCT_SRCE_SYS_ID")
    assert set(a["rows"]) == {"100", "200"}


def test_group_by_a_parent_segment(eng):
    a = ask(eng, "NNA in FY26-Q1 by parent segment")
    joins = f"{ACCOUNT} JOIN CLIENT_CD_ROLLUP_1 cr ON cr.CLNT_CD = a.EFF_CLNT_CD JOIN CLNT_SEGMT_DIM_1 s ON s.SEGMT_CD = cr.SEGMT_LVL_ID6"
    assert a["rows"] == oracle(FY26_Q1, joins=joins, group="s.PARNT_SEGMT_CD")


def test_a_count_of_an_entity_counts_its_keys(eng):
    a = ask(eng, "How many households had NNA in FY26-Q1?")
    assert a["figure"] == oracle(FY26_Q1, joins=HOUSEHOLD, select="count(DISTINCT h.HSHLD_DK)") and a["column"] == "household.household_id"


GW = {i["id"]: i for i in sets.items(BUILD / "golden_wealth_v1.json")}


def test_a_count_of_an_entity_with_a_two_column_key_counts_the_whole_key(eng):
    """W05: an account is its number and its source system (BANK and BROKERAGE share numbers), so a count of accounts
    counts the pair, as the flow carries it; never one column of it, and never count(*) of the flows."""
    a = ask(eng, "How many accounts had NNA in FY26-Q1?")
    assert a["behaviour"] == "figure" and "count(*)" not in a["sql"]
    assert ("count(DISTINCT (TRAN_FACT_1.DW_ACCT_ID, TRAN_FACT_1.ACCT_SRCE_SYS_ID))" in a["sql"]
            and a["column"] == "account.account_id, account.source_system")
    assert a["figure"] == oracle(FY26_Q1, select="count(DISTINCT (f.DW_ACCT_ID, f.ACCT_SRCE_SYS_ID))") == GW["W05"]["expect"]["figure"]
    assert a["figure"] not in (oracle(FY26_Q1, select="count(DISTINCT f.DW_ACCT_ID)"), oracle(FY26_Q1, select="count(*)"))


def test_a_count_that_names_no_entity_is_no_figure(eng):
    """A count of nothing the pack declares an entity is not a count of the fact's rows passed off as one: no figure."""
    plan = eng.semantics.plan(eng.semantics.contexts["nna"], Asked(agg="count", period=Period("fiscal_quarter", "FY26-Q1")))
    assert plan.decision["behaviour"] == "abstain" and not plan.select


def test_a_source_system_named_by_its_code_filters_on_sage_s_id_and_the_systems_add_up(eng):
    """W06, since pack 1.5.0: BROKERAGE was Meridian's code on its dropped flow table; SAGE's flows hold the source
    system's id, so the pack reads the code as a condition on it (a predicate, BROKERAGE as 100, BANK as 200 only in
    "from BANK" or "on BANK"): the figure is BROKERAGE's alone. Grouped by SAGE's id, the two systems add up to the
    whole, BROKERAGE's (100) the figure W06 expects, and the word bank alone is no condition."""
    a = ask(eng, "NNA from BROKERAGE in FY26-Q1")
    assert a["behaviour"] == "figure" and "TRAN_FACT_1.ACCT_SRCE_SYS_ID = 100" in a["sql"]
    rows = ask(eng, "NNA in FY26-Q1 by source system")["rows"]
    assert a["figure"] == rows["100"] == oracle(FY26_Q1, where="AND f.ACCT_SRCE_SYS_ID = 100") == GW["W06"]["expect"]["figure"]
    assert ask(eng, "NNA from BANK in FY26-Q1")["figure"] == rows["200"]
    assert round(sum(rows.values()), 2) == ask(eng, "NNA for FY26-Q1")["figure"]
    assert parse("Total retail revenue across the bank for June 2025", eng.term_names, eng).filters == []


def test_a_value_of_a_dimension_filters_through_the_declared_joins(eng):
    """A household's risk profile (GROWTH, a value vocabulary of the pack's) is reached from the flow through the declared
    joins, the account at its current version: the figure is the GROWTH households' alone, not every flow's."""
    a = ask(eng, "NNA in FY26-Q1 for households with a GROWTH risk profile")
    assert a["behaviour"] == "figure" and "HSHLD_DIM_1.RISK_PRFL_CD = 'GROWTH'" in a["sql"] and a["guard"]["passed"]
    assert {"flow_account", "account_household"} <= set(a["join_path"])
    assert a["figure"] == oracle(FY26_Q1, joins=HOUSEHOLD, where="AND h.RISK_PRFL_CD = 'GROWTH'")
    assert 0 < a["figure"] < ask(eng, "NNA for FY26-Q1")["figure"]


def test_a_value_on_no_table_the_context_reaches_is_no_figure(eng, monkeypatch):
    """A product (a Retail column the pack gives values for) is on no table the context's declared joins reach: no figure
    and no query, rather than the figure of every flow."""
    ran = []
    monkeypatch.setattr(eng, "run_plan", lambda plan, sql: ran.append(sql))
    a = ask(eng, "NNA for June 2025 on Premier Current")
    assert a["behaviour"] == "abstain" and "figure" not in a and "product_name" in a["reason"] and ran == []


@pytest.mark.parametrize("q, named", [("NNA for June 2025 in region NORTHEAST", "NORTHEAST"), ("NNA for June 2025 for office NYC", "NYC"),
                                      ("NNA for June 2025 for HNW clients", "HNW"),
                                      ("NNA for June 2025 in region NE", "retail column region_code")])
def test_a_qualifier_the_context_cannot_apply_is_no_figure(eng, monkeypatch, q, named):
    """Round 2's UAT: a region, an office or a client segment named by its code gave every region's figure. A code the
    question writes in capitals that nothing it read accounts for (no term, alias, acronym or value of the pack's) is a
    condition the context does not apply, and a value of a vocabulary is applied only in its own column's unit: either
    way, no figure and no query, rather than the figure of another question."""
    ran = []
    monkeypatch.setattr(eng, "run_plan", lambda plan, sql: ran.append(sql))
    a = ask(eng, q)
    assert a["behaviour"] == "abstain" and "figure" not in a and named in a["reason"] and ran == [], (q, a.get("reason"))
    assert "no figure" in a["reason"] or "none is given" in a["reason"]


@pytest.mark.parametrize("q", ["NNA for FY26 Q1", "NNA for Q1 FY26", "NNA for the first quarter of FY26",
                               "NNA for June", "What was NNA in March?", "NNA for Jun-25"])
def test_a_period_the_parser_cannot_place_is_no_figure(eng, monkeypatch, q):
    """Round 2's UAT: a period the parser does not read gave every month's figure, noted "no period asked". A question
    that says a quarter, a fiscal year or a month the parser cannot place gets no figure and no query, and the
    reason names the forms it reads; a question that names no period at all still reads every month the filters keep, and
    says so. A month without its year, full or abbreviated, was added in round 2's final verify."""
    ran = []
    monkeypatch.setattr(eng, "run_plan", lambda plan, sql: ran.append(sql))
    a = ask(eng, q)
    assert a["behaviour"] == "abstain_with_reason" and a.get("figure") is None and ran == [], (q, a.get("reason"))
    assert all(form in a["reason"] for form in ("June 2025", "31 Mar 2025", "Q2 2025", "FY26-Q1", "in 2025")), a["reason"]
    monkeypatch.undo()
    a = ask(eng, "NNA by source system")
    assert a["behaviour"] == "figure" and a["notes"][0] == "no period asked: every month the filters keep"


@pytest.mark.parametrize("q, term", [("How many accounts were excluded from NNA at 31 Mar 2025?", "Exclusion"),
                                     ("NNA for June 2025 for test accounts", "Test Account"),
                                     ("NNA for FY26-Q1 in the EAST division", "Division")])
def test_a_term_the_question_names_and_the_context_does_not_apply_is_no_figure(eng, monkeypatch, q, term):
    """W08 asks for the excluded accounts, and the second for the test accounts' flows: the very rows net new assets leaves
    out, so its figure would answer another question (the accounts not excluded, the flows not on a test account). The
    third names a division it does not group by. A certified term the question names is applied, or there is no figure:
    no query runs. A term it does apply (a dimension it groups by, a value's table, the variant's own) is no reason."""
    ran = []
    monkeypatch.setattr(eng, "run_plan", lambda plan, sql: ran.append(sql))
    a = ask(eng, q)
    assert a["behaviour"] == "abstain" and "figure" not in a and term in a["reason"] and ran == [], (q, a.get("reason"))


def test_an_abstention_is_given_only_to_a_principal_who_may_read_what_the_question_reads(eng):
    """Access first: a principal who reads one of the two units is refused, as on XB3, before the context says it applies
    no such condition; a refusal the graph decides (a no-join fact) stands for anyone."""
    a = ask(eng, "NNA in FY26-Q1 from clients who also bank with Retail, for test accounts", who="advisor_wealth")
    assert a["behaviour"] == "blocked_access" and a.get("figure") is None
    assert ask(eng, "NNA for June 2025 for test accounts", who="analyst_retail")["behaviour"] == "refuse"
    assert ask(eng, "NNA for SME clients who are also wealth households", who="analyst_retail")["joins_refused"] == ["sme-wealth"]


def test_a_unit_a_no_join_fact_keeps_apart_is_refused(eng):
    a = ask(eng, "NNA for SME clients who are also wealth households")
    assert a["behaviour"] == "refuse" and a["joins_refused"] == ["sme-wealth"] and "Separate customer populations" in a["reason"]


def test_without_a_release_driver_as_reported_and_the_latest_month_abstain(eng, monkeypatch):
    sem = Semantics(eng.g, eng)
    monkeypatch.setattr(sem, "releases", {})
    ctx = sem.contexts["nna"]
    for asked in (Asked(time_mode="as_reported", period=Period("month", "2025-03-01")), Asked(latest=True)):
        assert sem.plan(ctx, asked).decision["behaviour"] == "abstain"


def test_the_context_s_allowlist_admits_its_own_query_and_no_restricted_column(eng):
    a = ask(eng, "NNA in FY26-Q1 from clients who also bank with Retail")
    assert a["guard"]["passed"] and a["guard"]["failed_at"] is None
    sem = eng.semantics
    plan = sem.plan(sem.contexts["nna"], Asked(units=["retail"], period=Period("fiscal_quarter", "FY26-Q1")))
    assert plan.allow.attached and "xref.client_xref" in plan.allow.tables
    assert not {c for t, cs in plan.allow.columns.items() for c in cs if (t, c) in plan.allow.restricted}
    assert ("retail.customer", "date_of_birth") in plan.allow.restricted and "date_of_birth" not in plan.allow.columns["retail.customer"]


def gated(eng, plan, sql: str, figure: bool = True) -> dict:
    from kge.pipeline.sqlguard import gate
    with eng.session(plan) as con:
        return gate(con, sql, plan.allow, figure)


@pytest.mark.parametrize("mode", ["current", "as_reported"])
@pytest.mark.parametrize("join", [
    "JOIN DIVSN_DIM_1 ON DIVSN_DIM_1.DW_EFF_DT <= MONTH_DIM_1.FIN_MTRC_RLSE_DT",
    "JOIN DIVSN_DIM_1 ON DIVSN_DIM_1.DW_EXPR_DT >= MONTH_DIM_1.FIN_MTRC_RLSE_DT",
    "JOIN DIVSN_DIM_1 ON DIVSN_DIM_1.DW_EFF_DT <= MONTH_DIM_1.FIN_MTRC_RLSE_DT AND (DIVSN_DIM_1.DW_EXPR_DT IS NULL OR "
    "DIVSN_DIM_1.DW_EXPR_DT >= MONTH_DIM_1.FIN_MTRC_RLSE_DT)",
    "JOIN ACCT_DIM2_1 ON ACCT_DIM2_1.DW_EFF_DT <= MONTH_DIM_1.FIN_MTRC_RLSE_DT",
    "JOIN NNA_EXCL_1 ON TRAN_FACT_1.DW_TRAN_DT BETWEEN NNA_EXCL_1.EXCL_EFF_DT AND coalesce(NNA_EXCL_1.EXCL_EXPR_DT, DATE '9999-12-31')",
])
def test_a_piece_of_a_declared_condition_alone_joins_no_table(eng, mode, join):
    """A version's window is a condition on a table a declared join's keys reach already, and a row selector's date range
    one on the rows its keys match: neither, on its own, joins two tables. A table so joined is refused, as one no
    declared join reaches; the context's own query, in either time mode, still passes. Joined so, every flow would be
    counted once per division version, or once per exclusion that covers its date."""
    sem = eng.semantics
    plan = sem.plan(sem.contexts["nna"], Asked(period=Period("month", "2025-06-01"), time_mode=mode))
    sql = plan.query([dt.date(2025, 6, 30)])
    assert gated(eng, plan, sql)["passed"]
    bad = sql.replace(" WHERE ", f" {join} WHERE ", 1)
    g = gated(eng, plan, bad)
    table = join.split()[1]
    assert not g["passed"] and any(f"table {table} is not joined to the others by a declared join" in p for p in g["problems"]), g


def test_the_calendar_rollup_still_joins_on_its_one_condition(eng):
    """flow_month_end's condition is one predicate that is not one column equal to another, and the whole of the
    declared join: it joins the month-end to the flow, as before."""
    sem = eng.semantics
    plan = sem.plan(sem.contexts["nna"], Asked(period=Period("month", "2025-06-01")))
    sql = plan.query([dt.date(2025, 6, 30)])
    assert "JOIN MONTH_DIM_1 ON LAST_DAY(TRAN_FACT_1.DW_TRAN_DT) = MONTH_DIM_1.CLDR_EOM_DT" in sql and gated(eng, plan, sql)["passed"]


@pytest.mark.parametrize("q, kind, value", [
    ("NNA at 31 Mar 2025", "as_of", "2025-03-31"),
    ("Deposits and AUM, 31 Mar 2025", "as_of", "2025-03-31"),
    ("NNA for Mar 2025", "month", "2025-03-01"),
    ("NNA for Sept 2024", "month", "2024-09-01"),
    ("Total revenue across the bank for the quarter to 30 June 2025", "calendar_quarter", "2025-Q2"),
    ("Retail revenue for June 2025", "month", "2025-06-01"),
])
def test_a_date_may_abbreviate_its_month(eng, q, kind, value):
    p = parse(q, eng.term_names, eng).period
    assert (p.kind, p.value) == (kind, value)


def test_a_full_month_s_name_keeps_its_old_reading():
    """A day before a full month's name is read as a date only after at, as at or to, as before."""
    empty = SimpleNamespace(ALIASES={}, UNIT_WORDS={})
    assert parse("Revenue, 30 June 2025", [], empty).period.kind == "month"


def test_the_context_s_phrases_are_the_pack_s_alias_rows(eng):
    assert eng.TIME_MODES == {"as reported": "as_reported", "restated": "restated"} and eng.NAMED_PERIODS == {"latest month": "latest"}
    assert eng.GROUP_BY["by division"] == "wealth.DIVSN_DIM_1.DIVSN_CD" and eng.ENTITY_WORDS == {"household": "wealth_household",
                                                                                                    "customer": "retail_customer",
                                                                                                    "account": "wealth_account"}
    assert "wealth.TRAN_FACT_1.ACCT_SRCE_SYS_ID" in eng.ACRONYMS["SRC_SYS_CD"] and eng.CURATED == ["xref"]
    r = parse("NNA for March 2025 as reported by division for household 5001", eng.term_names, eng)
    assert (r.time_mode, r.group_by, r.entity) == ("as_reported", ["wealth.DIVSN_DIM_1.DIVSN_CD"], ("wealth_household", "5001"))
    assert parse("NNA for the latest month", eng.term_names, eng).latest
    off = parse("NNA for March 2025 as reported by division for household 5001", eng.term_names, eng, aliases=False)
    assert (off.time_mode, off.group_by, off.entity, off.latest) == ("current", [], None, False), "no term resolution: none of them"
    plain = parse("NNA for March 2025 as reported by division", [], SimpleNamespace(ALIASES={}, UNIT_WORDS={}))
    assert (plain.time_mode, plain.group_by) == ("current", []), "a domain with no such phrases reads none"


def test_month_ends():
    assert month_ends(dt.date(2025, 4, 1), dt.date(2025, 6, 30)) == [dt.date(2025, 4, 30), dt.date(2025, 5, 31), dt.date(2025, 6, 30)]
    assert month_ends(dt.date(2024, 12, 15), dt.date(2024, 12, 15)) == [dt.date(2024, 12, 31)]
