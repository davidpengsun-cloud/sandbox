"""A new client's table-backed people complete its first release without a served artifact."""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

from kge.adapters import appstate, released
from kge.estate import onboard
from kge.graph import release
from kge.pipeline.profile import resolve


DEMO = Path(__file__).resolve().parents[2]


def test_new_client_people_complete_the_first_release(tmp_path, monkeypatch):
    for key in ("STORE_URL", "LANDING_URL", "HASH", "PREVIOUS", "BASELINE", "MODEL_HIGH_PROVIDER",
                "MODEL_MEDIUM_PROVIDER", "MODEL_LOW_PROVIDER", "MODEL_DEFAULT_PROVIDER"):
        monkeypatch.delenv(key, raising=False)
    for key, value in {"WAREHOUSE_BACKEND": "duckdb", "GRAPH_STORE": "ladybug", "RETRIEVAL_BACKEND": "graph",
                       "APP_STATE_BACKEND": "files", "MODEL_PROVIDER": "none", "TAGGER_PROVIDER": "rules",
                       "PROFILE": "newco"}.items():
        monkeypatch.setenv(key, value)
    (tmp_path / "estate").mkdir()
    (tmp_path / "deploy").mkdir()
    registry = tmp_path / "deploy/profiles.yaml"
    shutil.copyfile(DEMO / "deploy/profiles.yaml", registry)
    shutil.copyfile(DEMO / "meta-model.yaml", tmp_path / "meta-model.yaml")
    monkeypatch.setattr(onboard.clients, "ROOT", tmp_path)
    monkeypatch.setattr(onboard.source, "ROOT", tmp_path)
    monkeypatch.setenv("PROFILES", str(registry))
    made = onboard.client("newco", "New Co", ddl_file=DEMO / "examples/brackenridge/schema.sql",
                          rows="invented", as_="platform")
    prof = resolve()
    assert prof.model_hash() is None
    people = appstate.access_state(prof).access_grants()
    assert {row["username"] for row in people} >= {
        "business_team.newco@example.com", "steward.newco@example.com"}

    def run(*args):
        done = subprocess.run([sys.executable, "-m", "kge.cli", *args], cwd=DEMO, env=dict(os.environ),
                              capture_output=True, text=True, timeout=600)
        assert done.returncode == 0, done.stdout + done.stderr
        return done.stdout

    for unit in made["units"]:
        run("accept", f"UNIT={unit}", "ITEM=all", "PRINCIPAL=business_team.newco@example.com")
        run("certify-draft", f"UNIT={unit}", "ITEM=all", "PRINCIPAL=steward.newco@example.com")
    assert prof.model_hash() is None
    checked = run("onboard", "REPEATS=1")
    model_hash = next(line.removeprefix("onboard: candidate ") for line in checked.splitlines()
                      if line.startswith("onboard: candidate "))
    assert prof.model_hash() is None
    folder = prof.at(model_hash).graph_path().parent
    assert released.evaluation(folder, "pre")["passed"] is True
    run("sign-off", f"HASH={model_hash}", "PRINCIPAL=steward.newco@example.com")
    assert release.marked(prof.at(model_hash).graph_path(), release.SIGNOFF)["principal"] == "steward.newco@example.com"
    run("release", f"HASH={model_hash}")
    assert prof.model_hash() == model_hash
    post = released.post_evaluation(prof.serve())
    assert post["passed"] is True and post["compared"] is True
    assert post["identity"] == prof.serve().identity()
    assert len(release.served(prof.store / prof.client)) == 1
