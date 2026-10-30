"""Chunk vectors that live in BigQuery (#94 item 3): EMBEDDING_DIM, and no vector written to the graph store.

    .venv/bin/python -m pytest tests/unit/test_bigquery_vectors.py -q

Where retrieval is BigQuery's (RETRIEVAL_BACKEND=bigquery, or WAREHOUSE_BACKEND=bigquery with RETRIEVAL_BACKEND unset),
the graph store's chunks keep no vector: rederive names the embedder on a ready chunk and embeds nothing, the BigQuery
sync embeds each ready chunk into the chunks table, and a steward's decision that makes a chunk ready embeds it there.
The Vertex AI embedder is EMBEDDING_DIM wide, 768 unset, on that path alone; the graph store's path, and the hash and
local embedders, keep DIM. Nothing here reaches BigQuery or Vertex AI: the loader, the query and the embedder are
stand-ins.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from kge.adapters.model import DIM, HashEmbedder, VertexEmbedder, make_embedder
from kge.adapters.retrieval.bigquery import BigQueryRetrieval
from kge.estate import load_bigquery
from kge.pipeline.state import rederive

PROJECT = "pilot-project"
INTERNAL = {"id": "C/cls", "family": "classification", "value": "Internal", "generator": "rule", "confidence": 1.0,
            "evidence": "", "superseded": False}
CARD = {"id": "C/pan", "family": "privacy", "value": "payment_card", "generator": "rule", "confidence": 1.0,
        "evidence": "", "superseded": False}


@pytest.fixture(autouse=True)
def plain(monkeypatch):
    for name in ("RETRIEVAL_BACKEND", "WAREHOUSE_BACKEND", "EMBEDDING_DIM", "EMBEDDING_PROVIDER", "EMBEDDING_MODEL",
                 "VERTEX_EMBEDDING_MODEL"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("VERTEX_PROJECT", PROJECT)
    monkeypatch.setenv("BIGQUERY_PROJECT", PROJECT)


class Recorder:
    """An embedder that keeps every text it is sent."""

    def __init__(self, dim: int = 768, name: str = "gemini-embedding-001"):
        self.dim, self.name, self.sent = dim, name, []

    def embed(self, text: str) -> list[float]:
        self.sent.append(text)
        return [1.0] + [0.0] * (self.dim - 1)


def test_the_vertex_embedder_is_768_wide_where_the_vectors_live_in_bigquery(monkeypatch):
    monkeypatch.setenv("EMBEDDING_PROVIDER", "vertex")
    assert make_embedder().dim == DIM
    monkeypatch.setenv("RETRIEVAL_BACKEND", "bigquery")
    assert make_embedder().dim == 768 and VertexEmbedder().dim == 768
    monkeypatch.delenv("RETRIEVAL_BACKEND")
    monkeypatch.setenv("WAREHOUSE_BACKEND", "bigquery")
    assert make_embedder().dim == 768
    monkeypatch.setenv("RETRIEVAL_BACKEND", "duckdb")
    assert make_embedder().dim == DIM
    monkeypatch.setenv("RETRIEVAL_BACKEND", "bigquery")
    monkeypatch.setenv("EMBEDDING_DIM", "1536")
    assert make_embedder().dim == 1536


def test_the_vertex_embedder_asks_vertex_ai_for_its_width(monkeypatch):
    monkeypatch.setenv("EMBEDDING_PROVIDER", "vertex")
    monkeypatch.setenv("RETRIEVAL_BACKEND", "bigquery")
    emb = make_embedder()
    sent = []
    monkeypatch.setattr(emb, "token", lambda: ("a-token", PROJECT))
    monkeypatch.setattr(emb, "_post", lambda url, headers, body: sent.append(body) or {
        "predictions": [{"embeddings": {"values": [0.5] * 768}}]})
    assert len(emb.embed("a chunk")) == 768
    assert sent[0]["parameters"] == {"outputDimensionality": 768}


def test_embedding_dim_is_refused_where_the_graph_store_holds_the_vectors_or_when_not_a_width(monkeypatch):
    monkeypatch.setenv("EMBEDDING_PROVIDER", "vertex")
    monkeypatch.setenv("EMBEDDING_DIM", "768")
    with pytest.raises(ValueError, match="EMBEDDING_DIM=768 needs RETRIEVAL_BACKEND=bigquery"):
        make_embedder()
    monkeypatch.setenv("EMBEDDING_DIM", str(DIM))
    assert make_embedder().dim == DIM
    monkeypatch.setenv("RETRIEVAL_BACKEND", "bigquery")
    for raw in ("0", "wide", "-1"):
        monkeypatch.setenv("EMBEDDING_DIM", raw)
        with pytest.raises(ValueError, match="EMBEDDING_DIM must be a whole number above 0"):
            make_embedder()


def test_the_hash_and_local_embedders_keep_the_graph_s_width_on_either_backend(monkeypatch):
    monkeypatch.setenv("RETRIEVAL_BACKEND", "bigquery")
    monkeypatch.setenv("EMBEDDING_DIM", "768")
    emb = make_embedder()
    assert isinstance(emb, HashEmbedder) and emb.dim == DIM and len(emb.embed("a b")) == DIM


class Chunk:
    """One chunk in a stand-in graph store, answering the queries rederive sends; its writes, made on w, kept."""

    def __init__(self, tags: list[dict], model: str = "", embedded: bool = False):
        from kge.adapters.graphstore.base import Writer
        self.tags, self.model, self.embedded, self.writes = tags, model, embedded, []
        chunk = self

        class Kept(Writer):
            def apply(self, op):
                if op.kind == "update" and op.label == "Chunk":
                    chunk.writes.append(op.props)
        self.w = Kept(None)

    def q(self, cypher: str, params: dict | None = None) -> list[dict]:
        if "RETURN d.shelf" in cypher:
            return [{"shelf": "certified", "text": "the chunk's text", "embedded": self.embedded, "model": self.model}]
        if "OPTIONAL MATCH (s:Tag)" in cypher:
            return self.tags
        return []


def test_rederive_writes_no_vector_to_the_graph_store_where_the_vectors_live_in_bigquery(monkeypatch):
    monkeypatch.setenv("RETRIEVAL_BACKEND", "bigquery")
    embedder = Recorder()
    ready = Chunk([INTERNAL])
    r = rederive(ready.q, ready.w, "C", embedder)
    assert r == {"readiness": "citable", "purposes": ["analysis", "assistant", "supervision"], "embedded_now": True,
                 "embedded": True}
    (props,) = ready.writes
    assert props["embedding"] is None and props["embedding_model"] == embedder.name
    assert embedder.sent == []
    again = Chunk([INTERNAL], model=embedder.name)
    assert rederive(again.q, again.w, "C", embedder)["embedded_now"] is False and again.writes[0]["embedding"] is None
    gated = Chunk([INTERNAL, CARD], model=embedder.name)
    r = rederive(gated.q, gated.w, "C", embedder)
    assert (r["readiness"], r["embedded"], r["embedded_now"]) == ("embed-prohibited", False, False)
    assert gated.writes[0]["embedding"] is None and gated.writes[0]["embedding_model"] == "" and embedder.sent == []


def test_rederive_still_embeds_into_the_graph_store_on_its_own_path():
    embedder = Recorder(dim=DIM, name="hash-v1")
    ready = Chunk([INTERNAL])
    assert rederive(ready.q, ready.w, "C", embedder)["embedded_now"] is True
    (props,) = ready.writes
    assert len(props["embedding"]) == DIM and embedder.sent == ["the chunk's text"]


CHUNKS = "chunks_abcdefabcdefabcd_00000000"


@pytest.fixture(autouse=True)
def named_chunks(monkeypatch):
    """The chunks table each graph's pointer names, and a sync's own fresh table: the client's graph dataset's CHUNKS."""
    monkeypatch.setattr("kge.adapters.retrieval.bigquery.chunks_table", lambda profile, record=None: f"`{PROJECT}.kge_meridian_graph.{CHUNKS}`")
    monkeypatch.setattr(load_bigquery, "new_chunks_table", lambda prof, model_hash: ("kge_meridian_graph", CHUNKS))
    monkeypatch.setattr(load_bigquery, "name_chunks", lambda prof, dataset, table: None)


class Graph_:
    profile = "meridian"
    chunks = f"`{PROJECT}.kge_meridian_graph.{CHUNKS}`"

    def __init__(self, embedder):
        self.embedder = embedder

    def q(self, cypher, params=None):
        return [{"purposes": ["analysis"], "embedding": None, "embedding_model": self.embedder.name, "text": "now ready"}]


def test_a_chunk_a_steward_makes_ready_is_embedded_into_bigquery_s_chunks_table(monkeypatch):
    monkeypatch.setenv("RETRIEVAL_BACKEND", "bigquery")
    sent: list[str] = []
    monkeypatch.setattr("kge.adapters.retrieval.bigquery.bq_query", lambda sql, project=None: sent.append(sql) or [])
    embedder = Recorder()
    BigQueryRetrieval(Graph_(embedder)).chunk_decided("CHUNK-7", "citable", True)
    assert embedder.sent == ["now ready"]
    assert "embedding = [1.0, 0.0" in sent[0] and 'embedding_model = "gemini-embedding-001"' in sent[0]
    BigQueryRetrieval(Graph_(embedder)).chunk_decided("CHUNK-7", "citable", False)
    BigQueryRetrieval(Graph_(embedder)).chunk_decided("CHUNK-7", "embed-prohibited", True)
    assert embedder.sent == ["now ready"]
    assert "embedding = [1.0" not in sent[1] and "embedding = []" in sent[2]


class Loader:
    """BigQueryLoader's surface sync_graph uses, the rows it would load kept."""

    dataset_prefix = "kge"

    def __init__(self, dry_run: bool = False):
        self.dry_run, self.loaded = dry_run, []

    def account(self):
        return None, PROJECT

    def ensure_dataset(self, dataset_id):
        pass

    def create_table(self, dataset_id, table_id, schema_fields, **_):
        pass

    def load_rows(self, dataset_id, table_id, schema_fields, rows):
        self.loaded.extend(rows)
        return len(rows)


