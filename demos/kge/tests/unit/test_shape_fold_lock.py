"""The fold mutex fences overlapping projections without automatically taking over a crashed holder."""

from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
from threading import Event
from types import SimpleNamespace

import pytest

from kge.adapters.objstore import open_store
from kge.adapters.shadowlog.foldlock import FoldBusy, FoldMutex, fold_mutex, held
from kge.adapters.shadowlog.objstore import StoreShadowLog
from kge.adapters.shadowlog.seals import StoreShadowSeals
from kge.eval import shapefold
from kge.ports import StoreConflict
from tests.gcs_stub import serve


def test_entire_fold_is_serialized_across_direct_and_candidate_calls(tmp_path, monkeypatch):
    entered, finish = Event(), Event()
    writes = []

    class Profile:
        client = "client"

        def run_dir(self):
            return tmp_path

    def perform(_profile, *, model_hash=None, **_kwargs):
        writes.append(model_hash)
        if model_hash is None:
            entered.set()
            assert finish.wait(5)
        return SimpleNamespace(written=False, model_hash=model_hash)

    monkeypatch.setattr(shapefold, "_run_locked", perform)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(shapefold.run, Profile())
        assert entered.wait(5)
        second = pool.submit(shapefold.run, Profile(), model_hash="candidate")
        with pytest.raises(shapefold.NotFolded, match="mutex"):
            second.result(timeout=5)
        assert writes == [None]
        finish.set()
        assert first.result(timeout=5).model_hash is None
    assert shapefold.run(Profile(), model_hash="candidate").model_hash == "candidate"
    assert writes == [None, "candidate"]


def test_exception_releases_holder_and_later_fold_can_acquire(tmp_path):
    mutex = FoldMutex(open_store(str(tmp_path)), "fold-lock")
    with pytest.raises(ValueError, match="failed"):
        with held(mutex):
            raise ValueError("failed")
    with held(mutex):
        assert mutex.inspect()[0] is not None
    assert mutex.inspect()[0] is None


def test_crash_never_expires_and_recovery_is_exact_and_explicit(tmp_path):
    mutex = FoldMutex(open_store(str(tmp_path)), "fold-lock")
    token = mutex.acquire()
    record, version = mutex.inspect()
    assert record["token"] == token
    with pytest.raises(FoldBusy):
        mutex.acquire()
    with pytest.raises(FoldBusy):
        mutex.recover(token, version, holder_stopped=False)
    with pytest.raises(FoldBusy):
        mutex.recover("0" * 32, version, holder_stopped=True)
    with pytest.raises(FoldBusy):
        mutex.recover(token, "wrong-version", holder_stopped=True)
    assert mutex.inspect() == (record, version)
    mutex.recover(token, version, holder_stopped=True)
    next_token = mutex.acquire()
    with pytest.raises(FoldBusy):
        mutex.release(token)
    assert mutex.inspect()[0]["token"] == next_token
    mutex.release(next_token)


def test_conditional_write_rejects_racing_acquisitions(tmp_path):
    store = open_store(str(tmp_path))
    a, b = FoldMutex(store, "fold-lock"), FoldMutex(store, "fold-lock")
    entered, release = Event(), Event()

    def first():
        token = a.acquire()
        entered.set()
        assert release.wait(5)
        a.release(token)

    with ThreadPoolExecutor(max_workers=2) as pool:
        job = pool.submit(first)
        assert entered.wait(5)
        with pytest.raises(FoldBusy):
            b.acquire()
        release.set()
        job.result(timeout=5)
    b.release(b.acquire())


def test_changed_version_recovery_does_not_clear_new_holder(tmp_path):
    store = open_store(str(tmp_path))
    mutex = FoldMutex(store, "fold-lock")
    token = mutex.acquire()
    record, version = mutex.inspect()
    store.put_version("fold-lock", json.dumps(record).encode(), version)
    with pytest.raises(FoldBusy, match="changed"):
        mutex.recover(token, version, holder_stopped=True)
    mutex.release(token)


def test_store_conflict_on_acquire_fails_closed():
    class Conflict:
        def get_version(self, _key):
            return None, "0"

        def put_version(self, _key, _data, _version):
            raise StoreConflict("version changed")

    with pytest.raises(FoldBusy, match="another fold"):
        FoldMutex(Conflict(), "lock").acquire()


