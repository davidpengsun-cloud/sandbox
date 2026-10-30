"""The retrieval adapters: vector search over a client's chunks behind the retrieval seam (kge/ports.py Retrieval), in the
graph store (graph.py) or with BigQuery's VECTOR_SEARCH (bigquery.py). open_retrieval picks one from RETRIEVAL_BACKEND,
else WAREHOUSE_BACKEND, as the graph did; a caller asks for the seam, never for a backend's class."""
from __future__ import annotations

from typing import Any

from kge.pipeline.settings import settings
from kge.ports import Retrieval


def backend() -> str:
    """The retrieval backend: bigquery where RETRIEVAL_BACKEND names it, or, unset, WAREHOUSE_BACKEND does; else graph."""
    named = (settings().RETRIEVAL_BACKEND or "").strip().lower() or (settings().WAREHOUSE_BACKEND or "").strip().lower()
    return "bigquery" if named == "bigquery" else "graph"


def graph_holds_vectors() -> bool:
    """Whether the graph store holds the chunk vectors retrieval searches (the graph's retrieval), or retrieval keeps
    them itself and embeds each ready chunk as it syncs it (BigQuery's chunks table), so the graph store keeps none."""
    return backend() != "bigquery"


def open_retrieval(graph: Any) -> Retrieval:
    """The retrieval backend for one graph (kge/services/ask/engine/graphview.py Graph)."""
    if backend() == "bigquery":
        from kge.adapters.retrieval.bigquery import BigQueryRetrieval
        return BigQueryRetrieval(graph)
    from kge.adapters.retrieval.graph import GraphRetrieval
    return GraphRetrieval(graph)


def new_chunks_table(profile: Any, model_hash: str) -> tuple[str, str]:
    """Where one sync of model_hash's chunks goes, BigQuery's retrieval's: a fresh table's dataset and name, never one a
    served graph reads (kge/adapters/retrieval/bigquery.py)."""
    from kge.adapters.retrieval import bigquery
    return bigquery.new_chunks_table(profile, model_hash)


def named_chunks(profile: Any) -> str | None:
    """The chunks table the graph profile names already (dataset.table), or None: what a sync of it would write is there."""
    from kge.adapters.retrieval import bigquery
    return bigquery.named_chunks(profile)


def forget_chunks(folder: Any) -> None:
    """A graph's chunks pointer dropped as the graph is loaded again (kge/graph/load.py), whichever backend retrieval is."""
    from kge.adapters.retrieval import bigquery
    bigquery.forget_chunks(folder)


def name_chunks(profile: Any, dataset: str, table: str) -> Any:
    """The graph's pointer at the chunks table its sync wrote."""
    from kge.adapters.retrieval import bigquery
    return bigquery.name_chunks(profile, dataset, table)


def release_entry(folder: Any) -> dict | None:
    """What a release of the graph in folder records of its chunks in the release record (kge/adapters/released.py):
    the chunks pointer, whole; None where the sync has named none, as where retrieval is the graph's."""
    from kge.adapters.retrieval import bigquery
    return bigquery.release_entry(folder)


def entry_chunks(entry: Any) -> str | None:
    """The chunks table a release record's entry names (release_entry), as dataset.table; None for none."""
    from kge.adapters.retrieval import bigquery
    return bigquery.entry_chunks(entry)


def pinned(profile: Any, record: dict | None = None) -> str | None:
    """The chunks a graph opened on profile searches until it closes, read once as it opens: BigQuery's table the
    graph's pointer names then, or record's, the release record a server opened its graph from; None where retrieval
    is the graph's, whose chunks are the graph's own."""
    if backend() != "bigquery":
        return None
    from kge.adapters.retrieval import bigquery
    return bigquery.pinned(profile, record)


def prune(root: Any, held: Any = ()) -> list[str]:
    """Once a release is made, drop the chunks nothing reads: BigQuery's tables no graph under root names, nor held
    (the release ledger's, dataset.table); nothing where retrieval is the graph's, which keeps its vectors itself."""
    if backend() != "bigquery":
        return []
    from kge.adapters.retrieval import bigquery
    return bigquery.prune(root, held)


def discard_chunks(folder: Any) -> list[str]:
    """A candidate's chunks dropped once its check has ended: BigQuery's table the folder's pointer names, and the
    pointer; nothing where retrieval is the graph's, whose chunks are the graph's own."""
    if backend() != "bigquery":
        return []
    from kge.adapters.retrieval import bigquery
    return bigquery.discard_chunks(folder)
