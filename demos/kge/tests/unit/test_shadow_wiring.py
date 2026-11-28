"""HTTP-only shadow persistence and attribution at the serving seam."""
from types import SimpleNamespace
from threading import RLock

import pytest

from kge.semantics.plan import Abstain
from kge.services.ask import service
from kge.services.ask.engine import planpublic, shadowing


def cached_engine(shapes=None):
    identity = {'hash': 'a' * 64}
    templates = SimpleNamespace(release=dict(identity), shapes=shapes or {})
    runtime = SimpleNamespace(plan_writes=(0, ()), mapping_declarations=[])
    semantics = SimpleNamespace(partitioned={}, plan_snapshot=runtime, baseline_snapshot=runtime)
    serving = SimpleNamespace(release=dict(identity), shapes=shapes or {})
    graph = SimpleNamespace(_templates=serving, _shadow_templates=templates, identity=dict(identity), writes=0)
    guard = RLock()
    engine = SimpleNamespace(g=graph, semantics=semantics, identity=dict(identity), term_names=[], UNIT_ORDER=[],
                             _shadow_baseline=runtime, shadow_write_guard=lambda: guard)
    return engine, runtime, SimpleNamespace(graph=True)


def test_service_records_only_redacted_observation_and_trace_id(monkeypatch):
    kept = []
    observation = SimpleNamespace(id='a' * 32, served='coded')
    monkeypatch.setattr(service, 'shadow_log', lambda state, client: SimpleNamespace(append=kept.append))
    profile = SimpleNamespace(run_dir=lambda: '/state', client='client')
    out = {'run_trace': {}, 'answer_text': 'unchanged'}
    service._record_shadow(profile, out, [observation])
    assert kept == [observation]
    assert out == {'run_trace': {'shadow': {'observation_id': observation.id, 'served': 'coded'}},
                   'answer_text': 'unchanged'}


def test_shadow_log_failure_does_not_change_answer(monkeypatch, caplog):
    def failed(*args):
        raise OSError('private question text')
    monkeypatch.setattr(service, 'shadow_log', failed)
    out = {'run_trace': {}, 'answer_text': 'unchanged'}
    service._record_shadow(SimpleNamespace(run_dir=lambda: '/state', client='client'), out, [object()])
    assert out == {'run_trace': {}, 'answer_text': 'unchanged'}
    assert 'private question text' not in caplog.text


def test_comparison_runs_without_logging_and_does_not_rerun_coded_reply(monkeypatch):
    calls = []
    reply = Abstain(kind='abstain', spans=(), missing=())
    observation = SimpleNamespace(id='a' * 32, served='coded')
    class Compared:
        @property
        def outcome(self):
            return 'agree'
        def observe(self, **kwargs):
            calls.append(('observation', kwargs['served'], kwargs['release']))
            return observation
    def compare(self, question, *, coded, check):
        calls.append(('compare', coded))
        assert check(coded) is coded
        return Compared()
    monkeypatch.setattr(shadowing.Shadowing, 'read', compare)
    monkeypatch.setattr(planpublic.planrules, 'ledger', lambda *args: ())
    import kge.semantics.coverage as coverage
    monkeypatch.setattr(coverage, 'new_phrase', lambda *args: False)
    engine, runtime, cfg = cached_engine()
    req = SimpleNamespace(question='synthetic')
    planner = SimpleNamespace(prepare=lambda *args: {'kind': 'abstain'})
    args = (engine, req, cfg, 'turn', (), False, planner, object(), reply, 'coded')
    planpublic._shadow_compare(*args, shadow_runtime=runtime)
    assert calls == [('compare', reply)]
    calls.clear()
    with planpublic.capture_shadow() as captured:
        planpublic._shadow_compare(*args, shadow_runtime=runtime)
    assert calls == [('compare', reply), ('observation', 'coded', 'a' * 64)]
    assert captured == [observation]