def test_gcs_generation_lock_rejects_stale_writer_and_recovers(monkeypatch):
    with serve() as server:
        monkeypatch.setenv("STORAGE_EMULATOR_HOST", f"127.0.0.1:{server.server_address[1]}")
        monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
        store = open_store("gs://fold-lock-test")
        first = FoldMutex(store, "shadow/client/_fold-lock.json")
        second = FoldMutex(store, "shadow/client/_fold-lock.json")
        token = first.acquire()
        record, version = first.inspect()
        with pytest.raises(FoldBusy):
            second.acquire()
        first.recover(token, version, holder_stopped=True)
        next_token = second.acquire()
        with pytest.raises(FoldBusy):
            first.release(token)
        assert second.inspect()[0]["token"] == next_token
        second.release(next_token)
        assert first.inspect()[0] is None


def test_corrupt_lock_never_treated_as_free(tmp_path):
    store = open_store(str(tmp_path))
    store.put("lock", b'{"token":"not-a-record"}')
    with pytest.raises(FoldBusy, match="unreadable"):
        FoldMutex(store, "lock").acquire()


def test_corrupt_lock_can_only_be_recovered_with_exact_inspected_digest(tmp_path):
    store = open_store(str(tmp_path))
    private = b'{"secret":"not-valid-lock"}'
    store.put("lock", private)
    mutex = FoldMutex(store, "lock")
    info = mutex.inspect_recovery()
    assert info["holder"] is None and info["corrupt_digest"]
    assert "secret" not in json.dumps(info)
    with pytest.raises(FoldBusy):
        mutex.recover_corrupt(info["corrupt_digest"], info["object_version"], holder_stopped=False)
    with pytest.raises(FoldBusy):
        mutex.recover_corrupt("0" * 64, info["object_version"], holder_stopped=True)
    store.put("lock", b"different invalid data")
    with pytest.raises(FoldBusy):
        mutex.recover_corrupt(info["corrupt_digest"], info["object_version"], holder_stopped=True)
    info = mutex.inspect_recovery()
    mutex.recover_corrupt(info["corrupt_digest"], info["object_version"], holder_stopped=True)
    assert mutex.inspect()[0] is None


def test_release_failure_never_exposes_a_success_report(tmp_path, monkeypatch):
    class Profile:
        client = "client"

        def run_dir(self):
            return tmp_path

    class BrokenRelease:
        def acquire(self):
            return "token"

        def release(self, _token):
            raise StoreConflict("PRIVATE_RELEASE_ERROR")

    report = shapefold.FoldReport("now", "served", None, 1, 1, 1, 0, 0, 0, 0, True, None)
    monkeypatch.setattr(shapefold, "_run_locked", lambda *_args, **_kwargs: report)
    monkeypatch.setattr("kge.adapters.shadowlog.foldlock.fold_mutex", lambda *_args: BrokenRelease())
    with pytest.raises(shapefold.NotFolded) as raised:
        shapefold.run(Profile())
    assert "PRIVATE_RELEASE_ERROR" not in str(raised.value)
    saved = shapefold.last_report(Profile())
    assert saved["written"] is False and "pending" in saved["skipped"]


def test_report_is_not_successful_during_a_graph_write_or_after_crash(tmp_path, monkeypatch):
    class Profile:
        client = "client"

        def run_dir(self):
            return tmp_path

    prior = shapefold.FoldReport("before", "served", None, 1, 1, 1, 0, 0, 0, 0, True, None)
    shapefold._report(Profile(), prior)

    def interrupted(*_args, **_kwargs):
        assert shapefold.last_report(Profile())["written"] is False
        raise KeyboardInterrupt()

    monkeypatch.setattr(shapefold, "_run_locked", interrupted)
    with pytest.raises(KeyboardInterrupt):
        shapefold.run(Profile())
    assert shapefold.last_report(Profile())["written"] is False


def test_mutex_os_errors_are_sanitized_for_acquire_and_recovery():
    class BrokenStore:
        def get_version(self, _key):
            raise OSError("PRIVATE_PATH")

    mutex = FoldMutex(BrokenStore(), "lock")
    for action in (mutex.acquire, mutex.inspect_recovery,
                   lambda: mutex.recover("x", "y", holder_stopped=True),
                   lambda: mutex.recover_corrupt("x", "y", holder_stopped=True)):
        with pytest.raises(shapefold.StoreError) as raised:
            action()
        assert "PRIVATE_PATH" not in str(raised.value)


