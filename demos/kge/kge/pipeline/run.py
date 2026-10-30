"""Tag, gate, embed, write: the landed chunks.

    python3 -m kge.pipeline.run [--profile <id>] [--hash <model_hash>]

For every chunk of every document on either shelf, as AI certification landed
it (kge/landing/zone.py): propose tags, derive readiness, embed only what the gate
allows, and write chunks, tags, mentions and candidate terms into the graph.
The graph and the landing are the profile's (kge/pipeline/profile.py): its
client's graph store and landing zone, and the graph is the one the client's
current pointer names, or with --hash the graph of that artifact, a candidate
make load has just loaded (it runs this on it before marking it LOADED). The
chunks are those of the release the graph's Release node names, the one it was
loaded from, not whatever has landed since; a graph whose Release names another
client than the profile's is refused. Certification cut them on the
raw text, masked them and said what personal data it found in each, so this
reads no raw text and chunks nothing: what it found is the privacy family, with
the detectors' own on the masked text beside it, and a chunk that landed
without its findings fails closed, Restricted. The terms are the graph's, and so
are the client's functional mailboxes (meta-model 1.2), which an address the
detectors find here may be kept in clear at; a graph with none has none.

Re-running is safe: chunks keep their ids, a proposal made again reuses its
tag, and a machine tag a human decision points at is kept even when it is no
longer proposed. A human correction is a Tag at confidence 1.0 whose SUPERSEDES
edge points at the automatic tag it retires, and the automatic tag stays,
because provenance is the record. Readiness is re-derived from every live tag,
human decisions included, and lives on one stable tag per chunk: earlier runs'
copies go. A chunk a document no longer has is removed with its machine tags,
unless a human decided something on it. Machine tags are written by a tagging
identity and nothing else, so the run refuses a principal the graph does not
let perform tagging. chunk_text and strip_header stay here under their old
names; certification cuts the chunks now (kge/estate/certify.py).

At its end it replays the runtime overlay: every steward decision journaled in
the client's graph store (<store>/<client>/overlay/decisions.jsonl,
kge/pipeline/overlay.py), onto the chunks it has just written, under the ids the
decisions had, and re-derives the chunks they touch. So a decision outlives the
graph it was made on: a new load, a recompiled pack, the other graph store. A
decision already in the graph is left as it is. One whose document, chunk or
match is gone, or whose chunk's text changed, is an orphan, never attached:
named on stderr, in orphans.json beside the journal and in pipeline_stats.json.
A journal holding another client's records stops it before it writes anything.
What the run did, pipeline_stats.json, goes in the profile's run state
(Profile.run_dir).
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import sys

from kge.landing.zone import LandingError, Zone
from kge.pipeline import chunking
from kge.adapters.model import make_embedder
from kge.pipeline.overlay import PIPELINE, Journal, OverlayError, replay, warn
from kge.pipeline.profile import ProfileError, resolve
from kge.adapters.graphstore import GraphLocked, batch, connect, graph_q, open_db
from kge.pipeline.state import READY_COUNTS, derive, ready_count, rederive
from kge.adapters.model import ModelSettingError
from kge.pipeline.tagger import make_tagger, unlanded

chunk_text, strip_header = chunking.chunk_text, chunking.strip_header


def tag_id(cid: str, p) -> str:
    """Stable across runs: the same proposal on the same chunk is the same tag."""
    h = hashlib.sha1(f"{p.family}|{p.value}|{p.generator}|{p.model}".encode()).hexdigest()[:10]
    return f"{cid}/{p.family}/{h}"


def count(stats: dict, r: dict, by: int) -> None:
    """A chunk's derived readiness, added to the run's counts (by=+1) or taken off them (by=-1). A chunk past the gate
    counts under the name the run's counts were opened with (ready_count)."""
    stats["readiness"][r["readiness"]] = stats["readiness"].get(r["readiness"], 0) + by
    if not stats["readiness"][r["readiness"]]:
        del stats["readiness"][r["readiness"]]
    ready = next(name for name in READY_COUNTS if name in stats)
    stats[ready if r["embedded"] else "gated_before_embedding"] += by



