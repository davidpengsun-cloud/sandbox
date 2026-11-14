"""External vectors keep their pending queue across pipeline and sync reruns.

A real scratch graph, landing, pipeline, sync, generator and pointer lifecycle
run here. Only the BigQuery row upload is replaced; hash embeddings are local.
"""
from __future__ import annotations

import json
import shutil
import sys
import threading

import pytest

from tests.scratch import registry, scratch_build


def bounded_call(call, timeout=180):
    result = []

    def invoke():
        try:
            result.append((True, call()))
        except BaseException as exc:
            result.append((False, exc))

    thread = threading.Thread(target=invoke, daemon=True)
    thread.start()
    thread.join(timeout)
    if thread.is_alive():
        raise TimeoutError(f"call exceeded {timeout} seconds")
    success, value = result[0]
    if not success:
        raise value
    return value


def test_bounded_call_fails_when_call_hangs():
    release = threading.Event()
    try:
        with pytest.raises(TimeoutError, match="call exceeded"):
            bounded_call(release.wait, timeout=0.01)
    finally:
        release.set()


def external_step(stage):
    from kge.adapters.graphstore import batch
    from kge.adapters.retrieval import named_chunks
    from kge.estate.load_bigquery import sync_graph
    from kge.landing.zone import Zone
    from kge.pipeline.profile import resolve
    from kge.services.ask.engine.graphview import Graph

    profile = resolve().pin()

    class Upload:
        dataset_prefix = "kge"
        dry_run = False

        def __init__(self):
            self.loaded = []

        def account(self):
            return None, "offline-project"

        def ensure_dataset(self, dataset):
            pass

        def preflight_staging(self, size):
            pass

        def create_table(self, dataset, table, schema):
            pass

        def load_rows(self, dataset, table, schema, rows):
            self.loaded.extend(rows)

    if stage == "sync":
        uploader = Upload()
        report = sync_graph(uploader, profile, profile.client)
        if not report.get("kept"):
            assert uploader.loaded
            assert all(not r["embedding"] for r in uploader.loaded if r["readiness"] == "embed-prohibited")
    else:
        report = {}
    graph = Graph(profile)
    try:
        if stage == "advance":
            release = Zone(profile.landing, profile.client).open()
            previous = graph.q("MATCH (r:Release) RETURN r.id AS id")[0]["id"]
            with batch(graph.conn) as writer:
                writer.delete_node("Release", previous)
                writer.upsert("Release", release.id, {k: v for k, v in release.about().items() if k != "id"})
        if stage == "forget_signature":
            pointer = profile.graph_path().parent / "chunks.bigquery"
            data = json.loads(pointer.read_text())
            data.pop("suggestions")
            pointer.write_text(json.dumps(data))
        if stage == "new_term":
            definition = graph.q("MATCH (t:Term) WHERE t.name = 'Fiscal Year' RETURN t.definition AS definition")[0]["definition"]
            with batch(graph.conn) as writer:
                writer.upsert("Term", "Accepted Fiscal Concept", {"domain": "Finance", "steward": "steward_glossary.meridian@example.com", "definition": definition})
        if stage == "reject":
            offered = [row for row in graph.steward_queue("steward_glossary.meridian@example.com") if row["family"] == "suggested_mention"]
            report["rejected"] = offered[0]["tag"]
            graph.steward_decide(offered[0]["tag"], "steward_glossary.meridian@example.com", "reject")
        queue = graph.steward_queue("steward_glossary.meridian@example.com")
        pending = [{"chunk": r["chunk"], "term": r["value"], "tag": r["tag"], "score": r["similarity"]}
                   for r in queue if r["family"] == "suggested_mention"]
        return {"pending": pending, "table": named_chunks(profile), "report": report}
    finally:
        graph.close()


