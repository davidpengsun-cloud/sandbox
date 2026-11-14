"""BigQuery's side of the warehouse seam (kge/ports.py WarehouseSet), as DuckDB's Warehouses
(kge/adapters/warehouse/duckdb.py) offers it: a client's warehouses as one dataset, <prefix>_<client>_warehouses, each
query gated by a BigQuery dry run (issue #70)."""
from __future__ import annotations

import datetime as dt
import json
import threading
import time
import urllib.error
import urllib.request
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import sqlglot
from sqlglot import exp
from sqlglot.optimizer.scope import traverse_scope

from kge.adapters import http
from kge.adapters.deadline import RequestDeadlineExceeded, request_cap, request_io
from kge.adapters.bigquery import BigQueryLoader, CLOUD_PLATFORM, DEFAULT_DATASET_PREFIX, dataset_name, http_failure
from kge.adapters.warehouse.runtimes import RuntimeManager
from kge.adapters.warehouse.types import catalogue_type
from kge.pipeline.sqlguard import as_figure, check
from kge.pipeline.profile import Profile
from kge.pipeline.settings import settings
from kge.ports import AUTHORED, QueryLimits, QueryRefused, WarehouseRefused


class _WarehouseQueries(BigQueryLoader):
    """Use the reader's credential and transport seam with the shared completed-query runner."""

    def __init__(self, warehouse, limits: QueryLimits | None):
        super().__init__(project=warehouse.project, client=warehouse.client, dataset_prefix=warehouse.dataset_prefix,
                         clock=time.monotonic)
        self.warehouse = warehouse
        self.limits = limits
        self.deadline = ((limits.deadline if limits.deadline is not None else time.monotonic() + limits.seconds)
                         if limits else None)

    def token(self):
        return self.warehouse.token()

    def post(self, url: str, headers: dict, body: dict, timeout: float | None = None) -> dict:
        if timeout is not None:
            return self.warehouse._post(url, headers, body, timeout=timeout)
        if self.deadline is None:
            return self.warehouse._post(url, headers, body)
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise QueryRefused(f"query exceeded the {self.limits.seconds:g} second time limit")
        return self.warehouse._post(url, headers, body, timeout=remaining)


class BigQueryCursor:
    """A cursor for BigQueryWarehouses matching the DuckDB cursor interface."""

    def __init__(self, warehouses: BigQueryWarehouses, unit: str | None = None, units: list[str] | None = None,
                 dialect: str = AUTHORED, on_progress=None):
        self.on_progress = on_progress
        self.warehouses = warehouses
        self.unit = unit
        self.units = units or ([unit] if unit else [])
        self.dialect = warehouses.reads(dialect)
        self._rows: list[tuple] = []
        self.description: list[tuple] = []
        self._idx = 0

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def close(self):
        pass

    def execute(self, sql: str) -> BigQueryCursor:
        is_explain = sql.strip().upper().startswith("EXPLAIN ")
        actual_sql = sql[8:].strip() if is_explain else sql
        bq_sql = self.warehouses.to_bigquery(actual_sql, self.unit, self.dialect)
        data = self.warehouses._query(bq_sql, dry_run=is_explain,
                                      **({"on_progress": self.on_progress} if self.on_progress is not None and not is_explain else {}))
        if is_explain:
            self._rows = [("EXPLAIN",)]
            self.description = [("explain",)]
        else:
            from kge.adapters.warehouse.jobs import record_completed
            record_completed(_WarehouseQueries(self.warehouses, None), data)
            self._rows = self.warehouses._parse_rows(data)
            self.description = [(field.get("name", ""),) for field in data.get("schema", {}).get("fields", [])]
        self._idx = 0
        return self

    def fetchone(self) -> tuple | None:
        if self._idx < len(self._rows):
            row = self._rows[self._idx]
            self._idx += 1
            return row
        return None

    def fetchall(self) -> list[tuple]:
        remaining = self._rows[self._idx:]
        self._idx = len(self._rows)
        return remaining


