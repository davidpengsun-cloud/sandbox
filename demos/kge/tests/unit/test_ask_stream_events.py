"""The NDJSON contract at the recording, progress and transport seams, without sockets."""
from __future__ import annotations

import asyncio
import json
import threading
from urllib.parse import parse_qs

import pytest

from kge.adapters.bigquery import BigQueryLoader
from kge.ports import QueryLimits
from kge.pipeline import trace
from kge.services.ask.engine.contextpath import ReadPath
from kge.services.ask.routes import _stream_response
from tests.bigquery_fake import install
from tests.unit.test_graph_path import engine, returned_context


def assembled(events):
    nodes, edges = {}, {}
    for event in events:
        if event['event'] in ('node_added', 'state_changed') and 'node' in event:
            nodes[event['node']['id']] = event['node']
        elif event['event'] in ('edge_added', 'edge_changed'):
            edge = event['edge']
            edges[edge['from'], edge['to'], edge['type'], edge['label']] = edge
    return {'nodes': list(nodes.values()), 'edges': list(edges.values())}


def test_graph_events_use_existing_names_stable_ids_and_no_duplicate_nodes(tmp_path):
    eng, events = engine(tmp_path), []
    with trace.graph_stream(events.append):
        recorder = ReadPath(eng, 'analyst', 'Net flows')
        with trace.graph_recording(recorder):
            returned_context(eng)
            trace.graph_read('join', ('flows', eng.semantics.contexts['flows'].joins[0]))
            returned_context(eng)
    path = recorder.path()
    assert assembled(events) == {key: path[key] for key in ('nodes', 'edges')}
    ids = [event['node']['id'] for event in events if event['event'] == 'node_added']
    assert len(ids) == len(set(ids))
    assert any(event['event'] == 'state_changed' for event in events)
    assert not any(event['event'].startswith('graph_') and event['event'] != 'graph_summary' for event in events)


def test_streamed_ids_columns_and_refused_plan_are_redacted_at_record_time(tmp_path):
    eng, events = engine(tmp_path), []
    with trace.graph_stream(events.append):
        recorder = ReadPath(eng, 'wealth_only', 'Net flows')
        with trace.graph_recording(recorder):
            returned_context(eng)
            trace.graph_read('runtime', ({'runtime': {'id': 'reference'},
                            'sql': 'SELECT FLOW.CUST_SSN FROM wealth.FLOW'}, {'passed': True}))
            trace.graph_read('plan_refusal', ({'kind': 'refuse', 'missing': ['CUST_SSN is restricted.'],
                             'plan': {'sql': 'private-model-sql'}, 'choices': ['private-choice']}, 'model', 'plan', None))
        snapshot = json.dumps(events)
        assert 'CUST_SSN' not in snapshot and 'private-model-sql' not in snapshot and 'private-choice' not in snapshot
        assert 'secret_ledger' not in snapshot and 'with_ledger' not in snapshot
        assert any(e['event'] == 'edge_added' and e['edge'].get('reason') == 'withheld is restricted.' for e in events)
    assert assembled(events) == {key: recorder.path()[key] for key in ('nodes', 'edges')}
    events.clear()
    with trace.graph_stream(events.append):
        recorder = ReadPath(eng, 'retail_only', 'Net flows')
        with trace.graph_recording(recorder):
            trace.graph_read('access', ('retail_only', ['wealth'], ['wealth']))
    assert 'Unit:withheld-1' in json.dumps(events)
    assert 'Unit:wealth' not in json.dumps(events) and 'Wealth' not in json.dumps(events)


@pytest.mark.parametrize('bounded', [False, True])
def test_bigquery_progress_uses_existing_status_polls_and_one_job(monkeypatch, bounded):
    fake = install(monkeypatch)
    try:
        fake.pending_polls = 5
        loader = BigQueryLoader(project='pilot-project')
        now = [0.0]
        loader._clock = lambda: now[0]
        get = loader.get

        def poll(*args, **kwargs):
            now[0] += 3
            return get(*args, **kwargs)

        loader.get = poll
        events = []
        answer = loader.run('SELECT 7 AS figure', dataset_id='demo' if bounded else None,
                            limits=QueryLimits(seconds=60) if bounded else None,
                            on_progress=lambda *values: events.append(values))
        assert answer['rows']
        assert events[0][0] == 'queued' and events[-1][0] == 'done'
        running = [e for e in events if e[0] == 'running']
        assert len(running) >= 4
        assert all(b[1] - a[1] <= 3000 for a, b in zip(running, running[1:]))
        assert events[-1][2] == 0
        assert len(fake.jobs) == 1
        assert len([r for r in fake.requests if r.get('method') == 'GET' and '/queries/' in r['url']]) == 5
        assert all(set(values) <= {'timeoutMs', 'formatOptions.useInt64Timestamp', 'location', 'maxResults'} for values in [
            parse_qs(r['url'].split('?', 1)[1])
            for r in fake.requests if r.get('method') == 'GET' and '/queries/' in r['url']])
    finally:
        fake.con.close()


