"""Build the synthetic estate into build/.

    python3 -m kge.estate.generate [--profile <id>] [--out build]

It writes the estate of the profile (kge/pipeline/profile.py: --profile, else
PROFILE, else the registry's default): the folder its warehouses are in
(Profile.estate), build/ in the demo; --out names another.

Produces, through the estate's warehouse port (kge/ports.py EstateWarehouses, the factory's open_estate: DuckDB files
in build/warehouses/, or with WAREHOUSE_BACKEND=bigquery the client's BigQuery dataset and no file):
  warehouses retail, sme, corporate, wealth                four warehouses, keys declared (on DuckDB,
                                                           build/warehouses/{retail,sme,corporate,wealth}.duckdb)
  warehouse xref                                           the certified client cross-reference, Retail to Wealth
                                                           (on DuckDB, build/warehouses/xref.duckdb)
  build/glossary.csv, mappings.csv, measures.csv           the enterprise glossary layer
  build/no_join.json, principals.json                      curated facts and the access mirror
  build/docs/certified/*.md, build/docs/uncertified/*.md   the two shelves
  build/golden.json, heldout.json                          the golden set (v2) and the held-out set (v1), expected values computed
  build/golden_wealth_v1.json                              the owner's Wealth and cross-unit questions (kge/estate/golden_wealth.py),
                                                           each figure its reference SQL's result, none a naive reading's
  build/sage_v1.json                                       questions on the SAGE wealth data (kge/estate/golden_sage.py)

Deterministic for a given seed. Nothing here is committed; build/ is git-ignored.
The Wealth expansion and the cross-reference (#39) are added after every
unit's draws, each from a stream of its own seeded by the seed and its name,
never from the master stream the units draw from, where one extra draw would
move every figure: so the tables the estate had keep their rows (make
check-estate). build/summary.json records the seed, which a run of the A/B
harness keeps beside the graph's hashes.
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import random
import string
import sys
from pathlib import Path

from kge.estate import names, sage, spec
from kge.ports import EstateWarehouses, WarehouseSession
from kge.estate.documents import all_documents

MONTHS = [dt.date(2024, 7, 1) + dt.timedelta(days=31 * i) for i in range(12)]
MONTHS = [dt.date(m.year, m.month, 1) for m in MONTHS]
FISCAL = [
    ("FY25-Q2", 2025, dt.date(2024, 7, 1), dt.date(2024, 9, 30)),
    ("FY25-Q3", 2025, dt.date(2024, 10, 1), dt.date(2024, 12, 31)),
    ("FY25-Q4", 2025, dt.date(2025, 1, 1), dt.date(2025, 3, 31)),
    ("FY26-Q1", 2026, dt.date(2025, 4, 1), dt.date(2025, 6, 30)),
]
FIRST = ["Amara", "Bao", "Chidi", "Dara", "Emeka", "Farah", "Gita", "Hamid", "Ines", "Joris",
         "Kaede", "Lior", "Maren", "Nadia", "Oren", "Priya", "Quang", "Rosa", "Soren", "Tariq"]
LAST = ["Okafor", "Lindqvist", "Marchetti", "Nakamura", "Osei", "Petrov", "Quintero", "Rahman",
        "Sato", "Tanaka", "Ubaldi", "Varga", "Weiss", "Xu", "Yilmaz", "Zamora", "Achebe", "Brandt"]
COMPANY = ["Harrowfield", "Northgate", "Bellweather", "Coldstream", "Ironbridge", "Larkspur",
           "Meadowvale", "Oakhurst", "Pinecrest", "Quarrywood", "Redfern", "Silverbeck"]
LEI_ALPHABET = "".join(ch for ch in string.ascii_uppercase + string.digits if ch not in "IO")
SUFFIX = ["Logistics", "Foods", "Engineering", "Holdings", "Retail", "Marine", "Textiles", "Energy"]


def quarter_of(d: dt.date) -> str:
    return f"{d.year}-Q{(d.month - 1) // 3 + 1}"


def fiscal_of(d: dt.date) -> str:
    for label, _, start, end in FISCAL:
        if start <= d <= end:
            return label
    raise ValueError(d)


def month_end(d: dt.date) -> dt.date:
    nxt = dt.date(d.year + (d.month == 12), d.month % 12 + 1, 1)
    return nxt - dt.timedelta(days=1)


def schema_statements(unit: str, schemas: dict | None = None) -> list[str]:
    """One warehouse's tables, keys and column comments, as DDL: a unit's (spec.SCHEMAS), or the curated layer's
    (spec.CURATED_SCHEMAS)."""
    out = []
    for tname, t in (spec.SCHEMAS if schemas is None else schemas)[unit].items():
        cols = ", ".join(f"{n} {typ}" for n, typ, _ in t["columns"])
        fks = "".join(f", FOREIGN KEY ({c}) REFERENCES {rt}({rc})" for c, rt, rc in t["fks"])
        out.append(f"CREATE TABLE {tname} ({cols}, PRIMARY KEY ({t['pk']}){fks})")
        for n, _, desc in t["columns"]:
            out.append(f"COMMENT ON COLUMN {tname}.{n} IS '{desc.replace(chr(39), chr(39) * 2)}'")
    return out


def create_schema(estate: EstateWarehouses, unit: str, schemas: dict | None = None) -> WarehouseSession:
    """One warehouse staged with its tables, keys and column comments (schema_statements), open to populate."""
    return estate.stage(unit, schema_statements(unit, schemas))


def insert(con: WarehouseSession, table: str, rows: list[tuple]) -> None:
    """The rows into the table's leading columns, in order: a column appended since stays null."""
    if not rows:
        return
    cols = [c["name"] for c in con.describe(table)["columns"]][:len(rows[0])]
    con.load(table, cols, [list(r) for r in rows])


