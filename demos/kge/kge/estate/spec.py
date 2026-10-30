"""The shape of the synthetic estate: four warehouses, one glossary.

Everything here is invented. Meridian Union Bank is the repository's reference
institution, and the numbers follow its stated ratios: four business units,
each with its own warehouse and no key that joins across them, and one
enterprise glossary. The one link between two units is curated, not a key: the
certified client cross-reference from Retail customers to Wealth households
(CURATED_SCHEMAS), in a warehouse of its own.

All but the warehouses' DDL is the client's own, and read from where it is
kept: the pack (clients/meridian/pack.yaml) holds the units, the glossary, the
certified mappings and their measure metadata, the no-join facts, the
restricted columns, the role templates and the purposes; the deployment's
identities (deploy/meridian/identities.yaml) bind each principal to a role; and
the processes a role may perform are the model's constants plus the platform's operations. This
module reads them into the names and values the estate has always used.

SCHEMAS stays here: it is the simulated client's warehouse DDL, not pack data,
and so does CURATED_SCHEMAS, the cross-reference's. A column spec is (name, type, description). A table spec carries its primary
key and its foreign keys as (column, referenced_table, referenced_column).
Foreign keys are declared in the warehouse itself, so the graph loader can
harvest join paths from DuckDB's own constraint catalogue rather than from a
hand-written list. That is the point: the within-unit join graph is regenerable.

Each unit's customer-like key (UNIT_KEY, the pack's entity_key) is deliberately
different. The Wealth expansion (#39) is appended to the schemas, with Retail's
counterparty columns filled on transfer legs alone; a key that is not unique (an
SCD2 table's code, flow to account) declares no foreign key, since those joins
are curated in the pack. The cross-reference's matching, on whatever personal
data it takes, happens upstream and offline: its table holds ids, never the data
matched on. Every column not in CERTIFIED_MAPPINGS is uncertified;
commensurability across units is derived by rule from MEASURE_METADATA, never
guessed; NO_JOIN_FACTS are curated, never inferred; and the SQL gate refuses
RESTRICTED_COLUMNS by name, in every arm. Enforcement in the real estate is IAM
and fine-grained access control: PRINCIPALS is a query-time mirror the graph
carries for analysis and for the demo's filter.

Wealth is a subset of SAGE, the reference brokerage warehouse (kge/estate/sage.py): its seventeen SAGE tables are held
as SAGE has them, under SAGE's physical names, every column under its own, after the Meridian tables they refer to and
before those that refer to them. Meridian's account, flow, month_end, office and ref_source_system tables are gone: SAGE's
ACCT_DIM2_1, TRAN_FACT_1, MONTH_DIM_1, BRCH_DIM_1 and SRCE_SYS_1 are those. Wealth's fifteen other tables are Meridian's,
with physical names in SAGE's style (kge/estate/names.py: the tables below are written under their business names and
renamed there), a foreign key to a dropped table pointing at SAGE's key where the key is unique (aum_month_end's month
end, at MONTH_DIM_1), and dropped where it is not (nna_exclusion's account and source system name an account by
Meridian's code, BANK or BROKERAGE, which the pack decodes from SAGE's id).
"""
from __future__ import annotations

from pathlib import Path

from kge.estate import names, sage

import yaml

from kge.pipeline.profile import resolve
from kge.pipeline.settings import ROOT
from kge.pipeline.access import process_vocabulary

DEMO = ROOT
PROFILE = resolve()
CLIENT = PROFILE.client
PACK = PROFILE.clients / CLIENT / "pack.yaml"
IDENTITIES = PROFILE.identities
META_MODEL = DEMO / "meta-model.yaml"


