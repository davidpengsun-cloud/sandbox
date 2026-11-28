"""Local onboarding recovers restored inputs and ignores metadata already in the live fixture."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys

import yaml

from kge.estate import from_files, upfront
from kge.graph import release
from kge.pipeline import objstore
from kge.pipeline.profile import resolve
from kge.pipeline.settings import ROOT


def test_restored_released_fixture_recovers_and_live_dotfiles_never_block_onboard(tmp_path, monkeypatch):
    for key in ("STORE_URL", "LANDING_URL", "HASH", "PREVIOUS", "BASELINE", "MODEL_HIGH_PROVIDER",
                "MODEL_MEDIUM_PROVIDER", "MODEL_LOW_PROVIDER", "MODEL_DEFAULT_PROVIDER"):
        monkeypatch.delenv(key, raising=False)
    for key, value in {"WAREHOUSE_BACKEND": "duckdb", "GRAPH_STORE": "ladybug", "RETRIEVAL_BACKEND": "graph",
                       "APP_STATE_BACKEND": "files", "MODEL_PROVIDER": "none", "TAGGER_PROVIDER": "rules",
                       "PROFILE": "fellmoor"}.items():
        monkeypatch.setenv(key, value)
    clients = tmp_path / "clients"
    shutil.copytree(ROOT / "clients/fellmoor", clients / "fellmoor")
    identities = tmp_path / "fellmoor" / "identities.yaml"
    identities.parent.mkdir()
    shutil.copyfile(ROOT / "deploy/fellmoor/identities.yaml", identities)
    registry = tmp_path / "profiles.yaml"
    registry.write_text(yaml.safe_dump({"default": "fellmoor", "profiles": {"fellmoor": {
        "client": "fellmoor", "clients": str(clients), "artifacts": str(tmp_path / "artifacts"),
        "store": str(tmp_path / "stores"), "landing": str(tmp_path / "landing"),
        "warehouses": str(tmp_path / "estate/warehouses"), "identities": str(identities), "baseline": None,
        "asker": "network_analyst"}}}))
    monkeypatch.setenv("PROFILES", str(registry))
    prof = resolve()

    def run(*args, code=0):
        done = subprocess.run([sys.executable, "-m", "kge.cli", *args], cwd=ROOT, env=dict(os.environ),
                              capture_output=True, text=True, timeout=600)
        assert done.returncode == code, done.stdout + done.stderr
        return done.stdout

    pack = clients / "fellmoor"
    fixture = pack / "fixture"
    original = upfront.check(prof)
    assert not original.findings
    digest = from_files.fixture_sha256(pack)
    hidden = []
    for folder in [fixture, *(p for p in fixture.rglob("*") if p.is_dir())]:
        for name in (".DS_Store", "._metadata"):
            path = folder / name
            path.write_bytes(b"\x00\xffMac metadata")
            hidden.append(path)
    for shelf in from_files.SHELVES:
        path = fixture / "docs" / shelf / "._MD-FARE.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"\x00\xffMac document metadata")
        hidden.append(path)
    checked = upfront.check(prof)
    assert not checked.findings
    assert checked.files == original.files
    assert checked.inputs_sha256 == original.inputs_sha256
    assert checked.fixture_sha256 == digest == from_files.fixture_sha256(pack)
    first = run("onboard", "REPEATS=1")
    model_hash = next(line.removeprefix("onboard: candidate ") for line in first.splitlines()
                      if line.startswith("onboard: candidate "))
    run("sign-off", f"HASH={model_hash}", "PRINCIPAL=fares_steward.fellmoor@example.com")
    run("release", f"HASH={model_hash}")
    graph = prof.at(model_hash).graph_path()
    summary = prof.estate() / "summary.json"
    released_summary = json.loads(summary.read_text())
    assert released_summary["fixture_sha256"] == digest
    csv = fixture / "data/ticketing/journey.csv"
    original_rows = csv.read_bytes()
    lines = original_rows.decode().splitlines()
    columns = lines[0].split(",")
    amount = columns.index("fare_usd")
    selected = next(i for i, line in enumerate(lines[1:], 1) if "2025-06-" in line)
    row = lines[selected].split(",")
    row[amount] = str(float(row[amount]) + 1000)
    lines[selected] = ",".join(row)
    csv.write_text("\n".join(lines) + "\n")
    failed = run("onboard", "REPEATS=1", code=1)
    assert "onboard: gate failed" in failed
    record = json.loads((prof.run_dir() / objstore.ONBOARD_RUN).read_text())
    assert record["step"] == "margin" and not record["gate"]["passed"]
    report = prof.paths()["runs"] / record["gate"]["run_id"] / "report.md"
    assert "F01" in report.read_text()
    scores = [json.loads(line) for line in report.with_name("records.jsonl").read_text().splitlines()]
    fare = [score for score in scores if score["id"] == "F01" and score["arm"] == "treatment"]
    assert fare and all(not score["ok"] for score in fare)
    failed_summary = json.loads(summary.read_text())
    assert "fixture_sha256" not in failed_summary
    assert failed_summary["warehouse_inputs"] == {u: value for u, value in released_summary["warehouse_inputs"].items()
                                                  if u != "ticketing"}
    csv.write_bytes(original_rows)
    recovered = run("onboard", "REPEATS=1")
    assert "onboard: gate passed" in recovered
    changes = release.changes(graph)
    assert changes["estate"]["rebuilt"]
    assert release.loaded(graph)
    assert release.gate(graph)["passed"]
    assert release.gate(graph)["run_id"] != record["gate"]["run_id"]
    assert json.loads(summary.read_text())["fixture_sha256"] == digest
    assert json.loads(summary.read_text())["warehouse_inputs"] == released_summary["warehouse_inputs"]
    source = tmp_path / "upload"
    shutil.copytree(pack, source, ignore=shutil.ignore_patterns(".*"))
    run("onboard", "REPEATS=1", f"FROM={source}")
    assert all(path.is_file() for path in hidden)
    record = json.loads((prof.run_dir() / objstore.ONBOARD_RUN).read_text())
    assert not record["findings"] and record["gate"]["passed"]
    assert from_files.fixture_sha256(pack) == digest
    assert json.loads(summary.read_text())["fixture_sha256"] == digest
