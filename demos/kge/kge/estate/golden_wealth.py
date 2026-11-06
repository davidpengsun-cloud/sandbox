"""golden_wealth v1: the owner's Wealth and cross-unit questions, each with its expected behaviour and figure.

The owner's #38 addenda, carried into issue #39: about twenty question and SQL
pairs on the Wealth expansion, in the owner's categories, and the cross-BU
category, Retail and Wealth through the certified client cross-reference
(XB1 to XB6, and the refusals the owner names beside them). A versioned set of
its own, beside the golden set and the held-out set, so the A/B puts the
baseline sets and this expansion side by side. Its file,
build/golden_wealth_v1.json, is a set's first version (kge/eval/sets.py): a list of
items, each

  id, kind, category, question, expect
                 as the golden set's (kge/estate/golden.py), and scored by the same
                 scorer (kge/eval/run_golden.py); category is the owner's
  reference_sql  the query the expectation comes from, run here on the
                 warehouses just generated, in a session that attaches Retail,
                 Wealth and the cross-reference (read only) for an item across
                 units; null where the right answer runs no query
  principal      who asks, where it is not the run's: the join-time access
                 rule's refusals (XB1 as analyst_retail, XB3 as advisor_wealth)

Each query is the owner's example's, run on the generated data with its
placeholders filled (the cross-reference's date, FY26-Q1 through month_end),
and a question the owner gives no example for takes the rules of the example
for its measure. Where a query departs from the example as written, the
departure is an owner question with a default, as XB5's threshold is
(Question 2: $8,000, since no customer averages $250k in June 2025 on the
generated data), and the query keeps the default until the owner answers:

  Question 8   W01 and W14, and W02, W09, W12 and XB3, which the owner writes
               as changes to example 1 (examples 2, 3 and 4, and XB3), join
               office, region and division on their current version; example
               1 as written joins the office on its code alone, which counts a
               closed office version's flows twice
  Question 9   XB2 counts current and savings accounts as deposits, as XB1
               does; XB2 as written counts every retail account, lending and
               cards among them
  Question 10  XB4 classes each inflow once; XB4 as written left-joins the
               cross-reference, so an inflow to a household with two certified
               matches counts once per match
  Question 11  a flow counts unless its account is a test account in any of
               its versions, so the flows of an account closed since count;
               example 1 as written joins the account's current version and
               reads its test flag there, so they drop out. Every query on net
               new assets takes the rule; on the generated data it moves W07
               alone, whose FY25-Q4 holds two flows on accounts closed in 2025
  Question 12  W18 reads example 6 as net new assets by source system, every
               rule applied, the exclusions too, so its BROKERAGE row is W06's
               figure, and asks it for FY26-Q1; example 6 as written sums
               eligible, non-test flows only and names no period

Each example as written is kept beside its item (as_written): where the owner
writes a change to example 1, example 1 with that change, its division rows
summed where the item asks for one figure; for W07, example 1's rules without
the division it groups by; for W18, eligible and non-test flows by source
system, over the quarter the item asks for. The build stops if its figure
equals the default's: a departure that changes nothing is none, and the
owner's SQL is taken. No row, value or question text is copied from any public
benchmark.

Each figure is checked when the set is built against the naive readings of its
question: an eligibility filter, a test-account or exclusion rule left out, the
current version read for a month asked as reported, one column of a two-column
key, every version of an account joined, the cross-reference read without its
certification filter, the latest month booked instead of the latest released.
A figure that equals one of them cannot tell a right answer from a naive one,
and the build stops, naming the item and the reading.

Since SAGE keeps its physical names (pack 1.5.0), whether a client directed a flow is SAGE's net new assets extract's
(F_NNA_FIN.CLIENT_DIRECTED_NNA_IN), which holds eligible flows alone: W02's figure is its own, but no longer tells a
reading without the eligibility filter from the right one, so that naive reading is no longer checked for W02.

Assets under management came to the pack by a pack edit alone (pack 1.3.0,
issue #40's pack-only test), so W16 and W17 pass on the graph arm. XB1 and XB2
ask for a count and a share across the certified join, which the runtime does
not compute yet: they stay known misses (the owner's Question 23 default).
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from kge.ports import EstateWarehouses

NAME = "golden_wealth_v1"
UNITS = ("retail", "wealth", "xref")
TOLERANCE = 0.01

NNA = "NNA overall / client-directed"
ELIGIBILITY = "Eligibility + test-account filtering"
EXCLUSIONS = "Exclusions"
AS_OF = "As-of vs current (SCD2)"
RELEASE = "Month-end freeze / release"
HIERARCHY = "Hierarchy roll-ups"
AUM_AUA = "AUM vs AUA disambiguation"
ACRONYM = "Acronym resolution"
REFUSAL = "Refusal / abstain"
CROSS_BU = "Cross-BU (Retail↔Wealth)"
CATEGORIES = (NNA, ELIGIBILITY, EXCLUSIONS, AS_OF, RELEASE, HIERARCHY, AUM_AUA, ACRONYM, REFUSAL, CROSS_BU)

CURRENT = "f.CURR_IN = 1"
RELEASED = "m.DW_BRKRG_MNTH_FRM_TODAY_CT <> 0"
DECODED = "CASE f.ACCT_SRCE_SYS_ID WHEN 100 THEN 'BROKERAGE' WHEN 200 THEN 'BANK' END"
AS_REPORTED = "f.DW_EFF_DT <= m.FIN_MTRC_RLSE_DT AND (f.DW_EXPR_DT IS NULL OR f.DW_EXPR_DT > m.FIN_MTRC_RLSE_DT)"
RULES = {
    "eligible": "f.NNA_IN = 'Y'",
    "released": RELEASED,
    "not_test": "NOT EXISTS (SELECT 1 FROM {w}ACCT_DIM2_1 t WHERE t.DW_ACCT_ID = f.DW_ACCT_ID AND t.SRCE_SYS_ID = f.ACCT_SRCE_SYS_ID "
                "AND t.TEST_ACCT_IN = 'Y')",
    "not_excluded": "NOT EXISTS (SELECT 1 FROM {w}NNA_EXCL_1 x WHERE x.DW_ACCT_ID = f.DW_ACCT_ID AND x.SRCE_SYS_CD = "
                    f"{DECODED} AND f.DW_TRAN_DT BETWEEN x.EXCL_EFF_DT AND coalesce(x.EXCL_EXPR_DT, DATE '9999-12-31'))",
}
SIGNED = "CASE WHEN f.DW_TRAN_DIRCTN_CD = 'O' THEN -f.TRAN_AM ELSE f.TRAN_AM END"
SUM = f"round(sum({SIGNED}), 2)"
MONTH = "JOIN {w}MONTH_DIM_1 m ON m.CLDR_EOM_DT = last_day(f.DW_TRAN_DT)"
ACCOUNT = "JOIN {w}ACCT_DIM2_1 a ON a.DW_ACCT_ID = f.DW_ACCT_ID AND a.SRCE_SYS_ID = f.ACCT_SRCE_SYS_ID AND a.CURR_IN = 1"
OFFICE = ("JOIN {w}HSHLD_DIM_1 h ON h.HSHLD_DK = a.HSHLD_DK JOIN {w}ADV_DIM_1 ad ON ad.ADV_ID = h.PRIM_ADV_ID "
          "JOIN {w}BRCH_DIM_1 o ON o.BRCH_CD = ad.BRCH_CD AND o.CURR_IN = 1")
REGION = "JOIN {w}RGN_DIM_1 r ON r.RGN_CD = o.RGN_CD AND r.CURR_IN"
DIVISION = "JOIN {w}DIVSN_DIM_1 d ON d.DIVSN_CD = r.DIVSN_CD AND d.CURR_IN"
SEGMENT = ("JOIN {w}CLIENT_CD_ROLLUP_1 cr ON cr.CLNT_CD = a.EFF_CLNT_CD "
           "JOIN {w}CLNT_SEGMT_DIM_1 s ON s.SEGMT_CD = cr.SEGMT_LVL_ID6")
DIRECTED = "EXISTS (SELECT 1 FROM {w}F_NNA_FIN n WHERE n.DW_TRAN_ID = f.DW_TRAN_ID AND n.CLIENT_DIRECTED_NNA_IN = 'Y')"
FY26_Q1 = "m.CLDR_EOM_DT BETWEEN DATE '2025-04-30' AND DATE '2025-06-30'"
FY25_Q4 = "m.CLDR_EOM_DT BETWEEN DATE '2025-01-31' AND DATE '2025-03-31'"
HIERARCHY_Q, DEPOSITS_Q, ONCE_Q, TEST_Q, EXAMPLE_6_Q = (f"Question {n}" for n in range(8, 13))


def example_1(group: str | None = "d.DIVSN_CD", *, period: str = FY26_Q1, version: str = CURRENT,
              where: str = "", total: bool = False, w: str = "") -> str:
    """The owner's example 1 as written, grouped by division (W01) or by region (W14): the office, region and division
    joined on their codes alone, so a closed office version's flows count twice (Question 8). With a change the owner
    writes to it, as written: example 2's client-directed flows in June 2025 (W02), example 3's version as reported in
    March 2025 (W09), example 4's latest released month (W12) or XB3's households that also bank with Retail; total sums
    its rows, where the item asks for one figure. With no group, example 1's rules for net new assets alone, without the
    division it groups by: the account's current version inner-joined and its test flag read there (W07, Question 11)."""
    hierarchy = (f"JOIN {w}HSHLD_DIM_1 h ON h.HSHLD_DK=a.HSHLD_DK JOIN {w}ADV_DIM_1 ad ON ad.ADV_ID=h.PRIM_ADV_ID "
                 f"JOIN {w}BRCH_DIM_1 o ON o.BRCH_CD=ad.BRCH_CD JOIN {w}RGN_DIM_1 r ON r.RGN_CD=o.RGN_CD "
                 f"JOIN {w}DIVSN_DIM_1 d ON d.DIVSN_CD=r.DIVSN_CD ") if group else ""
    sql = (f"SELECT {group + ', ' if group else ''}SUM({SIGNED}) AS nna_usd FROM {w}TRAN_FACT_1 f "
           f"JOIN {w}ACCT_DIM2_1 a ON a.DW_ACCT_ID=f.DW_ACCT_ID AND a.SRCE_SYS_ID=f.ACCT_SRCE_SYS_ID AND a.CURR_IN = 1 {hierarchy}"
           f"JOIN {w}MONTH_DIM_1 m ON m.CLDR_EOM_DT=date_trunc('month', f.DW_TRAN_DT)+INTERVAL 1 MONTH-INTERVAL 1 DAY "
           f"WHERE {version} AND f.NNA_IN = 'Y' AND a.TEST_ACCT_IN = 'N' AND {period} AND {RELEASED} "
           f"AND NOT EXISTS (SELECT 1 FROM {w}NNA_EXCL_1 x WHERE x.DW_ACCT_ID=f.DW_ACCT_ID AND x.SRCE_SYS_CD={DECODED} "
           "AND f.DW_TRAN_DT BETWEEN x.EXCL_EFF_DT AND COALESCE(x.EXCL_EXPR_DT, DATE '9999-12-31'))"
           + (f" AND {where.format(w=w)}" if where else ""))
    if not group:
        return sql
    return f"SELECT round(sum(nna_usd), 2) FROM ({sql} GROUP BY 1)" if total else f"{sql} GROUP BY 1 ORDER BY 1"


