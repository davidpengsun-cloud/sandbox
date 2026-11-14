"""The pack's rollup policy prevents historical versions multiplying a planned figure."""
from dataclasses import replace
from decimal import Decimal
import shutil

import pytest

from kge.adapters.warehouse import open_estate, open_warehouses
from kge.semantics.plan import Propose, QueryPlan
from kge.services.ask.engine import planmodel, planrules
from kge.services.ask.engine.combined import Combined
from kge.services.ask.engine.core import PRESETS, parse
from kge.services.ask.engine.planner import Planner
from kge.services.ask.engine.scope import ProductScope
from kge.services.ask.engine.wording import Hidden
from tests.plan_fixtures import grounded, name


@pytest.mark.parametrize('mode', ['current', 'as_reported'])
def test_client_code_rollup_planner_reads_one_version_per_key(eng, tmp_path, mode):
    suffix = ' as reported' if mode == 'as_reported' else ''
    question = 'NNA for June 2025 by client_code_rollup CLNT_CD' + suffix
    req = parse(question, eng.term_names, eng)
    vocab = planrules.vocabulary(eng, 'analyst_enterprise', 'analysis', ProductScope(eng, None), PRESETS['graph'], req)
    base = planrules.read(eng, parse('NNA for June 2025', eng.term_names, eng), vocab, PRESETS['graph'], 'turn')
    assert isinstance(base, Propose)
    data = base.plan.model_dump(mode='json', by_alias=True)
    data['measures'][0]['mode'] = mode
    data['dimensions'] = [{'kind': 'column', 'id': 'code', 'bindings': [{
        'context': name('nna'), 'column': name('wealth.CLIENT_CD_ROLLUP_1.CLNT_CD'),
        'path': [name('flow_account'), name('account_client_code')], 'level': None}]}]
    plan = grounded(QueryPlan.model_validate(data), question, vocab,
                    {'NNA': 'm0', 'June 2025': 'time', 'by': 'code', 'client_code_rollup': 'code',
                     'CLNT_CD': 'code', 'as reported': 'm0'})

    class Scripted:
        def json(self, prompt):
            return {'kind': 'propose', 'base_turn': None, 'plan': plan.model_dump(mode='json', by_alias=True)}

    reply = planmodel.read(Scripted(), question, vocab)
    hidden = Hidden.of(eng, 'analyst_enterprise')
    expected = Planner(vocab, eng.w, hidden).answer(question, reply)
    assert expected['kind'] == 'result', expected
    groups = [row for row in expected['result']['rows'] if not row.get('total')]
    totals = [row for row in expected['result']['rows'] if row.get('total')]
    assert len(groups) == 12 and len(totals) == 1
    assert Decimal(totals[0]['cells'][0]['value']['value']) == sum(Decimal(row['cells'][0]['value']['value']) for row in groups)
    folder = tmp_path / 'warehouses'
    folder.mkdir()
    shutil.copy2(eng.profile.warehouses / 'wealth.duckdb', folder / 'wealth.duckdb')
    estate = open_estate(folder, 'meridian')
    with estate.open('wealth', write=True) as session:
        session.execute('CREATE TABLE versioned_rollup AS SELECT * FROM CLIENT_CD_ROLLUP_1')
        session.execute('DROP TABLE CLIENT_CD_ROLLUP_1')
        session.execute('ALTER TABLE versioned_rollup RENAME TO CLIENT_CD_ROLLUP_1')
        session.execute("INSERT INTO CLIENT_CD_ROLLUP_1 SELECT * REPLACE (DATE '2020-01-01' AS DW_EFF_DT, "
                        "DATE '2024-12-31' AS DW_EXPR_DT, 0 AS CURR_IN) FROM CLIENT_CD_ROLLUP_1")
        assert session.execute('SELECT COUNT(*), COUNT(DISTINCT CLNT_CD) FROM CLIENT_CD_ROLLUP_1').fetchone() == (24, 12)
    warehouses = open_warehouses(replace(eng.profile, warehouses=folder), ['wealth'])
    try:
        actual = Planner(vocab, warehouses, hidden).answer(question, reply)
        assert actual['kind'] == 'result', actual
        assert actual['guard']['passed']
        assert actual['result'] == expected['result']
        assert ('CLIENT_CD_ROLLUP_1.CURR_IN <> 0' if mode == 'current'
                else 'CLIENT_CD_ROLLUP_1.DW_EFF_DT') in actual['sql']
    finally:
        warehouses.close()


