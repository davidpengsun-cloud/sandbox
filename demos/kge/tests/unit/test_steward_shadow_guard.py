"""A steward write and its refresh form one shadow-input epoch."""
import threading
from types import SimpleNamespace

import pytest

from kge.pipeline.overlay import OverlayError
from kge.services.steward import service


class Guard:
    def __init__(self, events):
        self.lock = threading.RLock()
        self.events = events

    def __enter__(self):
        self.lock.acquire()
        self.events.append('enter')

    def __exit__(self, *args):
        self.events.append('exit')
        self.lock.release()


@pytest.mark.parametrize('overlay_error', [False, True])
def test_steward_write_and_refresh_hold_same_guard_and_reraise_overlay_error(overlay_error):
    events = []
    guard = Guard(events)
    failure = OverlayError('journal unavailable')
    def write(*args):
        events.append('write')
        if overlay_error:
            raise failure
        return {'decision': 'accept'}
    def refresh():
        events.append('refresh')
    engine = SimpleNamespace(g=SimpleNamespace(steward_decide=write), refresh=refresh,
                             shadow_write_guard=lambda: guard)
    if overlay_error:
        with pytest.raises(OverlayError) as raised:
            service.decide(engine, 'tag', 'steward', 'accept', None, None)
        assert raised.value is failure
    else:
        assert service.decide(engine, 'tag', 'steward', 'accept', None, None) == {'decision': 'accept'}
    assert events == ['enter', 'write', 'refresh', 'exit']


def test_comparison_guard_waits_until_steward_refresh_finishes():
    guard = threading.RLock()
    refreshing, release_refresh = threading.Event(), threading.Event()
    comparison_started, comparison_entered = threading.Event(), threading.Event()
    errors = []
    def refresh():
        refreshing.set()
        if not release_refresh.wait(3):
            errors.append('refresh timed out')
    engine = SimpleNamespace(g=SimpleNamespace(steward_decide=lambda *args: {'decision': 'accept'}),
                             refresh=refresh, shadow_write_guard=lambda: guard)
    def write():
        try:
            service.decide(engine, 'tag', 'steward', 'accept', None, None)
        except Exception as exc:
            errors.append(type(exc).__name__)
    def compare():
        comparison_started.set()
        with engine.shadow_write_guard():
            comparison_entered.set()
    writer = threading.Thread(target=write)
    reader = threading.Thread(target=compare)
    writer.start()
    try:
        assert refreshing.wait(2)
        reader.start()
        assert comparison_started.wait(2)
        assert not comparison_entered.wait(0.1)
    finally:
        release_refresh.set()
        writer.join(3)
        if reader.ident is not None:
            reader.join(3)
    assert not writer.is_alive()
    assert not reader.is_alive()
    assert comparison_entered.is_set()
    assert errors == []


@pytest.mark.parametrize('refresh_fails', [False, True])
def test_unexpected_write_failure_refreshes_under_guard_and_preserves_original(refresh_fails, caplog):
    events = []
    guard = Guard(events)
    failure = RuntimeError('write failure')
    graph = SimpleNamespace(_shadow_template_generation=0)
    def partial_write(*args):
        events.append('write')
        graph._shadow_template_generation += 1
        raise failure
    def refresh():
        events.append('refresh')
        assert graph._shadow_template_generation == 1
        if refresh_fails:
            raise RuntimeError('private refresh details')
    graph.steward_decide = partial_write
    engine = SimpleNamespace(g=graph, refresh=refresh,
                             shadow_write_guard=lambda: guard)
    with pytest.raises(RuntimeError) as raised:
        service.decide(engine, 'tag', 'steward', 'accept', None, None)
    assert raised.value is failure
    assert events == ['enter', 'write', 'refresh', 'exit']
    assert 'private refresh details' not in caplog.text
    if refresh_fails:
        assert 'steward inputs could not be refreshed' in caplog.text


def test_refused_steward_write_does_not_refresh():
    events = []
    guard = Guard(events)
    failure = PermissionError('refused')
    def reject(*args):
        events.append('write')
        raise failure
    engine = SimpleNamespace(g=SimpleNamespace(steward_decide=reject, _shadow_template_generation=0, writes=0),
                             refresh=lambda: events.append('refresh'), shadow_write_guard=lambda: guard)
    with pytest.raises(PermissionError) as raised:
        service.decide(engine, 'tag', 'steward', 'accept', None, None)
    assert raised.value is failure
    assert events == ['enter', 'write', 'exit']
