"""BigQuery's side of the retrieval seam (kge/ports.py Retrieval): vector search with BigQuery's VECTOR_SEARCH over the
chunks table of the graph it serves, and the steward's readiness decisions written back to it (issue #70).

Each sync of a graph's chunks (kge/estate/load_bigquery.py sync_graph) writes a table of its own,
chunks_<first 16 of the model hash>_<8 hex characters>, in the client's graph dataset (or, for the improvement job's
candidates, its candidates dataset: GRAPH_DATASET), and names it in chunks.bigquery, a pointer beside the graph in the
model hash's folder of the client's graph store, which a release records, with the graph's, in one release record
(kge/adapters/released.py) and carries. A process reads only the table its graph's
pointer names, so a candidate's sync never touches the chunks a served release searches, and the margin check reads
the candidate's own; an open graph searches the table it pinned as it opened (pinned), never one a later release
names; prune, after a release, drops each chunks table no pointer, nor the release ledger, names. The candidates are checked against the in-process graph's live readiness and purposes, so a steward
decision in this process takes effect at once even where BigQuery lags."""
from __future__ import annotations

import json
import logging
import os
import re
import uuid
from pathlib import Path
from typing import Any

from kge.adapters import released
from kge.adapters.bigquery import (DEFAULT_DATASET_PREFIX, bq_account, bq_query, dataset_name, graph_kind,
                                   served)
from kge.pipeline import trace
from kge.pipeline.settings import settings
from kge.pipeline.tagger import EMBEDDABLE

log = logging.getLogger(__name__)


POINTER = "chunks.bigquery"
TABLE = re.compile(r"chunks_[0-9a-f]{16}_[0-9a-f]{8}")


def project_id() -> str:
    named = (settings().BIGQUERY_PROJECT or settings().VERTEX_PROJECT or "").strip()
    return named or bq_account()[1]


def pointer_of(profile: Any) -> Path:
    """Where the chunks pointer of the graph profile names is read: for a profile that serves (Profile.serve), the
    hash's release record (kge/adapters/released.py) where it names chunks, so a server reads the released chunks while
    a load of the same hash syncs new ones; else the folder's chunks.bigquery."""
    folder = Path(profile.graph_path()).parent
    if getattr(profile, "serving", False) and chunks_in((released.read(folder) or {}).get("chunks")) is not None:
        return released.path(folder)
    return folder / POINTER


def chunks_in(pointer: Any) -> dict | None:
    """pointer, if it is a chunks pointer ({dataset, table}); else None."""
    if isinstance(pointer, dict) and isinstance(pointer.get("dataset"), str) and isinstance(pointer.get("table"), str):
        return pointer
    return None


def _read(path: Path) -> dict | None:
    try:
        return chunks_in(json.loads(Path(path).read_text()))
    except (OSError, ValueError):
        return None


def chunks_pointer(profile: Any, record: dict | None = None) -> dict | None:
    """The chunks pointer of the graph profile names: record's, a release record read already, where it is given; for
    a profile that serves, its hash's release record's where that names chunks; else the folder's; None for none."""
    if record is not None:
        return chunks_in(record.get("chunks"))
    folder = Path(profile.graph_path()).parent
    if getattr(profile, "serving", False):
        held = chunks_in((released.read(folder) or {}).get("chunks"))
        if held is not None:
            return held
    return _read(folder / POINTER)


def release_entry(folder: Path) -> dict | None:
    """What a release of the graph in folder records of its chunks, in the release record: the folder's chunks pointer,
    whole; None while its sync has written none."""
    return _read(Path(folder) / POINTER)


def entry_chunks(entry: Any) -> str | None:
    """The chunks table a release record's entry names, as dataset.table; None for none."""
    held = chunks_in(entry)
    return f"{held['dataset']}.{held['table']}" if held is not None else None


def chunks_table(profile: Any, record: dict | None = None) -> str:
    """The chunks table the graph profile names (or record, a release record read already) searches, as
    `project.dataset.table`; RuntimeError, saying how to make one, while its sync has written none."""
    held = chunks_pointer(profile, record)
    where = released.path(Path(profile.graph_path()).parent) if record is not None else pointer_of(profile)
    if held is None:
        raise RuntimeError(f"{where}: the graph names no BigQuery chunks table yet: the BigQuery sync writes one as the graph "
                           "is loaded (the job's onboard)")
    if not TABLE.fullmatch(held["table"]):
        raise RuntimeError(f"{where} names {held['table']!r}, which is not a chunks table")
    return f"`{project_id()}.{held['dataset']}.{held['table']}`"


def pinned(profile: Any, record: dict | None = None) -> str | None:
    """The chunks table a graph opened on profile searches until it closes (Graph.chunks), read once as it opens: for a
    server, from the release record it opened its graph from (record), so the two are one release's; None while its
    sync has written none."""
    try:
        return chunks_table(profile, record)
    except RuntimeError:
        return None