def test_warehouse_progress_numbering_excludes_gates_that_never_execute():
    events = []
    with trace.graph_stream(events.append):
        with trace.warehouse_statement():
            pass
        for _ in range(2):
            with trace.warehouse_statement() as progress:
                progress('running', 0, None)
                progress('done', 1, 23)
    assert [e['statement'] for e in events] == [1, 1, 2, 2]
    assert all(set(e) == {'event', 'stage', 'state', 'elapsed_ms', 'bytes_processed', 'statement'} for e in events)


class Connected:
    async def is_disconnected(self):
        return False


def test_cancelled_stream_invokes_cleanup_and_finishes_worker_without_answer():
    stopped, exited = threading.Event(), threading.Event()

    def compute(on_stage):
        try:
            trace.register_cancel_hook(stopped.set)
            trace.stream_event({'event': 'context', 'context': {'label': 'Arrived'}})
            assert stopped.wait(2)
            trace.check_cancelled()
        finally:
            exited.set()

    async def consume():
        response = _stream_response(Connected(), 'Question', compute)
        event = json.loads(await anext(response.body_iterator))
        assert event == {'event': 'context', 'context': {'label': 'Arrived'}}
        await response.body_iterator.aclose()

    asyncio.run(consume())
    assert stopped.is_set() and exited.wait(2)


@pytest.mark.parametrize('failure', [ValueError, trace.QueryCancelled])
def test_failed_or_stopped_stream_keeps_prior_events_and_has_no_final_answer(failure):
    def compute(on_stage):
        trace.stream_event({'event': 'context', 'context': {'label': 'Arrived'}})
        raise failure('Stopped')

    async def consume():
        response = _stream_response(Connected(), 'Question', compute)
        return [json.loads(line) async for line in response.body_iterator]

    events = asyncio.run(consume())
    assert events[0]['event'] == 'context'
    assert not any(e['event'] == 'answer' for e in events)
    if failure is ValueError:
        assert events[-1] == {'event': 'error', 'error': 'This action is unavailable.'}


def node(name, **extra):
    return {'id': f'Table:{name}', 'type': 'Table', 'name': name, 'label': '', 'group': 'tables', 'used': True,
            'state': 'done', 'certified': False, 'badges': [], 'detail': name, **extra}


def test_a_later_pass_sends_only_what_changed_and_its_stage_totals():
    from kge.services.ask.engine.path import PathStreamRecorder
    edge = {'from': 'Table:a', 'to': 'Table:b', 'type': 'JoinRule', 'label': 'j', 'planner': False, 'used': True, 'state': 'done'}
    sent = [{'event': 'stage', 'stage': 'understand', 'state': 'running'},
            {'event': 'node_added', 'node': node('a')}, {'event': 'node_added', 'node': node('b')},
            {'event': 'edge_added', 'edge': edge},
            {'event': 'stage', 'stage': 'understand', 'state': 'done', 'ms': 1.5, 'llm_calls': 0, 'tokens_in': 0, 'tokens_out': 0},
            {'event': 'plan', 'plan': {'sql': 'SELECT 1'}}, {'event': 'graph_summary', 'unused': 1},
            {'event': 'stage', 'stage': 'execute', 'state': 'running'}]
    out = []
    recorder = PathStreamRecorder('Q', out.append, sent, [{'stages': [{'stage': 'understand', 'ms': 1.5, 'llm_calls': 0,
                                                                         'tokens_in': 0, 'tokens_out': 0}]}])
    recorder.on_stage('understand')
    for event in ({'event': 'node_added', 'node': node('a')}, {'event': 'node_added', 'node': node('c')},
                  {'event': 'edge_added', 'edge': {**edge, 'used': False}}, {'event': 'edge_added', 'edge': edge},
                  {'event': 'graph_summary', 'unused': 3}, {'event': 'graph_summary', 'unused': 1},
                  {'event': 'plan', 'plan': {'sql': 'SELECT 1'}}):
        recorder.publish(event)
    assert out == []
    stages = [{'stage': 'understand', 'ms': 3.0, 'llm_calls': 0, 'tokens_in': 0, 'tokens_out': 0},
              {'stage': 'execute', 'ms': 9.0, 'llm_calls': 0, 'tokens_in': 0, 'tokens_out': 0}]
    answer = {'graph_path': {'nodes': [node('a'), node('b'), node('c')], 'edges': [edge], 'unused': 1},
              'run_trace': {'stages': stages}}
    recorder.emit_final(answer)
    assert [e['event'] for e in out] == ['node_added', 'stage', 'stage', 'answer']
    assert out[0]['node']['id'] == 'Table:c'
    assert [(e['stage'], e['state'], e['ms']) for e in out[1:3]] == [('understand', 'done', 3.0), ('execute', 'done', 9.0)]
    assert assembled(sent + out) == {key: answer['graph_path'][key] for key in ('nodes', 'edges')}


