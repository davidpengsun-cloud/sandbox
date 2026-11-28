import json
import os
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from kge.adapters.model import QuotaUnavailable
from kge.adapters.objstore import open_store
from kge.pipeline import access_table, quota_job


JOB = "20261126T001200Z-abcdef"
NOW = datetime(2026, 11, 26, 0, 12, tzinfo=timezone.utc).timestamp()


@pytest.fixture
def profile(tmp_path, monkeypatch):
    monkeypatch.delenv("STORE_URL", raising=False)
    return SimpleNamespace(client="meridian", run_dir=lambda: tmp_path)


@pytest.fixture
def access(monkeypatch):
    held = {"roles": {"analyst"}, "people": {"alice"}, "allowed": True}
    reader = SimpleNamespace(people=lambda: held["people"], roles_of=lambda who: held["roles"],
                             role_of_person=lambda who, definitions: {"writes": ["wealth"]},
                             permissions=lambda who, definitions: {"run": held["allowed"], "improve": held["allowed"]})
    monkeypatch.setattr(access_table.Access, "for_profile", lambda prof: reader)
    monkeypatch.setattr(access_table, "served_roles", lambda prof: {"analyst": {}})
    return held


def local_job(profile, *, owner="alice", state="running", timestamp="2026-11-26T00:12:00Z", step="checks"):
    path = profile.run_dir() / "onboard" / "job.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"id": JOB, "by": owner, "state": state, "started_at": timestamp, "step": step}))


def test_job_capability_binds_only_server_job_identity_and_current_roles(profile, access):
    local_job(profile)
    marker = quota_job.issue(profile, JOB, clock=lambda: NOW)
    caller = quota_job.caller_for_job(profile, marker, clock=lambda: NOW)
    assert (caller.client, caller.kind, caller.identifier, caller.roles) == ("meridian", "person", "alice", ("analyst",))
    access["roles"] = {"steward"}
    assert quota_job.caller_for_job(profile, marker, clock=lambda: NOW).roles == ("steward",)
    access["allowed"] = False
    with pytest.raises(QuotaUnavailable):
        quota_job.caller_for_job(profile, marker, clock=lambda: NOW)


def test_running_job_reauthorizes_before_each_model_call(profile, access, monkeypatch, tmp_path):
    from kge.adapters.model import quota_caller
    from kge.adapters.model.quota import admit
    monkeypatch.setenv("MODEL_RATE_LIMITS", '{"default":10}')
    monkeypatch.setenv("MODEL_QUOTA_STORE_URL", str(tmp_path / "quota"))
    local_job(profile)
    marker = quota_job.issue(profile, JOB, clock=lambda: NOW)
    with quota_caller(lambda: quota_job.caller_for_job(profile, marker, clock=lambda: NOW)):
        admit("test:model", "plan")
        access["allowed"] = False
        with pytest.raises(QuotaUnavailable):
            admit("test:model", "plan")
    access["allowed"] = True
    access["people"] = set()
    with pytest.raises(QuotaUnavailable):
        quota_job.caller_for_job(profile, marker, clock=lambda: NOW)


def test_job_capability_fails_closed_when_forged_expired_or_job_inactive(profile, access):
    local_job(profile)
    marker = quota_job.issue(profile, JOB, clock=lambda: NOW)
    for bad in ("", JOB + "." + "A" * 43, "other." + marker.split(".")[1]):
        with pytest.raises(QuotaUnavailable):
            quota_job.caller_for_job(profile, bad, clock=lambda: NOW)
    with pytest.raises(QuotaUnavailable):
        quota_job.caller_for_job(profile, marker, clock=lambda: NOW + quota_job.CAP_SECONDS)
    access["people"] = {"alice", "mallory"}
    local_job(profile, owner="mallory")
    with pytest.raises(QuotaUnavailable):
        quota_job.caller_for_job(profile, marker, clock=lambda: NOW)
    local_job(profile, step="improve")
    with pytest.raises(QuotaUnavailable):
        quota_job.caller_for_job(profile, marker, clock=lambda: NOW)
    local_job(profile, state="done")
    with pytest.raises(QuotaUnavailable):
        quota_job.caller_for_job(profile, marker, clock=lambda: NOW)
    local_job(profile, timestamp="2026-11-25T00:12:00Z")
    with pytest.raises(QuotaUnavailable):
        quota_job.caller_for_job(profile, marker, clock=lambda: NOW)
    quota_job.revoke(profile, marker)
    local_job(profile)
    with pytest.raises(QuotaUnavailable):
        quota_job.caller_for_job(profile, marker, clock=lambda: NOW)


def test_stored_job_identity_comes_from_server_owned_record(profile, access, monkeypatch, tmp_path):
    monkeypatch.setenv("STORE_URL", str(tmp_path / "store"))
    monkeypatch.delenv("APP_STATE_BACKEND", raising=False)
    store = open_store(str(tmp_path / "store"))
    key = "clients/meridian/onboard/request.json"
    store.put(key, json.dumps({"id": JOB, "principal": "alice", "command": "onboard", "timestamp": "2026-11-26T00:12:00Z"}).encode())
    status = "clients/meridian/onboard/job.json"
    active = {"id": JOB, "by": "alice", "step": "checks", "started_at": "2026-11-26T00:12:00Z", "state": "running"}
    store.put(status, json.dumps(active).encode())
    marker = quota_job.issue(profile, JOB, clock=lambda: NOW)
    assert quota_job.caller_for_job(profile, marker, clock=lambda: NOW).identifier == "alice"
    for ended in ("done", "failed", "error"):
        store.put(status, json.dumps({**active, "state": ended}).encode())
        with pytest.raises(QuotaUnavailable):
            quota_job.caller_for_job(profile, marker, clock=lambda: NOW)
    store.put(status, json.dumps(active).encode())
    store.put(key, json.dumps({"id": "20261126T001200Z-000000", "principal": "alice"}).encode())
    with pytest.raises(QuotaUnavailable):
        quota_job.caller_for_job(profile, marker, clock=lambda: NOW)