def insert_batched(con: WarehouseSession, table: str, rows: list[tuple], size: int = 500) -> None:
    """As insert, a statement per `size` rows rather than per row where the warehouse inserts by statement: for the
    Wealth expansion's larger tables."""
    if not rows:
        return
    cols = [c["name"] for c in con.describe(table)["columns"]][:len(rows[0])]
    con.load(table, cols, [list(r) for r in rows], batch=size)


def populate_retail(con, rng: random.Random) -> None:
    insert(con, "calendar_month", [(m, m.year, quarter_of(m)) for m in MONTHS])
    regions = ["NW", "NE", "SW", "SE", "CEN"]
    insert(con, "branch", [(i, f"{rng.choice(COMPANY)} Street", regions[(i - 1) % len(regions)], dt.date(2000 + i, 3, 1)) for i in range(1, 13)])
    products = [(1, "Everyday Current", "current", 0, 0.00), (2, "Premier Current", "current", 25, 12.00),
                (3, "Instant Saver", "savings", 310, 0.00), (4, "Fixed Saver", "savings", 425, 0.00),
                (5, "Personal Loan", "lending", 890, 0.00), (6, "Rewards Card", "card", 2190, 5.00)]
    insert(con, "product", products)
    customers = []
    for cid in range(1, 201):
        fn, ln = rng.choice(FIRST), rng.choice(LAST)
        customers.append((cid, fn, ln, dt.date(rng.randint(1950, 2003), rng.randint(1, 12), rng.randint(1, 28)),
                          f"{fn[0].lower()}.{ln.lower()}{cid}@example.com", rng.choice(["MASS", "AFFLUENT", "STUDENT", "SENIOR"]),
                          rng.randint(1, 12), dt.date(rng.randint(2005, 2024), rng.randint(1, 12), 1)))
    insert(con, "customer", customers)
    accounts, aid = [], 0
    for cid in range(1, 201):
        for _ in range(rng.choice([1, 1, 2, 2, 3])):
            aid += 1
            opened = dt.date(rng.randint(2010, 2024), rng.randint(1, 12), 1)
            closed = dt.date(2025, rng.randint(1, 6), 15) if rng.random() < 0.06 else None
            status = "CLOSED" if closed else rng.choice(["OPEN"] * 9 + ["DORMANT"])
            accounts.append((aid, cid, rng.randint(1, 6), status, opened, closed, "USD"))
    insert(con, "account", accounts)
    txns, tid = [], 0
    revenue, rid = [], 0
    balances, bid = [], 0
    for a in accounts:
        for m in MONTHS:
            if a[5] and m > a[5]:
                continue
            n = rng.randint(0, 6) if a[3] != "DORMANT" else 0
            for _ in range(n):
                tid += 1
                txns.append((tid, a[0], m + dt.timedelta(days=rng.randint(0, 27)), round(rng.uniform(-900, 600), 2),
                             rng.choice(["DEBIT", "DEBIT", "CREDIT"]), rng.choice(["BRANCH", "ATM", "ONLINE", "MOBILE"]),
                             rng.choice(["5411", "5812", "4111", "5999", None])))
            rid += 1
            interest, fee = round(rng.uniform(2, 60), 2), round(rng.uniform(0, 15), 2)
            revenue.append((rid, a[0], m, interest, fee, round(interest + fee, 2)))
            for day in (1, 15, month_end(m).day):
                bid += 1
                ledger = round(rng.uniform(50, 12000), 2)
                balances.append((bid, a[0], dt.date(m.year, m.month, day), ledger, round(ledger * 0.97, 2)))
    insert(con, "transaction", txns)
    insert(con, "monthly_revenue", revenue)
    insert(con, "daily_balance", balances)
    activity, xid = [], 0
    for cid in range(1, 201):
        for m in MONTHS:
            xid += 1
            logins = rng.randint(0, 40)
            tcount = sum(1 for t in txns if t[1] in {a[0] for a in accounts if a[1] == cid} and t[2].year == m.year and t[2].month == m.month)
            activity.append((xid, cid, m, logins, tcount, tcount >= 1))
    insert(con, "customer_activity", activity)
    insert(con, "complaint", [(i, rng.randint(1, 200), dt.date(2025, rng.randint(1, 6), rng.randint(1, 28)),
                               rng.choice(["FEES", "SERVICE", "CARD", "DIGITAL"]),
                               dt.date(2025, rng.randint(1, 6), rng.randint(1, 28)) if rng.random() < 0.7 else None)
                              for i in range(1, 41)])


def populate_sme(con, rng: random.Random) -> None:
    insert(con, "calendar_month", [(m, m.year, quarter_of(m)) for m in MONTHS])
    insert(con, "relationship_manager", [(i, f"{rng.choice(FIRST)} {rng.choice(LAST)}", ["TEAM-A", "TEAM-B", "TEAM-C"][(i - 1) % 3]) for i in range(1, 9)])
    clients = [(c, f"{rng.choice(COMPANY)} {rng.choice(SUFFIX)} Ltd", f"REG{rng.randint(100000, 999999)}",
                rng.choice(["A01", "C41", "G47", "H49", "J62"]), rng.randint(1, 8), dt.date(rng.randint(2012, 2024), rng.randint(1, 12), 1))
               for c in range(1001, 1061)]
    insert(con, "client", clients)
    facilities = []
    fid = 0
    for c in clients:
        for _ in range(rng.choice([1, 1, 2])):
            fid += 1
            facilities.append((fid, c[0], rng.choice(["TERM_LOAN", "OVERDRAFT", "INVOICE_FINANCE"]),
                               round(rng.uniform(50000, 2000000), 2), "USD", dt.date(2023, rng.randint(1, 12), 1), dt.date(2028, 6, 30)))
    insert(con, "facility", facilities)
    insert(con, "drawdown", [(i, rng.choice(facilities)[0], MONTHS[rng.randrange(12)] + dt.timedelta(days=rng.randint(0, 27)),
                              round(rng.uniform(5000, 400000), 2)) for i in range(1, 201)])
    insert(con, "repayment", [(i, rng.choice(facilities)[0], MONTHS[rng.randrange(12)] + dt.timedelta(days=rng.randint(0, 27)),
                               round(rng.uniform(1000, 90000), 2), rng.choice([0] * 8 + [12, 35, 61]))
                              for i in range(1, 601)])
    rev, rid = [], 0
    act, xid = [], 0
    for c in clients:
        for m in MONTHS:
            rid += 1
            nii, fees = round(rng.uniform(400, 9000), 2), round(rng.uniform(50, 1500), 2)
            rev.append((rid, c[0], m, nii, fees, round(nii + fees, 2)))
            xid += 1
            p = rng.randint(0, 25)
            act.append((xid, c[0], m, p, p >= 1))
    insert(con, "monthly_client_revenue", rev)
    insert(con, "client_activity", act)
    insert(con, "covenant", [(i, f[0], rng.choice(["LEVERAGE", "INTEREST_COVER", "MIN_LIQUIDITY"]), round(rng.uniform(1, 4), 4),
                              dt.date(2025, rng.randint(1, 6), 28), rng.random() < 0.1) for i, f in enumerate(facilities, 1)])


