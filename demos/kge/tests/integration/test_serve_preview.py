"""A candidate preview is pinned and never changes the released pointer."""
import json
import os
import subprocess
import sys
from pathlib import Path

from kge.pipeline.profile import resolve

DEMO = Path(__file__).resolve().parents[2]


def run(model_hash: str):
    env = {**os.environ, "PROFILE": "meridian", "SERVE_HASH": model_hash, "MODEL_PROVIDER": "none"}
    return subprocess.run([sys.executable, "-c", "import json; from kge.services.quality import routes as q; from kge.services.tools import routes as t; print(json.dumps([t.profile(), q.golden()]))"],
                          cwd=DEMO, env=env, text=True, capture_output=True)


def test_preview_refuses_an_unloaded_hash_and_leaves_current_unchanged():
    prof = resolve("meridian"); current = prof.current().resolve()
    refused = run("0" * 64)
    assert refused.returncode != 0 and "candidate is not loaded" in refused.stderr
    assert prof.current().resolve() == current


def test_pinning_the_released_artifact_labels_it_released():
    prof = resolve("meridian"); current = prof.model_hash(); before = prof.current().resolve()
    shown = run(current)
    assert shown.returncode == 0, shown.stderr
    profile, golden = json.loads(shown.stdout.splitlines()[-1])
    assert profile["preview"]["banner"] == f"Artifact {current}, released"
    assert profile["preview"]["released"] is True
    assert golden["preview"]["hash"] == current
    assert prof.current().resolve() == before


def test_preview_refuses_a_path_as_a_one_line_hash_error():
    refused = run("../meridian")
    assert refused.returncode != 0
    assert refused.stderr.count("\n") == 1
    assert "must be a 64-character lower-case hexadecimal hash" in refused.stderr
    assert "Traceback" not in refused.stderr