def read(pack: Path = PACK, identities: Path = IDENTITIES, meta_model: Path = META_MODEL) -> dict:
    """The estate's names and values, from a pack, deployment identities and the shared process vocabulary."""
    p = yaml.safe_load(Path(pack).read_text())
    bound = yaml.safe_load(Path(identities).read_text())["principals"]
    processes = process_vocabulary(yaml.safe_load(Path(meta_model).read_text())["node_types"]["Process"]["constants"])
    units = [s["id"] for s in p["sources"]]
    term = {g["id"]: g["name"] for g in p["glossary"]}
    roles = {r["id"]: r for r in p["access"]["roles"]}

    def column(ref: str) -> tuple[str, str, str]:
        unit, table, col = ref.split(".")
        return unit, table, col

    def principal(b: dict) -> dict:
        r = roles[b["role"]]
        out = {"principal": b["principal"], "role": r["label"], "reads": list(units) if r["reads"] == "all" else list(r["reads"])}
        if "writes" in r:
            out["writes"] = list(r["writes"])
        out["processes"] = list(r["processes"])
        return out

    return {
        "UNITS": units,
        "UNIT_LABEL": {s["id"]: s["label"] for s in p["sources"]},
        "UNIT_KEY": {s["id"]: s["entity_key"] for s in p["sources"]},
        "GLOSSARY": [{"term": g["name"], "domain": g["domain"], "steward": g["steward"], "definition": g["definition"]}
                     for g in p["glossary"]],
        "CERTIFIED_MAPPINGS": [(term[m["term"]], *column(m["column"])) for m in p["mappings"] if m["certified"]],
        "MEASURE_METADATA": {column(m["column"]): {k: m[k] for k in ("grain", "currency", "calendar", "conversion")}
                             for m in p["measures"]},
        "NO_JOIN_FACTS": [{"a": f["between"][0], "b": f["between"][1], "asserted_by": f["asserted_by"],
                           "asserted_on": f["asserted_on"], "note": f["note"]} for f in p["no_join"]],
        "RESTRICTED_COLUMNS": [column(c) for r in p["policies"]["restricted_attributes"] for c in r["columns"]],
        "PROCESSES": dict(processes),
        "PRINCIPALS": [principal(b) for b in bound],
        "PURPOSES": list(p["access"]["purposes"]),
    }


_estate = read()

UNITS: list[str] = _estate["UNITS"]
UNIT_LABEL: dict[str, str] = _estate["UNIT_LABEL"]
UNIT_KEY: dict[str, str] = _estate["UNIT_KEY"]

