"""Documents on two shelves.

Certified: published data dictionaries, approved metric definitions, policies
and standards. Each carries an owner, a version and a publication date, and the
graph may cite them. Each also has a last review date, which upstream AI
certification reads (kge/estate/certify.py) and the document itself never prints.

Uncertified: emails, meeting notes, drafts, spreadsheet exports and an old wiki.
The same pipeline reads them, but nothing found only here becomes a mapping
without a steward, and nothing here is cited as an answer.

Every name, address and number is invented. The handful of planted personal
data items exist so the release gate has something to catch; they are assembled
at run time, so no identifier-shaped string sits in this source file.
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field

from . import spec


@dataclass
class Doc:
    """One document. shelf is certified or uncertified; kind is a data_dictionary, metric_definition, policy, standard,
    email, note, draft, export or wiki; tags are the golden set's planted expectations; reviewed_at, the last review,
    is metadata only and never in the text."""
    shelf: str
    doc_id: str
    title: str
    kind: str
    unit: str | None
    body: str
    owner: str | None = None
    version: str | None = None
    published_at: str | None = None
    superseded_by: str | None = None
    tags: list[str] = field(default_factory=list)
    reviewed_at: str | None = None

    def header(self) -> str:
        lines = [f"# {self.title}", ""]
        meta = [("Shelf", self.shelf), ("Document", self.doc_id), ("Kind", self.kind)]
        if self.unit:
            meta.append(("Business unit", spec.UNIT_LABEL[self.unit]))
        if self.owner:
            meta.append(("Owner", self.owner))
        if self.version:
            meta.append(("Version", self.version))
        if self.published_at:
            meta.append(("Published", self.published_at))
        if self.superseded_by:
            meta.append(("Superseded by", self.superseded_by))
        lines += ["| | |", "|---|---|"] + [f"| {k} | {v} |" for k, v in meta] + [""]
        return "\n".join(lines)

    def text(self) -> str:
        return self.header() + self.body.strip() + "\n"


CROSS_REFERENCED = {"retail", "wealth"}
NO_JOIN_SENTENCE = "It does not join to any other business unit's warehouse."
XREF_SENTENCE = "It joins another unit only by the certified client cross-reference."
EXPANSION_LEAD = {"wealth": ("division", (
    "Expansion tables: `month_end`, `division`, `region`, `office`, `client_segment`, `ref_source_system`, "
    "`ref_flow_type`, `account` and `flow` (SCD2: `valid_from`, `valid_to`, `is_current`), `nna_exclusion`, "
    "`household_member`, `aum_month_end`. Cross-reference: `client_xref`, outside this warehouse, `household_id` to "
    "`customer_id`. Physical names since 1.5.0: the data catalogue's."))}
CLOSING_NOTE = {"retail": (
    f"Transfer legs: `transaction.counterparty_bank` ({spec.CLIENT.upper()}) and `transaction.counterparty_account`. Cross-reference: "
    "`client_xref`, `customer_id` to `household_id`.")}


def joins_sentence(unit: str) -> str:
    """What a unit's dictionary says it joins. The two units the certified client cross-reference links (the #39 Option
    B update) say so where the others say no join exists, in a sentence no longer than the first chunk has room for, so
    every chunk after it keeps its text."""
    return XREF_SENTENCE if unit in CROSS_REFERENCED else NO_JOIN_SENTENCE


def dictionary_body(unit: str, tables: dict, joins: str, lead: tuple[str, str] | None = None, note: str | None = None) -> str:
    """A unit's data dictionary: what its key is and what it joins, then a section per table in the schema's order.
    lead: (table, paragraph), a paragraph before that table's section; note: a paragraph at the end. The key is named
    as the dictionary names it (spec.DOCUMENTED_KEY)."""
    out = [f"Certified data dictionary for the {spec.UNIT_LABEL[unit]} warehouse. "
           f"The customer-like key in this warehouse is `{spec.DOCUMENTED_KEY[unit]}`. "
           f"{joins}", ""]
    for tname, t in tables.items():
        if lead and lead[0] == tname:
            out += [lead[1], ""]
        out.append(f"## {tname}")
        out.append("")
        out.append("| Column | Type | Description |")
        out.append("|---|---|---|")
        for name, typ, desc in t["columns"]:
            out.append(f"| {name} | {typ} | {desc} |")
        if t["fks"]:
            out.append("")
            out.append("Joins: " + "; ".join(
                f"`{c}` references `{rt}.{rc}`" for c, rt, rc in t["fks"]))
        out.append("")
    if note:
        out += [note, ""]
    return "\n".join(out)


def data_dictionaries() -> list[Doc]:
    """One per unit, Wealth's documenting Meridian's tables, and its key, by their business names (spec.DOCUMENTED), as it
    always has: the data catalogue gives each table's physical name (pack 1.5.0). The Wealth expansion's tables
    (#39) are appended to its schema, so their sections follow every section the dictionary had, and the columns
    appended to a table follow its rows. What the expansion adds to a dictionary's text (Wealth's opening paragraph,
    Retail's closing note on its transfer legs) is each long enough to start a chunk of its own, and names rather
    than prose, so no chunk the dictionaries had takes in any of it and a search for anything else finds what it
    found before."""
    docs = []
    for unit, tables in spec.SCHEMAS.items():
        tables = spec.DOCUMENTED if unit == "wealth" else tables
        body = dictionary_body(unit, tables, joins_sentence(unit), EXPANSION_LEAD.get(unit), CLOSING_NOTE.get(unit))
        docs.append(Doc("certified", f"DD-{unit.upper()}", f"{spec.UNIT_LABEL[unit]} Data Dictionary",
                        "data_dictionary", unit, body,
                        owner=f"{spec.UNIT_LABEL[unit]} Data Office", version="3.2", published_at="2025-02-01"))
    return docs


METRIC_NOTES = {
    "Revenue": (
        "Revenue is certified in four warehouses and is **not summable across them** without "
        "Finance's reconciliation. Retail and SME report monthly, in USD, on the calendar year. "
        "Corporate reports quarterly in EUR, converted to USD at the period-end rate, on a fiscal "
        "year starting 1 April. Wealth reports quarterly in USD on the same fiscal year. "
        "An enterprise revenue figure is produced by Group Finance each quarter and nowhere else."
    ),
    "Active Customer": (
        "Version 2, effective 1 January 2025. A customer is active in a calendar month when "
        "`customer_activity.txn_count` (Retail) or `client_activity.payments_count` (SME) is at "
        "least one. Digital logins do not make a customer active. Version 1 counted any login in "
        "the trailing 90 days and is withdrawn."
    ),
    "Assets Under Advice": (
        "Measured at fiscal quarter end from `assets_under_advice.aua_usd`. Includes advisory and "
        "discretionary mandates. Excludes assets held away."
    ),
    "Net Interest Income": (
        "Interest earned less interest paid. Certified in Retail (`monthly_revenue.interest_income`) "
        "and SME (`monthly_client_revenue.nii`), both monthly, USD, calendar."
    ),
    "Fee Income": (
        "Fees and commissions. Certified in Retail (`monthly_revenue.fee_income`) and SME "
        "(`monthly_client_revenue.fees`), both monthly, USD, calendar."
    ),
    "Credit Exposure": (
        "Exposure at default from `exposure.ead_eur`, in EUR, at the measurement date. Not converted."
    ),
    "Delinquency Rate": (
        "Repayments with `days_past_due` above 30 as a share of scheduled repayments in the month. "
        "SME only. Retail lending is out of scope for this metric."
    ),
    "Customer Attrition": (
        "Customers whose last open account closed in the month, divided by customers open at the "
        "start of the month. Retail only."
    ),
    "Average Balance": (
        "Mean of `daily_balance.ledger_balance` over the calendar month, per account, then summed "
        "or averaged as the question requires."
    ),
    "Fiscal Year": (
        "Corporate & Institutional and Wealth Management report on a fiscal year beginning 1 April. "
        "FY26 runs from 1 April 2025 to 31 March 2026. Retail and SME report on the calendar year. "
        "A quarter label without a calendar is ambiguous and must be qualified."
    ),
    "Period-End FX Rate": (
        "The EUR to USD rate on the last day of the fiscal quarter, as recorded in "
        "`quarterly_revenue.fx_rate_period_end`. Average-rate conversion is not used for revenue."
    ),
}


def metric_definitions() -> list[Doc]:
    docs = []
    by_term = {g["term"]: g for g in spec.GLOSSARY}
    for term, note in METRIC_NOTES.items():
        g = by_term[term]
        impls = [m for m in spec.CERTIFIED_MAPPINGS if m[0] == term]
        body = [f"**Definition.** {g['definition']}", "", note, ""]
        if impls:
            body += ["## Certified implementations", "", "| Unit | Table | Column | Grain | Currency | Calendar |", "|---|---|---|---|---|---|"]
            for _, unit, table, col in impls:
                md = spec.MEASURE_METADATA.get((unit, table, col), {})
                body.append(f"| {spec.UNIT_LABEL[unit]} | {table} | {col} | {md.get('grain', 'n/a')} | "
                            f"{md.get('currency', 'n/a')} | {md.get('calendar', 'n/a')} |")
        version = "2.0" if term == "Active Customer" else "1.3"
        docs.append(Doc("certified", f"MD-{term.upper().replace(' ', '-')}", f"Metric Definition: {term}",
                        "metric_definition", None, "\n".join(body), owner=g["steward"], version=version,
                        published_at="2025-01-01" if term == "Active Customer" else "2024-11-15"))
    docs.append(Doc("certified", "MD-ACTIVE-CUSTOMER-V1", "Metric Definition: Active Customer (version 1, withdrawn)",
                    "metric_definition", None,
                    "**Definition (withdrawn).** A customer with at least one digital login in the trailing "
                    "90 days. This version was withdrawn on 31 December 2024 because login-based activity "
                    "overstated the active base by roughly a fifth. Use version 2.",
                    owner="Customer Data Steward", version="1.0", published_at="2023-06-01",
                    superseded_by="MD-ACTIVE-CUSTOMER"))
    return docs


POLICIES = [
    ("POL-DATA-CLASS", "Data Classification Policy", "Chief Data Office",
     "Data is classified as Public, Internal, Confidential or Restricted. Personal data of customers is "
     "Restricted by default. Restricted data may not be embedded, indexed or otherwise vectorised for "
     "retrieval by any assistant. Supervisory review tooling may retrieve Restricted material under a "
     "logged supervision purpose only."),
    ("POL-RETENTION", "Records Retention Policy", "Chief Data Office",
     "Customer communications are retained for seven years. Draft documents on shared drives are not "
     "records and carry no retention guarantee. A document is a record only when published to the "
     "certified shelf with an owner and a version. Retention queries go to records@example.com."),
    ("POL-METRIC-CERT", "Metric Certification Procedure", "Chief Data Office",
     "A column is certified to a glossary term when the term's steward signs the mapping. Certification "
     "is recorded with the steward, the date and the evidence. A proposed mapping, however confident, "
     "is not a certification. Certified mappings are the only ones an assistant may rely on for a "
     "figure in a signed pack."),
    ("POL-ACCESS", "Access Control Standard", "Information Security",
     "Access to a warehouse is granted by business unit role. No role spans two warehouses unless "
     "explicitly listed. The enterprise analyst and finance reporting roles read all four warehouses. "
     "Access is enforced by the platform; any mirror of access held for analysis is not a control."),
    ("POL-MODEL-RISK", "Model Risk Standard for Assistants", "Model Risk Management",
     "An assistant that produces a figure must return the query it ran and the sources it used. It "
     "must not infer a join between warehouses. Where inputs are not commensurable it returns the "
     "parts and the reason. Every answer is logged with model, prompt and tool versions."),
    ("POL-COMPLAINTS", "Complaints Handling Procedure", "Conduct Risk",
     "A complaint is logged on receipt with the customer identifier, category and date. Worked "
     "example from training: customer 118, complaint about a declined card, raised 4 March 2025, "
     "contact by email at m.okonkwo@example.com, date of birth 12 May 1971 confirmed for identity. "
     "Resolution is recorded with the date and outcome."),
    ("STD-FISCAL-CAL", "Fiscal Calendar Standard", "Group Finance",
     "Corporate & Institutional and Wealth Management operate a fiscal year beginning 1 April. "
     "Quarter labels take the form FY26-Q1. Retail and SME operate the calendar year. Reports that "
     "combine units must state which calendar each figure uses."),
    ("STD-FX", "Foreign Exchange Conversion Standard", "Group Finance",
     "Revenue reported in EUR is converted to USD at the period-end rate. Balances are converted at "
     "the spot rate on the measurement date. Average rates are not used for revenue."),
    ("PROC-RECON", "Enterprise Revenue Reconciliation Procedure", "Group Finance",
     "Each quarter Group Finance assembles enterprise revenue from the four unit figures. Retail and "
     "SME monthly figures are summed to the calendar quarter. Corporate and Wealth fiscal quarters "
     "are re-cut to the calendar quarter by month where available and by a stated allocation where "
     "not. Adjustments are documented. The result is the only enterprise revenue figure the bank "
     "publishes, and it is never a plain sum of the four certified columns."),
    ("PROC-STEWARD", "Steward Review Procedure", "Chief Data Office",
     "Proposed mappings and definitions arrive in the steward queue ordered by confidence. A steward "
     "accepts, rejects or edits. An accepted proposal becomes a certified mapping at confidence 1.0 "
     "with a supersedes link to the proposal it replaces. Rejections are recorded with a reason."),
    ("STD-TAGGING", "Document Tagging Standard", "Chief Data Office",
     "Every document chunk is tagged for readiness: citable, retrieval-internal or embed-prohibited. "
     "Embed-prohibited chunks are never sent to an embedding model. Tags carry their generator, "
     "confidence and version. Human corrections supersede automatic tags."),
    ("STD-SUITABILITY", "Suitability Review Standard", "Wealth Compliance",
     "Every household is reviewed for suitability at least annually. Outcomes are SUITABLE, "
     "ACTION_REQUIRED or ESCALATED. Health information disclosed by a client during a review is "
     "Restricted and is recorded only in the compliance system, never in advisor notes."),
]


def policies() -> list[Doc]:
    return [Doc("certified", doc_id, title, "policy" if doc_id.startswith("POL") else "standard",
                None, body, owner=owner, version="2.1", published_at="2024-09-30",
                tags=(["pii_in_certified"] if doc_id == "POL-COMPLAINTS" else []))
            for doc_id, title, owner, body in POLICIES]


FIRST = ["Amara", "Bao", "Chidi", "Dara", "Emeka", "Farah", "Gita", "Hamid", "Ines", "Joris",
         "Kaede", "Lior", "Maren", "Nadia", "Oren", "Priya", "Quang", "Rosa", "Soren", "Tariq"]
LAST = ["Okafor", "Lindqvist", "Marchetti", "Nakamura", "Osei", "Petrov", "Quintero", "Rahman",
        "Sato", "Tanaka", "Ubaldi", "Varga", "Weiss", "Xu", "Yilmaz", "Zamora", "Achebe", "Brandt"]


def _person(rng: random.Random) -> str:
    return f"{rng.choice(FIRST)} {rng.choice(LAST)}"


def _email(rng: random.Random, name: str) -> str:
    first, last = name.lower().split()
    return f"{first[0]}.{last}@example.com"


def _national_id(rng: random.Random) -> str:
    parts = [rng.randint(100, 999), rng.randint(10, 99), rng.randint(1000, 9999)]
    return "".join(str(p) for p in parts)


def _card_number(rng: random.Random) -> str:
    groups = [rng.randint(4000, 4999)] + [rng.randint(1000, 9999) for _ in range(3)]
    return " ".join(str(g) for g in groups)


def uncertified(rng: random.Random) -> list[Doc]:
    docs: list[Doc] = []

    emails = [
        ("EM-001", "Re: active customer numbers for the board pack",
         "From: Product Analytics\nTo: Retail Leadership\n\nQuick one. For the board pack we are counting "
         "an active customer as anyone who logged in during the last 90 days. That gives us 71 percent "
         "active versus 58 percent on the transaction basis. The 90-day login basis is what marketing "
         "has always used, so I propose we keep it.\n\nThanks,\n" + _person(rng),
         ["conflicts:Active Customer"]),
        ("EM-002", "Enterprise revenue, quick total",
         "From: Strategy\nTo: Finance\n\nCan someone confirm total bank revenue for the quarter? I summed "
         "the four revenue columns from the warehouses and got a number, but the corporate one looks "
         "like it is on a different quarter. Is that right or am I reading it wrong?\n\n" + _person(rng),
         ["anti:cross_unit_sum"]),
        ("EM-003", "customer_id join between retail and corporate",
         "From: Data Engineering\nTo: Analytics\n\nSomeone asked whether we can join retail customers to "
         "corporate contacts on customer_id since both tables have the column. Short answer no. The "
         "corporate one is the CRM contact number. The overlap in values is coincidence. Please do not "
         "build anything on it.\n\n" + _person(rng),
         ["supports:no_join"]),
        ("EM-004", "Digital engagement score, draft definition",
         "From: Digital Channels\nTo: Customer Data Steward\n\nWe would like to propose a new metric, "
         "Digital Engagement Score: logins per active customer per month, capped at 30. It is not in the "
         "glossary yet. Can we get it certified?\n\n" + _person(rng),
         ["candidate:Digital Engagement Score"]),
        ("EM-005", "Wealth fee revenue timing",
         "From: Wealth Finance\nTo: Group Finance\n\nReminder that our fee revenue is booked on the fiscal "
         "quarter, so FY26-Q1 is April to June 2025. If you need calendar Q2 that is the same window "
         "this year, but Q1 calendar is FY25-Q4 for us.\n\n" + _person(rng),
         ["supports:fiscal_calendar"]),
        ("EM-006", "Fwd: client identity check",
         "From: Branch Operations\nTo: Fraud Team\n\nForwarding the identity details the customer gave "
         f"over the phone. Name {_person(rng)}, national identity number {_national_id(rng)}, "
         f"contact {_email(rng, _person(rng))}. Please confirm the match.\n\n" + _person(rng),
         ["pii:national_id"]),
    ]
    for i in range(7, 13):
        emails.append((f"EM-{i:03d}", rng.choice([
            "Dashboard refresh timing", "Segment code changes", "Branch region remap",
            "Covenant test dates", "Rating model update", "Holiday coverage"]),
            f"From: {_person(rng)}\nTo: Team\n\nNo action needed, just noting the change discussed on "
            f"the call. Details in the ticket.\n", []))
    for doc_id, title, body, tags in emails:
        docs.append(Doc("uncertified", doc_id, title, "email", None, body, tags=tags))

    notes = [
        ("NOTE-001", "Customer data working group, March 2025",
         "Attendees: stewards, product analytics, digital channels.\n\n1. Active Customer v2 is in force "
         "from January. Marketing decks still using the 90-day login basis to be corrected.\n2. Digital "
         "Engagement Score proposed. Definition: logins per active customer per month, capped at 30. "
         "Steward to review. Not certified.\n3. Retail and corporate customer_id confusion raised again. "
         "Data Architecture confirms no join exists and none is planned.",
         ["candidate:Digital Engagement Score", "supports:no_join"]),
        ("NOTE-002", "Finance close, Q2 2025 calendar",
         "Enterprise revenue assembled per PROC-RECON. Corporate FY26-Q1 aligned to calendar Q2 by month. "
         "Wealth FY26-Q1 aligned likewise. Retail and SME summed from monthly. Adjustments logged.",
         ["supports:reconciliation"]),
        ("NOTE-003", "Wealth suitability review calibration",
         "Reviewers agreed ESCALATED is for cases needing compliance input. Client health disclosures "
         "stay in the compliance system per STD-SUITABILITY.", []),
    ]
    for i in range(4, 9):
        notes.append((f"NOTE-{i:03d}", rng.choice([
            "Weekly data quality stand-up", "Platform change board", "Glossary triage",
            "Access review", "Model risk committee readout"]),
            "Routine. No definitions changed. Actions tracked elsewhere.", []))
    for doc_id, title, body, tags in notes:
        docs.append(Doc("uncertified", doc_id, title, "note", None, body, tags=tags))

    drafts = [
        ("DRAFT-001", "Draft: enterprise revenue summary (do not circulate)",
         "Total revenue across the bank for the quarter is the sum of the four certified revenue columns. "
         "Retail plus SME plus Corporate plus Wealth. Figures pulled straight from the warehouses.\n\n"
         "Reviewer comment: this is wrong, corporate and wealth are fiscal quarters in a different "
         "currency basis. Do not use. See PROC-RECON.",
         ["anti:cross_unit_sum"]),
        ("DRAFT-002", "Draft: active customer definition v3",
         "Proposal to widen Active Customer to include any login or transaction in the month. Not agreed. "
         "Steward has not reviewed.", ["conflicts:Active Customer"]),
        ("DRAFT-003", "Draft: SME delinquency dashboard spec",
         "Delinquency rate as repayments over 30 days past due divided by scheduled repayments, monthly. "
         "Matches MD-DELINQUENCY-RATE.", []),
    ]
    for i in range(4, 9):
        drafts.append((f"DRAFT-{i:03d}", rng.choice([
            "Draft: branch performance pack", "Draft: covenant monitoring note",
            "Draft: rating migration analysis", "Draft: product holding view", "Draft: channel mix"]),
            "Work in progress. Numbers not yet reconciled to certified sources.", []))
    for doc_id, title, body, tags in drafts:
        docs.append(Doc("uncertified", doc_id, title, "draft", None, body, tags=tags))

    exports = [
        ("XLS-001", "Export: card dispute tracker",
         "dispute_ref,card_number,customer_name,amount,status\n" +
         "\n".join(f"D{rng.randint(1000, 9999)},{_card_number(rng)},{_person(rng)},{rng.randint(20, 900)}.00,"
                   f"{rng.choice(['OPEN', 'CLOSED'])}" for _ in range(6)),
         ["pii:card_number"]),
        ("XLS-002", "Export: revenue by unit, manual",
         "unit,period,revenue\nRetail,2025-06,see warehouse\nSME,2025-06,see warehouse\n"
         "Corporate,FY26-Q1,see warehouse\nWealth,FY26-Q1,see warehouse\n\nNote: periods differ. "
         "Do not add these up.", ["supports:not_commensurable"]),
    ]
    for i in range(3, 7):
        exports.append((f"XLS-{i:03d}", rng.choice([
            "Export: branch list", "Export: product catalogue", "Export: RM coverage", "Export: office codes"]),
            "code,name\n" + "\n".join(f"{rng.randint(10, 99)},{rng.choice(LAST)} {rng.choice(['North', 'South', 'Central'])}"
                                       for _ in range(5)), []))
    for doc_id, title, body, tags in exports:
        docs.append(Doc("uncertified", doc_id, title, "export", None, body, tags=tags))

    wiki = [
        ("WIKI-001", "Wiki: joining customers across the bank (2021)",
         "You can join retail customers to corporate contacts on customer_id. Both tables have it.\n\n"
         "Editor note 2024: this is incorrect and has caused at least one wrong report. Left for history.",
         ["anti:key_name_join"]),
        ("WIKI-002", "Wiki: which calendar does each unit use",
         "Retail and SME: calendar year. Corporate and Wealth: fiscal year from April. See STD-FISCAL-CAL.",
         ["supports:fiscal_calendar"]),
        ("WIKI-003", "Wiki: active customers, the old way",
         "Active means logged in within 90 days. (Outdated, see MD-ACTIVE-CUSTOMER.)",
         ["conflicts:Active Customer"]),
    ]
    for i in range(4, 7):
        wiki.append((f"WIKI-{i:03d}", rng.choice(["Wiki: onboarding checklist", "Wiki: report catalogue", "Wiki: team contacts"]),
                     "Historic page. Not maintained.", []))
    for doc_id, title, body, tags in wiki:
        docs.append(Doc("uncertified", doc_id, title, "wiki", None, body, tags=tags))

    return docs


LAST_REVIEW = {"data_dictionary": "2025-06-02", "metric_definition": "2025-05-15", "policy": "2025-06-16", "standard": "2025-06-16"}
REVIEWED = {"STD-FISCAL-CAL": "2024-12-02", "MD-PERIOD-END-FX-RATE": "2024-12-02", "PROC-STEWARD": "2024-11-18",
            "MD-ACTIVE-CUSTOMER-V1": "2024-10-15"}


def all_documents(seed: int = 7) -> list[Doc]:
    """Every document, the certified ones with their last review. Most were reviewed this quarter; three are overdue, so
    AI certification grades them silver rather than gold, and the withdrawn definition was reviewed while in force."""
    rng = random.Random(seed)
    docs = data_dictionaries() + metric_definitions() + policies() + uncertified(rng)
    for d in docs:
        if d.shelf == "certified":
            d.reviewed_at = REVIEWED.get(d.doc_id, LAST_REVIEW[d.kind])
    return docs
