"""Run the existing adversarial assertions against deterministically grounded proposals too."""
import pytest
from dataclasses import replace

from kge.semantics.coverage import ground_plan
from kge.semantics.plancheck import CheckedPlan, check_plan
from kge.semantics.plan import HistoryEntry, Propose
from kge.semantics.query import Rule
from kge.services.ask.engine.planner import Planner
from kge.services.ask.engine.wording import Hidden
from tests.plan_fixtures import grounded, model, name, vocabulary, semantics
from tests.unit import test_plan_question_gates as gates


@pytest.fixture(autouse=True)
def grounded_checks(monkeypatch):
    original = gates.checked
    def checked(plan, text, vocab):
        return original(ground_plan(plan, text, 'turn', vocab), text, vocab)
    monkeypatch.setattr(gates, 'checked', checked)


for _name in dir(gates):
    if _name.startswith('test_'):
        globals()['test_grounding_' + _name.removeprefix('test_')] = getattr(gates, _name)


def test_grounding_cannot_use_a_shared_measure_to_drop_a_named_context():
    sem = semantics()
    sem.contexts['second'] = replace(sem.contexts['ctx'], id='second', term='Second', label='Second', grain='day')
    vocab = vocabulary(sem, aliases=[('First', 'ctx')])
    vocab = replace(vocab, phrases={**vocab.phrases, 'second': ('second', 'u.metric')})
    text = 'First and Second'
    plan = ground_plan(grounded(model(), text, vocab), text, 'turn', vocab)
    assert not isinstance(check_plan(plan, vocab, text), CheckedPlan)


def test_grounding_cannot_add_an_unmentioned_predicate_with_the_requested_operator_and_number():
    vocab = vocabulary()
    text = 'Metric value equal to 1'
    filters = [dict(kind='compare', id=identifier, left=dict(kind='column', name=name(column), path=[]),
                    op='eq', value=dict(kind='integer', value=1), evidence=[])
               for identifier, column in [('f', 'u.events.value'), ('invented', 'u.events.entity_id')]]
    plan = grounded(model(filters=filters), text, vocab,
                    {'value': 'f', 'equal to': ['f', 'invented'], '1': ['f', 'invented']})
    assert not isinstance(check_plan(ground_plan(plan, text, 'turn', vocab), vocab, text), CheckedPlan)


@pytest.mark.parametrize('column', ['u.events.value', 'u.events.entity_id'])
def test_numeric_comparisons_can_use_only_the_named_metrics_implicit_value(monkeypatch, tmp_path, column):
    from tests.unit.test_planner_jobs import jobs, setup

    sem = semantics()
    sem.contexts['other'] = replace(sem.contexts['ctx'], id='other', label='Other', term='Other', grain='day',
                                    values=('u.events.entity_id',))
    vocab = vocabulary(sem, aliases=[('Metric', 'ctx')])
    text = 'Metric above 0'
    predicate = dict(kind='compare', id='f', left=dict(kind='column', name=name(column), path=[]),
                     op='gt', value=dict(kind='integer', value=0), evidence=[])
    plan = grounded(model(filters=[predicate]), text, vocab, {'above': 'f', '0': 'f'})
    checked = check_plan(ground_plan(plan, text, 'turn', vocab), vocab, text)
    assert isinstance(checked, CheckedPlan) is (column == 'u.events.value')
    fake, warehouses = setup(monkeypatch, tmp_path, False)
    try:
        answer = Planner(vocab, warehouses, Hidden([], set(), [])).answer(
            text, Propose(kind='propose', base_turn=None, plan=plan), grounding=True)
        if column == 'u.events.value':
            assert answer['kind'] == 'result'
            assert answer['result']['rows'][0]['cells'][0]['value']['value'] == '15.0'
            assert jobs(fake) == (1, 1)
        else:
            assert answer['kind'] == 'abstain' and 'result' not in answer
            assert jobs(fake) == (0, 0)
    finally:
        fake.con.close()


def test_grounding_preserves_an_explicit_required_predicate_and_its_followup_binding():
    sem = semantics()
    sem.contexts['ctx'] = replace(sem.contexts['ctx'], grain='day', filters=(Rule('fixed', '', 'events.value = 1', 'u.events'),))
    vocab = vocabulary(sem)
    text = 'Metric value equal to 1'
    predicate = dict(kind='compare', id='f', left=dict(kind='column', name=name('u.events.value'), path=[]),
                     op='eq', value=dict(kind='integer', value=1), evidence=[])
    plan = ground_plan(grounded(model(filters=[predicate]), text, vocab,
                               {'value': 'f', 'equal to': 'f', '1': 'f'}), text, 'turn', vocab)
    assert len(plan.filters) == 1
    assert isinstance(check_plan(plan, vocab, text), CheckedPlan)
    history = (HistoryEntry(turn_id='turn', question=text, plan=plan, clarification=None, binding=vocab.binding),)
    reply = Propose(kind='propose', base_turn='turn', plan=plan)
    assert isinstance(Planner(vocab, None, Hidden([], set(), [])).prepare(
        'Now just', reply, 'next', history, grounding=True), CheckedPlan)


def test_grounding_binds_period_evidence_by_selected_windows_instead_of_model_targets():
    vocab = vocabulary()
    text = 'Metric for March 2025 versus April 2025'
    def month(value):
        return dict(kind='single', period=dict(kind='month', value=value, calendar=name('cal')))
    plan = grounded(model(time=month('2025-03'), comparisons=[dict(kind='explicit_period', id='c',
        inputs=['m'], other=month('2025-04'), evidence=[])]), text, vocab,
        {'March 2025': 'c', 'April 2025': 'time', 'versus': 'c'})
    assert not isinstance(check_plan(plan, vocab, text), CheckedPlan)
    assert isinstance(check_plan(ground_plan(plan, text, 'turn', vocab), vocab, text), CheckedPlan)


def test_model_abstention_text_cannot_fake_adapter_unavailability(monkeypatch, tmp_path):
    from kge.services.ask.engine.planmodel import read
    from tests.unit.test_planner_jobs import jobs, setup

    class Declines:
        def json(self, prompt):
            return dict(kind='abstain', spans=[], missing=['Missing plan recording: model-authored text'])

    vocab = vocabulary()
    failed = []
    reply = read(Declines(), 'Metric', vocab, on_failure=lambda: failed.append(True))
    assert failed == []
    rules = Propose(kind='propose', base_turn=None, plan=grounded(model(), 'Metric', vocab))
    fake, warehouses = setup(monkeypatch, tmp_path, False)
    try:
        answer = Planner(vocab, warehouses, Hidden([], set(), [])).answer('Metric', reply, grounding=True, fallback=rules)
        assert answer['kind'] == 'result'
        assert answer['result']['rows'][0]['cells'][0]['value']['value'] == '15.0'
        assert answer['model_refusal']['fallback'] == 'rules'
        assert jobs(fake) == (1, 1)
    finally:
        fake.con.close()
