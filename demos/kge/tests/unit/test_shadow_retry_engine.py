"""Background shadow input preparation publishes only a still-current graph epoch."""
from threading import Event, RLock, Thread
from types import SimpleNamespace

from kge.services.ask.engine.core import Engine
from kge.services.ask.engine.shadowlock import ShadowLock
from kge.services.ask.engine import shadowinputs


def retry_engine(read):
    identity = {'release': 'r1'}
    graph = SimpleNamespace(writes=0, identity=identity, _snapshot_lock=RLock(), _shadow_writes_inflight=0,
                            _shadow_template_generation=0, _shadow_template_needs_read=True,
                            _shadow_templates=None, read_templates=read, read_uncached=lambda _: [])
    lock = ShadowLock()
    return SimpleNamespace(g=graph, semantics=SimpleNamespace(partitioned={}), term_names=['term'],
                           _shadow_baseline=None, shadow_read_guard=lock.read, shadow_write_guard=lock.write)


def test_retry_builds_without_blocking_ask_and_publishes_current_inputs(monkeypatch):
    started, release = Event(), Event()
    def read():
        started.set()
        assert release.wait(3)
        return SimpleNamespace(release={'release': 'r1'}, shapes={})
    engine = retry_engine(read)
    baseline = SimpleNamespace(plan_writes=(0, ()))
    monkeypatch.setattr(shadowinputs, 'prepare_baseline', lambda _: baseline)
    result = []
    worker = Thread(target=lambda: result.append(Engine._retry_shadow_inputs(engine)))
    worker.start()
    try:
        assert started.wait(2)
        with engine.shadow_read_guard():
            assert engine._shadow_baseline is None
    finally:
        release.set()
        worker.join(3)
    assert not worker.is_alive()
    assert result == [True]
    assert engine.g._shadow_templates.release == engine.g.identity
    assert engine._shadow_baseline is baseline


def test_retry_never_publishes_inputs_after_graph_epoch_changes(monkeypatch):
    engine = retry_engine(lambda: SimpleNamespace(release={'release': 'r1'}, shapes={}))
    def baseline(_):
        engine.g.writes += 1
        engine.g._shadow_template_generation += 1
        return SimpleNamespace(plan_writes=(1, ()))
    monkeypatch.setattr(shadowinputs, 'prepare_baseline', baseline)
    assert Engine._retry_shadow_inputs(engine) is False
    assert engine.g._shadow_templates is None
    assert engine._shadow_baseline is None


def test_retry_reads_fresh_when_existing_template_is_marked_stale(monkeypatch):
    reads = []
    def read():
        reads.append(True)
        return SimpleNamespace(release={'release': 'r1'}, shapes={'new': {}})
    engine = retry_engine(read)
    engine.g._shadow_templates = SimpleNamespace(release={'release': 'r1'}, shapes={'old': {}})
    engine._shadow_baseline = SimpleNamespace(plan_writes=(0, ()))
    monkeypatch.setattr(shadowinputs, 'prepare_baseline', lambda _: SimpleNamespace(plan_writes=(0, ())))
    assert Engine._retry_shadow_inputs(engine) is True
    assert reads == [True]
    assert set(engine.g._shadow_templates.shapes) == {'new'}
