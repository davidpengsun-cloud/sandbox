"""The object store (issue #56): its backends (kge/adapters/objstore/), the commands kge/pipeline/objstore.py runs on it,
and the entry script running each command on it.

    .venv/bin/python -m pytest tests/unit/test_objstore.py -q

A store is a local folder or a GCS bucket. GCS is a stub HTTP server here, speaking the JSON API the store uses (paged
lists, media uploads and downloads, a bucket made on the emulator's first put); KGE_GCS_EMULATOR=http://127.0.0.1:<port>
runs the same tests against a real emulator (fake-gcs-server) instead, but for the ones that need the stub's hand in it
(failures to retry, the headers it was sent). The entry script runs beside a kge that does what each command would
leave behind, from a folder of its own holding a copy of the entry script, kge/pipeline/, kge/adapters/ and kge/ports.py,
on a local store: so no test here touches the demo's own clients/, deploy/ or build/.
"""
from __future__ import annotations

import io
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tarfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from kge import packstore
from kge.adapters import objstore as adapter
from kge.adapters.objstore import StoreError, open_store
from kge.adapters.objstore import gcs as gcs_backend
from kge.adapters.objstore.gcs import GcsStore
from kge.adapters.objstore.folder import LocalStore
from kge.pipeline import objstore
from kge.pipeline.registry import setup_client
from kge.adapters.appstate import access_state
from kge.pipeline.objstore import Refused, pull, push, tarball, unpack, work_paths
from kge.pipeline.profile import read
from tests.gcs_stub import serve

