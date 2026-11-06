"""Owner-calibrated Meridian suggestions on Ladybug and the BigQuery REST twin.

The semantic fixture uses the owner's observed cosine scores, not a paid model.
It deliberately offers the contrasting assets metric so a steward can reject it.
"""
from __future__ import annotations

import json
import math
import os
import subprocess
import sys

import pytest

from tests.scratch import DEMO, profile_of, registry, scratch_build

PAIRS = (
    ("MD-ASSETS-UNDER-ADVICE#0", "Assets Under Management", .8062),
    ("STD-FX#0", "Period-End FX Rate", .7813),
    ("EM-005#0", "Fiscal Year", .7021),
)


def walkthrough(stage):
    """Exercise real graph, generation, steward and journal operations inside either store."""
    from kge.adapters.graphstore import batch
    from kge.adapters.model import DIM, make_embedder
    from kge.pipeline.overlay import Journal, replay
    from kge.pipeline.profile import resolve
    from kge.pipeline.suggestions import generate, suggestion_id
    from kge.services.ask.engine.core import Engine

    engine = Engine.candidate(resolve())
    g = engine.g
    steward = "steward_glossary.meridian@example.com"

    def queue():
        return [r for r in g.steward_queue(steward) if r["family"] == "suggested_mention"]

    def basis(index, score=1.0):
        vector = [0.0] * DIM
        vector[index], vector[10] = score, math.sqrt(1 - score * score)
        return vector

    def mentions():
        return {(r["chunk"], r["term"]): (r["generator"], r["confidence"])
                for r in g.q("MATCH (c:Chunk)-[m:MENTIONS]->(t:Term) RETURN c.id AS chunk, t.name AS term, "
                             "m.generator AS generator, m.confidence AS confidence")}

    definitions = {r["name"]: r["definition"] for r in g.q("MATCH (t:Term) RETURN t.name AS name, t.definition AS definition")}
    targets = {definitions[term]: basis(i) for i, (_, term, _) in enumerate(PAIRS)}
    targets[definitions["Revenue"]] = basis(3)

    class Calibration:
        name = "owner-calibration-fixture"
        mention_similarity = .70

        def embed(self, text):
            assert text in definitions.values(), "only glossary definitions are encoded by suggestion generation"
            return targets.get(text, basis(5))

    embedder = Calibration()

    def rows():
        data = g.q("MATCH (c:Chunk) RETURN c.id AS chunk, c.text AS text, c.readiness AS readiness, "
                   "c.embedding AS embedding, c.embedding_model AS embedding_model")
        for row in data:
            row.update(embedding=basis(4), embedding_model=embedder.name)
            for i, (cid, _, score) in enumerate(PAIRS):
                if row["chunk"] == cid:
                    row["embedding"] = basis(i, score)
            if row["chunk"] == "POL-COMPLAINTS#0":
                row["embedding"] = basis(0)
            if row["chunk"] == "MD-REVENUE#0":
                row["embedding"] = basis(3)
        return data

    def generate_fixture():
        return generate(g.q, lambda: batch(g.conn), embedder, rows())

    try:
        if stage == "advance":
            from kge.landing.zone import Zone
            landed = Zone(engine.profile.landing, engine.profile.client).open()
            old_release = g.q("MATCH (r:Release) RETURN r.id AS id")[0]["id"]
            assert old_release != landed.id
            with batch(g.conn) as w:
                w.delete_node("Release", old_release)
                w.upsert("Release", landed.id, {k: v for k, v in landed.about().items() if k != "id"})
            print(json.dumps({"stage": stage, "passed": True}))
            return
        if stage == "hash":
            os.environ.pop("SUGGESTED_MENTION_MIN_SIMILARITY", None)
            count = generate(g.q, lambda: batch(g.conn), make_embedder())
            offered = queue()
            assert count == len(offered) and 0 < count <= 20
            assert all(r["similarity"] >= .55 for r in offered)
            assert all((r["chunk"], r["value"]) not in mentions() for r in offered)
            assert all(r["doc"] not in {"POL-COMPLAINTS", "POL-RETENTION", "EM-006", "XLS-001"} for r in offered)
            print(json.dumps({"hash_suggestions": count}))
            return

        before = mentions()
        assert ("MD-REVENUE#0", "Revenue") in before
        if stage == "decide":
            assert all((cid, term) not in before for cid, term, _ in PAIRS)
            os.environ["SUGGESTED_MENTION_MIN_SIMILARITY"] = ".80"
            assert generate_fixture() == 1
            assert [(r["chunk"], r["value"]) for r in queue()] == [PAIRS[0][:2]]
            os.environ["SUGGESTED_MENTION_MIN_SIMILARITY"] = ".70"
            os.environ["SUGGESTED_MENTION_CAP"] = "2"
            assert generate_fixture() == 2
            assert [(r["chunk"], r["value"]) for r in queue()] == [p[:2] for p in PAIRS[:2]]
            os.environ["SUGGESTED_MENTION_CAP"] = "20"
            assert generate_fixture() == 3
            offered = queue()
            assert [(r["chunk"], r["value"]) for r in offered] == [p[:2] for p in PAIRS]
            assert [r["similarity"] for r in offered] == pytest.approx([p[2] for p in PAIRS])
            assert all(r["passage"] and r["definition"] for r in offered)
            assert mentions() == before, "a suggestion never creates a link"
            limited = "steward.meridian@example.com"
            restricted = offered[0]
            assert g.may(limited, "certification") and "corporate" not in g.readable_units(limited)
            assert "corporate" in g.readable_units(steward)
            original_unit = g.q("MATCH (d:Document) WHERE d.id = $d RETURN d.unit AS unit", {"d": restricted["doc"]})[0]["unit"]
            with batch(g.conn) as w:
                w.update("Document", restricted["doc"], {"unit": "corporate"})
            assert restricted["tag"] not in {r["tag"] for r in g.steward_queue(limited)}
            assert restricted["tag"] in {r["tag"] for r in g.steward_queue(steward)}
            for decision in ("accept", "reject"):
                with pytest.raises(PermissionError, match="may not read"):
                    g.steward_decide(restricted["tag"], limited, decision)
            from fastapi import FastAPI
            from fastapi.testclient import TestClient
            from unittest.mock import patch
            from kge.services.steward import routes
            app = FastAPI()
            app.include_router(routes.router)
            for route in routes.router.routes:
                for dependency in route.dependant.dependencies:
                    app.dependency_overrides[dependency.call] = lambda: limited
            with patch.object(routes.served, "engine", return_value=engine), TestClient(app) as http:
                response = http.get("/steward/queue")
                assert response.status_code == 200
                assert restricted["tag"] not in {r["tag"] for r in response.json()}
                assert restricted["passage"] not in response.text
                for decision in ("accept", "reject"):
                    response = http.post("/steward/decide", json={"tag": restricted["tag"], "decision": decision})
                    assert response.status_code == 403, response.text
            assert mentions() == before
            assert not Journal(engine.profile.overlay_dir(), engine.profile.client).decisions()
            with batch(g.conn) as w:
                w.update("Document", restricted["doc"], {"unit": ""})
            assert restricted["tag"] in {r["tag"] for r in g.steward_queue(limited)}, "global passages remain reviewable"
            with batch(g.conn) as w:
                w.update("Document", restricted["doc"], {"unit": original_unit})
            with pytest.raises(PermissionError):
                g.steward_decide(offered[0]["tag"], "knowledge_engineer.meridian@example.com", "accept")
            with pytest.raises(ValueError):
                g.steward_decide(offered[0]["tag"], steward, "accept", value="Revenue")
            rejected = g.steward_decide(offered[0]["tag"], steward, "reject")
            accepted = g.steward_decide(offered[1]["tag"], steward, "accept")
            assert rejected["decision"] == "reject" and accepted["mention_created"] is True
            assert mentions()[PAIRS[1][:2]][0] == "embedding_steward_accepted"
            assert mentions()[PAIRS[1][:2]][1] == pytest.approx(PAIRS[1][2])
            assert PAIRS[0][:2] not in mentions()
            assert generate_fixture() == 1
            assert [(r["chunk"], r["value"]) for r in queue()] == [PAIRS[2][:2]]
            with pytest.raises(LookupError):
                g.steward_decide(offered[0]["tag"], steward, "accept")
            records = Journal(engine.profile.overlay_dir(), engine.profile.client).decisions()
            assert len(records) == 2
            assert [r["decision"] for r in records] == ["reject", "accept"]
            assert all(r["machine"]["family"] == "suggested_mention" and r["who"] == steward for r in records)
        else:
            records = Journal(engine.profile.overlay_dir(), engine.profile.client).decisions()
            assert len(records) == 2
            assert before[PAIRS[1][:2]][0] == "embedding_steward_accepted"
            assert PAIRS[0][:2] not in before
            assert generate_fixture() == (2 if stage == "changed" else 1)
            if stage != "changed":
                print(json.dumps({"stage": stage, "passed": True, "pending": {r["chunk"]: r["tag"] for r in queue()}}))
                return
            offered = queue()
            old = next(r for r in offered if r["chunk"] == PAIRS[2][0])
            assert "Reviewed after the metric definition changed." in offered[0]["passage"]
            changed_row = g.q("MATCH (c:Chunk) WHERE c.id = $id RETURN c.text AS text, c.embedding AS embedding",
                              {"id": PAIRS[0][0]})[0]
            hash_embedder = make_embedder()
            assert changed_row["embedding"] == pytest.approx(hash_embedder.embed(changed_row["text"]))
            previous_text = changed_row["text"].removesuffix(" Reviewed after the metric definition changed.")
            assert changed_row["embedding"] != pytest.approx(hash_embedder.embed(previous_text))
            assert [(r["chunk"], r["value"]) for r in offered] == [PAIRS[0][:2], PAIRS[2][:2]]
            assert next(r for r in offered if r["chunk"] == old["chunk"])["tag"] == old["tag"]
            assert offered[0]["tag"] != records[0]["machine"]["id"]
            assert offered[0]["tag"] == suggestion_id(PAIRS[0][0], PAIRS[0][1], offered[0]["passage"])
            with batch(g.conn) as w:
                w.update("Chunk", old["chunk"], {"readiness": "embed-prohibited", "embedding": None})
            assert all(r["tag"] != old["tag"] for r in queue())
            with pytest.raises(LookupError):
                g.steward_decide(old["tag"], steward, "accept")
            replayed = replay(g.q, records, lambda cid, reason: None)
            assert any(r["machine"] == records[0]["machine"]["id"] for r in replayed["orphans"])
        print(json.dumps({"stage": stage, "passed": True, "unchanged_tag": old["tag"] if stage == "changed" else None}))
    finally:
        engine.close()


