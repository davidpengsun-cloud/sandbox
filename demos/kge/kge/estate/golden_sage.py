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
             (("every flow, eligible or not", nna(FY26_Q1, drop=("eligible", "not_test", "not_excluded"), group="f.ACCT_SRCE_SYS_ID")),
              ("test accounts kept", nna(FY26_Q1, drop=("not_test",), group="f.ACCT_SRCE_SYS_ID")))),
        Item("S02", "within", ACRONYM, "NNA_IN flows for June 2025", {**NNA_FIGURE}, nna(month("2025-06-30")), "figure",
             (("no eligibility filter", nna(month("2025-06-30"), drop=("eligible",))),
              ("test accounts kept", nna(month("2025-06-30"), drop=("not_test",))))),
        Item("S03", "within", ACRONYM, "NNA for FY26-Q1 by BRCH_CD", {**NNA_FIGURE},
             nna(FY26_Q1, joins=f"{ACCOUNT} {OFFICE}", group="o.BRCH_CD"), "rows",
             (("no eligibility filter", nna(FY26_Q1, drop=("eligible",), joins=f"{ACCOUNT} {OFFICE}", group="o.BRCH_CD")),
              ("test accounts kept", nna(FY26_Q1, drop=("not_test",), joins=f"{ACCOUNT} {OFFICE}", group="o.BRCH_CD")))),
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
        return built
    finally:
        alone.close()
        both.close()


def set_text(items_: list[dict]) -> str:
    """The set's file: a first version, a list of items (kge/eval/sets.py)."""
    return json.dumps(items_, indent=2) + "\n"
