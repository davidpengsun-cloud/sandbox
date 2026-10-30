"""BigQuery's side of the estate's warehouse seam (kge/ports.py EstateWarehouses): a client's warehouses as tables in its
dataset, <prefix>_<client>_warehouses, made, loaded, checked and read with no DuckDB file and no DuckDB module.

An established unit's tables are <unit>_<table>, a version of a unit onboarding made <unit>_v<N>_<table>, as the engine
reads them (kge/adapters/warehouse/bigquery.py BigQueryWarehouses), each labelled with its unit (and version), as the
sync labels them (kge/estate/load_bigquery.py). A table is made from DuckDB's DDL, as declared reads it
(kge/adapters/warehouse/types.py): each column in its BigQuery type with its comment as its description, the primary
key, and each foreign key that references a primary key, NOT ENFORCED; NOT NULL and a DECIMAL's precision are the
checks' (BigQuery's NUMERIC holds the rounded value), so a load job's schema matches the table's. Rows are cast and checked in Python as DuckDB
checks them on insert (types.checked, orphans), then loaded by load jobs, never streamed, so a later UPDATE can change
them; a staged warehouse holds its rows in the process until a read needs them or publish writes them, so rows a check
refuses leave the dataset as it was. publish counts every table in BigQuery against the rows loaded, then stamps it.

What BigQuery cannot hold of a warehouse is kept beside its tables, in the dataset's own table CATALOGUE: one row per
unit and version (0 for an established unit), its declared tables (with DuckDB's type names, so certification lands the
same catalogue as on DuckDB) and the digest of the inputs its rows were made from. The row is written last, in one
transaction with the old one's removal, as the local stack writes a version's inputs.sha256 last: a warehouse with no
row is not published, and nothing reads it but the step that made it.

Every statement goes through the shared client's loader (kge/adapters/bigquery.py BigQueryLoader.run), which waits for
the job and reads every page. A session's SQL is DuckDB's dialect, its ? parameters written in as literals and its
tables routed to the dataset (kge/adapters/warehouse/bigquery.py rewrite_sql)."""
from __future__ import annotations

import datetime as dt
import decimal
import json
import math
import secrets
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import sqlglot
from sqlglot import exp

from kge.adapters.bigquery import UNIT_LABEL, VERSION_LABEL, BigQueryLoader, dataset_name, typed_rows
from kge.adapters.warehouse.bigquery import rewrite_sql
from kge.adapters.warehouse.types import bigquery_type, checked, declared, orphans
from kge.pipeline import warehouse_versions
from kge.ports import FigureTimeout, WarehouseRefused

CATALOGUE = "_kge_catalogue"
LOAD_CHUNK = 20000
SCRATCH_HOURS = 24


def literal(value: Any) -> str:
    """A Python value as a SQL literal in DuckDB's dialect, which rewrite_sql then writes as BigQuery's."""
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, (int, decimal.Decimal)):
        return str(value)
    if isinstance(value, float):
        return repr(value) if math.isfinite(value) else f"CAST('{value}' AS DOUBLE)"
    if isinstance(value, dt.datetime):
        return f"TIMESTAMP '{value.isoformat(sep=' ')}'"
    if isinstance(value, dt.date):
        return f"DATE '{value.isoformat()}'"
    return "'" + str(value).replace("'", "''") + "'"


def bigquery_literal(value: Any) -> str:
    """A Python value as a BigQuery literal: its DuckDB literal (literal) read as DuckDB's and written as BigQuery's."""
    return sqlglot.parse_one(literal(value), read="duckdb").sql("bigquery")


def inline(sql: str, parameters: list | None) -> str:
    """The statement with each ? outside a quoted string replaced by its parameter's literal."""
    if not parameters:
        return sql
    out, values, quote = [], iter(parameters), None
    for char in sql:
        if quote:
            quote = None if char == quote else quote
            out.append(char)
        elif char in ("'", '"'):
            quote = char
            out.append(char)
        elif char == "?":
            out.append(literal(next(values)))
        else:
            out.append(char)
    return "".join(out)


