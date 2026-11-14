"""The step-nine gate's Map, query-card and newly onboarded recipe boundaries."""
import json
import shutil

import pytest
import yaml

from kge.adapters.warehouse.bigquery import BigQueryWarehouses
from kge.compiler import pack
from kge.estate import onboard
from kge.eval.margin import v05
from kge.eval.run_golden import score
from kge.graph.release import swap
from kge.pipeline import unit_history
from kge.services.ask.engine.agent import Agent
from kge.services.ask.engine.core import Engine
from kge.services.ask.engine.present import present
from kge.services.ask.service import term_map
from tests import bigquery_fake
from tests.access_fixture import seed_access
from tests.scratch import DEMO, profile_of, script, write_registry
from tests.unit.test_planner_jobs import jobs
from tests.integration.test_answer_text import client as client


def test_map_uses_the_checked_measure_with_an_entity_qualifier(eng):
    answer = eng.answer('AUM for household 5001 at 31 Mar 2025', 'analyst_enterprise')
    assert answer['behaviour'] == 'figure' and answer['figure'] == 7641422.24
    term = answer['context']['term']
    assert term == 'Assets Under Management'
    mapped = term_map(eng, term, 'analyst_enterprise')
    context, = [c for c in mapped['contexts'] if c['id'] == answer['context']['context']]
    assert context['id'] == 'aum'
    assert context['measure'] == 'wealth.assets-under-management'
    assert context['fact'] == 'wealth.AUM_MNTH_END_FACT_1'


def test_page_answer_supplies_the_term_for_its_actual_map_request(client):
    headers = client.as_('analyst_enterprise.meridian@example.com')
    response = client.post('/ask', json={'question': 'AUM for household 5001 at 31 Mar 2025', 'arm': 'graph'}, headers=headers)
    assert response.status_code == 200, response.text
    answer = response.json()
    assert answer['figure'] == 7641422.24
    term = answer['context']['term']
    assert term == 'Assets Under Management'
    response = client.get('/map/' + term, headers=headers)
    assert response.status_code == 200, response.text
    context, = [c for c in response.json()['contexts'] if c['id'] == answer['context']['context']]
    assert (context['id'], context['measure'], context['fact']) == ('aum', 'wealth.assets-under-management', 'wealth.AUM_MNTH_END_FACT_1')


def test_page_profile_lists_only_the_clean_packs_sources(client):
    response = client.get('/profile')
    assert response.status_code == 200, response.text
    declared = yaml.safe_load((DEMO / 'clients/meridian/pack.yaml').read_text())
    assert response.json()['units'] == [source['id'] for source in declared['sources']]


def test_division_card_lists_the_checked_answer_and_release_queries(eng):
    answer = eng.answer('NNA for FY26-Q1 by division', 'analyst_enterprise')
    assert answer['behaviour'] == 'figure' and answer['guard']['passed']
    assert len(answer['sql_run']) == 2
    assert answer['sql_run'][-1] == answer['sql']
    card = present(answer, eng)
    assert [query['role'] for query in card['queries']] == ['release', 'figure']
    assert card['checked']['line'] == 'gate passed · 2 checked queries · SQL by query-plan-v1 · text by template'


@pytest.fixture(scope='module', params=[1, 2], ids=['initial', 'updated'])
def onboarded_recipe(tmp_path_factory, request):
    root = tmp_path_factory.mktemp('gate-onboarding')
    clients = root / 'clients'
    shutil.copytree(DEMO / 'clients/meridian', clients / 'meridian')
    write_registry(root, clients=clients, artifacts=root / 'artifacts', store=root / 'stores',
                   landing=root / 'landing', warehouses=root / 'meridian/warehouses', baseline=None)
    prof = profile_of(root)
    for step in ('kge/estate/from_files.py', 'kge/estate/certify.py', 'kge/compiler/lock.py',
                 'kge/compiler/compile.py'):
        result = script(step, build=root)
        assert result.returncode == 0, result.stdout + result.stderr
    swap(prof, result.stdout.strip().rstrip('/').rsplit('/', 1)[-1])
    seed_access(prof)
    onboard.create(prof, 'bancassurance', semantic_dir=DEMO / 'examples/bancassurance-osi',
                   label='Bancassurance', as_='knowledge_engineer.meridian@example.com')
    onboard.draft(prof, 'bancassurance', 'knowledge_engineer.meridian@example.com', '2026-10-18')
    onboard.accept(prof, 'bancassurance', 'all', 'business_team.meridian@example.com', '2026-10-18')
    onboard.certify(prof, 'bancassurance', 'all', 'steward_glossary.meridian@example.com', '2026-10-18')
    onboard.build(prof)
    if request.param == 2:
        unit_history.record(prof, 'release', 'steward_glossary.meridian@example.com',
                            {'model_hash': prof.model_hash(), 'pack_version': '1.0.0'}, ['bancassurance'])
        onboard.create(prof, 'bancassurance', semantic_dir=DEMO / 'examples/bancassurance-osi-v2',
                       label='Bancassurance', as_='knowledge_engineer.meridian@example.com')
        onboard.draft(prof, 'bancassurance', 'knowledge_engineer.meridian@example.com', '2026-10-18')
        onboard.accept(prof, 'bancassurance', 'all', 'business_team.meridian@example.com', '2026-10-18')
        onboard.certify(prof, 'bancassurance', 'all', 'steward_glossary.meridian@example.com', '2026-10-18')
        onboard.build(prof)
    for step in ('kge/estate/certify.py', 'kge/compiler/lock.py', 'kge/compiler/compile.py'):
        result = script(step, build=root)
        assert result.returncode == 0, result.stdout + result.stderr
    artifact = result.stdout.strip().rstrip('/')
    result = script('kge/graph/load.py', '--artifact', artifact, build=root)
    assert result.returncode == 0, result.stdout + result.stderr
    engine = Engine.candidate(prof.at(artifact.rsplit('/', 1)[-1]))
    yield prof, engine
    engine.close()


