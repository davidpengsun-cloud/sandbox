"""The deliberately small, non-executing DDL reader used by onboarding.

Uploaded SQL is parsed into a restricted model.  Only statements rebuilt from
that model (statements) are sent to a warehouse, through the estate's warehouse
port (kge/ports.py EstateWarehouses, the factory's: kge/adapters/warehouse/
open_estate): DuckDB, whose external access and extension installation are
disabled before any statement runs, or BigQuery, which reads the statements as
declared tables and runs none of them as written. Thus a rejected expression is
never given an opportunity to read a server file or execute an extension.

preview checks and counts a model's rows in a throwaway warehouse, build makes
a unit's warehouse (or a version of it) and scratch one to query; fill, invent
and load put rows in through a session, and catalogue reads the tables back,
their types named as DuckDB names them on both backends.
"""
from __future__ import annotations

import csv
import hashlib
import io
import random
import re
from contextlib import contextmanager
from datetime import date, timedelta

import sqlglot
from sqlglot import exp

from kge.ports import EstateWarehouses, WarehouseRefused, WarehouseSession

MAX_BYTES = 256 * 1024
MAX_NESTING = 100
MAX_TABLES = 100
MAX_COLUMNS = 100
MAX_CSV_BYTES = 1024 * 1024
MAX_CSV_ROWS = 100_000
NAME = re.compile(r"[A-Za-z][A-Za-z0-9_]*")
IDENT = re.compile(r"[A-Za-z][A-Za-z0-9_]{0,62}")
UNIT = re.compile(r"[a-z][a-z0-9_-]{0,23}")
RESERVED_UNITS = {"main", "temp", "system", "memory", "information_schema", "pg_catalog"}
TYPES = {"INT", "BIGINT", "SMALLINT", "DECIMAL", "NUMERIC", "DOUBLE", "FLOAT", "REAL", "VARCHAR", "TEXT", "DATE",
         "TIMESTAMP", "BOOLEAN"}
CONSTRAINTS = (exp.PrimaryKeyColumnConstraint, exp.NotNullColumnConstraint, exp.UniqueColumnConstraint, exp.Reference)


class DdlError(ValueError):
    pass


class DdlReferenceError(DdlError):
    pass


def render(model: dict) -> str:
    """Render a validated restricted model; callers parse it again before use."""
    unit, lines = model["unit"], [f"CREATE SCHEMA {model['unit']}"]
    for table in model["tables"]:
        parts = [f"{c['name']} {c['type']} {' '.join(c.get('constraints', []))}".rstrip() for c in table["columns"]]
        parts += table.get("constraints", [])
        lines.append(f"CREATE TABLE {unit}.{table['name']} ({', '.join(parts)})")
    for table in model["tables"]:
        if table.get("comment") is not None:
            lines.append(f"COMMENT ON TABLE {unit}.{table['name']} IS {exp.Literal.string(table['comment']).sql()}")
        for col in table["columns"]:
            if col.get("comment") is not None:
                lines.append(f"COMMENT ON COLUMN {unit}.{table['name']}.{col['name']} IS {exp.Literal.string(col['comment']).sql()}")
    return ";\n".join(lines) + ";\n"


def refused(n: int, kind: str, why: str = "is not in the onboarding DDL allowlist") -> DdlError:
    return DdlError(f"statement {n}, {kind}: refused: {why}")


def _name(value, n: int) -> str:
    name = value.name if hasattr(value, "name") else str(value)
    if not IDENT.fullmatch(name) or getattr(value, "quoted", False):
        raise refused(n, "identifier", f"{name!r} must match {IDENT.pattern}")
    return name


def validate_unit(unit: str) -> None:
    if not isinstance(unit, str) or not UNIT.fullmatch(unit):
        raise DdlError(f"unit {unit!r} must match {UNIT.pattern}")
    if unit in RESERVED_UNITS:
        raise DdlError(f"unit {unit!r} is reserved by DuckDB")


