"""Public Ask and the baseline share the plan contract without numeric bypasses."""
import copy
import json

import pytest

from kge.pipeline.settings import ROOT
from kge.services.ask.engine.combined import Combined
from kge.services.ask.engine.core import PRESETS, parse
from kge.services.ask.engine.scope import ProductScope
from kge.services.ask.engine import planrules
from kge.semantics.plan import HistoryEntry
from tests.integration.test_combined import Stub
from tests.integration.test_answer_text import client as client, mcp_wire


@pytest.mark.parametrize('transport', ['http', 'mcp'])
def test_transports_forward_checked_history_and_reject_changed_purpose(client, transport):
    who = 'analyst_retail.meridian@example.com'
    def ask(body):
        if transport == 'mcp':
            return mcp_wire(client, '/mcp/answer', who, 'ask', body)['structuredContent']
        response = client.post('/ask', json=body, headers=client.as_(who))
        assert response.status_code == 200, response.text
        return response.json()
    body = {'question': 'Retail revenue for June 2025'}
    first = ask(body)
    second = ask({**body, 'history': first['history']})
    assert second['figure'] == first['figure'] == 13287.67
    assert len(second['history']) == 2 and second['history'][0] == first['history'][0]
    changed = ask({**body, 'history': first['history'], 'purpose': 'another purpose'})
    assert changed['behaviour'] == 'abstain' and not changed.get('figure') and not changed.get('result')


def test_public_rules_execute_checked_plan_and_preserve_scalar(eng):
    for call in (eng.answer, Combined(eng, llm=None).ask):
        answer = call('Retail revenue for June 2025', 'analyst_enterprise')
        assert answer['figure'] == 13287.67
        assert answer['guard']['passed'] and answer['sql_source'] == 'query-plan-v1'
        assert answer['versions']['interpretation'] == 'rules'
        assert answer['result']['rows'][0]['cells'][0]['citations']
        entry = HistoryEntry.model_validate(answer['history'][-1])
        assert entry.plan.measures[0].context.id == 'retail_revenue'


def test_configured_model_is_the_public_interpreter(eng):
    question = 'Retail revenue for June 2025'
    model = Stub(eng, question=question)
    answer = Combined(eng, llm=model).ask(question, 'analyst_enterprise',
        config=PRESETS['graph'].with_stages(resolution='glossary+model'))
    assert answer['figure'] == 13287.67
    assert len(model.prompts) == 1 and model.prompts[0].startswith('query-plan-v1\n')
    assert answer['versions']['interpretation'] == 'model:stub:fixed'
    assert 'Glossary terms:' not in model.prompts[0]


def test_explicit_model_sql_source_means_plan_interpretation(eng):
    model = Stub(eng, question='Retail revenue for June 2025')
    answer = Combined(eng, llm=model).ask('Retail revenue for June 2025', 'analyst_enterprise',
        config=PRESETS['graph'].with_stages(sql_source='model'))
    assert answer['figure'] == 13287.67 and answer['sql_source'] == 'query-plan-v1'
    assert len(model.prompts) == 1


@pytest.mark.parametrize('question', [
    'What is the email address of customer 118?', 'Show me the date of birth of customer 118',
    'Wealth revenue for June 2025',
])
def test_policy_and_access_precede_model_disclosure(eng, question):
    class Uncalled:
        name = 'uncalled'
        def json(self, *args, **kwargs):
            pytest.fail('denied question reached the interpreter')
    answer = Combined(eng, llm=Uncalled()).ask(question, 'analyst_retail')
    assert answer['behaviour'] == 'refuse' and not answer.get('figure') and not answer.get('sql')


def test_g023_is_typed_clarification_for_the_undeclared_corporate_population(eng):
    question = 'How many customers does the bank have across Retail and Corporate?'
    answer = eng.answer(question, 'analyst_enterprise')
    assert answer['behaviour'] == 'clarify' and not answer.get('figure') and not answer.get('parts')
    assert answer['clarification']['choices'] == [{'id': 'retail_customer', 'label': 'Retail customer'}]
    assert HistoryEntry.model_validate(answer['history'][-1]).clarification is not None


