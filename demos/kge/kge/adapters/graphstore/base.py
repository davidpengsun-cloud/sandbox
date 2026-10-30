"""What every graph store shares, whatever backend holds the graph: GraphLocked, the refusal a store gives while another
process holds the graph it is asked to open; Writer, the batch of writes a caller hands a store (GraphStore.batch); and
the markers a model hash's folder holds (held), LOADED among them, which kge/graph/release.py writes and each store reads
to say whether a graph is loaded (GraphStore.loaded).

A Writer takes the writes a caller makes, each one operation on a node by its key or an edge by its ends' keys, in the
order made. Each store's writer gives them the meaning they have run one after another in that order: Ladybug's runs
each as it is made, as one Cypher statement; BigQuery's keeps them, and sends them as few statements as it can, one per
kind of write and table, in one transaction, when the batch ends or a write would change what one it keeps means
(flush). A caller reads nothing it has written in a batch before the batch ends or is flushed."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


class GraphLocked(RuntimeError):
    """The graph file is open in another process. The store allows one at a time."""


LOADED = "LOADED"


def held(folder: Path, name: str = LOADED) -> dict:
    """The LOADED marker in folder (or name's: GATE, SIGNOFF, RELEASED): an entry per store whose graph there is whole (or
    checked, signed off, released). Empty if there is none, or none that can be read: a marker that cannot be read marks
    nothing."""
    try:
        out = json.loads((Path(folder) / name).read_text())
    except (OSError, ValueError):
        return {}
    return {k: v for k, v in out.items() if isinstance(v, dict)} if isinstance(out, dict) else {}


PHASES = {"delete_edges": 0, "delete_node": 1, "create_node": 2, "upsert": 2, "rows": 2, "update": 3, "create_edge": 4}


@dataclass
class Op:
    """One write. kind: upsert (a node by its key, made if it is not there, its properties set), update (a node there
    by its key, its properties set), create_node (a node with its properties), delete_node (a node by its key, with
    its edges), create_edge (an edge of type label from the src node to the dst node, both there, with its properties),
    delete_edges (the edges of type label from the src node to any node of type dst), rows (an artifact's file at path
    into table label, as COPY reads it; an edge file's pair of ends src and dst)."""
    kind: str
    label: str
    key: Any = None
    props: dict = field(default_factory=dict)
    src: str | None = None
    dst: str | None = None
    dst_key: Any = None
    path: Path | None = None
    data: Any = None

    @property
    def phase(self) -> int:
        return PHASES[self.kind]


class Writer:
    """A batch of writes on one connection (base for each store's): the methods make the operations, apply runs or keeps
    each, flush sends what is kept. As a context manager it flushes when the batch ends without an error."""

    def __init__(self, conn: Any):
        self.conn = conn

    def apply(self, op: Op) -> None:
        raise NotImplementedError

    def flush(self) -> None:
        return None

    def upsert(self, label: str, key: Any, props: dict) -> None:
        self.apply(Op("upsert", label, key, dict(props)))

    def update(self, label: str, key: Any, props: dict) -> None:
        self.apply(Op("update", label, key, dict(props)))

    def create_node(self, label: str, props: dict) -> None:
        self.apply(Op("create_node", label, None, dict(props)))

    def delete_node(self, label: str, key: Any) -> None:
        self.apply(Op("delete_node", label, key))

    def create_edge(self, type_: str, src: str, src_key: Any, dst: str, dst_key: Any, props: dict | None = None) -> None:
        self.apply(Op("create_edge", type_, src_key, dict(props or {}), src, dst, dst_key))

    def delete_edges(self, type_: str, src: str, src_key: Any, dst: str) -> None:
        self.apply(Op("delete_edges", type_, src_key, {}, src, dst))

    def load_file(self, table: str, path: Path, ends: tuple[str, str] | None = None) -> None:
        """One file of an artifact into table, with COPY's NULL rule; an edge file of a rel group names its pair of ends."""
        self.apply(Op("rows", table, None, {}, ends[0] if ends else None, ends[1] if ends else None, None, Path(path)))

    def __enter__(self):
        return self

    def __exit__(self, kind, exc, tb) -> None:
        if kind is None:
            self.flush()