class BigQueryWarehouses:
    """BigQuery warehouse backend: the warehouse seam (kge/ports.py WarehouseSet), as DuckDB's Warehouses offers it.
    query_dialect: a model is asked for BigQuery's own SQL, GoogleSQL, which the gate reads as BigQuery's (sql_dialect)
    and route_sql sends as written but for its tables' names. The code's own SQL, DuckDB's (AUTHORED), is written as
    BigQuery's by rewrite_sql: the caller says which (to_bigquery)."""

    query_dialect = "BigQuery GoogleSQL"
    sql_dialect = "bigquery"

    def __init__(self, target: Profile | Path | str, units: list[str], curated: list[str] = (),
                 project: str | None = None, dataset_prefix: str | None = None, client: str | None = None,
                 versions: dict[str, int] | None = None):
        """versions: the warehouse version of each onboarded unit the served artifact names (kge/services/ask/engine/core.py Engine): its
        tables are <unit>_v<N>_<table>, as kge/estate/load_bigquery.py loads them; every other unit's are <unit>_<table>."""
        if isinstance(target, Profile) or hasattr(target, "warehouses"):
            self.client = client or getattr(target, "client", "")
            self.folder = Path(target.warehouses)
        else:
            self.folder = Path(target)
            self.client = client or (settings().CLIENT or "").strip() or ""
        self.dataset_prefix = (dataset_prefix or settings().BIGQUERY_DATASET_PREFIX or "").strip() or DEFAULT_DATASET_PREFIX
        self.dataset_id = dataset_name(self.dataset_prefix, self.client, "warehouses")
        self.project = (project or settings().BIGQUERY_PROJECT or settings().VERTEX_PROJECT or "").strip() or None
        self.units = list(units)
        self.curated = list(curated)
        self.versions = {u: int(v) for u, v in (versions or {}).items()}
        self._account: tuple | None = None
        self._lock = threading.Lock()
        self._columns: dict[tuple[str, int], list[tuple[str, str, str]]] = {}
        self._catalogue_columns: dict[tuple[str, int], list[tuple[str, str, str]]] = {}
        self._primary_keys: dict[tuple[str, int], list[tuple[str, list[str]]]] = {}
        self._calendar_tables: dict[tuple[str, int], set[str]] = {}
        self._table_facts: dict[str, dict] = {}
        self._fact_units: set[tuple[str, int]] = set()
        self._declared_tables: dict[tuple[str, int], dict[str, str]] = {}
        self.con = {u: BigQueryCursor(self, u) for u in units}
        self._runtimes = RuntimeManager(self)

    def account(self) -> tuple[Any, str]:
        with self._lock:
            if self._account is None:
                import google.auth
                credentials, project = google.auth.default(scopes=[CLOUD_PLATFORM])
                self._account = (credentials, self.project or project)
        credentials, project = self._account
        if not project:
            raise ValueError("no Google Cloud project: set BIGQUERY_PROJECT or VERTEX_PROJECT, or run where credentials name one")
        return credentials, project

    @request_io
    def token(self) -> tuple[str, str]:
        credentials, project = self.account()
        with self._lock:
            if not credentials.valid:
                from google.auth.transport.requests import Request
                credentials.refresh(Request())
            return credentials.token, project

    @request_io
    def _post(self, url: str, headers: dict, body: dict, timeout: float = 60) -> dict:
        req = urllib.request.Request(url, method="POST", headers=headers, data=json.dumps(body, ensure_ascii=False).encode())
        try:
            with http.open_http(req, timeout=request_cap(timeout)) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as exc:
            raise http_failure(exc) from None

    def _query(self, sql: str, dry_run: bool = False, limits: QueryLimits | None = None,
               job_timeout_ms: int | None = None, max_bytes_billed: int | None = None, on_progress=None) -> dict:
        from kge.pipeline.settings import bigquery_max_bytes_billed
        loader = _WarehouseQueries(self, limits)
        options: dict[str, Any] = {"on_progress": on_progress} if on_progress is not None else {}
        if limits is not None:
            options["limits"] = limits
        if job_timeout_ms is not None:
            options["job_timeout_ms"] = job_timeout_ms
        cap = max_bytes_billed if max_bytes_billed is not None else bigquery_max_bytes_billed()
        if cap is not None:
            options["max_bytes_billed"] = cap
        return loader.run(sql, self.dataset_id, dry_run, **options)

    def submit_query(self, sql, unit, units, job_id):
        from kge.adapters.warehouse import jobs
        from kge.pipeline.settings import bigquery_max_bytes_billed
        return jobs.submit(_WarehouseQueries(self, None), self.to_bigquery(sql, unit if len(units) == 1 else None, AUTHORED),
                           self.dataset_id, job_id, bigquery_max_bytes_billed())

    def poll_query(self, handle):
        from kge.adapters.warehouse import jobs
        return jobs.poll(_WarehouseQueries(self, None), handle)

    def release_query(self, handle):
        pass

    def cancel_query(self, handle):
        from kge.adapters.warehouse import jobs
        jobs.cancel(_WarehouseQueries(self, None), handle)

    def _parse_rows(self, data: dict) -> list[tuple]:
        fields = data.get("schema", {}).get("fields", [])
        types = [f.get("type", "STRING").upper() for f in fields]
        out = []
        for r in data.get("rows", []):
            row = []
            for col, typ in zip(r.get("f", []), types):
                v = col.get("v")
                if v is None:
                    row.append(None)
                elif typ in ("INTEGER", "INT64"):
                    row.append(int(v))
                elif typ in ("FLOAT", "FLOAT64", "NUMERIC", "BIGNUMERIC"):
                    row.append(float(v))
                elif typ in ("BOOLEAN", "BOOL"):
                    row.append(v.lower() == "true" if isinstance(v, str) else bool(v))
                elif typ == "DATE":
                    try:
                        row.append(dt.date.fromisoformat(v))
                    except Exception:
                        row.append(v)
                elif typ in ("TIMESTAMP", "DATETIME"):
                    try:
                        if typ == "TIMESTAMP" and str(v).lstrip("-").isdigit():
                            row.append(dt.datetime(1970, 1, 1) + dt.timedelta(microseconds=int(v)))
                        else:
                            row.append(dt.datetime.fromisoformat(v))
                    except Exception:
                        row.append(v)
                else:
                    row.append(v)
            out.append(tuple(row))
        return out

    def prefix(self, unit: str) -> str:
        """The unit's table prefix in the dataset: <unit>, or <unit>_v<N> for the version of an onboarded unit the served
        artifact names."""
        return f"{unit}_v{self.versions[unit]}" if self.versions.get(unit) else unit

    def reads(self, dialect: str) -> str:
        """dialect, if it is one these warehouses take SQL in: the code's own (AUTHORED, rewritten) or BigQuery's."""
        if dialect not in (AUTHORED, self.sql_dialect):
            raise ValueError(f"the BigQuery warehouses take DuckDB's SQL (the code's, rewritten) or BigQuery's, not {dialect!r}")
        return dialect

    def to_bigquery(self, sql: str, unit: str | None, dialect: str) -> str:
        """What BigQuery runs for sql, written in dialect: the code's DuckDB SQL rewritten (rewrite_sql), or BigQuery's
        own with its tables routed and nothing else changed (route_sql)."""
        if self.reads(dialect) == self.sql_dialect:
            return route_sql(sql, self.prefix, unit, self.spelled)
        return self.rewrite_sql(sql, unit)

    def rewrite_sql(self, sql: str, unit: str | None = None, units: list[str] | None = None) -> str:
        """Route tables: map unadorned table references for unit (e.g. tbl -> unit_tbl AS tbl)
        and attached tables (unit.tbl -> unit_tbl AS unit_tbl). An onboarded unit's tables are its named version's
        (unit_vN_tbl), under the same aliases, so its columns bind as before (rewrite_sql, the module's)."""
        return rewrite_sql(sql, self.prefix, unit)

    @contextmanager
    def attached(self, units: list[str], dialect: str = AUTHORED, on_progress=None):
        unknown = [u for u in units if u not in self.units]
        if unknown or len(set(units)) < 2:
            raise ValueError(f"a session attaches two or more of the units ({', '.join(self.units)}), not {units}: a curated source "
                             "is attached beside them, never on its own")
        cur = BigQueryCursor(self, units=units, dialect=dialect, on_progress=on_progress)
        yield cur

    def cursor(self, unit: str, dialect: str = AUTHORED, on_progress=None) -> BigQueryCursor:
        return BigQueryCursor(self, unit=unit, dialect=dialect, on_progress=on_progress)

    def row(self, unit: str, sql: str, dialect: str = AUTHORED) -> tuple | None:
        with self.cursor(unit, dialect) as cur:
            return cur.execute(sql).fetchone()

    def rows(self, unit: str, sql: str, dialect: str = AUTHORED) -> list[tuple]:
        with self.cursor(unit, dialect) as cur:
            return cur.execute(sql).fetchall()

    def query(self, unit: str, sql: str, dialect: str = AUTHORED, limits: QueryLimits | None = None) -> tuple[list[str], list[tuple]]:
        if limits is not None:
            data = self.bounded_query(self.to_bigquery(sql, unit, dialect), limits)
            return [field["name"] for field in data.get("schema", {}).get("fields", [])], self._parse_rows(data)
        with self.cursor(unit, dialect) as cur:
            result = cur.execute(sql)
            return [column[0] for column in result.description], result.fetchall()

    def bounded_query(self, sql: str, limits: QueryLimits) -> dict:
        from kge.pipeline.settings import bigquery_max_bytes_billed
        env_cap = bigquery_max_bytes_billed()
        if env_cap is not None and int(env_cap) < limits.scan_bytes:
            limits = QueryLimits(seconds=limits.seconds, rows=limits.rows, scan_bytes=int(env_cap), deadline=limits.deadline)
        return _WarehouseQueries(self, limits).run(sql, self.dataset_id, limits=limits)

    def run(self, unit: str, sql: str, dialect: str = AUTHORED):
        return self.row(unit, sql, dialect)[0]

    def write(self, unit: str, sql: str) -> None:
        """Execute a write statement on the unit warehouse (issue #52 3b)."""
        with self.cursor(unit) as cur:
            cur.execute(sql)
        with self._lock:
            key = (unit, self.versions.get(unit, 0))
            self._columns.pop(key, None)
            self._catalogue_columns.pop(key, None)
            self._primary_keys.pop(key, None)
            self._calendar_tables.pop(key, None)
            self._fact_units.discard(key)

    def gate(self, unit: str, sql: str, allow, figure: bool = False, dialect: str = AUTHORED, limits: QueryLimits | None = None) -> dict:
        """The SQL gate, the query read as dialect, with BigQuery dryRun: true on what would run, its tables routed to the
        allowlist's spelling of them (learn)."""
        self.learn(allow)
        problems, failed_at, measured = check(sql, allow, self.reads(dialect)), "static", []
        if not problems and figure:
            problems, measured = as_figure(sql, allow, dialect)
        if not problems:
            try:
                bq_sql = self.to_bigquery(sql, unit, dialect)
                if limits is None:
                    self._query(bq_sql, dry_run=True)
                else:
                    self._query(bq_sql, dry_run=True, limits=limits)
            except RequestDeadlineExceeded:
                raise
            except Exception as exc:
                problems, failed_at = [f"dry run failed: {str(exc).splitlines()[0][:160]}"], "dry run"
            else:
                problems, failed_at = [], None
        return {"passed": not problems, "problems": problems, "failed_at": failed_at if problems else None,
                "checks": ["read-only", "tables", "columns", "joins"] + (["figure"] if figure else []) + ["dry run"],
                **({"measured": measured} if figure and not problems else {})}

    def _declared(self, unit: str) -> dict[str, str]:
        """Physical to logical table names from the exact published unit and version, never a prefix scan."""
        if unit not in [*self.units, *self.curated]:
            raise ValueError(f"unknown warehouse {unit}")
        key = (unit, self.versions.get(unit, 0))
        with self._lock:
            cached = self._declared_tables.get(key)
        if cached is not None:
            return dict(cached)
        _, project = self.token()
        version = self.versions.get(unit, 0)
        query = (f"SELECT tables FROM `{project}.{self.dataset_id}._kge_catalogue` "
                 f"WHERE unit = {json.dumps(unit)} AND version = {version}")
        try:
            rows = self._parse_rows(self._query(query))
        except RuntimeError as exc:
            if "not found" not in str(exc).lower():
                raise
            rows = []
        if len(rows) != 1:
            raise WarehouseRefused(f"{unit} version {version}: no unique published warehouse catalogue")
        try:
            tables = json.loads(rows[0][0])
            if not isinstance(tables, list) or any(not isinstance(t, dict) or not isinstance(t.get("name"), str)
                                                  or not t["name"] for t in tables):
                raise ValueError()
            names = [t["name"] for t in tables]
            if len(set(names)) != len(names):
                raise ValueError()
        except (ValueError, TypeError, IndexError):
            raise WarehouseRefused(f"{unit} version {version}: invalid published warehouse catalogue") from None
        declared = {f"{self.prefix(unit)}_{name}": name for name in names}
        with self._lock:
            self._declared_tables[key] = declared
        return dict(declared)

    @staticmethod
    def _table_filter(column: str, tables: dict[str, str]) -> str:
        return f"{column} IN ({', '.join(json.dumps(name) for name in tables)})" if tables else "FALSE"

    def columns(self, unit: str) -> list[tuple[str, str, str]]:
        """The declared unit version's columns, with types named as the warehouse port requires."""
        key = (unit, self.versions.get(unit, 0))
        with self._lock:
            cached = self._columns.get(key)
        if cached is not None:
            return list(cached)
        out = [(table, column, catalogue_type(kind)) for table, column, kind in self.catalogue_columns(unit)]
        with self._lock:
            self._columns[key] = out
        return list(out)

    def spelled(self, unit: str) -> dict[str, str]:
        """The unit's tables as the warehouse spells them, by their names folded (casefold), as the allowlists the gate
        checked queries on name them (learn): BigQuery reads a table's name only as spelled, so a query that writes it in
        another case is routed to its own spelling (route_sql). Empty before a query on the unit passed the gate."""
        return dict(getattr(self, "_spellings", {}).get(unit, {}))

    def learn(self, allow) -> None:
        """Keep the spelling of each table an allowlist names, by unit (spelled)."""
        spellings = self.__dict__.setdefault("_spellings", {})
        for name in getattr(allow, "tables", ()):
            unit, _, table = name.rpartition(".") if getattr(allow, "attached", False) else (allow.unit, "", name)
            spellings.setdefault(unit, {})[table.casefold()] = table

    def calendar_tables(self, unit: str) -> set[str]:
        key = (unit, self.versions.get(unit, 0))
        with self._lock:
            cached = self._calendar_tables.get(key)
        if cached is not None:
            return set(cached)
        tables = self._declared(unit)
        query = (f"SELECT table_name FROM {self._information_schema('TABLES')} "
                 f"WHERE {self._table_filter('table_name', tables)}")
        found = {tables[r[0]] for r in self._parse_rows(self._query(query)) if r[0] in tables}
        with self._lock:
            self._calendar_tables[key] = found
        return set(found)

    def _information_schema(self, view: str) -> str:
        _, project = self.token()
        return f"`{project}.{self.dataset_id}.INFORMATION_SCHEMA.{view}`"

    def catalogue_columns(self, unit: str) -> list[tuple[str, str, str]]:
        key = (unit, self.versions.get(unit, 0))
        with self._lock:
            cached = self._catalogue_columns.get(key)
        if cached is not None:
            return list(cached)
        tables = self._declared(unit)
        query = (f"SELECT table_name, column_name, data_type FROM {self._information_schema('COLUMNS')} "
                 f"WHERE {self._table_filter('table_name', tables)} ORDER BY table_name, ordinal_position")
        out = [(tables[tbl], col, dtype) for tbl, col, dtype in self._parse_rows(self._query(query)) if tbl in tables]
        with self._lock:
            self._catalogue_columns[key] = out
        return list(out)

    def table_facts(self, unit: str, table: str) -> dict:
        """Metadata for a table declared by the opened warehouse version, without scanning its rows."""
        tables = self._declared(unit)
        name = f"{self.prefix(unit)}_{table}"
        if name not in tables:
            raise WarehouseRefused(f"{unit}.{table}: no such table")
        key = (unit, self.versions.get(unit, 0))
        if key not in self._fact_units:
            _, project = self.token()
            query = (f"SELECT table_id, row_count, size_bytes FROM `{project}.{self.dataset_id}.__TABLES__` "
                     f"WHERE {self._table_filter('table_id', tables)}")
            facts = {name: {"records": int(rows) if rows is not None else None,
                            "storage": int(size) if size is not None else None}
                     for name, rows, size in self._parse_rows(self._query(query)) if name in tables}
            self._table_facts.update(facts)
            self._fact_units.add(key)
        if name not in self._table_facts:
            raise WarehouseRefused(f"{unit}.{table}: no such table")
        return dict(self._table_facts[name])

    def primary_keys(self, unit: str) -> list[tuple[str, list[str]]]:
        """BigQuery holds primary keys unenforced, as declared at load: a dataset loaded without them declares none."""
        key = (unit, self.versions.get(unit, 0))
        with self._lock:
            cached = self._primary_keys.get(key)
        if cached is not None:
            return [(table, list(columns)) for table, columns in cached]
        tables = self._declared(unit)
        query = (f"SELECT k.table_name, k.column_name FROM {self._information_schema('TABLE_CONSTRAINTS')} c "
                 f"JOIN {self._information_schema('KEY_COLUMN_USAGE')} k ON k.constraint_name = c.constraint_name "
                 f"WHERE c.constraint_type = 'PRIMARY KEY' AND {self._table_filter('k.table_name', tables)} "
                 f"ORDER BY k.table_name, k.ordinal_position")
        keys: dict[str, list[str]] = {}
        for tbl, col in self._parse_rows(self._query(query)):
            if tbl in tables:
                keys.setdefault(tables[tbl], []).append(col)
        out = list(keys.items())
        with self._lock:
            self._primary_keys[key] = out
        return [(table, list(columns)) for table, columns in out]

    def foreign_keys(self, unit: str) -> list[tuple[str, list[str], str, list[str]]]:
        """Each foreign key's columns pair with the referenced table's primary key through position_in_unique_constraint,
        the place in that key of the column each one points at."""
        tables = self._declared(unit)
        query = (f"SELECT k.table_name, k.constraint_name, k.column_name, k.position_in_unique_constraint, r.table_name "
                 f"FROM {self._information_schema('TABLE_CONSTRAINTS')} c "
                 f"JOIN {self._information_schema('KEY_COLUMN_USAGE')} k ON k.constraint_name = c.constraint_name "
                 f"JOIN (SELECT DISTINCT constraint_name, table_name FROM {self._information_schema('CONSTRAINT_COLUMN_USAGE')}) r "
                 f"ON r.constraint_name = c.constraint_name "
                 f"WHERE c.constraint_type = 'FOREIGN KEY' AND {self._table_filter('k.table_name', tables)} "
                 f"AND {self._table_filter('r.table_name', tables)} "
                 f"ORDER BY k.table_name, k.constraint_name, k.ordinal_position")
        rows = self._parse_rows(self._query(query))
        if not rows:
            return []
        key_of = dict(self.primary_keys(unit))
        found: dict[str, tuple[str, list[str], str, list[str]]] = {}
        for tbl, name, col, position, ref in rows:
            if tbl not in tables or ref not in tables:
                continue
            ref_table = tables[ref]
            entry = found.setdefault(name, (tables[tbl], [], ref_table, []))
            ref_key = key_of.get(ref_table, [])
            place = int(position) - 1 if position is not None else -1
            entry[1].append(col)
            entry[3].append(ref_key[place] if 0 <= place < len(ref_key) else col)
        return sorted(found.values(), key=lambda k: (k[0], k[1]))

    def resolve_reference_sql(
        self,
        sql: str,
        *,
        unit: str | None = None,
        runtimes: list[str] | None = None,
        runtime_id: str | None = None,
        readable_units: set[str] | None = None,
    ) -> dict:
        """Resolve a UAT reference_sql ground-truth query against the served or selected runtime warehouse with dryRun cost gate."""
        from kge.adapters.warehouse import (
            check_readonly_reference_sql,
            extract_figure,
            strip_schema_placeholder,
        )
        from kge.pipeline.settings import bigquery_max_bytes_billed

        ext = self._runtimes.resolve_reference_sql(
            sql,
            unit=unit,
            runtimes=runtimes,
            runtime_id=runtime_id,
            readable_units=readable_units,
        )
        if ext is not None:
            return ext

        cleaned = strip_schema_placeholder(sql)
        check_readonly_reference_sql(cleaned, dialect=self.sql_dialect)
        chosen_unit = unit or (self.units[0] if self.units else None)
        self._runtimes.check_reference_sql(cleaned, chosen_unit, readable_units, dialect=self.sql_dialect)
        try:
            routed = self.to_bigquery(cleaned, chosen_unit, self.sql_dialect)
        except ValueError as exc:
            raise QueryRefused(str(exc)) from None
        max_bytes = bigquery_max_bytes_billed(10_737_418_240)
        dry = self._query(routed, dry_run=True)
        estimated = int(dry.get("totalBytesProcessed") or 0)
        if max_bytes and estimated > int(max_bytes):
            raise QueryRefused(
                f"dry run estimated {estimated} bytes, which exceeds BIGQUERY_MAX_BYTES_BILLED cap of {int(max_bytes)} bytes"
            )
        data = self._query(routed, max_bytes_billed=int(max_bytes) if max_bytes else None)
        cols = [f.get("name", "") for f in data.get("schema", {}).get("fields", [])]
        rows = self._parse_rows(data)[:1000]
        return {
            "sql": routed,
            "reference_sql": cleaned,
            "runtime": self._runtimes.runtime_summary("reference"),
            "columns": cols,
            "rows": rows,
            "figure": extract_figure(rows),
            "bytes_estimated": estimated,
        }

    def configure_runtimes(self, subgraph: dict) -> None:
        self._runtimes.configure(subgraph)

    def runtimes_health(self) -> dict[str, dict]:
        return self._runtimes.health()

    def list_runtimes(self, readable_units: set[str] | None = None) -> list[dict]:
        return self._runtimes.list_runtimes(readable_units)

    def default_runtime_list(self) -> list[str]:
        return self._runtimes.default_runtime_list()

    def runtimes_active(self, requested: list[str] | None = None) -> bool:
        return self._runtimes.runtimes_active(requested)

    def select_runtime(self, statements: list[dict], *, runtimes: list[str] | None = None,
                       readable_units: set[str] | None = None,
                       measures: list[str] | tuple[str, ...] = ()) -> dict:
        return self._runtimes.select_runtime(statements, runtimes=runtimes, readable_units=readable_units, measures=measures)

    def reference_runtime_meta(self, statement: dict, *,
                               measures: list[str] | tuple[str, ...] = ()) -> tuple[dict | None, dict]:
        return self._runtimes.reference_runtime_meta(statement, measures=measures)

    def statement_sql(self, statement: dict, runtime_id: str) -> str:
        return self._runtimes.statement_sql(statement, runtime_id)

    def submit(self, statement: dict, *, runtime_id="reference", measures=(), job_id, on_ready=None, on_stage=None):
        guard, job, meta = self._runtimes.execute_statement(statement, runtime_id=runtime_id, measures=measures,
                                                          on_ready=on_ready, on_stage=on_stage, job_id=job_id)
        return {"id": job_id, "runtime_id": runtime_id, "job": job, "guard": guard, "meta": meta}

    def poll(self, handle: dict) -> dict:
        if not handle["guard"]["passed"]:
            return {"state": "done", "rows": None}
        return self._runtimes.poll(handle)

    def cancel(self, handle: dict) -> None:
        if handle.get("job"):
            self._runtimes.cancel(handle)

    def release(self, handle: dict) -> None:
        if handle.get("job"):
            self._runtimes.release(handle)

    def execute_statement(self, statement: dict, *, runtime_id: str = "reference",
                          measures: list[str] | tuple[str, ...] = (),
                          limits: QueryLimits | None = None,
                          on_stage: object = None, on_progress=None, on_ready=None) -> tuple[dict, list[tuple] | None, dict]:
        return self._runtimes.execute_statement(statement, runtime_id=runtime_id, measures=measures, limits=limits, on_stage=on_stage,
                                                on_progress=on_progress, on_ready=on_ready)

    def close(self) -> None:
        """Nothing to let go: every query is a request of its own."""
        self._runtimes.close()


