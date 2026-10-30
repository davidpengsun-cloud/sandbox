"""The graph store adapter: the GraphStore of kge/ports.py the demo runs on, chosen by GRAPH_STORE (kge/pipeline/settings.py)
each time it is asked for: ladybug (ladybug.py), the local stack's embedded store and the default, or bigquery
(bigquery.py), the pilot's, whose node and edge tables and property graph live in the client's graph dataset,
<prefix>_<client>_graph. store() gives it; the names below are its methods and constants, which the callers import from
here, never from a backend's module: STORE (the store's name), GraphLocked and NO_WHEEL, store_module, graph_path,
sidecars, open_db, connect, store_info, graph_q and loaded; and the markers beside a graph, LOADED and held (base.py). Each is read afresh on every call, so a process's store is what
GRAPH_STORE says when it asks; a backend's module is imported only when its store is first given, so the cloud path never
loads Ladybug's."""
from __future__ import annotations

import importlib
from typing import Any

from kge.adapters.graphstore.base import LOADED, GraphLocked, held
from kge.pipeline.settings import settings
from kge.ports import GraphStore

STORES = {"ladybug": ("kge.adapters.graphstore.ladybug", "LadybugStore"),
          "bigquery": ("kge.adapters.graphstore.bigquery", "BigQueryStore")}
DEFAULT = "ladybug"
_stores: dict[str, GraphStore] = {}


def store_name() -> str:
    """The graph store GRAPH_STORE names: ladybug (unset) or bigquery. Any other value is refused, naming the setting."""
    named = (settings().GRAPH_STORE or "").strip().lower() or DEFAULT
    if named not in STORES:
        raise ValueError(f"GRAPH_STORE={settings().GRAPH_STORE!r}: the graph store is ladybug (the local stack's, the default) "
                         "or bigquery (the pilot's)")
    return named


def store() -> GraphStore:
    """The graph store this process runs on now, GRAPH_STORE's; its module is imported when first given."""
    named = store_name()
    if named not in _stores:
        module, cls = STORES[named]
        _stores[named] = getattr(importlib.import_module(module), cls)()
    return _stores[named]


def store_module():
    return store().module()


def graph_path(root):
    return store().path(root)


def sidecars(path):
    return store().sidecars(path)


def open_db(path, pidfile=None, entry=None):
    return store().open(path, pidfile, entry)


def connect(db):
    return store().connect(db)


def store_info(conn=None) -> dict:
    return store().info(conn)


def graph_q(conn):
    return store().q(conn)


def batch(conn):
    """A batch of writes on conn (kge/adapters/graphstore/base.py Writer), the store's: use it as a context manager."""
    return store().batch(conn)


def release_entry(path):
    return store().release_entry(path)


def generation_of(entry):
    return store().generation_of(entry)


def served(path):
    return store().served(path)


def generation(path):
    return store().generation(path)


def loaded(path) -> bool:
    """Whether the graph at path is loaded on this process's store (its LOADED marker holds the store's entry), from the
    files alone."""
    return store().loaded(path)


def prune(root, held=()) -> list[str]:
    return store().prune(root, held)


def discard(path) -> list[str]:
    return store().discard(path)


def __getattr__(name: str) -> Any:
    """STORE, the store's name now, and NO_WHEEL, Ladybug's message for a platform it has no wheel for, read when asked."""
    if name == "STORE":
        return store().name
    if name == "NO_WHEEL":
        return importlib.import_module(STORES["ladybug"][0]).NO_WHEEL
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = ["DEFAULT", "LOADED", "GraphLocked", "STORES", "batch", "connect", "discard", "graph_path", "graph_q", "held", "loaded", "open_db",
           "sidecars", "store", "generation_of", "prune", "release_entry", "served", "store_info", "store_module", "store_name"]
