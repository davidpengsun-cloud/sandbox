"""SAGE, the reference brokerage warehouse that Meridian's Wealth unit is a subset of.

sage.json holds what the reference catalogue publishes for seventeen SAGE tables (MERIDIAN_DW, its CORE, WEALTH and CLIENT
report schemas): each column's name, type and description, its quality rule, the product's catalogue metadata, the
values its published sample shows, and the whole sample of the small reference tables. Everything in it is invented.
Each table is keyed there by its business name, the name Meridian calls it by (account, flow, month_end, ...); "sage"
is its physical name.

Wealth's warehouse holds all seventeen as SAGE has them, under SAGE's physical names (TABLES: ACCT_DIM2_1, TRAN_FACT_1,
MONTH_DIM_1, BRCH_DIM_1, SRCE_SYS_1 and the twelve more), every column under its field name, case kept, none renamed and
none dropped (schemas). Meridian's account, flow, month_end, office and ref_source_system tables are gone, and no table is
named by a business name: the pack reads SAGE's columns. ALIASES records, for each column those five Meridian tables
had, the SAGE column the pack reads instead; where Meridian's value is not the SAGE column's own, the pack says how it is
read, never a column added: Meridian's source_system is BANK or BROKERAGE where SAGE's SRCE_SYS_ID is 200 or 100, and
its signed amount_usd is TRAN_AM, a magnitude, signed by DW_TRAN_DIRCTN_CD (the net new assets measure).

Rows are Meridian's own, never SAGE's sample rows: a SAGE column's value comes from what the row already says where it
can (the account an enrollment is on, a flow's month end), else is drawn from the column's published sample values.
The calendars are computed, not drawn. The draws come from a stream of their own (kge/estate/generate.py), so nothing
here moves a figure an older table held. KEYS gives each table's primary key and foreign keys, in creation order (a
foreign key's table first); client_code_rollup is the rollup client_segment is the node view of: a client code's level 6
is the Meridian segment, level 5 its parent. RELEASE_LAG is the days after a month end its figures are released
(FIN_MTRC_RLSE_DT). A fiscal year from 1 April has its quarters on the calendar's.
"""
from __future__ import annotations

import datetime as dt
import json
import random
from decimal import Decimal
from functools import cache
from pathlib import Path

REFERENCE: dict = json.loads((Path(__file__).with_name("sage.json")).read_text())
TABLES: dict[str, str] = {name: spec["sage"] for name, spec in REFERENCE.items()}
OPEN_END = dt.date(9999, 12, 31)
NA = "N/A"

ALIASES: dict[str, dict[str, str]] = {
    "account": {"account_id": "DW_ACCT_ID", "source_system": "SRCE_SYS_ID", "household_id": "HSHLD_DK",
                "is_test_account": "TEST_ACCT_IN", "valid_from": "DW_EFF_DT", "valid_to": "DW_EXPR_DT", "is_current": "CURR_IN"},
    "flow": {"flow_id": "DW_TRAN_ID", "account_id": "DW_ACCT_ID", "source_system": "ACCT_SRCE_SYS_ID", "flow_date": "DW_TRAN_DT",
             "flow_type_code": "DW_TRAN_TYPE_CD", "amount_usd": "TRAN_AM", "nna_eligible_flag": "NNA_IN",
             "counterparty_account": "OFSET_ACCT_ID", "valid_from": "DW_EFF_DT", "valid_to": "DW_EXPR_DT", "is_current": "CURR_IN"},
    "month_end": {"month_end_date": "CLDR_EOM_DT", "release_date": "FIN_MTRC_RLSE_DT"},
    "office": {"office_code": "BRCH_CD", "office_name": "BRCH_NM", "region_code": "RGN_CD", "valid_from": "DW_EFF_DT",
               "valid_to": "DW_EXPR_DT", "is_current": "CURR_IN"},
    "ref_source_system": {"system_name": "SRCE_SYS_NM"},
}

KEYS: dict[str, dict] = {
    "ref_source_system": {"pk": "SRCE_SYS_ID", "fks": []},
    "month_end": {"pk": "CLDR_EOM_DT", "fks": []},
    "office": {"pk": "BRCH_CD, DW_EFF_DT", "fks": []},
    "account": {"pk": "DW_ACCT_ID, SRCE_SYS_ID, DW_EFF_DT", "fks": [("SRCE_SYS_ID", "SRCE_SYS_1", "SRCE_SYS_ID")]},
    "flow": {"pk": "DW_TRAN_ID, DW_EFF_DT", "fks": [("DW_TRAN_TYPE_CD", "TRAN_TYPE_1", "DW_TRAN_TYPE_CD"),
                                                    ("ACCT_SRCE_SYS_ID", "SRCE_SYS_1", "SRCE_SYS_ID"),
                                                    ("MNTH_END_DT", "MONTH_DIM_1", "CLDR_EOM_DT")]},
    "client_code_rollup": {"pk": "CLNT_CD, DW_EFF_DT", "fks": []},
    "calendar_date": {"pk": "CALENDAR_DATE", "fks": []},
    "account_segment": {"pk": "DW_ACCT_SEGMT_ID", "fks": []},
    "account_segment_enrollment": {"pk": "DW_ACCT_ID, SRCE_SYS_ID, DW_ACCT_SEGMT_ID, ACCT_ENRLMT_EFF_DT",
                                   "fks": [("DW_ACCT_SEGMT_ID", "D_ACCT_SEGMT", "DW_ACCT_SEGMT_ID")]},
    "core_offer": {"pk": "CORE_OFFER_DK", "fks": []},
    "core_offer_enrollment": {"pk": "OFFER_ORDER_ID", "fks": [("CORE_OFFER_DK", "D_CORE_OFFER", "CORE_OFFER_DK")]},
    "bank_deposit_account": {"pk": "DW_BANK_ACCT_ID", "fks": []},
    "brokerage_account_detail": {"pk": "ACCT_ID", "fks": []},
    "insurance_product": {"pk": "CUSIP_ID", "fks": []},
    "insurance_contract": {"pk": "CONTR_ID", "fks": [("PROD_CUSIP_ID", "INS_SEC_PROD_CD_1", "CUSIP_ID")]},
    "flow_excl_mf_clearing": {"pk": "DW_TRAN_ID, DW_EFF_DT", "fks": []},
    "nna_reference": {"pk": "DW_TRAN_ID", "fks": []},
}