def rewrite_sql(sql: str, prefix, unit: str | None = None, duckdb_types: bool = False, *,
                preserve_qualified_names: bool = False) -> str:
    """DuckDB's SQL as BigQuery runs it on a client's warehouses dataset: an unadorned table of unit is
    <prefix(unit)>_<table> under its own name as alias, and an attached one, <unit>.<table>, is <prefix(unit)>_<table>
    AS <unit>_<table>, its columns' qualifiers following; an INSERT's target takes no alias, which BigQuery refuses. A
    column a query names bare that is named as a source of its scope, a table by its alias, is qualified by it (_rows). A
    COUNT(DISTINCT (a, b)), which BigQuery lacks, counts an unambiguous JSON encoding of its STRUCT instead, including under CASE. DuckDB's
    date_trunc('month', d), on the estate's DATE columns, is BigQuery's DATE_TRUNC(d, MONTH), not the TIMESTAMP_TRUNC a
    DATE cannot take, and an interval's count is a number, INTERVAL 1 MONTH, as BigQuery writes one. duckdb_types:
    an AVG is cast to FLOAT64, the DOUBLE DuckDB's avg gives, where BigQuery's gives a NUMERIC's average as NUMERIC, so
    the estate's builders read back the type they read on DuckDB. preserve_qualified_names keeps configured runtime
    table paths whose database has no warehouse prefix. A statement sqlglot cannot read is passed as it is."""
    try:
        tree = sqlglot.parse_one(sql, read="duckdb")
    except Exception:
        return sql
    ctes = {cte.alias_or_name for cte in tree.find_all(exp.CTE)}
    target = tree.this if isinstance(tree, exp.Insert) else None
    if isinstance(target, exp.Schema):
        target = target.this
    routed: dict[int, str] = {}
    for t in tree.find_all(exp.Table):
        if not t.db and t.name in ctes:
            continue
        orig_alias = t.alias
        db_name = t.db
        if db_name and (not preserve_qualified_names or prefix(db_name)):
            pfx = prefix(db_name)
            new_name = f"{pfx}_{t.name}" if pfx else t.name
            t.set("db", None)
            alias_name = orig_alias or (f"{db_name}_{t.name}" if pfx else t.name)
        elif unit and prefix(unit) and not t.name.startswith(f"{prefix(unit)}_"):
            new_name = f"{prefix(unit)}_{t.name}"
            alias_name = orig_alias or t.name
        else:
            new_name = t.name
            alias_name = orig_alias
        t.set("this", exp.to_identifier(new_name, quoted=True if preserve_qualified_names and (t.db or t.catalog) else None))
        if preserve_qualified_names and (t.db or t.catalog):
            t.meta["quoted_table"] = True
        if t is target:
            t.set("alias", None)
        elif alias_name:
            t.set("alias", exp.TableAlias(this=exp.to_identifier(alias_name)))
            routed[id(t)] = alias_name
    for column, alias in _rows(tree, routed):
        column.set("table", exp.to_identifier(alias))
    if duckdb_types:
        for average in list(tree.find_all(exp.Avg)):
            value = average.parent if isinstance(average.parent, exp.Window) else average
            value.replace(exp.Cast(this=value.copy(), to=exp.DataType.build("DOUBLE")))
    for cast in list(tree.find_all(exp.Cast)):
        value = cast.this
        if (cast.to.is_type("TEXT", "VARCHAR") and isinstance(value, exp.Anonymous)
                and value.name.upper() == "TO_JSON" and len(value.expressions) == 1
                and isinstance(value.expressions[0], exp.Struct)):
            cast.replace(exp.Anonymous(this="TO_JSON_STRING", expressions=[value.expressions[0].copy()]))
    for alias in tree.find_all(exp.Alias):
        if "/" in alias.alias:
            alias.set("alias", exp.to_identifier(alias.alias.replace("/", "__"), quoted=True))
    for c in tree.find_all(exp.Column):
        if c.db:
            c.set("table", exp.to_identifier(f"{c.db}_{c.table}"))
            c.set("db", None)
    for trunc in list(tree.find_all(exp.TimestampTrunc)):
        trunc.replace(exp.DateTrunc(this=trunc.this.copy(), unit=trunc.args["unit"].copy()))
    for interval in tree.find_all(exp.Interval):
        if isinstance(interval.this, exp.Literal) and interval.this.is_string and interval.this.this.lstrip("-").isdigit():
            interval.set("this", exp.Literal.number(interval.this.this))
    for count in tree.find_all(exp.Count):
        distinct = count.find(exp.Distinct)
        if distinct:
            for expr in list(distinct.find_all(exp.Tuple)):
                fields = [exp.alias_(value.copy(), f'k{i}') for i, value in enumerate(expr.expressions)]
                expr.replace(exp.Anonymous(this='TO_JSON_STRING', expressions=[exp.Struct(expressions=fields)]))
    return tree.sql("bigquery")


