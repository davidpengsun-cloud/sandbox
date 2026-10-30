"""DuckDB's side of the warehouse seams (kge/ports.py WarehouseSet, EstateWarehouses): a client's warehouses as DuckDB
files on this machine, <unit>.duckdb each in the profile's warehouses folder, an onboarded unit's the version the served
artifact names (kge/pipeline/warehouse_versions.py). Warehouses reads them for the engine; DuckDBEstate writes and reads
them for the estate's builders, certification and onboarding, each connection a DuckDBSession. The local stack's only
module that imports DuckDB, with Warehouses: a step on the BigQuery path never loads it."""
from __future__ import annotations

import csv
import io
import re
import shutil
import tempfile
import threading
import time
from contextlib import contextmanager
from pathlib import Path

import duckdb

from kge.adapters.deadline import MIN_CALL_SECONDS, request_remaining
from kge.pipeline.sqlguard import gate
from kge.pipeline import warehouse_versions
from kge.ports import AUTHORED, FigureTimeout, QueryLimits, QueryRefused, WarehouseRefused


class RequestCursor:
    """Check the request before each local query; demo-sized queries run synchronously."""

    def __init__(self, cursor):
        self.cursor = cursor

    def __getattr__(self, name):
        return getattr(self.cursor, name)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.cursor.close()

    def execute(self, *args, **kwargs):
        request_remaining(MIN_CALL_SECONDS)
        self.cursor.execute(*args, **kwargs)
        return self


