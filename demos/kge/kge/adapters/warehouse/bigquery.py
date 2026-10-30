"""BigQuery's side of the warehouse seam (kge/ports.py WarehouseSet), as DuckDB's Warehouses
(kge/adapters/warehouse/duckdb.py) offers it: a client's warehouses as one dataset, <prefix>_<client>_warehouses, each
query gated by a BigQuery dry run (issue #70)."""
from __future__ import annotations

import datetime as dt
import json
import threading
import time
import uuid
import urllib.parse
import urllib.error
import urllib.request
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import sqlglot
from sqlglot import exp
from sqlglot.optimizer.scope import traverse_scope

from kge.adapters.deadline import (MIN_CALL_SECONDS, RequestDeadlineExceeded, query_budget,
                                   request_cap, request_io, request_remaining)
from kge.adapters.bigquery import CLOUD_PLATFORM, DEFAULT_DATASET_PREFIX, dataset_name
from kge.adapters.warehouse.types import catalogue_type
from kge.pipeline.sqlguard import as_figure, check
from kge.pipeline.profile import Profile
from kge.pipeline.settings import settings
from kge.ports import AUTHORED, QueryLimits, QueryRefused


class BigQueryCursor:
    """A cursor for BigQueryWarehouses matching the DuckDB cursor interface."""

    def __init__(self, warehouses: BigQueryWarehouses, unit: str | None = None, units: list[str] | None = None,
                 dialect: str = AUTHORED):
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
        data = self.warehouses._query(bq_sql, dry_run=is_explain)
        if is_explain:
            self._rows = [("EXPLAIN",)]
            self.description = [("explain",)]
        else:
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
        if isinstance(target, Profile):
            self.client = client or target.client
            self.folder = target.warehouses
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
        self._columns: dict[str, list[tuple[str, str, str]]] = {}
        self._calendar_tables: dict[str, set[str]] = {}
        self.con = {u: BigQueryCursor(self, u) for u in units}

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
            with urllib.request.urlopen(req, timeout=request_cap(timeout)) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as exc:
            err_body = exc.read().decode(errors="replace")
            try:
                err_json = json.loads(err_body)
                msg = err_json.get("error", {}).get("message") or err_body
            except Exception:
                msg = err_body
            raise RuntimeError(f"BigQuery error (HTTP {exc.code}): {msg}") from None

    def _query(self, sql: str, dry_run: bool = False, limits: QueryLimits | None = None) -> dict:
        request_remaining(MIN_CALL_SECONDS)
        deadline = (limits.deadline if limits.deadline is not None else time.monotonic() + limits.seconds) if limits else None
        token, project = self.token()
        url = f"https://bigquery.googleapis.com/bigquery/v2/projects/{project}/queries"
        headers = {
            "authorization": f"Bearer {token}",
            "content-type": "application/json",
        }
        body = {
            "query": sql,
            "useLegacySql": False,
            "defaultDataset": {
                "projectId": project,
                "datasetId": self.dataset_id,
            },
        }
        if dry_run:
            body["dryRun"] = True
        if limits is not None:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise QueryRefused(f"query exceeded the {limits.seconds:g} second time limit")
            body.update(timeoutMs=max(1, int(remaining * 1000)), jobTimeoutMs=str(max(1, int(remaining * 1000))),
                        maximumBytesBilled=str(limits.scan_bytes))
            query_budget(body, remaining)
            data = self._post(url, headers, body, timeout=request_cap(remaining))
            request_remaining()
            if time.monotonic() >= deadline:
                raise QueryRefused(f"query exceeded the {limits.seconds:g} second time limit")
            if int(data.get("totalBytesProcessed", 0)) > limits.scan_bytes:
                raise QueryRefused("query exceeds the scan work limit")
            return data
        query_budget(body, 60)
        data = self._post(url, headers, body)
        request_remaining()
        return data

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
        return f"{unit}_v{self.versions[unit]}" if unit in self.versions else unit

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
    def attached(self, units: list[str], dialect: str = AUTHORED):
        unknown = [u for u in units if u not in self.units]
        if unknown or len(set(units)) < 2:
            raise ValueError(f"a session attaches two or more of the units ({', '.join(self.units)}), not {units}: a curated source "
                             "is attached beside them, never on its own")
        cur = BigQueryCursor(self, units=units, dialect=dialect)
        yield cur

    def cursor(self, unit: str, dialect: str = AUTHORED) -> BigQueryCursor:
        return BigQueryCursor(self, unit=unit, dialect=dialect)

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
        """A named job with server and client deadlines, a billed scan cap, capped pages and cancellation on failure."""
        request_remaining(MIN_CALL_SECONDS)
        deadline = limits.deadline if limits.deadline is not None else time.monotonic() + limits.seconds
        token, project = self.token()
        base = f"https://bigquery.googleapis.com/bigquery/v2/projects/{project}"
        headers = {"authorization": f"Bearer {token}", "content-type": "application/json"}
        reference = {"projectId": project, "jobId": f"portal_{uuid.uuid4().hex}"}

        @request_io
        def request(method: str, url: str, body=None, seconds=None) -> dict:
            remaining = request_cap(deadline - time.monotonic() if seconds is None else seconds)
            if remaining <= 0:
                raise TimeoutError()
            req = urllib.request.Request(url, method=method, headers=headers,
                                         data=json.dumps(body).encode() if body is not None else None)
            with urllib.request.urlopen(req, timeout=remaining) as response:
                data = json.loads(response.read())
            if seconds is None and time.monotonic() >= deadline:
                raise TimeoutError()
            return data

        try:
            dataset = request("GET", f"{base}/datasets/{urllib.parse.quote(self.dataset_id, safe='')}")
            if not dataset.get("location"):
                raise QueryRefused("the warehouse did not identify its query location")
            reference["location"] = dataset["location"]
            job = request("POST", f"{base}/jobs", {"jobReference": reference, "configuration": {
                "jobTimeoutMs": str(max(1, int(request_cap(deadline - time.monotonic()) * 1000))), "query": {
                    "query": sql, "useLegacySql": False, "maximumBytesBilled": str(limits.scan_bytes),
                    "defaultDataset": {"projectId": project, "datasetId": self.dataset_id}}}})
            reference.update(job.get("jobReference", {}))
            rows, page, schema = [], None, {}
            while True:
                params = {"timeoutMs": str(max(1, int(request_cap(deadline - time.monotonic()) * 1000))),
                          "maxResults": str(limits.rows - len(rows))}
                if reference.get("location"):
                    params["location"] = reference["location"]
                if page:
                    params["pageToken"] = page
                data = request("GET", f"{base}/queries/{urllib.parse.quote(reference['jobId'], safe='')}?" + urllib.parse.urlencode(params))
                if data.get("errors"):
                    raise QueryRefused("query was refused by the warehouse or exceeded its resource limits")
                if not data.get("jobComplete", False):
                    time.sleep(request_cap(min(0.05, max(0, deadline - time.monotonic()))))
                    continue
                schema = data.get("schema") or schema
                rows.extend(data.get("rows", [])[:limits.rows - len(rows)])
                page = data.get("pageToken")
                if not page or len(rows) >= limits.rows:
                    return {"schema": schema, "rows": rows}
        except (TimeoutError, urllib.error.URLError, QueryRefused, RequestDeadlineExceeded) as exc:
            params = urllib.parse.urlencode({"location": reference["location"]}) if reference.get("location") else ""
            try:
                request("POST", f"{base}/jobs/{urllib.parse.quote(reference['jobId'], safe='')}/cancel?{params}", {}, seconds=1)
            except (OSError, ValueError, RequestDeadlineExceeded):
                pass
            request_remaining()
            if isinstance(exc, RequestDeadlineExceeded):
                raise
            if isinstance(exc, QueryRefused):
                raise
            if isinstance(exc, TimeoutError) or time.monotonic() >= deadline:
                raise QueryRefused(f"query exceeded the {limits.seconds:g} second time limit") from None
            raise QueryRefused("query was refused by the warehouse or exceeded its resource limits") from None

    def run(self, unit: str, sql: str, dialect: str = AUTHORED):
        return self.row(unit, sql, dialect)[0]

    def write(self, unit: str, sql: str) -> None:
        """Execute a write statement on the unit warehouse (issue #52 3b)."""
        with self.cursor(unit) as cur:
            cur.execute(sql)
        with self._lock:
            self._columns.pop(self.prefix(unit), None)
            self._calendar_tables.pop(self.prefix(unit), None)

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

    def columns(self, unit: str) -> list[tuple[str, str, str]]:
        """The unit's columns, (table, column, type), each type named DuckDB's way (types.catalogue_type), as the
        engine reads every warehouse's: BOOL is BOOLEAN, INT64 BIGINT, STRING VARCHAR. catalogue_columns keeps
        BigQuery's own names, for a prompt that asks for BigQuery's SQL."""
        key = self.prefix(unit)
        with self._lock:
            cached = self._columns.get(key)
        if cached is not None:
            return list(cached)
        prefix = f"{key}_"
        query = (f"SELECT table_name, column_name, data_type FROM {self._information_schema('COLUMNS')} "
                 f"WHERE STARTS_WITH(table_name, '{prefix}') ORDER BY table_name, ordinal_position")
        out = []
        for tbl, col, dtype in self._parse_rows(self._query(query)):
            clean_tbl = tbl[len(prefix):] if tbl.startswith(prefix) else tbl
            out.append((clean_tbl, col, catalogue_type(dtype)))
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
        key = self.prefix(unit)
        with self._lock:
            cached = self._calendar_tables.get(key)
        if cached is not None:
            return set(cached)
        prefix = f"{key}_"
        query = f"SELECT table_name FROM {self._information_schema('TABLES')} WHERE STARTS_WITH(table_name, '{prefix}')"
        found = {r[0][len(prefix):] if r[0].startswith(prefix) else r[0] for r in self._parse_rows(self._query(query))}
        with self._lock:
            self._calendar_tables[key] = found
        return set(found)

    def _information_schema(self, view: str) -> str:
        _, project = self.token()
        return f"`{project}.{self.dataset_id}.INFORMATION_SCHEMA.{view}`"

    def catalogue_columns(self, unit: str) -> list[tuple[str, str, str]]:
        prefix = f"{self.prefix(unit)}_"
        query = (f"SELECT table_name, column_name, data_type FROM {self._information_schema('COLUMNS')} "
                 f"WHERE STARTS_WITH(table_name, '{prefix}') ORDER BY table_name, ordinal_position")
        return [(tbl[len(prefix):], col, dtype) for tbl, col, dtype in self._parse_rows(self._query(query))]

    def primary_keys(self, unit: str) -> list[tuple[str, list[str]]]:
        """BigQuery holds primary keys unenforced, as declared at load: a dataset loaded without them declares none."""
        prefix = f"{self.prefix(unit)}_"
        query = (f"SELECT k.table_name, k.column_name FROM {self._information_schema('TABLE_CONSTRAINTS')} c "
                 f"JOIN {self._information_schema('KEY_COLUMN_USAGE')} k ON k.constraint_name = c.constraint_name "
                 f"WHERE c.constraint_type = 'PRIMARY KEY' AND STARTS_WITH(k.table_name, '{prefix}') "
                 f"ORDER BY k.table_name, k.ordinal_position")
        keys: dict[str, list[str]] = {}
        for tbl, col in self._parse_rows(self._query(query)):
            keys.setdefault(tbl[len(prefix):], []).append(col)
        return list(keys.items())

    def foreign_keys(self, unit: str) -> list[tuple[str, list[str], str, list[str]]]:
        """Each foreign key's columns pair with the referenced table's primary key through position_in_unique_constraint,
        the place in that key of the column each one points at."""
        prefix = f"{self.prefix(unit)}_"
        query = (f"SELECT k.table_name, k.constraint_name, k.column_name, k.position_in_unique_constraint, r.table_name "
                 f"FROM {self._information_schema('TABLE_CONSTRAINTS')} c "
                 f"JOIN {self._information_schema('KEY_COLUMN_USAGE')} k ON k.constraint_name = c.constraint_name "
                 f"JOIN (SELECT DISTINCT constraint_name, table_name FROM {self._information_schema('CONSTRAINT_COLUMN_USAGE')}) r "
                 f"ON r.constraint_name = c.constraint_name "
                 f"WHERE c.constraint_type = 'FOREIGN KEY' AND STARTS_WITH(k.table_name, '{prefix}') "
                 f"ORDER BY k.table_name, k.constraint_name, k.ordinal_position")
        rows = self._parse_rows(self._query(query))
        if not rows:
            return []
        key_of = dict(self.primary_keys(unit))
        found: dict[str, tuple[str, list[str], str, list[str]]] = {}
        for tbl, name, col, position, ref in rows:
            ref_table = ref[len(prefix):] if ref.startswith(prefix) else ref
            entry = found.setdefault(name, (tbl[len(prefix):], [], ref_table, []))
            ref_key = key_of.get(ref_table, [])
            place = int(position) - 1 if position is not None else -1
            entry[1].append(col)
            entry[3].append(ref_key[place] if 0 <= place < len(ref_key) else col)
        return sorted(found.values(), key=lambda k: (k[0], k[1]))

    def close(self) -> None:
        """Nothing to let go: every query is a request of its own."""


