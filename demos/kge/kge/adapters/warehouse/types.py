"""Warehouse types and declared tables, in Python alone: what both warehouse adapters agree on without a database.

DuckDB names a column's type in its catalogue (duckdb_type); BigQuery holds it in its own type (bigquery_type), and
every catalogue either backend gives names it DuckDB's way, so what certification lands and the lock freezes is the
same on both. declared reads the tables a list of DDL statements makes (CREATE TABLE, COMMENT ON), as DuckDB would
hold them, with no database. cast and checked are what DuckDB does to a row on insert: each value cast to its column's
type, then the primary key, NOT NULL, UNIQUE and foreign keys checked; BigQuery, which enforces neither, has its rows
checked so before they load (kge/adapters/warehouse/bigquery_estate.py), and a refusal is the port's WarehouseRefused."""
from __future__ import annotations

import datetime as dt
import decimal
import math
import re
import struct

import sqlglot
from sqlglot import exp

from kge.ports import WarehouseRefused

DUCKDB_NAMES = {"INT": "INTEGER", "INT4": "INTEGER", "INTEGER": "INTEGER", "BIGINT": "BIGINT", "INT8": "BIGINT",
                "SMALLINT": "SMALLINT", "INT2": "SMALLINT", "DOUBLE": "DOUBLE", "FLOAT8": "DOUBLE", "FLOAT": "FLOAT",
                "REAL": "FLOAT", "FLOAT4": "FLOAT", "VARCHAR": "VARCHAR", "TEXT": "VARCHAR", "STRING": "VARCHAR",
                "DATE": "DATE", "TIMESTAMP": "TIMESTAMP", "BOOLEAN": "BOOLEAN", "BOOL": "BOOLEAN"}
BIGQUERY_ONLY = {"INT64": "BIGINT", "FLOAT64": "DOUBLE", "NUMERIC": "DECIMAL(38,9)"}
DECIMAL_TYPE = re.compile(r"(?:DECIMAL|NUMERIC)\s*(?:\(\s*(\d+)\s*(?:,\s*(\d+)\s*)?\))?")
INT_RANGE = {"SMALLINT": 2 ** 15, "INTEGER": 2 ** 31, "BIGINT": 2 ** 63}
TRUE_WORDS, FALSE_WORDS = {"true", "t", "1", "yes", "y"}, {"false", "f", "0", "no", "n"}
DATE_TEXT = re.compile(r"\s*(-?\d{1,6})[-/](\d{1,2})[-/](\d{1,2})(?:[ T].*)?\s*")
TIMESTAMP_TEXT = re.compile(r"\s*(-?\d{1,6})[-/](\d{1,2})[-/](\d{1,2})(?:[ T](\d{1,2}):(\d{2})(?::(\d{2})(?:\.(\d{1,6})\d*)?)?"
                            r"(?:\s*(?:Z|[+-]\d{2}(?::?\d{2})?))?)?\s*")


def duckdb_type(declared_type: str) -> str:
    """A declared column type as DuckDB's catalogue names it: INT is INTEGER, TEXT and VARCHAR(n) are VARCHAR, REAL is
    FLOAT, a bare DECIMAL or NUMERIC is DECIMAL(18,3) and DECIMAL(p) is DECIMAL(p,0). Both backends' catalogues name
    types so, so what certification lands, and the lock freezes, does not depend on the backend."""
    text = re.sub(r"\s+", " ", str(declared_type).strip().upper())
    found = DECIMAL_TYPE.fullmatch(text)
    if found:
        precision = int(found.group(1) or 18)
        scale = int(found.group(2) or (0 if found.group(1) else 3))
        return f"DECIMAL({precision},{scale})"
    base = re.sub(r"\s*\(.*\)$", "", text)
    if base not in DUCKDB_NAMES:
        raise ValueError(f"type {declared_type} is not a type a warehouse here holds")
    return DUCKDB_NAMES[base]


