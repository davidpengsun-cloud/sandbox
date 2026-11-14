"""Stored ledger rollback crosses the real request, CLI, release and publishing paths with a local store and fake warehouse."""
from __future__ import annotations

import contextlib
import hashlib
import io
import json
import subprocess
import sys
import tarfile
from pathlib import Path

import pytest

from kge import cli
from kge.adapters.model import DIM
from kge.adapters.objstore import open_store
from kge.adapters.warehouse import estate_of
from kge.compiler import pack as packs
from kge.pipeline.registry import setup_client
from kge.adapters.appstate import access_state
from kge.graph import release
from kge.pipeline import objstore
from tests import bigquery_fake


DEMO = Path(__file__).resolve().parents[2]


def artifact(prof, label):
    """A checked artifact with real file digests and pack metadata, without building a graph."""
    pack = packs.read(prof.clients, prof.client, lock=True)
    files = {"ddl/kuzu.cypher": b"// test graph\n", "graph-model.yaml": f"label: {label}\n".encode(),
             "access/roles.json": json.dumps({"roles": pack.data["access"]["roles"]}).encode(),
             "data/nodes/Unit.csv": b"id\nretail\n"}
    digests = {name: hashlib.sha256(data).hexdigest() for name, data in files.items()}
    model_hash = hashlib.sha256("".join(f"{digests[name]}  {name}\n" for name in sorted(digests)).encode()).hexdigest()
    folder = prof.artifacts / prof.client / model_hash
    for name, data in files.items():
        path = folder / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    manifest = {"format": "graph-model/v1", "client": prof.client, "model_hash": model_hash,
                "meta_model_version": "1.2.0", "params": {"embedding_dim": DIM},
                "pack": {"id": prof.client, "version": "1.5.0", "hash": pack.hash},
                "files": digests, "load_order": []}
    (folder / "manifest.json").write_text(json.dumps(manifest))
    graph = prof.at(model_hash).graph_path()
    graph.parent.mkdir(parents=True, exist_ok=True)
    graph.write_text("held graph")
    return model_hash