@pytest.mark.parametrize('surface', ['ask', 'answer', 'agent', 'assist'])
def test_http_g023_ui_choice_answers_exactly_the_declared_population(client, surface):
    body = {'question': 'How many customers does the bank have across Retail and Corporate?', 'purpose': 'analysis',
            'arm': 'treatment' if surface == 'agent' else 'graph'}
    headers = client.as_('analyst_enterprise.meridian@example.com')
    first = client.post('/' + surface, json=body, headers=headers).json()
    assert first['behaviour'] == 'clarify', first
    golden = json.loads((ROOT / 'clients/meridian/fixture/golden.json').read_text())
    expected = next(i for i in golden['items'] if i['id'] == 'G023')['expect']['reason']
    assert first['reason'] == expected
    choice, = first['clarification']['choices']
    question = body['question']
    for span in sorted(first['clarification']['spans'], key=lambda s: s['start'], reverse=True):
        question = question[:span['start']] + choice['label'] + question[span['end']:]
    response = client.post('/' + surface, json={**body, 'question': question, 'history': first['history']}, headers=headers)
    assert response.status_code == 200, response.text
    answer = response.json()
    assert answer['behaviour'] == 'figure' and answer['figure'] == 200, answer
    assert answer['history'][-1]['plan']['measures'][0]['entity']['id'] == choice['id']
    assert len(answer['history']) == 2 and answer['history'][0] == first['history'][0]


@pytest.mark.parametrize('surface', ['ask', 'answer', 'agent', 'assist'])
@pytest.mark.parametrize('question', ['What is Revenue?', 'What is the email address of customer 118?',
                                   'Retail revenue for June 2025 unrecognized'])
def test_http_no_figure_outcomes_return_empty_history(client, surface, question):
    response = client.post('/' + surface, json={'question': question},
                           headers=client.as_('analyst_enterprise.meridian@example.com'))
    assert response.status_code == 200, response.text
    assert response.json()['history'] == []
    assert not response.json().get('figure') and not response.json().get('result')


@pytest.mark.parametrize('surface', ['ask', 'answer', 'agent', 'assist'])
@pytest.mark.parametrize('base,followup,explicit', [
    ('NNA for June 2025', 'And for April 2025?', 'NNA for April 2025'),
    ('Retail revenue for June 2025', 'And for SME?', 'SME revenue for June 2025'),
    ('NNA for June 2025', 'From BROKERAGE?', 'NNA for June 2025 from BROKERAGE'),
    ('NNA for June 2025 from BROKERAGE', 'From BANK?', 'NNA for June 2025 from BANK'),
])
def test_http_short_followup_replaces_only_its_named_slot(client, surface, base, followup, explicit):
    def ask(question, **extra):
        response = client.post('/' + surface, json={'question': question, **extra},
                               headers=client.as_('analyst_enterprise.meridian@example.com'))
        assert response.status_code == 200, response.text
        return response.json()
    first = ask(base)
    answer = ask(followup, history=first['history'])
    expected = ask(explicit)
    assert answer['behaviour'] == 'figure' and answer['figure'] == expected['figure'], answer
    assert answer['history'][0] == first['history'][0] and len(answer['history']) == 2
    assert 'no period asked: every month the filters keep' not in answer.get('notes', [])
    old, new = first['history'][-1]['plan'], answer['history'][-1]['plan']
    if 'April' in followup:
        assert old['measures'] == new['measures'] and old['filters'] == new['filters']
    else:
        assert old['time'] == new['time']
    again = ask('And for May 2025?', history=answer['history'])
    assert again['behaviour'] == 'figure' and len(again['history']) == 3, again
    assert again['figure'] == ask(explicit.replace('June', 'May').replace('April', 'May'))['figure']


@pytest.mark.parametrize('followup', ['And for April 2025 sparkly?', 'And for the secret unit?',
                                    'And for June?', 'Without that filter?'])