def test_stored_job_rechecks_bigquery_job_state_even_if_request_and_cap_remain(profile, access, monkeypatch, tmp_path):
    from kge.adapters import appstate
    monkeypatch.setenv("STORE_URL", str(tmp_path / "store"))
    store = open_store(str(tmp_path / "store"))
    store.put("clients/meridian/onboard/request.json", json.dumps({"id": JOB, "principal": "alice", "command": "eval", "timestamp": "2026-11-26T00:12:00Z"}).encode())
    state = {"id": JOB, "by": "alice", "step": "eval", "started_at": "2026-11-26T00:12:00Z", "state": "running"}
    monkeypatch.setattr(appstate, "job_backend", lambda prof: SimpleNamespace(job=lambda: dict(state)))
    marker = quota_job.issue(profile, JOB, clock=lambda: NOW)
    assert quota_job.caller_for_job(profile, marker, clock=lambda: NOW).identifier == "alice"
    state["state"] = "done"
    with pytest.raises(QuotaUnavailable):
        quota_job.caller_for_job(profile, marker, clock=lambda: NOW)


def test_standalone_cli_service_is_os_identity_and_bad_environment_cannot_claim_a_person(profile, monkeypatch):
    from kge.adapters.model import current_quota_caller
    from kge.cli import quota_exec
    monkeypatch.setattr(quota_exec, "resolve", lambda: profile)
    monkeypatch.setattr("sys.argv", ["quota_exec", "example.module"])
    seen = []
    monkeypatch.setattr(quota_exec.runpy, "run_module", lambda *a, **kw: seen.append(current_quota_caller()))
    monkeypatch.delenv(quota_job.MARKER, raising=False)
    quota_exec.main()
    assert seen[0].kind == "service" and seen[0].identifier == f"cli-uid-{os.geteuid()}"
    monkeypatch.setenv(quota_job.MARKER, JOB + "." + "A" * 43)
    monkeypatch.setattr("sys.argv", ["quota_exec", "example.module"])
    with pytest.raises(QuotaUnavailable):
        quota_exec.main()
    assert len(seen) == 1


def test_cli_job_wrapper_revalidates_capability_before_each_inference(profile, access, monkeypatch, tmp_path):
    from kge.adapters.model.quota import admit
    from kge.cli import quota_exec
    local_job(profile)
    marker = quota_job.issue(profile, JOB, clock=lambda: NOW)
    monkeypatch.setenv("MODEL_RATE_LIMITS", '{"default":10}')
    monkeypatch.setenv("MODEL_QUOTA_STORE_URL", str(tmp_path / "quotas"))
    monkeypatch.setenv(quota_job.MARKER, marker)
    monkeypatch.setattr(quota_exec, "resolve", lambda: profile)
    verify = quota_job.caller_for_job
    monkeypatch.setattr(quota_exec, "caller_for_job", lambda prof, cap: verify(prof, cap, clock=lambda: NOW))
    monkeypatch.setattr("sys.argv", ["quota_exec", "example.module"])
    seen = []

    def run(*args, **kwargs):
        admit("test:model", "plan")
        seen.append("first")
        access["allowed"] = False
        with pytest.raises(QuotaUnavailable):
            admit("test:model", "plan")

    monkeypatch.setattr(quota_exec.runpy, "run_module", run)
    quota_exec.main()
    assert seen == ["first"]


def test_local_job_launch_passes_unforgeable_capability_and_revokes_after_exit(profile, access, monkeypatch):
    import sys
    from kge.services.onboard import jobs
    monkeypatch.setenv("MODEL_RATE_LIMITS", '{"default":1}')
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    local_job(profile, timestamp=timestamp)
    profile.id = "meridian"
    app = SimpleNamespace(PROFILE=profile)
    job = {"id": JOB, "by": "alice", "state": "running", "started_at": timestamp,
           "step": "checks", "hash": None}
    monkeypatch.setattr(jobs, "save", lambda app, job: None)
    observed = {}

    def launch(command, *, cwd, env, stdout, stderr, text):
        observed["command"] = command
        observed["marker"] = env[quota_job.MARKER]
        observed["caller"] = quota_job.caller_for_job(profile, observed["marker"])
        return SimpleNamespace(wait=lambda: 1)

    monkeypatch.setattr(jobs.subprocess, "Popen", launch)
    jobs.run(app, job, [sys.executable, "-m", "kge.cli", "onboard"])
    assert observed["command"][1:4] == ["-m", "kge.cli.quota_exec", "kge.cli"]
    assert observed["caller"].identifier == "alice"
    assert observed["marker"] not in json.dumps(job)
    with pytest.raises(QuotaUnavailable):
        quota_job.caller_for_job(profile, observed["marker"])
