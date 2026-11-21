"""Real offline onboarding, tagging, Search, citations and unchanged candidates for both reader declarations."""
from __future__ import annotations

import shutil
import subprocess
import sys

import pytest
import yaml

from kge import cli
from kge.adapters.docreader.gemini import GeminiReader
from kge.adapters.docreader.text import TextReader
from kge.eval import sets
from kge.graph import release
from kge.pipeline.profile import resolve
from kge.pipeline.settings import ROOT
from kge.services.ask import Engine
from kge.services.search.models import Search
from kge.services.search.service import search
from tests.document_samples import sample
from tests.scratch import BUILD
from tests.unit.test_document_reader import offline as offline, vertex as vertex


@pytest.mark.parametrize("reader", [TextReader(), GeminiReader()], ids=lambda r: r.name)
def test_every_format_is_tagged_searchable_and_citable_after_release(tmp_path, monkeypatch, vertex, reader, capsys):
    estate = tmp_path / "estate"
    estate.mkdir()
    shutil.copy2(BUILD / "summary.json", estate / "summary.json")
    if sys.platform == "darwin":
        subprocess.run(["cp", "-cR", str(BUILD / "warehouses"), str(estate / "warehouses")], check=True)
    else:
        shutil.copytree(BUILD / "warehouses", estate / "warehouses")
    clients = tmp_path / "clients"
    shutil.copytree(ROOT / "clients/meridian", clients / "meridian")
    fx = clients / "meridian/fixture"
    schema_path = fx / "schema.yaml"
    schema = yaml.safe_load(schema_path.read_text())
    for format in reader.formats:
        path = fx / "docs/certified" / f"READ-{format}.{format}"
        schema["document_order"].append(path.relative_to(fx).as_posix())
        meta = {"title": f"Reading {format}", "kind": "policy", "unit": "retail", "owner": "Retail Data Office",
                "version": "1.0", "published_at": "2025-06-01", "reviewed_at": "2025-06-02"}
        if format == "md":
            path.write_text("---\n" + yaml.safe_dump(meta) + "---\n" + sample(format).decode())
        else:
            path.write_bytes(sample(format))
            path.with_name(path.name + ".yaml").write_text(yaml.safe_dump(meta))
    unsupported = fx / "docs/certified/UNREAD.docx"
    schema["document_order"].append(unsupported.relative_to(fx).as_posix())
    schema_path.write_text(yaml.safe_dump(schema, sort_keys=False))
    unsupported.write_bytes(b"PK\x03\x04unread original\x00\xff")
    unsupported.with_name(unsupported.name + ".yaml").write_text(yaml.safe_dump({
        "title": "Unread operating manual", "kind": "policy", "unit": "retail", "owner": "Retail Data Office"}))
    identities = tmp_path / "meridian/identities.yaml"
    identities.parent.mkdir()
    shutil.copyfile(ROOT / "deploy/meridian/identities.yaml", identities)
    registry = tmp_path / "profiles.yaml"
    registry.write_text(yaml.safe_dump({"default": "meridian", "profiles": {"meridian": {
        "client": "meridian", "clients": str(clients), "artifacts": str(tmp_path / "artifacts"),
        "store": str(tmp_path / "stores"), "landing": str(tmp_path / "landing"),
        "warehouses": str(tmp_path / "estate/warehouses"), "identities": str(identities),
        "asker": "analyst_enterprise", "baseline": None}}}))
    for name, value in {"PROFILES": str(registry), "PROFILE": "meridian", "MODEL_PROVIDER": "none",
                        "DOCUMENT_READER": reader.name, "TAGGER_PROVIDER": "rules"}.items():
        monkeypatch.setenv(name, value)
    for name in ("LANDING_URL", "BASELINE", "HASH", "PREVIOUS", "FROM", "CLOUD_RUN_TASK_ATTEMPT"):
        monkeypatch.delenv(name, raising=False)

    def run(*args):
        assert cli.main([*args, f"PY={sys.executable}"]) == 0
        return capsys.readouterr().out

    output = run("onboard", "REPEATS=1")
    assert f"content not read: docx isn't supported by {reader.name}" in output
    prof = resolve()
    model_hash = next(line.removeprefix("onboard: candidate ") for line in output.splitlines()
                      if line.startswith("onboard: candidate "))
    run("sign-off", f"HASH={model_hash}", "PRINCIPAL=steward_glossary.meridian@example.com")
    run("release", f"HASH={model_hash}")
    engine = Engine.serving(prof)
    who = "analyst_enterprise.meridian@example.com"
    try:
        for format in reader.formats:
            identifier = f"READ-{format}"
            hit = next(h for h in search(engine, who, Search(query=f"Reading {format}", kinds=["document"])).hits
                       if h.id == identifier)
            assert hit.why.kind == "name"
            chunks = engine.g.q("MATCH (d:Document {id: $id})-[:HAS_CHUNK]->(c:Chunk)-[:TAGGED]->(t:Tag) "
                                "RETURN c.id AS id, c.readiness AS readiness, c.text AS text", {"id": identifier})
            assert chunks
            passage = next((row for row in engine.g.retrieve(chunks[0]["text"], who, "analysis", k=1000)["exact"]
                            if row["chunk"] == chunks[0]["id"]), None)
            assert passage is not None, identifier

            def retrieve(question, principal, purpose, k=5, shelf=None, tiers=None):
                eligible = shelf != "uncertified" and (tiers is None or passage["tier"] in tiers)
                hits = [passage] if eligible else []
                return {"exact": hits, "index_returned": len(hits), "post_filter_lost": 0}

            with monkeypatch.context() as scoped:
                scoped.setattr(engine.g, "retrieve", retrieve)
                answer = engine.answer("What is reading?", principal=who, arm_name="graph")
            assert answer["behaviour"] == "cite_certified" and identifier in answer["cite"]
        retrieved = engine.g.retrieve("Takings", who, "analysis", k=100)["exact"]
        assert "UNREAD" not in {r["doc"] for r in retrieved}
        hit = next(h for h in search(engine, who, Search(query="Unread operating manual", kinds=["document"])).hits
                   if h.id == "UNREAD")
        assert hit.why.kind == "name" and hit.why.chunk is None
        assert not engine.g.q("MATCH (d:Document {id: $id})-[:HAS_CHUNK]->(c:Chunk) RETURN c.id AS id", {"id": "UNREAD"})
        answer = engine.answer("What is the unread operating manual?", principal=who, arm_name="graph")
        assert "UNREAD" not in (answer.get("cite") or [])
        assert all(citation["doc"] != "UNREAD" for citation in answer.get("citations") or [])
        golden = sets.items(prof.estate() / "golden_documents_v1.json")
        assert golden[0]["expect"]["cite"] == ["STD-DOCUMENT-READING"]
        heldout = sets.items(prof.estate() / "heldout_documents_v1.json")
        assert heldout[0]["paraphrase_of"] == golden[0]["question"], "the held-out paraphrase is scored and reported, never gated"
    finally:
        engine.close()
    graph = prof.at(model_hash).graph_path()
    loaded, gate = release.loaded(graph), release.gate(graph)
    calls = len(vertex[0])
    assert "onboard: current already" in run("onboard", "REPEATS=1")
    assert release.loaded(graph) == loaded and release.gate(graph) == gate
    assert len(vertex[0]) == calls
