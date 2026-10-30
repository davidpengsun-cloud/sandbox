"""The pilot's graph store: BigQuery, the GraphStore of kge/ports.py where GRAPH_STORE=bigquery. A client's graph is node
and edge tables and a property graph over them in its graph dataset, <prefix>_<client>_graph (BIGQUERY_DATASET_PREFIX,
kge by default, in BIGQUERY_PROJECT), the tables of each load under a prefix of its own, a generation of its model
hash, g<first 16 of the hash>_<8 hex characters>_ (g<hash>_<gen>_Unit, g<hash>_<gen>_HAS_TABLE__Unit__Tbl,
g<hash>_<gen>_graph). A load makes a new generation and never replaces the tables a served graph reads, even when the
same hash is loaded again; a release names a generation (its pointer), so a rollback is a pointer, and prune drops the
generations nothing names once a release is made. Nothing of Ladybug or Kuzu is imported, built or kept.

The graph's file in the graph store's folder, graph.bigquery (path), is a pointer, not the graph: the dataset, the
prefix and the schema (gql.Schema) its load made the tables from. It is what the LOADED, GATE, SIGNOFF and RELEASED
markers sit beside, what a release's tarball carries, and what a process finds when it opens the graph, as Ladybug's
graph.ladybug is. open names the graph by its folder (the client's, then the model hash's) until create writes the
pointer.

A connection takes the Cypher the code sends and runs its BigQuery form (gql.py): reads as GQL in GRAPH_TABLE, writes as
DML on the tables, each one query job over the REST API (jobs.query, polled with getQueryResults until done and read page
by page), parameters typed and named, never written into the text. The rows come back under the names Ladybug gives
its columns, typed by the result's schema, so a caller reads them as it reads Ladybug's. A transaction BigQuery aborts
for a concurrent update is run again, and so is a query BigQuery turned away before running it (rateLimitExceeded, its
403, or HTTP 429) and a read that failed on its side (a 5xx, backendError, internalError): up to RETRIES times, after a
short back-off with jitter, each within the request's budget. A write that failed on BigQuery's side is never sent again,
since it may have run. A transient failure still failing after that is GraphStoreBusy, an ExternalCallError: the API's
503, never a 500. BigQuery lets any number of processes read and write a graph at once, so open never refuses with
GraphLocked.

    python -m kge.adapters.graphstore.bigquery --graph-model <artifact>/graph-model.yaml [--inventory <queries.json>]

prints, a JSON line each, every whole Cypher text the package's code holds (literals) or a JSON list of them names,
beside its BigQuery form and its parameters' types, for checking against a live dataset; it runs nothing."""
from __future__ import annotations

import argparse
import csv
import json
import os
import random
import re
import sys
import threading
import time
import uuid
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Callable

from kge.adapters.deadline import ExternalCallError, query_budget, request_cap, request_io, request_remaining
from kge.adapters import released
from kge.adapters.bigquery import DEFAULT_DATASET_PREFIX, bq_account, dataset_name, graph_kind, served
from kge.adapters.graphstore.base import Op, Writer, held
from kge.adapters.graphstore.gql import (TEMPLATE, Column, Group, Namespace, Plan, Schema, Translate, batch_sql, bq_type, encode,
                                         groups, quote, rows, struct_rows)
from kge.adapters.graphstore.gql import ddl as gql_ddl
from kge.pipeline.settings import settings

STORE = "bigquery"
DIALECT = "bigquery"
POINTER_FORMAT = 1
HASH = re.compile(r"[0-9a-f]{64}")
API = "https://bigquery.googleapis.com/bigquery/v2/projects"
RETRIES = 3
BACKOFF = 0.25
QUERY_DEADLINE = 600
BATCH_ROWS = 500
NULL = "\\N"


def prefix_of(model_hash: str, generation: str | None = None) -> str:
    """The table prefix of one load of a model hash's graph: g<first 16 of the hash>_<generation>_, a fresh generation
    each load, so a load never replaces the tables a served graph reads; without a generation, every one's start."""
    return f"g{model_hash[:16]}_" + (f"{generation}_" if generation else "")


GENERATION = re.compile(r"^g[0-9a-f]{16}_[0-9a-f]{8}_")


