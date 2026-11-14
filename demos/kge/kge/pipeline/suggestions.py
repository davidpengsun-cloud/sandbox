"""Embedding signals for steward review, never automatically created mentions.

Both the graph-vector pipeline and the external-vector sync call generate with
vectors already made after the privacy gate. Only glossary definitions are
embedded here. Decisions remain ordinary overlay tags: a superseded suggestion
with the same passage fingerprint suppresses that pair on every unchanged run.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import math

from kge.pipeline.overlay import text_sha256
from kge.pipeline.settings import suggested_mention_settings
from kge.pipeline.tagger import EMBEDDABLE

FAMILY = "suggested_mention"


def configuration(embedder, q=None, active_chunks: set[str] | None = None) -> dict:
    """The generation inputs, excluding pending outputs and timestamps.

    External vectors need a fresh generation when settings, glossary definitions,
    extracted links or steward decisions change, just as graph-vector runs do.
    """
    threshold, cap = suggested_mention_settings(getattr(embedder, "mention_similarity", .70))
    result = {"model": embedder.name, "dimensions": getattr(embedder, "dim", None), "threshold": threshold, "cap": cap}
    if active_chunks is not None:
        result["chunks"] = hashlib.sha256(json.dumps(sorted(active_chunks)).encode()).hexdigest()
    if q is not None:
        terms = sorted((r["name"], r["definition"] or "") for r in q("MATCH (t:Term) RETURN t.name AS name, t.definition AS definition"))
        links = sorted((r["chunk"], r["term"]) for r in q("MATCH (c:Chunk)-[:MENTIONS]->(t:Term) RETURN c.id AS chunk, t.name AS term"))
        decisions = sorted(r["id"] for r in q("MATCH (:Tag)-[:SUPERSEDES]->(t:Tag) WHERE t.family = 'suggested_mention' RETURN DISTINCT t.id AS id"))
        chunks = sorted((r["chunk"], text_sha256(r["text"] or ""), r["readiness"] or "", r["embedding_model"] or "")
                        for r in q("MATCH (c:Chunk) RETURN c.id AS chunk, c.text AS text, c.readiness AS readiness, "
                                   "c.embedding_model AS embedding_model")
                        if active_chunks is None or r["chunk"] in active_chunks)
        result["inputs"] = hashlib.sha256(json.dumps([terms, links, decisions, chunks], ensure_ascii=False).encode()).hexdigest()
    return result


def suggestion_id(chunk: str, term: str, text: str) -> str:
    """An unchanged passage and term keep one identity across embeddings and loads."""
    digest = hashlib.sha256(f"{term}|{text_sha256(text)}".encode()).hexdigest()
    return f"{chunk}/{FAMILY}/{digest}"


def cosine(left: list, right: list) -> float | None:
    """A finite cosine in one vector space; empty, zero or mismatched vectors do not qualify."""
    if not left or len(left) != len(right):
        return None
    norm = math.sqrt(sum(x * x for x in left) * sum(x * x for x in right))
    if not norm or not math.isfinite(norm):
        return None
    score = sum(x * y for x, y in zip(left, right)) / norm
    return max(-1.0, min(1.0, score)) if math.isfinite(score) else None


def generate(q, writer_factory, embedder, rows: list[dict] | None = None, active_chunks: set[str] | None = None) -> int:
    """Replace pending suggestions with the run's highest scoring unlinked pairs.

    rows, when supplied by external retrieval's sync, are its freshly embedded
    records: chunk, text, readiness, embedding and embedding_model. Otherwise
    the graph supplies those same fields. Read all inputs before opening a
    batch; every store receives identical node and edge operations.
    """
    threshold, cap = suggested_mention_settings(getattr(embedder, "mention_similarity", .70))
    if rows is None:
        rows = q("MATCH (c:Chunk) RETURN c.id AS chunk, c.text AS text, c.readiness AS readiness, "
                 "c.embedding AS embedding, c.embedding_model AS embedding_model")
    eligible = [r for r in rows if (active_chunks is None or r["chunk"] in active_chunks) and r.get("readiness") in EMBEDDABLE and r.get("embedding")
                and r.get("embedding_model") == embedder.name]
    old = q("MATCH (c:Chunk)-[:TAGGED]->(t:Tag) WHERE t.family = 'suggested_mention' "
            "RETURN c.id AS chunk, t.id AS id, t.value AS value, t.generator AS generator, t.evidence AS evidence")
    superseded = {r["id"] for r in q("MATCH (:Tag)-[:SUPERSEDES]->(t:Tag) RETURN DISTINCT t.id AS id")}
    decided = {(r["chunk"], r["value"], r["evidence"]) for r in old if r["id"] in superseded}
    linked = {(r["chunk"], r["term"]) for r in q("MATCH (c:Chunk)-[:MENTIONS]->(t:Term) RETURN c.id AS chunk, t.name AS term")}
    terms = q("MATCH (t:Term) RETURN t.name AS name, t.definition AS definition") if cap and eligible else []
    vectors = [(t["name"], embedder.embed(t["definition"])) for t in terms if (t.get("definition") or "").strip()]
    candidates = []
    for row in eligible:
        cid, text = row["chunk"], row.get("text") or ""
        fingerprint = text_sha256(text)
        for term, vector in vectors:
            if (cid, term) in linked or (cid, term, fingerprint) in decided:
                continue
            score = cosine(row["embedding"], vector)
            if score is not None and score >= threshold:
                candidates.append((score, cid, term, text, fingerprint))
    candidates.sort(key=lambda item: (-item[0], item[1], item[2]))
    chosen = candidates[:cap]
    now = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    with writer_factory() as w:
        for tag in old:
            if tag["generator"] == "embedding" and tag["id"] not in superseded:
                w.delete_node("Tag", tag["id"])
        for score, cid, term, text, fingerprint in chosen:
            tid = suggestion_id(cid, term, text)
            w.upsert("Tag", tid, {"family": FAMILY, "value": term, "generator": "embedding", "confidence": score,
                                  "model": embedder.name, "prompt_version": "definition-v1", "created_at": now,
                                  "evidence": fingerprint})
            w.create_edge("TAGGED", "Chunk", cid, "Tag", tid)
    return len(chosen)


def retain(q, writer_factory, embedder, active_chunks: set[str]) -> int:
    """Keep valid pending signals while external vectors await or already have a sync.

    Extraction owns its own tags. An unchanged external-vector pipeline rerun
    must not erase the suggestions its previous sync produced. It can still
    remove stale or newly linked pairs and apply tightened queue settings
    without sending any text to the embedder.
    """
    threshold, cap = suggested_mention_settings(getattr(embedder, "mention_similarity", .70))
    chunks = {r["chunk"]: r for r in q("MATCH (c:Chunk) RETURN c.id AS chunk, c.text AS text, c.readiness AS readiness, "
                                      "c.embedding_model AS embedding_model")}
    tags = q("MATCH (c:Chunk)-[:TAGGED]->(t:Tag) WHERE t.family = 'suggested_mention' "
             "RETURN c.id AS chunk, t.id AS id, t.value AS value, t.generator AS generator, t.evidence AS evidence, "
             "t.confidence AS confidence, t.model AS model")
    superseded = {r["id"] for r in q("MATCH (:Tag)-[:SUPERSEDES]->(t:Tag) RETURN DISTINCT t.id AS id")}
    linked = {(r["chunk"], r["term"]) for r in q("MATCH (c:Chunk)-[:MENTIONS]->(t:Term) RETURN c.id AS chunk, t.name AS term")}
    terms = {r["name"] for r in q("MATCH (t:Term) RETURN t.name AS name")}
    pending = [t for t in tags if t["generator"] == "embedding" and t["id"] not in superseded]
    valid = []
    for tag in pending:
        chunk = chunks.get(tag["chunk"], {})
        if (tag["chunk"] in active_chunks and chunk.get("readiness") in EMBEDDABLE
                and chunk.get("embedding_model") == embedder.name == tag["model"]
                and tag["value"] in terms and (tag["chunk"], tag["value"]) not in linked
                and tag["evidence"] == text_sha256(chunk.get("text") or "")
                and tag["confidence"] is not None and threshold <= tag["confidence"] <= 1):
            valid.append(tag)
    valid.sort(key=lambda tag: (-tag["confidence"], tag["chunk"], tag["value"]))
    kept = {tag["id"] for tag in valid[:cap]}
    with writer_factory() as w:
        for tag in pending:
            if tag["id"] not in kept:
                w.delete_node("Tag", tag["id"])
    return len(kept)
