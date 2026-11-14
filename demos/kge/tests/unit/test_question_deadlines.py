"""Deadline, isolated checkpoint and local-worker regression contracts."""
import copy
import asyncio
import json
import threading
import time
from types import SimpleNamespace
from urllib.error import HTTPError

import pytest

from kge.adapters import deadline
from kge.adapters.bigquery import BigQueryLoader
from kge.adapters.objstore.gcs import GcsStore
from kge.adapters.questionstate.store import StoreQuestionRuns
from kge.adapters.warehouse import jobs, localjobs
from kge.pipeline import questionrun
from kge.ports import StateConflict, StateUnavailable
from kge.services.ask import service
from kge.services.ask.engine.config import PRESETS
from kge.services.ask.models import Ask
from tests.unit.test_question_runs import run_setup, states as states, warehouse as warehouse


@pytest.mark.parametrize('end,expected', [(1599999, 'done'), (1600000, 'done'), (1605000, 'failed')])
def test_done_job_checks_actual_end_before_fetching_results(monkeypatch, end, expected):
    loader = BigQueryLoader(project='billing-project')
    calls, cancelled = [], []
    monkeypatch.setattr(loader, 'token', lambda: ('offline', 'billing-project'))
    def get(url, headers):
        calls.append(url)
        if '/jobs/' in url:
            return {'status': {'state': 'DONE'}, 'statistics': {'endTime': str(end)}}
        return {'jobComplete': True, 'schema': {'fields': []}, 'rows': []}
    monkeypatch.setattr(loader, 'get', get)
    monkeypatch.setattr(loader, 'cancel_job', lambda *args: cancelled.append(args))
    monkeypatch.setattr(time, 'time', lambda: 1610.)
    result = jobs.poll(loader, {'reference': {'jobId': 'ask_late', 'location': 'EU'}, 'started': 1000., 'expires': 1600.})
    assert result['state'] == expected
    assert len(calls) == (2 if expected == 'done' else 1)
    assert not cancelled
    if expected == 'failed':
        assert 'time limit' in result['error'] and 'rows' not in result


@pytest.mark.parametrize('operation', ['get', 'put'])
@pytest.mark.parametrize('request_seconds,run_seconds', [(.08, .15), (.2, .08)])
def test_checkpoint_io_is_bounded_by_the_run_and_request(monkeypatch, operation, request_seconds, run_seconds):
    entered, release, exited = threading.Event(), threading.Event(), threading.Event()
    def stall(*args):
        entered.set()
        try:
            release.wait(2)
            return (None, '0') if operation == 'get' else '1'
        finally:
            exited.set()
    state = StoreQuestionRuns(SimpleNamespace(get_version=stall, put_version=stall))
    record = {'expires_at': time.time() + run_seconds}
    try:
        with pytest.raises(deadline.RequestDeadlineExceeded):
            with deadline.request_window(request_seconds), questionrun.checkpoints(record, lambda: None):
                state.get('a' * 32) if operation == 'get' else state.put('a' * 32, record, None)
        assert entered.is_set() and not exited.is_set()
    finally:
        release.set()
        assert exited.wait(2)


def test_gcs_checkpoint_retries_and_http_timeout_share_one_budget(monkeypatch):
    monkeypatch.setenv('STORAGE_EMULATOR_HOST', 'http://offline.invalid')
    clock, calls, sleeps = [10.], [], []
    monkeypatch.setattr(deadline.time, 'monotonic', lambda: clock[0])
    def open_http(request, timeout):
        calls.append(timeout)
        clock[0] += .2
        raise HTTPError(request.full_url, 503, 'offline', {}, None)
    def sleep(seconds):
        sleeps.append(seconds)
        clock[0] += seconds
    monkeypatch.setattr('kge.adapters.http.open_http', open_http)
    monkeypatch.setattr(time, 'sleep', sleep)
    store = StoreQuestionRuns(GcsStore('gs://offline/questions', 'offline', 'questions'))
    with pytest.raises(deadline.RequestDeadlineExceeded):
        with deadline.request_window(.6):
            store.get('a' * 32)
    assert len(calls) == len(sleeps) == 1
    assert calls[0] <= .6 and sleeps[0] <= .4 + 1e-9


