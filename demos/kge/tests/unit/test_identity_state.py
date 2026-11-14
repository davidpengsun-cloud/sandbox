"""Shared credentials preserve concurrency, access scope and a bounded query budget."""
import os
import stat
import subprocess
import sys
import time
import threading
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest

from kge.adapters.sessionstate import identity_refresher, for_profile as identity_state
from kge.adapters.appstate import bigquery as adapter
from kge.adapters.deadline import ExternalCallError
from kge.adapters.objstore import open_store
from kge.adapters.graphstore.bigquery import dataset_inventory
from kge.pipeline.settings import ROOT, check_service_auth_settings
from kge.services.identity.service import Identities, SignedSessions
from tests import bigquery_fake


@pytest.fixture(params=["store", "bigquery"])
def shared(request, tmp_path, monkeypatch):
    monkeypatch.setenv("SESSION_STATE_BACKEND", request.param)
    monkeypatch.setenv("STORE_URL", str(tmp_path / "store"))
    monkeypatch.setenv("BIGQUERY_PROJECT", bigquery_fake.PROJECT)
    monkeypatch.setenv("BIGQUERY_DATASET_PREFIX", "kge")
    monkeypatch.setenv("SESSION_SECRET", "shared-test-key-" * 3)
    monkeypatch.setattr(adapter, "_IDENTITY_ENSURED", set())
    fake = bigquery_fake.FakeBigQuery()
    sent = []

    def post(self, body):
        sent.append(body["query"])
        return fake.state_post(body)

    monkeypatch.setattr(adapter.BigQueryState, "_post", post)
    now = [100.0]

    def new(client="sample"):
        prof = SimpleNamespace(client=client, run_dir=lambda: tmp_path / client / "run")
        state = identity_state(prof)
        state.identity_clock = lambda: now[0]
        if state._identity_held is not None:
            state.refresh_identity()
        return state

    return new, now, sent, fake


def test_identity_snapshot_cache_has_one_refresh_job_and_no_job_per_request(shared, tmp_path):
    new, now, sent, _ = shared
    state = new()
    ids = Identities(tmp_path / "one", lambda: {"reader@example.com"},
                     SignedSessions(tmp_path / "one", "sample", state=state), state=state)
    token = ids.token_for("reader@example.com")
    sid, _ = ids.open_session(token)
    before = len(sent)
    for _ in range(6):
        now[0] += 4
        for _ in range(20):
            assert ids.resolve(None, sid) == "reader@example.com"
            assert ids.resolve(f"Bearer {token}", None) == "reader@example.com"
        assert len(sent) == before
    state.refresh_identity()
    assert len(sent) - before == (1 if sent else 0)
    snapshot = state.identity_snapshot()
    snapshot["tokens"].clear()
    assert state.identity_snapshot()["tokens"] == {"reader@example.com": token}


def test_sign_out_is_seen_at_three_seconds_and_tokens_remain_client_scoped(shared):
    new, now, _, _ = shared
    instances = [new() for _ in range(3)]
    first, *peers = instances
    assert first.identity_token("reader@example.com", "first-token") == "first-token"
    assert peers[0].identity_token("reader@example.com", "losing-token") == "first-token"
    assert new("other").identity_snapshot() == {"tokens": {}, "signed_out": {}}
    expires = int(time.time()) + 100
    first.identity_sign_out("one", expires)
    assert first.identity_snapshot()["signed_out"] == {"one": expires}
    now[0] += 2.999
    for peer in peers:
        assert "one" not in peer.identity_snapshot()["signed_out"]
    now[0] += .001
    for peer in peers:
        peer.refresh_identity()
        assert peer.identity_snapshot() == {"signed_out": {"one": expires},
                                            "tokens": {"reader@example.com": "first-token"}}


