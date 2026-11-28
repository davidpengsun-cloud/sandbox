"""Rollback removes a discarded unit through the pack store's prefix contract."""
import json
from types import SimpleNamespace
from urllib.parse import parse_qs, unquote, urlsplit

import pytest

from kge import packstore
from kge.adapters.objstore import open_store
from kge.adapters.objstore.gcs import GcsStore
from kge.compiler import inputs
from kge.graph import history


@pytest.mark.parametrize("backend", ["folder", "gcs"])
def test_restore_removes_whole_unit_and_keeps_neighbor(tmp_path, monkeypatch, backend):
    held = {"clients/sample/units/leasing/draft.yaml": b"draft",
            "clients/sample/units/leasing/nested/history.jsonl": b"audit",
            "clients/sample/units/leasing2/draft.yaml": b"neighbor"}
    calls = []
    if backend == "folder":
        remote = open_store(str(tmp_path / "remote"))
        for key, data in held.items():
            remote.put(key, data)
    else:
        remote = GcsStore("gs://test-store", "test-store")

        def call(method, url, key, *args, **kwargs):
            calls.append((method, key))
            parsed = urlsplit(url)
            if method == "GET" and parsed.path.endswith("/o"):
                prefix = parse_qs(parsed.query)["prefix"][0]
                return 200, json.dumps({"items": [{"name": name} for name in held
                                                 if name.startswith(prefix)]}).encode()
            assert method == "DELETE"
            held.pop(unquote(parsed.path.split("/o/")[1]), None)
            return 204, b""

        monkeypatch.setattr(remote, "call", call)
    store = packstore.Store(tmp_path / "clients/sample", remote=remote, prefix="clients/sample/")
    prof = SimpleNamespace(client="sample", clients=tmp_path / "clients",
                           estate=lambda: tmp_path / "estate", run_dir=lambda: tmp_path / "run")
    monkeypatch.setattr(history.packstore, "for_profile", lambda _: store)
    monkeypatch.setattr(history, "_input_names", lambda *args, **kwargs: {"units/leasing/draft.yaml"})
    monkeypatch.setattr(inputs, "read_declared", lambda _: None)
    history.restore_release(prof, {"identity": {"hash": "earlier", "release": "first"}},
                            checked=({"estate.json": b"{}"}, None))
    assert remote.list("clients/sample/units/leasing/") == []
    assert remote.list("clients/sample/units/leasing2/") == ["clients/sample/units/leasing2/draft.yaml"]
    if backend == "gcs":
        assert ("DELETE", "clients/sample/units/leasing/nested/history.jsonl") in calls
        assert ("DELETE", "clients/sample/units/leasing") not in calls