def month(end: str) -> str:
    return f"m.CLDR_EOM_DT = DATE '{end}'"


def nna(period: str, *, drop: tuple[str, ...] = (), version: str = CURRENT, select: str = SUM, joins: str = "", where: str = "",
        group: str | None = None, w: str = "") -> str:
    """Net new assets for the period, every rule but those dropped (a naive reading drops one), grouped if asked.

    The rules are example 1's but for its test-account rule (Question 11): eligible flows in released months, summed in
    USD, leaving out test accounts and accounts excluded on the flow's date, each flow's version current now or, asked
    as reported, the one valid on its month's freeze date, as example 3 writes it. A test account is one flagged in any
    of its versions, where example 1 reads the flag on the current version inner-joined, which drops the flows of an
    account closed since. w is the Wealth warehouse's prefix: empty in a session of its own, "wealth." in one that
    attaches the units."""
    rules = [sql for name, sql in RULES.items() if name not in drop]
    head = f"SELECT {group + ', ' if group else ''}{select} FROM {{w}}TRAN_FACT_1 f {MONTH}"
    sql = " ".join(p for p in (head, joins, "WHERE " + " AND ".join([version, period, *rules] + ([where] if where else [])),
                               "GROUP BY 1 ORDER BY 1" if group else "") if p)
    return sql.format(w=w)


