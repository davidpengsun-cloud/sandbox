"""A document added to Fellmoor's fixture survives real local onboarding and is searchable after release."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import yaml

from kge.graph import release
from kge.pipeline.profile import resolve
from kge.services.ask import Engine
from kge.services.search.models import Search
from kge.services.search.service import search


DEMO = Path(__file__).resolve().parents[2]
DOCUMENT = "OP-BOARDING-19"
PASSAGE = "Copper lantern boarding protocol records a blue signal before the ticketing validator is reset."


def test_added_fixture_document_reloads_the_same_model_and_search_finds_its_passage(tmp_path, monkeypatch):
    for key in ("STORE_URL", "LANDING_URL", "HASH", "PREVIOUS", "BASELINE", "MODEL_HIGH_PROVIDER",
                "MODEL_MEDIUM_PROVIDER", "MODEL_LOW_PROVIDER", "MODEL_DEFAULT_PROVIDER"):
        monkeypatch.delenv(key, raising=False)
    for key, value in {"WAREHOUSE_BACKEND": "duckdb", "GRAPH_STORE": "ladybug", "RETRIEVAL_BACKEND": "graph",
                       "APP_STATE_BACKEND": "files", "MODEL_PROVIDER": "none", "TAGGER_PROVIDER": "rules",
                       "PROFILE": "fellmoor"}.items():
        monkeypatch.setenv(key, value)
    clients = tmp_path / "clients"
    shutil.copytree(DEMO / "clients/fellmoor", clients / "fellmoor")
    identities = tmp_path / "fellmoor" / "identities.yaml"
    identities.parent.mkdir()
    shutil.copyfile(DEMO / "deploy/fellmoor/identities.yaml", identities)
    registry = tmp_path / "profiles.yaml"
    registry.write_text(yaml.safe_dump({"default": "fellmoor", "profiles": {"fellmoor": {
        "client": "fellmoor", "clients": str(clients), "artifacts": str(tmp_path / "artifacts"),
        "store": str(tmp_path / "stores"), "landing": str(tmp_path / "landing"),
        "warehouses": str(tmp_path / "estate/warehouses"), "identities": str(identities), "baseline": None,
        "asker": "network_analyst"}}}))
    monkeypatch.setenv("PROFILES", str(registry))
    prof = resolve()

    def run(*args):
        done = subprocess.run([sys.executable, "-m", "kge.cli", *args], cwd=DEMO, env=dict(os.environ),
                              capture_output=True, text=True, timeout=600)
        assert done.returncode == 0, done.stdout + done.stderr
        return done.stdout

    first = run("onboard", "REPEATS=1")
    assert first.count("onboard: [6/10] certify\n") == 1 and "onboard: certify: nothing changed" not in first
    assert (prof.landing / prof.client / "current").exists()
    assert not (prof.estate() / "landing").exists()
    model_hash = next(line.removeprefix("onboard: candidate ") for line in first.splitlines()
                      if line.startswith("onboard: candidate "))
    run("sign-off", f"HASH={model_hash}", "PRINCIPAL=fares_steward.fellmoor@example.com")
    run("release", f"HASH={model_hash}")
    assert prof.model_hash() == model_hash
    engine = Engine.serving(prof)
    try:
        assert DOCUMENT not in {hit.id for hit in search(engine, "network_analyst.fellmoor@example.com", Search(query=PASSAGE)).hits}
    finally:
        engine.close()
    lock = (clients / "fellmoor/pack.lock").read_bytes()
    graph = prof.at(model_hash).graph_path()
    previous_load = release.loaded(graph)
    previous_gate = release.gate(graph)
    summary = prof.estate() / "summary.json"
    previous_digest = json.loads(summary.read_text())["fixture_sha256"]
    note = clients / "fellmoor/fixture/docs/certified" / f"{DOCUMENT}.md"
    note.write_text('---\ntitle: Depot operating note\nkind: policy\nunit: ticketing\nowner: Fares Data Steward\n'
                    'version: "1.0"\npublished_at: "2025-06-01"\nreviewed_at: "2025-06-02"\n---\n'
                    f"{PASSAGE}\n")
    second = run("onboard", "REPEATS=1")
    assert second.count("onboard: [6/10] certify\n") == 1 and "onboard: certify: nothing changed" not in second
    assert (prof.landing / prof.client / "current").exists()
    assert not (prof.estate() / "landing").exists()
    assert f"onboard: candidate {model_hash}\n" in second
    assert "onboard: current already" not in second
    assert (clients / "fellmoor/pack.lock").read_bytes() == lock
    assert prof.model_hash() == model_hash
    changes = release.changes(graph)
    assert changes["estate"]["rebuilt"] is True and changes["estate"]["why"] == "fixture-changed"
    assert changes["lock"] == {"written": False, "changes": []} and changes["reloaded"] is True
    assert json.loads(summary.read_text())["fixture_sha256"] != previous_digest
    assert release.loaded(graph)["release"] != previous_load["release"]
    assert release.gate(graph)["passed"] is True and release.gate(graph)["run_id"] != previous_gate["run_id"]
    assert release.marked(graph, release.SIGNOFF) is None
    run("sign-off", f"HASH={model_hash}", "PRINCIPAL=fares_steward.fellmoor@example.com")
    run("release", f"HASH={model_hash}")
    engine = Engine.serving(prof)
    try:
        result = search(engine, "network_analyst.fellmoor@example.com", Search(query=PASSAGE, kinds=["document"]))
        hit = next((hit for hit in result.hits if hit.id == DOCUMENT), None)
        assert hit is not None, result.model_dump()
        assert hit.unit == "ticketing" and hit.why.kind == "passage" and PASSAGE in hit.why.text
        assert hit.why.chunk.startswith(f"{DOCUMENT}#")
    finally:
        engine.close()