def catalogue_type(native: str) -> str:
    """A column's type as BigQuery's INFORMATION_SCHEMA names it, named DuckDB's way, as every catalogue here names types:
    INT64 is BIGINT, FLOAT64 DOUBLE, a bare NUMERIC DECIMAL(38,9) (BigQuery's own precision and scale), and the rest
    through duckdb_type and DUCKDB_NAMES (BOOL is BOOLEAN, STRING and STRING(n) VARCHAR, NUMERIC(p,s) DECIMAL(p,s)). A
    type DuckDB has no name for here (ARRAY<...>, STRUCT<...>, BYTES, DATETIME) keeps BigQuery's."""
    text = re.sub(r"\s+", " ", str(native).strip().upper())
    if text in BIGQUERY_ONLY:
        return BIGQUERY_ONLY[text]
    try:
        return duckdb_type(text)
    except ValueError:
        return str(native)


def bigquery_type(type_name: str) -> str:
    """The BigQuery column type a DuckDB type is held in: DECIMAL(p,s) as NUMERIC (its value already rounded to its
    scale), or BIGNUMERIC beyond NUMERIC's 29 integer and 9 fractional digits."""
    name = duckdb_type(type_name)
    found = DECIMAL_TYPE.fullmatch(name)
    if found:
        precision, scale = int(found.group(1)), int(found.group(2))
        return "NUMERIC" if scale <= 9 and precision - scale <= 29 else "BIGNUMERIC"
    return {"INTEGER": "INT64", "BIGINT": "INT64", "SMALLINT": "INT64", "DOUBLE": "FLOAT64", "FLOAT": "FLOAT64",
            "VARCHAR": "STRING", "DATE": "DATE", "TIMESTAMP": "TIMESTAMP", "BOOLEAN": "BOOL"}[name]


def _reference(node) -> tuple[str, list[str]]:
    target = node.this
    table = target.this if isinstance(target, exp.Schema) else target
    return table.name, ([e.name for e in target.expressions] if isinstance(target, exp.Schema) else [])


def declared(statements: list[str]) -> list[dict]:
    """The tables the statements make, in order, as the warehouse holds them: each with its comment, its columns (name,
    type as DuckDB names it, comment, not_null, unique), its primary key, its foreign keys (columns, referenced table,
    referenced columns: its primary key where the reference names none) and its UNIQUE keys. CREATE SCHEMA is skipped,
    and a schema qualifier dropped: a warehouse is one unit's."""
    tables: dict[str, dict] = {}
    comments = []
    for sql in statements:
        statement = sqlglot.parse_one(sql, read="duckdb")
        if isinstance(statement, exp.Create) and statement.args.get("kind") == "TABLE":
            columns, primary, foreign, unique = [], [], [], []
            for part in statement.this.expressions:
                if isinstance(part, exp.ColumnDef):
                    column = {"name": part.this.name, "type": duckdb_type(part.args["kind"].sql(dialect="duckdb")),
                              "comment": None, "not_null": False, "unique": False}
                    for constraint in part.args.get("constraints") or []:
                        kind = constraint.kind
                        if isinstance(kind, exp.PrimaryKeyColumnConstraint):
                            primary = [column["name"]]
                        elif isinstance(kind, exp.NotNullColumnConstraint) and not kind.args.get("allow_null"):
                            column["not_null"] = True
                        elif isinstance(kind, exp.UniqueColumnConstraint):
                            column["unique"] = True
                            unique.append([column["name"]])
                        elif isinstance(kind, exp.Reference):
                            ref_table, ref_columns = _reference(kind)
                            foreign.append({"columns": [column["name"]], "ref_table": ref_table, "ref_columns": ref_columns})
                    columns.append(column)
                elif isinstance(part, exp.PrimaryKey):
                    primary = [e.name for e in part.expressions]
                elif isinstance(part, exp.ForeignKey):
                    ref_table, ref_columns = _reference(part.args["reference"])
                    foreign.append({"columns": [e.name for e in part.expressions], "ref_table": ref_table,
                                    "ref_columns": ref_columns})
                elif isinstance(part, exp.UniqueColumnConstraint):
                    unique.append([e.name for e in part.this.expressions])
            for column in columns:
                column["not_null"] = column["not_null"] or column["name"] in primary
            name = statement.this.this.name
            tables[name] = {"name": name, "comment": None, "columns": columns, "primary_key": primary,
                            "foreign_keys": foreign, "unique": unique}
        elif isinstance(statement, exp.Comment) and statement.args.get("kind") in ("TABLE", "COLUMN"):
            text = statement.args["expression"].this
            if statement.args["kind"] == "TABLE":
                comments.append((statement.this.name, None, text))
            else:
                comments.append((statement.this.table, statement.this.name, text))
    for table, column, text in comments:
        if column is None:
            tables[table]["comment"] = text
        else:
            next(c for c in tables[table]["columns"] if c["name"] == column)["comment"] = text
    keys = {t["name"]: t["primary_key"] for t in tables.values()}
    for table in tables.values():
        for fk in table["foreign_keys"]:
            if not fk["ref_columns"]:
                fk["ref_columns"] = list(keys.get(fk["ref_table"], []))
    return list(tables.values())


