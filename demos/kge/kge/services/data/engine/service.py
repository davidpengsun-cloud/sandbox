from __future__ import annotations

import re
import time
from dataclasses import replace

from sqlglot import exp, parse
from sqlglot.errors import SqlglotError

from kge.pipeline import trace
from kge.pipeline.settings import data_export_row_cap
from kge.pipeline.sqlguard import check, schema_allowlist, warehouse_names
from kge.ports import AUTHORED, QueryLimits, QueryRefused
from kge.services import identity
from kge.services.ask import traced
from kge.services.data.engine.limits import bounded, complexity
from kge.services.data.engine.models import ExportResponse, RowsResponse
from kge.services.data.engine.governance import constrain, declared_allowlist

QUERY_ROW_CAP = 1000


class DataRefused(ValueError):
    def __init__(self, status: int, detail: str):
        super().__init__(detail)
        self.status = status
        self.detail = detail


def preview(engine, who: str, unit: str, table: str, limit: int = 20) -> RowsResponse:
    """Return the first requested rows of one governed product."""
    tables = {table}
    def call() -> dict:
        if limit < 1 or limit > 100:
            raise DataRefused(422, "limit must be between 1 and 100")
        allow = product_allowlist(engine, who, unit, table)
        columns = sorted(allow.columns[table])
        if not columns:
            raise DataRefused(403, "this product has no columns available to read")
        sql = exp.select(*(exp.column(column, table=table) for column in columns)).from_(table).limit(limit).sql(AUTHORED)
        sql, allow = governed(engine, unit, sql, allow, AUTHORED, tables)
        return execute(engine, unit, sql, allow, AUTHORED, limit)
    return RowsResponse(**read_trace(engine, who, unit, tables, "preview", call))


def export(engine, who: str, unit: str, table: str) -> ExportResponse:
    """Return a capped governed product export."""
    cap = data_export_row_cap()
    tables = {table}

    def call() -> dict:
        allow = product_allowlist(engine, who, unit, table)
        columns = sorted(allow.columns[table])
        if not columns:
            raise DataRefused(403, "this product has no columns available to export")
        sql = exp.select(*(exp.column(column, table=table) for column in columns)).from_(table).limit(cap).sql(AUTHORED)
        sql, allow = governed(engine, unit, sql, allow, AUTHORED, tables)
        return {**execute(engine, unit, sql, allow, AUTHORED, cap), "filename": safe_filename(unit, table)}
    return ExportResponse(**read_trace(engine, who, unit, tables, "export", call))


def query(engine, who: str, unit: str, sql: str) -> RowsResponse:
    """Run one read-only query in the warehouse dialect, capped at QUERY_ROW_CAP rows."""
    deadline = time.monotonic() + QueryLimits().seconds
    tables = set()
    referenced = set()
    def call() -> dict:
        complexity(sql, engine.w.sql_dialect)
        if re.match(r"\s*TABLE\s+", sql, re.IGNORECASE):
            try:
                named = parse(re.sub(r"^\s*TABLE\s+", "SELECT * FROM ", sql, count=1, flags=re.IGNORECASE),
                              read=engine.w.sql_dialect)
                referenced.update(".".join(part.name for part in t.parts) for item in named if item is not None
                                  for t in item.find_all(exp.Table))
            except (SqlglotError, RecursionError):
                pass
        try:
            statements = [tree for tree in parse(sql, read=engine.w.sql_dialect) if tree is not None]
            if not statements:
                raise DataRefused(400, "The query does not parse; use one read-only SELECT statement.")
            referenced.update(".".join(part.name for part in t.parts) for tree in statements for t in tree.find_all(exp.Table))
            readable(engine, who, unit)
            allow = declared_allowlist(engine, unit, schema_allowlist(engine.g, unit, engine.RESTRICTED_COLUMNS))
            statements = [warehouse_names(tree, allow, engine.w.sql_dialect) for tree in statements]
            statement = statements[0]
            ctes = {c.alias_or_name for c in statement.find_all(exp.CTE)}
            tables.update(t.name for t in statement.find_all(exp.Table) if t.name not in ctes)
            if any(node.args.get("recursive") for node in statement.find_all(exp.With)):
                raise DataRefused(400, "recursive queries are not allowed")
            problems = check(sql, allow, dialect=engine.w.sql_dialect, inner_only=True)
            if problems:
                raise DataRefused(400, "; ".join(problems))
            held_limit = statement.args.get("limit")
            requested = QUERY_ROW_CAP
            if held_limit is not None and isinstance(held_limit.expression, exp.Literal) and held_limit.expression.is_int:
                requested = min(int(held_limit.expression.this), QUERY_ROW_CAP)
            limited = statement.limit(requested, copy=True).sql(engine.w.sql_dialect)
            limited, allow = governed(engine, unit, limited, allow, engine.w.sql_dialect, tables)
        except (SqlglotError, RecursionError):
            raise DataRefused(400, "The query does not parse; use one read-only SELECT statement.") from None
        return execute(engine, unit, limited, allow, engine.w.sql_dialect, QUERY_ROW_CAP, deadline)
    def limited_call():
        try:
            with bounded(deadline):
                return call()
        except QueryRefused as exc:
            raise DataRefused(400, str(exc)) from None
    return RowsResponse(**read_trace(engine, who, unit, tables, "query", limited_call, referenced))


