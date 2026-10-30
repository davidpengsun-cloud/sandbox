"""A stand-in for BigQuery, for the tests of the warehouses' BigQuery path (kge/adapters/warehouse/bigquery_estate.py):
it records every statement and load job the shared client's loader (kge/adapters/bigquery.py BigQueryLoader) is asked to
send, and serves rows by running each statement, read as BigQuery's dialect and written as DuckDB's, in a DuckDB of its
own in memory, answering in the REST API's shape (every value text, NUMERIC with no trailing zero, TIMESTAMP in
microseconds, as useInt64Timestamp asks), and failing as the loader reports BigQuery's errors (a RuntimeError; a missing
table's says Not found). Nothing reaches the network and no file is written.

install(monkeypatch) puts it behind every BigQueryLoader. block_duckdb(monkeypatch) then takes DuckDB away from every
module that imports it after that (sys.modules["duckdb"] = None), so a step on the BigQuery path that reached for it
fails loudly; the stand-in keeps the module it imported first. What it cannot show is BigQuery's own semantics: its
dialect is read by sqlglot and run by DuckDB, so whether BigQuery itself accepts each statement is only seen live, but
for one rule DuckDB does not keep: a bare name that is a table's alias in its scope is that table's row (_no_row_read)."""
from __future__ import annotations

import datetime as dt
import decimal
import re
import sys

import duckdb
import pandas as pd
import sqlglot
from sqlglot import exp
from sqlglot.optimizer.scope import traverse_scope

from kge.adapters.bigquery import BigQueryLoader

PROJECT = "pilot-project"
TYPES = {"BIGINT": "INTEGER", "INTEGER": "INTEGER", "HUGEINT": "INTEGER", "SMALLINT": "INTEGER", "DOUBLE": "FLOAT",
         "FLOAT": "FLOAT", "VARCHAR": "STRING", "DATE": "DATE", "BOOLEAN": "BOOLEAN", "TIMESTAMP": "TIMESTAMP"}
COLUMN_TYPES = {"NUMERIC": "DECIMAL(38,9)", "BIGNUMERIC": "DECIMAL(38,9)", "INT64": "BIGINT", "FLOAT64": "DOUBLE",
                "STRING": "VARCHAR", "BOOL": "BOOLEAN", "DATE": "DATE", "TIMESTAMP": "TIMESTAMP"}