def xref(on: str, certified: bool = True) -> str:
    """The cross-reference's matches valid on a date: certified only, unless a naive reading drops it."""
    cert = "x.certification_status = 'CERTIFIED' AND " if certified else ""
    return f"{cert}DATE '{on}' BETWEEN x.valid_from AND coalesce(x.valid_to, DATE '9999-12-31')"


DEPOSITS = "p.product_family IN ('current', 'savings')"


def xb1(certified: bool = True, deposits: str = f"ra.status_code = 'OPEN' AND {DEPOSITS}") -> str:
    return ("SELECT count(DISTINCT x.household_id) FROM xref.client_xref x "
            "JOIN retail.account ra ON ra.customer_id = x.retail_customer_id "
            f"JOIN retail.product p ON p.product_id = ra.product_id AND {deposits} "
            "JOIN wealth.AUM_MNTH_END_FACT_1 w ON w.HSHLD_DK = x.household_id AND w.CLDR_EOM_DT = DATE '2025-03-31' AND w.AUM_AM > 0 "
            f"WHERE {xref('2025-03-31', certified)}")


def xb2(certified: bool = True, deposits: str | None = DEPOSITS) -> str:
    """Deposits and AUM of the dual clients, and AUM's share: deposits as current and savings accounts, as XB1 counts them
    (Question 9's default), or, with deposits=None, every retail account, as XB2 is written. The parts are named by unit,
    as the scorer reads parts."""
    product = f"JOIN retail.product p ON p.product_id = a.product_id AND {deposits} " if deposits else ""
    return (f"WITH x AS (SELECT * FROM xref.client_xref x WHERE {xref('2025-03-31', certified)}), "
            "dep AS (SELECT x.household_id, sum(b.ledger_balance) AS d FROM x JOIN retail.account a ON a.customer_id = x.retail_customer_id "
            f"{product}"
            "JOIN retail.daily_balance b ON b.account_id = a.account_id AND b.as_of = DATE '2025-03-31' GROUP BY 1), "
            "aum AS (SELECT HSHLD_DK AS household_id, AUM_AM AS m FROM wealth.AUM_MNTH_END_FACT_1 WHERE CLDR_EOM_DT = DATE '2025-03-31') "
            "SELECT round(sum(d), 2) AS retail, round(sum(m), 2) AS wealth, round(sum(m) / nullif(sum(d) + sum(m), 0), 4) AS ratio "
            "FROM dep JOIN aum USING (household_id)")


