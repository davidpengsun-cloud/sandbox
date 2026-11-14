import hashlib
import json

import pytest

from kge.services.ask.engine.core import parse
from tests.integration.test_answer_text import client as client, mcp_wire
from tests.scratch import DEMO


def same_result(actual, expected):
    assert actual and expected
    assert [column.get('display_order') for column in actual['columns']] == [None, 17.0, 17.0, 17.0, 17.0]
    assert [column.get('display_order') for column in expected['columns']] == [None, 0.0, 0.0, 29.0, 29.0]
    def columns(result):
        return [{key: value for key, value in column.items() if key != 'display_order'} for column in result['columns']]
    assert columns(actual) == columns(expected)
    assert {key: value for key, value in actual.items() if key != 'columns'} == {key: value for key, value in expected.items() if key != 'columns'}


@pytest.mark.parametrize('surface', ['ask', 'agent'])
@pytest.mark.parametrize('question', [
    'Average ledger balance across retail accounts in June 2025',
    'Number of retail accounts in June 2025',
    'Retail fees for June 2025',
    'Total retail revenue for June 2025 in region NW',
    'Retail revenue for June 2025 by region',
])
def test_one_unit_reader_keeps_own_meaning_over_hidden_labels(client, surface, question):
    def ask(who):
        response = client.post('/' + surface, json={'question': question}, headers=client.as_(who + '.meridian@example.com'))
        assert response.status_code == 200, response.text
        return response.json()
    expected = ask('analyst_enterprise')
    actual = ask('analyst_retail')
    assert expected['behaviour'] not in ('refuse', 'blocked_access'), expected
    assert actual['behaviour'] == expected['behaviour'] and actual.get('figure') == expected.get('figure'), actual
    assert actual.get('rows') == expected.get('rows') and actual.get('result') == expected.get('result'), actual


@pytest.mark.parametrize('surface', ['ask', 'agent', 'answer'])
@pytest.mark.parametrize('question', [
    'SME revenue for June 2025 on facilities older than 5 years',
    'Retail revenue for June 2025 by age band',
])
def test_policy_phrase_in_another_unit_sense_abstains_over_http(client, surface, question):
    response = client.post('/' + surface, json={'question': question},
        headers=client.as_('analyst_enterprise.meridian@example.com'))
    assert response.status_code == 200, response.text
    answer = response.json()
    assert answer['behaviour'] == 'abstain' and not answer.get('result') and not answer.get('figure'), answer


@pytest.mark.parametrize('surface', ['ask', 'agent', 'assist'])
def test_http_cohort_followup_rebuilds_checked_contexts(client, surface):
    question = 'How many active customers in Retail in June 2025 raised a complaint that month, and what were their average balance and fee income?'
    def ask(text, **extra):
        response = client.post('/' + surface, json={'question': text, **extra},
            headers=client.as_('analyst_retail.meridian@example.com'))
        assert response.status_code == 200, response.text
        return response.json()
    first = ask(question)
    assert first.get('result') and first['history'], first
    answer = ask('And for May 2025?', history=first['history'])
    expected = ask(question.replace('June', 'May'))
    same_result(answer.get('result'), expected.get('result'))
    assert answer.get('figures') == expected.get('figures'), answer
    assert len(answer['history']) == 2 and answer['history'][0] == first['history'][0]
    fresh = ask('Retail fee income for May 2025', history=first['history'])
    assert fresh.get('result') == ask('Retail fee income for May 2025').get('result') and fresh.get('result'), fresh


def test_opt_in_context_history_matches_over_http_and_mcp(client):
    who = 'analyst_retail.meridian@example.com'
    question = 'How many active customers in Retail in June 2025 raised a complaint that month, and what were their average balance and fee income?'
    first = client.post('/ask', json={'question': question}, headers=client.as_(who)).json()
    body = {'question': 'And for May 2025?', 'plan_vocabulary': True, 'history': first['history']}
    response = client.post('/context', json=body, headers=client.as_(who))
    assert response.status_code == 200, response.text
    http = response.json()
    mcp = mcp_wire(client, '/mcp', who, 'get_context', body)['structuredContent']
    assert http['plan_vocabulary'] == mcp['plan_vocabulary']
    identifiers = {c['id'] for c in http['plan_vocabulary']['contexts']}
    assert {m['context']['id'] for m in first['history'][-1]['plan']['measures']} <= identifiers