def test_mutex_write_os_error_is_sanitized():
    class BrokenStore:
        def get_version(self, _key):
            return None, "0"

        def put_version(self, _key, _data, _version):
            raise OSError("PRIVATE_PATH")

    with pytest.raises(shapefold.StoreError) as raised:
        FoldMutex(BrokenStore(), "lock").acquire()
    assert "PRIVATE_PATH" not in str(raised.value)


def test_cleanup_failure_preserves_primary_fold_error(tmp_path, monkeypatch):
    class Profile:
        client = "client"

        def run_dir(self):
            return tmp_path

    class BrokenRelease:
        def acquire(self):
            return "token"

        def release(self, _token):
            raise StoreConflict("PRIVATE_RELEASE_ERROR")

    def fail(*_args, **_kwargs):
        raise shapefold.NotFolded("the primary fold error")

    monkeypatch.setattr(shapefold, "_run_locked", fail)
    monkeypatch.setattr("kge.adapters.shadowlog.foldlock.fold_mutex", lambda *_args: BrokenRelease())
    with pytest.raises(shapefold.NotFolded, match="primary fold error") as raised:
        shapefold.run(Profile())
    assert "PRIVATE_RELEASE_ERROR" not in " ".join(getattr(raised.value, "__notes__", []))
    assert any("mutex release failed" in note for note in raised.value.__notes__)


def test_later_report_is_not_overwritten_after_mutex_release(tmp_path, monkeypatch):
    class Profile:
        client = "client"

        def run_dir(self):
            return tmp_path

    class NextRun:
        def acquire(self):
            return "token"

        def release(self, _token):
            (tmp_path / "fold.json").write_text('{"written": false, "skipped": "later run"}')

    report = shapefold.FoldReport("now", "served", None, 1, 1, 1, 0, 0, 0, 0, True, None)
    monkeypatch.setattr(shapefold, "_run_locked", lambda *_args, **_kwargs: report)
    monkeypatch.setattr("kge.adapters.shadowlog.foldlock.fold_mutex", lambda *_args: NextRun())
    assert shapefold.run(Profile()) == report
    assert shapefold.last_report(Profile())["skipped"] == "later run"


def test_report_finalization_failure_leaves_pending_not_success(tmp_path, monkeypatch):
    class Profile:
        client = "client"

        def run_dir(self):
            return tmp_path

    class Mutex:
        def acquire(self):
            return "token"

        def release(self, _token):
            pass

    class BrokenReportStore:
        def __init__(self):
            self.inner = open_store(str(tmp_path), private=True)
            self.calls = 0

        def get_version(self, key):
            return self.inner.get_version(key)

        def put_version(self, key, value, version):
            self.calls += 1
            if self.calls > 1:
                raise shapefold.StoreError("PRIVATE_REPORT_ERROR")
            return self.inner.put_version(key, value, version)

    report = shapefold.FoldReport("now", "served", None, 1, 1, 1, 0, 0, 0, 0, True, None)
    monkeypatch.setattr(shapefold, "_run_locked", lambda *_args, **_kwargs: report)
    monkeypatch.setattr("kge.adapters.shadowlog.foldlock.fold_mutex", lambda *_args: Mutex())
    report_store = BrokenReportStore()
    monkeypatch.setattr("kge.adapters.objstore.open_store", lambda *_args, **_kwargs: report_store)
    with pytest.raises(shapefold.NotFolded, match="report could not be finalized") as raised:
        shapefold.run(Profile())
    assert "PRIVATE_REPORT_ERROR" not in str(raised.value)
    assert shapefold.last_report(Profile())["written"] is False


@pytest.mark.parametrize("fail_call", [1, 2])
def test_report_os_error_is_sanitized_and_releases_mutex(tmp_path, monkeypatch, fail_call):
    class Profile:
        client = "client"

        def run_dir(self):
            return tmp_path

    class Mutex:
        released = False

        def acquire(self):
            return "token"

        def release(self, _token):
            self.released = True

    class ReportStore:
        def __init__(self):
            self.inner = open_store(str(tmp_path), private=True)
            self.calls = 0

        def get_version(self, key):
            return self.inner.get_version(key)

        def put_version(self, key, value, version):
            self.calls += 1
            if self.calls == fail_call:
                raise PermissionError("PRIVATE_PATH")
            return self.inner.put_version(key, value, version)

    mutex = Mutex()
    store = ReportStore()
    monkeypatch.setattr("kge.adapters.shadowlog.foldlock.fold_mutex", lambda *_args: mutex)
    monkeypatch.setattr("kge.adapters.objstore.open_store", lambda *_args, **_kwargs: store)
    report = shapefold.FoldReport("now", "served", None, 1, 1, 1, 0, 0, 0, 0, True, None)
    monkeypatch.setattr(shapefold, "_run_locked", lambda *_args, **_kwargs: report)
    with pytest.raises(shapefold.NotFolded, match="report could not be") as raised:
        shapefold.run(Profile())
    assert "PRIVATE_PATH" not in str(raised.value)
    assert raised.value.__cause__ is None
    assert mutex.released
    saved = shapefold.last_report(Profile())
    assert saved is None or saved["written"] is False