def parse(text: str, unit: str) -> dict:
    """Parse text into the safe catalogue model; no database is opened and nothing is written.

    Pathological nesting is refused before sqlglot reaches Python's recursion limit; the guard is deliberately
    conservative and counts parentheses in comments and strings too, which normal DDL stays far below."""
    if len(text.encode()) > MAX_BYTES:
        raise DdlError(f"DDL is larger than {MAX_BYTES} bytes")
    validate_unit(unit)
    depth = 0
    for char in text:
        if char == "(":
            depth += 1
            if depth > MAX_NESTING:
                raise DdlError(f"DDL nesting is deeper than {MAX_NESTING} parentheses")
        elif char == ")":
            depth = max(0, depth - 1)
    try:
        statements = sqlglot.parse(text, read="duckdb")
    except sqlglot.errors.SqlglotError as exc:
        raise refused(1, "SQL", str(exc).splitlines()[0]) from None
    except RecursionError:
        raise DdlError(f"DDL nesting is deeper than the supported limit of {MAX_NESTING} parentheses") from None
    statements = [statement for statement in statements if statement is not None]
    if not statements:
        raise DdlError("DDL contains no statements")
    tables, comments, references, schema_seen = [], [], [], False
    for n, statement in enumerate(statements, 1):
        if isinstance(statement, exp.Create) and statement.args.get("kind") == "SCHEMA":
            if schema_seen:
                raise refused(n, "a second schema")
            schema_seen = True
            schema = statement.this.db or statement.this.name
            if _name(exp.to_identifier(schema), n) != unit:
                raise refused(n, "CREATE SCHEMA", f"the schema must be named {unit}")
            continue
        if isinstance(statement, exp.Create) and statement.args.get("kind") == "TABLE":
            if statement.args.get("replace") or statement.args.get("exists") or statement.args.get("properties"):
                raise refused(n, "CREATE TABLE", "OR REPLACE, IF NOT EXISTS and temporary tables are not allowed")
            if statement.args.get("expression") is not None or not isinstance(statement.this, exp.Schema):
                raise refused(n, "CREATE TABLE AS SELECT")
            table = statement.this.this
            for identifier in statement.find_all(exp.Identifier):
                _name(identifier, n)
            if table.catalog:
                raise refused(n, "CREATE TABLE", "a catalogue qualifier is not allowed")
            db, name = table.db, _name(table.this, n)
            if db and _name(exp.to_identifier(db), n) != unit:
                raise refused(n, "CREATE TABLE", f"the schema must be named {unit}")
            for reference in statement.find_all(exp.Reference):
                target = reference.this.this
                target_name = _name(target.this, n)
                target_db = target.db
                if target.catalog:
                    raise DdlReferenceError(f"statement {n}, FOREIGN KEY: refused: a catalogue qualifier is not allowed")
                if target_db and _name(target_db, n) != unit:
                    raise DdlReferenceError(f"statement {n}, FOREIGN KEY: refused: the referenced schema must be named {unit}")
                target.set("db", exp.to_identifier(unit))
                references.append((n, target_name))
            columns, table_constraints = [], []
            for part in statement.this.expressions:
                if isinstance(part, exp.ColumnDef):
                    cname, dtype = _name(part.this, n), part.args.get("kind")
                    typename = dtype.this.value if hasattr(dtype.this, "value") else str(dtype.this)
                    if typename not in TYPES:
                        raise refused(n, f"type {typename}")
                    bad = [c.kind for c in part.args.get("constraints") or [] if not isinstance(c.kind, CONSTRAINTS)]
                    if bad:
                        label = ("DEFAULT" if any(isinstance(x, exp.DefaultColumnConstraint) for x in bad) else
                                 "CHECK" if any(isinstance(x, exp.CheckColumnConstraint) for x in bad) else type(bad[0]).__name__)
                        raise refused(n, label)
                    columns.append({"name": cname, "type": dtype.sql(dialect="duckdb"),
                                    "constraints": [c.sql(dialect="duckdb") for c in part.args.get("constraints") or []]})
                elif isinstance(part, (exp.PrimaryKey, exp.ForeignKey, exp.UniqueColumnConstraint)):
                    table_constraints.append(part.sql(dialect="duckdb"))
                else:
                    label = "CHECK" if isinstance(part, exp.CheckColumnConstraint) else type(part).__name__
                    raise refused(n, label)
            tables.append({"name": name, "columns": columns, "constraints": table_constraints, "comment": None})
            if len(columns) > MAX_COLUMNS:
                raise refused(n, "CREATE TABLE", f"a table may declare at most {MAX_COLUMNS} columns")
            if len(tables) > MAX_TABLES:
                raise DdlError(f"DDL may declare at most {MAX_TABLES} tables")
            continue
        if isinstance(statement, exp.Comment) and statement.args.get("kind") in ("TABLE", "COLUMN"):
            literal = statement.args.get("expression")
            if not isinstance(literal, exp.Literal) or not literal.is_string:
                raise refused(n, "COMMENT", "the comment must be a string literal")
            target = statement.this
            db = target.db
            if db and _name(exp.to_identifier(db), n) != unit:
                raise refused(n, "COMMENT", f"the schema must be named {unit}")
            table_name = target.table if statement.args["kind"] == "COLUMN" else target.name
            comments.append({"kind": statement.args["kind"], "table": _name(exp.to_identifier(table_name), n),
                             "column": _name(target.this, n) if statement.args["kind"] == "COLUMN" else None,
                             "text": literal.this})
            continue
        kind = statement.key.upper().replace("_", " ")
        if isinstance(statement, exp.Create):
            kind = f"CREATE {statement.args.get('kind') or kind}"
        elif isinstance(statement, exp.Command):
            command = statement.sql(dialect="duckdb").strip().upper()
            kind = "CREATE SECRET" if command.startswith("CREATE SECRET") else command.split(None, 1)[0]
        raise refused(n, kind)
    if not tables:
        raise DdlError("DDL declares no tables")
    known = {t["name"]: t for t in tables}
    for n, target in references:
        if target not in known:
            raise DdlReferenceError(f"statement {n}, FOREIGN KEY: refused: the referenced table {target} is not declared")
    for c in comments:
        if c["table"] not in known:
            raise DdlError(f"COMMENT names unknown table {c['table']}")
        if c["column"] is None:
            known[c["table"]]["comment"] = c["text"]
        else:
            col = next((x for x in known[c["table"]]["columns"] if x["name"] == c["column"]), None)
            if col is None:
                raise DdlError(f"COMMENT names unknown column {c['table']}.{c['column']}")
            col["comment"] = c["text"]
    return {"unit": unit, "tables": tables, "statements": len(statements)}