def test_a_live_pass_shows_the_running_step_and_holds_the_unused_count_until_the_plan():
    from kge.services.ask.engine import runtrace
    from kge.services.ask.engine.path import PathStreamRecorder
    out = []
    recorder = PathStreamRecorder('Q', out.append)
    with runtrace.start('ask', on_stage=recorder.on_stage) as trace_:
        trace_.stage('resolve')
        recorder.publish({'event': 'node_added', 'node': node('a', used=False)})
        recorder.publish({'event': 'graph_summary', 'unused': 3})
        recorder.publish({'event': 'state_changed', 'id': 'Table:a', 'state': 'done', 'node': node('a', detail='more')})
        trace_.stage('plan')
        recorder.publish({'event': 'state_changed', 'id': 'Table:a', 'state': 'done', 'node': node('a', detail='most')})
        recorder.publish({'event': 'graph_summary', 'unused': 1})
        recorder.publish({'event': 'plan', 'plan': {'sql': 'SELECT 1'}})
        recorder.publish({'event': 'plan', 'plan': {'sql': 'SELECT 1'}})
        run_trace = trace_.finish({})
    resolve_ms = next(e['ms'] for e in out if e['event'] == 'stage' and e['state'] == 'done')
    assert resolve_ms == run_trace['stages'][0]['ms']
    recorder.emit_final({'graph_path': {'nodes': [node('a', detail='most')], 'edges': [], 'unused': 1}, 'run_trace': run_trace})
    shown = [(e['event'], e.get('stage') or e.get('node', {}).get('state') or e.get('unused'), e.get('state')) for e in out]
    assert shown == [('stage', 'resolve', 'running'), ('node_added', 'running', None), ('stage', 'resolve', 'done'),
                     ('state_changed', 'done', 'done'), ('stage', 'plan', 'running'), ('state_changed', 'running', 'running'),
                     ('plan', None, None), ('graph_summary', 1, None), ('state_changed', 'done', 'done'),
                     ('stage', 'plan', 'done'), ('answer', None, None)]
    assert out[3]['node']['detail'] == 'more' and out[-3]['node']['detail'] == 'most'
    assert [(e['stage'], e['ms']) for e in out if e['event'] == 'stage' and e['state'] == 'done'] == [
        (s['stage'], s['ms']) for s in run_trace['stages']]


def test_a_later_pass_streams_live_once_its_replay_reaches_a_new_stage():
    from kge.services.ask.engine.path import PathStreamRecorder
    sent = [{'event': 'stage', 'stage': 'plan', 'state': 'running'}, {'event': 'node_added', 'node': node('a')},
            {'event': 'stage', 'stage': 'plan', 'state': 'done', 'ms': 1.0, 'llm_calls': 0, 'tokens_in': 0, 'tokens_out': 0},
            {'event': 'plan', 'plan': {'sql': 'SELECT 1'}}, {'event': 'stage', 'stage': 'execute', 'state': 'running'}]
    out = []
    recorder = PathStreamRecorder('Q', out.append, sent, [])
    recorder.on_stage('plan')
    recorder.publish({'event': 'node_added', 'node': node('a')})
    recorder.publish({'event': 'node_added', 'node': node('b')})
    assert out == []
    recorder.on_stage('bind')
    assert [(e['event'], e.get('stage') or e['node']['id'], e.get('state')) for e in out] == [
        ('node_added', 'Table:b', None), ('stage', 'bind', 'running')]
    out.clear()
    recorder.publish({'event': 'node_added', 'node': node('c')})
    recorder.publish({'event': 'graph_summary', 'unused': 2})
    assert [(e['event'], e.get('node', {}).get('id'), e.get('node', {}).get('state')) for e in out] == [
        ('node_added', 'Table:c', 'running'), ('graph_summary', None, None)]


