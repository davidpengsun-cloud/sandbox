"""Live Ask event order and unchanged request budgets on the local warehouse and native BigQuery twin."""
from __future__ import annotations

import json

import pytest

from kge.pipeline import trace
from kge.services.ask import service
from kge.services.ask.engine.combined import Combined
from kge.services.ask.engine.core import PRESETS
from tests.bigquery_fake import budgets
from tests.integration.test_answer_text import client as client
from tests.integration.test_ask_gates import comparison_engine as comparison_engine
from tests.unit.test_ask_stream_events import assembled

QUESTION = 'NNA for FY26-Q1 by division'
WHO = 'analyst_enterprise'


def streamed(engine, question=QUESTION, who=WHO, combined=None):
    events = []
    with service.recording(question, events.append) as recorder:
        if combined is None:
            answer = service.answer(engine, engine.profile, question, who, 'analysis', 'graph', PRESETS['graph'],
                                    on_stage=recorder.on_stage)
        else:
            answer = service.ask(combined, engine.profile, question, who, 'analysis', 'graph',
                                 PRESETS['graph'].with_stages(sql_source='planner+model'), on_stage=recorder.on_stage)
    recorder.emit_final(answer)
    return events, answer


def test_figure_streams_plan_context_and_graph_before_execution_with_live_progress(comparison_engine, monkeypatch):
    engine, fake = comparison_engine
    baseline = service.answer(engine, engine.profile, QUESTION, WHO, 'analysis', 'graph', PRESETS['graph'])
    assert baseline['behaviour'] == 'figure'
    if fake is not None:
        from kge.adapters.warehouse.bigquery import _WarehouseQueries
        init = _WarehouseQueries.__init__
        now = [0.0]

        def queries(self, *args, **kwargs):
            init(self, *args, **kwargs)
            self._clock = lambda: now[0]

        monkeypatch.setattr(_WarehouseQueries, '__init__', queries)
        get = fake.get

        def poll(url):
            now[0] += 3
            return get(url)

        monkeypatch.setattr(fake, 'get', poll)
        fake.pending_polls = 5
        fake.requests.clear()
    events, answer = streamed(engine)
    assert answer['behaviour'] == 'figure', answer
    kinds = [event['event'] for event in events]
    assert kinds[-1] == 'answer' and kinds.count('answer') == kinds.count('plan') == kinds.count('context') == 1
    plan = next(event['plan'] for event in events if event['event'] == 'plan')
    assert plan['sql'] == answer['sql'] and plan['unit'] == answer['unit']
    assert plan['sql_source'] == answer['sql_source'] and plan['guard']['passed'] is True
    assert next(event['context'] for event in events if event['event'] == 'context') == answer.get('context')
    first_job = kinds.index('progress')
    assert max(kinds.index('plan'), kinds.index('context')) < first_job
    before = assembled(events[:first_job])
    assert {'Words:NNA', 'Context:nna', 'Measure:wealth.net-new-assets', 'Table:wealth.TRAN_FACT_1',
            'Runtime:reference', 'Unit:wealth'} <= {n['id'] for n in before['nodes']}
    assert any(e['type'] == 'matches' for e in before['edges'])
    assert any(e['type'] == 'IMPLEMENTS' for e in before['edges'])
    graph = assembled(events)
    assert graph == {key: answer['graph_path'][key] for key in ('nodes', 'edges')}
    assert graph == {key: baseline['graph_path'][key] for key in ('nodes', 'edges')}
    node_ids = [event['node']['id'] for event in events if event['event'] == 'node_added']
    assert len(node_ids) == len(set(node_ids))
    progress = [event for event in events if event['event'] == 'progress']
    assert set(e['statement'] for e in progress) == set(range(1, len(answer['sql_run']) + 1))
    assert all(set(e) == {'event', 'stage', 'state', 'elapsed_ms', 'bytes_processed', 'statement'} for e in progress)
    for statement in sorted({e['statement'] for e in progress}):
        updates = [e for e in progress if e['statement'] == statement]
        assert updates[-1]['state'] == 'done'
        if fake is not None:
            assert updates[0]['state'] == 'queued'
            running = [e for e in updates if e['state'] == 'running']
            assert len(running) >= 4
            assert all(b['elapsed_ms'] - a['elapsed_ms'] <= 3000 for a, b in zip(running, running[1:]))
        else:
            assert [e['state'] for e in updates] == ['running', 'done']
    if fake is not None:
        assert budgets(fake) == dict(graph=0, retrieval=0, warehouse=2, dry=2, catalogue=0)


def test_refused_model_streams_only_reason_then_the_replacement_rules_plan(comparison_engine):
    from kge.semantics.plan import Abstain
    from tests.integration.test_plan_wording_round2 import Recording

    engine, fake = comparison_engine
    reason = 'The recorded model declined this plan.'
    caller = Combined(engine, llm=Recording(Abstain(kind='abstain', spans=(), missing=(reason,))))
    events, answer = streamed(engine, 'certified NNA for March 2025', combined=caller)
    assert answer['behaviour'] == 'figure'
    refused = [i for i, e in enumerate(events) if e['event'] == 'edge_added' and e['edge'].get('state') == 'refused']
    assert len(refused) == 1 and refused[0] < next(i for i, e in enumerate(events) if e['event'] == 'plan')
    edge = events[refused[0]]['edge']
    assert edge['from'] == 'Plan:model' and edge['reason'] == edge['detail'] == reason
    assert next(s for s in answer['trace'] if s['step'] == 'model_plan_refused')['fallback'] == 'rules'
    plan = next(e['plan'] for e in events if e['event'] == 'plan')
    assert plan['sql'] == answer['sql']
    assert assembled(events) == {key: answer['graph_path'][key] for key in ('nodes', 'edges')}