def project() -> str:
    """The project holding the datasets: BIGQUERY_PROJECT, else VERTEX_PROJECT, else the credentials'."""
    named = (settings().BIGQUERY_PROJECT or settings().VERTEX_PROJECT or "").strip()
    return named or bq_account()[1]


class Client:
    """BigQuery's REST API for one project: a query in, the whole result out. _post and _get are the seam a test stands
    a fake behind."""

    def __init__(self, project_id: str, sleeper: Callable[[float], None] = time.sleep):
        self.project = project_id
        self._sleep = sleeper

    def _token(self) -> str:
        credentials, _ = bq_account(self.project)
        if not credentials.valid:
            from google.auth.transport.requests import Request
            credentials.refresh(Request())
        return credentials.token

    @request_io
    def _call(self, url: str, body: dict | None) -> dict:
        request = urllib.request.Request(
            url, method="POST" if body is not None else "GET",
            headers={"authorization": f"Bearer {self._token()}", **({"content-type": "application/json"} if body is not None else {})},
            data=json.dumps(body, ensure_ascii=False).encode() if body is not None else None)
        try:
            with urllib.request.urlopen(request, timeout=request_cap(120)) as response:
                return json.loads(response.read())
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode(errors="replace")
            try:
                error = json.loads(raw).get("error", {})
                reason = ", ".join(e.get("reason", "") for e in error.get("errors", []))
                message = error.get("message") or raw
            except ValueError:
                reason, message = "", raw
            raise QueryFailed(f"BigQuery graph error (HTTP {exc.code}): {message}", reason, exc.code) from None

    def _post(self, url: str, body: dict) -> dict:
        return self._call(url, body)

    def _get(self, url: str) -> dict:
        return self._call(url, None)

    def query(self, sql: str, parameters: list[dict], read: bool = False) -> dict:
        """The query's result, {schema, rows}, every page of it; QueryFailed, naming BigQuery's reason, if it fails. Sent
        again, up to RETRIES times after a jittered back-off: an aborted transaction, a query BigQuery turned away before
        running it (QueryFailed.rejected, but on a poll of the job it started: polled), and a read (read) that failed on
        BigQuery's side (QueryFailed.transient). A
        transient failure that is not sent again, or fails each time, is GraphStoreBusy, a 503 from the API."""
        for attempt in range(RETRIES + 1):
            try:
                return self._once(sql, parameters)
            except QueryFailed as exc:
                again = exc.aborted or (exc.rejected and not exc.polled) or (read and exc.transient)
                if again and attempt < RETRIES:
                    request_remaining()
                    self._sleep(request_cap(BACKOFF * 2 ** attempt * random.uniform(1, 2)))
                    continue
                if exc.transient:
                    raise GraphStoreBusy(f"The graph store is busy: {exc} (tried {attempt + 1} times); try again shortly",
                                         transient=True) from None
                raise
        raise AssertionError("unreachable")

    def _once(self, sql: str, parameters: list[dict]) -> dict:
        body = {"query": sql, "useLegacySql": False, "timeoutMs": 60000, "parameterMode": "NAMED", "queryParameters": parameters}
        query_budget(body, QUERY_DEADLINE)
        data = self._post(f"{API}/{self.project}/queries", body)
        request_remaining()
        deadline = time.monotonic() + request_cap(QUERY_DEADLINE)
        ref = data.get("jobReference") or {}
        while not data.get("jobComplete", True):
            request_remaining()
            if time.monotonic() > deadline:
                raise QueryFailed(f"BigQuery graph query {ref.get('jobId')} did not finish within {QUERY_DEADLINE} seconds", "timeout")
            data = self._poll(self.results_url(ref, None))
        request_remaining()
        failed(data)
        out = {"schema": data.get("schema") or {}, "rows": list(data.get("rows") or [])}
        token = data.get("pageToken")
        while token:
            page = self._poll(self.results_url(ref, token))
            request_remaining()
            failed(page)
            out["rows"] += page.get("rows") or []
            token = page.get("pageToken")
        return out

    def _poll(self, url: str) -> dict:
        """A GET of a job the POST started: a failure here is marked polled, since the job may have run."""
        try:
            return self._get(url)
        except QueryFailed as exc:
            exc.polled = True
            raise

    def results_url(self, ref: dict, token: str | None) -> str:
        if not ref.get("jobId"):
            raise QueryFailed("BigQuery graph query is incomplete and has no job id", "")
        query = {"timeoutMs": int(request_cap(60) * 1000), **({"location": ref["location"]} if ref.get("location") else {}),
                 **({"pageToken": token} if token else {})}
        return f"{API}/{ref.get('projectId') or self.project}/queries/{urllib.parse.quote(ref['jobId'])}?{urllib.parse.urlencode(query)}"