SCHEMAS: dict[str, dict[str, dict]] = {
    "retail": {
        "calendar_month": {
            "pk": "month_start",
            "columns": [
                ("month_start", "DATE", "First day of the calendar month"),
                ("calendar_year", "INTEGER", "Calendar year"),
                ("calendar_quarter", "VARCHAR", "Calendar quarter, e.g. 2025-Q2"),
            ],
            "fks": [],
        },
        "branch": {
            "pk": "branch_id",
            "columns": [
                ("branch_id", "INTEGER", "Branch identifier"),
                ("branch_name", "VARCHAR", "Branch trading name"),
                ("region_code", "VARCHAR", "Sales region code"),
                ("opened_on", "DATE", "Date the branch opened"),
            ],
            "fks": [],
        },
        "product": {
            "pk": "product_id",
            "columns": [
                ("product_id", "INTEGER", "Product identifier"),
                ("product_name", "VARCHAR", "Product marketing name"),
                ("product_family", "VARCHAR", "Current account, savings, lending, card"),
                ("rate_bps", "INTEGER", "Headline rate in basis points"),
                ("fee_monthly", "DECIMAL(10,2)", "Monthly account fee in USD"),
            ],
            "fks": [],
        },
        "customer": {
            "pk": "customer_id",
            "columns": [
                ("customer_id", "INTEGER", "Retail customer identifier"),
                ("first_name", "VARCHAR", "Customer first name"),
                ("last_name", "VARCHAR", "Customer last name"),
                ("date_of_birth", "DATE", "Customer date of birth"),
                ("email", "VARCHAR", "Customer email address"),
                ("segment_code", "VARCHAR", "Marketing segment code"),
                ("branch_id", "INTEGER", "Home branch"),
                ("opened_on", "DATE", "Date the relationship opened"),
            ],
            "fks": [("branch_id", "branch", "branch_id")],
        },
        "account": {
            "pk": "account_id",
            "columns": [
                ("account_id", "INTEGER", "Account identifier"),
                ("customer_id", "INTEGER", "Owning customer"),
                ("product_id", "INTEGER", "Product held"),
                ("status_code", "VARCHAR", "OPEN, DORMANT or CLOSED"),
                ("opened_on", "DATE", "Date opened"),
                ("closed_on", "DATE", "Date closed, null while open"),
                ("currency_code", "VARCHAR", "Account currency, always USD in retail"),
            ],
            "fks": [("customer_id", "customer", "customer_id"), ("product_id", "product", "product_id")],
        },
        "transaction": {
            "pk": "txn_id",
            "columns": [
                ("txn_id", "BIGINT", "Transaction identifier"),
                ("account_id", "INTEGER", "Account posted to"),
                ("posted_on", "DATE", "Posting date"),
                ("amount", "DECIMAL(14,2)", "Signed amount in account currency"),
                ("txn_type", "VARCHAR", "DEBIT or CREDIT"),
                ("channel_code", "VARCHAR", "BRANCH, ATM, ONLINE, MOBILE"),
                ("merchant_category", "VARCHAR", "Merchant category code where applicable"),
                ("counterparty_bank", "VARCHAR", f"Institution on the other side of a transfer; {CLIENT.upper()} for an account held here"),
                ("counterparty_account", "BIGINT", "Account number on the other side of a transfer, at that institution"),
            ],
            "fks": [("account_id", "account", "account_id")],
        },
        "monthly_revenue": {
            "pk": "revenue_id",
            "columns": [
                ("revenue_id", "INTEGER", "Row identifier"),
                ("account_id", "INTEGER", "Account the revenue accrues to"),
                ("month_start", "DATE", "Calendar month"),
                ("interest_income", "DECIMAL(14,2)", "Net interest income for the month, USD"),
                ("fee_income", "DECIMAL(14,2)", "Fee income for the month, USD"),
                ("total_revenue_usd", "DECIMAL(14,2)", "Interest plus fees, USD, calendar month"),
            ],
            "fks": [("account_id", "account", "account_id"), ("month_start", "calendar_month", "month_start")],
        },
        "daily_balance": {
            "pk": "balance_id",
            "columns": [
                ("balance_id", "BIGINT", "Row identifier"),
                ("account_id", "INTEGER", "Account"),
                ("as_of", "DATE", "Balance date"),
                ("ledger_balance", "DECIMAL(14,2)", "End-of-day ledger balance"),
                ("available_balance", "DECIMAL(14,2)", "End-of-day available balance"),
            ],
            "fks": [("account_id", "account", "account_id")],
        },
        "customer_activity": {
            "pk": "activity_id",
            "columns": [
                ("activity_id", "INTEGER", "Row identifier"),
                ("customer_id", "INTEGER", "Customer"),
                ("month_start", "DATE", "Calendar month"),
                ("login_count", "INTEGER", "Digital logins in the month"),
                ("txn_count", "INTEGER", "Customer-initiated transactions in the month"),
                ("is_active_flag", "BOOLEAN", "True where txn_count is at least one in the month"),
            ],
            "fks": [("customer_id", "customer", "customer_id"), ("month_start", "calendar_month", "month_start")],
        },
        "complaint": {
            "pk": "complaint_id",
            "columns": [
                ("complaint_id", "INTEGER", "Complaint identifier"),
                ("customer_id", "INTEGER", "Complainant"),
                ("raised_on", "DATE", "Date raised"),
                ("category", "VARCHAR", "Complaint category"),
                ("resolved_on", "DATE", "Date resolved, null while open"),
            ],
            "fks": [("customer_id", "customer", "customer_id")],
        },
    },
    "sme": {
        "calendar_month": {
            "pk": "month_start",
            "columns": [
                ("month_start", "DATE", "First day of the calendar month"),
                ("calendar_year", "INTEGER", "Calendar year"),
                ("calendar_quarter", "VARCHAR", "Calendar quarter"),
            ],
            "fks": [],
        },
        "relationship_manager": {
            "pk": "rm_id",
            "columns": [
                ("rm_id", "INTEGER", "Relationship manager identifier"),
                ("rm_name", "VARCHAR", "Relationship manager name"),
                ("team_code", "VARCHAR", "Coverage team"),
            ],
            "fks": [],
        },
        "client": {
            "pk": "client_no",
            "columns": [
                ("client_no", "INTEGER", "SME client number"),
                ("legal_name", "VARCHAR", "Registered legal name"),
                ("registration_no", "VARCHAR", "Company registration number"),
                ("industry_code", "VARCHAR", "Industry classification code"),
                ("rm_id", "INTEGER", "Covering relationship manager"),
                ("onboarded_on", "DATE", "Onboarding date"),
            ],
            "fks": [("rm_id", "relationship_manager", "rm_id")],
        },
        "facility": {
            "pk": "facility_id",
            "columns": [
                ("facility_id", "INTEGER", "Facility identifier"),
                ("client_no", "INTEGER", "Borrowing client"),
                ("facility_type", "VARCHAR", "TERM_LOAN, OVERDRAFT, INVOICE_FINANCE"),
                ("limit_amount", "DECIMAL(14,2)", "Approved limit"),
                ("currency_code", "VARCHAR", "Facility currency, USD"),
                ("start_on", "DATE", "Facility start"),
                ("end_on", "DATE", "Facility maturity"),
            ],
            "fks": [("client_no", "client", "client_no")],
        },
        "drawdown": {
            "pk": "drawdown_id",
            "columns": [
                ("drawdown_id", "INTEGER", "Drawdown identifier"),
                ("facility_id", "INTEGER", "Facility drawn"),
                ("drawn_on", "DATE", "Drawdown date"),
                ("amount", "DECIMAL(14,2)", "Amount drawn"),
            ],
            "fks": [("facility_id", "facility", "facility_id")],
        },
        "repayment": {
            "pk": "repayment_id",
            "columns": [
                ("repayment_id", "INTEGER", "Repayment identifier"),
                ("facility_id", "INTEGER", "Facility repaid"),
                ("paid_on", "DATE", "Payment date"),
                ("amount", "DECIMAL(14,2)", "Amount paid"),
                ("days_past_due", "INTEGER", "Days past the scheduled date, zero when on time"),
            ],
            "fks": [("facility_id", "facility", "facility_id")],
        },
        "monthly_client_revenue": {
            "pk": "rev_id",
            "columns": [
                ("rev_id", "INTEGER", "Row identifier"),
                ("client_no", "INTEGER", "Client"),
                ("month_start", "DATE", "Calendar month"),
                ("nii", "DECIMAL(14,2)", "Net interest income, USD"),
                ("fees", "DECIMAL(14,2)", "Fee income, USD"),
                ("revenue_usd", "DECIMAL(14,2)", "NII plus fees, USD, calendar month"),
            ],
            "fks": [("client_no", "client", "client_no"), ("month_start", "calendar_month", "month_start")],
        },
        "client_activity": {
            "pk": "activity_id",
            "columns": [
                ("activity_id", "INTEGER", "Row identifier"),
                ("client_no", "INTEGER", "Client"),
                ("month_start", "DATE", "Calendar month"),
                ("payments_count", "INTEGER", "Outbound payments in the month"),
                ("active_flag", "BOOLEAN", "True where payments_count is at least one"),
            ],
            "fks": [("client_no", "client", "client_no"), ("month_start", "calendar_month", "month_start")],
        },
        "covenant": {
            "pk": "covenant_id",
            "columns": [
                ("covenant_id", "INTEGER", "Covenant identifier"),
                ("facility_id", "INTEGER", "Facility the covenant attaches to"),
                ("covenant_type", "VARCHAR", "LEVERAGE, INTEREST_COVER, MIN_LIQUIDITY"),
                ("threshold", "DECIMAL(10,4)", "Covenant threshold"),
                ("tested_on", "DATE", "Last test date"),
                ("breached_flag", "BOOLEAN", "True where the last test breached"),
            ],
            "fks": [("facility_id", "facility", "facility_id")],
        },
    },
    "corporate": {
        "fiscal_calendar": {
            "pk": "fiscal_quarter",
            "columns": [
                ("fiscal_quarter", "VARCHAR", "Fiscal quarter label, e.g. FY25-Q1"),
                ("fiscal_year", "INTEGER", "Fiscal year, starting 1 April"),
                ("quarter_start", "DATE", "First day of the fiscal quarter"),
                ("quarter_end", "DATE", "Last day of the fiscal quarter"),
            ],
            "fks": [],
        },
        "counterparty": {
            "pk": "counterparty_ref",
            "columns": [
                ("counterparty_ref", "VARCHAR", "Counterparty reference"),
                ("counterparty_name", "VARCHAR", "Legal entity name"),
                ("lei_code", "VARCHAR", "Legal entity identifier"),
                ("country_code", "VARCHAR", "Country of incorporation"),
                ("sector_code", "VARCHAR", "Sector classification"),
                ("onboarded_on", "DATE", "Onboarding date"),
            ],
            "fks": [],
        },
        "contact": {
            "pk": "contact_id",
            "columns": [
                ("contact_id", "INTEGER", "Contact identifier"),
                ("counterparty_ref", "VARCHAR", "Counterparty the contact belongs to"),
                ("customer_id", "INTEGER", "CRM contact number. Not the retail customer identifier, despite the name"),
                ("contact_name", "VARCHAR", "Contact name"),
                ("contact_email", "VARCHAR", "Contact email"),
                ("role_code", "VARCHAR", "TREASURER, CFO, OPERATIONS"),
            ],
            "fks": [("counterparty_ref", "counterparty", "counterparty_ref")],
        },
        "deal": {
            "pk": "deal_id",
            "columns": [
                ("deal_id", "INTEGER", "Deal identifier"),
                ("counterparty_ref", "VARCHAR", "Counterparty"),
                ("product_code", "VARCHAR", "FX_FWD, IRS, LOAN, BOND"),
                ("notional_eur", "DECIMAL(16,2)", "Notional in EUR"),
                ("trade_on", "DATE", "Trade date"),
                ("maturity_on", "DATE", "Maturity date"),
            ],
            "fks": [("counterparty_ref", "counterparty", "counterparty_ref")],
        },
        "exposure": {
            "pk": "exposure_id",
            "columns": [
                ("exposure_id", "INTEGER", "Row identifier"),
                ("counterparty_ref", "VARCHAR", "Counterparty"),
                ("as_of", "DATE", "Measurement date"),
                ("ead_eur", "DECIMAL(16,2)", "Exposure at default, EUR"),
                ("pd_bps", "INTEGER", "Probability of default, basis points"),
                ("lgd_pct", "DECIMAL(5,2)", "Loss given default, per cent"),
            ],
            "fks": [("counterparty_ref", "counterparty", "counterparty_ref")],
        },
        "quarterly_revenue": {
            "pk": "qrev_id",
            "columns": [
                ("qrev_id", "INTEGER", "Row identifier"),
                ("counterparty_ref", "VARCHAR", "Counterparty"),
                ("fiscal_quarter", "VARCHAR", "Fiscal quarter"),
                ("revenue_eur", "DECIMAL(16,2)", "Revenue in EUR for the fiscal quarter"),
                ("fx_rate_period_end", "DECIMAL(10,6)", "EUR to USD rate at quarter end"),
                ("revenue_usd_converted", "DECIMAL(16,2)", "Revenue converted to USD at the period-end rate"),
            ],
            "fks": [("counterparty_ref", "counterparty", "counterparty_ref"), ("fiscal_quarter", "fiscal_calendar", "fiscal_quarter")],
        },
        "limit_utilisation": {
            "pk": "util_id",
            "columns": [
                ("util_id", "INTEGER", "Row identifier"),
                ("counterparty_ref", "VARCHAR", "Counterparty"),
                ("as_of", "DATE", "Measurement date"),
                ("limit_eur", "DECIMAL(16,2)", "Approved limit, EUR"),
                ("utilised_eur", "DECIMAL(16,2)", "Utilised amount, EUR"),
            ],
            "fks": [("counterparty_ref", "counterparty", "counterparty_ref")],
        },
        "rating": {
            "pk": "rating_id",
            "columns": [
                ("rating_id", "INTEGER", "Row identifier"),
                ("counterparty_ref", "VARCHAR", "Counterparty"),
                ("rated_on", "DATE", "Rating date"),
                ("internal_grade", "VARCHAR", "Internal rating grade"),
                ("agency_grade", "VARCHAR", "External agency grade where available"),
            ],
            "fks": [("counterparty_ref", "counterparty", "counterparty_ref")],
        },
    },
    "wealth": {
        "fiscal_calendar": {
            "pk": "fiscal_quarter",
            "columns": [
                ("fiscal_quarter", "VARCHAR", "Fiscal quarter label, e.g. FY25-Q1"),
                ("fiscal_year", "INTEGER", "Fiscal year, starting 1 April"),
                ("quarter_start", "DATE", "First day of the fiscal quarter"),
                ("quarter_end", "DATE", "Last day of the fiscal quarter"),
            ],
            "fks": [],
        },
        "advisor": {
            "pk": "advisor_id",
            "columns": [
                ("advisor_id", "INTEGER", "Advisor identifier"),
                ("advisor_name", "VARCHAR", "Advisor name"),
                ("office_code", "VARCHAR", "Office"),
                ("licence_class", "VARCHAR", "Licence class held"),
            ],
            "fks": [],
        },
        "household": {
            "pk": "household_id",
            "columns": [
                ("household_id", "INTEGER", "Household identifier"),
                ("household_name", "VARCHAR", "Household display name"),
                ("primary_advisor_id", "INTEGER", "Primary advisor"),
                ("risk_profile", "VARCHAR", "CAUTIOUS, BALANCED, GROWTH"),
                ("onboarded_on", "DATE", "Onboarding date"),
            ],
            "fks": [("primary_advisor_id", "advisor", "advisor_id")],
        },
        "portfolio": {
            "pk": "portfolio_id",
            "columns": [
                ("portfolio_id", "INTEGER", "Portfolio identifier"),
                ("household_id", "INTEGER", "Owning household"),
                ("mandate_type", "VARCHAR", "DISCRETIONARY or ADVISORY"),
                ("base_currency", "VARCHAR", "Portfolio base currency, USD"),
                ("inception_on", "DATE", "Inception date"),
            ],
            "fks": [("household_id", "household", "household_id")],
        },
        "holding": {
            "pk": "holding_id",
            "columns": [
                ("holding_id", "BIGINT", "Row identifier"),
                ("portfolio_id", "INTEGER", "Portfolio"),
                ("as_of", "DATE", "Valuation date"),
                ("instrument_id", "VARCHAR", "Instrument identifier"),
                ("quantity", "DECIMAL(18,4)", "Units held"),
                ("market_value", "DECIMAL(16,2)", "Market value in base currency"),
            ],
            "fks": [("portfolio_id", "portfolio", "portfolio_id")],
        },
        "quarterly_fee_revenue": {
            "pk": "fee_id",
            "columns": [
                ("fee_id", "INTEGER", "Row identifier"),
                ("household_id", "INTEGER", "Household"),
                ("fiscal_quarter", "VARCHAR", "Fiscal quarter"),
                ("advisory_fee_usd", "DECIMAL(14,2)", "Advisory fees, USD"),
                ("product_fee_usd", "DECIMAL(14,2)", "Product fees, USD"),
                ("revenue_usd", "DECIMAL(14,2)", "Advisory plus product fees, USD, fiscal quarter"),
            ],
            "fks": [("household_id", "household", "household_id"), ("fiscal_quarter", "fiscal_calendar", "fiscal_quarter")],
        },
        "assets_under_advice": {
            "pk": "aua_id",
            "columns": [
                ("aua_id", "INTEGER", "Row identifier"),
                ("household_id", "INTEGER", "Household"),
                ("as_of", "DATE", "Valuation date, quarter end"),
                ("aua_usd", "DECIMAL(16,2)", "Assets under advice, USD"),
            ],
            "fks": [("household_id", "household", "household_id")],
        },
        "suitability_review": {
            "pk": "review_id",
            "columns": [
                ("review_id", "INTEGER", "Review identifier"),
                ("household_id", "INTEGER", "Household reviewed"),
                ("reviewed_on", "DATE", "Review date"),
                ("outcome_code", "VARCHAR", "SUITABLE, ACTION_REQUIRED, ESCALATED"),
                ("reviewer_id", "INTEGER", "Reviewing advisor"),
            ],
            "fks": [("household_id", "household", "household_id"), ("reviewer_id", "advisor", "advisor_id")],
        },
        "division": {
            "pk": "division_code, valid_from",
            "columns": [
                ("division_code", "VARCHAR", "Division code"),
                ("division_name", "VARCHAR", "Division name"),
                ("valid_from", "DATE", "First day this version of the row holds"),
                ("valid_to", "DATE", "Last day this version of the row holds, null while it is current"),
                ("is_current", "BOOLEAN", "True for the version that holds today"),
            ],
            "fks": [],
        },
        "region": {
            "pk": "region_code, valid_from",
            "columns": [
                ("region_code", "VARCHAR", "Region code"),
                ("region_name", "VARCHAR", "Region name"),
                ("division_code", "VARCHAR", "Division the region rolls up to"),
                ("valid_from", "DATE", "First day this version of the row holds"),
                ("valid_to", "DATE", "Last day this version of the row holds, null while it is current"),
                ("is_current", "BOOLEAN", "True for the version that holds today"),
            ],
            "fks": [],
        },
        "client_segment": {
            "pk": "segment_code",
            "columns": [
                ("segment_code", "VARCHAR", "Segment code"),
                ("segment_name", "VARCHAR", "Segment name"),
                ("parent_segment_code", "VARCHAR", "Parent segment, null at the top level"),
                ("level", "INTEGER", "Level in the segment hierarchy, 1 at the top"),
            ],
            "fks": [],
        },
        "ref_flow_type": {
            "pk": "flow_type_code",
            "columns": [
                ("flow_type_code", "VARCHAR", "Flow type code"),
                ("description", "VARCHAR", "What the flow type covers"),
                ("direction", "VARCHAR", "IN or OUT"),
                ("nna_counts", "BOOLEAN", "True where flows of this type count towards net new assets"),
            ],
            "fks": [],
        },
        "nna_exclusion": {
            "pk": "exclusion_id",
            "columns": [
                ("exclusion_id", "INTEGER", "Exclusion identifier"),
                ("account_id", "INTEGER", "Account excluded, on its source system"),
                ("source_system", "VARCHAR", "Source system of the account"),
                ("reason_code", "VARCHAR", "Why the account is excluded"),
                ("effective_from", "DATE", "First day the exclusion applies"),
                ("effective_to", "DATE", "Last day the exclusion applies, null while it is open"),
            ],
            "fks": [],
        },
        "household_member": {
            "pk": "household_id, member_id",
            "columns": [
                ("household_id", "INTEGER", "Household"),
                ("member_id", "INTEGER", "Member of the household"),
                ("member_role", "VARCHAR", "PRIMARY, JOINT or DEPENDENT"),
            ],
            "fks": [("household_id", "household", "household_id")],
        },
        "aum_month_end": {
            "pk": "household_id, month_end_date",
            "columns": [
                ("household_id", "INTEGER", "Household"),
                ("month_end_date", "DATE", "Month end; a quarter end, when holdings are valued"),
                ("aum_usd", "DECIMAL(18,2)", "Assets under management, USD: the market value of the household's holdings on the date"),
            ],
            "fks": [("household_id", "household", "household_id"), ("month_end_date", "MONTH_DIM_1", "CLDR_EOM_DT")],
        },
    },
}

