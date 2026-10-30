"""The graph's side of the retrieval seam (kge/ports.py Retrieval): vector search over the chunk embeddings the graph
store holds, an exact search beside a scan that shows what filtering after an index loses."""
from __future__ import annotations

from typing import Any

from kge.pipeline import trace
from kge.adapters.model import DIM


class GraphRetrieval:
    """Retrieval in the graph store, for one graph (kge/services/ask/engine/graphview.py Graph): its q runs the reads and its
    embedder names the model."""

    def __init__(self, graph: Any):
        self.graph = graph

    def search(self, qv: list[float], units: list[str], purpose: str, k: int, shelf: str | None,
               tiers: list[str] | None) -> dict:
        """The exact search filters first and scores the survivors. Scores equal to six decimals tie by chunk id:
        FLOAT cosine accumulation differs by a few ulps across CPUs. Rank before LIMIT and after aggregation with
        that same precision, keeping the original score for callers. The scan filters after ranking, as an index does."""
        graph = self.graph
        params = {"q": qv, "k": k, "purpose": purpose, "units": units, "shelf": shelf or "", "model": graph.embedder.name,
                  **({"tiers": tiers} if tiers is not None else {})}
        tiered = " AND d.ai_ready AND d.tier IN $tiers" if tiers is not None else ""
        exact = graph.q(
            f"MATCH (d:Document)-[:HAS_CHUNK]->(c:Chunk) WHERE c.embedding IS NOT NULL AND c.embedding_model = $model AND $purpose IN c.purposes "
            f"AND (d.unit = '' OR d.unit IN $units) AND ($shelf = '' OR d.shelf = $shelf){tiered} "
            f"WITH d, c, array_cosine_similarity(c.embedding, CAST($q AS FLOAT[{DIM}])) AS score ORDER BY round(score, 6) DESC, c.id LIMIT $k "
            f"OPTIONAL MATCH (c)-[:MENTIONS]->(t:Term) "
            f"RETURN d.id AS doc, d.title AS title, d.shelf AS shelf, d.tier AS tier, d.ai_ready AS ai_ready, c.id AS chunk, "
            f"c.readiness AS readiness, score, collect(t.name) AS terms, left(c.text, 200) AS excerpt ORDER BY score DESC, chunk", params)
        scanned = graph.q(
            f"MATCH (d:Document)-[:HAS_CHUNK]->(c:Chunk) WHERE c.embedding IS NOT NULL AND c.embedding_model = $model "
            f"WITH d, c, array_cosine_similarity(c.embedding, CAST($q AS FLOAT[{DIM}])) AS score ORDER BY round(score, 6) DESC, c.id LIMIT $k "
            f"RETURN d.id AS doc, d.shelf AS shelf, d.tier AS tier, d.ai_ready AS ai_ready, c.id AS chunk, score, "
            f"($purpose IN c.purposes AND (d.unit = '' OR d.unit IN $units) AND ($shelf = '' OR d.shelf = $shelf){tiered}) AS eligible", params)
        exact.sort(key=lambda row: (-round(row["score"], 6), row["chunk"]))
        scanned.sort(key=lambda row: (-round(row["score"], 6), row["chunk"]))
        post = [r for r in scanned if r["eligible"]]
        trace.retrieved(exact, "vector" + (f":{shelf}" if shelf else ""))
        other = graph.q("MATCH (c:Chunk) WHERE c.embedding IS NOT NULL AND c.embedding_model <> $m RETURN count(*) AS n", {"m": graph.embedder.name})[0]["n"]
        return {"asked_for": k, "skipped_other_model": other, "exact": exact, "post_filtered": post, "index_returned": len(scanned), "post_filter_lost": len(scanned) - len(post),
                "embedding_model": graph.embedder.name}

    def chunk_decided(self, chunk: str, readiness: str, embedded_now: bool | None) -> None:
        """Nothing to do: the graph already holds the decision."""