@pytest.mark.parametrize('source', ['narrowed_clarification', 'proposal_becomes_clarification'])
def test_shadow_records_the_checked_clarification_and_only_its_permitted_choices(monkeypatch, source):
    from kge.adapters.shadowlog.record import line_of
    from kge.semantics.plan import Propose
    from kge.services.ask.engine import graphplanner
    from tests.unit.test_currency_candidates import clarify, coordinator, revenue_plan, revenues

    vocabulary, choices = revenues()
    question = 'Revenue in USD' if source == 'narrowed_clarification' else 'Revenue'
    raw = (clarify(vocabulary, question, choices) if source == 'narrowed_clarification' else
           Propose(kind='propose', base_turn=None, plan=revenue_plan(vocabulary, choices[0], question)))
    before = raw.model_dump()
    planner = coordinator(vocabulary)
    checked = planner.prepare(question, raw)
    assert checked['kind'] == 'clarify'
    expected = [choices[0]] if source == 'narrowed_clarification' else sorted(choices)
    assert [c['id'] for c in checked['choices']] == expected
    graph_reply = Abstain(kind='abstain', spans=(), missing=())
    monkeypatch.setattr(graphplanner, 'GRAPH', SimpleNamespace(read=lambda _: (graph_reply, ())))
    engine, runtime, cfg = cached_engine()
    with planpublic.capture_shadow() as captured:
        planpublic._shadow_compare(engine, SimpleNamespace(question=question), cfg, 'turn', (), False,
                                   planner, vocabulary, raw, 'coded', shadow_runtime=runtime)
    assert len(captured) == 1
    observation = captured[0]
    assert observation.comparison == 'declines_differ'
    assert (observation.coded_outcome, observation.graph_outcome) == ('clarify', 'abstain')
    assert observation.evidence['coded']['outcome'] == 'clarify'
    assert observation.evidence['coded']['candidates'] == expected
    assert observation.evidence['coded']['applied'] == [[0, 7]]
    line_of(observation)
    assert raw.model_dump() == before


def test_model_comparison_skips_when_all_shapes_retired(monkeypatch):
    calls = []
    def unexpected(*args, **kwargs):
        calls.append(True)
    monkeypatch.setattr(shadowing.Shadowing, 'read', unexpected)
    engine, runtime, cfg = cached_engine({'a': {'stage': 'retired'}})
    with planpublic.capture_shadow() as captured:
        planpublic._shadow_compare(engine, SimpleNamespace(question='synthetic'), cfg, 'turn', (), False,
                                   object(), object(), None, 'model', shadow_runtime=runtime)
    assert captured == []
    assert calls == []


def test_model_request_is_compared_while_a_shape_is_not_retired(monkeypatch):
    calls = []
    observation = SimpleNamespace(id='b' * 32, served='model')
    class Compared:
        def observe(self, **kwargs):
            calls.append(('served', kwargs['served']))
            return observation
    def compare(self, question, *, coded, check):
        calls.append(('coded', coded))
        return Compared()
    monkeypatch.setattr(shadowing.Shadowing, 'read', compare)
    monkeypatch.setattr(planpublic.planrules, 'ledger', lambda *args: ())
    import kge.semantics.coverage as coverage
    monkeypatch.setattr(coverage, 'new_phrase', lambda *args: True)
    engine, runtime, cfg = cached_engine({'a': {'stage': 'shadow', 'id': 'a'}})
    with planpublic.capture_shadow() as captured:
        planpublic._shadow_compare(engine, SimpleNamespace(question='synthetic'), cfg, 'turn', (), False,
                                   object(), object(), None, 'model', shadow_runtime=runtime)
    assert calls == [('coded', None), ('served', 'model')]
    assert captured == [observation]


def test_model_first_control_uses_shadow_baseline_and_captured_columns_without_io(monkeypatch):
    from kge.semantics import coverage
    from kge.services.ask.engine.planrules import snapshot

    calls, records = [], []
    engine, runtime, _ = cached_engine({'a': {'stage': 'shadow', 'id': 'a'}})
    engine.semantics.baseline_snapshot = None
    engine._shadow_baseline = runtime
    engine.UNIT_ORDER = ['a']
    engine.g.q = lambda *args, **kwargs: calls.append('graph I/O')
    engine.w = SimpleNamespace(columns=lambda *args: calls.append('warehouse I/O'))
    observation = SimpleNamespace(id='b' * 32, served='model')
    class Compared:
        def observe(self, **kwargs):
            records.append(kwargs['served'])
            return observation
    def compare(self, question, *, coded, check):
        assert snapshot(question.engine, graph=False) is runtime
        result = question.engine.guess(['metric'])
        assert [(row['unit'], row['tbl'], row['col']) for row in result] == [('a', 'table', 'metric')]
        calls.append('compared')
        return Compared()
    monkeypatch.setattr(shadowing.Shadowing, 'read', compare)
    monkeypatch.setattr(planpublic.planrules, 'ledger', lambda *args: ())
    monkeypatch.setattr(coverage, 'new_phrase', lambda *args: False)
    with planpublic.capture_shadow() as captured:
        planpublic._shadow_compare(engine, SimpleNamespace(question='synthetic'), SimpleNamespace(graph=False),
                                   'turn', (), False, object(), object(), None, 'model',
                                   shadow_columns={'a': [('table', 'metric', 'NUMBER')]}, shadow_runtime=object())
    assert calls == ['compared']
    assert records == ['model']
    assert captured == [observation]