class QueryFailed(RuntimeError):
    """A BigQuery query that failed, with BigQuery's reason and the HTTP status it came with (None for a job's own
    error, read from its status); polled, where it failed polling a job its POST had started."""

    def __init__(self, message: str, reason: str = "", code: int | None = None):
        super().__init__(message)
        self.reason = reason
        self.code = code
        self.polled = False

    @property
    def aborted(self) -> bool:
        text = f"{self.reason} {self}".lower()
        return "aborted" in text or "concurrent update" in text

    @property
    def rejected(self) -> bool:
        """BigQuery turned the query away before running it: rateLimitExceeded (a 403, or a job's own error, which runs
        nothing of its transaction) or HTTP 429. Any query, a write too, may be sent again, unless it was a poll that was
        turned away (polled)."""
        return self.code == 429 or "ratelimitexceeded" in self.reason.lower()

    @property
    def transient(self) -> bool:
        """Whether the same query may pass if sent again, as ExternalCallError.transient says of an outside call: turned
        away (rejected), HTTP 429 or a 5xx, or BigQuery's backendError or internalError; never a refusal such as
        quotaExceeded, accessDenied or invalidQuery."""
        reason = self.reason.lower()
        return self.rejected or (self.code or 0) >= 500 or "backenderror" in reason or "internalerror" in reason


class GraphStoreBusy(ExternalCallError):
    """A graph-store query that failed transiently (QueryFailed.transient) and was not sent again, or failed each time
    it was: one line, BigQuery's own words, and a 503 from the API (kge/app.py), never a 500."""


def failed(data: dict) -> None:
    errors = (data.get("status") or {}).get("errors") or data.get("errors") or []
    fatal = (data.get("status") or {}).get("errorResult")
    if fatal or errors:
        items = [fatal] if fatal else errors
        raise QueryFailed("BigQuery graph query failed: " + "; ".join(e.get("message", str(e)) for e in items),
                          ", ".join(e.get("reason", "") for e in items))


class Result:
    """A query's rows, as a Ladybug result offers them: get_all, get_as_df, has_next and get_next."""

    def __init__(self, columns: list[str], records: list[list]):
        self.columns = columns
        self.records = records
        self._at = 0

    def get_all(self) -> list[list]:
        return [list(r) for r in self.records]

    def get_as_df(self):
        import pandas as pd
        return pd.DataFrame([list(r) for r in self.records], columns=self.columns, dtype=object)

    def get_column_names(self) -> list[str]:
        return list(self.columns)

    def has_next(self) -> bool:
        return self._at < len(self.records)

    def get_next(self) -> list:
        row = self.records[self._at]
        self._at += 1
        return list(row)

    def close(self) -> None:
        return None

    def __enter__(self):
        return self

    def __exit__(self, *exc) -> None:
        self.close()


def pointer_in(pointer: Any) -> dict | None:
    """pointer, if it is a graph pointer; else None."""
    return pointer if isinstance(pointer, dict) and pointer.get("format") == POINTER_FORMAT else None


def read_pointer(path: Path) -> dict | None:
    """The graph pointer at path, or, where path is a release record (kge/adapters/released.py), the one it names; None
    if there is none, or it is not one."""
    path = Path(path)
    if path.name == released.RECORD:
        return pointer_in((released.read(path.parent) or {}).get("graph"))
    try:
        pointer = json.loads(path.read_text())
    except (OSError, ValueError):
        return None
    return pointer_in(pointer)


