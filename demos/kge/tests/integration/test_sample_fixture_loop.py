"""The test drive's baseline and local job steps leave an editable fixture client."""
from __future__ import annotations

import csv
import json
import os
import shutil
import subprocess
import sys
from decimal import Decimal

import yaml

from kge.graph import release
from kge.pipeline import objstore
from kge.pipeline.profile import resolve
from kge.pipeline.settings import ROOT
from kge.services.ask import Engine
from kge.services.onboard import test_drive_run


def test_branded_baseline_is_current_then_uploaded_row_reloads_same_hash(tmp_path, monkeypatch):
    client = "aster-bank"
    for key in ("STORE_URL", "LANDING_URL", "HASH", "PREVIOUS", "BASELINE", "FROM", "MODEL_HIGH_PROVIDER",
                "MODEL_MEDIUM_PROVIDER", "MODEL_LOW_PROVIDER", "MODEL_DEFAULT_PROVIDER"):
        monkeypatch.delenv(key, raising=False)
    for key, value in {"PROFILE": client, "WAREHOUSE_BACKEND": "duckdb", "GRAPH_STORE": "ladybug",
                       "RETRIEVAL_BACKEND": "graph", "APP_STATE_BACKEND": "files", "MODEL_PROVIDER": "none",
                       "TAGGER_PROVIDER": "rules"}.items():
        monkeypatch.setenv(key, value)
    examples, store = tmp_path / "examples", tmp_path / "store"
    shutil.copytree(ROOT / "clients/meridian", examples / "meridian/clients/meridian")
    with monkeypatch.context() as mp:
        mp.setenv("FROM", str(examples))
        mp.setenv("STORE_URL", str(store))
        objstore.put_baseline()
    clients = store / "clients"
    pack = clients / client
    assert (pack / "fixture/schema.yaml").is_file()
    assert b"MERIDIAN" not in (pack / "fixture/data/retail/transaction.csv").read_bytes()
    assert yaml.safe_load((pack / "pack.yaml").read_text())["manifest"]["inputs"]["estate"] == {"fixture": "fixture"}
    identities = tmp_path / client / "identities.yaml"
    identities.parent.mkdir()
    people = [{"principal": person.split(":")[0].replace(".sample@", f".{client}@"), "role": person.split(":")[1]}
              for person in test_drive_run.CONFIG["people"]]
    identities.write_text(yaml.safe_dump({"client": client, "principals": people}))
    registry = tmp_path / "profiles.yaml"
    registry.write_text(yaml.safe_dump({"default": client, "profiles": {client: {
        "client": client, "clients": str(clients), "artifacts": str(tmp_path / "artifacts"),
        "store": str(tmp_path / "graphs"), "landing": str(tmp_path / "landing"),
        "warehouses": str(tmp_path / "estate/warehouses"), "identities": str(identities),
        "baseline": None, "asker": "analyst_enterprise"}}}))
    monkeypatch.setenv("PROFILES", str(registry))
    prof = resolve()

    def run(*args):
        done = subprocess.run([sys.executable, "-m", "kge.cli", *args], cwd=ROOT, env=dict(os.environ),
                              capture_output=True, text=True, timeout=600)
        assert done.returncode == 0, done.stdout + done.stderr
        return done.stdout

    run("seed")
    summary = prof.estate() / "summary.json"
    digest = json.loads(summary.read_text())["fixture_sha256"]
    assert digest
    original_lock = json.loads((pack / "pack.lock").read_text())
    first = run("onboard", "REPEATS=1")
    model_hash = next(line.removeprefix("onboard: candidate ") for line in first.splitlines()
                      if line.startswith("onboard: candidate "))
    steward = f"steward_glossary.{client}@example.com"
    run("sign-off", f"HASH={model_hash}", f"PRINCIPAL={steward}")
    run("release", f"HASH={model_hash}")
    lock = (pack / "pack.lock").read_bytes()
    assert json.loads(lock) == original_lock
    golden = test_drive_run.golden()
    assert golden["passed"] == golden["of"] and not golden["failures"], golden
    assert (golden["held_out"]["passed"], golden["held_out"]["of"]) == (15, 16), golden
    assert "current already" in run("onboard", "REPEATS=1")
    assert json.loads(summary.read_text())["fixture_sha256"] == digest

    def answer():
        engine = Engine.serving(prof)
        try:
            result = engine.answer("Total retail revenue for July 2024", principal=f"analyst_enterprise.{client}@example.com",
                                   arm_name="graph")
            assert result["behaviour"] == "figure", result
            return Decimal(str(result["figure"]))
        finally:
            engine.close()

    old = answer()
    upload = tmp_path / "upload"
    shutil.copytree(pack, upload)
    csv_path = upload / "fixture/data/retail/monthly_revenue.csv"
    with csv_path.open(newline="") as stream:
        rows = list(csv.DictReader(stream))
    row = next(row for row in rows if row["month_start"] == "2024-07-01")
    row["total_revenue_usd"] = str(Decimal(row["total_revenue_usd"]) + Decimal("100.00"))
    with csv_path.open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]), lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
    second = run("onboard", f"FROM={upload}", "REPEATS=1")
    assert f"onboard: candidate {model_hash}" in second and "current already" not in second
    assert (pack / "pack.lock").read_bytes() == lock
    assert json.loads(summary.read_text())["fixture_sha256"] != digest
    graph = prof.at(model_hash).graph_path()
    changes = release.changes(graph)
    assert changes["estate"]["why"] == "fixture-changed" and changes["reloaded"] is True
    assert changes["lock"] == {"written": False, "changes": []}
    assert release.gate(graph)["passed"] is True and release.marked(graph, release.SIGNOFF) is None
    run("sign-off", f"HASH={model_hash}", f"PRINCIPAL={steward}")
    run("release", f"HASH={model_hash}")
    assert prof.model_hash() == model_hash
    assert answer() == old + Decimal("100.00")