def test_model_first_control_missing_shadow_baseline_is_fail_open_without_io(monkeypatch, caplog):
    engine, _, _ = cached_engine({'a': {'stage': 'shadow', 'id': 'a'}})
    engine.semantics.baseline_snapshot = None
    engine._shadow_baseline = None
    calls = []
    engine.g.q = lambda *args, **kwargs: calls.append('graph I/O')
    engine.w = SimpleNamespace(columns=lambda *args: calls.append('warehouse I/O'))
    monkeypatch.setattr(shadowing.Shadowing, 'read', lambda *args, **kwargs: calls.append('comparison'))
    with planpublic.capture_shadow() as captured:
        planpublic._shadow_compare(engine, SimpleNamespace(question='synthetic'), SimpleNamespace(graph=False),
                                   'turn', (), False, object(), object(), None, 'model', shadow_runtime=object())
    assert calls == []
    assert captured == []
    assert 'shadow comparison could not be recorded' in caplog.text


def test_comparison_failure_is_fail_open_and_never_logs_exception_text(monkeypatch, caplog):
    def failed(*args, **kwargs):
        raise ValueError('private question text')
    monkeypatch.setattr(shadowing.Shadowing, 'read', failed)
    engine, runtime, cfg = cached_engine()
    with planpublic.capture_shadow() as captured:
        planpublic._shadow_compare(engine, SimpleNamespace(question='synthetic'), cfg, 'turn', (), False,
                                   object(), object(), None, 'coded', shadow_runtime=runtime)
    assert captured == []
    assert 'shadow comparison could not be recorded' in caplog.text
    assert 'private question text' not in caplog.text


@pytest.mark.parametrize('invalid', ['missing_runtime', 'stale_runtime', 'missing_templates'])
def test_missing_or_stale_cache_warns_without_backend_io(monkeypatch, caplog, invalid):
    engine, runtime, cfg = cached_engine({'a': {'stage': 'shadow', 'id': 'a'}})
    calls = []
    engine.g.q = lambda *args, **kwargs: calls.append('graph')
    engine.w = SimpleNamespace(columns=lambda *args: calls.append('warehouse'))
    monkeypatch.setattr(shadowing.Shadowing, 'read', lambda *args, **kwargs: calls.append('comparison'))
    if invalid == 'missing_runtime':
        runtime = None
    elif invalid == 'stale_runtime':
        engine.g.writes += 1
    else:
        engine.g._shadow_templates = None
    with planpublic.capture_shadow() as captured:
        planpublic._shadow_compare(engine, SimpleNamespace(question='synthetic'), cfg, 'turn', (), False,
                                   object(), object(), None, 'coded', shadow_runtime=runtime)
    assert captured == []
    assert calls == []
    assert 'shadow comparison could not be recorded' in caplog.text


def test_model_served_request_compares_without_extra_model_or_warehouse_calls(monkeypatch):
    from kge.services.ask.engine import graphplanner, rulesport
    import kge.semantics.coverage as coverage

    counts = {'model': 0, 'warehouse': 0, 'coded': 0, 'graph': 0}
    reply = Abstain(kind='abstain', spans=(), missing=())
    class Coded:
        name = 'coded'
        def plan(self, question):
            counts['coded'] += 1
            return reply
    class Graph:
        def read(self, question):
            counts['graph'] += 1
            return reply, ()
    monkeypatch.setattr(rulesport, 'rules_planner', lambda name='coded': Coded())
    monkeypatch.setattr(graphplanner, 'GRAPH', Graph())
    monkeypatch.setattr(planpublic.planrules, 'ledger', lambda *args: ())
    monkeypatch.setattr(coverage, 'new_phrase', lambda *args: False)
    monkeypatch.setattr(service.runtrace, 'run', lambda path, call, **kwargs: {**call(), 'run_trace': {}})
    monkeypatch.setattr(service, 'with_receipt', lambda out, engine, profile: out)
    monkeypatch.setattr(service, 'log_unmatched', lambda *args: None)
    monkeypatch.setattr(service, 'worded', lambda out, engine: out)
    monkeypatch.setattr(service, 'ordered', lambda out: out)
    monkeypatch.setattr(service, 'client_result', lambda out: out)
    monkeypatch.setattr(service, 'with_sql_certified', lambda out: out)
    kept = []
    monkeypatch.setattr(service, 'shadow_log', lambda state, client: SimpleNamespace(append=kept.append))
    cached, runtime, cfg = cached_engine({'a': {'stage': 'shadow', 'id': 'a'}})
    planner = SimpleNamespace(prepare=lambda *args: {'kind': 'abstain'})
    class Engine:
        g = cached.g
        semantics = cached.semantics
        identity = cached.identity
        term_names = cached.term_names
        UNIT_ORDER = cached.UNIT_ORDER
        def shadow_write_guard(self):
            return cached.shadow_write_guard()
        def answer(self, question, who, purpose, **kwargs):
            counts['model'] += 1
            counts['warehouse'] += 1
            planpublic._shadow_compare(self, SimpleNamespace(question=question), cfg, 'turn', (), False,
                                       planner, object(), None, 'model', shadow_runtime=runtime)
            return {'answer_text': 'unchanged'}
    profile = SimpleNamespace(run_dir=lambda: '/state', client='client')
    engine = Engine()
    compare = planpublic._shadow_compare
    monkeypatch.setattr(planpublic, '_shadow_compare', lambda *args, **kwargs: None)
    baseline = service.answer(engine, profile, 'synthetic', 'analyst', 'analysis', 'graph', cfg)
    baseline_calls = {key: counts[key] for key in ('model', 'warehouse')}
    counts.update(model=0, warehouse=0)
    monkeypatch.setattr(planpublic, '_shadow_compare', compare)
    result = service.answer(engine, profile, 'synthetic', 'analyst', 'analysis', 'graph', cfg)
    assert baseline['answer_text'] == result['answer_text'] == 'unchanged'
    assert {key: counts[key] for key in baseline_calls} == baseline_calls == {'model': 1, 'warehouse': 1}
    assert counts['coded'] == counts['graph'] == 1
    assert len(kept) == 1 and kept[0].served == 'model'
    assert result['run_trace']['shadow']['observation_id'] == kept[0].id