def test_done_checkpoint_is_saved_inside_the_run_window(monkeypatch):
    writes = []
    class State:
        def put(self, run_id, record, revision):
            writes.append((copy.deepcopy(record), deadline.request_remaining()))
            return '1'
    c, _ = run_setup(monkeypatch, object())
    monkeypatch.setattr(service, 'ask', lambda *args, **kwargs: {'run_trace': {}, 'answer_text': '15'})
    monkeypatch.setenv('QUESTION_RUN_TIMEOUT', '5')
    run = service.start_run(c, None, Ask(question='Metric'), 'analyst', PRESETS['graph'], state=State())
    assert run['state'] == 'done'
    final, seconds = writes[-1]
    assert final['state'] == 'done' and 0 < seconds <= 5


def test_done_checkpoint_cannot_start_after_run_expiry(monkeypatch):
    clock, writes = [1000.], []
    monkeypatch.setattr(time, 'time', lambda: clock[0])
    monkeypatch.setattr(time, 'monotonic', lambda: clock[0])
    class State:
        def put(self, run_id, record, revision):
            deadline.request_remaining()
            writes.append(copy.deepcopy(record))
            return '1'
    c, _ = run_setup(monkeypatch, object())
    def assemble(*args, **kwargs):
        clock[0] += 6
        return {'run_trace': {}, 'answer_text': '15'}
    monkeypatch.setattr(service, 'ask', assemble)
    monkeypatch.setenv('QUESTION_RUN_TIMEOUT', '5')
    run = service.start_run(c, None, Ask(question='Metric'), 'analyst', PRESETS['graph'], state=State(), clock=lambda: clock[0])
    assert run['state'] == 'expired' and 'answer' not in run
    assert not any(record['state'] == 'done' for record in writes)


@pytest.mark.parametrize('started_at,committed_at,initial_state', [(4., 4.1, 'done'), (4.95, 5.1, 'expired')])
def test_on_time_completion_survives_late_reads_and_a_delayed_checkpoint(states, monkeypatch, started_at, committed_at, initial_state):
    new, fake = states
    state, clock, saved = new(), [0.], []
    committed = threading.Event()
    monkeypatch.setattr(time, 'time', lambda: clock[0])
    monkeypatch.setattr(time, 'monotonic', lambda: clock[0])
    monkeypatch.setattr(deadline, 'MIN_CALL_SECONDS', 0)
    monkeypatch.setenv('QUESTION_RUN_TIMEOUT', '5')
    def committing(record):
        saved.append(copy.deepcopy(record))
        if record.get('state') == 'done':
            assert clock[0] == started_at
            if initial_state == 'expired':
                assert clock[0] == pytest.approx(record['expires_at'] - .05)
            assert record['completed_at'] == clock[0]
            clock[0] = committed_at
    if isinstance(state, StoreQuestionRuns):
        put = state.store.put_version
        def put_version(key, data, revision):
            record = questionrun.loads(data)
            committing(record)
            result = put(key, data, revision)
            if record.get('state') == 'done':
                committed.set()
            return result
        monkeypatch.setattr(state.store, 'put_version', put_version)
    else:
        post = state.records._post
        def post_record(body):
            params = {p['name']: p['parameterValue']['value'] for p in body.get('queryParameters', [])}
            if 'record' in params:
                committing(questionrun.loads(params['record']))
            result = post(body)
            if saved and saved[-1]['state'] == 'done':
                committed.set()
            return result
        monkeypatch.setattr(state.records, '_post', post_record)
    c, _ = run_setup(monkeypatch, object())
    def assemble(*args, **kwargs):
        clock[0] = started_at
        return {'question': 'Metric', 'principal': 'analyst', 'purpose': 'analysis', 'arm': 'graph', 'behaviour': 'figure',
                'answer_text': '15', 'phrased_by': 'template', 'versions': {}, 'combined': False, 'run_trace': {}}
    monkeypatch.setattr(service, 'ask', assemble)
    run = service.start_run(c, None, Ask(question='Metric'), 'analyst', PRESETS['graph'], state=state, clock=lambda: clock[0])
    assert committed.wait(2)
    assert run['state'] == initial_state
    assert saved[0]['expires_at'] == saved[-1]['expires_at'] == 5
    assert saved[0]['completed_at'] is None and saved[-1]['completed_at'] == started_at
    assert saved[-1]['state'] == 'done'
    if fake.requests:
        assert len(fake.requests) == 3
        fake.requests.clear()
    clock[0] = saved[-1]['expires_at'] + 3600
    held, _ = new().get(run['id'])
    assert held['state'] == 'done' and held['answer']['answer_text'] == '15'
    assert_poll_resume_and_reconnect(monkeypatch, new, c, run['id'], 'done', len(saved[-1]['events']))
    if fake.requests:
        assert len(fake.requests) == 5


