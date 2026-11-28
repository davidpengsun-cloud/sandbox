"""A shadow planner may read only fresh, already-captured data."""
from types import SimpleNamespace

import pytest

from kge.services.ask.engine.planrules import snapshot
from kge.services.ask.engine.graphview import Graph
from kge.services.ask.engine.schema_guess import guess_columns
from kge.services.ask.engine.shadowinputs import ShadowInputs, ShadowInputsStale, ShadowIOForbidden, prepare_baseline


def inputs():
    identity = {'release': 'r1', 'hash': 'a' * 64}
    templates = SimpleNamespace(release=dict(identity), shapes={})
    mappings = [
        {'term': 'flows', 'qualified': 'b.z.x', 'unit': 'b', 'tbl': 'z', 'col': 'x', 'dtype': 'TEXT',
         'grain': None, 'currency': None, 'calendar': None, 'conversion': None, 'certified_by': 's', 'certified_on': 'd'},
        {'term': 'flows', 'qualified': 'a.t.x', 'unit': 'a', 'tbl': 't', 'col': 'x', 'dtype': 'NUMBER',
         'grain': 'month', 'currency': 'USD', 'calendar': None, 'conversion': None, 'certified_by': 's', 'certified_on': 'd'},
        {'term': 'other', 'qualified': 'a.t.y', 'unit': 'a', 'tbl': 't', 'col': 'y', 'dtype': 'NUMBER',
         'grain': None, 'currency': None, 'calendar': None, 'conversion': None, 'certified_by': 's', 'certified_on': 'd'},
    ]
    graph_snapshot = SimpleNamespace(plan_writes=(3, ('a.t',)), mapping_declarations=mappings)
    baseline_snapshot = SimpleNamespace(plan_writes=(3, ('a.t',)), mapping_declarations=list(mappings))
    semantics = SimpleNamespace(contexts={}, partitioned={'a.t': {}}, plan_snapshot=graph_snapshot,
                                baseline_snapshot=baseline_snapshot)
    serving = SimpleNamespace(release=dict(identity), shapes={})
    graph = SimpleNamespace(writes=3, identity=identity, _templates=serving, _shadow_templates=templates)
    engine = SimpleNamespace(g=graph, semantics=semantics, UNIT_ORDER=['a', 'b'], term_names=['flows', 'other'],
                             identity={**identity, 'warehouses': {}})
    columns = {'a': [('T', 'NET_FLOW', 'NUMBER'), ('NET', 'CODE', 'TEXT')], 'b': [('Z', 'FLOW', 'TEXT')]}
    return engine, templates, graph_snapshot, baseline_snapshot, columns


def make(values, graph_mode=True):
    engine, templates, graph_snapshot, baseline_snapshot, columns = values
    runtime = graph_snapshot if graph_mode else baseline_snapshot
    return ShadowInputs(engine, templates=templates, graph_mode=graph_mode, runtime=runtime, columns=columns)


@pytest.mark.parametrize('graph_mode', [True, False])
def test_cached_inputs_preserve_mapping_projection_and_guess_order(graph_mode):
    values = inputs()
    facade = make(values, graph_mode)
    assert facade.identity == values[0].identity
    assert facade.g.templates() == values[1]
    assert facade.g.templates() is not values[1]
    assert snapshot(facade, graph=graph_mode) is values[2 if graph_mode else 3]
    with pytest.raises(ShadowIOForbidden):
        snapshot(facade, graph=not graph_mode)
    expected = [{key: row[key] for key in ('unit', 'tbl', 'col', 'dtype', 'grain', 'currency', 'calendar',
                                             'conversion', 'certified_by', 'certified_on')}
                for row in (values[2].mapping_declarations[1], values[2].mapping_declarations[0])]
    assert facade.g.implementations('flows') == expected
    assert facade.resolve('flows', SimpleNamespace(glossary=True)) == expected
    guessed = facade.guess(['flow'])
    assert guessed == guess_columns(values[0].UNIT_ORDER, ['flow'], values[4])
    assert [(row['unit'], row['tbl'], row['col']) for row in guessed] == [('a', 'T', 'NET_FLOW'), ('b', 'Z', 'FLOW')]
    assert facade.resolve('flow', SimpleNamespace(glossary=False, resolution='names')) == guessed
    values[4]['a'].clear()
    assert facade.w.columns('a') == [('T', 'NET_FLOW', 'NUMBER'), ('NET', 'CODE', 'TEXT')]
    facade.g.templates().shapes['changed'] = {}
    assert facade.g.templates().shapes == {}
    assert values[1].shapes == {}
    values[0].g._templates.shapes['serving-only'] = {}
    assert facade.g.templates().shapes == {}