def test_concurrent_tokens_and_sign_outs_keep_every_committed_value(shared):
    new, now, _, _ = shared
    first, second = new(), new()
    expires = int(time.time()) + 100
    with ThreadPoolExecutor(max_workers=2) as workers:
        tokens = list(workers.map(lambda state: state.identity_token("reader@example.com", "candidate-" + str(id(state))),
                                  (first, second)))
        assert tokens[0] == tokens[1]
        list(workers.map(lambda pair: pair[0].identity_sign_out(pair[1], expires), ((first, "one"), (second, "two"))))
    now[0] += 3
    assert new().identity_snapshot() == {"tokens": {"reader@example.com": tokens[0]},
                                        "signed_out": {"one": expires, "two": expires}}


def test_expired_identity_cache_fails_closed_and_does_not_hide_a_storage_failure(shared, monkeypatch):
    new, now, sent, _ = shared
    state = new()

    def unavailable():
        raise RuntimeError("private backend diagnostic")

    monkeypatch.setattr(state, "_identity_snapshot", unavailable)
    now[0] += 3
    with pytest.raises(ExternalCallError, match="^Sign-in state unavailable; retry shortly$"):
        state.refresh_identity()
    now[0] += 26.999
    assert state.identity_snapshot() == {"tokens": {}, "signed_out": {}}
    state.identity_sign_out("local-write", int(time.time()) + 100)
    before = len(sent)
    now[0] += .001
    for _ in range(2):
        with pytest.raises(ExternalCallError, match="snapshot is missing or at least 30 seconds old"):
            state.identity_snapshot()
    assert len(sent) == before


def test_a_stalled_refresh_never_blocks_request_reads_or_stale_refusals(shared, monkeypatch):
    new, now, _, _ = shared
    state = new()
    started, release = threading.Event(), threading.Event()
    read = state._identity_snapshot

    def stalled():
        started.set()
        assert release.wait(2)
        return read()

    monkeypatch.setattr(state, "_identity_snapshot", stalled)

    def clock():
        if now[0] >= 130:
            release.set()
        return now[0]

    monkeypatch.setattr(state, "identity_clock", clock)
    with ThreadPoolExecutor(max_workers=1) as worker:
        pending = worker.submit(state.refresh_identity)
        try:
            assert started.wait(2)
            now[0] += 29.999
            assert state.identity_snapshot() == {"tokens": {}, "signed_out": {}}
            now[0] += .001
            with pytest.raises(ExternalCallError, match="at least 30 seconds old"):
                state.identity_snapshot()
        finally:
            release.set()
        with pytest.raises(ExternalCallError, match="refresh took at least 30 seconds"):
            pending.result(timeout=2)


def test_missing_startup_snapshot_fails_closed_until_a_refresh_succeeds(shared, tmp_path, monkeypatch):
    new, _, sent, _ = shared
    state = new()
    backend = type(state)
    read = backend._identity_snapshot

    def unavailable(self):
        raise RuntimeError("private backend diagnostic")

    with monkeypatch.context() as failed:
        failed.setattr(backend, "_identity_snapshot", unavailable)
        state = new()
        ids = Identities(tmp_path / "one", lambda: {"reader@example.com"},
                         SignedSessions(tmp_path / "one", "sample", state=state), state=state)
        sid = ids.sessions.open("reader@example.com")
        before = len(sent)
        with pytest.raises(ExternalCallError, match="snapshot is missing"):
            ids.resolve(None, sid)
        assert len(sent) == before
    assert backend._identity_snapshot is read
    state.refresh_identity()
    assert ids.resolve(None, sid) == "reader@example.com"


def test_background_refresh_runs_once_per_interval_and_stops_with_the_lifespan(shared, monkeypatch):
    new, now, sent, _ = shared
    state = new()
    tick, waiting, refreshed = threading.Event(), threading.Event(), threading.Event()
    pauses = []

    class Ticks:
        stopped = False

        def wait(self, pause):
            pauses.append(pause)
            waiting.set()
            assert tick.wait(2)
            tick.clear()
            return self.stopped

        def clear(self):
            self.stopped = False

        def set(self):
            self.stopped = True
            tick.set()

    monkeypatch.setattr(state, "_identity_stop", Ticks())
    refresh = state.refresh_identity

    def published():
        refresh()
        refreshed.set()

    monkeypatch.setattr(state, "refresh_identity", published)
    with identity_refresher(state):
        worker = state._identity_thread
        state.start_identity_refresh()
        assert state._identity_thread is worker
        for _ in range(12):
            assert waiting.wait(2)
            waiting.clear()
            now[0] += 3
            before = len(sent)
            for _ in range(20):
                state.identity_snapshot()
            assert len(sent) == before
            tick.set()
            assert refreshed.wait(2)
            refreshed.clear()
            assert len(sent) - before == (1 if sent else 0)
        assert pauses and set(pauses) == {3}
    assert not worker.is_alive()