SOURCE_CODES = {"BROKERAGE": 100, "BANK": 200, "MF_CLEARING": 300, "ADVISOR_SERVICES": 400, "STOCK_PLAN": 500,
                "INSURANCE": 600, "TRUST": 700}
OFFICE_PLACES = {"BOS": ("BOSTON", "MA", "02110"), "CHI": ("CHICAGO", "IL", "60603"), "NYC": ("NEW YORK", "NY", "10022"),
                 "SFO": ("SAN FRANCISCO", "CA", "94111")}
ROLLUP_LEVEL_4 = ("WLTH", "Wealth Management")

EXCHANGE_HOLIDAYS = {
    "2022-01-17", "2022-02-21", "2022-04-15", "2022-05-30", "2022-06-20", "2022-07-04", "2022-09-05", "2022-11-24", "2022-12-26",
    "2023-01-02", "2023-01-16", "2023-02-20", "2023-04-07", "2023-05-29", "2023-06-19", "2023-07-04", "2023-09-04", "2023-11-23",
    "2023-12-25", "2024-01-01", "2024-01-15", "2024-02-19", "2024-03-29", "2024-05-27", "2024-06-19", "2024-07-04", "2024-09-02",
    "2024-11-28", "2024-12-25", "2025-01-01", "2025-01-20", "2025-02-17", "2025-04-18", "2025-05-26", "2025-06-19", "2025-07-04",
    "2025-09-01", "2025-11-27", "2025-12-25", "2026-01-01", "2026-01-19", "2026-02-16", "2026-04-03", "2026-05-25", "2026-06-19",
    "2026-07-03", "2026-09-07", "2026-11-26", "2026-12-25"}
BANK_ONLY_HOLIDAYS = {"2022-10-10", "2022-11-11", "2023-10-09", "2024-10-14", "2024-11-11", "2025-10-13", "2025-11-11",
                      "2026-10-12", "2026-11-11"}
HOLIDAY_NAMES = {"01-01": "New Year's Day", "07-04": "Independence Day", "12-25": "Christmas Day", "06-19": "Juneteenth"}
CALENDAR_FROM, CALENDAR_TO = dt.date(2022, 1, 1), dt.date(2026, 12, 31)
RELEASE_LAG = 8


def fields(table: str) -> list[dict]:
    return REFERENCE[table]["fields"]


def schemas() -> dict[str, dict]:
    """The seventeen tables' DDL specs, by physical name, in KEYS' order: every field under its own name and type."""
    return {TABLES[table]: {"pk": spec["pk"], "columns": [(f["name"], f["type"], f["description"]) for f in fields(table)],
                            "fks": spec["fks"]} for table, spec in KEYS.items()}


def cast(kind: str, value):
    """A published sample value, or a derived one, as the column's type holds it."""
    if value is None:
        return None
    if kind == "DATE":
        return value if isinstance(value, dt.date) else dt.date.fromisoformat(str(value)[:10])
    if kind == "TIMESTAMP":
        if isinstance(value, dt.datetime):
            return value
        if isinstance(value, dt.date):
            return dt.datetime.combine(value, dt.time())
        return dt.datetime.fromisoformat(str(value))
    if kind == "BIGINT":
        return int(value)
    if kind.startswith("DECIMAL"):
        scale = int(kind.rstrip(")").split(",")[1])
        return Decimal(str(value)).quantize(Decimal(1).scaleb(-scale))
    return value if isinstance(value, str) else str(value)


class Filler:
    """SAGE column values for Meridian rows, from one seeded stream. It keeps the accounts' and flows' SAGE readings, which
    the other tables are built from."""

    def __init__(self, rng: random.Random):
        self.rng = rng
        self.accounts: list[dict] = []
        self.flows: list[dict] = []

    def draw(self, table: str, column: str):
        pool = REFERENCE[table]["pools"][column]
        if not pool["values"] or self.rng.random() < pool["nulls"]:
            return None
        return self.rng.choice(pool["values"])

    def values(self, table: str, names: list[str], derived: dict) -> dict:
        kinds = {f["name"]: f["type"] for f in fields(table)}
        return {n: cast(kinds[n], derived[n] if n in derived else self.draw(table, n)) for n in names}

    def row(self, table: str, derived: dict) -> tuple[tuple, dict]:
        """A table's row in its fields' order, and the row as SAGE reads it, by field."""
        values = self.values(table, [f["name"] for f in fields(table)], derived)
        return tuple(values.values()), values

    def full(self, table: str, derived: dict) -> tuple:
        return self.row(table, derived)[0]