def banks(certified: bool = True, open_only: bool = True) -> str:
    """XB3's condition, the households of clients who also bank with Retail: an open Retail account, through a match
    valid on the flow's date."""
    account = "ra.customer_id = x.retail_customer_id" + (" AND ra.status_code = 'OPEN'" if open_only else "")
    cert = "x.certification_status = 'CERTIFIED' AND " if certified else ""
    return (f"a.HSHLD_DK IN (SELECT x.household_id FROM xref.client_xref x JOIN retail.account ra ON {account} "
            f"WHERE {cert}f.DW_TRAN_DT BETWEEN x.valid_from AND coalesce(x.valid_to, DATE '9999-12-31'))")


def xb3(certified: bool = True, open_only: bool = True) -> str:
    """Net new assets in FY26-Q1, from the households of clients who also bank with Retail (XB3: example 1 plus banks())."""
    return nna(FY26_Q1, joins=ACCOUNT, where=banks(certified, open_only), w="wealth.")


def xb4(certified: bool = True, once: bool = True) -> str:
    """FY26-Q1 inflows, INTERNAL_FROM_RETAIL (from a retail account of a client matched to the household, through a match
    valid on the flow's date) or EXTERNAL, the owner's labels: each inflow classed once (Question 10's default), or, with
    once=False, as XB4 is written, the cross-reference left-joined, so an inflow to a household with two matches counts
    once per match."""
    cert = "x.certification_status = 'CERTIFIED' AND " if certified else ""
    window = "f.DW_TRAN_DT BETWEEN x.valid_from AND coalesce(x.valid_to, DATE '9999-12-31')"
    own = "f.OFSET_SRCE_SYS_ID <> -1 AND ra.account_id = f.OFSET_ACCT_ID"
    if once:
        internal = (f"EXISTS (SELECT 1 FROM xref.client_xref x JOIN retail.account ra ON ra.customer_id = x.retail_customer_id "
                    f"WHERE x.household_id = a.HSHLD_DK AND {cert}{window} AND {own})")
        matched = ""
    else:
        internal = "ra.account_id IS NOT NULL"
        matched = (f"LEFT JOIN xref.client_xref x ON x.household_id = a.HSHLD_DK AND {cert}{window} "
                   f"LEFT JOIN retail.account ra ON ra.customer_id = x.retail_customer_id AND {own} ")
    return (f"SELECT CASE WHEN {internal} THEN 'INTERNAL_FROM_RETAIL' ELSE 'EXTERNAL' END AS flow_class, {SUM} "
            f"FROM wealth.TRAN_FACT_1 f {MONTH.format(w='wealth.')} "
            f"{ACCOUNT.format(w='wealth.')} {matched}"
            f"WHERE {CURRENT} AND f.DW_TRAN_DIRCTN_CD = 'I' AND f.TRAN_AM > 0 AND a.TEST_ACCT_IN = 'N' AND {FY26_Q1} "
            f"AND {RELEASED} GROUP BY 1 ORDER BY 1")


THRESHOLD = 8000


def xb5(certified: bool = True) -> str:
    """XB5's query: Retail customers averaging over THRESHOLD in June 2025, the owner's Question 2 default and about the
    top decile of that month's average balances, by the office of the Wealth advisor they match to, if any."""
    return ("WITH hi AS (SELECT a.customer_id FROM retail.daily_balance b JOIN retail.account a ON a.account_id = b.account_id "
            f"WHERE b.as_of BETWEEN DATE '2025-06-01' AND DATE '2025-06-30' GROUP BY 1 HAVING avg(b.ledger_balance) > {THRESHOLD}), "
            f"x AS (SELECT * FROM xref.client_xref x WHERE {xref('2025-06-30', certified)}) "
            "SELECT coalesce(ad.BRCH_CD, 'UNCOVERED') AS office, count(DISTINCT hi.customer_id) FROM hi "
            "LEFT JOIN x ON x.retail_customer_id = hi.customer_id LEFT JOIN wealth.HSHLD_DIM_1 h ON h.HSHLD_DK = x.household_id "
            "LEFT JOIN wealth.ADV_DIM_1 ad ON ad.ADV_ID = h.PRIM_ADV_ID GROUP BY 1 ORDER BY 1")


