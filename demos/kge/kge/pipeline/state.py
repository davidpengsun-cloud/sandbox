"""Chunk state shared by the pipeline and the steward: live tags and readiness.

Readiness is derived, never authored. Both the pipeline and a steward decision
re-derive it here, from every live tag on the chunk (machine proposals not
retired, and human decisions), with the same rules the pipeline applies on
first sight. A chunk that stops being ready loses its vector; a chunk that
becomes ready is embedded then, and not before.

Where the graph store holds no vectors (kge.adapters.retrieval graph_holds_vectors:
retrieval keeps them, in BigQuery's chunks table), it keeps none: a ready chunk's embedding stays NULL, its
embedding_model names the embedder, and nothing is embedded here. The BigQuery
sync (kge/estate/load_bigquery.py sync_graph) embeds each ready chunk into the
chunks table, and a steward's decision that makes one ready embeds it there
(kge/adapters/retrieval/bigquery.py chunk_decided). A ready chunk counts as
embedded when its embedding_model is the embedder's.

The graph store is the graph store adapter's (kge/adapters/graphstore/), the
only place that opens a graph; graph_store() names it here for the callers that
read it from this module, as GRAPH_STORE says when they ask.
"""
from __future__ import annotations

import datetime as dt
from typing import Callable

from kge.adapters.graphstore import store
from kge.adapters.retrieval import graph_holds_vectors
from kge.pipeline.tagger import derive_readiness, live_proposals

Q = Callable[..., list[dict]]


def graph_store() -> str:
    """The graph store's name, ladybug on the local stack: kept for the frozen snapshot tests, which read it from here."""
    return store().name


READY_COUNTS = ("embedded", "to_embed_in_bigquery")


def ready_count() -> str:
    """The name the pipeline counts the chunks past the gate under, by what it did with them: embedded, where the graph
    store holds their vectors; to_embed_in_bigquery, where it wrote none and the BigQuery sync embeds them into its
    chunks table (graph_holds_vectors)."""
    embedded, to_embed_in_bigquery = READY_COUNTS
    return embedded if graph_holds_vectors() else to_embed_in_bigquery


def chunk_tags(q: Q, chunk: str) -> list[dict]:
    return q("MATCH (c:Chunk {id: $c})-[:TAGGED]->(t:Tag) OPTIONAL MATCH (s:Tag)-[:SUPERSEDES]->(t) "
             "RETURN t.id AS id, t.family AS family, t.value AS value, t.generator AS generator, t.confidence AS confidence, "
             "t.evidence AS evidence, count(s) > 0 AS superseded", {"c": chunk})


REASON = "derived from classification and privacy"


def rederive(q: Q, w, chunk: str, embedder, reason: str = REASON) -> dict:
    """Derive a chunk's readiness from its live tags again: what the graph holds, read with q, then derive's writes on
    the graph store's writer w (kge/adapters/graphstore GraphStore.batch), which the caller ends. When the gate closes
    the vector goes, so the chunk is unretrievable by construction; the readiness tag is one stable node per chunk,
    rewritten on every derivation."""
    rows = q("MATCH (d:Document)-[:HAS_CHUNK]->(c:Chunk {id: $c}) RETURN d.shelf AS shelf, c.text AS text, "
             "c.embedding IS NOT NULL AS embedded, c.embedding_model AS model", {"c": chunk})
    if not rows:
        return {"error": f"no chunk {chunk}"}
    tid = f"{chunk}/readiness"
    tagged = bool(q("MATCH (c:Chunk {id: $c})-[:TAGGED]->(t:Tag {id: $t}) RETURN t.id", {"c": chunk, "t": tid}))
    return derive(w, chunk, rows[0], chunk_tags(q, chunk), tagged, embedder, reason)


def derive(w, chunk: str, row: dict, tags: list[dict], tagged: bool, embedder, reason: str = REASON) -> dict:
    """A chunk's readiness from its tags (chunk_tags's rows), written on w: its readiness, purposes and vector, its
    readiness tag, and that tag's TAGGED edge unless it has one (tagged). row is the chunk as the graph holds it: its
    document's shelf, its text, whether it is embedded and by which model."""
    readiness, purposes = derive_readiness(live_proposals(tags), row["shelf"])
    embedded_now = False
    if not graph_holds_vectors():
        model = embedder.name if readiness != "embed-prohibited" else ""
        w.update("Chunk", chunk, {"readiness": readiness, "purposes": purposes, "embedding": None, "embedding_model": model})
        embedded_now = bool(model) and row["model"] != model
    elif readiness == "embed-prohibited":
        w.update("Chunk", chunk, {"readiness": readiness, "purposes": purposes, "embedding": None, "embedding_model": ""})
    elif row["embedded"] and row["model"] == embedder.name:
        w.update("Chunk", chunk, {"readiness": readiness, "purposes": purposes})
    else:
        w.update("Chunk", chunk, {"readiness": readiness, "purposes": purposes, "embedding": embedder.embed(row["text"]),
                                  "embedding_model": embedder.name})
        embedded_now = not row["embedded"]
    tid = f"{chunk}/readiness"
    w.upsert("Tag", tid, {"family": "ai_readiness", "value": readiness, "generator": "rule", "confidence": 1.0,
                          "model": "readiness-v2", "prompt_version": "", "evidence": reason,
                          "created_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")})
    if not tagged:
        w.create_edge("TAGGED", "Chunk", chunk, "Tag", tid)
    return {"readiness": readiness, "purposes": purposes, "embedded_now": embedded_now, "embedded": readiness != "embed-prohibited"}