def populate_corporate(con, rng: random.Random) -> None:
    """Corporate's tables; its CRM contact numbers overlap the Retail customer_id range on purpose."""
    insert(con, "fiscal_calendar", [(lbl, fy, s, e) for lbl, fy, s, e in FISCAL])
    cps = [(f"CP-{i:04d}", f"{rng.choice(COMPANY)} {rng.choice(SUFFIX)} SE", "".join(rng.choice(LEI_ALPHABET) for _ in range(20)),
            rng.choice(["DE", "FR", "NL", "GB", "US"]), rng.choice(["FIN", "IND", "UTL", "TMT"]), dt.date(rng.randint(2010, 2023), 1, 15))
           for i in range(1, 41)]
    insert(con, "counterparty", cps)
    contacts = []
    for i in range(1, 81):
        cp = rng.choice(cps)
        fn, ln = rng.choice(FIRST), rng.choice(LAST)
        contacts.append((i, cp[0], rng.randint(1, 200), f"{fn} {ln}", f"{fn[0].lower()}.{ln.lower()}@example.com",
                         rng.choice(["TREASURER", "CFO", "OPERATIONS"])))
    insert(con, "contact", contacts)
    insert(con, "deal", [(i, rng.choice(cps)[0], rng.choice(["FX_FWD", "IRS", "LOAN", "BOND"]), round(rng.uniform(1e6, 5e7), 2),
                          MONTHS[rng.randrange(12)], dt.date(2027, 6, 30)) for i in range(1, 121)])
    exp, eid = [], 0
    rev, rid = [], 0
    util, uid = [], 0
    for cp in cps:
        for lbl, _, s, e in FISCAL:
            eid += 1
            exp.append((eid, cp[0], e, round(rng.uniform(5e5, 2e7), 2), rng.randint(20, 900), round(rng.uniform(20, 60), 2)))
            rid += 1
            eur = round(rng.uniform(80000, 900000), 2)
            fx = round(rng.uniform(1.04, 1.12), 6)
            rev.append((rid, cp[0], lbl, eur, fx, round(eur * fx, 2)))
            uid += 1
            lim = round(rng.uniform(1e6, 3e7), 2)
            util.append((uid, cp[0], e, lim, round(lim * rng.uniform(0.1, 0.95), 2)))
    insert(con, "exposure", exp)
    insert(con, "quarterly_revenue", rev)
    insert(con, "limit_utilisation", util)
    insert(con, "rating", [(i, cp[0], dt.date(2025, 3, 31), rng.choice(["A1", "A2", "B1", "B2", "C1"]), rng.choice(["AA", "A", "BBB", None]))
                           for i, cp in enumerate(cps, 1)])


def populate_wealth(con, rng: random.Random) -> None:
    insert(con, T["fiscal_calendar"], [(lbl, fy, s, e) for lbl, fy, s, e in FISCAL])
    insert(con, T["advisor"], [(i, f"{rng.choice(FIRST)} {rng.choice(LAST)}", rng.choice(["NYC", "BOS", "CHI", "SFO"]), rng.choice(["S7", "S66"]))
                            for i in range(1, 31)])
    hh = [(i, f"{rng.choice(LAST)} Household", rng.randint(1, 30), rng.choice(["CAUTIOUS", "BALANCED", "GROWTH"]),
           dt.date(rng.randint(2008, 2024), rng.randint(1, 12), 1)) for i in range(5001, 5151)]
    insert(con, T["household"], hh)
    pf, pid = [], 0
    for h in hh:
        for _ in range(rng.choice([1, 1, 2])):
            pid += 1
            pf.append((pid, h[0], rng.choice(["DISCRETIONARY", "ADVISORY"]), "USD", dt.date(rng.randint(2010, 2024), 6, 1)))
    insert(con, T["portfolio"], pf)
    hold, hid = [], 0
    for p in pf:
        for _, _, _, e in FISCAL:
            for k in range(5):
                hid += 1
                q = round(rng.uniform(10, 5000), 4)
                hold.append((hid, p[0], e, f"INS-{rng.randint(100, 999)}", q, round(q * rng.uniform(20, 400), 2)))
    insert(con, T["holding"], hold)
    fee, fid = [], 0
    aua, aid = [], 0
    for h in hh:
        for lbl, _, s, e in FISCAL:
            fid += 1
            adv, prod = round(rng.uniform(1500, 25000), 2), round(rng.uniform(0, 6000), 2)
            fee.append((fid, h[0], lbl, adv, prod, round(adv + prod, 2)))
            aid += 1
            aua.append((aid, h[0], e, round(rng.uniform(4e5, 2.5e7), 2)))
    insert(con, T["quarterly_fee_revenue"], fee)
    insert(con, T["assets_under_advice"], aua)
    insert(con, T["suitability_review"], [(i, h[0], dt.date(2025, rng.randint(1, 6), rng.randint(1, 28)),
                                        rng.choice(["SUITABLE"] * 8 + ["ACTION_REQUIRED", "ESCALATED"]), rng.randint(1, 30))
                                       for i, h in enumerate(hh, 1)])