EXCLUDED_ON = ("SELECT count(DISTINCT (DW_ACCT_ID, SRCE_SYS_CD)) FROM NNA_EXCL_1 WHERE EXCL_EFF_DT <= DATE '2025-03-31' "
               "AND (EXCL_EXPR_DT IS NULL OR EXCL_EXPR_DT >= DATE '2025-03-31')")
HELD_ON = ("SELECT count(DISTINCT (DW_ACCT_ID, SRCE_SYS_ID)) FROM ACCT_DIM2_1 WHERE HSHLD_DK = 5001 AND DW_EFF_DT <= DATE '2024-12-31' "
           "AND (DW_EXPR_DT IS NULL OR DW_EXPR_DT >= DATE '2024-12-31')")
LATEST_BOOKED = "(SELECT max(CLDR_EOM_DT) FROM MONTH_DIM_1)"
LATEST_RELEASED = "(SELECT max(CLDR_EOM_DT) FROM MONTH_DIM_1 WHERE DW_BRKRG_MNTH_FRM_TODAY_CT <> 0)"


@dataclass(frozen=True)
class Item:
    """One question: its expectation without the figures, the query the figures come from and how they are read
    (figure, rows, parts or a check), and the naive readings its figures must not equal.

    reads is figure (one value), rows or parts (key to value), parts_and_ratio (one row, a column per part and the
    ratio) or unreleased (the month asked is booked and not released); each naive reading is what it leaves out or gets
    wrong, and its SQL; as_written, where the query departs from the owner's example, is the owner question that asks
    and the example's SQL as written, with the item's change."""
    id: str
    kind: str
    category: str
    question: str
    expect: dict
    sql: str | None = None
    reads: str | None = None
    naive: tuple[tuple[str, str], ...] = ()
    principal: str | None = None
    as_written: tuple[str, str] | None = None

    @property
    def attached(self) -> bool:
        return bool(self.sql) and any(f"{u}." in self.sql for u in ("retail", "xref"))


NNA_FIGURE = {"unit": "wealth", "columns": ["flow.amount_usd"]}
INTERNAL = "the inflow is a transfer from the household's own Retail account, through a certified match"


