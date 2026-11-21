"""The pipeline where the chunk vectors live in BigQuery (#94 item 3), on a scratch copy of the demo's graph.

    .venv/bin/python -m pytest tests/integration/test_bigquery_pipeline.py -q

With RETRIEVAL_BACKEND=bigquery the pipeline writes no vector to the graph store, old ones included, and names the
embedder on each ready chunk, so the BigQuery sync embeds those and no others. Nothing calls BigQuery: the pipeline
never does, and the embedder is the hashing one.
"""
from __future__ import annotations

from tests.scratch import open_engine, scratch_build, script


def test_the_pipeline_keeps_no_vector_in_the_graph_store_where_bigquery_holds_them(tmp_path):
    build = scratch_build(tmp_path / "build")
    e = open_engine(build)
    before = e.g.q("MATCH (c:Chunk) WHERE c.embedding IS NOT NULL RETURN count(*) AS n")[0]["n"]
    e.close()
    assert before > 0
    r = script("kge/pipeline/run.py", build=build, env={"RETRIEVAL_BACKEND": "bigquery", "EMBEDDING_PROVIDER": "hash"})
    assert r.returncode == 0, r.stderr
    e = open_engine(build)
    rows = e.g.q("MATCH (c:Chunk) RETURN c.readiness AS readiness, c.embedding IS NULL AS empty, c.embedding_model AS model")
    e.close()
    assert rows and all(row["empty"] for row in rows)
    assert {row["model"] for row in rows if row["readiness"] != "embed-prohibited"} == {"hash-v1"}
    assert {row["model"] for row in rows if row["readiness"] == "embed-prohibited"} <= {""}