def test_facade_refuses_graph_and_warehouse_io():
    facade = make(inputs())
    with pytest.raises(ShadowIOForbidden):
        facade.g.q('MATCH (x) RETURN x')
    with pytest.raises(ShadowIOForbidden):
        facade.w.rows('a', 'SELECT 1')
    with pytest.raises(ShadowInputsStale):
        facade.w.columns('unknown')


@pytest.mark.parametrize('changed', ['writes', 'identity', 'templates', 'partitions', 'runtime', 'term_names'])
@pytest.mark.parametrize('graph_mode', [True, False])
def test_stale_release_inputs_are_refused(changed, graph_mode):
    values = inputs()
    facade = make(values, graph_mode)
    engine = values[0]
    if changed == 'writes':
        engine.g.writes += 1
    elif changed == 'identity':
        engine.g.identity = {'release': 'r2'}
    elif changed == 'templates':
        engine.g._shadow_templates = None
    elif changed == 'partitions':
        engine.semantics.partitioned['a.other'] = {}
    elif changed == 'term_names':
        engine.term_names = ['changed']
    else:
        setattr(engine.semantics, 'plan_snapshot' if graph_mode else 'baseline_snapshot', None)
    with pytest.raises(ShadowInputsStale):
        facade.validate()


def test_missing_columns_fail_when_guess_needs_them():
    values = inputs()
    values[4].pop('b')
    with pytest.raises(ShadowInputsStale):
        make(values).guess(['flow'])


def test_stale_snapshot_rejected_at_capture():
    values = inputs()
    values[2].plan_writes = (2, ('a.t',))
    with pytest.raises(ShadowInputsStale):
        make(values)


def test_missing_snapshot_rejected_at_capture():
    engine, templates, _, baseline_snapshot, columns = inputs()
    with pytest.raises(ShadowInputsStale):
        ShadowInputs(engine, templates=templates, graph_mode=True, runtime=None, columns=columns)


def test_explicit_request_term_names_must_match_serving_epoch():
    engine, templates, runtime, _, columns = inputs()
    with pytest.raises(ShadowInputsStale):
        ShadowInputs(engine, templates=templates, graph_mode=True, runtime=runtime, columns=columns, term_names=['older'])


def test_template_release_mismatch_is_refused():
    values = inputs()
    values[1].release = {'release': 'other', 'hash': 'a' * 64}
    with pytest.raises(ShadowInputsStale):
        make(values)


def test_shadow_baseline_preparation_does_not_fill_or_replace_the_serving_cache(monkeypatch):
    from kge.services.ask.engine import planrules

    class Engine:
        def __init__(self):
            self._semantics = SimpleNamespace(baseline_snapshot='serving')
            self.g = SimpleNamespace(read_uncached=lambda query, params: [{'source': 'uncached'}])

        @property
        def semantics(self):
            return self._semantics

    engine = Engine()
    shadow = object()
    def build(isolated, graph):
        assert isolated is not engine and isolated.semantics is not engine.semantics
        assert graph is False and isolated.semantics.baseline_snapshot is None
        assert isolated.g.q('read') == [{'source': 'uncached'}]
        isolated.semantics.baseline_snapshot = shadow
        return shadow
    monkeypatch.setattr(planrules, 'snapshot', build)
    assert prepare_baseline(engine) is shadow
    assert engine.semantics.baseline_snapshot == 'serving'


def test_shadow_baseline_uses_only_the_explicit_shadow_owner():
    engine, templates, _, baseline, columns = inputs()
    engine._shadow_baseline = baseline
    engine.semantics.baseline_snapshot = None
    facade = ShadowInputs(engine, templates=templates, graph_mode=False, runtime=baseline, columns=columns,
                          shadow_baseline=True)
    assert snapshot(facade, graph=False) is baseline
    engine._shadow_baseline = None
    with pytest.raises(ShadowInputsStale):
        facade.validate()


def test_uncached_shadow_preparation_cannot_write_to_the_graph():
    with pytest.raises(ValueError, match='cannot write'):
        Graph.read_uncached(SimpleNamespace(), 'MATCH (n:Term) SET n.name = $name', {'name': 'x'})
