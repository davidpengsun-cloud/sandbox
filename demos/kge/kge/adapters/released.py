"""The release record: what a release of a model hash names, in one file in the hash's folder of the client's graph
store, release.json (RECORD): the hash, the graph store's entry (GraphStore.release_entry: BigQuery's graph pointer,
whole; None where the graph is its own file) and retrieval's (the chunks pointer, whole; None where the graph keeps its
vectors). It is written whole, under a temporary name renamed over the old, so whoever reads it reads one release's
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
import os
import secrets
from pathlib import Path

from kge.ports import StoreError

RECORD = "release.json"
PARTS = ("graph", "chunks")
LEGACY = {"graph": "graph.bigquery.released", "chunks": "chunks.bigquery.released"}


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


def read(folder: Path) -> dict | None:
    """The release record in folder, read once: {hash, graph, chunks}; composed from the separate records before it
    where there is no record; None where neither is. Unreadable where one is there and cannot be read."""
    held = _json(path(folder))
    if held is not None:
        return {"hash": held.get("hash"), **{part: held.get(part) for part in PARTS}}
    legacy = {part: _json(Path(folder) / name) for part, name in LEGACY.items()}
    if not any(legacy.values()):
        return None
    return {"hash": Path(folder).name, **legacy}


def write(folder: Path, model_hash: str, graph: dict | None, chunks: dict | None) -> Path | None:
    """The release of model_hash recorded in its folder, the graph's entry and the chunks' together in one file, written
    whole and renamed over the old in one step; the separate records before it retired once it is. With neither entry
    (a graph that is its own file, searching its own vectors) there is nothing to record, and a record left from
    before is removed. The record, or None."""
    folder = Path(folder)
    if graph is None and chunks is None:
        for file in paths(folder):
            file.unlink(missing_ok=True)
        return None
    record = path(folder)
    record.parent.mkdir(parents=True, exist_ok=True)
    tmp = record.with_name(f".{RECORD}.{os.getpid()}.{secrets.token_hex(4)}")
    tmp.write_text(json.dumps({"hash": model_hash, "graph": graph, "chunks": chunks}, indent=1, sort_keys=True) + "\n")
    os.replace(tmp, record)
    for name in LEGACY.values():
        (folder / name).unlink(missing_ok=True)
    return record
