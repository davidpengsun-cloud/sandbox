"""The release record: what a release of a model hash names, in one file in the hash's folder of the client's graph
store, release.json (RECORD): the hash, the graph store's entry (GraphStore.release_entry: BigQuery's graph pointer,
whole; None where the graph is its own file) and retrieval's (the chunks pointer, whole; None where the graph keeps its
vectors), and the fixture warehouse versions its load checked. It is written whole, under a temporary name renamed over the old, so whoever reads it reads one release's
graph and chunks together, never one release's graph with another's chunks (review of #97). A server opens its graph
and pins its chunks from one read of it (Profile.released), and a release's identity (Profile.identity), the release
ledger and the prunes read it the same way.

Before it, a release kept the two in separate records, graph.bigquery.released and chunks.bigquery.released (LEGACY),
each replaced on its own: read composes a record from them where a folder has no record yet, so a release made before
the upgrade is served and kept as it was, and the next release's write retires them.

A record is put in place whole when it is downloaded too (kge/pipeline/objstore.py unpack: each file by temporary name
and rename). One that is there and cannot be read is refused (Unreadable, a StoreError: the store's 503), never taken
for no record, which would open the hash's last load, a generation nothing released."""
from __future__ import annotations

import json
import io
import os
import secrets
import tarfile
from pathlib import Path

from kge.ports import StoreError

RECORD = "release.json"
PARTS = ("graph", "chunks")
LEGACY = {"graph": "graph.bigquery.released", "chunks": "chunks.bigquery.released"}
PRE_EVAL = "eval-pre.json"
POST_EVAL = "eval-post.json"
POST_FAILED = 3


def evaluation(folder: Path, stage: str) -> dict | None:
    """Evaluation evidence beside the release record, carried by the existing work and release stores."""
    return _json(Path(folder) / {"pre": PRE_EVAL, "post": POST_EVAL}[stage])


def write_evaluation(folder: Path, stage: str, result: dict) -> None:
    file = Path(folder) / {"pre": PRE_EVAL, "post": POST_EVAL}[stage]
    files = [file]
    record = result.get("identity") or {}
    if stage == "post" and record.get("release"):
        files.append(Path(folder).parent / "evaluations" / f"{record['release']}.json")
    for target in files:
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_name(f".{target.name}.{secrets.token_hex(4)}")
        tmp.write_text(json.dumps(result, sort_keys=True, indent=1) + "\n")
        os.replace(tmp, target)


def evaluation_key(prof, record: dict) -> str:
    """A rollback's latest check travels separately from its immutable release tarball."""
    return f"releases/{prof.client}/evaluations/{record.get('release') or record['hash']}.json"


def post_evaluation(prof) -> dict | None:
    from kge.adapters.objstore import open_store
    from kge.pipeline.settings import settings
    folder = prof.graph_path().parent
    if settings().STORE_URL:
        store = open_store(settings().STORE_URL)
        current = store.get(f"releases/{prof.client}/identity.json")
        record = json.loads(current) if current is not None else read(folder)
        if record:
            data = store.get(evaluation_key(prof, record))
            if data is not None:
                return json.loads(data)
    return evaluation(folder, "post")


def pre_evaluation(prof) -> dict | None:
    """Read candidate evidence from persisted work when the server has only the served graph."""
    from kge.adapters.objstore import open_store
    from kge.pipeline.settings import ROOT, settings
    folder = prof.graph_path().parent
    if settings().STORE_URL:
        try:
            name = (folder / PRE_EVAL).relative_to(ROOT).as_posix()
        except ValueError:
            return evaluation(folder, "pre")
        data = open_store(settings().STORE_URL).get(f"work/{prof.client}.tar.gz")
        if data is not None:
            with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as archive:
                try:
                    member = archive.getmember(name)
                except KeyError:
                    return None
                if member.isfile():
                    with archive.extractfile(member) as stream:
                        return json.load(stream)
        return None
    return evaluation(folder, "pre")


def identity(record: dict) -> dict:
    """The five identity fields, with absent legacy warehouse versions and release ids made explicit."""
    return {k: record.get(k, {} if k == "warehouses" else None)
            for k in ("hash", "graph", "chunks", "warehouses", "release")}


def path(folder: Path) -> Path:
    """The release record of the model hash whose folder this is."""
    return Path(folder) / RECORD


def paths(folder: Path) -> list[Path]:
    """Every file that records a release in folder: the record and the separate records before it."""
    return [path(folder), *(Path(folder) / name for name in LEGACY.values())]


class Unreadable(StoreError):
    """A release record that is there and cannot be read: refused, never taken for none."""