def test_a_live_run_that_fails_after_its_start_ends_with_the_state_error_and_a_log(caplog):
    import logging
    import time as clock
    from kge.ports import StateUnavailable
    from kge.services.ask import service

    def work(on_start, on_event, stop):
        on_start({'id': 'a' * 32, 'state': 'running', 'expires_at': clock.time() + 900, 'poll_after': 2, 'events': [],
                  'request': {'question': 'Q'}})
        on_event(1, {'event': 'stage', 'stage': 'understand', 'state': 'running'})
        raise StateUnavailable('no project')

    out, lines, finished = service.live_run(work, lambda: None, Connected().is_disconnected, starting=True)
    assert out is None

    async def consume():
        return [json.loads(line) async for line in lines]

    with caplog.at_level(logging.WARNING, logger='kge.services.ask.service'):
        events = asyncio.run(consume())
    assert finished.wait(2)
    assert [e['event'] for e in events] == ['run', 'stage', 'error']
    assert events[1]['cursor'] == 1
    assert events[-1] == {'event': 'error', 'error': service.STATE_UNAVAILABLE, 'status': 503}
    assert any(record.exc_info and isinstance(record.exc_info[1], StateUnavailable) for record in caplog.records)


def test_a_runtime_read_in_planning_runs_again_while_its_query_executes():
    from kge.services.ask.engine import runtrace
    from kge.services.ask.engine.path import PathStreamRecorder
    out = []
    recorder = PathStreamRecorder('Q', out.append)
    runtime = {**node('x'), 'id': 'Runtime:reference', 'type': 'Runtime', 'group': 'runtime'}
    with runtrace.start('ask', on_stage=recorder.on_stage) as trace_:
        trace_.stage('plan')
        recorder.publish({'event': 'node_added', 'node': runtime})
        trace_.stage('gate')
        trace_.stage('execute')
        run_trace = trace_.finish({})
    recorder.emit_final({'graph_path': {'nodes': [runtime], 'edges': [], 'unused': 0}, 'run_trace': run_trace})
    shown = [(e['event'], e.get('stage') or e['node']['state'], e.get('state')) for e in out
             if e['event'] == 'stage' or e.get('node', {}).get('id') == 'Runtime:reference']
    assert shown == [('stage', 'plan', 'running'), ('node_added', 'running', None), ('stage', 'plan', 'done'),
                     ('state_changed', 'done', 'done'), ('stage', 'gate', 'running'), ('stage', 'gate', 'done'),
                     ('state_changed', 'running', 'running'), ('stage', 'execute', 'running'),
                     ('state_changed', 'done', 'done'), ('stage', 'execute', 'done')]


def test_a_stage_done_after_a_pause_carries_the_earlier_passes_time_with_this_ones():
    import time as clock
    from kge.services.ask.engine import runtrace
    from kge.services.ask.engine.path import PathStreamRecorder
    zero = {'llm_calls': 0, 'tokens_in': 0, 'tokens_out': 0}
    sent = [{'event': 'stage', 'stage': 'gate', 'state': 'running'},
            {'event': 'stage', 'stage': 'gate', 'state': 'done', 'ms': 2.0, **zero},
            {'event': 'plan', 'plan': {'sql': 'SELECT 1'}}, {'event': 'stage', 'stage': 'execute', 'state': 'running'}]
    parts = [{'stages': [{'stage': 'gate', 'ms': 2.0, **zero}, {'stage': 'execute', 'ms': 1.0, **zero}]}]
    out = []
    recorder = PathStreamRecorder('Q', out.append, sent, parts)
    with runtrace.start('ask', on_stage=recorder.on_stage) as trace_:
        trace_.stage('gate')
        clock.sleep(0.02)
        trace_.stage('bind')
        trace_.stage('gate')
        clock.sleep(0.02)
        trace_.stage('execute')
        now = {s['stage']: s for s in trace_.stage_totals()}
    done = [e for e in out if e['event'] == 'stage' and e['state'] == 'done']
    assert [(e['stage'], e['ms']) for e in done] == [('execute', 1.0), ('bind', now['bind']['ms']),
                                                     ('gate', 2.0 + now['gate']['ms'])]
    assert now['gate']['ms'] >= 40
