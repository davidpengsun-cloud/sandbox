"""The image job permits inspection; separately authorized local recovery uses CAS."""

import json
import os
import shutil
import subprocess
import sys

import pytest

from kge.adapters.objstore import open_store
from kge.adapters.shadowlog.foldlock import FoldBusy, FoldMutex
from kge.pipeline.settings import ROOT


@pytest.fixture
def job(tmp_path):
    app = tmp_path / "app"
    app.mkdir()
    shutil.copytree(ROOT / "kge", app / "kge", ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    shutil.copy2(ROOT / "entrypoint.sh", app / "entrypoint.sh")
    store = tmp_path / "store"
    shutil.copytree(ROOT / "clients", store / "clients")
    shutil.copytree(ROOT / "deploy", store / "deploy")
    for name in ("clients", "deploy", "build"):
        (app / name).mkdir(exist_ok=True)
    env = {k: v for k, v in os.environ.items() if k not in ("STORE_URL", "GRAPH_STORE", "PROFILE", "PROFILES")}
    env.update({"STORE_URL": str(store), "PROFILE": "meridian", "PYTHONPATH": str(app), "PY": sys.executable})

    def execute(*args):
        return subprocess.run(["sh", str(app / "entrypoint.sh"), "fold-lock", *args], cwd=app,
                              env=env, text=True, capture_output=True, timeout=30)

    def recover(info, *, confirm=True):
        args = ["--version", info["object_version"], "--object-digest", info["object_digest"]]
        if confirm:
            args.append("--holder-and-graph-stopped")
        return subprocess.run([sys.executable, "-m", "kge.adapters.shadowlog.foldlock", "recover",
                               "--client", "meridian", "--state-dir", str(app / "build"), *args],
                              cwd=app, env=env, text=True, capture_output=True, timeout=30)

    mutex = FoldMutex(open_store(str(store)), "shadow/meridian/_fold-lock.json")
    return execute, mutex, recover


def test_image_entrypoint_inspects_without_token_and_rejects_recovery(job):
    execute, mutex, recover = job
    token = mutex.acquire()
    result = execute("ACTION=inspect")
    assert result.returncode == 0, result.stderr
    info = json.loads(result.stdout)
    assert info["holder"]["pid"] and info["object_version"] and info["object_digest"]
    assert token not in result.stdout and token not in result.stderr
    base = ("ACTION=recover", f"OBJECT_VERSION={info['object_version']}", f"OBJECT_DIGEST={info['object_digest']}")
    assert execute(*base).returncode == 2
    assert execute(*base, "HOLDER_AND_GRAPH_STOPPED=no").returncode == 2
    assert execute(*base, "HOLDER_AND_GRAPH_STOPPED=yes", "CLIENT=another").returncode == 2
    assert mutex.inspect()[0]["token"] == token
    assert execute(*base, "HOLDER_AND_GRAPH_STOPPED=yes").returncode == 2
    assert recover(info, confirm=False).returncode == 2
    assert mutex.inspect()[0]["token"] == token
    assert recover(info).returncode == 0
    assert mutex.inspect()[0] is None


def test_recovery_rejects_stale_or_corrupt_digest_without_exposing_raw_lock(job):
    execute, mutex, recover = job
    token = mutex.acquire()
    old = json.loads(execute("ACTION=inspect").stdout)
    mutex.release(token)
    newer = mutex.acquire()
    result = recover(old)
    assert result.returncode == 2 and mutex.inspect()[0]["token"] == newer
    mutex.release(newer)
    mutex.store.put(mutex.key, b"PRIVATE_CORRUPT_CONTENT")
    result = execute("ACTION=inspect")
    assert result.returncode == 0 and "PRIVATE_CORRUPT_CONTENT" not in result.stdout + result.stderr
    info = json.loads(result.stdout)
    assert info["corrupt"] and info["holder"] is None
    bad = recover({**info, "object_digest": "a" * 64})
    assert bad.returncode == 2
    good = recover(info)
    assert good.returncode == 0 and mutex.inspect()[0] is None


def test_recovery_rechecks_generation_on_conditional_write(job, monkeypatch):
    _, mutex, _ = job
    mutex.acquire()
    observed = mutex.inspect_observed()
    original_put = mutex.store.put_version

    def raced_put(key, data, version):
        mutex.store.put(key, b'{"replacement": true}')
        return original_put(key, data, version)

    monkeypatch.setattr(mutex.store, "put_version", raced_put)
    with pytest.raises(FoldBusy, match="changed; inspect it again"):
        mutex.recover_observed(observed["object_digest"], observed["object_version"], holder_stopped=True)
    assert mutex.store.get(mutex.key) == b'{"replacement": true}'


@pytest.mark.parametrize("args", [(), ("ACTION=inspect", "OBJECT_VERSION=x"), ("ACTION=inspect", "ACTION=recover"),
                                  ("ACTION=recover", "TOKEN=secret"), ("ACTION=inspect", "STORE_URL=/tmp/other")])
def test_recovery_job_rejects_unrecognized_ambiguous_or_store_override_arguments(job, args):
    execute, _, _ = job
    assert execute(*args).returncode == 2
