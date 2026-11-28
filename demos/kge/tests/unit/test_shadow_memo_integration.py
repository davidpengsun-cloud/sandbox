"""Resume a background answer without rereading its rules planner or shadow comparison."""
from dataclasses import dataclass, field
from threading import RLock
from types import SimpleNamespace

import pytest

from kge.pipeline import questionrun
from kge.semantics.plan import Abstain
from kge.services.ask.engine import planpublic, shadow


@dataclass
class Vocabulary:
    binding: object
    default_units: tuple = ()
    contexts: dict = field(default_factory=dict)
    derived_measures: dict = field(default_factory=dict)

    def denial(self, question):
        return False


def harness(monkeypatch, *, model=False, model_eligible=True):
    class Binding:
        def model_dump(self, **kwargs):
            return {}
    vocab = Vocabulary(Binding())
    runtime = SimpleNamespace(mapping_declarations=[], contexts={})
    guard = RLock()
    shapes = {'a': {'stage': 'shadow' if model_eligible else 'retired'}}
    graph = SimpleNamespace(_templates=SimpleNamespace(shapes=shapes), readable_units=lambda who: set())
    engine = SimpleNamespace(g=graph, w=object(), UNIT_ORDER=[], shadow_write_guard=lambda: guard)
    req = SimpleNamespace(question='synthetic question', units=[], terms_found=[], term=None, problem=None, period=None)
    cfg = SimpleNamespace(graph=False, label='schema', uses_model=lambda stage: model)
    scope = SimpleNamespace(products=None)
    reply = Abstain(kind='abstain', spans=(), missing=('private question text',))
    observation = shadow.observe(release='release-1', graph=reply, coded=reply, graph_shapes=(), served='coded',
                                 pieces=(), vocab=SimpleNamespace(entries={}), new_id=lambda: 'a' * 32)
    counts = {'coded': 0, 'compare': 0}
    def planned(*args):
        counts['coded'] += 1
        return reply
    def candidate(*args, **kwargs):
        counts['compare'] += 1
        return observation
    class Planner:
        def __init__(self, *args, **kwargs):
            pass
        def answer_model(self, *args, **kwargs):
            return {'kind': 'abstain', 'missing': []}
        def safe(self, value):
            return value
    def rules_first(planner, question, rules, turn, history, release_periods, follow_ups):
        found = rules(question, vocab)
        return (None if model else {'kind': 'abstain', 'missing': []}), found
    monkeypatch.setattr(planpublic.planrules, 'vocabulary', lambda *args: vocab)
    monkeypatch.setattr(planpublic.planrules, 'snapshot', lambda *args: runtime)
    monkeypatch.setattr(planpublic.planrules, 'ledger', lambda *args: ())
    monkeypatch.setattr(planpublic.Hidden, 'of', lambda *args: object())
    monkeypatch.setattr(planpublic, 'Planner', Planner)
    monkeypatch.setattr(planpublic, 'rules_first', rules_first)
    monkeypatch.setattr(planpublic, 'ask_order', lambda: 'rules_first')
    monkeypatch.setattr(planpublic, 'ask_ai_fallback', lambda configured: 'on')
    monkeypatch.setattr(planpublic.rulesport, 'rules_planner', lambda: object())
    monkeypatch.setattr(planpublic.rulesport, 'planned', planned)
    monkeypatch.setattr(planpublic, '_shadow_candidate', candidate)
    monkeypatch.setattr(planpublic.planledger, 'block', lambda *args: {})
    monkeypatch.setattr(planpublic.planmodel, 'verified_examples', lambda *args: ())
    combined = SimpleNamespace(llm=SimpleNamespace(name='stub')) if model else None
    def run():
        return planpublic.selected_answer(engine, req, 'who', 'analysis', cfg, scope, {'trace': [], 'versions': {}},
                                          combined=combined)
    return run, counts, observation


def test_resume_reuses_bare_reply_and_recompares_current_inputs(monkeypatch):
    run, counts, observation = harness(monkeypatch)
    record, saves = {}, []
    with questionrun.checkpoints(record, lambda: saves.append(questionrun.dumps(record))), planpublic.capture_shadow() as found:
        first = run()
    assert counts == {'coded': 1, 'compare': 1}
    assert len(saves) == 1 and found == [observation]
    restored = questionrun.loads(saves[0])
    key = next(iter(restored['plan']))
    assert key.startswith('0:rules:')
    assert restored['plan'][key]['kind'] == 'abstain'
    assert restored['shadow_plan'][key]['observation']['id'] == observation.id
    assert 'private question text' not in questionrun.dumps(restored['shadow_plan'])
    with questionrun.checkpoints(restored, lambda: pytest.fail('resume must not save')), planpublic.capture_shadow() as resumed:
        second = run()
    assert counts == {'coded': 1, 'compare': 2}
    assert resumed == [observation]
    assert first == second