class Held:
    """What the graph holds of the chunks when the run starts, read once each, so the run reads nothing it is writing:
    each chunk's vector and its embedder (chunks, in the graph's order), the documents' HAS_CHUNK edges (has_chunk),
    each document's shelf, each chunk's tags in the order the graph gives them (tags_of) and the tags a SUPERSEDES edge
    retires (superseded). A chunk's tags change only through its own writes, so what is read here is what reading one
    chunk at a time as the run reaches it would give."""

    def __init__(self, q):
        self.chunks = {r["id"]: {"embedded": r["embedded"], "model": r["model"]}
                       for r in q("MATCH (c:Chunk) RETURN c.id AS id, c.embedding IS NOT NULL AS embedded, c.embedding_model AS model")}
        self.has_chunk = {(r["doc"], r["chunk"]) for r in q("MATCH (d:Document)-[:HAS_CHUNK]->(c:Chunk) RETURN d.id AS doc, c.id AS chunk")}
        self.shelf = {r["id"]: r["shelf"] for r in q("MATCH (d:Document) RETURN d.id AS id, d.shelf AS shelf")}
        self.superseded = {r["id"] for r in q("MATCH (:Tag)-[:SUPERSEDES]->(t:Tag) RETURN DISTINCT t.id AS id")}
        self.tags: dict[str, list[dict]] = {}
        for r in q("MATCH (c:Chunk)-[:TAGGED]->(t:Tag) RETURN c.id AS chunk, t.id AS id, t.family AS family, t.value AS value, "
                   "t.generator AS generator, t.confidence AS confidence, t.evidence AS evidence"):
            self.tags.setdefault(r.pop("chunk"), []).append({**r, "superseded": r["id"] in self.superseded})

    def tags_of(self, chunk: str) -> list[dict]:
        return self.tags.get(chunk, [])


