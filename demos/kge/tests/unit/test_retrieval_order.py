"""Cosine ties are stable at FLOAT precision, without rounding reported scores."""
from types import SimpleNamespace

from kge.adapters.retrieval.graph import GraphRetrieval


def test_float_roundoff_cannot_reverse_tied_chunks():
    lower = {"chunk": "a", "score": 0.2552637457847595, "eligible": True}
    upper = {"chunk": "b", "score": 0.2552637755870819, "eligible": True}
    queries = []

    def query(text, params):
        queries.append(text)
        return [{"n": 0}] if "count(*)" in text else [upper.copy(), lower.copy()]

    graph = SimpleNamespace(q=query, embedder=SimpleNamespace(name="hash-v1"))
    result = GraphRetrieval(graph).search([0.0] * 128, ["unit"], "analysis", 2, None, None)
    assert [row["chunk"] for row in result["exact"]] == ["a", "b"]
    assert [row["chunk"] for row in result["post_filtered"]] == ["a", "b"]
    assert [row["score"] for row in result["exact"]] == [lower["score"], upper["score"]]
    assert all("ORDER BY round(score, 6) DESC, c.id LIMIT $k" in text for text in queries[:2])