@pytest.mark.parametrize("backend", ["ladybug", "bigquery"])
def test_external_pending_queue_survives_unchanged_pipeline_and_regenerates_changes(tmp_path, backend, monkeypatch, request):
    from tests.integration.test_overlay import land_edited
    from kge.pipeline import run as pipeline

    build = scratch_build(tmp_path / "build")
    from tests.integration.test_graphstore_twin import pointer
    graph = pointer(build)[0] if backend == "bigquery" else None
    env = {"PROFILES": str(registry(build)), "PROFILE": "", "MODEL_PROVIDER": "none", "GRAPH_STORE": backend,
           "EMBEDDING_PROVIDER": "hash", "RETRIEVAL_BACKEND": "bigquery", "BIGQUERY_PROJECT": "offline-project",
           "SUGGESTED_MENTION_MIN_SIMILARITY": ".55", "SUGGESTED_MENTION_CAP": "20"}
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    if graph is not None:
        from kge.adapters.graphstore import bigquery
        from tests.graphtwin import install
        for cls, name in ((bigquery.Connection, "plan"), (bigquery.BigQueryWriter, "__init__"),
                          (bigquery.Client, "_post"), (bigquery.Client, "_get")):
            monkeypatch.setattr(cls, name, getattr(cls, name))
        twin = install(graph)

        def close_twin():
            if twin.conn is not None:
                twin.conn.close()
                twin.db.close()
        request.addfinalizer(close_twin)

    def run(stage):
        for name, value in env.items():
            monkeypatch.setenv(name, value)
        if stage == "pipeline":
            with monkeypatch.context() as scoped:
                scoped.setattr(sys, "argv", ["kge.pipeline.run"])
                assert bounded_call(pipeline.main) == 0
            return None
        return bounded_call(lambda: external_step(stage))

    run("pipeline")
    first = run("sync")
    assert first["pending"] and first["report"]["embedded"] > 0
    run("pipeline")
    repeated = run("sync")
    assert repeated["report"]["kept"] and repeated["pending"] == first["pending"]
    assert repeated["table"] == first["table"]

    target = first["pending"][0]

    def edit(change):
        def apply(staging):
            path = staging / "documents" / "chunks.jsonl"
            rows = [json.loads(line) for line in path.read_text().splitlines()]
            for row in rows:
                if row["id"] == target["chunk"]:
                    change(row)
            path.write_text("\n".join(json.dumps(row) for row in rows) + "\n")
        if (build / "landing").is_symlink():
            land_edited(build, apply)
        else:
            from kge.landing.zone import Zone
            zone = Zone(build / "landing", "meridian")
            source = zone.open()
            staging = zone.stage()
            for path in source.manifest["files"]:
                (staging / path).parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source.path / path, staging / path)
            apply(staging)
            zone.land(staging, {k: v for k, v in source.manifest.items() if k not in ("client", "release_id", "files")})
        run("advance")
        run("pipeline")

    edit(lambda row: row.update(text=row["text"] + " "))
    awaiting = run("inspect")
    assert awaiting["table"] is None
    assert all(row["tag"] != target["tag"] for row in awaiting["pending"])
    changed = run("sync")
    replacement = next(row for row in changed["pending"] if (row["chunk"], row["term"]) == (target["chunk"], target["term"]))
    assert replacement["tag"] != target["tag"]
    assert replacement["score"] == target["score"]
    assert changed["table"] != first["table"]

    edit(lambda row: row.pop("findings", None))
    assert all(row["chunk"] != target["chunk"] for row in run("inspect")["pending"])
    held = run("sync")
    assert all(row["chunk"] != target["chunk"] for row in held["pending"])
    assert held["table"] != changed["table"]

    env["SUGGESTED_MENTION_CAP"] = "1"
    run("pipeline")
    capped = run("sync")
    assert not capped["report"].get("kept") and len(capped["pending"]) == 1
    env["SUGGESTED_MENTION_CAP"] = "20"
    run("pipeline")
    restored = run("sync")
    assert not restored["report"].get("kept") and len(restored["pending"]) > 1
    run("pipeline")
    assert run("sync")["report"]["kept"]
    env["SUGGESTED_MENTION_MIN_SIMILARITY"] = "1"
    run("pipeline")
    assert all(row["score"] == 1 for row in run("sync")["pending"])
    env["SUGGESTED_MENTION_MIN_SIMILARITY"] = ".55"
    run("pipeline")
    relaxed = run("sync")
    assert relaxed["pending"] == restored["pending"]
    assert not relaxed["report"].get("kept")
    run("pipeline")
    assert run("sync")["report"]["kept"]

    run("forget_signature")
    run("pipeline")
    assert run("inspect")["table"] is None
    upgraded = run("sync")
    assert upgraded["pending"] == relaxed["pending"]
    run("pipeline")
    assert run("sync")["report"]["kept"]

    env["SUGGESTED_MENTION_CAP"] = "1"
    run("pipeline")
    assert len(run("sync")["pending"]) == 1
    decided = run("reject")
    assert decided["pending"] == []
    run("pipeline")
    backfilled = run("sync")
    assert len(backfilled["pending"]) == 1
    assert backfilled["pending"][0]["tag"] != decided["report"]["rejected"]
    run("pipeline")
    assert run("sync")["report"]["kept"]

    env["SUGGESTED_MENTION_CAP"] = "20"
    run("pipeline")
    before_term = run("sync")
    run("new_term")
    run("pipeline")
    assert run("inspect")["table"] is None
    with_term = run("sync")
    assert with_term["table"] != before_term["table"]
    assert any(row["term"] == "Accepted Fiscal Concept" for row in with_term["pending"])
    run("pipeline")
    assert run("sync")["report"]["kept"]
