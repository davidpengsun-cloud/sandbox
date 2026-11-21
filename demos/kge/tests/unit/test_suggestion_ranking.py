"""The same suggestion ranking and writes for stored and externally supplied vectors."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from kge.adapters.graphstore.base import Writer
from kge.adapters.model import HashEmbedder, VertexEmbedder
from kge.pipeline.overlay import text_sha256
from kge.pipeline.settings import suggested_mention_settings
from kge.pipeline.suggestions import cosine, generate, suggestion_id


class Kept(Writer):
    def __init__(self):
        super().__init__(None)
        self.ops = []

    def apply(self, op):
        self.ops.append(op)


class Graph:
    def __init__(self, rows, old=(), superseded=(), linked=()):
        self.rows, self.old, self.superseded, self.linked = rows, old, superseded, linked
        self.writer = Kept()

    def q(self, query):
        if query.startswith("MATCH (c:Chunk) RETURN"):
            return self.rows
        if "TAGGED" in query:
            return list(self.old)
        if "SUPERSEDES" in query:
            return [{"id": s} for s in self.superseded]
        if "MENTIONS" in query:
            return [{"chunk": c, "term": t} for c, t in self.linked]
        if "MATCH (t:Term)" in query:
            return [{"name": "Fiscal Year", "definition": "accounting period"},
                    {"name": "Revenue", "definition": "income"}]
        raise AssertionError(query)


def row(chunk, score=1.0, **overrides):
    return {"chunk": chunk, "text": f"text of {chunk}", "readiness": "citable", "embedding": [score, (1-score**2)**.5],
            "embedding_model": "fixture", **overrides}


@pytest.fixture
def embedder():
    sent = []
    return SimpleNamespace(name="fixture", mention_similarity=.7, sent=sent,
                           embed=lambda text: sent.append(text) or ([1., 0.] if text == "accounting period" else [0., 1.]))


@pytest.mark.parametrize("external", [False, True])
def test_ranking_cap_link_exclusion_and_privacy_gate_are_shared(monkeypatch, embedder, external):
    monkeypatch.setenv("SUGGESTED_MENTION_CAP", "2")
    graph = Graph([row("top"), row("second", .9), row("third", .8), row("linked"),
                   row("held", readiness="embed-prohibited"), row("pending", readiness=None),
                   row("missing", embedding=None), row("other", embedding_model="other")], linked=[("linked", "Fiscal Year")])
    assert generate(graph.q, lambda: graph.writer, embedder, graph.rows if external else None) == 2
    tags = [o for o in graph.writer.ops if o.kind == "upsert"]
    assert [o.key.split("/")[0] for o in tags] == ["top", "second"]
    assert [o.props["confidence"] for o in tags] == pytest.approx([1, .9])
    assert all(o.label != "MENTIONS" for o in graph.writer.ops)
    assert embedder.sent == ["accounting period", "income"]


def test_rejection_survives_rerun_but_changed_text_has_a_new_identity(embedder):
    chunk = row("c")
    old = {"id": suggestion_id("c", "Fiscal Year", chunk["text"]), "chunk": "c", "value": "Fiscal Year",
           "generator": "embedding", "evidence": text_sha256(chunk["text"])}
    graph = Graph([chunk], old=[old], superseded=[old["id"]])
    assert generate(graph.q, lambda: graph.writer, embedder) == 0
    assert graph.writer.ops == []
    chunk["text"] = "a changed fiscal period"
    assert generate(graph.q, lambda: graph.writer, embedder) == 1
    assert graph.writer.ops[0].key != old["id"]


def test_zero_cap_removes_pending_without_embedding_definitions(monkeypatch, embedder):
    monkeypatch.setenv("SUGGESTED_MENTION_CAP", "0")
    graph = Graph([row("c")], old=[{"id": "old", "chunk": "c", "value": "Fiscal Year", "generator": "embedding", "evidence": "old"}])
    assert generate(graph.q, lambda: graph.writer, embedder) == 0
    assert [(o.kind, o.key) for o in graph.writer.ops] == [("delete_node", "old")]
    assert not embedder.sent


def test_owner_defaults_and_single_override(monkeypatch):
    monkeypatch.delenv("SUGGESTED_MENTION_MIN_SIMILARITY", raising=False)
    monkeypatch.delenv("SUGGESTED_MENTION_CAP", raising=False)
    assert suggested_mention_settings(HashEmbedder.mention_similarity) == (.55, 20)
    assert suggested_mention_settings(VertexEmbedder.mention_similarity) == (.7, 20)
    monkeypatch.setenv("SUGGESTED_MENTION_MIN_SIMILARITY", "0.6")
    assert suggested_mention_settings(HashEmbedder.mention_similarity) == (.6, 20)
    assert suggested_mention_settings(VertexEmbedder.mention_similarity) == (.6, 20)


@pytest.mark.parametrize("value", ["-0.1", "1.1", "nan", "inf", "high"])
def test_invalid_threshold_is_refused(monkeypatch, value):
    monkeypatch.setenv("SUGGESTED_MENTION_MIN_SIMILARITY", value)
    with pytest.raises(ValueError, match="SUGGESTED_MENTION_MIN_SIMILARITY"):
        suggested_mention_settings(.7)


@pytest.mark.parametrize("value", ["-1", "1.5", "many"])
def test_invalid_cap_is_refused(monkeypatch, value):
    monkeypatch.setenv("SUGGESTED_MENTION_CAP", value)
    with pytest.raises(ValueError, match="SUGGESTED_MENTION_CAP"):
        suggested_mention_settings(.7)


@pytest.mark.parametrize("vector", [[], [0., 0.], [1.], [float("nan"), 0.], [float("inf"), 0.]])
def test_unscorable_vectors_are_not_suggestions(vector):
    assert cosine(vector, [1., 0.]) is None


def test_retained_human_provenance_on_a_removed_passage_gets_no_suggestion(embedder):
    graph = Graph([row("removed"), row("current")])
    assert generate(graph.q, lambda: graph.writer, embedder, active_chunks={"current"}) == 1
    assert graph.writer.ops[0].key.startswith("current/")


def test_external_generation_signature_tracks_inputs_but_not_read_order(embedder):
    from kge.pipeline.suggestions import configuration

    graph = Graph([row("c")])
    initial = configuration(embedder, graph.q)
    assert configuration(embedder, graph.q, {"active"}) != configuration(embedder, graph.q, {"active", "removed"})
    graph.rows[0]["text"] += " changed"
    assert configuration(embedder, graph.q) != initial
    graph.rows[0]["text"] = "text of c"
    graph.linked = [("c", "Fiscal Year")]
    linked = configuration(embedder, graph.q)
    assert linked != initial
    graph.linked = []
    graph.superseded = ["decision-b", "decision-a"]
    decided = configuration(embedder, graph.q)
    assert decided != initial
    graph.superseded.reverse()
    assert configuration(embedder, graph.q) == decided
    graph.superseded = []
    original = graph.q

    def revised(query):
        rows = original(query)
        return [{**r, "definition": r["definition"] + " revised"} for r in rows] if "MATCH (t:Term)" in query else rows

    assert configuration(embedder, revised) != initial
