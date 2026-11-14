"""An evaluation holds role scope across workers and restores it when the batch ends."""
import json
import threading
from types import SimpleNamespace

import pytest

from kge.eval import harness
from kge.services.ask.engine.graphview import Graph, Rights


class StopEvaluation(BaseException):
    pass


@pytest.mark.parametrize("stop", [False, True])
def test_role_scope_outlives_each_worker_and_restores_after_success_or_failure(tmp_path, monkeypatch, stop):
    monkeypatch.setenv("EVAL_CONCURRENCY", "2")
    graph = object.__new__(Graph)
    graph._roles_only = False
    graph.role_ids = lambda: {"reader"}

    def no_people(who):
        pytest.fail("the evaluation must never read people")

    graph.people = SimpleNamespace(roles_of=no_people)
    barrier, finished = threading.Barrier(2), threading.Event()

    def answer(question, principal, config):
        assert graph.acting_roles(principal) == {"reader"}
        barrier.wait(timeout=5)
        if question == "second":
            assert finished.wait(timeout=5)
            assert graph.acting_roles(principal) == {"reader"}
            if stop:
                raise StopEvaluation()
        return {"question": question}

    arms = object.__new__(harness.Arms)
    arms.e = SimpleNamespace(g=graph, answer=answer)
    arms.about = lambda: {}
    arms.describe = lambda arm: {"side": "preset", "mode": "rules", "config": arm}
    questions = [{"id": value, "question": value, "kind": "within"} for value in ("first", "second")]
    monkeypatch.setattr(harness, "load", lambda *args: {"golden": questions})
    monkeypatch.setattr(harness, "describe_sets", lambda *args: {})
    monkeypatch.setattr(harness, "estate", lambda *args: {})
    monkeypatch.setattr(harness, "uncertified_ids", lambda *args: set())
    monkeypatch.setattr(harness.setfiles, "declared", lambda *args: {})
    monkeypatch.setattr(harness.metrics, "load_landing", lambda *args: {})
    monkeypatch.setattr(harness.metrics, "prices", lambda *args: {})
    monkeypatch.setattr(harness.metrics, "measure", lambda *args: {})
    monkeypatch.setattr(harness.runtrace, "run", lambda name, call, **kwargs: call())
    monkeypatch.setattr(harness.report, "write", lambda *args: None)
    monkeypatch.setattr(harness, "score", lambda *args: (True, "passed"))
    one = harness.one

    def after_question(*args):
        result = one(*args)
        if args[2]["id"] == "first":
            finished.set()
        return result

    monkeypatch.setattr(harness, "one", after_question)
    profile = SimpleNamespace(id="test", estate=lambda: tmp_path, asking=lambda who: "reader",
                              landing=tmp_path, paths=lambda: {"pack": tmp_path / "pack.yaml"})
    if stop:
        with pytest.raises(StopEvaluation):
            harness.run(profile, arms, [next(iter(harness.PRESETS))], ["golden"], 1, out=tmp_path / "runs", echo=lambda _: None)
    else:
        run = harness.run(profile, arms, [next(iter(harness.PRESETS))], ["golden"], 1, out=tmp_path / "runs", echo=lambda _: None)
        records = [json.loads(line) for line in (run / "records.jsonl").read_text().splitlines()]
        assert len(records) == 2 and all(record["ok"] for record in records)
    assert graph._roles_only is False