class Graph:
    """One model hash's graph in BigQuery: where its tables are, and its schema once its load has made them."""

    def __init__(self, path: Path, client: Client | None = None, pointer: dict | None = None):
        """The graph the pointer at path names, or pointer, where the caller read it already (a release record's
        entry), so what opens is what it read."""
        self.path = Path(path)
        pointer = pointer_in(pointer) or self.read()
        if pointer is not None:
            self.ns = Namespace(project(), pointer["dataset"], pointer["prefix"])
            self.schema: Schema | None = Schema.from_json(pointer["schema"])
        else:
            model_hash = self.path.parent.name
            if not HASH.fullmatch(model_hash):
                raise ValueError(f"{self.path}: a BigQuery graph is kept in its model hash's folder, <store>/<client>/<hash>/")
            prefix = (settings().BIGQUERY_DATASET_PREFIX or "").strip() or DEFAULT_DATASET_PREFIX
            self.ns = Namespace(project(), dataset_name(prefix, self.path.parent.parent.name, graph_kind()), "")
            self.schema = None
        self.client = client or Client(self.ns.project)
        self.kept: dict[tuple[str, str], Result] = {}
        self.lock = threading.Lock()

    def read(self) -> dict | None:
        return read_pointer(self.path)

    def write(self, schema: Schema) -> None:
        """The pointer, written whole once the tables are made: replaced in one step, never half written."""
        self.schema = schema
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name(f".{self.path.name}.{os.getpid()}")
        tmp.write_text(json.dumps({"format": POINTER_FORMAT, "store": STORE, "dataset": self.ns.dataset, "prefix": self.ns.prefix,
                                   "graph": f"{self.ns.prefix}graph", "schema": schema.to_json()}, indent=1, sort_keys=True) + "\n")
        os.replace(tmp, self.path)

    def translate(self) -> Translate:
        if self.schema is None:
            raise RuntimeError(f"Binder exception: the BigQuery graph {self.path} has no tables yet: make load makes them")
        return Translate(self.schema, self.ns)

    def close(self) -> None:
        return None


class Connection:
    """A connection to a BigQuery graph: execute(cypher, params) runs the query's BigQuery form."""

    def __init__(self, graph: Graph):
        self.graph = graph

    def plan(self, cypher: str, params: dict | None) -> Plan:
        return self.graph.translate()(cypher, params)

    def execute(self, cypher: str, params: dict | None = None) -> Result:
        """The query's rows. A read of what only the load writes (no type of the schema's mutable ones, and no element
        of any type) is kept for the process, as the engine reads its domain once; a write here forgets every kept read."""
        params = dict(params or {})
        plan = self.plan(cypher, params)
        if plan.kind == "noop":
            return Result([], [])
        graph = self.graph
        keep = plan.kind == "rows" and not plan.touches & (graph.schema.mutable | {"*"})
        key = (plan.sql, json.dumps({k: params.get(k) for k in plan.params}, sort_keys=True, default=str))
        if keep:
            with graph.lock:
                held = graph.kept.get(key)
            if held is not None:
                return Result(held.columns, held.get_all())
        data = self.run(plan.sql, [encode(name, bq, params.get(name)) for name, bq in plan.params.items()], plan.kind == "rows")
        if plan.kind != "rows":
            with graph.lock:
                graph.kept.clear()
            return Result([], [])
        names, records = rows(data, plan.columns)
        if keep:
            with graph.lock:
                graph.kept[key] = Result(names, [list(r) for r in records])
        return Result(names, records)

    def run(self, sql: str, parameters: list[dict], read: bool = False) -> dict:
        return self.graph.client.query(sql, parameters, read)

    def close(self) -> None:
        return None

    def __enter__(self):
        return self

    def __exit__(self, *exc) -> None:
        self.close()


def cell(value: str, cypher_type: str) -> Any:
    """One field of an artifact's CSV as COPY reads it: \\N is NULL in a STRING column and an empty field NULL in any
    other; '' stays '' in a STRING column."""
    t = cypher_type.strip().upper()
    if t == "STRING":
        return None if value == NULL else value
    if value == "":
        return None
    if t == "INT64":
        return int(value)
    if t == "DOUBLE":
        return float(value)
    if t == "BOOLEAN":
        if value.lower() not in ("true", "false"):
            raise ValueError(f"{value!r} is not a BOOLEAN")
        return value.lower() == "true"
    raise ValueError(f"an artifact column of type {cypher_type} cannot be loaded")


