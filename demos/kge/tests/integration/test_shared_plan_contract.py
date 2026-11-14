"""Agent and Assist use Ask's checked plan, result and untrusted history."""
import copy
import json

import pytest

from kge.semantics.plan import HistoryEntry
from kge.semantics.planhistory import semantic
from kge.services.ask.engine.agent import Agent
from kge.services.ask.engine.combined import Combined
from kge.services.ask.engine.present import present
from tests.integration.test_answer_text import client as client, mcp_wire
from tests.integration.test_combined import Stub


@pytest.mark.parametrize('surface', ['agent', 'assist'])
@pytest.mark.parametrize('question', ['Retail revenue for June 2025', 'What is Revenue?',
    'Retail revenue for June 2025 excluding unrecognized', 'What is the email address of customer 118?'])
def test_every_outcome_returns_history(client, surface, question):
    response = client.post('/' + surface, json={'question': question},
                           headers=client.as_('analyst_enterprise.meridian@example.com'))
    assert response.status_code == 200, response.text
    assert isinstance(response.json()['history'], list)


@pytest.mark.parametrize('surface', ['agent', 'assist'])
def test_http_history_is_checked_and_bound_to_purpose(client, surface):
    who = 'analyst_retail.meridian@example.com'
    def ask(body):
        response = client.post('/' + surface, json=body, headers=client.as_(who))
        assert response.status_code == 200, response.text
        return response.json()
    body = {'question': 'Retail revenue for June 2025', 'products': ['retail.monthly_revenue']}
    first = ask(body)
    second = ask({**body, 'history': first['history']})
    assert first['figure'] == second['figure'] == 13287.67
    assert len(second['history']) == 2
    assert first['history'][0] == second['history'][0]
    denied = ask({**body, 'history': first['history'], 'purpose': 'another purpose'})
    assert denied['behaviour'] == 'abstain' and not denied.get('result')
    forged = copy.deepcopy(first['history'])
    forged[0]['binding']['access_fingerprint'] = 'forged'
    denied = ask({**body, 'history': forged})
    assert denied['behaviour'] == 'abstain' and not denied.get('result')


def test_assist_mcp_preserves_the_http_history_contract(client):
    who = 'analyst_retail.meridian@example.com'
    body = {'question': 'Retail revenue for June 2025'}
    first = client.post('/assist', json=body, headers=client.as_(who)).json()
    reply = mcp_wire(client, '/mcp/assist', who, 'assist', {**body, 'history': first['history']})
    assert not reply.get('isError'), reply
    out = reply['structuredContent']
    assert out['figure'] == first['figure'] and len(out['history']) == 2


def test_agent_treatment_uses_the_public_model_contract(eng):
    question = 'Retail revenue for June 2025'
    public = Combined(eng, llm=Stub(eng, question=question)).ask(question, 'analyst_enterprise')
    model = Stub(eng, question=question)
    answer = Agent(eng, llm=model).ask(question, 'analyst_enterprise')
    assert answer['figure'] == public['figure']
    assert answer['result'] == public['result']
    assert semantic(answer['history'][-1]['plan']) == semantic(public['history'][-1]['plan'])
    assert len(model.prompts) == 1 and model.prompts[0].startswith('query-plan-v1\n')
    assert answer['agent']['planner'] == 'query-plan-v1'


@pytest.mark.parametrize('surface', ['agent', 'assist'])
def test_clarification_choices_resume_the_same_checked_plan(client, monkeypatch, surface):
    import kge.app as app
    from kge.semantics.query import Context
    engine = app.engine()
    for identifier, label in [('choice_a', 'Operating receipts'), ('choice_b', 'Other receipts')]:
        monkeypatch.setitem(engine.semantics.contexts, identifier,
            Context(identifier, label, '', 'Revenue', 'retail.revenue', 'sum(monthly_revenue.total_revenue_usd)',
                    'sum', 'month', 'retail.monthly_revenue', ('retail.monthly_revenue.total_revenue_usd',), (), (), ()))
    monkeypatch.setattr(engine.semantics, 'plan_snapshot', None, raising=False)
    who = 'analyst_enterprise.meridian@example.com'
    first = client.post('/' + surface, json={'question': 'Retail revenue for June 2025'}, headers=client.as_(who)).json()
    assert first['behaviour'] == 'clarify' and not first.get('result'), first
    assert {'choice_a', 'choice_b'} <= {c['id'] for c in first['clarification']['choices']}
    second = client.post('/' + surface, json={'question': 'Retail Operating receipts for June 2025',
        'history': first['history']}, headers=client.as_(who)).json()
    assert second['figure'] == 13287.67 and second['history'][-1]['plan']['measures'][0]['context']['id'] == 'choice_a'


