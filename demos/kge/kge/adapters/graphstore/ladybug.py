"""The local stack's graph store: Ladybug, a maintained fork of Kuzu with the same Python API and Cypher, embedded in the
process. This is the only module that imports it, by its name (STORE), when first used: LadybugStore, a GraphStore of
kge/ports.py, opens a graph, connects to it and names the store. A graph is graph.ladybug in the client's graph store
(Profile.graph_path). The DDL it loads is in the Cypher dialect named kuzu (kge/compiler/emit.py), a name the model
hashes carry, so it stays."""
from __future__ import annotations

import importlib
import json
from pathlib import Path
from typing import Callable

from kge.adapters.deadline import MIN_CALL_SECONDS, request_remaining
from kge.adapters.graphstore.base import GraphLocked, Op, Writer, held
from kge.pipeline.settings import ladybug_max_db_size

STORE = "ladybug"
DIALECT = "kuzu"
COPY = "(header=true, parallel=false, null_strings=['\\\\N']{ends})"
NO_WHEEL = ("this platform has no ladybug wheel (a Mac needs macOS 15 or later): run the local stack on Linux, in a "
            "container, or with the kit's Compose")
_info: dict | None = None


def serving(pidfile: Path, pid: str | None) -> tuple[str | None, str | None]:
    """The profile and the address of the API whose pid is in pidfile, as it wrote them beside it (api.json), or none
    where it wrote none, or another process's are there."""
    try:
        said = json.loads(pidfile.with_name("api.json").read_text())
    except (OSError, ValueError):
        return None, None
    if not isinstance(said, dict) or str(said.get("pid")) != pid:
        return None, None
    return said.get("profile") or None, said.get("url") or None


def literal(path: Path) -> str:
    """A Cypher string literal of path, for COPY."""
    return "'" + str(Path(path).resolve()).replace("\\", "\\\\").replace("'", "\\'") + "'"


class RequestConnection:
    """Keep local graph work synchronous, checking the shared budget before every query."""

    def __init__(self, connection):
        self.connection = connection

    def __getattr__(self, name):
        return getattr(self.connection, name)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.connection.close()

    def execute(self, *args, **kwargs):
        request_remaining(MIN_CALL_SECONDS)
        return self.connection.execute(*args, **kwargs)


class LadybugStore:
    """Ladybug as the graph store."""

    name = STORE
    dialect = DIALECT

    def module(self):
        """The store's module, ladybug, imported when first asked for."""
        try:
            return importlib.import_module(STORE)
        except ImportError as exc:
            raise ImportError(NO_WHEEL) from exc

    def path(self, root: Path) -> Path:
        """The graph kept under root: root/graph.ladybug."""
        return Path(root) / f"graph.{STORE}"

    def sidecars(self, path: Path) -> list[Path]:
        """The files the store keeps beside a graph, its write-ahead log among them: graph.<store>.*"""
        path = Path(path)
        return sorted(path.parent.glob(f"{path.name}.*"))

    def open(self, path: Path, pidfile: Path | None = None, entry: dict | None = None):
        """Open the graph, or say plainly who holds it and what to do: the API that serves it writes its pid to pidfile, in
        the profile's run state (Profile.run_dir). entry is not read: the file is the graph."""
        try:
            return self.module().Database(str(path), max_db_size=ladybug_max_db_size())
        except RuntimeError as exc:
            if "lock" not in str(exc).lower():
                raise
            held = pidfile is not None and Path(pidfile).is_file()
            pid = Path(pidfile).read_text().strip() if held else None
            who = f"the demo API (pid {pid})" if held else "another process, probably the demo API"
            profile, url = serving(Path(pidfile), pid) if held else (None, None)
            raise GraphLocked(
                f"{path} is locked by {who}. The graph store lets one process open the graph at a time. Stop the API "
                f"(kill <pid>), run this, then start the API again; or run the golden set through the API that holds it "
                f"with kge/eval/run_golden.py --profile {profile or '<its profile>'} --api {url or '<its address>'}.") from exc

    def connect(self, db):
        """A connection to an open graph."""
        return RequestConnection(self.module().Connection(db))

    def info(self, conn=None) -> dict:
        """{engine, version, storage_version}: the graph store. The version is the database's own (CALL
        db_version()), asked on conn, or with none on an in-memory database; read once per process."""
        global _info
        if _info is None:
            mod = self.module()
            db = mod.Database(max_db_size=ladybug_max_db_size()) if conn is None else None
            c = mod.Connection(db) if conn is None else conn
            try:
                version = c.execute("CALL db_version() RETURN *").get_all()[0][0]
            finally:
                if conn is None:
                    c.close()
                    db.close()
            _info = {"engine": STORE, "version": str(version), "storage_version": getattr(mod, "storage_version", None)}
        return dict(_info)

    def q(self, conn) -> Callable[..., list[dict]]:
        """A q(cypher, params) over a raw graph connection, returning dict rows with None for nulls."""
        def q(cypher: str, params: dict | None = None) -> list[dict]:
            request_remaining(MIN_CALL_SECONDS)
            df = conn.execute(cypher, params or {}).get_as_df()
            df = df.astype(object).where(df.notna(), None)
            return df.to_dict("records")
        return q

    def create(self, conn, model: dict, ddl: list[str]) -> None:
        """The graph's tables, from the artifact's DDL in the kuzu dialect (ddl/kuzu.cypher), one statement at a time."""
        for stmt in ddl:
            conn.execute(stmt)

    def batch(self, conn) -> "LadybugWriter":
        """A batch of writes, each run as it is made (LadybugWriter)."""
        return LadybugWriter(conn)

    def ddl(self, folder: Path, model: dict) -> list[str]:
        """The artifact's own DDL, ddl/kuzu.cypher, a statement a line."""
        return [s.strip().removesuffix(";") for s in (Path(folder) / "ddl" / "kuzu.cypher").read_text().splitlines() if s.strip()]

    def release_entry(self, path: Path) -> dict | None:
        """None: the graph is its own file, which a load replaces only while no process serves it."""
        return None

    def generation_of(self, entry: dict | None) -> str | None:
        """None: the file is the graph, with no generation beside its model hash."""
        return None

    def served(self, path: Path) -> Path:
        return Path(path)

    def loaded(self, path: Path) -> bool:
        """The graph file at path is there and its folder's LOADED marker holds Ladybug's entry: its load finished."""
        return Path(path).exists() and self.name in held(Path(path).parent)

    def generation(self, path: Path) -> str | None:
        """None: the file is the graph, with no generation beside its model hash."""
        return None

    def discard(self, path: Path) -> list[str]:
        """Nothing: the graph is its own file in its model hash's folder, which stays, as a local candidate always has."""
        return []

    def prune(self, root: Path, held=()) -> list[str]:
        """Nothing: each graph is a file in its model hash's folder, and the folders stay, to release again."""
        return []