def test_background_resuming_after_idle_reuses_the_request_refresh(shared, monkeypatch):
    new, now, jobs, _ = shared
    state = new()
    tick, waiting = threading.Event(), threading.Event()

    class Ticks:
        stopped = False

        def wait(self, pause):
            waiting.set()
            assert tick.wait(2)
            tick.clear()
            return self.stopped

        def clear(self):
            self.stopped = False

        def set(self):
            self.stopped = True
            tick.set()

    monkeypatch.setattr(state, '_identity_stop', Ticks())
    with identity_refresher(state):
        assert waiting.wait(2)
        now[0] += 60
        before = len(jobs)
        assert state.identity_snapshot() == {'tokens': {}, 'signed_out': {}}
        assert len(jobs) - before == (1 if jobs else 0)
        after_request = len(jobs)
        waiting.clear()
        tick.set()
        assert waiting.wait(2)
        assert len(jobs) == after_request
        now[0] += 3
        waiting.clear()
        tick.set()
        assert waiting.wait(2)
        assert len(jobs) - after_request == (1 if jobs else 0)
    assert not state._identity_thread.is_alive()


def test_run_folder_tokens_minted_after_startup_work_before_and_after_refresh(tmp_path, monkeypatch):
    from contextlib import ExitStack
    from kge.services.identity.service import token_for

    monkeypatch.setenv("APP_STATE_BACKEND", "files")
    monkeypatch.delenv("STORE_URL", raising=False)
    prof = SimpleNamespace(client="sample", run_dir=lambda: tmp_path / "run")
    instances = [identity_state(prof) for _ in range(3)]
    identities = [Identities(prof.run_dir(), lambda: {"reader@example.com"}, state=state) for state in instances]
    with ExitStack() as stack:
        for state in instances:
            stack.enter_context(identity_refresher(state))
        token = token_for(prof.run_dir(), "reader@example.com")
        for ids in identities:
            assert ids.resolve(f"Bearer {token}", None) == "reader@example.com"
            ids.state.refresh_identity()
            assert ids.resolve(f"Bearer {token}", None) == "reader@example.com"
        other = Identities(tmp_path / "other", lambda: {"reader@example.com"})
        assert other.resolve(f"Bearer {token}", None) is None
        def unavailable():
            raise RuntimeError("private backend diagnostic")
        monkeypatch.setattr(instances[0], "_identity_snapshot", unavailable)
        with pytest.raises(ExternalCallError, match="snapshot is missing or at least 30 seconds old"):
            identities[0].resolve(f"Bearer {token}", None)


def test_local_shared_token_files_are_private(tmp_path, monkeypatch):
    monkeypatch.setenv("APP_STATE_BACKEND", "files")
    monkeypatch.setenv("STORE_URL", str(tmp_path))
    state = identity_state(SimpleNamespace(client="sample"))
    state.identity_token("reader@example.com", "candidate")
    state.identity_sign_out("one", int(time.time()) + 100)
    for name in ("session-state.json",):
        assert stat.S_IMODE((tmp_path / "access/sample/identity" / name).stat().st_mode) == 0o600


def test_cli_refreshes_identity_while_minting_tokens_and_stops_afterward(shared, tmp_path, monkeypatch):
    from kge.pipeline import profile
    from kge.services.identity import cli

    new, _, _, _ = shared
    state = new()
    prof = SimpleNamespace(run_dir=lambda: tmp_path)
    monkeypatch.setattr(sys, "argv", ["identity"])
    monkeypatch.setattr(profile, "resolve", lambda _: prof)
    monkeypatch.setattr(cli.Access, "for_profile", lambda _: SimpleNamespace(people=lambda: []))
    monkeypatch.setattr(cli, "authorization_roles", lambda _: {})
    monkeypatch.setattr(cli, "identity_state", lambda _: state)

    def mint(*args, **kwargs):
        assert state._identity_thread.is_alive()
        return SimpleNamespace(by_token={}, known=set())

    monkeypatch.setattr(cli, "Identities", mint)
    assert cli.main() == 0
    assert not state._identity_thread.is_alive()


