from copy import deepcopy
from decimal import Decimal

import pytest

from kge.semantics.plan import QueryPlan
from kge.services.ask.engine.combined import Combined
from kge.services.ask.engine.core import PRESETS
from tests.integration.test_ask_gates import comparison_engine as comparison_engine
from tests.integration.test_context_scope import vocabulary
from tests.integration.test_grounding_plans import QUESTION, Recorded, certified, citation_variant, model_reply
from tests.integration.test_planner_request_jobs import budgets


@pytest.mark.parametrize('proposal', ['split', 'refused'])
def test_public_model_sql_mode_grounds_citations_and_keeps_the_rules_floor(comparison_engine, proposal):
    eng, fake = comparison_engine
    config = PRESETS['graph'].with_stages(sql_source='model', renderer='template')
    rules = Combined(eng, llm=None)
    rules.ask(QUESTION, 'analyst_enterprise')
    if fake:
        fake.requests.clear()
    expected = rules.ask(QUESTION, 'analyst_enterprise')
    expected_jobs = budgets(fake) if fake else None
    plan = QueryPlan.model_validate(expected['history'][-1]['plan'])
    reply = model_reply(citation_variant(plan, 'split_names')).model_dump(mode='json', by_alias=True)
    if proposal == 'refused':
        reply = dict(kind='abstain', spans=[], missing=['The recorded model declined.'])
    if fake:
        fake.requests.clear()
    answer = Combined(eng, llm=Recorded(reply)).ask(QUESTION, 'analyst_enterprise', config=config)
    assert answer['behaviour'] == 'figure', answer
    assert Decimal(str(answer['figure'])) == Decimal('5637481.28')
    assert answer['result'] == expected['result']
    refusals = [step for step in answer['trace'] if step['step'] == 'model_plan_refused']
    if proposal == 'refused':
        assert len(refusals) == 1
        assert refusals[0]['fallback'] == 'rules'
        assert refusals[0]['reason'] == 'The recorded model declined.'
    else:
        assert refusals == []
        assert answer['graph_path'] == expected['graph_path']
    if fake:
        assert budgets(fake) == expected_jobs
        assert expected_jobs == dict(graph=0, retrieval=0, warehouse=2, dry=2, catalogue=0)


def test_public_arm_a_preserves_the_empty_obligation_decline(eng):
    reply = model_reply(certified(vocabulary(eng, QUESTION))).model_dump(mode='json', by_alias=True)
    reply['plan']['obligations'].append(dict(id='empty', spans=[], kind='name', targets=['m'], state='applied'))
    caller = Combined(eng, llm=Recorded(deepcopy(reply)))
    caller.plan_grounding = False
    config = PRESETS['graph'].with_stages(sql_source='planner+model', renderer='template')
    answer = caller.ask(QUESTION, 'analyst_enterprise', config=config)
    assert answer['behaviour'] == 'abstain'
    assert answer['reason'] == 'The configured model could not provide a valid query plan. No figure is given.'
    assert not answer.get('figure') and not answer.get('result')
    assert not any(step['step'] == 'model_plan_refused' for step in answer['trace'])
    caller.plan_grounding = True
    grounded = caller.ask(QUESTION, 'analyst_enterprise', config=config)
    assert grounded['behaviour'] == 'figure', grounded
    assert Decimal(str(grounded['figure'])) == Decimal('5637481.28')