def rewrite_sql(sql: str, prefix, unit: str | None = None, duckdb_types: bool = False) -> str:
    """DuckDB's SQL as BigQuery runs it on a client's warehouses dataset: an unadorned table of unit is
    <prefix(unit)>_<table> under its own name as alias, and an attached one, <unit>.<table>, is <prefix(unit)>_<table>
    AS <unit>_<table>, its columns' qualifiers following; an INSERT's target takes no alias, which BigQuery refuses. A
    column a query names bare that is named as a source of its scope, a table by its alias, is qualified by it (_rows). A
    COUNT(DISTINCT (a, b)), which BigQuery lacks, counts the text of the tuple instead, NULL written \\N. DuckDB's
    date_trunc('month', d), on the estate's DATE columns, is BigQuery's DATE_TRUNC(d, MONTH), not the TIMESTAMP_TRUNC a
    DATE cannot take, and an interval's count is a number, INTERVAL 1 MONTH, as BigQuery writes one. duckdb_types:
    an AVG is cast to FLOAT64, the DOUBLE DuckDB's avg gives, where BigQuery's gives a NUMERIC's average as NUMERIC, so
    the estate's builders read back the type they read on DuckDB. A statement sqlglot cannot read is passed as it is."""
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
        if db_name:
            new_name = f"{prefix(db_name)}_{t.name}"
            t.set("db", None)
            alias_name = orig_alias or f"{db_name}_{t.name}"
        elif unit and not t.name.startswith(f"{prefix(unit)}_"):
            new_name = f"{prefix(unit)}_{t.name}"
            alias_name = orig_alias or t.name
        else:
            new_name = t.name
            alias_name = orig_alias
        t.set("this", exp.to_identifier(new_name))
        if t is target:
            t.set("alias", None)
        elif alias_name:
            t.set("alias", exp.TableAlias(this=exp.to_identifier(alias_name)))
            routed[id(t)] = alias_name
    for column, alias in _rows(tree, routed):
        column.set("table", exp.to_identifier(alias))
    if duckdb_types:
        for average in list(tree.find_all(exp.Avg)):
            average.replace(exp.Cast(this=average.copy(), to=exp.DataType.build("DOUBLE")))
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
            for expr in list(distinct.expressions):
                if isinstance(expr, exp.Tuple):
                    parts = []
                    for sub_expr in expr.expressions:
                        if parts:
                            parts.append(exp.Literal.string("||"))
                        coalesced = exp.Anonymous(
                            this="COALESCE",
                            expressions=[
                                exp.Cast(this=sub_expr.copy(), to=exp.DataType.build("STRING")),
                                exp.Literal.string(r"\N"),
                            ],
                        )
                        parts.append(coalesced)
                    concat_expr = exp.Concat(expressions=parts)
                    expr.replace(concat_expr)
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
            new, alias = f"{prefix(owner)}_{name}", f"{owner}_{name}"
        elif unit and not name.startswith(f"{prefix(unit)}_"):
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