def parse_client(text: str) -> list[dict]:
    """Parse one new-client file into one ordinary onboarding model per schema.

    The ordinary parser remains the security boundary.  This dispatcher only
    separates statements by their declared schema, then submits each complete
    subset to :func:`parse`; it never executes the uploaded SQL.  A statement
    that names no declared schema goes through the ordinary parser first, so an
    otherwise unsupported statement gets its allowlist error.
    """
    if len(text.encode()) > MAX_BYTES:
        raise DdlError(f"DDL is larger than {MAX_BYTES} bytes")
    depth = 0
    for char in text:
        if char == "(":
            depth += 1
            if depth > MAX_NESTING:
                raise DdlError(f"DDL nesting is deeper than {MAX_NESTING} parentheses")
        elif char == ")":
            depth = max(0, depth - 1)
    try:
        parsed = [s for s in sqlglot.parse(text, read="duckdb") if s is not None]
    except (sqlglot.errors.SqlglotError, RecursionError) as exc:
        raise refused(1, "SQL", str(exc).splitlines()[0]) from None
    schemas: list[str] = []
    for n, statement in enumerate(parsed, 1):
        if isinstance(statement, exp.Create) and statement.args.get("kind") == "SCHEMA":
            name = _name(exp.to_identifier(statement.this.db or statement.this.name), n)
            validate_unit(name)
            if name in schemas:
                raise refused(n, "CREATE SCHEMA", f"schema {name} is declared twice")
            schemas.append(name)
    if not schemas:
        raise DdlError("new-client DDL declares no schemas")
    groups = {name: [] for name in schemas}
    for n, statement in enumerate(parsed, 1):
        if isinstance(statement, exp.Create) and statement.args.get("kind") == "SCHEMA":
            groups[statement.this.db or statement.this.name].append(statement)
            continue
        target = statement.this if isinstance(statement, exp.Comment) else (
            statement.this.this if isinstance(statement, exp.Create) and isinstance(statement.this, exp.Schema) else None)
        schema = target.db if target is not None else None
        if not schema or schema not in groups:
            parse(statement.sql(dialect="duckdb"), schemas[0])
            raise refused(n, statement.key.upper(), "every new-client statement must name a declared schema")
        groups[schema].append(statement)
    return [parse(";\n".join(s.sql(dialect="duckdb") for s in groups[name]), name) for name in schemas]


