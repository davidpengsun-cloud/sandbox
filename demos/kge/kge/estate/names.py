"""Every Wealth table's physical name and its business name, in one place.

Wealth's warehouse is named in SAGE's style throughout: SAGE's seventeen tables under SAGE's own names (kge/estate/sage.py),
and the fifteen tables Meridian keeps beside them under physical names in the same style (MERIDIAN): upper case, SAGE's
abbreviations for the same words (HSHLD, ACCT, BRCH, RGN, DIVSN, MNTH, CLDR, TRAN, ADV, ...) and its suffixes (_ID, _CD,
_NM, _DT, _AM, _IN, _DIM_1, _FACT_1), and a key that refers to a SAGE table under SAGE's name for it (a household is
HSHLD_DK, as ACCT_DIM2_1 holds it). Rows and values are the tables' own, unchanged: only the names are physical.

The business names are the names the tables and columns had: the fifteen keep theirs as business names, the five SAGE
tables Meridian's dropped tables stood for (account, flow, month_end, office, ref_source_system) take those tables'
names, and the twelve other SAGE tables the names Meridian gave them (account_segment, nna_reference, ...). A column's
business name is the name it had: a Meridian table's every column, and the SAGE columns Meridian's dropped tables held
under a name of their own (sage.ALIASES). The pack carries them in its curated layer, as an entity each table represents
and an alias of each column, so a question worded the business way resolves, and its SQL runs on the physical names.

MERIDIAN maps each kept table's business name to (physical name, {business column: physical column}), every column, in
the table's order.
"""
from __future__ import annotations

from kge.estate import sage

MERIDIAN: dict[str, tuple[str, dict[str, str]]] = {
    "fiscal_calendar": ("FISCAL_QTR_DIM_1", {"fiscal_quarter": "FISCAL_QTR_CD", "fiscal_year": "FISCAL_YEAR_NR",
                                             "quarter_start": "QRTR_START_CLDR_DT", "quarter_end": "QRTR_END_CLDR_DT"}),
    "advisor": ("ADV_DIM_1", {"advisor_id": "ADV_ID", "advisor_name": "ADV_NM", "office_code": "BRCH_CD", "licence_class": "LICNS_CLS_CD"}),
    "household": ("HSHLD_DIM_1", {"household_id": "HSHLD_DK", "household_name": "HSHLD_NM", "primary_advisor_id": "PRIM_ADV_ID",
                                  "risk_profile": "RISK_PRFL_CD", "onboarded_on": "HSHLD_ONBRD_DT"}),
    "portfolio": ("PRTFL_DIM_1", {"portfolio_id": "PRTFL_ID", "household_id": "HSHLD_DK", "mandate_type": "MNDT_TYPE_CD",
                                  "base_currency": "BASE_CRNCY_CD", "inception_on": "PRTFL_INCPTN_DT"}),
    "holding": ("HLDG_FACT_1", {"holding_id": "HLDG_ID", "portfolio_id": "PRTFL_ID", "as_of": "AS_OF_DT", "instrument_id": "INSTR_ID",
                                "quantity": "HLDG_QY", "market_value": "MKT_VAL_AM"}),
    "quarterly_fee_revenue": ("QTR_FEE_REV_FACT_1", {"fee_id": "FEE_ID", "household_id": "HSHLD_DK", "fiscal_quarter": "FISCAL_QTR_CD",
                                                     "advisory_fee_usd": "ADV_FEE_AM", "product_fee_usd": "PROD_FEE_AM",
                                                     "revenue_usd": "REV_AM"}),
    "assets_under_advice": ("AUA_FACT_1", {"aua_id": "AUA_ID", "household_id": "HSHLD_DK", "as_of": "AS_OF_DT", "aua_usd": "AUA_AM"}),
    "suitability_review": ("SUIT_RVW_FACT_1", {"review_id": "RVW_ID", "household_id": "HSHLD_DK", "reviewed_on": "RVW_DT",
                                               "outcome_code": "RVW_OUTCM_CD", "reviewer_id": "RVWR_ADV_ID"}),
    "division": ("DIVSN_DIM_1", {"division_code": "DIVSN_CD", "division_name": "DIVSN_NM", "valid_from": "DW_EFF_DT",
                                 "valid_to": "DW_EXPR_DT", "is_current": "CURR_IN"}),
    "region": ("RGN_DIM_1", {"region_code": "RGN_CD", "region_name": "RGN_NM", "division_code": "DIVSN_CD", "valid_from": "DW_EFF_DT",
                             "valid_to": "DW_EXPR_DT", "is_current": "CURR_IN"}),
    "client_segment": ("CLNT_SEGMT_DIM_1", {"segment_code": "SEGMT_CD", "segment_name": "SEGMT_NM",
                                            "parent_segment_code": "PARNT_SEGMT_CD", "level": "SEGMT_LVL_NR"}),
    "ref_flow_type": ("TRAN_TYPE_1", {"flow_type_code": "DW_TRAN_TYPE_CD", "description": "TRAN_TYPE_DESC_TX", "direction": "DIRCTN_CD",
                                      "nna_counts": "NNA_CNT_IN"}),
    "household_member": ("HSHLD_MBR_1", {"household_id": "HSHLD_DK", "member_id": "MBR_ID", "member_role": "MBR_ROLE_CD"}),
    "nna_exclusion": ("NNA_EXCL_1", {"exclusion_id": "EXCL_ID", "account_id": "DW_ACCT_ID", "source_system": "SRCE_SYS_CD",
                                     "reason_code": "EXCL_RSN_CD", "effective_from": "EXCL_EFF_DT", "effective_to": "EXCL_EXPR_DT"}),
    "aum_month_end": ("AUM_MNTH_END_FACT_1", {"household_id": "HSHLD_DK", "month_end_date": "CLDR_EOM_DT", "aum_usd": "AUM_AM"}),
}


