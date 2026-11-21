"""sage v1: questions on the SAGE wealth data (kge/estate/sage.py), each with its expected behaviour and figure.

A further set (kge/eval/sets.py), beside golden_wealth v1, so the sets the owner's baselines ran keep their files. Its
file, build/sage_v1.json, is a set's first version, its items shaped and scored as golden_wealth's
(kge/estate/golden_wealth.py): each figure is its reference SQL's result on the warehouses just generated, and the build
stops on a figure that equals a naive reading of its question.

The questions ask through what pack 1.5.0 added for SAGE: its physical column names, which Wealth holds as SAGE spells
them (NNA_IN, ACCT_SRCE_SYS_ID, BRCH_CD), and the restricted personal data of the SAGE tables (an account's name, a
branch's email address), which graph on refuses before any warehouse is queried. Net new assets keep every rule
golden_wealth's do; grouped by ACCT_SRCE_SYS_ID, its rows are SAGE's source system ids (100, 200), not Meridian's codes for
them, which W18's SRC_SYS_CD names.

v4 (issue #474, after v2's S01 to S05; the pack names it, tests.sets) adds the owner's 20 golden NNA questions as S06 to
S25 (golden_20): each with the owner's reference SQL, approved 2026-11-14, rewritten only for the sample's names and
months, its question and other phrasing as the owner wrote them (client name out, years two earlier; also_asked_as), our
phrasings after them (added_phrasings), each phrasing an item of its own when the set is read, and its table, every
column and row that SQL returns on the sample. tests/integration/test_golden20.py asks each through the Ask path and
judges its figures against that SQL run on the runtime the answer used (kge/eval/run_golden.py, judge).
"""
from __future__ import annotations

import json
from pathlib import Path

from kge.estate.golden_wealth import (ACCOUNT, ACRONYM, FY26_Q1, NNA_FIGURE, OFFICE, REFUSAL, Item, Naive, month, nna, read, same,
                                      sessions)
from kge.ports import EstateWarehouses

NAME = "sage_v1"


def items() -> list[Item]:
    """The set's items in order, each with its query and its naive readings."""
    return [
        Item("S01", "within", ACRONYM, "Sum of NNA_IN flows by ACCT_SRCE_SYS_ID in FY26-Q1", {**NNA_FIGURE},
             nna(FY26_Q1, group="f.ACCT_SRCE_SYS_ID"), "rows",
             (("every flow, eligible or not", nna(FY26_Q1, drop=("eligible", "not_excluded"), group="f.ACCT_SRCE_SYS_ID")),)),
        Item("S02", "within", ACRONYM, "NNA_IN flows for June 2025", {**NNA_FIGURE}, nna(month("2025-06-30")), "figure",
             (("no eligibility filter", nna(month("2025-06-30"), drop=("eligible",))),)),
        Item("S03", "within", ACRONYM, "NNA for FY26-Q1 by BRCH_CD", {**NNA_FIGURE},
             nna(FY26_Q1, joins=f"{ACCOUNT} {OFFICE}", group="o.BRCH_CD"), "rows",
             (("no eligibility filter", nna(FY26_Q1, drop=("eligible",), joins=f"{ACCOUNT} {OFFICE}", group="o.BRCH_CD")),)),
        Item("S04", "abstain", REFUSAL, "NNA for June 2025 by ACCT_NM",
             {"behaviour": "refuse", "reason": "An account name is restricted personal data (POL-DATA-CLASS).", "no_figure": True}),
        Item("S05", "abstain", REFUSAL, "NNA for June 2025 by EMAIL_ADDR_TX",
             {"behaviour": "refuse", "reason": "A branch's email address is restricted personal data (POL-DATA-CLASS).",
              "no_figure": True}),
    ]


def build_golden_sage(out: Path, estate: EstateWarehouses | None = None) -> list[dict]:
    """The set's items, their expectations from their queries on the Wealth warehouse on disk. Raises Naive, naming the
    item and the reading, if a figure equals a naive reading of its question."""
    alone, both = sessions(out, estate)
    try:
        built = []
        for item in items():
            expect = dict(item.expect)
            if item.reads:
                got = read(alone, item, item.sql)
                for what, sql in item.naive:
                    if same(got, read(alone, item, sql)):
                        raise Naive(f"{item.id} ({item.question}): its expectation {json.dumps(got)} equals the naive reading "
                                    f"with {what}, so it cannot tell a right answer from that one")
                expect.update(got)
            built.append({"id": item.id, "kind": item.kind, "category": item.category, "question": item.question, "expect": expect,
                          "reference_sql": item.sql})
        return built + golden_20_items(alone)
    finally:
        alone.close()
        both.close()


def set_text(items_: list[dict]) -> str:
    """The set's file: a first version, a list of items (kge/eval/sets.py)."""
    return json.dumps(items_, indent=2) + "\n"


