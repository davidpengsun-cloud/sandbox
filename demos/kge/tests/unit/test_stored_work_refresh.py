"""A stored work snapshot invalidates obsolete approvals without mirroring app-owned state."""
import io
import json
import tarfile

import pytest

from kge.adapters.objstore import open_store
from kge.adapters.graphstore import graph_path
from kge.adapters.graphstore.bigquery import read_pointer
from kge.graph import release
from kge.pipeline import objstore
from tests.unit.test_release_record_unpack import profile


HASH = "ab" * 32
OTHER = "cd" * 32


def archive(entries):
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as tar:
        for name, body in entries.items():
            data = body if isinstance(body, bytes) else json.dumps(body).encode()
            member = tarfile.TarInfo(name)
            member.size = len(data)
            tar.addfile(member, io.BytesIO(data))
    return buffer.getvalue()


@pytest.fixture
def stored(tmp_path, monkeypatch):
    monkeypatch.setenv("GRAPH_STORE", "ladybug")
    monkeypatch.setenv("WAREHOUSE_BACKEND", "duckdb")
    app = tmp_path / "app"
    prof = profile(app)
    root = prof.store / prof.client / HASH
    root.mkdir(parents=True)
    store = open_store(str(tmp_path / "objects"))
    return app, prof, root, store


@pytest.mark.parametrize("backend", ["ladybug", "bigquery"])
def test_same_hash_work_refresh_removes_old_signoff_after_new_gate(stored, monkeypatch, backend):
    app, prof, root, store = stored
    monkeypatch.setenv("GRAPH_STORE", backend)
    prefix = root.relative_to(app).as_posix()
    graph = graph_path(root)

    def graph_payload(generation):
        if backend == "ladybug":
            return generation.encode()
        graph_prefix = f"g{HASH[:16]}_{generation}_"
        return {"format": 1, "store": "bigquery", "dataset": "kge_meridian_graph", "prefix": graph_prefix,
                "graph": graph_prefix + "graph", "schema": {"mutable": [], "nodes": [], "edges": []}}

    approved = {f"{prefix}/{graph.name}": graph_payload("1a1a1a1a"),
                f"{prefix}/LOADED": {backend: {"model_hash": HASH}},
                f"{prefix}/GATE": {backend: {"passed": True, "run_id": "old-check"}},
                f"{prefix}/SIGNOFF": {backend: {"principal": "steward_glossary.meridian@example.com",
                                                "run_id": "old-check", "at": "2026-11-01"}}}
    store.put(objstore.work_key(prof), archive(approved))
    objstore.pull_work(store, app, prof)
    assert release.state(prof, HASH)["signed_off"]["run_id"] == "old-check"
    refreshed = {f"{prefix}/{graph.name}": graph_payload("2b2b2b2b"),
                 f"{prefix}/LOADED": {backend: {"model_hash": HASH}},
                 f"{prefix}/GATE": {backend: {"passed": True, "run_id": "new-check"}}}
    store.put(objstore.work_key(prof), archive(refreshed))
    objstore.pull_work(store, app, prof)
    status = release.state(prof, HASH)
    assert status["loaded"] is True
    assert status["gate"] == {"passed": True, "run_id": "new-check"}
    assert status["signed_off"] is None
    if backend == "bigquery":
        assert read_pointer(graph) == graph_payload("2b2b2b2b")
    else:
        assert graph.read_bytes() == graph_payload("2b2b2b2b")


def test_work_refresh_preserves_other_backend_approvals_carried_by_the_snapshot(stored):
    app, prof, root, store = stored
    prefix = root.relative_to(app).as_posix()
    other_signoff = {"principal": "steward_glossary.meridian@example.com", "run_id": "other-check"}
    (root / "SIGNOFF").write_text(json.dumps({"ladybug": {"run_id": "obsolete"}, "bigquery": other_signoff}))
    snapshot = {f"{prefix}/graph.ladybug": b"reloaded graph",
                f"{prefix}/GATE": {"ladybug": {"passed": True, "run_id": "new-check"}},
                f"{prefix}/SIGNOFF": {"bigquery": other_signoff}}
    store.put(objstore.work_key(prof), archive(snapshot))
    objstore.pull_work(store, app, prof)
    assert release.state(prof, HASH)["signed_off"] is None
    assert json.loads((root / "SIGNOFF").read_text()) == {"bigquery": other_signoff}


def test_work_refresh_only_clears_absent_markers_in_this_clients_snapshot_folders(stored):
    app, prof, root, store = stored
    for name in objstore.CANDIDATE_MARKERS:
        (root / name).write_bytes(b"old marker")
    preserved = [root / "release.json", root / "eval-post.json", root / "overlay.jsonl",
                 root / "tokens.json", root.parent / OTHER / "SIGNOFF",
                 prof.store / "other-client" / HASH / "SIGNOFF"]
    for path in preserved:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"keep")
    prefix = root.relative_to(app).as_posix()
    store.put(objstore.work_key(prof), archive({f"{prefix}/graph.ladybug": b"graph",
                                               f"{prefix}/GATE": b"new gate"}))
    objstore.pull_work(store, app, prof)
    assert (root / "GATE").read_bytes() == b"new gate"
    assert all(not (root / name).exists() for name in objstore.CANDIDATE_MARKERS - {"GATE"})
    assert all(path.read_bytes() == b"keep" for path in preserved)


@pytest.mark.parametrize("bad_name", ["../outside", "/absolute", "clients/pack.yaml"])
def test_invalid_work_archive_keeps_existing_markers(stored, bad_name):
    app, prof, root, store = stored
    (root / "SIGNOFF").write_bytes(b"keep sign-off")
    prefix = root.relative_to(app).as_posix()
    store.put(objstore.work_key(prof), archive({f"{prefix}/graph.ladybug": b"graph", bad_name: b"bad"}))
    with pytest.raises(objstore.Refused):
        objstore.pull_work(store, app, prof)
    assert (root / "SIGNOFF").read_bytes() == b"keep sign-off"
    assert not (root / "graph.ladybug").exists()


def test_release_archive_does_not_remove_candidate_markers(stored):
    app, prof, root, store = stored
    (root / "SIGNOFF").write_bytes(b"keep sign-off")
    prefix = root.relative_to(app).as_posix()
    objstore.unpack(archive({f"{prefix}/graph.ladybug": b"graph"}), app)
    assert (root / "SIGNOFF").read_bytes() == b"keep sign-off"