def searched(graph: Any) -> str:
    """The chunks table graph pinned as it opened; RuntimeError, saying how to make one, if its sync had written none."""
    if graph.chunks is None:
        raise RuntimeError(f"{pointer_of(graph.profile)}: the graph named no BigQuery chunks table when it opened: the BigQuery "
                           "sync writes one as the graph is loaded (the job's onboard), and a release or restart opens it")
    return graph.chunks


def new_chunks_table(profile: Any, model_hash: str) -> tuple[str, str]:
    """A fresh chunks table's dataset and name for one sync of model_hash's graph: never a table a pointer names.
    ValueError with no model hash: a sync writes one graph's chunks."""
    if not model_hash:
        raise ValueError("the sync writes one graph's chunks: name its model hash (--hash)")
    prefix = (settings().BIGQUERY_DATASET_PREFIX or "kge").strip() or DEFAULT_DATASET_PREFIX
    return dataset_name(prefix, profile.client, graph_kind()), f"chunks_{model_hash[:16]}_{uuid.uuid4().hex[:8]}"


def named_chunks(profile: Any) -> str | None:
    """The chunks table the graph profile names already, as dataset.table; None while its sync has written none (since
    its last load, which forgets it)."""
    return entry_chunks(chunks_pointer(profile))


def forget_chunks(folder: Path) -> None:
    """The chunks pointer in a model hash's folder removed: its graph is being loaded again, so its chunks are to be
    synced again."""
    (Path(folder) / POINTER).unlink(missing_ok=True)


def name_chunks(profile: Any, dataset: str, table: str) -> Path:
    """The graph's pointer at its chunks table, written whole once the table is loaded: the folder's own, never its
    released one."""
    path = Path(profile.graph_path()).parent / POINTER
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{POINTER}.{os.getpid()}")
    tmp.write_text(json.dumps({"dataset": dataset, "table": table}, sort_keys=True) + "\n")
    os.replace(tmp, path)
    return path


def prune(root: Path, held=()) -> list[str]:
    """Drop each chunks table that no pointer under root (each model hash's folder) names, nor its release record
    (kge/adapters/released.py, or the separate records before it), nor held (the tables, as
    dataset.table, of the releases a service may still serve: the release ledger's, kge/graph/release.py served, the one
    just released and the one before it), in each dataset a pointer names (the graph dataset, a candidates one),
    whichever sorts first. The ledger before release identities, root/chunks.bigquery.served, is kept from too, and
    retired once a prune has read it. The tables dropped."""
    legacy = Path(root) / f"{POINTER}.served"
    kept = {str(t).rsplit(".", 1)[-1] for t in held if t} | set(served(legacy, None))
    folders = sorted(f for f in Path(root).glob("*") if f.is_dir())
    pointers = [p for f in folders for p in (_read(f / POINTER), chunks_in((released.read(f) or {}).get("chunks")))
                if p is not None]
    kept |= {p["table"] for p in pointers}
    project = project_id()
    dropped = []
    for dataset in sorted({p["dataset"] for p in pointers}):
        listed = bq_query(f"SELECT table_name FROM `{project}.{dataset}`.INFORMATION_SCHEMA.TABLES "
                          "WHERE STARTS_WITH(table_name, 'chunks_')", project)
        stale = sorted(t for t in (r["table_name"] for r in listed) if TABLE.fullmatch(t) and t not in kept)
        if stale:
            bq_query("; ".join(f"DROP TABLE IF EXISTS `{project}.{dataset}.{t}`" for t in stale) + ";", project)
        dropped += stale
    legacy.unlink(missing_ok=True)
    return dropped


def discard_chunks(folder: Path) -> list[str]:
    """The chunks table the folder's pointer names dropped, and the pointer removed: a candidate's, once its check has
    ended; never the table the hash's release record names. The tables dropped."""
    pointer = _read(Path(folder) / POINTER)
    if pointer is None or not TABLE.fullmatch(pointer["table"]):
        return []
    if chunks_in((released.read(folder) or {}).get("chunks")) == pointer:
        return []
    project = project_id()
    bq_query(f"DROP TABLE IF EXISTS `{project}.{pointer['dataset']}.{pointer['table']}`;", project)
    forget_chunks(folder)
    return [pointer["table"]]


