"""A stand-in for BigQuery, for the tests of the warehouses' BigQuery path (kge/adapters/warehouse/bigquery_estate.py):
it records every statement and load job the shared client's loader (kge/adapters/bigquery.py BigQueryLoader) is asked to
send, and serves rows by running each statement, read as BigQuery's dialect and written as DuckDB's, in a DuckDB of its
own in memory, answering in the REST API's shape (every value text, NUMERIC with no trailing zero, TIMESTAMP in
microseconds, as useInt64Timestamp asks), and failing as the loader reports BigQuery's errors (a RuntimeError; a missing
table's says Not found). Nothing reaches the network and no file is written.

install(monkeypatch) replaces REST transports only: production run, query completion, pagination and multipart or
staged load submission remain exercised. Query envelopes and SQL have independent byte ceilings; jobs can stay pending,
be cancelled or page; a catalogue abort rolls its writes back before returning the REST error. Metadata and resource
labels belong to exact dataset/table names. install_graph adds the real per-generation Ladybug contents behind graph
REST, preserving the existing Cypher twin convention.

block_duckdb(monkeypatch) then takes DuckDB away from every
module that imports it after that (sys.modules["duckdb"] = None), so a step on the BigQuery path that reached for it
fails loudly; the stand-in keeps the module it imported first. What it cannot show is BigQuery's own semantics: its
dialect is read by sqlglot and run by DuckDB, so whether BigQuery itself accepts each statement is only seen live, but
for one rule DuckDB does not keep: a bare name that is a table's alias in its scope is that table's row (_no_row_read),
and for one rule sqlglot does not keep: a GoogleSQL reserved keyword used as a name without backticks is a syntax error
(refuse_reserved), which sqlglot reads as a name and writes back quoted, so it is checked on the text as sent."""
from __future__ import annotations

import datetime as dt
import copy
import json
from email import policy
from email.parser import BytesParser
from urllib.parse import parse_qs, urlsplit
import decimal
import re
import sys
import threading

import duckdb
import pandas as pd
import sqlglot
from sqlglot import exp
from sqlglot.optimizer.scope import traverse_scope

from kge.adapters.bigquery import BigQueryLoader

PROJECT = "pilot-project"
TYPES = {"BIGINT": "INTEGER", "INTEGER": "INTEGER", "HUGEINT": "INTEGER", "SMALLINT": "INTEGER", "DOUBLE": "FLOAT",
         "FLOAT": "FLOAT", "VARCHAR": "STRING", "DATE": "DATE", "BOOLEAN": "BOOLEAN", "TIMESTAMP": "TIMESTAMP"}
RESERVED = frozenset("""ALL AND ANY ARRAY AS ASC ASSERT_ROWS_MODIFIED AT BETWEEN BY CASE CAST COLLATE CONTAINS CREATE CROSS
CUBE CURRENT DEFAULT DEFINE DESC DISTINCT ELSE END ENUM ESCAPE EXCEPT EXCLUDE EXISTS EXTRACT FALSE FETCH FOLLOWING FOR FROM
FULL GROUP GROUPING GROUPS HASH HAVING IF IGNORE IN INNER INTERSECT INTERVAL INTO IS JOIN LATERAL LEFT LIKE LIMIT LOOKUP
MERGE NATURAL NEW NO NOT NULL NULLS OF ON OR ORDER OUTER OVER PARTITION PRECEDING PROTO QUALIFY RANGE RECURSIVE RESPECT
RIGHT ROLLUP ROWS SELECT SET SOME STRUCT TABLESAMPLE THEN TO TREAT TRUE UNBOUNDED UNION UNNEST USING WHEN WHERE WINDOW WITH
WITHIN""".split())
COLUMN_TYPES = {"NUMERIC": "DECIMAL(38,9)", "BIGNUMERIC": "DECIMAL(38,9)", "INT64": "BIGINT", "FLOAT64": "DOUBLE",
                "STRING": "VARCHAR", "BOOL": "BOOLEAN", "DATE": "DATE", "TIMESTAMP": "TIMESTAMP", "JSON": "JSON"}