POPULATE = {"retail": populate_retail, "sme": populate_sme, "corporate": populate_corporate, "wealth": populate_wealth}


OPEN_MONTH = dt.date(2025, 7, 31)
BOOKED_THROUGH = dt.date(2025, 7, 15)
MONTH_ENDS = [month_end(m) for m in MONTHS] + [OPEN_MONTH]
WINDOW_START = MONTHS[0]
REVIEW_DAY = dt.date(2025, 1, 1)
RESTATED_ON = dt.date(2025, 4, 22)
EXAMPLE_HOUSEHOLD = 5001
HERE = spec.CLIENT.upper()
EXTERNAL = [f"EXT-{i:02d}" for i in range(1, 10)]
CERTIFIER = "Client Reference Data Steward"
SEGMENTS = [
    ("AFFLUENT", "Affluent", None, 1), ("PRIVATE", "Private Clients", None, 1),
    ("CORE", "Core Affluent", "AFFLUENT", 2), ("EMERGING", "Emerging Affluent", "AFFLUENT", 2),
    ("HNW", "High Net Worth", "PRIVATE", 2), ("UHNW", "Ultra High Net Worth", "PRIVATE", 2),
]
LEAF_SEGMENTS, LEAF_WEIGHTS = ["CORE", "EMERGING", "HNW", "UHNW"], [4, 3, 3, 1]
SOURCE_SYSTEMS = ["BANK", "BROKERAGE", "MF_CLEARING", "ADVISOR_SERVICES", "STOCK_PLAN", "INSURANCE", "TRUST"]
FREEZE_LAG = sage.RELEASE_LAG
T = {business: physical for business, (physical, _) in names.MERIDIAN.items()}
SOURCE_SYSTEM = "CASE SRCE_SYS_ID WHEN 100 THEN 'BROKERAGE' WHEN 200 THEN 'BANK' END"
FLOW_TYPES = [
    ("CASH_IN", "Cash received from another institution", "IN", True),
    ("CASH_OUT", "Cash paid to another institution", "OUT", True),
    ("FEE", "Fees charged to the account", "OUT", False),
    ("INCOME", "Dividends and interest credited", "IN", False),
    ("INT_TRF", "Transfer from the household's own account elsewhere in the bank", "IN", False),
    ("SEC_IN", "Securities transferred in from another institution", "IN", True),
    ("SEC_OUT", "Securities transferred out to another institution", "OUT", True),
]
NNA_COUNTS = {code: counts for code, _, _, counts in FLOW_TYPES}
FLOW_MIX = {"BANK": (["CASH_IN", "CASH_OUT", "INCOME", "FEE"], [30, 25, 25, 20]),
            "BROKERAGE": (["SEC_IN", "SEC_OUT", "CASH_IN", "CASH_OUT", "INCOME", "FEE"], [20, 10, 20, 15, 20, 15])}
AMOUNTS = {"CASH_IN": (2_000, 150_000), "CASH_OUT": (-120_000, -1_000), "SEC_IN": (10_000, 400_000),
           "SEC_OUT": (-250_000, -10_000), "INCOME": (50, 4_000), "FEE": (-2_500, -40)}
EXCLUSION_REASONS = ["ESTATE", "MIGRATION", "SANCTIONS", "STAFF"]
EXCLUSION_WINDOWS = [(dt.date(2023, 5, 1), None)] * 4 + [(dt.date(2024, 8, 1), dt.date(2025, 1, 31))] * 3 + \
                    [(dt.date(2025, 4, 15), None)] * 3 + [(dt.date(2025, 2, 1), dt.date(2025, 5, 31))] * 2
OFFICES = [
    ("BOS", "Boston Federal Street", "NORTHEAST", dt.date(2010, 3, 1), dt.date(2024, 2, 29)),
    ("BOS", "Boston Back Bay", "NORTHEAST", dt.date(2024, 3, 1), None),
    ("CHI", "Chicago LaSalle Street", "MIDWEST", dt.date(2011, 6, 1), None),
    ("NYC", "New York Park Avenue", "NORTHEAST", dt.date(2010, 1, 1), None),
    ("SFO", "San Francisco Embarcadero", "PACIFIC", dt.date(2014, 9, 1), None),
]
REGIONS = [("MIDWEST", "Midwest", "WEST"), ("NORTHEAST", "Northeast", "EAST"), ("PACIFIC", "Pacific", "WEST")]
DIVISIONS = [("EAST", "East Division"), ("WEST", "Central and West Division")]
ID, SYSTEM, HOUSEHOLD, SEGMENT, TEST, FROM, TO, CURRENT = range(8)


def fiscal_label(d: dt.date) -> str:
    """The fiscal quarter a date falls in, on the fiscal year from 1 April: FY26-Q1 for April to June 2025."""
    return f"FY{(d.year + (d.month >= 4)) % 100:02d}-Q{(d.month - 4) % 12 // 3 + 1}"


def valid_on(row: list, d: dt.date, start: int = FROM, end: int = TO) -> bool:
    return row[start] <= d and (row[end] is None or d <= row[end])