def _json(file: Path) -> dict | None:
    """The record at file; None where there is no file; Unreadable where there is one that is not a JSON object."""
    try:
        held = json.loads(file.read_text())
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as exc:
        raise Unreadable(f"{file.name} in {file.parent.name} cannot be read ({type(exc).__name__})") from None
    if not isinstance(held, dict):
        raise Unreadable(f"{file.name} in {file.parent.name} is not a release record")
    return held


def content_inputs(inputs: dict | None) -> dict | None:
    """Ignore deployment rosters carried by records written before access was separated."""
    return {key: value for key, value in inputs.items() if key != "roster"} if inputs is not None else None


def read(folder: Path) -> dict | None:
    """The release record in folder, including its id, inputs and review audit; composed from the separate records before it
    where there is no record; None where neither is. Unreadable where one is there and cannot be read."""
    held = _json(path(folder))
    if held is not None:
        return {**identity(held), "inputs": content_inputs(held.get("inputs")), "audit": held.get("audit", {})}
    legacy = {part: _json(Path(folder) / name) for part, name in LEGACY.items()}
    if not any(legacy.values()):
        return None
    return {"hash": Path(folder).name, **legacy, "warehouses": {}, "release": None, "inputs": None, "audit": {}}


def write(folder: Path, model_hash: str, graph: dict | None, chunks: dict | None,
          warehouses: dict[str, int] | None = None, release: str | None = None,
          inputs: dict | None = None, audit: dict | None = None) -> Path | None:
    """The release of model_hash recorded in its folder, the graph's entry and the chunks' together in one file, written
    whole and renamed over the old in one step; the separate records before it retired once it is. A warehouse-only
    record is kept too. A release id always keeps a record, including a local graph with its own vectors. Without
    any of these a record left from before is removed. The record, or None."""
    folder = Path(folder)
    if graph is None and chunks is None and not warehouses and release is None:
        for file in paths(folder):
            file.unlink(missing_ok=True)
        return None
    record = path(folder)
    record.parent.mkdir(parents=True, exist_ok=True)
    tmp = record.with_name(f".{RECORD}.{os.getpid()}.{secrets.token_hex(4)}")
    record_data = {"hash": model_hash, "graph": graph, "chunks": chunks, "warehouses": warehouses or {},
                   "release": release, "inputs": content_inputs(inputs), "audit": audit or {}}
    tmp.write_text(json.dumps(record_data, indent=1, sort_keys=True) + "\n")
    os.replace(tmp, record)
    for name in LEGACY.values():
        (folder / name).unlink(missing_ok=True)
    return record


def previous(folder: Path, identity: dict) -> dict:
    """Reconstruct and check a ledger identity before rollback; no pointer changes until every part is held.

    Generations of one compiled hash share a schema. Its kept pointer supplies that schema and dataset, while the
    ledger supplies the generation and chunks table. Local graphs remain in their hash's folder.
    """
    from kge.adapters.graphstore import graph_path, release_entry
    path = graph_path(folder)
    template = (read(folder) or {}).get("graph") or release_entry(path)
    graph = None
    if identity.get("graph"):
        if template is None:
            raise ValueError(f"previous graph generation {identity['graph']} has no held pointer")
        graph = {**template, "prefix": identity["graph"], "graph": f"{identity['graph']}graph"}
        from kge.adapters.graphstore import connect, open_db
        db, conn = None, None
        try:
            db = open_db(path, entry=graph)
            conn = connect(db)
            models = conn.execute("MATCH (m:GraphModel) RETURN m.id").get_all()
            if models != [[identity["hash"]]]:
                raise RuntimeError(f"it no longer holds model {identity['hash']}")
        except RuntimeError as exc:
            raise ValueError(f"previous graph generation {identity['graph']} is not held: {exc}") from None
        finally:
            if conn is not None:
                conn.close()
            if db is not None:
                db.close()
    elif not path.exists():
        raise ValueError(f"previous graph {path} is not held")
    chunks = None
    if identity.get("chunks"):
        dataset, table = identity["chunks"].rsplit(".", 1)
        _require_table(dataset, table, f"chunks {identity['chunks']}")
        chunks = {"dataset": dataset, "table": table}
    return {"hash": identity["hash"], "graph": graph, "chunks": chunks,
            "warehouses": identity.get("warehouses", {}), "release": identity.get("release")}


def _require_table(dataset: str, table: str, part: str) -> None:
    """A remote table must still exist, including when it holds no rows."""
    from kge.adapters.bigquery import BigQueryLoader
    from kge.adapters.graphstore.gql import quote
    loader = BigQueryLoader()
    project = loader.account()[1]
    try:
        loader.run(f"SELECT 1 FROM {quote(project + '.' + dataset + '.' + table)} LIMIT 0")
    except RuntimeError as exc:
        raise ValueError(f"previous {part} is not held: {exc}") from None
