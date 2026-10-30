"""The runtime overlay: what stewards decide, journaled in the client's graph store and replayed into every graph loaded for it.

    python3 -m kge.pipeline.overlay [--profile <id>] --reset     # make reset-overlay

Every load builds the graph from scratch (kge/graph/load.py): the compiled pack and
the landed release give the same graph each time. What a steward decides on
top of it is the runtime overlay. It is kept beside the graph, in the client's
graph store (the profile's, kge/pipeline/profile.py: Profile.overlay_dir), and
never in the pack or the landing (certification keeps a store of its own):

    <store>/<client>/overlay/decisions.jsonl       append-only, one decision a line
    <store>/<client>/overlay/orphans.json          what the last replay could not attach, and why
    <store>/<client>/overlay/archive/<utc>.jsonl   a journal make reset-overlay set aside

A record is keyed by stable ids: the client; the model hash of the artifact
the decision was made on; who decided, when, and the decision (accept, reject,
or reject_requested for a reject that waits for a second steward); the tag it
wrote, the human tag ({tag}/h<uuid>) or the reject request
({tag}/reject-request), with its properties; a snapshot of the machine tag it
decided; the chunk, with its document and the sha256 of its masked text as it
landed; and for an accepted candidate term, the term and the document that
defines it. A cross-reference match is decided on no chunk. What a record
leaves out comes back by itself: chunk text and vectors from the landing,
certification from the landing on every load, the access edges from the access
mirror, and machine tags, mentions and readiness from the pipeline, under the
same tag ids.

Graph.steward_decide (kge/services/ask/engine/graphview.py) appends its record once its graph writes
have committed, under a lock, and synced to disk before it returns. An append
that fails is an OverlayError, which the API answers with a 500 saying so.
kge/graph/load.py replays the accepted terms once the documents have loaded
(replay_terms): a term the artifact already has keeps the artifact's
definition, and only its DEFINES edge is attached. kge/pipeline/run.py replays every
decision at its end (replay), then re-derives the chunks it touched. Replay
merges, so running it again changes nothing, and it never writes the journal.
A record whose document, chunk, term or match is gone, or whose chunk's text is
not the text it was decided on, is an orphan: never attached, and reported in
orphans.json, on stderr and in pipeline_stats.json. A journal that holds
another client's record, or a line that cannot be read, is refused whole.

make reset-overlay moves the journal to archive/<utc>.jsonl, so the next make
graph and make pipeline start from a fresh queue. Names no client: the client
is the profile's.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import sys
import threading
from pathlib import Path
from typing import Callable

from kge.landing.zone import CLIENT_ID, CLIENT_ID_RULE

JOURNAL, ORPHANS, ARCHIVE = "decisions.jsonl", "orphans.json", "archive"
LOAD, PIPELINE = "kge/graph/load.py", "kge/pipeline/run.py"
REQUESTED = "reject_requested"
DECISIONS = ("accept", "reject", REQUESTED)
TAG_FIELDS = ("id", "family", "value", "generator", "confidence", "model", "prompt_version", "created_at", "evidence")
_lock = threading.Lock()

Q = Callable[..., list[dict]]


class OverlayError(RuntimeError):
    """A journal that cannot be written or read, or that is not this client's."""