class FakeBigQuery:
    """One project's datasets, every table in one DuckDB in memory by its table id."""

    def __init__(self):
        self.con = duckdb.connect(":memory:")
        self.statements: list[str] = []
        self.loads: list[tuple[str, int, str]] = []
        self.labels: dict[str, dict[str, str]] = {}
        self.datasets: set[str] = set()

    def _unqualified(self, tree):
        for table in tree.find_all(exp.Table):
            table.set("catalog", None)
            table.set("db", None)
        return tree

    def _create(self, sql: str, tree) -> None:
        name = tree.this.this.name
        columns = []
        for part in tree.this.expressions:
            if isinstance(part, exp.ColumnDef):
                kind = re.sub(r"\(.*\)$", "", part.args["kind"].sql("bigquery").upper())
                columns.append(f'"{part.this.name}" {COLUMN_TYPES.get(kind, "VARCHAR")}')
        exists = tree.args.get("exists")
        self.con.execute(f'CREATE {"TABLE IF NOT EXISTS" if exists else "TABLE"} "{name}" ({", ".join(columns)})')
        found = re.search(r"labels=\[(.*?)\]", sql)
        if found and not (exists and name in self.labels):
            self.labels[name] = dict(re.findall(r"\('([^']*)', '([^']*)'\)", found.group(1)))
        else:
            self.labels.setdefault(name, {})

    def _answer(self, result) -> dict:
        if result is None or result.description is None:
            return {"jobComplete": True, "schema": {"fields": []}, "rows": []}
        fields = []
        for name, kind, *_ in result.description:
            text = str(kind).upper()
            fields.append({"name": name, "type": "NUMERIC" if text.startswith("DECIMAL") else TYPES.get(text, "STRING"),
                           "mode": "REPEATED" if text.endswith("[]") else "NULLABLE"})
        rows = []
        for row in result.fetchall():
            rows.append({"f": [{"v": self._text(v)} for v in row]})
        return {"jobComplete": True, "schema": {"fields": fields}, "rows": rows}

    @staticmethod
    def _text(value):
        if value is None:
            return None
        if isinstance(value, bool):
            return "true" if value else "false"
        if isinstance(value, decimal.Decimal):
            text = format(value.normalize(), "f")
            return text
        if isinstance(value, dt.datetime):
            return str(int((value - dt.datetime(1970, 1, 1)).total_seconds() * 1_000_000))
        if isinstance(value, dt.date):
            return value.isoformat()
        if isinstance(value, float):
            return repr(value)
        return str(value)

    def run(self, loader, sql: str, dataset_id: str | None = None, dry_run: bool = False, job_timeout_ms: int | None = None) -> dict:
        self.statements.append(sql)
        answer = {"jobComplete": True, "schema": {"fields": []}, "rows": []}
        for tree in [t for t in sqlglot.parse(sql, read="bigquery") if t is not None]:
            if isinstance(tree, exp.Transaction) or isinstance(tree, exp.Commit):
                continue
            tree = self._unqualified(tree)
            if isinstance(tree, exp.Create) and tree.args.get("kind") == "TABLE":
                self._create(sql, tree)
                continue
            if isinstance(tree, exp.Drop):
                name = (tree.this or tree.args["tables"][0]).name
                self.con.execute(f'DROP TABLE IF EXISTS "{name}"')
                self.labels.pop(name, None)
                continue
            self._no_row_read(tree)
            text = tree.sql("duckdb")
            try:
                if dry_run:
                    self.con.execute("EXPLAIN " + text)
                    continue
                answer = self._answer(self.con.execute(text))
            except duckdb.CatalogException as exc:
                raise RuntimeError(f"BigQuery error (HTTP 404): Not found: {str(exc).splitlines()[0]}") from None
            except duckdb.Error as exc:
                raise RuntimeError(f"BigQuery error (HTTP 400): {str(exc).splitlines()[0]}") from None
        return answer

    @staticmethod
    def _no_row_read(tree) -> None:
        """BigQuery reads a bare name that is a source of its scope (a table, a CTE or a subquery, by its alias or its
        name) as the source's whole row, a STRUCT, before a column of that name, where DuckDB reads the column: such a
        statement is refused as BigQuery refuses one that uses the row as a value. In an ORDER BY, GROUP BY, HAVING or
        QUALIFY a name the select outputs is that output, as BigQuery reads it, not the row."""
        try:
            scopes = traverse_scope(tree)
        except Exception:
            return
        for scope in scopes:
            aliases = {alias.casefold() for alias in scope.sources}
            outputs = {s.alias_or_name.casefold() for s in scope.expression.selects} if isinstance(scope.expression, exp.Select) else set()
            for column in scope.walk():
                if not isinstance(column, exp.Column) or column.table or column.name.casefold() not in aliases:
                    continue
                if column.name.casefold() in outputs and column.find_ancestor(exp.Order, exp.Group, exp.Having, exp.Qualify):
                    continue
                raise RuntimeError(f"BigQuery error (HTTP 400): {column.name} is the row of {column.name}, a STRUCT")

    def load_rows(self, loader, dataset_id: str, table_id: str, schema_fields: list[dict], rows: list[dict],
                  write_disposition: str = "WRITE_TRUNCATE") -> int:
        self.loads.append((table_id, len(rows), write_disposition))
        names = [f["name"] for f in schema_fields]
        if write_disposition == "WRITE_TRUNCATE":
            self.con.execute(f'DROP TABLE IF EXISTS "{table_id}"')
            columns = ", ".join(f'"{f["name"]}" {COLUMN_TYPES.get(f["type"].upper(), "VARCHAR")}' for f in schema_fields)
            self.con.execute(f'CREATE TABLE "{table_id}" ({columns})')
        if rows:
            frame = pd.DataFrame([[r.get(n) for n in names] for r in rows], columns=names, dtype=object)
            self.con.register("loaded", frame)
            try:
                self.con.execute(f'INSERT INTO "{table_id}" ({", ".join(chr(34) + n + chr(34) for n in names)}) '
                                 f'SELECT {", ".join(chr(34) + n + chr(34) for n in names)} FROM loaded')
            finally:
                self.con.unregister("loaded")
        return len(rows)

    def tables(self, loader, dataset_id: str) -> list[dict]:
        return [{"tableReference": {"tableId": name}, "labels": labels} for name, labels in sorted(self.labels.items())]

    def held(self) -> list[str]:
        return [r[0] for r in self.con.execute("SELECT table_name FROM duckdb_tables() ORDER BY 1").fetchall()]

    def rows(self, table: str) -> list[tuple]:
        return self.con.execute(f'SELECT * FROM "{table}"').fetchall()


def install(monkeypatch) -> FakeBigQuery:
    """A FakeBigQuery behind every BigQueryLoader: run, query, load_rows, tables, ensure_dataset and account."""
    fake = FakeBigQuery()
    monkeypatch.setattr(BigQueryLoader, "account", lambda self: (None, PROJECT))
    monkeypatch.setattr(BigQueryLoader, "token", lambda self: ("stand-in", PROJECT))
    monkeypatch.setattr(BigQueryLoader, "ensure_dataset", lambda self, dataset_id: fake.datasets.add(dataset_id))
    monkeypatch.setattr(BigQueryLoader, "run", lambda self, sql, dataset_id=None, dry_run=False, job_timeout_ms=None:
                        fake.run(self, sql, dataset_id, dry_run, job_timeout_ms))
    monkeypatch.setattr(BigQueryLoader, "query", lambda self, sql: fake.run(self, sql))
    monkeypatch.setattr(BigQueryLoader, "load_rows", lambda self, dataset_id, table_id, schema_fields, rows,
                        write_disposition="WRITE_TRUNCATE": fake.load_rows(self, dataset_id, table_id, schema_fields, rows,
                                                                         write_disposition))
    monkeypatch.setattr(BigQueryLoader, "tables", lambda self, dataset_id: fake.tables(self, dataset_id))
    return fake


def block_duckdb(monkeypatch) -> list[str]:
    """DuckDB taken away: every import of it after this one fails, and so does every connection opened through a module
    that imported it before (duckdb.connect refuses, naming it in the list returned); the stand-in keeps the connection it
    opened first."""
    opened: list[str] = []

    def refused(*args, **kwargs):
        opened.append(str(args[0]) if args else ":memory:")
        raise RuntimeError("DuckDB opened on the BigQuery path")
    monkeypatch.setattr(duckdb, "connect", refused)
    monkeypatch.setitem(sys.modules, "duckdb", None)
    for name in [m for m in sys.modules if m.startswith("duckdb.")]:
        monkeypatch.setitem(sys.modules, name, None)
    return opened
