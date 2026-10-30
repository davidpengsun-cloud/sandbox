"""A stand-in for BigQuery's REST API behind the BigQuery graph store (kge/adapters/graphstore/bigquery.py), which answers
each query the store sends with what Ladybug answers that query's Cypher twin, on a Ladybug graph of its own.

No BigQuery is reachable offline, and nothing here runs the GQL or SQL the store writes: what it proves is everything
around it. Each query reaches it as BigQuery's API takes one (POST .../queries, with its named, typed queryParameters);
the twin finds the Cypher the store translated it from (a spy on Connection.plan), rebuilds the Cypher's parameters from
the queryParameters alone, runs it on Ladybug, and answers in BigQuery's REST shape: a schema typed from the values,
every cell as text, a NULL array as the empty array BigQuery gives, no NULL in an array, the rows by position in the
query's own columns, in pages, and every third query not complete at first, so the store polls. The store's mapping
back (types, NULLs, the names Ladybug gives its columns) is what the engine then reads.

The writes a batch sends (GraphStore.batch) have no Cypher twin: the twin takes each script's groups from a spy on the
store's writer and writes each row as the script's typed parameter carries it, in the script's own order, on Ladybug
(a file's rows back to a CSV with COPY's NULL rule, COPYed in), so a load, the pipeline and a steward's decision
through the store give the graph the direct ones do only if the rows survive the trip and the script's order keeps
their meaning. The DDL script makes the Ladybug graph afresh from the artifact's ddl/kuzu.cypher (ARTIFACT), and a
prune's listing and drops are answered from the generations the twin has made.

    python -m tests.graphtwin <graph.ladybug> <artifact folder> <module> [args...]

runs a module (python -m), or a script by its path, with GRAPH_STORE=bigquery on the twin, then prints one line on
stderr, "graphtwin: <n> queries, <m> Cypher texts", and writes the texts it saw to GRAPHTWIN_RECORD and its answers to
reads, by query and parameters (key), to GRAPHTWIN_ANSWERS, where those name files: what a replay with no Ladybug serves
(tests/unit/test_gcp_native.py)."""
from __future__ import annotations

import atexit
import csv
import json
import os
import re
import runpy
import sys
import tempfile
from pathlib import Path

PAGE = 40
GENERATION = re.compile(r"g[0-9a-f]{16}_[0-9a-f]{8}_")


