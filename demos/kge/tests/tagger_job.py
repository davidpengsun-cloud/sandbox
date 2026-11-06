"""Run the shipped entry script and CLI in an isolated image-shaped folder."""
import os
from pathlib import Path
import shutil
import subprocess
import sys

from kge.eval.tagger import ROOT
from tests.unit.test_image import kge_path


def image(tmp_path: Path, built: bool = False):
    app, store = tmp_path / "app", tmp_path / "store"
    app.mkdir()
    shutil.copy(ROOT / "entrypoint.sh", app)
    shutil.copy(ROOT / "meta-model.yaml", app)
    shutil.copytree(ROOT / "standards", app / "standards")
    shutil.copytree(ROOT / "kge", app / "kge", ignore=shutil.ignore_patterns("__pycache__"))
    data = app / "examples/tagger-eval"
    data.mkdir(parents=True)
    shutil.copy(ROOT / "examples/tagger-eval/set.json", data)
    shutil.copy(ROOT / "examples/tagger-eval/targets.json", data)
    shutil.copytree(ROOT / "deploy", store / "deploy")
    shutil.copytree(ROOT / "clients", store / "clients")
    if built:
        from kge.pipeline import objstore
        from kge.pipeline.profile import resolve
        from kge.adapters.objstore import open_store
        profile = resolve("meridian")
        paths = objstore.work_paths(ROOT, objstore.local_files(profile))
        local = open_store(str(store))
        local.put(objstore.work_key(profile), objstore.tarball(ROOT, paths))
        objstore.push(profile.landing / profile.client, local, f"landing/{profile.client}/", links=("current",))
    env = {key: value for key, value in os.environ.items()
           if not key.startswith(("MODEL_", "TAGGER_", "VERTEX_", "KGE_"))
           and key not in {"PROFILE", "PROFILES", "STORE_URL", "LANDING_URL", "TMP_URL", "GRAPH_STORE",
                           "WAREHOUSE_BACKEND", "RETRIEVAL_BACKEND", "APP_STATE_BACKEND", "EMBEDDING_PROVIDER"}}
    env.update(PATH=kge_path(tmp_path / "bin"), PY=sys.executable, PYTHONPATH=str(app),
               MODEL_PROVIDER="mock", STORE_URL=str(store), PROFILE="meridian", GRAPH_STORE="ladybug",
               EMBEDDING_PROVIDER="hash", WAREHOUSE_BACKEND="duckdb", APP_STATE_BACKEND="files")

    for tier in ("LOW", "MEDIUM", "HIGH"):
        env[f"MODEL_{tier}_PROVIDER"] = "mock"
        env[f"MODEL_{tier}_NAME"] = "mock"

    def run(*args):
        return subprocess.run(["sh", str(app / "entrypoint.sh"), "tagger-eval", "REPEATS=1", *args],
                              cwd=app, env=env, text=True, capture_output=True)
    return run, store, app