def mailboxes(q) -> frozenset[str]:
    """The client's functional mailboxes, as its compiled pack loaded them (meta-model 1.2's Vocabulary); none in a graph
    that holds no vocabulary, so every address the detectors find there is a person's."""
    try:
        rows = q("MATCH (v:Vocabulary) WHERE v.kind = 'mailbox' RETURN v.params AS params")
    except RuntimeError as exc:
        if "Binder exception" not in str(exc):
            raise
        return frozenset()
    return frozenset(json.loads(r["params"])["mailbox"] for r in rows)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--profile", help="the profile whose graph to fill (default: PROFILE, else the registry's default)")
    ap.add_argument("--hash", help="the graph of this artifact's model hash, a candidate (default: the one the client's current "
                                   "pointer names)")
    ap.add_argument("--as", dest="principal", default="pipeline_tagger", help="the tagging identity this run acts as")
    args = ap.parse_args()
    try:
        prof = resolve(args.profile)
        prof = prof.at(args.hash) if args.hash else prof.pin()
        graph = prof.graph_path()
    except ProfileError as exc:
        print(f"pipeline: {exc}", file=sys.stderr)
        return 2
    if not graph.exists():
        first = f"make load HASH={args.hash}" if args.hash else "make graph"
        print(f"pipeline: no graph {graph}: {first} first", file=sys.stderr)
        return 2
    try:
        conn = connect(open_db(graph, prof.run_dir() / "api.pid"))
    except GraphLocked as exc:
        print(f"pipeline: {exc}", file=sys.stderr)
        return 2
    q = graph_q(conn)
    if not q("MATCH (p:Principal {id: $p})-[:MAY_PERFORM]->(:Process {id: 'tagging'}) RETURN p.id", {"p": args.principal}):
        taggers = sorted(r["id"] for r in q("MATCH (p:Principal)-[:MAY_PERFORM]->(:Process {id: 'tagging'}) RETURN p.id AS id"))
        print(f"pipeline: {args.principal} is not a tagging identity; run as one of the graph's: {', '.join(taggers) or 'none'}",
              file=sys.stderr)
        return 2

    try:
        named = q("MATCH (r:Release) RETURN r.id AS id, r.client AS client")
    except RuntimeError:
        named = []
    if len(named) != 1:
        print(f"pipeline: {graph} names no landed release to read chunks from: make graph first", file=sys.stderr)
        return 2
    if named[0]["client"] != prof.client:
        print(f"pipeline: {graph} was loaded for {named[0]['client']}, and profile {prof.id} is {prof.client}'s: make graph loads "
              "the profile's own", file=sys.stderr)
        return 2
    try:
        release = Zone(prof.landing, prof.client).open(named[0]["id"])
    except LandingError as exc:
        print(f"pipeline: {exc}. The graph was loaded from it: make certify, then make graph", file=sys.stderr)
        return 2
    try:
        journal = Journal(prof.overlay_dir(), prof.client)
        decided = journal.decisions()
    except OverlayError as exc:
        print(f"pipeline: {exc}. Nothing was written.", file=sys.stderr)
        return 2
    terms = [r["name"] for r in q("MATCH (t:Term) RETURN t.name AS name ORDER BY t.name")]
    try:
        tagger = make_tagger(terms, mailboxes(q))
    except ModelSettingError as exc:
        print(f"pipeline: {exc}. Nothing was written.", file=sys.stderr)
        return 2
    embedder = make_embedder()
    docs = {d["id"]: d for d in release.documents()}
    now = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")

    stats = {"documents": len(docs), "chunks": 0, ready_count(): 0, "gated_before_embedding": 0, "tags": 0,
             "mentions": 0, "candidate_terms": 0, "readiness": {}, "tagger": type(tagger).__name__, "embedding_model": embedder.name,
             "run_as": args.principal, "tags_kept_for_provenance": 0, "human_tags_preserved": q("MATCH (t:Tag {generator: 'human'}) RETURN count(*) AS n")[0]["n"]}
    seen_chunks: set[str] = set()
    derived: dict[str, dict] = {}
    held = Held(q)

    with batch(conn) as w:
        for chunk in release.chunks():
            d, cid, i, text = docs[chunk["doc_id"]], chunk["id"], chunk["ordinal"], chunk["text"]
            seen_chunks.add(cid)
            proposals = tagger.propose(text, d["shelf"], d["kind"], findings=chunk.get("findings") or [])
            if chunk.get("findings") is None:
                proposals = unlanded(proposals)
            w.upsert("Chunk", cid, {"doc_id": d["id"], "ordinal": i, "text": text})
            if (d["id"], cid) not in held.has_chunk:
                w.create_edge("HAS_CHUNK", "Document", d["id"], "Chunk", cid)
                held.has_chunk.add((d["id"], cid))
            stats["chunks"] += 1

            tags_now = held.tags_of(cid)
            live = {t["id"]: dict(t) for t in tags_now}
            for t in tags_now:
                if t["family"] == "ai_readiness" and t["id"] != f"{cid}/readiness" and not t["superseded"]:
                    w.delete_node("Tag", t["id"])
                    live.pop(t["id"], None)
            existing = [t for t in tags_now if t["generator"] != "human" and t["family"] != "ai_readiness"]
            by_key = {(t["family"], t["value"], t["generator"]): t for t in existing}
            kept: set[str] = set()
            for p in proposals:
                row = {"f": p.family, "v": p.value, "g": p.generator, "c": p.confidence, "m": p.model, "pv": p.prompt_version, "at": now, "ev": p.evidence[:200]}
                old = by_key.get((p.family, p.value, p.generator))
                if old:
                    w.update("Tag", old["id"], {"confidence": row["c"], "model": row["m"], "prompt_version": row["pv"],
                                                "created_at": row["at"], "evidence": row["ev"]})
                    if old["id"] in live:
                        live[old["id"]].update(confidence=row["c"], evidence=row["ev"])
                    kept.add(old["id"])
                else:
                    tid = tag_id(cid, p)
                    w.upsert("Tag", tid, {"family": row["f"], "value": row["v"], "generator": row["g"], "confidence": row["c"],
                                          "model": row["m"], "prompt_version": row["pv"], "created_at": row["at"], "evidence": row["ev"]})
                    if tid not in live:
                        w.create_edge("TAGGED", "Chunk", cid, "Tag", tid)
                    live[tid] = {**live.get(tid, {"id": tid, "superseded": tid in held.superseded}), "family": row["f"],
                                 "value": row["v"], "generator": row["g"], "confidence": row["c"], "evidence": row["ev"]}
                    kept.add(tid)
                stats["tags"] += 1
                if p.family == "candidate_term":
                    stats["candidate_terms"] += 1
            for t in existing:
                if t["id"] in kept:
                    continue
                if t["superseded"]:
                    stats["tags_kept_for_provenance"] += 1
                else:
                    w.delete_node("Tag", t["id"])
                    live.pop(t["id"], None)

            w.delete_edges("MENTIONS", "Chunk", cid, "Term")
            for p in proposals:
                if p.family == "business_meaning" and p.value in terms:
                    w.create_edge("MENTIONS", "Chunk", cid, "Term", p.value, {"confidence": p.confidence, "generator": p.generator})
                    stats["mentions"] += 1

            state = held.chunks.get(cid, {"embedded": False, "model": None})
            r = derive(w, cid, {"shelf": held.shelf.get(d["id"]), "text": text, **state}, list(live.values()),
                       f"{cid}/readiness" in live, embedder)
            count(stats, r, +1)
            derived[cid] = r

        for gone_chunk in held.chunks:
            if gone_chunk in seen_chunks:
                continue
            if any(t["generator"] == "human" for t in held.tags_of(gone_chunk)):
                continue
            for t in held.tags_of(gone_chunk):
                w.delete_node("Tag", t["id"])
            w.delete_node("Chunk", gone_chunk)

    def again(cid: str, reason: str) -> None:
        with batch(conn) as w:
            r = rederive(q, w, cid, embedder, reason=reason)
        if cid in derived:
            count(stats, derived[cid], -1)
        count(stats, r, +1)
        derived[cid] = r
    stats["overlay"] = {"journal": str(journal.path), "records": len(decided), "replayed": 0, "present": 0, "orphans": []}
    if decided:
        stats["overlay"].update(replay(q, decided, again))
        warn("pipeline", stats["overlay"]["orphans"])
        journal.orphans(PIPELINE, stats["overlay"]["orphans"])

    if hasattr(tagger, "withheld"):
        stats["withheld_from_model"] = tagger.withheld
    prof.run_dir().mkdir(parents=True, exist_ok=True)
    (prof.run_dir() / "pipeline_stats.json").write_text(json.dumps(stats, indent=2))
    print(json.dumps(stats, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