class BigQueryStore:
    """BigQuery as the graph store."""

    name = STORE
    dialect = DIALECT

    def module(self):
        """This module: BigQuery needs no library beyond the REST API it calls."""
        return sys.modules[__name__]

    def path(self, root: Path) -> Path:
        """The graph's pointer under root: root/graph.bigquery."""
        return Path(root) / f"graph.{STORE}"

    def sidecars(self, path: Path) -> list[Path]:
        """None: the graph is BigQuery's tables, and the pointer is written whole."""
        return []

    def open(self, path: Path, pidfile: Path | None = None, entry: dict | None = None) -> Graph:
        """The graph entry names (a release record's), else the one the pointer at path names, or, with none there yet,
        the one its folder names, for a load to make."""
        return Graph(path, pointer=entry)

    def connect(self, db: Graph) -> Connection:
        return Connection(db)

    def info(self, conn: Any = None) -> dict:
        """{engine, version, storage_version}: BigQuery's GoogleSQL, versioned by the service, not the process."""
        return {"engine": STORE, "version": "GoogleSQL", "storage_version": None}

    def q(self, conn: Connection) -> Callable[..., list[dict]]:
        def q(cypher: str, params: dict | None = None) -> list[dict]:
            result = conn.execute(cypher, params or {})
            return [dict(zip(result.columns, r)) for r in result.records]
        return q

    def create(self, conn: Connection, model: dict, ddl: list[str]) -> None:
        """A new generation of the graph's tables and property graph, from the compiled model's BigQuery DDL
        (kge/compiler/emit.py bigquery_ddl), run as one script, then the pointer, naming the generation, with the schema
        from the same model (graph-model.yaml). The generation before it is left as it is: a served graph keeps reading
        it until a release names another, and prune drops it once nothing does."""
        graph = conn.graph
        if not HASH.fullmatch(graph.path.parent.name):
            raise ValueError(f"{graph.path}: a BigQuery graph is kept in its model hash's folder, <store>/<client>/<hash>/")
        graph.ns.prefix = prefix_of(graph.path.parent.name, uuid.uuid4().hex[:8])
        graph.schema = None
        ns = graph.ns
        text = "; ".join(s.replace("${project}", ns.project).replace("${dataset}", ns.dataset).replace("${prefix}", ns.prefix)
                         for s in ddl) + ";"
        conn.run(text, [])
        conn.graph.write(Schema.from_graph_model(model))

    def batch(self, conn: Connection) -> "BigQueryWriter":
        """A batch of writes, sent as one script of set-based statements (BigQueryWriter)."""
        return BigQueryWriter(conn)

    def ddl(self, folder: Path, model: dict) -> list[str]:
        """The compiled model's tables and property graph (gql.py ddl), from its graph-model.yaml, model, naming the
        project, dataset and prefix as ${project}, ${dataset} and ${prefix}, which create fills (kge/compiler/emit.py
        bigquery_ddl gives the same)."""
        return gql_ddl(Schema.from_graph_model(model), TEMPLATE)

    def release_entry(self, path: Path) -> dict | None:
        """The graph's pointer at path, whole: the generation this release names, which the release record keeps."""
        return read_pointer(path)

    def generation_of(self, entry: dict | None) -> str | None:
        """The table prefix a graph pointer names, g<first 16 of the hash>_<generation>_; None for none."""
        pointer = pointer_in(entry)
        return pointer["prefix"] if pointer is not None else None

    def served(self, path: Path) -> Path:
        """The hash's release record where it names a graph (kge/adapters/released.py), else the separate released
        pointer before it where there is one, else path."""
        folder = Path(path).parent
        record = released.path(folder)
        if record.is_file() and read_pointer(record) is not None:
            return record
        legacy = folder / released.LEGACY["graph"]
        return legacy if read_pointer(legacy) is not None else Path(path)

    def generation(self, path: Path) -> str | None:
        """The table prefix the pointer at path names, g<first 16 of the hash>_<generation>_; None without a pointer."""
        return self.generation_of(read_pointer(path))

    def loaded(self, path: Path) -> bool:
        """The pointer at path names a generation and its folder's LOADED marker holds BigQuery's entry: a load takes the
        entry out before it makes a new generation and writes it once the pointer names that one whole, so the
        generation the pointer names is the one marked. Read from the files alone, no query."""
        return self.generation(path) is not None and self.name in held(Path(path).parent)

    def prune(self, root: Path, held=()) -> list[str]:
        """Drop every generation in the client's graph dataset that nothing may read: one no pointer under root names
        (each model hash's folder of the client's graph store: the released, the current and the candidates, each folder's
        release record among them, kge/adapters/released.py, or the separate records before it), nor held, the generations of the releases a service may still serve (the release
        ledger's, kge/graph/release.py served: the one just released and the one before it, so a service that follows
        each release, or restarts, before the release after next never loses the tables it reads). The ledger before
        release identities, root/graph.bigquery.served, is kept from too, and retired once a prune has read it. Each
        dataset a pointer names is pruned on its own, the graph dataset and a candidates one alike, whichever pointer
        sorts first. The generations, by table prefix, that it dropped."""
        legacy = Path(root) / f"graph.{STORE}.served"
        kept = {*held, *served(legacy, None)}
        folders = sorted(f for f in Path(root).glob("*") if f.is_dir())
        named = [(f / f"graph.{STORE}", read_pointer(f / f"graph.{STORE}")) for f in folders]
        named += [(released.path(f), pointer_in((released.read(f) or {}).get("graph"))) for f in folders]
        held = [Graph(p, pointer=pointer) for p, pointer in named if pointer is not None]
        datasets: dict[tuple[str, str], list[Graph]] = {}
        for g in held:
            datasets.setdefault((g.ns.project, g.ns.dataset), []).append(g)
        dropped = []
        for (project_id, dataset), graphs in sorted(datasets.items()):
            named = {g.ns.prefix for g in graphs} | kept
            listed = graphs[0].client.query(
                f"SELECT table_name FROM {quote(f'{project_id}.{dataset}')}.INFORMATION_SCHEMA.TABLES "
                "WHERE REGEXP_CONTAINS(table_name, @pattern)", [encode("pattern", "STRING", GENERATION.pattern)])
            tables = [r["f"][0]["v"] for r in listed.get("rows", [])]
            stale = sorted({GENERATION.match(t).group(0) for t in tables if GENERATION.match(t)} - named)
            if not stale:
                continue
            drop(graphs[0].client, project_id, dataset, stale, tables)
            dropped += stale
        legacy.unlink(missing_ok=True)
        return dropped

    def discard(self, path: Path) -> list[str]:
        """The generation the pointer at path names dropped, its property graph and its tables, and the pointer removed;
        never the generation the hash's release record names (a candidate that compiled to the hash being served).
        What was dropped, by table prefix."""
        path = Path(path)
        pointer = read_pointer(path)
        if pointer is None:
            return []
        record = released.read(path.parent) or {}
        named = pointer_in(record.get("graph"))
        mine = Graph(path, pointer=pointer)
        if named is not None and (named["dataset"], named["prefix"]) == (pointer["dataset"], pointer["prefix"]):
            return []
        ns = mine.ns
        listed = mine.client.query(
            f"SELECT table_name FROM {quote(f'{ns.project}.{ns.dataset}')}.INFORMATION_SCHEMA.TABLES "
            "WHERE STARTS_WITH(table_name, @prefix)", [encode("prefix", "STRING", ns.prefix)])
        tables = [r["f"][0]["v"] for r in listed.get("rows", [])]
        drop(mine.client, ns.project, ns.dataset, [ns.prefix], tables)
        path.unlink(missing_ok=True)
        return [ns.prefix]