_meridian = SCHEMAS["wealth"]
_referring = ("nna_exclusion", "aum_month_end")
DOCUMENTED: dict[str, dict] = {**{t: v for t, v in _meridian.items() if t not in _referring}, **sage.schemas(),
                                **{t: _meridian[t] for t in _referring}}
"""Wealth's tables as its certified data dictionary documents them: Meridian's kept tables under their business names, as
the dictionary always named them, and SAGE's under SAGE's; SCHEMAS is the same in the warehouse's physical names."""
SCHEMAS["wealth"] = names.physical_schemas(DOCUMENTED)
DOCUMENTED_KEY: dict[str, str] = {**UNIT_KEY, "wealth": names.business_key(UNIT_KEY["wealth"])}
"""Each unit's customer-like key as its data dictionary names it: the pack's (UNIT_KEY), but Wealth's by its business name."""

CURATED_SCHEMAS: dict[str, dict[str, dict]] = {
    "xref": {
        "client_xref": {
            "pk": "xref_id, valid_from",
            "columns": [
                ("xref_id", "INTEGER", "Cross-reference identifier"),
                ("retail_customer_id", "INTEGER", "Retail customer, as retail.customer.customer_id"),
                ("household_id", "INTEGER", "Wealth household, as wealth.HSHLD_DIM_1.HSHLD_DK"),
                ("member_id", "INTEGER", "The household member the customer is, as wealth.HSHLD_MBR_1.MBR_ID"),
                ("source_system", "VARCHAR", "Wealth source system the match was made against"),
                ("valid_from", "DATE", "First day this version of the match holds"),
                ("valid_to", "DATE", "Last day this version of the match holds, null while it is current"),
                ("is_current", "BOOLEAN", "True for the version that holds today"),
                ("match_method", "VARCHAR", "DETERMINISTIC, PROBABILISTIC or MANUAL"),
                ("match_confidence", "DECIMAL(4,3)", "Confidence in the match, from 0 to 1"),
                ("certification_status", "VARCHAR", "CERTIFIED, PROPOSED or REJECTED: only a CERTIFIED match may be joined on"),
                ("certified_by", "VARCHAR", "Steward who certified the match"),
                ("certified_on", "DATE", "Date the match was certified"),
            ],
            "fks": [],
        },
    },
}

GLOSSARY: list[dict] = _estate["GLOSSARY"]
CERTIFIED_MAPPINGS: list[tuple[str, str, str, str]] = _estate["CERTIFIED_MAPPINGS"]
MEASURE_METADATA: dict[tuple[str, str, str], dict] = _estate["MEASURE_METADATA"]
NO_JOIN_FACTS: list[dict] = _estate["NO_JOIN_FACTS"]
RESTRICTED_COLUMNS: list[tuple[str, str, str]] = _estate["RESTRICTED_COLUMNS"]
PROCESSES: dict[str, str] = _estate["PROCESSES"]
PRINCIPALS: list[dict] = _estate["PRINCIPALS"]
PURPOSES: list[str] = _estate["PURPOSES"]