def assert_poll_resume_and_reconnect(monkeypatch, new, c, run_id, expected, cursor):
    from kge.services.ask import routes
    resumed = service.get_run(c, None, run_id, 'analyst', state=new())
    assert resumed['state'] == expected
    if expected == 'done':
        assert resumed['answer']['answer_text'] == '15'
    else:
        assert 'answer' not in resumed and 'time limit' in resumed['error']
        assert not any(event['event'] == 'answer' for event in resumed['events'])
    monkeypatch.setattr(routes, 'served', SimpleNamespace(combined=lambda: c, PROFILE=None))
    monkeypatch.setattr('kge.adapters.questionstate.for_profile', lambda _: new())
    async def events(response):
        return [json.loads(line) async for line in response.body_iterator]
    for accept, after in [('application/json', 0), ('application/x-ndjson', 0), ('application/x-ndjson', cursor)]:
        response = routes.question_run(run_id, SimpleNamespace(headers={'accept': accept}), who='analyst', after=after)
        assert response.status_code == 200
        if accept == 'application/json':
            out = json.loads(response.body)
            assert out['state'] == expected
            if expected == 'expired':
                assert 'answer' not in out and 'time limit' in out['error']
                assert not any(event['event'] == 'answer' for event in out['events'])
            else:
                assert out['answer']['answer_text'] == '15'
        else:
            out = asyncio.run(events(response))
            assert out[0]['state'] == expected
            answers = [event for event in out if event['event'] == 'answer']
            assert bool(answers) == (expected == 'done')
            if expected == 'expired':
                assert out[-1]['event'] == 'error' and 'time limit' in out[-1]['error']


@pytest.mark.parametrize('completed_at,expected', [(4., 'done'), (5., 'done'), (5.1, 'expired'), (None, 'expired')])
def test_saved_completion_time_governs_poll_resume_and_reconnect(states, monkeypatch, completed_at, expected):
    new, fake = states
    clock = [4.9]
    monkeypatch.setattr(time, 'time', lambda: clock[0])
    c, _ = run_setup(monkeypatch, object())
    answer = {'question': 'Metric', 'principal': 'analyst', 'purpose': 'analysis', 'arm': 'graph', 'behaviour': 'figure',
              'answer_text': '15', 'phrased_by': 'template', 'versions': {}, 'combined': False}
    record = {'id': 'b' * 32, 'principal': 'analyst', 'identity': c.e.identity, 'readable': ['u'],
              'state': 'done', 'expires_at': 5., 'completed_at': completed_at, 'poll_after': 2, 'answer': answer,
              'events': [{'event': 'progress'}, {'event': 'answer', 'answer': answer}]}
    state = new()
    revision = state.put(record['id'], record, None)
    read, read_revision = new().get(record['id'])
    assert read_revision == revision and read['state'] == expected
    clock[0] = record['expires_at'] + 3600
    read, read_revision = new().get(record['id'])
    assert read_revision == revision and read['state'] == expected
    if expected == 'expired':
        assert 'answer' not in read and 'time limit' in read['error']
        assert read['events'] == [{'event': 'progress'}]
    else:
        assert read == record
    assert_poll_resume_and_reconnect(monkeypatch, new, c, record['id'], expected, len(record['events']))
    if fake.requests:
        assert len(fake.requests) == 8


