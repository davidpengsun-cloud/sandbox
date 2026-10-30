"""The golden set: questions with answers agreed before the agent sees them.

Four kinds, scored separately, as the control scenario requires:
  within      one warehouse; figure, join path and abstention are all scored
  cross       several warehouses; the agent must refuse every join that does not
              exist, state commensurability, and return parts where it is false
  shelf       the right answer depends on preferring the certified shelf
  abstain     the right behaviour is to stop and route to a steward

Numeric expectations are computed from the generated warehouses, so the set is
right by construction for the estate it ships with.

The set is versioned. build_golden builds v1, the set the #38 baseline ran.
Golden v2 (issue #39) is v1 with one change the owner stated: under Option B,
Retail and Wealth join through a certified client cross-reference, so
retail-wealth is no longer a refused join, and G021 and G030 drop it. Nothing
else changes, and the v2 file declares the change (changes_from, kge/eval/sets.py),
naming v1's file by its sha256, so a run on v2 is still put beside a baseline
on v1: its other items question by question.
"""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

from kge.adapters.warehouse import open_estate
from kge.estate.spec import UNITS
from kge.ports import EstateWarehouses

V2_CHANGES = {"version": "v1", "items": ["G021", "G030"],
              "why": "Option B, issue #39: Retail and Wealth join through the certified client cross-reference, so "
                     "retail-wealth is no longer a refused join"}


def q(con, sql: str):
    return con.execute(sql).fetchone()[0]


def warehouses(out: Path, client: str | None = None) -> EstateWarehouses:
    """The estate's warehouses in out, through the estate's warehouse port (the factory's): out/warehouses/ on DuckDB,
    the client's dataset on BigQuery (client: the profile's unless given)."""
    if client is None:
        from kge.pipeline.profile import ProfileError, resolve
        try:
            client = resolve().client
        except ProfileError:
            client = ""
    return open_estate(Path(out) / "warehouses", client)