def test_http_model_agent_forwards_cohort_history_to_its_context_capability(client, monkeypatch):
    import kge.app as appmod
    from kge.semantics.plan import HistoryEntry
    from kge.services.ask.engine import planmodel, planrules
    from kge.services.ask.engine.agent import Agent
    from kge.services.ask.engine.scope import ProductScope
    from tests.plan_recordings import recorded
    who = 'analyst_retail.meridian@example.com'
    question = 'How many active customers in Retail in June 2025 raised a complaint that month, and what were their average balance and fee income?'
    first = client.post('/ask', json={'question': question}, headers=client.as_(who)).json()
    history = [HistoryEntry.model_validate(entry) for entry in first['history']]
    expected = client.post('/ask', json={'question': question.replace('June', 'May')}, headers=client.as_(who)).json()
    engine = appmod.engine()
    cfg = Agent(engine, llm=recorded({})).config('treatment')
    followup = 'And for May 2025?'
    req = parse(followup, engine.term_names, engine)
    vocab = planrules.vocabulary(engine, who, 'analysis', ProductScope(engine, None), cfg, req, history)
    turn = hashlib.sha256(json.dumps([followup, cfg.label, vocab.binding.model_dump(mode='json'),
        [entry.model_dump(mode='json', by_alias=True) for entry in history]], sort_keys=True).encode()).hexdigest()[:32]
    reply = planrules.followup(engine, req, vocab, cfg, history, turn)
    model = recorded({planmodel.payload(followup, vocab, history, turn)['recording_key']: reply.model_dump(mode='json', by_alias=True)})
    monkeypatch.setenv('CONTEXT_URL', '')
    monkeypatch.setattr(appmod, '_agent', Agent(engine, llm=model))
    response = client.post('/agent', json={'question': followup, 'history': first['history']}, headers=client.as_(who))
    assert response.status_code == 200, response.text
    answer = response.json()
    same_result(answer.get('result'), expected['result'])
    assert answer.get('figures') == expected.get('figures'), answer
    assert answer['versions']['interpretation'] == 'model:mock:mock'
    assert len(answer['history']) == 2 and answer['history'][0] == first['history'][0]


@pytest.mark.parametrize('unit,principal', [('retail', 'analyst_retail'), ('sme', 'analyst_sme'),
    ('corporate', 'analyst_corporate'), ('wealth', 'advisor_wealth')])
def test_golden_and_heldout_single_unit_access_matches_enterprise(eng, monkeypatch, unit, principal):
    from kge.services.ask.engine.combined import Combined
    combined = Combined(eng, llm=None)
    if principal not in eng.g.role_ids():
        monkeypatch.setitem(eng.g.role_templates(), principal, {'label': principal, 'reads': [unit], 'processes': ['querying']})
        monkeypatch.setitem(eng.g.rights().reads, principal, frozenset([unit]))
        monkeypatch.setitem(eng.g.rights().processes, principal, frozenset(['querying']))
    compared = 0
    for filename in ('golden', 'heldout'):
        data = json.loads((DEMO / 'clients/meridian/fixture' / (filename + '.json')).read_text())
        for item in data['items'] if isinstance(data, dict) else data:
            question = item['question']
            req = parse(question, eng.term_names, eng)
            expected = combined.ask(question, 'analyst_enterprise')
            actual = combined.ask(question, principal)
            named = set(req.units)
            if not req.units:
                named.update(e['table'].split('.')[0] for key, e in eng.semantics.entities.items() if key == req.counted)
            if named - {unit}:
                assert not actual.get('result') and not actual.get('figure'), (item['id'], actual)
                continue
            if expected.get('result'):
                used = {eng.semantics.contexts[c['context']['id']].unit for h in expected.get('history', []) if h.get('plan')
                    for c in h['plan']['measures'] if c['context']['id'] in eng.semantics.contexts}
                if used and used != {unit}:
                    continue
                if expected.get('unit') and expected['unit'] != unit:
                    continue
                assert actual.get('result') == expected['result'], (item['id'], actual)
                compared += 1
            elif actual['behaviour'] == 'refuse' and expected['behaviour'] != 'refuse':
                terms = {c.unit for c in eng.semantics.contexts.values() if c.term in req.terms_found}
                assert terms and unit not in terms, (item['id'], actual)
    assert compared > 0


@pytest.mark.parametrize('surface', ['ask', 'agent', 'answer'])
def test_real_cross_unit_policy_request_still_refuses_over_http(client, surface):
    response = client.post('/' + surface, json={'question': 'NNA in FY26-Q1 from Retail clients older than 60'},
        headers=client.as_('analyst_enterprise.meridian@example.com'))
    assert response.status_code == 200, response.text
    answer = response.json()
    assert answer['behaviour'] == 'refuse' and not answer.get('result') and not answer.get('figure'), answer


