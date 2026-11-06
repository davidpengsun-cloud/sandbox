"""One run's trace: what each stage took, what it read, what it asked a model, what it bound.

Every /answer and /ask response carries one as run_trace, and the A/B harness
writes one per question. It holds ids only, never chunk text or prompts:

  profile       the profile the process serves (kge/pipeline/profile.py): set by the
                process at deployment, never per request, and carried by the run
                config the answer ran on (RunConfig.profile)
  graph         the graph model hash (schema DDL and the curated layers, with
                the certification each document landed with, the landed
                release, and the query-semantics layer a context's query is
                assembled from, in a fixed order), the legacy model digest (the same
                before issue #39 added the release, which the A/B baseline
                recorded), the artifact (the model hash of the compiled pack
                the graph was loaded from, its GraphModel node; none for a
                graph loaded without one), the overlay hash (what the pipeline
                and the stewards add at run time: chunks, tags, mentions,
                decisions), and the store they were read from: engine, version
                and storage version
  llm           the model's identity with its config hash, or null when the run
                used no model; never the key
  config        the run config the answer ran on (kge/services/ask/engine/config.py)
  stages        milliseconds, model calls and tokens per stage. A stage runs
                from stage(name) until the next stage starts; a stage entered
                twice (one per unit, say) adds up
  llm_calls     each call: stage, model asked, model that served, tokens, ms; for
                the reference agent also its prompt version, and hashes of the
                prompt less its context block and of the context block alone
  agent         for the reference agent (kge/services/ask/engine/agent.py): its arm, whether rules,
                a model or the mock ran it, and its prompt versions; else null
  retrieved     each chunk read: doc, chunk, via, score, and the document's tier
                and ai_ready as upstream certification landed them
  citations     what the answer cites, by number, document and chunk
  bound         each figure and the document and chunk it is bound to
  number_check  whether model wording passed the number check, or null

The trace lives in a ContextVar, so two requests served at once never write
into each other's. Code records through the module functions (stage, retrieved,
llm_call, number_check), which do nothing when no trace is open. The ContextVar,
those functions and the JSON-lines helpers are kept in kge/pipeline/trace.py, so
the adapters below the engine record into the same trace, and are offered here
too.

The graph's layers are fixed lists (MODEL_LAYERS, LEGACY_LAYERS, OVERLAY_LAYERS).
A steward who accepts a candidate term adds to the curated layers (a term, its
definition, who may write it), so the model hash says the vocabulary has
changed. The columns layer holds the units' columns only: a curated source's
(meta-model 1.1) are no unit's, so the layer holds what the graph had before
it. A graph without one of the types issue #39 moved out of code holds none of
that layer. The overlay hashes chunk text and never returns it, and leaves out
vectors, which follow from the text and the embedding model, and timestamps,
which a rerun rewrites.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import time
import uuid
import weakref
from contextlib import contextmanager
from typing import Callable, Iterator

from kge.pipeline.trace import _current
from kge.pipeline.trace import agent as agent, append_jsonl as append_jsonl, current as current, llm_call as llm_call
from kge.pipeline.trace import number_check as number_check, prompting as prompting, read_jsonl as read_jsonl
from kge.pipeline.trace import retrieved as retrieved, stage as stage, use as use

STAGE_NAMES = ("understand", "resolve", "access", "plan", "gate", "execute", "retrieve", "bind", "judge", "render")


class Trace:
    def __init__(self, path: str, graph=None, clock: Callable[[], float] = time.perf_counter):
        self.run_id = uuid.uuid4().hex
        self.path, self.graph, self.clock = path, graph, clock
        self.profile: str | None = getattr(getattr(graph, "profile", None), "id", None)
        self.started_at = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
        self.t0 = clock()
        self.open: tuple[str, float] | None = None
        self.ms: dict[str, float] = {}
        self.config: dict | None = None
        self.llm = None
        self.calls: list[dict] = []
        self.rows: list[dict] = []
        self.numbers: dict | None = None
        self.agent: dict | None = None
        self.prompt: dict | None = None
        self.data_reads: list[dict] = []

    def stage(self, name: str) -> None:
        if name not in STAGE_NAMES:
            raise ValueError(f"stage must be one of {', '.join(STAGE_NAMES)}, not {name!r}")
        now = self.clock()
        self._close(now)
        self.open = (name, now)
        self.ms.setdefault(name, 0.0)

    def _close(self, now: float) -> None:
        if self.open:
            name, since = self.open
            self.ms[name] += (now - since) * 1000
            self.open = None

    def use(self, config, llm=None) -> None:
        """The run config the answer runs on; the last one set is the one it ran on, and its profile the one the process
        serves. A model, once named, stays: a call it made still happened."""
        self.config = {"arm": config.arm, "label": config.label, **config.stages()}
        self.profile = getattr(config, "profile", "") or self.profile
        if llm is not None:
            self.llm = llm

    def llm_call(self, model: str, served_model: str | None, tokens_in: int | None, tokens_out: int | None, ms: float,
                 ok: bool = True, error: str | None = None) -> None:
        self.calls.append({"stage": self.open[0] if self.open else None, "model": model, "served_model": served_model,
                           "tokens_in": tokens_in, "tokens_out": tokens_out, "ms": round(ms, 2), "ok": ok,
                           **({"error": error} if error else {}), **(self.prompt or {})})

    def agent_run(self, arm: str, mode: str, prompt_versions: dict | None) -> None:
        self.agent = {"arm": arm, "mode": mode, "prompt_versions": prompt_versions}

    def retrieved(self, rows: list[dict], via: str) -> None:
        for r in rows:
            ready = r.get("ai_ready")
            row = {"doc": r["doc"], "chunk": r.get("chunk"), "via": via, "tier": r.get("tier"), "ai_ready": None if ready is None else bool(ready),
                   "score": round(float(r["score"]), 4) if r.get("score") is not None else None}
            if row not in self.rows:
                self.rows.append(row)

    def number_check(self, passed: bool, stray_numbers: int = 0, bad_markers: int = 0, bad_documents: int | None = None) -> None:
        self.numbers = {"passed": passed, "stray_numbers": stray_numbers, "bad_markers": bad_markers,
                        **({"bad_documents": bad_documents} if bad_documents is not None else {})}

    def finish(self, answer: dict) -> dict:
        """Close the open stage and return the trace for this answer."""
        now = self.clock()
        self._close(now)
        stages = []
        for name, ms in self.ms.items():
            calls = [c for c in self.calls if c["stage"] == name]
            stages.append({"stage": name, "ms": round(ms, 2), "llm_calls": len(calls),
                           "tokens_in": total(c["tokens_in"] for c in calls), "tokens_out": total(c["tokens_out"] for c in calls)})
        return {"run_id": self.run_id, "path": self.path, "started_at": self.started_at, "profile": self.profile,
                "graph": fingerprint(self.graph) if self.graph is not None else None,
                "llm": identity(self.llm), "config": self.config, "total_ms": round((now - self.t0) * 1000, 2), "stages": stages,
                "tokens": {"in": total(c["tokens_in"] for c in self.calls), "out": total(c["tokens_out"] for c in self.calls)},
                "llm_calls": list(self.calls), "retrieved": list(self.rows), **cited(answer), "number_check": self.numbers,
                "agent": self.agent, **({"data_reads": list(self.data_reads)} if self.data_reads else {})}


def total(counts) -> int | None:
    """A sum of token counts; unknown if any count is."""
    counts = list(counts)
    return None if any(c is None for c in counts) else sum(counts)


def identity(llm) -> dict | None:
    if llm is None:
        return None
    if hasattr(llm, "identity"):
        return llm.identity()
    return {"name": getattr(llm, "name", type(llm).__name__), "config_hash": None}


def cited(answer: dict) -> dict:
    """What the answer cites and what each figure is bound to, by id."""
    if isinstance(answer.get("citations"), list):
        cites = [{"n": c.get("n"), "doc": c["doc"], "chunk": c.get("chunk"), "why": c.get("why")} for c in answer["citations"]]
    else:
        cites = [{"n": None, "doc": d, "chunk": None, "why": None} for d in answer.get("cite") or []]
    by_n = {c["n"]: c for c in cites if c["n"] is not None}
    bound = []
    for f in answer.get("figures") or []:
        c = by_n.get(f.get("cite"))
        bound.append({"figure": f.get("id"), "doc": c["doc"] if c else None, "chunk": c["chunk"] if c else None})
    return {"citations": cites, "bound": bound}


@contextmanager
def start(path: str, graph=None) -> Iterator[Trace]:
    t = Trace(path, graph)
    token = _current.set(t)
    try:
        yield t
    finally:
        _current.reset(token)


def run(path: str, call: Callable[[], dict], graph=None) -> dict:
    """call() under a fresh trace: its answer, with run_trace added."""
    with start(path, graph) as t:
        out = call()
        return {**out, "run_trace": t.finish(out)}


MODEL_LAYERS = [
    ("units", "MATCH (u:Unit) RETURN u.id, u.label, u.key_column ORDER BY u.id"),
    ("tables", "MATCH (u:Unit)-[:HAS_TABLE]->(t:Tbl) RETURN u.id, t.id, t.name ORDER BY t.id"),
    ("columns", "MATCH (:Unit)-[:HAS_TABLE]->(t:Tbl)-[:HAS_COLUMN]->(c:Col) RETURN t.id, c.id, c.data_type, c.description, c.is_key, "
                "c.certified, c.grain, c.currency, c.calendar, c.conversion ORDER BY c.id"),
    ("joins", "MATCH (a:Tbl)-[j:JOINS_TABLE]->(b:Tbl) RETURN a.id, b.id, j.from_column, j.to_column ORDER BY a.id, b.id, j.from_column, j.to_column"),
    ("terms", "MATCH (t:Term) RETURN t.name, t.domain, t.steward, t.definition ORDER BY t.name"),
    ("mappings", "MATCH (c:Col)-[m:MAPS_TO]->(t:Term) RETURN c.id, t.name, m.certified, m.certified_by, m.certified_on, m.generator "
                 "ORDER BY c.id, t.name"),
    ("no_join", "MATCH (a:Unit)-[n:NO_JOIN]->(b:Unit) RETURN a.id, b.id, n.asserted_by, n.asserted_on, n.note ORDER BY a.id, b.id"),
    ("principals", "MATCH (p:Principal) RETURN p.id, p.role ORDER BY p.id"),
    ("reads", "MATCH (p:Principal)-[:CAN_READ]->(u:Unit) RETURN p.id, u.id ORDER BY p.id, u.id"),
    ("writes", "MATCH (p:Principal)-[:CAN_WRITE]->(t:Term) RETURN p.id, t.name ORDER BY p.id, t.name"),
    ("processes", "MATCH (p:Principal)-[:MAY_PERFORM]->(x:Process) RETURN p.id, x.id ORDER BY p.id, x.id"),
    ("documents", "MATCH (d:Document) RETURN d.id, d.title, d.shelf, d.kind, d.unit, d.owner, d.version, d.published_at ORDER BY d.id"),
    ("certification", "MATCH (d:Document) RETURN d.id, d.ai_ready, d.tier, d.cert_evidence, d.cert_policy, d.cert_version, "
                      "d.cert_from, d.cert_to ORDER BY d.id"),
    ("defines", "MATCH (d:Document)-[:DEFINES]->(t:Term) RETURN d.id, t.name ORDER BY d.id, t.name"),
    ("supersedes", "MATCH (a:Document)-[:SUPERSEDED_BY]->(b:Document) RETURN a.id, b.id ORDER BY a.id, b.id"),
    ("release", "MATCH (r:Release) RETURN r.id, r.client, r.policy, r.as_of ORDER BY r.id"),
    ("measures", "MATCH (m:Measure)-[:MEASURE_OF]->(t:Term) RETURN m.id, m.expression, m.aggregation, m.grain, m.currency, m.conversion, "
                 "t.name ORDER BY m.id"),
    ("calendars", "MATCH (c:Calendar) RETURN c.id, c.year_start_month, c.quarter_label ORDER BY c.id"),
    ("aliases", "MATCH (a:Alias) RETURN a.phrase, a.kind, a.target, a.ordinal ORDER BY a.phrase"),
    ("periods", "MATCH (t:Tbl)-[p:PERIOD_COLUMN]->(c:Col) RETURN t.id, c.id, p.kind, p.calendar, p.rollup_table, p.rollup_column ORDER BY t.id"),
    ("policies", "MATCH (p:Policy)-[g:GOVERNS]->(c:Col) RETURN p.id, p.kind, p.params, p.version, c.id, g.ordinal ORDER BY p.id, c.id"),
    ("curated_sources", "MATCH (s:CuratedSource)-[:HAS_CURATED_TABLE]->(t:Tbl)-[:HAS_COLUMN]->(c:Col) RETURN s.id, t.id, c.id, c.data_type "
                        "ORDER BY c.id"),
    ("contexts", "MATCH (t:Term)-[:RESOLVES_TO]->(c:Context)-[:SPECIALIZES]->(m:Measure), (c)-[:USES_FACT]->(f:Tbl) "
                 "RETURN c.id, c.label, c.description, t.name, m.id, f.id ORDER BY c.id"),
    ("context_conditions", "MATCH (c:Context)-[r]->(x)-[:TARGETS]->(t:Tbl) RETURN c.id, label(r), r.ordinal, x.id, x.predicate, t.id "
                           "ORDER BY c.id, label(r), r.ordinal"),
    ("context_joins", "MATCH (c:Context)-[r:REQUIRES_JOIN]->(j:JoinRule) RETURN c.id, r.ordinal, r.required, j.id ORDER BY c.id, r.ordinal"),
    ("declared_joins", "MATCH (j:JoinRule)-[:JOIN_FROM]->(a:Tbl), (j)-[:JOIN_TO]->(b:Tbl) RETURN j.id, j.kind, j.condition, j.alias, j.through, "
                       "a.id, b.id ORDER BY j.id"),
    ("conflicts", "MATCH (a)-[:CONFLICTS_WITH]->(b) RETURN a.id, b.id ORDER BY a.id, b.id"),
    ("acronyms", "MATCH (c:Col)-[:SPELLED_WITH]->(a:Acronym) RETURN a.id, a.expansion, c.id ORDER BY a.id, c.id"),
    ("entities", "MATCH (t:Tbl)-[:REPRESENTS]->(e:Entity)-[i:IDENTIFIED_BY]->(c:Col) RETURN e.id, e.label, t.id, c.id, i.ordinal "
                 "ORDER BY e.id, i.ordinal"),
    ("relations", "MATCH (j:JoinRule)-[:IMPLEMENTS]->(r:Relation)-[:REL_FROM]->(a:Entity), (r)-[:REL_TO]->(b:Entity) "
                  "RETURN j.id, r.id, r.label, a.id, b.id ORDER BY j.id, r.id"),
    ("certified_joins", "MATCH (a:Unit)-[c:CERTIFIED_JOIN]->(b:Unit) RETURN a.id, b.id, c.asserted_by, c.asserted_on, c.note, c.declared_join "
                        "ORDER BY a.id, b.id"),
    ("not_joinable", "MATCH (c:Col)-[n:NOT_JOINABLE_TO]->(t:Tbl) RETURN c.id, t.id, n.asserted_by, n.asserted_on, n.note ORDER BY c.id, t.id"),
    ("lineage", "MATCH (a:Tbl)-[d:DERIVED_FROM]->(b:Tbl) RETURN a.id, b.id, d.note ORDER BY a.id, b.id"),
    ("golden", "MATCH (c:Context)-[:CERTIFIED_BY]->(g:Golden) RETURN c.id, g.id ORDER BY c.id, g.id"),
    ("matches", "MATCH (t:Tag) WHERE t.family = 'xref_match' AND t.generator <> 'human' RETURN t.id, t.value, t.confidence, t.model ORDER BY t.id"),
    ("vocabulary", "MATCH (v:Vocabulary) RETURN v.id, v.kind, v.params, v.ordinal ORDER BY v.id"),
]
LEGACY_LAYERS = ("units", "tables", "columns", "joins", "terms", "mappings", "no_join", "principals", "reads", "writes", "processes",
                 "documents", "certification", "defines", "supersedes")
OVERLAY_LAYERS = [
    ("chunks", "MATCH (d:Document)-[:HAS_CHUNK]->(c:Chunk) RETURN d.id, c.id, c.ordinal, c.text, c.readiness, c.purposes, "
               "c.embedding_model ORDER BY c.id"),
    ("tags", "MATCH (c:Chunk)-[:TAGGED]->(t:Tag) RETURN c.id, t.id, t.family, t.value, t.generator, t.confidence, t.model, "
             "t.prompt_version ORDER BY t.id, c.id"),
    ("decisions", "MATCH (a:Tag)-[:SUPERSEDES]->(b:Tag) RETURN a.id, b.id ORDER BY a.id, b.id"),
    ("mentions", "MATCH (c:Chunk)-[m:MENTIONS]->(t:Term) RETURN c.id, t.name, m.generator, m.confidence ORDER BY c.id, t.name"),
    ("proposals", "MATCH (t:Tag)-[p:PROPOSES]->(c:Col) RETURN t.id, c.id, p.term ORDER BY t.id, c.id"),
]
_layers: weakref.WeakKeyDictionary = weakref.WeakKeyDictionary()


def digest(parts: list[tuple[str, object]]) -> str:
    h = hashlib.sha256()
    for name, value in parts:
        h.update(name.encode() + b"\0" + json.dumps(value, sort_keys=True, default=str).encode() + b"\n")
    return h.hexdigest()


def held(conn, cypher: str) -> list:
    """A layer's rows; none for a graph that has no table the layer reads (one loaded before its type existed)."""
    try:
        return conn.execute(cypher).get_all()
    except RuntimeError as exc:
        if "Binder exception" in str(exc) or "does not exist" in str(exc):
            return []
        raise


