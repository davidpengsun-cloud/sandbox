"""Shadow templates are isolated, release-bound, and invalidated by real graph write handling."""
from contextlib import contextmanager
from threading import Event, RLock, Thread
from types import SimpleNamespace

import pytest

from kge.services.ask.engine.core import Engine
from kge.services.ask.engine.graphview import Graph
from kge.services.ask.engine.shadowinputs import ShadowInputs, ShadowInputsStale


class Frame:
    def astype(self, *args):
        return self

    def notna(self):
        return self

    def where(self, *args):
        return self

    def to_dict(self, *args):
        return []


def graph_for_write(fails=False):
    graph = Graph.__new__(Graph)
    graph._snapshot_lock = RLock()
    graph.writes = 0
    graph._templates = object()
    graph._shadow_templates = object()
    def execute(*args):
        if fails:
            raise RuntimeError('write failed')
        return SimpleNamespace(get_as_df=lambda: Frame())
    graph.conn = SimpleNamespace(execute=execute)
    return graph


@contextmanager
def blocked_shape_write(graph):
    started, release, errors = Event(), Event(), []
    def execute(*args):
        started.set()
        if not release.wait(3):
            raise RuntimeError('blocked write timed out')
        return SimpleNamespace(get_as_df=lambda: Frame())
    graph.conn = SimpleNamespace(execute=execute)
    def write():
        try:
            graph.q("MATCH (s:Shape) SET s.stage = 'retired'")
        except Exception as exc:
            errors.append(exc)
    thread = Thread(target=write)
    thread.start()
    try:
        assert started.wait(2)
        yield
    finally:
        release.set()
        thread.join(3)
    assert not thread.is_alive()
    assert errors == []


@pytest.mark.parametrize('fails', [False, True])
def test_shape_write_invalidates_serving_and_shadow_copies_even_on_failure(fails):
    graph = graph_for_write(fails)
    if fails:
        with pytest.raises(RuntimeError, match='write failed'):
            graph.q("MATCH (s:Shape) SET s.stage = 'retired'")
    else:
        assert graph.q("MATCH (s:Shape) SET s.stage = 'retired'") == []
    assert graph.writes == 1
    assert graph._templates is None
    assert graph._shadow_templates is None


def test_unrelated_graph_write_preserves_serving_but_invalidates_shadow_copy():
    graph = graph_for_write()
    serving = graph._templates
    graph.q("MATCH (t:Term) SET t.name = 'renamed'")
    assert graph.writes == 1
    assert graph._templates is serving
    assert graph._shadow_templates is None
    assert graph._shadow_template_needs_read is True


def test_untyped_write_forces_fresh_shadow_read_without_changing_serving_cache():
    graph = graph_for_write()
    graph.identity = {'release': 'r1'}
    serving = graph._templates = SimpleNamespace(release=dict(graph.identity), shapes={'a': {'stage': 'shadow'}})
    fresh = SimpleNamespace(release=dict(graph.identity), shapes={'a': {'stage': 'retired'}})
    reads = []
    graph.read_templates = lambda: reads.append(1) or fresh
    graph.q("MATCH (n) SET n.stage = 'retired'")
    assert graph._templates is serving
    assert graph._shadow_templates is None
    graph.prepare_shadow_templates()
    assert reads == [1]
    assert graph._shadow_templates.shapes['a']['stage'] == 'retired'
    assert graph._templates is serving
    graph.prepare_shadow_templates()
    assert reads == [1]


def test_cold_shadow_preparation_leaves_serving_cache_unpopulated():
    graph = graph_for_write()
    graph.identity = {'release': 'r1'}
    graph._templates = None
    graph._shadow_templates = None
    source = SimpleNamespace(release=dict(graph.identity), shapes={'a': {'stage': 'shadow'}})
    graph.read_templates = lambda: source
    graph.prepare_shadow_templates()
    assert graph._templates is None
    assert graph._shadow_templates == source
    assert graph._shadow_templates is not source


def test_shadow_snapshot_invalidates_before_blocked_shape_write_executes():
    graph = graph_for_write()
    with blocked_shape_write(graph):
        assert graph._shadow_templates is None
        assert graph.writes == 0
        with pytest.raises(RuntimeError, match='during a graph write'):
            graph.prepare_shadow_templates()
        assert graph._shadow_templates is None
    assert graph.writes == 1


def test_existing_facade_refuses_snapshot_while_shape_write_is_still_blocked():
    graph = graph_for_write()
    identity = {'release': 'r1'}
    graph.identity = dict(identity)
    graph._templates = SimpleNamespace(release=dict(identity), shapes={})
    templates = graph._shadow_templates = SimpleNamespace(release=dict(identity), shapes={})
    runtime = SimpleNamespace(plan_writes=(0, ()), mapping_declarations=[])
    semantics = SimpleNamespace(partitioned={}, plan_snapshot=runtime)
    engine = SimpleNamespace(g=graph, semantics=semantics, identity=dict(identity), term_names=[], UNIT_ORDER=[])
    facade = ShadowInputs(engine, templates=templates, graph_mode=True, runtime=runtime, columns={})
    with blocked_shape_write(graph):
        assert graph.writes == 0
        with pytest.raises(ShadowInputsStale):
            facade.validate()


def test_prepared_shadow_templates_do_not_share_serving_mutation():
    graph = graph_for_write()
    graph.identity = {'release': 'r1'}
    graph._shadow_templates = None
    graph._templates = SimpleNamespace(release={'release': 'r1'}, shapes={'a': {'stage': 'shadow'}})
    graph.prepare_shadow_templates()
    graph._templates.shapes['a']['stage'] = 'retired'
    assert graph._shadow_templates.shapes['a']['stage'] == 'shadow'
    assert graph._shadow_templates is not graph._templates


def test_true_template_preparation_failure_clears_shadow_inputs_without_leaking_error(caplog):
    graph = graph_for_write()
    graph.identity = {'release': 'r1'}
    graph._templates = None
    graph._shadow_template_needs_read = True
    graph.read_templates = lambda: (_ for _ in ()).throw(RuntimeError('private details'))
    engine = SimpleNamespace(g=graph, _shadow_baseline=object())
    Engine._warm_shadow_inputs(engine)
    assert graph._shadow_templates is None
    assert engine._shadow_baseline is None
    assert 'shadow inputs could not be prepared' in caplog.text
    assert 'private details' not in caplog.text