def test_http_followup_cannot_drop_constraints_or_unread_words(client, followup):
    headers = client.as_('analyst_enterprise.meridian@example.com')
    first = client.post('/ask', json={'question': 'NNA for June 2025 from BROKERAGE'}, headers=headers).json()
    answer = client.post('/ask', json={'question': followup, 'history': first['history']}, headers=headers).json()
    assert answer['behaviour'] == 'abstain' and answer['history'] == [], answer
    assert not answer.get('figure') and not answer.get('result') and not answer.get('sql_run')


def test_declared_activity_alias_answers_h003(eng):
    answer = eng.answer('Count the SME clients that were active during June 2025', 'analyst_enterprise')
    assert answer['figure'] == 59 and answer['column'] == 'client_activity.active_flag'


def test_a_flag_on_a_declared_event_entity_counts_that_entitys_key(eng):
    answer = eng.answer('Number of suitability reviews escalated in 2025', 'analyst_enterprise')
    assert answer['figure'] == 14 and 'RVW_ID' in answer['sql']
    assert 'COUNT(DISTINCT' in answer['sql']


def test_narrow_context_keeps_release_metadata_and_round_trippable_history(eng):
    answer = eng.answer('NNA June 2025', 'advisor_wealth')
    from tests.integration.test_plan_wording_round2 import sql
    expected = sql(eng, "SELECT round(sum(CASE WHEN DW_TRAN_DIRCTN_CD='O' THEN -TRAN_AM ELSE TRAN_AM END),2) "
                   "FROM TRAN_FACT_1 f JOIN MONTH_DIM_1 m ON f.MNTH_END_DT=m.CLDR_EOM_DT "
                   "WHERE f.MNTH_END_DT=DATE '2025-06-30' AND f.CURR_IN<>0 AND f.RCV_IN=1 AND f.NNA_IN='Y' "
                   "AND m.DW_BRKRG_MNTH_FRM_TODAY_CT<>0 AND NOT EXISTS (SELECT 1 FROM ACCT_DIM2_1 a "
                   "WHERE a.DW_ACCT_ID=f.DW_ACCT_ID AND a.SRCE_SYS_ID=f.ACCT_SRCE_SYS_ID "
                   "AND a.CURR_IN<>0 AND a.TEST_ACCT_IN='Y')")[0][0]
    assert answer['figure'] == float(expected)
    assert answer['context']['context'] == 'nna' and answer['context']['fact'] == 'wealth.TRAN_FACT_1'
    assert answer['months'] == ['2025-06-30'] and len(answer['sql_run']) == 2
    history = [HistoryEntry.model_validate(h) for h in answer['history']]
    again = eng.answer('NNA June 2025', 'advisor_wealth', history=history)
    assert again['figure'] == answer['figure']


def test_fact_only_mapping_cannot_promote_a_harvested_key_to_a_declared_role(eng):
    answer = eng.answer('Retail net interest income for June 2025 in region NW', 'analyst_enterprise')
    assert answer['behaviour'] == 'abstain' and not answer.get('figure') and not answer.get('sql')


def test_retained_numeric_helpers_cannot_bypass_coverage(eng, monkeypatch):
    question = 'Retail revenue for June 2025 sparkly'
    req = parse(question, eng.term_names, eng)
    impl = next(i for i in eng.g.implementations('Revenue') if i['unit'] == 'retail')
    known = parse('Retail revenue for June 2025', eng.term_names, eng)
    part = eng.plan_unit(known, impl, PRESETS['graph'])
    assert part['status'] == 'ok' and part['value'] == 13287.67 and part['guard']['passed']
    monkeypatch.setattr('kge.services.ask.engine.planner.execute', lambda *args: pytest.fail('unchecked query executed'))
    part = eng.plan_unit(req, impl, PRESETS['graph'])
    assert part['status'] == 'abstain' and 'sparkly' in part['reason'] and 'value' not in part
    stale = {'question': question, 'figure': 999, 'sql': 'SELECT 999'}
    for answer in (eng.context_answer(req, 'analyst_enterprise', stale, []),
                   eng.linked_answer(None, 'analyst_enterprise', stale, [])):
        assert answer['behaviour'] == 'abstain' and 'sparkly' in answer['reason']
        assert not answer.get('figure') and not answer.get('sql')