def _rows(tree: exp.Expression, routed: dict[int, str]) -> list[tuple[exp.Column, str]]:
    """Each bare column of a query's scope named, in any case, as one of the scope's sources (a table, a CTE, a
    subquery) by the name the query sent reads it by (routed: a routed table's id to the alias routing gave it; any other
    source its own alias or name), with that name. BigQuery reads such a name as the source's whole row, a STRUCT, where
    the gate and DuckDB read a column (a calendar_date table's calendar_date), so the column is qualified by its source,
    and BigQuery reads the column the gate passed, never a row it did not. One an ORDER BY, GROUP BY, HAVING or QUALIFY
    names that is a select output other than that column (an alias of another expression, another source's column of
    the name) is that output, and is left as it is. A statement with no
    query (an UPDATE, a DELETE) has none."""
    found = []
    try:
        scopes = traverse_scope(tree)
    except Exception:
        return []
    for scope in scopes:
        names, keys = {}, {}
        for name, source in scope.sources.items():
            spelled = routed.get(id(source), name) if isinstance(source, exp.Table) else name
            names[spelled.casefold()] = spelled
            keys[spelled.casefold()] = {name.casefold(), spelled.casefold()}
        selects = scope.expression.selects if isinstance(scope.expression, exp.Select) else []
        for column in scope.walk():
            if not isinstance(column, exp.Column) or column.table or column.name.casefold() not in names:
                continue
            folded = column.name.casefold()
            others = [s for s in selects if s.alias_or_name.casefold() == folded and not (
                isinstance(s.unalias(), exp.Column) and s.unalias().name.casefold() == folded
                and (not s.unalias().table or s.unalias().table.casefold() in keys[folded]))]
            if others and column.find_ancestor(exp.Order, exp.Group, exp.Having, exp.Qualify):
                continue
            found.append((column, names[column.name.casefold()]))
    return found