def table(business: str) -> str:
    """A Wealth table's physical name, by its business name (a kept table's, or a SAGE table's)."""
    return MERIDIAN[business][0] if business in MERIDIAN else sage.TABLES[business]


def column(business_table: str, business_column: str) -> str:
    """A Wealth column's physical name, by its table's and its own business names: a kept table's column, a SAGE column a
    dropped Meridian table held under a name of its own (sage.ALIASES), else SAGE's own field."""
    if business_table in MERIDIAN:
        return MERIDIAN[business_table][1][business_column]
    named = sage.ALIASES.get(business_table, {})
    if business_column in named:
        return named[business_column]
    fields = {f["name"].lower(): f["name"] for f in sage.fields(business_table)}
    return fields[business_column.lower()]


def business_key(physical: str) -> str:
    """A key column's business name, by its physical name in a kept table (HSHLD_DK is household_id)."""
    return next(b for _, cols in MERIDIAN.values() for b, p in cols.items() if p == physical)


def tables() -> dict[str, str]:
    """Every Wealth table, business name -> physical name: the fifteen kept, then SAGE's seventeen."""
    return {**{b: p for b, (p, _) in MERIDIAN.items()}, **sage.TABLES}


def columns() -> dict[tuple[str, str], tuple[str, str]]:
    """Every column with a business name, (business table, business column) -> (physical table, physical column): each
    kept table's columns, and the SAGE columns a dropped Meridian table held under a name of its own."""
    out = {(b, c): (p, pc) for b, (p, cs) in MERIDIAN.items() for c, pc in cs.items()}
    out.update({(b, c): (sage.TABLES[b], pc) for b, cs in sage.ALIASES.items() for c, pc in cs.items()})
    return out


def physical_schemas(schemas: dict[str, dict]) -> dict[str, dict]:
    """The Wealth schemas with each kept table, and every foreign key to one, under its physical names, in the same order."""
    out = {}
    for t, s in schemas.items():
        if t in MERIDIAN:
            phys, cols = MERIDIAN[t]
            out[phys] = {"pk": ", ".join(cols[k.strip()] for k in s["pk"].split(",")),
                         "columns": [(cols[n], typ, desc) for n, typ, desc in s["columns"]],
                         "fks": [(cols[c], table(rt) if rt in MERIDIAN else rt, MERIDIAN[rt][1][rc] if rt in MERIDIAN else rc)
                                 for c, rt, rc in s["fks"]]}
        else:
            out[t] = s
    return out
