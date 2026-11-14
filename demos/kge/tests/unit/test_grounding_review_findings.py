from copy import deepcopy

import pytest

from kge.semantics.plan import HistoryEntry, Propose, QueryPlan
from kge.services.ask.engine.planmodel import read
from kge.services.ask.engine.planner import Planner
from kge.services.ask.engine.wording import Hidden
from tests.plan_fixtures import grounded, model, name, vocabulary
from tests.unit.test_grounding_empty_obligations import Recorded
from tests.unit.test_planner_jobs import jobs, setup


@pytest.mark.parametrize('grounding', [False, True], ids=['rules', 'grounded'])
def test_metric_filter_deletion_followup_answers_fifteen_with_one_job(monkeypatch, tmp_path, grounding):
    fake, warehouses = setup(monkeypatch, tmp_path, False)
    vocab = vocabulary()
    question, followup = 'Metric value above 6', 'without the filter'
    base = grounded(model(filters=[dict(kind='compare', id='f', left=dict(kind='column',
        name=name('u.events.value'), path=[]), op='gt', value=dict(kind='integer', value=6), evidence=[])]),
        question, vocab, {'value': 'f', 'above': 'f', '6': 'f'})
    planner = Planner(vocab, warehouses, Hidden([], set(), []))
    try:
        first = planner.answer(question, Propose(kind='propose', base_turn=None, plan=base), grounding=grounding)
        assert first['kind'] == 'result', first
        assert first['result']['rows'][0]['cells'][0]['value']['value'] == '10.0'
        assert jobs(fake) == (1, 1)
        history = [HistoryEntry(turn_id='turn', question=question, plan=QueryPlan.model_validate(first['plan']),
                               clarification=None, binding=vocab.binding)]
        data = deepcopy(first['plan'])
        data['filters'] = []
        data['obligations'] = [o for o in data['obligations'] if 'f' not in o['targets']]
        data['obligations'].append(dict(id='remove', kind='name', targets=['f'], state='removed',
            spans=[dict(turn='next', start=0, end=len(followup))]))
        fake.requests.clear()
        answer = planner.answer(followup, Propose(kind='propose', base_turn='turn', plan=QueryPlan.model_validate(data)),
                                turn='next', history=history, grounding=grounding)
        assert answer['kind'] == 'result', answer
        assert answer['result']['rows'][0]['cells'][0]['value']['value'] == '15.0'
        assert jobs(fake) == (1, 1)
        assert [o['state'] for o in answer['plan']['obligations']] == ['applied', 'removed']
    finally:
        fake.con.close()


def test_arm_a_empty_span_obligation_keeps_the_original_validation_failure():
    vocab = vocabulary()
    data = grounded(model(), 'Metric', vocab).model_dump(mode='json', by_alias=True)
    data['obligations'].append(dict(id='empty', spans=[], kind='name', targets=['m'], state='applied'))
    raw = dict(kind='propose', base_turn=None, plan=data)
    baseline = Recorded(raw)
    reply = read(baseline, 'Metric', vocab, grounding=False)
    assert baseline.calls == 2
    assert reply.kind == 'abstain'
    assert reply.missing == ('The configured model could not provide a valid query plan.',)
    assert raw['plan']['obligations'][-1]['spans'] == []
    grounded_model = Recorded(raw)
    grounded_reply = read(grounded_model, 'Metric', vocab, grounding=True)
    assert grounded_model.calls == 1
    assert grounded_reply.kind == 'propose'
    assert grounded_reply.plan.obligations == grounded(model(), 'Metric', vocab).obligations


@pytest.mark.parametrize('followup,target,span_end', [
    ('without the filter and sparkly', 'f', len('without the filter')),
    ('without the filter and sparkly', 'f', len('without the filter and sparkly')),
    ('without the filter', 'time', len('without the filter')),
    ('drop Metric', 'f', len('drop Metric')),
])
def test_grounded_removal_cannot_hide_unsupported_words_or_an_unrelated_target(monkeypatch, tmp_path, followup, target, span_end):
    fake, warehouses = setup(monkeypatch, tmp_path, False)
    vocab = vocabulary()
    question = 'Metric value above 6'
    base = grounded(model(filters=[dict(kind='compare', id='f', left=dict(kind='column',
        name=name('u.events.value'), path=[]), op='gt', value=dict(kind='integer', value=6), evidence=[])]),
        question, vocab, {'value': 'f', 'above': 'f', '6': 'f'})
    history = [HistoryEntry(turn_id='turn', question=question, plan=base, clarification=None, binding=vocab.binding)]
    data = base.model_dump(mode='json', by_alias=True)
    data['filters'] = []
    data['obligations'] = [o for o in data['obligations'] if 'f' not in o['targets']]
    data['obligations'].append(dict(id='remove', kind='name', targets=[target], state='removed',
        spans=[dict(turn='next', start=0, end=span_end)]))
    try:
        answer = Planner(vocab, warehouses, Hidden([], set(), [])).answer(followup,
            Propose(kind='propose', base_turn='turn', plan=QueryPlan.model_validate(data)),
            turn='next', history=history, grounding=True)
        assert answer['kind'] == 'abstain', answer
        assert not answer.get('result')
        assert jobs(fake) == (0, 0)
    finally:
        fake.con.close()
