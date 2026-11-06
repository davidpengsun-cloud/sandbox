"""Release and rollback compare the actual served scratch graph with its own saved pre run."""
import json
import shutil
import subprocess
import sys

import pytest

from kge.adapters import released
from kge.eval import margin
from kge.graph import release
from tests.scratch import scratch_build, profile_of, running_as, write_registry


@pytest.fixture
def checked(tmp_path, monkeypatch):
    monkeypatch.setenv("MODEL_PROVIDER", "none")
    monkeypatch.delenv("STORE_URL", raising=False)
    build = scratch_build(tmp_path / "scratch")
    prof = profile_of(build)
    clients = tmp_path / "clients"
    shutil.copytree(prof.clients / prof.client, clients / prof.client)
    artifact = prof.artifacts / prof.client / prof.model_hash()
    source = artifact.resolve()
    artifact.unlink()
    shutil.copytree(source, artifact)
    for path in build.iterdir():
        if path.is_symlink() and path.name != "review":
            source = path.resolve()
            path.unlink()
            (shutil.copytree if source.is_dir() else shutil.copy2)(source, path)
    write_registry(build, clients=clients)
    prof = profile_of(build)
    with running_as(build):
        assert margin.main(["--repeats", "1", "--baseline", ""]) == 0
    return prof


def test_release_then_rollback_runs_post_on_each_and_preserves_the_saved_pre(checked):
    prof = checked
    folder = prof.graph_path().parent
    original = released.evaluation(folder, "pre")
    identities = []
    for _ in range(2):
        release.release(prof, prof.model_hash(), ungated=True)
        result = released.evaluation(folder, "post")
        assert result["passed"], result.get("differences")
        assert result["compared"]
        assert result["pre_run_id"] == original["manifest"]["run_id"]
        identities.append(result["identity"])
    assert identities[0] != identities[1]
    released.write_evaluation(folder, "pre", {"hash": "wrong"})
    release.release(prof, previous=True)
    restored = released.evaluation(folder, "post")
    assert restored["passed"]
    assert restored["identity"] == identities[0]
    assert released.evaluation(folder, "pre") == original
    assert restored["model_graded"]["status"] == "not applicable"


def test_a_doctored_served_answer_fails_loudly_without_undoing_the_release(checked, monkeypatch):
    real_run = subprocess.run
    doctor = '''import runpy
from kge.eval.stages import EvidenceArms
original = EvidenceArms.ask
def ask(self, *args, **kwargs):
    result = original(self, *args, **kwargs)
    if "figure" in result:
        result["figure"] = "doctored served figure"
    return result
EvidenceArms.ask = ask
from kge.eval.stages import main
raise SystemExit(main())
'''
    def run(args, **kwargs):
        assert args == [sys.executable, "-m", "kge.eval.stages"]
        return real_run([sys.executable, "-c", doctor], capture_output=True, **kwargs)
    monkeypatch.setattr(release.subprocess, "run", run)
    with pytest.raises(release.PostReleaseFailed, match="run kge rollback$"):
        release.release(checked, checked.model_hash(), ungated=True)
    result = released.evaluation(checked.graph_path().parent, "post")
    assert result["passed"] is False
    assert result["message"].endswith("run kge rollback")
    assert result["identity"] == checked.serve().identity()
    assert release.kept(checked)[0]["identity"] == result["identity"]
    assert any("deterministic" in row["fields"] for row in result["differences"])
    assert "doctored served figure" in json.dumps(result["records"])