@pytest.mark.parametrize('question', [
    'Show client names', 'Show household names', 'Show household name',
    'Show HSHLD_DIM_1.HSHLD_NM', 'Show wealth.HSHLD_DIM_1.HSHLD_NM', 'Show household.household_name',
])
def test_privacy_phrases_decline_up_front_under_data_classification(eng, question):
    class Uncalled:
        name = 'uncalled'

        def json(self, *args, **kwargs):
            pytest.fail('Restricted phrase reached the interpreter')

    vocab = planrules.vocabulary(eng, 'analyst_enterprise', 'analysis', ProductScope(eng, None), PRESETS['graph'])
    assert vocab.denial(question)
    answer = Combined(eng, llm=Uncalled()).ask(question, 'analyst_enterprise')
    assert answer['behaviour'] == 'refuse', answer
    assert 'POL-DATA-CLASS' in answer['reason']
    assert answer['restricted']
    assert not answer.get('figure') and not answer.get('result') and not answer.get('sql')


@pytest.mark.parametrize('context,phrase', [('nna_certified', 'Certified NNA'), ('nna_branch_sales', 'Branch Sales NNA')])
@pytest.mark.parametrize('mode', ['current', 'as_reported'])
def test_as_of_account_join_never_selects_a_voided_version(eng, tmp_path, context, phrase, mode):
    question = phrase + ' for April 2025 by household' + (' as reported' if mode == 'as_reported' else '')
    req = parse(question, eng.term_names, eng)
    vocab = planrules.vocabulary(eng, 'analyst_enterprise', 'analysis', ProductScope(eng, None), PRESETS['graph'], req)
    reply = planrules.read(eng, req, vocab, PRESETS['graph'])
    assert isinstance(reply, Propose), reply
    assert {m.context.id for m in reply.plan.measures} == {context}
    hidden = Hidden.of(eng, 'analyst_enterprise')
    expected = Planner(vocab, eng.w, hidden).answer(question, reply)
    assert expected['kind'] == 'result', expected
    folder = tmp_path / 'warehouses'
    folder.mkdir()
    shutil.copy2(eng.profile.warehouses / 'wealth.duckdb', folder / 'wealth.duckdb')
    estate = open_estate(folder, 'meridian')
    with estate.open('wealth', write=True) as session:
        session.execute('CREATE TABLE account_versions AS SELECT * FROM ACCT_DIM2_1')
        session.execute('DROP TABLE ACCT_DIM2_1')
        session.execute('ALTER TABLE account_versions RENAME TO ACCT_DIM2_1')
        session.execute("INSERT INTO ACCT_DIM2_1 SELECT * REPLACE (DATE '2025-05-01' AS DW_EFF_DT, "
                        "DATE '2025-03-31' AS DW_EXPR_DT, 1 AS CURR_IN, -999 AS LAST_RCV_HSHLD_DK) FROM ACCT_DIM2_1")
    warehouses = open_warehouses(replace(eng.profile, warehouses=folder), ['wealth'])
    try:
        actual = Planner(vocab, warehouses, hidden).answer(question, reply)
        assert actual['kind'] == 'result', actual
        assert actual['guard']['passed']
        assert actual['result'] == expected['result']
        assert 'ACCT_DIM2_1.DW_EFF_DT' in actual['sql'] and 'ACCT_DIM2_1.DW_EXPR_DT' in actual['sql']
    finally:
        warehouses.close()