@pytest.mark.parametrize('arm', ['graph', 'treatment'])
def test_onboarded_recipe_answers_its_own_checked_questions(onboarded_recipe, arm):
    prof, engine = onboarded_recipe
    data = json.loads((prof.estate() / 'golden_bancassurance_v1.json').read_text())
    for item in data['items']:
        answer = (engine.answer(item['question'], 'analyst_enterprise') if arm == 'graph'
                  else Agent(engine, llm=None).ask(item['question'], 'analyst_enterprise', arm='treatment'))
        passed, why = score(item, answer)
        assert passed, (item['id'], why, answer)


def test_onboarded_recipes_pass_v05_at_the_unchanged_pack_floor(onboarded_recipe):
    prof, engine = onboarded_recipe
    data = json.loads((prof.estate() / 'golden_bancassurance_v1.json').read_text())
    agent = Agent(engine, llm=None)
    records = [dict(set='golden_bancassurance_v1', arm='treatment', kind=item['kind'],
                    ok=score(item, agent.ask(item['question'], 'analyst_enterprise', arm='treatment'))[0])
               for item in data['items']]
    declared = pack.read(prof.clients, prof.client)
    waiting, checks = v05(declared.drafts, records, {'sets': {'golden_bancassurance_v1': {}}},
                         declared.data['manifest']['gates'])
    assert not waiting and len(checks) == 2 and all(check.ok for check in checks), checks
    assert all(record['ok'] for record in records)


def test_recipe_qualifier_on_native_warehouse_keeps_one_job_and_its_scope(onboarded_recipe, monkeypatch):
    prof, engine = onboarded_recipe
    version = onboard.show(prof)['units'][0]['version']
    fake = bigquery_fake.install(monkeypatch)
    warehouses = BigQueryWarehouses(prof, ['bancassurance'], project=bigquery_fake.PROJECT,
                                   versions={'bancassurance': version})
    source = prof.warehouses / f'onboarded/bancassurance/v{version}/bancassurance.duckdb'
    fake.con.execute(f"ATTACH '{source}' AS source (READ_ONLY)")
    tables = fake.con.execute("SELECT table_name FROM information_schema.tables WHERE table_catalog='source'").fetchall()
    for (table,) in tables:
        physical = fake._physical(warehouses.dataset_id, warehouses.prefix('bancassurance') + '_' + table)
        fake.con.execute(f'CREATE TABLE "{physical}" AS SELECT * FROM source."{table}"')
    fake.con.execute('DETACH source')
    monkeypatch.setattr(engine, 'w', warehouses)
    try:
        qualifiers = ['Active-policy'] * 2 if version == 1 else ['Active and lapsed policy', 'Active or lapsed policy']
        for qualifier in qualifiers:
            fake.requests.clear()
            answer = engine.answer(f'{qualifier} written premium for June 2025', 'analyst_enterprise')
            assert answer['behaviour'] == 'figure' and answer['figure'] == (1200 if version == 1 else 1650)
            assert answer['guard']['passed'] and jobs(fake) == (1, 1)
        refused = [
            ('Lapsed-policy written premium for June 2025', 'analyst_enterprise'),
            (f'{qualifiers[0]} written premium for June 2025', 'analyst_retail'),
        ]
        if version == 2:
            refused.append(('Active-policy written premium for June 2025', 'analyst_enterprise'))
        for question, principal in refused:
            fake.requests.clear()
            answer = engine.answer(question, principal)
            assert answer['behaviour'] in ('abstain', 'refuse') and answer.get('figure') is None
            assert jobs(fake) == (0, 0)
    finally:
        fake.con.close()