@pytest.mark.parametrize('behaviour', ['figure', 'combined'])
@pytest.mark.parametrize('text', [None, 'A checked result.'])
def test_display_preserves_general_result_dimensions_and_cell_evidence(eng, behaviour, text):
    result = {'columns': [{'id': 'region', 'kind': 'dimension', 'currency': None},
                         {'id': 'month', 'kind': 'dimension', 'currency': None},
                         {'id': 'ratio', 'kind': 'derived', 'currency': None}],
              'rows': [{'panel': None, 'dimensions': [{'kind': 'text', 'value': 'North'}, {'kind': 'text', 'value': '2025-06'}],
                        'cells': [{'value': {'kind': 'null'}, 'citations': ['pack:left', 'pack:right'],
                                   'null_reasons': ['missing denominator']}]}]}
    display = present({'behaviour': behaviour, 'figures': [], 'result': result, 'answer_text': text}, eng)
    assert 'error' not in display
    assert display['headline']['text'] == (text or 'The checked result is in the table.')
    assert [c['key'] for c in display['table']['columns']] == ['region', 'month', 'ratio', '__sources__', '__notes__']
    assert display['table']['rows'] == [['North', '2025-06', None, 'ratio: pack:left, pack:right', 'ratio: missing denominator']]
    assert display['table']['cell_citations'] == [[['pack:left', 'pack:right']]]
    assert display['table']['null_reasons'] == [[['missing denominator']]]
    assert display['chart'] is None and display['table']['total'] is None


def test_agent_unused_forged_history_stops_model_disclosure(eng):
    question = 'Retail revenue for June 2025'
    first = Agent(eng, llm=None).ask(question, 'analyst_enterprise')
    history = copy.deepcopy(first['history'])
    history[0]['binding']['release_identity'] = 'forged'
    model = Stub(eng, question=question)
    out = Agent(eng, llm=model).ask(question, 'analyst_enterprise',
                                  history=[HistoryEntry.model_validate(h) for h in history])
    assert out['behaviour'] == 'abstain' and not out.get('result') and model.prompts == []


@pytest.mark.parametrize('sql', [
    'SELECT 99999',
    'SELECT sum(total_revenue_usd) * 2 FROM monthly_revenue',
    'SELECT count(email) FROM customer',
    'SELECT count(*) FROM complaint JOIN monthly_revenue ON complaint.customer_id = monthly_revenue.account_id',
    'SELECT count(*) FROM retail.customer JOIN wealth.account ON customer.customer_id = account.customer_id',
])
def test_agent_rejects_model_sql_before_any_warehouse_execution(eng, monkeypatch, sql):
    def forbidden(*args, **kwargs):
        pytest.fail('Unvalidated model SQL reached the warehouse.')
    monkeypatch.setattr(eng.w, 'cursor', forbidden)
    model = Stub(eng, proposal={'behaviour': 'figure', 'sql': {'retail': sql}, 'citations': [99]})
    answer = Agent(eng, llm=model).ask('Retail revenue for June 2025', 'analyst_enterprise')
    assert answer['behaviour'] == 'abstain' and not answer.get('result') and not answer.get('sql')
    assert len(model.prompts) == 2
    assert json.loads(model.prompts[1].split('\n', 1)[1])['errors']


def test_agent_schema_retry_accepts_only_a_whole_checked_plan(eng):
    question = 'Retail revenue for June 2025'
    class Retry(Stub):
        def json(self, prompt, max_tokens=800):
            if not self.prompts:
                self.prompts.append(prompt)
                return {'sql': 'SELECT 99999'}
            return super().json(prompt, max_tokens)
    model = Retry(eng, question=question)
    answer = Agent(eng, llm=model).ask(question, 'analyst_enterprise')
    assert answer['figure'] == 13287.67 and len(model.prompts) == 2
    assert answer['sql_source'] == 'query-plan-v1' and '99999' not in answer['sql']


def test_agent_model_control_does_not_receive_governed_vocabulary(eng):
    package = Agent(eng, llm=None).context('Retail revenue for June 2025', 'analyst_retail', 'analysis', 'control',
                                         plan_vocabulary=True)
    contract = package['plan_vocabulary']
    assert contract['contexts'] and set(contract['calendars']) == {'schema_calendar'}
    assert all(c['id'].startswith('schema:') and not c['definition'] for c in contract['contexts'])
    assert not any(e['id'].startswith('wealth.') for e in contract['names'])
    assert not {'.'.join(column) for column in eng.RESTRICTED_COLUMNS} & {e['id'] for e in contract['names']}
    assert all(not c['paths'] for c in contract['contexts'])


@pytest.mark.parametrize('surface', ['agent', 'assist'])
def test_request_history_is_strictly_bounded_on_both_surfaces(client, surface):
    response = client.post('/' + surface, json={'question': 'Retail revenue', 'history': [{}] * 9},
                           headers=client.as_('analyst_retail.meridian@example.com'))
    assert response.status_code == 422