@pytest.mark.parametrize("backend", ["ladybug", "bigquery"])
def test_meridian_embedding_suggestions_require_and_preserve_steward_decisions(tmp_path, backend):
    from tests.integration.test_graphstore_twin import pointer

    build = scratch_build(tmp_path / "build")
    graph, artifact = pointer(build) if backend == "bigquery" else (None, None)
    if artifact is None:
        profile = profile_of(build)
        artifact = profile.artifacts / profile.client / profile.model_hash()
    program = tmp_path / "walkthrough.py"
    env = {**os.environ, "PROFILES": str(registry(build)), "PROFILE": "", "MODEL_PROVIDER": "none",
           "EMBEDDING_PROVIDER": "hash", "SUGGESTED_MENTION_MIN_SIMILARITY": ".70", "SUGGESTED_MENTION_CAP": "20"}

    def run(stage):
        program.write_text("from tests.integration.test_mention_suggestions import walkthrough\nwalkthrough(" + repr(stage) + ")\n")
        command = ([sys.executable, "-m", "tests.graphtwin", str(graph), "-", str(program)]
                   if backend == "bigquery" else [sys.executable, "-c", program.read_text()])
        result = subprocess.run(command, cwd=DEMO, env=env, capture_output=True, text=True, timeout=180)
        assert result.returncode == 0, result.stdout[-2000:] + result.stderr[-5000:]
        return result

    run("hash")
    run("decide")
    command = ([sys.executable, "-m", "tests.graphtwin", str(graph), "-", "kge.pipeline.run"]
               if backend == "bigquery" else [sys.executable, "-m", "kge.pipeline.run"])
    result = subprocess.run(command, cwd=DEMO, env=env, capture_output=True, text=True, timeout=180)
    assert result.returncode == 0, result.stdout[-2000:] + result.stderr[-5000:]
    run("persisted")
    load = ([sys.executable, "-m", "tests.graphtwin", str(graph), str(artifact), "kge.graph.load", "--artifact", str(artifact)]
            if backend == "bigquery" else [sys.executable, "-m", "kge.graph.load", "--artifact", str(artifact)])
    loaded = subprocess.run(load, cwd=DEMO, env=env, capture_output=True, text=True, timeout=180)
    assert loaded.returncode == 0, loaded.stdout[-2000:] + loaded.stderr[-5000:]
    result = subprocess.run(command, cwd=DEMO, env=env, capture_output=True, text=True, timeout=180)
    assert result.returncode == 0, result.stdout[-2000:] + result.stderr[-5000:]
    persisted = json.loads(run("replayed").stdout.strip().splitlines()[-1])
    from tests.integration.test_overlay import land_edited

    def change_passage(staging):
        path = staging / "documents" / "chunks.jsonl"
        rows = [json.loads(line) for line in path.read_text().splitlines()]
        for row in rows:
            if row["id"] == PAIRS[0][0]:
                row["text"] += " Reviewed after the metric definition changed."
        path.write_text("\n".join(json.dumps(row) for row in rows) + "\n")

    land_edited(build, change_passage)
    run("advance")
    result = subprocess.run(command, cwd=DEMO, env=env, capture_output=True, text=True, timeout=180)
    assert result.returncode == 0, result.stdout[-2000:] + result.stderr[-5000:]
    changed = run("changed")
    assert persisted["pending"][PAIRS[2][0]] in changed.stdout