def test_direct_evaluation_compares_but_does_not_append(monkeypatch):
    calls = []
    class Compared:
        @property
        def outcome(self):
            calls.append('compared')
            return 'agree'
    monkeypatch.setattr(shadowing.Shadowing, 'read', lambda *args, **kwargs: Compared())
    engine, runtime, cfg = cached_engine()
    planpublic._shadow_compare(engine, SimpleNamespace(question='synthetic'), cfg, 'turn', (), False,
                               object(), object(), None, 'coded', shadow_runtime=runtime)
    assert calls == ['compared']


def test_shadow_work_never_adds_reads_to_the_served_graph_path(monkeypatch):
    from kge.pipeline import trace
    import kge.semantics.coverage as coverage

    seen = []
    class Recorder:
        def record(self, kind, value, used):
            seen.append(kind)
            return {}
    class Compared:
        def observe(self, **kwargs):
            trace.graph_read('shadow_evidence', None)
            return object()
    def compare(*args, **kwargs):
        trace.graph_read('shadow_adapter', None)
        return Compared()
    def ledger(*args):
        trace.graph_read('shadow_ledger', None)
        return ()
    monkeypatch.setattr(shadowing.Shadowing, 'read', compare)
    monkeypatch.setattr(planpublic.planrules, 'ledger', ledger)
    monkeypatch.setattr(coverage, 'new_phrase', lambda *args: False)
    engine, runtime, cfg = cached_engine()
    with trace.graph_recording(Recorder()), planpublic.capture_shadow() as captured:
        trace.graph_read('served_before', None)
        planpublic._shadow_compare(engine, SimpleNamespace(question='synthetic'), cfg, 'turn', (), False,
                                   object(), object(), None, 'coded', shadow_runtime=runtime)
        trace.graph_read('served_after', None)
    assert seen == ['served_before', 'served_after']
    assert len(captured) == 1


def test_scope_fallback_keeps_only_final_attempt_and_preserves_existing_capture(monkeypatch):
    requests = [SimpleNamespace(units=[unit]) for unit in ('a', 'b', 'c')]
    monkeypatch.setattr(planpublic, 'scope_requests', lambda *args: requests)
    def selected(engine, request, *args, **kwargs):
        if (capture := planpublic._shadow_capture.get()) is not None:
            capture.extend([request.units[0], request.units[0] + '-second'])
        return {'behaviour': 'abstain', 'unit': request.units[0]}
    monkeypatch.setattr(planpublic, 'selected_answer', selected)
    engine = SimpleNamespace(needs_scope_fallback=lambda result: True)
    req = SimpleNamespace(units=[], all_units=False)
    scope = SimpleNamespace(products=None, units=[])
    with planpublic.capture_shadow() as captured:
        captured.append('previous')
        result = planpublic.answer(engine, req, 'who', 'purpose', None, scope, {'trace': []})
        assert captured == ['previous', 'c', 'c-second']
    assert result == {'behaviour': 'abstain', 'unit': 'c'}
    assert planpublic.answer(engine, req, 'who', 'purpose', None, scope, {'trace': []}) == result