def text_sha256(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def tag_props(row: dict) -> dict:
    """A tag's properties, as a record keeps them."""
    return {k: row.get(k) for k in TAG_FIELDS}


def record(client: str, model_hash: str | None, who: str, at: str, decision: str, tag: dict, machine: dict,
           chunk: dict | None = None, term: dict | None = None) -> dict:
    """One decision, as the journal keeps it (see the module's docstring)."""
    return {"client": client, "model_hash": model_hash, "who": who, "at": at, "decision": decision, "tag": tag_props(tag),
            "machine": tag_props(machine), "chunk": chunk, "term": term}


class Journal:
    """One client's steward-decision journal, in folder: the client's runtime overlay (Profile.overlay_dir). Appends
    are made one at a time, under a process-wide lock, since the API decides on several threads."""

    def __init__(self, folder: Path, client: str):
        if not isinstance(client, str) or not CLIENT_ID.fullmatch(client):
            raise OverlayError(f"journal client {client!r} is refused: {CLIENT_ID_RULE}")
        self.client = client
        self.dir = Path(folder)
        self.path = self.dir / JOURNAL

    def append(self, rec: dict) -> None:
        """rec appended as one line, under the lock, and synced to disk before this returns. OverlayError if it is not
        written, or is another client's."""
        if not isinstance(rec, dict) or rec.get("client") != self.client:
            who = rec.get("client") if isinstance(rec, dict) else None
            raise OverlayError(f"a record of {who!r} does not go in {self.client}'s journal, {self.path}")
        line = memoryview((json.dumps(rec, sort_keys=True) + "\n").encode())
        try:
            with _lock:
                self.dir.mkdir(parents=True, exist_ok=True)
                fd = os.open(self.path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
                try:
                    while line:
                        line = line[os.write(fd, line):]
                    os.fsync(fd)
                finally:
                    os.close(fd)
        except OSError as exc:
            raise OverlayError(f"the overlay journal {self.path} could not be written ({type(exc).__name__}: "
                               f"{exc.strerror or exc})") from exc

    def records(self) -> list[dict]:
        """Every record, in the order the decisions were made; none if there is no journal. OverlayError for a line that
        cannot be read or a record of another client's: the journal is refused whole."""
        if not self.path.exists():
            return []
        try:
            text = self.path.read_text()
        except (OSError, UnicodeDecodeError) as exc:
            raise OverlayError(f"the overlay journal {self.path} cannot be read ({type(exc).__name__})") from None
        out = []
        for n, line in enumerate(text.splitlines(), 1):
            if not line.strip():
                continue
            try:
                rec = json.loads(line)
            except ValueError:
                raise OverlayError(f"line {n} of the overlay journal {self.path} cannot be read, so nothing is replayed") from None
            who = rec.get("client") if isinstance(rec, dict) else None
            if who != self.client:
                raise OverlayError(f"the overlay journal {self.path} holds a record of {who!r} (line {n}), and this graph is "
                                   f"{self.client}'s: a client's journal holds its own decisions only, so nothing is replayed")
            out.append(rec)
        return out

    def decisions(self) -> list[dict]:
        """records(), each checked to be a decision replay can read: OverlayError naming the first that is not."""
        out = self.records()
        for n, rec in enumerate(out, 1):
            ok = (rec.get("decision") in DECISIONS and isinstance(rec.get("tag"), dict) and isinstance(rec.get("machine"), dict)
                  and isinstance(rec["tag"].get("id"), str) and isinstance(rec["machine"].get("id"), str)
                  and (rec.get("chunk") is None or (isinstance(rec["chunk"], dict) and {"id", "doc", "text_sha256"} <= set(rec["chunk"])))
                  and (rec.get("term") is None or (isinstance(rec["term"], dict) and rec.get("chunk") is not None
                                                   and {"name", "domain", "steward", "definition", "defined_by"} <= set(rec["term"]))))
            if not ok:
                raise OverlayError(f"record {n} of the overlay journal {self.path} is not a decision replay can read, so nothing "
                                   "is replayed")
        return out

    def orphans(self, stage: str, rows: list[dict], fresh: bool = False) -> None:
        """orphans.json, with stage's orphans in it, replaced whole; fresh (a new graph) drops what other stages said."""
        path = self.dir / ORPHANS
        held: dict = {}
        if not fresh and path.exists():
            try:
                held = json.loads(path.read_text())
            except (OSError, ValueError):
                held = {}
        held = {**(held if isinstance(held, dict) else {}), stage: rows}
        self.dir.mkdir(parents=True, exist_ok=True)
        tmp = self.dir / f".{ORPHANS}.{os.getpid()}"
        tmp.write_text(json.dumps(held, indent=2, sort_keys=True) + "\n")
        os.replace(tmp, path)

    def reset(self, now: dt.datetime | None = None) -> Path | None:
        """The journal moved to archive/<utc>.jsonl, and orphans.json, which described it, removed: the path it went to, or
        None if there was no journal."""
        if not self.path.exists():
            return None
        stamp = (now or dt.datetime.now(dt.timezone.utc)).strftime("%Y%m%dT%H%M%SZ")
        archive = self.dir / ARCHIVE
        archive.mkdir(parents=True, exist_ok=True)
        target, n = archive / f"{stamp}.jsonl", 0
        while target.exists():
            n += 1
            target = archive / f"{stamp}-{n}.jsonl"
        with _lock:
            os.replace(self.path, target)
        (self.dir / ORPHANS).unlink(missing_ok=True)
        return target


def orphan(rec: dict, why: str) -> dict:
    """An orphan as orphans.json, stderr and pipeline_stats.json report it."""
    return {"tag": rec["tag"]["id"], "decision": rec["decision"], "who": rec.get("who"), "at": rec.get("at"),
            "machine": rec["machine"]["id"], "chunk": (rec.get("chunk") or {}).get("id"), "why": why}


def warn(prefix: str, rows: list[dict]) -> None:
    for o in rows:
        print(f"{prefix}: WARNING: overlay orphan {o['tag']} ({o['decision']} of {o['machine']} by {o['who']}): {o['why']}; "
              "not attached", file=sys.stderr)


def replay_terms(q: Q, records: list[dict], release) -> tuple[int, list[dict]]:
    """The accepted candidate terms of records, into a graph whose documents have just loaded from release
    (kge/graph/load.py): a term the graph does not hold is created as the steward defined it, and the document that defines
    it gets its DEFINES edge; a term the artifact already holds keeps the artifact's definition. A record whose document
    or chunk did not land in release, or whose chunk landed with other text, is an orphan. How many were attached, and the
    orphans."""
    terms = [r for r in records if r.get("term")]
    if not terms:
        return 0, []
    docs = {d["id"] for d in release.documents()}
    chunks = {c["id"]: c for c in release.chunks()}
    attached, orphans = 0, []
    for r in terms:
        c = r["chunk"]
        landed = chunks.get(c["id"])
        if c["doc"] not in docs:
            why = f"its document {c['doc']} is gone: it did not land in release {release.id}"
        elif landed is None or landed.get("doc_id") != c["doc"]:
            why = f"its chunk {c['id']} is gone: {c['doc']} has no such chunk in release {release.id}"
        elif text_sha256(landed.get("text") or "") != c["text_sha256"]:
            why = f"the text of its chunk {c['id']} has changed since the decision (release {release.id})"
        else:
            why = None
        if why:
            orphans.append(orphan(r, why))
            continue
        t = r["term"]
        if not q("MATCH (t:Term) WHERE t.name = $n RETURN t.name AS n", {"n": t["name"]}):
            q("MERGE (t:Term {name: $n}) SET t.domain = $d, t.steward = $s, t.definition = $def",
              {"n": t["name"], "d": t["domain"], "s": t["steward"], "def": t["definition"]})
        if not q("MATCH (d:Document)-[:DEFINES]->(t:Term) WHERE d.id = $d AND t.name = $n RETURN d.id AS d",
                 {"d": t["defined_by"], "n": t["name"]}):
            q("MATCH (d:Document), (t:Term) WHERE d.id = $d AND t.name = $n CREATE (d)-[:DEFINES]->(t)", {"d": t["defined_by"], "n": t["name"]})
        attached += 1
    return attached, orphans


def gone(q: Q, rec: dict) -> str | None:
    """Why rec cannot be attached to the graph the pipeline has just filled, or None if it can."""
    c = rec.get("chunk")
    if c is not None:
        if not q("MATCH (d:Document) WHERE d.id = $d RETURN d.id AS d", {"d": c["doc"]}):
            return f"its document {c['doc']} is gone: it did not land in the release the graph was loaded from"
        rows = q("MATCH (d:Document)-[:HAS_CHUNK]->(c:Chunk) WHERE c.id = $c RETURN d.id AS doc, c.text AS text", {"c": c["id"]})
        if not rows or rows[0]["doc"] != c["doc"]:
            return f"its chunk {c['id']} is gone: {c['doc']} no longer has it"
        if text_sha256(rows[0]["text"] or "") != c["text_sha256"]:
            return f"the text of its chunk {c['id']} has changed since the decision"
    elif not q("MATCH (t:Tag) WHERE t.id = $id AND t.family = $f AND t.generator <> 'human' RETURN t.id AS id",
               {"id": rec["machine"]["id"], "f": rec["machine"]["family"]}):
        return f"{rec['machine']['id']} no longer lands for review: its source certified it, or dropped it, upstream"
    if rec.get("term") and not q("MATCH (t:Term) WHERE t.name = $n RETURN t.name AS n", {"n": rec["term"]["name"]}):
        return f"its term {rec['term']['name']} is not in the graph: the load did not replay it"
    return None


SET_TAG = ("t.family = $family, t.value = $value, t.generator = $generator, t.confidence = $confidence, t.model = $model, "
           "t.prompt_version = $prompt_version, t.created_at = $created_at, t.evidence = $evidence")
PROPS = ", ".join(f"t.{k} AS {k}" for k in TAG_FIELDS)


def has_tag(q: Q, tid: str) -> bool:
    return bool(q("MATCH (t:Tag) WHERE t.id = $id RETURN t.id AS id", {"id": tid}))


def tagged(q: Q, chunk: str, tid: str) -> bool:
    """TAGGED from chunk to the tag, created if it is not there: whether it was created."""
    if q("MATCH (c:Chunk)-[:TAGGED]->(t:Tag) WHERE c.id = $c AND t.id = $t RETURN t.id AS id", {"c": chunk, "t": tid}):
        return False
    q("MATCH (c:Chunk), (t:Tag) WHERE c.id = $c AND t.id = $t CREATE (c)-[:TAGGED]->(t)", {"c": chunk, "t": tid})
    return True


def restore(q: Q, machine: dict, chunk: str) -> bool:
    """The machine tag a decision is on, under its id, on its chunk: whether anything was written. The pipeline proposes
    it again under the same id, as a rule does, so it is usually there; a proposal it no longer makes (a candidate that is
    a term now) comes back from the snapshot. The same proposal under another id (a model tagger's, say) is that tag: it
    takes the decided tag's id, as a rerun on the old graph would have kept it."""
    if has_tag(q, machine["id"]):
        return tagged(q, chunk, machine["id"])
    same = q(f"MATCH (c:Chunk)-[:TAGGED]->(t:Tag) WHERE c.id = $c AND t.family = $f AND t.value = $v AND t.generator = $g "
             f"AND NOT EXISTS {{ MATCH (:Tag)-[:SUPERSEDES]->(t) }} RETURN {PROPS}",
             {"c": chunk, "f": machine["family"], "v": machine["value"], "g": machine["generator"]})
    props = {**machine, **{k: v for k, v in (same[0] if same else {}).items() if k != "id"}}
    for s in same:
        q("MATCH (t:Tag) WHERE t.id = $id DETACH DELETE t", {"id": s["id"]})
    q(f"MERGE (t:Tag {{id: $id}}) SET {SET_TAG}", {k: props.get(k) for k in TAG_FIELDS})
    tagged(q, chunk, machine["id"])
    return True


def replay(q: Q, records: list[dict], rederive: Callable[[str, str], object]) -> dict:
    """Every decision of records, into the graph the pipeline has just filled (kge/pipeline/run.py), in the order they were
    made: the machine tag each is on (restore), the tag it wrote, TAGGED from its chunk, and SUPERSEDES the machine tag
    unless it is a reject request. Then each chunk a replay wrote to is re-derived: rederive(chunk, reason). A decision
    the graph already holds whole is left as it is. {replayed, present, orphans}."""
    replayed = present = 0
    orphans: list[dict] = []
    touched: dict[str, str] = {}
    for r in records:
        why = gone(q, r)
        if why:
            orphans.append(orphan(r, why))
            continue
        tag, machine, c = r["tag"], r["machine"], r.get("chunk")
        chunk = c["id"] if c is not None else None
        wrote = restore(q, machine, chunk) if chunk is not None else False
        if not has_tag(q, tag["id"]):
            q(f"MERGE (t:Tag {{id: $id}}) SET {SET_TAG}", {k: tag.get(k) for k in TAG_FIELDS})
            wrote = True
        if chunk is not None:
            wrote = tagged(q, chunk, tag["id"]) or wrote
        if r["decision"] != REQUESTED and not q("MATCH (n:Tag)-[:SUPERSEDES]->(o:Tag) WHERE n.id = $n AND o.id = $o RETURN n.id AS n",
                                                {"n": tag["id"], "o": machine["id"]}):
            q("MATCH (n:Tag), (o:Tag) WHERE n.id = $n AND o.id = $o CREATE (n)-[:SUPERSEDES]->(o)", {"n": tag["id"], "o": machine["id"]})
            wrote = True
        if not wrote:
            present += 1
            continue
        replayed += 1
        if chunk is not None:
            touched.setdefault(chunk, f"re-derived after replaying {r['decision']} of {machine['id']} by {r.get('who')}")
    for chunk, reason in touched.items():
        rederive(chunk, reason)
    return {"replayed": replayed, "present": present, "orphans": orphans}


def main(argv: list[str] | None = None) -> int:
    """The command line. --reset sets the journal aside as it is, even one no replay can read, which is the first one
    to move."""
    from kge.pipeline.profile import ProfileError, resolve
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--profile", help="whose overlay: the profile's client (default: PROFILE, else the registry's default)")
    ap.add_argument("--reset", action="store_true", help="set the journal aside, in archive/<utc>.jsonl: the next make graph and "
                                                         "make pipeline start from a fresh queue")
    args = ap.parse_args(argv)
    try:
        p = resolve(args.profile)
        journal = Journal(p.overlay_dir(), p.client)
    except (ProfileError, OverlayError) as exc:
        print(f"overlay: {exc}", file=sys.stderr)
        return 2
    if not args.reset:
        try:
            recs = journal.records()
        except OverlayError as exc:
            print(f"overlay: {exc}", file=sys.stderr)
            return 2
        print(f"overlay: {len(recs)} decision{'' if len(recs) == 1 else 's'} journaled in {journal.path}")
        return 0
    try:
        n = len(journal.records())
    except OverlayError:
        n = None
    moved = journal.reset()
    if moved is None:
        print(f"overlay: no journal at {journal.path}: nothing to set aside; make graph and make pipeline start from a fresh queue")
        return 0
    held = "a journal no replay can read" if n is None else f"{n} decision{'' if n == 1 else 's'}"
    print(f"overlay: set aside {journal.path} ({held}) as {moved}: the next make graph and make pipeline start from a fresh "
          "queue")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