def populate_wealth_expansion(con, rng: random.Random, fill: sage.Filler) -> list[list]:
    """The expansion's tables in the Wealth warehouse, from rng alone. Returns the accounts' versions.

    The traps are planted on purpose: account ids are numbered on each source system from the same start, so a join on
    account_id alone counts twice, and an office has a version closed before the data window, so a join without
    is_current counts its flows twice. EXAMPLE_HOUSEHOLD, the one the owner's examples name, holds a bank and two
    brokerage accounts, the second of which moves to the next household on 1 March 2025; other accounts are re-segmented
    at the annual review (REVIEW_DAY), move household or close, each touched once, and test accounts are booked under
    live households and take round test deposits. Flows run from July 2024 to OPEN_MONTH, still booking and not
    released; a few of a counting type are ruled ineligible, and a day an account is not open still takes its draws, so
    the stream does not depend on it. March 2025 is restated after its freeze (RESTATED_ON), so the month as reported
    and as restated differ. Exclusion windows are in force throughout, end before 31 March 2025, begin after it or span
    it, on accounts with eligible flows in FY25-Q4 and FY26-Q1. AUM at each quarter end is the market value of the
    household's holdings, so its lineage is exact. Every table is written under its physical name: SAGE's for the SAGE
    tables (kge/estate/sage.py), and for Meridian's own the physical name in SAGE's style its business name maps to (T,
    kge/estate/names.py). SAGE records no freeze date, so a month as reported is read as it stood on its release date
    (FREEZE_LAG, RELEASE_LAG days after the month end), and TRAN_FACT_1's FIN_MTRC_RLSE_FRZN_IN says so."""
    insert(con, "MONTH_DIM_1", sage.month_end_rows(fill, [(me, me + dt.timedelta(days=sage.RELEASE_LAG)) for me in MONTH_ENDS],
                                                   BOOKED_THROUGH))
    since = dt.date(2010, 1, 1)
    insert(con, T["division"], [(code, name, since, None, True) for code, name in DIVISIONS])
    insert(con, T["region"], [(code, name, division, since, None, True) for code, name, division in REGIONS])
    insert(con, "BRCH_DIM_1", sage.office_rows(
        fill, [(code, name, region, start, end, end is None) for code, name, region, start, end in OFFICES],
        {code: name for code, name, _ in REGIONS}))
    insert(con, T["client_segment"], SEGMENTS)
    insert(con, "SRCE_SYS_1", sage.source_system_rows(fill, SOURCE_SYSTEMS))
    insert(con, T["ref_flow_type"], FLOW_TYPES)

    households = con.execute("SELECT HSHLD_DK, HSHLD_ONBRD_DT FROM HSHLD_DIM_1 ORDER BY HSHLD_DK").fetchall()
    members = []
    for h, _ in households:
        roles = ["PRIMARY"] + (["JOINT"] if rng.random() < 0.5 else []) + (["DEPENDENT"] if rng.random() < 0.25 else [])
        members += [(h, h * 10 + k, role) for k, role in enumerate(roles, 1)]
    insert_batched(con, T["household_member"], members)

    versions: list[list] = []
    last = {"BANK": 70000, "BROKERAGE": 70000}

    def open_account(system: str, household: int, segment: str, opened: dt.date, test: bool = False) -> None:
        last[system] += 1
        versions.append([last[system], system, household, segment, test, opened, None, True])

    for h, onboarded in households:
        segment = rng.choices(LEAF_SEGMENTS, LEAF_WEIGHTS)[0]
        brokerage = 2 if h == EXAMPLE_HOUSEHOLD else rng.choice([0, 1, 1, 2])
        bank = h == EXAMPLE_HOUSEHOLD or rng.random() < 0.9 or not brokerage
        if bank:
            open_account("BANK", h, segment, onboarded)
        for _ in range(brokerage):
            open_account("BROKERAGE", h, segment, onboarded)
    for system in ("BANK", "BROKERAGE") * 3:
        h = rng.choice([h for h, _ in households if h != EXAMPLE_HOUSEHOLD])
        open_account(system, h, rng.choice(LEAF_SEGMENTS), dt.date(2024, 1, 15), test=True)

    def change(v: list, on: dt.date, **fields) -> None:
        """A new version of v from `on`; v closes the day before."""
        new = v[:]
        v[TO], v[CURRENT] = on - dt.timedelta(days=1), False
        new[FROM], new[TO], new[CURRENT] = on, None, True
        for k, value in fields.items():
            new[{"household": HOUSEHOLD, "segment": SEGMENT}[k]] = value
        versions.append(new)

    moved = max((v for v in versions if v[HOUSEHOLD] == EXAMPLE_HOUSEHOLD and v[SYSTEM] == "BROKERAGE"), key=lambda v: v[ID])
    change(moved, dt.date(2025, 3, 1), household=EXAMPLE_HOUSEHOLD + 1)
    pool = [v for v in versions if not v[TEST] and v[FROM] < WINDOW_START and v[HOUSEHOLD] != EXAMPLE_HOUSEHOLD and v[CURRENT]]
    rng.shuffle(pool)
    for v in pool[:15]:
        change(v, REVIEW_DAY, segment=rng.choice([s for s in LEAF_SEGMENTS if s != v[SEGMENT]]))
    for v in pool[15:19]:
        change(v, rng.choice([dt.date(2024, 10, 1), dt.date(2025, 2, 1), dt.date(2025, 5, 1)]),
               household=rng.choice([h for h, _ in households if h not in (v[HOUSEHOLD], EXAMPLE_HOUSEHOLD)]))
    for v in pool[19:25]:
        v[TO], v[CURRENT] = dt.date(2024, rng.randint(9, 12), 15) if rng.random() < 0.5 else dt.date(2025, rng.randint(1, 5), 15), False
    versions.sort(key=lambda v: (v[SYSTEM], v[ID], v[FROM]))
    homes = {h: (office, onboarded, h * 10 + 1) for h, office, onboarded in
             con.execute("SELECT h.HSHLD_DK, a.BRCH_CD, h.HSHLD_ONBRD_DT "
                         "FROM HSHLD_DIM_1 h JOIN ADV_DIM_1 a ON a.ADV_ID = h.PRIM_ADV_ID").fetchall()}
    codes = sage.client_codes(LEAF_SEGMENTS)
    insert_batched(con, "ACCT_DIM2_1", sage.account_rows(fill, versions, homes, codes))

    span: dict[tuple, list] = {}
    for v in versions:
        s = span.setdefault((v[ID], v[SYSTEM]), [v[FROM], v[TO], v[TEST]])
        s[0] = min(s[0], v[FROM])
        s[1] = None if v[TO] is None or s[1] is None else max(s[1], v[TO])
    flows, fid = [], 0
    for (acct, system), (start, end, test) in sorted(span.items(), key=lambda kv: (kv[0][1], kv[0][0])):
        for me in MONTH_ENDS:
            first, through = me.replace(day=1), min(me, BOOKED_THROUGH)
            for _ in range(rng.choice([1, 2, 2, 3]) if test else rng.choice([0, 0, 1, 1, 2, 3])):
                day = first + dt.timedelta(days=rng.randint(0, (through - first).days))
                if test:
                    code, amount = "CASH_IN", float(rng.choice([100_000, 250_000, 500_000]))
                else:
                    code = rng.choices(*FLOW_MIX[system])[0]
                    amount = round(rng.uniform(*AMOUNTS[code]), 2)
                counts = NNA_COUNTS[code]
                eligible = counts and rng.random() >= 0.04
                directed = counts and rng.random() < 0.7
                cp = (rng.choice(EXTERNAL), rng.randint(10_000_000, 99_999_999)) if code not in ("INCOME", "FEE") else (None, None)
                if day < start or (end is not None and day > end):
                    continue
                fid += 1
                flows.append([fid, acct, system, day, code, amount, directed, eligible, *cp, day, None, True])
    test_accounts = {(v[ID], v[SYSTEM]) for v in versions if v[TEST]}
    march = [f for f in flows if (f[3].year, f[3].month) == (2025, 3) and f[7] and (f[1], f[2]) not in test_accounts]
    for f in sorted(rng.sample(march, 12)):
        new = f[:]
        f[11], f[12] = RESTATED_ON - dt.timedelta(days=1), False
        new[10], new[11], new[12] = RESTATED_ON, None, True
        if rng.random() < 0.5:
            new[7] = False
        else:
            new[5] = round(f[5] * rng.uniform(0.5, 0.9), 2)
        flows.append(new)
    flows.sort(key=lambda f: (f[0], f[10]))
    insert_batched(con, "TRAN_FACT_1", sage.flow_rows(fill, flows, FREEZE_LAG))

    quarters: dict[tuple, set] = {}
    for f in flows:
        if f[7] and f[12] and (f[1], f[2]) not in test_accounts:
            quarters.setdefault((f[1], f[2]), set()).add(fiscal_label(f[3]))
    eligible = sorted(k for k, q in quarters.items() if {"FY25-Q4", "FY26-Q1"} <= q)
    chosen = rng.sample(eligible, len(EXCLUSION_WINDOWS))
    insert(con, T["nna_exclusion"], [(i, acct, system, rng.choice(EXCLUSION_REASONS), start, end)
                                  for i, ((acct, system), (start, end)) in enumerate(zip(chosen, EXCLUSION_WINDOWS), 1)])

    con.execute("INSERT INTO AUM_MNTH_END_FACT_1 SELECT p.HSHLD_DK, h.AS_OF_DT, sum(h.MKT_VAL_AM) FROM HLDG_FACT_1 h "
                "JOIN PRTFL_DIM_1 p ON p.PRTFL_ID = h.PRTFL_ID GROUP BY p.HSHLD_DK, h.AS_OF_DT ORDER BY 1, 2")
    tiers = {"EMERGING": 1000, "CORE": 1001, "HNW": 1002, "UHNW": 1003}
    for table, rows in sage.new_rows(fill, SEGMENTS, codes, tiers, BOOKED_THROUGH).items():
        insert_batched(con, sage.TABLES[table], rows)
    return versions