class Warehouses:
    """One read-only connection per unit's warehouse. The API answers requests on several threads at once, and a
    DuckDB connection is not safe to share between them: one request could read another's result. So every query,
    the gate's dry run too, runs on a cursor of its own (DuckDB's way to use one database from many threads)."""

    query_dialect = "DuckDB"
    sql_dialect = "duckdb"

    def __init__(self, folder: Path, units: list[str], curated: list[str] = (), versions: dict[str, int] | None = None,
                 model_hash: str | None = None, inputs: dict[str, str] | None = None):
        """folder: the client's warehouses (the profile's), <unit>.duckdb each. units: the client's, in the pack's order
        (Graph.domain), one warehouse each. FileNotFoundError, naming each one the estate has not built, rather than a
        connection that fails later. curated: the curated sources' (a certified cross-reference's), which no unit holds
        and no connection here reads on its own: only a session that attaches two units through it (attached).
        versions: the warehouse version of each onboarded unit the served artifact names (Graph.warehouse_versions):
        that version's file, never another (kge/pipeline/warehouse_versions.py); MissingWarehouse if it is not on disk, or
        holds rows made from other inputs than inputs (Graph.warehouse_inputs) names for it."""
        folder = Path(folder)
        self.paths = warehouse_versions.paths(folder, list(units), versions or {}, model_hash, inputs)
        missing = [f"{u}.duckdb" for u in units if not self.paths[u].is_file()]
        if missing:
            raise FileNotFoundError(f"no {', '.join(missing)} in {folder}: the graph's units each need a warehouse; make estate builds them")
        self.folder, self.units, self.curated = folder, list(units), list(curated)
        self.versions = {u: v for u, v in (versions or {}).items() if u in self.paths}
        self.con = {u: duckdb.connect(str(self.paths[u]), read_only=True) for u in units}

    def reads(self, dialect: str) -> None:
        """DuckDB runs DuckDB's SQL alone, the code's and a model's alike: SQL in any other dialect is refused, never
        translated."""
        if dialect != self.sql_dialect:
            raise ValueError(f"the DuckDB warehouses read DuckDB's SQL, not {dialect!r}")

    @contextmanager
    def attached(self, units: list[str], dialect: str = AUTHORED):
        """A session of its own that ATTACHes these units' warehouses and the curated sources, every one READ_ONLY, each
        under its id: a query of two units through a certified join names each table <unit>.<table>. The runtime
        attaches, and only once the access rule has passed on every unit; SQL never does (the gate refuses ATTACH).
        Closed when done."""
        self.reads(dialect)
        unknown = [u for u in units if u not in self.units]
        if unknown or len(set(units)) < 2:
            raise ValueError(f"a session attaches two or more of the units ({', '.join(self.units)}), not {units}: a curated source "
                             "is attached beside them, never on its own")
        missing = [f"{c}.duckdb" for c in self.curated if not (self.folder / f"{c}.duckdb").is_file()]
        if missing:
            raise FileNotFoundError(f"no {', '.join(missing)} in {self.folder}: make estate builds it")
        con = RequestCursor(duckdb.connect(":memory:"))
        try:
            for name in [*dict.fromkeys(units), *self.curated]:
                path = str((self.paths.get(name) or self.folder / f"{name}.duckdb").resolve()).replace("'", "''")
                con.execute(f"ATTACH '{path}' AS {name} (READ_ONLY)")
            yield con
        finally:
            con.close()

    def cursor(self, unit: str, dialect: str = AUTHORED) -> RequestCursor:
        self.reads(dialect)
        return RequestCursor(self.con[unit].cursor())

    def row(self, unit: str, sql: str, dialect: str = AUTHORED) -> tuple | None:
        with self.cursor(unit, dialect) as cur:
            return cur.execute(sql).fetchone()

    def rows(self, unit: str, sql: str, dialect: str = AUTHORED) -> list[tuple]:
        with self.cursor(unit, dialect) as cur:
            return cur.execute(sql).fetchall()

    def query(self, unit: str, sql: str, dialect: str = AUTHORED, limits: QueryLimits | None = None) -> tuple[list[str], list[tuple]]:
        if limits is not None:
            return self.bounded_query(unit, sql, dialect, limits)
        with self.cursor(unit, dialect) as cur:
            result = cur.execute(sql)
            return [column[0] for column in result.description], result.fetchall()

    def bounded_query(self, unit: str, sql: str, dialect: str, limits: QueryLimits) -> tuple[list[str], list[tuple]]:
        """Execute and fetch inside the same bounded connection and deadline."""
        with self.bounded_connection(unit, dialect, limits) as con:
            result = con.execute(sql)
            return [column[0] for column in result.description], result.fetchmany(limits.rows)

    @contextmanager
    def bounded_connection(self, unit: str, dialect: str, limits: QueryLimits):
        """An isolated read-only attachment with one worker, a buffer memory cap, no spill or external access,
        and repeated deadline interrupts. DuckDB's scalar allocations and calls can exceed these engine limits."""
        self.reads(dialect)
        deadline = limits.deadline if limits.deadline is not None else time.monotonic() + limits.seconds
        con = duckdb.connect(":memory:", config={"threads": "1", "memory_limit": f"{limits.memory_bytes}B",
                             "max_temp_directory_size": "0B", "autoload_known_extensions": "false",
                             "autoinstall_known_extensions": "false"})
        stopped = threading.Event()
        timer = None

        def interrupt_until_closed():
            if stopped.wait(max(0, deadline - time.monotonic())):
                return
            while not stopped.is_set():
                try:
                    con.interrupt()
                except duckdb.Error:
                    return
                stopped.wait(0.01)
        try:
            timer = threading.Thread(target=interrupt_until_closed, daemon=True)
            timer.start()
            path = str(self.paths[unit].resolve()).replace("'", "''")
            con.execute(f"ATTACH '{path}' AS governed (READ_ONLY)")
            con.execute("USE governed")
            con.execute("SET enable_external_access=false")
            con.execute("SET lock_configuration=true")
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise QueryRefused(f"query exceeded the {limits.seconds:g} second time limit")
            yield RequestCursor(con)
            if time.monotonic() >= deadline:
                raise QueryRefused(f"query exceeded the {limits.seconds:g} second time limit")
        except duckdb.InterruptException:
            raise QueryRefused(f"query exceeded the {limits.seconds:g} second time limit") from None
        except duckdb.OutOfMemoryException:
            raise QueryRefused("query exceeded the working memory limit") from None
        except duckdb.Error as exc:
            raise QueryRefused("warehouse execution failed: " + str(exc).splitlines()[0][:240]) from None
        finally:
            try:
                con.close()
            finally:
                stopped.set()
                if timer is not None and timer.ident is not None:
                    timer.join()

    def run(self, unit: str, sql: str, dialect: str = AUTHORED):
        return self.row(unit, sql, dialect)[0]

    def gate(self, unit: str, sql: str, allow, figure: bool = False, dialect: str = AUTHORED, limits: QueryLimits | None = None) -> dict:
        """The SQL gate (kge/pipeline/sqlguard.py), its dry run on a cursor of its own."""
        if limits is not None:
            with self.bounded_connection(unit, dialect, limits) as con:
                return gate(con, sql, allow, figure, dialect)
        with self.cursor(unit, dialect) as cur:
            return gate(cur, sql, allow, figure, dialect)

    def columns(self, unit: str) -> list[tuple[str, str, str]]:
        return self.rows(unit, "SELECT table_name, column_name, data_type FROM duckdb_columns() WHERE NOT internal")

    def calendar_tables(self, unit: str) -> set[str]:
        return {r[0] for r in self.rows(unit, "SELECT table_name FROM duckdb_tables() WHERE NOT internal")}

    def catalogue_columns(self, unit: str) -> list[tuple[str, str, str]]:
        return self.rows(unit, "SELECT table_name, column_name, data_type FROM duckdb_columns() WHERE NOT internal "
                               "ORDER BY table_name, column_index")

    def primary_keys(self, unit: str) -> list[tuple[str, list[str]]]:
        return [(t, list(cols)) for t, cols in self.rows(unit, "SELECT table_name, constraint_column_names FROM duckdb_constraints() "
                                                               "WHERE constraint_type = 'PRIMARY KEY'")]

    def foreign_keys(self, unit: str) -> list[tuple[str, list[str], str, list[str]]]:
        return [(t, list(cols), rt, list(rcols))
                for t, cols, rt, rcols in self.rows(unit, "SELECT table_name, constraint_column_names, referenced_table, referenced_column_names "
                                                          "FROM duckdb_constraints() WHERE constraint_type = 'FOREIGN KEY' ORDER BY 1, 2")]

    def close(self) -> None:
        for con in self.con.values():
            con.close()


