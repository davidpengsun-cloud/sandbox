import copy
import json

import pytest

from kge.services.ask.engine import planmodel, planrules
from kge.services.ask.engine.combined import Combined
from kge.services.ask.engine.core import parse
from kge.services.ask.engine.scope import ProductScope
from tests.integration.test_answer_text import client as client
from tests.plan_recordings import recorded


@pytest.mark.parametrize('question', [
    'current vs certified NNA by month',
    'the stock-plan share of client-directed NNA',
    'NNA and AUM by product code with counts',
    'gross inflows and outflows',
    'NNA inflows, outflows and a ratio with a household count',
    'compare NNA to plan',
])
def test_http_a1_none_never_returns_a_partial_figure(client, question):
    response = client.post('/ask', json={'question': question}, headers=client.as_('analyst_enterprise.meridian@example.com'))
    assert response.status_code == 200, response.text
    answer = response.json()
    if question == 'current vs certified NNA by month':
        import kge.app as app
        from tests.integration.test_rules_shapes import assert_public_current_certified_monthly
        assert_public_current_certified_monthly(app.engine(), answer)
        return
    assert answer['behaviour'] in ('abstain', 'clarify', 'refuse'), answer
    assert not answer.get('figure') and not answer.get('result') and not answer.get('sql_run')


def test_numeric_surfaces_do_not_read_legacy_context_classifications(eng, monkeypatch):
    from kge.services.ask.engine.agent import Agent
    from kge.services.ask.engine import core, planpublic, agent, question
    def forbidden(*args, **kwargs):
        pytest.fail('Legacy Context classification read on a numeric path')
    for module in (core, planpublic, agent, question):
        if hasattr(module, 'parse_context'):
            monkeypatch.setattr(module, 'parse_context', forbidden)
    for call in (eng.answer, Combined(eng, llm=None).ask, Agent(eng, llm=None).ask):
        out = call('Retail revenue for June 2025', 'analyst_enterprise')
        assert out['figure'] == 13287.67
        count = call('How many clients hold both retail deposits and advisory AUM at 31 Mar 2025?', 'analyst_enterprise')
        assert count['behaviour'] == 'abstain' and not count.get('figure')


@pytest.mark.parametrize('slot,base,followup', [
    ('dimensions', 'NNA for June 2025 by region', 'drop the region breakdown'),
    ('filters', 'NNA for June 2025 from BROKERAGE', 'without the filter'),
    ('measures', 'Retail revenue and fee income for June 2025', 'drop fee income'),
])
def test_http_model_followup_can_evidence_deletion(client, monkeypatch, slot, base, followup):
    import kge.app as app
    engine = app.engine()
    headers = client.as_('analyst_enterprise.meridian@example.com')
    first = client.post('/ask', json={'question': base}, headers=headers).json()
    assert first.get('result'), first
    history = copy.deepcopy(first['history'])
    plan = copy.deepcopy(history[-1]['plan'])
    removed = ([m for m in plan[slot] if m['context']['id'].endswith('fee-income')] if slot == 'measures' else plan[slot][-1:])
    if slot == 'measures' and not removed:
        removed = [m for m in plan[slot] if 'fee' in m['measure']['id'].casefold()]
    assert removed
    plan[slot] = [node for node in plan[slot] if node not in removed] if slot == 'measures' else []
    removed_ids = {n['id'] for n in removed}
    if slot == 'measures':
        removed_ids.update(n['id'] + '/mode' for n in removed)
    for obligation in plan['obligations']:
        obligation['targets'] = [t for t in obligation['targets'] if t not in removed_ids]
    plan['obligations'] = [o for o in plan['obligations'] if o['targets']]
    plan['obligations'].append({'id': 'deletion', 'spans': [{'turn': 'turn', 'start': 0, 'end': len(followup)}],
        'kind': 'name', 'targets': sorted(removed_ids), 'state': 'removed'})
    reply = {'kind': 'propose', 'base_turn': history[-1]['turn_id'], 'plan': plan}
    class Replay:
        name = 'mock:deletion'
        def json(self, prompt):
            payload = json.loads(prompt.split('\n', 1)[1])
            value = copy.deepcopy(reply)
            value['plan']['obligations'][-1]['spans'][0]['turn'] = payload['turn_id']
            return value
    monkeypatch.setattr(app, '_combined', Combined(engine, llm=Replay()))
    response = client.post('/ask', json={'question': followup, 'history': history}, headers=headers)
    assert response.status_code == 200, response.text
    out = response.json()
    assert out.get('result'), out.get('reason', out)
    assert len(out['history'][-1]['plan'][slot]) == len(plan[slot])
    monkeypatch.setattr(app, '_combined', Combined(engine, llm=None))
    again = client.post('/ask', json={'question': 'Retail revenue for June 2025', 'history': out['history']}, headers=headers)
    assert again.status_code == 200
    assert again.json().get('result'), again.json()
    returned = client.post('/ask', json={'question': 'NNA for June 2025', 'history': again.json()['history']}, headers=headers)
    assert returned.status_code == 200 and returned.json().get('result'), returned.text