def populate_xref(xref, retail, wealth, rng: random.Random) -> list[list]:
    """The certified client cross-reference, from rng alone: Retail customers matched to Wealth household members,
    mostly certified, some proposed or rejected, a few matches ended (a customer no longer the member, closed with
    nothing after, and one re-certified on a firmer method, a new version from that day). Some of the matched customers
    are among Retail's highest June 2025 balances, so a question on their advisor coverage has an answer. Returns the
    rows."""
    customers = dict(retail.execute("SELECT customer_id, opened_on FROM customer ORDER BY customer_id").fetchall())
    june = retail.execute("SELECT a.customer_id, avg(b.ledger_balance) FROM daily_balance b JOIN account a ON a.account_id = b.account_id "
                          "WHERE b.as_of BETWEEN DATE '2025-06-01' AND DATE '2025-06-30' GROUP BY 1 ORDER BY 2 DESC, 1").fetchall()
    onboarded = dict(wealth.execute("SELECT HSHLD_DK, HSHLD_ONBRD_DT FROM HSHLD_DIM_1 ORDER BY HSHLD_DK").fetchall())
    members: dict[int, dict[str, int]] = {}
    for h, m, role in wealth.execute("SELECT HSHLD_DK, MBR_ID, MBR_ROLE_CD FROM HSHLD_MBR_1 ORDER BY 1, 2").fetchall():
        members.setdefault(h, {})[role] = m
    systems = dict(wealth.execute(f"SELECT HSHLD_DK, min({SOURCE_SYSTEM}) FROM ACCT_DIM2_1 WHERE TEST_ACCT_IN = 'N' "
                                  "GROUP BY 1 ORDER BY 1").fetchall())

    top = [c for c, _ in june[:20]]
    linked = rng.sample(top, 8) + rng.sample([c for c in customers if c not in top], 37)
    households = rng.sample(sorted(members), 40)
    joint = [h for h in households if "JOINT" in members[h]][:5]
    pairs = [(c, h, members[h]["PRIMARY"]) for c, h in zip(linked, households)]
    pairs += [(c, h, members[h]["JOINT"]) for c, h in zip(linked[40:], joint)]
    statuses = ["CERTIFIED"] * 36 + ["PROPOSED"] * 5 + ["REJECTED"] * 4
    rng.shuffle(statuses)

    rows = []
    for xid, ((c, h, m), status) in enumerate(zip(pairs, statuses), 1):
        start = max(customers[c], onboarded[h])
        if status == "CERTIFIED":
            method = rng.choices(["DETERMINISTIC", "PROBABILISTIC", "MANUAL"], [7, 2, 1])[0]
            confidence = round(rng.uniform(0.9, 0.99), 3) if method == "PROBABILISTIC" else 1.0
            by, on = CERTIFIER, start + dt.timedelta(days=rng.randint(3, 30))
        else:
            method, by, on = "PROBABILISTIC", None, None
            confidence = round(rng.uniform(0.55, 0.85) if status == "PROPOSED" else rng.uniform(0.3, 0.6), 3)
        rows.append([xid, c, h, m, systems.get(h, "BANK"), start, None, True, method, confidence, status, by, on])
    certified = [r for r in rows if r[10] == "CERTIFIED" and r[5] < WINDOW_START]
    for r in rng.sample(certified, 3):
        r[6], r[7] = rng.choice([dt.date(2024, 12, 31), dt.date(2025, 2, 28)]), False
    again = next((r for r in certified if r[7] and r[8] == "PROBABILISTIC"), None)
    if again is not None:
        new = again[:]
        again[6], again[7] = dt.date(2024, 10, 31), False
        new[5], new[8], new[9], new[12] = dt.date(2024, 11, 1), "DETERMINISTIC", 1.0, dt.date(2024, 11, 1)
        rows.append(new)
    rows.sort(key=lambda r: (r[0], r[5]))
    insert(xref, "client_xref", [tuple(r) for r in rows])
    return rows