def test_legacy_memo_compares_the_held_reply_on_resume(monkeypatch):
    run, counts, _ = harness(monkeypatch)
    record = {}
    with questionrun.checkpoints(record, lambda: None), planpublic.capture_shadow():
        run()
    record.pop('shadow_plan')
    with questionrun.checkpoints(record, lambda: pytest.fail('legacy hit must not save')), planpublic.capture_shadow() as resumed:
        run()
    assert counts == {'coded': 1, 'compare': 2}
    assert len(resumed) == 1


def test_malformed_sidecar_is_replaced_by_fresh_comparison(monkeypatch):
    run, counts, _ = harness(monkeypatch)
    record = {}
    with questionrun.checkpoints(record, lambda: None), planpublic.capture_shadow():
        run()
    key = next(iter(record['shadow_plan']))
    record['shadow_plan'][key] = {'observation': {'id': 'bad'}, 'model_eligible': True}
    with questionrun.checkpoints(record, lambda: pytest.fail('resume must not save')), planpublic.capture_shadow() as resumed:
        out = run()
    assert counts == {'coded': 1, 'compare': 2}
    assert len(resumed) == 1
    assert out['behaviour'] == 'abstain'


@pytest.mark.parametrize('eligible', [True, False])
def test_model_served_resume_keeps_only_eligible_observations(monkeypatch, eligible):
    run, counts, _ = harness(monkeypatch, model=True, model_eligible=eligible)
    record = {}
    with questionrun.checkpoints(record, lambda: None), planpublic.capture_shadow() as first:
        out = run()
    assert out['answered_by'] == 'model'
    assert len(first) == int(eligible)
    if eligible:
        assert first[0].served == 'model'
    with questionrun.checkpoints(record, lambda: pytest.fail('resume must not save')), planpublic.capture_shadow() as resumed:
        out = run()
    assert out['answered_by'] == 'model'
    assert len(resumed) == int(eligible)
    assert counts == {'coded': 1, 'compare': 2}


@pytest.mark.parametrize('commits', [False, True])
def test_terminal_write_fault_keeps_the_durable_shadow_id(monkeypatch, tmp_path, commits):
    from datetime import datetime, timedelta, timezone
    import time

    from kge.adapters.objstore import open_store
    from kge.adapters.questionstate.store import StoreQuestionRuns
    from kge.adapters.shadowlog.folder import FolderShadowLog
    from kge.ports import StateUnavailable
    from kge.services.ask import service
    from kge.services.ask.engine.config import PRESETS
    from kge.services.ask.models import Ask

    run, counts, observation = harness(monkeypatch)
    raw_ids, writes = [], []
    reply = Abstain(kind='abstain', spans=(), missing=())
    log = FolderShadowLog(tmp_path / 'shadow')

    def candidate(*args, **kwargs):
        counts['compare'] += 1
        at = datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(days=len(raw_ids))
        obs = shadow.observe(release=observation.release, graph=reply, coded=reply, graph_shapes=(),
                             served='coded', pieces=(), vocab=SimpleNamespace(entries={}), now=at)
        raw_ids.append(obs.id)
        return obs

    monkeypatch.setattr(planpublic, '_shadow_candidate', candidate)
    monkeypatch.setattr(service, 'shadow_log', lambda *args: log)
    monkeypatch.setattr(service.runtrace, 'run', lambda path, call, **kwargs: call())
    monkeypatch.setattr(service, 'with_receipt', lambda out, *args: out)
    monkeypatch.setattr(service, 'worded', lambda out, *args: out)
    monkeypatch.setattr(service, 'log_unmatched', lambda *args: None)
    combined = SimpleNamespace(e=SimpleNamespace(identity='r1'), g=SimpleNamespace(readable_units=lambda who: set()),
                               ask=lambda *args, **kwargs: run())
    profile = SimpleNamespace(run_dir=lambda: tmp_path, client='sample')
    state = StoreQuestionRuns(open_store(str(tmp_path / 'runs')))
    put = state.put
    run_id = None

    def terminal_fault(key, record, revision):
        nonlocal run_id
        run_id = key
        writes.append(record['state'])
        if record['state'] == 'done' and writes.count('done') == 1:
            if commits:
                put(key, record, revision)
            raise StateUnavailable('injected terminal write error')
        return put(key, record, revision)

    monkeypatch.setattr(state, 'put', terminal_fault)
    with pytest.raises(StateUnavailable, match='injected terminal'):
        service.start_run(combined, profile, Ask(question='synthetic question'), 'who', PRESETS['graph'],
                          state=state, clock=time.time)
    first = log.read(observation.release)
    assert len(first) == 1
    held, _ = state.get(run_id)
    now = max(held['lease'], held['next_poll']) + 1
    result = service.get_run(combined, profile, run_id, 'who', state=state, clock=lambda: now)

    attempts = 1 if commits else 2
    assert len(set(raw_ids)) == attempts
    shadow_id = first[0]['id']
    assert shadow_id != run_id and shadow_id not in raw_ids
    assert log.read(observation.release) == first
    assert counts == {'coded': 1, 'compare': attempts}
    assert result['state'] == 'done' and result['answer']['behaviour'] == 'abstain'
    assert result['answer']['run_trace']['shadow']['observation_id'] == shadow_id
    assert writes == ['running', 'running', 'done'] + ([] if commits else ['running', 'done'])