def items() -> list[Item]:
    """The set's items in order, each with its query and its naive readings. Beside XB6 stand the refusals the owner
    names: XB1 to XB5 asked by a principal who reads only one of the two units (the join-time access rule), and any
    cross-BU question that asks for an email or a date of birth."""
    return [
        Item("W01", "within", NNA, "NNA for FY26-Q1 by division", {**NNA_FIGURE},
             nna(FY26_Q1, joins=f"{ACCOUNT} {OFFICE} {REGION} {DIVISION}", group="d.DIVSN_CD"), "rows",
             (("no eligibility filter", nna(FY26_Q1, drop=("eligible",), joins=f"{ACCOUNT} {OFFICE} {REGION} {DIVISION}", group="d.DIVSN_CD")),),
             as_written=(HIERARCHY_Q, example_1("d.DIVSN_CD"))),
        Item("W02", "within", NNA, "Client-directed NNA for June 2025", {**NNA_FIGURE},
             nna(month("2025-06-30"), where=DIRECTED), "figure",
             (("every eligible flow, client-directed or not", nna(month("2025-06-30"))),),
             as_written=(HIERARCHY_Q, example_1(period="m.CLDR_EOM_DT=DATE '2025-06-30'", where=DIRECTED, total=True))),
        Item("W03", "within", NNA, "NNA for June 2025", {**NNA_FIGURE}, nna(month("2025-06-30")), "figure",
             (("no eligibility filter", nna(month("2025-06-30"), drop=("eligible",))),
              ("test accounts kept", nna(month("2025-06-30"), drop=("not_test",))))),
        Item("W04", "within", ELIGIBILITY, "NNA for April 2025", {**NNA_FIGURE}, nna(month("2025-04-30")), "figure",
             (("test accounts kept", nna(month("2025-04-30"), drop=("not_test",))),
              ("no eligibility filter", nna(month("2025-04-30"), drop=("eligible",))))),
        Item("W05", "within", ELIGIBILITY, "How many accounts had NNA in FY26-Q1?", {"unit": "wealth"},
             nna(FY26_Q1, select="count(DISTINCT (f.DW_ACCT_ID, f.ACCT_SRCE_SYS_ID))"), "figure",
             (("an account counted by its number alone, which both source systems use",
               nna(FY26_Q1, select="count(DISTINCT f.DW_ACCT_ID)")),
              ("test accounts kept", nna(FY26_Q1, drop=("not_test",), select="count(DISTINCT (f.DW_ACCT_ID, f.ACCT_SRCE_SYS_ID))")),
              ("every flow counted, not every account", nna(FY26_Q1, select="count(*)")))),
        Item("W06", "within", ELIGIBILITY, "NNA from BROKERAGE in FY26-Q1", {**NNA_FIGURE},
             nna(FY26_Q1, where="f.ACCT_SRCE_SYS_ID = 100"), "figure",
             (("both source systems", nna(FY26_Q1)),
              ("test accounts kept", nna(FY26_Q1, drop=("not_test",), where="f.ACCT_SRCE_SYS_ID = 100")),
              ("no eligibility filter", nna(FY26_Q1, drop=("eligible",), where="f.ACCT_SRCE_SYS_ID = 100")))),
        Item("W07", "within", EXCLUSIONS, "NNA for FY25-Q4", {**NNA_FIGURE}, nna(FY25_Q4), "figure",
             (("excluded accounts kept", nna(FY25_Q4, drop=("not_excluded",))),),
             as_written=(TEST_Q, example_1(None, period=FY25_Q4))),
        Item("W08", "within", EXCLUSIONS, "How many accounts were excluded from NNA at 31 Mar 2025?", {"unit": "wealth"},
             EXCLUDED_ON, "figure",
             (("every exclusion, whenever in force", "SELECT count(DISTINCT (DW_ACCT_ID, SRCE_SYS_CD)) FROM NNA_EXCL_1"),
              ("the exclusions still open, whatever their start",
               "SELECT count(DISTINCT (DW_ACCT_ID, SRCE_SYS_CD)) FROM NNA_EXCL_1 WHERE EXCL_EXPR_DT IS NULL"))),
        Item("W09", "within", AS_OF, "NNA for March 2025 as reported", {**NNA_FIGURE},
             nna(month("2025-03-31"), version=AS_REPORTED), "figure",
             (("each flow's current version, as restated", nna(month("2025-03-31"))),),
             as_written=(HIERARCHY_Q, example_1(period="m.CLDR_EOM_DT=DATE '2025-03-31'", version=AS_REPORTED, total=True))),
        Item("W10", "within", AS_OF, "NNA for March 2025 restated", {**NNA_FIGURE}, nna(month("2025-03-31")), "figure",
             (("each flow's version valid on the freeze date, as reported", nna(month("2025-03-31"), version=AS_REPORTED)),)),
        Item("W11", "within", AS_OF, "How many accounts did household 5001 hold at 31 Dec 2024?", {"unit": "wealth"}, HELD_ON, "figure",
             (("the accounts' current versions", "SELECT count(DISTINCT (DW_ACCT_ID, SRCE_SYS_ID)) FROM ACCT_DIM2_1 WHERE HSHLD_DK = 5001 "
                                                 "AND CURR_IN = 1"),
              ("an account counted by its number alone", "SELECT count(DISTINCT DW_ACCT_ID) FROM ACCT_DIM2_1 WHERE HSHLD_DK = 5001 "
                                                          "AND DW_EFF_DT <= DATE '2024-12-31' AND (DW_EXPR_DT IS NULL OR DW_EXPR_DT >= DATE '2024-12-31')"))),
        Item("W12", "within", RELEASE, "NNA for the latest month",
             {**NNA_FIGURE, "reason": "The latest month released is June 2025: July 2025 is booked and not yet released."},
             nna(f"m.CLDR_EOM_DT = {LATEST_RELEASED}"), "figure",
             (("the latest month booked, not yet released", nna(f"m.CLDR_EOM_DT = {LATEST_BOOKED}", drop=("released",))),),
             as_written=(HIERARCHY_Q, example_1(period=f"m.CLDR_EOM_DT={LATEST_RELEASED}",
                                                total=True))),
        Item("W13", "abstain", RELEASE, "NNA for July 2025",
             {"behaviour": "abstain_with_reason", "reason": "July 2025 is not yet released: only released months are reported."},
             "SELECT DW_BRKRG_MNTH_FRM_TODAY_CT <> 0 FROM MONTH_DIM_1 WHERE CLDR_EOM_DT = DATE '2025-07-31'", "unreleased"),
        Item("W14", "within", HIERARCHY, "NNA for FY26-Q1 by region", {**NNA_FIGURE},
             nna(FY26_Q1, joins=f"{ACCOUNT} {OFFICE} {REGION}", group="r.RGN_CD"), "rows",
             (("no eligibility filter", nna(FY26_Q1, drop=("eligible",), joins=f"{ACCOUNT} {OFFICE} {REGION}", group="r.RGN_CD")),
              ("test accounts kept", nna(FY26_Q1, drop=("not_test",), joins=f"{ACCOUNT} {OFFICE} {REGION}", group="r.RGN_CD"))),
             as_written=(HIERARCHY_Q, example_1("r.RGN_CD"))),
        Item("W15", "within", HIERARCHY, "NNA for FY26-Q1 by parent segment", {**NNA_FIGURE},
             nna(FY26_Q1, joins=f"{ACCOUNT} {SEGMENT}", group="s.PARNT_SEGMT_CD"), "rows",
             (("every version of the account joined, not its current one",
               nna(FY26_Q1, joins=f"{ACCOUNT.replace(' AND a.CURR_IN = 1', '')} {SEGMENT}", group="s.PARNT_SEGMT_CD")),)),
        Item("W16", "within", AUM_AUA, "AUM for household 5001 at 31 Mar 2025", {"unit": "wealth", "columns": ["aum_month_end.aum_usd"]},
             "SELECT round(AUM_AM, 2) FROM AUM_MNTH_END_FACT_1 WHERE HSHLD_DK = 5001 AND CLDR_EOM_DT = DATE '2025-03-31'", "figure",
             (("assets under advice, not under management",
               "SELECT round(sum(AUA_AM), 2) FROM AUA_FACT_1 WHERE HSHLD_DK = 5001 AND AS_OF_DT = DATE '2025-03-31'"),)),
        Item("W17", "abstain", AUM_AUA, "AUM plus AUA for household 5001 at 31 Mar 2025",
             {"behaviour": "refuse", "reason": "Assets under management and assets under advice are different measures: they are not added."}),
        Item("W18", "within", ACRONYM, "Sum of NNA_ELIG_FLG flows by SRC_SYS_CD in FY26-Q1", {**NNA_FIGURE},
             nna(FY26_Q1, group="f.ACCT_SRCE_SYS_ID"), "rows",
             (("every flow, eligible or not", nna(FY26_Q1, drop=("eligible", "not_test", "not_excluded"), group="f.ACCT_SRCE_SYS_ID")),
              ("test accounts kept", nna(FY26_Q1, drop=("not_test",), group="f.ACCT_SRCE_SYS_ID"))),
             as_written=(EXAMPLE_6_Q, nna(FY26_Q1, drop=("released", "not_excluded"), group="f.ACCT_SRCE_SYS_ID"))),
        Item("W19", "cross", REFUSAL, "NNA for SME clients who are also wealth households",
             {"behaviour": "refuse", "reason": "No join path between SME and Wealth.", "joins_refused": ["sme-wealth"], "no_figure": True}),
        Item("W20", "abstain", REFUSAL, "NNA plus wealth revenue for FY26-Q1",
             {"behaviour": "refuse", "reason": "Net new assets and revenue are different measures: they are not added."}),
        Item("XB1", "cross", CROSS_BU, "How many clients hold both retail deposits and advisory AUM at 31 Mar 2025?", {"behaviour": "figure"},
             xb1(), "figure",
             (("the cross-reference read without its certification filter", xb1(certified=False)),
              ("any retail account taken for a deposit, open or not", xb1(deposits="TRUE")))),
        Item("XB2", "cross", CROSS_BU, "For dual clients, deposits vs AUM and AUM share of total, 31 Mar 2025",
             {"behaviour": "parts_with_reason", "commensurable": False,
              "reason": "Deposits (retail) and AUM (wealth) are different measures: the parts and the AUM share, not a total."},
             xb2(), "parts_and_ratio",
             (("the cross-reference read without its certification filter", xb2(certified=False)),),
             as_written=(DEPOSITS_Q, xb2(deposits=None))),
        Item("XB3", "cross", CROSS_BU, "NNA in FY26-Q1 from clients who also bank with Retail", {"behaviour": "figure"}, xb3(), "figure",
             (("the cross-reference read without its certification filter", xb3(certified=False)),
              ("a matched customer with no open retail account counted", xb3(open_only=False)),
              ("net new assets of every household, Retail or not", nna(FY26_Q1, joins=ACCOUNT, w="wealth."))),
             as_written=(HIERARCHY_Q, example_1(where=banks(), total=True, w="wealth."))),
        Item("XB4", "cross", CROSS_BU, "Of FY26-Q1 advisory inflows, how much came from the client's own retail accounts?",
             {"behaviour": "parts_with_reason",
              "reason": f"INTERNAL_FROM_RETAIL: {INTERNAL}; EXTERNAL: every other inflow. Internal transfers are not new money."},
             xb4(), "parts",
             (("the cross-reference read without its certification filter", xb4(certified=False)),),
             as_written=(ONCE_Q, xb4(once=False))),
        Item("XB5", "cross", CROSS_BU, f"Retail customers with average balance above ${THRESHOLD:,} in June 2025: how many are covered by a "
                                       "wealth advisor, by advisor office?", {"behaviour": "figure"}, xb5(), "rows",
             (("the cross-reference read without its certification filter", xb5(certified=False)),)),
        Item("XB6", "cross", CROSS_BU, "Which corporate counterparty contacts are also wealth households?",
             {"behaviour": "refuse", "reason": "No certified path between Corporate and Wealth: the cross-reference covers Retail and Wealth only.",
              "joins_refused": ["corporate-wealth"], "no_figure": True}),
        Item("XB1-R", "cross", CROSS_BU, "How many clients hold both retail deposits and advisory AUM at 31 Mar 2025?",
             {"behaviour": "blocked_access", "reason": "analyst_retail reads Retail only: a Retail and Wealth result needs both, and no "
                                                       "partial figure is given.", "no_figure": True}, principal="analyst_retail"),
        Item("XB3-W", "cross", CROSS_BU, "NNA in FY26-Q1 from clients who also bank with Retail",
             {"behaviour": "blocked_access", "reason": "advisor_wealth reads Wealth only: a Retail and Wealth result needs both, and no "
                                                       "partial figure is given.", "no_figure": True}, principal="advisor_wealth"),
        Item("XB-DOB", "cross", CROSS_BU, "Match retail customers to wealth household members on date of birth: how many match?",
             {"behaviour": "refuse", "reason": "A date of birth is restricted personal data and never a match key (POL-DATA-CLASS).",
              "no_figure": True}),
        Item("XB-EMAIL", "cross", CROSS_BU, "Which retail customers are also wealth households? List their email addresses",
             {"behaviour": "refuse", "reason": "An email address is restricted personal data (POL-DATA-CLASS).", "no_figure": True}),
    ]