@pytest.mark.parametrize('question', [
    'Total assets under advice plus corporate credit exposure',
    'AUM plus AUA for household 5001 at 31 Mar 2025', 'NNA plus wealth revenue for FY26-Q1',
])
def test_unsupported_addition_never_returns_an_operand(eng, question):
    answer = Combined(eng, llm=None).ask(question, 'analyst_enterprise')
    assert answer['behaviour'] == 'abstain' and not answer.get('figure') and not answer.get('figures')
    assert 'Addition' in answer['reason']


@pytest.mark.parametrize('question,pair', [
    ('Which SME clients are also wealth households?', 'sme-wealth'),
    ('Which corporate counterparty contacts are also wealth households?', 'corporate-wealth'),
    ('Can you match SME clients with wealth households?', 'sme-wealth'),
])
def test_declared_entity_phrases_keep_no_join_refusal_precedence(eng, question, pair):
    answer = Combined(eng, llm=Stub(eng)).ask(question, 'analyst_enterprise')
    assert answer['behaviour'] == 'refuse' and pair in answer['joins_refused'], answer
    assert not answer.get('sql') and not answer.get('figure')


def test_same_declared_term_across_units_remains_separate_population_parts(eng):
    answer = eng.answer('Active customers across Retail and SME in June 2025', 'analyst_enterprise')
    assert answer['behaviour'] == 'parts_with_reason' and answer['parts'] == {'retail': 176, 'sme': 59}, answer


def test_missing_observations_cannot_be_summed_as_a_partial_total(eng):
    answer = eng.answer('Retail and SME revenue for June 2090', 'analyst_enterprise')
    assert answer['behaviour'] == 'parts_with_reason' and not answer.get('figure')
    assert set(answer['missing_parts']) == {'retail', 'sme'}
    assert all(c['null_reasons'] for c in answer['result']['rows'][0]['cells'])


def test_longest_declared_variant_does_not_add_its_unqualified_measure(eng):
    answer = eng.answer('Client-directed NNA for June 2025', 'analyst_enterprise')
    from tests.integration.test_plan_wording_round2 import sql
    expected = sql(eng, "SELECT round(sum(NNA_AM),2) FROM F_NNA_FIN f "
                   "JOIN MONTH_DIM_1 m ON f.DW_TRAN_MNTH_END_DT=m.CLDR_EOM_DT "
                   "WHERE f.DW_TRAN_MNTH_END_DT=DATE '2025-06-30' AND f.CLIENT_DIRECTED_NNA_IN='Y' "
                   "AND m.DW_BRKRG_MNTH_FRM_TODAY_CT<>0")[0][0]
    assert answer['behaviour'] == 'figure' and answer['figure'] == float(expected), answer
    assert len(answer['result']['columns']) == 1
    assert answer['history'][-1]['plan']['measures'][0]['context']['id'] == 'nna_client_directed'


@pytest.mark.parametrize('question,expected', [
    ('Total revenue across the bank for the quarter to 30 June 2025',
     {'retail': 39983.72, 'sme': 953824.62, 'corporate': 20190379.43, 'wealth': 2506757.26}),
    ('Bank revenue for calendar Q1 2025',
     {'retail': 40483.98, 'sme': 899746.55, 'corporate': 20046400.76, 'wealth': 2404440.18}),
])
def test_declared_source_set_preserves_all_parts(eng, question, expected):
    answer = eng.answer(question, 'analyst_enterprise')
    assert answer['behaviour'] == 'parts_with_reason' and answer['parts'] == expected
    assert not answer.get('figure') and len(answer['result']['columns']) == 4
    assert {p['unit']: p['value'] for p in answer['plan']} == expected


@pytest.mark.parametrize('question', [
    'What is an active customer?', 'What is the email address of customer 118?',
    'Retail revenue for June 2025 unrecognized', 'Who owns the retail customer table?',
])
def test_every_public_outcome_returns_history(eng, question):
    for call in (eng.answer, Combined(eng, llm=None).ask):
        answer = call(question, 'analyst_retail')
        assert isinstance(answer['history'], list)
        assert not answer.get('figure')