def test_assist_keeps_explicit_metadata_routing_after_a_numeric_turn(client):
    who = 'analyst_retail.meridian@example.com'
    first = client.post('/assist', json={'question': 'Retail revenue for June 2025'}, headers=client.as_(who)).json()
    response = client.post('/assist', json={'question': 'Who owns the Monthly Revenue table?',
                           'history': first['history']}, headers=client.as_(who)).json()
    assert response['source'] == 'catalogue' and response['behaviour'] == 'catalogue'
    assert response['figure'] is None and response['history'] == []



@pytest.mark.parametrize('modeled', [False, True])
@pytest.mark.parametrize('question', [
    'current vs certified NNA by month',
    'the stock-plan share of client-directed NNA',
    'NNA and AUM by product code with counts',
    'gross inflows and outflows',
    'NNA inflows, outflows and a ratio with a household count',
    'compare NNA to plan',
    'Retail customer deposits and Wealth AUM share of total for June 2025',
    'NNA for Retail clients, with household counts and current vs certified values',
])
def test_agent_a1_and_cross_unit_near_misses_never_return_partial_figures(eng, question, modeled):
    answer = Agent(eng, llm=Stub(eng, question=question) if modeled else None).ask(question, 'analyst_enterprise')
    if question == 'current vs certified NNA by month':
        from tests.integration.test_rules_shapes import assert_public_current_certified_monthly
        assert_public_current_certified_monthly(eng, answer)
        return
    assert answer['behaviour'] in ('abstain', 'clarify', 'refuse'), answer
    assert not answer.get('figure') and not answer.get('result') and not answer.get('sql_run')
    assert not answer.get('citations') and not answer.get('figures')
    assert answer['history'] is not None


@pytest.mark.parametrize('surface,transport', [('agent', 'http'), ('assist', 'http'), ('assist', 'mcp'), ('ask', 'http'), ('ask', 'mcp')])
def test_unreadable_page_cohort_refuses_before_clarification_or_execution(client, surface, transport):
    from tests.integration.test_combined import Q
    who = 'analyst_retail.meridian@example.com'
    body = {'question': Q.replace('Retail', 'SME'), 'products': ['sme.monthly_client_revenue']}
    if transport == 'http':
        response = client.post('/' + surface, json=body, headers=client.as_(who))
        assert response.status_code == 200, response.text
        answer = response.json()
    else:
        reply = mcp_wire(client, '/mcp/assist' if surface == 'assist' else '/mcp/answer', who, surface, body)
        assert not reply.get('isError'), reply
        answer = reply['structuredContent']
    assert answer['behaviour'] == 'refuse' and answer['denied_warehouses'] == ['sme']
    assert not answer.get('result') and not answer.get('sql') and not answer.get('clarification')


@pytest.mark.parametrize('modeled', [False, True])
def test_agent_cohort_matches_the_complete_public_plan_and_citations(eng, modeled):
    from tests.integration.test_combined import Q
    public = Combined(eng, llm=None).ask(Q, 'analyst_enterprise')
    agent = Agent(eng, llm=Stub(eng, question=Q) if modeled else None).ask(Q, 'analyst_enterprise')
    assert agent['behaviour'] == public['behaviour'] == 'combined'
    assert [f['value'] for f in agent['figures']] == [176, 3, 6400.62, 42.64]
    assert agent['result'] == public['result']
    assert agent['citations'] == public['citations']
    assert semantic(agent['history'][-1]['plan']) == semantic(public['history'][-1]['plan'])
    assert agent['phrased_by'] == 'template'



def test_assist_numeric_history_delegates_without_a_second_router_call(client, monkeypatch):
    from kge.services.assist import service
    who = 'analyst_retail.meridian@example.com'
    body = {'question': 'Retail revenue for June 2025'}
    first = client.post('/assist', json=body, headers=client.as_(who)).json()
    monkeypatch.setattr(service, 'routing_model', lambda c: object())
    def forbidden(*args):
        pytest.fail('A checked numeric conversation was sent to a second router.')
    monkeypatch.setattr(service, 'route_question', forbidden)
    response = client.post('/assist', json={**body, 'history': first['history']}, headers=client.as_(who))
    assert response.status_code == 200, response.text
    answer = response.json()
    assert answer['source'] == 'ask' and answer['figure'] == 13287.67 and len(answer['history']) == 2



@pytest.mark.parametrize('contract', [None, {}, {'names': None}, {'names': ['untyped']}, {'names': [{'id': None}]}])
def test_agent_refuses_a_missing_or_malformed_opt_in_vocabulary_before_model_disclosure(eng, monkeypatch, contract):
    from kge.services.ask.engine.context import ContextError
    model = Stub(eng, question='Retail revenue for June 2025')
    agent = Agent(eng, llm=model)
    monkeypatch.setattr(agent, 'fetch', lambda *args, **kwargs: {'plan_vocabulary': contract})
    with pytest.raises(ContextError, match='supplied no plan vocabulary'):
        agent.ask('Retail revenue for June 2025', 'analyst_enterprise', arm='control')
    assert model.prompts == []