GOLDEN_20 = "Golden 20 (owner, 2026-11-14)"
APPROVED = {"reference_sql_approved_by": "owner", "reference_sql_approved_on": "2026-11-14"}
AMOUNT = "t.TRAN_AM"
AT_MONTH_END = "t.DW_TRAN_MNTH_END_DT BETWEEN {a}.DW_EFF_DT AND {a}.DW_EXPR_DT"


def certified(select: str, where: str, *, group: str = "", order: str = "", join: str = "INNER", extra: str = "") -> str:
    """The owner's certified NNA query: F_NNA_FIN with the account and its client code in effect at the month end."""
    return " ".join(p for p in (
        f"SELECT {select} FROM F_NNA_FIN AS t",
        f"{join} JOIN ACCT_DIM2_1 AS j1 ON t.ACCT_SRCE_SYS_ID = j1.SRCE_SYS_ID AND t.DW_ACCT_ID = j1.DW_ACCT_ID AND "
        + AT_MONTH_END.format(a="j1"),
        f"{join} JOIN CLIENT_CD_ROLLUP_1 AS j2 ON j1.CLNT_CD = j2.CLNT_CD AND " + AT_MONTH_END.format(a="j2"),
        extra, f"WHERE {where}", f"GROUP BY {group}" if group else "", f"ORDER BY {order}" if order else "") if p)


def branch_sales(select: str, period: str, *, where: str = "", group: str = "", order: str = "") -> str:
    """The owner's branch sales NNA query: each transaction as it stood on its month's financial release, S4100
    accounts on bank and brokerage, homed in the BR division, AQ and DT left out; the amount signed in place."""
    rules = [period, "j1.FIN_MTRC_RLSE_DT BETWEEN t.DW_EFF_DT AND t.DW_EXPR_DT", "t.RCV_IN = 1", "t.NNA_IN = 'Y'",
             "j3.SEGMT_LVL_ID5 = 'S4100'", "t.ACCT_SRCE_SYS_ID IN (100, 200)", "UPPER(j4.BRCH_DIVSN_CD) = 'BR'",
             "NOT UPPER(COALESCE(t.DW_TRAN_TYPE_CD, '')) IN ('AQ', 'DT')", *([where] if where else [])]
    return " ".join(p for p in (
        f"SELECT {select} FROM TRAN_FACT_EXCL_MFCLR_G AS t",
        "INNER JOIN MONTH_DIM_1 AS j1 ON t.MNTH_END_DT = j1.CLDR_EOM_DT",
        "INNER JOIN ACCT_DIM2_1 AS j2 ON t.ACCT_SRCE_SYS_ID = j2.SRCE_SYS_ID AND t.DW_ACCT_ID = j2.DW_ACCT_ID AND "
        "t.MNTH_END_DT BETWEEN j2.DW_EFF_DT AND j2.DW_EXPR_DT",
        "INNER JOIN CLIENT_CD_ROLLUP_1 AS j3 ON j2.CLNT_CD = j3.CLNT_CD AND t.MNTH_END_DT BETWEEN j3.DW_EFF_DT AND j3.DW_EXPR_DT",
        "INNER JOIN BRCH_DIM_1 AS j4 ON j2.ACCT_DOMCL_BRCH_CD = j4.BRCH_CD AND j4.CURR_IN = 1",
        "WHERE " + " AND ".join(rules), f"GROUP BY {group}" if group else "", f"ORDER BY {order}" if order else "") if p)


def between(column: str, first: str, last: str) -> str:
    return f"{column} BETWEEN CAST('{first}' AS DATE) AND CAST('{last}' AS DATE)"


def on(column: str, day: str) -> str:
    return f"{column} = CAST('{day}' AS DATE)"


def compared(current: str, comparison: str, keys: tuple[str, ...] = (), label: str = "") -> str:
    """The owner's two-period comparison: each period's NNA stacked, then the figure, its comparison, the change and
    the change over the comparison's absolute value."""
    cols = "".join(f"{k}, " for k in keys)
    named = f"arg_min(s.{label}, s.part_rank) FILTER (WHERE s.{label} IS NOT NULL) AS {label}, " if label else ""
    cur, cmp = "SUM(IF(s.part = 'current_period', s.value, NULL))", "SUM(IF(s.part = 'comparison_period', s.value, NULL))"
    lab = f"{label}, " if label else ""
    return (f"WITH current_period AS ({current}), comparison_period AS ({comparison}), stacked AS ("
            f"SELECT 'current_period' AS part, 0 AS part_rank, {cols}{lab}nna_am AS value FROM current_period UNION ALL "
            f"SELECT 'comparison_period' AS part, 1 AS part_rank, {cols}{lab}nna_am AS value FROM comparison_period) "
            f"SELECT {''.join(f's.{k} AS {k}, ' for k in keys)}{named}{cur} AS nna_am, {cmp} AS nna_am_comparison, "
            f"{cur} - {cmp} AS nna_am_change, ({cur} - {cmp}) / NULLIF(ABS({cmp}), 0) AS nna_am_pct_change FROM stacked AS s"
            + (f" GROUP BY {', '.join('s.' + k for k in keys)} ORDER BY nna_am DESC" if keys else ""))