def _decimal_text(value) -> decimal.Decimal:
    if isinstance(value, bool):
        raise ValueError(value)
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError(value)
        return decimal.Decimal(repr(value))
    if isinstance(value, (int, decimal.Decimal)):
        return decimal.Decimal(value)
    text = str(value).strip()
    if not re.fullmatch(r"[+-]?(\d+\.?\d*|\.\d+)([eE][+-]?\d+)?", text):
        raise ValueError(value)
    return decimal.Decimal(text)


def cast(value, type_name: str):
    """One value cast to a column's type as DuckDB casts it on insert, or ValueError where DuckDB refuses it: an integer
    rounds a fraction half away from zero and must fit its width, a DECIMAL(p,s) rounds to its scale and must fit its
    precision, a BOOLEAN reads true/false, t/f, 1/0, yes/no and y/n, a DATE reads YYYY-MM-DD (or with /, and any time
    part dropped), a FLOAT is held in single precision. NULL stays NULL."""
    if value is None:
        return None
    name = duckdb_type(type_name)
    found = DECIMAL_TYPE.fullmatch(name)
    if found:
        precision, scale = int(found.group(1)), int(found.group(2))
        number = _decimal_text(value).quantize(decimal.Decimal(1).scaleb(-scale), rounding=decimal.ROUND_HALF_UP)
        if len(number.as_tuple().digits) - max(0, -number.as_tuple().exponent) > precision - scale and number != 0:
            raise ValueError(value)
        return number
    if name in INT_RANGE:
        number = int(_decimal_text(value).to_integral_value(rounding=decimal.ROUND_HALF_UP))
        if not -INT_RANGE[name] <= number < INT_RANGE[name]:
            raise ValueError(value)
        return number
    if name in ("DOUBLE", "FLOAT"):
        if isinstance(value, bool):
            raise ValueError(value)
        number = float(value) if isinstance(value, (int, float, decimal.Decimal)) else float(str(value).strip())
        return struct.unpack("f", struct.pack("f", number))[0] if name == "FLOAT" else number
    if name == "BOOLEAN":
        if isinstance(value, bool):
            return value
        text = str(value).strip().lower()
        if text in TRUE_WORDS or text in FALSE_WORDS:
            return text in TRUE_WORDS
        raise ValueError(value)
    if name == "DATE":
        if isinstance(value, dt.datetime):
            return value.date()
        if isinstance(value, dt.date):
            return value
        parts = DATE_TEXT.fullmatch(str(value))
        if not parts:
            raise ValueError(value)
        return dt.date(int(parts.group(1)), int(parts.group(2)), int(parts.group(3)))
    if name == "TIMESTAMP":
        if isinstance(value, dt.datetime):
            return value.replace(tzinfo=None)
        if isinstance(value, dt.date):
            return dt.datetime(value.year, value.month, value.day)
        parts = TIMESTAMP_TEXT.fullmatch(str(value))
        if not parts:
            raise ValueError(value)
        year, month, day, hour, minute, second, fraction = parts.groups()
        return dt.datetime(int(year), int(month), int(day), int(hour or 0), int(minute or 0), int(second or 0),
                           int((fraction or "0").ljust(6, "0")))
    if isinstance(value, (dt.date, dt.datetime)):
        return value.isoformat(sep=" ") if isinstance(value, dt.datetime) else value.isoformat()
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def checked(table: dict, columns: list[str], rows: list[list], held: dict[str, list[list]]) -> list[list]:
    """A table's rows, given for columns (a leading part of its columns, the rest NULL), as DuckDB inserts them: each
    value cast (cast), then NOT NULL, the primary key, UNIQUE and every foreign key checked, against the rows already
    held (held, by table, every column) and these. Every column's values, in the table's order. WarehouseRefused names
    the first value or key DuckDB would refuse."""
    names = [c["name"] for c in table["columns"]]
    if columns != names[:len(columns)]:
        raise WarehouseRefused(f"table {table['name']}: rows name columns {', '.join(columns)}, not its leading ones")
    typed = []
    for n, row in enumerate(rows, 1):
        values = []
        for column, value in zip(table["columns"], list(row) + [None] * (len(names) - len(row))):
            try:
                values.append(cast(value, column["type"]))
            except (ValueError, ArithmeticError, OverflowError):
                raise WarehouseRefused(f"Conversion Error: Could not convert {str(value)!r} to {column['type']} "
                                       f"(table {table['name']}, column {column['name']}, row {n})") from None
            if values[-1] is None and column["not_null"]:
                raise WarehouseRefused(f"Constraint Error: NOT NULL constraint failed: {table['name']}.{column['name']}")
        typed.append(values)
    every = (held.get(table["name"]) or []) + typed
    for key, what in [(table["primary_key"], "primary key"), *((u, "unique") for u in table.get("unique") or [])]:
        if not key:
            continue
        seen, index = set(), [names.index(c) for c in key]
        for values in every:
            found = tuple(values[i] for i in index)
            if what == "unique" and any(v is None for v in found):
                continue
            if found in seen:
                label = ", ".join(f"{c}: {v}" for c, v in zip(key, found))
                raise WarehouseRefused(f'Constraint Error: Duplicate key "{label}" violates {what} constraint')
            seen.add(found)
    return typed


def orphans(tables: list[dict], held: dict[str, list[list]]) -> None:
    """Every foreign key of the held rows (every column, by table) checked against its parent's rows, as DuckDB checks
    one on insert; WarehouseRefused names the first that references no row."""
    by_name = {t["name"]: t for t in tables}
    for table in tables:
        names = [c["name"] for c in table["columns"]]
        for fk in table["foreign_keys"]:
            parent = by_name.get(fk["ref_table"])
            if parent is None:
                continue
            parent_names = [c["name"] for c in parent["columns"]]
            available = {tuple(r[parent_names.index(c)] for c in fk["ref_columns"]) for r in held.get(parent["name"], [])}
            for values in held.get(table["name"], []):
                key = tuple(values[names.index(c)] for c in fk["columns"])
                if not all(v is None for v in key) and key not in available:
                    label = ", ".join(f"{c}: {v}" for c, v in zip(fk["columns"], key))
                    raise WarehouseRefused(f'Constraint Error: Violates foreign key constraint because key "{label}" does '
                                           f"not exist in the referenced table {fk['ref_table']}")