def graph_rows(embedder):
    """A stand-in for the graph sync_graph opens: one ready chunk and one embed-prohibited, as the graph store holds
    them where the vectors live in BigQuery, and one ready chunk holding a 128-wide vector from before."""

    class G:
        def __init__(self, prof):
            self.embedder = embedder

        def q(self, cypher, params=None):
            row = {"doc": "D", "title": "t", "shelf": "certified", "unit": "", "tier": "gold", "ai_ready": True,
                   "purposes": ["analysis"], "terms": [], "excerpt": ""}
            return [{**row, "chunk": "C1", "readiness": "citable", "embedding": None, "embedding_model": embedder.name,
                     "text": "ready text"},
                    {**row, "chunk": "C2", "readiness": "embed-prohibited", "embedding": None, "embedding_model": "",
                     "text": "a card number"},
                    {**row, "chunk": "C3", "readiness": "retrieval-internal", "embedding": [0.5] * DIM,
                     "embedding_model": "text-embedding-004", "text": "older text"}]

        def close(self):
            pass

    return G


def test_the_sync_embeds_each_ready_chunk_into_bigquery_and_never_a_prohibited_one(monkeypatch):
    monkeypatch.setenv("WAREHOUSE_BACKEND", "bigquery")
    embedder = Recorder()
    monkeypatch.setattr(load_bigquery, "Graph", graph_rows(embedder))
    loader = Loader()
    assert load_bigquery.sync_graph(loader, None, "meridian") == {"total_chunks": 3, "embedded": 2, "table": f"kge_meridian_graph.{CHUNKS}"}
    assert embedder.sent == ["ready text", "older text"]
    by = {r["chunk"]: r for r in loader.loaded}
    assert len(by["C1"]["embedding"]) == 768 and by["C1"]["embedding_model"] == "gemini-embedding-001"
    assert len(by["C3"]["embedding"]) == 768 and by["C3"]["embedding_model"] == "gemini-embedding-001"
    assert by["C2"]["embedding"] == [] and by["C2"]["embedding_model"] == ""