def sync_bigquery_chunk_readiness(
    profile: Any,
    chunk_id: str,
    readiness: str,
    embedded_now: bool | None = None,
    purposes: list[str] | None = None,
    embedding: list[float] | None = None,
    embedding_model: str | None = None,
    table: str | None = None,
) -> None:
    """Propagate steward readiness decision (readiness, purposes, embedding) to BigQuery chunks table: table, the one
    the deciding graph pinned as it opened, else the one profile's pointer names.

    Raises OverlayError if the BigQuery update fails so the caller never silently serves stale
    readiness state while reporting 200 OK.
    """
    from kge.pipeline.overlay import OverlayError

    try:
        table = table or chunks_table(profile)
        p = table.strip("`").split(".")[0]
        chunk_json = json.dumps(chunk_id, ensure_ascii=False)
        readiness_json = json.dumps(readiness, ensure_ascii=False)
        sets = [f"readiness = {readiness_json}"]
        if purposes is not None:
            sets.append(f"purposes = {json.dumps(list(purposes), ensure_ascii=False)}")
        if readiness == "embed-prohibited":
            sets.append("embedding = []")
            sets.append("embedding_model = ''")
        elif embedded_now and embedding is not None:
            emb_floats = [float(x) for x in embedding]
            sets.append(f"embedding = {json.dumps(emb_floats)}")
            sets.append(f"embedding_model = {json.dumps(embedding_model or '', ensure_ascii=False)}")
        sql = f"UPDATE {table} SET {', '.join(sets)} WHERE chunk = {chunk_json}"
        bq_query(sql, p)
    except Exception as exc:
        raise OverlayError(
            f"chunk {chunk_id} readiness ({readiness}) was decided in the graph, "
            f"but the BigQuery chunks table could not be updated: {exc}"
        ) from exc


