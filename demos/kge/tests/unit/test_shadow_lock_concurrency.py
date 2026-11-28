"""Concurrent planning reads do not serialize, and writes wait for all of them."""
import threading
import time

import pytest

from kge.adapters.deadline import RequestDeadlineExceeded, request_window
from kge.pipeline.trace import QueryCancelled, cancellable
from kge.semantics.plan import Abstain
from kge.services.ask.engine import planpublic
from kge.services.ask.engine.shadowlock import ShadowLock
from tests.unit.test_shadow_memo_integration import harness


def test_actual_rules_planners_run_concurrently_and_writer_waits(monkeypatch):
    run, _, _ = harness(monkeypatch)
    closed = dict(zip(run.__code__.co_freevars, (cell.cell_contents for cell in run.__closure__)))
    engine = closed['engine']
    guard = ShadowLock()
    engine.shadow_read_guard = guard.read
    engine.shadow_write_guard = guard.write
    entered = threading.Barrier(3)
    release = threading.Event()
    writer_started, writer_entered = threading.Event(), threading.Event()
    errors, answers = [], []

    def blocked_planner(*args):
        entered.wait(timeout=3)
        if not release.wait(3):
            raise RuntimeError('planner timed out')
        return Abstain(kind='abstain', spans=(), missing=())

    def read():
        try:
            answers.append(run())
        except BaseException as exc:
            errors.append(exc)

    def write():
        writer_started.set()
        with guard.write():
            writer_entered.set()

    monkeypatch.setattr(planpublic.rulesport, 'planned', blocked_planner)
    readers = [threading.Thread(target=read) for _ in range(2)]
    writer = threading.Thread(target=write)
    for reader in readers:
        reader.start()
    try:
        entered.wait(timeout=3)
        writer.start()
        assert writer_started.wait(2)
        assert not writer_entered.wait(0.1)
    finally:
        release.set()
        for reader in readers:
            reader.join(3)
        if writer.ident is not None:
            writer.join(3)
    assert all(not reader.is_alive() for reader in readers)
    assert not writer.is_alive()
    assert writer_entered.is_set()
    assert not errors
    assert [answer['behaviour'] for answer in answers] == ['abstain', 'abstain']


@pytest.mark.parametrize('failure', [QueryCancelled('cancelled'), RuntimeError('failed')])
def test_reader_cancellation_or_exception_releases_waiting_writer(failure):
    lock = ShadowLock()
    reader_entered, fail_reader, writer_entered = threading.Event(), threading.Event(), threading.Event()
    errors = []

    def read():
        try:
            with lock.read():
                reader_entered.set()
                if not fail_reader.wait(3):
                    raise RuntimeError('reader timed out')
                raise failure
        except BaseException as exc:
            errors.append(exc)

    def write():
        with lock.write():
            writer_entered.set()

    reader = threading.Thread(target=read)
    writer = threading.Thread(target=write)
    reader.start()
    try:
        assert reader_entered.wait(2)
        writer.start()
        assert not writer_entered.wait(0.1)
    finally:
        fail_reader.set()
        reader.join(3)
        if writer.ident is not None:
            writer.join(3)
    assert not reader.is_alive()
    assert not writer.is_alive()
    assert writer_entered.is_set()
    assert errors == [failure]


@pytest.mark.parametrize('reason', ['deadline', 'cancel'])
def test_reader_waiting_on_writer_honours_deadline_or_cancellation(reason):
    lock = ShadowLock()
    started, finished, cancelled = threading.Event(), threading.Event(), threading.Event()
    waiting = threading.Event()
    entered, failures, durations = [], [], []
    wait = lock._wait
    def observed_wait():
        waiting.set()
        wait()
    lock._wait = observed_wait
    def read():
        started.set()
        start = time.monotonic()
        try:
            if reason == 'deadline':
                with request_window(0.15), lock.read():
                    entered.append(True)
            else:
                with cancellable(cancelled), lock.read():
                    entered.append(True)
        except BaseException as exc:
            failures.append(exc)
        finally:
            durations.append(time.monotonic() - start)
            finished.set()
    reader = threading.Thread(target=read)
    with lock.write():
        reader.start()
        try:
            assert started.wait(2)
            assert waiting.wait(2)
            if reason == 'cancel':
                cancelled.set()
            assert finished.wait(2)
        finally:
            cancelled.set()
    reader.join(2)
    assert not reader.is_alive()
    assert entered == []
    assert len(failures) == 1
    assert isinstance(failures[0], RequestDeadlineExceeded if reason == 'deadline' else QueryCancelled)
    assert durations[0] < 2
    writer_entered = threading.Event()
    def write():
        with lock.write():
            writer_entered.set()
    writer = threading.Thread(target=write)
    writer.start()
    writer.join(2)
    assert not writer.is_alive()
    assert writer_entered.is_set()