def ranked(grouped: str, key: str, keep: str, measures: str) -> str:
    """The owner's top and bottom: the grouped rows numbered from each end, ties broken by the key."""
    return (f"WITH grouped AS ({grouped}), ranked AS (SELECT g.*, "
            f"ROW_NUMBER() OVER (ORDER BY g.nna_am DESC, g.{key}) AS rank_from_top, "
            f"ROW_NUMBER() OVER (ORDER BY g.nna_am ASC, g.{key}) AS rank_from_bottom FROM grouped AS g) "
            f"SELECT r.{key} AS {key}, {measures}, IF(r.rank_from_top <= 10, 'top', 'bottom') AS rank_end, "
            "IF(r.rank_from_top <= 10, r.rank_from_top, r.rank_from_bottom) AS rank_position "
            f"FROM ranked AS r WHERE {keep} ORDER BY nna_am DESC")


MONTHLY = ("LAST_DAY(t.DW_TRAN_MNTH_END_DT) AS reporting_period_month_id, "
           "UPPER(strftime(t.DW_TRAN_MNTH_END_DT, '%b %Y')) AS reporting_period_month")
JAN_SEP = between("t.DW_TRAN_MNTH_END_DT", "2024-01-31", "2024-09-30")
DIRECTED_AM = "SUM(IF(t.CLIENT_DIRECTED_NNA_IN = 'Y', t.NNA_AM, 0)) AS nna_am_nna_x_fdic"
ACCOUNTS = "count(DISTINCT (t.ACCT_SRCE_SYS_ID, t.DW_ACCT_ID)) AS accounts_count"
TRANSACTIONS = "count(DISTINCT (t.ACCT_SRCE_SYS_ID, t.DW_ACCT_ID, t.DW_TRAN_ID)) AS transactions_count"
FLOWS = (f"SUM({AMOUNT}) AS nna_am, SUM(IF(t.DW_TRAN_DIRCTN_CD = 'I', {AMOUNT}, 0)) AS nna_am_inflows, "
         f"SUM(IF(t.DW_TRAN_DIRCTN_CD = 'O', {AMOUNT}, 0)) AS nna_am_outflows")
JAN_2024 = on("t.MNTH_END_DT", "2024-01-31")
TWO_REGIONS = "j4.RGN_CD IN ('CEO', 'NER')"
GROUP_NOTE = ("The Heartland region is a group of two regions, CEO and NER: the pack names it as one filter (op: in), and the "
              "reference SQL lists the regions.")