@pytest.mark.parametrize("runner", ["parity", "golden"])
@pytest.mark.parametrize("stop", [False, True])
def test_direct_evaluation_uses_only_roles_and_restores_scope(tmp_path, monkeypatch, runner, stop):
    from kge.eval import parity, run_golden

    monkeypatch.setenv("EVAL_CONCURRENCY", "2")
    graph = object.__new__(Graph)
    graph._roles_only = False
    graph.role_ids = lambda: {"reader"}
    graph.identity = {}
    graph._rights = Rights(graph.identity, {"reader": "Reader"}, {"reader": frozenset({"retail"})}, {}, frozenset())

    def no_people(who):
        pytest.fail("the evaluation must never read people")

    graph.people = SimpleNamespace(roles_of=no_people)
    asked = []

    def answer(question, principal, **kwargs):
        assert graph.acting_roles(principal) == {"reader"}
        asked.append((question, principal))
        if stop and question == "second":
            raise StopEvaluation()
        return {"behaviour": "answer", "figure": 1}

    engine = SimpleNamespace(g=graph, answer=answer, w=None, UNIT_ORDER=[], close=lambda: None)
    items = [{"id": value, "question": value, "kind": "within", "expect": {"figure": 1}} for value in ("first", "second")]
    module = parity if runner == "parity" else run_golden
    monkeypatch.setattr(module, "uncertified_ids", lambda *args: set())
    monkeypatch.setattr(module, "score", lambda *args: (True, "passed"))
    monkeypatch.setattr(run_golden.sets, "items", lambda path: items)
    if runner == "parity":
        def call():
            return parity.results(engine, tmp_path, "reader")
    else:
        profile = SimpleNamespace(asking=lambda who: "reader", estate=lambda: tmp_path, run_dir=lambda: tmp_path / "run")
        profile.serve = lambda: profile
        monkeypatch.setattr(run_golden, "resolve", lambda _: profile)
        monkeypatch.setattr(run_golden, "unknown_principal", lambda *args: None)
        monkeypatch.setattr(run_golden.Engine, "serving", lambda _: engine)
        monkeypatch.setattr(run_golden.sys, "argv", ["run_golden", "--arm", "all"])
        (tmp_path / "heldout.json").write_text("{}")
        call = run_golden.evaluate
    if stop:
        with pytest.raises(StopEvaluation):
            call()
    elif runner == "parity":
        assert all(row["ok"] for row in call().values())
    else:
        assert call() == 0
        assert json.loads((tmp_path / "run/eval.json").read_text())["heldout"]
    assert asked and all(principal == "reader" for _, principal in asked)
    assert graph._roles_only is False


@pytest.mark.parametrize('llm', [None, SimpleNamespace(provider='mock')])
def test_grounded_harness_arm_sets_the_planner_switch_and_preserves_existing_arms(monkeypatch, llm):
    from kge.services.ask.engine.combined import Combined

    calls = []
    def answer(question, principal, config):
        calls.append(('answer', question, principal, config))
        return {'behaviour': 'figure', 'figure': 4}
    engine = SimpleNamespace(g=object(), answer=answer)
    class FakeAgent:
        def __init__(self, engine, llm):
            self.llm = llm
            self.mode = 'rules' if llm is None else 'mock'
        def config(self, arm):
            config = harness.AGENT_ARMS[arm]
            return config if self.llm is None else config.with_stages(sql_source='model')
        def ask(self, question, principal, arm):
            calls.append(('agent', question, principal, arm))
            return {'behaviour': 'figure', 'figure': 4}
    def grounded(combined, question, principal, config):
        assert combined.plan_grounding is True
        assert combined.llm is llm
        calls.append(('ask', question, principal, config))
        return {'behaviour': 'figure', 'figure': 4}
    monkeypatch.setattr(harness, 'Agent', FakeAgent)
    monkeypatch.setattr(Combined, 'ask', grounded)
    paths = []
    def traced(path, call, **kwargs):
        assert kwargs == {'graph': engine.g}
        paths.append(path)
        return call()
    monkeypatch.setattr(harness.runtrace, 'run', traced)
    arms = harness.Arms(engine, llm=llm)
    legacy = list(harness.PRESETS) + list(harness.AGENT_ARMS)
    assert harness.choose_arms('all') == legacy
    assert harness.choose_arms('grounded,graph,grounded') == ['grounded', 'graph']
    for arm in legacy:
        preset = arm in harness.PRESETS
        config = harness.PRESETS[arm] if preset else arms.agent.config(arm)
        path = 'answer' if preset else 'agent'
        assert arms.describe(arm) == {'arm': arm, 'side': harness.SIDES[arm], 'path': path,
            'mode': 'rules' if preset else arms.agent.mode, 'config': config.label, 'stages': config.stages()}
        assert arms.ask(arm, 'one', 'reader')['figure'] == 4
        assert calls[-1] == (path, 'one', 'reader', config if preset else arm)
        assert paths[-1] == path
    config = harness.PRESETS['graph'] if llm is None else arms.grounded.default_config('graph').with_stages(
        sql_source='planner+model', renderer='template')
    assert arms.describe('grounded') == {'arm': 'grounded', 'side': harness.SIDES['grounded'], 'path': 'ask',
        'mode': arms.agent.mode, 'config': config.label, 'stages': config.stages()}
    assert arms.ask('grounded', 'one', 'reader')['figure'] == 4
    assert calls[-1] == ('ask', 'one', 'reader', config)
    assert paths[-1] == 'ask'