def drop(client: "Client", project_id: str, dataset: str, prefixes: list[str], tables: list[str]) -> None:
    """The generations prefixes name dropped from dataset, each its property graph and its tables (of tables), in one
    script."""
    drops = []
    for prefix in prefixes:
        drops.append(f"DROP PROPERTY GRAPH IF EXISTS {Namespace(project_id, dataset, prefix).graph}")
        drops += [f"DROP TABLE IF EXISTS {quote(f'{project_id}.{dataset}.{t}')}" for t in tables if t.startswith(prefix)]
    client.query("; ".join(drops) + ";", [])


class BigQueryWriter(Writer):
    """Writes kept until the batch ends, then sent as one script: one set-based statement per kind of write and table
    (gql.py groups and batch_sql), in one transaction, the kinds in a fixed order (deletes of edges, of nodes, then
    nodes made or upserted, updated, then edges made). A write whose meaning that order would change sends what is kept
    first (interferes), so the meaning of the order made is kept. sent keeps each script and its groups, for a test."""

    def __init__(self, conn: Connection):
        super().__init__(conn)
        self.sent: list[tuple[str, list[Group]]] = []
        self.clear()

    def clear(self) -> None:
        self.pending: list[Op] = []
        self.nodes: set[tuple] = set()
        self.updated: set[tuple] = set()
        self.ends: set[tuple] = set()
        self.edges: set[tuple] = set()

    def interferes(self, op: Op) -> bool:
        """Whether op, sent in its place in the order, would change what a kept write means: the edges deleted from a
        node after some were made from it; a node deleted after a write on it; a node made or upserted after an update
        of it or an edge to or from it."""
        node = (op.label, op.key)
        if op.kind == "delete_edges":
            return (op.label, op.src, op.key, op.dst) in self.edges
        if op.kind == "delete_node":
            return node in self.nodes
        if op.kind in ("upsert", "create_node"):
            return node in self.updated or node in self.ends
        return False

    def apply(self, op: Op) -> None:
        if op.kind == "rows":
            op.data = file_rows(self.conn.graph.schema, op.label, op.src, op.dst, op.path)
        if self.interferes(op):
            self.flush()
        self.pending.append(op)
        if op.kind in ("upsert", "update"):
            self.nodes.add((op.label, op.key))
        if op.kind == "update":
            self.updated.add((op.label, op.key))
        if op.kind == "create_edge":
            self.edges.add((op.label, op.src, op.key, op.dst))
            self.ends |= {(op.src, op.key), (op.dst, op.dst_key)}
            self.nodes |= {(op.src, op.key), (op.dst, op.dst_key)}

    def flush(self) -> None:
        if not self.pending:
            return
        graph = self.conn.graph
        if graph.schema is None:
            raise RuntimeError(f"the BigQuery graph {graph.path} has no tables yet")
        made = groups(graph.schema, self.pending)
        for g in made:
            if g.kind == "rows":
                g.fields, g.rows, g.src, g.dst = g.data
        statements = [sql for g in made for sql in batch_sql(graph.schema, graph.ns, g)]
        text = statements[0] if len(statements) == 1 else \
            "BEGIN TRANSACTION; " + " ".join(f"{x};" for x in statements) + " COMMIT TRANSACTION;"
        self.sent.append((text, made))
        self.conn.run(text, [struct_rows(g) for g in made])
        with graph.lock:
            graph.kept.clear()
        self.clear()