def _json(value: Any) -> Any:
    """A checked value as a load job's NEWLINE_DELIMITED_JSON reads it: NUMERIC as text, so no digit is lost."""
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else ("NaN" if math.isnan(value) else ("Infinity" if value > 0 else "-Infinity"))
    if isinstance(value, decimal.Decimal):
        return format(value, "f")
    if isinstance(value, dt.datetime):
        return value.isoformat(sep=" ")
    if isinstance(value, dt.date):
        return value.isoformat()
    return str(value)


def _quoted(text: str) -> str:
    return exp.Literal.string(text).sql("bigquery")


def _parents_first(tables: list[dict]) -> list[dict]:
    ordered, pending = [], list(tables)
    while pending:
        names = {t["name"] for t in pending}
        ready = next((t for t in pending if all(f["ref_table"] not in names or f["ref_table"] == t["name"]
                                                for f in t["foreign_keys"])), pending[0])
        pending.remove(ready)
        ordered.append(ready)
    return ordered


class Result:
    """A read's rows, as DuckDB's result offers them: fetchone, fetchall and description."""

    def __init__(self, rows: list[tuple], fields: list[dict]):
        self.rows, self.index = rows, 0
        self.description = [(f.get("name"), f.get("type"), None, None, None, None, None) for f in fields]

    def fetchone(self) -> tuple | None:
        if self.index < len(self.rows):
            self.index += 1
            return self.rows[self.index - 1]
        return None

    def fetchall(self) -> list[tuple]:
        rest, self.index = self.rows[self.index:], len(self.rows)
        return rest