@pytest.mark.parametrize('question,followup', [
    ('Retail revenue for June 2025 in region NW', None),
    ('Retail revenue for June 2025 in US dollars', None),
    ('NNA for June 2025 by region', 'Retail revenue for June 2025'),
    ('How many active customers in Retail in June 2025 raised a complaint that month, and what were their average balance and fee income?',
     'And for May 2025?'),
    ('How many active customers in Retail in June 2025 raised a complaint that month, and what were their average balance and fee income?',
     'Retail fee income for May 2025'),
])
def test_changed_paths_keep_native_bigquery_request_jobs(monkeypatch, tmp_path, question, followup):
    from kge.semantics.plan import HistoryEntry
    from kge.services.ask.engine.combined import Combined
    from tests.integration.test_planner_request_jobs import budgets, pilot
    fake, twin, opened, engines = pilot(monkeypatch, tmp_path)
    try:
        caller = Combined(opened(), llm=None)
        first = caller.ask(question, 'analyst_retail' if question.startswith(('Retail', 'How many')) else 'analyst_enterprise')
        assert first.get('result'), first
        fake.requests.clear()
        baseline_question = (followup if not followup.startswith('And ') else question.replace('June', 'May')) if followup else question
        caller.ask(baseline_question,
            'analyst_retail' if question.startswith(('Retail', 'How many')) else 'analyst_enterprise')
        baseline = budgets(fake)
        for _ in range(2):
            fake.requests.clear()
            answer = caller.ask(followup or question,
                'analyst_retail' if question.startswith(('Retail', 'How many')) else 'analyst_enterprise',
                history=[HistoryEntry.model_validate(h) for h in first['history']] if followup else ())
            assert answer.get('result'), answer
            measured = budgets(fake)
            assert measured['warehouse'] == measured['dry'] == 1, measured
            assert measured['catalogue'] == 0 and measured['graph'] <= baseline['graph'], measured
            assert measured['retrieval'] <= baseline['retrieval'], measured
    finally:
        for engine in engines:
            engine.close()
        if twin.conn is not None:
            twin.conn.close()
            twin.db.close()
        fake.con.close()


@pytest.mark.parametrize('configured', [False, True])
def test_invalid_fractional_year_uses_no_model_or_native_warehouse_job(monkeypatch, tmp_path, configured):
    from tests.integration.test_planner_request_jobs import budgets, pilot
    from kge.services.ask.engine.combined import Combined
    class Uncalled:
        name = 'mock:uncalled'

        def json(self, *args, **kwargs):
            pytest.fail('invalid fractional year called a model')
    fake, twin, opened, engines = pilot(monkeypatch, tmp_path)
    try:
        caller = Combined(opened(), llm=Uncalled() if configured else None)
        measured = []
        for _ in range(2):
            fake.requests.clear()
            answer = caller.ask('Retail revenue for June 2025.5', 'analyst_retail')
            assert answer['behaviour'] == 'abstain' and answer['reason'].startswith('Please give a valid period:'), answer
            assert not answer.get('figure') and not answer.get('sql_run')
            counts = budgets(fake)
            assert {key: value for key, value in counts.items() if key != 'graph'} == dict(
                retrieval=0, warehouse=0, dry=0, catalogue=0), counts
            measured.append(counts['graph'])
        assert measured[1] == 0 and measured[0] <= 2, measured
    finally:
        for engine in engines:
            engine.close()
        if twin.conn is not None:
            twin.conn.close()
            twin.db.close()
        fake.con.close()


def test_preferred_page_cannot_drop_a_possessive_relationship_before_a_native_read(monkeypatch, tmp_path):
    from kge.services.ask.engine.combined import Combined
    from tests.integration.test_planner_request_jobs import budgets, pilot
    fake, twin, opened, engines = pilot(monkeypatch, tmp_path)
    try:
        caller = Combined(opened(), llm=None)
        question = 'How many suitability reviews and what was their revenue for Q1 2025?'
        caller.ask(question, 'analyst_enterprise')
        for _ in range(2):
            fake.requests.clear()
            answer = caller.ask(question, 'analyst_enterprise', products=['wealth.SUIT_RVW_FACT_1'])
            assert answer['behaviour'] == 'abstain', answer
            assert not answer.get('figure') and not answer.get('figures') and not answer.get('sql_run')
            assert budgets(fake) == dict(graph=0, retrieval=0, warehouse=0, dry=0, catalogue=0)
    finally:
        for engine in engines:
            engine.close()
        if twin.conn is not None:
            twin.conn.close()
            twin.db.close()
        fake.con.close()