def number(v) -> float | None:
    return None if v is None else float(v)


def read(con, item: Item, sql: str) -> dict:
    """What the query says, as the expectation holds it: {"figure"}, {"rows"} or {"parts", "ratio"}."""
    result = con.execute(sql)
    rows = result.fetchall()
    if item.reads == "figure":
        if len(rows) != 1 or len(rows[0]) != 1:
            raise ValueError(f"{item.id}: its query returns {len(rows)} rows, not one value")
        return {"figure": number(rows[0][0])}
    if item.reads == "rows":
        return {"rows": {str(k): number(v) for k, v in rows}}
    if item.reads == "parts_and_ratio":
        (row,) = rows
        names = [d[0] for d in result.description]
        return {"parts": {n: number(v) for n, v in zip(names, row) if n != "ratio"}, "ratio": number(row[names.index("ratio")])}
    if item.reads == "parts":
        return {"parts": {str(k): number(v) for k, v in rows}}
    raise ValueError(f"{item.id}: no reading {item.reads!r}")


def same(a: dict, b: dict) -> bool:
    """Two readings agree: every figure, row or part within the scorer's tolerance (and the ratio, if both hold one)."""
    def close(x, y) -> bool:
        return x is not None and y is not None and abs(x - y) <= TOLERANCE
    for key in ("figure", "ratio"):
        if key in a and not close(a[key], b.get(key)):
            return False
    for key in ("rows", "parts"):
        if key in a and (set(a[key]) != set(b.get(key) or {}) or not all(close(v, b[key][k]) for k, v in a[key].items())):
            return False
    return True