DEMO = Path(__file__).resolve().parents[2]
PEOPLE = "ann@example.com:analyst sam@example.com:steward"
ONBOARD_RUN_ID = datetime(2026, 10, 31, 12, tzinfo=timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")


def test_packstore_remote_generation_conflict_is_a_reload_refusal(tmp_path):
    """Remote stores deliberately do not take the local flock: the object's generation guards the write instead."""
    class Remote:
        def get_version(self, key):
            return b"old", "17"

        def put_version(self, key, data, version):
            assert (key, data, version) == ("clients/acme/units/u/draft.yaml", b"new", "17")
            raise StoreError("cannot put: object changed concurrently")

    store = packstore.Store(tmp_path / "mirror", tmp_path / "run", Remote(), "clients/acme/")
    with store.lock():
        with pytest.raises(StoreError, match="draft.yaml changed by someone else; reload"):
            store.write("units/u/draft.yaml", b"new")


def released_role_store(tmp_path, released_roles):
    """A minimum current release plus protected deployment identities."""
    store = open_store(str(tmp_path / "store"))
    model_hash = "a" * 64
    roles_name = f"build/artifacts/meridian/{model_hash}/access/roles.json"
    archive = io.BytesIO()
    payload = json.dumps({"roles": released_roles}).encode()
    with tarfile.open(fileobj=archive, mode="w:gz") as tar:
        member = tarfile.TarInfo(roles_name); member.size = len(payload)
        tar.addfile(member, io.BytesIO(payload))
    store.put("releases/meridian/current", f"{model_hash}\n".encode())
    store.put(f"releases/meridian/{model_hash}.tar.gz", archive.getvalue())
    return store


def seed_people(prof):
    """Install fixture grants explicitly; role checks never read a deployment seed."""
    if not hasattr(prof, "store"):
        prof.store = prof.identities.parent / "stores"
    if not prof.identities or not prof.identities.is_file():
        return
    state = access_state(prof)
    if state.access_grants():
        return
    for bound in yaml.safe_load(prof.identities.read_text())["principals"]:
        state.access_put({"username": bound["principal"], "name": bound["principal"], "roles": [bound["role"]]},
                         by="operator@example.com", via="migration")


def loaded_candidate(prof, root, role_templates, model_hash="a" * 64):
    """Give a request the immutable role templates and roster its candidate load captured."""
    if not hasattr(prof, "at"):
        prof.store, prof.artifacts = root / "stores", root / "artifacts"
        prof.at = lambda _hash: prof
    folder = prof.store / prof.client / model_hash
    folder.mkdir(parents=True, exist_ok=True)
    seed_people(prof)
    path = prof.artifacts / prof.client / model_hash / "access/roles.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"roles": role_templates}))


@pytest.mark.parametrize("command", ["onboard", "improve", "sign-off", "release"])
@pytest.mark.parametrize("principal,allowed", [
    ("knowledge_engineer", {"onboard", "improve"}),
    ("business_team", set()),
    ("steward_glossary", {"sign-off", "release"}),
    ("analyst_enterprise", set()),
    ("unknown", set()),
])
def test_job_authorises_building_from_release_and_certification_from_candidate(monkeypatch, tmp_path, command, principal, allowed):
    pack = yaml.safe_load((DEMO / "clients/meridian/pack.yaml").read_text())
    store = released_role_store(tmp_path, pack["access"]["roles"])
    store.put("deploy/meridian/identities.yaml", (DEMO / "deploy/meridian/identities.yaml").read_bytes())
    store.put("deploy/profiles.yaml", (DEMO / "deploy/profiles.yaml").read_bytes())
    candidate_roles = pack["access"]["roles"]
    principal = f"{principal}.meridian@example.com"
    pack["access"]["roles"] = [{"id": principal, "processes": ["certification", "building", "improving"]}]
    store.put("clients/meridian/pack.yaml", yaml.safe_dump(pack).encode())
    key = "clients/meridian/onboard/request.json"
    store.put(key, json.dumps({"id": "20261024T120000Z-abcdef", "command": command, "principal": principal,
                              "hash": "a" * 64, "timestamp": "2026-10-24T12:00:00Z"}).encode())
    monkeypatch.setenv("STORE_URL", str(tmp_path / "store"))
    monkeypatch.setenv("PROFILE", "meridian")
    prof = objstore.pull_config(store, tmp_path)
    for role in candidate_roles:
        access_state(prof).access_put({"username": f"{role['id']}.meridian@example.com", "name": role["id"],
                                      "roles": [role["id"]]}, by="operator@example.com", via="migration")
    loaded_candidate(prof, tmp_path, candidate_roles)
    calls = []

    def execute(args, **kwargs):
        calls.append(args)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(objstore.subprocess, "run", execute)
    monkeypatch.setattr(objstore.subprocess, "check_output", lambda *a, **k: "")
    if command in allowed:
        objstore.onboard_request(tmp_path)
        expected = ["sh", str(tmp_path / "entrypoint.sh"), command]
        if command in {"improve", "sign-off"}:
            expected.append(f"PRINCIPAL={principal}")
        if command in {"sign-off", "release"}:
            expected.append(f"HASH={'a' * 64}")
        assert calls == [expected]
    else:
        with pytest.raises(Refused, match="request refused"):
            objstore.onboard_request(tmp_path)
        assert calls == []
    job = json.loads(store.get("clients/meridian/onboard/job.json"))
    assert job["state"] == ("done" if command in allowed else "failed")
    assert store.get(key) == b""


@pytest.mark.parametrize("process,command,allowed", [
    ("building", "onboard", True), ("building", "improve", False),
    ("improving", "onboard", False), ("improving", "improve", True),
    ("certification", "onboard", False), ("certification", "improve", False),
    ("accepting", "onboard", False), ("accepting", "improve", False),
])
def test_job_requires_the_specific_process_not_a_role_name(monkeypatch, tmp_path, process, command, allowed):
    store = released_role_store(tmp_path, [{"id": "custom", "processes": [process]}])
    identities = tmp_path / "identities.yaml"
    identities.write_text("principals:\n  - {principal: person@example.com, role: custom}\n")
    prof = SimpleNamespace(client="meridian", clients=tmp_path / "clients", identities=identities, run_dir=lambda: tmp_path / "run")
    seed_people(prof)
    store.put("clients/meridian/onboard/request.json", json.dumps({
        "id": "20261024T120000Z-abcdef", "command": command, "principal": "person@example.com", "timestamp": "2026-10-24T12:00:00Z"}).encode())
    monkeypatch.setattr(objstore, "the_store", lambda: store)
    monkeypatch.setattr(objstore, "pull_config", lambda *a: prof)
    calls = []

    def execute(*args, **kwargs):
        calls.append(args)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(objstore.subprocess, "run", execute)
    monkeypatch.setattr(objstore.subprocess, "check_output", lambda *a, **k: "")
    if allowed:
        objstore.onboard_request(tmp_path)
    else:
        with pytest.raises(Refused, match="request refused"):
            objstore.onboard_request(tmp_path)
    assert bool(calls) == allowed


@pytest.mark.parametrize("role,allowed", [
    ({"processes": ["accepting"]}, True),
    ({"processes": ["querying"], "writes": ["terms", "mappings"]}, True),
    ({"processes": ["drafting", "building", "improving", "querying"]}, False),
    ({"processes": ["certification", "querying"], "writes": ["terms", "mappings"]}, False),
])
def test_job_permissions_resolve_acceptance_from_the_released_role(tmp_path, role, allowed):
    store = released_role_store(tmp_path, [{"id": "reviewer", **role}])
    identities = tmp_path / "identities.yaml"
    identities.write_text("principals:\n  - {principal: person@example.com, role: reviewer}\n")
    prof = SimpleNamespace(client="meridian", clients=tmp_path / "clients", identities=identities)
    seed_people(prof)
    released = objstore.released_roles(store, prof)
    assert objstore.permissions(released["person@example.com"])["accept"] == allowed


def test_a_local_store_puts_gets_and_lists_keys_relative_to_its_root(tmp_path):
    s = open_store(str(tmp_path / "store"))
    assert isinstance(s, LocalStore) and s.list() == [] and s.get("a/b") is None
    for key in ("deploy/profiles.yaml", "clients/x/pack.yaml", "clients/x/baselines/b.json", "work/x.tar.gz"):
        s.put(key, key.encode())
    s.put("clients/x/pack.yaml", b"again")
    assert s.get("clients/x/pack.yaml") == b"again" and s.get("clients/x") is None
    assert s.list() == ["clients/x/baselines/b.json", "clients/x/pack.yaml", "deploy/profiles.yaml", "work/x.tar.gz"]
    assert s.list("clients/") == ["clients/x/baselines/b.json", "clients/x/pack.yaml"]
    assert not [p for p in (tmp_path / "store").rglob(".*")], "written whole: no temporary file left"
    assert isinstance(open_store(f"file://{tmp_path}/store"), LocalStore) and open_store(f"file://{tmp_path}/store").list("work/") == ["work/x.tar.gz"]


def test_a_forged_pack_cannot_make_a_non_steward_sign_off(monkeypatch, tmp_path):
    store = released_role_store(tmp_path, [
        {"id": "business", "writes": ["terms", "mappings"], "processes": []},
        {"id": "steward", "writes": [], "processes": ["certification"]},
    ])
    clients = tmp_path / "clients"; identities = tmp_path / "deploy/meridian/identities.yaml"
    (clients / "meridian").mkdir(parents=True); identities.parent.mkdir(parents=True)
    (clients / "meridian/pack.yaml").write_text(
        "access:\n  roles:\n    - {id: business, writes: [terms, mappings], processes: [certification]}\n"
    )
    identities.write_text("principals:\n  - {principal: ann@example.com, role: business}\n  - {principal: sam@example.com, role: steward}\n")
    prof = SimpleNamespace(client="meridian", clients=clients, identities=identities)
    seed_people(prof)
    loaded_candidate(prof, tmp_path, [
        {"id": "business", "writes": ["terms", "mappings"], "processes": []},
        {"id": "steward", "writes": [], "processes": ["certification"]},
    ])
    request = {"id": "20261018T120000Z-abcdef", "command": "sign-off", "principal": "ann@example.com", "hash": "a" * 64,
               "timestamp": "2026-10-18T12:00:00Z", "checked_hash": "a" * 64, "checks_id": "20261018T110000Z-abcdef"}
    store.put("clients/meridian/onboard/request.json", json.dumps(request).encode())
    monkeypatch.setattr(objstore, "the_store", lambda: store)
    monkeypatch.setattr(objstore, "pull_config", lambda _store, _app: prof)
    with pytest.raises(Refused, match="ann@example.com may not sign-off aaaaaaaaaaaa: its roles do not let them certify"):
        objstore.onboard_request(tmp_path)
    assert store.get("clients/meridian/onboard/request.json") == b""
    failed = json.loads(store.get("clients/meridian/onboard/job.json"))
    assert failed["state"] == "failed" and failed["message"] == "request refused: ann@example.com may not sign-off aaaaaaaaaaaa: its roles do not let them certify"


def test_onboard_request_requires_the_released_building_process(monkeypatch, tmp_path):
    store = released_role_store(tmp_path, [
        {"id": "reader", "writes": [], "processes": []},
        {"id": "business", "writes": ["terms", "mappings"], "processes": []},
    ])
    identities = tmp_path / "deploy/meridian/identities.yaml"; identities.parent.mkdir(parents=True)
    identities.write_text("principals:\n  - {principal: ann@example.com, role: reader}\n")
    prof = SimpleNamespace(client="meridian", clients=tmp_path / "clients", identities=identities, run_dir=lambda: tmp_path / "run")
    seed_people(prof)
    request = {"id": "20261018T120000Z-abcdef", "command": "onboard", "principal": "ann@example.com", "hash": None,
               "timestamp": "2026-10-18T12:00:00Z", "checked_hash": None, "checks_id": None}
    store.put("clients/meridian/onboard/request.json", json.dumps(request).encode())
    monkeypatch.setattr(objstore, "the_store", lambda: store)
    monkeypatch.setattr(objstore, "pull_config", lambda _store, _app: prof)
    with pytest.raises(Refused, match="ann@example.com may not run checks"):
        objstore.onboard_request(tmp_path)
    assert store.get("clients/meridian/onboard/request.json") == b""
    failed = json.loads(store.get("clients/meridian/onboard/job.json"))
    assert failed["state"] == "failed" and "may not run checks" in failed["message"]


def test_a_released_engineer_may_run_onboarding_checks(monkeypatch, tmp_path):
    store = released_role_store(tmp_path, [{"id": "knowledge_engineer", "writes": [], "processes": ["building"]}])
    identities = tmp_path / "deploy/meridian/identities.yaml"; identities.parent.mkdir(parents=True)
    identities.write_text("principals:\n  - {principal: sam@example.com, role: knowledge_engineer}\n")
    prof = SimpleNamespace(client="meridian", clients=tmp_path / "clients", identities=identities, run_dir=lambda: tmp_path / "run")
    seed_people(prof)
    store.put("clients/meridian/onboard/request.json", json.dumps(
        {"id": "20261018T120000Z-abcdef", "command": "onboard", "principal": "sam@example.com", "hash": None,
         "timestamp": "2026-10-18T12:00:00Z", "checked_hash": None, "checks_id": None}).encode())
    monkeypatch.setattr(objstore, "the_store", lambda: store)
    monkeypatch.setattr(objstore, "pull_config", lambda _store, _app: prof)
    monkeypatch.setattr(objstore.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(
        returncode=2, stdout="", stderr="refused by stand-in"))
    with pytest.raises(Refused, match="job execution failed"):
        objstore.onboard_request(tmp_path)


def test_invalid_json_request_is_consumed_and_recorded_as_failed(monkeypatch, tmp_path):
    store = released_role_store(tmp_path, [])
    prof = SimpleNamespace(client="meridian", clients=tmp_path / "clients",
                           identities=tmp_path / "deploy/meridian/identities.yaml")
    seed_people(prof)
    store.put("clients/meridian/onboard/request.json", b"{")
    monkeypatch.setattr(objstore, "the_store", lambda: store)
    monkeypatch.setattr(objstore, "pull_config", lambda _store, _app: prof)
    with pytest.raises(Refused, match="request is not valid JSON"):
        objstore.onboard_request(tmp_path)
    assert store.get("clients/meridian/onboard/request.json") == b""
    assert json.loads(store.get("clients/meridian/onboard/job.json"))["state"] == "failed"


def test_a_malformed_request_is_consumed_and_recorded_as_failed(monkeypatch, tmp_path):
    store = released_role_store(tmp_path, [])
    prof = SimpleNamespace(client="meridian", clients=tmp_path / "clients",
                           identities=tmp_path / "deploy/meridian/identities.yaml")
    seed_people(prof)
    store.put("clients/meridian/onboard/request.json", json.dumps(
        {"id": "bad", "command": "release", "principal": "sam@example.com", "hash": "not-a-hash",
         "timestamp": "2026-10-18T12:00:00Z"}).encode())
    monkeypatch.setattr(objstore, "the_store", lambda: store)
    monkeypatch.setattr(objstore, "pull_config", lambda _store, _app: prof)

    with pytest.raises(Refused, match="sign-off and release require a model hash"):
        objstore.onboard_request(tmp_path)

    assert store.get("clients/meridian/onboard/request.json") == b""
    failed = json.loads(store.get("clients/meridian/onboard/job.json"))
    assert failed["id"] is None and failed["state"] == "failed" and failed["hash"] is None


def test_onboard_request_keeps_inner_output_and_consumes_the_request(monkeypatch, tmp_path, capsys):
    store = released_role_store(tmp_path, [
        {"id": "steward", "writes": [], "processes": ["certification"]},
    ])
    identities = tmp_path / "deploy/meridian/identities.yaml"; identities.parent.mkdir(parents=True)
    identities.write_text("principals:\n  - {principal: sam@example.com, role: steward}\n")
    prof = SimpleNamespace(client="meridian", clients=tmp_path / "clients", identities=identities, run_dir=lambda: tmp_path / "run")
    seed_people(prof)
    loaded_candidate(prof, tmp_path, [{"id": "steward", "processes": ["certification"]}])
    request = {"id": "20261018T120000Z-abcdef", "command": "sign-off", "principal": "sam@example.com", "hash": "a" * 64,
               "timestamp": "2026-10-18T12:00:00Z", "checked_hash": "a" * 64, "checks_id": "20261018T110000Z-abcdef"}
    store.put("clients/meridian/onboard/request.json", json.dumps(request).encode())
    (tmp_path / "entrypoint.sh").write_text("#!/bin/sh\necho SIGNOFF-output\necho diagnostic >&2\n")
    monkeypatch.setattr(objstore, "the_store", lambda: store)
    monkeypatch.setattr(objstore, "pull_config", lambda _store, _app: prof)
    objstore.onboard_request(tmp_path)
    output = capsys.readouterr()
    assert "SIGNOFF-output" in output.out and "diagnostic" in output.err
    assert store.get("clients/meridian/onboard/request.json") == b""


@pytest.mark.parametrize("exit_code", [0, 1, 2])
def test_onboard_request_carries_gate_lines_and_report_into_job_state(monkeypatch, tmp_path, exit_code):
    store = released_role_store(tmp_path, [
        {"id": "engineer", "writes": [], "processes": ["building"]},
    ])
    identities = tmp_path / "deploy/meridian/identities.yaml"; identities.parent.mkdir(parents=True)
    identities.write_text("principals:\n  - {principal: ann@example.com, role: engineer}\n")
    prof = SimpleNamespace(client="meridian", clients=tmp_path / "clients", identities=identities, run_dir=lambda: tmp_path / "run")
    seed_people(prof)
    request = {"id": "20261018T120000Z-abcdef", "command": "onboard", "principal": "ann@example.com", "hash": None,
               "timestamp": "2026-10-18T12:00:00Z", "checked_hash": None, "checks_id": None}
    store.put("clients/meridian/onboard/request.json", json.dumps(request).encode())
    model_hash = "c" * 64
    monkeypatch.setattr(objstore, "the_store", lambda: store)
    monkeypatch.setattr(objstore, "pull_config", lambda _store, _app: prof)
    monkeypatch.setattr(objstore.subprocess, "run", lambda *a, **k: SimpleNamespace(
        returncode=exit_code, stdout=f"onboard: candidate {model_hash}\n  V01 PASS schema\n  V02 PASS access\n", stderr=""))
    monkeypatch.setattr(objstore.subprocess, "check_output", lambda *a, **k: f"/tmp/{model_hash}\n")
    import kge.graph.release
    changes = {"lock": {"written": False, "changes": []},
               "estate": {"rebuilt": True, "why": "fixture-changed", "version": 2, "fixture_sha256": "f" * 64},
               "reloaded": True}
    monkeypatch.setattr(kge.graph.release, "state", lambda profile, candidate: {
        "gate": {"passed": True, "run_id": "run-7"}, "changes": changes})
    failed_gate = {"passed": False, "run_id": "failed-run-8"}
    prof.paths = lambda: {"runs": tmp_path / "run/runs"}
    touch(prof.paths()["runs"] / "failed-run-8/report.md", "V01 FAIL: failed run report")
    touch(prof.run_dir() / objstore.ONBOARD_RUN, json.dumps({
        "started_at": "2026-10-31T12:00:00Z", "outcome": "gate failed" if exit_code else "passed",
        "gate": failed_gate if exit_code else {"passed": True, "run_id": "run-7"}}))

    if exit_code:
        with pytest.raises(Refused, match="job execution failed"):
            objstore.onboard_request(tmp_path)
    else:
        objstore.onboard_request(tmp_path)

    job = json.loads(store.get("clients/meridian/onboard/job.json"))
    assert job["gate"] == (failed_gate if exit_code else {
        "passed": True, "run_id": "run-7", "lines": ["  V01 PASS schema", "  V02 PASS access"]})
    assert job["report"] == (None if exit_code else "/ab/run-7/report.html")
    if exit_code:
        assert f"report: runs/meridian/onboard/{ONBOARD_RUN_ID}/report.md" in job["message"]
    assert job["changes"] == (None if exit_code else changes)
    assert job["checked_hash"] == (None if exit_code else model_hash)
    assert job["checks_id"] == (None if exit_code else "20261018T120000Z-abcdef")


@pytest.mark.parametrize("key", ["../x", "/x", "a//b", "a/./b", "a/../b", "", "a\\b"])
def test_a_key_is_a_relative_path_with_no_dot_dot(tmp_path, key):
    s = open_store(str(tmp_path))
    with pytest.raises(ValueError):
        s.put(key, b"x")
    with pytest.raises(ValueError):
        s.get(key)
    assert not (tmp_path.parent / "x").exists()


def test_a_store_url_is_a_folder_or_a_bucket_and_s3_is_not_supported_yet(tmp_path):
    g = open_store("gs://kge-store/pilot/a")
    assert isinstance(g, GcsStore) and (g.bucket, g.prefix) == ("kge-store", "pilot/a")
    assert open_store("gs://kge-store").prefix == ""
    with pytest.raises(ValueError, match="not supported yet"):
        open_store("s3://kge-store")
    for bad in ("store", "", "gs://Bad_Bucket", "gs://kge-store/../x", "http://example.org/x"):
        with pytest.raises(ValueError, match=re.escape(adapter.FORMS)):
            open_store(bad)


@pytest.fixture
def stub():
    with serve() as srv:
        yield srv


@pytest.fixture
def local_calls(monkeypatch):
    """Calls to 127.0.0.1 go straight there, never through a proxy the environment names."""
    for name in ("NO_PROXY", "no_proxy"):
        monkeypatch.setenv(name, ",".join(x for x in (os.environ.get(name, ""), "127.0.0.1", "localhost") if x))
    monkeypatch.setattr(gcs_backend, "BACKOFF", 0.01)


@pytest.fixture
def gcs(request, local_calls, monkeypatch):
    """A GCS emulator's address in STORAGE_EMULATOR_HOST: KGE_GCS_EMULATOR's, else the stub's, named with no scheme as
    an emulator's host may be; and a bucket name no other test uses."""
    real = os.environ.get("KGE_GCS_EMULATOR", "").strip()
    if real:
        monkeypatch.setenv("STORAGE_EMULATOR_HOST", real)
        yield None, f"t{uuid.uuid4().hex[:12]}"
        return
    srv = request.getfixturevalue("stub")
    monkeypatch.setenv("STORAGE_EMULATOR_HOST", f"127.0.0.1:{srv.server_address[1]}")
    yield srv, f"t{uuid.uuid4().hex[:12]}"


def test_gcs_puts_gets_and_lists_through_the_json_api_under_a_prefix(gcs):
    srv, bucket = gcs
    s = open_store(f"gs://{bucket}/pilot")
    assert s.list() == [] and s.get("deploy/profiles.yaml") is None, "no bucket yet: nothing in it"
    keys = ["clients/x/pack.yaml", "clients/x/baselines/b 1.json", "deploy/profiles.yaml", "releases/x/current", "work/x.tar.gz"]
    for k in keys:
        s.put(k, f"<{k}>".encode())
    assert s.list() == sorted(keys), "every page of the list, each name relative to the prefix"
    assert s.list("clients/") == ["clients/x/baselines/b 1.json", "clients/x/pack.yaml"]
    assert s.get("clients/x/baselines/b 1.json") == b"<clients/x/baselines/b 1.json>" and s.get("clients/y") is None
    other = open_store(f"gs://{bucket}")
    assert other.get("pilot/deploy/profiles.yaml") == b"<deploy/profiles.yaml>", "the prefix is a folder in the bucket"
    if srv is not None:
        assert all(auth is None for _, _, auth in srv.seen), "no credentials to an emulator"
        assert ("POST", "/storage/v1/b?project=kge", None) in srv.seen, "the emulator's bucket made on the first put"


def test_gcs_conditional_put_uses_the_generation_precondition(gcs):
    _, bucket = gcs
    s = open_store(f"gs://{bucket}")
    s.put("clients/x/draft.yaml", b"initial")
    data, generation = s.get_version("clients/x/draft.yaml")
    assert data == b"initial"
    s.put_version("clients/x/draft.yaml", b"newer", generation)
    with pytest.raises(StoreError, match="changed concurrently"):
        s.put_version("clients/x/draft.yaml", b"lost", generation)


def test_gcs_retries_429_and_5xx_then_fails_naming_the_key_and_the_url(stub, local_calls, monkeypatch):
    monkeypatch.setenv("STORAGE_EMULATOR_HOST", f"http://127.0.0.1:{stub.server_address[1]}")
    stub.buckets["bkt"] = {}
    s = open_store("gs://bkt")
    stub.fail = [503, 429, 500]
    s.put("work/x.tar.gz", b"secret-free bytes")
    assert stub.buckets["bkt"]["work/x.tar.gz"] == b"secret-free bytes", "three retries, then it went"
    stub.fail = [503] * 4
    with pytest.raises(StoreError) as err:
        s.get("work/x.tar.gz")
    assert "work/x.tar.gz" in str(err.value) and "gs://bkt" in str(err.value) and "secret-free" not in str(err.value)
    stub.fail = [403]
    with pytest.raises(StoreError, match="HTTP 403"):
        s.put("work/x.tar.gz", b"x")
    assert stub.fail == [], "a 403 is not retried"


def test_gcs_without_an_emulator_sends_the_service_account_s_token_and_never_makes_a_bucket(stub, local_calls, monkeypatch):
    import google.auth

    class Creds:
        token, valid, refreshed = None, False, 0

        def refresh(self, request):
            self.token, self.valid, self.refreshed = "tok-123", True, self.refreshed + 1

    creds = Creds()
    asked = []
    monkeypatch.setattr(google.auth, "default", lambda scopes=None: asked.append(scopes) or (creds, "proj"))
    monkeypatch.delenv("STORAGE_EMULATOR_HOST", raising=False)
    s = open_store("gs://nobucket")
    assert s.base == "https://storage.googleapis.com"
    s.base = f"http://127.0.0.1:{stub.server_address[1]}"
    with pytest.raises(StoreError, match="HTTP 404"):
        s.put("deploy/profiles.yaml", b"x")
    assert "nobucket" not in stub.buckets and not [p for m, p, _ in stub.seen if p.startswith("/storage/v1/b?")]
    assert asked == [[gcs_backend.SCOPE]] and creds.refreshed == 1
    assert {auth for _, _, auth in stub.seen} == {"Bearer tok-123"}


def scratch_round(open_area) -> None:
    """Two clients' scratch areas, as one process would open them: each writes, reads and deletes its own, and neither
    sees the other's file of the same name. Neither offers a list: live, the prefix grant cannot allow one."""
    meridian, thornbury = open_area("meridian"), open_area("thornbury")
    assert not hasattr(meridian, "list") and not hasattr(thornbury, "list")
    meridian.put("bigquery/load.json", b"meridian's rows\n")
    thornbury.put("bigquery/load.json", b"thornbury's rows\n")
    assert meridian.get("bigquery/load.json") == b"meridian's rows\n"
    assert thornbury.get("bigquery/load.json") == b"thornbury's rows\n"
    with pytest.raises(ValueError):
        meridian.get("../thornbury/bigquery/load.json")
    meridian.delete("bigquery/load.json")
    assert meridian.get("bigquery/load.json") is None and thornbury.get("bigquery/load.json") == b"thornbury's rows\n"
    thornbury.delete("bigquery/load.json")


def test_the_scratch_area_is_a_local_temporary_folder_per_client_without_tmp_url(tmp_path, monkeypatch):
    """Issue #99: with TMP_URL unset a client's scratch area is a folder of its own in this machine's temporary folder,
    which no load job can read (source_uri None), so the local stack and Compose work as before."""
    monkeypatch.delenv("TMP_URL", raising=False)
    monkeypatch.setattr(adapter.tempfile, "gettempdir", lambda: str(tmp_path))
    scratch_round(adapter.open_scratch)
    area = adapter.open_scratch("meridian")
    assert area.url == str(tmp_path / "kge-tmp" / "meridian") and area.source_uri("bigquery/load.json") is None
    area.put("bigquery/load.json", b"x")
    assert (tmp_path / "kge-tmp" / "meridian" / "bigquery" / "load.json").read_bytes() == b"x"
    assert not (tmp_path / "kge-tmp" / "thornbury" / "bigquery" / "load.json").exists()


def test_the_scratch_area_is_tmp_client_in_the_tmp_bucket_on_the_emulator(gcs, monkeypatch):
    """Issue #99: with TMP_URL a bucket, a client's scratch area is tmp/<client>/ in it, the prefix Terraform grants that
    client's service and job alone, reached through the emulator as the store is; a load job reads a file there from
    its gs:// URI."""
    srv, bucket = gcs
    monkeypatch.setenv("TMP_URL", f"gs://{bucket}")
    scratch_round(adapter.open_scratch)
    area = adapter.open_scratch("meridian")
    assert area.url == f"gs://{bucket}/tmp/meridian"
    assert area.source_uri("bigquery/a b.json") == f"gs://{bucket}/tmp/meridian/bigquery/a b.json"
    area.put("bigquery/load.json", b"rows\n")
    assert open_store(f"gs://{bucket}").list() == ["tmp/meridian/bigquery/load.json"], "under tmp/<client>/ alone"
    area.delete("bigquery/load.json")
    if srv is not None:
        assert all(auth is None for _, _, auth in srv.seen), "no credentials to an emulator"
        assert srv.buckets[bucket] == {}


@pytest.mark.parametrize("status", [200, 204])
def test_a_delete_succeeds_on_either_answer_the_json_api_gives(status, monkeypatch):
    """Review of #100: Cloud Storage answers a delete with 204 and the kit's emulator (fake-gcs-server 1.56.1) with 200;
    both are a delete done, so a staged load's file is removed on either, and a missing object is no error."""
    with serve() as srv:
        srv.delete_status = status
        monkeypatch.setenv("STORAGE_EMULATOR_HOST", f"http://127.0.0.1:{srv.server_address[1]}")
        monkeypatch.setenv("TMP_URL", "gs://kge-tmp")
        area = adapter.open_scratch("meridian")
        area.put("bigquery/load.json", b"rows\n")
        area.delete("bigquery/load.json")
        area.delete("bigquery/load.json")
        assert srv.buckets["kge-tmp"] == {}


def test_a_scratch_area_offers_put_get_delete_and_source_uri_and_no_list(tmp_path, monkeypatch):
    """Review of #100: on Cloud Storage storage.objects.list is checked against the bucket, so a client's grant on its
    tmp/<client>/ prefix cannot allow one, and a list there is refused with 403 live. The scratch area is the narrower
    ScratchArea port, put, get, delete and source_uri and no list, on a folder and on a bucket alike, so no caller can
    come to depend on a call the pilot's grants refuse."""
    from kge.ports import ObjectStore, ScratchArea
    members = {n for n in vars(ScratchArea) if not n.startswith("_")} | set(ScratchArea.__annotations__)
    assert members == {"url", "put", "get", "delete", "source_uri"}, members
    assert "list" in vars(ObjectStore), "the store keeps its list"
    monkeypatch.delenv("TMP_URL", raising=False)
    monkeypatch.setattr(adapter.tempfile, "gettempdir", lambda: str(tmp_path))
    folder = adapter.open_scratch("meridian")
    with serve() as srv:
        monkeypatch.setenv("STORAGE_EMULATOR_HOST", f"http://127.0.0.1:{srv.server_address[1]}")
        monkeypatch.setenv("TMP_URL", "gs://kge-tmp")
        bucket = adapter.open_scratch("meridian")
    for area in (folder, bucket):
        assert isinstance(area, ScratchArea) and not isinstance(area, ObjectStore), type(area).__name__
        assert not hasattr(area, "list") and not hasattr(area, "get_version") and not hasattr(area, "put_version")


def test_the_stand_in_refuses_a_list_under_a_scratch_area_as_cloud_storage_does(monkeypatch):
    """Review of #100: tests/gcs_stub.py answers a list whose prefix is under tmp/ with 403, as Cloud Storage answers a
    client holding only its prefix grant, so a future caller that lists a scratch area fails offline, not on the pilot;
    the store's own lists, and a list of the whole bucket, still answer."""
    with serve() as srv:
        monkeypatch.setenv("STORAGE_EMULATOR_HOST", f"http://127.0.0.1:{srv.server_address[1]}")
        monkeypatch.setattr(gcs_backend, "BACKOFF", 0.01)
        root = open_store("gs://kge-tmp")
        root.put("tmp/meridian/bigquery/load.json", b"rows\n")
        root.put("clients/x/pack.yaml", b"pack")
        with pytest.raises(StoreError, match="HTTP 403"):
            open_store("gs://kge-tmp/tmp/meridian").list()
        with pytest.raises(StoreError, match="HTTP 403"):
            root.list("tmp/")
        assert root.list("clients/") == ["clients/x/pack.yaml"]
        assert root.list() == ["clients/x/pack.yaml", "tmp/meridian/bigquery/load.json"]


def test_a_scratch_area_needs_a_client_slug_and_tmp_url_a_store(monkeypatch):
    for client in ("", "Meridian", "../meridian", "meridian/x", "1st", None):
        with pytest.raises(ValueError, match="a client id is a lower-case letter"):
            adapter.open_scratch(client)
    monkeypatch.setenv("TMP_URL", "s3://kge-tmp")
    with pytest.raises(ValueError, match="^TMP_URL: .*not supported yet"):
        adapter.open_scratch("meridian")
    monkeypatch.setenv("TMP_URL", "kge-tmp")
    with pytest.raises(ValueError, match="^TMP_URL: "):
        adapter.open_scratch("meridian")


class Recording(LocalStore):
    def __init__(self, root: Path):
        super().__init__(str(root), root)
        self.order: list[str] = []

    def put(self, key, data):
        self.order.append(key)
        super().put(key, data)


def landing(folder: Path, rid: str = "a1b2c3d4e5f6a7b8") -> Path:
    rel = folder / "releases" / rid
    (rel / "documents").mkdir(parents=True)
    (rel / "documents" / "a.md").write_text("a")
    (rel / "certification.json").write_text("{}")
    (rel / "manifest.json").write_text('{"release_id": "%s"}' % rid)
    (folder / ".staging-1").mkdir()
    (folder / ".staging-1" / "x").write_text("x")
    os.symlink(f"releases/{rid}", folder / "current")
    return folder


def test_push_puts_every_file_then_current_then_each_manifest_and_pull_mirrors_it_back(tmp_path):
    src = landing(tmp_path / "landing")
    s = Recording(tmp_path / "store")
    assert push(src, s, "landing/x/", links=("current",)) == 4
    rid = "releases/a1b2c3d4e5f6a7b8"
    assert s.order == [f"landing/x/{rid}/certification.json", f"landing/x/{rid}/documents/a.md", "landing/x/current",
                       f"landing/x/{rid}/manifest.json"], "files, then current, then the manifest: QA and PROD's trigger"
    assert s.get("landing/x/current") == f"{rid}\n".encode(), "current as a text object, never a symlink"
    dest = tmp_path / "pulled"
    (dest / "stale").mkdir(parents=True)
    (dest / "stale" / "old.md").write_text("old")
    assert pull(s, "landing/x/", dest, links=("current",)) == 4
    assert not (dest / "stale").exists(), "a mirror: what the store does not hold is gone"
    assert os.readlink(dest / "current") == rid and (dest / "current" / "documents" / "a.md").read_text() == "a"


def test_local_store_conditional_put_refuses_a_stale_writer(tmp_path):
    store = open_store(f"file://{tmp_path}/store")
    _, absent = store.get_version("clients/acme/units/u/draft.yaml")
    assert absent == "0"
    current = store.put_version("clients/acme/units/u/draft.yaml", b"first", absent)
    assert current != "0"
    with pytest.raises(StoreError, match="changed concurrently"):
        store.put_version("clients/acme/units/u/draft.yaml", b"lost update", absent)
    assert store.get("clients/acme/units/u/draft.yaml") == b"first"


def test_pull_refuses_a_link_that_leaves_the_folder(tmp_path):
    s = open_store(str(tmp_path / "store"))
    s.put("current", b"../../etc\n")
    with pytest.raises(ValueError):
        pull(s, "", tmp_path / "landing", links=("current",))


def touch(path: Path, text: str = "x") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


SECRETS = ("tokens.json", "passwords.json", "passwords.txt", "session_secret", "signed_out.json", "api.pid", "api.json", "access-grants.json", "access-audit.jsonl", "ROSTER")


def built(app: Path, client: str = "acme", h: str = "a1") -> Path:
    """A build as the job leaves it, for a profile setup-client made: its estate, landing, artifact, graph and run state."""
    b = app / "build"
    touch(b / client / "summary.json", "{}")
    touch(b / client / "golden.json", "{}")
    touch(b / client / "docs" / "d.md")
    touch(b / client / "warehouses" / "retail.duckdb", "db")
    touch(b / client / "certification" / client / "runs" / "r.json")
    landing(b / "landing" / client)
    touch(b / "artifacts" / client / h / "manifest.json", json.dumps({"client": client}))
    os.symlink(h, b / "artifacts" / client / "current")
    touch(b / "stores" / client / h / "graph.ladybug", "graph")
    for marker in ("LOADED", "GATE", "SIGNOFF", "RELEASED"):
        touch(b / "stores" / client / h / marker, "{}")
    touch(b / "stores" / client / "overlay" / "decisions.jsonl")
    run = b / "stores" / client / "run"
    for name in ("principals.json", "pipeline_stats.json", "eval.json", *SECRETS):
        touch(run / name, "{}")
    touch(run / "runs" / "ab-1.json")
    for folder in ("node", "demo-script", "kit"):
        touch(b / folder / "x")
    return app


def names(data: bytes) -> set[str]:
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tar:
        return set(tar.getnames())


def test_the_work_is_build_but_the_landing_node_s_the_demo_script_s_the_kit_and_every_secret(tmp_path):
    app = built(tmp_path / "app")
    data = tarball(app, work_paths(app))
    got = names(data)
    assert {n.split("/")[1] for n in got} == {"acme", "artifacts", "stores"}, "no landing, node, demo-script or kit"
    assert not [n for n in got if n.rsplit("/", 1)[-1] in SECRETS], "no token, password, session key, pid or address"
    assert {"build/stores/acme/run/principals.json", "build/stores/acme/run/runs/ab-1.json", "build/stores/acme/overlay/decisions.jsonl",
            "build/artifacts/acme/current", "build/stores/acme/a1/graph.ladybug", "build/acme/certification/acme/runs/r.json"} <= got
    fresh = tmp_path / "fresh"
    assert unpack(data, fresh) == len(got)
    assert os.readlink(fresh / "build/artifacts/acme/current") == "a1" and (fresh / "build/artifacts/acme/current/manifest.json").is_file()


def tar_of(members: dict[str, str], links: dict[str, str] | None = None) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for name, text in members.items():
            info = tarfile.TarInfo(name)
            info.size = len(text)
            tar.addfile(info, io.BytesIO(text.encode()))
        for name, target in (links or {}).items():
            info = tarfile.TarInfo(name)
            info.type, info.linkname = tarfile.SYMTYPE, target
            tar.addfile(info)
    return buf.getvalue()


@pytest.mark.parametrize("members,links", [
    ({"build/ok": "x", "clients/x/pack.yaml": "x"}, None),
    ({"build/ok": "x", "build/../escape": "x"}, None),
    ({"build/ok": "x", "/etc/escape": "x"}, None),
    ({"build/ok": "x"}, {"build/link": "/etc/passwd"}),
    ({"build/ok": "x"}, {"build/link": "../../outside"}),
])
def test_a_tarball_with_a_member_outside_build_is_refused_and_nothing_is_extracted(tmp_path, members, links):
    with pytest.raises(Refused):
        unpack(tar_of(members, links), tmp_path / "app")
    assert not (tmp_path / "app" / "build" / "ok").exists() and not (tmp_path / "escape").exists()


@pytest.fixture
def store_env(tmp_path, monkeypatch):
    """STORE_URL a local store, and nothing of the caller's store, landing, passwords or profile."""
    for name in ("STORE_URL", "LANDING_URL", "STORAGE_EMULATOR_HOST", "SIGNIN_PASSWORDS", "PROFILE", "PROFILES",
                 "CLIENT", "PEOPLE", "ASKER", "BASELINE", "EXAMPLE", "FROM"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("STORE_URL", str(tmp_path / "store"))
    return tmp_path / "store"


def setup(monkeypatch, client: str = "acme", people: str = PEOPLE, **more: str) -> None:
    with monkeypatch.context() as m:
        for k, v in {"CLIENT": client, "PEOPLE": people, **more}.items():
            m.setenv(k, v)
        setup_client()
        store = objstore.the_store()
        bindings = [{"principal": pair.split(":")[0], "role": pair.split(":")[1]} for pair in people.split()]
        store.put(f"deploy/{client}/identities.yaml", yaml.safe_dump({"client": client, "principals": bindings}).encode())


def test_release_publishes_what_serve_needs_and_serve_fetches_it_with_current_at_its_hash(tmp_path, store_env, monkeypatch):
    """F2 adds a nullable release id to the identity published from legacy work."""
    setup(monkeypatch)
    app = built(tmp_path / "app")
    s = Recording(store_env)
    monkeypatch.setattr(objstore, "the_store", lambda: s)
    prof = objstore.pull_config(s, app)
    assert objstore.publish(s, app, prof) == "a1"
    assert s.order[-3:] == ["releases/acme/a1.tar.gz", "releases/acme/identity.json", "releases/acme/current"], \
        "the tarball first, then the release identity, then current"
    assert s.get("releases/acme/current") == b"a1\n"
    assert json.loads(s.get("releases/acme/identity.json")) == {"hash": "a1", "graph": None, "chunks": None, "warehouses": {}, "release": None}
    assert objstore.released_identity(s, prof, "a1") == {"hash": "a1", "graph": None, "chunks": None, "warehouses": {}, "release": None}
    assert objstore.released_identity(s, prof, "b2") is None, "an identity of another hash is not current's"
    got = names(s.get("releases/acme/a1.tar.gz"))
    run = "build/stores/acme/run"
    assert got == {"build/artifacts/acme/a1/manifest.json", "build/stores/acme/a1/graph.ladybug",
                   *(f"build/stores/acme/a1/{m}" for m in ("LOADED", "GATE", "SIGNOFF", "RELEASED")),
                   f"{run}/principals.json", f"{run}/pipeline_stats.json", f"{run}/eval.json",
                   "build/acme/warehouses/retail.duckdb", "build/acme/summary.json", "build/acme/golden.json"}, \
        "no landing, overlay, runs, token or password"
    fresh = tmp_path / "fresh"
    s.put("deploy/acme/passwords-secret", b"projects/example/secrets/current-passwords")
    s.put("secrets/acme/passwords.json", b'{"ann@example.com": "scrypt$hash"}')
    objstore.before("serve", fresh)
    assert os.readlink(fresh / "build/artifacts/acme/current") == "a1"
    assert (fresh / "build/stores/acme/a1/graph.ladybug").read_text() == "graph"
    pw = fresh / run / "passwords.json"
    assert pw.read_text() == '{"ann@example.com": "scrypt$hash"}' and stat.S_IMODE(pw.stat().st_mode) == 0o600
    assert not (fresh / run / "tokens.json").exists() and not (fresh / "build/landing").exists()
    assert (fresh / "deploy/acme/identities.yaml").is_file(), "deploy/ pulled"
    assert (fresh / "deploy/acme/passwords-secret").read_bytes() == b"projects/example/secrets/current-passwords"
    later = tmp_path / "later"
    monkeypatch.setenv("SIGNIN_PASSWORDS", '{"ann@example.com": "from the secret store"}')
    objstore.before("serve", later)
    assert not (later / run / "passwords.json").exists(), "SIGNIN_PASSWORDS set: the store's hashes are not read"


@pytest.mark.parametrize("warehouses", [None, {}, {"retail": 0}, {"retail": 2}])
def test_published_legacy_identity_normalizes_missing_warehouses(tmp_path, warehouses):
    """F2 normalizes a legacy identity with release=None as well as its warehouse versions."""
    store = open_store(str(tmp_path / "store"))
    prof = SimpleNamespace(client="acme")
    named = {"hash": "a1", "graph": "g1", "chunks": "c1"}
    if warehouses is not None:
        named["warehouses"] = warehouses
    store.put("releases/acme/identity.json", json.dumps(named).encode())
    assert objstore.released_identity(store, prof, "a1") == {**named, "warehouses": warehouses or {}, "release": None}


def test_serve_without_release_continues_but_a_broken_release_is_refused(tmp_path, store_env, monkeypatch, capsys):
    setup(monkeypatch)
    objstore.before("serve", tmp_path / "app")
    assert "store: nothing released for acme: release first" in capsys.readouterr().out
    s = open_store(str(store_env))
    s.put("releases/acme/current", b"../../x\n")
    with pytest.raises(Refused, match="names no model hash"):
        objstore.before("serve", tmp_path / "app")
    s.put("releases/acme/current", b"b2\n")
    with pytest.raises(Refused, match="no releases/acme/b2.tar.gz"):
        objstore.before("serve", tmp_path / "app")


def test_a_job_pulls_config_then_its_work_then_its_landing_and_seed_pushes_the_landing_and_the_work(tmp_path, store_env, monkeypatch):
    setup(monkeypatch)
    app = built(tmp_path / "app")
    s = open_store(str(store_env))
    objstore.pull_config(s, app)
    objstore.after("seed", app)
    assert s.get("landing/acme/current") == b"releases/a1b2c3d4e5f6a7b8\n", "the landing under the store, LANDING_URL unset"
    assert "work/acme.tar.gz" in s.list("work/")
    job = tmp_path / "job"
    objstore.before("onboard", job)
    assert os.readlink(job / "build/landing/acme/current") == "releases/a1b2c3d4e5f6a7b8"
    assert (job / "build/acme/summary.json").is_file() and (job / "deploy/profiles.yaml").is_file()
    monkeypatch.setenv("LANDING_URL", str(tmp_path / "landing-bucket"))
    objstore.after("seed", app)
    assert open_store(str(tmp_path / "landing-bucket")).get("current") == b"releases/a1b2c3d4e5f6a7b8\n"
    with pytest.raises(Refused, match="run setup-client"):
        monkeypatch.setenv("STORE_URL", str(tmp_path / "empty"))
        objstore.before("seed", tmp_path / "job2")


def test_setup_client_preserves_seed_and_writes_a_profile_the_reader_takes(tmp_path, store_env, monkeypatch):
    setup(monkeypatch, ASKER="analyst", BASELINE="baselines/b.json")
    store = open_store(str(store_env))
    seed = store.get("deploy/acme/identities.yaml")
    with monkeypatch.context() as patch:
        patch.setenv("CLIENT", "acme")
        setup_client()
    assert store.get("deploy/acme/identities.yaml") == seed
    pull(store, "deploy/", tmp_path / "app/deploy")
    default, profiles = read(tmp_path / "app/deploy/profiles.yaml")
    assert default == "acme"
    assert profiles["acme"].asker() == "analyst"


@pytest.mark.parametrize("asker,argument", [("analyst", " ASKER=analyst"), (None, "")])
def test_setup_client_line_uses_profile_role_without_people(asker, argument):
    line, warning = objstore.format_setup_client_line("acme", "baselines/x.json", asker)
    assert line == f"setup-client CLIENT=acme{argument} BASELINE=baselines/x.json"
    assert warning is None


@pytest.mark.parametrize("settings", [
    {"CLIENT": ""}, {"CLIENT": "Acme"}, {"CLIENT": "acme/x"}, {"CLIENT": "a" * 21},
    {"ASKER": "role:invalid"}, {"BASELINE": "../other/b.json"}, {"BASELINE": "/b.json"},
])
def test_setup_client_refuses_what_it_cannot_set_up_and_puts_nothing(store_env, monkeypatch, settings):
    with pytest.raises(Refused):
        setup(monkeypatch, client=settings.pop("CLIENT", "acme"), **settings)
    assert open_store(str(store_env)).list() == []


def test_put_example_puts_its_files_under_the_same_keys_and_refuses_one_outside_clients(tmp_path, store_env, monkeypatch):
    kit = tmp_path / "examples"
    touch(kit / "demo" / "clients" / "acme" / "pack.yaml", "pack")
    touch(kit / "demo" / "clients" / "acme" / "baselines" / "b.json", "{}")
    touch(kit / "bad" / "clients" / "acme" / "pack.yaml", "pack")
    touch(kit / "bad" / "deploy" / "profiles.yaml", "x")
    monkeypatch.setenv("FROM", str(kit))
    monkeypatch.setenv("EXAMPLE", "demo")
    objstore.put_example()
    s = open_store(str(store_env))
    assert s.list() == ["clients/acme/baselines/b.json", "clients/acme/pack.yaml"] and s.get("clients/acme/pack.yaml") == b"pack"
    for example in ("bad", "none", "../examples/demo"):
        monkeypatch.setenv("EXAMPLE", example)
        with pytest.raises(Refused):
            objstore.put_example()
    assert s.list() == ["clients/acme/baselines/b.json", "clients/acme/pack.yaml"]


def test_releases_lists_each_hash_and_marks_current(tmp_path, store_env, monkeypatch, capsys):
    setup(monkeypatch)
    with pytest.raises(Refused, match="nothing released"):
        objstore.releases(tmp_path / "app")
    s = open_store(str(store_env))
    for h in ("a1", "b2"):
        s.put(f"releases/acme/{h}.tar.gz", b"x")
    s.put("releases/acme/current", b"a1\n")
    capsys.readouterr()
    objstore.releases(tmp_path / "app")
    out = capsys.readouterr().out
    assert re.search(r"^  a1  current$", out, re.M) and re.search(r"^  b2$", out, re.M)


FAKE_MAKE = r'''
import json, os, sys
from pathlib import Path
target, c, b = sys.argv[1], "acme", Path("build")
cur = b / "artifacts" / c / "current"
pw = b / "stores" / c / "run" / "passwords.json"
seen = {"args": sys.argv[1:], "landed": os.path.islink(b / "landing" / c / "current"),
        "summary": (b / c / "summary.json").is_file(), "current": os.readlink(cur) if os.path.islink(cur) else None,
        "passwords": oct(os.stat(pw).st_mode & 0o777) if pw.exists() else None,
        "pack": Path("clients", c, "pack.yaml").is_file()}
if target == "ab":
    seen["model"] = {name: os.environ.get(name) for name in
                     ("MODEL_PROVIDER", "TAGGER_PROVIDER", "MODEL_HIGH_PROVIDER", "MODEL_HIGH_NAME",
                      "MODEL_MEDIUM_PROVIDER", "MODEL_MEDIUM_NAME", "MODEL_LOW_PROVIDER", "MODEL_LOW_NAME", "MOCK_MODE")}
with open(os.environ["MAKE_LOG"], "a") as f:
    f.write(json.dumps(seen) + "\n")
def put(p, text="x"):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)
failure = int(os.environ.get("MAKE_EXIT", "0"))
fails_here = failure and os.environ.get("MAKE_EXIT_TARGET", target) == target
if fails_here and target != "margin" and not os.environ.get("MAKE_FAIL_AFTER"):
    sys.exit(failure)
if target == "_stored-after":
    from kge.pipeline import objstore
    given = dict(a.split("=", 1) for a in sys.argv[2:] if "=" in a)
    objstore.after(given["COMMAND"], Path.cwd())
elif target == "estate":
    put(b / c / "summary.json", "{}")
    put(b / c / "warehouses" / "retail.duckdb", "db")
    put(b / "landing" / c / "releases" / "a1b2c3d4e5f6a7b8" / "manifest.json", "{}")
    os.symlink("releases/a1b2c3d4e5f6a7b8", b / "landing" / c / "current")
    put(b / "stores" / c / "run" / "tokens.json", "{}")
elif target == "onboard":
    put(b / c / "changed-by-onboard.txt", "uncommitted")
    put(b / "stores" / c / "run" / "onboard-run.json", json.dumps({
        "started_at": "2026-10-31T12:00:00Z", "outcome": "failed" if fails_here else "passed", "gate": None}))
elif target == "release":
    h = os.environ.get("HASH", "a1")
    put(b / "artifacts" / c / h / "manifest.json", json.dumps({"client": c}))
    put(b / "stores" / c / h / "graph.ladybug", "graph")
    put(b / "stores" / c / "run" / "principals.json", "[]")
    tmp = b / "artifacts" / c / ".current.tmp"
    os.symlink(h, tmp)
    os.replace(tmp, cur)
elif target == "passwords":
    put(b / "stores" / c / "run" / "passwords.json", "{}")
    put(b / "stores" / c / "run" / "passwords.txt", "ann@example.com pw")
elif target == "ab":
    put(b / "stores" / c / "run" / "runs" / "20261021T000000Z-abcdef" / "manifest.json", "{}")
elif target == "baseline":
    given = dict(a.split("=", 1) for a in sys.argv[2:] if "=" in a)
    put(Path(given["BASELINE"]), json.dumps({"run": given["RUN"], "name": given["NAME"]}))
elif target == "load":
    given = dict(a.split("=", 1) for a in sys.argv[2:] if "=" in a)
    h = given["HASH"]
    put(b / "artifacts" / c / h / "manifest.json", json.dumps({"client": c}))
    folder = b / "stores" / c / h
    put(folder / "graph.bigquery", json.dumps({"format": 1, "project": "twin-project",
                                                "dataset": "kge_acme_graph", "prefix": f"g{h[:16]}_11111111_"}))
    put(folder / "chunks.bigquery", json.dumps({"format": 1, "project": "twin-project",
                                                 "dataset": "kge_acme_graph", "table": f"chunks_{h[:16]}_11111111"}))
    put(folder / "LOADED", json.dumps({"bigquery": {"client": c, "model_hash": h}}))
elif target == "margin":
    given = dict(a.split("=", 1) for a in sys.argv[2:] if "=" in a)
    h = given["HASH"]
    report = b / "stores" / c / "run" / "runs" / "failed-run" / "report.md"
    put(report, f"failed margin for {h}\n")
    print(f"report: {report}")
if os.environ.get("MAKE_BREAK_STORE"):
    for root, dirs, files in os.walk(os.environ["STORE_URL"]):
        Path(root).chmod(0o500)
if fails_here:
    sys.exit(failure)
'''


@pytest.fixture
def image(tmp_path):
    """run(*args, env=, app=) -> (process, make's calls): the entry script from an app folder of its own (a copy of it and
    kge/pipeline/, kge/adapters/, kge/landing/ and kge/ports.py), beside the fake kge, on the local store tmp_path/store."""
    bin_ = tmp_path / "bin"
    bin_.mkdir()
    fake = bin_ / "kge"
    fake.write_text(f"#!{sys.executable}\n{FAKE_MAKE}")
    fake.chmod(0o755)
    log = tmp_path / "make.jsonl"
    kit = tmp_path / "examples"
    touch(kit / "acme" / "clients" / "acme" / "pack.yaml", "pack")
    keep = ("PROFILE", "PROFILES", "SIGNIN", "STORE_URL", "LANDING_URL", "STORAGE_EMULATOR_HOST", "SIGNIN_PASSWORDS", "API_URL")

    def make_app(name: str) -> Path:
        app = tmp_path / name
        if not app.exists():
            app.mkdir()
            shutil.copy(DEMO / "entrypoint.sh", app / "entrypoint.sh")
            (app / "kge").mkdir()
            shutil.copy(DEMO / "kge" / "__init__.py", app / "kge" / "__init__.py")
            shutil.copytree(DEMO / "kge" / "pipeline", app / "kge" / "pipeline", ignore=shutil.ignore_patterns("__pycache__"))
            shutil.copytree(DEMO / "kge" / "adapters", app / "kge" / "adapters", ignore=shutil.ignore_patterns("__pycache__"))
            for package in ("landing", "estate", "compiler"):
                shutil.copytree(DEMO / "kge" / package, app / "kge" / package, ignore=shutil.ignore_patterns("__pycache__"))
            shutil.copy(DEMO / "kge" / "ports.py", app / "kge" / "ports.py")
        return app

    def run(*args, env=None, app="app", store=True):
        log.unlink(missing_ok=True)
        base = {k: v for k, v in os.environ.items() if k not in keep}
        r = subprocess.run(["sh", str(make_app(app) / "entrypoint.sh"), *args], capture_output=True, text=True, cwd=tmp_path,
                           env={**base, "PATH": f"{bin_}{os.pathsep}{os.environ['PATH']}", "MAKE_LOG": str(log),
                                "PY": sys.executable, "FROM": str(kit),
                                **({"STORE_URL": str(tmp_path / "store")} if store else {}), **(env or {})})
        calls = [json.loads(x) for x in log.read_text().splitlines()] if log.exists() else []
        return r, calls
    return run


def test_the_store_s_own_commands_need_a_store(image):
    for command in ("setup-client", "put-example", "releases"):
        r, calls = image(command, "CLIENT=acme", store=False)
        assert r.returncode == 2 and "needs STORE_URL" in r.stderr and calls == [], (command, r.stderr)


def test_another_clients_units_do_not_change_this_clients_onboard_path(image, tmp_path):
    store = open_store(str(tmp_path / "store"))
    r, _ = image("setup-client", "CLIENT=acme", f"PEOPLE={PEOPLE}", "ASKER=analyst")
    assert r.returncode == 0, r.stderr
    store.put("clients/other/units/leasing/unit.yaml", b"unit: leasing\n")
    r, calls = image("onboard")
    assert r.returncode == 0, r.stderr
    assert [call["args"] for call in calls] == [["onboard"], ["_stored-after", "COMMAND=onboard"]], r.stderr


def onboard_without_units(image, tmp_path, store: str, marked: str | None, on: str | None = None):
    """The entry script's onboard with no units and no object store, on a profile whose pack compiles to current (a
    stand-in compiler prints current's artifact), current's graph made on store and its LOADED marker holding marked's
    entry (none for None)."""
    image("releases", store=False)
    h = "c" * 64
    compiler = tmp_path / "app" / "kge" / "compiler"
    compiler.mkdir(exist_ok=True)
    (compiler / "__init__.py").write_text("")
    (compiler / "compile.py").write_text(f"print({str(tmp_path / 'artifacts' / 'meridian' / h)!r})\n")
    (tmp_path / "artifacts" / "meridian" / h).mkdir(parents=True, exist_ok=True)
    (tmp_path / "artifacts" / "meridian" / h / "manifest.json").write_text('{"client": "meridian"}')
    if not (tmp_path / "artifacts" / "meridian" / "current").is_symlink():
        (tmp_path / "artifacts" / "meridian" / "current").symlink_to(h)
    folder = tmp_path / "stores" / "meridian" / h
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"graph.{store}").write_text(json.dumps({"format": 1, "prefix": f"g{h[:16]}_1_"}) if store == "bigquery" else "")
    (folder / "LOADED").unlink(missing_ok=True)
    if marked:
        (folder / "LOADED").write_text(json.dumps({marked: {"client": "meridian", "model_hash": h}}))
    registry = tmp_path / "profiles.yaml"
    registry.write_text("default: meridian\nprofiles:\n  meridian:\n    client: meridian\n"
                        f"    clients: {tmp_path / 'clients'}\n    artifacts: {tmp_path / 'artifacts'}\n"
                        f"    store: {tmp_path / 'stores'}\n    landing: {tmp_path / 'landing'}\n"
                        f"    warehouses: {tmp_path / 'warehouses'}\n    identities: {tmp_path / 'identities.yaml'}\n"
                        "    baseline: null\n")
    r, calls = image("onboard", store=False, env={"PROFILES": str(registry), "GRAPH_STORE": on or store})
    return h, r, [call["args"] for call in calls]


@pytest.mark.parametrize("store", ["ladybug", "bigquery"])
def test_the_image_delegates_loaded_current_to_python_onboard(image, tmp_path, store):
    """The image's onboard of a pack that compiles to current, loaded on this graph store, loads nothing and checks
    nothing, as make onboard skips it: the served hash is never reloaded or unmarked."""
    h, r, calls = onboard_without_units(image, tmp_path, store, store)
    assert r.returncode == 0, r.stderr
    assert calls == [["onboard"]]


@pytest.mark.parametrize("store, marked", [("ladybug", None), ("bigquery", None), ("bigquery", "ladybug")])
def test_the_image_delegates_unloaded_current_to_python_onboard(image, tmp_path, store, marked):
    h, r, calls = onboard_without_units(image, tmp_path, store, marked)
    assert r.returncode == 0, r.stderr
    assert calls == [["onboard"]]


def test_set_up_seed_release_and_serve_through_the_entry_script_on_a_store(image, tmp_path):
    store = open_store(str(tmp_path / "store"))
    r, calls = image("seed")
    assert r.returncode == 2 and "run setup-client" in r.stderr and calls == [], "no client set up: nothing runs"
    r, _ = image("setup-client", "CLIENT=acme", f"PEOPLE={PEOPLE}", "ASKER=analyst")
    assert r.returncode == 0, r.stderr
    r, _ = image("put-example", "EXAMPLE=acme")
    assert r.returncode == 0 and store.get("clients/acme/pack.yaml") == b"pack", r.stderr
    r, calls = image("seed")
    assert r.returncode == 0, r.stderr
    assert [c["args"] for c in calls] == [["estate"]] and calls[0]["pack"], "the pack pulled before make ran"
    assert store.get("landing/acme/current") == b"releases/a1b2c3d4e5f6a7b8\n"
    assert "build/stores/acme/run/tokens.json" not in names(store.get("work/acme.tar.gz"))
    r, calls = image("serve", app="service")
    assert r.returncode == 0 and "nothing released for acme: release first" in r.stdout
    assert [call["args"] for call in calls] == [["serve", "HOST=0.0.0.0"]]
    work = store.get("work/acme.tar.gz")
    r, calls = image("sign-off", "PRINCIPAL=sam@example.com", env={"MAKE_EXIT": "2"}, app="job-2")
    assert r.returncode == 2 and names(store.get("work/acme.tar.gz")) == names(work), \
        "a failed job still saves its work"
    r, calls = image("release", app="job-3")
    assert r.returncode == 0, r.stderr
    assert calls[0]["summary"] and calls[0]["landed"], "a fresh job sees seed's work and the landing"
    assert store.get("releases/acme/current") == b"a1\n" and "releases/acme/a1.tar.gz" in store.list("releases/")
    assert [call["args"][0] for call in calls] == ["release", "_stored-after", "ab", "baseline"]
    assert calls[1]["args"] == ["_stored-after", "COMMAND=release"]
    assert all(arg == "BASELINE=" for arg in calls[2]["args"][1:])
    baseline = yaml.safe_load(store.get("deploy/profiles.yaml"))["profiles"]["acme"]["baseline"]
    assert baseline.startswith("../clients/acme/baselines/ladybug-")
    assert store.get(baseline.removeprefix("../")) is not None
    assert f"release: baseline re-recorded: {baseline.removeprefix('../clients/acme/')}, from run 20261021T000000Z-abcdef; the profile names it, people and asker kept" in r.stdout
    assert store.get("deploy/acme/identities.yaml") is None
    assert yaml.safe_load(store.get("deploy/profiles.yaml"))["profiles"]["acme"]["asker"] == "analyst"
    r, _ = image("passwords", app="job-4")
    assert r.returncode == 0 and store.get("secrets/acme/passwords.txt") == b"ann@example.com pw", r.stderr
    assert "ann@example.com pw" not in r.stdout + r.stderr, "never a password on the output"
    r, calls = image("serve", app="service")
    assert r.returncode == 0, r.stderr
    assert calls == [{"args": ["serve", "HOST=0.0.0.0"], "landed": False, "summary": True, "current": "a1",
                      "passwords": "0o600", "pack": True}]
    r, _ = image("releases")
    assert r.returncode == 0 and re.search(r"^  a1  current$", r.stdout, re.M), r.stdout


def test_the_image_delegates_a_different_graph_store_to_python_onboard(image, tmp_path):
    """The live run on #97: current's folder held Ladybug's graph and LOADED entry, no BigQuery pointer; on
    GRAPH_STORE=bigquery that is not loaded, so the image's onboard loads and checks it."""
    h, r, calls = onboard_without_units(image, tmp_path, "ladybug", "ladybug", on="bigquery")
    assert r.returncode == 0, r.stderr
    assert calls == [["onboard"]]


@pytest.mark.parametrize("onboard_exit", [1, 2])
def test_a_failed_onboard_preserves_saved_work_and_exit_code(image, tmp_path, onboard_exit):
    store = open_store(str(tmp_path / "store"))
    assert image("setup-client", "CLIENT=acme", f"PEOPLE={PEOPLE}", "ASKER=analyst")[0].returncode == 0
    assert image("put-example", "EXAMPLE=acme")[0].returncode == 0
    assert image("seed")[0].returncode == 0
    saved = store.get("work/acme.tar.gz")
    failed, calls = image("onboard", app="failed-onboard", env={"MAKE_EXIT": str(onboard_exit), "MAKE_FAIL_AFTER": "1"})
    assert failed.returncode == onboard_exit
    assert [call["args"] for call in calls] == [["onboard"]]
    assert store.get("work/acme.tar.gz") == saved
    assert (tmp_path / "failed-onboard/build/acme/changed-by-onboard.txt").is_file()
    record = json.loads(store.get(f"runs/acme/onboard/{ONBOARD_RUN_ID}.json"))
    assert record["outcome"] == "failed"
    assert store.get("jobs/acme/lease.json") == b"{}"


def test_a_failed_release_does_not_save_its_current_swap(image, tmp_path):
    store = open_store(str(tmp_path / "store"))
    assert image("setup-client", "CLIENT=acme", f"PEOPLE={PEOPLE}", "ASKER=analyst")[0].returncode == 0
    before = store.get("work/acme.tar.gz")
    failed, calls = image("release", app="failed-release", env={"MAKE_EXIT": "1", "MAKE_FAIL_AFTER": "1"})
    assert failed.returncode == 1 and [call["args"][0] for call in calls] == ["release"]
    assert (tmp_path / "failed-release/build/artifacts/acme/current").is_symlink()
    assert store.get("work/acme.tar.gz") == before


def test_after_failure_changes_success_to_failure_but_never_masks_the_commands_failure(image, tmp_path):
    assert image("setup-client", "CLIENT=acme", f"PEOPLE={PEOPLE}", "ASKER=analyst")[0].returncode == 0
    store = tmp_path / "store"
    succeeded, _ = image("sign-off", app="after-success", env={"MAKE_BREAK_STORE": "1"})
    for root, dirs, files in os.walk(store):
        Path(root).chmod(0o700)
    assert succeeded.returncode != 0
    failed, _ = image("sign-off", app="after-failure",
                      env={"MAKE_BREAK_STORE": "1", "MAKE_EXIT": "2"})
    for root, dirs, files in os.walk(store):
        Path(root).chmod(0o700)
    assert failed.returncode == 2


@pytest.mark.parametrize("graph_store", ["ladybug", "bigquery"])
def test_the_image_records_the_profile_s_own_baseline_on_this_setup_and_puts_it_in_the_store(image, tmp_path, graph_store):
    """Record the baseline and point the profile at it, keeping the existing people and asker."""
    store = open_store(str(tmp_path / "store"))
    assert image("setup-client", "CLIENT=acme", f"PEOPLE={PEOPLE}", "ASKER=analyst")[0].returncode == 0
    assert image("put-example", "EXAMPLE=acme")[0].returncode == 0
    model_env = {"GRAPH_STORE": graph_store, "MODEL_PROVIDER": "vertex-gemini", "TAGGER_PROVIDER": "model",
                 "MODEL_HIGH_PROVIDER": "vertex-gemini", "MODEL_HIGH_NAME": "high",
                 "MODEL_MEDIUM_PROVIDER": "vertex-gemini", "MODEL_MEDIUM_NAME": "medium",
                 "MODEL_LOW_PROVIDER": "vertex-gemini", "MODEL_LOW_NAME": "low", "MOCK_MODE": "careful"}
    r, calls = image("baseline", env=model_env)
    assert r.returncode == 0, r.stderr
    run_id = "20261021T000000Z-abcdef"
    assert [c["args"][0] for c in calls] == ["ab", "baseline"] and calls[0]["pack"], "the pack pulled before make ran"
    assert calls[0]["model"] == {"MODEL_PROVIDER": "none", "TAGGER_PROVIDER": "rules", "MODEL_HIGH_PROVIDER": "none",
                                  "MODEL_HIGH_NAME": None, "MODEL_MEDIUM_PROVIDER": "none", "MODEL_MEDIUM_NAME": None,
                                  "MODEL_LOW_PROVIDER": "none", "MODEL_LOW_NAME": None, "MOCK_MODE": None}
    said = dict(a.split("=", 1) for a in calls[1]["args"][1:])
    file = said["BASELINE"].removeprefix("clients/acme/baselines/")
    assert said["RUN"] == run_id and re.fullmatch(rf"{graph_store}-\d{{8}}T\d{{6}}Z\.json", file), said
    assert json.loads(store.get(f"clients/acme/baselines/{file}")) == {"run": run_id, "name": said["NAME"]}
    assert "work/acme.tar.gz" in store.list("work/")
    assert "setup-client " not in r.stdout
    assert f"baseline: baselines/{file}, from run {run_id}; the profile names it, people and asker kept" in r.stdout
    assert store.get("deploy/acme/identities.yaml") is None
    assert yaml.safe_load(store.get("deploy/profiles.yaml"))["profiles"]["acme"]["asker"] == "analyst"
    registry = yaml.safe_load(store.get("deploy/profiles.yaml"))
    assert registry["profiles"]["acme"]["baseline"] == f"../clients/acme/baselines/{file}"


def test_the_image_baseline_publishes_nothing_when_the_run_fails(image, tmp_path):
    store = open_store(str(tmp_path / "store"))
    assert image("setup-client", "CLIENT=acme", f"PEOPLE={PEOPLE}", "ASKER=analyst")[0].returncode == 0
    assert image("put-example", "EXAMPLE=acme")[0].returncode == 0
    r, calls = image("baseline", env={"MAKE_EXIT": "2"})
    assert r.returncode == 2 and [c["args"][0] for c in calls] == ["ab"]
    assert store.list("clients/acme/baselines/") == [] and store.get("work/acme.tar.gz") is None


@pytest.mark.parametrize("rebuilt,landed", [(True, False), (False, False), (None, False), (False, True)])
def test_onboard_pushes_landing_only_after_this_runs_certification(tmp_path, store_env, monkeypatch, rebuilt, landed):
    setup(monkeypatch)
    app = built(tmp_path / "app")
    store = open_store(str(store_env))
    prof = objstore.pull_config(store, app)
    touch(prof.clients / prof.client / "pack.lock", "refreshed lock")
    if rebuilt is not None:
        touch(prof.run_dir() / objstore.ONBOARD_RECORD, json.dumps({"estate": {"rebuilt": rebuilt}, "landed": landed}))
    touch(prof.store / prof.client / "a1" / "CHANGES", json.dumps({"estate": {"rebuilt": True}}))
    touch(prof.run_dir() / objstore.ONBOARD_RUN, json.dumps({"outcome": "passed", "started_at": "2026-10-31T12:00:00Z"}))
    objstore.after("onboard", app)
    assert bool(store.list("landing/acme/")) is landed
    assert store.get("clients/acme/pack.lock") == b"refreshed lock"
    assert store.get("work/acme.tar.gz") is not None


def test_before_onboard_clears_the_previous_runs_rebuild_record(tmp_path, store_env, monkeypatch):
    setup(monkeypatch)
    app = built(tmp_path / "app")
    store = open_store(str(store_env))
    prof = objstore.pull_config(store, app)
    touch(prof.run_dir() / objstore.ONBOARD_RECORD, json.dumps({"estate": {"rebuilt": True}}))
    objstore.after("seed", app)
    job = tmp_path / "job"
    objstore.before("onboard", job)
    assert not (job / prof.run_dir().relative_to(app) / objstore.ONBOARD_RECORD).exists()


@pytest.fixture
def stored_job_request(tmp_path, monkeypatch):
    store = released_role_store(tmp_path, [
        {"id": "steward", "processes": ["certification"]},
        {"id": "engineer", "processes": ["building"]},
    ])
    identities = tmp_path / "identities.yaml"
    identities.write_text("principals:\n  - {principal: sam@example.com, role: steward}\n  - {principal: ann@example.com, role: engineer}\n")
    prof = SimpleNamespace(client="meridian", clients=tmp_path / "clients", identities=identities,
                           run_dir=lambda: tmp_path / "run")
    seed_people(prof)
    for model_hash in ("a" * 64, "b" * 64):
        loaded_candidate(prof, tmp_path, [{"id": "steward", "processes": ["certification"]},
                                          {"id": "engineer", "processes": ["building"]}], model_hash)
    calls = []

    def execute(args, **kwargs):
        calls.append(args)
        if "PREVIOUS=yes" in args:
            store.put("releases/meridian/current", f"{'b' * 64}\n".encode())
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(objstore, "the_store", lambda: store)
    monkeypatch.setattr(objstore, "pull_config", lambda *args: prof)
    monkeypatch.setattr(objstore.subprocess, "run", execute)
    request = {"id": "20261024T120000Z-abcdef", "command": "release", "principal": "sam@example.com", "hash": None,
               "previous": True, "timestamp": "2026-10-24T12:00:00Z"}
    return store, request, calls


def test_job_request_normalizes_username_and_records_the_canonical_actor(stored_job_request, tmp_path):
    store, request, calls = stored_job_request
    request["principal"] = " ＳＡＭ@EXAMPLE.COM "
    store.put("clients/meridian/onboard/request.json", json.dumps(request).encode())
    objstore.onboard_request(tmp_path)
    assert calls == [["sh", str(tmp_path / "entrypoint.sh"), "release", "PREVIOUS=yes"]]
    assert json.loads(store.get("clients/meridian/onboard/job.json"))["by"] == "sam@example.com"


@pytest.mark.parametrize("principal", ["role:steward", "access_admin", "sam@\nexample.com"])
def test_job_request_refuses_invalid_usernames_before_execution(stored_job_request, tmp_path, principal):
    store, request, calls = stored_job_request
    request["principal"] = principal
    store.put("clients/meridian/onboard/request.json", json.dumps(request).encode())
    with pytest.raises(Refused, match="principal is not valid"):
        objstore.onboard_request(tmp_path)
    assert calls == []
    assert json.loads(store.get("clients/meridian/onboard/job.json"))["by"] is None


@pytest.mark.parametrize("principal", ["sam@example.com", "ann@example.com", "unknown@example.com"])
def test_ledger_rollback_request_requires_a_steward_and_passes_previous(stored_job_request, tmp_path, principal):
    store, request, calls = stored_job_request
    request["principal"] = principal
    store.put("clients/meridian/onboard/request.json", json.dumps(request).encode())
    if principal == "sam@example.com":
        objstore.onboard_request(tmp_path)
        assert calls == [["sh", str(tmp_path / "entrypoint.sh"), "release", "PREVIOUS=yes"]]
        job = json.loads(store.get("clients/meridian/onboard/job.json"))
        assert job["previous"] is True and job["hash"] == "b" * 64 and job["state"] == "done"
        assert job["checked_hash"] is None and job["checks_id"] is None
    else:
        with pytest.raises(Refused, match="its roles do not let them certify"):
            objstore.onboard_request(tmp_path)
        assert not calls
    assert store.get("clients/meridian/onboard/request.json") == b""


@pytest.mark.parametrize("fields,reason", [
    ({"previous": "yes"}, "previous must be a boolean"),
    ({"previous": 1}, "previous must be a boolean"),
    ({"previous": None}, "previous must be a boolean"),
    ({"command": "sign-off"}, "previous is only supported for release"),
    ({"command": "onboard"}, "previous is only supported for release"),
    ({"command": "improve"}, "previous is only supported for release"),
    ({"hash": "a" * 64}, "previous and hash cannot be supplied together"),
    ({"previous": False}, "sign-off and release require a model hash"),
])
def test_invalid_previous_requests_are_consumed_without_execution(stored_job_request, tmp_path, fields, reason):
    store, request, calls = stored_job_request
    request.update(fields)
    store.put("clients/meridian/onboard/request.json", json.dumps(request).encode())
    with pytest.raises(Refused, match=reason):
        objstore.onboard_request(tmp_path)
    assert not calls
    assert store.get("clients/meridian/onboard/request.json") == b""
    assert json.loads(store.get("clients/meridian/onboard/job.json"))["state"] == "failed"


@pytest.mark.parametrize("backend", [False, True])
@pytest.mark.parametrize("same_candidate", [False, True])
@pytest.mark.parametrize("exit_code", [0, 2])
def test_sign_off_and_release_keep_only_the_same_candidates_changes(
        stored_job_request, tmp_path, monkeypatch, backend, same_candidate, exit_code):
    from kge.adapters import appstate
    store, request, _ = stored_job_request
    changes = {"lock": {"written": False, "changes": []},
               "estate": {"rebuilt": True, "why": "fixture-changed", "version": 2, "fixture_sha256": "f" * 64},
               "reloaded": True}
    held = {"hash": "a" * 64, "changes": changes, "step": "checks", "state": "done"}
    saved = []

    def save(value):
        held.clear()
        held.update(json.loads(json.dumps(value)))
        saved.append(dict(held))

    if backend:
        monkeypatch.setattr(appstate, "job_backend", lambda prof: SimpleNamespace(job=lambda: held, save_job=save))
    else:
        store.put("clients/meridian/onboard/job.json", json.dumps(held).encode())
        original = store.put

        def put(key, data):
            if key == "clients/meridian/onboard/job.json":
                saved.append(json.loads(data))
            return original(key, data)

        monkeypatch.setattr(store, "put", put)
    monkeypatch.setattr(objstore.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(
        returncode=exit_code, stdout="", stderr=""))
    request.update(previous=False, hash=("a" if same_candidate else "b") * 64)
    for command in ("sign-off", "release"):
        request["command"] = command
        store.put("clients/meridian/onboard/request.json", json.dumps(request).encode())
        if exit_code:
            with pytest.raises(Refused, match="job execution failed"):
                objstore.onboard_request(tmp_path)
        else:
            objstore.onboard_request(tmp_path)
    assert len(saved) == 4
    assert all(value.get("changes") == (changes if same_candidate else None) for value in saved)


@pytest.mark.parametrize("step,previous", [("release", False), ("release", True), ("rollback", False)],
                         ids=["released", "rolled-back", "rollback-step"])
@pytest.mark.parametrize("backend", [False, True], ids=["object-store", "app-state"])
def test_signoff_after_a_completed_release_does_not_reopen_its_changes(stored_job_request, tmp_path, monkeypatch,
                                                                   step, previous, backend):
    from kge.adapters import appstate

    store, request, _ = stored_job_request
    model_hash = "a" * 64
    held = {"hash": model_hash, "step": step, "state": "done", "previous": previous,
            "changes": {"estate": {"rebuilt": True}, "reloaded": True}}
    saved = []

    def save(value):
        held.clear()
        held.update(json.loads(json.dumps(value)))
        saved.append(dict(held))

    if backend:
        monkeypatch.setattr(appstate, "job_backend", lambda prof: SimpleNamespace(job=lambda: held, save_job=save))
    else:
        store.put("clients/meridian/onboard/job.json", json.dumps(held).encode())
        put = store.put

        def put_job(key, data):
            if key == "clients/meridian/onboard/job.json":
                saved.append(json.loads(data))
            return put(key, data)

        monkeypatch.setattr(store, "put", put_job)
    request.update(command="sign-off", previous=False, hash=model_hash)
    store.put("clients/meridian/onboard/request.json", json.dumps(request).encode())
    objstore.onboard_request(tmp_path)
    assert len(saved) == 2 and all(job.get("changes") is None for job in saved)
    assert saved[-1]["state"] == "done" and saved[-1]["hash"] == model_hash


def test_ledger_rollback_does_not_inherit_the_candidates_changes(stored_job_request, tmp_path):
    store, request, _ = stored_job_request
    store.put("clients/meridian/onboard/job.json", json.dumps({
        "hash": "b" * 64, "changes": {"estate": {"rebuilt": True}}, "step": "checks", "state": "done"}).encode())
    store.put("clients/meridian/onboard/request.json", json.dumps(request).encode())
    objstore.onboard_request(tmp_path)
    assert "changes" not in json.loads(store.get("clients/meridian/onboard/job.json"))


@pytest.mark.parametrize("backend", [False, True])
def test_current_already_clears_the_stored_runs_changes(stored_job_request, tmp_path, monkeypatch, backend):
    from kge.adapters import appstate
    from kge.graph import release

    store, request, _ = stored_job_request
    model_hash = "a" * 64
    stale = {"estate": {"rebuilt": True}, "reloaded": True}
    held = {"hash": model_hash, "step": "checks", "state": "done", "changes": stale}

    def save(value):
        held.clear()
        held.update(json.loads(json.dumps(value)))

    if backend:
        monkeypatch.setattr(appstate, "job_backend", lambda prof: SimpleNamespace(job=lambda: held, save_job=save))
    else:
        store.put("clients/meridian/onboard/job.json", json.dumps(held).encode())
    request.update(command="onboard", previous=False, hash=None, principal="ann@example.com")
    store.put("clients/meridian/onboard/request.json", json.dumps(request).encode())
    monkeypatch.setattr(objstore.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(
        returncode=0, stdout=f"onboard: candidate {model_hash}\nonboard: current already\n", stderr=""))
    monkeypatch.setattr(objstore.subprocess, "check_output", lambda *args, **kwargs: f"/tmp/{model_hash}\n")
    monkeypatch.setattr(release, "state", lambda *args: {"changes": stale, "gate": {"passed": True}})
    objstore.onboard_request(tmp_path)
    job = held if backend else json.loads(store.get("clients/meridian/onboard/job.json"))
    assert job["state"] == "done" and job["hash"] == model_hash and job["changes"] is None


def test_entrypoint_passes_ledger_rollback_to_the_python_command(image, tmp_path):
    assert image("setup-client", "CLIENT=acme", f"PEOPLE={PEOPLE}", "ASKER=analyst")[0].returncode == 0
    pull(open_store(str(tmp_path / "store")), "deploy/", tmp_path / "app/deploy")
    result, calls = image("release", "PREVIOUS=yes", store=False)
    assert result.returncode == 0, result.stderr
    assert calls[0]["args"] == ["release", "PREVIOUS=yes"]
    assert [call["args"][0] for call in calls] == ["release", "ab", "baseline"]


@pytest.mark.parametrize("outcome", [None, "gate failed", "failed", "refused", "not checked"])
def test_after_onboard_refuses_uncommitted_work_without_publishing(tmp_path, store_env, monkeypatch, outcome):
    setup(monkeypatch)
    app = built(tmp_path / "app")
    store = open_store(str(store_env))
    prof = objstore.pull_config(store, app)
    touch(prof.clients / prof.client / "pack.lock", "not committed")
    touch(prof.run_dir() / objstore.ONBOARD_RECORD, json.dumps({"landed": True}))
    if outcome is not None:
        touch(prof.run_dir() / objstore.ONBOARD_RUN, json.dumps({"outcome": outcome}))
    before = {key: store.get(key) for key in store.list()}
    with pytest.raises(Refused, match="onboard has no successful run to publish"):
        objstore.after("onboard", app)
    assert {key: store.get(key) for key in store.list()} == before


def test_after_warns_on_a_lease_release_race_without_failing_completed_work(tmp_path, store_env, monkeypatch, capsys):
    from kge.pipeline import lease

    setup(monkeypatch)
    app = built(tmp_path / "app")
    store = open_store(str(store_env))
    prof = objstore.pull_config(store, app)
    touch(prof.run_dir() / lease.LEASE_FILE, json.dumps({
        "client": "acme", "command": "seed", "token": "held", "started_at": "start", "expires_at": "end"}))

    def lost_race(_store, _held):
        raise StoreError("object changed concurrently")

    monkeypatch.setattr(lease, "release", lost_race)
    objstore.after("seed", app)
    assert store.get("work/acme.tar.gz") is not None
    assert store.get("landing/acme/current") == b"releases/a1b2c3d4e5f6a7b8\n"
    assert not (prof.run_dir() / lease.LEASE_FILE).exists()
    assert "warning: could not release the finished job's lease: object changed concurrently" in capsys.readouterr().out


@pytest.mark.parametrize("stored", [None, b'{"ann@example.com":"retained hash"}'])
def test_before_passwords_reads_existing_hashes_for_merge_and_removes_stale_local_hashes(
        tmp_path, store_env, monkeypatch, stored):
    setup(monkeypatch)
    app = tmp_path / "app"
    store = open_store(str(store_env))
    prof = objstore.pull_config(store, app)
    path = touch(prof.run_dir() / "passwords.json", '{"stale":"old hash"}')
    if stored is not None:
        store.put("secrets/acme/passwords.json", stored)
    objstore.before("passwords", app)
    assert (path.read_bytes() if path.exists() else None) == stored
    if stored is not None:
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
    objstore.failed("passwords", app)
    assert store.get("jobs/acme/lease.json") == b"{}"


def test_setup_client_refuses_an_existing_registry_without_profiles(store_env, monkeypatch):
    store = open_store(str(store_env))
    store.put("deploy/profiles.yaml", b"default: acme\n")
    before = {key: store.get(key) for key in store.list()}
    with pytest.raises(Refused, match="holds no profiles"):
        setup(monkeypatch)
    assert {key: store.get(key) for key in store.list()} == before


@pytest.mark.parametrize("failed_target", ["ab", "baseline"])
def test_release_keeps_published_release_and_names_baseline_failure(image, tmp_path, failed_target):
    store = open_store(str(tmp_path / "store"))
    assert image("setup-client", "CLIENT=acme", f"PEOPLE={PEOPLE}", "ASKER=analyst")[0].returncode == 0
    before_people = store.get("deploy/acme/identities.yaml")
    result, calls = image("release", env={"MAKE_EXIT": "2", "MAKE_EXIT_TARGET": failed_target})
    assert result.returncode == 2
    assert [call["args"][0] for call in calls] == (["release", "_stored-after", "ab"] if failed_target == "ab" else ["release", "_stored-after", "ab", "baseline"])
    assert store.get("releases/acme/current") == b"a1\n"
    assert store.get("releases/acme/a1.tar.gz") is not None
    assert "release: a1 is released; recording its baseline failed: exit 2: run baseline" in result.stderr
    assert store.list("clients/acme/baselines/") == []
    assert store.get("deploy/acme/identities.yaml") == before_people
    assert yaml.safe_load(store.get("deploy/profiles.yaml"))["profiles"]["acme"]["baseline"] is None
    assert store.get("jobs/acme/lease.json") == b"{}"


@pytest.mark.parametrize("command", ["sign-off", "release"])
def test_candidate_roles_use_live_table_grants_and_ignore_changed_seed(monkeypatch, tmp_path, command):
    store = released_role_store(tmp_path, [{"id": "old_reader", "processes": ["querying"]}])
    identities = touch(tmp_path / "identities.yaml", "principals:\n  - {principal: ann@example.com, role: new_certifier}\n")
    prof = SimpleNamespace(client="meridian", clients=tmp_path / "clients", identities=identities,
                           run_dir=lambda: tmp_path / "run")
    seed_people(prof)
    loaded_candidate(prof, tmp_path, [{"id": "new_certifier", "processes": ["certification"]}])
    identities.write_text("principals:\n  - {principal: ann@example.com, role: old_reader}\n")
    store.put("clients/meridian/onboard/request.json", json.dumps({
        "id": "20261031T120000Z-abcdef", "command": command, "principal": "ann@example.com", "hash": "a" * 64,
        "timestamp": "2026-10-31T12:00:00Z"}).encode())
    monkeypatch.setattr(objstore, "the_store", lambda: store)
    monkeypatch.setattr(objstore, "pull_config", lambda *args: prof)
    calls = []

    def execute(args, **kwargs):
        calls.append(args)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(objstore.subprocess, "run", execute)
    objstore.onboard_request(tmp_path)
    expected = ["sh", str(tmp_path / "entrypoint.sh"), command]
    if command == "sign-off":
        expected.append("PRINCIPAL=ann@example.com")
    assert calls == [[*expected, f"HASH={'a' * 64}"]]
    assert json.loads(store.get("clients/meridian/onboard/job.json"))["state"] == "done"
    access_state(prof).access_delete("ann@example.com", by="operator@example.com", via="job")
    store.put("clients/meridian/onboard/request.json", json.dumps({
        "id": "20261031T120001Z-abcdef", "command": command, "principal": "ann@example.com", "hash": "a" * 64,
        "timestamp": "2026-10-31T12:00:01Z"}).encode())
    with pytest.raises(Refused, match="roles do not let them certify"):
        objstore.onboard_request(tmp_path)
    assert len(calls) == 1


def release_entry(prof):
    """A release made after the upgrade has a distinct archive and a matching kept ledger entry."""
    from kge.adapters import released

    release_id = "20261031T120000Z-abcdef"
    released.write(prof.store / prof.client / "a1", "a1", None, None, release=release_id)
    identity = prof.at("a1").serve().identity()
    archive = f"releases/{prof.client}/a1/{release_id}.tar.gz"
    ledger = [{"identity": identity, "tarball": archive}]
    touch(prof.store / prof.client / "kept.json", json.dumps(ledger))
    return identity, archive, ledger


def test_publish_keeps_per_release_archive_and_fetch_uses_its_identity(tmp_path, store_env, monkeypatch):
    setup(monkeypatch)
    app = built(tmp_path / "app")
    store = Recording(store_env)
    prof = objstore.pull_config(store, app)
    identity, archive, ledger = release_entry(prof)
    objstore.publish(store, app, prof)
    assert store.order == [archive, "releases/acme/a1.tar.gz", "releases/acme/ledger.json",
                           "releases/acme/identity.json", "releases/acme/current"]
    assert store.get(archive) == store.get("releases/acme/a1.tar.gz")
    assert json.loads(store.get("releases/acme/identity.json")) == identity
    assert json.loads(store.get("releases/acme/ledger.json")) == ledger
    store.put("releases/acme/a1.tar.gz", b"a later same-hash release is not this identity")
    objstore.before("serve", tmp_path / "fresh")
    assert (tmp_path / "fresh/build/stores/acme/a1/graph.ladybug").read_text() == "graph"
    assert os.readlink(tmp_path / "fresh/build/artifacts/acme/current") == "a1"


@pytest.mark.parametrize("previous", [True, False], ids=["previous", "named-hash"])
def test_rollback_publish_reuses_archive_and_restores_named_store_keys(tmp_path, store_env, monkeypatch, previous):
    setup(monkeypatch)
    app = built(tmp_path / "app")
    store = Recording(store_env)
    prof = objstore.pull_config(store, app)
    identity, archive, ledger = release_entry(prof)
    original = tarball(app, objstore.release_paths(prof, "a1"))
    store.put(archive, original)
    store.put("clients/acme/obsolete.yaml", b"newer input")
    restored = touch(app / "restored.lock", "earlier lock")
    touch(prof.run_dir() / "rollback.json", json.dumps({
        "hash": "a1", "release": identity["release"],
        "restored": [{"key": "clients/acme/pack.lock", "file": str(restored)}],
        "removed": ["clients/acme/obsolete.yaml"]}))
    touch(prof.store / prof.client / "a1/graph.ladybug", "newer work must never replace the archive")
    monkeypatch.setenv("PREVIOUS", "yes" if previous else "")

    def cannot_build(*args):
        pytest.fail("rollback rebuilt a tarball")

    monkeypatch.setattr(objstore, "tarball", cannot_build)
    store.order.clear()
    objstore.publish(store, app, prof)
    assert archive not in store.order and "releases/acme/a1.tar.gz" not in store.order
    assert store.get(archive) == original
    assert store.get("clients/acme/pack.lock") == b"earlier lock"
    assert store.get("clients/acme/obsolete.yaml") is None
    assert store.get("releases/acme/current") == b"a1\n"
    assert json.loads(store.get("releases/acme/identity.json")) == identity
    assert json.loads(store.get("releases/acme/ledger.json")) == ledger


def test_failed_onboard_publishes_only_its_run_and_margin_report(tmp_path, store_env, monkeypatch):
    setup(monkeypatch)
    app = built(tmp_path / "app")
    store = open_store(str(store_env))
    prof = objstore.pull_config(store, app)
    record = {"started_at": "2026-10-31T12:00:00Z", "outcome": "gate failed", "step": "margin",
              "gate": {"passed": False, "run_id": "failed-margin"}}
    touch(prof.run_dir() / objstore.ONBOARD_RUN, json.dumps(record))
    touch(prof.paths()["runs"] / "failed-margin/report.md", "V01 FAIL: the gate refused")
    touch(prof.clients / prof.client / "pack.lock", "uncommitted")
    before = {key: store.get(key) for key in store.list()}
    objstore.failed("onboard", app)
    key = f"runs/acme/onboard/{ONBOARD_RUN_ID}"
    after = {name: store.get(name) for name in store.list()}
    assert after == {**before, key + ".json": json.dumps(record).encode(),
                     key + "/report.md": b"V01 FAIL: the gate refused"}



def test_released_roles_follow_the_restored_release_identity_at_the_same_hash(tmp_path):
    earlier = {"id": "engineer", "processes": ["building"]}
    latest = {"id": "engineer", "processes": ["certification"]}
    store = released_role_store(tmp_path, [earlier])
    archive = f"releases/meridian/{'a' * 64}.tar.gz"
    store.put(f"releases/meridian/{'a' * 64}/earlier.tar.gz", store.get(archive))
    later_store = released_role_store(tmp_path / "later", [latest])
    store.put(archive, later_store.get(archive))
    store.put("releases/meridian/identity.json", json.dumps({"hash": "a" * 64, "release": "earlier"}).encode())
    identities = touch(tmp_path / "identities.yaml", "principals:\n  - {principal: ann@example.com, role: engineer}\n")
    prof = SimpleNamespace(client="meridian", identities=identities)
    seed_people(prof)
    roles = objstore.released_roles(store, prof)
    assert roles["ann@example.com"]["processes"] == ["building"]


def test_failed_request_without_a_run_record_cannot_inherit_an_earlier_verdict(stored_job_request, tmp_path, monkeypatch):
    from kge.graph import release
    store, request, _ = stored_job_request
    model_hash = "a" * 64
    request.update(command="onboard", previous=False, hash=None, principal="ann@example.com")
    store.put("clients/meridian/onboard/request.json", json.dumps(request).encode())
    monkeypatch.setattr(objstore.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(
        returncode=2, stdout=f"onboard: candidate {model_hash}\n", stderr=""))
    monkeypatch.setattr(release, "state", lambda *args: {
        "changes": {"estate": {"rebuilt": True}}, "gate": {"passed": True, "run_id": "old"}})
    with pytest.raises(Refused, match="job execution failed"):
        objstore.onboard_request(tmp_path)
    job = json.loads(store.get("clients/meridian/onboard/job.json"))
    assert job["state"] == "failed" and job["hash"] == model_hash
    assert job["gate"] is None and job["report"] is None and job["changes"] is None
    assert "report:" not in job["message"]