def transfer_legs(retail, wealth, xref: list[list], versions: list[list], rng: random.Random, fill: sage.Filler) -> int:
    """Debits on Retail accounts that are transfers to the same client's Wealth account, from rng alone. Each leg is an
    existing debit, chosen, never a new row: its counterparty is filled (this institution, the Wealth account) and a
    Wealth flow mirrors it, an inflow of type INT_TRF. An internal move is not new money, so the mirror is not eligible for
    net new assets, and its flow type does not count towards them: an eligibility filter on Wealth data alone leaves it
    out. Mostly from certified matches; a few from matches only proposed. The derived SAGE transaction extract
    (flow_excl_mf_clearing) includes the legs, which are ineligible for NNA. Returns the number of legs."""
    debits = retail.execute("SELECT t.txn_id, t.account_id, a.customer_id, t.posted_on, t.amount FROM transaction t "
                            "JOIN account a ON a.account_id = t.account_id WHERE t.txn_type = 'DEBIT' AND t.amount < 0 "
                            "ORDER BY t.txn_id").fetchall()
    matches: dict[int, list] = {}
    for r in xref:
        matches.setdefault(r[1], []).append(r)
    found = {"CERTIFIED": [], "PROPOSED": []}
    for txn, account, customer, day, amount in debits:
        for r in matches.get(customer, []):
            if r[10] not in found or not valid_on(r, day, 5, 6):
                continue
            target = sorted((v[SYSTEM], v[ID]) for v in versions if v[HOUSEHOLD] == r[2] and not v[TEST] and valid_on(v, day))
            if target:
                found[r[10]].append((txn, account, day, amount, target[0]))
                break
    legs = sorted(rng.sample(found["CERTIFIED"], min(60, len(found["CERTIFIED"])))
                  + rng.sample(found["PROPOSED"], min(3, len(found["PROPOSED"]))))
    fid = wealth.execute("SELECT max(DW_TRAN_ID) FROM TRAN_FACT_1").fetchone()[0]
    mirrors = []
    retail.update_many("transaction", "txn_id", ["counterparty_bank", "counterparty_account"],
                       [(txn, HERE, acct) for txn, account, day, amount, (system, acct) in legs])
    for txn, account, day, amount, (system, acct) in legs:
        fid += 1
        mirrors.append((fid, acct, system, day, "INT_TRF", -float(amount), True, False, HERE, account, day, None, True))
    insert_batched(wealth, "TRAN_FACT_1", sage.flow_rows(fill, [list(m) for m in mirrors], FREEZE_LAG))
    insert_batched(wealth, "TRAN_FACT_EXCL_MFCLR_G", sage.excl_rows_for(fill, len(mirrors)))
    return len(legs)


def expand(warehouses, seed: int) -> dict:
    """The Wealth expansion and the certified client cross-reference, on the four units' warehouses already built
    (warehouses: the estate's, or the folder of DuckDB files they are in): the expansion's Wealth tables, the
    cross-reference's own warehouse (xref, written fresh) and the transfer legs between them, each from its own stream.
    Returns what it added."""
    estate = warehouses if hasattr(warehouses, "stage") else _warehouses(Path(warehouses).parent)
    retail, wealth = estate.open("retail", write=True), estate.open("wealth", write=True)
    xref = create_schema(estate, "xref", spec.CURATED_SCHEMAS)
    try:
        fill = sage.Filler(random.Random(f"{seed}:sage"))
        versions = populate_wealth_expansion(wealth, random.Random(f"{seed}:wealth"), fill)
        matches = populate_xref(xref, retail, wealth, random.Random(f"{seed}:xref"))
        legs = transfer_legs(retail, wealth, matches, versions, random.Random(f"{seed}:transfers"), fill)
    except BaseException:
        for con in (retail, wealth, xref):
            con.close()
        estate.discard("xref")
        raise
    for con in (retail, wealth, xref):
        con.close()
    estate.publish("xref")
    return {"xref_rows": len(matches), "transfer_legs": legs}


def _warehouses(out: Path, client: str | None = None) -> EstateWarehouses:
    from kge.estate.golden import warehouses
    return warehouses(out, client)


