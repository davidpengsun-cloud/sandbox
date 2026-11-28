"""Shadow evidence from a suspended run must not be credited after its inputs change."""
from dataclasses import replace
from threading import Event, Thread
from types import SimpleNamespace

import pytest

from kge.pipeline import questionrun
from kge.semantics.plan import Abstain
from kge.services.ask.engine import planpublic, shadowing

from tests.unit.test_shadow_memo_integration import harness
from tests.unit.test_shadow_template_snapshot import graph_for_write
from tests.unit.test_shadow_wiring import cached_engine


def test_resume_after_same_release_term_change_does_not_reuse_stale_observation(monkeypatch):
    run, counts, _ = harness(monkeypatch)
    values = {name: cell.cell_contents for name, cell in zip(run.__code__.co_freevars, run.__closure__)}
    engine = values['engine']
    engine.term_names = ['original']
    compare = planpublic._shadow_candidate
    def current_candidate(*args, **kwargs):
        observation = compare(*args, **kwargs)
        return replace(observation, id='b' * 32, new_phrase=True) if 'new-term' in engine.term_names else observation
    monkeypatch.setattr(planpublic, '_shadow_candidate', current_candidate)
    record = {}
    with questionrun.checkpoints(record, lambda: None), planpublic.capture_shadow() as first:
        first_answer = run()
    assert len(first) == 1
    assert first[0].id == 'a' * 32
    engine.term_names = ['original', 'new-term']
    with questionrun.checkpoints(record, lambda: pytest.fail('memo hit must not save')), planpublic.capture_shadow() as resumed:
        resumed_answer = run()
    assert counts == {'coded': 1, 'compare': 2}
    assert len(resumed) == 1
    assert resumed[0].id == 'b' * 32
    assert resumed[0].new_phrase is True
    assert first_answer == resumed_answer


def test_same_term_graph_write_between_comparison_and_answer_is_not_credited(monkeypatch, caplog):
    run, _, _ = harness(monkeypatch)
    values = {name: cell.cell_contents for name, cell in zip(run.__code__.co_freevars, run.__closure__)}
    engine = values['engine']
    engine.term_names = ['original']
    engine.g.writes = 0
    def change_graph(*args):
        engine.g.writes += 1
        return {}
    monkeypatch.setattr(planpublic.planledger, 'block', change_graph)
    with questionrun.checkpoints({}, lambda: None), planpublic.capture_shadow() as captured:
        result = run()
    assert result['behaviour'] == 'abstain'
    assert engine.term_names == ['original']
    assert engine.g.writes == 1
    assert captured == []
    assert 'shadow checkpoint inputs changed before resume' in caplog.text


def test_direct_graph_write_during_last_plan_check_cannot_credit_stale_observation(monkeypatch, caplog):
    from kge.semantics import coverage

    engine, runtime, cfg = cached_engine()
    graph = graph_for_write()
    graph.identity = dict(engine.identity)
    graph._shadow_templates = SimpleNamespace(release=dict(engine.identity), shapes={})
    engine.g = graph
    started, completed = Event(), Event()
    errors = []
    def write():
        try:
            if not started.wait(3):
                raise AssertionError('planner did not reach the final check')
            graph.q("MATCH (n) SET n.stage = 'retired'")
        except Exception as exc:
            errors.append(exc)
        finally:
            completed.set()
    writer = Thread(target=write)
    writer.start()
    def prepare(*args):
        started.set()
        assert completed.wait(3)
        return {'kind': 'abstain'}
    class Compared:
        def observe(self, **kwargs):
            return SimpleNamespace(id='a' * 32)
    def read(self, question, *, coded, check):
        check(coded)
        return Compared()
    monkeypatch.setattr(shadowing.Shadowing, 'read', read)
    monkeypatch.setattr(planpublic.planrules, 'ledger', lambda *args: ())
    monkeypatch.setattr(coverage, 'new_phrase', lambda *args: False)
    reply = Abstain(kind='abstain', spans=(), missing=())
    with planpublic.capture_shadow() as captured:
        planpublic._shadow_compare(engine, SimpleNamespace(question='synthetic'), cfg, 'turn', (), False,
                                   SimpleNamespace(prepare=prepare), object(), reply, 'coded', shadow_runtime=runtime)
    writer.join(3)
    assert not writer.is_alive() and not errors
    assert graph.writes == 1
    assert captured == []
    assert 'shadow comparison could not be recorded' in caplog.text
