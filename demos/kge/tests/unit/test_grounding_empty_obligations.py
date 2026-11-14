from copy import deepcopy

import pytest

from kge.semantics.coverage import ground_plan, ledger
from kge.semantics.plancheck import CheckedPlan, check_plan
from kge.services.ask.engine.planmodel import read
from tests.plan_fixtures import grounded, model, name, vocabulary


class Recorded:
    def __init__(self, reply):
        self.reply = reply
        self.calls = 0

    def json(self, prompt):
        self.calls += 1
        return deepcopy(self.reply)


def reply(plan):
    data = plan.model_dump(mode='json', by_alias=True)
    for obligation in data['obligations']:
        obligation['spans'] = []
    return dict(kind='propose', base_turn=None, plan=data)


def test_empty_obligation_spans_reach_grounding_without_a_model_retry():
    vocab = vocabulary()
    recorded = Recorded(reply(grounded(model(), 'Metric', vocab)))
    proposal = read(recorded, 'Metric', vocab, grounding=True)
    assert recorded.calls == 1
    assert proposal.kind == 'propose'
    assert proposal.plan.obligations == ()
    assert not isinstance(check_plan(proposal.plan, vocab, 'Metric'), CheckedPlan)
    checked = ground_plan(proposal.plan, 'Metric', 'turn', vocab)
    assert isinstance(check_plan(checked, vocab, 'Metric'), CheckedPlan)
    assert checked.obligations[0].spans == (ledger('Metric', 'turn', vocab)[0].span,)


@pytest.mark.parametrize('mistake', ['measure', 'period', 'predicate', 'limit'])
def test_empty_citations_cannot_cover_unsupported_selections(mistake):
    vocab = vocabulary()
    raw = reply(grounded(model(), 'Metric', vocab))
    if mistake == 'measure':
        raw['plan']['measures'][0]['measure']['id'] = 'unknown'
    elif mistake == 'period':
        raw['plan']['time'] = dict(kind='single', period=dict(kind='month', value='2025-03', calendar=name('cal')))
    elif mistake == 'predicate':
        raw['plan']['filters'] = [dict(kind='compare', id='invented', left=dict(kind='column',
            name=name('u.events.value'), path=[]), op='eq', value=dict(kind='integer', value=1), evidence=[])]
    else:
        raw['plan']['limit'] = 1
    target = {'measure': 'm', 'period': 'time', 'predicate': 'invented', 'limit': 'limit'}[mistake]
    raw['plan']['obligations'].append(dict(id='unsupported', spans=[], kind='name', targets=[target], state='applied'))
    recorded = Recorded(raw)
    proposal = read(recorded, 'Metric', vocab, grounding=True)
    assert recorded.calls == 1
    assert proposal.kind == 'propose'
    assert not isinstance(check_plan(proposal.plan, vocab, 'Metric'), CheckedPlan)
    assert not isinstance(check_plan(ground_plan(proposal.plan, 'Metric', 'turn', vocab), vocab, 'Metric'), CheckedPlan)