class Twin:
    def __init__(self, ladybug_path: Path, artifact: Path | None):
        from kge.adapters.graphstore.ladybug import LadybugStore
        self.store = LadybugStore()
        self.lb = self.store.module()
        self.path = Path(ladybug_path)
        self.artifact = Path(artifact) if artifact else None
        self.db = self.conn = None
        self.twins: dict[str, str] = {}
        self.texts: set[str] = set()
        self.queries = 0
        self.pending: dict[str, list] = {}
        self.answers: dict[str, dict] = {}
        self.scripts: dict[str, list] = {}
        self.generations: set[str] = set()
        self.dropped: set[str] = set()

    def open(self):
        if self.conn is None:
            self.db = self.store.open(self.path)
            self.conn = self.lb.Connection(self.db)
        return self.conn

    def fresh(self) -> None:
        """The DDL script: a new Ladybug graph made from the artifact's own DDL."""
        if self.conn is not None:
            self.conn.close()
            self.db.close()
            self.conn = self.db = None
        for p in [self.path, *self.path.parent.glob(f"{self.path.name}.*")]:
            if p.exists():
                p.unlink()
        conn = self.open()
        for stmt in (self.artifact / "ddl" / "kuzu.cypher").read_text().splitlines():
            if stmt.strip():
                conn.execute(stmt.strip().removesuffix(";"))

    def spy(self, plan):
        self.twins[plan.sql] = plan.cypher
        self.texts.add(plan.cypher)
        return plan

    def post(self, url: str, body: dict) -> dict:
        self.queries += 1
        sql = body["query"]
        params = {p["name"]: value(p) for p in body.get("queryParameters") or []}
        if sql in self.twins:
            result = self.open().execute(self.twins[sql], params)
            if sql.startswith("SELECT"):
                out = self.answer(result)
                self.answers[key(sql, body.get("queryParameters") or [])] = self.whole(out)
                return out
            return {"jobComplete": True}
        if sql in self.scripts:
            self.apply(self.scripts[sql], params)
            return {"jobComplete": True}
        if "CREATE OR REPLACE PROPERTY GRAPH" in sql:
            self.generations |= set(GENERATION.findall(sql))
            self.fresh()
            return {"jobComplete": True}
        if "INFORMATION_SCHEMA.TABLES" in sql:
            rows = [{"f": [{"v": f"{g}Unit"}]} for g in sorted(self.generations)]
            return {"jobComplete": True, "schema": {"fields": [{"name": "table_name", "type": "STRING"}]}, "rows": rows}
        if sql.startswith("DROP PROPERTY GRAPH"):
            self.dropped |= set(GENERATION.findall(sql))
            self.generations -= self.dropped
            return {"jobComplete": True}
        raise AssertionError(f"the twin has no Cypher twin for: {sql[:300]}")

    def apply(self, groups: list, params: dict) -> None:
        """A batch's script, in its groups' order, each row as the store sent it in its parameter, written on Ladybug."""
        from kge.adapters.graphstore.ladybug import LadybugWriter
        w = LadybugWriter(self.open())
        for g in groups:
            rows = [{n: plain(*cell_) for n, cell_ in r.items()} for r in params[g.param]]
            if g.kind == "rows":
                self.copy(g.label, g.src, g.dst, [n for n, _ in g.fields], params[g.param])
            for r in rows if g.kind != "rows" else []:
                props = {n: v for n, v in r.items() if not n.startswith("_")}
                if g.kind == "upsert":
                    w.upsert(g.label, r["_key"], props)
                elif g.kind == "update":
                    w.update(g.label, r["_key"], props)
                elif g.kind == "create_node":
                    w.create_node(g.label, props)
                elif g.kind == "delete_node":
                    w.delete_node(g.label, r["_key"])
                elif g.kind == "delete_edges":
                    w.delete_edges(g.label, g.src, r["_key"], g.dst)
                elif g.kind == "create_edge":
                    w.create_edge(g.label, g.src, r["_src"], g.dst, r["_dst"], props)

    def copy(self, name: str, src: str | None, dst: str | None, cols: list[str], rows: list[dict]) -> None:
        """An artifact file's rows as the store sent them, COPYed into Ladybug: a node table's under its own columns, an
        edge table's ends' keys first, under its pair of ends."""
        ends = f", from='{src}', to='{dst}'" if src else ""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "rows.csv"
            with path.open("w", newline="") as f:
                w = csv.writer(f, lineterminator="\n")
                w.writerow(cols)
                for r in rows:
                    w.writerow([field(*r[c]) for c in cols])
            self.open().execute(f"COPY {name} FROM '{path}' (header=true, parallel=false, null_strings=['\\\\N']{ends})")

    def answer(self, result) -> dict:
        names = result.get_column_names()
        records = result.get_all()
        fields = [schema_field(f"c{n}", [r[n] for r in records]) for n in range(len(names))]
        rows = [{"f": [{"v": cell(fields[n], r[n])} for n in range(len(names))]} for r in records]
        pages = [rows[i:i + PAGE] for i in range(0, len(rows), PAGE)] or [[]]
        job = f"job{self.queries}"
        self.pending[job] = pages[1:]
        first = {"jobComplete": True, "jobReference": {"jobId": job, "projectId": "twin-project"}, "schema": {"fields": fields},
                 "rows": pages[0], **({"pageToken": "1"} if len(pages) > 1 else {})}
        if self.queries % 3 == 0:
            self.pending[f"{job}:first"] = [first]
            return {"jobComplete": False, "jobReference": {"jobId": job, "projectId": "twin-project"}}
        return first

    def whole(self, first: dict) -> dict:
        """The whole answer a query's pages make, for a replay: {schema, rows}."""
        job = (first.get("jobReference") or {}).get("jobId")
        head = self.pending.get(f"{job}:first", [first])[0]
        return {"jobComplete": True, "schema": head.get("schema", {}),
                "rows": head.get("rows", []) + [r for page in self.pending.get(job, []) for r in page]}

    def get(self, url: str) -> dict:
        job = url.split("/queries/")[1].split("?")[0]
        if f"{job}:first" in self.pending:
            return self.pending.pop(f"{job}:first")[0]
        pages = self.pending[job]
        page = pages.pop(0)
        return {"jobComplete": True, "rows": page, **({"pageToken": "1"} if pages else {})}


def field(v, t: str) -> str:
    """A CSV field as the artifact writes one: NULL is \\N in a STRING column and an empty field in any other."""
    if v is None:
        return "\\N" if t == "STRING" else ""
    return str(v)