def governed(engine, unit, sql, allow, dialect, tables):
    try:
        return constrain(engine, unit, sql, allow, dialect, tables)
    except PermissionError as exc:
        raise DataRefused(403, str(exc)) from None
    except (ValueError, SqlglotError) as exc:
        raise DataRefused(400, str(exc)) from None


def read_trace(engine, who, unit, tables, kind, call, referenced=None):
    def recorded():
        try:
            result = call()
        except DataRefused as exc:
            trace.data_read(who, unit, list(referenced or tables), kind, 0, True, exc.detail)
            return {"refusal": exc}
        except Exception:
            trace.data_read(who, unit, list(referenced or tables), kind, 0, True, "The data read failed.")
            raise
        trace.data_read(who, unit, list(tables), kind, result["row_count"], False)
        return result
    result = traced(f"data-{kind}", recorded, graph=engine.g)
    if "refusal" in result:
        refusal = result["refusal"]
        refusal.run_trace = result.get("run_trace", {})
        raise refusal
    return result


def readable(engine, who: str, unit: str) -> None:
    if unit not in identity.readable_units(engine, who) or unit not in engine.UNIT_ORDER:
        raise DataRefused(404, "product not found")


def product_allowlist(engine, who: str, unit: str, table: str):
    readable(engine, who, unit)
    allow = schema_allowlist(engine.g, unit, engine.RESTRICTED_COLUMNS)
    if table not in allow.tables:
        raise DataRefused(404, "product not found")
    return allow


def execute(engine, unit: str, sql: str, allow, dialect: str, cap: int, deadline: float | None = None) -> dict:
    limits = QueryLimits(rows=cap)
    deadline = deadline if deadline is not None else time.monotonic() + limits.seconds
    limits = replace(limits, deadline=deadline)
    try:
        guard = engine.w.gate(unit, sql, allow, dialect=dialect, limits=limits)
        if not guard["passed"]:
            detail = "; ".join(guard["problems"]) or "the query was refused"
            raise DataRefused(400, detail)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise QueryRefused(f"query exceeded the {limits.seconds:g} second time limit")
        columns, values = engine.w.query(unit, sql, dialect=dialect, limits=limits)
        if time.monotonic() >= deadline:
            raise QueryRefused(f"query exceeded the {limits.seconds:g} second time limit")
    except QueryRefused as exc:
        raise DataRefused(400, str(exc)) from None
    rows = [dict(zip(columns, row)) for row in values[:cap]]
    return {"columns": columns, "rows": rows, "row_count": len(rows), "capped_at": cap}


def safe_filename(unit: str, table: str) -> str:
    stem = re.sub(r"[^A-Za-z0-9._-]+", "-", f"{unit}-{table}").strip("-.") or "data"
    return f"{stem}.csv"