def test_pending_past_deadline_expires_on_poll_resume_and_reconnect(states, monkeypatch):
    new, fake = states
    clock = [4.]
    monkeypatch.setattr(time, 'time', lambda: clock[0])
    c, _ = run_setup(monkeypatch, object())
    record = {'id': 'b' * 32, 'principal': 'analyst', 'identity': c.e.identity, 'readable': ['u'],
              'state': 'running', 'expires_at': 5., 'completed_at': None, 'poll_after': 2,
              'events': [], 'jobs': [], 'request': {'question': 'Metric'}}
    state = new()
    state.put(record['id'], record, None)
    clock[0] = 5.1
    assert_poll_resume_and_reconnect(monkeypatch, new, c, record['id'], 'expired', 0)
    if fake.requests:
        assert len(fake.requests) == 6


@pytest.mark.parametrize('states', ['bigquery'], indirect=True)
def test_questions_have_isolated_bigquery_transactions_and_bounded_abort_retries(states, monkeypatch):
    new, fake = states
    state = new()
    first, second = 'c' * 32, 'd' * 32
    revision = state.put(first, {'state': 'running'}, None)
    state.put(second, {'state': 'running'}, None)
    creates = [r['body']['query'] for r in fake.requests if 'CREATE TABLE' in r['body']['query']]
    assert len(creates) == 2 and all('question_run_' in sql for sql in creates)
    assert first in creates[0] and second in creates[1]
    assert all("WHERE id=''" not in r['body']['query'] for r in fake.requests)
    original, calls = state.records._query, []
    def aborted(sql, **params):
        calls.append(sql)
        if len(calls) < 3:
            raise RuntimeError('Transaction aborted due to concurrent update')
        return original(sql, **params)
    monkeypatch.setattr(state.records, '_query', aborted)
    monkeypatch.setattr(state.records, '_sleep', lambda _: None)
    fake.requests.clear()
    revision = state.put(first, {'state': 'done', 'answer': 15}, revision)
    assert len(calls) == 3 and len(fake.requests) == 1
    assert state.get(first)[0] == {'state': 'done', 'answer': 15}
    assert state.get(second)[0] == {'state': 'running'}
    calls.clear()
    def always_abort(sql, **params):
        calls.append(sql)
        raise RuntimeError('Transaction aborted')
    monkeypatch.setattr(state.records, '_query', always_abort)
    with pytest.raises(StateConflict):
        state.put(first, {'state': 'failed'}, revision)
    assert len(calls) == 4


@pytest.mark.parametrize('states', ['bigquery'], indirect=True)
def test_bigquery_checkpoint_jobs_respect_request_budget(states, monkeypatch):
    new, fake = states
    state = new()
    with deadline.request_window(2):
        state.put('e' * 32, {'state': 'running', 'expires_at': time.time() + 1}, None)
    assert len(fake.requests) == 2
    assert all(0 < int(r['body']['jobTimeoutMs']) <= 1000 for r in fake.requests)
    assert all(0 < r['body']['timeoutMs'] <= 1000 for r in fake.requests)


def test_local_watchdog_cancels_without_a_poll_and_releases_jobs(monkeypatch):
    entered, cancelled, exited = threading.Event(), threading.Event(), threading.Event()
    monkeypatch.setenv('QUESTION_RUN_TIMEOUT', '.1')
    def compute(register):
        register(cancelled.set)
        entered.set()
        try:
            assert cancelled.wait(2)
            return [(15,)]
        finally:
            exited.set()
    handle = localjobs.submit('watchdog', compute)
    assert entered.wait(2) and exited.wait(2)
    assert handle['id'] not in localjobs._jobs
    result = localjobs.poll(handle)
    assert result['state'] == 'failed' and 'time limit' in result['error']