def test_a_dry_run_sync_embeds_nothing(monkeypatch):
    monkeypatch.setenv("RETRIEVAL_BACKEND", "bigquery")
    embedder = Recorder()
    monkeypatch.setattr(load_bigquery, "Graph", graph_rows(embedder))
    assert load_bigquery.sync_graph(Loader(dry_run=True), None, "meridian")["embedded"] == 2
    assert embedder.sent == []


def test_on_the_graph_store_s_path_the_sync_copies_its_vectors_as_before(monkeypatch):
    monkeypatch.setenv("WAREHOUSE_BACKEND", "bigquery")
    monkeypatch.setenv("RETRIEVAL_BACKEND", "duckdb")
    embedder = Recorder(dim=DIM, name="hash-v1")
    monkeypatch.setattr(load_bigquery, "Graph", graph_rows(embedder))
    loader = Loader()
    assert load_bigquery.sync_graph(loader, None, "meridian") == {"total_chunks": 3, "embedded": 1, "table": f"kge_meridian_graph.{CHUNKS}"}
    assert embedder.sent == []
    by = {r["chunk"]: r for r in loader.loaded}
    assert by["C3"]["embedding"] == [0.5] * DIM and by["C3"]["embedding_model"] == "text-embedding-004"
    assert by["C1"]["embedding"] == [] and by["C1"]["embedding_model"] == "hash-v1"


