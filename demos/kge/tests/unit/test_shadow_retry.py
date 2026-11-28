"""Shadow input recovery is single-flight, cancellable and closes resources only when safe."""
import threading
import time

from kge.pipeline.trace import cancel_hook, check_cancelled
from kge.services.ask.engine.shadowretry import ShadowRetry


def test_retries_past_three_failures_until_recovered():
    attempts, recovered = [], threading.Event()
    def attempt():
        attempts.append(len(attempts) + 1)
        if len(attempts) == 5:
            recovered.set()
            return True
        return False
    retry = ShadowRetry(attempt, delays=(0.01, 0.02), attempt_seconds=1, join_seconds=1)
    retry.start()
    try:
        assert recovered.wait(2)
    finally:
        retry.stop(lambda: None)
    assert attempts == [1, 2, 3, 4, 5]


def test_retry_failure_warning_does_not_expose_exception_details(caplog):
    attempts, recovered = [], threading.Event()
    def attempt():
        attempts.append(1)
        if len(attempts) == 1:
            raise ValueError('private question and backend details')
        recovered.set()
        return True
    retry = ShadowRetry(attempt, delays=(0.01,), attempt_seconds=1, join_seconds=1)
    retry.start()
    try:
        assert recovered.wait(2)
    finally:
        retry.stop(lambda: None)
    assert 'shadow input retry failed' in caplog.text
    assert 'private question and backend details' not in caplog.text


def test_repeated_starts_never_overlap_attempts():
    first, second, release = threading.Event(), threading.Event(), threading.Event()
    active, maximum, calls = 0, 0, 0
    lock = threading.Lock()
    def attempt():
        nonlocal active, maximum, calls
        with lock:
            active += 1
            calls += 1
            maximum = max(maximum, active)
            number = calls
        try:
            if number == 1:
                first.set()
                assert release.wait(2)
            else:
                second.set()
            return True
        finally:
            with lock:
                active -= 1
    retry = ShadowRetry(attempt, delays=(0.01,), attempt_seconds=2, join_seconds=1)
    retry.start()
    try:
        assert first.wait(2)
        for _ in range(10):
            retry.start()
        release.set()
        assert second.wait(2)
    finally:
        release.set()
        retry.stop(lambda: None)
    assert maximum == 1
    assert calls == 2


def test_stop_idle_closes_once_and_prevents_restart():
    attempted, closed = [], []
    retry = ShadowRetry(lambda: attempted.append(1), delays=(0.01,))
    retry.stop(lambda: closed.append('closed'))
    retry.stop(lambda: closed.append('twice'))
    retry.start()
    assert attempted == []
    assert closed == ['closed']


def test_stop_during_backoff_closes_without_attempting():
    attempted = []
    closed = threading.Event()
    retry = ShadowRetry(lambda: attempted.append(1), delays=(5,), join_seconds=1)
    retry.start()
    retry.stop(closed.set)
    assert closed.wait(2)
    assert attempted == []


def test_stop_defers_close_while_uncancellable_attempt_is_blocked():
    entered, release, exited, closed = (threading.Event() for _ in range(4))
    def attempt():
        entered.set()
        try:
            assert release.wait(2)
            return True
        finally:
            exited.set()
    closed_too_early = []
    def close():
        closed_too_early.append(not exited.is_set())
        closed.set()
    retry = ShadowRetry(attempt, delays=(0.01,), attempt_seconds=2, join_seconds=0.03)
    retry.start()
    try:
        assert entered.wait(2)
        before = time.monotonic()
        retry.stop(close)
        assert time.monotonic() - before < 1
        assert not closed.is_set()
    finally:
        release.set()
    assert closed.wait(2)
    assert closed_too_early == [False]


def test_stop_invokes_backend_cancel_hook_and_then_closes():
    entered, cancelled, closed = threading.Event(), threading.Event(), threading.Event()
    def attempt():
        with cancel_hook(cancelled.set):
            entered.set()
            assert cancelled.wait(2)
            check_cancelled()
        return True
    retry = ShadowRetry(attempt, delays=(0.01,), attempt_seconds=2, join_seconds=1)
    retry.start()
    try:
        assert entered.wait(2)
        retry.stop(closed.set)
        assert cancelled.wait(2)
        assert closed.wait(2)
    finally:
        retry.stop(lambda: None)


def test_close_waits_for_both_worker_and_blocked_cancel_hook():
    entered, hook_entered, release_attempt, release_hook, exited, closed = (threading.Event() for _ in range(6))
    def hook():
        hook_entered.set()
        assert release_hook.wait(2)
    def attempt():
        try:
            with cancel_hook(hook):
                entered.set()
                assert release_attempt.wait(2)
                return True
        finally:
            exited.set()
    retry = ShadowRetry(attempt, delays=(0.01,), attempt_seconds=2, join_seconds=0.03)
    retry.start()
    try:
        assert entered.wait(2)
        retry.stop(closed.set)
        assert hook_entered.wait(2)
        release_attempt.set()
        assert exited.wait(2)
        assert not closed.is_set()
    finally:
        release_attempt.set()
        release_hook.set()
    assert closed.wait(2)