COLUMN_TYPES_REVERSE = {v: k for k, v in COLUMN_TYPES.items()}


def refuse_reserved(tree) -> None:
    """Refuse a statement that uses a GoogleSQL reserved keyword as a name (a column, its definition, an alias, a table)
    without backticks, as BigQuery refuses it: Syntax error, Unexpected keyword. It reads the tree parsed from the text
    as sent, before anything here rewrites or quotes it. Path segments after the first may be reserved words."""
    for identifier in tree.find_all(exp.Identifier):
        parent = identifier.parent
        if isinstance(parent, (exp.Column, exp.Table)) and identifier is not parent.parts[0]:
            continue
        if isinstance(parent, exp.Dot) and identifier.arg_key == "expression":
            continue
        if not identifier.quoted and identifier.name.upper() in RESERVED:
            raise RuntimeError(f"BigQuery error (HTTP 400): Syntax error: Unexpected keyword {identifier.name.upper()}")


class FakeBigQuery:
    """One project's datasets, every table in one DuckDB in memory by its table id."""

    def __init__(self):
        self._lock = threading.RLock()
        self.con = duckdb.connect(":memory:")
        self.con.execute("SET TimeZone='UTC'")
        self.statements: list[str] = []
        self.loads: list[tuple[str, int, str]] = []
        self.labels: dict[str, dict[str, str]] = {}
        self.datasets: set[str] = set()
        self.objects: dict[tuple[str, str], str] = {}
        self.physical: dict[tuple[str, str], str] = {}
        self.definitions: dict[tuple[str, str], object] = {}
        self.resource_labels: dict[tuple[str, str], dict[str, str]] = {}
        self.requests: list[dict] = []
        self.jobs: dict[str, dict] = {}
        self.pending_polls = 0
        self.page_size = 1000
        self.cancel_next = False
        self.catalogue_aborts = 0
        self.query_limit = 10_000_000
        self.sql_limit = 1_000_000
        self.staged: dict[str, bytes] = {}
        self.graph = None


    def _physical(self, dataset: str, name: str) -> str:
        key = (dataset, name)
        if key not in self.physical:
            self.physical[key] = name if name not in self.physical.values() else f"{dataset}__{name}"
        return self.physical[key]

    def _unqualified(self, tree):
        for table in tree.find_all(exp.Table):
            if table.db:
                name = self._physical(table.db, table.name)
                if name != table.name and not table.alias and isinstance(table.parent, (exp.From, exp.Join)):
                    table.set("alias", exp.TableAlias(this=exp.to_identifier(table.name)))
                table.set("this", exp.to_identifier(name, quoted=True))
            table.set("catalog", None)
            table.set("db", None)
        return tree

    def _create(self, sql: str, tree) -> None:
        for description in tree.find_all(exp.Property):
            if description.name.lower() == "description" and len(str(getattr(description.args.get("value"), "this", ""))) > 1024:
                raise RuntimeError("BigQuery error (HTTP 400): Column description exceeds 1024 characters")
        for labels in re.findall(r"labels\s*=\s*\[(.*?)\]", sql):
            values = re.findall(r"[\"']([^\"']*)[\"']", labels)
            if len(values) > 128 or any(len(value) > 63 for value in values):
                raise RuntimeError("BigQuery error (HTTP 400): Resource label exceeds limits")
        table = tree.this.this
        dataset, name = table.db, table.name
        physical = self._physical(dataset, name)
        self.datasets.add(dataset)
        columns = []
        for part in tree.this.expressions:
            if isinstance(part, exp.ColumnDef):
                kind = re.sub(r"\(.*\)$", "", part.args["kind"].sql("bigquery").upper())
                target = part.args["kind"].sql("duckdb") if kind.startswith("ARRAY") else COLUMN_TYPES.get(kind, "VARCHAR")
                columns.append(f'"{part.this.name}" {target}')
        exists = tree.args.get("exists")
        present = (dataset, name) in self.objects
        self.con.execute(f'CREATE {"OR REPLACE " if tree.args.get("replace") else ""}{"TABLE IF NOT EXISTS" if exists else "TABLE"} "{physical}" ({", ".join(columns)})')
        if tree.expression is not None and not (exists and present):
            query = self._unqualified(tree.expression.copy()).sql("duckdb")
            self.con.execute(f'INSERT INTO "{physical}" {query}')
        self.objects[(dataset, name)] = "BASE TABLE"
        self.definitions[(dataset, name)] = tree.copy()
        found = re.search(r"labels=\[(.*?)\]", sql)
        if found and not (exists and name in self.labels):
            self.labels[name] = dict(re.findall(r"\('([^']*)', '([^']*)'\)", found.group(1)))
        else:
            self.labels.setdefault(name, {})
        self.resource_labels[(dataset, name)] = dict(self.labels[name])

    def _metadata(self, sql: str) -> dict | None:
        if "INFORMATION_SCHEMA" not in sql:
            return None
        tree = sqlglot.parse_one(sql, read="bigquery")
        refuse_reserved(tree)
        registered = []
        try:
            for table in list(tree.find_all(exp.Table)):
                bare = table.copy()
                bare.set("alias", None)
                path = bare.sql("bigquery").strip("`").replace("`", "")
                if ".INFORMATION_SCHEMA." not in path:
                    continue
                prefix, view = path.split(".INFORMATION_SCHEMA.", 1)
                dataset = prefix.rsplit(".", 1)[-1]
                if dataset not in self.datasets:
                    raise RuntimeError(f"BigQuery error (HTTP 404): Not found: Dataset {prefix}")
                columns, records = self._metadata_rows(dataset, view)
                name = f"fake_metadata_{len(registered)}"
                frame = pd.DataFrame(records, columns=columns)
                self.con.register(name, frame)
                registered.append(name)
                alias = table.args.get("alias")
                replacement = exp.to_table(name)
                if alias:
                    replacement.set("alias", alias.copy())
                table.replace(replacement)
            return self._answer(self.con.execute(tree.sql("duckdb")))
        finally:
            for name in registered:
                self.con.unregister(name)

    def _metadata_rows(self, dataset: str, view: str) -> tuple[list[str], list[list]]:
        schemas = {
            "TABLES": ["table_name", "table_type"], "PROPERTY_GRAPHS": ["property_graph_name"],
            "COLUMNS": ["table_name", "column_name", "data_type", "ordinal_position"],
            "TABLE_CONSTRAINTS": ["constraint_name", "table_name", "constraint_type"],
            "KEY_COLUMN_USAGE": ["constraint_name", "table_name", "column_name", "ordinal_position", "position_in_unique_constraint"],
            "CONSTRAINT_COLUMN_USAGE": ["constraint_name", "table_name", "column_name"],
        }
        records = []
        for (ds, name), kind in sorted(self.objects.items()):
            if ds != dataset:
                continue
            if view == "TABLES" and kind != "PROPERTY GRAPH":
                records.append([name, kind])
            elif view == "PROPERTY_GRAPHS" and kind == "PROPERTY GRAPH":
                records.append([name])
            elif view == "COLUMNS" and kind != "PROPERTY GRAPH":
                for column in self.con.execute(f'PRAGMA table_info("{self._physical(ds, name)}")').fetchall():
                    records.append([name, column[1], COLUMN_TYPES_REVERSE.get(column[2], column[2]), column[0] + 1])
            elif (definition := self.definitions.get((ds, name))) is not None:
                for i, constraint in enumerate(definition.find_all(exp.PrimaryKey, exp.ForeignKey)):
                    identifier = f"{name}_constraint_{i}"
                    primary = isinstance(constraint, exp.PrimaryKey)
                    if view == "TABLE_CONSTRAINTS":
                        records.append([identifier, name, "PRIMARY KEY" if primary else "FOREIGN KEY"])
                    reference = constraint.args.get("reference")
                    for j, col in enumerate(constraint.expressions, 1):
                        if view == "KEY_COLUMN_USAGE":
                            records.append([identifier, name, col.name, j, None if primary else j])
                    if view == "CONSTRAINT_COLUMN_USAGE":
                        target = reference.this if reference else definition.this
                        for col in (target.expressions if reference else constraint.expressions):
                            records.append([identifier, target.this.name, col.name])
        return schemas[view], records

    def _drop(self, dataset: str, name: str, kind: str, exists: bool) -> None:
        if not dataset:
            matches = [ds for ds, table in self.objects if table == name]
            if len(matches) == 1:
                dataset = matches[0]
        key = (dataset, name)
        held = self.objects.get(key)
        if held is None:
            if not exists:
                raise RuntimeError(f"BigQuery error (HTTP 404): Not found: {kind} {dataset}.{name}")
            return
        if held != kind:
            raise RuntimeError(f"BigQuery error (HTTP 400): {dataset}.{name} is {held}, not {kind}")
        if kind != "PROPERTY GRAPH":
            sql_kind = "VIEW" if kind == "VIEW" else "TABLE"
            self.con.execute(f'DROP {sql_kind} "{self._physical(dataset, name)}"')
            self.labels.pop(name, None)
            self.resource_labels.pop(key, None)
        if kind == "PROPERTY GRAPH" and self.graph is not None:
            from tests.graphtwin import GENERATION
            dropped = set(GENERATION.findall(name))
            self.graph.dropped |= dropped
            self.graph.generations -= dropped
        self.definitions.pop(key, None)
        del self.objects[key]

    def _property_graph(self, sql: str) -> bool:
        found = re.fullmatch(r"(CREATE(?: OR REPLACE)?|DROP) PROPERTY GRAPH (IF EXISTS )?`([^`]+)`(.*)", sql, re.S | re.I)
        if not found:
            return False
        action, exists, path, _ = found.groups()
        dataset, name = path.split(".")[-2:]
        if action.upper() == "DROP":
            self._drop(dataset, name, "PROPERTY GRAPH", bool(exists))
        else:
            if (dataset, name) in self.objects and action.upper() != "CREATE OR REPLACE":
                raise RuntimeError(f"BigQuery error (HTTP 409): Already exists: {path}")
            self.datasets.add(dataset)
            self.objects[(dataset, name)] = "PROPERTY GRAPH"
        return True

    def _answer(self, result) -> dict:
        if result is None or result.description is None:
            return {"jobComplete": True, "schema": {"fields": []}, "rows": []}
        fields = []
        for name, kind, *_ in result.description:
            text = str(kind).upper()
            fields.append({"name": name, "type": "NUMERIC" if text.startswith("DECIMAL") else TYPES.get(text.removesuffix("[]"), "STRING"),
                           "mode": "REPEATED" if text.endswith("[]") else "NULLABLE"})
        rows = []
        for row in result.fetchall():
            rows.append({"f": [{"v": [{"v": self._text(x)} for x in (v or [])] if field["mode"] == "REPEATED"
                                  else self._text(v)} for v, field in zip(row, fields)]})
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

    def run(self, loader, sql: str, dataset_id: str | None = None, dry_run: bool = False,
            job_timeout_ms: int | None = None) -> dict:
        """Execute scripts atomically, including an abort after writes but before commit."""
        with self._lock:
            transactional = sql.lstrip().upper().startswith("BEGIN TRANSACTION")
            snapshot = copy.deepcopy((self.objects, self.physical, self.labels, self.resource_labels, self.definitions)) if transactional else None
            if transactional:
                self.con.execute("BEGIN TRANSACTION")
            try:
                answer = self._execute(loader, sql, dataset_id, dry_run, job_timeout_ms)
                if transactional and "_kge_catalogue" in sql and self.catalogue_aborts:
                    self.catalogue_aborts -= 1
                    error = RuntimeError("BigQuery error (HTTP 400): Transaction is aborted due to concurrent update")
                    error.reason, error.status = "invalidQuery", 400
                    raise error
                if transactional:
                    self.con.execute("COMMIT")
                return answer
            except BaseException:
                if transactional:
                    self.con.execute("ROLLBACK")
                    self.objects, self.physical, self.labels, self.resource_labels, self.definitions = snapshot
                raise

    def _execute(self, loader, sql: str, dataset_id: str | None = None, dry_run: bool = False, job_timeout_ms: int | None = None) -> dict:
        self.statements.append(sql)
        metadata = self._metadata(sql)
        if metadata is not None:
            return metadata
        if ".__TABLES__`" in sql:
            rows = []
            for name in self.held():
                columns = [row[1] for row in self.con.execute(f'PRAGMA table_info("{name}")').fetchall()]
                sizes = " + ".join(f'LENGTH(COALESCE(CAST("{c}" AS VARCHAR), \'\'))' for c in columns)
                count, size = self.con.execute(f'SELECT COUNT(*), COALESCE(SUM({sizes}), 0) FROM "{name}"').fetchone()
                rows.append({"f": [{"v": name}, {"v": str(count)}, {"v": str(size)}]})
            return {"jobComplete": True, "schema": {"fields": [{"name": "table_id", "type": "STRING"},
                    {"name": "row_count", "type": "INTEGER"}, {"name": "size_bytes", "type": "INTEGER"}]}, "rows": rows}
        answer = {"jobComplete": True, "schema": {"fields": []}, "rows": []}
        for tree in [t for t in sqlglot.parse(sql, read="bigquery") if t is not None]:
            refuse_reserved(tree)
            if isinstance(tree, exp.Transaction) or isinstance(tree, exp.Commit):
                continue
            if isinstance(tree, exp.Command) and self._property_graph(tree.sql("bigquery")):
                continue
            if isinstance(tree, exp.Create) and tree.args.get("kind") == "TABLE":
                self._create(sql, tree)
                continue
            if isinstance(tree, exp.Drop):
                for table in tree.args.get("tables") or [tree.this]:
                    kind = "BASE TABLE" if tree.args["kind"] == "TABLE" else tree.args["kind"]
                    self._drop(table.db, table.name, kind, bool(tree.args.get("exists")))
                continue
            if isinstance(tree, exp.Create) and tree.args.get("kind") == "VIEW":
                target = tree.this
                self.datasets.add(target.db)
                self.objects[(target.db, target.name)] = "VIEW"
            self._vector_search(tree)
            tree = self._unqualified(tree)
            self._no_row_read(tree)
            self._zero_offsets(tree)
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
    def _vector_search(tree) -> None:
        """Evaluate exact cosine search over the actual filtered base rows, preserving pre/post-filter behavior."""
        for search in list(tree.find_all(exp.VectorSearch)):
            assert search.args["distance_type"].expression.name == "COSINE"
            source = search.this.copy()
            query = search.args["query_table"].this
            vector = query.expressions[0].this.copy()
            column = search.args["column_to_search"].name
            k = int(search.args["top_k"].expression.name)
            distance = exp.func("list_cosine_distance", exp.column(column), vector)
            inner = exp.select("*", exp.alias_(distance, "distance")).from_(source).order_by("distance").limit(k)
            search.parent.replace(inner.subquery("base"))

    @staticmethod
    def _zero_offsets(tree) -> None:
        """BigQuery's WITH OFFSET starts at zero; DuckDB's translated WITH ORDINALITY starts at one."""
        for scope in traverse_scope(tree):
            offsets = {source.expression.args["offset"].name for source in scope.sources.values()
                       if isinstance(getattr(source, "expression", None), exp.Unnest)
                       and isinstance(source.expression.args.get("offset"), exp.Identifier)}
            for column in list(scope.columns):
                if not column.table and column.name in offsets:
                    adjusted = exp.Sub(this=column.copy(), expression=exp.Literal.number(1))
                    column.replace(exp.alias_(adjusted, column.name) if column.parent is scope.expression else adjusted)

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
            aliases = {alias.casefold() for alias, source in scope.sources.items()
                       if not isinstance(getattr(source, "expression", source), exp.Unnest)}
            outputs = {s.alias_or_name.casefold() for s in scope.expression.selects} if isinstance(scope.expression, exp.Select) else set()
            for column in scope.walk():
                if not isinstance(column, exp.Column) or column.table or column.name.casefold() not in aliases:
                    continue
                if column.name.casefold() in outputs and column.find_ancestor(exp.Order, exp.Group, exp.Having, exp.Qualify):
                    continue
                raise RuntimeError(f"BigQuery error (HTTP 400): {column.name} is the row of {column.name}, a STRUCT")

    def load_rows(self, loader, dataset_id: str, table_id: str, schema_fields: list[dict], rows: list[dict],
                  write_disposition: str = "WRITE_TRUNCATE") -> int:
        with self._lock:
            self.loads.append((table_id, len(rows), write_disposition))
            self.datasets.add(dataset_id)
            self.objects[(dataset_id, table_id)] = "BASE TABLE"
            table_id = self._physical(dataset_id, table_id)
            names = [f["name"] for f in schema_fields]
            if write_disposition == "WRITE_TRUNCATE":
                self.con.execute(f'DROP TABLE IF EXISTS "{table_id}"')
                columns = ", ".join(f'"{f["name"]}" {(COLUMN_TYPES.get(f["type"].upper(), "VARCHAR") + ("[]" if f.get("mode") == "REPEATED" else ""))}' for f in schema_fields)
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
        with self._lock:
            if dataset_id not in self.datasets:
                raise RuntimeError(f"BigQuery error (HTTP 404): Not found: Dataset {dataset_id}")
            return [{"tableReference": {"tableId": name}, "labels": self.resource_labels.get((dataset_id, name), self.labels.get(name, {}))}
                    for (dataset, name), kind in sorted(self.objects.items()) if dataset == dataset_id and kind != "PROPERTY GRAPH"]

    def held(self) -> list[str]:
        with self._lock:
            return sorted(name for (_, name), kind in self.objects.items() if kind == "BASE TABLE")

    def state_post(self, body: dict) -> dict:
        """Run AppState's parameterized scripts against real tables, including ASSERT and atomic rollback. The SQL
        is translated, not replaced with an operation inferred from its parameters."""
        with self._lock:
            params = {p["name"]: (int(p["parameterValue"]["value"]) if p["parameterType"]["type"] == "INT64"
                                  else p["parameterValue"]["value"]) for p in body.get("queryParameters", [])}
            transactional = body["query"].lstrip().startswith("BEGIN TRANSACTION;")
            if transactional:
                self.con.execute("BEGIN TRANSACTION")
            answer = {}
            try:
                for statement in body["query"].split(";"):
                    statement = statement.strip()
                    if not statement or statement in ("BEGIN TRANSACTION", "COMMIT TRANSACTION"):
                        continue
                    assertion = re.fullmatch(r"ASSERT (.*) AS '([^']+)'", statement, re.S)
                    source = "SELECT " + assertion[1] if assertion else statement
                    tree = sqlglot.parse_one(source, read="bigquery")
                    refuse_reserved(tree)
                    if not isinstance(tree, exp.Create):
                        for table in tree.find_all(exp.Table):
                            if table.db and "INFORMATION_SCHEMA" not in table.sql() and (table.db, table.name) not in self.objects:
                                err = RuntimeError(f"Not found: Table {table.catalog}:{table.db}.{table.name}")
                                err.reason, err.status = "notFound", 404
                                raise err
                    for parameter in list(tree.find_all(exp.Parameter)):
                        value = params[parameter.name]
                        parameter.replace(exp.Literal.number(value) if isinstance(value, int) else exp.Literal.string(value))
                    answer = self.run(None, tree.sql("bigquery"))
                    if assertion and answer["rows"][0]["f"][0]["v"] != "true":
                        raise RuntimeError(assertion[2])
                if transactional:
                    self.con.execute("COMMIT")
            except Exception:
                if transactional:
                    self.con.execute("ROLLBACK")
                raise
            return answer

    def _job(self, execute, reference=None, load=False) -> dict:
        name = (reference or {}).get("jobId") or f"fake-job-{len(self.jobs) + 1}"
        ref = {"projectId": PROJECT, "location": "US", "jobId": name, **(reference or {})}
        self.jobs[name] = {"reference": ref, "execute": execute, "pending": self.pending_polls,
                           "cancelled": self.cancel_next, "load": load, "answer": None}
        self.cancel_next = False
        return self._result(name, 0, submitted=True)

    def _result(self, name: str, offset: int = 0, submitted: bool = False) -> dict:
        job = self.jobs[name]
        ref = {"jobReference": job["reference"]}
        if job["pending"]:
            if job["pending"] > 0:
                job["pending"] -= 1
            return {**ref, "jobComplete": False, "status": {"state": "RUNNING"}}
        if job["cancelled"]:
            error = {"reason": "stopped", "message": "Job cancelled"}
            return {**ref, "jobComplete": True, "errors": [error], "status": {"state": "DONE", "errorResult": error}}
        if job["answer"] is None and not job.get("error"):
            try:
                job["answer"] = job["execute"]()
            except RuntimeError as exc:
                if not getattr(exc, "reason", None):
                    raise
                job["error"] = {"reason": exc.reason, "message": str(exc)}
        if job.get("error"):
            error = job["error"]
            return {**ref, "jobComplete": True, "errors": [error], "status": {"state": "DONE", "errorResult": error}}

        if job["load"]:
            return {**ref, "status": {"state": "DONE"}}
        answer = job["answer"]
        rows = answer.get("rows", [])
        page = rows[offset:offset + self.page_size]
        out = {**ref, "jobComplete": True, "rows": page, "totalRows": str(len(rows)),
               "totalBytesProcessed": "0"}
        if offset == 0:
            out["schema"] = answer.get("schema", {"fields": []})
        if offset + len(page) < len(rows):
            out["pageToken"] = str(offset + len(page))
        return out

    def post(self, url: str, body: dict) -> dict:
        """REST query envelopes, finite jobs and byte limits, before any SQL rewrite."""
        with self._lock:
            payload = json.dumps(body, ensure_ascii=False).encode()
            self.requests.append({"url": url, "bytes": len(payload), "body": copy.deepcopy(body)})
            if url.endswith("/cancel"):
                name = url.rsplit("/", 2)[-2]
                self.jobs[name]["cancelled"] = True
                return {"job": self._result(name)}
            if url.endswith("/datasets"):
                self.datasets.add(body["datasetReference"]["datasetId"])
                return body
            query = body.get("configuration", {}).get("query", body)
            sql = query["query"]
            if len(payload) > self.query_limit or len(sql.encode()) > self.sql_limit:
                raise RuntimeError("BigQuery error (HTTP 400): Request or query is too large")
            def execute():
                if self.graph is not None and self.graph.accepts(sql):
                    if "CREATE OR REPLACE PROPERTY GRAPH" in sql:
                        self.run(None, sql)
                    else:
                        from tests.graphtwin import GENERATION
                        prefixes = set(GENERATION.findall(sql))
                        if prefixes - self.graph.generations:
                            error = RuntimeError("Not found: graph generation " + ", ".join(sorted(prefixes - self.graph.generations)))
                            error.reason = "notFound"
                            raise error
                    return self.graph.whole(self.graph.post(url, query))
                if query.get("queryParameters"):
                    return self.state_post(query)
                return self.run(None, sql, (query.get("defaultDataset") or {}).get("datasetId"), query.get("dryRun", False))
            if query.get("dryRun") or body.get("dryRun"):
                return {**execute(), "totalBytesProcessed": "0"}
            return self._job(execute, body.get("jobReference"))

    def get(self, url: str) -> dict:
        """Poll incomplete jobs and retrieve pages; later pages deliberately omit their schema."""
        with self._lock:
            parsed = urlsplit(url)
            args = parse_qs(parsed.query)
            path = parsed.path.split("/")
            self.requests.append({"url": url, "method": "GET"})
            if "queries" in path or "jobs" in path:
                name = path[-1]
                return self._result(name, int(args.get("pageToken", [0])[0]))
            if path[-1] == "tables":
                rows = self.tables(None, path[-2])
                offset = int(args.get("pageToken", [0])[0])
                page = rows[offset:offset + self.page_size]
                return {"tables": page, **({"nextPageToken": str(offset + len(page))} if offset + len(page) < len(rows) else {})}
            if "datasets" in path:
                return {"location": "US", "datasetReference": {"datasetId": path[-1]}}
            raise AssertionError(f"unexpected BigQuery GET: {url}")

    def submit(self, url: str, content_type: str, payload: bytes, token: str) -> dict:
        """Decode the real multipart upload or GCS load job; apply rows only when the job finishes."""
        with self._lock:
            self.requests.append({"url": url, "bytes": len(payload), "content_type": content_type})
            if content_type.startswith("multipart/"):
                message = BytesParser(policy=policy.default).parsebytes(
                    f'Content-Type: multipart/related; boundary="{content_type.split("boundary=", 1)[1].strip(chr(34))}"\r\nMIME-Version: 1.0\r\n\r\n'.encode() + payload)
                parts = list(message.iter_parts())
                body = json.loads(parts[0].get_payload(decode=True))
                data = parts[1].get_payload(decode=True)
            else:
                body = json.loads(payload)
                data = b"\n".join(self.staged[uri] for uri in body["configuration"]["load"]["sourceUris"])
            load = body["configuration"]["load"]
            destination = load["destinationTable"]
            rows = [json.loads(line) for line in data.splitlines() if line.strip()]
            def execute():
                self.load_rows(None, destination["datasetId"], destination["tableId"], load["schema"]["fields"], rows,
                               load.get("writeDisposition", "WRITE_APPEND"))
                return {}
            return self._job(execute, body.get("jobReference"), load=True)

    def rows(self, table: str) -> list[tuple]:
        with self._lock:
            physical = next((self.physical[key] for key in self.objects if key[1] == table), table)
            return self.con.execute(f'SELECT * FROM "{physical}"').fetchall()