@pytest.mark.parametrize("same_hash", [False, True], ids=["previous-model", "previous-warehouse"])
def test_stored_previous_request_publishes_the_ledger_identity_not_the_candidate(tmp_path, monkeypatch, same_hash):
    for key, value in {"WAREHOUSE_BACKEND": "bigquery", "GRAPH_STORE": "ladybug", "RETRIEVAL_BACKEND": "graph",
                       "APP_STATE_BACKEND": "files", "BIGQUERY_PROJECT": bigquery_fake.PROJECT,
                       "MODEL_PROVIDER": "none", "STORE_URL": str(tmp_path / "store"), "PROFILE": "meridian",
                       "CLIENT": "meridian", "ASKER": "analyst_enterprise"}.items():
        monkeypatch.setenv(key, value)
    for key in ("HASH", "PREVIOUS", "LANDING_URL", "BASELINE"):
        monkeypatch.delenv(key, raising=False)
    fake = bigquery_fake.install(monkeypatch)
    app = tmp_path / "app"
    app.mkdir()
    monkeypatch.chdir(app)
    monkeypatch.setenv("PROFILES", str(app / "deploy/profiles.yaml"))
    setup_client()
    store = open_store(str(tmp_path / "store"))
    for name in ("pack.yaml", "pack.lock", "catalogue.yaml"):
        store.put(f"clients/meridian/{name}", (DEMO / "clients/meridian" / name).read_bytes())
    prof = objstore.pull_config(store, app)
    access_state(prof).access_put({"username": "sam@example.com", "name": "Sam", "roles": ["steward_glossary"]},
                                  by="operator@example.com", via="job")

    def load_version(model_hash, version):
        estate = estate_of(prof)
        session = estate.stage("retail", ["CREATE TABLE sales (amount INTEGER)"], version)
        session.load("sales", ["amount"], [[str(version * 100)]])
        estate.publish("retail", version)
        session.close()
        prof.estate().mkdir(parents=True, exist_ok=True)
        (prof.estate() / "summary.json").write_text(json.dumps({"fixture_units": ["retail"], "version": version}))
        graph = prof.at(model_hash).graph_path()
        release.put(graph, release.LOADED, {"client": prof.client, "model_hash": model_hash,
                                           "warehouses": {"retail": version}})

    previous = artifact(prof, "previous")
    load_version(previous, 1)
    release.release(prof, previous, ungated=True)
    objstore.after("release", app)
    first_archive = store.get(f"releases/meridian/{previous}.tar.gz")
    first_key = release.kept(prof)[0]["tarball"]
    store.put(first_key, first_archive)
    expected = prof.serve().identity()
    current = previous if same_hash else artifact(prof, "current")
    load_version(current, 2)
    release.release(prof, current, ungated=True)
    objstore.after("release", app)
    store.put(release.kept(prof)[0]["tarball"], store.get(f"releases/meridian/{current}.tar.gz"))
    candidate = artifact(prof, "unchecked-candidate")
    load_version(candidate, 3)
    prof.run_dir().mkdir(parents=True, exist_ok=True)
    (prof.run_dir() / objstore.ONBOARD_RUN).write_text(json.dumps({"started_at": "2026-10-30T11:00:00Z", "outcome": "passed"}))
    objstore.after("onboard", app)
    assert prof.model_hash() == current
    assert prof.serve().identity()["warehouses"] == {"retail": 2}
    request_key = "clients/meridian/onboard/request.json"
    store.put(request_key, json.dumps({"id": "20261030T120000Z-abcdef", "command": "release", "previous": True,
                                      "hash": None, "principal": "sam@example.com", "timestamp": "2026-10-30T12:00:00Z"}).encode())
    calls, published, switched = [], [], []
    real_publish = objstore.publish
    real_swap = release.swap

    def swap(target_prof, model_hash):
        switched.append(model_hash)
        real_swap(target_prof, model_hash)

    def publish(target_store, target_app, target_prof):
        published.append(target_prof.model_hash())
        return real_publish(target_store, target_app, target_prof)

    def execute(args, **kwargs):
        """Keep the fake shared across process boundaries while running both real CLI dispatchers and store hooks."""
        calls.append(args)
        output, errors = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
            if args[:2] == ["sh", str(app / "entrypoint.sh")]:
                assert args[2:] == ["release", "PREVIOUS=yes"]
                objstore.before("release", app)
                code = cli.main(args[2:])
                if code == 0:
                    objstore.after("release", app)
            else:
                assert args[:3] == [sys.executable, "-m", "kge.graph.release"]
                assert args[3:] == ["--previous", "--defer-retention"]
                code = release.main(args[3:])
        return subprocess.CompletedProcess(args, code, output.getvalue(), errors.getvalue())

    monkeypatch.setattr(objstore, "publish", publish)
    monkeypatch.setattr(release, "swap", swap)
    monkeypatch.setattr(objstore.subprocess, "run", execute)
    objstore.onboard_request(app)
    assert len(calls) == 2 and published == switched == [previous]
    assert store.get("releases/meridian/current") == f"{previous}\n".encode()
    assert objstore.released_identity(store, prof, previous) == expected
    assert prof.model_hash() == previous and prof.serve().identity() == expected
    assert store.get(first_key) == first_archive
    job = json.loads(store.get("clients/meridian/onboard/job.json"))
    assert job["state"] == "done" and job["hash"] == previous and job["previous"] is True
    assert job.get("changes") is None and job["checked_hash"] is None and job["checks_id"] is None
    assert store.get(request_key) == b""
    assert store.get(f"releases/meridian/{candidate}.tar.gz") is None
    assert first_key == f"releases/meridian/{previous}/{expected['release']}.tar.gz"
    with tarfile.open(fileobj=io.BytesIO(store.get(first_key)), mode="r:gz") as archive:
        saved = json.load(archive.extractfile(f"build/stores/meridian/{previous}/release.json"))
        assert saved["warehouses"] == {"retail": 1}
        assert not any(candidate in name for name in archive.getnames())
    assert {"retail_v1_sales", "retail_v2_sales", "retail_v3_sales"} <= set(fake.held())