def write_glossary_layer(out: Path) -> None:
    with (out / "glossary.csv").open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["term", "domain", "steward", "definition"])
        w.writeheader()
        w.writerows(spec.GLOSSARY)
    with (out / "mappings.csv").open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["term", "unit", "table", "column", "certified_by", "certified_on"])
        stewards = {g["term"]: g["steward"] for g in spec.GLOSSARY}
        for term, unit, table, col in spec.CERTIFIED_MAPPINGS:
            w.writerow([term, unit, table, col, stewards[term], "2024-11-15"])
    with (out / "measures.csv").open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["unit", "table", "column", "grain", "currency", "calendar", "conversion"])
        for (unit, table, col), md in spec.MEASURE_METADATA.items():
            w.writerow([unit, table, col, md["grain"], md["currency"], md["calendar"], md["conversion"]])
    (out / "no_join.json").write_text(json.dumps(spec.NO_JOIN_FACTS, indent=2))
    (out / "principals.json").write_text(json.dumps(spec.PRINCIPALS, indent=2))
    (out / "units.json").write_text(json.dumps({"units": spec.UNITS, "labels": spec.UNIT_LABEL, "keys": spec.UNIT_KEY}, indent=2))


def write_documents(out: Path, seed: int = 7) -> dict:
    docs = all_documents(seed)
    index = []
    for d in docs:
        folder = out / "docs" / d.shelf
        folder.mkdir(parents=True, exist_ok=True)
        (folder / f"{d.doc_id}.md").write_text(d.text())
        index.append({"doc_id": d.doc_id, "shelf": d.shelf, "title": d.title, "kind": d.kind, "unit": d.unit,
                      "owner": d.owner, "version": d.version, "published_at": d.published_at,
                      "superseded_by": d.superseded_by, "reviewed_at": d.reviewed_at, "planted": d.tags, "path": f"docs/{d.shelf}/{d.doc_id}.md"})
    (out / "documents.json").write_text(json.dumps(index, indent=2))
    return {"certified": sum(d.shelf == "certified" for d in docs), "uncertified": sum(d.shelf == "uncertified" for d in docs)}


def write_question_sets(out: Path, estate: EstateWarehouses | None = None) -> tuple[list[dict], list[dict], list[dict], list[dict]]:
    """The gated golden set, v2, the held-out paraphrases, v1, golden_wealth v1 and sage v1, computed from the
    warehouses on disk. All four are built before any is written: a figure of golden_wealth's or golden_sage's that equals
    a naive reading of its question stops the build (kge/estate/golden_wealth.py, kge/estate/golden_sage.py), and no set
    is written."""
    from kge.estate import golden_sage, golden_wealth
    from kge.estate.golden import build_golden, golden_v2, set_text
    from kge.estate.heldout import build_heldout
    estate = estate or _warehouses(out)
    golden, heldout, wealth = build_golden(out, estate), build_heldout(out, estate), golden_wealth.build_golden_wealth(out, estate)
    sage_set = golden_sage.build_golden_sage(out, estate)
    (out / "golden.json").write_text(json.dumps(golden_v2(golden), indent=2, default=str))
    (out / "heldout.json").write_text(set_text(heldout))
    (out / f"{golden_wealth.NAME}.json").write_text(golden_wealth.set_text(wealth))
    (out / f"{golden_sage.NAME}.json").write_text(golden_sage.set_text(sage_set))
    return golden, heldout, wealth, sage_set


def main() -> int:
    from kge.pipeline.profile import ProfileError, resolve
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--profile", help="whose estate to write (default: PROFILE, else the registry's default)")
    ap.add_argument("--out", help="the folder to write the estate in, instead of the profile's")
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--golden-only", action="store_true", help="recompute build/golden.json (v2), heldout.json (v1), golden_wealth_v1.json and sage_v1.json "
                                                               "from the warehouses already on disk and stop")
    args = ap.parse_args()
    try:
        prof = resolve(args.profile)
        out = Path(args.out) if args.out else prof.estate()
    except ProfileError as exc:
        print(f"generate: {exc}", file=sys.stderr)
        return 2
    estate = _warehouses(out, prof.client)
    if args.golden_only:
        golden, heldout, wealth, sage_set = write_question_sets(out, estate)
        print(json.dumps({"golden_questions": len(golden), "heldout_questions": len(heldout), "golden_wealth_questions": len(wealth),
                          "sage_questions": len(sage_set)}))
        return 0
    out.mkdir(parents=True, exist_ok=True)
    rng = random.Random(args.seed)

    for unit in spec.UNITS:
        con = create_schema(estate, unit)
        try:
            POPULATE[unit](con, random.Random(rng.randint(0, 10**9)))
        except BaseException:
            con.close()
            estate.discard(unit)
            raise
        con.close()
        estate.publish(unit)
    added = expand(estate, args.seed)
    counts = {}
    for unit, tables in [*spec.SCHEMAS.items(), *spec.CURATED_SCHEMAS.items()]:
        con = estate.open(unit)
        counts[unit] = {t: con.execute(f"SELECT count(*) FROM {t}").fetchone()[0] for t in tables}
        con.close()

    write_glossary_layer(out)
    shelves = write_documents(out, args.seed)

    golden, heldout, wealth, sage_set = write_question_sets(out, estate)

    ncols = sum(len(t["columns"]) for u in spec.SCHEMAS.values() for t in u.values())
    summary = {
        "seed": args.seed,
        "units": spec.UNITS,
        "tables": sum(len(u) for u in spec.SCHEMAS.values()),
        "columns": ncols,
        "certified_columns": len(spec.CERTIFIED_MAPPINGS),
        "certified_share": round(len(spec.CERTIFIED_MAPPINGS) / ncols, 3),
        "glossary_terms": len(spec.GLOSSARY),
        "documents": shelves,
        "golden_questions": len(golden),
        "heldout_questions": len(heldout),
        "golden_wealth_questions": len(wealth),
        "sage_questions": len(sage_set),
        "curated": {"warehouses": list(spec.CURATED_SCHEMAS), "tables": sum(len(u) for u in spec.CURATED_SCHEMAS.values()), **added},
        "rows": counts,
    }
    (out / "summary.json").write_text(json.dumps(summary, indent=2))
    print(json.dumps({k: v for k, v in summary.items() if k != "rows"}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