def statements(model: dict, qualified: bool = True) -> list[str]:
    """The statements that rebuild model; unqualified, the unit qualifier is removed from constraints (which hold only
    accepted identifiers and types) before comments are rendered, so comment text is data and is never rewritten as
    SQL."""
    def q(x):
        return f'"{x}"'
    prefix = f"{q(model['unit'])}." if qualified else ""
    out = [f"CREATE SCHEMA {q(model['unit'])}"] if qualified else []
    def constraint(text):
        return re.sub(rf'(?<![\w"]){re.escape(model["unit"])}\.', "", text) if not qualified else text
    for table in model["tables"]:
        parts = [f"{q(c['name'])} {c['type']} {' '.join(constraint(x) for x in c['constraints'])}".rstrip()
                 for c in table["columns"]]
        parts += [constraint(x) for x in table["constraints"]]
        out.append(f"CREATE TABLE {prefix}{q(table['name'])} ({', '.join(parts)})")
    for table in model["tables"]:
        if table.get("comment") is not None:
            out.append(f"COMMENT ON TABLE {prefix}{q(table['name'])} IS {sqlglot.exp.Literal.string(table['comment']).sql()}")
        for col in table["columns"]:
            if col.get("comment") is not None:
                out.append(f"COMMENT ON COLUMN {prefix}{q(table['name'])}.{q(col['name'])} IS "
                           f"{sqlglot.exp.Literal.string(col['comment']).sql()}")
    return out


def preview(model: dict, rows, estate: EstateWarehouses) -> dict:
    """The model's catalogue with its rows (rows: False or None for none, "invented", or {"csv": read_csvs's}) checked
    and counted in a throwaway warehouse nothing outside the process sees (the port's preview), its tables in the
    unit's schema."""
    with estate.preview(statements(model)) as session:
        fill(session, model, rows)
        return catalogue(session, model)


def stage(model: dict, estate: EstateWarehouses, rows, version: int | None = None) -> WarehouseSession:
    """The unit's warehouse (version, an onboarded unit's) made from the model and filled with its rows, staged beside
    any in place and open: the caller reads it, then publishes it (or discards it); a refusal discards it."""
    session = estate.stage(model["unit"], statements(model, qualified=False), version)
    try:
        fill(session, model, rows)
    except BaseException:
        session.close()
        estate.discard(model["unit"], version)
        raise
    return session


def build(model: dict, estate: EstateWarehouses, rows, version: int | None = None, inputs: str | None = None,
          keep: frozenset[int] = frozenset()) -> dict:
    """The unit's warehouse (version, an onboarded unit's) made from the model, with its rows, staged and published in
    place (stamped with inputs, a version keep names never replaced by rows from other inputs); its catalogue. A
    refusal leaves the warehouse in place as it was."""
    session = stage(model, estate, rows, version)
    try:
        made = catalogue(session, model)
        session.close()
        estate.publish(model["unit"], version, inputs, keep)
    except BaseException:
        session.close()
        estate.discard(model["unit"], version)
        raise
    return made


@contextmanager
def scratch(model: dict, estate: EstateWarehouses, rows):
    """A throwaway warehouse of the model, with its rows, to query; gone when the context ends."""
    with estate.scratch(model["unit"], statements(model, qualified=False)) as session:
        fill(session, model, rows)
        yield session


