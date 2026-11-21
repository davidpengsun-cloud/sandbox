"""A held-out set: paraphrases of golden questions, worded differently on purpose.

The parser is rule-based and was tuned against the golden set's wording, so a
perfect golden score says the rules fit those sentences, not that they
generalise. These sixteen questions ask the same things another way. They are
scored by the same scorer and reported apart from the gated forty-five; nothing
here was tuned to pass, and the score is whatever it is.
"""
from __future__ import annotations

from pathlib import Path

from kge.estate.golden import warehouses
from kge.estate.spec import UNITS
from kge.ports import EstateWarehouses


def q(con, sql: str):
    return con.execute(sql).fetchone()[0]


def build_heldout(out: Path, estate: EstateWarehouses | None = None) -> list[dict]:
    estate = estate or warehouses(out)
    w = {u: estate.open(u) for u in UNITS}
    r, s, c, wl = w["retail"], w["sme"], w["corporate"], w["wealth"]
    h: list[dict] = []

    def add(kind, question, expect, paraphrase_of):
        h.append({"id": f"H{len(h) + 1:03d}", "kind": kind, "question": question, "expect": expect, "paraphrase_of": paraphrase_of})

    june_retail = q(r, "SELECT round(sum(total_revenue_usd),2) FROM monthly_revenue WHERE month_start='2025-06-01'")
    june_sme = q(s, "SELECT round(sum(revenue_usd),2) FROM monthly_client_revenue WHERE month_start='2025-06-01'")
    fy26q1_corp = q(c, "SELECT round(sum(revenue_usd_converted),2) FROM quarterly_revenue WHERE fiscal_quarter='FY26-Q1'")
    fy26q1_wealth = q(wl, "SELECT round(sum(REV_AM),2) FROM QTR_FEE_REV_FACT_1 WHERE FISCAL_QTR_CD='FY26-Q1'")

    add("within", "How much revenue did SME make in June 2025?",
        {"figure": june_sme, "unit": "sme", "columns": ["monthly_client_revenue.revenue_usd"], "join_path": []}, "Total SME revenue for June 2025")
    add("within", "What did retail bring in as revenue during June 2025?",
        {"figure": june_retail, "unit": "retail", "columns": ["monthly_revenue.total_revenue_usd"], "join_path": []}, "Total retail revenue for June 2025")
    add("within", "Count the SME clients that were active during June 2025",
        {"figure": q(s, "SELECT count(*) FROM client_activity WHERE month_start='2025-06-01' AND active_flag"), "unit": "sme",
         "columns": ["client_activity.active_flag"], "join_path": []}, "How many SME clients were active in June 2025?")
    add("within", "Corporate credit exposure in total as at 30 June 2025",
        {"figure": q(c, "SELECT round(sum(ead_eur),2) FROM exposure WHERE as_of='2025-06-30'"), "unit": "corporate",
         "columns": ["exposure.ead_eur"], "join_path": []}, "Total corporate credit exposure at 30 June 2025")
    add("within", "Revenue booked by Wealth Management in FY26-Q1",
        {"figure": fy26q1_wealth, "unit": "wealth", "columns": ["quarterly_fee_revenue.revenue_usd"], "join_path": []}, "Wealth revenue for fiscal quarter FY26-Q1")
    add("within", "How many retail complaints were still unresolved at 30 June 2025?",
        {"figure": q(r, "SELECT count(*) FROM complaint WHERE resolved_on IS NULL"), "unit": "retail",
         "columns": ["complaint.complaint_id", "complaint.resolved_on"], "join_path": []}, "Number of open retail complaints as at 30 June 2025")

    add("cross", "Add up Retail and SME revenue for June 2025",
        {"behaviour": "sum_with_verdict", "figure": round(june_retail + june_sme, 2), "commensurable": True, "joins_refused": ["retail-sme"]},
        "Combined Retail and SME revenue for June 2025")
    add("cross", "Sum Corporate and Wealth revenue for FY26-Q1",
        {"behaviour": "parts_with_reason", "parts": {"corporate": fy26q1_corp, "wealth": fy26q1_wealth}, "commensurable": False, "joins_refused": ["corporate-wealth"]},
        "Combined Corporate and Wealth revenue for FY26-Q1")
    add("cross", "Can you match SME clients with wealth households?",
        {"behaviour": "refuse", "joins_refused": ["sme-wealth"]}, "Which SME clients are also wealth households?")
    add("cross", "Put retail customer_id next to corporate contacts and tell me how many line up",
        {"behaviour": "refuse", "joins_refused": ["retail-corporate"]}, "Join retail customers to corporate contacts on customer_id and count the matches")

    add("shelf", "How is an active customer defined?",
        {"behaviour": "cite_certified", "cite": ["MD-ACTIVE-CUSTOMER"], "must_not_cite": ["MD-ACTIVE-CUSTOMER-V1", "EM-001", "DRAFT-002", "WIKI-003"]},
        "What is the definition of an active customer?")
    add("shelf", "Explain which fiscal calendar Wealth Management uses",
        {"behaviour": "cite_certified", "cite": ["STD-FISCAL-CAL"], "must_not_cite": ["WIKI-002", "EM-005"]}, "Which calendar does Wealth Management report on?")
    add("shelf", "Is it acceptable to embed restricted personal data for the assistant?",
        {"behaviour": "cite_certified", "cite": ["POL-DATA-CLASS"]}, "May restricted personal data be embedded for the assistant?")

    add("abstain", "Give me the Digital Engagement Score for June 2025",
        {"behaviour": "abstain_route_steward"}, "What was the Digital Engagement Score for June 2025?")
    add("abstain", "Show me the date of birth of customer 118",
        {"behaviour": "refuse"}, "What is the email address of customer 118?")
    add("abstain", "Delinquency rate for retail in June 2025",
        {"behaviour": "abstain"}, "Delinquency rate for retail lending in June 2025")

    for con in w.values():
        con.close()
    return h