LOCKED = {"enable_external_access": "false", "autoload_known_extensions": "false", "autoinstall_known_extensions": "false"}
IDENT = re.compile(r"[A-Za-z][A-Za-z0-9_]{0,62}")
UNIT = re.compile(r"[a-z][a-z0-9_-]{0,23}")


def _refused(exc: Exception) -> WarehouseRefused:
    return WarehouseRefused(str(exc).splitlines()[0] if str(exc) else type(exc).__name__)


class DuckDBSession:
    """A DuckDB connection behind the port's session (kge/ports.py WarehouseSession). A warehouse made by statements that
    name its unit as a schema (a preview's) holds its tables there; every other in main."""

    def __init__(self, con):
        self.con = con

    def execute(self, sql: str, parameters: list | None = None):
        try:
            return self.con.execute(sql, parameters) if parameters is not None else self.con.execute(sql)
        except duckdb.Error as exc:
            raise _refused(exc) from None

    def update_many(self, table: str, key: str, columns: list[str], rows: list[tuple]) -> int:
        """One UPDATE a row, each its own statement, as the estate's builders always ran them."""
        statement = f"UPDATE {table} SET {', '.join(f'{c} = ?' for c in columns)} WHERE {key} = ?"
        for row in rows:
            self.execute(statement, [*row[1:], row[0]])
        return len(rows)

    def _schema(self) -> str:
        names = [r[0] for r in self.con.execute("SELECT schema_name FROM duckdb_schemas() "
                                                "WHERE database_name = current_database()").fetchall()
                 if r[0] not in ("main", "information_schema", "pg_catalog")]
        return names[0] if len(names) == 1 else "main"

    def _relation(self, table: str) -> str:
        catalog = self.con.execute("SELECT current_database()").fetchone()[0]
        return f'"{catalog}"."{self._schema()}"."{table}"'

    def load(self, table: str, columns: list[str], rows: list[list], batch: int | None = None) -> int:
        if not rows:
            return 0
        names = ", ".join(f'"{c}"' for c in columns)
        one = "(" + ", ".join("?" for _ in columns) + ")"
        relation = self._relation(table)
        try:
            if batch:
                for i in range(0, len(rows), batch):
                    chunk = rows[i:i + batch]
                    self.con.execute(f"INSERT INTO {relation} ({names}) VALUES {', '.join(one for _ in chunk)}",
                                     [v for r in chunk for v in r])
            else:
                self.con.executemany(f"INSERT INTO {relation} ({names}) VALUES {one}", rows)
        except duckdb.Error as exc:
            raise _refused(exc) from None
        return len(rows)

    def describe(self, table: str) -> dict:
        schema = self._schema()
        cols = self.con.execute("SELECT column_name, data_type, comment, column_index FROM duckdb_columns() "
                                "WHERE schema_name=? AND table_name=? ORDER BY column_index", [schema, table]).fetchall()
        keys = self.con.execute("SELECT constraint_type, constraint_column_names, referenced_table, referenced_column_names "
                                "FROM duckdb_constraints() WHERE schema_name=? AND table_name=? "
                                "AND constraint_type IN ('PRIMARY KEY', 'FOREIGN KEY') ORDER BY constraint_index",
                                [schema, table]).fetchall()
        rows = self.con.execute(f"SELECT count(*) FROM {self._relation(table)}").fetchone()[0]
        primary = next((list(cols_) for kind, cols_, _, _ in keys if kind == "PRIMARY KEY"), [])
        return {"columns": [{"name": c, "type": t, "comment": note, "ordinal": i} for c, t, note, i in cols],
                "primary_key": primary,
                "foreign_keys": [{"columns": list(c), "ref_table": rt, "ref_columns": list(rc)}
                                 for kind, c, rt, rc in keys if kind == "FOREIGN KEY"],
                "rows": rows}

    def storage(self, table: str) -> int | None:
        columns = [column["name"] for column in self.describe(table)["columns"]]
        sizes = " + ".join(f'LENGTH(COALESCE(CAST("{column}" AS VARCHAR), \'\'))' for column in columns)
        return int(self.con.execute(f"SELECT COALESCE(SUM({sizes}), 0) FROM {self._relation(table)}").fetchone()[0])

    def tables(self) -> list[str]:
        return [r[0] for r in self.con.execute("SELECT table_name FROM duckdb_tables() WHERE NOT internal ORDER BY 1").fetchall()]

    def sample(self, table: str, column: str, limit: int = 50) -> list[str]:
        rows = self.con.execute(f'SELECT DISTINCT CAST("{column}" AS VARCHAR) FROM {self._relation(table)} '
                                f'WHERE "{column}" IS NOT NULL LIMIT {int(limit)}').fetchall()
        return [r[0] for r in rows]

    def figure(self, sql: str, seconds: float) -> tuple | None:
        timer = threading.Timer(seconds, self.con.interrupt)
        timer.start()
        try:
            return self.con.execute(sql).fetchone()
        except duckdb.InterruptException:
            raise FigureTimeout(f"it ran for more than {seconds:g} seconds") from None
        except duckdb.Error as exc:
            raise _refused(exc) from None
        finally:
            timer.cancel()

    def close(self) -> None:
        self.con.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