def fill(session: WarehouseSession, model: dict, rows) -> None:
    """The rows into a warehouse made from the model: invented, or the CSVs' (load); none for False or None."""
    if rows is True or rows == "invented":
        invent(session, model)
    elif isinstance(rows, dict) and set(rows) == {"csv"}:
        load(session, model, rows["csv"])
    elif rows is not None and rows is not False:
        raise DdlError(f"unknown row source {rows!r}")


def catalogue(session: WarehouseSession, model: dict) -> dict:
    """The model's tables as the warehouse holds them, in the model's order with its table comments: each table's rows,
    columns (name, type, comment, key), primary key and foreign keys."""
    held = []
    for table in model["tables"]:
        found = session.describe(table["name"])
        keys = found["primary_key"]
        foreign = found["foreign_keys"]
        def key_of(column):
            if keys and column in keys:
                return "primary key"
            match = next((f for f in foreign if column in f["columns"]), None)
            return (f"references {match['ref_table']}.{match['ref_columns'][match['columns'].index(column)]}" if match else None)
        held.append({"name": table["name"], "comment": table.get("comment"), "rows": found["rows"],
                     "columns": [{"name": c["name"], "type": c["type"], "comment": c["comment"], "key": key_of(c["name"])}
                                 for c in found["columns"]],
                     "primary_key": list(keys), "foreign_keys": foreign})
    return {"unit": model["unit"], "tables": held, "statements": len(statements(model))}


def invent(session: WarehouseSession, model: dict) -> None:
    """Insert deterministic, labelled-demo rows, parents before children (invented_rows)."""
    tables = catalogue(session, model)["tables"]
    by_name = {t["name"]: t for t in tables}
    for name, rows in invented_rows(model["unit"], tables):
        session.load(name, [c["name"] for c in by_name[name]["columns"]], rows)


def invented_rows(unit: str, tables: list[dict]) -> list[tuple[str, list[list]]]:
    """The deterministic, labelled-demo rows of each table, parents before children, as Python values before any cast:
    (table, rows) in the order they are inserted. tables: a catalogue's (catalogue, or described on the BigQuery path),
    each with its columns' types as DuckDB names them, its primary key and its foreign keys."""
    by_name = {t["name"]: t for t in tables}
    out = []
    referenced = {f["ref_table"] for t in tables for f in t["foreign_keys"]}
    pending = list(tables)
    while pending:
        ready = next((t for t in pending if all(f["ref_table"] not in {x["name"] for x in pending} for f in t["foreign_keys"])), pending[0])
        pending.remove(ready)
        n = 12 if ready["name"] in referenced else 120
        rows = []
        for k in range(1, n + 1):
            row = []
            for col in ready["columns"]:
                seed = int(hashlib.sha256(f"{unit}:{ready['name']}:{col['name']}:7".encode()).hexdigest()[:16], 16)
                rng = random.Random(seed + k)
                fk = next((f for f in ready["foreign_keys"] if col["name"] in f["columns"]), None)
                typ = col["type"].upper()
                if col["name"] in ready["primary_key"]:
                    value = k
                elif fk:
                    parent = by_name[fk["ref_table"]]
                    value = rng.randint(1, 12 if parent["name"] in referenced else 120)
                elif typ == "DATE":
                    value = date(2025, 1, 1) + timedelta(days=rng.randrange(181))
                elif typ.startswith(("DECIMAL", "NUMERIC")):
                    scale = int(re.search(r",\s*(\d+)\)", typ).group(1)) if "," in typ else 2
                    value = round(rng.uniform(50, 5000), scale)
                elif typ in ("INTEGER", "BIGINT", "SMALLINT"):
                    value = rng.randint(1, 60)
                elif typ in ("DOUBLE", "FLOAT", "REAL"):
                    value = rng.uniform(50, 5000)
                elif typ == "BOOLEAN":
                    value = bool(rng.randrange(2))
                elif typ == "TIMESTAMP":
                    value = date(2025, 1, 1) + timedelta(days=rng.randrange(181))
                else:
                    words = re.findall(r"\b[A-Z][A-Z0-9_]+\b", col.get("comment") or "")
                    value = rng.choice(words) if words else f"{col['name']}-{k}"
                row.append(value)
            rows.append(row)
        out.append((ready["name"], rows))
    return out