def flag(value: bool) -> str:
    return "Y" if value else "N"


def business_day(d: dt.date) -> bool:
    return d.weekday() < 5 and d.isoformat() not in EXCHANGE_HOLIDAYS


def bank_day(d: dt.date) -> bool:
    return business_day(d) and d.isoformat() not in BANK_ONLY_HOLIDAYS


def month_last(d: dt.date) -> dt.date:
    return dt.date(d.year + (d.month == 12), d.month % 12 + 1, 1) - dt.timedelta(days=1)


def step(d: dt.date, by: int, test=business_day) -> dt.date:
    d += dt.timedelta(days=by)
    while not test(d):
        d += dt.timedelta(days=by)
    return d


def fiscal_start(d: dt.date) -> dt.date:
    """The first day of the fiscal year a date falls in: Meridian's runs from 1 April."""
    return dt.date(d.year - (d.month < 4), 4, 1)


@cache
def calendar() -> list[dict]:
    """DATE_DIM_1's days, computed: exchange and bank holidays, business days and Meridian's fiscal year from April."""
    days, d = [], CALENDAR_FROM
    while d <= CALENDAR_TO:
        days.append(d)
        d += dt.timedelta(days=1)
    rows = []
    for d in days:
        me, qs, ys = month_last(d), dt.date(d.year, (d.month - 1) // 3 * 3 + 1, 1), dt.date(d.year, 1, 1)
        qe = month_last(dt.date(d.year, (d.month - 1) // 3 * 3 + 3, 1))
        fqe, fye = qe, dt.date(fiscal_start(d).year + 1, 3, 31)
        published = d.replace(day=1) - dt.timedelta(days=1) + dt.timedelta(days=RELEASE_LAG)
        open_ = business_day(d)
        last_business = step(me + dt.timedelta(days=1), -1)
        holiday = d.isoformat() in EXCHANGE_HOLIDAYS or d.isoformat() in BANK_ONLY_HOLIDAYS
        friday = d + dt.timedelta(days=(4 - d.weekday()) % 7)
        week_end_business = step(friday + dt.timedelta(days=1), -1) if not business_day(friday) else friday
        rows.append({
            "CALENDAR_DATE": d, "CALENDAR_EOD_DATE_TS": dt.datetime.combine(d, dt.time(23, 59, 59)),
            "CALENDAR_DATE_TS": dt.datetime.combine(d, dt.time()), "YEAR_MONTH": d.year * 100 + d.month, "YEAR_VAL": d.year,
            "DAY_OF_YEAR": d.timetuple().tm_yday, "DAY_OF_WEEK": d.strftime("%A"), "DAY_OF_WEEK_NUMBER": d.isoweekday(),
            "WEEK_NUMBER": d.isocalendar()[1], "MONTH_DAY_NUMBER": d.day, "MONTH_NAME": d.strftime("%B"), "MONTH_NUMBER": d.month,
            "CALENDAR_MONTH_END_DAY": int(d == me), "QUARTER_NUMBER": (d.month - 1) // 3 + 1, "CALENDAR_QRTR_END_DAY": int(d == qe),
            "FISCAL_WEEK_END_DAY": int(d.weekday() == 4), "FISCAL_MONTH_END_DAY": int(d == me), "FISCAL_QRTR_END_DAY": int(d == fqe),
            "FISCAL_YEAR_END_DAY": int(d == fye), "BUSINESS_WEEK_END_DAY": int(d == week_end_business),
            "BUSINESS_MONTH_END_DAY": int(d == last_business), "BUSINESS_QRTR_END_DAY": int(d == step(qe + dt.timedelta(days=1), -1)),
            "BUSINESS_YEAR_END_DAY": int(d == step(dt.date(d.year, 12, 31) + dt.timedelta(days=1), -1)),
            "HR_PAY_PERIOD_END_DAY": int(d.day == 15 or d == me), "HR_MONTH_END_DAY": int(d == me), "HR_QRTR_END_DAY": int(d == qe),
            "HR_YEAR_END_DAY": int(d == dt.date(d.year, 12, 31)), "HOLIDAY": int(holiday), "WEEK_OF_MONTH_NUMBER": (d.day - 1) // 7 + 1,
            "EXCH_OPEN_IN": int(open_), "SETTL_DAY_IN": int(bank_day(d)), "FED_RSRV_BANK_OPEN_IN": int(bank_day(d)),
            "TIME_PRD_GRAIN_CD": "D", "PREV_EXCH_OPEN_DT": step(d, -1), "NXT_EXCH_OPEN_DT": step(d, 1),
            "RPTG_EXCH_OPEN_DT": d if open_ else step(d, -1), "PREV_MUB_WRK_DT": step(d, -1), "NEXT_MUB_WRK_DT": step(d, 1),
            "PREV_MUB_WRK_FRI_DT": previous_friday(d), "NEXT_MUB_WRK_FRI_DT": previous_friday(d) + dt.timedelta(days=7),
            "MUB_WRK_MTD_CT": business_days_from(d.replace(day=1), d), "MUB_WRK_QTD_CT": business_days_from(qs, d), "MUB_WRK_YTD_CT": business_days_from(ys, d),
            "MNTH_START_CLDR_DT": d.replace(day=1), "MNTH_TIME_PRD_GRAIN_CD": "M", "QRTR_START_CLDR_DT": qs,
            "QRTR_TIME_PRD_GRAIN_CD": "Q", "YEAR_START_CLDR_DT": ys, "YEAR_TIME_PRD_GRAIN_CD": "Y",
            "EXCH_DAY_CT": sum(business_day(x) for x in days_between(d.replace(day=1), me)), "MNTH_END_CLDR_DT": me,
            "CLDR_1_MOST_RCNT_DT": d - dt.timedelta(days=1), "CLDR_2_MOST_RCNT_DT": d - dt.timedelta(days=2),
            "SPCL_HLDY_NM": HOLIDAY_NAMES.get(d.strftime("%m-%d"), "Market holiday") if holiday else NA,
            "FIN_MTRC_PBLSHNG_DT_IN": flag(d == published),
        })
    return rows


def business_days_from(start: dt.date, end: dt.date) -> int:
    """The business days from start to end, both included."""
    return sum(business_day(x) for x in days_between(start, end))


def days_between(start: dt.date, end: dt.date) -> list[dt.date]:
    return [start + dt.timedelta(days=i) for i in range((end - start).days + 1)]


def previous_friday(d: dt.date) -> dt.date:
    back = (d.weekday() - 4) % 7 or 7
    return step(d - dt.timedelta(days=back) + dt.timedelta(days=1), -1)


def month_row(me: dt.date, booked_through: dt.date) -> dict:
    """MONTH_DIM_1's columns for a Meridian month end, computed from the calendar."""
    first = me.replace(day=1)
    days = days_between(first, me)
    open_days = [d for d in days if business_day(d)]
    bank_days = [d for d in days if bank_day(d)]
    busn = open_days[-1]
    open_month = booked_through.year * 12 + booked_through.month
    return {
        "CLDR_EOM_TS": dt.datetime.combine(me, dt.time(23, 59, 59)), "YEAR_MONTH": me.year * 100 + me.month,
        "YEAR_QTR": f"{me.year}Q{(me.month - 1) // 3 + 1}", "BUSN_EOM_TS": dt.datetime.combine(busn, dt.time(23, 59, 59)),
        "BUSN_EOM_DT": busn, "MNTH_START_CLDR_DT": first, "TIME_PRD_GRAIN_CD": "M", "FIRST_MUB_WRK_DAY_OF_MNTH_DT": open_days[0],
        "MNTH_NM": me.strftime("%B"), "MNTH_CD": me.strftime("%b").upper(), "MNTH_YYYY_TX": f"{me.strftime('%b').upper()}-{me.year}",
        "MNTH_OF_YEAR_CT": me.month, "DW_BRKRG_MNTH_FRM_TODAY_CT": me.year * 12 + me.month - open_month,
        "FIRST_EXCH_OPEN_DAY_OF_MNTH_DT": open_days[0], "LAST_EXCH_OPEN_DAY_OF_MNTH_DT": open_days[-1],
        "TOT_CLDR_DAY_OF_MNTH_NR": len(days), "TOT_EXCH_OPEN_DAY_OF_MNTH_NR": len(open_days),
        "TOT_BANK_OPEN_DAY_OF_MNTH_NR": len(bank_days), "TOT_MUB_WRK_DAY_OF_MNTH_NR": len(open_days),
        "TOT_MUB_NON_WRK_DAY_OF_MNTH_NR": len(days) - len(open_days),
        "QRTR_START_CLDR_DT": dt.date(me.year, (me.month - 1) // 3 * 3 + 1, 1), "QRTR_TIME_PRD_GRAIN_CD": "Q",
        "YEAR_START_CLDR_DT": dt.date(me.year, 1, 1), "YEAR_TIME_PRD_GRAIN_CD": "Y", "TOT_MNTH_EXCH_OPEN_DAY_CT": len(open_days),
    }


def add_months(d: dt.date, months: int) -> dt.date:
    index = d.year * 12 + d.month - 1 + months
    return min(dt.date(index // 12, index % 12 + 1, 1) + dt.timedelta(days=d.day - 1), month_last(dt.date(index // 12, index % 12 + 1, 1)))


def at(day: dt.date, hour: int, minute: int = 0) -> dt.datetime:
    return dt.datetime.combine(day, dt.time(hour, minute))


def literal(table: str) -> list[dict]:
    return REFERENCE[table]["rows"]


def month_end_rows(fill: Filler, months: list[tuple[dt.date, dt.date]], booked_through: dt.date) -> list[tuple]:
    """MONTH_DIM_1's rows for Meridian's month ends, each (month end, release date), computed from the calendar."""
    return [fill.full("month_end", {"CLDR_EOM_DT": me, "FIN_MTRC_RLSE_DT": released, **month_row(me, booked_through)})
            for me, released in months]


def office_rows(fill: Filler, rows: list[tuple], regions: dict[str, str]) -> list[tuple]:
    """BRCH_DIM_1's rows for Meridian's office versions, each (code, name, region, valid from, valid to, current)."""
    out = []
    for code, name, region, start, end, current in rows:
        city, state, postal = OFFICE_PLACES.get(code, (NA, NA, NA))
        out.append(fill.full("office", {
            "BRCH_CD": code, "BRCH_NM": name.upper(), "RGN_CD": region, "DW_EFF_DT": start, "DW_EXPR_DT": end or OPEN_END,
            "CURR_IN": int(current), "RCV_IN": int(current), "BRCH_OPEN_DT": start, "BRCH_CLOS_DT": end or OPEN_END,
            "BRCH_STAT_CD": "O" if current else "C", "BRCH_ORG_ID": f"ORG{code}", "RGN_NM": f"{regions[region].upper()} REGION",
            "PARNT_BRCH_CD": NA, "PARNT_BRCH_NM": NA, "PARNT_BRCH_ORG_ID": NA, "NEAREST_CMPY_MNGD_BRCH_CD": code,
            "CITY_NM": city, "STATE_CD": state, "POSTL_CD": postal, "EMAIL_ADDR_TX": f"branch.{code.lower()}@example.com",
            "SRCE_SYS_ID": SOURCE_CODES["BROKERAGE"], "INSERT_TS": at(start, 8), "DW_CREAT_TS": at(start, 8)}))
    return out


def source_system_rows(fill: Filler, codes: list[str]) -> list[tuple]:
    """SRCE_SYS_1's seven rows, as the reference publishes them, in the order of Meridian's codes for them."""
    sage_rows = {r["SRCE_SYS_ID"]: r for r in literal("ref_source_system")}
    return [fill.full("ref_source_system", {**sage_rows[SOURCE_CODES[code]]}) for code in codes]


def client_codes(leaves: list[str]) -> dict[str, list[str]]:
    """Three client codes a segment's accounts are booked under: CLIENT_CD_ROLLUP_1 rolls each up to its segment."""
    return {leaf: [f"{leaf[:3]}{k:02d}" for k in range(1, 4)] for leaf in leaves}


def account_rows(fill: Filler, versions: list[list], homes: dict[int, tuple], codes: dict[str, list[str]]) -> list[tuple]:
    """ACCT_DIM2_1's rows for Meridian's account versions, each (account_id, source_system, household_id, segment_code,
    is_test_account, valid_from, valid_to, is_current): the segment as the client code it is booked under. homes:
    household -> (office_code of its advisor, onboarded_on, primary member). The SAGE reading of each version is kept for
    the tables built on accounts."""
    span: dict[tuple, list] = {}
    for v in versions:
        s = span.setdefault((v[0], v[1]), [v[5], v[6], v])
        s[0] = min(s[0], v[5])
        s[1] = None if v[6] is None or s[1] is None else max(s[1], v[6])
        if v[5] >= s[2][5]:
            s[2] = v
    out = []
    for dk, v in enumerate(versions, 5_500_001):
        account, system, household, segment, test, start, end, current = v
        opened, closed, latest = span[(account, system)]
        office, onboarded, member = homes[household]
        code = codes[segment][account % len(codes[segment])]
        moment = fill.draw("account", "ACCT_OPEN_TM") or "09:30:00"
        derived = {
            "DW_ACCT_ID": account, "SRCE_SYS_ID": SOURCE_CODES[system], "HSHLD_DK": household, "TEST_ACCT_IN": flag(test),
            "DW_EFF_DT": start, "DW_EXPR_DT": end or OPEN_END, "CURR_IN": int(current), "RCV_IN": int(current),
            "SRCE_ACCT_ID": f"{SOURCE_CODES[system]}{account}", "LAST_RCV_HSHLD_DK": latest[2],
            "CUST_GRP_ID": 610_000 + household, "LAST_RCV_CUST_GRP_ID": 610_000 + latest[2], "ACCT_DOMCL_BRCH_CD": office,
            "CLNT_CD": code, "EFF_CLNT_CD": code, "ACCT_STAT_CD": "C" if closed is not None else "O",
            "ACCT_ACTIV_CD": "D" if closed is not None else "A", "ACCT_OPEN_DT": opened, "ACCT_OPEN_TM": moment,
            "ACCT_OPEN_TS": dt.datetime.combine(opened, dt.time.fromisoformat(moment)), "ACCT_CLOS_DT": closed or OPEN_END,
            "HSHLD_OPEN_DT": onboarded, "FIRM_HSHLD_OPEN_DT": onboarded, "EFF_HSHLD_OPEN_DT": onboarded, "EFF_ACCT_OPEN_DT": opened,
            "TXPYR_CUST_DK": member, "DW_ACCT_DK": dk, "DW_CREAT_TS": at(start, 22),
            **{f"ACCT_OPEN_{n}_DAY_EXPR_DT": opened + dt.timedelta(days=n) for n in (30, 90, 180, 365)},
            **{f"ACCT_OPEN_{n}_MNTH_EXPR_DT": add_months(opened, n) for n in (1, 3, 6, 12)},
        }
        row, reading = fill.row("account", derived)
        fill.accounts.append({**reading, "_v": v, "_office": office, "_latest": latest is v, "_closed": closed})
        out.append(row)
    return out


def account_on(fill: Filler, account: int, system: str, day: dt.date) -> dict:
    """The account's version on the day (SAGE's date poke), else its latest."""
    found = [a for a in fill.accounts if a["_v"][0] == account and a["_v"][1] == system]
    for a in found:
        v = a["_v"]
        if v[5] <= day and (v[6] is None or day <= v[6]):
            return a
    return next(a for a in found if a["_latest"])


def flow_rows(fill: Filler, flows: list[list], freeze_lag: int) -> list[tuple]:
    """TRAN_FACT_1's rows for Meridian's flow versions, each (flow_id, account_id, source_system, flow_date, flow_type_code,
    amount_usd, is_client_directed, nna_eligible_flag, counterparty_bank, counterparty_account, valid_from, valid_to,
    is_current): the amount a magnitude with its direction, the counterparty as the offset account and, at this
    institution, its source system. A version is in the month as frozen where it holds on the month's freeze date."""
    out = []
    for f in flows:
        fid, account, system, day, code, amount, directed, eligible, _, offset, start, end, current = f
        poke = account_on(fill, account, system, day)
        me = month_last(day)
        freeze = me + dt.timedelta(days=freeze_lag)
        security = code.startswith("SEC_")
        price = round(fill.rng.uniform(20, 600), 2) if security else 0
        derived = {
            "DW_TRAN_ID": fid, "DW_ACCT_ID": account, "ACCT_SRCE_SYS_ID": SOURCE_CODES[system], "DW_TRAN_DT": day,
            "DW_TRAN_TYPE_CD": code, "TRAN_AM": abs(amount), "NNA_IN": flag(eligible), "OFSET_ACCT_ID": offset if offset is not None else -1,
            "DW_EFF_DT": start, "DW_EXPR_DT": end or OPEN_END, "CURR_IN": int(current), "RCV_IN": int(current),
            "DW_ACCT_DK": poke["DW_ACCT_DK"], "MNTH_END_DT": me, "DW_TRAN_DIRCTN_CD": "I" if amount >= 0 else "O",
            "TRANS_SRCE_SYS_ID": SOURCE_CODES[system], "TRAN_EXEC_DT": day, "TRAN_SETTL_DT": day, "TRAN_EFF_DT": day,
            "TRAN_EXEC_TS": at(day, fill.rng.randint(9, 15), fill.rng.randint(0, 59)), "TRAN_ADD_TS": at(day, 18),
            "TRAN_PROC_TS": at(start, 22), "TRAN_SHR_QY": round(abs(amount) / price, 3) if security else 0,
            "TRAN_EXEC_PRICE_PER_SHR_AM": price, "OFSET_SRCE_SYS_ID": SOURCE_CODES[system] if code == "INT_TRF" else -1,
            "FIN_MTRC_RLSE_FRZN_IN": flag(start <= freeze and (end is None or end >= freeze)),
            "TXPYR_CUST_DK": poke["TXPYR_CUST_DK"], "HSHLD_DK": poke["HSHLD_DK"],
        }
        row, reading = fill.row("flow", derived)
        fill.flows.append({**reading, "_amount": amount, "_directed": directed, "_test": poke["_v"][4]})
        out.append(row)
    return out


def rollup_rows(fill: Filler, segments: list[tuple], codes: dict[str, list[str]]) -> list[tuple]:
    """CLIENT_CD_ROLLUP_1: each client code down its seven levels, the Meridian segment at level 6 and its parent at 5."""
    names = {code: name for code, name, _, _ in segments}
    parents = {code: parent for code, _, parent, _ in segments}
    out = []
    for leaf, leaf_codes in codes.items():
        for k, client in enumerate(leaf_codes, 1):
            levels = [("FIRM", "Total Firm"), ("RET", "Retail Clients"), ("RET2", "Advice Solutions"), ROLLUP_LEVEL_4,
                      (parents[leaf], names[parents[leaf]]), (leaf, names[leaf]), (client, f"{names[leaf]} {k}")]
            derived = {"CLNT_CD": client, "DW_EFF_DT": dt.date(2010, 1, 1), "DW_EXPR_DT": OPEN_END, "CURR_IN": 1, "RCV_IN": 1,
                       "FILL_PARNT_HIER_LVL_NR": 7}
            for n, (code, name) in enumerate(levels, 1):
                derived |= {f"SEGMT_LVL_ID{n}_INODE_IN": int(n < 7), f"SEGMT_LVL_ID{n}_FILL_IN": 0, f"SEGMT_LVL_ID{n}": code,
                            f"SEGMT_LVL_NM{n}": name, f"SEGMT_LVL_SHRT_NM{n}": name[:20], f"SEGMT_LVL_ULTRA_SHRT_NM{n}": code,
                            f"SEGMT_LVL_SORT_SEQ_NR{n}": n * 100 + k}
            out.append(fill.full("client_code_rollup", derived))
    return out


def latest_accounts(fill: Filler, system: str | None = None, live: bool = False) -> list[dict]:
    return [a for a in fill.accounts if a["_latest"] and (system is None or a["_v"][1] == system) and not (live and a["_v"][4])]


def enrollment_rows(fill: Filler, tiers: dict[str, int]) -> list[tuple]:
    """F_ACCT_SEGMT_ENRLMT: each account version's asset tier (its Meridian segment, so a re-segmentation is a new
    enrollment), and for each account a service model and sometimes a trader, retirement or lifecycle segment."""
    segments = {r["DW_ACCT_SEGMT_ID"]: r for r in literal("account_segment")}
    groups = dict(enumerate(["ASSET TIER", "SERVICE MODEL", "TRADER", "RETIREMENT", "LIFECYCLE"], 1))
    by_group: dict[int, list[int]] = {}
    for sid, r in segments.items():
        by_group.setdefault(r["DW_SEGMT_GRP_ID"], []).append(sid)
    rows = []

    def enrol(a: dict, sid: int, start: dt.date, end: dt.date | None, active: bool) -> None:
        group = segments[sid]["DW_SEGMT_GRP_ID"]
        rows.append(fill.full("account_segment_enrollment", {
            "DW_ACCT_ID": a["DW_ACCT_ID"], "SRCE_SYS_ID": a["SRCE_SYS_ID"], "DW_SEGMT_GRP_ID": group, "DW_ACCT_SEGMT_ID": sid,
            "ACCT_ENRLMT_EFF_DT": start, "ACCT_ENRLMT_EXPR_DT": end or OPEN_END, "ACCT_ENRLMT_ACTIV_IN": flag(active),
            "SEGMT_NM": segments[sid]["SEGMT_NM"], "SEGMT_GRP_NM": groups[group], "DW_SRCE_TBL_NM": "WEALTH_REPORTS.D_ACCT_SEGMT",
            "DW_CREAT_TS": at(start, 23, 15)}))

    for a in fill.accounts:
        v = a["_v"]
        if not v[4]:
            enrol(a, tiers[v[3]], v[5], v[6], v[7])
    for a in latest_accounts(fill, live=True):
        enrol(a, fill.rng.choice(by_group[2]), a["ACCT_OPEN_DT"], a["_closed"], a["_closed"] is None)
        if fill.rng.random() < 0.5:
            enrol(a, fill.rng.choice(by_group[fill.rng.choice([3, 4, 5])]), a["ACCT_OPEN_DT"], a["_closed"], a["_closed"] is None)
    return rows


def between(fill: Filler, start: dt.date, end: dt.date) -> dt.date:
    return start + dt.timedelta(days=fill.rng.randint(0, max((end - start).days, 0)))


def offer_rows(fill: Filler, through: dt.date) -> list[tuple]:
    """CORE_OFFER_ENRLMT_1: about two in five live accounts enrolled in a core offer."""
    offers = literal("core_offer")
    rows = []
    for a in latest_accounts(fill, live=True):
        if fill.rng.random() >= 0.4:
            continue
        offer, on = fill.rng.choice(offers), between(fill, max(a["ACCT_OPEN_DT"], dt.date(2018, 1, 1)), through)
        rows.append(fill.full("core_offer_enrollment", {
            "ACCT_ID": a["DW_ACCT_ID"], "ACCT_DK": a["DW_ACCT_DK"], "OFFER_ORDER_ID": 5_000_001 + len(rows), "DW_EFF_DT": on,
            "OFFER_ENRLMT_DT": on, "OFFER_ENRLMT_EXPR_DT": OPEN_END, "CUST_GRP_ID": a["CUST_GRP_ID"], "OFFER_ID": offer["OFFER_ID"],
            "CORE_OFFER_DK": offer["CORE_OFFER_DK"], "OFFER_NM": offer["OFFER_NM"], "DW_CREAT_TS": at(on, 23)}))
    return rows


def bank_rows(fill: Filler) -> list[tuple]:
    """BANK_DEP_ACCT_ADDR_1: every bank account, as it stands, with its mailing address."""
    return [fill.full("bank_deposit_account", {
        "DW_BANK_ACCT_ID": a["DW_ACCT_ID"], "DW_EFF_DT": a["ACCT_OPEN_DT"], "DW_EXPR_DT": a["_closed"] or OPEN_END,
        "CURR_IN": int(a["_closed"] is None), "RCV_IN": 1, "DEP_ACCT_NR": int(a["SRCE_ACCT_ID"]), "DEP_ACCT_NR_DK": 3_000_001 + n,
        "MAIL_CNTRY_CD": "US", "DW_CREAT_TS": at(a["ACCT_OPEN_DT"], 22)}) for n, a in enumerate(latest_accounts(fill, "BANK"))]


def brokerage_rows(fill: Filler) -> list[tuple]:
    """LGCY_ACCT_1: every brokerage account (source system 100), as it stands."""
    return [fill.full("brokerage_account_detail", {
        "ACCT_ID": a["DW_ACCT_ID"], "ACCT_DK": a["DW_ACCT_DK"], "DW_EFF_DT": a["ACCT_OPEN_DT"], "DW_EFF_TS": at(a["ACCT_OPEN_DT"], 0),
        "DW_EXPR_DT": a["_closed"] or OPEN_END, "DW_EXPR_TS": at(a["_closed"] or OPEN_END, 23, 59), "CURR_IN": int(a["_closed"] is None),
        "RCV_IN": 1, "LGCY_ACCT_BRCH_CD": a["_office"], "ACCT_ACTIV_CD": a["ACCT_ACTIV_CD"], "CLNT_CD": a["CLNT_CD"],
        "POSTL_CD": a["POSTL_CD"], "TXPYR_ID": a["PRIM_TXPYR_ID"], "ACCT_OPEN_DT": a["ACCT_OPEN_DT"], "VIP_CD": a["VIP_CD"],
        "HSHLD_ACCT_ID": a["HSHLD_DK"], "HSHLD_ACCT_DK": a["HSHLD_DK"]}) for a in latest_accounts(fill, "BROKERAGE")]


def contract_rows(fill: Filler, through: dt.date) -> list[tuple]:
    """INS_CONTR_1: insurance and annuity contracts distributed through about three in ten live brokerage accounts."""
    products = literal("insurance_product")
    rows = []
    for a in latest_accounts(fill, "BROKERAGE", live=True):
        if fill.rng.random() >= 0.3:
            continue
        product, kind = fill.rng.choice(products), fill.draw("insurance_contract", "CONTR_PROD_TYPE_CD") or "FIA"
        contract = f"{kind}{fill.rng.randint(10_000_000, 99_999_999)}"
        rows.append(fill.full("insurance_contract", {
            "CONTR_ID": contract, "NSCC_ID": product["NSCC_ID"], "DW_EFF_DT": between(fill, max(a["ACCT_OPEN_DT"], dt.date(2019, 1, 1)), through),
            "DW_EXPR_DT": OPEN_END, "CURR_IN": 1, "RCV_IN": 1, "PROD_CUSIP_ID": product["CUSIP_ID"], "END_RCV_CMPY_ID": str(product["NSCC_ID"]),
            "ORIG_CONTR_ID": contract, "CONTR_DIST_ACCT_ID": str(a["DW_ACCT_ID"]), "CONTR_PROD_TYPE_CD": kind}))
    return rows


def excl_rows(fill: Filler) -> list[tuple]:
    """TRAN_FACT_EXCL_MFCLR_G: the transactions not booked on mutual fund clearing (source system 300)."""
    return excl_rows_for(fill, len(fill.flows))


def excl_rows_for(fill: Filler, count: int) -> list[tuple]:
    """The last `count` transactions, for the transfer legs added after the initial extract."""
    if not count:
        return []
    names = [f["name"] for f in fields("flow_excl_mf_clearing")]
    return [fill.full("flow_excl_mf_clearing", {**{n: f[n] for n in names if n in f}, "TRAN_SRCE_SYS_ID": f["TRANS_SRCE_SYS_ID"]})
            for f in fill.flows[-count:] if f["ACCT_SRCE_SYS_ID"] != SOURCE_CODES["MF_CLEARING"]]


def nna_rows(fill: Filler) -> list[tuple]:
    """F_NNA_FIN: the reference's own net new assets extract, a certification reference only. It keeps the current,
    eligible transactions of live accounts, signed, and applies no exclusion window and no release rule: its
    derivation is unverified, and the governed figure is the pack's (contexts: nna)."""
    names = [f["name"] for f in fields("nna_reference")]
    return [fill.full("nna_reference", {
        **{n: f[n] for n in names if n in f}, "DW_TRAN_MNTH_END_DT": f["MNTH_END_DT"], "CLIENT_DIRECTED_NNA_IN": flag(f["_directed"]),
        "NNA_SHR_QY": f["TRAN_SHR_QY"], "NNA_EXEC_PRICE_PER_SHR_AM": f["TRAN_EXEC_PRICE_PER_SHR_AM"], "NNA_AM": f["_amount"],
        "FRZN_IN": f["FIN_MTRC_RLSE_FRZN_IN"], "MUB_SEC_ID": fill.rng.randint(9_000_000, 9_000_999) if f["TRAN_SHR_QY"] else -1})
        for f in fill.flows if f["CURR_IN"] and f["NNA_IN"] == "Y" and not f["_test"]]


def new_rows(fill: Filler, segments: list[tuple], codes: dict[str, list[str]], tiers: dict[str, int], through: dt.date) -> dict[str, list[tuple]]:
    """The rows of the twelve tables built on the accounts and flows, by business name, in KEYS' order."""
    return {
        "client_code_rollup": rollup_rows(fill, segments, codes),
        "calendar_date": [fill.full("calendar_date", day) for day in calendar()],
        "account_segment": [fill.full("account_segment", r) for r in literal("account_segment")],
        "account_segment_enrollment": enrollment_rows(fill, tiers),
        "core_offer": [fill.full("core_offer", r) for r in literal("core_offer")],
        "core_offer_enrollment": offer_rows(fill, through),
        "bank_deposit_account": bank_rows(fill),
        "brokerage_account_detail": brokerage_rows(fill),
        "insurance_product": [fill.full("insurance_product", r) for r in literal("insurance_product")],
        "insurance_contract": contract_rows(fill, through),
        "flow_excl_mf_clearing": excl_rows(fill),
        "nna_reference": nna_rows(fill),
    }