def value(p: dict):
    """A queryParameter's value, as the Cypher twin takes it."""
    t = p["parameterType"]
    v = p.get("parameterValue") or {}
    if t["type"] == "ARRAY":
        inner = t["arrayType"]
        if inner["type"] == "STRUCT":
            types = {s["name"]: s["type"] for s in inner["structTypes"]}
            out = []
            for x in v.get("arrayValues", []):
                row = {}
                for n, t in types.items():
                    cell_ = x["structValues"].get(n, {})
                    if t["type"] == "ARRAY":
                        row[n] = ([scalar(t["arrayType"]["type"], y.get("value")) for y in cell_.get("arrayValues", [])],
                                  f"ARRAY<{t['arrayType']['type']}>")
                    else:
                        row[n] = (cell_.get("value"), t["type"])
                out.append(row)
            return out
        return [scalar(inner["type"], x.get("value")) for x in v.get("arrayValues", [])]
    return scalar(t["type"], v.get("value"))


def plain(v, t: str):
    """A struct field's value as Ladybug takes it: typed, and an empty vector, BigQuery's NULL, NULL."""
    if t == "ARRAY<FLOAT64>" and not v:
        return None
    return v if t.startswith("ARRAY") else scalar(t, v)


def scalar(t: str, v):
    if v is None:
        return None
    if t == "INT64":
        return int(v)
    if t == "FLOAT64":
        return float(v)
    if t == "BOOL":
        return v == "true"
    return v


def kind(v) -> str | None:
    if isinstance(v, bool):
        return "BOOLEAN"
    if isinstance(v, int):
        return "INTEGER"
    if isinstance(v, float):
        return "FLOAT"
    if isinstance(v, str):
        return "STRING"
    return None


def schema_field(name: str, values: list) -> dict:
    """A REST schema field typed as BigQuery would type the column from its values."""
    lists = [v for v in values if isinstance(v, (list, tuple))]
    if lists:
        inner = [kind(x) for v in lists for x in v if x is not None]
        t = "FLOAT" if "FLOAT" in inner else (inner[0] if inner else "STRING")
        return {"name": name, "type": t, "mode": "REPEATED"}
    kinds = [kind(v) for v in values if v is not None]
    t = "FLOAT" if "FLOAT" in kinds else (kinds[0] if kinds else "STRING")
    return {"name": name, "type": t, "mode": "NULLABLE"}


def text(v) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, float):
        return repr(v)
    return str(v)


def cell(f: dict, v):
    if f["mode"] == "REPEATED":
        return [{"v": text(x)} for x in (v or []) if x is not None]
    return None if v is None else text(v)


def key(sql: str, parameters: list[dict]) -> str:
    """What a replay finds an answer by: the query and its parameters."""
    import hashlib
    return hashlib.sha256(json.dumps([sql, parameters], sort_keys=True).encode()).hexdigest()


def install(ladybug_path: Path, artifact: Path | None = None) -> Twin:
    """The twin behind the BigQuery graph store, in this process: GRAPH_STORE=bigquery, a project named, the REST seam
    and the spy in place."""
    from kge.adapters.graphstore import bigquery
    os.environ["GRAPH_STORE"] = "bigquery"
    os.environ.setdefault("BIGQUERY_PROJECT", "twin-project")
    twin = Twin(ladybug_path, artifact)
    plan = bigquery.Connection.plan
    bigquery.Connection.plan = lambda self, cypher, params: twin.spy(plan(self, cypher, params))
    made = bigquery.BigQueryWriter.__init__

    class Sent(list):
        def append(self, item) -> None:
            twin.scripts[item[0]] = item[1]
            super().append(item)

    def init(self, conn) -> None:
        made(self, conn)
        self.sent = Sent()
    bigquery.BigQueryWriter.__init__ = init
    bigquery.Client._post = lambda self, url, body: twin.post(url, body)
    bigquery.Client._get = lambda self, url: twin.get(url)
    return twin


def main() -> None:
    graph, artifact, module, *args = sys.argv[1:]
    twin = install(Path(graph), Path(artifact) if artifact != "-" else None)

    def report() -> None:
        print(f"graphtwin: {twin.queries} queries, {len(twin.texts)} Cypher texts", file=sys.stderr)
        out = os.environ.get("GRAPHTWIN_RECORD")
        if out:
            held = set(json.loads(Path(out).read_text())) if Path(out).exists() else set()
            Path(out).write_text(json.dumps(sorted(held | twin.texts), indent=0))
        answers = os.environ.get("GRAPHTWIN_ANSWERS")
        if answers:
            held = json.loads(Path(answers).read_text()) if Path(answers).exists() else {}
            Path(answers).write_text(json.dumps({**held, **twin.answers}))
    atexit.register(report)
    sys.argv = [module, *args]
    if module.endswith(".py"):
        runpy.run_path(module, run_name="__main__")
    else:
        runpy.run_module(module, run_name="__main__")


if __name__ == "__main__":
    main()