class BigQueryRetrieval:
    """Retrieval on BigQuery, for one graph (kge/services/ask/engine/graphview.py Graph): its profile names the client's
    dataset, its embedder the model, and its store the chunks' live readiness."""

    def __init__(self, graph: Any):
        self.graph = graph

    def query(self, sql: str) -> list[dict]:
        """One query on BigQuery, in the project BIGQUERY_PROJECT names, else VERTEX_PROJECT's, else the credentials'."""
        return bq_query(sql, settings().BIGQUERY_PROJECT or settings().VERTEX_PROJECT)

    def search(self, qv: list[float], units: list[str], purpose: str, k: int, shelf: str | None,
               tiers: list[str] | None) -> dict:
        """VECTOR_SEARCH over the chunks this principal's units and this purpose allow, as the graph's own search
        answers."""
        graph = self.graph
        table = searched(graph)

        units_json = json.dumps(units, ensure_ascii=False)
        purpose_json = json.dumps(purpose, ensure_ascii=False)
        model = graph.embedder.name
        model_json = json.dumps(model, ensure_ascii=False)
        q_json = json.dumps(qv)
        k_int = int(k)
        dim_int = int(len(qv))
        shelf_json = json.dumps(shelf or "", ensure_ascii=False)
        shelf_clause = f" AND ({shelf_json} = '' OR shelf = {shelf_json})" if shelf else ""
        shelf_base = f" AND ({shelf_json} = '' OR base.shelf = {shelf_json})" if shelf else ""
        tiered_clause = f" AND ai_ready AND tier IN UNNEST({json.dumps(tiers, ensure_ascii=False)})" if tiers is not None else ""
        tiered_base = f" AND base.ai_ready AND base.tier IN UNNEST({json.dumps(tiers, ensure_ascii=False)})" if tiers is not None else ""

        exact_sql = f"""
SELECT
  base.doc AS doc,
  base.title AS title,
  base.shelf AS shelf,
  base.tier AS tier,
  base.ai_ready AS ai_ready,
  base.chunk AS chunk,
  base.readiness AS readiness,
  1.0 - distance AS score,
  base.terms AS terms,
  SUBSTR(base.text, 1, 200) AS excerpt
FROM VECTOR_SEARCH(
  (SELECT * FROM {table}
   WHERE ARRAY_LENGTH(embedding) = {dim_int} AND embedding_model = {model_json}
     AND readiness <> 'embed-prohibited'
     AND {purpose_json} IN UNNEST(purposes)
     AND (unit = '' OR unit IN UNNEST({units_json}))
     {shelf_clause}
     {tiered_clause}),
  'embedding',
  (SELECT {q_json} AS embedding),
  top_k => {k_int},
  distance_type => 'COSINE'
)
ORDER BY score DESC, chunk
LIMIT {k_int}
"""
        scanned_sql = f"""
SELECT
  base.doc AS doc,
  base.shelf AS shelf,
  base.tier AS tier,
  base.ai_ready AS ai_ready,
  base.chunk AS chunk,
  1.0 - distance AS score,
  (base.readiness <> 'embed-prohibited'
   AND {purpose_json} IN UNNEST(base.purposes)
   AND (base.unit = '' OR base.unit IN UNNEST({units_json}))
   {shelf_base}
   {tiered_base}) AS eligible
FROM VECTOR_SEARCH(
  (SELECT * FROM {table}
   WHERE ARRAY_LENGTH(embedding) = {dim_int} AND embedding_model = {model_json}),
  'embedding',
  (SELECT {q_json} AS embedding),
  top_k => {k_int},
  distance_type => 'COSINE'
)
ORDER BY score DESC, chunk
"""
        other_sql = f"""
SELECT count(*) AS vectors, COUNTIF(embedding_model != {model_json}) AS n,
       COUNTIF(embedding_model = {model_json} AND ARRAY_LENGTH(embedding) != {dim_int}) AS w
FROM {table}
WHERE ARRAY_LENGTH(embedding) > 0
"""
        exact_res = self.query(exact_sql)
        scanned_res = self.query(scanned_sql)
        other_res = self.query(other_sql)
        other = int(other_res[0]["n"]) if other_res and other_res[0].get("n") is not None else 0
        other_width = int(other_res[0]["w"]) if other_res and other_res[0].get("w") is not None else 0
        if other_width:
            log.warning("%d chunks in %s are %s vectors of another width than the question's %d, and are not "
                        "searched: EMBEDDING_DIM changed since the chunks were synced; run the BigQuery sync again "
                        "(make load, or the job's onboard)", other_width, table, model, dim_int)

        live_chunks: dict[str, dict] = {}
        cids = list({r["chunk"] for r in [*exact_res, *scanned_res] if r.get("chunk")})
        if cids:
            for row in graph.q(
                "MATCH (c:Chunk) WHERE c.id IN $ids RETURN c.id AS chunk, c.readiness AS readiness, c.purposes AS purposes",
                {"ids": cids},
            ):
                live_chunks[row["chunk"]] = row

        exact = []
        for r in exact_res:
            cid = r.get("chunk")
            readiness = r.get("readiness")
            if cid in live_chunks:
                live = live_chunks[cid]
                readiness = live.get("readiness")
                live_purposes = live.get("purposes") or []
                if readiness == "embed-prohibited" or purpose not in live_purposes:
                    continue
            elif readiness == "embed-prohibited":
                continue
            raw_terms = r.get("terms") or []
            terms = [t for t in raw_terms if t]
            exact.append({
                "doc": r.get("doc"),
                "title": r.get("title"),
                "shelf": r.get("shelf"),
                "tier": r.get("tier"),
                "ai_ready": r.get("ai_ready") in (True, "true", "True", 1),
                "chunk": cid,
                "readiness": readiness,
                "score": float(r.get("score") or 0.0),
                "terms": terms,
                "excerpt": r.get("excerpt") or "",
            })
        post = []
        for r in scanned_res:
            cid = r.get("chunk")
            eligible = r.get("eligible") in (True, "true", "True", 1)
            if eligible and cid in live_chunks:
                live = live_chunks[cid]
                if live.get("readiness") == "embed-prohibited" or purpose not in (live.get("purposes") or []):
                    eligible = False
            if eligible:
                post.append({
                    "doc": r.get("doc"),
                    "shelf": r.get("shelf"),
                    "tier": r.get("tier"),
                    "ai_ready": r.get("ai_ready") in (True, "true", "True", 1),
                    "chunk": cid,
                    "score": float(r.get("score") or 0.0),
                    "eligible": True,
                })
        trace.retrieved(exact, "vector" + (f":{shelf}" if shelf else ""))
        return {
            "asked_for": k_int,
            "skipped_other_model": other,
            "skipped_other_width": other_width,
            "exact": exact,
            "post_filtered": post,
            "index_returned": len(scanned_res),
            "post_filter_lost": len(scanned_res) - len(post),
            "embedding_model": model,
        }

    def chunk_decided(self, chunk: str, readiness: str, embedded_now: bool | None) -> None:
        """The chunk's readiness, purposes and embedding written to the chunks table: OverlayError if BigQuery refuses,
        so a decision is never reported done while the search serves the old one. The graph store keeps no vector where
        retrieval is BigQuery's (kge/pipeline/state.py rederive), so a chunk the decision made ready is embedded here,
        with the graph's embedder, and only then: a chunk whose readiness is not citable or retrieval-internal (EMBEDDABLE)
        is never sent to the embedder."""
        rows = self.graph.q(
            "MATCH (c:Chunk {id: $id}) RETURN c.purposes AS purposes, c.embedding AS embedding, c.embedding_model AS embedding_model, "
            "c.text AS text",
            {"id": chunk},
        )
        row = rows[0] if rows else {}
        embedding, model = row.get("embedding"), row.get("embedding_model")
        if embedded_now and embedding is None and readiness in EMBEDDABLE and row.get("text") is not None:
            embedding, model = self.graph.embedder.embed(row["text"]), self.graph.embedder.name
        sync_bigquery_chunk_readiness(
            self.graph.profile,
            chunk,
            readiness,
            embedded_now,
            purposes=row.get("purposes"),
            embedding=embedding,
            embedding_model=model,
            table=searched(self.graph),
        )