class BigQuerySession:
    """One warehouse (or attached ones) in the dataset, behind the port's session (kge/ports.py WarehouseSession).
    prefix: the tables' prefix (<unit>, <unit>_v<N>, a scratch one); tables: what the warehouse declares; created: whether
    its tables exist in BigQuery yet. A preview (estate None) never reaches BigQuery: its rows are checked and counted."""

    def __init__(self, estate: BigQueryEstate | None, unit: str, prefix: str, tables: list[dict], created: bool,
                 version: int | None = None, attached: list[str] | None = None, expires: bool = False):
        self.estate, self.unit, self.prefix, self.version = estate, unit, prefix, version
        self.declared = {t["name"]: t for t in tables}
        self.created, self.expires, self.attached_units = created, expires, attached
        self.pending: dict[str, list[list]] = {}
        self.held: dict[str, list[list]] = {t["name"]: [] for t in tables} if not created else {}
        self.loaded: dict[str, int] = {}
        self.wrote_by_sql = False
        self.own = not created

    def _table(self, name: str) -> dict:
        if name not in self.declared:
            raise WarehouseRefused(f"Catalog Error: Table with name {name} does not exist")
        return self.declared[name]

    def flush(self) -> None:
        """Make the tables (once) and load every pending row, so a read sees them."""
        if self.estate is None:
            return
        if not self.created:
            self.estate.create_tables(self.unit, self.prefix, list(self.declared.values()), self.version, self.expires)
            self.created = True
        for name, rows in list(self.pending.items()):
            self.estate.load_table(self.prefix, self._table(name), rows)
            self.loaded[name] = self.loaded.get(name, 0) + len(rows)
            del self.pending[name]

    def execute(self, sql: str, parameters: list | None = None) -> Result:
        if self.estate is None:
            raise WarehouseRefused("a preview checks and counts rows; it runs no SQL")
        self.flush()
        statement = inline(sql, parameters)
        if statement.lstrip().upper().startswith("EXPLAIN "):
            return self._plan(statement.lstrip()[len("EXPLAIN "):])
        self.wrote_by_sql = self.wrote_by_sql or not statement.lstrip().upper().startswith(("SELECT", "WITH", "EXPLAIN"))
        prefix = (lambda unit: unit) if self.attached_units else (lambda _unit: self.prefix)
        bq_sql = rewrite_sql(statement, prefix, None if self.attached_units else self.unit, duckdb_types=True)
        try:
            data = self.estate.loader.run(bq_sql, self.estate.dataset_id)
        except RuntimeError as exc:
            raise WarehouseRefused(str(exc).splitlines()[0]) from None
        return Result(typed_rows(data), (data.get("schema") or {}).get("fields") or [])

    def update_many(self, table: str, key: str, columns: list[str], rows: list[tuple]) -> int:
        """Every row's update in one statement, one BigQuery DML job: UPDATE ... FROM UNNEST([STRUCT(...), ...]), each
        value written as BigQuery's literal of the one DuckDB's dialect gives it (literal)."""
        if self.estate is None or self.attached_units:
            raise WarehouseRefused("rows are updated in one warehouse opened to write")
        self._table(table)
        if not rows:
            return 0
        self.flush()
        self.wrote_by_sql = True
        names = [key, *columns]
        structs = ", ".join("STRUCT(" + ", ".join(f"{bigquery_literal(v)} AS `{n}`" for v, n in zip(row, names, strict=True)) + ")"
                            for row in rows)
        statement = (f"UPDATE `{self.prefix}_{table}` AS t SET {', '.join(f'`{c}` = u.`{c}`' for c in columns)} "
                     f"FROM UNNEST([{structs}]) AS u WHERE t.`{key}` = u.`{key}`")
        try:
            self.estate.loader.run(statement, self.estate.dataset_id)
        except RuntimeError as exc:
            raise WarehouseRefused(str(exc).splitlines()[0]) from None
        return len(rows)

    def _plan(self, sql: str) -> Result:
        """EXPLAIN, as BigQuery offers it: a dry run, which plans the query and runs nothing."""
        prefix = (lambda unit: unit) if self.attached_units else (lambda _unit: self.prefix)
        try:
            self.estate.loader.run(rewrite_sql(sql, prefix, None if self.attached_units else self.unit),
                                   self.estate.dataset_id, dry_run=True)
        except RuntimeError as exc:
            raise WarehouseRefused(str(exc).splitlines()[0]) from None
        return Result([("EXPLAIN",)], [{"name": "plan", "type": "STRING"}])

    def load(self, table: str, columns: list[str], rows: list[list], batch: int | None = None) -> int:
        """Rows cast and checked now, as DuckDB checks them on insert, against the rows this session loaded (a warehouse
        opened to write has its earlier rows checked by the step that loaded them); held until a read or publish."""
        declared_table = self._table(table)
        typed = checked(declared_table, list(columns), [list(r) for r in rows], self.held)
        if declared_table["name"] in self.held:
            self.held[declared_table["name"]].extend(typed)
            orphans(list(self.declared.values()), self.held)
        self.pending.setdefault(table, []).extend(typed)
        return len(typed)

    def describe(self, table: str) -> dict:
        found = self._table(table)
        if self.own and not self.wrote_by_sql:
            rows = self.loaded.get(table, 0) + len(self.pending.get(table, []))
        else:
            rows = self.execute(f'SELECT count(*) FROM "{table}"').fetchone()[0]
        return {"columns": [{"name": c["name"], "type": c["type"], "comment": c.get("comment"), "ordinal": i}
                            for i, c in enumerate(found["columns"], 1)],
                "primary_key": list(found["primary_key"]),
                "foreign_keys": [{"columns": list(f["columns"]), "ref_table": f["ref_table"], "ref_columns": list(f["ref_columns"])}
                                 for f in found["foreign_keys"]],
                "rows": int(rows)}

    def storage(self, table: str) -> int | None:
        if self.estate is None:
            return None
        columns = [column["name"] for column in self.describe(table)["columns"]]
        sizes = " + ".join(f'LENGTH(COALESCE(CAST("{column}" AS VARCHAR), \'\'))' for column in columns)
        row = self.execute(f'SELECT COALESCE(SUM({sizes}), 0) FROM "{table}"').fetchone()
        return int(row[0]) if row else 0

    def tables(self) -> list[str]:
        return sorted(self.declared)

    def sample(self, table: str, column: str, limit: int = 50) -> list[str]:
        self._table(table)
        rows = self.execute(f'SELECT DISTINCT CAST("{column}" AS VARCHAR) FROM "{table}" WHERE "{column}" IS NOT NULL '
                            f'LIMIT {int(limit)}').fetchall()
        return [r[0] for r in rows]

    def figure(self, sql: str, seconds: float) -> tuple | None:
        if self.estate is None:
            raise WarehouseRefused("a preview checks and counts rows; it runs no SQL")
        self.flush()
        bq_sql = rewrite_sql(sql, lambda _unit: self.prefix, self.unit, duckdb_types=True)
        try:
            data = self.estate.loader.run(bq_sql, self.estate.dataset_id, job_timeout_ms=int(seconds * 1000))
        except TimeoutError:
            raise FigureTimeout(f"it ran for more than {seconds:g} seconds") from None
        except RuntimeError as exc:
            text = str(exc)
            if "timeout" in text.lower() or "timed out" in text.lower():
                raise FigureTimeout(f"it ran for more than {seconds:g} seconds") from None
            raise WarehouseRefused(text.splitlines()[0]) from None
        rows = typed_rows(data)
        return rows[0] if rows else None

    def close(self) -> None:
        """A warehouse opened to write loads what it holds; a staged one keeps it for publish."""
        if self.estate is not None and self.created:
            self.flush()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