def file_rows(schema: Schema | None, label: str, src: str | None, dst: str | None, path: Path) -> tuple:
    """An artifact file's header and rows as struct fields and typed values, with COPY's NULL rule (cell): a node file's
    columns, or an edge file's ends' keys (_src, _dst) and properties; and the edge's pair of ends. Read when the write
    is made, as COPY reads it, so a file may go once it is written."""
    if schema is None:
        raise RuntimeError("the BigQuery graph has no tables yet")
    with Path(path).open(newline="") as f:
        reader = csv.reader(f)
        header = next(reader)
        records = list(reader)
    if label in schema.nodes:
        t = schema.nodes[label]
        types = [t.props[h] for h in header]
        names = list(header)
    else:
        pairs = schema.edge(label)
        chosen = [e for e in pairs if src is None or (e.src, e.dst) == (src, dst)]
        if len(chosen) != 1:
            raise ValueError(f"{label}: name its pair of ends; it joins {', '.join(f'{e.src}-{e.dst}' for e in pairs)}")
        e = chosen[0]
        src, dst = e.src, e.dst
        types = [schema.node(e.src).props[schema.node(e.src).key], schema.node(e.dst).props[schema.node(e.dst).key]] + \
                [e.props[h] for h in header[2:]]
        names = ["_src", "_dst"] + header[2:]
    return [(n, bq_type(t)) for n, t in zip(names, types)], [[cell(v, t) for v, t in zip(r, types)] for r in records], src, dst