def test_a_slow_refresh_cannot_extend_the_sign_out_bound(shared, monkeypatch):
    new, now, _, _ = shared
    state = new()
    read = state._identity_snapshot

    def slow():
        snapshot = read()
        now[0] += 30
        return snapshot

    monkeypatch.setattr(state, "_identity_snapshot", slow)
    with pytest.raises(ExternalCallError, match="Sign-in state unavailable"):
        state.refresh_identity()
    with pytest.raises(ExternalCallError, match="at least 30 seconds old"):
        state.identity_snapshot()


def test_shared_identity_is_outside_pack_snapshots_and_data_cleanup_inventory(shared, tmp_path):
    new, _, jobs, fake = shared
    state = new()
    state.identity_token("reader@example.com", "candidate")
    state.identity_sign_out("closed", int(time.time()) + 100)
    assert open_store(str(tmp_path / "store")).list("clients/") == []
    if jobs:
        assert dataset_inventory(lambda sql: fake.run(None, sql), bigquery_fake.PROJECT, "kge_sample_graph") == []
        assert {name for dataset, name in fake.objects if dataset == "kge_sample_graph"} == {"access_identity_tokens", "access_signed_out"}


@pytest.mark.parametrize("key", ["", "   "])
def test_cloud_run_password_startup_refuses_a_missing_session_secret(monkeypatch, key):
    monkeypatch.setenv("K_SERVICE", "test-service")
    monkeypatch.setenv("SIGNIN", " PASSWORDS ")
    monkeypatch.setenv("SESSION_SECRET", key)
    with pytest.raises(ValueError, match="SESSION_SECRET"):
        check_service_auth_settings()
    result = subprocess.run([sys.executable, "-c", "import kge.app"], cwd=ROOT,
                            env={**os.environ, "MODEL_PROVIDER": "none", "PROFILE": "", "PROFILES": ""},
                            capture_output=True, text=True, timeout=60)
    assert result.returncode != 0 and "SESSION_SECRET" in result.stderr


@pytest.mark.parametrize("cloud,signin", [("", "passwords"), ("test-service", "")])
def test_local_and_non_password_startup_keep_generated_keys(monkeypatch, tmp_path, cloud, signin):
    monkeypatch.setenv("K_SERVICE", cloud)
    monkeypatch.setenv("SIGNIN", signin)
    monkeypatch.setenv("SESSION_SECRET", "")
    check_service_auth_settings()
    first = SignedSessions(tmp_path, "sample")
    second = SignedSessions(tmp_path, "sample")
    assert first.key == second.key
    assert first.fingerprint == second.fingerprint and len(first.fingerprint) == 12
    assert stat.S_IMODE((tmp_path / "session_secret").stat().st_mode) == 0o600


def test_existing_local_credentials_move_into_one_object_without_losing_revocations(tmp_path):
    import json
    from kge.adapters.sessionstate import local_identity

    expiry = int(time.time()) + 100
    (tmp_path / "tokens.json").write_text(json.dumps({"reader@example.com": "existing"}))
    (tmp_path / "signed_out.json").write_text(json.dumps({"closed": expiry}))
    state = local_identity(tmp_path)
    assert state.identity_snapshot() == {"tokens": {"reader@example.com": "existing"}, "signed_out": {"closed": expiry}}
    assert stat.S_IMODE((tmp_path / "session-state.json").stat().st_mode) == 0o600
    state.identity_sign_out("another", expiry)
    (tmp_path / "tokens.json").write_text('{}')
    assert local_identity(tmp_path).identity_snapshot() == {
        "tokens": {"reader@example.com": "existing"}, "signed_out": {"closed": expiry, "another": expiry}}