class Naive(ValueError):
    """A figure that equals a naive reading of its question."""


def sessions(out: Path, estate: EstateWarehouses | None = None):
    """The Wealth warehouse on its own, and a session that attaches Retail, Wealth and the cross-reference, read only
    (the estate's warehouse port)."""
    from kge.estate.golden import warehouses
    estate = estate or warehouses(out)
    return estate.open("wealth"), estate.attached(list(UNITS))


def build_golden_wealth(out: Path, estate: EstateWarehouses | None = None) -> list[dict]:
    """The set's items, their expectations from their queries on the warehouses on disk. Raises Naive, naming the item and
    the reading, if a figure equals a naive reading of its question, and ValueError if a check an item rests on fails, or
    if a figure equals the owner's example as written where the query departs from it."""
    alone, both = sessions(out, estate)
    try:
        built = []
        for item in items():
            expect = dict(item.expect)
            if item.reads == "unreleased":
                (row,) = alone.execute(item.sql).fetchall()
                if row[0] is not False:
                    raise ValueError(f"{item.id}: the month it asks for is released, so it is no question about a month that is not")
            elif item.reads:
                con = both if item.attached else alone
                got = read(con, item, item.sql)
                for what, sql in item.naive:
                    if same(got, read(con, item, sql)):
                        raise Naive(f"{item.id} ({item.question}): its expectation {json.dumps(got)} equals the naive reading "
                                    f"with {what}, so it cannot tell a right answer from that one")
                if item.as_written and same(got, read(con, item, item.as_written[1])):
                    raise ValueError(f"{item.id} ({item.question}): its expectation {json.dumps(got)} equals the owner's example as "
                                     f"written, so the departure {item.as_written[0]} asks about changes nothing: take the example")
                expect.update(got)
            built.append({"id": item.id, "kind": item.kind, "category": item.category, "question": item.question, "expect": expect,
                          "reference_sql": item.sql, **({"principal": item.principal} if item.principal else {})})
        return built
    finally:
        alone.close()
        both.close()


def set_text(items_: list[dict]) -> str:
    """The set's file: a first version, a list of items (kge/eval/sets.py), in ASCII (the cross-BU category's arrow escaped)."""
    return json.dumps(items_, indent=2) + "\n"