def bq_scalar(cypher_type: str) -> str:
    t = cypher_type.strip().upper()
    return {"STRING": "STRING", "INT64": "INT64", "DOUBLE": "FLOAT64", "BOOLEAN": "BOOL"}[t]


def struct_array(name: str, fields: list[dict], records: list[list]) -> dict:
    """An ARRAY<STRUCT<...>> query parameter of records, each field typed; None a NULL."""
    def one(f: dict, v: Any) -> dict:
        if v is None:
            return {}
        if f["type"] == "BOOL":
            return {"value": "true" if v else "false"}
        if f["type"] == "FLOAT64":
            return {"value": repr(float(v))}
        return {"value": str(v)}
    return {"name": name,
            "parameterType": {"type": "ARRAY", "arrayType": {"type": "STRUCT", "structTypes": [
                {"name": f["name"], "type": {"type": f["type"]}} for f in fields]}},
            "parameterValue": {"arrayValues": [{"structValues": {f["name"]: one(f, v) for f, v in zip(fields, r)}} for r in records]}}


CYPHER = re.compile(r"^\s*(MATCH|OPTIONAL MATCH|MERGE|CREATE \(|CALL table_info)")


def literals(package: Path) -> dict[str, str]:
    """Every whole Cypher text the code under package holds as a string literal, by file and line: what the code asks
    a graph store, but an f-string's, whose texts are made as it runs, and the parity oracle's (kge/graph/legacy.py),
    which only the local stack runs."""
    import ast
    out = {}
    for path in sorted(Path(package).rglob("*.py")):
        if path.name == "legacy.py" or "adapters/graphstore" in path.as_posix() or path.parent.name == "appstate":
            continue
        tree = ast.parse(path.read_text())
        joined = {id(v) for n in ast.walk(tree) if isinstance(n, ast.JoinedStr) for v in n.values}
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in joined and CYPHER.match(node.value):
                out[f"{path.relative_to(Path(package).parent).as_posix()}:{node.lineno}"] = node.value
    return out


def inventory(texts: list[str], schema: Schema, ns: Namespace) -> list[dict]:
    """Each Cypher text beside its BigQuery form, or why it has none."""
    translate = Translate(schema, ns)
    out = []
    for text in texts:
        try:
            plan = translate(text, None)
            out.append({"cypher": text, "kind": plan.kind, "bigquery": plan.sql, "params": plan.params})
        except Exception as exc:
            out.append({"cypher": text, "kind": "none", "error": f"{type(exc).__name__}: {exc}"})
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--inventory", help="a JSON list of Cypher texts, or an object keyed by them (default: the code's literals)")
    ap.add_argument("--graph-model", required=True, help="an artifact's graph-model.yaml, the schema to translate against")
    ap.add_argument("--project", default="your-project")
    ap.add_argument("--dataset", default="kge_client_graph")
    ap.add_argument("--hash", default="0" * 64)
    args = ap.parse_args(argv)
    import yaml
    if args.inventory:
        held = json.loads(Path(args.inventory).read_text())
        texts = list(held) if isinstance(held, (list, dict)) else []
    else:
        from kge.pipeline.settings import ROOT
        texts = list(dict.fromkeys(literals(ROOT / "kge").values()))
    schema = Schema.from_graph_model(yaml.safe_load(Path(args.graph_model).read_text()))
    for row in inventory(texts, schema, Namespace(args.project, args.dataset, prefix_of(args.hash))):
        print(json.dumps(row, ensure_ascii=False))
    return 0


__all__ = ["BigQueryStore", "Client", "Column", "Connection", "Graph", "GraphStoreBusy", "QueryFailed", "Result"]

if __name__ == "__main__":
    raise SystemExit(main())
