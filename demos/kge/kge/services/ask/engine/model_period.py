"""Validate a model's period using the same types and calendar clauses as the planners."""
import datetime as dt
import re

from kge.adapters.deadline import RequestDeadlineExceeded
from kge.pipeline.sqlguard import build_allowlist
from kge.pipeline.trace import transient_failure
from kge.ports import AUTHORED
from kge.services.ask.engine.errors import WAREHOUSE_PROBLEM, warehouse_guard
from kge.services.ask.engine.question import Period, Request, asked_window, calendar_from_fiscal


def window(kind, value, calendar):
    """The interval a stored period represents, using the binding's calendar."""
    if kind == "fiscal_quarter":
        period = Period("calendar_quarter", calendar_from_fiscal(str(value), calendar["year_start_month"]))
    else:
        period = Period("month" if kind == "month" else "as_of", str(value))
    return asked_window(Request(question="", period=period), None)


def read(value) -> tuple[Period | None, str | None]:
    """Only complete, canonical dates enter a planner; ranges have no execution contract."""
    if value is None:
        return None, "The model did not place a period."
    if not isinstance(value, dict):
        return None, "The model's period must be an object."
    kind, text = value.get("kind"), value.get("value")
    if kind == "range":
        return None, "The planner cannot execute a period range or comparison. Ask for one day, month, quarter or year."
    if value.get("start") is not None or value.get("end") is not None:
        return None, "A single period cannot also carry range endpoints."
    if not isinstance(text, str):
        return None, "The model's period needs a date value."
    try:
        if kind == "day" and re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", text):
            day = dt.date.fromisoformat(text)
            dt.date(day.year + (day.month == 12), day.month % 12 + 1, 1)
            return Period("as_of", text), None
        if kind == "month" and re.fullmatch(r"[0-9]{4}-[0-9]{2}(?:-01)?", text):
            first = dt.date.fromisoformat(text[:7] + "-01")
            dt.date(first.year + (first.month == 12), first.month % 12 + 1, 1)
            return Period("month", first.isoformat()), None
        if kind == "quarter" and re.fullmatch(r"[0-9]{4}-Q[1-4]", text):
            dt.date(int(text[:4]), 1, 1)
            dt.date(int(text[:4]) + (text[-1] == "4"), 1, 1)
            return Period("calendar_quarter", text), None
        if kind == "year" and re.fullmatch(r"[0-9]{4}", text):
            dt.date(int(text), 1, 1)
            dt.date(int(text) + 1, 1, 1)
            return Period("year", text), None
    except ValueError:
        return None, "The model's period is not a real date or supported calendar interval."
    return None, "The model's period has an invalid date, grain or format."


def validate(engine, period: Period, targets: list[tuple[str, str, str]], cfg) -> str | None:
    """Check every grain before probing availability, after the caller has checked access.

    Availability probes use the period clauses and SQL gate, never a figure query or
    model SQL. An empty interval is not evidence for an all-time figure. Contexts
    with a release driver use their stronger booked/released-month checks instead.
    """
    probes = []
    seen = set()
    for unit, table, agg in dict.fromkeys(targets):
        binding = engine.PERIODS.get(f"{unit}.{table}")
        key = (unit, table, agg if binding and binding["kind"] == "snapshot" else None)
        if key in seen:
            continue
        seen.add(key)
        clause, joins, reason = engine.period_clause(unit, table, period, cfg, agg)
        if not clause:
            return reason or f"{unit}.{table} cannot apply that period."
        counted = binding and binding["kind"] in {"month", "fiscal_quarter"}
        counts = f"count(*), count(DISTINCT {table}.{binding['column']})" if counted else "count(*)"
        sql = f"SELECT {counts} FROM {table}" + "".join(" " + join for join in joins) + f" WHERE {clause}"
        columns = [(table, binding["column"])] if binding else []
        allow = build_allowlist(engine.g, unit, columns, engine.RESTRICTED_COLUMNS)
        probes.append((unit, table, sql, allow, binding))
    for unit, table, sql, allow, binding in probes:
        try:
            guard = warehouse_guard(engine.w.gate(unit, sql, allow, dialect=AUTHORED))
            if not guard["passed"]:
                return "Cannot check the period: " + "; ".join(guard["problems"])
            row = engine.w.row(unit, sql, AUTHORED)
            if not row or not row[0]:
                return f"{unit}.{table} holds no data for {period.label()}."
            wanted = asked_window(Request(question="", period=period), engine)
            if binding and binding["kind"] in {"month", "fiscal_quarter"}:
                lo, hi = (dt.date.fromisoformat(d) for d in wanted)
                months = (hi.year - lo.year) * 12 + hi.month - lo.month + 1
                expected = months if binding["kind"] == "month" else (4 if period.kind == "year" else 1)
                if row[1] != expected:
                    return f"{unit}.{table} does not hold the whole interval {period.label()}; expected {expected} periods, found {row[1]}."
            if binding and binding["kind"] in {"month", "fiscal_quarter", "snapshot"}:
                column = binding["column"]
                bounds = f"SELECT min({column}), max({column}) FROM {table}"
                guard = warehouse_guard(engine.w.gate(unit, bounds, allow, dialect=AUTHORED))
                if not guard["passed"]:
                    return "Cannot check the period: " + "; ".join(guard["problems"])
                lo, hi = engine.w.row(unit, bounds, AUTHORED)
                calendar = engine.CALENDARS[binding["calendar"]]
                first = window(binding["kind"], lo, calendar)[0]
                last = window(binding["kind"], hi, calendar)[1]
                if binding["kind"] == "fiscal_quarter" and period.kind == "year":
                    year, start = int(period.value), calendar["year_start_month"]
                    wanted = (dt.date(year, start, 1).isoformat(), (dt.date(year + 1, start, 1) - dt.timedelta(days=1)).isoformat())
                if wanted[0] < first or wanted[1] > last:
                    return f"{unit}.{table} does not hold the whole interval {period.label()}; data covers {first} to {last}."
        except RequestDeadlineExceeded:
            raise
        except Exception as exc:
            transient_failure(exc)
            return WAREHOUSE_PROBLEM
    return None