class BigQueryEstate:
    """A client's warehouses as tables in its BigQuery dataset (kge/ports.py EstateWarehouses)."""

    backend = "bigquery"

    def __init__(self, client: str, loader: BigQueryLoader | None = None, project: str | None = None,
                 dataset_prefix: str | None = None):
        self.client = client
        self.loader = loader or BigQueryLoader(project=project, dataset_prefix=dataset_prefix, client=client)
        self.dataset_id = dataset_name(self.loader.dataset_prefix, client, "warehouses")
        self.staged: dict[tuple[str, int | None], BigQuerySession] = {}
        self._ready = False

    def _project(self) -> str:
        return self.loader.account()[1]

    def holds_files(self) -> bool:
        """Never: the warehouses are tables in the client's dataset."""
        return False

    def kept_in(self, dataset: str) -> bool:
        """Whether dataset is the client's warehouses dataset, where these tables are."""
        return dataset == self.dataset_id

    def ref(self, table: str) -> str:
        return f"`{self._project()}.{self.dataset_id}.{table}`"

    def at(self, prefix: str, table: dict) -> str:
        """A declared table's (or a foreign key's referenced table's) name under prefix, quoted whole."""
        return self.ref(f"{prefix}_{table.get('ref_table') or table['name']}")

    def _ensure(self) -> None:
        if self._ready:
            return
        self.loader.ensure_dataset(self.dataset_id)
        self.loader.run(f"CREATE TABLE IF NOT EXISTS {self.ref(CATALOGUE)} (unit STRING NOT NULL, version INT64 NOT NULL, "
                        "inputs STRING, tables STRING NOT NULL, stamped_at TIMESTAMP)")
        self._ready = True

    @staticmethod
    def prefix(unit: str, version: int | None = None) -> str:
        return unit if version is None else f"{unit}_v{int(version)}"

    def entries(self) -> list[tuple[str, int, str | None, list[dict]]]:
        """Every published warehouse: (unit, version (0 established), inputs digest, declared tables). A dataset with no
        catalogue table yet holds none: a reader (the service, which may only read the dataset where it onboards
        nothing) never makes it, only a writer does (_ensure)."""
        try:
            rows = self.loader.rows(f"SELECT unit, version, inputs, tables FROM {self.ref(CATALOGUE)} ORDER BY unit, version")
        except RuntimeError as exc:
            if "not found" in str(exc).lower():
                return []
            raise
        return [(u, int(v), i, json.loads(t)) for u, v, i, t in rows]

    def _entry(self, unit: str, version: int | None) -> tuple[str, int, str | None, list[dict]] | None:
        want = int(version or 0)
        return next((e for e in self.entries() if e[0] == unit and e[1] == want), None)

    def _drop(self, table_ids: list[str]) -> None:
        """Tables dropped, if there, children first as given; one another's foreign key still references, which
        BigQuery may refuse to drop, is tried again once the rest are gone, until a round drops none."""
        pending = list(dict.fromkeys(table_ids))
        while pending:
            failed, last = [], None
            for table in pending:
                try:
                    self.loader.run(f"DROP TABLE IF EXISTS {self.ref(table)}")
                except RuntimeError as exc:
                    failed.append(table)
                    last = exc
            if len(failed) == len(pending):
                raise last
            pending = failed

    def _children_first(self, prefix: str, tables: list[dict]) -> list[str]:
        return [f"{prefix}_{t['name']}" for t in reversed(_parents_first(tables))]

    def create_tables(self, unit: str, prefix: str, tables: list[dict], version: int | None, expires: bool) -> None:
        """The warehouse's tables made afresh, any it had under the prefix dropped first (children first)."""
        self._ensure()
        ordered = _parents_first(tables)
        self._drop(self._children_first(prefix, tables))
        keys = {t["name"]: t["primary_key"] for t in tables}
        labels = [(UNIT_LABEL, unit)] + ([(VERSION_LABEL, f"v{int(version)}")] if version is not None else [])
        for table in ordered:
            parts = []
            for c in table["columns"]:
                options = f" OPTIONS(description={_quoted(c['comment'])})" if c.get("comment") else ""
                parts.append(f"`{c['name']}` {bigquery_type(c['type'])}{options}")
            if table["primary_key"]:
                parts.append(f"PRIMARY KEY ({', '.join(f'`{c}`' for c in table['primary_key'])}) NOT ENFORCED")
            for fk in table["foreign_keys"]:
                if fk["ref_columns"] and fk["ref_columns"] == keys.get(fk["ref_table"]):
                    parts.append(f"FOREIGN KEY ({', '.join(f'`{c}`' for c in fk['columns'])}) REFERENCES "
                                 f"{self.at(prefix, fk)} ({', '.join(f'`{c}`' for c in fk['ref_columns'])}) "
                                 "NOT ENFORCED")
            options = [f"labels=[{', '.join(f'({_quoted(k)}, {_quoted(v)})' for k, v in labels)}]"] if not expires else []
            if table.get("comment"):
                options.append(f"description={_quoted(table['comment'])}")
            if expires:
                options.append(f"expiration_timestamp=TIMESTAMP_ADD(CURRENT_TIMESTAMP(), INTERVAL {SCRATCH_HOURS} HOUR)")
            self.loader.run(f"CREATE TABLE {self.at(prefix, table)} ({', '.join(parts)}) "
                            f"OPTIONS({', '.join(options)})")

    def load_table(self, prefix: str, table: dict, rows: list[list]) -> None:
        """Checked rows appended by load jobs, LOAD_CHUNK rows each, into a table made with its schema."""
        fields = [{"name": c["name"], "type": bigquery_type(c["type"]), "mode": "NULLABLE"}
                  for c in table["columns"]]
        names = [c["name"] for c in table["columns"]]
        for i in range(0, len(rows), LOAD_CHUNK):
            chunk = [{n: _json(v) for n, v in zip(names, r)} for r in rows[i:i + LOAD_CHUNK]]
            self.loader.load_rows(self.dataset_id, f"{prefix}_{table['name']}", fields, chunk, "WRITE_APPEND")

    def _stamp(self, unit: str, version: int | None, inputs: str | None, tables: list[dict]) -> None:
        text = json.dumps(tables, sort_keys=True, separators=(",", ":"))
        v = int(version or 0)
        self.loader.run(f"BEGIN TRANSACTION; DELETE FROM {self.ref(CATALOGUE)} WHERE unit = {_quoted(unit)} AND version = {v}; "
                        f"INSERT INTO {self.ref(CATALOGUE)} (unit, version, inputs, tables, stamped_at) VALUES ({_quoted(unit)}, {v}, "
                        f"{_quoted(inputs) if inputs else 'NULL'}, {_quoted(text)}, CURRENT_TIMESTAMP()); COMMIT TRANSACTION;")

    def _unstamp(self, unit: str, version: int | None = None) -> None:
        self.loader.run(f"DELETE FROM {self.ref(CATALOGUE)} WHERE unit = {_quoted(unit)} AND version = {int(version or 0)}")

    def stage(self, unit: str, statements: list[str], version: int | None = None) -> BigQuerySession:
        try:
            tables = declared(statements)
        except (ValueError, sqlglot.errors.SqlglotError) as exc:
            raise WarehouseRefused(str(exc).splitlines()[0]) from None
        session = BigQuerySession(self, unit, self.prefix(unit, version), tables, created=False, version=version)
        self.staged[(unit, version)] = session
        return session

    def publish(self, unit: str, version: int | None = None, inputs: str | None = None,
                keep: frozenset[int] = frozenset()) -> None:
        session = self.staged.get((unit, version))
        if session is None:
            raise WarehouseRefused(f"no staged warehouse of {unit} to publish")
        if version is not None and int(version) in keep:
            held = self.stamped(unit, version)
            if held is not None and held != inputs:
                raise FileExistsError(f"{unit}'s warehouse v{int(version)} is kept for a release and was made from other "
                                      "inputs: it is not replaced")
        self._ensure()
        self._unstamp(unit, version)
        loaded = {name: session.loaded.get(name, 0) + len(rows) for name, rows in
                  {**{n: [] for n in session.declared}, **session.pending}.items()}
        session.flush()
        for name in session.declared:
            got = self.loader.rows(f"SELECT count(*) FROM {self.ref(f'{session.prefix}_{name}')}")[0][0]
            if not session.wrote_by_sql and int(got) != loaded[name]:
                raise WarehouseRefused(f"table {name}: BigQuery holds {got} rows, {loaded[name]} were loaded")
        self._drop_others(unit, version, {f"{session.prefix}_{n}" for n in session.declared})
        self._stamp(unit, version, inputs, list(session.declared.values()))
        del self.staged[(unit, version)]

    def _labelled(self, unit: str, version: int | None | bool = False) -> list[str]:
        """The tables labelled with unit (and, unless version is False, with that version, None meaning none)."""
        out = []
        for table in self.loader.tables(self.dataset_id):
            labels = table.get("labels") or {}
            if labels.get(UNIT_LABEL) != unit:
                continue
            if version is not False and labels.get(VERSION_LABEL) != (None if version is None else f"v{int(version)}"):
                continue
            out.append((table.get("tableReference") or {}).get("tableId"))
        return [t for t in out if t]

    def _drop_others(self, unit: str, version: int | None, keep: set[str]) -> None:
        self._drop([table for table in self._labelled(unit, version) if table not in keep])

    def discard(self, unit: str, version: int | None = None) -> None:
        """A staged warehouse dropped. One a read made in BigQuery already (its tables replaced the warehouse's in place)
        takes the warehouse with it: its tables are dropped and its catalogue row removed, so nothing reads a stamp
        whose tables are gone, and the next step that needs it builds it again (ensure_warehouse)."""
        session = self.staged.pop((unit, version), None)
        if session is not None and session.created:
            self._unstamp(unit, version)
            self._drop(self._children_first(session.prefix, list(session.declared.values())))

    def open(self, unit: str, version: int | None = None, write: bool = False) -> BigQuerySession:
        entry = self._entry(unit, version)
        if entry is None:
            what = f"{unit}'s warehouse" + (f" v{int(version)}" if version is not None else "")
            raise WarehouseRefused(f"{what} is not in BigQuery's {self.dataset_id}: build the estate, or onboard the unit")
        return BigQuerySession(self, unit, self.prefix(unit, version), entry[3], created=True, version=version)

    def attached(self, units: list[str]) -> BigQuerySession:
        tables = []
        for unit in units:
            entry = self._entry(unit, None)
            if entry is None:
                raise WarehouseRefused(f"{unit}'s warehouse is not in BigQuery's {self.dataset_id}")
            tables += [{**t, "name": f"{unit}.{t['name']}"} for t in entry[3]]
        return BigQuerySession(self, units[0], units[0], tables, created=True, attached=list(units))

    @contextmanager
    def preview(self, statements: list[str]):
        try:
            tables = declared(statements)
        except (ValueError, sqlglot.errors.SqlglotError) as exc:
            raise WarehouseRefused(str(exc).splitlines()[0]) from None
        yield BigQuerySession(None, "preview", "preview", tables, created=False)

    @contextmanager
    def scratch(self, unit: str, statements: list[str]):
        try:
            tables = declared(statements)
        except (ValueError, sqlglot.errors.SqlglotError) as exc:
            raise WarehouseRefused(str(exc).splitlines()[0]) from None
        prefix = f"{unit}_x{secrets.token_hex(4)}"
        session = BigQuerySession(self, unit, prefix, tables, created=False, expires=True)
        try:
            yield session
        finally:
            if session.created:
                self._drop(self._children_first(prefix, tables))

    def exists(self, unit: str) -> bool:
        return self._entry(unit, None) is not None

    def established(self) -> list[str]:
        return sorted(u for u, v, _, _ in self.entries() if v == 0)

    def label(self, unit: str, version: int | None = None) -> str:
        return f"bigquery:{self.dataset_id}.{self.prefix(unit, version)}"

    def connection(self, unit: str) -> dict:
        """The unit's tables in the client's warehouses dataset, <unit>_<table> (a version's <unit>_v<N>_<table>), as
        BigQuery's wildcard names them; the project is the deployment's, never asked for here."""
        return {"dialect": "bigquery", "ref": f"{self.dataset_id}.{unit}_*"}

    def versions(self, unit: str) -> list[int]:
        return sorted(v for u, v, _, _ in self.entries() if u == unit and v > 0)

    def held(self) -> dict[str, list[int]]:
        out: dict[str, list[int]] = {}
        for u, v, _, _ in self.entries():
            if v > 0:
                out.setdefault(u, []).append(v)
        return {u: sorted(vs) for u, vs in sorted(out.items())}

    def stamped(self, unit: str, version: int) -> str | None:
        entry = self._entry(unit, version)
        return entry[2] if entry else None

    def drop(self, unit: str, version: int) -> None:
        self._ensure()
        self._unstamp(unit, version)
        self._drop(self._labelled(unit, version))

    def remove(self, unit: str) -> None:
        self._ensure()
        self.loader.run(f"DELETE FROM {self.ref(CATALOGUE)} WHERE unit = {_quoted(unit)}")
        self._drop(self._labelled(unit))

    def present(self) -> bool:
        return bool(self.entries())

    def check(self, units: list[str], versions: dict[str, int], model_hash: str | None = None,
              inputs: dict[str, str] | None = None) -> None:
        inputs = inputs or {}
        whose = f"the artifact {model_hash[:12]}" if model_hash else "the artifact"
        held = {(u, v): i for u, v, i, _ in self.entries()}
        gone = [u for u in units if u in versions and (u, int(versions[u])) not in held]
        if gone:
            raise warehouse_versions.MissingWarehouse(
                f"{whose} reads " + ", ".join(f"{u}'s warehouse v{versions[u]}" for u in gone) +
                f", not in BigQuery's {self.dataset_id}: a unit's version is never read from another one. Release a hash "
                "whose warehouses are kept, or make onboard to build the live version again")
        other = [u for u in units if u in versions and u in inputs and held[(u, int(versions[u]))] != inputs[u]]
        if other:
            raise warehouse_versions.MissingWarehouse(
                f"{whose} reads " + ", ".join(f"{u}'s warehouse v{versions[u]}" for u in other) +
                f" in BigQuery's {self.dataset_id}, which holds rows made from other inputs than the artifact was checked "
                "on: a unit's version is never read from another one's rows")

    def read_database(self, path: Path) -> tuple[list[dict], dict[str, dict[str, bytes]]]:
        raise WarehouseRefused("a DuckDB database is read on the local stack only (WAREHOUSE_BACKEND=duckdb); with BigQuery, "
                               "give the client's DDL and its CSVs")