@pytest.mark.parametrize('model', [False, True])
def test_public_interpreters_resolve_the_ui_clarification_label_to_exactly_its_context(eng, monkeypatch, model):
    from kge.semantics.query import Context
    for identifier, label in [('choice_a', 'Operating receipts'), ('choice_b', 'Other receipts')]:
        monkeypatch.setitem(eng.semantics.contexts, identifier,
            Context(identifier, label, '', 'Revenue', 'retail.revenue', 'sum(monthly_revenue.total_revenue_usd)',
                    'sum', 'month', 'retail.monthly_revenue', ('retail.monthly_revenue.total_revenue_usd',), (), (), ()))
    monkeypatch.setattr(eng.semantics, 'plan_snapshot', None, raising=False)
    question = 'Retail revenue for June 2025'
    first = Combined(eng, llm=Stub(eng, question=question) if model else None).ask(question, 'analyst_enterprise')
    assert first['behaviour'] == 'clarify', first
    assert len(first['clarification']['spans']) == 1
    span = first['clarification']['spans'][0]
    assert question[span['start']:span['end']] == 'revenue'
    revised = question[:span['start']] + 'Operating receipts' + question[span['end']:]
    replay = Stub(eng, question=revised) if model else None
    if replay is not None:
        replay.proposal['base_turn'] = first['history'][-1]['turn_id']
    second = Combined(eng, llm=replay).ask(revised, 'analyst_enterprise', history=[HistoryEntry.model_validate(h) for h in first['history']])
    assert second['figure'] == 13287.67 and second['context']['id'] == 'choice_a', second


@pytest.mark.parametrize('question', [
    'Retail revenue for June 2025 unrecognized', 'Retail revenue for June 2025 excluding unrecognized',
    'Retail revenue and complaint count for June 2025',
])
def test_unconsumed_obligations_cannot_fall_back_to_a_total(eng, question):
    answer = Combined(eng, llm=None).ask(question, 'analyst_enterprise')
    assert answer['behaviour'] in ('abstain', 'clarify') and not answer.get('figure') and not answer.get('sql')


def test_product_default_can_fall_back_to_a_readable_source(eng):
    answer = Combined(eng, llm=None).ask('Retail revenue for June 2025', 'analyst_enterprise', products=['wealth.TRAN_FACT_1'])
    assert answer['figure'] == 13287.67 and answer['products'] == ['wealth.tran_fact_1']


def test_forged_unused_history_is_rejected_on_rules_and_model(eng):
    first = eng.answer('Retail revenue for June 2025', 'analyst_enterprise')
    data = copy.deepcopy(first['history'][0])
    data['binding']['release_identity'] = 'forged'
    history = [HistoryEntry.model_validate(data)]
    for combined in (Combined(eng, llm=None), Combined(eng, llm=Stub(eng, question='Retail revenue for June 2025'))):
        answer = combined.ask('Retail revenue for June 2025', 'analyst_enterprise', history=history)
        assert answer['behaviour'] == 'abstain' and not answer.get('figure')
        if combined.llm is not None:
            assert not combined.llm.prompts


def test_model_cannot_replace_a_count_with_rows(eng):
    question = 'Number of SME facilities in covenant breach'
    req = parse(question, eng.term_names, eng)
    vocabulary = planrules.vocabulary(eng, 'analyst_enterprise', 'analysis', ProductScope(eng, None), PRESETS['graph'], req)
    reply = planrules.read(eng, req, vocabulary, PRESETS['graph'])
    assert reply.kind in ('propose', 'abstain')
    baseline = eng.answer(question, 'analyst_enterprise')
    assert baseline['behaviour'] == 'figure' and baseline['figure'] == 8
    model = Stub(eng, proposal={'kind': 'propose', 'base_turn': None, 'sql': 'SELECT count(*) FROM covenant'})
    answer = Combined(eng, llm=model).ask(question, 'analyst_enterprise')
    assert answer['behaviour'] == 'abstain' and not answer.get('figure')
    assert len(model.prompts) == 2 and json.loads(model.prompts[1].split('\n', 1)[1])['errors']