def test_failed_failure_report_preserves_primary_error(tmp_path, monkeypatch):
    class Profile:
        client = "client"

        def run_dir(self):
            return tmp_path

    def fail_history(_prof):
        raise OSError("PRIVATE_HISTORY_PATH")

    def fail_report(*_args):
        raise shapefold.NotFolded("the fold report could not be written")

    monkeypatch.setattr(shapefold, "versions_path", fail_history)
    monkeypatch.setattr(shapefold, "_report", fail_report)
    with pytest.raises(shapefold.NotFolded, match="shape evidence files") as raised:
        shapefold._run_locked(Profile())
    assert "PRIVATE_HISTORY_PATH" not in str(raised.value)


def test_lock_never_appears_as_observation_or_sealed_event(tmp_path):
    store = open_store(str(tmp_path))
    mutex = FoldMutex(store, "shadow/client/_fold-lock.json")
    token = mutex.acquire()
    assert StoreShadowLog(store, "client").releases() == []
    assert StoreShadowSeals(store, "client").events() == []
    mutex.release(token)


def test_client_lock_identity_and_remote_store_requirement(tmp_path, monkeypatch):
    config = SimpleNamespace(STORE_URL="", GRAPH_STORE="ladybug")
    monkeypatch.setattr("kge.adapters.shadowlog.foldlock.settings", lambda: config)
    a = fold_mutex(tmp_path, "client")
    assert a.key == "_fold-lock.json"
    assert Path(a.store.url) == tmp_path / "shadow"
    config.GRAPH_STORE = "bigquery"
    with pytest.raises(FoldBusy, match="shared gs://"):
        fold_mutex(tmp_path, "client")
    config.STORE_URL = "gs://example-bucket/prefix"
    assert fold_mutex(tmp_path, "client").key == "shadow/client/_fold-lock.json"
    assert fold_mutex(tmp_path, "other").key == "shadow/other/_fold-lock.json"


def test_later_pending_report_wins_when_prior_finalizer_is_inside_cas_lock(tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event, current_thread
    from kge.adapters.objstore.folder import LocalStore

    class Profile:
        def run_dir(self):
            return tmp_path

    pending = shapefold.FoldReport('old', 'served', None, 0, 0, 0, 0, 0, 0, 0, False, 'old pending')
    newer = shapefold.FoldReport('new', 'served', None, 0, 0, 0, 0, 0, 0, 0, False, 'new pending')
    final = shapefold.FoldReport('old', 'served', None, 0, 0, 0, 0, 0, 0, 0, True, None)
    store, version = shapefold._report(Profile(), pending)
    locked, release, newer_started = Event(), Event(), Event()
    original = LocalStore._version

    def delayed(path):
        value = original(path)
        if current_thread().name.startswith('finalizer') and not locked.is_set():
            locked.set()
            assert release.wait(5)
        return value

    monkeypatch.setattr(LocalStore, '_version', staticmethod(delayed))

    def finalize():
        return store.put_version(shapefold.REPORT, json.dumps(final.to_json()).encode(), version)

    def publish_newer():
        newer_started.set()
        return shapefold._report(Profile(), newer)

    with ThreadPoolExecutor(max_workers=1, thread_name_prefix='finalizer') as first, ThreadPoolExecutor(max_workers=1) as second:
        old_task = first.submit(finalize)
        assert locked.wait(5)
        new_task = second.submit(publish_newer)
        assert newer_started.wait(5)
        assert not new_task.done()
        release.set()
        old_task.result(timeout=5)
        new_task.result(timeout=5)
    assert shapefold.last_report(Profile())['skipped'] == 'new pending'