def _connect(path: Path | str | None, read_only: bool = False, locked: bool = True):
    """A DuckDB connection; locked (a warehouse made from statements onboarding rebuilt): external access and
    extension installation off, and the configuration locked, before any statement runs."""
    target = str(path) if path else ":memory:"
    if not locked:
        return duckdb.connect(target, read_only=read_only)
    con = duckdb.connect(target, read_only=read_only, config=dict(LOCKED))
    con.execute("SET lock_configuration=true")
    return con


class DuckDBEstate:
    """A client's warehouses as DuckDB files (kge/ports.py EstateWarehouses): <folder>/<unit>.duckdb an established
    unit's, <folder>/onboarded/<unit>/v<N>/<unit>.duckdb a version of one onboarding made (kge/pipeline/warehouse_versions.py).
    A staged warehouse is a part file beside its place, .<unit>.duckdb.part or .onboard-<unit>.duckdb (that name, so its
    catalogue is never <unit> as a schema's would be), put in place by publish."""

    backend = "duckdb"

    def __init__(self, folder: Path):
        self.folder = Path(folder)

    def holds_files(self) -> bool:
        """Always: the warehouses are DuckDB files in the folder."""
        return True

    def kept_in(self, dataset: str) -> bool:
        """Never: the files are copied into any dataset that is to hold them."""
        return False

    def path(self, unit: str, version: int | None = None) -> Path:
        return self.folder / f"{unit}.duckdb" if version is None else warehouse_versions.path(self.folder, unit, version)

    def part(self, unit: str, version: int | None = None) -> Path:
        return self.folder / (f".{unit}.duckdb.part" if version is None else f".onboard-{unit}.duckdb")

    def stage(self, unit: str, statements: list[str], version: int | None = None) -> DuckDBSession:
        part = self.part(unit, version)
        part.parent.mkdir(parents=True, exist_ok=True)
        part.unlink(missing_ok=True)
        session = DuckDBSession(_connect(part))
        try:
            for sql in statements:
                session.execute(sql)
        except BaseException:
            session.close()
            part.unlink(missing_ok=True)
            raise
        return session

    def publish(self, unit: str, version: int | None = None, inputs: str | None = None,
                keep: frozenset[int] = frozenset()) -> None:
        part = self.part(unit, version)
        if version is None:
            part.replace(self.path(unit))
            return
        warehouse_versions.install(part, self.folder, unit, version, inputs or "", keep)

    def discard(self, unit: str, version: int | None = None) -> None:
        self.part(unit, version).unlink(missing_ok=True)

    def open(self, unit: str, version: int | None = None, write: bool = False) -> DuckDBSession:
        try:
            return DuckDBSession(_connect(self.path(unit, version), read_only=not write, locked=False))
        except duckdb.Error as exc:
            raise _refused(exc) from None

    def attached(self, units: list[str]) -> DuckDBSession:
        con = duckdb.connect(":memory:")
        for unit in units:
            con.execute(f"ATTACH '{self.path(unit)}' AS {unit} (READ_ONLY)")
        return DuckDBSession(con)

    @contextmanager
    def preview(self, statements: list[str]):
        session = DuckDBSession(_connect(None))
        try:
            for sql in statements:
                session.execute(sql)
            yield session
        finally:
            session.close()

    @contextmanager
    def scratch(self, unit: str, statements: list[str]):
        with tempfile.TemporaryDirectory(prefix=".onboard-update-") as folder:
            session = DuckDBSession(_connect(Path(folder) / f"{unit}.duckdb"))
            try:
                for sql in statements:
                    session.execute(sql)
                yield session
            finally:
                session.close()

    def exists(self, unit: str) -> bool:
        return self.path(unit).exists()

    def established(self) -> list[str]:
        return sorted(p.stem for p in self.folder.glob("*.duckdb")) if self.folder.is_dir() else []

    def label(self, unit: str, version: int | None = None) -> str:
        return str(self.path(unit, version))

    def connection(self, unit: str) -> dict:
        """The unit's file, relative to the client's folder, as the packs have always named it."""
        return {"dialect": "duckdb", "ref": f"warehouses/{unit}.duckdb"}

    def versions(self, unit: str) -> list[int]:
        return warehouse_versions.versions(self.folder, unit)

    def held(self) -> dict[str, list[int]]:
        return warehouse_versions.held(self.folder)

    def stamped(self, unit: str, version: int) -> str | None:
        if not self.path(unit, version).is_file():
            return None
        return warehouse_versions.stamped(self.folder, unit, version)

    def drop(self, unit: str, version: int) -> None:
        shutil.rmtree(self.path(unit, version).parent, ignore_errors=True)

    def remove(self, unit: str) -> None:
        self.path(unit).unlink(missing_ok=True)
        self.part(unit, 1).unlink(missing_ok=True)
        warehouse_versions.remove(self.folder, unit)

    def present(self) -> bool:
        return warehouse_versions.any_held(self.folder)

    def check(self, units: list[str], versions: dict[str, int], model_hash: str | None = None,
              inputs: dict[str, str] | None = None) -> None:
        warehouse_versions.paths(self.folder, units, versions, model_hash, inputs)

    def read_database(self, path: Path) -> tuple[list[dict], dict[str, dict[str, bytes]]]:
        """An invented DuckDB catalogue read without permitting writes or external access: each schema (main, named for
        the file) a unit's model, its tables' rows as CSV. Names are checked against the identifier allowlist, and quoted
        as well, a second boundary, before any catalogue query."""
        path = Path(path)
        try:
            con = _connect(path, read_only=True)
        except duckdb.Error as exc:
            raise _refused(exc) from None
        try:
            schemas = [r[0] for r in con.execute("SELECT schema_name FROM duckdb_schemas() ORDER BY schema_name").fetchall()
                       if r[0] not in {"main", "information_schema", "pg_catalog"}]
            if not schemas:
                schemas = ["main"]
            models, files = [], {}
            for schema in schemas:
                unit = path.stem if schema == "main" else schema
                if schema == "main" and not (IDENT.fullmatch(unit) and UNIT.fullmatch(unit)):
                    raise WarehouseRefused(f"database {path.name!r} uses the main schema; name a schema or use a database "
                                           "filename that is a valid unit")
                if not IDENT.fullmatch(schema) or not UNIT.fullmatch(unit):
                    raise WarehouseRefused(f"schema name {schema!r} must match {IDENT.pattern}")
                tables = []
                held_constraints = con.execute(
                    "SELECT table_name, constraint_type, constraint_column_names, referenced_table, referenced_column_names "
                    "FROM duckdb_constraints() WHERE schema_name=?", [schema]).fetchall()
                for name, comment in con.execute("SELECT table_name, comment FROM duckdb_tables() WHERE schema_name=? "
                                                 "ORDER BY table_name", [schema]).fetchall():
                    if not IDENT.fullmatch(name):
                        raise WarehouseRefused(f"table name {name!r} must match {IDENT.pattern}")
                    cols = con.execute("SELECT column_name, data_type, comment FROM duckdb_columns() WHERE schema_name=? "
                                       "AND table_name=? ORDER BY column_index", [schema, name]).fetchall()
                    for column, _, _ in cols:
                        if not IDENT.fullmatch(column):
                            raise WarehouseRefused(f"column name {column!r} must match {IDENT.pattern}")
                    constraints = []
                    for table_name, kind, columns, referenced, referenced_columns in held_constraints:
                        if table_name != name:
                            continue
                        named = ", ".join(columns)
                        if kind == "PRIMARY KEY":
                            constraints.append(f"PRIMARY KEY ({named})")
                        elif kind == "UNIQUE":
                            constraints.append(f"UNIQUE ({named})")
                        elif kind == "FOREIGN KEY":
                            constraints.append(f"FOREIGN KEY ({named}) REFERENCES {unit}.{referenced} ({', '.join(referenced_columns)})")
                    tables.append({"name": name, "comment": comment, "columns": [
                        {"name": c, "type": typ, "comment": note, "constraints": []} for c, typ, note in cols],
                        "constraints": constraints})
                if not tables:
                    continue
                raw = {}
                for table in tables:
                    quoted_schema = schema.replace('"', '""')
                    quoted_table = table["name"].replace('"', '""')
                    names = [c[0] for c in con.execute(f'SELECT * FROM "{quoted_schema}"."{quoted_table}" LIMIT 0').description]
                    out = io.StringIO()
                    writer = csv.writer(out, lineterminator="\n")
                    writer.writerow(names)
                    writer.writerows(con.execute(f'SELECT * FROM "{quoted_schema}"."{quoted_table}"').fetchall())
                    raw[f'{table["name"]}.csv'] = out.getvalue().encode()
                models.append({"unit": unit, "tables": tables, "statements": len(tables)})
                files[unit] = raw
            if not models:
                raise WarehouseRefused("database declares no tables")
            return models, files
        except duckdb.Error as exc:
            raise _refused(exc) from None
        finally:
            con.close()