def read_csvs(model: dict, files: dict[str, bytes]) -> dict[str, list[list]]:
    """Validate and decode one UTF-8 CSV for every declared table."""
    tables = {t["name"]: t for t in model["tables"]}
    supplied = {name[:-4] if name.endswith(".csv") else name: data for name, data in files.items()}
    unknown = sorted(set(supplied) - set(tables))
    if unknown:
        raise DdlError(f"unknown table {unknown[0]} in CSV files")
    missing = sorted(set(tables) - set(supplied))
    if missing:
        raise DdlError(f"missing table {missing[0]}.csv")
    if sum(len(data) for data in supplied.values()) > MAX_CSV_BYTES:
        raise DdlError(f"CSV files are larger than {MAX_CSV_BYTES} bytes in all")
    result, total = {}, 0
    for name, table in tables.items():
        try:
            text = supplied[name].decode("utf-8")
        except UnicodeDecodeError as exc:
            raise DdlError(f"table {name}, line {exc.start + 1}: CSV is not UTF-8") from None
        reader = csv.reader(io.StringIO(text, newline=""))
        try:
            header = next(reader)
        except StopIteration:
            raise DdlError(f"table {name}, line 1: missing header") from None
        expected = [c["name"] for c in table["columns"]]
        if header != expected:
            raise DdlError(f"table {name}, line 1: header must be {', '.join(expected)}")
        held = []
        try:
            for line, row in enumerate(reader, 2):
                if len(row) != len(expected):
                    raise DdlError(f"table {name}, line {line}: expected {len(expected)} fields, got {len(row)}")
                held.append([None if value == "" else value for value in row])
                total += 1
                if total > MAX_CSV_ROWS:
                    raise DdlError(f"table {name}, line {line}: CSV files have more than {MAX_CSV_ROWS} rows in all")
        except csv.Error as exc:
            raise DdlError(f"table {name}, line {reader.line_num}: {exc}") from None
        result[name] = held
    return result


def load(session: WarehouseSession, model: dict, rows: dict[str, list[list]]) -> None:
    """Load already parsed values, with parents first; an orphan key is named before the warehouse's constraint error
    can obscure it, and a value or key the warehouse refuses is a DdlError naming its table."""
    cat = catalogue(session, model)
    by_name = {t["name"]: t for t in cat["tables"]}
    pending = list(cat["tables"])
    ordered = []
    while pending:
        names = {t["name"] for t in pending}
        ready = next((t for t in pending if all(f["ref_table"] not in names for f in t["foreign_keys"])), None)
        if ready is None:
            ready = pending[0]
        pending.remove(ready); ordered.append(ready)
    for table in ordered:
        columns = [c["name"] for c in table["columns"]]
        for fk in table["foreign_keys"]:
            child_ix = [columns.index(c) for c in fk["columns"]]
            parent = by_name[fk["ref_table"]]
            parent_cols = [c["name"] for c in parent["columns"]]
            parent_ix = [parent_cols.index(c) for c in fk["ref_columns"]]
            available = {tuple(r[i] for i in parent_ix) for r in rows[fk["ref_table"]]}
            for row in rows[table["name"]]:
                key = tuple(row[i] for i in child_ix)
                if not all(v is None for v in key) and key not in available:
                    label = ", ".join(f"{c}={v}" for c, v in zip(fk["columns"], key))
                    raise DdlError(f"table {table['name']}: orphan foreign key {label} references {fk['ref_table']}")
    for table in ordered:
        try:
            session.load(table["name"], [c["name"] for c in table["columns"]], rows[table["name"]])
        except WarehouseRefused as exc:
            raise DdlError(f"table {table['name']}: {str(exc).splitlines()[0]}") from None
