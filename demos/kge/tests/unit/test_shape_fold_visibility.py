"""Candidate fold diagnostics cross isolated job and service workspaces."""

import json
import os
import subprocess
import sys

import pytest

from kge.adapters.objstore import open_store
from kge.adapters.shadowlog.objstore import StoreShadowLog
from kge.adapters.shadowlog.foldlock import fold_mutex
from kge.eval import shapefold
from kge.pipeline.profile import Profile
from kge.services.onboard import service
from kge.ports import StoreConflict


def test_folder_store_real_fold_is_visible_and_survives_served_fold(tmp_path, monkeypatch):
    store = tmp_path / "shared"
    job = tmp_path / "job"
    server = tmp_path / "service"
    model_hash = "a" * 64
    env = {**os.environ, "STORE_URL": str(store), "GRAPH_STORE": "ladybug"}
    monkeypatch.setenv("STORE_URL", str(store))
    monkeypatch.setenv("GRAPH_STORE", "ladybug")
    program = r'''
import json, sys
from pathlib import Path
from kge.adapters.graphstore import open_db, connect
from kge.eval import shapefold
root, h = Path(sys.argv[1]), sys.argv[2]
class Profile:
    client = "synthetic"
    artifacts = root / "artifacts"
    def run_dir(self): return root / "run"
p = Profile()
history = p.artifacts / p.client / "versions.jsonl"
history.parent.mkdir(parents=True)
history.write_text(json.dumps({"release": "r1", "released_at": "2026-01-01T00:00:00Z"}) + "\n")
db = open_db(root / "graph.ladybug")
conn = connect(db)
conn.execute("CREATE NODE TABLE ShapeStatus(id STRING, shape STRING, release_event STRING, counters STRING, PRIMARY KEY(id))")
conn.execute("CREATE NODE TABLE Shape(id STRING, PRIMARY KEY(id))")
result = shapefold.run(p, model_hash=h, conn=conn)
assert result.written
served = shapefold.run(p, conn=conn)
assert served.written
assert served.observations == 0 and served.malformed == 0
conn.close()
db.close()
'''
    job.mkdir()
    subprocess.run([sys.executable, "-c", program, str(job), model_hash], env=env, check=True)
    prof = Profile("synthetic", "synthetic", server / "clients", server / "artifacts", server / "store",
                   server / "landing", server / "warehouses", server / "identities", None, server / "profiles.yaml")
    prof.at(model_hash).graph_path().parent.mkdir(parents=True)
    other_hash = "b" * 64
    prof.at(other_hash).graph_path().parent.mkdir(parents=True)
    result = service.margin_report(prof, model_hash)
    assert result["fold"]["written"] is True
    assert result["fold"]["target"] == "candidate"
    assert result["fold"]["hash"] == model_hash
    assert service.margin_report(prof, other_hash)["fold"] is None
    assert not prof.run_dir().exists()
    remote = open_store(str(store), private=True)
    log = StoreShadowLog(remote, "synthetic")
    assert log.releases() == []
    assert log.read("fold-reports") == []
    key = f"fold-reports/synthetic/{model_hash}.json"
    saved = result["fold"]
    remote.put(key, json.dumps({**saved, "hash": other_hash}).encode())
    assert shapefold.candidate_report(prof, model_hash) is None
    remote.put(key, json.dumps({**saved, "question": "private"}).encode())
    assert shapefold.candidate_report(prof, model_hash) is None
    remote.put(key, json.dumps({**saved, "written": False, "skipped": ["private"]}).encode())
    assert shapefold.candidate_report(prof, model_hash) is None


def test_stale_finalizer_cannot_replace_a_newer_candidate_report(tmp_path, monkeypatch):
    monkeypatch.setenv("STORE_URL", str(tmp_path / "shared"))
    model_hash = "a" * 64

    class Profile:
        client = "synthetic"

        def run_dir(self):
            return tmp_path / "run"

    def pending(reason):
        return shapefold.FoldReport("2026-01-01T00:00:00Z", "candidate", model_hash,
                                    0, 0, 0, 0, 0, 0, 0, False, reason)

    prof = Profile()
    shapefold._report(prof, pending("the release history is empty"))
    store, key = shapefold._candidate_store(prof, model_hash)
    _, version = store.get_version(key)
    shapefold._report(prof, pending("fold is in progress; mutex release is pending"))
    with pytest.raises(StoreConflict):
        store.put_version(key, b'{"written": true}', version)
    assert shapefold.candidate_report(prof, model_hash)["skipped"] == "fold is in progress; mutex release is pending"


def test_candidate_version_read_failure_is_safe_and_releases_mutex(tmp_path, monkeypatch):
    monkeypatch.setenv("STORE_URL", str(tmp_path / "shared"))
    monkeypatch.setenv("GRAPH_STORE", "ladybug")

    class Profile:
        client = "synthetic"

        def run_dir(self):
            return tmp_path / "run"

    original = shapefold._candidate_store
    reads = 0

    class FailingStore:
        def __init__(self, wrapped):
            self.wrapped = wrapped

        def get_version(self, key):
            nonlocal reads
            reads += 1
            if reads == 2:
                raise OSError("SECRET_FILE_PATH")
            return self.wrapped.get_version(key)

        def put_version(self, *args):
            return self.wrapped.put_version(*args)

    def candidate_store(prof, model_hash):
        store, key = original(prof, model_hash)
        return FailingStore(store), key

    monkeypatch.setattr(shapefold, "_candidate_store", candidate_store)
    prof = Profile()
    with pytest.raises(shapefold.NotFolded, match="fold report could not be read") as failure:
        shapefold.run(prof, model_hash="a" * 64)
    assert "SECRET_FILE_PATH" not in str(failure.value)
    assert failure.value.__suppress_context__
    assert fold_mutex(prof.run_dir(), prof.client).inspect()[0] is None