def graph_with_unready(embedder):
    """graph_rows' graph, with a chunk a stopped pipeline left before its readiness was derived (None) and one with a
    readiness the gate does not name."""
    base = graph_rows(embedder)

    class G(base):
        def q(self, cypher, params=None):
            row = super().q(cypher, params)[0]
            return [{**row, "chunk": "C4", "readiness": None, "embedding_model": "", "text": "not yet classified"},
                    {**row, "chunk": "C5", "readiness": "pending", "embedding_model": "", "text": "unknown state"},
                    {**row, "chunk": "C1"}]

    return G


def test_the_sync_never_embeds_a_chunk_whose_readiness_is_missing_or_not_an_embeddable_one(monkeypatch):
    monkeypatch.setenv("WAREHOUSE_BACKEND", "bigquery")
    embedder = Recorder()
    monkeypatch.setattr(load_bigquery, "Graph", graph_with_unready(embedder))
    loader = Loader()
    assert load_bigquery.sync_graph(loader, None, "meridian") == {"total_chunks": 3, "embedded": 1, "table": f"kge_meridian_graph.{CHUNKS}"}
    assert embedder.sent == ["ready text"]
    by = {r["chunk"]: r for r in loader.loaded}
    assert by["C4"]["embedding"] == [] and by["C4"]["embedding_model"] == ""
    assert by["C5"]["embedding"] == [] and by["C5"]["embedding_model"] == ""
    monkeypatch.setattr(load_bigquery, "Graph", graph_with_unready(embedder))
    assert load_bigquery.sync_graph(Loader(dry_run=True), None, "meridian")["embedded"] == 1


@pytest.mark.parametrize("readiness", [None, "", "pending"])
def test_a_decision_that_leaves_no_embeddable_readiness_sends_nothing_to_the_embedder(monkeypatch, readiness):
    monkeypatch.setenv("RETRIEVAL_BACKEND", "bigquery")
    sent: list[str] = []
    monkeypatch.setattr("kge.adapters.retrieval.bigquery.bq_query", lambda sql, project=None: sent.append(sql) or [])
    embedder = Recorder()
    BigQueryRetrieval(Graph_(embedder)).chunk_decided("CHUNK-7", readiness, True)
    assert embedder.sent == [] and "embedding = [1.0" not in sent[0]


def opened(monkeypatch, **env) -> dict:
    """The pipeline's counts as kge/pipeline/run.py opens them, then one ready chunk and one gated chunk counted."""
    for name in ("RETRIEVAL_BACKEND", "WAREHOUSE_BACKEND"):
        monkeypatch.delenv(name, raising=False)
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    from kge.pipeline import run
    stats = {"chunks": 0, run.ready_count(): 0, "gated_before_embedding": 0, "readiness": {}}
    run.count(stats, {"readiness": "citable", "embedded": True}, +1)
    run.count(stats, {"readiness": "embed-prohibited", "embedded": False}, +1)
    return stats


def test_the_pipeline_counts_ready_chunks_as_embedded_only_where_it_embeds_them(monkeypatch):
    """Where the vectors live in BigQuery the pipeline writes none, and its count of ready chunks says what it means,
    to_embed_in_bigquery (Grok on #97); on the graph store's path the output is as it was."""
    local = opened(monkeypatch)
    assert list(local) == ["chunks", "embedded", "gated_before_embedding", "readiness"]
    assert (local["embedded"], local["gated_before_embedding"]) == (1, 1)
    cloud = opened(monkeypatch, RETRIEVAL_BACKEND="bigquery")
    assert "embedded" not in cloud and (cloud["to_embed_in_bigquery"], cloud["gated_before_embedding"]) == (1, 1)