class LadybugWriter(Writer):
    """Each write run at once, as one Cypher statement on the connection, in the order made; the table's key and types
    read from Ladybug (table_info) once each."""

    def __init__(self, conn):
        super().__init__(conn)
        self.tables: dict[str, tuple[str | None, dict[str, str]]] = {}

    def table(self, label: str) -> tuple[str | None, dict[str, str]]:
        if label not in self.tables:
            rows = self.conn.execute(f"CALL table_info('{label}') RETURN *").get_all()
            key = next((r[1] for r in rows if r[4] is True), None)
            self.tables[label] = (key, {r[1]: str(r[2]) for r in rows})
        return self.tables[label]

    def sets(self, var: str, label: str, props: dict, params: dict) -> list[str]:
        """var.p = value for each property: NULL for None, a vector cast to its FLOAT[n]."""
        types = self.table(label)[1]
        out = []
        for name, value in props.items():
            if value is None:
                out.append(f"{var}.{name} = NULL")
                continue
            params[f"p_{name}"] = value
            t = types.get(name, "")
            out.append(f"{var}.{name} = CAST($p_{name} AS {t})" if t.startswith("FLOAT[") else f"{var}.{name} = $p_{name}")
        return out

    def apply(self, op: Op) -> None:
        params: dict = {}
        if op.kind == "rows":
            more = f", from='{op.src}', to='{op.dst}'" if op.src else ""
            self.conn.execute(f"COPY {op.label} FROM {literal(op.path)} " + COPY.format(ends=more))
            return
        if op.kind in ("upsert", "update", "delete_node"):
            key = self.table(op.label)[0]
            params["k"] = op.key
            head = f"{'MERGE' if op.kind == 'upsert' else 'MATCH'} (n:{op.label} {{{key}: $k}})"
            if op.kind == "delete_node":
                cypher = f"{head} DETACH DELETE n"
            else:
                sets = self.sets("n", op.label, op.props, params)
                cypher = head + (f" SET {', '.join(sets)}" if sets else "")
        elif op.kind == "create_node":
            fields = [f"{n}: NULL" if v is None else f"{n}: $p_{n}" for n, v in op.props.items()]
            params.update({f"p_{n}": v for n, v in op.props.items() if v is not None})
            cypher = f"CREATE (:{op.label} {{{', '.join(fields)}}})"
        elif op.kind == "create_edge":
            params.update({"a": op.key, "b": op.dst_key})
            fields = [f"{n}: NULL" if v is None else f"{n}: $p_{n}" for n, v in op.props.items()]
            params.update({f"p_{n}": v for n, v in op.props.items() if v is not None})
            rel = f"[:{op.label} {{{', '.join(fields)}}}]" if fields else f"[:{op.label}]"
            cypher = (f"MATCH (a:{op.src} {{{self.table(op.src)[0]}: $a}}), (b:{op.dst} {{{self.table(op.dst)[0]}: $b}}) "
                      f"CREATE (a)-{rel}->(b)")
        elif op.kind == "delete_edges":
            params["a"] = op.key
            cypher = f"MATCH (a:{op.src} {{{self.table(op.src)[0]}: $a}})-[r:{op.label}]->(:{op.dst}) DELETE r"
        else:
            raise ValueError(f"no write {op.kind}")
        self.conn.execute(cypher, params)