def build_golden(out: Path, estate: EstateWarehouses | None = None) -> list[dict]:
    estate = estate or warehouses(out)
    w = {u: estate.open(u) for u in UNITS}
    g: list[dict] = []

    def add(kind, question, expect, **extra):
        g.append({"id": f"G{len(g) + 1:03d}", "kind": kind, "question": question, "expect": expect, **extra})

    r, s, c, wl = w["retail"], w["sme"], w["corporate"], w["wealth"]
    add("within", "Total retail revenue for June 2025",
        {"figure": q(r, "SELECT round(sum(total_revenue_usd),2) FROM monthly_revenue WHERE month_start='2025-06-01'"),
         "unit": "retail", "columns": ["monthly_revenue.total_revenue_usd"], "join_path": []})
    add("within", "Retail net interest income for calendar Q1 2025",
        {"figure": q(r, "SELECT round(sum(interest_income),2) FROM monthly_revenue m JOIN calendar_month c USING(month_start) WHERE c.calendar_quarter='2025-Q1'"),
         "unit": "retail", "columns": ["monthly_revenue.interest_income"], "join_path": ["monthly_revenue.month_start -> calendar_month.month_start"]})
    add("within", "How many retail customers were active in June 2025?",
        {"figure": q(r, "SELECT count(*) FROM customer_activity WHERE month_start='2025-06-01' AND is_active_flag"),
         "unit": "retail", "columns": ["customer_activity.is_active_flag"], "join_path": [],
         "note": "Active Customer v2: transactions, not logins"})
    add("within", "Retail revenue for June 2025 from customers whose home branch is in region NW",
        {"figure": q(r, "SELECT round(sum(mr.total_revenue_usd),2) FROM monthly_revenue mr JOIN account a USING(account_id) JOIN customer cu USING(customer_id) JOIN branch b USING(branch_id) WHERE mr.month_start='2025-06-01' AND b.region_code='NW'"),
         "unit": "retail", "columns": ["monthly_revenue.total_revenue_usd", "branch.region_code"],
         "join_path": ["monthly_revenue.account_id -> account.account_id", "account.customer_id -> customer.customer_id", "customer.branch_id -> branch.branch_id"]})
    add("within", "Average ledger balance across retail accounts in June 2025",
        {"figure": q(r, "SELECT round(avg(ledger_balance),2) FROM daily_balance WHERE as_of BETWEEN '2025-06-01' AND '2025-06-30'"),
         "unit": "retail", "columns": ["daily_balance.ledger_balance"], "join_path": []})
    add("within", "Number of open retail complaints as at 30 June 2025",
        {"figure": q(r, "SELECT count(*) FROM complaint WHERE resolved_on IS NULL"),
         "unit": "retail", "columns": ["complaint.complaint_id", "complaint.resolved_on"], "join_path": []})
    add("within", "Retail fee income by product family for June 2025, Everyday Current only",
        {"figure": q(r, "SELECT round(sum(mr.fee_income),2) FROM monthly_revenue mr JOIN account a USING(account_id) JOIN product p USING(product_id) WHERE mr.month_start='2025-06-01' AND p.product_name='Everyday Current'"),
         "unit": "retail", "columns": ["monthly_revenue.fee_income", "product.product_name"],
         "join_path": ["monthly_revenue.account_id -> account.account_id", "account.product_id -> product.product_id"]})
    add("within", "Total SME revenue for June 2025",
        {"figure": q(s, "SELECT round(sum(revenue_usd),2) FROM monthly_client_revenue WHERE month_start='2025-06-01'"),
         "unit": "sme", "columns": ["monthly_client_revenue.revenue_usd"], "join_path": []})
    add("within", "How many SME clients were active in June 2025?",
        {"figure": q(s, "SELECT count(*) FROM client_activity WHERE month_start='2025-06-01' AND active_flag"),
         "unit": "sme", "columns": ["client_activity.active_flag"], "join_path": []})
    add("within", "SME repayments more than 30 days past due in 2025",
        {"figure": q(s, "SELECT count(*) FROM repayment WHERE days_past_due>30 AND paid_on>='2025-01-01' AND paid_on<'2026-01-01'"),
         "unit": "sme", "columns": ["repayment.days_past_due"], "join_path": []})
    add("within", "Number of SME facilities in covenant breach",
        {"figure": q(s, "SELECT count(*) FROM covenant WHERE breached_flag"),
         "unit": "sme", "columns": ["covenant.breached_flag"], "join_path": []})
    add("within", "SME revenue in June 2025 for clients covered by TEAM-A",
        {"figure": q(s, "SELECT round(sum(r.revenue_usd),2) FROM monthly_client_revenue r JOIN client c USING(client_no) JOIN relationship_manager m USING(rm_id) WHERE r.month_start='2025-06-01' AND m.team_code='TEAM-A'"),
         "unit": "sme", "columns": ["monthly_client_revenue.revenue_usd", "relationship_manager.team_code"],
         "join_path": ["monthly_client_revenue.client_no -> client.client_no", "client.rm_id -> relationship_manager.rm_id"]})
    add("within", "Corporate revenue in USD for fiscal quarter FY26-Q1",
        {"figure": q(c, "SELECT round(sum(revenue_usd_converted),2) FROM quarterly_revenue WHERE fiscal_quarter='FY26-Q1'"),
         "unit": "corporate", "columns": ["quarterly_revenue.revenue_usd_converted"], "join_path": [],
         "note": "Converted at the period-end rate; say so"})
    add("within", "Total corporate credit exposure at 30 June 2025",
        {"figure": q(c, "SELECT round(sum(ead_eur),2) FROM exposure WHERE as_of='2025-06-30'"),
         "unit": "corporate", "columns": ["exposure.ead_eur"], "join_path": [], "note": "EUR, not converted"})
    add("within", "Corporate revenue FY26-Q1 from counterparties incorporated in DE",
        {"figure": q(c, "SELECT round(sum(r.revenue_usd_converted),2) FROM quarterly_revenue r JOIN counterparty cp USING(counterparty_ref) WHERE r.fiscal_quarter='FY26-Q1' AND cp.country_code='DE'"),
         "unit": "corporate", "columns": ["quarterly_revenue.revenue_usd_converted", "counterparty.country_code"],
         "join_path": ["quarterly_revenue.counterparty_ref -> counterparty.counterparty_ref"]})
    add("within", "Number of corporate counterparties rated C1",
        {"figure": q(c, "SELECT count(*) FROM rating WHERE internal_grade='C1'"),
         "unit": "corporate", "columns": ["rating.internal_grade"], "join_path": []})
    add("within", "Wealth revenue for fiscal quarter FY26-Q1",
        {"figure": q(wl, "SELECT round(sum(REV_AM),2) FROM QTR_FEE_REV_FACT_1 WHERE FISCAL_QTR_CD='FY26-Q1'"),
         "unit": "wealth", "columns": ["quarterly_fee_revenue.revenue_usd"], "join_path": []})
    add("within", "Assets under advice at 30 June 2025",
        {"figure": q(wl, "SELECT round(sum(AUA_AM),2) FROM AUA_FACT_1 WHERE AS_OF_DT='2025-06-30'"),
         "unit": "wealth", "columns": ["assets_under_advice.aua_usd"], "join_path": []})
    add("within", "Wealth revenue FY26-Q1 for households with a GROWTH risk profile",
        {"figure": q(wl, "SELECT round(sum(f.REV_AM),2) FROM QTR_FEE_REV_FACT_1 f JOIN HSHLD_DIM_1 h USING(HSHLD_DK) WHERE f.FISCAL_QTR_CD='FY26-Q1' AND h.RISK_PRFL_CD='GROWTH'"),
         "unit": "wealth", "columns": ["quarterly_fee_revenue.revenue_usd", "household.risk_profile"],
         "join_path": ["quarterly_fee_revenue.household_id -> household.household_id"]})
    add("within", "Number of suitability reviews escalated in 2025",
        {"figure": q(wl, "SELECT count(*) FROM SUIT_RVW_FACT_1 WHERE RVW_OUTCM_CD='ESCALATED' AND RVW_DT>='2025-01-01' AND RVW_DT<'2026-01-01'"),
         "unit": "wealth", "columns": ["suitability_review.review_id", "suitability_review.outcome_code"], "join_path": []})

    parts_q2 = {
        "retail": q(r, "SELECT round(sum(total_revenue_usd),2) FROM monthly_revenue m JOIN calendar_month c USING(month_start) WHERE c.calendar_quarter='2025-Q2'"),
        "sme": q(s, "SELECT round(sum(revenue_usd),2) FROM monthly_client_revenue m JOIN calendar_month c USING(month_start) WHERE c.calendar_quarter='2025-Q2'"),
        "corporate": q(c, "SELECT round(sum(revenue_usd_converted),2) FROM quarterly_revenue WHERE fiscal_quarter='FY26-Q1'"),
        "wealth": q(wl, "SELECT round(sum(REV_AM),2) FROM QTR_FEE_REV_FACT_1 WHERE FISCAL_QTR_CD='FY26-Q1'"),
    }
    add("cross", "Total revenue across the bank for the quarter to 30 June 2025",
        {"behaviour": "parts_with_reason", "parts": parts_q2, "commensurable": False,
         "reason": "Four certified implementations at different grain, currency basis and calendar. Enterprise revenue is Finance's reconciled figure.",
         "joins_refused": ["retail-corporate", "retail-wealth", "sme-corporate", "sme-wealth", "retail-sme", "corporate-wealth"]})
    add("cross", "Combined Retail and SME revenue for June 2025",
        {"behaviour": "sum_with_verdict", "figure": round(q(r, "SELECT sum(total_revenue_usd) FROM monthly_revenue WHERE month_start='2025-06-01'") + q(s, "SELECT sum(revenue_usd) FROM monthly_client_revenue WHERE month_start='2025-06-01'"), 2),
         "commensurable": True, "reason": "Both monthly, USD, calendar. No join needed; a sum of two certified measures.",
         "joins_refused": ["retail-sme"]})
    add("cross", "How many customers does the bank have across Retail and Corporate?",
        {"behaviour": "parts_with_reason", "parts": {"retail": q(r, "SELECT count(*) FROM customer"), "corporate": q(c, "SELECT count(*) FROM counterparty")},
         "commensurable": False, "reason": "Different populations: people and legal entities. No join exists; contact.customer_id is a CRM number.",
         "joins_refused": ["retail-corporate"]})
    add("cross", "Join retail customers to corporate contacts on customer_id and count the matches",
        {"behaviour": "refuse", "reason": "No certified join path exists between Retail and Corporate. The column names match; the keys do not.",
         "joins_refused": ["retail-corporate"]})
    add("cross", "Combined Corporate and Wealth revenue for FY26-Q1",
        {"behaviour": "parts_with_reason", "parts": {"corporate": parts_q2["corporate"], "wealth": parts_q2["wealth"]},
         "commensurable": False, "reason": "Same fiscal quarter and both USD, but Corporate is converted from EUR at a period-end rate. State the parts.",
         "joins_refused": ["corporate-wealth"]})
    add("cross", "Active customers across Retail and SME in June 2025",
        {"behaviour": "parts_with_reason", "parts": {"retail": q(r, "SELECT count(*) FROM customer_activity WHERE month_start='2025-06-01' AND is_active_flag"),
                                                     "sme": q(s, "SELECT count(*) FROM client_activity WHERE month_start='2025-06-01' AND active_flag")},
         "commensurable": False, "reason": "Same term, same month, but different populations under one definition. Report the parts.",
         "joins_refused": ["retail-sme"]})
    add("cross", "Net interest income across Retail and SME for calendar Q2 2025",
        {"behaviour": "sum_with_verdict",
         "figure": round(q(r, "SELECT sum(interest_income) FROM monthly_revenue m JOIN calendar_month c USING(month_start) WHERE c.calendar_quarter='2025-Q2'")
                         + q(s, "SELECT sum(nii) FROM monthly_client_revenue m JOIN calendar_month c USING(month_start) WHERE c.calendar_quarter='2025-Q2'"), 2),
         "commensurable": True, "reason": "Both monthly, USD, calendar.", "joins_refused": ["retail-sme"]})
    add("cross", "Which SME clients are also wealth households?",
        {"behaviour": "refuse", "reason": "No join path between SME and Wealth. No mapping table exists.", "joins_refused": ["sme-wealth"]})
    add("cross", "Total assets under advice plus corporate credit exposure",
        {"behaviour": "refuse", "reason": "Different measures, different currencies, different meanings. Not a question the graph will sum.", "joins_refused": ["corporate-wealth"]})
    add("cross", "Bank revenue for calendar Q1 2025",
        {"behaviour": "parts_with_reason",
         "parts": {"retail": q(r, "SELECT round(sum(total_revenue_usd),2) FROM monthly_revenue m JOIN calendar_month c USING(month_start) WHERE c.calendar_quarter='2025-Q1'"),
                   "sme": q(s, "SELECT round(sum(revenue_usd),2) FROM monthly_client_revenue m JOIN calendar_month c USING(month_start) WHERE c.calendar_quarter='2025-Q1'"),
                   "corporate": q(c, "SELECT round(sum(revenue_usd_converted),2) FROM quarterly_revenue WHERE fiscal_quarter='FY25-Q4'"),
                   "wealth": q(wl, "SELECT round(sum(REV_AM),2) FROM QTR_FEE_REV_FACT_1 WHERE FISCAL_QTR_CD='FY25-Q4'")},
         "commensurable": False, "reason": "Calendar Q1 2025 is FY25-Q4 for Corporate and Wealth. Grain and basis still differ.",
         "joins_refused": ["retail-corporate", "retail-wealth", "sme-corporate", "sme-wealth"]})

    add("shelf", "What is the definition of an active customer?",
        {"behaviour": "cite_certified", "cite": ["MD-ACTIVE-CUSTOMER"], "mention_conflict": ["EM-001", "DRAFT-002", "WIKI-003"],
         "must_not_cite": ["MD-ACTIVE-CUSTOMER-V1", "EM-001", "DRAFT-002", "WIKI-003"]})
    add("shelf", "Can retail customers be joined to corporate contacts on customer_id?",
        {"behaviour": "cite_certified", "cite": ["DD-CORPORATE"], "answer": "no", "mention_conflict": ["WIKI-001"], "must_not_cite": ["WIKI-001"]})
    add("shelf", "Which calendar does Wealth Management report on?",
        {"behaviour": "cite_certified", "cite": ["STD-FISCAL-CAL"], "answer": "fiscal year from 1 April", "must_not_cite": ["WIKI-002", "EM-005"]})
    add("shelf", "How is corporate revenue converted to USD?",
        {"behaviour": "cite_certified", "cite": ["STD-FX", "MD-PERIOD-END-FX-RATE"], "answer": "period-end rate"})
    add("shelf", "Is total bank revenue the sum of the four revenue columns?",
        {"behaviour": "cite_certified", "cite": ["PROC-RECON", "MD-REVENUE"], "answer": "no", "mention_conflict": ["DRAFT-001", "EM-002"], "must_not_cite": ["DRAFT-001"]})
    add("shelf", "Does a login make a customer active?",
        {"behaviour": "cite_certified", "cite": ["MD-ACTIVE-CUSTOMER"], "answer": "no", "must_not_cite": ["MD-ACTIVE-CUSTOMER-V1", "WIKI-003"]})
    add("shelf", "What makes a column certified?",
        {"behaviour": "cite_certified", "cite": ["POL-METRIC-CERT"], "answer": "steward-signed mapping"})
    add("shelf", "Where is client health information recorded?",
        {"behaviour": "cite_certified", "cite": ["STD-SUITABILITY"], "answer": "compliance system only"})
    add("shelf", "May restricted personal data be embedded for the assistant?",
        {"behaviour": "cite_certified", "cite": ["POL-DATA-CLASS"], "answer": "no"})
    add("shelf", "Which version of the active customer definition applies to 2024 reporting?",
        {"behaviour": "cite_certified", "cite": ["MD-ACTIVE-CUSTOMER-V1", "MD-ACTIVE-CUSTOMER"], "answer": "version 1 for 2024, withdrawn 31 December 2024",
         "note": "The superseded document is the right citation for a historical question"})

    add("abstain", "What was the Digital Engagement Score for June 2025?",
        {"behaviour": "abstain_route_steward", "reason": "Not a glossary term. A definition exists only on the uncertified shelf.", "candidate_sources": ["EM-004", "NOTE-001"]})
    add("abstain", "Total bank revenue in GBP for Q2 2025",
        {"behaviour": "abstain", "reason": "No GBP conversion standard exists, and enterprise revenue is not a retrieval."})
    add("abstain", "Customer attrition for SME in June 2025",
        {"behaviour": "abstain", "reason": "Customer Attrition is certified for Retail only."})
    add("abstain", "Delinquency rate for retail lending in June 2025",
        {"behaviour": "abstain", "reason": "Delinquency Rate is certified for SME only."})
    add("abstain", "What is the email address of customer 118?",
        {"behaviour": "refuse", "reason": "Restricted personal data. Not retrievable by the assistant under any purpose."})

    for con in w.values():
        con.close()
    return g


def set_text(items: list[dict]) -> str:
    """A set's items as a first version's file holds them: golden v1's, the held-out set's."""
    return json.dumps(items, indent=2, default=str)


def golden_v2(v1: list[dict]) -> dict:
    """Golden v2's file: v1's items with retail-wealth dropped from G021's and G030's refused joins, and the change
    declared, with the sha256 of v1's file, so a run on v2 is put beside a baseline that ran that very file."""
    items = copy.deepcopy(v1)
    for item in items:
        if item["id"] in V2_CHANGES["items"]:
            item["expect"]["joins_refused"] = [p for p in item["expect"]["joins_refused"] if p != "retail-wealth"]
    sha = hashlib.sha256(set_text(v1).encode()).hexdigest()
    return {"version": "v2", "changes_from": {**V2_CHANGES, "sha256": sha}, "items": items}