class Searched:
    """A graph for BigQueryRetrieval: its client, its embedder's name, and no live chunk."""
    profile = SimpleNamespace(client="meridian")
    embedder = SimpleNamespace(name="vertex:gemini-embedding-001")
    chunks = f"`{PROJECT}.kge_meridian_graph.{CHUNKS}`"

    def q(self, cypher, params=None):
        return []


def search_with(monkeypatch, counts: dict) -> tuple[dict, list[str]]:
    monkeypatch.setenv("BIGQUERY_PROJECT", "pilot-project")
    sent = []

    def query(self, sql):
        sent.append(sql)
        return [counts] if "COUNTIF" in sql else []
    monkeypatch.setattr(BigQueryRetrieval, "query", query)
    return BigQueryRetrieval(Searched()).search([0.1] * 768, ["retail"], "analysis", 3, None, None), sent


def test_a_search_says_how_many_vectors_are_of_another_width(monkeypatch, caplog):
    """After an EMBEDDING_DIM change without a new sync, the question's vector is wider or narrower than the table's,
    and VECTOR_SEARCH finds nothing: the search counts those beside the other model's and logs why (Grok on #97)."""
    with caplog.at_level("WARNING", logger="kge.adapters.retrieval.bigquery"):
        res, sent = search_with(monkeypatch, {"vectors": "83", "n": "0", "w": "83"})
    assert res["exact"] == [] and (res["skipped_other_model"], res["skipped_other_width"]) == (0, 83)
    counted = next(sql for sql in sent if "COUNTIF" in sql)
    assert "ARRAY_LENGTH(embedding) != 768" in counted and "embedding_model = \"vertex:gemini-embedding-001\"" in counted
    assert f"83 chunks in `{PROJECT}.kge_meridian_graph.{CHUNKS}`" in caplog.text and "of another width than the question's 768" in caplog.text


def test_a_search_with_every_vector_the_question_s_width_logs_nothing(monkeypatch, caplog):
    with caplog.at_level("WARNING", logger="kge.adapters.retrieval.bigquery"):
        res, _ = search_with(monkeypatch, {"vectors": "83", "n": "2", "w": "0"})
    assert (res["skipped_other_model"], res["skipped_other_width"]) == (2, 0) and caplog.text == ""


def test_a_sync_of_a_graph_whose_chunks_are_synced_since_its_load_does_nothing(monkeypatch, tmp_path):
    """The release's sync, after the load's: the graph's pointer names its chunks already, so nothing is read, embedded
    or written; a load forgets the pointer, so the next sync writes the reloaded graph's chunks to a fresh table. The
pointer is the real one here (the file's stand-ins for it undone)."""
    from kge.adapters import retrieval
    monkeypatch.undo()
    monkeypatch.setenv("RETRIEVAL_BACKEND", "bigquery")
    monkeypatch.setenv("BIGQUERY_PROJECT", PROJECT)
    model_hash = "ab" * 32

    class Prof:
        pinned, client = model_hash, "meridian"

        def graph_path(self):
            return tmp_path / "stores" / "meridian" / model_hash / "graph.bigquery"
    prof = Prof()
    embedder = Recorder()
    monkeypatch.setattr(load_bigquery, "Graph", graph_rows(embedder))
    first = Loader()
    made = load_bigquery.sync_graph(first, prof, "meridian", embedder=embedder)
    assert made["total_chunks"] == 3 and made["embedded"] == 2 and embedder.sent == ["ready text", "older text"]
    assert retrieval.named_chunks(prof) == made["table"]
    again, sent_before = Loader(), list(embedder.sent)
    monkeypatch.setattr(load_bigquery, "Graph", lambda prof: (_ for _ in ()).throw(AssertionError("the graph is read")))
    assert load_bigquery.sync_graph(again, prof, "meridian", embedder=embedder) == {"total_chunks": 0, "embedded": 0,
                                                                                    "table": made["table"], "kept": True}
    assert again.loaded == [] and embedder.sent == sent_before
    retrieval.forget_chunks(prof.graph_path().parent)
    assert retrieval.named_chunks(prof) is None
