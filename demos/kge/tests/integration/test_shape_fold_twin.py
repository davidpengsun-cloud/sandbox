"""The shape status projection gives the same results on Ladybug and BigQuery's offline twin."""

import json

from tests.integration.test_graphstore_twin import direct, pointer, twin
from tests.scratch import scratch_build


EXERCISE = '''
import json
from contextlib import contextmanager
from kge.adapters.graphstore import batch, connect, graph_q, open_db
from kge.eval import shapefold
from kge.eval.shapefold import StatusRow
from kge.pipeline.profile import resolve

prof = resolve()
db = open_db(prof.graph_path(), prof.run_dir() / "api.pid")
conn = connect(db)
try:
    with batch(conn) as writer:
        writer.upsert("Shape", "s.synthetic", {"label": "Synthetic", "stage": "shadow"})
    initial = [
        StatusRow("s.synthetic@0000-r1", "s.synthetic", "0000-r1", '{"compared":1}'),
        StatusRow("coded.synthetic@0000-r1", "coded.synthetic", "0000-r1", '{"compared":2}'),
    ]
    assert shapefold.write(conn, initial) == 2
    changed = [
        StatusRow("s.synthetic@0000-r1", "s.synthetic", "0000-r1", '{"compared":3}'),
        StatusRow("s.synthetic@0001-r2", "s.synthetic", "0001-r2", '{"compared":4}'),
    ]
    original_batch = shapefold.batch
    @contextmanager
    def fail_during_write(connection):
        with original_batch(connection) as writer:
            class InterruptedWriter:
                def __getattr__(self, name):
                    return getattr(writer, name)
                def upsert(self, label, identity, props):
                    if identity == "s.synthetic@0001-r2":
                        raise RuntimeError("planted writer interruption")
                    return writer.upsert(label, identity, props)
            yield InterruptedWriter()
    shapefold.batch = fail_during_write
    try:
        try:
            shapefold.write(conn, changed)
            raise AssertionError("writer interruption did not fire")
        except RuntimeError as exc:
            assert str(exc) == "planted writer interruption"
    finally:
        shapefold.batch = original_batch
    partial = graph_q(conn)("MATCH (s:ShapeStatus) RETURN s.id AS id, s.counters AS counters ORDER BY s.id")
    assert shapefold.write(conn, changed) == 2
    assert shapefold.write(conn, changed) == 2
    q = graph_q(conn)
    rows = q("MATCH (s:ShapeStatus) RETURN s.id AS id, s.shape AS shape, s.release_event AS release_event, s.counters AS counters ORDER BY s.id")
    edges = q("MATCH (s:ShapeStatus)-[:STATUS_OF]->(t:Shape) RETURN s.id AS id, t.id AS shape ORDER BY s.id")
    print(json.dumps({"rows": rows, "edges": edges, "partial": partial}, sort_keys=True))
finally:
    conn.close()
    db.close()
'''


def test_rewrite_and_delete_match_ladybug_and_offline_bigquery_twin(tmp_path):
    local = scratch_build(tmp_path / "local")
    remote = scratch_build(tmp_path / "remote")
    graph_path, _ = pointer(remote)
    script = tmp_path / "exercise_fold.py"
    script.write_text(EXERCISE)
    local_result = json.loads(direct(local, str(script)).stdout.strip().splitlines()[-1])
    twin_result = json.loads(twin(remote, graph_path, "-", str(script)).stdout.strip().splitlines()[-1])
    assert twin_result["rows"] == local_result["rows"]
    assert twin_result["edges"] == local_result["edges"]
    assert local_result["partial"] == [{"id": "s.synthetic@0000-r1", "counters": '{"compared":3}'}]
    assert [(row["id"], row["counters"]) for row in local_result["rows"]] == [
        ("s.synthetic@0000-r1", '{"compared":3}'),
        ("s.synthetic@0001-r2", '{"compared":4}'),
    ]
    assert local_result["edges"] == [
        {"id": "s.synthetic@0000-r1", "shape": "s.synthetic"},
        {"id": "s.synthetic@0001-r2", "shape": "s.synthetic"},
    ]