def _span(*parts) -> tuple[int, int]:
    """Where the identifiers parts sit in the text read: the first's start to the last's end. ValueError if the reader
    kept no place for one."""
    places = [part.meta for part in parts if part is not None]
    if not places or not all("start" in m and "end" in m for m in places):
        raise ValueError("a table's name could not be placed in the query, so it is not routed")
    return min(m["start"] for m in places), max(m["end"] for m in places)


def route_sql(sql: str, prefix, unit: str | None = None, spelled=None) -> str:
    """BigQuery's own SQL (a model's, written for these warehouses) as BigQuery runs it on a client's warehouses
    dataset, its text changed only where it names a table: an unadorned table of unit is `<prefix(unit)>_<table>` under
    its own name as alias, and an attached one, <unit>.<table>, `<prefix(unit)>_<table>` AS <unit>_<table>, with a
    column's <unit>.<table> qualifier following, and a bare column named as a source of its scope qualified by it
    (_rows). Nothing else is read and written again, so no construct is translated: what BigQuery refuses, its dry run
    refuses. A table is routed by its name as the dataset spells it, since BigQuery's
    table names are case-sensitive and the gate reads a table's name in any case: spelled(unit), the unit's tables by
    their folded names, gives the spelling (MONTHLY_REVENUE is `<prefix>_monthly_revenue`, tran_fact_1
    `<prefix>_TRAN_FACT_1`), and a table it does not hold, or with no spelled, keeps the name as written, case kept; a
    CTE's name is any case. ValueError for a table it cannot route (one in another project)."""
    tree = sqlglot.parse_one(sql, read="bigquery")
    ctes = {cte.alias_or_name.casefold() for cte in tree.find_all(exp.CTE)}
    edits: list[tuple[int, int, str]] = []
    routed: dict[int, str] = {}
    for t in tree.find_all(exp.Table):
        db = t.args.get("db")
        name, owner = t.name, t.db.lower()
        if t.args.get("catalog") is not None:
            raise ValueError(f"table {t.sql('bigquery')} is outside the warehouses dataset")
        if db is None and name.casefold() in ctes:
            continue
        if db is not None:
            name = spelled(owner).get(name.casefold(), name) if spelled else name
            pfx = prefix(owner)
            new, alias = (f"{pfx}_{name}" if pfx else name), (f"{owner}_{name}" if pfx else name)
        elif unit and prefix(unit) and not name.startswith(f"{prefix(unit)}_"):
            new, alias = f"{prefix(unit)}_{spelled(unit).get(name.casefold(), name) if spelled else name}", t.name
        else:
            continue
        start, end = _span(db, t.this)
        edits.append((start, end, f"`{new}`" + ("" if t.alias else f" AS {alias}")))
        routed[id(t)] = t.alias or alias
    for column, alias in _rows(tree, routed):
        start, end = _span(column.this)
        edits.append((start, end, f"{alias}.{sql[start:end + 1]}"))
    for c in tree.find_all(exp.Column):
        if c.args.get("db") is not None:
            start, end = _span(c.args["db"], c.args.get("table"))
            table = spelled(c.db.lower()).get(c.table.casefold(), c.table) if spelled else c.table
            edits.append((start, end, f"{c.db.lower()}_{table}"))
    out = sql
    for start, end, text in sorted(set(edits), reverse=True):
        out = out[:start] + text + out[end + 1:]
    return out