def test_finished_local_job_is_released_and_result_replays():
    finished = threading.Event()
    def compute(register):
        finished.set()
        return [(15,)]
    handle = localjobs.submit('released', compute)
    assert finished.wait(2)
    future = localjobs._jobs[handle['id']][0]
    future.result(timeout=2)
    result = localjobs.poll(handle)
    assert result['rows'] == [(15,)] and handle['id'] in localjobs._jobs
    localjobs.release(handle)
    assert handle['id'] not in localjobs._jobs
    assert localjobs.poll(json.loads(json.dumps(handle))) == json.loads(json.dumps(result))


@pytest.mark.parametrize('warehouse', ['duckdb'], indirect=True)
def test_completed_local_job_survives_failed_result_checkpoint(warehouse, monkeypatch, tmp_path):
    from kge.adapters.objstore import open_store
    w, _ = warehouse
    state = StoreQuestionRuns(open_store(str(tmp_path / 'runs')))
    c, _ = run_setup(monkeypatch, w)
    now = [time.time()]
    run = service.start_run(c, None, Ask(question='Metric'), 'analyst', PRESETS['graph'], state=state, clock=lambda: now[0])
    record, _ = state.get(run['id'])
    job_id = record['jobs'][0]['handle']['job']['id']
    localjobs._jobs[job_id][0].result(timeout=2)
    put, failed = state.put, []
    def fail_result_checkpoint(run_id, record, revision):
        if any('result' in job for job in record['jobs']) and not failed:
            failed.append(True)
            raise StateUnavailable('offline')
        return put(run_id, record, revision)
    monkeypatch.setattr(state, 'put', fail_result_checkpoint)
    now[0] += 3
    with pytest.raises(StateUnavailable):
        service.get_run(c, None, run['id'], 'analyst', state=state, clock=lambda: now[0])
    assert job_id in localjobs._jobs
    held, _ = state.get(run['id'])
    assert 'result' not in held['jobs'][0]
    now[0] = held['lease'] + 1
    done = service.get_run(c, None, run['id'], 'analyst', state=state, clock=lambda: now[0])
    assert done['state'] == 'done' and done['answer']['figure'] == 15
    assert job_id not in localjobs._jobs
    assert state.get(run['id'])[0]['jobs'][0]['result']['rows'] == [[15]]


@pytest.mark.parametrize('states', ['bigquery'], indirect=True)
def test_successful_answer_survives_aborted_final_bigquery_checkpoint(states, monkeypatch):
    new, fake = states
    state, aborted = new(), []
    post = state.records._post
    def retry_final(body):
        params = {p['name']: p['parameterValue']['value'] for p in body.get('queryParameters', [])}
        record = json.loads(params.get('record', '{}'))
        if record.get('state') == 'done' and len(aborted) < 2:
            aborted.append(body)
            raise RuntimeError('Transaction aborted due to concurrent update')
        return post(body)
    monkeypatch.setattr(state.records, '_post', retry_final)
    monkeypatch.setattr(state.records, '_sleep', lambda _: None)
    c, _ = run_setup(monkeypatch, object())
    monkeypatch.setattr(service, 'ask', lambda *args, **kwargs: {'run_trace': {}, 'answer_text': '15'})
    run = service.start_run(c, None, Ask(question='Metric'), 'analyst', PRESETS['graph'], state=state)
    assert run['state'] == 'done' and run['answer']['answer_text'] == '15'
    assert len(aborted) == 2
    assert aborted[0]['query'] == aborted[1]['query']
    assert aborted[0]['queryParameters'] == aborted[1]['queryParameters']
    assert int(aborted[1]['jobTimeoutMs']) <= int(aborted[0]['jobTimeoutMs'])
    assert len(fake.requests) == 3
    assert len(fake.requests) + len(aborted) == 5
    assert state.get(run['id'])[0]['state'] == 'done'