def golden_20() -> list[tuple[Item, list[str], tuple[list[str], list[str]], list[str]]]:
    """The owner's 20 golden NNA questions on Meridian's sample, each with its reference SQL (the owner's, approved
    2026-11-14), the measures among its columns, its other phrasings (the owner's, then ours: also_asked_as and
    added_phrasings) and what the SQL changes from the owner's: the sample's names (no dataset prefix, DuckDB's dialect,
    the sample's segment, region and branch codes), and its months, two years earlier (2026 to 2024, September 2025 to
    September 2023), since the sample holds July 2023 to July 2025."""
    table = {"unit": "wealth"}

    def item(n: int, question: str, sql: str, measures: list[str], phrasings: list[str], notes: list[str] = (), *,
             added: list[str] = ()):
        return (Item(f"S{n:02d}", "within", GOLDEN_20, question, {**table, "measures": measures}, sql, "table"),
                measures, (phrasings, list(added)), list(notes))
    return [
        item(6, "What was certified Q2 2024 NNA by business line?",
             certified("j2.SEGMT_LVL_ID6 AS client_segment_6_id, j2.SEGMT_LVL_NM6 AS client_segment_6, SUM(t.NNA_AM) AS nna_am",
                       between("t.DW_TRAN_MNTH_END_DT", "2024-04-30", "2024-06-30"), join="LEFT",
                       group="client_segment_6_id, client_segment_6", order="nna_am DESC"),
             ["nna_am"],
             ["Break out certified NNA for the second quarter of 2024 by business line."],
             added=["Certified NNA by business line for Q2 2024",
                    "Show certified NNA for April through June 2024, broken down by business line."]),
        item(7, "Show me certified Investor Services NNA by sub-segment and quarter for Q1 through Q3 2024.",
             certified("j2.SEGMT_LVL_ID5 AS client_segment_5_id, j2.SEGMT_LVL_NM5 AS client_segment_5, "
                       "strftime(t.DW_TRAN_MNTH_END_DT, '%Y') || 'Q' || quarter(t.DW_TRAN_MNTH_END_DT) AS reporting_period_quarter_id, "
                       "SUM(t.NNA_AM) AS nna_am", f"{JAN_SEP} AND j2.SEGMT_LVL_ID6 IN ('S5100')",
                       group="client_segment_5_id, client_segment_5, reporting_period_quarter_id",
                       order="reporting_period_quarter_id, nna_am DESC"),
             ["nna_am"],
             ["For Investor Services, what was certified NNA by sub-segment for each quarter from Q1 to Q3 2024?"],
             added=["Certified Investor Services NNA by sub-segment by quarter, Q1 to Q3 2024",
                    "Break down certified NNA for Investor Services by sub-segment and by quarter for the first three quarters of 2024."],
             notes=["Investor Services is S5100, the sample's level 6 code; its sub-segment is level 5, as in the owner's SQL."]),
        item(8, "Compare current vs. certified Q3 2024 NNA by month, with the dollar and percent difference.",
             "WITH as_is AS (SELECT LAST_DAY(t.MNTH_END_DT) AS reporting_period_month_id, "
             "UPPER(strftime(t.MNTH_END_DT, '%b %Y')) AS reporting_period_month, "
             f"SUM({AMOUNT}) AS nna_am FROM TRAN_FACT_1 AS t WHERE {between('t.MNTH_END_DT', '2024-07-31', '2024-09-30')} "
             "AND t.CURR_IN = 1 AND t.RCV_IN = 1 AND t.NNA_IN = 'Y' GROUP BY reporting_period_month_id, reporting_period_month), "
             f"corporate_frozen AS (SELECT {MONTHLY}, SUM(t.NNA_AM) AS nna_am FROM F_NNA_FIN AS t "
             f"WHERE {between('t.DW_TRAN_MNTH_END_DT', '2024-07-31', '2024-09-30')} GROUP BY reporting_period_month_id, reporting_period_month), "
             "stacked AS (SELECT 'as_is' AS part, 0 AS part_rank, reporting_period_month_id, reporting_period_month, nna_am AS value FROM as_is "
             "UNION ALL SELECT 'corporate_frozen' AS part, 1 AS part_rank, reporting_period_month_id, reporting_period_month, nna_am AS value "
             "FROM corporate_frozen) SELECT s.reporting_period_month_id AS reporting_period_month_id, "
             "arg_min(s.reporting_period_month, s.part_rank) FILTER (WHERE s.reporting_period_month IS NOT NULL) AS reporting_period_month, "
             "SUM(IF(s.part = 'as_is', s.value, NULL)) AS nna_am, SUM(IF(s.part = 'corporate_frozen', s.value, NULL)) AS nna_am_comparison, "
             "SUM(IF(s.part = 'as_is', s.value, NULL)) - SUM(IF(s.part = 'corporate_frozen', s.value, NULL)) AS nna_am_change, "
             "(SUM(IF(s.part = 'as_is', s.value, NULL)) - SUM(IF(s.part = 'corporate_frozen', s.value, NULL))) / "
             "NULLIF(ABS(SUM(IF(s.part = 'corporate_frozen', s.value, NULL))), 0) AS nna_am_pct_change "
             "FROM stacked AS s GROUP BY reporting_period_month_id ORDER BY reporting_period_month_id",
             ["nna_am", "nna_am_comparison", "nna_am_change", "nna_am_pct_change"],
             ["Show current versus certified NNA for each month of Q3 2024, with the dollar and percent difference."],
             added=["Current NNA vs certified NNA by month for July to September 2024, with the difference in dollars and percent",
                    "For each month in the third quarter of 2024, how did current NNA compare with certified NNA, in dollars and in percent?"]),
        item(9, "How much did SPS contribute to Retail Investor’s certified September 2024 NNAxFDIC?",
             certified("j1.ACCT_RELT_SPS_IN AS sps_account_sps_relationship_id, CASE j1.ACCT_RELT_SPS_IN WHEN 'Y' THEN 'SPS' "
                       "WHEN 'N' THEN 'Non-SPS' ELSE CAST(j1.ACCT_RELT_SPS_IN AS VARCHAR) END AS sps_account_sps_relationship, "
                       "SUM(t.NNA_AM) AS nna_am, SUM(t.NNA_AM) / NULLIF(SUM(SUM(t.NNA_AM)) OVER (), 0) AS nna_am_share",
                       f"{on('t.DW_TRAN_MNTH_END_DT', '2024-09-30')} AND t.CLIENT_DIRECTED_NNA_IN = 'Y' AND j2.SEGMT_LVL_ID5 IN ('S4100')",
                       group="sps_account_sps_relationship_id, sps_account_sps_relationship", order="nna_am DESC"),
             ["nna_am", "nna_am_share"],
             ["What was the SPS contribution to certified NNAxFDIC for Retail Investor in September 2024?"],
             added=["SPS share of Affluent certified client-directed NNA for September 2024",
                    "For Affluent in September 2024, how much certified client-directed NNA came from SPS accounts and how much from the rest?",
                    "How much did SPS contribute to Affluent's certified September 2024 client-directed NNA?",
                    "What was the SPS contribution to certified client-directed NNA for Affluent in September 2024?"]),
        item(10, "Sum January 2024 branch sales NNA inflows, outflows, and net by Branch for the Heartland region.",
             branch_sales(f"j4.BRCH_CD AS branch_branch_id, j4.BRCH_NM AS branch_branch, {FLOWS}", JAN_2024, where=TWO_REGIONS,
                          group="branch_branch_id, branch_branch", order="nna_am DESC"),
             ["nna_am", "nna_am_inflows", "nna_am_outflows"],
             ["For the Heartland region, give January 2024 branch sales NNA inflows, outflows, and net for each branch."],
             added=["Branch sales NNA inflow, outflow and net by branch for Northeast and Pacific regions in January 2024",
                    "January 2024 branch sales NNA by branch in the Northeast or Pacific region: inflows, outflows and net",
                    "Sum January 2024 branch sales NNA inflows, outflows, and net by branch for the Northeast and Pacific regions.",
                    "For the Northeast and Pacific regions, give January 2024 branch sales NNA inflows, outflows, and net for each branch."],
             notes=[GROUP_NOTE]),
        item(11, "List the top 10 and bottom 10 households in the Boulder branch based on January 2024 branch sales NNA.",
             ranked(branch_sales(f"j2.LAST_RCV_HSHLD_DK AS household_household_id, SUM({AMOUNT}) AS nna_am", JAN_2024,
                                 where="j4.BRCH_CD IN ('QO')", group="household_household_id"),
                    "household_household_id", "(r.rank_from_top <= 10 OR r.rank_from_bottom <= 10)", "r.nna_am AS nna_am"),
             ["nna_am"],
             ["Which 10 households had the highest and which 10 the lowest branch sales NNA in the Boulder branch in January 2024?"],
             added=["Top 10 and bottom 10 households by branch sales NNA for branch NYC, January 2024",
                    "In January 2024, rank the households of the NYC branch by branch sales NNA and show the ten highest and the ten lowest.",
                    "List the top 10 and bottom 10 households in the NYC branch based on January 2024 branch sales NNA.",
                    "Which 10 households had the highest and which 10 the lowest branch sales NNA in the NYC branch in January 2024?"]),
        item(12, "Break down certified February 2024 Retail Investor NNA and NNAxFDIC by account product code, with account counts.",
             certified(f"j1.ACCT_DETL_PROD_CD AS account_product_product_id, SUM(t.NNA_AM) AS nna_am, {DIRECTED_AM}, {ACCOUNTS}",
                       f"{on('t.DW_TRAN_MNTH_END_DT', '2024-02-29')} AND j2.SEGMT_LVL_ID5 IN ('S4100')",
                       group="account_product_product_id", order="nna_am DESC"),
             ["nna_am", "nna_am_nna_x_fdic", "accounts_count"],
             ["For Retail Investor, show certified NNA and NNAxFDIC for February 2024 by account product code, with the number of accounts."],
             added=["Affluent certified NNA and client-directed NNA by account product code for February 2024, with account counts",
                    "In February 2024, what were Affluent's certified NNA and client-directed NNA for each account product code, and how many accounts had each?",
                    "Break down certified February 2024 Affluent NNA and client-directed NNA by account product code, with account counts.",
                    "For Affluent, show certified NNA and client-directed NNA for February 2024 by account product code, with the number of accounts."]),
        item(13, "How many transactions of $250K or more did the Fort Collins branch have in January 2024, and what was their branch sales NNA?",
             branch_sales(f"SUM({AMOUNT}) AS nna_am, {TRANSACTIONS}", JAN_2024,
                          where="ABS(t.TRAN_AM) >= 250000 AND j4.BRCH_CD IN ('5H')"),
             ["nna_am", "transactions_count"],
             ["In January 2024, how many transactions of at least $250K did Fort Collins branch have, and what branch sales NNA did they bring?"],
             added=["Number of transactions of $250,000 or more and their branch sales NNA for branch BOS in January 2024",
                    "For the BOS branch in January 2024, count the transactions of $250K and over and give their branch sales NNA.",
                    "How many transactions of $250K or more did the BOS branch have in January 2024, and what was their branch sales NNA?",
                    "In January 2024, how many transactions of at least $250K did the BOS branch have, and what branch sales NNA did they bring?"]),
        item(14, "List the top 10 jurisdiction countries based on International certified NNA for January through September 2024, with account counts.",
             ranked(certified(f"j1.JRSDCTN_CNTRY_CD AS jurisdiction_country_country_id, SUM(t.NNA_AM) AS nna_am, {ACCOUNTS}",
                              f"{JAN_SEP} AND j2.SEGMT_LVL_ID4 IN ('S3200')", group="jurisdiction_country_country_id"),
                    "jurisdiction_country_country_id", "r.rank_from_top <= 10", "r.nna_am AS nna_am, r.accounts_count AS accounts_count"),
             ["nna_am", "accounts_count"],
             ["Which 10 jurisdiction countries had the most International certified NNA from January to September 2024, and how many accounts did each have?"],
             added=["Top 10 jurisdiction countries by Wealth Management certified NNA, January to September 2024, with account counts",
                    "Rank jurisdiction countries by certified Wealth Management NNA for the first nine months of 2024 and show the top ten with their account counts.",
                    "List the top 10 jurisdiction countries based on Wealth Management certified NNA for January through September 2024, with account counts.",
                    "Which 10 jurisdiction countries had the most Wealth Management certified NNA from January to September 2024, and how many accounts did each have?"],
             notes=["Wealth Management (S3200) is the sample's only level 4 segment, and every sample account's jurisdiction is US, so the answer has one row."]),
        item(15, "Show me YTD International certified NNA through September 2024 broken down by source system.",
             certified("j3.SRCE_SYS_ID AS source_system_source_system_id, j3.SRCE_SYS_NM AS source_system_source_system, SUM(t.NNA_AM) AS nna_am",
                       f"{JAN_SEP} AND j2.SEGMT_LVL_ID4 IN ('S3200')",
                       extra="LEFT JOIN SRCE_SYS_1 AS j3 ON t.ACCT_SRCE_SYS_ID = j3.SRCE_SYS_ID AND j3.CURR_IN = 1 AND j3.RCV_IN = 1",
                       group="source_system_source_system_id, source_system_source_system", order="nna_am DESC"),
             ["nna_am"],
             ["Break down International certified NNA year to date through September 2024 by source system."],
             added=["Wealth Management certified NNA by source system, year to date through September 2024",
                    "What was certified Wealth Management NNA for January to September 2024 for each source system?",
                    "Show me YTD Wealth Management certified NNA through September 2024 broken down by source system.",
                    "Break down Wealth Management certified NNA year to date through September 2024 by source system."]),
        item(16, "Show me Retail Investor certified NNA and NNAxFDIC by month for January through September 2024.",
             certified(f"{MONTHLY}, SUM(t.NNA_AM) AS nna_am, {DIRECTED_AM}", f"{JAN_SEP} AND j2.SEGMT_LVL_ID5 IN ('S4100')",
                       group="reporting_period_month_id, reporting_period_month", order="reporting_period_month_id, nna_am DESC"),
             ["nna_am", "nna_am_nna_x_fdic"],
             ["What was Retail Investor certified NNA and NNAxFDIC for each month from January to September 2024?"],
             added=["Affluent certified NNA and client-directed NNA by month, January to September 2024",
                    "Monthly certified NNA and client-directed NNA for Affluent over the first nine months of 2024",
                    "Show me Affluent certified NNA and client-directed NNA by month for January through September 2024.",
                    "What was Affluent certified NNA and client-directed NNA for each month from January to September 2024?"]),
        item(17, "What were Retail Investor certified NNAxFDIC gross inflows and outflows for September 2024?",
             certified("SUM(t.NNA_AM) AS nna_am, SUM(IF(t.DW_TRAN_DIRCTN_CD = 'I', t.NNA_AM, 0)) AS nna_am_inflows, "
                       "SUM(IF(t.DW_TRAN_DIRCTN_CD = 'O', t.NNA_AM, 0)) AS nna_am_outflows",
                       f"{on('t.DW_TRAN_MNTH_END_DT', '2024-09-30')} AND t.CLIENT_DIRECTED_NNA_IN = 'Y' AND j2.SEGMT_LVL_ID5 IN ('S4100')"),
             ["nna_am", "nna_am_inflows", "nna_am_outflows"],
             ["Give Retail Investor certified NNAxFDIC inflows and outflows for September 2024."],
             added=["Affluent certified client-directed NNA gross inflows, gross outflows and net for September 2024",
                    "For September 2024, how much certified client-directed NNA flowed in and how much flowed out for Affluent?",
                    "What were Affluent certified client-directed NNA gross inflows and outflows for September 2024?",
                    "Give Affluent certified client-directed NNA inflows and outflows for September 2024."]),
        item(18, "Show me Retail Investor certified NNAxFDIC by client segment level 4 for January through September 2024.",
             certified("j2.SEGMT_LVL_ID4 AS client_segment_4_id, j2.SEGMT_LVL_NM4 AS client_segment_4, SUM(t.NNA_AM) AS nna_am",
                       f"{JAN_SEP} AND t.CLIENT_DIRECTED_NNA_IN = 'Y' AND j2.SEGMT_LVL_ID5 IN ('S4100')",
                       group="client_segment_4_id, client_segment_4", order="nna_am DESC"),
             ["nna_am"],
             ["Break down Retail Investor certified NNAxFDIC from January to September 2024 by client segment level 4."],
             added=["Affluent certified client-directed NNA by client segment level 4, January to September 2024",
                    "For January through September 2024, what was Affluent's certified client-directed NNA in each level 4 client segment?",
                    "Show me Affluent certified client-directed NNA by client segment level 4 for January through September 2024.",
                    "Break down Affluent certified client-directed NNA from January to September 2024 by client segment level 4."],
             notes=["S3200 is the sample's only level 4 segment, so the answer has one row."]),
        item(19, "Show me International certified NNA by month for January through September 2024, with transaction and account counts.",
             certified(f"{MONTHLY}, SUM(t.NNA_AM) AS nna_am, {ACCOUNTS}, {TRANSACTIONS}", f"{JAN_SEP} AND j2.SEGMT_LVL_ID4 IN ('S3200')",
                       group="reporting_period_month_id, reporting_period_month", order="reporting_period_month_id, nna_am DESC"),
             ["nna_am", "accounts_count", "transactions_count"],
             ["For each month from January to September 2024, what was International certified NNA, with the number of transactions and accounts?"],
             added=["Wealth Management certified NNA by month for January to September 2024 with transaction counts and account counts",
                    "Monthly certified Wealth Management NNA, January through September 2024, with how many transactions and accounts each month",
                    "Show me Wealth Management certified NNA by month for January through September 2024, with transaction and account counts.",
                    "For each month from January to September 2024, what was Wealth Management certified NNA, with the number of transactions and accounts?"]),
        item(20, "How did Retail Investor certified NNA for September 2024 compare year over year?",
             compared(certified("SUM(t.NNA_AM) AS nna_am", f"{on('t.DW_TRAN_MNTH_END_DT', '2024-09-30')} AND j2.SEGMT_LVL_ID5 IN ('S4100')"),
                      certified("SUM(t.NNA_AM) AS nna_am", f"{on('t.DW_TRAN_MNTH_END_DT', '2023-09-30')} AND j2.SEGMT_LVL_ID5 IN ('S4100')")),
             ["nna_am", "nna_am_comparison", "nna_am_change", "nna_am_pct_change"],
             ["Compare Retail Investor certified NNA in September 2024 with September 2023."],
             added=["Affluent certified NNA for September 2024 versus September 2023, with the change",
                    "Year over year, how did certified NNA for Affluent in September 2024 compare with the same month a year earlier?",
                    "How did Affluent certified NNA for September 2024 compare year over year?",
                    "Compare Affluent certified NNA in September 2024 with September 2023."]),
        item(21, "Which day of the week had the most Retail Investor certified NNA, year to date through September 2024?",
             certified("j3.DAY_OF_WEEK_NUMBER AS day_of_week_day_of_week_id, j3.DAY_OF_WEEK AS day_of_week_day_of_week, SUM(t.NNA_AM) AS nna_am",
                       f"{JAN_SEP} AND j2.SEGMT_LVL_ID5 IN ('S4100')",
                       extra="LEFT JOIN DATE_DIM_1 AS j3 ON t.DW_TRAN_DT = j3.CALENDAR_DATE",
                       group="day_of_week_day_of_week_id, day_of_week_day_of_week", order="nna_am DESC"),
             ["nna_am"],
             ["On which weekday was Retail Investor certified NNA highest, year to date through September 2024?"],
             added=["Affluent certified NNA by day of the week, January to September 2024",
                    "Year to date through September 2024, which weekday brought the most certified NNA for Affluent?",
                    "Which day of the week had the most Affluent certified NNA, year to date through September 2024?",
                    "On which weekday was Affluent certified NNA highest, year to date through September 2024?"]),
        item(22, "Show me Retail Investor certified NNA and NNAxFDIC from SPS accounts by month for January through September 2024.",
             certified(f"{MONTHLY}, SUM(t.NNA_AM) AS nna_am, {DIRECTED_AM}",
                       f"{JAN_SEP} AND j2.SEGMT_LVL_ID5 IN ('S4100') AND j1.ACCT_RELT_SPS_IN IN ('Y')",
                       group="reporting_period_month_id, reporting_period_month", order="reporting_period_month_id, nna_am DESC"),
             ["nna_am", "nna_am_nna_x_fdic"],
             ["What was Retail Investor certified NNA and NNAxFDIC from SPS accounts for each month from January to September 2024?"],
             added=["Affluent SPS accounts: certified NNA and client-directed NNA by month, January to September 2024",
                    "Monthly certified NNA and client-directed NNA for Affluent SPS accounts over January through September 2024",
                    "Show me Affluent certified NNA and client-directed NNA from SPS accounts by month for January through September 2024.",
                    "What was Affluent certified NNA and client-directed NNA from SPS accounts for each month from January to September 2024?"],
             notes=["The owner's question is a second turn (now just SPS accounts); its single-question form is asked, so a follow-up turn is not exercised."]),
        item(23, "What were January 2024 branch sales NNA inflows, outflows, and the ratio of inflows to outflows, with a count of households?",
             branch_sales(f"{FLOWS}, ROUND(SUM(IF(t.DW_TRAN_DIRCTN_CD = 'I', {AMOUNT}, 0)) / "
                          f"NULLIF(ABS(SUM(IF(t.DW_TRAN_DIRCTN_CD = 'O', {AMOUNT}, 0))), 0), 2) AS nna_am_inflows_to_outflows_ratio, "
                          "COUNT(DISTINCT IF(j2.LAST_RCV_HSHLD_DK > 0, j2.LAST_RCV_HSHLD_DK, NULL)) AS households_count", JAN_2024),
             ["nna_am", "nna_am_inflows", "nna_am_outflows", "nna_am_inflows_to_outflows_ratio", "households_count"],
             ["For January 2024, what were branch sales NNA inflows and outflows, the inflow-to-outflow ratio, and the number of households?"],
             added=["Branch sales NNA inflows, outflows, inflow to outflow ratio and household count for January 2024",
                    "In January 2024, how much branch sales NNA came in and went out, what was the ratio of inflows to outflows, and how many households were there?"]),
        item(24, "Show Heartland region branch sales NNA by month for January through September 2024, with the month-over-month % change and cumulative NNA.",
             "WITH grouped AS (" + branch_sales(
                 "LAST_DAY(t.MNTH_END_DT) AS reporting_period_month_id, UPPER(strftime(t.MNTH_END_DT, '%b %Y')) AS reporting_period_month, "
                 f"SUM({AMOUNT}) AS nna_am", between("t.MNTH_END_DT", "2024-01-31", "2024-09-30"), where=TWO_REGIONS,
                 group="reporting_period_month_id, reporting_period_month") + ") "
             "SELECT g.reporting_period_month_id AS reporting_period_month_id, g.reporting_period_month AS reporting_period_month, "
             "g.nna_am AS nna_am, LAG(g.nna_am) OVER (ORDER BY g.reporting_period_month_id) AS nna_am_prior, "
             "g.nna_am - LAG(g.nna_am) OVER (ORDER BY g.reporting_period_month_id) AS nna_am_change, "
             "(g.nna_am - LAG(g.nna_am) OVER (ORDER BY g.reporting_period_month_id)) / "
             "NULLIF(ABS(LAG(g.nna_am) OVER (ORDER BY g.reporting_period_month_id)), 0) AS nna_am_pct_change, "
             "SUM(g.nna_am) OVER (ORDER BY g.reporting_period_month_id ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) AS nna_am_running_total "
             "FROM grouped AS g ORDER BY reporting_period_month_id",
             ["nna_am", "nna_am_prior", "nna_am_change", "nna_am_pct_change", "nna_am_running_total"],
             ["Give Heartland region branch sales NNA for each month from January to September 2024, with month-over-month % change and a running total."],
             added=["Branch sales NNA by month for the Northeast and Pacific regions, January to September 2024, with month over month change and running total",
                    "Monthly branch sales NNA for the Northeast or Pacific region from January through September 2024, with the % change from the prior month and the cumulative NNA",
                    "Show Northeast and Pacific regions branch sales NNA by month for January through September 2024, with the month-over-month % change and cumulative NNA.",
                    "Give Northeast and Pacific regions branch sales NNA for each month from January to September 2024, with month-over-month % change and a running total."],
             notes=[GROUP_NOTE]),
        item(25, "How did Retail Investor certified NNAxFDIC for September 2024 compare year over year by client segment level 4?",
             compared(certified("j2.SEGMT_LVL_ID4 AS client_segment_4_id, j2.SEGMT_LVL_NM4 AS client_segment_4, SUM(t.NNA_AM) AS nna_am",
                                f"{on('t.DW_TRAN_MNTH_END_DT', '2024-09-30')} AND t.CLIENT_DIRECTED_NNA_IN = 'Y' AND j2.SEGMT_LVL_ID5 IN ('S4100')",
                                group="client_segment_4_id, client_segment_4"),
                      certified("j2.SEGMT_LVL_ID4 AS client_segment_4_id, j2.SEGMT_LVL_NM4 AS client_segment_4, SUM(t.NNA_AM) AS nna_am",
                                f"{on('t.DW_TRAN_MNTH_END_DT', '2023-09-30')} AND t.CLIENT_DIRECTED_NNA_IN = 'Y' AND j2.SEGMT_LVL_ID5 IN ('S4100')",
                                group="client_segment_4_id, client_segment_4"),
                      keys=("client_segment_4_id",), label="client_segment_4"),
             ["nna_am", "nna_am_comparison", "nna_am_change", "nna_am_pct_change"],
             ["Compare Retail Investor certified NNAxFDIC in September 2024 with September 2023 by client segment level 4."],
             added=["Affluent certified client-directed NNA by client segment level 4, September 2024 versus September 2023",
                    "For each level 4 client segment, how did Affluent's certified client-directed NNA in September 2024 compare with September 2023?",
                    "How did Affluent certified client-directed NNA for September 2024 compare year over year by client segment level 4?",
                    "Compare Affluent certified client-directed NNA in September 2024 with September 2023 by client segment level 4."]),
    ]


def golden_20_items(estate_session) -> list[dict]:
    """The 20 items as the set holds them, their tables read from their reference SQL on the sample."""
    out = []
    for item, measures, (phrasings, added), notes in golden_20():
        got = read(estate_session, item, item.sql)
        if not got["table"]["rows"]:
            raise ValueError(f"{item.id} ({item.question}): its reference SQL returns no row on the sample")
        out.append({"id": item.id, "kind": item.kind, "category": item.category, "question": item.question,
                    "also_asked_as": phrasings, **({"added_phrasings": added} if added else {}), "expect": {**item.expect, **got}, "reference_sql": item.sql, **APPROVED,
                    **({"notes": notes} if notes else {})})
    return out