def install(monkeypatch) -> FakeBigQuery:
    """Replace transport only: production query waiting, paging and load serialization stay exercised."""
    from kge.adapters.warehouse.bigquery import BigQueryWarehouses
    from kge.adapters import bigquery as shared
    monkeypatch.setattr(shared, "bq_token", lambda project=None: ("stand-in", PROJECT))
    fake = FakeBigQuery()
    monkeypatch.setattr(BigQueryWarehouses, "token", lambda self: ("stand-in", PROJECT))
    monkeypatch.setattr(BigQueryWarehouses, "_post", lambda self, url, headers, body, timeout=120: fake.post(url, body))
    monkeypatch.setattr(BigQueryLoader, "account", lambda self: (None, PROJECT))
    monkeypatch.setattr(BigQueryLoader, "token", lambda self: ("stand-in", PROJECT))
    monkeypatch.setattr(BigQueryLoader, "ensure_dataset", lambda self, dataset_id: fake.datasets.add(dataset_id))
    monkeypatch.setattr(BigQueryLoader, "post", lambda self, url, headers, body, timeout=120: fake.post(url, body))
    monkeypatch.setattr(BigQueryLoader, "get", lambda self, url, headers, timeout=120: fake.get(url))
    monkeypatch.setattr(BigQueryLoader, "submit", staticmethod(fake.submit))
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


def install_graph(monkeypatch, fake: FakeBigQuery, path, artifact):
    """Compose the graph's Cypher twin and the warehouse SQL stand-in behind one REST job service."""
    from tests.graphtwin import Twin
    from kge.adapters.graphstore import bigquery
    twin = Twin(path, artifact)
    fake.graph = twin
    plan = bigquery.Connection.plan
    monkeypatch.setattr(bigquery.Connection, "plan", lambda self, cypher, params: twin.spy(plan(self, cypher, params)))
    made = bigquery.BigQueryWriter.__init__

    class Sent(list):
        def append(self, item):
            twin.scripts[item[0]] = item[1]
            super().append(item)

    def init(self, conn):
        made(self, conn)
        self.sent = Sent()
    monkeypatch.setattr(bigquery.BigQueryWriter, "__init__", init)
    monkeypatch.setattr(bigquery.Client, "_post", lambda self, url, body: fake.post(url, body))
    monkeypatch.setattr(bigquery.Client, "_get", lambda self, url: fake.get(url))
    monkeypatch.setenv("BIGQUERY_PROJECT", PROJECT)
    monkeypatch.setenv("GRAPH_STORE", "bigquery")
    return twin