def test_recovered_job_retains_its_original_deadline(monkeypatch):
    from kge.adapters.bigquery import QueryFailed
    loader = BigQueryLoader(project='billing-project')
    monkeypatch.setattr(loader, 'token', lambda: ('offline', 'billing-project'))
    monkeypatch.setattr(time, 'time', lambda: 1300.)
    def get(url, headers):
        if '/datasets/' in url:
            return {'location': 'EU'}
        return {'configuration': {'jobTimeoutMs': '600000', 'query': {'query': 'SELECT 1',
                    'defaultDataset': {'projectId': 'billing-project', 'datasetId': 'data'}}},
                'statistics': {'creationTime': '1000000'}}
    def duplicate(*args):
        raise QueryFailed('Already exists', code=409)
    monkeypatch.setattr(loader, 'get', get)
    monkeypatch.setattr(loader, 'post', duplicate)
    handle = jobs.submit(loader, 'SELECT 1', 'data', 'ask_recovered')
    assert handle['started'] == 1000 and handle['expires'] == 1600


def test_submission_deadline_cannot_move_past_the_run_deadline(monkeypatch):
    loader = BigQueryLoader(project='billing-project')
    clock = iter([1000., 1001.])
    monkeypatch.setattr(loader, 'token', lambda: ('offline', 'billing-project'))
    monkeypatch.setattr(loader, 'get', lambda *args: {'location': 'EU'})
    monkeypatch.setattr(loader, 'post', lambda url, headers, body: {'jobReference': body['jobReference']})
    monkeypatch.setattr(time, 'time', lambda: next(clock))
    with questionrun.checkpoints({'expires_at': 1020.}, lambda: None):
        handle = jobs.submit(loader, 'SELECT 1', 'data', 'ask_clock')
    assert handle['expires'] <= 1020.


def test_bigquery_checkpoint_discovery_cannot_outlive_the_request(monkeypatch):
    from kge.adapters.appstate import bigquery
    from kge.adapters.questionstate.bigquery import BigQueryQuestionRuns
    import google.auth.transport.requests
    entered, release, exited = threading.Event(), threading.Event(), threading.Event()
    def refresh(request):
        entered.set()
        try:
            release.wait(2)
        finally:
            exited.set()
    credentials = SimpleNamespace(valid=False, token='offline', refresh=refresh)
    monkeypatch.setenv('BIGQUERY_PROJECT', '')
    monkeypatch.setenv('VERTEX_PROJECT', '')
    monkeypatch.setattr(bigquery, 'bq_account', lambda project: (credentials, 'pilot-project'))
    monkeypatch.setattr(google.auth.transport.requests, 'Request', lambda: object())
    state = BigQueryQuestionRuns(SimpleNamespace(client='sample'))
    try:
        with pytest.raises(deadline.RequestDeadlineExceeded):
            with deadline.request_window(.15):
                state.get('f' * 32)
        assert entered.is_set() and not exited.is_set()
    finally:
        release.set()
        assert exited.wait(2)


def test_bigquery_checkpoint_job_deadline_is_refreshed_after_authentication(monkeypatch):
    from kge.adapters.questionstate import bigquery
    from tests.unit.test_bigquery import JsonResponse
    monkeypatch.setenv('BIGQUERY_PROJECT', 'pilot-project')
    monkeypatch.setattr(bigquery, '_prepared', set())
    state = bigquery.BigQueryQuestionRuns(SimpleNamespace(client='sample'))
    clock, calls = [10.], []
    monkeypatch.setattr(time, 'monotonic', lambda: clock[0])
    def token():
        clock[0] += .1
        return 'offline', 'pilot-project'
    def open_http(request, timeout):
        body = json.loads(request.data)
        calls.append(body)
        assert 0 < int(body['jobTimeoutMs']) <= int(deadline.request_remaining() * 1000)
        assert timeout <= deadline.request_remaining()
        return JsonResponse({'jobComplete': True})
    monkeypatch.setattr(state.records, '_token', token)
    monkeypatch.setattr('kge.adapters.http.open_http', open_http)
    with deadline.request_window(2):
        state.put('f' * 32, {'state': 'running'}, None)
    assert len(calls) == 2