@pytest.mark.parametrize('question,who,forbidden', [
    (QUESTION, 'analyst_retail', ('Unit:wealth', 'TRAN_FACT', 'Wealth')),
    ('Average retail revenue by customer.email in June 2025', WHO, ('customer.email',)),
])
def test_refused_stream_has_no_plan_context_query_or_progress_and_redacts_graph(comparison_engine, question, who, forbidden):
    engine, fake = comparison_engine
    events, answer = streamed(engine, question, who)
    assert answer['behaviour'] in ('refuse', 'blocked_access')
    assert not any(e['event'] in ('progress', 'plan', 'context') for e in events)
    text = json.dumps([e for e in events if e['event'] != 'answer'])
    assert not any(word in text for word in forbidden)
    assert assembled(events) == {key: answer['graph_path'][key] for key in ('nodes', 'edges')}


def test_http_ndjson_keeps_answer_last_and_compact_stream_keeps_its_projection(client):
    headers = {**client.as_('analyst_enterprise.meridian@example.com'), 'accept': 'application/x-ndjson'}
    for compact in (False, True):
        response = client.post('/ask', json={'question': QUESTION, 'compact': compact}, headers=headers)
        assert response.status_code == 200
        assert response.headers['content-type'].startswith('application/x-ndjson')
        events = [json.loads(line) for line in response.text.splitlines()]
        assert events[-1]['event'] == 'answer'
        answer = events[-1]['answer']
        assert answer['behaviour'] == 'figure'
        if compact:
            assert not any(e['event'] in ('plan', 'node_added', 'edge_added', 'state_changed') for e in events)
            assert 'path' not in answer and 'graph_path' not in answer and 'sql' not in answer
            assert all(set(e['context'] or {}) <= service.CONTEXT_LABELS for e in events if e['event'] == 'context')
        else:
            assert assembled(events) == {key: answer['graph_path'][key] for key in ('nodes', 'edges')}


def test_cancelling_native_warehouse_progress_cancels_the_existing_job(monkeypatch, tmp_path):
    from tests.bigquery_fake import pilot

    fake, twin, opened, engines = pilot(monkeypatch, tmp_path)
    events = []
    try:
        engine = opened()
        service.answer(engine, engine.profile, QUESTION, WHO, 'analysis', 'graph', PRESETS['graph'])
        fake.requests.clear()
        fake.pending_polls = -1
        post = fake.post
        cancel_urls = []

        def posted(url, body):
            if '/cancel?' in url:
                cancel_urls.append(url)
                url = url.split('?', 1)[0]
            return post(url, body)

        monkeypatch.setattr(fake, 'post', posted)
        with trace.cancellable() as (cancelled, hooks):
            def emit(event):
                events.append(event)
                if event['event'] == 'progress' and event['state'] == 'running':
                    trace.trigger_cancel(cancelled, hooks)

            with trace.graph_stream(emit), pytest.raises(trace.QueryCancelled):
                service.answer(engine, engine.profile, QUESTION, WHO, 'analysis', 'graph', PRESETS['graph'])
        assert any(e['event'] == 'plan' for e in events)
        assert not any(e['event'] == 'answer' for e in events)
        cancelled_jobs = [r for r in fake.requests if r['url'].endswith('/cancel')]
        assert len(cancelled_jobs) == 1
        assert len(cancel_urls) == 1 and 'location=US' in cancel_urls[0]
        job = cancelled_jobs[0]['url'].rsplit('/', 2)[-2]
        assert fake.jobs[job]['cancelled'] is True
        assert len([r for r in fake.requests if r.get('body', {}).get('configuration', {}).get('query')]) == 1
        assert len([r for r in fake.requests if r.get('body', {}).get('dryRun')]) == 1
    finally:
        for engine in engines:
            engine.close()
        if twin.conn is not None:
            twin.conn.close()
            twin.db.close()
        fake.con.close()


@pytest.mark.parametrize('question', ['NNA for the latest month', 'certified NNA for December 2026'])
def test_release_gates_allow_only_a_resolved_public_plan(comparison_engine, question):
    engine, fake = comparison_engine
    events, answer = streamed(engine, question)
    plans = [e['plan'] for e in events if e['event'] == 'plan']
    if answer['behaviour'] == 'figure':
        assert len(plans) == 1 and plans[0]['sql'] == answer['sql']
        assert next(i for i, e in enumerate(events) if e['event'] == 'plan') < next(
            i for i, e in enumerate(events) if e['event'] == 'progress')
    else:
        assert answer['behaviour'] == 'abstain_with_reason', answer
        assert plans == []
        assert not any(e['event'] == 'context' for e in events)
        assert any(e['event'] == 'edge_added' and e['edge'].get('state') == 'refused' for e in events)
    assert assembled(events) == {key: answer['graph_path'][key] for key in ('nodes', 'edges')}


def test_external_runtime_plan_uses_the_executed_sql_and_port_progress(eng, tmp_path):
    from tests.integration.test_runtimes_execution import _create_live_duckdb, _runtime_subgraph

    live = tmp_path / 'live.duckdb'
    _create_live_duckdb(live)
    eng.w.configure_runtimes(_runtime_subgraph(live))
    try:
        events, answer = streamed(eng, 'What was Retail fee income for June 2025?', 'analyst_retail')
        assert answer['behaviour'] == 'figure' and answer['runtime']['id'] == 'bq_live'
        plan, = [e['plan'] for e in events if e['event'] == 'plan']
        assert plan['sql'] == answer['sql'] and 'rev_fact_live' in plan['sql']
        assert [e['state'] for e in events if e['event'] == 'progress'] == ['running', 'done']
    finally:
        eng.w.configure_runtimes(eng.g.runtime_subgraph())