def artifact(conn) -> str | None:
    """The model hash of the compiled artifact the graph was loaded from, as its GraphModel node names it; None for a
    graph loaded without one (kge/graph/legacy.py's)."""
    try:
        rows = conn.execute("MATCH (m:GraphModel) RETURN m.id ORDER BY m.id").get_all()
    except RuntimeError:
        return None
    return rows[0][0] if len(rows) == 1 else None


def fingerprint(g) -> dict:
    """{model, legacy_model, artifact, overlay, store}: sha256 hashes of what the
    graph holds, and the store it is held in (kge/adapters/graphstore/ladybug.py: LadybugStore.info). The
    model hash covers kge/graph/schema.py's DDL and the curated layers, the release
    among them; the legacy model digest the DDL and layers as they were before
    issue #39 (kge/graph/legacy.py's LEGACY_DDL, LEGACY_LAYERS), which the A/B
    baseline's model hash is; the artifact is the compiled pack's model hash,
    which names every file it was loaded from; the overlay hash what was added
    at run time. None depends on the graph store. The layers are
    read once per write, on a read connection of their own: Graph counts its
    writes, and the store lets no other process write while this one holds the
    graph."""
    import kge.graph.legacy
    import kge.graph.schema
    from kge.adapters.graphstore import store_info
    stamp = getattr(g, "writes", None)
    hit = _layers.get(g)
    if hit is None or stamp is None or hit[0] != stamp:
        with g.reader() as conn:
            rows = lambda layers: [(name, held(conn, cypher)) for name, cypher in layers]  # noqa: E731
            model = rows(MODEL_LAYERS)
            hit = (stamp, digest(model), digest(rows(OVERLAY_LAYERS)), digest([m for m in model if m[0] in LEGACY_LAYERS]), artifact(conn))
            store = store_info(conn)
        if stamp is not None:
            _layers[g] = hit
    else:
        store = store_info()
    return {"model": digest([("ddl", list(kge.graph.schema.DDL)), ("layers", hit[1])]),
            "legacy_model": digest([("ddl", list(kge.graph.legacy.LEGACY_DDL)), ("layers", hit[3])]), "artifact": hit[4], "overlay": hit[2],
            "store": store}