@pytest.mark.parametrize('shape', ['B4', 'B6', 'B7'])
def test_meridian_capabilities_execute_through_mock_recordings(eng, shape):
    import hashlib
    from kge.semantics.plan import QueryPlan
    from tests.plan_fixtures import grounded, name
    cfg = Combined(eng, llm=recorded({})).default_config()
    measure = {'kind': 'measure', 'id': 'm', 'measure': name('retail.revenue'), 'context': name('retail_revenue'), 'mode': 'current', 'filters': []}
    other = {'kind': 'measure', 'id': 'n', 'measure': name('retail.fee-income'), 'context': name('retail_fees'), 'mode': 'current', 'filters': []}
    data = {'schema': 'query-plan/v1', 'layout': 'aligned', 'measures': [measure, other], 'dimensions': [], 'filters': [],
        'time': {'kind': 'single', 'period': {'kind': 'month', 'value': '2025-06', 'calendar': name('calendar')}},
        'comparisons': [], 'derived': [], 'order': [], 'limit': None, 'obligations': []}
    text = 'Retail revenue and fee income for June 2025'
    assignments = {'Retail': ['m', 'n'], 'revenue': 'm', 'fee income': 'n', 'June 2025': 'time'}
    if shape == 'B4':
        data['dimensions'] = [{'kind': 'time', 'id': 't', 'calendar': name('calendar'), 'grain': 'month'}]
        text += ' by month'
        assignments.update({'by': 't', 'month': 't'})
    elif shape == 'B6':
        data['measures'] = [measure, {'kind': 'distinct_count', 'id': 'n', 'entity': name('retail_customer'), 'context': name('retail_revenue'), 'mode': 'current', 'filters': []}]
        text = 'Retail revenue and count Retail customer for June 2025'
        assignments.update({'count': 'n', 'Retail customer': 'n'})
    else:
        data['derived'] = [{'kind': 'ratio', 'id': 'r', 'numerator': 'm', 'denominator': 'n', 'evidence': []}]
        text = 'Retail ratio of revenue to fee income for June 2025'
        assignments['ratio'] = 'r'
    req = parse(text, eng.term_names, eng)
    vocab = planrules.vocabulary(eng, 'analyst_enterprise', 'analysis', ProductScope(eng, None), cfg, req)
    plan = grounded(QueryPlan.model_validate(data), text, vocab, assignments).model_dump(mode='json', by_alias=True)
    turn = hashlib.sha256(json.dumps([text, cfg.label, vocab.binding.model_dump(mode='json'), []], sort_keys=True).encode()).hexdigest()[:32]
    for obligation in plan['obligations']:
        for span in obligation['spans']:
            span['turn'] = turn
    key = planmodel.payload(text, vocab)['recording_key']
    llm = recorded({key: {'kind': 'propose', 'base_turn': None, 'plan': plan}})
    answer = Combined(eng, llm=llm).ask(text, 'analyst_enterprise', config=cfg)
    assert answer.get('result'), answer.get('reason', answer)
    assert answer['versions']['interpretation'].startswith('model:')
    cells = answer['result']['rows'][0]['cells']
    assert float(cells[0]['value']['value']) == pytest.approx(13287.67)
    if shape == 'B6':
        assert cells[1]['value']['kind'] == 'integer' and cells[1]['value']['value'] > 0
    if shape == 'B7':
        assert float(cells[2]['value']['value']) == pytest.approx(float(cells[0]['value']['value']) / float(cells[1]['value']['value']))
